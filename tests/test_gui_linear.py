"""Qt job path for linear development jobs: run, cancel, GUI action, labels."""

from __future__ import annotations

import time

import pytest

from conftest import requires_qt, requires_solver

pytestmark = [requires_qt, requires_solver]

from PySide6.QtWidgets import QApplication  # noqa: E402

from anymarine import RESULT_KIND_LINEAR  # noqa: E402
from anymarine.domain import LINEAR_MAX_FRAMES  # noqa: E402
from anymarine.linear import analytic_demo_inputs  # noqa: E402
from anymarine.qtapp.jobs import (  # noqa: E402
    JOB_STATE_CANCELLED,
    JOB_STATE_COMPLETED,
    JobManager,
)
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
    if app is not None:
        app.processEvents()
    return predicate()


def test_linear_job_completes_with_result(qapp, service, project):
    manager = JobManager(service)
    inputs = analytic_demo_inputs(project)
    results = []
    manager.result_ready.connect(lambda job_id, result: results.append((job_id, result)))
    job_id = manager.submit(project, linear_inputs=inputs)
    assert manager.job(job_id).kind == RESULT_KIND_LINEAR
    assert wait_until(
        lambda: manager.job(job_id).state in (JOB_STATE_COMPLETED, "failed"), app=qapp
    ), manager.job(job_id).diagnostics
    assert manager.job(job_id).state == JOB_STATE_COMPLETED, manager.job(job_id).diagnostics
    assert len(results) == 1
    result = results[0][1]
    assert result.kind == RESULT_KIND_LINEAR
    assert len(result.times) == inputs.n_steps
    assert any(name.startswith("q:") for name in result.channels)


def test_linear_job_cancellation_produces_no_result(qapp, service, project):
    manager = JobManager(service)
    inputs = analytic_demo_inputs(project, n_steps=LINEAR_MAX_FRAMES)
    results = []
    manager.result_ready.connect(lambda job_id, result: results.append(result))
    job_id = manager.submit(project, linear_inputs=inputs)
    assert wait_until(
        lambda: manager.job(job_id).state in ("running", "cancelling"), app=qapp
    )
    manager.request_cancel(job_id)
    assert wait_until(
        lambda: manager.job(job_id).state == JOB_STATE_CANCELLED, timeout_s=30.0, app=qapp
    ), manager.job(job_id).diagnostics
    assert results == []
    assert not manager.active_jobs()


def test_gui_action_creates_demo_and_runs_job(qapp, service, project_dir, monkeypatch):
    window = MainWindow()
    monkeypatch.setattr(window, "_notify_warning", lambda *args: None)
    monkeypatch.setattr(window, "_notify_critical", lambda *args: None)
    monkeypatch.setattr(window, "_notify_info", lambda *args: None)
    try:
        window.load_project(str(project_dir))
        assert window._project.linear_inputs is None
        window._run_linear_job()
        assert window._project.linear_inputs is not None
        job_ids = list(window._job_manager.jobs())
        assert len(job_ids) == 1
        job_id = job_ids[0]
        assert window._job_manager.job(job_id).kind == RESULT_KIND_LINEAR
        assert wait_until(
            lambda: window._job_manager.job(job_id).state in (JOB_STATE_COMPLETED, "failed"),
            app=qapp,
        ), window._job_manager.job(job_id).diagnostics
        assert window._job_manager.job(job_id).state == JOB_STATE_COMPLETED, window._job_manager.job(job_id).diagnostics
        assert window._project.results
        assert window._project.results[-1].kind == RESULT_KIND_LINEAR
        # Labels distinguish development dynamics from synthetic results.
        assert "linear development dynamics" in window._frame_label.text()
        assert "synthetic" not in window._frame_label.text()
        assert "linear development job" in window._diagnostics.toPlainText()
    finally:
        window._job_manager.cancel_all()
        window._dirty = False
        window.close()
        qapp.processEvents()


def test_gui_action_warns_for_one_body_project(qapp, service, tmp_path, monkeypatch):
    window = MainWindow()
    warnings = []
    monkeypatch.setattr(window, "_notify_warning", lambda title, text: warnings.append(text))
    monkeypatch.setattr(window, "_notify_critical", lambda *args: None)
    monkeypatch.setattr(window, "_notify_info", lambda *args: None)
    try:
        project = service.new_project("one body")
        project = project.with_edits(bodies=project.bodies[:1])
        window._set_project(project)
        window._run_linear_job()
        assert warnings and "2 bodies" in warnings[0]
        assert window._job_manager.jobs() == {}
    finally:
        window._dirty = False
        window.close()
        qapp.processEvents()
