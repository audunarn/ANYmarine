"""Marine look for the 3D view: a soft sky-tone background and an ocean surface at z = 0.

Public ANY3dView API only. The surface is one non-pickable mesh with baked per-face colours (see
``ocean``: depth tint, Fresnel-style sky mix, horizon haze, very slow colour ripples) plus a thin
waterline where the model crosses z = 0; it is hidden while the view is fitted, so fit-to-view
frames the model only, and while a computed wave surface is displayed (that surface is the water
then). Mode (``ocean.read_mode``): ANYMARINE_OCEAN=static|still|animated (wins) or the QSettings
boolean ``anymarine/ocean_motion`` (default on = animated, off = still). ``static`` is the previous
single-colour translucent plane, which is also the fallback when the new path fails for any reason
(recorded in ``MarineScene.diagnostics``). A true gradient background needs an ANY3dView feature
and is not attempted here.
"""

from __future__ import annotations

import math
import time
from typing import Iterable, Optional

import numpy as np

from any3dview import MeshArrays

from . import ocean

BACKGROUND = "#e7eef5"
WATER_COLOUR = "#5b9cc8"
WATER_OPACITY = 0.26
GRID_COLOUR = "#7fb0d2"
TAG = "anymarine-still-water"


def _nice_step(span: float) -> float:
    """A 1/2/5 x 10^k grid step giving roughly 12-16 cells across ``span``."""
    raw = max(span, 1e-9) / 14.0
    power = 10.0 ** math.floor(math.log10(raw))
    for factor in (1.0, 2.0, 5.0, 10.0):
        if raw <= factor * power:
            return factor * power
    return 10.0 * power


def model_extent(handles: Iterable[object]) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Plan-view (x, y) bounds of the visible handles at their current transforms."""
    lows, highs = [], []
    for handle in handles:
        if handle is None or getattr(handle, "removed", False) or not getattr(handle, "visible", True):
            continue
        positions = np.asarray(handle.mesh.positions, dtype=np.float64)
        if positions.size == 0:
            continue
        transform = np.asarray(handle.transform, dtype=np.float64)
        world = positions @ transform[:3, :3].T + transform[:3, 3]
        lows.append(world[:, :2].min(axis=0))
        highs.append(world[:, :2].max(axis=0))
    if not lows:
        return None
    return np.min(lows, axis=0), np.max(highs, axis=0)


def water_mesh(low: np.ndarray, high: np.ndarray, *, grid: bool) -> MeshArrays:
    """A square still-water patch around the model extent, split into grid cells (the static plane).

    Cell-sized triangles keep near-plane clipping local when the camera is over the water;
    grid lines lie on whole steps.
    """
    centre = 0.5 * (low + high)
    span = float(max(high[0] - low[0], high[1] - low[1], 1.0))
    size = span * 1.5 + max(4.0, 0.25 * span)
    step = _nice_step(size)
    half = math.ceil(0.5 * size / step) * step
    x0, x1 = math.floor((centre[0] - half) / step) * step, math.ceil((centre[0] + half) / step) * step
    y0, y1 = math.floor((centre[1] - half) / step) * step, math.ceil((centre[1] + half) / step) * step
    nx, ny = max(1, round((x1 - x0) / step)), max(1, round((y1 - y0) / step))
    xs, ys = np.linspace(x0, x1, nx + 1), np.linspace(y0, y1, ny + 1)
    gx, gy = np.meshgrid(xs, ys, indexing="xy")
    positions = np.column_stack((gx.ravel(), gy.ravel(), np.zeros(gx.size)))
    index = np.arange((nx + 1) * (ny + 1), dtype=np.uint32).reshape(ny + 1, nx + 1)
    a, b, c, d = index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, 1:].ravel(), index[1:, :-1].ravel()
    triangles = np.concatenate((np.column_stack((a, b, c)), np.column_stack((a, c, d)))).astype(np.uint32)
    lines = None
    if grid:
        horizontal = np.column_stack((index[:, :-1].ravel(), index[:, 1:].ravel()))
        vertical = np.column_stack((index[:-1, :].ravel(), index[1:, :].ravel()))
        lines = np.concatenate((horizontal, vertical)).astype(np.uint32)
    return MeshArrays(positions=positions, triangles=triangles, lines=lines)


class MarineScene:
    """Owns the water handle (and its waterline companion) in the current viewer."""

    def __init__(self) -> None:
        self.viewer = None
        self.handle = None
        self.water_visible = True
        self.grid_visible = True
        self.suppressed = False
        self.diagnostics: list[str] = []
        self.updates = 0                   # handle replacements by the animation (idle = 0)
        self.mode_override: Optional[str] = None   # tests / callers; otherwise ocean.read_mode()
        self._records: dict[int, dict] = {}   # id(handle) -> {handle, layout, extras, colours}
        self._timer = None
        self._clock0 = time.monotonic()
        self._clock = time.monotonic            # injectable for tests

    # ------------------------------------------------------------------ mode
    @property
    def mode(self) -> str:
        return self.mode_override if self.mode_override in ocean.MODES else ocean.read_mode()

    def apply(self, viewer) -> None:
        """Adopt ``viewer`` (the initial renderer): background, no plane yet."""
        self.viewer = viewer
        self.handle = None
        self._records.clear()
        self._stop_timer()
        viewer.set_background(BACKGROUND)

    # ------------------------------------------------------------------ build
    def build(self, viewer, model_handles: Iterable[object]):
        """Prepare ``viewer`` (background + plane) without changing this scene's state.

        Used for a candidate renderer before it replaces the current one; returns the plane handle
        (None without a model or with the plane switched off).
        """
        viewer.set_background(BACKGROUND)
        handles = tuple(model_handles)
        extent = model_extent(handles)
        if extent is None or not self.water_visible:
            return None
        if self.mode != "static":
            try:
                return self._build_ocean(viewer, handles, extent)
            except Exception as error:   # any viewer/GPU/data failure degrades to the static plane
                self.diagnostics.append(f"ocean surface unavailable, static plane used: {type(error).__name__}: {error}")
        return self._build_static(viewer, extent)

    def _build_static(self, viewer, extent):
        return viewer.add_mesh_arrays(
            water_mesh(*extent, grid=self.grid_visible), color=WATER_COLOUR, opacity=WATER_OPACITY,
            line_color=GRID_COLOUR, line_width=1, back_color=WATER_COLOUR, two_sided_shell=True,
            cull_backface=False, pickable=False, tags=TAG)

    def _add_surface(self, viewer, layout, colours):
        return viewer.add_mesh_arrays(
            layout.mesh, color=ocean.SKY_TONE, opacity=ocean.OPACITY, face_colors=colours, lit=False,
            line_color=ocean.GRID_LINE, line_width=1, back_color=ocean.SHALLOW, two_sided_shell=True,
            cull_backface=False, pickable=False, tags=TAG,
            # One outline group: the renderer then draws no per-triangle edges (no quad diagonals), only the
            # patch-boundary loop; that loop and the grid use GRID_LINE, which equals the background colour.
            _outline_groups=np.zeros(layout.mesh.triangle_count, dtype=np.uint32))

    def _build_ocean(self, viewer, handles, extent):
        layout = ocean.ocean_layout(*extent, grid=self.grid_visible)
        colours = ocean.to_hex(ocean.face_colours(layout, self._elapsed(), motion=self.mode == "animated"))
        extras: list = []
        handle = None
        try:
            handle = self._add_surface(viewer, layout, colours)
            line_mesh = ocean.waterline_mesh(handles)
            if line_mesh is not None:
                extras.append(viewer.add_mesh_arrays(
                    line_mesh, color=ocean.WATERLINE, line_color=ocean.WATERLINE, line_width=2, lit=False,
                    pickable=False, tags=TAG))
        except BaseException:
            for part in (handle, *extras):
                if part is not None and not part.removed:
                    try:
                        part.remove()
                    except Exception:
                        pass
            raise
        self._records[id(handle)] = {"handle": handle, "layout": layout, "extras": extras, "colours": tuple(colours)}
        return handle

    # ------------------------------------------------------------------ lifecycle
    def adopt(self, viewer, handle) -> None:
        """Take over a viewer prepared by :meth:`build` (the previous viewer is already gone)."""
        self.viewer = viewer
        self.handle = handle
        self._records = {key: rec for key, rec in self._records.items() if rec["handle"] is handle}
        self._show(not self.suppressed)
        self._update_timer()

    def _parts(self):
        rec = self._records.get(id(self.handle)) if self.handle is not None else None
        return ([self.handle] if self.handle is not None else []) + (rec["extras"] if rec else [])

    def _show(self, visible: bool) -> None:
        for part in self._parts():
            if not part.removed:
                part.set_visible(visible)

    def _drop(self) -> None:
        handle, self.handle = self.handle, None
        for rec in list(self._records.values()):
            for part in (rec["handle"], *rec["extras"]):
                if not part.removed:
                    try:
                        part.remove()
                    except Exception:
                        pass
        self._records.clear()
        self._stop_timer()
        if handle is not None and not handle.removed:
            handle.remove()

    def sync(self, model_handles: Iterable[object]) -> None:
        """Rebuild the plane around the model in the current viewer; no model, no plane."""
        if self.viewer is None:
            return
        self._drop()
        self.handle = self.build(self.viewer, model_handles)
        if self.handle is not None:
            self._show(not self.suppressed)
        self._update_timer()

    def set_suppressed(self, suppressed: bool) -> None:
        """Hide the plane while a computed wave surface stands in for the water."""
        self.suppressed = bool(suppressed)
        if self.handle is not None and not self.handle.removed:
            self._show(not self.suppressed)
            self.viewer.redraw()
        self._update_timer()

    def fit(self, viewer) -> None:
        """Fit the view to the model only (the plane is hidden during the fit)."""
        handle = self.handle if viewer is self.viewer else None
        shown = handle is not None and not handle.removed and handle.visible
        parts = self._parts() if shown else []
        if shown:
            for part in parts:
                part.set_visible(False)
        try:
            viewer.fit_to_scene(redraw=not shown)
        finally:
            if shown:
                for part in parts:
                    if not part.removed:
                        part.set_visible(True)
                viewer.redraw()

    # ------------------------------------------------------------------ animation
    def _elapsed(self) -> float:
        return self._clock() - self._clock0

    def _plane_live(self) -> bool:
        return self.handle is not None and not self.handle.removed and id(self.handle) in self._records

    def _viewer_state(self) -> tuple[bool, bool]:
        viewer = self.viewer
        try:
            visible = bool(viewer.isVisible())
            window = viewer.window()
            minimised = bool(window.isMinimized()) if window is not None else False
        except Exception:
            return False, False
        return visible, minimised

    def animating(self) -> bool:
        """Whether a tick would update now (pure gate, see ``ocean.should_animate``)."""
        if not self._plane_live():
            return False
        visible, minimised = self._viewer_state()
        return ocean.should_animate(self.mode, True, bool(self.handle.visible), self.suppressed, visible, minimised)

    def _update_timer(self) -> None:
        """The timer exists only while an animated, visible, unsuppressed plane exists."""
        wanted = (self.mode == "animated" and self._plane_live() and bool(self.handle.visible)
                  and not self.suppressed)
        if not wanted:
            self._stop_timer()
            return
        if self._timer is None:
            try:
                from PySide6.QtCore import QCoreApplication, QTimer
                if QCoreApplication.instance() is None:
                    return
                self._timer = QTimer()
                self._timer.setInterval(int(round(1000.0 / ocean.FPS)))
                self._timer.timeout.connect(self.tick)
            except Exception as error:
                self.diagnostics.append(f"ocean animation timer unavailable: {error}")
                self._timer = None
                return
        if not self._timer.isActive():
            self._timer.start()

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def tick(self) -> bool:
        """One animation step; returns True when the surface was redrawn.

        ANY3dView applies face colours only when a mesh is added, so a step replaces the surface handle
        (new one first, then the old one is removed). Nothing happens when the gate is closed or when
        the quantised colours did not change.
        """
        if not self.animating():
            return False
        rec = self._records[id(self.handle)]
        colours = tuple(ocean.to_hex(ocean.face_colours(rec["layout"], self._elapsed(), motion=True)))
        if colours == rec["colours"]:
            return False
        old = self.handle
        try:
            new = self._add_surface(self.viewer, rec["layout"], colours)
        except Exception as error:
            self.diagnostics.append(f"ocean animation stopped: {type(error).__name__}: {error}")
            self.mode_override = "still"
            self._update_timer()
            return False
        new.set_visible(not self.suppressed)
        self._records.pop(id(old), None)
        self._records[id(new)] = {**rec, "handle": new, "colours": colours}
        self.handle = new
        if not old.removed:
            old.remove()
        self.updates += 1
        return True
