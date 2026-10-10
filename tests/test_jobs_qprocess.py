"""Real QProcess job workflow: success, cooperative cancel, failure."""

from __future__ import annotations

import time

import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtCore import QProcess  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from anymarine.domain import AnalysisSettings  # noqa: E402
from anymarine.qtapp.jobs import (  # noqa: E402
    JOB_STATE_CANCELLED,
    JOB_STATE_COMPLETED,
    JOB_STATE_FAILED,
    JOB_STATE_RUNNING,
    JobManager,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(predicate, timeout_s=30.0, app=None):
    """Bounded wait that keeps the Qt event loop pumping (GUI thread)."""

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


def test_job_success_completed(qapp, service, project):
    manager = JobManager(service)
    job_id = manager.submit(project)
    assert manager.job(job_id).state in ("queued", "running")
    assert wait_until(lambda: manager.job(job_id).state == JOB_STATE_COMPLETED, app=qapp)
    record = manager.job(job_id)
    assert record.progress == 1.0
    assert record.output_path.exists()
    assert any("completed" in line for line in record.diagnostics)


def test_job_result_validates_against_input_hash(qapp, service, project):
    manager = JobManager(service)
    states = []
    manager.state_changed.connect(lambda jid, state: states.append(state))
    job_id = manager.submit(project)
    assert wait_until(lambda: manager.job(job_id).state == JOB_STATE_COMPLETED, app=qapp)
    assert states[0] == "queued"
    assert JOB_STATE_RUNNING in states
    assert states[-1] == JOB_STATE_COMPLETED
    result = service.load_job_output(
        manager.job(job_id).output_path, manager.job(job_id).input_hash
    )
    assert result.body_ids == tuple(b.body_id for b in project.bodies)


def test_job_cooperative_cancel(qapp, service, project):
    long_settings = AnalysisSettings(duration=60.0, dt=0.0005)  # 120k steps
    manager = JobManager(service)
    job_id = manager.submit(project, long_settings)
    assert wait_until(lambda: manager.job(job_id).state == JOB_STATE_RUNNING, app=qapp)
    manager.request_cancel(job_id)
    assert manager.job(job_id).state == "cancelling"
    assert wait_until(lambda: manager.job(job_id).state == JOB_STATE_CANCELLED, timeout_s=30, app=qapp)
    record = manager.job(job_id)
    assert not record.output_path.exists()  # no partial success
    assert record.process is not None
    assert record.process.state() != QProcess.ProcessState.Running


def test_job_failure_on_killed_process(qapp, service, project):
    long_settings = AnalysisSettings(duration=60.0, dt=0.0005)
    manager = JobManager(service)
    job_id = manager.submit(project, long_settings)
    assert wait_until(lambda: manager.job(job_id).state == JOB_STATE_RUNNING, app=qapp)
    record = manager.job(job_id)
    record.process.kill()
    assert wait_until(lambda: manager.job(job_id).state == JOB_STATE_FAILED, timeout_s=30, app=qapp)
    assert not record.output_path.exists()


def test_queued_job_cancelled_before_start(qapp, service, project):
    long_settings = AnalysisSettings(duration=60.0, dt=0.0005)
    manager = JobManager(service)
    first = manager.submit(project, long_settings)
    second = manager.submit(project, long_settings)
    assert manager.job(second).state == "queued"
    manager.request_cancel(second)
    assert manager.job(second).state == JOB_STATE_CANCELLED
    # first job unaffected, still active; cancel it too and drain
    manager.request_cancel(first)
    assert wait_until(lambda: not manager.active_jobs(), timeout_s=30, app=qapp)
