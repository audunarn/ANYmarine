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


# ------------------------------------------------------------------------------------------ review round 1 (Codex)
def _water_entries(viewer):
    from anymarine.qtapp.marine_scene import TAG
    return [e for e in viewer._entries.values() if TAG in e["tags"] and not e["handle"].removed]


def _timer_active(scene) -> bool:
    return scene._timer is not None and scene._timer.isActive()


def _assert_static_restored(scene, viewer, hull):
    from anymarine.qtapp.marine_scene import model_extent, water_mesh
    expected = water_mesh(*model_extent([hull]), grid=True)
    assert scene.handle is not None and not scene.handle.removed
    assert np.array_equal(np.asarray(scene.handle.mesh.positions), np.asarray(expected.positions))
    assert viewer._entries[id(scene.handle)]["appearance"]["face_colors"] is None
    water = _water_entries(viewer)
    assert len(water) == 1 and water[0]["handle"] is scene.handle      # no ocean surface, no waterline left
    assert scene._records == {} and not _timer_active(scene) and not scene.animating()


def _failing_tick(scene, viewer, hull, how, monkeypatch):
    from any3dview.retained import MeshHandle
    scene._clock = lambda: scene._clock0 + 4.0
    old = scene.handle
    poisoned: set = set()
    real_set_visible, real_remove = MeshHandle.set_visible, MeshHandle.remove
    original_add = viewer.add_mesh_arrays
    if how == "colours":
        def bad(*a, **k):
            raise RuntimeError("colour boom")
        monkeypatch.setattr(ocean, "face_colours", bad)
    elif how == "add":
        def wrapped(mesh, **kwargs):
            if kwargs.get("face_colors") is not None:
                raise RuntimeError("gpu lost")
            return original_add(mesh, **kwargs)
        monkeypatch.setattr(viewer, "add_mesh_arrays", wrapped)
    elif how == "set_visible":          # the NEW surface is created, then set_visible fails
        def wrapped(mesh, **kwargs):
            handle = original_add(mesh, **kwargs)
            if kwargs.get("face_colors") is not None:
                poisoned.add(id(handle))
            return handle
        monkeypatch.setattr(viewer, "add_mesh_arrays", wrapped)

        def set_visible(self, visible):
            if id(self) in poisoned:
                raise RuntimeError("set_visible boom")
            return real_set_visible(self, visible)
        monkeypatch.setattr(MeshHandle, "set_visible", set_visible)
    else:                               # the OLD surface cannot be removed
        def remove(self):
            if self is old and not poisoned:
                poisoned.add(1)           # transient failure: the first attempt only
                raise RuntimeError("remove boom")
            return real_remove(self)
        monkeypatch.setattr(MeshHandle, "remove", remove)
    return scene.tick()                 # must never raise


@requires_qt
@pytest.mark.parametrize("how", ["colours", "set_visible", "add", "remove_old"])
def test_animation_failure_anywhere_restores_static_plane_and_does_not_repeat(scene_env, how, monkeypatch):
    scene, viewer, hull, qapp = scene_env
    assert _failing_tick(scene, viewer, hull, how, monkeypatch) is False
    monkeypatch.undo()
    assert scene.diagnostics and any("animation" in d for d in scene.diagnostics)
    _assert_static_restored(scene, viewer, hull)
    # no repeat on later ticks / timer events
    scene._clock = lambda: scene._clock0 + 40.0
    before = len(scene.diagnostics)
    count = {"n": 0}
    original = viewer.add_mesh_arrays

    def counting(mesh, **kwargs):
        count["n"] += 1
        return original(mesh, **kwargs)
    viewer.add_mesh_arrays = counting
    for _ in range(3):
        assert scene.tick() is False
    viewer.add_mesh_arrays = original
    assert count["n"] == 0 and len(scene.diagnostics) == before and scene.updates == 0
    scene.sync([hull])                              # a later rebuild stays on the static plane
    _assert_static_restored(scene, viewer, hull)


@requires_qt
def test_timer_is_inactive_while_hidden_minimised_closed_and_destroyed(qapp, scene_env):
    scene, viewer, hull, _ = scene_env
    assert _timer_active(scene)
    viewer.hide()
    qapp.processEvents()
    assert not _timer_active(scene)
    viewer.show()
    qapp.processEvents()
    assert _timer_active(scene)
    viewer.showMinimized()
    qapp.processEvents()
    assert not _timer_active(scene)
    viewer.showNormal()
    qapp.processEvents()
    assert _timer_active(scene)


def _own_env(qapp, monkeypatch):
    from any3dview import MeshArrays, create_viewer
    from any3dview.qt import QtViewerHostAdapter
    from anymarine.qtapp.marine_scene import MarineScene
    monkeypatch.delenv(ocean.ENV_VAR, raising=False)
    viewer = create_viewer(None, "software", host=QtViewerHostAdapter())
    viewer.resize(320, 200)
    viewer.show()
    qapp.processEvents()
    pos, tri = _box()
    hull = viewer.add_mesh_arrays(MeshArrays(positions=pos, triangles=tri), color="#c0703a")
    scene = MarineScene()
    scene.apply(viewer)
    scene.mode_override = "animated"
    scene.sync([hull])
    return scene, viewer, hull


@requires_qt
def test_timer_is_inactive_after_viewer_close(qapp, monkeypatch):
    scene, viewer, hull = _own_env(qapp, monkeypatch)
    assert _timer_active(scene)
    viewer.close()
    qapp.processEvents()
    assert not _timer_active(scene)
    scene.shutdown()


@requires_qt
def test_timer_stops_when_viewer_is_destroyed_and_after_shutdown(qapp, monkeypatch):
    import shiboken6
    from any3dview import MeshArrays, create_viewer
    from any3dview.qt import QtViewerHostAdapter
    scene, viewer, hull = _own_env(qapp, monkeypatch)
    pos, tri = _box()
    assert _timer_active(scene)
    shiboken6.delete(viewer)
    qapp.processEvents()
    assert not _timer_active(scene)
    assert scene.tick() is False                      # a dead viewer is not touched
    # explicit teardown also releases the timer
    viewer2 = create_viewer(None, "software", host=QtViewerHostAdapter())
    viewer2.show()
    qapp.processEvents()
    hull2 = viewer2.add_mesh_arrays(MeshArrays(positions=pos, triangles=tri), color="#c0703a")
    scene.apply(viewer2)
    scene.sync([hull2])
    assert _timer_active(scene)
    scene.shutdown()
    assert scene._timer is None and scene.handle is None
    viewer2.close()


def _waterline_positions(scene):
    return np.asarray(scene._records[id(scene.handle)]["extras"][0].mesh.positions)


@requires_qt
def test_waterline_follows_hull_transform_on_refresh_and_on_redraw(scene_env):
    scene, viewer, hull, qapp = scene_env
    before = _waterline_positions(scene).copy()
    move = np.eye(4)
    move[0, 3] = 3.0
    hull.set_transform(move)
    assert scene.refresh_waterline() is True
    after = _waterline_positions(scene)
    expected = np.asarray(ocean.waterline_mesh([hull]).positions)
    assert np.allclose(np.sort(after, axis=0), np.sort(expected, axis=0)) and np.all(after[:, 2] == 0.0)
    assert not np.allclose(after[:, 0].mean(), before[:, 0].mean())
    assert scene.refresh_waterline() is False                       # unchanged: nothing rebuilt
    assert len(_water_entries(viewer)) == 2                         # surface + one waterline, no leak
    # without any explicit call: the next painted frame brings the line along
    move[0, 3] = -2.0
    hull.set_transform(move)
    viewer.redraw()
    viewer.grab()
    qapp.processEvents()
    expected = np.asarray(ocean.waterline_mesh([hull]).positions)
    assert np.allclose(np.sort(_waterline_positions(scene), axis=0), np.sort(expected, axis=0))
    assert len(_water_entries(viewer)) == 2
    # hull rises out of the water: the line disappears
    lift = np.eye(4)
    lift[2, 3] = 5.0
    hull.set_transform(lift)
    assert scene.refresh_waterline() is True
    assert scene._records[id(scene.handle)]["extras"] == [] and len(_water_entries(viewer)) == 1


@requires_qt
def test_renderer_swap_deferred_deletion_of_old_viewer_keeps_new_viewer(qapp, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from any3dview import MeshArrays, create_viewer
    from any3dview.qt import QtViewerHostAdapter
    scene, old, hull = _own_env(qapp, monkeypatch)
    pos, tri = _box()
    new = create_viewer(None, "software", host=QtViewerHostAdapter())
    new.resize(320, 200)
    new.show()
    qapp.processEvents()
    hull2 = new.add_mesh_arrays(MeshArrays(positions=pos, triangles=tri), color="#c0703a")
    old.deleteLater()                                   # what the old renderer's destroy() schedules
    handle = scene.build(new, [hull2])
    scene.adopt(new, handle)                            # candidate adopted before the deferred delete runs
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert scene.viewer is new
    assert scene.handle is handle and handle is not None
    assert _timer_active(scene)
    assert scene.tick() is not None and scene.viewer is new
    # the new viewer's own destruction still clears the scene
    import shiboken6
    shiboken6.delete(new)
    qapp.processEvents()
    assert scene.viewer is None and not _timer_active(scene)
    scene.shutdown()
