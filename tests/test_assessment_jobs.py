"""Assessment job transport: freeze, child execution, refusal and evidence.

Headless (no Qt). Children run as real ``python -m anymarine.worker``
subprocesses with PYTHONPATH=src only — proving the assessment child
needs no solver and no GUI. Analytic exponential fixtures come from the
existing assessment suite (honest coefficients, never BEM labels).
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from conftest import SRC, requires_solver

from anymarine.assessment_jobs import (
    ASSESSMENT_KIND_ADJUDICATE_PAIR,
    ASSESSMENT_KIND_ADJUDICATE_SINGLE,
    ASSESSMENT_JOB_KINDS,
    OUTPUT_SEAL_FIELD,
    AssessmentJobError,
    adjudication_job,
    load_assessment_job_output,
    output_seal,
    pair_assessment_job,
    prepare_assessment_job,
    single_assessment_job,
    verify_snapshot_manifest,
)
from anymarine.linear_assessment import (
    MODE_REGULAR_WAVE,
    read_case_artifact,
)
from anymarine.services import ApplicationService

from test_linear_assessment import (
    DT,
    N_STEPS,
    WINDOWS,
    _regular_wave_inputs,
    _spec,
)
from test_hydro_decay_refinement import decay_pair, raw_decay  # noqa: F401


def run_child(snapshot: dict, tmp_path: Path, cancel: bool = False):
    """Run one frozen assessment job in a real worker subprocess."""

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    cancel_path = tmp_path / "cancel"
    input_path.write_text(json.dumps(snapshot, allow_nan=False), encoding="utf-8")
    if cancel:
        cancel_path.write_text("", encoding="utf-8")
    env = os.environ.copy()
    # src only: the assessment child is solver-free and Qt-free.
    env["PYTHONPATH"] = str(SRC)
    env["ANYMARINE_SOLVER_PATH"] = ""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "anymarine.worker",
            str(input_path),
            str(output_path),
            str(cancel_path),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    ), output_path


def _prepared(tmp_path, case_id="job-case", *, results=1):
    """Saved project + registered case + solved stored result(s)."""

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Assessment job fixture")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec(case_id, MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)
    return service, project, result, case


def _write_case_artifact(case, tmp_path) -> Path:
    from anymarine.linear_assessment import write_case_artifact

    path = tmp_path / "registered-case.json"
    write_case_artifact(case, path)
    return path


# ------------------------------------------------------------------ prepare


def test_prepare_job_child_success_and_tamper_refusal(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Prepare job fixture")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    spec = _spec("prep-case", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    target = tmp_path / "registered" / "case.json"
    target.parent.mkdir(parents=True)
    snapshot = prepare_assessment_job(project, spec, target)
    assert snapshot["kind"] == "assessment_prepare"
    assert snapshot["context"]["source_route"] == "explicit"

    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(output_path, snapshot["input_hash"])
    assert payload["case_hash"]
    registered = read_case_artifact(target)
    assert registered.case_hash == payload["case_hash"]
    assert registered.case_id == "prep-case"
    # The exclusive target is refused when it already exists.
    with pytest.raises(AssessmentJobError, match="already exists"):
        prepare_assessment_job(project, spec, target)

    # A tampered frozen payload cannot reproduce the declared job hash.
    tampered = json.loads(json.dumps(snapshot))
    tampered["spec"]["case_id"] = "other-case"
    completed, _ = run_child(tampered, tmp_path)
    assert completed.returncode == 2
    assert "does not match" in completed.stderr


# ----------------------------------------------------------- single assess


@requires_solver
def test_single_pass_job_snapshot_and_original_disappearance(tmp_path):
    service, project, result, case = _prepared(tmp_path)
    case_path = _write_case_artifact(case, tmp_path)
    snapshot_target = tmp_path / "exclusive-snapshot"
    snapshot = single_assessment_job(
        project,
        result,
        case,
        service_root=service.root,
        snapshot_target=snapshot_target,
    )
    # Originals disappear AFTER submission: the frozen job must not care.
    import shutil

    shutil.rmtree(service.root)
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(output_path, snapshot["input_hash"])
    assert payload["verdict"] == "pass"
    assert payload["report"]["result_id"] == result.result_id
    checks = payload["report"]["checks"]
    assert checks and all(
        {"name", "value", "limit", "unit", "passed", "detail"} <= set(c)
        for c in checks
    )
    # The saved snapshot matches the child-validated manifest exactly.
    manifest = payload["snapshot"]["files"]
    assert "project/project.json" in manifest or any(
        k.startswith("project/") for k in manifest
    )
    verify_snapshot_manifest(snapshot_target, manifest)
    # A mutated on-disk snapshot is refused for GUI admission.
    victim = next(iter(manifest))
    (snapshot_target / victim).write_text("tampered", encoding="utf-8")
    with pytest.raises(AssessmentJobError, match="does not match"):
        load_assessment_job_output(output_path, snapshot["input_hash"])


@requires_solver
def test_single_nogo_job_is_validated_non_success(tmp_path):
    service, project, result, case = _prepared(tmp_path, "nogo-case")
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
    forged_project = project.with_edits(results=(forged,))
    snapshot = single_assessment_job(
        forged_project, forged, case, service_root=service.root
    )
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(output_path, snapshot["input_hash"])
    assert payload["verdict"] == "nogo"
    assert "force_balance" in payload["report"]["failed"]
    assert payload["report"]["checks"]  # the NoGo report is preserved, not swallowed


@requires_solver
def test_single_submission_refusals(tmp_path):
    service, project, result, case = _prepared(tmp_path, "refusal-case")
    # A foreign result (different inputs) is refused at submission.
    other_inputs = _regular_wave_inputs(project, dt=DT / 2, n_steps=2 * N_STEPS - 1)
    other_project = service.set_linear_inputs(project, other_inputs)
    with pytest.raises(AssessmentJobError, match="stale or foreign result"):
        single_assessment_job(other_project, result, case, service_root=service.root)
    # A case bound to different inputs is refused.
    other_case = service.prepare_linear_assessment(
        other_project,
        _spec("other-case", MODE_REGULAR_WAVE, other_inputs, windows=WINDOWS),
    )
    with pytest.raises(AssessmentJobError, match="does not describe"):
        single_assessment_job(project, result, other_case, service_root=service.root)
    # An existing snapshot target is refused (evidence is never overwritten).
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(AssessmentJobError, match="already exists"):
        single_assessment_job(
            project, result, case, service_root=service.root, snapshot_target=existing
        )


# --------------------------------------------------------------------- pair


@requires_solver
def test_pair_job_exclusive_snapshot_and_reopen(tmp_path):
    coarse_dir = tmp_path / "coarse"
    fine_dir = tmp_path / "fine"
    service = ApplicationService(coarse_dir)
    project_coarse = service.new_project("Pair coarse")
    inputs_coarse = _regular_wave_inputs(project_coarse)
    project_coarse = service.set_linear_inputs(project_coarse, inputs_coarse)
    service.save(project_coarse)
    service_fine = ApplicationService(fine_dir)
    # A refinement pair is the SAME physical model: the fine member reuses
    # the coarse bodies/equilibrium verbatim (only the time refinement
    # differs), so the public same-model binding gate accepts the pair.
    from anymarine.domain import ProjectModel

    project_fine = ProjectModel(
        name="Pair fine",
        bodies=project_coarse.bodies,
        equilibrium=project_coarse.equilibrium,
        settings=project_coarse.settings,
    )
    inputs_fine = _regular_wave_inputs(project_fine, dt=DT / 2, n_steps=2 * N_STEPS - 1)
    project_fine = service_fine.set_linear_inputs(project_fine, inputs_fine)
    service_fine.save(project_fine)
    case_fine = service_fine.prepare_linear_assessment(
        project_fine,
        _spec(
            "pair-fine",
            MODE_REGULAR_WAVE,
            inputs_fine,
            windows=WINDOWS,
            refinement={"partner_case_hash": None, "dt_ratio": 2, "role": "fine"},
        ),
    )
    case_coarse = service.prepare_linear_assessment(
        project_coarse,
        _spec(
            "pair-coarse",
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

    out_root = tmp_path / "pair-snapshot"
    snapshot = pair_assessment_job(
        project_coarse,
        result_coarse,
        case_coarse,
        coarse_dir,
        project_fine,
        result_fine,
        case_fine,
        fine_dir,
        out_root,
    )
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(output_path, snapshot["input_hash"])
    assert payload["verdict"] == "pass"
    assert set(payload["pair"]["members"]) == {"coarse", "fine"}
    verify_snapshot_manifest(out_root, payload["snapshot"]["files"])

    # Reopen: the snapshot's OWN bytes are frozen at submission; the
    # original root may then disappear entirely.
    import shutil

    reopen = adjudication_job(out_root)
    assert reopen["kind"] == ASSESSMENT_KIND_ADJUDICATE_PAIR
    shutil.rmtree(out_root)
    completed, output_path = run_child(reopen, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(output_path, reopen["input_hash"])
    assert payload["adjudication"]["agrees"] is True
    assert payload["verdict"] == "pass"


@requires_solver
def test_adjudicate_single_snapshot_only_reopen(tmp_path):
    service, project, result, case = _prepared(tmp_path, "reopen-case")
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    snapshot_root = tmp_path / "single-snapshot"
    service.save_assessment_snapshot(project, result, case, report, snapshot_root)
    case_path = next((snapshot_root / "cases").glob("*.json"))
    report_path = next((snapshot_root / "reports").glob("*.json"))
    job = adjudication_job(
        snapshot_root,
        case_path.relative_to(snapshot_root).as_posix(),
        report_path.relative_to(snapshot_root).as_posix(),
    )
    assert job["kind"] == ASSESSMENT_KIND_ADJUDICATE_SINGLE
    # Snapshot-only reopen: the original root disappears after submission.
    import shutil

    shutil.rmtree(snapshot_root)
    completed, output_path = run_child(job, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(output_path, job["input_hash"])
    assert payload["adjudication"]["agrees"] is True
    assert payload["verdict"] == "pass"
    assert payload["report"]["checks"]


# --------------------------------------------- freeze tamper / cancel / foreign


def test_frozen_adjudication_tamper_cancel_and_foreign_output(tmp_path):
    # A minimal (invalid) snapshot tree: freezing captures bytes only;
    # the child must refuse the unknown/incomplete snapshot by content.
    root = tmp_path / "fake-snapshot"
    (root / "cases").mkdir(parents=True)
    (root / "reports").mkdir(parents=True)
    (root / "cases" / "case.json").write_text("{}", encoding="utf-8")
    (root / "reports" / "report.json").write_text("{}", encoding="utf-8")
    job = adjudication_job(root)
    completed, output_path = run_child(job, tmp_path)
    assert completed.returncode == 2
    assert not output_path.exists()

    # Tampered frozen bytes: the payload no longer matches its frozen sha.
    tampered = json.loads(json.dumps(job))
    first = sorted(tampered["files"])[0]
    tampered["files"][first]["data"] = (
        tampered["files"][first]["data"][:-2] + "aa=="
    )
    completed, _ = run_child(tampered, tmp_path)
    assert completed.returncode == 2

    # Cooperative cancellation before any phase: exit 3, no output.
    completed, output_path = run_child(job, tmp_path, cancel=True)
    assert completed.returncode == 3
    assert "CANCELLED" in completed.stdout
    assert not output_path.exists()

    # Foreign output validation: wrong hash / unknown kind / bad JSON.
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"kind": "assessment_single"}), encoding="utf-8")
    with pytest.raises(AssessmentJobError, match="input_hash"):
        load_assessment_job_output(foreign, "0" * 64)
    foreign.write_text("not json", encoding="utf-8")
    with pytest.raises(AssessmentJobError, match="not valid JSON"):
        load_assessment_job_output(foreign, "0" * 64)
    foreign.write_text(
        json.dumps({"kind": "unknown_kind", "input_hash": "0" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(AssessmentJobError, match="unknown kind"):
        load_assessment_job_output(foreign, "0" * 64)
    # Missing output artifact.
    with pytest.raises(AssessmentJobError, match="was not produced"):
        load_assessment_job_output(tmp_path / "never.json", "0" * 64)


# ------------------------------------------- hydro-sourced prepare/single


def _hydro_spec(project, **kwargs):
    from test_hydro_decay_refinement import _decay_case_spec

    return _decay_case_spec(project, **kwargs)


def test_raw_hydro_prepare_and_single_after_originals_disappear(raw_decay, tmp_path):
    """Two-sidecar raw route: registration and assessment survive the loss
    of the original project root and source files after submission."""

    import shutil

    project = raw_decay["project"]
    service = raw_decay["service"]
    spec = _hydro_spec(
        project, role="rawjob", partner=None, route="raw_dataset", refinement=None
    )
    target = tmp_path / "registered" / "case.json"
    target.parent.mkdir(parents=True)
    snapshot = prepare_assessment_job(
        project, spec, target, service_root=service.root
    )
    # Exactly the two raw sidecars are frozen, hash-checked in place.
    assert set(snapshot["hydro_source_payloads"]) == {"dataset", "kernel"}
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(
        output_path,
        snapshot["input_hash"],
        expected_kind="assessment_prepare",
        expected_snapshot=snapshot,
    )
    # A case registration completes without any assessment verdict.
    assert payload.get("verdict") is None
    case = read_case_artifact(target)
    assert case.case_id == "hydro-decay-rawjob"

    single = single_assessment_job(
        project, raw_decay["result"], case, service_root=service.root
    )
    assert set(single["hydro_source_payloads"]) == {"dataset", "kernel"}
    # Every original (project root, dataset, kernel) disappears AFTER
    # submission; the frozen job must not care.
    shutil.rmtree(raw_decay["root"])
    completed, output_path = run_child(single, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(
        output_path,
        single["input_hash"],
        expected_kind="assessment_single",
        expected_snapshot=single,
    )
    assert payload["verdict"] == "pass"
    assert payload["report"]["result_id"] == raw_decay["result"].result_id


def test_derived_hydro_prepare_and_single_after_originals_disappear(
    decay_pair, tmp_path
):
    """Three-sidecar derived route: the operators artifact is frozen too."""

    import shutil

    fine = decay_pair["fine"]
    project = fine["project"]
    spec = _hydro_spec(project, role="finejob", partner=None, refinement=None)
    target = tmp_path / "registered" / "case.json"
    target.parent.mkdir(parents=True)
    snapshot = prepare_assessment_job(
        project, spec, target, service_root=fine["service"].root
    )
    assert set(snapshot["hydro_source_payloads"]) == {
        "dataset",
        "kernel",
        "operators",
    }
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(
        output_path,
        snapshot["input_hash"],
        expected_kind="assessment_prepare",
        expected_snapshot=snapshot,
    )
    assert payload.get("verdict") is None
    case = read_case_artifact(target)
    assert case.case_id == "hydro-decay-finejob"

    single = single_assessment_job(
        project, fine["result"], case, service_root=fine["service"].root
    )
    assert set(single["hydro_source_payloads"]) == {
        "dataset",
        "kernel",
        "operators",
    }
    shutil.rmtree(decay_pair["root"])
    completed, output_path = run_child(single, tmp_path)
    assert completed.returncode == 0, completed.stderr
    payload = load_assessment_job_output(
        output_path,
        single["input_hash"],
        expected_kind="assessment_single",
        expected_snapshot=single,
    )
    assert payload["verdict"] == "pass"
    assert payload["report"]["result_id"] == fine["result"].result_id


# -------------------------------------------- output admission rejections


@requires_solver
def test_output_admission_rejects_relabelled_and_inconsistent(tmp_path):
    """Kind, verdict, identity, partial and foreign output are refused."""

    service, project, result, case = _prepared(tmp_path, "admission-case")
    snapshot = single_assessment_job(
        project, result, case, service_root=service.root
    )
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    good = json.loads(output_path.read_text(encoding="utf-8"))

    def admit(payload, **kwargs):
        # Every candidate is re-sealed like an attacker who rewrites the
        # file AND its envelope seal; the per-kind field/identity checks
        # must still refuse relabelled evidence, and the trusted producer
        # digest (checked separately) refuses re-sealed replacements.
        candidate = json.loads(json.dumps(payload, allow_nan=False))
        candidate[OUTPUT_SEAL_FIELD] = output_seal(candidate)
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps(candidate, allow_nan=False), encoding="utf-8")
        return load_assessment_job_output(path, snapshot["input_hash"], **kwargs)

    # Relabelled kind: the output claims another job kind.
    relabelled = dict(good)
    relabelled["kind"] = "assessment_pair"
    with pytest.raises(AssessmentJobError, match="relabelled"):
        admit(relabelled, expected_kind="assessment_single")
    # Verdict inconsistent with the stored report.
    forged = dict(good)
    forged["verdict"] = "nogo"
    with pytest.raises(AssessmentJobError, match="inconsistent"):
        admit(forged)
    # Wrong result identity against the frozen job input.
    forged = dict(good)
    forged["context"] = dict(good["context"])
    forged["context"]["result_id"] = "foreign-result"
    with pytest.raises(AssessmentJobError, match="foreign or relabelled"):
        admit(forged, expected_snapshot=snapshot)
    # Partial payload: the report is stripped.
    partial = {key: value for key, value in good.items() if key != "report"}
    with pytest.raises(AssessmentJobError, match="missing the required"):
        admit(partial)
    # A case registration must not carry an assessment verdict.
    reprepared = dict(good)
    reprepared["kind"] = "assessment_prepare"
    with pytest.raises(AssessmentJobError, match="must not declare"):
        admit(reprepared)
    # A snapshot declared although the job requested none.
    withsnap = dict(good)
    withsnap["snapshot"] = {"root": str(tmp_path), "files": {}}
    with pytest.raises(AssessmentJobError, match="did not request a snapshot"):
        admit(withsnap, expected_snapshot=snapshot)
    # The honest payload admits with the full expected identity.
    admitted = admit(
        good, expected_kind="assessment_single", expected_snapshot=snapshot
    )
    assert admitted["verdict"] == "pass"


# --------------------------------------------- output seal transport integrity


@pytest.mark.parametrize("kind", sorted(ASSESSMENT_JOB_KINDS))
def test_unsealed_partial_output_with_correct_input_hash_refused(tmp_path, kind):
    """A correct input hash alone can never admit partial evidence.

    Adapted from the independent parent admission checks: an output that
    names the right kind and input hash but carries no canonical producer
    seal (and no per-kind payload) is refused for every assessment kind.
    """

    expected = "a" * 64
    path = tmp_path / "output.json"
    path.write_text(
        json.dumps({"kind": kind, "input_hash": expected, "verdict": "pass"}),
        encoding="utf-8",
    )
    with pytest.raises(AssessmentJobError):
        load_assessment_job_output(path, expected)


@pytest.mark.parametrize("verdict", ["completed", "cancelled", "PASS", "", None])
def test_unrecognized_or_missing_assessment_verdict_refused(tmp_path, verdict):
    expected = "a" * 64
    path = tmp_path / "output.json"
    path.write_text(
        json.dumps(
            {
                "kind": "assessment_single",
                "input_hash": expected,
                "verdict": verdict,
                "report": {},
                "context": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AssessmentJobError):
        load_assessment_job_output(path, expected)


@requires_solver
def test_output_seal_refuses_modified_and_replaced_evidence(tmp_path):
    """The canonical seal ties admitted evidence to the child's bytes.

    Naive modification of any field (metric value, body identity, case
    hash) is refused by the envelope seal; a replacement that re-hashes
    the envelope is refused by the trusted producer digest captured from
    the worker stdout, and identity tampering is refused twice.
    """

    service, project, result, case = _prepared(tmp_path, "seal-case")
    snapshot = single_assessment_job(project, result, case, service_root=service.root)
    completed, output_path = run_child(snapshot, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "OUTPUT_SHA256 " in completed.stdout
    producer = completed.stdout.split("OUTPUT_SHA256 ")[1].split()[0]
    good = json.loads(output_path.read_text(encoding="utf-8"))
    assert good[OUTPUT_SEAL_FIELD] == producer

    def write(payload):
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
        return path

    def tampered(change):
        payload = json.loads(json.dumps(good))
        if change == "metric":
            payload["report"]["checks"][0]["value"] += 12345.0
        elif change == "body":
            payload["context"]["body_ids"] = ["foreign-body"]
        else:
            payload["report"]["case_hash"] = "f" * 64
        return payload

    # Naive modification (envelope seal left stale): refused.
    for change in ("metric", "body", "case_hash"):
        with pytest.raises(AssessmentJobError, match="seal"):
            load_assessment_job_output(
                write(tampered(change)),
                snapshot["input_hash"],
                expected_kind=snapshot["kind"],
                expected_snapshot=snapshot,
            )

    # Re-sealed replacement: identity tampering is still refused by the
    # frozen body/case checks even without the producer digest.
    for change in ("body", "case_hash"):
        payload = tampered(change)
        payload[OUTPUT_SEAL_FIELD] = output_seal(payload)
        with pytest.raises(AssessmentJobError):
            load_assessment_job_output(
                write(payload),
                snapshot["input_hash"],
                expected_kind=snapshot["kind"],
                expected_snapshot=snapshot,
            )

    # A re-sealed metric replacement passes the standalone envelope but
    # can never replace the producer evidence the JobManager captured.
    payload = tampered("metric")
    payload[OUTPUT_SEAL_FIELD] = output_seal(payload)
    with pytest.raises(AssessmentJobError, match="producer digest"):
        load_assessment_job_output(
            write(payload),
            snapshot["input_hash"],
            expected_kind=snapshot["kind"],
            expected_snapshot=snapshot,
            expected_output_seal=producer,
        )
    # A forged producer digest is refused too.
    with pytest.raises(AssessmentJobError, match="producer digest"):
        load_assessment_job_output(
            write(good),
            snapshot["input_hash"],
            expected_kind=snapshot["kind"],
            expected_snapshot=snapshot,
            expected_output_seal="0" * 64,
        )
    # The honest payload admits with the captured producer digest.
    admitted = load_assessment_job_output(
        output_path,
        snapshot["input_hash"],
        expected_kind=snapshot["kind"],
        expected_snapshot=snapshot,
        expected_output_seal=producer,
    )
    assert admitted["verdict"] == "pass"
