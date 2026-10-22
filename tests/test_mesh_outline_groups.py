"""Body meshes outline only real edges: coplanar triangles share an outline group."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_qt
from anymarine.qtapp.mesh_outline import coplanar_outline_groups


def _box():
    pos = np.array([[x, y, z] for z in (-1.0, 1.0) for y in (-2.0, 2.0) for x in (-5.0, 5.0)])
    tri = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                    [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], dtype=np.uint32)
    return pos, tri


def _cylinder(sides, rings=3):
    ang = np.linspace(0.0, 2 * np.pi, sides, endpoint=False)
    pos = np.array([[np.cos(a), np.sin(a), z] for z in np.linspace(0, 2, rings) for a in ang])
    tri = []
    for r in range(rings - 1):
        for i in range(sides):
            j = (i + 1) % sides
            a, b, c, d = r * sides + i, r * sides + j, (r + 1) * sides + j, (r + 1) * sides + i
            tri += [[a, b, c], [a, c, d]]
    return pos, np.array(tri, dtype=np.uint32)


def test_box_has_six_groups_two_triangles_each():
    pos, tri = _box()
    groups = coplanar_outline_groups(pos, tri)
    assert groups.dtype == np.uint32 and groups.shape == (12,)
    assert np.array_equal(np.bincount(groups), [2] * 6)
    assert np.array_equal(groups, coplanar_outline_groups(pos, tri))  # deterministic


def test_unwelded_box_still_six_groups():
    pos, tri = _box()
    flat = pos[tri].reshape(-1, 3)
    assert len(np.unique(coplanar_outline_groups(flat, np.arange(36, dtype=np.uint32).reshape(12, 3)))) == 6


def test_curved_mesh_keeps_facet_boundaries():
    pos, tri = _cylinder(sides=16)  # 22.5 degrees per facet, 2 ring bands merge along the axis
    assert len(np.unique(coplanar_outline_groups(pos, tri, angle_deg=1.0))) == 16
    assert len(np.unique(coplanar_outline_groups(pos, tri, angle_deg=30.0))) == 1
    # fine facets (< angle) are deliberately merged
    pos, tri = _cylinder(sides=200)
    assert len(np.unique(coplanar_outline_groups(pos, tri, angle_deg=5.0))) == 1


def test_degenerate_and_nonmanifold_are_safe():
    pos, tri = _box()
    pos = np.vstack((pos, pos[:1], [[9.0, 9.0, 9.0]]))
    extra = np.array([[0, 0, 1], [0, 1, 8], [8, 9, 0]], dtype=np.uint32)  # collapsed, sliver, fin on edge 0-1
    groups = coplanar_outline_groups(pos, np.vstack((tri, extra)))
    assert groups.shape == (15,) and groups.dtype == np.uint32
    assert len(np.unique(groups[:12])) >= 1
    assert coplanar_outline_groups(pos, np.zeros((0, 3), dtype=np.uint32)).shape == (0,)
    # three faces on one edge: no merging across it
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]], dtype=float)
    t = np.array([[0, 1, 2], [0, 3, 1], [0, 1, 4]])
    assert len(np.unique(coplanar_outline_groups(p, t))) >= 2


def test_timing_50k_triangles():
    pos, tri = _cylinder(sides=5000, rings=6)  # 50k triangles
    assert len(tri) == 50000
    t0 = time.perf_counter()
    groups = coplanar_outline_groups(pos, tri)
    assert time.perf_counter() - t0 < 5.0
    assert len(groups) == 50000


@requires_qt
def test_add_viewer_body_stores_box_outline_groups(monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from any3dview import create_viewer
    from any3dview.qt import QtViewerHostAdapter
    from anymarine.domain import Body, BodyMesh
    from anymarine.qtapp.mainwindow import MainWindow

    pos, tri = _box()
    body = Body("b1", "Box", (0.0, 0.0, 0.0), 1000.0,
                BodyMesh(tuple(map(tuple, pos.tolist())), tuple(map(tuple, tri.tolist()))))
    viewer = create_viewer(None, "software", host=QtViewerHostAdapter())
    try:
        handle = MainWindow._add_viewer_body(viewer, body, SimpleNamespace(native_inputs=None))
        entry = viewer._entries[id(handle)]
        groups = entry["outline_groups"]
        assert groups is not None
        assert len(np.unique(np.asarray(groups))) == 6
    finally:
        viewer.close()
