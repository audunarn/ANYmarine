"""In-place ocean colour updates (ANY3dView ``capabilities.face_color_update``) with the replace-mesh fallback."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_qt

from anymarine.qtapp import ocean

SIBLING_SRC = Path(r"C:\Github\wt\any3dview-face-colors\src")

_POS = np.array([[-5, -2, -1], [5, -2, -1], [5, 2, -1], [-5, 2, -1], [-5, -2, 1], [5, -2, 1], [5, 2, 1], [-5, 2, 1]], float)
_TRI = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6],
                 [1, 2, 6], [1, 6, 5], [3, 0, 4], [3, 4, 7]], np.uint32)


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _env(qapp, monkeypatch):
    from any3dview import MeshArrays, create_viewer
    from any3dview.qt import QtViewerHostAdapter
    from anymarine.qtapp.marine_scene import MarineScene
    monkeypatch.delenv(ocean.ENV_VAR, raising=False)
    viewer = create_viewer(None, "software", host=QtViewerHostAdapter())
    viewer.resize(320, 200)
    viewer.show()
    qapp.processEvents()
    hull = viewer.add_mesh_arrays(MeshArrays(positions=_POS, triangles=_TRI), color="#c0703a")
    scene = MarineScene()
    scene.apply(viewer)
    scene.mode_override = "animated"
    scene.sync([hull])
    return scene, viewer, hull


@pytest.fixture()
def inplace(qapp, monkeypatch):
    """A software viewer that claims ``face_color_update``; handles record every update_face_colors call."""
    from any3dview.retained import MeshHandle
    calls: list = []

    def update(self, face_colors):
        calls.append((self, np.array(face_colors)))

    scene, viewer, hull = _env(qapp, monkeypatch)
    monkeypatch.setattr(type(viewer), "capabilities", property(lambda self: SimpleNamespace(face_color_update=True)),
                        raising=False)
    monkeypatch.setattr(MeshHandle, "update_face_colors", update, raising=False)
    adds = {"n": 0}
    original = viewer.add_mesh_arrays

    def counting(mesh, **kwargs):
        adds["n"] += 1
        return original(mesh, **kwargs)
    monkeypatch.setattr(viewer, "add_mesh_arrays", counting)
    yield scene, viewer, hull, calls, adds
    scene._drop()
    viewer.close()


def _at(scene, seconds):
    scene._clock = lambda: scene._clock0 + seconds


def _hex_of(rgb8):
    return ["#%02x%02x%02x" % tuple(v) for v in rgb8.tolist()]


def test_to_uint8_matches_hex_quantisation():
    rng = np.random.default_rng(3)
    rgb = np.concatenate([rng.random((500, 3)) * 1.2 - 0.1, [[0.5 / 255, 0.5, 1.0]]])   # out-of-range and ties
    values = ocean.to_uint8(rgb)
    assert values.dtype == np.uint8 and values.shape == rgb.shape
    assert ocean.to_hex(rgb) == _hex_of(values)


@requires_qt
def test_tick_updates_in_place_without_new_handle(inplace):
    scene, viewer, hull, calls, adds = inplace
    handle, key = scene.handle, id(scene.handle)
    layout = scene._records[key]["layout"]
    adds["n"] = 0
    for step, seconds in enumerate((3.0, 6.0, 9.0), 1):
        _at(scene, seconds)
        assert scene.tick() is True
        assert len(calls) == step and scene.updates == step
        assert calls[-1][0] is handle and scene.handle is handle and not handle.removed
        expected = ocean.to_uint8(ocean.face_colours(layout, seconds, motion=True))
        assert calls[-1][1].dtype == np.uint8 and np.array_equal(calls[-1][1], expected)
        assert list(scene._records) == [key] and np.array_equal(scene._records[key]["colours"], expected)
        # same quantisation as the hex (replace-mesh) path
        assert ocean.to_hex(ocean.face_colours(layout, seconds, motion=True)) == _hex_of(calls[-1][1])
    assert adds["n"] == 0
    assert scene.tick() is False and len(calls) == 3           # unchanged quantised colours: skipped


@requires_qt
def test_inplace_keeps_visibility_and_gate(inplace):
    scene, viewer, hull, calls, adds = inplace
    _at(scene, 4.0)
    scene.suppressed = True
    assert scene.tick() is False and calls == []
    scene.suppressed = False
    assert scene.tick() is True and scene.handle.visible


@requires_qt
def test_inplace_first_step_compares_with_built_colours(inplace):
    scene, viewer, hull, calls, adds = inplace
    rec = scene._records[id(scene.handle)]
    rec["colours"] = tuple(ocean.to_hex(ocean.face_colours(rec["layout"], 3.0, motion=True)))   # as built (hex)
    _at(scene, 3.0)
    assert scene.tick() is False and calls == []
    _at(scene, 6.0)
    assert scene.tick() is True and len(calls) == 1


@requires_qt
@pytest.mark.parametrize("error", [RuntimeError("gpu lost"), ValueError("refused"), NotImplementedError("no")])
def test_inplace_failure_falls_back_to_static_plane(inplace, monkeypatch, error):
    from any3dview.retained import MeshHandle
    from anymarine.qtapp.marine_scene import TAG, model_extent, water_mesh
    scene, viewer, hull, calls, adds = inplace

    def bad(self, face_colors):
        raise error
    monkeypatch.setattr(MeshHandle, "update_face_colors", bad, raising=False)
    old = scene.handle
    _at(scene, 5.0)
    assert scene.tick() is False
    assert any("animation stopped" in d and type(error).__name__ in d for d in scene.diagnostics)
    assert old.removed and scene._records == {} and scene._timer is None and not scene.animating()
    expected = water_mesh(*model_extent([hull]), grid=True)
    assert scene.handle is not None and np.array_equal(np.asarray(scene.handle.mesh.positions),
                                                       np.asarray(expected.positions))
    water = [e["handle"] for e in viewer._entries.values() if TAG in e["tags"] and not e["handle"].removed]
    assert water == [scene.handle]                              # only the static plane is left
    _at(scene, 50.0)
    assert scene.tick() is False and scene.updates == 0         # no repeat


@requires_qt
def test_without_capability_the_replace_mesh_path_is_used(qapp, monkeypatch):
    from any3dview.retained import MeshHandle
    scene, viewer, hull = _env(qapp, monkeypatch)
    try:
        monkeypatch.setattr(MeshHandle, "update_face_colors",
                            lambda self, c: pytest.fail("update_face_colors must not be used"), raising=False)
        monkeypatch.setattr(type(viewer), "capabilities",
                            property(lambda self: SimpleNamespace(face_color_update=False)), raising=False)
        old = scene.handle
        _at(scene, 4.0)
        assert scene.tick() is True
        assert scene.handle is not old and old.removed
        assert isinstance(scene._records[id(scene.handle)]["colours"], tuple)
        # a viewer without the capability attribute at all (older ANY3dView) behaves the same
        monkeypatch.setattr(type(viewer), "capabilities", property(lambda self: SimpleNamespace()), raising=False)
        old = scene.handle
        _at(scene, 8.0)
        assert scene.tick() is True and scene.handle is not old
    finally:
        scene._drop()
        viewer.close()


_REAL = """
import sys, numpy as np
from PySide6.QtWidgets import QApplication
from any3dview import MeshArrays, create_viewer
from any3dview.qt import QtViewerHostAdapter
from any3dview.retained import MeshHandle
if not hasattr(MeshHandle, "update_face_colors"):
    print("NOAPI"); sys.exit(0)
app = QApplication([])
from anymarine.qtapp.marine_scene import MarineScene
viewer = create_viewer(None, "software", host=QtViewerHostAdapter())
if getattr(viewer.capabilities, "face_color_update", False) is not True:
    print("NOCAP"); sys.exit(0)
viewer.resize(320, 200); viewer.show(); app.processEvents()
pos = np.array(%s, float)
tri = np.array(%s, np.uint32)
hull = viewer.add_mesh_arrays(MeshArrays(positions=pos, triangles=tri), color="#c0703a")
scene = MarineScene(); scene.apply(viewer); scene.mode_override = "animated"; scene.sync([hull])
handle = scene.handle
adds = [0]; orig = viewer.add_mesh_arrays
def counting(*a, **k):
    adds[0] += 1
    return orig(*a, **k)
viewer.add_mesh_arrays = counting
for s in (3.0, 6.0):
    scene._clock = lambda s=s: scene._clock0 + s
    assert scene.tick() is True, scene.diagnostics
    assert scene.handle is handle and not handle.removed
assert adds[0] == 0 and scene.updates == 2 and not scene.diagnostics
print("OK")
""" % (_POS.tolist(), _TRI.tolist())


def test_real_viewer_inplace_when_api_available(tmp_path):
    if not (SIBLING_SRC / "any3dview").is_dir():
        pytest.skip("sibling ANY3dView face-colour worktree not present")
    pytest.importorskip("PySide6")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    own = str(Path(__file__).resolve().parent.parent / "src")
    rest = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and "any3dview" not in p.lower()]
    env["PYTHONPATH"] = os.pathsep.join([str(SIBLING_SRC), own, *rest])
    out = subprocess.run([sys.executable, "-c", _REAL], env=env, capture_output=True, text=True, timeout=100,
                         cwd=str(tmp_path))
    if "NOAPI" in out.stdout or "NOCAP" in out.stdout:
        pytest.skip("ANY3dView face_color_update API absent")
    assert out.returncode == 0 and "OK" in out.stdout, out.stdout + out.stderr
