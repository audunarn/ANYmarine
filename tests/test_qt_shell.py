"""Offscreen Qt shell: load, edit (dirty/stale), replay via set_transform."""

from __future__ import annotations

import time

import numpy as np
import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(predicate, timeout_s=30.0, app=None):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.02)
    return predicate()


@pytest.fixture()
def window(qapp, service, project_dir, monkeypatch):
    win = MainWindow()
    monkeypatch.setattr(win, "_notify_warning", lambda *args: None)
    monkeypatch.setattr(win, "_notify_critical", lambda *args: None)
    monkeypatch.setattr(win, "_notify_info", lambda *args: None)
    win.load_project(str(project_dir))
    yield win
    win._dirty = False
    win.close()


def test_viewer_is_public_qt_widget(window):
    assert isinstance(window.viewer, QWidget)
    assert window.viewer.backend_name == "software"
    assert isinstance(window.viewer.backend_diagnostics, tuple)


def test_load_populates_tree_and_meshes(window, service):
    project = window._project
    assert project is not None and len(project.bodies) == 2
    assert set(window._handles) == {body.body_id for body in project.bodies}
    assert window._tree_model.rowCount() == 4  # Bodies, Equilibrium, Settings, Results


def test_details_edit_marks_dirty_and_stale(qapp, window, service):
    project = window._project
    result = service.run_synthetic_local(project)
    window._project = service.add_result(project, result)
    window._tree_model.set_project(window._project)
    window._details.set_project(window._project)
    window._set_result(result)
    assert window._dirty is False

    body_id = window._project.bodies[0].body_id
    window._details.show_body(body_id)
    window._details._body_name.setText("Renamed body")
    window._details._body_name.editingFinished.emit()

    assert window._dirty is True
    assert window._project.body(body_id).name == "Renamed body"
    # stored result is now flagged stale
    assert service.result_stale(window._project, result) is True
    assert "STALE" in window._frame_label.text()


def test_invalid_edit_rejected_without_mutation(qapp, window):
    project = window._project
    body_id = project.bodies[0].body_id
    window._details.show_body(body_id)
    window._details._body_name.setText("")  # invalid: empty name
    window._details._body_name.editingFinished.emit()
    assert window._project.body(body_id).name == project.bodies[0].name
    assert window._dirty is False


def test_replay_uses_set_transform_from_stored_arrays(qapp, window, service, monkeypatch):
    project = window._project
    result = service.run_synthetic_local(project)
    window._set_result(result)

    # capture set_transform calls per handle (topology reused, not rebuilt)
    calls = {body_id: [] for body_id in window._handles}
    handle_type = type(next(iter(window._handles.values())))
    original = handle_type.set_transform
    identities = {id(handle): body_id for body_id, handle in window._handles.items()}
    def capture(handle, matrix):
        calls[identities[id(handle)]].append(np.array(matrix, copy=True))
        return original(handle, matrix)
    monkeypatch.setattr(handle_type, 'set_transform', capture)

    window._apply_frame(5)
    for body_id in result.body_ids:
        captured = calls[body_id]
        assert len(captured) == 1  # exactly one set_transform per body per frame
        expected_position = result.positions[body_id][5]
        assert np.allclose(captured[0][:3, 3], expected_position)

    window._apply_frame(10)
    for body_id in result.body_ids:
        assert len(calls[body_id]) == 2
        assert np.allclose(calls[body_id][1][:3, 3], result.positions[body_id][10])


def test_plot_values_derive_from_stored_arrays(qapp, window, service):
    project = window._project
    result = service.run_synthetic_local(project)
    window._set_result(result)
    channel = sorted(result.channels)[0]
    window._channel_combo.setCurrentText(channel)
    qapp.processEvents()
    assert window._plot.series_values() == tuple(result.channels[channel])
    assert window._plot.series_times() == tuple(result.times)
    window._apply_frame(7)
    assert window._plot.cursor_index() == 7


def test_slider_drives_frames(qapp, window, service):
    result = service.run_synthetic_local(window._project)
    window._set_result(result)
    assert window._frame_slider.maximum() == result.frame_count() - 1
    window._frame_slider.setValue(3)
    qapp.processEvents()
    assert window._current_frame == 3


def test_job_workflow_in_shell(qapp, window, service):
    from anymarine.qtapp.jobs import JOB_STATE_COMPLETED

    job_id = window._job_manager.submit(window._project)
    assert wait_until(
        lambda: window._job_manager.job(job_id).state == JOB_STATE_COMPLETED, app=qapp
    )
    # result_ready handler stored and saved the result
    assert len(window._project.results) == 1
    stored = window._project.results[0]
    assert stored.kind == "synthetic_unqualified"
    import json
    manifest = json.loads((window._project_root / 'project.json').read_text())
    entry = next(item for item in manifest['results'] if item['result_id'] == stored.result_id)
    assert (window._project_root / entry['sidecar']).is_file()
    window._set_result(stored)
    assert window._plot is not None


def test_close_with_no_active_jobs_accepts(qapp, window):
    from PySide6.QtGui import QCloseEvent

    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
