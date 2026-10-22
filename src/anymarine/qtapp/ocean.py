"""Ocean-surface look for the 3D view: pure, Qt-free helpers (mesh, colours, ripples, waterline, state).

This is scenery, not a wave model: the surface is a flat patch at z = 0 whose per-face colours are baked on the
CPU (depth tint, a distance-based Fresnel-style mix toward a sky tone, horizon haze into the background) and
gently modulated by a few very slow, low-amplitude colour ripples.  Ripples never move geometry, so the
waterline stays exactly at z = 0, and they make no claim about any sea state.

Fresnel approximation: the viewer's camera is not consulted (a changed camera would force a colour rebuild
and, with ANY3dView, a handle replacement); the grazing-angle effect is approximated from the distance to the
model centre, which grows with the view obliquity for the usual orbit cameras.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from any3dview import MeshArrays

BACKGROUND = "#e7eef5"
SKY_TONE = "#cfe1ef"
SHALLOW = "#58a9bd"      # blue-green near the model
DEEP = "#2f6f9d"         # deeper blue farther out
GRID_LINE = BACKGROUND   # also the patch-boundary loop colour: invisible against the background
WATERLINE = "#1d4a66"
OPACITY = 0.62
RINGS = 10               # radial bands of the disc (software-renderer cost scales with faces)
SECTORS = 32             # angular sectors
GRID_LINES = 10          # grid lines per direction across the grid radius (step = half / 10)
FPS = 5.0                # animation update rate (cap is 10 fps)
RIPPLE_AMPLITUDE = 0.035  # relative lightness, +-
RIPPLE_PERIODS = (11.0, 15.0, 21.0)   # seconds, all >= 8 s
ENV_VAR = "ANYMARINE_OCEAN"
SETTING_KEY = "anymarine/ocean_motion"
MODES = ("static", "still", "animated")


# --------------------------------------------------------------------------------------------- mode / state
def resolve_mode(env_value: Optional[str], setting: object) -> str:
    """``static`` (old translucent plane), ``still`` (new look, no motion) or ``animated``.

    The environment variable ANYMARINE_OCEAN wins when it names a mode; otherwise the stored
    ``anymarine/ocean_motion`` boolean (default on) selects animated or still.
    """
    value = (env_value or "").strip().casefold()
    if value in MODES:
        return value
    if isinstance(setting, str):
        off = setting.strip().casefold() in ("false", "0", "no", "off")
    else:
        off = setting is not None and not bool(setting)
    return "still" if off else "animated"


def read_mode() -> str:
    """Mode from the environment and the user's QSettings (a settings failure means the default)."""
    setting: object = None
    try:
        from PySide6.QtCore import QSettings
        setting = QSettings("ANYmarine", "ANYmarine").value(SETTING_KEY, True)
    except Exception:
        setting = None
    return resolve_mode(os.environ.get(ENV_VAR), setting)


def store_motion(enabled: bool) -> None:
    """Persist the motion switch (the View menu entry is a mainwindow-level follow-up)."""
    from PySide6.QtCore import QSettings
    settings = QSettings("ANYmarine", "ANYmarine")
    settings.setValue(SETTING_KEY, bool(enabled))
    settings.sync()


def should_animate(mode: str, plane_live: bool, plane_visible: bool, suppressed: bool,
                   viewer_visible: bool, window_minimised: bool) -> bool:
    """Animation gate: only the animated mode, with a visible plane, a visible viewer and a window on screen."""
    return (mode == "animated" and plane_live and plane_visible and not suppressed
            and viewer_visible and not window_minimised)


# ------------------------------------------------------------------------------------------------ geometry
def _nice_ceil(value: float) -> float:
    """Smallest 1/2/5 x 10^k at or above ``value``."""
    value = max(value, 1e-9)
    power = 10.0 ** math.floor(math.log10(value))
    for factor in (1.0, 2.0, 5.0, 10.0):
        if value <= factor * power * (1 + 1e-12):
            return factor * power
    return 10.0 * power


def half_size(span: float) -> float:
    """Fixed-policy half edge: 3 x model span, floor 20 m, quantised to 1/2/5 steps (stable across examples)."""
    return _nice_ceil(max(3.0 * float(span), 20.0))


@dataclass(frozen=True)
class OceanLayout:
    """Geometry and colour inputs of one surface patch (all plain numpy, no Qt)."""

    centre: np.ndarray          # model plan centre (x, y)
    half: float                 # disc radius
    step: float                 # grid step
    span: float                 # model span (ripple length scale)
    mesh: MeshArrays
    centroids: np.ndarray       # (faces, 2) world x, y of the face centroids
    distance: np.ndarray        # (faces,) distance from ``centre``


def ocean_layout(low: np.ndarray, high: np.ndarray, *, grid: bool) -> OceanLayout:
    """Flat disc (rings x sectors, about 600 faces) around the model centre, plus optional faint grid lines.

    A disc of ~600 faces keeps the software renderer within about the cost of the old plane; the colours
    do the work (fade into the background), so the coarse faces are not visible as such.
    """
    low = np.asarray(low, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    centre = 0.5 * (low + high)
    span = float(max(high[0] - low[0], high[1] - low[1], 1.0))
    half = half_size(span)
    step = half / GRID_LINES
    radii = half * np.arange(1, RINGS + 1) / RINGS
    angles = 2.0 * math.pi * np.arange(SECTORS) / SECTORS
    ring_xy = np.stack((np.cos(angles), np.sin(angles)), axis=1)                    # (S, 2)
    outer = centre[None, None, :] + radii[:, None, None] * ring_xy[None, :, :]      # (R, S, 2)
    xy = np.concatenate((centre[None, :], outer.reshape(-1, 2)))
    positions = np.column_stack((xy, np.zeros(len(xy))))
    tris = []
    j = np.arange(SECTORS)
    jn = (j + 1) % SECTORS
    tris.append(np.column_stack((np.zeros(SECTORS, dtype=np.int64), 1 + j, 1 + jn)))   # centre fan
    for ring in range(1, RINGS):
        inner0, outer0 = 1 + (ring - 1) * SECTORS, 1 + ring * SECTORS
        a, b, c, d = inner0 + j, inner0 + jn, outer0 + jn, outer0 + j
        tris.append(np.column_stack((a, c, b)))      # counter-clockwise seen from +z (front face up)
        tris.append(np.column_stack((a, d, c)))
    triangles = np.concatenate(tris).astype(np.uint32)
    lines = None
    if grid:
        positions, lines = _grid_lines(positions, centre, half, step)
    mesh = MeshArrays(positions=positions, triangles=triangles, lines=lines)
    tri_pos = positions[triangles]                       # (faces, 3, 3)
    centroids = tri_pos[:, :, :2].mean(axis=1)
    distance = np.hypot(centroids[:, 0] - centre[0], centroids[:, 1] - centre[1])
    return OceanLayout(centre, half, step, span, mesh, centroids, distance)


def _grid_lines(positions: np.ndarray, centre: np.ndarray, half: float, step: float):
    """Faint axis-aligned grid lines on whole steps within 0.4 x half of the centre (never quad diagonals).

    Their end points are appended after the disc vertices (the disc faces do not use them).
    """
    limit = 0.4 * half
    cx, cy = round(centre[0] / step) * step, round(centre[1] / step) * step
    extra, segments = [], []
    base = len(positions)
    for k in range(-int(limit // step), int(limit // step) + 1):
        for horizontal in (True, False):
            offset = (cy if horizontal else cx) + k * step - (centre[1] if horizontal else centre[0])
            if abs(offset) >= limit:
                continue
            reach = math.sqrt(limit * limit - offset * offset)
            if horizontal:
                p0, p1 = (centre[0] - reach, centre[1] + offset), (centre[0] + reach, centre[1] + offset)
            else:
                p0, p1 = (centre[0] + offset, centre[1] - reach), (centre[0] + offset, centre[1] + reach)
            segments.append((base + len(extra), base + len(extra) + 1))
            extra.extend((p0, p1))
    if not segments:
        return positions, None
    extra_xyz = np.column_stack((np.asarray(extra), np.zeros(len(extra))))
    return np.concatenate((positions, extra_xyz)), np.asarray(segments, dtype=np.uint32)


# -------------------------------------------------------------------------------------------------- colours
def _rgb(hex_colour: str) -> np.ndarray:
    value = hex_colour.lstrip("#")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64) / 255.0


def _smooth(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# Plane-wave directions, spatial wavelengths (x model span) and phase offsets of the three ripple components.
_DIRECTIONS = np.array([[0.94, 0.34], [-0.55, 0.83], [0.20, -0.98]])
_WAVELENGTH = np.array([1.6, 2.4, 3.6])
_PHASE = np.array([0.4, 2.1, 4.3])


def ripple(x: np.ndarray, y: np.ndarray, t: float, span: float) -> np.ndarray:
    """Slow, small colour ripple field in [-1, 1] (decorative; not a wave model, no physical wavelength)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    total = np.zeros_like(x)
    for direction, wavelength, period, phase in zip(_DIRECTIONS, _WAVELENGTH, RIPPLE_PERIODS, _PHASE):
        k = 2.0 * math.pi / (wavelength * max(span, 1.0))
        total += np.sin(k * (direction[0] * x + direction[1] * y) - 2.0 * math.pi * t / period + phase)
    return total / len(_WAVELENGTH)


def face_colours(layout: OceanLayout, t: float = 0.0, *, motion: bool = True) -> np.ndarray:
    """(faces, 3) float RGB in [0, 1] for time ``t`` seconds.

    depth tint (shallow near the model to deep) -> ripple lightness -> Fresnel-style mix toward the sky tone
    with distance -> horizon haze into BACKGROUND, reaching it exactly before the patch edge.
    """
    u = np.clip(layout.distance / layout.half, 0.0, 2.0)
    base = _rgb(SHALLOW)[None, :] * (1.0 - _smooth(0.0, 0.6, u))[:, None] + _rgb(DEEP)[None, :] * _smooth(0.0, 0.6, u)[:, None]
    fresnel = 0.05 + 0.95 * _smooth(0.15, 0.9, u) ** 2
    if motion:
        wave = ripple(layout.centroids[:, 0], layout.centroids[:, 1], t, layout.span)
        base = base * (1.0 + RIPPLE_AMPLITUDE * wave * (1.0 - fresnel))[:, None]
    mixed = base * (1.0 - fresnel)[:, None] + _rgb(SKY_TONE)[None, :] * fresnel[:, None]
    haze = _smooth(0.25, 0.8, u)
    out = mixed * (1.0 - haze)[:, None] + _rgb(BACKGROUND)[None, :] * haze[:, None]
    return np.clip(out, 0.0, 1.0)


def to_hex(rgb: np.ndarray) -> list[str]:
    values = np.rint(np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    return ["#%02x%02x%02x" % (r, g, b) for r, g, b in values.tolist()]


# ------------------------------------------------------------------------------------------------ waterline
def waterline_mesh(handles: Iterable[object], *, tag: str = "") -> Optional[MeshArrays]:
    """Line mesh of the intersection of the visible model triangles with z = 0 (exactly z = 0), or None."""
    segments = []
    for handle in handles:
        if handle is None or getattr(handle, "removed", False) or not getattr(handle, "visible", True):
            continue
        mesh = handle.mesh
        if mesh.triangles is None or len(mesh.triangles) == 0 or len(mesh.positions) == 0:
            continue
        transform = np.asarray(handle.transform, dtype=np.float64)
        world = np.asarray(mesh.positions, dtype=np.float64) @ transform[:3, :3].T + transform[:3, 3]
        tri = world[np.asarray(mesh.triangles, dtype=np.int64)]       # (n, 3, 3)
        below = tri[:, :, 2] < 0.0
        crossing = below.any(axis=1) & ~below.all(axis=1)
        if not crossing.any():
            continue
        tri, below = tri[crossing], below[crossing]
        points = np.zeros((len(tri), 2, 3))
        counts = np.zeros(len(tri), dtype=np.int64)
        rows = np.arange(len(tri))
        for i, j in ((0, 1), (1, 2), (2, 0)):
            cross = below[:, i] != below[:, j]
            zi, zj = tri[:, i, 2], tri[:, j, 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                s = np.where(cross, -zi / (zj - zi), 0.0)
            p = tri[:, i, :] + (tri[:, j, :] - tri[:, i, :]) * s[:, None]
            take = cross & (counts < 2)
            points[rows[take], counts[take]] = p[take]
            counts[take] += 1
        segments.append(points[counts == 2])
    if not segments:
        return None
    pts = np.concatenate(segments)
    if len(pts) == 0:
        return None
    positions = pts.reshape(-1, 3).copy()
    positions[:, 2] = 0.0
    lines = np.arange(len(positions), dtype=np.uint32).reshape(-1, 2)
    return MeshArrays(positions=positions, triangles=np.zeros((0, 3), dtype=np.uint32), lines=lines)
