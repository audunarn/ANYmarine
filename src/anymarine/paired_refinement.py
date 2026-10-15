"""Paired coarse/fine linear refinement assessment (development only).

This module owns the PAIRED outcome contract on top of the single-member
linear assessment slice: two caller-explicit coarse/fine members of the
SAME physical model/source are independently assessed under their
registered cases, the raw dt-refinement diagnostic is re-run, the pairing
itself is proved (same ordered bodies/DOFs/geometry/equilibrium, same
source identities, same physical kernel at the shared lag times, same
tail/window/source settings apart from the sampling dt/n_time), and an
immutable closed-world hash-bound paired record (separate format
``anymarine-linear-refinement-v1``) binds both member identities, the
actual member reports, the refinement metrics and the combined verdict.

The combined verdict is PASS only when BOTH member full reports pass AND
the refinement report passes. Valid NoGo members still produce a
preserved composite NoGo report — never a refusal, and never a "pair
pass because close". Pairing/eligibility violations (different kernels,
different sources, stale geometry, wrong roles, non-nested grids) are
typed refusals and produce no paired record. The raw
``assess_refinement`` diagnostic alone is never a physical admission.

Snapshots are exclusive and self-contained: each selected-result project
is saved through the existing public save-as route (which copies and
checks the real hydro dataset/operators/kernel sidecars), the case/report
artifacts go through the existing public ``AssessmentStore``, and the
paired record references them with validated relative references.
Reproduction uses ONLY the snapshot's own projects/results/cases/
sidecars; both members' complete physical/source proofs and the
refinement are re-run and the full semantic payload is compared.

Everything here is DEVELOPMENT ONLY and unqualified: never a qualified
marine analysis, never a production promotion.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import uuid
from pathlib import Path

import numpy as np

from .domain import (
    LINEAR_SOURCE_HYDRO,
    ProjectModel,
    ResultRecord,
)
from .hashing import canonical_json
from .linear import linear_input_hash, verify_linear_binding
from .linear_assessment import (
    AssessmentCase,
    AssessmentRefusalError,
    AssessmentReport,
    AssessmentStore,
    _FRESH_REPORT_KEYS,
    _payload_disagreement,
    assess_refinement,
    proven_full_kernel,
    assess_result,
    read_case_artifact,
    read_report_artifact,
)
from .persistence import (
    UnsafePathError,
    atomic_write_bytes,
    check_relative_reference,
    json_bytes,
    resolve_sidecar,
)

__all__ = [
    "PAIRED_SCHEMA_VERSION",
    "PAIRED_ARTIFACT_FORMAT",
    "PAIRED_SCOPE_NOTE",
    "PairedRefinementError",
    "PairedRefinementRefusalError",
    "PairedMember",
    "PairedRefinementReport",
    "assess_paired_refinement",
    "save_paired_snapshot",
    "adjudicate_paired_snapshot",
    "write_paired_artifact",
    "read_paired_artifact",
]

PAIRED_SCHEMA_VERSION = 1
PAIRED_ARTIFACT_FORMAT = "anymarine-linear-refinement-v1"
PAIRED_ARTIFACT_KIND = "anymarine_linear_refinement_pair"
MEMBER_ROLES = ("coarse", "fine")

# The paired kernel identity is a tight roundoff identity in the kernel's
# OWN units (kg/s^2), not a processing allowance: two members of a dt
# refinement must carry the SAME physical kernel, sampled on nested lag
# grids. Anything looser would admit different damping as "refinement".
KERNEL_IDENTITY_RELATIVE = 1.0e-12
KERNEL_IDENTITY_ABSOLUTE = 1.0e-12
TIME_GRID_ABSOLUTE = 1.0e-12

PAIRED_SCOPE_NOTE = (
    "Paired linear refinement output is DEVELOPMENT ONLY and unqualified: "
    "it is a registered diagnostic of two stored linear_development "
    "results of the same physical model, never a qualified marine analysis "
    "and never a production promotion."
)

_PAIR_KEYS = (
    "schema_version",
    "format",
    "pair_id",
    "dt_ratio",
    "members",
    "refinement",
    "pairing",
    "combined_verdict",
    "failed",
    "scope",
    "created_utc",
)
_PAIR_ARTIFACT_KEYS = (
    "artifact",
    "format",
    "schema_version",
    "pair_hash",
    "members",
    "pair",
)
_MEMBER_REF_KEYS = ("project", "case", "report")
_MEMBER_SECTION_KEYS = (
    "role",
    "case_hash",
    "case_id",
    "input_hash",
    "result_id",
    "report_hash",
    "verdict",
    "report",
)
# Fresh identity fields are excluded from the semantic reproduction
# comparison: the pair/report ids and timestamps are random per
# assessment and every hash derived from them (pair_hash, member
# report_hash) is a function of the compared payload plus those random
# ids, so comparing the full payload with the identity fields stripped is
# the STRONGER check — a re-hashed record with forged metrics is still
# refused because the metrics themselves are compared.
_FRESH_IDENTITY_KEYS = ("pair_id", "report_id", "created_utc", "report_hash")


def _strip_fresh_identity(value):
    if isinstance(value, dict):
        return {
            key: _strip_fresh_identity(item)
            for key, item in value.items()
            if key not in _FRESH_IDENTITY_KEYS
        }
    if isinstance(value, list):
        return [_strip_fresh_identity(item) for item in value]
    return value


def _validate_member_refs(member_refs) -> dict:
    if not isinstance(member_refs, dict) or set(member_refs) != set(MEMBER_ROLES):
        raise PairedRefinementError(
            "the paired artifact's member references must hold exactly "
            "the coarse and fine roles"
        )
    for role in MEMBER_ROLES:
        refs = member_refs[role]
        if not isinstance(refs, dict) or set(refs) != set(_MEMBER_REF_KEYS):
            raise PairedRefinementError(
                f"the paired artifact's {role} member references must "
                f"hold exactly the project/case/report references"
            )
        for key in _MEMBER_REF_KEYS:
            if not isinstance(refs[key], str) or not refs[key]:
                raise PairedRefinementError(
                    f"the paired artifact's {role} member reference "
                    f"{key!r} must be a non-empty relative reference"
                )
    return member_refs


class PairedRefinementError(Exception):
    """Base class for paired refinement failures."""


class PairedRefinementRefusalError(PairedRefinementError):
    """The pair is not eligible; no paired record is produced."""


class PairedMember:
    """One explicit coarse/fine member: project, root, result, case.

    The project root is REQUIRED for hydro-sourced members (the source
    proofs re-run against the authoritative sidecars); analytic members
    may omit it. The record holds no mutable state and never mutates the
    project, the root or any source.
    """

    def __init__(self, project, result, case, project_root=None):
        if not isinstance(project, ProjectModel):
            raise PairedRefinementRefusalError(
                "a paired member requires the actual ProjectModel"
            )
        if not isinstance(result, ResultRecord):
            raise PairedRefinementRefusalError(
                "a paired member requires the actual ResultRecord"
            )
        if not isinstance(case, AssessmentCase):
            raise PairedRefinementRefusalError(
                "a paired member requires its registered AssessmentCase"
            )
        self.project = project
        self.result = result
        self.case = case
        self.root = Path(project_root) if project_root is not None else None

    @property
    def inputs(self):
        return self.project.linear_inputs

    @property
    def is_hydro(self) -> bool:
        inputs = self.project.linear_inputs
        return (
            inputs is not None
            and inputs.provenance.source == LINEAR_SOURCE_HYDRO
        )


class PairedRefinementReport:
    """Immutable paired refinement record (closed-world, hash-bound).

    The constructor refuses unknown or missing record fields: a paired
    record is closed-world exactly like the stored artifact. Property
    access never exposes the internal mutable dictionaries/lists — every
    access returns a defensive deep copy, so attempted mutation of a
    exposed payload cannot change the record content or its hash.
    """

    def __init__(self, data: dict):
        if not isinstance(data, dict) or set(data) != set(_PAIR_KEYS):
            unknown = sorted(set(data) - set(_PAIR_KEYS)) if isinstance(
                data, dict
            ) else []
            missing = sorted(set(_PAIR_KEYS) - set(data)) if isinstance(
                data, dict
            ) else []
            raise PairedRefinementError(
                "a paired refinement record is closed-world: "
                + (
                    f"unknown fields {unknown}"
                    if unknown
                    else f"missing fields {missing}"
                    if missing
                    else "the record must be a mapping"
                )
            )
        self._data = json.loads(json.dumps(dict(data), allow_nan=False))

    @property
    def pair_id(self) -> str:
        return self._data["pair_id"]

    @property
    def combined_verdict(self) -> str:
        return self._data["combined_verdict"]

    @property
    def dt_ratio(self) -> int:
        return int(self._data["dt_ratio"])

    @property
    def members(self) -> dict:
        return json.loads(json.dumps(self._data["members"], allow_nan=False))

    @property
    def refinement(self) -> dict:
        return json.loads(json.dumps(self._data["refinement"], allow_nan=False))

    @property
    def pairing(self) -> dict:
        return json.loads(json.dumps(self._data["pairing"], allow_nan=False))

    @property
    def failed(self) -> list:
        return json.loads(json.dumps(self._data["failed"], allow_nan=False))

    @property
    def scope(self) -> dict:
        return json.loads(json.dumps(self._data["scope"], allow_nan=False))

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self._data, allow_nan=False))

    @property
    def pair_hash(self) -> str:
        data = self.to_dict()
        data.pop("created_utc", None)
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"PairedRefinementReport(verdict={self.combined_verdict!r}, "
            f"pair={self.pair_hash[:12]}...)"
        )


def _refusal(message: str):
    raise PairedRefinementRefusalError(message)


def _member_inputs(member: PairedMember):
    inputs = member.inputs
    if inputs is None:
        _refusal(
            "a paired member's project carries no stored linear inputs; "
            "there is nothing to assess"
        )
    if getattr(inputs, "layout", "two_body_surge_heave_v1") != "two_body_surge_heave_v1":
        _refusal(f"paired refinement is not available for six-DOF layouts ({inputs.layout}) yet "
                 "(ADR-015 section 7)")
    return inputs


def _assess_member(member: PairedMember) -> AssessmentReport:
    """Independently assess one member under its registered case.

    The member must pass the current binding/source/completeness
    eligibility on its own: the public binding verifier, the case/result/
    input hash correspondence and (through ``assess_result``) the public
    result completeness proof and, for hydro references, the actual
    project context with the full source proof. Physics failures produce
    a preserved NoGo member report, never a refusal.
    """

    inputs = _member_inputs(member)
    project = member.project
    try:
        verify_linear_binding(project, inputs)
    except Exception as error:
        raise PairedRefinementRefusalError(
            f"the member's stored inputs do not describe the actual project "
            f"bodies/mesh/equilibrium: {error}"
        ) from error
    if member.case.input_hash != linear_input_hash(inputs):
        _refusal(
            "the member's case does not describe the project's stored "
            "linear inputs (input hash mismatch)"
        )
    if member.result.input_hash != member.case.input_hash:
        _refusal(
            "the member's result does not match its case's bound input "
            "hash (stale or foreign result)"
        )
    if (
        member.case.result_id is not None
        and member.result.result_id != member.case.result_id
    ):
        _refusal(
            f"the member's case selects result {member.case.result_id!r} "
            f"but the provided result is {member.result.result_id!r}"
        )
    if member.is_hydro and member.root is None:
        _refusal(
            "a hydro-sourced paired member requires its actual project "
            "root holding the authoritative sidecars; no root, no "
            "source proof, no paired assessment"
        )
    try:
        return assess_result(
            member.case,
            member.result,
            inputs,
            project=project,
            provenance=project.hydro_provenance,
            project_root=member.root,
        )
    except AssessmentRefusalError as error:
        raise PairedRefinementRefusalError(str(error)) from error


def _same_pair_identity(member_coarse: PairedMember,
                        member_fine: PairedMember, ratio: int) -> dict:
    """Prove the pairing identity: same model, same physical kernel.

    The ordered body bindings (identity, reference geometry, mass, mesh/
    lid hashes), the DOF labels and the equilibrium hash must be
    identical; the kernel lag grids must be exactly nested and the kernel
    VALUES at the shared lag times must agree within the tight roundoff
    identity (rtol/atol 1e-12 in the kernel's own units kg/s^2) — the
    same physical kernel sampled at different dt, never a 2% processing
    allowance. Different kernels or damping are a typed refusal, never a
    dt refinement.
    """

    inputs_coarse = _member_inputs(member_coarse)
    inputs_fine = _member_inputs(member_fine)
    bindings_coarse = tuple(binding.to_dict() for binding in inputs_coarse.bindings)
    bindings_fine = tuple(binding.to_dict() for binding in inputs_fine.bindings)
    if bindings_coarse != bindings_fine:
        _refusal(
            "refinement pairs the SAME physical model: the ordered body "
            "bindings (identity, reference geometry, mass, mesh/lid "
            "hashes) differ between the coarse and fine members"
        )
    if inputs_coarse.equilibrium_hash != inputs_fine.equilibrium_hash:
        _refusal(
            "refinement pairs the SAME physical model: the equilibrium "
            "hash differs between the coarse and fine members"
        )
    if tuple(inputs_coarse.labels) != tuple(inputs_fine.labels):
        _refusal(
            "refinement pairs the SAME physical model: the retained DOF "
            "labels/order differ between the coarse and fine members"
        )
    times_coarse = np.asarray(inputs_coarse.kernel_times, dtype=float)
    times_fine = np.asarray(inputs_fine.kernel_times, dtype=float)
    shared = times_fine[::ratio]
    if shared.shape != times_coarse.shape:
        _refusal(
            "the kernel lag grids are not exactly nested with the "
            "declared integer dt ratio; the members do not share the "
            "same physical kernel grid"
        )
    time_tolerance = TIME_GRID_ABSOLUTE * max(
        float(inputs_coarse.dt), float(inputs_fine.dt), 1.0
    )
    time_deviation = np.abs(shared - times_coarse)
    if not np.all(time_deviation <= time_tolerance):
        _refusal(
            "the shared kernel lag times do not agree on the nested "
            "grids; the members do not sample the same physical kernel"
        )
    kernel_coarse = np.asarray(inputs_coarse.radiation_kernel, dtype=float)
    kernel_fine = np.asarray(inputs_fine.radiation_kernel, dtype=float)
    if kernel_fine.shape[0] < kernel_coarse.shape[0]:
        _refusal(
            "the fine member's kernel holds fewer lag samples than the "
            "coarse member's; the grids are not nested"
        )
    shared_fine = kernel_fine[::ratio][: kernel_coarse.shape[0]]
    deviation = np.abs(shared_fine - kernel_coarse)
    scale = np.maximum(np.abs(shared_fine), np.abs(kernel_coarse))
    if not np.all(
        deviation
        <= KERNEL_IDENTITY_RELATIVE * scale + KERNEL_IDENTITY_ABSOLUTE
    ):
        _refusal(
            "the coarse and fine members carry DIFFERENT physical kernels "
            "(shared-lag values disagree beyond the tight roundoff "
            "identity rtol/atol 1e-12 in the kernel's own units kg/s^2); "
            "comparing different kernels/damping is never dt refinement"
        )
    return {
        "bindings_identical": True,
        "equilibrium_hash": inputs_coarse.equilibrium_hash,
        "dof_labels": list(inputs_coarse.labels),
        "max_shared_lag_time_deviation_s": float(np.max(time_deviation)),
        "max_shared_lag_kernel_deviation_kg_s2": float(np.max(deviation)),
        "kernel_identity_relative": KERNEL_IDENTITY_RELATIVE,
        "kernel_identity_absolute": KERNEL_IDENTITY_ABSOLUTE,
    }


def _same_hydro_source(member_coarse: PairedMember,
                      member_fine: PairedMember) -> dict:
    """Prove the hydro source identity of a hydro-sourced pair.

    The dataset/kernel/request identities, the body/DOF order, the
    derived-operator identities (derived route) and the wave selection
    must be identical, and the kernel tail/window/source settings must
    agree apart from the sampling dt/n_time. Different sources are a
    typed refusal, never a dt refinement.
    """

    provenance_coarse = member_coarse.project.hydro_provenance
    provenance_fine = member_fine.project.hydro_provenance
    if provenance_coarse is None or provenance_fine is None:
        _refusal(
            "a hydro-sourced paired member carries no hydro provenance "
            "record; the source intent cannot be proved"
        )
    for key in (
        "dataset_content_hash",
        "request_hash",
        "a_inf_source",
    ):
        if getattr(provenance_coarse, key) != getattr(provenance_fine, key):
            _refusal(
                f"refinement pairs the SAME hydro source: the source "
                f"identity {key!r} differs between the coarse and fine "
                f"members"
            )
    if (
        tuple(provenance_coarse.body_ids) != tuple(provenance_fine.body_ids)
        or tuple(provenance_coarse.dof_keys)
        != tuple(provenance_fine.dof_keys)
    ):
        _refusal(
            "refinement pairs the SAME hydro source: the retained body/"
            "DOF order differs between the coarse and fine members"
        )
    bundle_coarse = provenance_coarse.derived_bundle
    bundle_fine = provenance_fine.derived_bundle
    if (bundle_coarse is None) != (bundle_fine is None):
        _refusal(
            "refinement pairs the SAME hydro source: one member carries a "
            "derived-operator bundle and the other does not"
        )
    if bundle_coarse is not None:
        for key in ("derived_content_hash", "operators_raw_sha256"):
            if bundle_coarse[key] != bundle_fine[key]:
                _refusal(
                    f"refinement pairs the SAME hydro source: the derived "
                    f"identity {key!r} differs between the coarse and "
                    f"fine members"
                )
    if (provenance_coarse.wave is None) != (provenance_fine.wave is None):
        _refusal(
            "refinement pairs the SAME physical forcing: one member was "
            "imported with a regular-wave selection and the other as free "
            "decay"
        )
    # The provenance freezes the wave selection (read-only mappings); compare and
    # record its own JSON form.
    wave_coarse = provenance_coarse.to_dict()["wave"]
    wave_fine = provenance_fine.to_dict()["wave"]
    if provenance_coarse.wave is not None and (
        json.loads(json.dumps(wave_coarse, allow_nan=False))
        != json.loads(json.dumps(wave_fine, allow_nan=False))
    ):
        _refusal(
            "refinement pairs the SAME physical forcing: the recorded "
            "wave selections differ between the coarse and fine members"
        )
    settings_coarse = dict(provenance_coarse.to_dict()["kernel_settings"])
    settings_fine = dict(provenance_fine.to_dict()["kernel_settings"])
    sampling_keys = ("dt", "n_time")
    for key in sampling_keys:
        settings_coarse.pop(key, None)
        settings_fine.pop(key, None)
    if settings_coarse != settings_fine:
        _refusal(
            "refinement pairs the SAME physical kernel: the kernel tail/"
            "window/source settings differ between the coarse and fine "
            "members apart from the sampling dt/n_time"
        )
    return {
        "dataset_content_hash": provenance_coarse.dataset_content_hash,
        "kernel_content_hash": provenance_coarse.kernel_content_hash,
        "request_hash": provenance_coarse.request_hash,
        "route": (
            "derived_operators"
            if bundle_coarse is not None
            else "raw_dataset"
        ),
        "wave": json.loads(
            json.dumps(wave_coarse, allow_nan=False)
        ),
        "kernel_settings_shared": settings_coarse,
    }


def _member_section(role: str, member: PairedMember,
                    report: AssessmentReport) -> dict:
    return {
        "role": role,
        "case_hash": member.case.case_hash,
        "case_id": member.case.case_id,
        "input_hash": member.case.input_hash,
        "result_id": member.result.result_id,
        "report_hash": report.report_hash,
        "verdict": report.verdict,
        "report": report.to_dict(),
    }


def assess_paired_refinement(member_coarse: PairedMember,
                             member_fine: PairedMember) -> PairedRefinementReport:
    """Assess an explicit coarse/fine pair of the SAME physical model.

    Each member independently passes the binding/source/completeness
    eligibility and is fully assessed under its registered case; the raw
    dt-refinement diagnostic and the pairing identity proofs run on the
    actual stored arrays. The combined verdict is PASS only if BOTH
    member full reports and the refinement report pass; valid NoGo
    members produce a preserved composite NoGo record.
    """

    if not isinstance(member_coarse, PairedMember) or not isinstance(
        member_fine, PairedMember
    ):
        _refusal("assess_paired_refinement takes two PairedMember records")
    inputs_coarse = _member_inputs(member_coarse)
    inputs_fine = _member_inputs(member_fine)
    report_coarse = _assess_member(member_coarse)
    report_fine = _assess_member(member_fine)
    kernels = None
    if member_coarse.case.reference.get("prehistory") is not None:
        kernels = tuple(proven_full_kernel(m.project.linear_inputs, m.project.hydro_provenance, m.root)
                        for m in (member_coarse, member_fine))
    try:
        refinement = assess_refinement(
            member_coarse.case,
            member_fine.case,
            member_coarse.result,
            member_fine.result,
            inputs_coarse,
            inputs_fine,
            kernels,
        )
    except AssessmentRefusalError as error:
        raise PairedRefinementRefusalError(str(error)) from error
    ratio = int(refinement.arrays["refinement"]["dt_ratio"])
    pairing = _same_pair_identity(member_coarse, member_fine, ratio)
    if member_coarse.is_hydro or member_fine.is_hydro:
        if not (member_coarse.is_hydro and member_fine.is_hydro):
            _refusal(
                "refinement pairs the SAME source: one member is "
                "hydro-sourced and the other is not"
            )
        pairing["hydro_source"] = _same_hydro_source(
            member_coarse, member_fine
        )
    failed = []
    for role, report in (("coarse", report_coarse), ("fine", report_fine)):
        if report.verdict != "pass":
            failed.extend(f"{role}:{name}" for name in report.failed)
    if refinement.verdict != "pass":
        failed.extend(f"refinement:{name}" for name in refinement.failed)
    combined = (
        "pass"
        if (
            report_coarse.verdict == "pass"
            and report_fine.verdict == "pass"
            and refinement.verdict == "pass"
        )
        else "nogo"
    )
    data = {
        "schema_version": PAIRED_SCHEMA_VERSION,
        "format": PAIRED_ARTIFACT_FORMAT,
        "pair_id": f"pair-{uuid.uuid4().hex}",
        "dt_ratio": ratio,
        "members": {
            "coarse": _member_section("coarse", member_coarse, report_coarse),
            "fine": _member_section("fine", member_fine, report_fine),
        },
        "refinement": refinement.to_dict(),
        "pairing": pairing,
        "combined_verdict": combined,
        "failed": failed,
        "scope": {
            "development_only": True,
            "note": PAIRED_SCOPE_NOTE,
        },
        "created_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    }
    return PairedRefinementReport(data)


# ------------------------------------------------------ artifacts/snapshot


def write_paired_artifact(report: PairedRefinementReport, path,
                          member_refs: dict) -> Path:
    _validate_member_refs(member_refs)
    payload = {
        "artifact": PAIRED_ARTIFACT_KIND,
        "format": PAIRED_ARTIFACT_FORMAT,
        "schema_version": PAIRED_SCHEMA_VERSION,
        "pair_hash": report.pair_hash,
        "members": member_refs,
        "pair": report.to_dict(),
    }
    target = Path(path)
    atomic_write_bytes(target, json_bytes(payload))
    return target


def read_paired_artifact(path) -> PairedRefinementReport:
    """Rebuild a stored paired record; refuse mutation/unknown fields."""

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PairedRefinementError(
            f"cannot read the paired artifact {path}: {error}"
        ) from error
    if not isinstance(data, dict) or set(data) != set(_PAIR_ARTIFACT_KEYS):
        raise PairedRefinementError(
            f"the paired artifact {path} is not a closed-world paired "
            "refinement record"
        )
    if data["artifact"] != PAIRED_ARTIFACT_KIND:
        raise PairedRefinementError(f"{path} is not a paired refinement artifact")
    if data["format"] != PAIRED_ARTIFACT_FORMAT:
        raise PairedRefinementError(
            f"unsupported paired artifact format {data['format']!r}"
        )
    if data["schema_version"] != PAIRED_SCHEMA_VERSION:
        raise PairedRefinementError(
            f"unsupported paired artifact schema_version "
            f"{data['schema_version']!r}"
        )
    _validate_member_refs(data["members"])
    pair = data["pair"]
    if not isinstance(pair, dict) or set(pair) != set(_PAIR_KEYS):
        raise PairedRefinementError(
            f"the paired record {path} is not closed-world "
            f"(unknown or missing fields)"
        )
    report = PairedRefinementReport(pair)
    if report.pair_hash != data["pair_hash"]:
        raise PairedRefinementError(
            f"the paired artifact {path} was mutated (stored hash "
            f"{data['pair_hash'][:12]}... != recomputed "
            f"{report.pair_hash[:12]}...); tampered records are refused"
        )
    return report


def _relative_member_refs(out_root: Path, role: str, case_path: Path,
                          report_path: Path) -> dict:
    return {
        "project": f"{role}/project",
        "case": case_path.relative_to(out_root).as_posix(),
        "report": report_path.relative_to(out_root).as_posix(),
    }


def save_paired_snapshot(member_coarse: PairedMember,
                        member_fine: PairedMember,
                        paired_report: PairedRefinementReport,
                        out_root) -> Path:
    """Write an EXCLUSIVE self-contained paired snapshot under *out_root*.

    The paired record must correspond to the members and must REPRODUCE
    from the actual arrays before anything is created: the full paired
    assessment (both member physical/source proofs and the refinement)
    is re-run here and the semantic payload must agree. Each selected-
    result project is then saved through the existing public save-as
    route (which copies and checks the real hydro sidecars), the case/
    report artifacts go through the existing public AssessmentStore, and
    the paired artifact records validated relative references. Existing
    targets are refused BEFORE any write; failed/NoGo evidence is
    preserved, never overwritten.
    """

    from .services import ApplicationService

    out = Path(out_root)
    if out.exists():
        raise PairedRefinementRefusalError(
            f"the paired snapshot target {out} already exists; existing "
            "outputs (including NoGo/failed records) are preserved and "
            "never overwritten"
        )
    for role, member in (
        ("coarse", member_coarse),
        ("fine", member_fine),
    ):
        section = paired_report.members.get(role)
        if not isinstance(section, dict):
            _refusal(f"the paired record carries no {role} member section")
        if section["case_hash"] != member.case.case_hash:
            _refusal(
                f"the paired record's {role} case hash does not match the "
                f"provided member; the record and members do not correspond"
            )
        if section["result_id"] != member.result.result_id:
            _refusal(
                f"the paired record's {role} result id does not match the "
                f"provided member; the record and members do not correspond"
            )
        if section["input_hash"] != member.case.input_hash:
            _refusal(
                f"the paired record's {role} input hash does not match the "
                f"provided member; the record and members do not correspond"
            )
    # Reproduction before writes: the full paired assessment is re-run on
    # the actual arrays/sources and the semantic payload must agree.
    fresh = assess_paired_refinement(member_coarse, member_fine)
    stored_payload = _strip_fresh_identity(paired_report.to_dict())
    fresh_payload = _strip_fresh_identity(fresh.to_dict())
    disagreement = _payload_disagreement(stored_payload, fresh_payload, "pair")
    if disagreement is not None:
        raise PairedRefinementRefusalError(
            f"the paired record does not reproduce from the actual arrays "
            f"(first semantic payload disagreement at {disagreement}); a "
            "re-hashed record with forged metrics is refused before any "
            "write"
        )
    refs = {}
    for role, member in (
        ("coarse", member_coarse),
        ("fine", member_fine),
    ):
        # Private scoped service instance: the caller's active service root
        # is never changed by a snapshot save.
        service = ApplicationService(member.root)
        selected = member.project.with_edits(results=(member.result,))
        service.save(selected, out / role / "project")
        store = AssessmentStore(out / role)
        case_path = store.save_case(member.case)
        member_report = AssessmentReport(paired_report.members[role]["report"])
        report_path = store.save_report(member_report)
        refs[role] = _relative_member_refs(out, role, case_path, report_path)
    write_paired_artifact(paired_report, out / "pair.json", refs)
    return out


def _bind_captured_member_report(role: str, section, report) -> None:
    """Bind a captured member report to the stored paired member section.

    The snapshot's captured report artifact is never merely parsed and
    discarded: its case/input/result identities, its verdict, its actual
    hash and its full semantic payload must match the corresponding
    stored paired member section BEFORE any new assessment. A re-hashed
    captured report with a changed check metric/flag is refused as a
    paired inventory violation even when the embedded pair payload is
    unchanged — the binding is semantic, not a hash-only proof.
    """

    if not isinstance(section, dict) or set(section) != set(
        _MEMBER_SECTION_KEYS
    ):
        raise PairedRefinementRefusalError(
            f"the paired record's {role} member section is not closed-world "
            "(unknown or missing fields); corrupt member inventories are "
            "refused"
        )
    captured_payload = report.to_dict()
    for key in ("case_hash", "case_id", "input_hash", "result_id", "verdict"):
        if captured_payload[key] != section[key]:
            raise PairedRefinementRefusalError(
                f"the paired snapshot's captured {role} member report does "
                f"not carry the stored {role} member identity {key!r}; the "
                "paired member inventory is inconsistent and reproduction "
                "is refused"
            )
    if report.report_hash != section["report_hash"]:
        raise PairedRefinementRefusalError(
            f"the paired snapshot's captured {role} member report was "
            f"re-hashed or mutated (stored member report hash "
            f"{section['report_hash'][:12]}... != captured "
            f"{report.report_hash[:12]}...); the paired member inventory "
            "is inconsistent and reproduction is refused"
        )
    stored_payload = section["report"]
    for key in _FRESH_REPORT_KEYS:
        captured_payload.pop(key, None)
        stored_payload.pop(key, None)
    disagreement = _payload_disagreement(
        stored_payload, captured_payload, f"members.{role}.report"
    )
    if disagreement is not None:
        raise PairedRefinementRefusalError(
            f"the paired snapshot's captured {role} member report does not "
            f"match the stored paired member section (first semantic "
            f"payload disagreement at {disagreement}); a re-hashed captured "
            "report with changed check metrics/flags is refused even when "
            "the embedded pair payload is unchanged"
        )


def _snapshot_member(snapshot_root: Path, refs: dict, role: str,
                      section: dict) -> PairedMember:
    """Rebuild one member from the snapshot's own captured data only."""

    from .services import ApplicationService

    for key in ("project", "case", "report"):
        if not isinstance(refs, dict) or key not in refs:
            raise PairedRefinementRefusalError(
                f"the paired snapshot's {role} member reference {key!r} "
                "is missing; unknown or incomplete records are refused"
            )
        try:
            check_relative_reference(refs[key])
        except UnsafePathError as error:
            raise PairedRefinementRefusalError(
                f"the paired snapshot's {role} member reference {key!r} "
                f"is unsafe: {error}"
            ) from error
    project_path = resolve_sidecar(snapshot_root, refs["project"])
    case_path = resolve_sidecar(snapshot_root, refs["case"])
    report_path = resolve_sidecar(snapshot_root, refs["report"])
    for label, path in (
        ("project", project_path),
        ("case", case_path),
        ("report", report_path),
    ):
        if not path.exists():
            raise PairedRefinementRefusalError(
                f"the paired snapshot's {role} {label} reference "
                f"{refs[label]!r} does not exist under the snapshot root; "
                "missing paired references are refused and there is no "
                "fallback to external originals"
            )
    service = ApplicationService()
    project = service.load(project_path)
    case = read_case_artifact(case_path)
    report = read_report_artifact(report_path)
    _bind_captured_member_report(role, section, report)
    inputs = project.linear_inputs
    if inputs is None:
        raise PairedRefinementRefusalError(
            f"the paired snapshot's {role} project carries no stored "
            "linear inputs"
        )
    if case.result_id is None:
        if len(project.results) != 1:
            raise PairedRefinementRefusalError(
                f"the paired snapshot's {role} case selects no result_id "
                "and its project does not hold exactly one result"
            )
        result = project.results[0]
    else:
        matches = [
            record
            for record in project.results
            if record.result_id == case.result_id
        ]
        if len(matches) != 1:
            raise PairedRefinementRefusalError(
                f"the paired snapshot's {role} project does not hold "
                f"exactly one result with id {case.result_id!r}"
            )
        result = matches[0]
    return PairedMember(
        project, result, case, project_root=project_path
    )


def adjudicate_paired_snapshot(snapshot_root) -> dict:
    """Reproduce a paired snapshot from its own captured actual data.

    Loads the snapshot's own projects/cases/reports (the original
    projects and external source files may be absent or renamed),
    re-runs BOTH members' complete physical/source proofs and the
    refinement, and compares the actual semantic metrics/arrays/
    identities against the stored paired payload — never flags or
    hashes alone. Any disagreement is a typed refusal.
    """

    root = Path(snapshot_root)
    pair_path = root / "pair.json"
    if not pair_path.is_file():
        raise PairedRefinementRefusalError(
            f"the paired snapshot root {root} holds no pair.json; "
            "unknown or incomplete snapshots are refused"
        )
    try:
        data = json.loads(pair_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PairedRefinementRefusalError(
            f"cannot read the paired artifact {pair_path}: {error}"
        ) from error
    if not isinstance(data, dict) or set(data) != set(_PAIR_ARTIFACT_KEYS):
        raise PairedRefinementRefusalError(
            f"the paired artifact {pair_path} is not a closed-world "
            "paired refinement record"
        )
    if data["artifact"] != PAIRED_ARTIFACT_KIND:
        raise PairedRefinementRefusalError(
            f"{pair_path} is not a paired refinement artifact"
        )
    if data["format"] != PAIRED_ARTIFACT_FORMAT:
        raise PairedRefinementRefusalError(
            f"unsupported paired artifact format {data['format']!r}"
        )
    if data["schema_version"] != PAIRED_SCHEMA_VERSION:
        raise PairedRefinementRefusalError(
            f"unsupported paired artifact schema_version "
            f"{data['schema_version']!r}"
        )
    stored = PairedRefinementReport(data["pair"])
    if stored.pair_hash != data["pair_hash"]:
        raise PairedRefinementRefusalError(
            f"the paired artifact {pair_path} was mutated (stored hash "
            f"{data['pair_hash'][:12]}... != recomputed "
            f"{stored.pair_hash[:12]}...); tampered records are refused"
        )
    refs = data["members"]
    try:
        _validate_member_refs(refs)
    except PairedRefinementError as error:
        raise PairedRefinementRefusalError(str(error)) from error
    member_coarse = _snapshot_member(
        root, refs["coarse"], "coarse", stored.members["coarse"]
    )
    member_fine = _snapshot_member(
        root, refs["fine"], "fine", stored.members["fine"]
    )
    recomputed = assess_paired_refinement(member_coarse, member_fine)
    if recomputed.combined_verdict != stored.combined_verdict:
        raise PairedRefinementRefusalError(
            f"the stored paired verdict {stored.combined_verdict!r} does "
            "not reproduce from the snapshot's actual arrays (recomputed "
            f"{recomputed.combined_verdict!r})"
        )
    stored_payload = _strip_fresh_identity(stored.to_dict())
    recomputed_payload = _strip_fresh_identity(recomputed.to_dict())
    disagreement = _payload_disagreement(
        stored_payload, recomputed_payload, "pair"
    )
    if disagreement is not None:
        raise PairedRefinementRefusalError(
            f"the stored paired record does not reproduce from the "
            f"snapshot's actual arrays (first semantic payload "
            f"disagreement at {disagreement}); a re-hashed record with "
            "forged numerical metrics and unchanged pass flags is refused"
        )
    return {
        "agrees": True,
        "combined_verdict": recomputed.combined_verdict,
        "recomputed_pair_hash": recomputed.pair_hash,
        "stored_pair_hash": stored.pair_hash,
    }
