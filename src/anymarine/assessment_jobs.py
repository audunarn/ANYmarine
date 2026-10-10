"""Qt-free application assessment jobs: frozen transport + child execution.

This module owns ONLY the application job transport for the Qt assessment
workflow: freezing complete actual inputs before execution, reconstructing
private roots in the isolated child, dispatching to the existing public
application services and validating the child's output artifact for the GUI.
It contains no physics, no criteria and no persistence format of its own;
every scientific proof runs inside the child through the public service/
domain owners (``ApplicationService`` assessment APIs).

Freeze contract (submission time, caller/GUI thread, no numerics):
- ``assessment_prepare``: the project's actual bodies/equilibrium/settings/
  linear inputs/hydro provenance plus the case specification and an
  exclusive case-artifact target.
- ``assessment_single``: the trimmed actual project (with exactly the
  explicitly selected stored result), the registered case artifact payload
  and, for hydro-sourced projects, the base64 raw sidecar bytes
  hash-checked against the provenance record (same pattern as
  ``linear_job_snapshot``). Optional exclusive snapshot target.
- ``assessment_pair``: both explicit coarse/fine members' frozen data and
  an exclusive paired snapshot target.
- ``assessment_adjudicate_single`` / ``assessment_adjudicate_pair``: the
  snapshot's OWN complete bytes — every file under the snapshot root as
  (relative path, sha256, base64 payload) — captured at submission. A
  path+manifest alone is not immutable input: the child reconstructs a
  private snapshot root from the frozen bytes and never re-reads the
  mutable original root during execution.

The job ``input_hash`` is the sha256 of the canonical JSON of the whole
frozen snapshot (full input/result/case/source identity included); the
child recomputes it and refuses any mismatch. Original files may disappear
after submission; a job never depends on later external originals or on the
caller's changing active service root.

A validated NoGo assessment is a NON-SUCCESS outcome with its report
preserved: the child exits 0 with ``verdict: "nogo"`` in the output payload
and the JobManager transitions such jobs to the distinct ``nogo`` state,
never ``completed`` and never a generic error.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from .domain import (
    AnalysisSettings,
    Body,
    EquilibriumMetadata,
    HydroBridgeProvenance,
    LinearDevelopmentInputs,
    ProjectModel,
    ResultRecord,
)
from .hashing import canonical_json
from .linear import linear_input_hash
from .linear_assessment import (
    AssessmentCase,
    AssessmentError,
    load_case,
    read_case_artifact,
    read_report_artifact,
    write_case_artifact,
)
from .persistence import (
    ProjectError,
    ProjectStore,
    atomic_write_json,
    check_relative_reference,
    resolve_sidecar,
    sha256_bytes,
)
from .services import ApplicationService, ServiceError

ASSESSMENT_JOB_SCHEMA_VERSION = 1
ASSESSMENT_KIND_PREPARE = "assessment_prepare"
ASSESSMENT_KIND_SINGLE = "assessment_single"
ASSESSMENT_KIND_PAIR = "assessment_pair"
ASSESSMENT_KIND_ADJUDICATE_SINGLE = "assessment_adjudicate_single"
ASSESSMENT_KIND_ADJUDICATE_PAIR = "assessment_adjudicate_pair"
ASSESSMENT_JOB_KINDS = (
    ASSESSMENT_KIND_PREPARE,
    ASSESSMENT_KIND_SINGLE,
    ASSESSMENT_KIND_PAIR,
    ASSESSMENT_KIND_ADJUDICATE_SINGLE,
    ASSESSMENT_KIND_ADJUDICATE_PAIR,
)
VERDICT_PASS = "pass"
VERDICT_NOGO = "nogo"

EXIT_SUCCESS = 0
EXIT_INVALID_INPUT = 2
EXIT_CANCELLED = 3
EXIT_FAILURE = 4

MAX_PROGRESS_LINES = 8


class AssessmentJobError(Exception):
    """Actionable assessment-job transport error (typed refusal)."""


class _Cancelled(Exception):
    """Internal cooperative-cancellation signal."""


# --------------------------------------------------------------- freezing


def _job_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def _freeze_project(project: ProjectModel, results=()) -> dict:
    """The actual project intent, trimmed to the selected result(s) only.

    Unrelated results are not frozen: the job's identity covers exactly the
    material the assessment may touch. Round-trips through the public
    domain records; no new persistence format.
    """

    return {
        "name": project.name,
        "bodies": [body.to_dict() for body in project.bodies],
        "equilibrium": project.equilibrium.to_dict(),
        "settings": project.settings.to_dict(),
        "results": [result.to_dict() for result in results],
        "linear_inputs": (
            project.linear_inputs.to_dict()
            if project.linear_inputs is not None
            else None
        ),
        "hydro_provenance": (
            project.hydro_provenance.to_dict()
            if project.hydro_provenance is not None
            else None
        ),
    }


def project_from_frozen(data: dict) -> ProjectModel:
    """Rebuild a frozen/child-serialized project through the public records.

    Transport-level rebuild only (no numerics, no source proof): used by
    the child to reconstruct frozen job inputs and by the GUI to rebuild
    the child-returned reproduced snapshot records for replay.
    """

    try:
        return ProjectModel(
            name=str(data["name"]),
            bodies=tuple(Body.from_dict(item) for item in data["bodies"]),
            equilibrium=EquilibriumMetadata.from_dict(
                data.get("equilibrium") or {}
            ),
            settings=AnalysisSettings.from_dict(data.get("settings") or {}),
            results=tuple(
                ResultRecord.from_dict(item) for item in data.get("results") or ()
            ),
            linear_inputs=(
                LinearDevelopmentInputs.from_dict(data["linear_inputs"])
                if data.get("linear_inputs") is not None
                else None
            ),
            hydro_provenance=(
                HydroBridgeProvenance.from_dict(data["hydro_provenance"])
                if data.get("hydro_provenance") is not None
                else None
            ),
        )
    except (KeyError, ValueError, TypeError) as error:
        raise AssessmentJobError(f"the frozen project payload is invalid: {error}")


def _freeze_hydro_payloads(
    provenance: HydroBridgeProvenance | None, root: Path | None
) -> dict | None:
    """Embed the authoritative raw sidecar bytes, hash-checked in-place.

    The bytes are checked against the provenance record's declared raw
    sha256 values at submission; the child re-checks every payload before
    any source is decoded. A hydro project without its root cannot freeze
    sources and is refused.
    """

    if provenance is None:
        return None
    if root is None:
        raise AssessmentJobError(
            "a hydro-sourced assessment job requires the project root "
            "holding the authoritative sidecars; without the root the "
            "sources cannot be frozen and the job is refused"
        )
    payloads = {}
    for role, reference, expected in ApplicationService._hydro_artifact_roles(
        provenance
    ):
        source = resolve_sidecar(Path(root), reference)
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise AssessmentJobError(
                f"cannot read the authoritative {role} sidecar {reference!r} "
                f"for the assessment job: {error}"
            ) from error
        if sha256_bytes(payload) != expected:
            raise AssessmentJobError(
                f"the {role} sidecar {reference!r} does not match the hydro "
                "provenance raw sha256; refusing the assessment job"
            )
        payloads[role] = {
            "reference": reference,
            "sha256": expected,
            "data": base64.b64encode(payload).decode("ascii"),
        }
    return payloads


def _freeze_tree(root: Path) -> dict:
    """Capture every file under *root* as (relative id, sha256, bytes).

    Relative identities are validated POSIX references; symlinks and
    escapes are refused. The frozen bytes are the immutable input — the
    child reconstructs a private root from them and never re-reads the
    (mutable, possibly disappearing) original root.
    """

    root = Path(root)
    if not root.is_dir():
        raise AssessmentJobError(
            f"the snapshot root {root} is not a directory; unknown or "
            "missing snapshots are refused"
        )
    files = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            relative = path.relative_to(root).as_posix()
            check_relative_reference(relative)
            if path.is_symlink():
                raise AssessmentJobError(
                    f"the snapshot root {root} contains the symlink "
                    f"{relative!r}; unsafe snapshot content is refused"
                )
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise AssessmentJobError(
                    f"cannot freeze the snapshot file {relative!r}: {error}"
                ) from error
            files[relative] = {
                "sha256": sha256_bytes(payload),
                "data": base64.b64encode(payload).decode("ascii"),
            }
    if not files:
        raise AssessmentJobError(
            f"the snapshot root {root} holds no files; unknown or "
            "incomplete snapshots are refused"
        )
    return files


def _tree_manifest(root: Path) -> dict:
    """Relative path -> sha256 for every file under a freshly written root."""

    manifest = {}
    for dirpath, _dirnames, filenames in os.walk(Path(root)):
        for name in filenames:
            path = Path(dirpath) / name
            relative = path.relative_to(Path(root)).as_posix()
            manifest[relative] = sha256_bytes(path.read_bytes())
    return manifest


def _context(project: ProjectModel, result: ResultRecord | None, case: AssessmentCase | None) -> dict:
    """Bound identity shown next to the evidence (never a proof by itself)."""

    inputs = project.linear_inputs
    return {
        "project_name": project.name,
        "input_hash": linear_input_hash(inputs) if inputs is not None else None,
        "source_route": inputs.provenance.source if inputs is not None else None,
        "result_id": result.result_id if result is not None else None,
        "body_ids": list(result.body_ids) if result is not None else [],
        "case_id": case.case_id if case is not None else None,
    }


def _seal(snapshot: dict) -> dict:
    snapshot["input_hash"] = _job_hash(snapshot)
    return snapshot


OUTPUT_SEAL_FIELD = "output_seal"


def output_seal(payload: dict) -> str:
    """Canonical SHA256 over every child output field except the seal itself.

    Application transport integrity only: the seal ties the admitted
    payload to the bytes the child actually produced. It is not a
    scientific proof and replaces none; all full proof stays in the
    child's public services.
    """

    unsealed = {key: value for key, value in payload.items() if key != OUTPUT_SEAL_FIELD}
    return hashlib.sha256(canonical_json(unsealed).encode("utf-8")).hexdigest()


# ------------------------------------------------------------- job builders


def prepare_assessment_job(
    project: ProjectModel,
    spec: dict,
    case_target: os.PathLike | str,
    service_root: os.PathLike | str | None = None,
) -> dict:
    """Freeze a case-registration job: project + spec + exclusive target.

    A hydro-sourced project freezes its authoritative raw sidecar payloads
    (hash-checked against the provenance) exactly like the assessment jobs:
    the child re-proves the sources from the frozen bytes and the original
    files may disappear after submission.
    """

    if project.linear_inputs is None:
        raise AssessmentJobError(
            "the project has no stored linear inputs; there is nothing to "
            "register an assessment case against"
        )
    target = Path(case_target)
    if target.exists():
        raise AssessmentJobError(
            f"the case artifact target {target} already exists; existing "
            "outputs are preserved and never overwritten"
        )
    if not isinstance(spec, dict):
        raise AssessmentJobError("the case specification must be a JSON object")
    payloads = _freeze_hydro_payloads(project.hydro_provenance, service_root)
    snapshot = {
        "schema_version": ASSESSMENT_JOB_SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND_PREPARE,
        "project": _freeze_project(project),
        "spec": json.loads(json.dumps(spec, allow_nan=False)),
        "case_target": str(target),
        "context": _context(project, None, None),
    }
    if payloads is not None:
        snapshot["hydro_source_payloads"] = payloads
    return _seal(snapshot)


def single_assessment_job(
    project: ProjectModel,
    result: ResultRecord,
    case: AssessmentCase,
    service_root: os.PathLike | str | None = None,
    snapshot_target: os.PathLike | str | None = None,
) -> dict:
    """Freeze a single-result assessment job (optional snapshot save).

    Submission-time identity checks are light string/hash comparisons for
    early typed refusal only; every eligibility and source proof re-runs
    in the child through the public service.
    """

    inputs = project.linear_inputs
    if inputs is None:
        raise AssessmentJobError(
            "the project has no stored linear inputs; there is nothing to "
            "assess against"
        )
    stored_ids = {record.result_id for record in project.results}
    if result.result_id not in stored_ids:
        raise AssessmentJobError(
            "the selected result is not one of the project's stored results "
            "(stale or foreign result); refusing the assessment job"
        )
    if result.input_hash != linear_input_hash(inputs):
        raise AssessmentJobError(
            "the selected result does not match the stored linear inputs "
            "(stale or foreign result); refusing the assessment job"
        )
    if case.input_hash != linear_input_hash(inputs):
        raise AssessmentJobError(
            "the registered case does not describe the project's stored "
            "linear inputs; refusing the assessment job"
        )
    if case.result_id is not None and case.result_id != result.result_id:
        raise AssessmentJobError(
            "the registered case selects a different result than the "
            "explicitly selected one; refusing the assessment job"
        )
    payloads = _freeze_hydro_payloads(project.hydro_provenance, service_root)
    target = None
    if snapshot_target is not None:
        target = str(Path(snapshot_target))
        if Path(target).exists():
            raise AssessmentJobError(
                f"the assessment snapshot target {target} already exists; "
                "existing outputs (including NoGo/failed reports) are "
                "preserved and never overwritten"
            )
    snapshot = {
        "schema_version": ASSESSMENT_JOB_SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND_SINGLE,
        "project": _freeze_project(project, results=(result,)),
        "case": case.to_dict(),
        "context": _context(project, result, case),
        "snapshot_target": target,
    }
    if payloads is not None:
        snapshot["hydro_source_payloads"] = payloads
    return _seal(snapshot)


def pair_assessment_job(
    coarse_project: ProjectModel,
    coarse_result: ResultRecord,
    coarse_case: AssessmentCase,
    coarse_root: os.PathLike | str | None,
    fine_project: ProjectModel,
    fine_result: ResultRecord,
    fine_case: AssessmentCase,
    fine_root: os.PathLike | str | None,
    out_root: os.PathLike | str,
) -> dict:
    """Freeze an explicit coarse/fine pair job with an exclusive target."""

    def freeze_member(project, result, case, root):
        inputs = project.linear_inputs
        if inputs is None:
            raise AssessmentJobError(
                "a paired member's project has no stored linear inputs; "
                "there is nothing to assess"
            )
        stored_ids = {record.result_id for record in project.results}
        if result.result_id not in stored_ids:
            raise AssessmentJobError(
                "the member's selected result is not one of its project's "
                "stored results (stale or foreign result); refusing the "
                "paired assessment job"
            )
        if result.input_hash != linear_input_hash(inputs):
            raise AssessmentJobError(
                "the member's result does not match its project's stored "
                "linear inputs (stale or foreign result); refusing the "
                "paired assessment job"
            )
        if case.input_hash != linear_input_hash(inputs):
            raise AssessmentJobError(
                "the member's registered case does not describe its "
                "project's stored linear inputs; refusing the paired "
                "assessment job"
            )
        if case.result_id is not None and case.result_id != result.result_id:
            raise AssessmentJobError(
                "the member's registered case selects a different result "
                "than the explicitly selected one; refusing the paired "
                "assessment job"
            )
        member = {
            "project": _freeze_project(project, results=(result,)),
            "case": case.to_dict(),
            "context": _context(project, result, case),
        }
        payloads = _freeze_hydro_payloads(project.hydro_provenance, root)
        if payloads is not None:
            member["hydro_source_payloads"] = payloads
        return member

    coarse = freeze_member(coarse_project, coarse_result, coarse_case, coarse_root)
    fine = freeze_member(fine_project, fine_result, fine_case, fine_root)
    target = Path(out_root)
    if target.exists():
        raise AssessmentJobError(
            f"the paired snapshot target {target} already exists; existing "
            "outputs (including NoGo/failed records) are preserved and "
            "never overwritten"
        )
    snapshot = {
        "schema_version": ASSESSMENT_JOB_SCHEMA_VERSION,
        "kind": ASSESSMENT_KIND_PAIR,
        "coarse": coarse,
        "fine": fine,
        "snapshot_target": str(target),
    }
    return _seal(snapshot)


def adjudication_job(
    snapshot_root: os.PathLike | str,
    case_relative: str | None = None,
    report_relative: str | None = None,
) -> dict:
    """Freeze a snapshot-reopen job: the snapshot's OWN complete bytes.

    The original root is captured once at submission; the child reproduces
    from a private reconstruction and the original files may disappear.
    A paired snapshot (pair.json present) is adjudicated as a pair; a
    single snapshot requires the case/report artifact identities (they
    default to the unique artifacts when exactly one of each exists).
    """

    root = Path(snapshot_root)
    files = _freeze_tree(root)
    kind = ASSESSMENT_KIND_ADJUDICATE_PAIR
    if "pair.json" not in files:
        kind = ASSESSMENT_KIND_ADJUDICATE_SINGLE
        case_relative = _default_artifact(files, "cases/", case_relative, "case")
        report_relative = _default_artifact(
            files, "reports/", report_relative, "report"
        )
    snapshot = {
        "schema_version": ASSESSMENT_JOB_SCHEMA_VERSION,
        "kind": kind,
        "snapshot_root": str(root),
        "files": files,
    }
    if kind == ASSESSMENT_KIND_ADJUDICATE_SINGLE:
        snapshot["case_relative"] = case_relative
        snapshot["report_relative"] = report_relative
    return _seal(snapshot)


def _default_artifact(
    files: dict, prefix: str, explicit: str | None, label: str
) -> str:
    if explicit is not None:
        if explicit not in files:
            raise AssessmentJobError(
                f"the snapshot holds no {label} artifact {explicit!r}; "
                "missing artifacts are refused"
            )
        return explicit
    matches = sorted(
        relative for relative in files if relative.startswith(prefix)
    )
    if len(matches) != 1:
        raise AssessmentJobError(
            f"the snapshot holds {len(matches)} {label} artifacts under "
            f"{prefix!r}; pass an explicit selection"
        )
    return matches[0]


# ---------------------------------------------------------- child execution


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _check_cancel(cancel_path: Path | None) -> None:
    if cancel_path is not None and cancel_path.exists():
        raise _Cancelled()


def _materialize_payloads(payloads: dict, root: Path) -> None:
    """Write the frozen sidecar bytes into a private root, re-checked."""

    for role, entry in payloads.items():
        try:
            payload = base64.b64decode(
                str(entry["data"]).encode("ascii"), validate=True
            )
        except (KeyError, TypeError, ValueError, binascii.Error) as error:
            raise AssessmentJobError(
                f"the frozen {role} source payload is invalid: {error}"
            ) from error
        actual = hashlib.sha256(payload).hexdigest()
        if actual != str(entry["sha256"]):
            raise AssessmentJobError(
                f"the frozen {role} source payload hashes to {actual} but "
                f"the job declares {entry['sha256']!r}; an inconsistent "
                "raw byte identity is refused"
            )
        reference = str(entry["reference"])
        relative = check_relative_reference(reference)
        target = root / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _materialize_tree(files: dict, root: Path) -> None:
    """Reconstruct the private snapshot root from the frozen bytes."""

    for relative, entry in files.items():
        check_relative_reference(relative)
        try:
            payload = base64.b64decode(
                str(entry["data"]).encode("ascii"), validate=True
            )
        except (KeyError, TypeError, ValueError, binascii.Error) as error:
            raise AssessmentJobError(
                f"the frozen snapshot file {relative!r} is invalid: {error}"
            ) from error
        actual = hashlib.sha256(payload).hexdigest()
        if actual != str(entry["sha256"]):
            raise AssessmentJobError(
                f"the frozen snapshot file {relative!r} hashes to {actual} "
                f"but the job declares {entry['sha256']!r}; tampered "
                "frozen input is refused"
            )
        target = root / Path(*check_relative_reference(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _private_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="anymarine-assessment-"))


def _load_member_case(member: dict) -> AssessmentCase:
    try:
        return load_case(member["case"])
    except AssessmentError as error:
        raise AssessmentJobError(
            f"the frozen member case payload is invalid: {error}"
        ) from error


def _execute_prepare(snapshot: dict, cancel_path: Path | None) -> dict:
    _check_cancel(cancel_path)
    project = project_from_frozen(snapshot["project"])
    payloads = snapshot.get("hydro_source_payloads")
    root = None
    if isinstance(payloads, dict):
        root = _private_root()
        _materialize_payloads(payloads, root)
    service = ApplicationService(root)
    _check_cancel(cancel_path)
    try:
        case = service.prepare_linear_assessment(project, snapshot["spec"])
    except ServiceError as error:
        raise AssessmentJobError(str(error)) from error
    _check_cancel(cancel_path)
    target = Path(str(snapshot["case_target"]))
    if target.exists():
        raise AssessmentJobError(
            f"the case artifact target {target} already exists; existing "
            "outputs are preserved and never overwritten"
        )
    write_case_artifact(case, target)
    return {
        "kind": ASSESSMENT_KIND_PREPARE,
        "input_hash": snapshot["input_hash"],
        "case": case.to_dict(),
        "case_hash": case.case_hash,
        "case_target": str(target),
        "context": snapshot["context"],
    }


def _execute_single(snapshot: dict, cancel_path: Path | None) -> dict:
    _check_cancel(cancel_path)
    project = project_from_frozen(snapshot["project"])
    if len(project.results) != 1:
        raise AssessmentJobError(
            "the frozen project must hold exactly the explicitly selected "
            "result; a stripped or foreign result set is refused"
        )
    result = project.results[0]
    try:
        case = load_case(snapshot["case"])
    except AssessmentError as error:
        raise AssessmentJobError(
            f"the frozen case payload is invalid: {error}"
        ) from error
    payloads = snapshot.get("hydro_source_payloads")
    root = None
    if isinstance(payloads, dict):
        root = _private_root()
        _materialize_payloads(payloads, root)
    service = ApplicationService(root)
    _check_cancel(cancel_path)
    _emit("PROGRESS 0.25")
    try:
        report = service.assess_linear_result(project, result, case)
    except ServiceError as error:
        raise AssessmentJobError(str(error)) from error
    _check_cancel(cancel_path)
    _emit("PROGRESS 0.6")
    saved = None
    target = snapshot.get("snapshot_target")
    if target is not None:
        if Path(str(target)).exists():
            raise AssessmentJobError(
                f"the assessment snapshot target {target} already exists; "
                "existing outputs (including NoGo/failed reports) are "
                "preserved and never overwritten"
            )
        try:
            service.save_assessment_snapshot(
                project, result, case, report, Path(str(target))
            )
        except ServiceError as error:
            raise AssessmentJobError(str(error)) from error
        saved = {
            "root": str(target),
            "files": _tree_manifest(Path(str(target))),
        }
    _emit("PROGRESS 0.95")
    return {
        "kind": ASSESSMENT_KIND_SINGLE,
        "input_hash": snapshot["input_hash"],
        "verdict": report.verdict,
        "report": report.to_dict(),
        "snapshot": saved,
        "context": snapshot["context"],
    }


def _execute_pair(snapshot: dict, cancel_path: Path | None) -> dict:
    members = {}
    roots = {}
    for role in ("coarse", "fine"):
        _check_cancel(cancel_path)
        member = snapshot[role]
        project = project_from_frozen(member["project"])
        if len(project.results) != 1:
            raise AssessmentJobError(
                f"the frozen {role} member must hold exactly its explicitly "
                "selected result; a stripped or foreign result set is refused"
            )
        members[role] = {
            "project": project,
            "result": project.results[0],
            "case": _load_member_case(member),
        }
        payloads = member.get("hydro_source_payloads")
        root = None
        if isinstance(payloads, dict):
            root = _private_root()
            _materialize_payloads(payloads, root)
        roots[role] = root
    service = ApplicationService()
    _emit("PROGRESS 0.3")
    target = Path(str(snapshot["snapshot_target"]))
    if target.exists():
        raise AssessmentJobError(
            f"the paired snapshot target {target} already exists; existing "
            "outputs (including NoGo/failed records) are preserved and "
            "never overwritten"
        )
    try:
        service.save_paired_refinement_snapshot(
            members["coarse"]["project"],
            members["coarse"]["result"],
            members["coarse"]["case"],
            roots["coarse"],
            members["fine"]["project"],
            members["fine"]["result"],
            members["fine"]["case"],
            roots["fine"],
            target,
        )
    except ServiceError as error:
        raise AssessmentJobError(str(error)) from error
    _check_cancel(cancel_path)
    _emit("PROGRESS 0.9")
    pair_path = target / "pair.json"
    try:
        artifact = json.loads(pair_path.read_text(encoding="utf-8"))
        pair = artifact["pair"]
    except (OSError, ValueError, KeyError) as error:
        raise AssessmentJobError(
            f"the freshly written paired artifact is unreadable: {error}"
        ) from error
    return {
        "kind": ASSESSMENT_KIND_PAIR,
        "input_hash": snapshot["input_hash"],
        "verdict": str(pair.get("combined_verdict")),
        "pair": pair,
        "snapshot": {"root": str(target), "files": _tree_manifest(target)},
        "context": {
            "coarse": snapshot["coarse"]["context"],
            "fine": snapshot["fine"]["context"],
        },
        "replay": _pair_replay_records(target, pair),
    }


def _pair_replay_records(target: Path, pair: dict) -> dict:
    """Captured authoritative per-member replay records.

    Read back from the snapshot the child itself just wrote (in the
    child, never in the GUI): the GUI replays these captured arrays and
    never re-reads the mutable output root or re-runs numerics.
    """

    replay = {}
    for role in ("coarse", "fine"):
        section = (pair.get("members") or {}).get(role) or {}
        replay[role] = _snapshot_replay_records(
            target / role / "project", section.get("result_id"), f"{role} member"
        )
    return replay


def _json_adjudication(outcome: dict) -> dict:
    """Public serialization of an adjudication outcome for job transport."""

    data = {}
    for key, value in (outcome or {}).items():
        if hasattr(value, "to_dict"):
            data[key] = value.to_dict()
        else:
            data[key] = value
    return data


def _replay_payload(project: ProjectModel, result: ResultRecord) -> dict:
    """The child's captured authoritative replay records (public serialization).

    The GUI rebuilds these through the public records and never re-runs
    hydro source/assessment numerics; the arrays here are the snapshot's
    own reproduced stored arrays, not pointers into a mutable root.
    """

    return {"project": project.to_dict(), "result": result.to_dict()}


def _snapshot_replay_records(
    project_root: Path, result_id: str | None, label: str
) -> dict:
    """Select the snapshot's own stored result from a private member root."""

    try:
        project = ProjectStore(project_root).load()
    except ProjectError as error:
        raise AssessmentJobError(
            f"the reproduced {label} snapshot project cannot be loaded: {error}"
        ) from error
    if result_id is None:
        if len(project.results) != 1:
            raise AssessmentJobError(
                f"the reproduced {label} snapshot holds no unique stored "
                "result; member-specific replay is refused"
            )
        return _replay_payload(project, project.results[0])
    matches = [
        record for record in project.results if record.result_id == result_id
    ]
    if len(matches) != 1:
        raise AssessmentJobError(
            f"the reproduced {label} snapshot holds no unique stored result "
            f"{result_id!r}; member-specific replay is refused"
        )
    return _replay_payload(project, matches[0])


def _execute_adjudicate_single(snapshot: dict, cancel_path: Path | None) -> dict:
    _check_cancel(cancel_path)
    root = _private_root()
    _materialize_tree(snapshot["files"], root)
    case_relative = str(snapshot["case_relative"])
    report_relative = str(snapshot["report_relative"])
    service = ApplicationService()
    _emit("PROGRESS 0.3")
    try:
        outcome = service.adjudicate_linear_assessment(
            root, root / case_relative, root / report_relative
        )
        stored = read_report_artifact(root / report_relative)
        case = read_case_artifact(root / case_relative)
    except (ServiceError, ProjectError, AssessmentError) as error:
        raise AssessmentJobError(str(error)) from error
    _check_cancel(cancel_path)
    _emit("PROGRESS 0.9")
    replay = _snapshot_replay_records(
        root / "project", case.result_id, "assessment"
    )
    replay_result_id = replay["result"].get("result_id")
    if str(replay_result_id) != str(stored.to_dict().get("result_id") or ""):
        raise AssessmentJobError(
            "the reproduced snapshot's own stored result does not match the "
            "stored report's result identity; relabelled evidence is refused"
        )
    return {
        "kind": ASSESSMENT_KIND_ADJUDICATE_SINGLE,
        "input_hash": snapshot["input_hash"],
        "verdict": str(outcome.get("verdict")),
        "adjudication": _json_adjudication(outcome),
        "report": stored.to_dict(),
        "case": case.to_dict(),
        "snapshot_root": snapshot["snapshot_root"],
        "files_manifest": {
            relative: entry["sha256"]
            for relative, entry in snapshot["files"].items()
        },
        "context": _context_from_report(stored),
        "replay": replay,
    }


def _context_from_report(report) -> dict:
    data = report.to_dict()
    return {
        "project_name": None,
        "input_hash": data.get("input_hash"),
        "source_route": None,
        "result_id": data.get("result_id"),
        "body_ids": [],
        "case_id": data.get("case_id"),
    }


def _execute_adjudicate_pair(snapshot: dict, cancel_path: Path | None) -> dict:
    _check_cancel(cancel_path)
    root = _private_root()
    _materialize_tree(snapshot["files"], root)
    service = ApplicationService()
    _emit("PROGRESS 0.3")
    try:
        outcome = service.adjudicate_paired_refinement_snapshot(root)
        artifact = json.loads((root / "pair.json").read_text(encoding="utf-8"))
        pair = artifact["pair"]
    except (ServiceError, ProjectError, AssessmentError) as error:
        raise AssessmentJobError(str(error)) from error
    _check_cancel(cancel_path)
    _emit("PROGRESS 0.9")
    replay = {}
    for role in ("coarse", "fine"):
        section = (pair.get("members") or {}).get(role) or {}
        replay[role] = _snapshot_replay_records(
            root / role / "project", section.get("result_id"), f"{role} member"
        )
    return {
        "kind": ASSESSMENT_KIND_ADJUDICATE_PAIR,
        "input_hash": snapshot["input_hash"],
        "verdict": str(outcome.get("combined_verdict")),
        "adjudication": _json_adjudication(outcome),
        "pair": pair,
        "snapshot_root": snapshot["snapshot_root"],
        "files_manifest": {
            relative: entry["sha256"]
            for relative, entry in snapshot["files"].items()
        },
        "context": {
            role: {
                "project_name": None,
                "input_hash": section.get("input_hash"),
                "source_route": None,
                "result_id": section.get("result_id"),
                "body_ids": [],
                "case_id": section.get("case_id"),
            }
            for role, section in pair.get("members", {}).items()
        },
        "replay": replay,
    }


def execute(snapshot: dict, output_path: Path, cancel_path: Path | None = None) -> int:
    """Run one frozen assessment job in this (child) process; return exit code.

    Headless: no Qt, no spawned processes. All scientific work goes through
    the public application services on private reconstructed roots. Output
    is written atomically only after the complete payload exists.
    """

    try:
        if not isinstance(snapshot, dict):
            raise AssessmentJobError("the job snapshot is not a JSON object")
        if snapshot.get("schema_version") != ASSESSMENT_JOB_SCHEMA_VERSION:
            raise AssessmentJobError(
                f"unsupported assessment job schema_version "
                f"{snapshot.get('schema_version')!r}"
            )
        kind = snapshot.get("kind")
        if kind not in ASSESSMENT_JOB_KINDS:
            raise AssessmentJobError(f"unsupported assessment job kind {kind!r}")
        declared = str(snapshot.get("input_hash", ""))
        recomputed = _job_hash(
            {
                key: value
                for key, value in snapshot.items()
                if key != "input_hash"
            }
        )
        if recomputed != declared:
            raise AssessmentJobError(
                f"the job input_hash {declared[:12]}... does not match the "
                f"recomputed frozen payload {recomputed[:12]}...; refusing "
                "the job"
            )
        if kind == ASSESSMENT_KIND_PREPARE:
            payload = _execute_prepare(snapshot, cancel_path)
        elif kind == ASSESSMENT_KIND_SINGLE:
            payload = _execute_single(snapshot, cancel_path)
        elif kind == ASSESSMENT_KIND_PAIR:
            payload = _execute_pair(snapshot, cancel_path)
        elif kind == ASSESSMENT_KIND_ADJUDICATE_SINGLE:
            payload = _execute_adjudicate_single(snapshot, cancel_path)
        else:
            payload = _execute_adjudicate_pair(snapshot, cancel_path)
    except _Cancelled:
        _emit("CANCELLED")
        return EXIT_CANCELLED
    except AssessmentJobError as error:
        print(f"assessment job refused: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except (KeyError, ValueError, TypeError) as error:
        print(f"invalid assessment job snapshot: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception as error:  # noqa: BLE001 - fail closed, never crash-trace
        print(f"assessment worker failure: {error}", file=sys.stderr)
        return EXIT_FAILURE
    try:
        payload[OUTPUT_SEAL_FIELD] = output_seal(payload)
        atomic_write_json(Path(output_path), payload)
    except (OSError, ValueError, TypeError) as error:
        print(f"assessment worker failure: {error}", file=sys.stderr)
        return EXIT_FAILURE
    _emit(f"OUTPUT_SHA256 {payload[OUTPUT_SEAL_FIELD]}")
    _emit("PROGRESS 1.0000")
    _emit("DONE")
    return EXIT_SUCCESS


# ------------------------------------------------------------ GUI validation


def _require(payload: dict, field: str, label: str):
    value = payload.get(field)
    if value is None or value == "" or value == {} or value == []:
        raise AssessmentJobError(
            f"assessment output is missing the required {label} payload "
            f"({field!r}); partial evidence is refused"
        )
    return value


def _require_verdict(payload: dict) -> str:
    verdict = payload.get("verdict")
    if verdict not in (VERDICT_PASS, VERDICT_NOGO):
        raise AssessmentJobError(
            f"assessment output declares unknown verdict {verdict!r}; "
            "expected 'pass' or 'nogo'"
        )
    return str(verdict)


def _validate_checks(checks, label: str) -> None:
    if not isinstance(checks, list) or not checks:
        raise AssessmentJobError(
            f"the {label} report carries no engineering checks; partial "
            "evidence is refused"
        )
    for check in checks:
        if not isinstance(check, dict) or "name" not in check or "passed" not in check:
            raise AssessmentJobError(
                f"the {label} report holds a malformed engineering check; "
                "partial evidence is refused"
            )


def _validate_prepare_output(payload: dict, expected: dict | None) -> None:
    if payload.get("verdict") is not None:
        raise AssessmentJobError(
            "a case-registration output must not declare an assessment "
            "verdict; relabelled evidence is refused"
        )
    case_data = _require(payload, "case", "registered case")
    case_hash = _require(payload, "case_hash", "case hash")
    case_target = _require(payload, "case_target", "case artifact target")
    _require(payload, "context", "bound identity")
    if not isinstance(case_data, dict):
        raise AssessmentJobError("the registered case payload is not an object")
    try:
        case = load_case(case_data)
    except AssessmentError as error:
        raise AssessmentJobError(
            f"the registered case payload is invalid: {error}"
        ) from error
    if case.case_hash != str(case_hash):
        raise AssessmentJobError(
            "the registered case payload does not reproduce its declared "
            "case hash; relabelled evidence is refused"
        )
    if expected is not None:
        if str(case_target) != str(expected.get("case_target")):
            raise AssessmentJobError(
                "the registered case artifact target does not match the "
                "job's exclusive target; foreign output is refused"
            )
        if case.case_id != str((expected.get("spec") or {}).get("case_id")):
            raise AssessmentJobError(
                "the registered case identity does not match the frozen "
                "specification; relabelled evidence is refused"
            )


def _validate_single_output(payload: dict, expected: dict | None) -> None:
    verdict = _require_verdict(payload)
    report = _require(payload, "report", "assessment report")
    context = _require(payload, "context", "bound identity")
    if not isinstance(report, dict) or not isinstance(context, dict):
        raise AssessmentJobError(
            "the assessment report/context payloads are not objects"
        )
    if str(report.get("verdict")) != verdict:
        raise AssessmentJobError(
            "the assessment verdict is inconsistent with the stored report; "
            "relabelled evidence is refused"
        )
    _validate_checks(report.get("checks"), "assessment")
    if not isinstance(report.get("failed"), list):
        raise AssessmentJobError(
            "the assessment report carries no failed-check record; "
            "partial evidence is refused"
        )
    if expected is not None:
        frozen_results = (expected.get("project") or {}).get("results") or []
        if len(frozen_results) != 1:
            raise AssessmentJobError(
                "the expected job input does not hold exactly the selected "
                "result; refusing the evidence"
            )
        expected_result_id = str(frozen_results[0].get("result_id"))
        expected_case_id = str((expected.get("case") or {}).get("case_id"))
        expected_input_hash = str((expected.get("context") or {}).get("input_hash"))
        if str(context.get("result_id")) != expected_result_id:
            raise AssessmentJobError(
                "the assessment evidence does not describe the explicitly "
                "selected result; foreign or relabelled output is refused"
            )
        if str(context.get("case_id")) != expected_case_id:
            raise AssessmentJobError(
                "the assessment evidence does not describe the registered "
                "case; foreign or relabelled output is refused"
            )
        if str(context.get("input_hash")) != expected_input_hash:
            raise AssessmentJobError(
                "the assessment evidence does not carry the job's bound "
                "input hash; foreign output is refused"
            )
        if str(report.get("result_id")) != expected_result_id:
            raise AssessmentJobError(
                "the report's result identity does not match the frozen "
                "selected result; relabelled evidence is refused"
            )
        if str(report.get("case_id")) != expected_case_id:
            raise AssessmentJobError(
                "the report's case identity does not match the frozen "
                "registered case; relabelled evidence is refused"
            )
        expected_body_ids = [str(b) for b in frozen_results[0].get("body_ids") or []]
        if [str(b) for b in context.get("body_ids") or []] != expected_body_ids:
            raise AssessmentJobError(
                "the assessment evidence does not describe the selected "
                "result's bodies; foreign or relabelled output is refused"
            )
        frozen_case = expected.get("case")
        if isinstance(frozen_case, dict):
            try:
                expected_case_hash = load_case(frozen_case).case_hash
            except AssessmentError as error:
                raise AssessmentJobError(
                    f"the expected frozen case payload is invalid: {error}"
                ) from error
            if str(report.get("case_hash")) != expected_case_hash:
                raise AssessmentJobError(
                    "the report's case hash does not match the frozen "
                    "registered case; relabelled evidence is refused"
                )
    saved = payload.get("snapshot")
    target = (expected or {}).get("snapshot_target")
    if target is None:
        if saved is None:
            return
        if expected is not None:
            raise AssessmentJobError(
                "the job did not request a snapshot save but the output "
                "declares one; foreign output is refused"
            )
        # Lighter direct admission (no frozen job at hand): the manifest is
        # still verified against the actual files on disk.
        if not isinstance(saved, dict) or not isinstance(saved.get("files"), dict):
            raise AssessmentJobError(
                "a snapshot-saving assessment output must carry its file "
                "manifest"
            )
        verify_snapshot_manifest(saved.get("root"), saved.get("files"))
        return
    if not isinstance(saved, dict) or not isinstance(saved.get("files"), dict):
        raise AssessmentJobError(
            "a snapshot-saving assessment output must carry its file manifest"
        )
    if str(saved.get("root")) != str(target):
        raise AssessmentJobError(
            "the saved snapshot root does not match the job's exclusive "
            "target; foreign output is refused"
        )
    verify_snapshot_manifest(saved.get("root"), saved.get("files"))


def _validate_pair_output(payload: dict, expected: dict | None) -> None:
    verdict = _require_verdict(payload)
    pair = _require(payload, "pair", "paired assessment record")
    saved = _require(payload, "snapshot", "paired snapshot")
    if not isinstance(pair, dict) or not isinstance(saved, dict):
        raise AssessmentJobError(
            "the paired assessment payloads are not objects"
        )
    if str(pair.get("combined_verdict")) != verdict:
        raise AssessmentJobError(
            "the combined verdict is inconsistent with the paired record; "
            "relabelled evidence is refused"
        )
    members = pair.get("members")
    if not isinstance(members, dict) or set(members) != {"coarse", "fine"}:
        raise AssessmentJobError(
            "the paired record must hold exactly the coarse and fine "
            "members; partial evidence is refused"
        )
    for role, member in members.items():
        if not isinstance(member, dict) or not isinstance(member.get("report"), dict):
            raise AssessmentJobError(
                f"the {role} member carries no full report; partial "
                "evidence is refused"
            )
        _validate_checks(member["report"].get("checks"), f"{role} member")
    replay = _require(payload, "replay", "captured member replay records")
    if not isinstance(replay, dict) or set(replay) != {"coarse", "fine"}:
        raise AssessmentJobError(
            "the captured replay must hold exactly the coarse and fine "
            "members; partial evidence is refused"
        )
    for role in ("coarse", "fine"):
        _validate_replay_entry(replay.get(role), f"{role} member")
        if str(((replay.get(role) or {}).get("result") or {}).get(
            "result_id"
        )) != str((members.get(role) or {}).get("result_id")):
            raise AssessmentJobError(
                f"the captured {role} replay result does not match the "
                "paired record's member identity; relabelled evidence is "
                "refused"
            )
    if not isinstance(saved.get("files"), dict):
        raise AssessmentJobError(
            "a snapshot-saving assessment output must carry its file manifest"
        )
    if expected is not None:
        if str(saved.get("root")) != str(expected.get("snapshot_target")):
            raise AssessmentJobError(
                "the saved paired snapshot root does not match the job's "
                "exclusive target; foreign output is refused"
            )
        for role in ("coarse", "fine"):
            frozen_results = (
                ((expected.get(role) or {}).get("project") or {}).get("results")
                or []
            )
            if len(frozen_results) != 1:
                raise AssessmentJobError(
                    f"the expected {role} member input does not hold exactly "
                    "its selected result; refusing the evidence"
                )
            expected_result_id = str(frozen_results[0].get("result_id"))
            if str((members.get(role) or {}).get("result_id")) != expected_result_id:
                raise AssessmentJobError(
                    f"the {role} member's result identity does not match the "
                    "frozen selected result; relabelled evidence is refused"
                )
    verify_snapshot_manifest(saved.get("root"), saved.get("files"))


def _validate_replay_entry(entry, label: str) -> None:
    if not isinstance(entry, dict):
        raise AssessmentJobError(
            f"the reproduced {label} replay payload is not an object; "
            "partial evidence is refused"
        )
    project_data = entry.get("project")
    result_data = entry.get("result")
    if not isinstance(project_data, dict) or not isinstance(result_data, dict):
        raise AssessmentJobError(
            f"the reproduced {label} replay payload is missing its captured "
            "project/result records; partial evidence is refused"
        )
    try:
        project = project_from_frozen(project_data)
        result = ResultRecord.from_dict(result_data)
    except (AssessmentJobError, KeyError, ValueError, TypeError) as error:
        raise AssessmentJobError(
            f"the reproduced {label} replay records are invalid: {error}"
        ) from error
    matches = [
        record for record in project.results if record.result_id == result.result_id
    ]
    if len(matches) != 1 or matches[0].to_dict() != result.to_dict():
        raise AssessmentJobError(
            f"the reproduced {label} replay result is not the snapshot's own "
            "stored result; relabelled evidence is refused"
        )


def _validate_adjudicate_single_output(payload: dict) -> None:
    verdict = _require_verdict(payload)
    adjudication = _require(payload, "adjudication", "adjudication outcome")
    report = _require(payload, "report", "stored report")
    case = _require(payload, "case", "stored case")
    _require(payload, "files_manifest", "snapshot file manifest")
    replay = _require(payload, "replay", "reproduced replay records")
    if not isinstance(adjudication, dict) or not isinstance(report, dict) \
            or not isinstance(case, dict):
        raise AssessmentJobError(
            "the adjudication payloads are not objects"
        )
    if str(adjudication.get("verdict")) != verdict:
        raise AssessmentJobError(
            "the adjudication outcome is inconsistent with the declared "
            "verdict; relabelled evidence is refused"
        )
    if str(report.get("verdict")) != verdict:
        raise AssessmentJobError(
            "the stored report's verdict is inconsistent with the declared "
            "verdict; relabelled evidence is refused"
        )
    if str(report.get("case_id")) != str(case.get("case_id")):
        raise AssessmentJobError(
            "the stored report and case disagree on the case identity; "
            "relabelled evidence is refused"
        )
    if str(report.get("input_hash")) != str(case.get("input_hash")):
        raise AssessmentJobError(
            "the stored report and case disagree on the bound input hash; "
            "relabelled evidence is refused"
        )
    _validate_checks(report.get("checks"), "stored")
    _validate_replay_entry(replay, "assessment")
    if str((replay.get("result") or {}).get("result_id")) != str(
        report.get("result_id")
    ):
        raise AssessmentJobError(
            "the reproduced replay result does not match the stored "
            "report's result identity; relabelled evidence is refused"
        )


def _validate_adjudicate_pair_output(payload: dict) -> None:
    verdict = _require_verdict(payload)
    adjudication = _require(payload, "adjudication", "adjudication outcome")
    pair = _require(payload, "pair", "paired record")
    _require(payload, "files_manifest", "snapshot file manifest")
    replay = _require(payload, "replay", "reproduced replay records")
    if not isinstance(adjudication, dict) or not isinstance(pair, dict) \
            or not isinstance(replay, dict):
        raise AssessmentJobError(
            "the paired adjudication payloads are not objects"
        )
    if str(adjudication.get("combined_verdict")) != verdict:
        raise AssessmentJobError(
            "the paired adjudication outcome is inconsistent with the "
            "declared verdict; relabelled evidence is refused"
        )
    if str(pair.get("combined_verdict")) != verdict:
        raise AssessmentJobError(
            "the paired record's combined verdict is inconsistent with the "
            "declared verdict; relabelled evidence is refused"
        )
    members = pair.get("members")
    if not isinstance(members, dict) or set(members) != {"coarse", "fine"}:
        raise AssessmentJobError(
            "the paired record must hold exactly the coarse and fine "
            "members; partial evidence is refused"
        )
    if set(replay) != {"coarse", "fine"}:
        raise AssessmentJobError(
            "the reproduced replay must hold exactly the coarse and fine "
            "members; partial evidence is refused"
        )
    for role in ("coarse", "fine"):
        _validate_replay_entry(replay.get(role), f"{role} member")
        if str(((replay.get(role) or {}).get("result") or {}).get(
            "result_id"
        )) != str((members.get(role) or {}).get("result_id")):
            raise AssessmentJobError(
                f"the reproduced {role} replay result does not match the "
                "paired record's member identity; relabelled evidence is "
                "refused"
            )


def load_assessment_job_output(
    path: os.PathLike | str,
    expected_input_hash: str,
    expected_kind: str | None = None,
    expected_snapshot: dict | None = None,
    expected_output_seal: str | None = None,
) -> dict:
    """Validate a child assessment output artifact for GUI admission.

    Transport/identity/hash validation only — never a qualification claim.
    The exact expected job kind, the per-kind required payload fields, the
    closed-world case/report/pair records and the identities from the
    frozen job input are all checked, so unknown, partial, relabelled or
    foreign output is refused. The whole payload must reproduce the
    child's canonical output seal, so any field modified after production
    is refused; when the trusted producer digest captured from the worker
    stdout is supplied, even a re-sealed replacement cannot masquerade as
    the child's evidence. A snapshot payload is additionally verified
    against the actual files on disk; the scientific reproduction itself
    already ran in the child.
    """

    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError as error:
        raise AssessmentJobError(
            f"assessment worker output {target} was not produced"
        ) from error
    except OSError as error:
        raise AssessmentJobError(
            f"cannot read assessment worker output {target}: {error}"
        ) from error
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssessmentJobError(
            f"assessment worker output {target} is not valid JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise AssessmentJobError(
            f"assessment worker output {target} is not a JSON object"
        )
    kind = payload.get("kind")
    if kind not in ASSESSMENT_JOB_KINDS:
        raise AssessmentJobError(
            f"assessment worker output {target} declares unknown kind "
            f"{kind!r}"
        )
    if expected_kind is not None and str(kind) != str(expected_kind):
        raise AssessmentJobError(
            f"assessment worker output declares kind {kind!r} but the "
            f"submitted job is {expected_kind!r}; relabelled output is refused"
        )
    if str(payload.get("input_hash")) != str(expected_input_hash):
        raise AssessmentJobError(
            f"assessment output input_hash {str(payload.get('input_hash'))[:12]}... "
            f"does not match the job input snapshot "
            f"{str(expected_input_hash)[:12]}...; refusing the evidence"
        )
    declared_seal = payload.get(OUTPUT_SEAL_FIELD)
    if not isinstance(declared_seal, str) or len(declared_seal) != 64:
        raise AssessmentJobError(
            f"assessment worker output {target} carries no canonical output "
            "seal; unsealed, partial or hand-written evidence is refused"
        )
    if output_seal(payload) != declared_seal:
        raise AssessmentJobError(
            f"assessment worker output {target} does not reproduce its "
            "declared output seal; modified evidence is refused"
        )
    if expected_output_seal is not None and str(expected_output_seal) != declared_seal:
        raise AssessmentJobError(
            "the output seal does not match the trusted producer digest "
            "captured from the worker; replaced or re-sealed evidence is "
            "refused"
        )
    if kind == ASSESSMENT_KIND_PREPARE:
        _validate_prepare_output(payload, expected_snapshot)
    elif kind == ASSESSMENT_KIND_SINGLE:
        _validate_single_output(payload, expected_snapshot)
    elif kind == ASSESSMENT_KIND_PAIR:
        _validate_pair_output(payload, expected_snapshot)
    elif kind == ASSESSMENT_KIND_ADJUDICATE_SINGLE:
        _validate_adjudicate_single_output(payload)
    else:
        _validate_adjudicate_pair_output(payload)
    return payload


def verify_snapshot_manifest(root: os.PathLike | str, manifest: dict) -> None:
    """Verify an on-disk snapshot root matches a frozen file manifest."""

    base = Path(root)
    if not base.is_dir():
        raise AssessmentJobError(
            f"the snapshot root {base} is not present on disk; the "
            "evidence cannot be admitted"
        )
    actual = _tree_manifest(base)
    if actual != dict(manifest):
        missing = sorted(set(manifest) - set(actual))
        extra = sorted(set(actual) - set(manifest))
        changed = sorted(
            relative
            for relative in set(manifest) & set(actual)
            if actual[relative] != manifest[relative]
        )
        raise AssessmentJobError(
            "the snapshot root does not match the child-validated manifest "
            f"(missing={missing[:3]}, extra={extra[:3]}, "
            f"changed={changed[:3]}); tampered or incomplete evidence is "
            "refused"
        )
