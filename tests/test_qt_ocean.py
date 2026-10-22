"""Ocean surface (U1): pure colour/ripple/state logic, waterline, idle behaviour, animation gating, fallback."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_qt

from anymarine.qtapp import ocean


def _box(low=(-5.0, -2.0, -1.0), high=(5.0, 2.0, 1.0)):
    x0, y0, z0 = low
    x1, y1, z1 = high
    pos = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], dtype=float)
    tri = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                    [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], dtype=np.uint32)
    return pos, tri


class _FakeHandle:
    def __init__(self, positions, triangles, transform=None):
        from any3dview import MeshArrays
        self.mesh = MeshArrays(positions=positions, triangles=triangles)
        self.transform = np.eye(4) if transform is None else transform
        self.visible = True
        self.removed = False


# ------------------------------------------------------------------------------------------ pure logic
def test_resolve_mode_env_wins_and_setting_default_on():
    assert ocean.resolve_mode(None, None) == "animated"
    assert ocean.resolve_mode(None, True) == "animated"
    assert ocean.resolve_mode(None, False) == "still"
    assert ocean.resolve_mode(None, "false") == "still"
    assert ocean.resolve_mode("static", True) == "static"
    assert ocean.resolve_mode("STILL", True) == "still"
    assert ocean.resolve_mode("animated", False) == "animated"
    assert ocean.resolve_mode("nonsense", False) == "still"


@pytest.mark.parametrize("args,expected", [
    (("animated", True, True, False, True, False), True),
    (("still", True, True, False, True, False), False),
    (("static", True, True, False, True, False), False),
    (("animated", False, True, False, True, False), False),
    (("animated", True, False, False, True, False), False),
    (("animated", True, True, True, True, False), False),
    (("animated", True, True, False, False, False), False),
    (("animated", True, True, False, True, True), False),
])
def test_should_animate_gate(args, expected):
    assert ocean.should_animate(*args) is expected


def test_half_size_is_quantised_and_stable():
    assert ocean.half_size(5.0) == ocean.half_size(6.0) == 20.0
    assert ocean.half_size(20.0) == 100.0 or ocean.half_size(20.0) == 50.0
    assert ocean.half_size(7.0) <= ocean.half_size(70.0)
    for span in (1.0, 12.0, 70.0, 333.0):
        h = ocean.half_size(span)
        assert h >= 3.0 * span and h >= 20.0
        mant = h / 10.0 ** np.floor(np.log10(h))
        assert any(abs(mant - m) < 1e-9 for m in (1.0, 2.0, 5.0))


def test_layout_is_flat_without_diagonals_and_grid_optional():
    layout = ocean.ocean_layout(np.array([-5.0, -2.0]), np.array([5.0, 2.0]), grid=True)
    pos = np.asarray(layout.mesh.positions)
    assert np.all(pos[:, 2] == 0.0)
    lines = np.asarray(layout.mesh.lines)
    d = pos[lines[:, 0], :2] - pos[lines[:, 1], :2]
    assert np.all((np.abs(d[:, 0]) < 1e-9) | (np.abs(d[:, 1]) < 1e-9))   # axis-aligned only
    assert ocean.ocean_layout(np.zeros(2), np.ones(2), grid=False).mesh.lines is None
    assert pos[:, 0].min() < -5.0 and pos[:, 0].max() > 5.0
    assert layout.mesh.triangle_count == ocean.SECTORS * (2 * ocean.RINGS - 1)
    assert layout.mesh.triangle_count <= 700


def test_far_field_fades_exactly_to_background_and_near_is_tinted():
    layout = ocean.ocean_layout(np.array([-5.0, -2.0]), np.array([5.0, 2.0]), grid=False)
    colours = ocean.to_hex(ocean.face_colours(layout, 3.0))
    far = layout.distance >= 0.85 * layout.half
    assert far.any() and all(colours[i] == ocean.BACKGROUND for i in np.flatnonzero(far))
    near = int(np.argmin(layout.distance))
    assert colours[near] != ocean.BACKGROUND
    outermost = int(np.argmax(layout.distance))
    assert colours[outermost] == ocean.BACKGROUND      # the disc edge is invisible against the background


def test_ripple_is_small_slow_bounded_and_colour_only():
    assert min(ocean.RIPPLE_PERIODS) >= 8.0 and ocean.FPS <= 10.0
    assert ocean.RIPPLE_AMPLITUDE <= 0.05
    layout = ocean.ocean_layout(np.array([-5.0, -2.0]), np.array([5.0, 2.0]), grid=False)
    x, y = layout.centroids[:, 0], layout.centroids[:, 1]
    for t in (0.0, 1.7, 9.0):
        assert np.all(np.abs(ocean.ripple(x, y, t, layout.span)) <= 1.0 + 1e-12)
    a = ocean.face_colours(layout, 0.0, motion=True)
    b = ocean.face_colours(layout, 4.0, motion=True)
    still = ocean.face_colours(layout, 0.0, motion=False)
    assert np.max(np.abs(a - still)) < 0.05 and np.max(np.abs(a - b)) < 0.08
    assert not np.array_equal(a, b)
    assert np.array_equal(ocean.face_colours(layout, 0.0, motion=False), ocean.face_colours(layout, 50.0, motion=False))
    # one second of motion changes colours far less than the whole range (slow)
    c = ocean.face_colours(layout, 0.2, motion=True)
    assert np.max(np.abs(a - c)) < 0.01


def test_waterline_is_exactly_at_z_zero_and_follows_transform():
    pos, tri = _box()
    mesh = ocean.waterline_mesh([_FakeHandle(pos, tri)])
    p = np.asarray(mesh.positions)
    assert np.all(p[:, 2] == 0.0) and len(mesh.triangles) == 0
    assert p[:, 0].min() == pytest.approx(-5.0) and p[:, 0].max() == pytest.approx(5.0)
    assert p[:, 1].min() == pytest.approx(-2.0) and p[:, 1].max() == pytest.approx(2.0)
    lift = np.eye(4)
    lift[2, 3] = 1.5                      # hull lifted clear of the water: no waterline
    assert ocean.waterline_mesh([_FakeHandle(pos, tri, lift)]) is None
    shift = np.eye(4)
    shift[0, 3] = 10.0
    q = np.asarray(ocean.waterline_mesh([_FakeHandle(pos, tri, shift)]).positions)
    assert q[:, 0].min() == pytest.approx(5.0)
    hidden = _FakeHandle(pos, tri)
    hidden.visible = False
    assert ocean.waterline_mesh([hidden]) is None


# -------------------------------------------------------------------------------------------- Qt scene
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def scene_env(qapp, monkeypatch):
    from any3dview import MeshArrays, create_viewer
    from any3dview.qt import QtViewerHostAdapter
    from anymarine.qtapp.marine_scene import MarineScene

    monkeypatch.delenv(ocean.ENV_VAR, raising=False)
    viewer = create_viewer(None, "software", host=QtViewerHostAdapter())
    viewer.resize(640, 400)
    viewer.show()
    qapp.processEvents()
    pos, tri = _box()
    hull = viewer.add_mesh_arrays(MeshArrays(positions=pos, triangles=tri), color="#c0703a")
    scene = MarineScene()
    scene.apply(viewer)
    scene.mode_override = "animated"
    scene.sync([hull])
    viewer.set_view(-35.0, 20.0)
    viewer.fit_to_scene()
    qapp.processEvents()
    yield scene, viewer, hull, qapp
    scene._drop()
    viewer.close()


def _frame(viewer):
    image = viewer.grab().toImage()
    return bytes(image.constBits())


pytestmark_qt = requires_qt


@requires_qt
def test_static_mode_reproduces_previous_plane(scene_env):
    from anymarine.qtapp.marine_scene import GRID_COLOUR, TAG, WATER_COLOUR, WATER_OPACITY, model_extent, water_mesh
    scene, viewer, hull, _ = scene_env
    scene.mode_override = "static"
    scene.sync([hull])
    expected = water_mesh(*model_extent([hull]), grid=True)
    got = scene.handle.mesh
    assert np.array_equal(np.asarray(got.positions), np.asarray(expected.positions))
    assert np.array_equal(np.asarray(got.triangles), np.asarray(expected.triangles))
    assert np.array_equal(np.asarray(got.lines), np.asarray(expected.lines))
    entry = viewer._entries[id(scene.handle)]["appearance"]
    assert entry["color"] == WATER_COLOUR and entry["opacity"] == WATER_OPACITY and entry["line_color"] == GRID_COLOUR
    assert entry["face_colors"] is None and entry["lit"] is True
    assert scene.updates == 0 and scene._timer is None and scene.diagnostics == []
    assert TAG in viewer._entries[id(scene.handle)]["tags"]


@requires_qt
def test_ocean_geometry_flat_waterline_present_and_surface_not_pickable(scene_env):
    scene, viewer, hull, qapp = scene_env
    rec = scene._records[id(scene.handle)]
    assert np.all(np.asarray(scene.handle.mesh.positions)[:, 2] == 0.0)
    assert len(rec["extras"]) == 1
    assert np.all(np.asarray(rec["extras"][0].mesh.positions)[:, 2] == 0.0)
    assert viewer._entries[id(scene.handle)]["appearance"]["pickable"] is False
    for _ in range(3):
        scene._clock = lambda base=scene._clock0: base + 3.0 * (_ + 1)
        assert scene.tick()
        assert np.all(np.asarray(scene.handle.mesh.positions)[:, 2] == 0.0)   # ripples are colour only
    assert scene.updates == 3
    assert sum(1 for e in viewer._entries.values() if e["handle"] is scene.handle) == 1
    water_entries = [e for e in viewer._entries.values() if "anymarine-still-water" in e["tags"]]
    assert len(water_entries) == 2        # surface + waterline, no leaked old surfaces


@requires_qt
def test_idle_frames_are_identical_no_flicker_and_no_redraws(scene_env):
    scene, viewer, hull, qapp = scene_env
    scene._stop_timer()                      # animation paused
    frames = []
    count = {"n": 0}
    original = viewer.redraw

    def counting(*a, **k):
        count["n"] += 1
        return original(*a, **k)

    viewer.redraw = counting
    try:
        baseline = _frame(viewer)
        count["n"] = 0
        for _ in range(100):
            frames.append(_frame(viewer))
        assert all(f == baseline for f in frames)            # 100 consecutive idle frames, diff 0
        count["n"] = 0
        scene.mode_override = "still"
        scene._update_timer()
        import time
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            qapp.processEvents()
        assert count["n"] == 0 and scene.updates == 0 and scene._timer is None
    finally:
        viewer.redraw = original


@requires_qt
def test_animation_gate_hidden_suppressed_minimised_and_still(scene_env):
    scene, viewer, hull, qapp = scene_env
    scene._clock = lambda: scene._clock0 + 5.0
    assert scene._timer is not None and scene._timer.isActive()
    assert scene.animating()
    scene.set_suppressed(True)
    assert scene._timer is None and not scene.animating() and scene.tick() is False
    scene.set_suppressed(False)
    assert scene._timer is not None and scene.animating()
    viewer.hide()
    qapp.processEvents()
    before = scene.updates
    assert not scene.animating() and scene.tick() is False and scene.updates == before
    viewer.show()
    qapp.processEvents()
    assert scene.animating() and scene.tick() is True
    scene.mode_override = "still"
    scene._update_timer()
    assert scene._timer is None and scene.tick() is False
    scene.mode_override = "animated"
    scene.water_visible = False
    scene.sync([hull])
    assert scene.handle is None and scene._timer is None and not scene.animating()


@requires_qt
def test_unchanged_colours_do_not_redraw(scene_env):
    scene, viewer, hull, qapp = scene_env
    scene._clock = lambda: scene._clock0 + 2.0
    assert scene.tick() is True
    handle = scene.handle
    assert scene.tick() is False and scene.handle is handle         # same time -> same colours -> nothing


@requires_qt
def test_fit_hides_surface_and_waterline_then_restores(scene_env):
    scene, viewer, hull, qapp = scene_env
    parts = scene._parts()
    assert len(parts) == 2
    seen = []
    original = viewer.fit_to_scene

    def spy(**kw):
        seen.append([p.visible for p in parts])
        return original(**kw)

    viewer.fit_to_scene = spy
    scene.fit(viewer)
    assert seen == [[False, False]] and all(p.visible for p in parts)


@requires_qt
def test_forced_failure_falls_back_to_static_plane(scene_env):
    from anymarine.qtapp.marine_scene import TAG, model_extent, water_mesh
    scene, viewer, hull, qapp = scene_env
    original = viewer.add_mesh_arrays

    def broken(mesh, **kwargs):
        if kwargs.get("face_colors") is not None:
            raise RuntimeError("no per-face colours on this renderer")
        return original(mesh, **kwargs)

    viewer.add_mesh_arrays = broken
    try:
        scene.sync([hull])                    # never raises
    finally:
        pass
    assert scene.handle is not None and scene.diagnostics
    assert "static plane" in scene.diagnostics[-1] and "RuntimeError" in scene.diagnostics[-1]
    expected = water_mesh(*model_extent([hull]), grid=True)
    assert np.array_equal(np.asarray(scene.handle.mesh.positions), np.asarray(expected.positions))
    assert scene._records == {} and scene._timer is None and scene.tick() is False
    # a failure while adding the waterline also leaves no half-built surface behind
    def broken_lines(mesh, **kwargs):
        if len(mesh.triangles) == 0:
            raise RuntimeError("lines-only mesh rejected")
        return original(mesh, **kwargs)
    viewer.add_mesh_arrays = broken_lines
    scene.sync([hull])
    assert "lines-only" in scene.diagnostics[-1]
    surfaces = [e for e in viewer._entries.values() if TAG in e["tags"] and not e["handle"].removed]
    assert len(surfaces) == 1             # only the static plane
    viewer.add_mesh_arrays = original


@requires_qt
def test_animation_failure_stops_motion_and_records_diagnostic(scene_env):
    scene, viewer, hull, qapp = scene_env
    original = viewer.add_mesh_arrays

    def broken(mesh, **kwargs):
        raise RuntimeError("gpu lost")

    scene._clock = lambda: scene._clock0 + 4.0
    old = scene.handle
    viewer.add_mesh_arrays = broken
    assert scene.tick() is False
    viewer.add_mesh_arrays = original
    assert scene.handle is old and not old.removed and scene.mode == "still" and scene._timer is None
    assert any("gpu lost" in d for d in scene.diagnostics)


@requires_qt
def test_motion_toggle_persists(qapp, monkeypatch, tmp_path):
    from PySide6 import QtCore
    real = QtCore.QSettings
    monkeypatch.setattr(QtCore, "QSettings", lambda *a: real(str(tmp_path / "s.ini"), real.Format.IniFormat))
    monkeypatch.delenv(ocean.ENV_VAR, raising=False)
    assert ocean.read_mode() == "animated"
    ocean.store_motion(False)
    assert ocean.read_mode() == "still"
    assert (tmp_path / "s.ini").is_file()
    monkeypatch.setenv(ocean.ENV_VAR, "animated")
    assert ocean.read_mode() == "animated"       # env wins
    monkeypatch.delenv(ocean.ENV_VAR)
    ocean.store_motion(True)
    assert ocean.read_mode() == "animated"
