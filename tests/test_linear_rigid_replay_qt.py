"""Replay of rigid_subset_v1 linear results (ADR-015 section 6): producing snapshot geometry, matrices, envelope."""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from conftest import requires_qt, requires_solver

pytestmark = [requires_qt, requires_solver]

from PySide6.QtWidgets import QApplication  # noqa: E402

from anymarine.domain import BodyMesh  # noqa: E402
from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402
from test_linear_rigid_run import _mixed_run  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_rigid_result_replays_its_producing_bodies_and_labels_the_envelope(qapp, monkeypatch):
    project, inputs, result = _mixed_run(amplitude=(0.0, 0.0, 0.0, 0.3, 0.0, 0.0))
    # edit the current project's hull after the run: replay must still use the producing snapshot geometry
    body = project.bodies[0]
    moved = BodyMesh(positions=tuple((x + 5.0, y, z) for x, y, z in body.mesh.positions), triangles=body.mesh.triangles)
    edited = dataclasses.replace(project, bodies=(dataclasses.replace(body, mesh=moved),))
    win = MainWindow(viewer_backend="software")
    monkeypatch.setattr(win, "_notify_warning", lambda *a: None)
    try:
        win._project = edited
        win._set_result(result)
        assert win._replay_project is not None
        replay_body = win._replay_project.bodies[0]
        assert replay_body.mesh.positions == body.mesh.positions        # producing geometry, not the edited hull
        win._apply_frame(0)
        assert "OUTSIDE the small-angle envelope" in win._frame_label.text()
        last = result.frame_count() - 1
        flags = result.channels[f"envelope_exceeded:{body.body_id}"]
        inside = next(i for i in range(last + 1) if flags[i] == 0.0)
        win._apply_frame(inside)
        assert "OUTSIDE" not in win._frame_label.text()
    finally:
        win._dirty = False
        win.close()
