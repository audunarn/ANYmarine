"""Marine look for the 3D view: a soft sky-tone background and a still-water plane at z = 0.

Public ANY3dView API only. The plane is one non-pickable translucent mesh (with optional grid
lines) sized from the visible model; it is hidden while the view is fitted, so fit-to-view
frames the model only, and while a computed wave surface is displayed (that surface is the
water then). A true gradient background needs an ANY3dView feature and is not attempted here.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np

from any3dview import MeshArrays

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
    """A square still-water patch around the model extent, split into grid cells.

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
    """Owns the still-water handle in the current viewer."""

    def __init__(self) -> None:
        self.viewer = None
        self.handle = None
        self.water_visible = True
        self.grid_visible = True
        self.suppressed = False

    def apply(self, viewer) -> None:
        """Adopt ``viewer`` (the initial renderer): background, no plane yet."""
        self.viewer = viewer
        self.handle = None
        viewer.set_background(BACKGROUND)

    def build(self, viewer, model_handles: Iterable[object]):
        """Prepare ``viewer`` (background + plane) without changing this scene's state.

        Used for a candidate renderer before it replaces the current one; returns the plane handle
        (None without a model or with the plane switched off).
        """
        viewer.set_background(BACKGROUND)
        extent = model_extent(model_handles)
        if extent is None or not self.water_visible:
            return None
        return viewer.add_mesh_arrays(
            water_mesh(*extent, grid=self.grid_visible), color=WATER_COLOUR, opacity=WATER_OPACITY,
            line_color=GRID_COLOUR, line_width=1, back_color=WATER_COLOUR, two_sided_shell=True,
            cull_backface=False, pickable=False, tags=TAG)

    def adopt(self, viewer, handle) -> None:
        """Take over a viewer prepared by :meth:`build` (the previous viewer is already gone)."""
        self.viewer = viewer
        self.handle = handle
        if handle is not None:
            handle.set_visible(not self.suppressed)

    def _drop(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None and not handle.removed:
            handle.remove()

    def sync(self, model_handles: Iterable[object]) -> None:
        """Rebuild the plane around the model in the current viewer; no model, no plane."""
        if self.viewer is None:
            return
        self._drop()
        self.handle = self.build(self.viewer, model_handles)
        if self.handle is not None:
            self.handle.set_visible(not self.suppressed)

    def set_suppressed(self, suppressed: bool) -> None:
        """Hide the plane while a computed wave surface stands in for the water."""
        self.suppressed = bool(suppressed)
        if self.handle is not None and not self.handle.removed:
            self.handle.set_visible(not self.suppressed)
            self.viewer.redraw()

    def fit(self, viewer) -> None:
        """Fit the view to the model only (the plane is hidden during the fit)."""
        handle = self.handle if viewer is self.viewer else None
        shown = handle is not None and not handle.removed and handle.visible
        if shown:
            handle.set_visible(False)
        try:
            viewer.fit_to_scene(redraw=not shown)
        finally:
            if shown:
                handle.set_visible(True)
                viewer.redraw()
