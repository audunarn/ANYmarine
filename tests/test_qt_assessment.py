"""Offscreen Qt assessment workflow: real QProcess jobs through JobManager.

Every assessment runs in an isolated child process (``python -m
anymarine.worker``) supervised by the shell's existing JobManager; the GUI
thread only freezes inputs, validates transport and displays child-validated
evidence. Source-proof instrumentation proves no hydro/assessment numerics
run in GUI callbacks. Honest analytic fixtures only; never BEM labels.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from conftest import requires_qt, requires_solver

pytestmark = [requires_qt, requires_solver]

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from anymarine.domain import ProjectModel  # noqa: E402
from anymarine.services import ApplicationService  # noqa: E402
from anymarine.qtapp.jobs import (  # noqa: E402
    ACTIVE_STATES,
    JOB_STATE_CANCELLED,
    JOB_STATE_COMPLETED,
    JOB_STATE_NOGO,
    JOB_STATE_RUNNING,
)
from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402
from anymarine.linear_assessment import (  # noqa: E402
    MODE_REGULAR_WAVE,
    write_case_artifact,
)

from test_linear_assessment import (  # noqa: E402
    DT,
    N_STEPS,
    WINDOWS,
    _regular_wave_inputs,
    _spec,
)


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


def _prepared_project(root: Path):
    """Saved project + stored linear result + registered case artifact."""

    service = ApplicationService(root)
    project = service.new_project("Qt assessment fixture")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)
    case = service.prepare_linear_assessment(
        project,
        _spec("qt-case", MODE_REGULAR_WAVE, inputs, windows=WINDOWS),
    )
    case_path = root.parent / f"{root.name}-case.json"
    write_case_artifact(case, case_path)
    return service, project, result, case, case_path


@pytest.fixture()
def awindow(qapp, tmp_path):
    service, project, result, case, case_path = _prepared_project(
        tmp_path / "project"
    )
    win = MainWindow()
    win._notify_warning = lambda *args: None
    win._notify_critical = lambda *args: None
    win._notify_info = lambda *args: None
    win.load_project(str(service.root))
    win._case_path = str(case_path)
    win._qt_result_id = result.result_id
    submitted = []
    win._job_manager.state_changed.connect(
        lambda job_id, state: submitted.append(job_id)
        if job_id not in submitted else None
    )
    win._submitted_jobs = submitted
    yield win
    win._dirty = False
    win.close()


def _last_job(win):
    assert win._submitted_jobs, "no job was submitted"
    return win._submitted_jobs[-1]


def _instrument_no_gui_proof(monkeypatch):
    """Fail loudly if any source proof runs on the GUI thread."""

    def _load(cls, root):
        raise AssertionError(
            "ApplicationService.load (source proof) ran on the GUI thread"
        )

    def _verify(self, project):
        raise AssertionError("hydro source proof ran on the GUI thread")

    monkeypatch.setattr(ApplicationService, "load", classmethod(_load))
    monkeypatch.setattr(ApplicationService, "verify_hydro_provenance", _verify)


# ------------------------------------------------------------- prepare


def test_register_case_completes_without_verdict(
    qapp, awindow, tmp_path, monkeypatch
):
    _instrument_no_gui_proof(monkeypatch)
    spec = _spec(
        "qt-prepare-case",
        MODE_REGULAR_WAVE,
        awindow._project.linear_inputs,
        windows=WINDOWS,
    )
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, allow_nan=False), encoding="utf-8")
    target = tmp_path / "registered" / "case.json"
    target.parent.mkdir(parents=True)

    awindow._do_register_case(str(spec_path), str(target))
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_COMPLETED,
        app=qapp,
    ), awindow._job_manager.job(job_id).diagnostics
    # A registration is not assessment evidence: nothing is displayed as a
    # verdict and no verdict was required for completion.
    assert awindow._assessment_panel.payload() is None
    assert "registered" in awindow._assessment_panel._status_label.text()
    assert target.is_file()


def test_single_assessment_pass_through_qprocess(qapp, awindow, monkeypatch):
    _instrument_no_gui_proof(monkeypatch)
    awindow._do_assess_result(awindow._case_path, awindow._qt_result_id, "")
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_COMPLETED,
        app=qapp,
    ), awindow._job_manager.job(job_id).diagnostics
    payload = awindow._assessment_panel.payload()
    assert payload is not None and payload["verdict"] == "pass"
    assert "PASS" in awindow._assessment_panel._verdict_label.text()
    # Real engineering checks with values/limits/units, not flags.
    model = awindow._assessment_panel._model
    assert model.rowCount() > 0
    names = [model.index(row, 0).data() for row in range(model.rowCount())]
    assert any("balance" in str(name) for name in names)
    assert model.index(0, 1).data() not in (None, "")
    assert model.index(0, 3).data()  # unit
    assert model.index(0, 4).data() == "Pass"
    # Bound identity is shown next to the evidence.
    identity = awindow._assessment_panel._identity_label.text()
    assert "input" in identity and "result" in identity and "case" in identity
    assert "DETACHED" not in identity


def test_single_nogo_is_displayed_non_success(qapp, awindow):
    project = awindow._project
    result = project.results[0]
    labels = project.linear_inputs.labels
    forged_external = {
        **result.channels,
        **{
            f"force_external:{label}": tuple(
                1.01 * np.asarray(result.channels[f"force_external:{label}"])
            )
            for label in labels
        },
    }
    forged = dataclasses.replace(result, channels=forged_external)
    awindow._project = project.with_edits(results=(forged,))
    awindow._do_assess_result(awindow._case_path, result.result_id, "")
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_NOGO,
        app=qapp,
    ), awindow._job_manager.job(job_id).diagnostics
    payload = awindow._assessment_panel.payload()
    assert payload["verdict"] == "nogo"
    assert "NoGo" in awindow._assessment_panel._verdict_label.text()
    # The failed checks with their real values are preserved and shown.
    model = awindow._assessment_panel._model
    statuses = [model.index(row, 4).data() for row in range(model.rowCount())]
    assert "NoGo" in statuses
    record = awindow._job_manager.job(job_id)
    assert "non-success" in record.diagnostics[-1]


# ------------------------------------------------------------- snapshot


def test_reopen_snapshot_after_originals_deleted(
    qapp, awindow, tmp_path, monkeypatch
):
    service, project, result, case, _ = _prepared_project(tmp_path / "project2")
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    snapshot_root = tmp_path / "single-snapshot"
    service.save_assessment_snapshot(project, result, case, report, snapshot_root)

    _instrument_no_gui_proof(monkeypatch)
    awindow._do_reopen(str(snapshot_root))
    job_id = _last_job(awindow)
    # The original root disappears right after submission: the frozen bytes
    # are the immutable input and replay comes from the child's captured
    # records, never from the original root.
    shutil.rmtree(snapshot_root)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_COMPLETED,
        app=qapp,
    ), awindow._job_manager.job(job_id).diagnostics
    payload = awindow._assessment_panel.payload()
    assert payload["verdict"] == "pass"
    # Member-specific replay from the child's captured authoritative arrays.
    assert awindow._current_result is not None
    assert awindow._current_result.result_id == result.result_id
    assert awindow._current_result.times == tuple(result.times)
    assert awindow._replay_project is not None
    assert "detached replay" in awindow._result_status


def test_pair_member_specific_replay(qapp, awindow, tmp_path):
    coarse_root = tmp_path / "coarse"
    fine_root = tmp_path / "fine"
    service = ApplicationService(coarse_root)
    project_coarse = service.new_project("Pair coarse")
    inputs_coarse = _regular_wave_inputs(project_coarse)
    project_coarse = service.set_linear_inputs(project_coarse, inputs_coarse)
    service.save(project_coarse)
    project_fine = ProjectModel(
        name="Pair fine",
        bodies=project_coarse.bodies,
        equilibrium=project_coarse.equilibrium,
        settings=project_coarse.settings,
    )
    inputs_fine = _regular_wave_inputs(
        project_fine, dt=DT / 2, n_steps=2 * N_STEPS - 1
    )
    service_fine = ApplicationService(fine_root)
    project_fine = service_fine.set_linear_inputs(project_fine, inputs_fine)
    service_fine.save(project_fine)
    case_fine = service_fine.prepare_linear_assessment(
        project_fine,
        _spec(
            "qt-pair-fine",
            MODE_REGULAR_WAVE,
            inputs_fine,
            windows=WINDOWS,
            refinement={"partner_case_hash": None, "dt_ratio": 2, "role": "fine"},
        ),
    )
    case_coarse = service.prepare_linear_assessment(
        project_coarse,
        _spec(
            "qt-pair-coarse",
            MODE_REGULAR_WAVE,
            inputs_coarse,
            windows=WINDOWS,
            refinement={
                "partner_case_hash": case_fine.case_hash,
                "dt_ratio": 2,
                "role": "coarse",
            },
        ),
    )
    result_coarse = service.run_linear_local(project_coarse)
    project_coarse = service.add_result(project_coarse, result_coarse)
    service.save(project_coarse)
    result_fine = service_fine.run_linear_local(project_fine)
    project_fine = service_fine.add_result(project_fine, result_fine)
    service_fine.save(project_fine)
    coarse_case_path = tmp_path / "coarse-case.json"
    fine_case_path = tmp_path / "fine-case.json"
    write_case_artifact(case_coarse, coarse_case_path)
    write_case_artifact(case_fine, fine_case_path)
    out_root = tmp_path / "pair-snapshot"

    awindow._do_pair(
        str(coarse_root),
        result_coarse.result_id,
        str(coarse_case_path),
        str(fine_root),
        result_fine.result_id,
        str(fine_case_path),
        str(out_root),
    )
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_COMPLETED,
        app=qapp,
    ), awindow._job_manager.job(job_id).diagnostics
    payload = awindow._assessment_panel.payload()
    assert payload["verdict"] == "pass"
    # Default member replay is the coarse member's own arrays.
    assert awindow._current_result.result_id == result_coarse.result_id
    assert len(awindow._current_result.times) == N_STEPS
    # Switching members replays THAT member's own project/result only.
    awindow._assessment_panel._member_combo.setCurrentText("fine")
    assert awindow._current_result.result_id == result_fine.result_id
    assert len(awindow._current_result.times) == 2 * N_STEPS - 1
    assert awindow._replay_project.name == "Pair fine"
    # Switching back never mixes channels or members.
    awindow._assessment_panel._member_combo.setCurrentText("coarse")
    assert awindow._current_result.result_id == result_coarse.result_id
    assert len(awindow._current_result.times) == N_STEPS


# ----------------------------------------------------- stale / detached


def test_evidence_becomes_detached_after_project_change(qapp, awindow, tmp_path):
    awindow._do_assess_result(awindow._case_path, awindow._qt_result_id, "")
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_COMPLETED,
        app=qapp,
    )
    assert "DETACHED" not in awindow._assessment_panel._identity_label.text()

    # The current project is replaced by one with different linear inputs:
    # the already-displayed evidence must be re-labelled detached/stale.
    other_service = ApplicationService(tmp_path / "other")
    other = other_service.new_project("Other model")
    other_inputs = _regular_wave_inputs(
        other, dt=DT / 2, n_steps=2 * N_STEPS - 1
    )
    other = other_service.set_linear_inputs(other, other_inputs)
    awindow._set_project(other)
    assert "DETACHED" in awindow._assessment_panel._identity_label.text()
    # A later edit re-evaluates detachment again (still detached).
    awindow._mark_edited(other.with_edits(name="Other model edited"))
    assert "DETACHED" in awindow._assessment_panel._identity_label.text()


def test_physical_body_edit_detaches_displayed_evidence(qapp, awindow):
    """A physical edit invalidates the binding, not just the input hash.

    The stored linear-inputs hash is unchanged by a body edit, so the
    detachment check must use the light public result-staleness/binding
    identity checks: evidence edited before completion and evidence
    already on screen are both re-labelled detached/stale.
    """

    awindow._do_assess_result(awindow._case_path, awindow._qt_result_id, "")
    job_id = _last_job(awindow)
    # Physical mutation BEFORE completion: the frozen run is unaffected,
    # but its evidence can never be presented as current.
    body = awindow._project.bodies[0]
    awindow._apply_body_edit(body.body_id, {"mass": body.mass * 1.1})
    assert awindow._service.result_stale(awindow._project, awindow._project.results[0])
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_COMPLETED,
        app=qapp,
    )
    assert "DETACHED" in awindow._assessment_panel._identity_label.text()

    # Restore the mass so the binding matches again: the same evidence
    # (bound to the frozen submission inputs) is current once more.
    awindow._apply_body_edit(body.body_id, {"mass": body.mass})
    assert not awindow._service.result_stale(
        awindow._project, awindow._project.results[0]
    )
    assert "DETACHED" not in awindow._assessment_panel._identity_label.text()

    # A reference-position edit after display detaches again, and a
    # later equilibrium edit keeps it detached.
    awindow._apply_body_edit(
        body.body_id,
        {"reference_position": tuple(v + 0.5 for v in body.reference_position)},
    )
    assert "DETACHED" in awindow._assessment_panel._identity_label.text()
    equilibrium = awindow._project.equilibrium
    awindow._apply_equilibrium_edit(
        {"waterline_z": equilibrium.waterline_z + 0.1}
    )
    assert "DETACHED" in awindow._assessment_panel._identity_label.text()


# ----------------------------------------------------- cancel / closure


def test_cancel_during_active_process(qapp, awindow):
    awindow._do_assess_result(awindow._case_path, awindow._qt_result_id, "")
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_RUNNING,
        app=qapp,
    )
    awindow._job_manager.request_cancel(job_id)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_CANCELLED,
        app=qapp,
    ), awindow._job_manager.job(job_id).diagnostics
    # Cancelled evidence is never displayed as an assessment result.
    assert awindow._assessment_panel.payload() is None


def test_closure_with_active_assessment_job(qapp, awindow, monkeypatch):
    from PySide6.QtGui import QCloseEvent

    awindow._do_assess_result(awindow._case_path, awindow._qt_result_id, "")
    job_id = _last_job(awindow)
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state in ACTIVE_STATES,
        app=qapp,
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    event = QCloseEvent()
    awindow.closeEvent(event)
    assert event.isAccepted()
    assert wait_until(
        lambda: awindow._job_manager.job(job_id).state == JOB_STATE_CANCELLED,
        app=qapp,
    )
