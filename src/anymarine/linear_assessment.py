"""Registered headless assessment of stored coupled4 linear development results.

This module owns the linear assessment slice. It never re-implements the
solver, the domain records, the hydro import or the project persistence: it
consumes the public records (``LinearDevelopmentInputs``, ``ResultRecord``),
the public verifiers (``verify_linear_result``, ``linear_input_hash``) and
the public persistence primitives. The optional solver is only ever reached
through the existing ``ApplicationService.run_linear_local`` route by callers
of this module.

Assessment contract (development only, never a qualification):

* An immutable, closed-world, hash-bound :class:`AssessmentCase` is prepared
  and registered BEFORE the rehearsal solve. It binds the exact effective
  ``linear_input_hash``, the mode (``regular_wave`` / ``free_decay``), the
  labelled reference, the absolute time windows, the fit/drift rules and
  every prospective criterion with explicit units and floors. Criteria may
  be stricter than the governing contract minima, never looser.
* The matched frequency-domain reference uses the SI convention
  ``Z(omega) = K - omega^2 (M_dry + A(omega)) - i omega (B(omega) + C)``
  solved as one full complex 4x4 system (off-diagonal coupling kept), with
  ``q(t) = Re(qhat exp(-i omega t))`` evaluated at the absolute input times.
* RAO fits are per-window least-squares cos/sin fits with the negative
  phasor convention ``qhat = a + i b`` over two predeclared adjacent
  equal-duration windows, each at least two cycles, below Nyquist, with an
  explicit condition bound. An optional common-surge linear drift is only
  admitted along ``(1, 0, 1, 0)/sqrt2`` and only when ``K u = 0`` for the
  actual stiffness.
* The authoritative physics balance is recomputed from the stored q/v/a and
  the effective matrices (dry inertia, added inertia, memory, damping,
  stiffness), the stored external channel is compared against the input
  forces, the Newmark q/v updates and the declared q0/v0 are verified, and
  the stored memory force is validated against an independent discrete
  causal trapezoid convolution oracle (FFT-accelerated, verification only).
* Free decay requires zero external forcing and reports the full discrete
  energy/work accounting. Returning stored radiation energy (negative
  instantaneous power or a decreasing cumulative work segment) is not a
  failure; only the net cumulative radiation work must satisfy the
  zero-history passivity bound, and the discrete closure and mechanical
  peak-growth bounds hold.
* dt refinement compares two caller-explicit cases of the SAME physical
  model on a nested grid with an integer ratio >= 2.

Every produced report is labelled development-only/unqualified. NoGo
reports are preserved with all raw authoritative arrays; eligibility
failures (wrong kind, wrong hash, tampered case, truncated or foreign data)
are typed refusals and produce no report.
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import math
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np

from . import RESULT_KIND_LINEAR
from .domain import (
    HYDRO_ROUTE_DERIVED,
    HYDRO_ROUTE_RAW,
    LINEAR_SOURCE_HYDRO,
    HydroBridgeProvenance,
    LinearDevelopmentInputs,
    ProjectModel,
    ResultRecord,
)
from .hashing import canonical_json
from .linear import linear_input_hash, verify_linear_binding, verify_linear_result
from .prehistory import (PrehistoryError, declaration_phasor, periodic_prehistory_force,
                         validated_declaration)
from .persistence import (
    atomic_write_bytes,
    check_relative_reference,
    json_bytes,
    resolve_sidecar,
    sha256_bytes,
)

__all__ = [
    "ASSESSMENT_SCHEMA_VERSION",
    "MODE_REGULAR_WAVE",
    "MODE_FREE_DECAY",
    "REFERENCE_ANALYTIC_EXPONENTIAL",
    "REFERENCE_HYDRO_SPECTRUM",
    "REFERENCE_HYDRO_SOURCE",
    "DRIFT_NONE",
    "DRIFT_COMMON_SURGE_LINEAR",
    "LINEAR_ASSESSMENT_SCOPE_NOTE",
    "AssessmentError",
    "AssessmentCaseError",
    "AssessmentRefusalError",
    "AssessmentCase",
    "AssessmentReport",
    "AssessmentStore",
    "prepare_case",
    "validate_assessment_controls",
    "assess_result",
    "assess_refinement",
    "adjudicate_report",
    "load_hydro_spectrum",
    "hydro_reciprocal_sensitivity",
    "kernel_spectrum_self_consistency",
    "write_case_artifact",
    "read_case_artifact",
    "write_report_artifact",
    "read_report_artifact",
]

ASSESSMENT_SCHEMA_VERSION = 1
MODE_REGULAR_WAVE = "regular_wave"
MODE_FREE_DECAY = "free_decay"
MODES = (MODE_REGULAR_WAVE, MODE_FREE_DECAY)
REFERENCE_ANALYTIC_EXPONENTIAL = "analytic_exponential"
REFERENCE_HYDRO_SPECTRUM = "hydro_spectrum"
REFERENCE_HYDRO_SOURCE = "hydro_source"
REFERENCE_KINDS = (
    REFERENCE_ANALYTIC_EXPONENTIAL,
    REFERENCE_HYDRO_SPECTRUM,
    REFERENCE_HYDRO_SOURCE,
)
HYDRO_REFERENCE_ROUTES = (HYDRO_ROUTE_RAW, HYDRO_ROUTE_DERIVED)
# Identity (not physics) tolerance binding the case force phasors to the
# actual stored excitation source sample: both sides are float64 round-trips
# of the same arrays, so anything looser would admit a substituted phasor.
HYDRO_PHASOR_IDENTITY_RELATIVE = 1.0e-12
HYDRO_PHASOR_IDENTITY_ABSOLUTE = 1.0e-12
DRIFT_NONE = "none"
DRIFT_COMMON_SURGE_LINEAR = "common_surge_linear"
DRIFT_RULES = (DRIFT_NONE, DRIFT_COMMON_SURGE_LINEAR)
COMMON_SURGE_DIRECTION = (1.0 / math.sqrt(2.0), 0.0, 1.0 / math.sqrt(2.0), 0.0)

LINEAR_ASSESSMENT_SCOPE_NOTE = (
    "Linear assessment output is DEVELOPMENT ONLY and unqualified: it is a "
    "registered diagnostic of a stored linear_development result, never a "
    "qualified marine analysis and never a production promotion."
)

# Governing contract minima: a case may declare stricter criteria, never
# looser ones. Each entry maps a criterion key to its maximum admissible
# (loosest) value; keys with None have no contract minimum and only need to
# be positive finite prospective controls.
_CRITERIA_MAXIMA = {
    "force_match_relative": 1.0e-10,
    "force_match_absolute_n": 1.0e-9,
    "channel_relative": 1.0e-10,
    "channel_absolute_n": 1.0e-9,
    "balance_relative": 1.0e-10,
    "balance_absolute_n": 1.0e-9,
    "memory_oracle_relative": 1.0e-10,
    "memory_oracle_absolute_n": 1.0e-9,
    "newmark_relative": None,
    "newmark_absolute_m": None,
    "newmark_absolute_ms": None,
    "steady_relative": 0.02,
    "steady_floor_m": 1.0e-8,
    "steady_absolute_m": 1.0e-8,
    "fit_rms_relative": 0.02,
    "fit_rms_floor_m": 1.0e-8,
    "fit_rms_absolute_m": 1.0e-8,
    "rao_amplitude_relative": 0.03,
    "rao_phase_deg": 3.0,
    "nearzero_amplitude_m": 1.0e-8,
    "nearzero_amplitude_absolute_m": 1.0e-8,
    "max_condition": 1.0e12,
    "energy_closure_absolute_j": 1.0e-10,
    "energy_closure_relative": 1.0e-8,
    "energy_scale_j": None,
    "nearzero_energy_floor_j": 1.0e-12,
    "free_decay_external_absolute_n": None,
    "kernel_match_relative": None,
    "small_excursion_length_scale_m": None,
    "impedance_relative_limit": 0.01,
    # Optional (owner decision 2026-10-13, prospective): when declared, the raw-vs-
    # processed impedance check is full-block, ||Z_raw - Z_proc||_F <= limit ||Z_raw||_F
    # (+ impedance_absolute_j), instead of per coefficient.
    "impedance_full_block_relative": 0.01,
    "impedance_floor_j": 1.0e-9,
    "impedance_absolute_j": 1.0e-9,
    "response_relative_limit": 0.02,
    "response_floor": 1.0e-8,
    "response_absolute": 1.0e-8,
    "refinement_relative": 0.02,
    "refinement_floor": 1.0e-8,
    "refinement_absolute": 1.0e-8,
}
_CRITERIA_KEYS = frozenset(_CRITERIA_MAXIMA)
# Roundoff identity bound for the shared wave forcing of a prehistory pair, where each
# member's stored force is F_wave - H and H is added back (not an adopted criterion).
PREHISTORY_FORCING_IDENTITY_RELATIVE = 1.0e-12
_OPTIONAL_CRITERIA = ("impedance_full_block_relative",)

_CASE_KEYS = (
    "schema_version",
    "case_id",
    "mode",
    "input_hash",
    "result_id",
    "reference",
    "omega",
    "force_phasors",
    "windows",
    "fit_controls",
    "criteria",
    "refinement",
    "scope",
    "created_utc",
)
_REPORT_KEYS = (
    "schema_version",
    "report_id",
    "case_hash",
    "case_id",
    "mode",
    "input_hash",
    "result_id",
    "verdict",
    "checks",
    "arrays",
    "failed",
    "scope",
    "created_utc",
)


class AssessmentError(Exception):
    """Base class for linear assessment failures."""


class AssessmentCaseError(AssessmentError):
    """A case specification or stored case artifact is invalid."""


class AssessmentRefusalError(AssessmentError):
    """The result/case pair is not eligible; no report is produced."""


def _require_finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise AssessmentCaseError(f"{name} must be a finite number; received {value!r}")
    return number


def _require_positive(name: str, value: float) -> float:
    number = _require_finite(name, value)
    if number <= 0.0:
        raise AssessmentCaseError(f"{name} must be strictly positive; received {number!r}")
    return number


def _require_hash(name: str, value) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise AssessmentCaseError(f"{name} must be a 64-character sha256 hex string")
    return text


def _exact_keys(name: str, data, allowed: tuple, optional: tuple = ()) -> None:
    if not isinstance(data, dict):
        raise AssessmentCaseError(f"{name} must be a JSON object")
    unknown = sorted(set(data) - set(allowed))
    missing = sorted(set(allowed) - set(optional) - set(data))
    if unknown:
        raise AssessmentCaseError(
            f"{name} contains unknown field(s) {unknown}; the case schema is "
            "closed-world and nothing is silently ignored"
        )
    if missing:
        raise AssessmentCaseError(f"{name} is missing required field(s) {missing}")


def _validate_criteria(data) -> dict:
    _exact_keys("criteria", data, tuple(sorted(_CRITERIA_KEYS)), optional=_OPTIONAL_CRITERIA)
    validated = {}
    for key in sorted(_CRITERIA_KEYS):
        if key in _OPTIONAL_CRITERIA and key not in data:
            continue
        value = _require_positive(f"criteria.{key}", data[key])
        maximum = _CRITERIA_MAXIMA[key]
        if maximum is not None and value > maximum:
            raise AssessmentCaseError(
                f"criteria.{key} = {value!r} is looser than the governing contract "
                f"maximum {maximum!r}; stricter controls are admitted, never looser"
            )
        validated[key] = value
    return validated


def validate_assessment_controls(criteria: dict, fit_controls: dict) -> dict:
    """Validate prospective controls before source work or input construction.

    Reuses the same validators as prepare_case; it does not register a case,
    supply defaults or confer eligibility/qualification. Returns defensive data.
    """
    return {"criteria": _validate_criteria(criteria),
            "fit_controls": _validate_fit_controls(fit_controls)}


def _validate_reference(data) -> dict:
    _exact_keys(
        "reference",
        data,
        (
            "kind",
            "coefficient_matrix",
            "decay",
            "route",
            "omega",
            "heading",
            "wave_amplitude",
            "wave_phase",
            "dataset_content_hash",
            "derived_content_hash",
            "operators_raw_sha256",
            "prehistory",
        ),
        optional=(
            "prehistory",
            "coefficient_matrix",
            "decay",
            "route",
            "omega",
            "heading",
            "wave_amplitude",
            "wave_phase",
            "dataset_content_hash",
            "derived_content_hash",
            "operators_raw_sha256",
        ),
    )
    kind = str(data["kind"])
    if kind not in REFERENCE_KINDS:
        raise AssessmentCaseError(
            f"reference.kind must be one of {REFERENCE_KINDS}; received {kind!r}. "
            "The stored TD kernel is never a qualification reference: a "
            "kernel-quadrature self-reference cannot yield RAO admission."
        )
    reference: dict[str, Any] = {"kind": kind}
    if "prehistory" in data:
        # Declared periodic prehistory (owner decision 2026-10-13): hydro_spectrum only,
        # bound to the provenance's recorded declaration at assessment.
        if kind != REFERENCE_HYDRO_SPECTRUM:
            raise AssessmentCaseError("only a hydro_spectrum reference may declare a prehistory")
        try:
            reference["prehistory"] = validated_declaration(data["prehistory"])
        except PrehistoryError as error:
            raise AssessmentCaseError(f"reference.prehistory: {error}") from error
    if kind == REFERENCE_ANALYTIC_EXPONENTIAL:
        if "coefficient_matrix" not in data or "decay" not in data:
            raise AssessmentCaseError(
                "the analytic_exponential reference must declare its 4x4 "
                "coefficient matrix R and decay rate alpha"
            )
        matrix = np.asarray(data["coefficient_matrix"], dtype=float)
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            raise AssessmentCaseError(
                "reference.coefficient_matrix must be a finite 4x4 numeric matrix"
            )
        decay = _require_positive("reference.decay", data["decay"])
        reference["coefficient_matrix"] = [[float(x) for x in row] for row in matrix]
        reference["decay"] = decay
    elif kind == REFERENCE_HYDRO_SOURCE:
        # hydro_source: the SOURCE-ONLY free-decay reference. Free decay has
        # no frequency-domain phasor to select, so the descriptor binds only
        # the actual source identity (route, dataset content hash and, for
        # the derived route, the derived operator identities); the physical
        # proof re-runs the full source recompilation at assessment. No
        # omega/heading/wave phasor and no analytic label is admitted here.
        for key in ("route", "dataset_content_hash"):
            if key not in data:
                raise AssessmentCaseError(
                    f"the hydro_source reference must declare {key!r}"
                )
        for key in (
            "omega",
            "heading",
            "wave_amplitude",
            "wave_phase",
            "coefficient_matrix",
            "decay",
        ):
            if key in data:
                raise AssessmentCaseError(
                    f"a hydro_source reference is source-only free decay and "
                    f"must not declare {key!r}; no frequency-domain phasor "
                    "or analytic label is admitted"
                )
        route = str(data["route"])
        if route not in HYDRO_REFERENCE_ROUTES:
            raise AssessmentCaseError(
                f"reference.route must be one of {HYDRO_REFERENCE_ROUTES}; "
                f"received {route!r}"
            )
        reference["route"] = route
        reference["dataset_content_hash"] = _require_hash(
            "reference.dataset_content_hash", data["dataset_content_hash"]
        )
        if route == HYDRO_ROUTE_DERIVED:
            for key in ("derived_content_hash", "operators_raw_sha256"):
                if key not in data:
                    raise AssessmentCaseError(
                        f"the derived-route hydro_source reference must "
                        f"declare {key!r}"
                    )
            reference["derived_content_hash"] = _require_hash(
                "reference.derived_content_hash", data["derived_content_hash"]
            )
            reference["operators_raw_sha256"] = _require_hash(
                "reference.operators_raw_sha256", data["operators_raw_sha256"]
            )
        elif (
            "derived_content_hash" in data or "operators_raw_sha256" in data
        ):
            raise AssessmentCaseError(
                "a raw-route hydro_source reference must not declare derived "
                "operator identities; the raw route consumes the exact stored "
                "raw dataset sample only"
            )
    else:
        # hydro_spectrum: the FD reference is the ACTUAL stored neutral
        # dataset / derived-operator spectrum sample, never a transform of
        # the TD kernel. The immutable source identity is bound here and
        # re-proved against the public project hydro provenance at
        # assessment and reproduction.
        for key in (
            "route",
            "omega",
            "heading",
            "wave_amplitude",
            "wave_phase",
            "dataset_content_hash",
        ):
            if key not in data:
                raise AssessmentCaseError(
                    f"the hydro_spectrum reference must declare {key!r}"
                )
        route = str(data["route"])
        if route not in HYDRO_REFERENCE_ROUTES:
            raise AssessmentCaseError(
                f"reference.route must be one of {HYDRO_REFERENCE_ROUTES}; "
                f"received {route!r}"
            )
        reference["route"] = route
        reference["omega"] = _require_positive("reference.omega", data["omega"])
        reference["heading"] = _require_finite("reference.heading", data["heading"])
        reference["wave_amplitude"] = _require_positive(
            "reference.wave_amplitude", data["wave_amplitude"]
        )
        reference["wave_phase"] = _require_finite(
            "reference.wave_phase", data["wave_phase"]
        )
        reference["dataset_content_hash"] = _require_hash(
            "reference.dataset_content_hash", data["dataset_content_hash"]
        )
        if route == HYDRO_ROUTE_DERIVED:
            for key in ("derived_content_hash", "operators_raw_sha256"):
                if key not in data:
                    raise AssessmentCaseError(
                        f"the derived-route hydro_spectrum reference must "
                        f"declare {key!r}"
                    )
            reference["derived_content_hash"] = _require_hash(
                "reference.derived_content_hash", data["derived_content_hash"]
            )
            reference["operators_raw_sha256"] = _require_hash(
                "reference.operators_raw_sha256", data["operators_raw_sha256"]
            )
        elif (
            "derived_content_hash" in data or "operators_raw_sha256" in data
        ):
            raise AssessmentCaseError(
                "a raw-route hydro_spectrum reference must not declare derived "
                "operator identities; the raw route consumes the exact stored "
                "raw dataset sample only"
            )
    return reference


def _validate_windows(data, inputs: LinearDevelopmentInputs) -> dict:
    _exact_keys("windows", data, ("start", "mid", "end"))
    start = _require_finite("windows.start", data["start"])
    mid = _require_finite("windows.mid", data["mid"])
    end = _require_finite("windows.end", data["end"])
    grid_start = inputs.t0
    grid_end = inputs.t0 + (inputs.n_steps - 1) * inputs.dt
    if not (grid_start <= start < mid < end <= grid_end + 1.0e-9):
        raise AssessmentCaseError(
            f"windows must satisfy t0 <= start < mid < end <= grid end inside the "
            f"input grid [{grid_start!r}, {grid_end!r}]; received "
            f"[{start!r}, {mid!r}, {end!r}]"
        )
    if abs((mid - start) - (end - mid)) > 1.0e-9:
        raise AssessmentCaseError(
            "the two fit windows must be adjacent and of equal duration; "
            f"received durations {mid - start!r} and {end - mid!r}"
        )
    return {"start": start, "mid": mid, "end": end}


def _validate_fit_controls(data) -> dict:
    _exact_keys("fit_controls", data, ("intercepts", "drift", "max_condition"))
    if not isinstance(data["intercepts"], bool):
        raise AssessmentCaseError("fit_controls.intercepts must be a boolean")
    drift = str(data["drift"])
    if drift not in DRIFT_RULES:
        raise AssessmentCaseError(
            f"fit_controls.drift must be one of {DRIFT_RULES}; received {drift!r}"
        )
    max_condition = _require_positive("fit_controls.max_condition", data["max_condition"])
    if max_condition > 1.0e12:
        raise AssessmentCaseError(
            f"fit_controls.max_condition {max_condition!r} exceeds the explicit "
            "bound 1e12"
        )
    return {
        "intercepts": bool(data["intercepts"]),
        "drift": drift,
        "max_condition": max_condition,
    }


def _validate_refinement(data):
    if data is None:
        return None
    _exact_keys("refinement", data, ("partner_case_hash", "dt_ratio", "role"))
    role = str(data["role"])
    if role not in ("coarse", "fine"):
        raise AssessmentCaseError("refinement.role must be 'coarse' or 'fine'")
    ratio = data["dt_ratio"]
    if isinstance(ratio, bool) or not isinstance(ratio, int):
        raise AssessmentCaseError(
            f"refinement.dt_ratio must be an integer >= 2; received {ratio!r}"
        )
    if ratio < 2:
        raise AssessmentCaseError(f"refinement.dt_ratio must be >= 2; received {ratio}")
    partner = data["partner_case_hash"]
    if partner is not None:
        partner = _require_hash("refinement.partner_case_hash", partner)
    return {"partner_case_hash": partner, "dt_ratio": int(ratio), "role": role}


def _validate_force_phasors(data) -> list:
    if not isinstance(data, list) or len(data) != 4:
        raise AssessmentCaseError("force_phasors must be a list of four [re, im] pairs")
    phasors = []
    for entry in data:
        if not isinstance(entry, list) or len(entry) != 2:
            raise AssessmentCaseError("each force phasor must be a [re, im] pair")
        real = _require_finite("force_phasors.re", entry[0])
        imag = _require_finite("force_phasors.im", entry[1])
        phasors.append([real, imag])
    return phasors


class AssessmentCase:
    """Immutable closed-world hash-bound assessment case.

    Built by :func:`prepare_case` from a plain specification and the stored
    linear inputs BEFORE the rehearsal solve. The case hash covers every
    field except ``created_utc``; stored case artifacts are content-addressed
    and any mutation is refused on load.
    """

    def __init__(self, data: dict):
        self._data = json.loads(json.dumps(dict(data), allow_nan=False))

    @property
    def case_id(self) -> str:
        return self._data["case_id"]

    @property
    def mode(self) -> str:
        return self._data["mode"]

    @property
    def input_hash(self) -> str:
        return self._data["input_hash"]

    @property
    def result_id(self):
        return self._data["result_id"]

    @property
    def reference(self) -> dict:
        return self._data["reference"]

    @property
    def omega(self):
        return self._data["omega"]

    @property
    def force_phasors(self):
        return self._data["force_phasors"]

    @property
    def windows(self):
        return self._data["windows"]

    @property
    def fit_controls(self) -> dict:
        return self._data["fit_controls"]

    @property
    def criteria(self) -> dict:
        return self._data["criteria"]

    @property
    def refinement(self):
        return self._data["refinement"]

    @property
    def scope(self) -> dict:
        return self._data["scope"]

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self._data, allow_nan=False))

    @property
    def case_hash(self) -> str:
        data = self.to_dict()
        data.pop("created_utc", None)
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"AssessmentCase(case_id={self.case_id!r}, mode={self.mode!r})"


class AssessmentReport:
    """Immutable assessment report: verdict, checks and raw authoritative arrays."""

    def __init__(self, data: dict):
        self._data = json.loads(json.dumps(dict(data), allow_nan=False))

    @property
    def verdict(self) -> str:
        return self._data["verdict"]

    @property
    def case_hash(self) -> str:
        return self._data["case_hash"]

    @property
    def report_id(self) -> str:
        return self._data["report_id"]

    @property
    def input_hash(self) -> str:
        return self._data["input_hash"]

    @property
    def result_id(self) -> str:
        return self._data["result_id"]

    @property
    def scope(self) -> dict:
        return self._data["scope"]

    @property
    def checks(self) -> list:
        return self._data["checks"]

    @property
    def failed(self) -> list:
        return self._data["failed"]

    @property
    def arrays(self) -> dict:
        return self._data["arrays"]

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self._data, allow_nan=False))

    @property
    def report_hash(self) -> str:
        data = self.to_dict()
        data.pop("created_utc", None)
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"AssessmentReport(verdict={self.verdict!r}, case={self.case_hash[:12]}...)"


def _admit_source_reference(inputs: LinearDevelopmentInputs, mode: str,
                            reference_kind: str, error) -> None:
    """Shared source/reference admission guard.

    Governing contract: for HYDRO inputs the RAO reference MUST be the
    actual neutral/derived source spectrum. Declaring a different
    reference kind cannot revoke hydro ownership of the source proof, so
    the guard runs at BOTH prepare_case (registration) and assess_result
    (a loaded or directly constructed AssessmentCase cannot bypass
    preparation). Hydro-source free decay is admitted ONLY through the
    source-only ``hydro_source`` reference: the actual project context and
    the full source recompilation proof are mandatory, and it is never
    admitted by relabelling the reference analytic. Explicit/analytic
    rehearsal inputs remain admitted unchanged.
    """

    if inputs.provenance.source != LINEAR_SOURCE_HYDRO:
        return
    if mode == MODE_REGULAR_WAVE:
        if reference_kind != REFERENCE_HYDRO_SPECTRUM:
            raise error(
                f"the inputs are hydro-sourced (provenance.source="
                f"{LINEAR_SOURCE_HYDRO!r}): a regular_wave case MUST use the "
                f"{REFERENCE_HYDRO_SPECTRUM!r} reference (the actual neutral/"
                "derived source spectrum); declaring "
                f"{REFERENCE_ANALYTIC_EXPONENTIAL!r} cannot revoke hydro "
                "source ownership"
            )
    elif mode == MODE_FREE_DECAY:
        if reference_kind != REFERENCE_HYDRO_SOURCE:
            raise error(
                "the inputs are hydro-sourced (provenance.source="
                f"{LINEAR_SOURCE_HYDRO!r}): a free_decay case MUST use the "
                f"source-only {REFERENCE_HYDRO_SOURCE!r} reference (the "
                "actual project context and the full source recompilation "
                "proof are mandatory); declaring "
                f"{REFERENCE_ANALYTIC_EXPONENTIAL!r} cannot revoke hydro "
                "source ownership"
            )


def prepare_case(spec: dict, inputs: LinearDevelopmentInputs) -> AssessmentCase:
    """Validate and freeze an assessment case against the stored *inputs*.

    The case is bound to the exact effective ``linear_input_hash`` and the
    actual input grid; windows, the Nyquist limit, cycle counts, the drift
    rule and every criterion are validated here, before any rehearsal solve.
    The specification is closed-world: unknown fields are refused.
    """

    if not isinstance(spec, dict):
        raise AssessmentCaseError("the case specification must be a JSON object")
    # The caller's specification may omit schema_version (it defaults to the
    # current schema); a declared value must match exactly, never upgrade.
    _exact_keys(
        "case", spec, _CASE_KEYS, optional=("created_utc", "schema_version")
    )
    if spec.get("schema_version", ASSESSMENT_SCHEMA_VERSION) != (
        ASSESSMENT_SCHEMA_VERSION
    ):
        raise AssessmentCaseError(
            f"unsupported case schema_version {spec.get('schema_version')!r}"
        )
    mode = str(spec["mode"])
    if mode not in MODES:
        raise AssessmentCaseError(f"case mode must be one of {MODES}; received {mode!r}")
    case_id = str(spec["case_id"])
    if not case_id.strip():
        raise AssessmentCaseError("case_id must be a non-empty string")
    input_hash = _require_hash("case.input_hash", spec["input_hash"])
    if input_hash != linear_input_hash(inputs):
        raise AssessmentCaseError(
            "the case input_hash does not match the stored linear inputs; a case "
            "is bound to the exact effective inputs it will assess"
        )
    result_id = spec["result_id"]
    if result_id is not None and (not isinstance(result_id, str) or not result_id.strip()):
        raise AssessmentCaseError("case result_id must be a non-empty string or null")

    reference = _validate_reference(spec["reference"])
    if reference["kind"] == REFERENCE_HYDRO_SPECTRUM and mode != MODE_REGULAR_WAVE:
        raise AssessmentCaseError(
            "a hydro_spectrum reference selects an exact stored frequency/"
            "heading sample and is only admissible for a regular_wave case; "
            "free decay has no frequency-domain reference to select"
        )
    if reference["kind"] == REFERENCE_HYDRO_SOURCE and mode != MODE_FREE_DECAY:
        raise AssessmentCaseError(
            "a hydro_source reference is the source-only free-decay "
            "reference and is only admissible for a free_decay case; a "
            "regular_wave case must select the actual stored spectrum sample"
        )
    _admit_source_reference(inputs, mode, reference["kind"], AssessmentCaseError)
    omega = None
    force_phasors = None
    windows = None
    if mode == MODE_REGULAR_WAVE:
        omega = _require_positive("case.omega", spec["omega"])
        if reference["kind"] == REFERENCE_HYDRO_SPECTRUM:
            if omega != reference["omega"]:
                raise AssessmentCaseError(
                    "the case omega must equal the hydro_spectrum reference's "
                    "declared source frequency; the reference sample selection "
                    "is bound prospectively, never re-picked at assessment"
                )
        if omega * inputs.dt >= math.pi:
            raise AssessmentCaseError(
                f"omega {omega!r} is not below the Nyquist limit pi/dt = "
                f"{math.pi / inputs.dt!r} for dt = {inputs.dt!r}"
            )
        force_phasors = _validate_force_phasors(spec["force_phasors"])
        windows = _validate_windows(spec["windows"], inputs)
        cycle = 2.0 * math.pi / omega
        for name, duration in (
            ("first", windows["mid"] - windows["start"]),
            ("second", windows["end"] - windows["mid"]),
        ):
            if duration < 2.0 * cycle - 1.0e-9:
                raise AssessmentCaseError(
                    f"the {name} fit window duration {duration!r} is shorter than "
                    f"two cycles ({2.0 * cycle!r}) at omega = {omega!r}"
                )
    else:
        if spec["omega"] is not None or spec["force_phasors"] is not None:
            raise AssessmentCaseError(
                "a free_decay case must not declare omega or force phasors"
            )
        if spec["windows"] is not None:
            raise AssessmentCaseError(
                "a free_decay case must not declare fit windows; there is no "
                "steady-state extraction to predeclare"
            )

    fit_controls = _validate_fit_controls(spec["fit_controls"])
    if fit_controls["drift"] == DRIFT_COMMON_SURGE_LINEAR:
        # The drift direction is only admitted for a stiffness that actually
        # annihilates it, under the existing 1e-10 symmetry tolerance and the
        # declared N/m scale; a model that resists the common surge mode must
        # not have free drift masked by it.
        stiffness = np.asarray(inputs.stiffness, dtype=float)
        direction = np.asarray(COMMON_SURGE_DIRECTION, dtype=float)
        residual = float(np.max(np.abs(stiffness @ direction)))
        scale = max(float(np.max(np.abs(stiffness))), 1.0)
        if residual > 1.0e-10 * scale:
            raise AssessmentCaseError(
                f"fit_controls.drift common_surge_linear requires K u = 0 for "
                f"the actual stiffness (max |K u| = {residual:.6g} > "
                f"{1.0e-10 * scale:.6g}); the drift rule does not apply to this model"
            )
    criteria = _validate_criteria(spec["criteria"])
    refinement = _validate_refinement(spec["refinement"])
    scope = spec["scope"]
    if not isinstance(scope, dict) or scope.get("development_only") is not True:
        raise AssessmentCaseError(
            "case scope must declare development_only=true; assessment output is "
            "never a qualified or production result"
        )
    data = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "case_id": case_id,
        "mode": mode,
        "input_hash": input_hash,
        "result_id": result_id,
        "reference": reference,
        "omega": omega,
        "force_phasors": force_phasors,
        "windows": windows,
        "fit_controls": fit_controls,
        "criteria": criteria,
        "refinement": refinement,
        "scope": {
            "development_only": True,
            "role": str(scope.get("role", "development_assessment")),
        },
        "created_utc": str(
            spec.get("created_utc")
            or _datetime.datetime.now(_datetime.timezone.utc).isoformat()
        ),
    }
    return AssessmentCase(data)


def load_case(data: dict) -> AssessmentCase:
    """Rebuild a stored case artifact; refuse mutation or unknown fields."""

    if not isinstance(data, dict):
        raise AssessmentCaseError("a stored case must be a JSON object")
    _exact_keys("case", data, _CASE_KEYS, optional=("created_utc",))
    if data.get("schema_version") != ASSESSMENT_SCHEMA_VERSION:
        raise AssessmentCaseError(
            f"unsupported case schema_version {data.get('schema_version')!r}"
        )
    case = AssessmentCase(data)
    _validate_criteria(case.criteria)
    _validate_fit_controls(case.fit_controls)
    _validate_reference(case.reference)
    _validate_refinement(case.refinement)
    if case.mode not in MODES:
        raise AssessmentCaseError(f"unknown case mode {case.mode!r}")
    if case.mode == MODE_REGULAR_WAVE:
        if case.omega is None or case.force_phasors is None or case.windows is None:
            raise AssessmentCaseError(
                "a regular_wave case must carry omega, force phasors and windows"
            )
        _validate_force_phasors(case.force_phasors)
    else:
        if case.omega is not None or case.force_phasors is not None:
            raise AssessmentCaseError(
                "a free_decay case must not declare omega or force phasors"
            )
    _require_hash("case.input_hash", case.input_hash)
    if not isinstance(case.scope, dict) or case.scope.get("development_only") is not True:
        raise AssessmentCaseError(
            "a stored case must declare development_only=true scope"
        )
    return case


def load_report(data: dict) -> AssessmentReport:
    """Rebuild a stored report artifact; refuse mutation or unknown fields."""

    if not isinstance(data, dict):
        raise AssessmentCaseError("a stored report must be a JSON object")
    _exact_keys("report", data, _REPORT_KEYS, optional=("created_utc",))
    if data.get("schema_version") != ASSESSMENT_SCHEMA_VERSION:
        raise AssessmentCaseError(
            f"unsupported report schema_version {data.get('schema_version')!r}"
        )
    if data.get("verdict") not in ("pass", "nogo"):
        raise AssessmentCaseError(f"unknown report verdict {data.get('verdict')!r}")
    return AssessmentReport(data)


# ------------------------------------------------------------------ numerics


def _channels(result: ResultRecord, inputs: LinearDevelopmentInputs) -> dict:
    arrays = {}
    for prefix in (
        "q",
        "v",
        "a",
        "force_external",
        "force_dry_inertia",
        "force_added_inertia",
        "force_memory",
        "force_damping",
        "force_stiffness",
        "force_residual",
    ):
        arrays[prefix] = np.column_stack(
            [
                np.asarray(result.channels[f"{prefix}:{label}"], dtype=float)
                for label in inputs.labels
            ]
        )
    return arrays


def _fd_reference(inputs: LinearDevelopmentInputs, case: AssessmentCase, spectrum):
    """Full complex coupled4 frequency-domain reference at the case omega.

    Returns ``(qhat_fd, A_omega, B_omega)``. ``Ainf`` is used in the time
    domain; the frequency-domain reference uses ``A(omega)`` per the matched
    SI convention. The reference arrays come from the case's declared
    reference: the independent analytic closed form (after validating the
    stored kernel against it) or the ACTUAL stored hydro spectrum sample
    loaded and source-proved by :func:`load_hydro_spectrum`. The stored TD
    kernel is never Fourier-transformed into an admission reference.
    """

    omega = case.omega
    mass_dry = np.asarray(inputs.mass_dry, dtype=float)
    ainf = np.asarray(inputs.added_mass_infinite, dtype=float)
    damping = np.asarray(inputs.damping, dtype=float)
    stiffness = np.asarray(inputs.stiffness, dtype=float)
    reference = case.reference
    if reference["kind"] == REFERENCE_ANALYTIC_EXPONENTIAL:
        r_matrix = np.asarray(reference["coefficient_matrix"], dtype=float)
        alpha = float(reference["decay"])
        kernel = np.asarray(inputs.radiation_kernel, dtype=float)
        factors = np.exp(-alpha * np.asarray(inputs.kernel_times, dtype=float))
        expected = r_matrix[None, :, :] * factors[:, None, None]
        scale = float(np.max(np.abs(kernel)))
        deviation = float(np.max(np.abs(kernel - expected)))
        tolerance = case.criteria["kernel_match_relative"] * max(scale, 1.0)
        if deviation > tolerance:
            raise AssessmentRefusalError(
                f"the stored radiation kernel does not match the case's analytic "
                f"exponential reference (max deviation {deviation:.6g} > "
                f"{tolerance:.6g}); the reference does not describe this result"
            )
        b_omega = alpha * r_matrix / (alpha * alpha + omega * omega)
        a_omega = ainf - r_matrix / (alpha * alpha + omega * omega)
    else:
        if spectrum is None:
            raise AssessmentRefusalError(
                "a hydro_spectrum reference requires the real project context "
                "(public hydro provenance and the project root holding the "
                "authoritative sidecars); a generic case without the actual "
                "stored dataset/operator proof is refused"
            )
        a_omega = np.asarray(spectrum["a_omega"], dtype=float)
        b_omega = np.asarray(spectrum["b_omega"], dtype=float)
    impedance = (
        stiffness
        - omega * omega * (mass_dry + a_omega)
        - 1j * omega * (b_omega + damping)
    )
    force = _phasor_array(case.force_phasors)
    try:
        qhat = np.linalg.solve(impedance, force)
    except np.linalg.LinAlgError as error:
        raise AssessmentRefusalError(
            f"the frequency-domain reference system is singular at omega={omega!r}: "
            f"{error}"
        ) from error
    return qhat, a_omega, b_omega


def fd_reference_response(case: AssessmentCase, inputs: LinearDevelopmentInputs, provenance,
                          project_root) -> np.ndarray:
    """The source-proved FD reference response phasor of a hydro_spectrum case.

    The same reference the assessment compares against (actual stored spectrum,
    re-proved from the project's sidecars); used, e.g., to start a regular-wave run
    from its declared FD steady state.
    """
    if case.reference["kind"] != REFERENCE_HYDRO_SPECTRUM:
        raise AssessmentRefusalError("an FD steady state needs a hydro_spectrum reference")
    if case.input_hash != linear_input_hash(inputs):
        raise AssessmentRefusalError(
            "the case input_hash does not match the given linear inputs; the FD "
            "steady state of one case is never computed from another model's inputs")
    spectrum = load_hydro_spectrum(case, inputs, provenance, project_root)
    qhat, _, _ = _fd_reference(inputs, case, spectrum)
    return qhat


def _phasor_array(pairs) -> np.ndarray:
    """Build the complex force-phasor vector from [real, imag] pairs."""

    return np.asarray(
        [complex(float(re), float(im)) for re, im in pairs],
        dtype=np.complex128,
    )


def _window_indices(times: np.ndarray, start: float, end: float) -> np.ndarray:
    return np.nonzero((times >= start - 1.0e-12) & (times <= end + 1.0e-12))[0]


def _fit_window(times: np.ndarray, q: np.ndarray, case: AssessmentCase,
                start: float, end: float):
    """Least-squares cos/sin fit with the negative phasor convention.

    Returns ``(phasors, residuals, condition, rank, columns, indices,
    drift_coefficient)`` where ``phasors[d] = a + i b`` for
    ``q(t) ~ a cos(omega t) + b sin(omega t)`` and ``drift_coefficient`` is
    the ONE shared common-surge drift coefficient (None when the drift rule
    is not declared).
    The four DOFs are fit as ONE stacked least-squares problem: independent
    cos/sin (and optional intercept) columns per DOF, plus — when the
    common-surge drift rule is declared — exactly ONE shared drift
    coefficient multiplying ``u = (1, 0, 1, 0)/sqrt2`` across all stacked
    4-DOF observations. No per-DOF or heave drift is ever admitted.
    """

    omega = case.omega
    indices = _window_indices(times, start, end)
    controls = case.fit_controls
    drift = controls["drift"] != DRIFT_NONE
    per_dof_columns = 2 + (1 if controls["intercepts"] else 0)
    total_columns = 4 * per_dof_columns + (1 if drift else 0)
    if len(indices) < per_dof_columns:
        raise AssessmentRefusalError(
            f"the fit window [{start!r}, {end!r}] holds {len(indices)} samples, "
            f"fewer than the {per_dof_columns} per-DOF fit columns; the "
            "window/rank is inadequate"
        )
    t = times[indices]
    samples = len(indices)
    base = np.column_stack((np.cos(omega * t), np.sin(omega * t)))
    if controls["intercepts"]:
        base = np.column_stack((base, np.ones_like(t)))
    design = np.zeros((4 * samples, total_columns))
    for dof in range(4):
        design[
            dof * samples:(dof + 1) * samples,
            dof * per_dof_columns:(dof + 1) * per_dof_columns,
        ] = base
    if drift:
        direction = np.asarray(COMMON_SURGE_DIRECTION, dtype=float)
        design[:, -1] = np.concatenate(
            [direction[dof] * t for dof in range(4)]
        )
    condition = float(np.linalg.cond(design))
    rank = int(np.linalg.matrix_rank(design))
    if rank < total_columns:
        raise AssessmentRefusalError(
            f"the fit design matrix in [{start!r}, {end!r}] is rank deficient "
            f"({rank} < {total_columns} columns); the window is inadequate"
        )
    if condition > controls["max_condition"]:
        raise AssessmentRefusalError(
            f"the fit design matrix condition {condition:.6g} exceeds the declared "
            f"bound {controls['max_condition']:.6g} in [{start!r}, {end!r}]"
        )
    rhs = np.concatenate([q[indices, dof] for dof in range(4)])
    coefficients, _, _, _ = np.linalg.lstsq(design, rhs, rcond=None)
    residuals = (rhs - design @ coefficients).reshape(4, samples).T
    qhat = np.empty(4, dtype=complex)
    for dof in range(4):
        offset = dof * per_dof_columns
        qhat[dof] = complex(coefficients[offset], coefficients[offset + 1])
    drift_coefficient = float(coefficients[-1]) if drift else None
    return (qhat, residuals, condition, rank, total_columns, indices,
            drift_coefficient)


def _memory_oracle(inputs: LinearDevelopmentInputs, velocities: np.ndarray) -> np.ndarray:
    """Independent discrete causal trapezoid convolution of the stored kernel.

    ``F[n] = dt*(0.5*K[n] v_0 + sum_{j=1..n-1} K[n-j] v_j + 0.5*K[0] v_n)``
    with ``F[0] = 0`` (zero prehistory), computed through an FFT full
    convolution plus the endpoint corrections. Verification oracle only: it
    never replaces the production solver.
    """

    n_steps = inputs.n_steps
    dt = inputs.dt
    kernel = np.asarray(inputs.radiation_kernel[:n_steps], dtype=float)
    size = 1
    while size < 2 * n_steps:
        size *= 2
    full = np.zeros((n_steps, 4))
    for i in range(4):
        for j in range(4):
            spectrum = np.fft.rfft(kernel[:, i, j], size) * np.fft.rfft(
                velocities[:, j], size
            )
            full[:, i] += np.fft.irfft(spectrum, size)[:n_steps]
    # Independent endpoint contractions: K[n] v_0 per step (first endpoint)
    # and K[0] v_n per step (second endpoint, velocities @ K[0]^T).
    oracle = dt * (
        full
        - 0.5 * np.einsum("kij,j->ki", kernel, velocities[0])
        - 0.5 * (velocities @ kernel[0].T)
    )
    oracle[0] = 0.0
    return oracle


def _check(name: str, unit: str, value, limit: float, detail: str = "") -> dict:
    if value is None:
        return {
            "name": name,
            "unit": unit,
            "value": None,
            "limit": None,
            "passed": True,
            "detail": detail,
        }
    passed = bool(float(value) <= float(limit))
    return {
        "name": name,
        "unit": unit,
        "value": float(value),
        "limit": float(limit),
        "passed": passed,
        "detail": detail,
    }


def _max_entry_deviation(stored: np.ndarray, recomputed: np.ndarray,
                         relative: float, absolute: float):
    deviation = np.abs(stored - recomputed)
    scale = np.maximum(np.abs(stored), np.abs(recomputed))
    normalized = deviation / np.maximum(scale, 1.0)
    worst_value = float(np.max(deviation))
    worst_normalized = float(np.max(normalized))
    passed = bool(np.all(deviation <= relative * scale + absolute))
    return worst_value, worst_normalized, passed


# ------------------------------------------------------ hydro spectrum route


def _import_anyhydro():
    try:
        import anyhydro
    except ImportError as error:  # pragma: no cover - environment dependent
        raise AssessmentRefusalError(
            f"the hydro_spectrum reference requires the installed ANYhydro "
            f"package: {error}"
        ) from error
    return anyhydro


def _read_project_sidecar(project_root, reference: str, expected_sha256: str,
                          role: str) -> bytes:
    check_relative_reference(reference)
    path = resolve_sidecar(Path(project_root), reference)
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise AssessmentRefusalError(
            f"cannot read the authoritative {role} sidecar {reference!r} from "
            f"the project root {project_root}: {error}"
        ) from error
    digest = sha256_bytes(payload)
    if digest != expected_sha256:
        raise AssessmentRefusalError(
            f"the {role} sidecar {reference!r} does not match the hydro "
            f"provenance raw sha256 ({digest[:12]}... != "
            f"{expected_sha256[:12]}...); the actual source bytes are refused"
        )
    return payload


def _exact_sample_index(values, target, label: str) -> int:
    array = np.asarray(values, dtype=np.float64)
    matches = np.nonzero(array == float(target))[0]
    if matches.size != 1:
        raise AssessmentRefusalError(
            f"the case {label} {target!r} is not an exact unique stored "
            f"sample ({matches.size} matches); no interpolation is performed"
        )
    return int(matches[0])


def _verify_hydro_project_context(inputs: LinearDevelopmentInputs,
                                  project, provenance,
                                  project_root) -> HydroBridgeProvenance:
    """Prove the hydro case against the ACTUAL project context.

    A hydro_spectrum assessment is only eligible with the actual
    ``ProjectModel`` and the project root holding the authoritative
    sidecars. The assessed inputs must BE the project's stored linear
    inputs (the public binding verifier re-checks bodies/mesh/equilibrium,
    and the stored input hash must match the argument), any supplied
    provenance record must be the project's own record, and the public
    ``ApplicationService.verify_hydro_provenance`` re-runs the full source
    proof (including the authoritative recompilation for the derived
    route) so re-hashed bookkeeping over substituted effective matrices is
    refused. No caller flag, hash string or documentation substitute is
    accepted; a generic hydro case without the actual project is refused.
    The project is supplied by the caller (the live or reloaded project);
    a stale disk project is never loaded here to substitute for the
    actual current unsaved project.
    """

    if not isinstance(project, ProjectModel):
        raise AssessmentRefusalError(
            "a hydro_spectrum reference requires the actual ProjectModel "
            "context (the project whose stored inputs, hydro provenance and "
            "authoritative sidecars are being assessed); a generic case "
            "without the actual project is refused"
        )
    if project_root is None:
        raise AssessmentRefusalError(
            "a hydro_spectrum reference requires the project root holding "
            "the authoritative hydro sidecars; a generic case without the "
            "actual stored sources is refused"
        )
    try:
        verify_linear_binding(project, inputs)
    except Exception as error:
        raise AssessmentRefusalError(
            f"the assessed inputs do not describe the actual project "
            f"bodies/mesh/equilibrium: {error}"
        ) from error
    stored_inputs = project.linear_inputs
    if stored_inputs is None:
        raise AssessmentRefusalError(
            "the actual project carries no stored linear inputs; the hydro "
            "case cannot be assessed against it"
        )
    if linear_input_hash(stored_inputs) != linear_input_hash(inputs):
        raise AssessmentRefusalError(
            "the assessed inputs are not the actual project's stored linear "
            "inputs (input hash mismatch); substituted effective matrices "
            "are refused even when the case and provenance bookkeeping are "
            "re-hashed consistently"
        )
    actual_provenance = project.hydro_provenance
    if actual_provenance is None:
        raise AssessmentRefusalError(
            "the actual project carries no hydro provenance record; the "
            "hydro source intent cannot be proved and the assessment is "
            "refused"
        )
    if provenance is not None and (
        not isinstance(provenance, HydroBridgeProvenance)
        or provenance.to_dict() != actual_provenance.to_dict()
    ):
        raise AssessmentRefusalError(
            "the supplied hydro provenance record is not the actual "
            "project's hydro provenance; a substituted or re-hashed "
            "provenance record is refused"
        )
    # Public service verifier: re-runs the full hydro source proof on the
    # actual project (metadata/input-owned evidence consistency and, for
    # the derived route, the authoritative recompilation from the stored
    # dataset/operators/kernel sidecars matching the actual input hash).
    from .services import ApplicationService, ServiceError

    try:
        ApplicationService(project_root).verify_hydro_provenance(project)
    except ServiceError as error:
        raise AssessmentRefusalError(
            f"the actual project's hydro provenance does not re-verify "
            f"against the authoritative sources: {error}"
        ) from error
    return actual_provenance


def hydro_reciprocal_sensitivity(artifact, inputs: LinearDevelopmentInputs,
                                 omega: float, omega_index: int,
                                 f_hat: np.ndarray, criteria: dict) -> dict:
    """Working raw-versus-processed impedance/response sensitivity check.

    From the loaded public derived-operator artifact's ``raw_added_mass`` /
    ``raw_damping`` and ``derived_added_mass`` / ``derived_damping`` at the
    case omega, under the SAME physical dry mass, stiffness, mechanical
    damping and force phasor as the assessment, computes the full coupled
    complex impedance ``Z_raw`` vs ``Z_processed`` (compared as the
    work-conjugate ``S^T Z S`` max entry with the declared J floor/abs) and
    the full coupled complex response ``q_raw`` vs ``q_processed`` as the
    DIMENSIONLESS ``S^-1 qhat`` (declared dimensionless floor/abs; the
    retained SI solutions are recorded separately). No physics processing,
    clamping or criteria change happens here. A singular or non-finite raw
    FD system is a typed non-success with diagnostics, never a skip=>pass.
    """

    mass_dry = np.asarray(inputs.mass_dry, dtype=float)
    stiffness = np.asarray(inputs.stiffness, dtype=float)
    damping = np.asarray(inputs.damping, dtype=float)
    a_raw = np.asarray(artifact.raw_added_mass[omega_index], dtype=float)
    b_raw = np.asarray(artifact.raw_damping[omega_index], dtype=float)
    a_proc = np.asarray(artifact.derived_added_mass[omega_index], dtype=float)
    b_proc = np.asarray(artifact.derived_damping[omega_index], dtype=float)

    def impedance(a_matrix, b_matrix):
        return (
            stiffness
            - omega * omega * (mass_dry + a_matrix)
            - 1j * omega * (b_matrix + damping)
        )

    z_raw = impedance(a_raw, b_raw)
    z_processed = impedance(a_proc, b_proc)
    scales = np.asarray(artifact.settings.normalization.scales, dtype=float)
    conjugate = np.diag(scales)
    z_raw_c = conjugate.T @ z_raw @ conjugate
    z_processed_c = conjugate.T @ z_processed @ conjugate
    impedance_deviation = np.abs(z_raw_c - z_processed_c)
    impedance_scale = np.maximum(np.abs(z_raw_c), np.abs(z_processed_c))
    impedance_floor = float(criteria["impedance_floor_j"])
    impedance_relative = float(criteria["impedance_relative_limit"])
    impedance_absolute = float(criteria["impedance_absolute_j"])
    impedance_form = "per_coefficient"
    impedance_metric = float(np.max(impedance_deviation))
    impedance_pass = bool(
        np.all(
            impedance_deviation
            <= impedance_relative * np.maximum(impedance_scale, impedance_floor)
            + impedance_absolute
        )
    )
    if "impedance_full_block_relative" in criteria:
        # Full-block form (prospective owner decision): the per-coefficient maximum
        # stays recorded as a diagnostic (max_coefficient_deviation).
        impedance_form = "full_block"
        impedance_relative = float(criteria["impedance_full_block_relative"])
        block_deviation = float(np.linalg.norm(z_raw_c - z_processed_c))
        block_scale = float(np.linalg.norm(z_raw_c))
        impedance_metric = block_deviation / block_scale if block_scale > 0 else (
            0.0 if block_deviation == 0 else float("inf"))
        impedance_pass = bool(block_deviation <= impedance_relative * block_scale + impedance_absolute)

    solutions = {}
    for label, matrix in (("raw", z_raw), ("processed", z_processed)):
        try:
            solution = np.linalg.solve(matrix, f_hat)
        except np.linalg.LinAlgError as error:
            raise AssessmentRefusalError(
                f"the {label} frequency-domain system is singular at omega="
                f"{omega!r} (raw-versus-processed sensitivity cannot be "
                f"computed): {error}"
            ) from error
        if not np.all(np.isfinite(solution)):
            raise AssessmentRefusalError(
                f"the {label} frequency-domain response is non-finite at "
                f"omega={omega!r}; the raw-versus-processed sensitivity is a "
                "typed non-success, never a skip=>pass"
            )
        solutions[label] = solution
    q_raw_si = solutions["raw"]
    q_processed_si = solutions["processed"]
    # Governing response change: the DIMENSIONLESS qhat normalized by the
    # declared normalization scales, S^-1 q (one division per retained DOF).
    # The retained SI solutions are recorded separately; the comparison and
    # its floor/abs are dimensionless.
    scales = np.asarray(scales, dtype=float)
    q_raw = q_raw_si / scales
    q_processed = q_processed_si / scales
    response_deviation = np.abs(q_raw - q_processed)
    response_scale = np.maximum(np.abs(q_raw), np.abs(q_processed))
    response_floor = float(criteria["response_floor"])
    response_relative = float(criteria["response_relative_limit"])
    response_absolute = float(criteria["response_absolute"])
    response_pass = bool(
        np.all(
            response_deviation
            <= response_relative * np.maximum(response_scale, response_floor)
            + response_absolute
        )
    )
    return {
        "applicable": True,
        "policy": str(artifact.settings.correction_policy),
        "impedance": {
            "unit": "J (work-conjugate S^T Z S entries)",
            "max_deviation": float(np.max(impedance_deviation)),
            "max_scale": float(np.max(impedance_scale)),
            "relative_limit": impedance_relative,
            "floor": impedance_floor,
            "absolute": impedance_absolute,
            "passed": impedance_pass,
            **({
                # Full-block form: "unit" (J) still describes max_deviation/max_scale;
                # the governing value is dimensionless and carries its own unit.
                "form": impedance_form,
                "value": impedance_metric,
                "value_unit": "relative (Frobenius, ||Z_raw - Z_proc||_F / ||Z_raw||_F)",
                "max_coefficient_deviation": float(np.max(impedance_deviation)),
            } if impedance_form == "full_block" else {}),
        },
        "response": {
            "unit": "dimensionless (S^-1 qhat)",
            "max_deviation": float(np.max(response_deviation)),
            "max_scale": float(np.max(response_scale)),
            "relative_limit": response_relative,
            "floor": response_floor,
            "absolute": response_absolute,
            "passed": response_pass,
        },
        "q_raw": [[float(value.real), float(value.imag)] for value in q_raw],
        "q_processed": [
            [float(value.real), float(value.imag)] for value in q_processed
        ],
        "q_raw_si": [
            [float(value.real), float(value.imag)] for value in q_raw_si
        ],
        "q_processed_si": [
            [float(value.real), float(value.imag)] for value in q_processed_si
        ],
        "normalization_scales": [float(value) for value in scales],
    }


def kernel_spectrum_self_consistency(inputs: LinearDevelopmentInputs,
                                     omega: float, a_omega: np.ndarray,
                                     b_omega: np.ndarray) -> dict:
    """Self-consistency diagnostic of the stored TD kernel (never a gate).

    Tests the stored kernel's cosine/sine quadrature against the INDEPENDENT
    spectrum A(omega)/B(omega) the assessment actually used. This is an
    informational self-consistency diagnostic only: it can never yield RAO
    admission, a PASS verdict or a qualification reference.
    """

    kernel = np.asarray(inputs.radiation_kernel, dtype=float)
    times = np.asarray(inputs.kernel_times, dtype=float)
    # Trapezoid weights on the (uniform) kernel grid.
    step = float(times[1] - times[0])
    weights = np.full_like(times, step)
    weights[0] = weights[-1] = 0.5 * step
    b_quad = np.tensordot(weights * np.cos(omega * times), kernel, axes=(0, 0))
    a_quad = (
        np.asarray(inputs.added_mass_infinite, dtype=float)
        - np.tensordot(weights * np.sin(omega * times), kernel, axes=(0, 0)) / omega
    )
    return {
        "note": (
            "self-consistency diagnostic only: the stored TD kernel quadrature "
            "tested against the independent spectrum; never an admission "
            "reference and never a gate"
        ),
        "max_added_mass_deviation": float(np.max(np.abs(a_quad - a_omega))),
        "max_damping_deviation": float(np.max(np.abs(b_quad - b_omega))),
    }


def load_hydro_spectrum(case: AssessmentCase, inputs: LinearDevelopmentInputs,
                        provenance, project_root) -> dict:
    """Load and source-prove the ACTUAL stored hydro spectrum sample.

    The FD reference for hydro-sourced inputs is the actual stored neutral
    dataset ``A(omega)``/``B(omega)``/``F(omega, heading)`` (raw route, exact
    work-conjugate selector projection) or the public derived-operator
    artifact's processed arrays plus the full raw-source proof (derived
    route). The stored TD kernel is never Fourier-transformed into the
    reference. This load does NOT rely on the caller having run a service
    verifier: the actual dataset/operators/kernel sidecar bytes are re-read
    here from the project root, re-checked against the public provenance
    raw sha256 identities, re-loaded through the public ANYhydro loaders
    (which re-run the full source proof on the actual bytes) and the
    re-proved kernel must reproduce the stored effective input matrices
    exactly. Hash strings or caller booleans alone are never accepted as
    proof.
    """

    if not isinstance(provenance, HydroBridgeProvenance):
        raise AssessmentRefusalError(
            "a hydro_spectrum reference requires the project's public "
            "HydroBridgeProvenance record; no provenance, no assessment"
        )
    if project_root is None:
        raise AssessmentRefusalError(
            "a hydro_spectrum reference requires the project root holding "
            "the authoritative hydro sidecars; a generic case without the "
            "actual stored sources is refused"
        )
    if provenance.linear_input_hash != linear_input_hash(inputs):
        raise AssessmentRefusalError(
            "the hydro provenance does not describe the stored linear inputs "
            "(input hash mismatch); the hydro intent is stale and the "
            "assessment is refused"
        )
    reference = case.reference
    route = reference["route"]
    bundle = provenance.derived_bundle
    if route == HYDRO_ROUTE_DERIVED and bundle is None:
        raise AssessmentRefusalError(
            "the case declares the derived-operator route but the project "
            "carries no derived bundle; stripped derived intent is refused"
        )
    if route == HYDRO_ROUTE_RAW and bundle is not None:
        raise AssessmentRefusalError(
            "the case declares the raw route but the project carries a "
            "derived-operator bundle; the case route must match the actual "
            "import route"
        )
    if reference["dataset_content_hash"] != provenance.dataset_content_hash:
        raise AssessmentRefusalError(
            "the case's bound dataset content hash does not match the "
            "project hydro provenance; the case does not describe this source"
        )
    if route == HYDRO_ROUTE_DERIVED:
        if reference["derived_content_hash"] != bundle["derived_content_hash"]:
            raise AssessmentRefusalError(
                "the case's bound derived-operator content hash does not match "
                "the project hydro provenance derived bundle"
            )
        if reference["operators_raw_sha256"] != bundle["operators_raw_sha256"]:
            raise AssessmentRefusalError(
                "the case's bound operators raw sha256 does not match the "
                "project hydro provenance derived bundle"
            )
    if provenance.kernel_sidecar is None or (
        not str(provenance.kernel_sidecar)
    ):
        raise AssessmentRefusalError(
            "the hydro provenance records no radiation kernel sidecar; the "
            "actual TD source cannot be re-proved and the assessment is "
            "refused"
        )
    wave = provenance.wave
    if wave is None:
        raise AssessmentRefusalError(
            "the hydro import recorded no regular-wave selection; a "
            "hydro_spectrum case cannot be bound to this project"
        )
    for key, case_key in (
        ("omega", "omega"),
        ("heading", "heading"),
        ("amplitude", "wave_amplitude"),
        ("phase", "wave_phase"),
    ):
        if float(wave[key]) != float(reference[case_key]):
            raise AssessmentRefusalError(
                f"the case wave {case_key!r} {reference[case_key]!r} does not "
                f"match the provenance's recorded wave selection "
                f"({wave[key]!r}); the case selection is bound prospectively"
            )
    recorded = provenance.to_dict()["wave"].get("prehistory")
    if (recorded is None) != ("prehistory" not in reference) or (
            recorded is not None and json.loads(json.dumps(recorded)) != reference["prehistory"]):
        raise AssessmentRefusalError(
            "the case's declared prehistory does not match the provenance's recorded "
            "regular-wave prehistory (both absent, or identical)"
        )

    anyhydro = _import_anyhydro()
    dataset_payload = _read_project_sidecar(
        project_root, provenance.dataset_sidecar,
        provenance.dataset_raw_sha256, "dataset",
    )
    with tempfile.TemporaryDirectory(prefix="anymarine-assessment-") as temp_dir:
        dataset_copy = Path(temp_dir) / "dataset.nc"
        dataset_copy.write_bytes(dataset_payload)
        try:
            dataset = anyhydro.load_hydro_dataset(dataset_copy)
        except Exception as error:
            raise AssessmentRefusalError(
                f"cannot load the stored ANYhydro dataset sidecar: {error}"
            ) from error
        dataset_hash = anyhydro.dataset_content_hash(dataset)
        if dataset_hash != provenance.dataset_content_hash:
            raise AssessmentRefusalError(
                "the loaded dataset content hash does not reproduce the "
                "provenance identity; the actual source bytes are refused"
            )
        if route == HYDRO_ROUTE_DERIVED:
            operators_payload = _read_project_sidecar(
                project_root, bundle["operators_sidecar"],
                bundle["operators_raw_sha256"], "operators",
            )
            operators_copy = Path(temp_dir) / "operators.npz"
            operators_copy.write_bytes(operators_payload)
            try:
                # Public loader with the actual raw dataset: the full
                # raw-source proof (complete rederivation, never hash-only)
                # runs here on the actual bytes.
                artifact = anyhydro.load_derived_operators(
                    operators_copy, source_dataset=dataset
                )
            except Exception as error:
                raise AssessmentRefusalError(
                    f"cannot load/verify the stored derived operators "
                    f"sidecar: {error}"
                ) from error
        else:
            artifact = None
        # The radiation kernel sidecar is re-proved here as well: the actual
        # bytes are read from the project root, checked against the public
        # provenance raw sha256, loaded through the public ANYhydro loader
        # and its content hash re-checked. This load never relies on the
        # caller having run a service verifier first.
        kernel_payload = _read_project_sidecar(
            project_root, provenance.kernel_sidecar,
            provenance.kernel_raw_sha256, "kernel",
        )
        kernel_copy = Path(temp_dir) / "kernel.nc"
        kernel_copy.write_bytes(kernel_payload)
        try:
            kernel = anyhydro.load_radiation_kernel(kernel_copy)
        except Exception as error:
            raise AssessmentRefusalError(
                f"cannot load/verify the stored ANYhydro radiation kernel "
                f"sidecar: {error}"
            ) from error
        if kernel.content_hash() != provenance.kernel_content_hash:
            raise AssessmentRefusalError(
                "the loaded radiation kernel content hash does not reproduce "
                "the provenance identity; the actual source bytes are refused"
            )

    # The actual TD source must match the actual input matrices: the loaded
    # kernel is projected with the exact work-conjugate selector (derived
    # route: already retained) and must reproduce the stored effective
    # radiation kernel samples exactly.
    kernel_dof_keys = tuple(str(key) for key in kernel.dof_keys)
    kernel_array = np.asarray(kernel.kernel, dtype=float)
    if route == HYDRO_ROUTE_DERIVED:
        if kernel_dof_keys != tuple(provenance.dof_keys):
            raise AssessmentRefusalError(
                "the stored radiation kernel's DOF keys do not match the "
                "provenance record; the TD source and the inputs disagree"
            )
        kernel_4 = kernel_array
    else:
        if any(key not in kernel_dof_keys for key in provenance.dof_keys):
            raise AssessmentRefusalError(
                "the stored radiation kernel does not contain the "
                "provenance's retained DOF keys; the TD source and the "
                "inputs disagree"
            )
        selector_rows = np.zeros((len(kernel_dof_keys), 4), dtype=np.float64)
        selector_columns = np.zeros((len(kernel_dof_keys), 4), dtype=np.float64)
        for local, key in enumerate(provenance.dof_keys):
            position = kernel_dof_keys.index(key)
            selector_rows[position, local] = 1.0
            selector_columns[position, local] = 1.0
        kernel_4 = np.einsum(
            "ki,tkl,lj->tij", selector_rows, kernel_array, selector_columns
        )
    stored_kernel = np.asarray(inputs.radiation_kernel, dtype=float)
    if kernel_4.shape[0] < stored_kernel.shape[0]:
        raise AssessmentRefusalError(
            "the stored radiation kernel holds fewer samples than the "
            "effective inputs; the TD source and the inputs disagree"
        )
    if not np.array_equal(kernel_4[: stored_kernel.shape[0]], stored_kernel):
        raise AssessmentRefusalError(
            "the stored radiation kernel does not reproduce the stored "
            "effective inputs' radiation kernel samples; the actual TD "
            "source and the input matrices disagree"
        )

    if route == HYDRO_ROUTE_RAW:
        omegas = np.asarray(dataset["omega"].values, dtype=np.float64)
        directions = np.asarray(
            dataset["wave_direction"].values, dtype=np.float64
        )
        omega_index = _exact_sample_index(omegas, case.omega, "omega")
        direction_index = _exact_sample_index(
            directions, reference["heading"], "heading"
        )
        influenced = [str(v) for v in dataset["influenced_dof"].values.tolist()]
        radiating = [str(v) for v in dataset["radiating_dof"].values.tolist()]
        dof_keys = tuple(provenance.dof_keys)
        row_index = {key: position for position, key in enumerate(influenced)}
        column_index = {key: position for position, key in enumerate(radiating)}
        if any(key not in row_index or key not in column_index for key in dof_keys):
            raise AssessmentRefusalError(
                "the stored dataset does not contain the provenance's "
                "retained DOF keys; the source and the inputs disagree"
            )
        selector_rows = np.zeros((len(influenced), 4), dtype=np.float64)
        selector_columns = np.zeros((len(radiating), 4), dtype=np.float64)
        for local, key in enumerate(dof_keys):
            selector_rows[row_index[key], local] = 1.0
            selector_columns[column_index[key], local] = 1.0
        a_omega = (
            selector_rows.T
            @ np.asarray(dataset["added_mass"].values[omega_index], dtype=float)
            @ selector_columns
        )
        b_omega = (
            selector_rows.T
            @ np.asarray(
                dataset["radiation_damping"].values[omega_index], dtype=float
            )
            @ selector_columns
        )
        f_hat_source = selector_rows.T @ np.asarray(
            dataset["excitation_force"].values[omega_index, direction_index, :],
            dtype=np.complex128,
        )
        ainf_projected = (
            selector_rows.T
            @ np.asarray(dataset["added_mass_infinite"].values, dtype=float)
            @ selector_columns
        )
        if not np.array_equal(
            ainf_projected, np.asarray(inputs.added_mass_infinite, dtype=float)
        ):
            raise AssessmentRefusalError(
                "the dataset's projected added_mass_infinite does not "
                "reproduce the stored effective inputs; the source and the "
                "inputs disagree"
            )
        sensitivity = None
        sensitivity_reason = (
            "raw route: the strict raw admission already admitted the exact "
            "stored raw sample and no processing is applied, so the "
            "raw-versus-processed sensitivity is not applicable"
        )
    else:
        if tuple(artifact.settings.retained_dofs) != tuple(provenance.dof_keys):
            raise AssessmentRefusalError(
                "the derived artifact's retained DOF order does not match the "
                "provenance record; the source and the inputs disagree"
            )
        if artifact.content_hash() != bundle["derived_content_hash"]:
            raise AssessmentRefusalError(
                "the loaded derived artifact content hash does not reproduce "
                "the provenance derived bundle identity"
            )
        omega_index = _exact_sample_index(
            artifact.omegas, case.omega, "omega"
        )
        direction_index = _exact_sample_index(
            artifact.wave_directions, reference["heading"], "heading"
        )
        # The TD consumed the processed arrays, so the FD reference uses the
        # artifact's processed arrays; the raw arrays are never silently
        # substituted and no A is invented.
        a_omega = np.asarray(
            artifact.derived_added_mass[omega_index], dtype=float
        )
        b_omega = np.asarray(artifact.derived_damping[omega_index], dtype=float)
        f_hat_source = np.asarray(
            artifact.excitation_force[omega_index, direction_index, :],
            dtype=np.complex128,
        )
        expected_ainf = np.asarray(artifact.derived_added_mass_infinite, dtype=float)
        stored_kernel = _load_stored_kernel(provenance, project_root)
        if getattr(stored_kernel.settings, "a_inf_policy", "computed") == "band_consistent":
            try:
                expected_ainf = np.asarray(
                    _import_anyhydro().verify_band_consistent_added_mass_infinite(
                        stored_kernel, artifact.derived_added_mass_infinite,
                        artifact.omegas, artifact.derived_added_mass,
                        calibration_source=artifact), dtype=float)
            except Exception as error:
                raise AssessmentRefusalError(
                    f"the stored kernel's band-consistent added_mass_infinite is not "
                    f"proved: {error}") from error
        if not np.array_equal(
            expected_ainf,
            np.asarray(inputs.added_mass_infinite, dtype=float),
        ):
            raise AssessmentRefusalError(
                "the artifact's derived added_mass_infinite does not "
                "reproduce the stored effective inputs; the source and the "
                "inputs disagree"
            )
        if int(omega_index) != int(wave["omega_index"]) or int(
            direction_index
        ) != int(wave["direction_index"]):
            raise AssessmentRefusalError(
                "the exact stored sample indices do not match the "
                "provenance's recorded wave selection indices"
            )
        if str(artifact.settings.correction_policy) in (
                "reciprocal_average", "reciprocal_average_far_field_weak_modes"):
            f_hat_case = _case_source_phasor(case, f_hat_source)
            sensitivity = hydro_reciprocal_sensitivity(
                artifact, inputs, case.omega, omega_index, f_hat_case,
                case.criteria,
            )
            sensitivity_reason = None
        else:
            sensitivity = None
            sensitivity_reason = (
                f"correction policy {artifact.settings.correction_policy!r} "
                "applies no reciprocal average; the raw-versus-processed "
                "sensitivity is not applicable"
            )

    f_hat_case = _case_source_phasor(case, f_hat_source)
    source = {
        "kind": REFERENCE_HYDRO_SPECTRUM,
        "route": route,
        "dataset_content_hash": provenance.dataset_content_hash,
        "omega": float(case.omega),
        "heading": float(reference["heading"]),
        "wave_amplitude": float(reference["wave_amplitude"]),
        "wave_phase": float(reference["wave_phase"]),
        "omega_index": int(omega_index),
        "direction_index": int(direction_index),
    }
    if route == HYDRO_ROUTE_DERIVED:
        source["derived_content_hash"] = bundle["derived_content_hash"]
        source["operators_raw_sha256"] = bundle["operators_raw_sha256"]
    return {
        "a_omega": a_omega,
        "b_omega": b_omega,
        "f_hat": f_hat_case,
        "source": source,
        "sensitivity": sensitivity,
        "sensitivity_reason": sensitivity_reason,
    }


def _case_source_phasor(case: AssessmentCase, f_hat_source: np.ndarray):
    """The case phasor must come from the actual F source (or match it)."""

    amplitude = float(case.reference["wave_amplitude"])
    phase = float(case.reference["wave_phase"])
    expected = amplitude * np.exp(1j * phase) * np.asarray(
        f_hat_source, dtype=np.complex128
    )
    declared = _phasor_array(case.force_phasors)
    deviation = np.abs(declared - expected)
    scale = np.maximum(np.abs(expected), np.abs(declared))
    if not np.all(
        deviation
        <= HYDRO_PHASOR_IDENTITY_RELATIVE * scale
        + HYDRO_PHASOR_IDENTITY_ABSOLUTE
    ):
        raise AssessmentRefusalError(
            "the case force phasors do not match the actual stored excitation "
            "source sample (amplitude * exp(+i phase) * F_source); a "
            "substituted phasor is refused"
        )
    return expected


def _load_stored_kernel(provenance, project_root):
    """Load the stored kernel sidecar from its actual, hash-checked bytes."""
    anyhydro = _import_anyhydro()
    if provenance.kernel_sidecar is None or (
        not str(provenance.kernel_sidecar)
    ):
        raise AssessmentRefusalError(
            "the hydro provenance records no radiation kernel sidecar; the "
            "actual TD source cannot be re-proved and the assessment is "
            "refused"
        )
    kernel_payload = _read_project_sidecar(
        project_root, provenance.kernel_sidecar,
        provenance.kernel_raw_sha256, "kernel",
    )
    with tempfile.TemporaryDirectory(prefix="anymarine-assessment-") as temp_dir:
        kernel_copy = Path(temp_dir) / "kernel.nc"
        kernel_copy.write_bytes(kernel_payload)
        try:
            kernel = anyhydro.load_radiation_kernel(kernel_copy)
        except Exception as error:
            raise AssessmentRefusalError(
                f"cannot load/verify the stored ANYhydro radiation kernel "
                f"sidecar: {error}"
            ) from error
        if kernel.content_hash() != provenance.kernel_content_hash:
            raise AssessmentRefusalError(
                "the loaded radiation kernel content hash does not reproduce "
                "the provenance identity; the actual source bytes are refused"
            )
    return kernel


def _prove_stored_kernel(inputs: LinearDevelopmentInputs,
                         provenance: HydroBridgeProvenance,
                         project_root, route: str):
    """Re-prove the stored radiation kernel sidecar against the inputs.

    The actual kernel sidecar bytes are read from the project root,
    checked against the public provenance raw sha256, loaded through the
    public ANYhydro loader on a private byte-identical copy and
    content-hash re-checked; the retained 4-DOF kernel must reproduce the
    stored effective inputs' radiation kernel samples exactly.
    """

    kernel = _load_stored_kernel(provenance, project_root)
    kernel_dof_keys = tuple(str(key) for key in kernel.dof_keys)
    kernel_array = np.asarray(kernel.kernel, dtype=float)
    if route == HYDRO_ROUTE_DERIVED:
        if kernel_dof_keys != tuple(provenance.dof_keys):
            raise AssessmentRefusalError(
                "the stored radiation kernel's DOF keys do not match the "
                "provenance record; the TD source and the inputs disagree"
            )
        kernel_4 = kernel_array
    else:
        if any(key not in kernel_dof_keys for key in provenance.dof_keys):
            raise AssessmentRefusalError(
                "the stored radiation kernel does not contain the "
                "provenance's retained DOF keys; the TD source and the "
                "inputs disagree"
            )
        selector_rows = np.zeros((len(kernel_dof_keys), 4), dtype=np.float64)
        selector_columns = np.zeros((len(kernel_dof_keys), 4), dtype=np.float64)
        for local, key in enumerate(provenance.dof_keys):
            position = kernel_dof_keys.index(key)
            selector_rows[position, local] = 1.0
            selector_columns[position, local] = 1.0
        kernel_4 = np.einsum(
            "ki,tkl,lj->tij", selector_rows, kernel_array, selector_columns
        )
    stored_kernel = np.asarray(inputs.radiation_kernel, dtype=float)
    if kernel_4.shape[0] < stored_kernel.shape[0]:
        raise AssessmentRefusalError(
            "the stored radiation kernel holds fewer samples than the "
            "effective inputs; the TD source and the inputs disagree"
        )
    if not np.array_equal(kernel_4[: stored_kernel.shape[0]], stored_kernel):
        raise AssessmentRefusalError(
            "the stored radiation kernel does not reproduce the stored "
            "effective inputs' radiation kernel samples; the actual TD "
            "source and the input matrices disagree"
        )
    return kernel


def _verify_raw_source_recompilation(inputs: LinearDevelopmentInputs,
                                     provenance: HydroBridgeProvenance,
                                     project, project_root) -> None:
    """Rebuild the raw-route effective inputs read-only from the sources.

    The actual dataset/kernel sidecar bytes are read from the project root
    and checked against the public provenance raw sha256 values; the
    PUBLIC PURE ``anymarine.hydro.import_hydro_inputs`` is then re-run on
    private temporary byte-identical copies under the original declared
    controls (n_steps/criteria/t0/surge-link from the provenance record;
    q0/v0 and the execution limits are the caller's mechanical controls,
    preserved from the stored inputs; no wave selection), and the full
    resulting input hash must reproduce the stored inputs' hash exactly.
    The project is never mutated and the writing service import route is
    never used; no copied physics or private sibling helper is involved.
    """

    from .hydro import HydroImportError, import_hydro_inputs

    provenance_data = provenance.to_dict()
    options = {
        "n_steps": int(provenance_data["n_steps"]),
        "criteria": provenance_data["criteria"],
        "t0": float(provenance_data["t0"]),
        "surge_link_stiffness": float(provenance_data["surge_link_stiffness"]),
    }
    if inputs.q0 is not None:
        options["q0"] = [float(value) for value in inputs.q0]
    if inputs.v0 is not None:
        options["v0"] = [float(value) for value in inputs.v0]
    if inputs.execution_limits is not None:
        options["execution_limits"] = inputs.execution_limits.to_dict()
    dataset_payload = _read_project_sidecar(
        project_root, provenance.dataset_sidecar,
        provenance.dataset_raw_sha256, "dataset",
    )
    kernel_payload = _read_project_sidecar(
        project_root, provenance.kernel_sidecar,
        provenance.kernel_raw_sha256, "kernel",
    )
    with tempfile.TemporaryDirectory(
        prefix="anymarine-hydro-source-proof-"
    ) as temp_dir:
        dataset_copy = Path(temp_dir) / "dataset.nc"
        kernel_copy = Path(temp_dir) / "kernel.nc"
        dataset_copy.write_bytes(dataset_payload)
        kernel_copy.write_bytes(kernel_payload)
        try:
            rebuilt, _ = import_hydro_inputs(
                project, dataset_copy, kernel_copy, options
            )
        except HydroImportError as error:
            raise AssessmentRefusalError(
                f"the stored raw hydro sources do not re-verify through the "
                f"public import route: {error}"
            ) from error
        if linear_input_hash(rebuilt) != linear_input_hash(inputs):
            raise AssessmentRefusalError(
                "the effective inputs recompiled from the authoritative "
                "dataset/kernel sidecars do not reproduce the stored linear "
                "input hash; effective-operator tampering is not cured by "
                "re-hashing the provenance bookkeeping and the case is refused"
            )


def load_hydro_source_proof(case: AssessmentCase,
                            inputs: LinearDevelopmentInputs,
                            provenance, project,
                            project_root) -> dict:
    """Prove the ACTUAL hydro source of a source-only free-decay case.

    The case's closed-world descriptor is matched against the project's
    public hydro provenance, the import must be a free-decay import
    (``wave`` is None) with exactly zero stored external forcing, the
    stored kernel sidecar is re-proved against the effective inputs, and
    the full source recompilation is re-run: the derived route through the
    existing public service recompile proof (already re-run by the
    project-context verifier), the raw route through the public PURE
    ``import_hydro_inputs`` on private byte-identical sidecar copies
    under the original declared controls. Hash strings or caller booleans
    alone are never accepted as proof.
    """

    if not isinstance(provenance, HydroBridgeProvenance):
        raise AssessmentRefusalError(
            "a hydro_source reference requires the project's public "
            "HydroBridgeProvenance record; no provenance, no assessment"
        )
    if project_root is None:
        raise AssessmentRefusalError(
            "a hydro_source reference requires the project root holding "
            "the authoritative hydro sidecars; a generic case without the "
            "actual stored sources is refused"
        )
    if provenance.linear_input_hash != linear_input_hash(inputs):
        raise AssessmentRefusalError(
            "the hydro provenance does not describe the stored linear inputs "
            "(input hash mismatch); the hydro intent is stale and the "
            "assessment is refused"
        )
    reference = case.reference
    route = reference["route"]
    bundle = provenance.derived_bundle
    if route == HYDRO_ROUTE_DERIVED and bundle is None:
        raise AssessmentRefusalError(
            "the case declares the derived-operator route but the project "
            "carries no derived bundle; stripped derived intent is refused"
        )
    if route == HYDRO_ROUTE_RAW and bundle is not None:
        raise AssessmentRefusalError(
            "the case declares the raw route but the project carries a "
            "derived-operator bundle; the case route must match the actual "
            "import route"
        )
    if reference["dataset_content_hash"] != provenance.dataset_content_hash:
        raise AssessmentRefusalError(
            "the case's bound dataset content hash does not match the "
            "project hydro provenance; the case does not describe this source"
        )
    if route == HYDRO_ROUTE_DERIVED:
        if reference["derived_content_hash"] != bundle["derived_content_hash"]:
            raise AssessmentRefusalError(
                "the case's bound derived-operator content hash does not "
                "match the project hydro provenance derived bundle"
            )
        if reference["operators_raw_sha256"] != bundle["operators_raw_sha256"]:
            raise AssessmentRefusalError(
                "the case's bound operators raw sha256 does not match the "
                "project hydro provenance derived bundle"
            )
    if provenance.wave is not None:
        raise AssessmentRefusalError(
            "the hydro import recorded a regular-wave selection; a "
            "hydro_source free-decay case cannot be bound to this project"
        )
    stored_forces = np.asarray(inputs.forces, dtype=float)
    if not np.all(stored_forces == 0.0):
        raise AssessmentRefusalError(
            "the stored inputs carry nonzero external forcing; a "
            "hydro_source free-decay case requires zero forcing"
        )
    _prove_stored_kernel(inputs, provenance, project_root, route)
    if route == HYDRO_ROUTE_RAW:
        # Derived route: the existing public service recompile proof has
        # already re-run inside the project-context verifier. Raw route:
        # the read-only public re-import proof runs here.
        _verify_raw_source_recompilation(
            inputs, provenance, project, project_root
        )
    source = {
        "kind": REFERENCE_HYDRO_SOURCE,
        "route": route,
        "dataset_content_hash": provenance.dataset_content_hash,
        "kernel_content_hash": provenance.kernel_content_hash,
        "wave": None,
    }
    if route == HYDRO_ROUTE_DERIVED:
        source["derived_content_hash"] = bundle["derived_content_hash"]
        source["operators_raw_sha256"] = bundle["operators_raw_sha256"]
    return source


# ------------------------------------------------------------- assessment


def _normalized(name: str, unit: str, deviation: np.ndarray, scale: np.ndarray,
                relative: float, floor: float, absolute: float,
                detail: str = "") -> dict:
    worst = float(np.max(deviation)) if deviation.size else 0.0
    passed = bool(
        np.all(deviation <= relative * np.maximum(scale, floor) + absolute)
    )
    return {
        "name": name,
        "unit": unit,
        "value": worst,
        "limit": relative,
        "passed": passed,
        "detail": detail,
    }


def _plain(name: str, unit: str, deviation: np.ndarray, scale: np.ndarray,
           relative: float, absolute: float, detail: str = "") -> dict:
    worst = float(np.max(deviation)) if deviation.size else 0.0
    passed = bool(np.all(deviation <= relative * scale + absolute))
    return {
        "name": name,
        "unit": unit,
        "value": worst,
        "limit": relative,
        "passed": passed,
        "detail": detail,
    }


def _build_report(case: AssessmentCase, result: ResultRecord, checks: list,
                  arrays: dict) -> AssessmentReport:
    failed = [check["name"] for check in checks if not check["passed"]]
    data = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "report_id": f"report-{uuid.uuid4().hex}",
        "case_hash": case.case_hash,
        "case_id": case.case_id,
        "mode": case.mode,
        "input_hash": case.input_hash,
        "result_id": result.result_id,
        "verdict": "pass" if not failed else "nogo",
        "checks": checks,
        "arrays": arrays,
        "failed": failed,
        "scope": {
            "development_only": True,
            "note": LINEAR_ASSESSMENT_SCOPE_NOTE,
        },
        "created_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    }
    return AssessmentReport(data)


def _assess_regular_wave(case, inputs, channels, spectrum, checks, arrays):
    criteria = case.criteria
    times = np.asarray(inputs.times, dtype=float)
    omega = case.omega
    f_hat = _phasor_array(case.force_phasors)
    expected_forces = (
        f_hat[None, :] * np.exp(-1j * omega * times)[:, None]
    ).real
    detail = "stored input forces vs Re(F_hat exp(-i omega t)) at absolute times"
    if "prehistory" in case.reference:
        # The history force of the declared periodic prehistory, recomputed from the
        # stored kernel (the same lags the solver integrates).
        history = _history_force(case, inputs)
        expected_forces = expected_forces - history
        detail += " minus the recomputed periodic-prehistory history force"
        arrays["prehistory"] = {
            "kind": case.reference["prehistory"]["kind"],
            "max_abs_history_force_n": float(np.max(np.abs(history))),
        }
    stored_forces = np.asarray(inputs.forces, dtype=float)
    checks.append(
        _plain(
            "input_force_match",
            "N",
            np.abs(stored_forces - expected_forces),
            np.abs(expected_forces),
            criteria["force_match_relative"],
            criteria["force_match_absolute_n"],
            detail,
        )
    )
    qhat_fd, a_omega, b_omega = _fd_reference(inputs, case, spectrum)
    q = channels["q"]
    fit_results = []
    for label, start, end in (
        ("first", case.windows["start"], case.windows["mid"]),
        ("second", case.windows["mid"], case.windows["end"]),
    ):
        qhat, residuals, condition, rank, columns, indices, drift_coefficient = (
            _fit_window(times, q, case, start, end)
        )
        fit_results.append(
            {
                "label": label,
                "qhat": [
                    [float(value.real), float(value.imag)] for value in qhat
                ],
                "residual_rms": [
                    float(np.sqrt(np.mean(residuals[:, dof] ** 2)))
                    for dof in range(4)
                ],
                "condition": condition,
                "rank": rank,
                "samples": int(len(indices)),
                "shared_drift_coefficient": drift_coefficient,
            }
        )
    arrays["fits"] = fit_results
    arrays["fd_reference"] = {
        "qhat": [
            [float(value.real), float(value.imag)] for value in qhat_fd
        ],
        "a_omega": [[float(x) for x in row] for row in a_omega],
        "b_omega": [[float(x) for x in row] for row in b_omega],
    }
    qhat_first = np.asarray(
        [complex(entry[0], entry[1]) for entry in fit_results[0]["qhat"]],
        dtype=complex,
    )
    qhat_second = np.asarray(
        [complex(entry[0], entry[1]) for entry in fit_results[1]["qhat"]],
        dtype=complex,
    )
    window_scale = np.maximum(np.abs(qhat_first), np.abs(qhat_second))
    checks.append(
        _normalized(
            "steadiness",
            "m",
            np.abs(qhat_first - qhat_second),
            window_scale,
            criteria["steady_relative"],
            criteria["steady_floor_m"],
            criteria["steady_absolute_m"],
            "complex phasor change between the two predeclared windows",
        )
    )
    fd_amplitude = np.abs(qhat_fd)
    for fit in fit_results:
        fit_qhat = _phasor_array(fit["qhat"])
        rms = np.asarray(fit["residual_rms"], dtype=float)
        rms_limit = criteria["fit_rms_absolute_m"] + criteria[
            "fit_rms_relative"
        ] * np.maximum(fd_amplitude, criteria["fit_rms_floor_m"])
        checks.append(
            {
                "name": f"fit_rms_{fit['label']}",
                "unit": "m",
                "value": float(np.max(rms)),
                "limit": float(np.max(rms_limit)),
                "passed": bool(np.all(rms <= rms_limit)),
                "detail": "per-DOF window fit RMS vs FD amplitude scale",
            }
        )
        amplitude_error = np.abs(np.abs(fit_qhat) - fd_amplitude)
        nearzero = fd_amplitude <= criteria["nearzero_amplitude_m"]
        amplitude_pass = bool(
            np.all(
                np.where(
                    nearzero,
                    amplitude_error <= criteria["nearzero_amplitude_absolute_m"],
                    amplitude_error
                    <= criteria["rao_amplitude_relative"] * fd_amplitude,
                )
            )
        )
        checks.append(
            {
                "name": f"rao_amplitude_{fit['label']}",
                "unit": "m",
                "value": float(np.max(amplitude_error)),
                "limit": float(criteria["rao_amplitude_relative"]),
                "passed": amplitude_pass,
                "detail": (
                    "near-zero reference DOFs use the absolute amplitude bound"
                ),
            }
        )
        phase_measured = ~nearzero
        if np.any(phase_measured):
            angle = np.angle(fit_qhat[phase_measured]) - np.angle(
                qhat_fd[phase_measured]
            )
            phase_error = np.abs(np.degrees(np.angle(np.exp(1j * angle))))
            phase_pass = bool(
                np.all(phase_error <= criteria["rao_phase_deg"])
            )
        else:
            phase_error = np.asarray([])
            phase_pass = True
        checks.append(
            {
                "name": f"rao_phase_{fit['label']}",
                "unit": "deg",
                "value": float(np.max(phase_error)) if phase_error.size else None,
                "limit": float(criteria["rao_phase_deg"]),
                "passed": phase_pass,
                "detail": (
                    "phase is NOT measured for near-zero reference DOFs "
                    "(<= nearzero_amplitude_m); no fabricated phase pass"
                    if not np.all(phase_measured)
                    else "phase error vs the FD reference phasor"
                ),
            }
        )
    excursion = np.max(np.abs(q), axis=0)
    arrays["max_abs_q"] = [float(value) for value in excursion]
    checks.append(
        {
            "name": "small_excursion",
            "unit": "m",
            "value": float(np.max(excursion)),
            "limit": float(criteria["small_excursion_length_scale_m"]),
            "passed": bool(
                np.all(excursion <= criteria["small_excursion_length_scale_m"])
            ),
            "detail": (
                "actual max |q| per DOF (offsets/drift included, no demeaning) "
                "vs the declared caller length scale"
            ),
        }
    )
    if spectrum is not None:
        arrays["reference_source"] = spectrum["source"]
        sensitivity = spectrum.get("sensitivity")
        if sensitivity is None:
            reason = spectrum.get("sensitivity_reason") or ""
            checks.append(
                {
                    "name": "hydro_reciprocal_sensitivity",
                    "unit": None,
                    "value": None,
                    "limit": None,
                    "passed": True,
                    "detail": f"not applicable: {reason}",
                }
            )
        else:
            arrays["hydro_sensitivity"] = sensitivity
            for quantity, key in (
                ("impedance", "hydro_impedance_sensitivity"),
                ("response", "hydro_response_sensitivity"),
            ):
                metrics = sensitivity[quantity]
                full_block = metrics.get("form") == "full_block"
                checks.append(
                    {
                        "name": key,
                        "unit": "relative (Frobenius)" if full_block else metrics["unit"],
                        "value": metrics["value"] if full_block else metrics["max_deviation"],
                        "limit": metrics["relative_limit"],
                        "passed": metrics["passed"],
                        "detail": (
                            (f"raw vs processed {quantity}, full block ||dZ||_F/||Z_raw||_F; max "
                             f"coefficient deviation {metrics['max_coefficient_deviation']:.6g} reported")
                            if full_block else
                            f"raw vs processed {quantity}, max scale "
                            f"{metrics['max_scale']:.6g}, floor "
                            f"{metrics['floor']:.6g}, abs {metrics['absolute']:.6g}"
                        ),
                    }
                )
        arrays["kernel_self_consistency"] = kernel_spectrum_self_consistency(
            inputs, omega, a_omega, b_omega
        )
    else:
        arrays["reference_source"] = {
            "kind": case.reference["kind"],
            "closed_form": (
                "A(omega)=Ainf-R/(alpha^2+omega^2), "
                "B(omega)=alpha*R/(alpha^2+omega^2)"
            ),
            "decay": case.reference.get("decay"),
        }
    return qhat_fd


def _assess_balance(case, inputs, result, channels, checks, arrays):
    criteria = case.criteria
    mass_dry = np.asarray(inputs.mass_dry, dtype=float)
    ainf = np.asarray(inputs.added_mass_infinite, dtype=float)
    damping = np.asarray(inputs.damping, dtype=float)
    stiffness = np.asarray(inputs.stiffness, dtype=float)
    q = channels["q"]
    v = channels["v"]
    a = channels["a"]
    external = channels["force_external"]
    dry = a @ mass_dry.T
    added = a @ ainf.T
    damp = v @ damping.T
    stiff = q @ stiffness.T
    memory = channels["force_memory"]
    resisting = dry + added + memory + damp + stiff
    recomputed_residual = external - resisting
    scale = np.maximum(np.abs(external), np.abs(resisting))
    checks.append(
        _normalized(
            "force_balance",
            "N",
            np.abs(recomputed_residual),
            scale,
            criteria["balance_relative"],
            criteria["balance_absolute_n"],
            criteria["balance_absolute_n"],
            "external - (dry + added + memory + damping + stiffness), "
            "recomputed from stored q/v/a and the effective matrices",
        )
    )
    checks.append(
        _normalized(
            "stored_residual_channel",
            "N",
            np.abs(recomputed_residual - channels["force_residual"]),
            scale,
            criteria["channel_relative"],
            criteria["channel_absolute_n"],
            criteria["channel_absolute_n"],
            "recomputed balance vs the stored force_residual channel; a stored "
            "zero residual alone is not evidence",
        )
    )
    stored_forces = np.asarray(inputs.forces, dtype=float)
    checks.append(
        _normalized(
            "external_channel_vs_inputs",
            "N",
            np.abs(external - stored_forces),
            np.maximum(np.abs(external), np.abs(stored_forces)),
            criteria["channel_relative"],
            criteria["channel_absolute_n"],
            criteria["channel_absolute_n"],
            "stored external channel vs the stored input forces",
        )
    )
    oracle = _memory_oracle(inputs, v)
    checks.append(
        _normalized(
            "memory_oracle",
            "N",
            np.abs(memory - oracle),
            np.maximum(np.abs(memory), np.abs(oracle)),
            criteria["memory_oracle_relative"],
            criteria["memory_oracle_absolute_n"],
            criteria["memory_oracle_absolute_n"],
            "stored memory channel vs the independent discrete causal "
            "trapezoid convolution oracle (verification only)",
        )
    )
    dt = inputs.dt
    q_pred = q[:-1] + dt * v[:-1] + dt * dt / 4.0 * (a[:-1] + a[1:])
    v_pred = v[:-1] + dt / 2.0 * (a[:-1] + a[1:])
    checks.append(
        _normalized(
            "newmark_q_update",
            "m",
            np.abs(q[1:] - q_pred),
            np.maximum(np.abs(q[1:]), np.abs(q[:-1])),
            criteria["newmark_relative"],
            criteria["newmark_absolute_m"],
            criteria["newmark_absolute_m"],
            "q[n] = q[n-1] + dt v[n-1] + dt^2/4 (a[n-1] + a[n])",
        )
    )
    checks.append(
        _normalized(
            "newmark_v_update",
            "m/s",
            np.abs(v[1:] - v_pred),
            np.maximum(np.abs(v[1:]), np.abs(v[:-1])),
            criteria["newmark_relative"],
            criteria["newmark_absolute_ms"],
            criteria["newmark_absolute_ms"],
            "v[n] = v[n-1] + dt/2 (a[n-1] + a[n])",
        )
    )
    q0 = np.asarray(inputs.q0 if inputs.q0 is not None else np.zeros(4), dtype=float)
    v0 = np.asarray(inputs.v0 if inputs.v0 is not None else np.zeros(4), dtype=float)
    checks.append(
        _normalized(
            "initial_state",
            "m, m/s",
            np.concatenate(
                [
                    np.abs(q[0] - q0),
                    np.abs(v[0] - v0),
                ]
            ),
            np.concatenate(
                [
                    np.maximum(np.abs(q[0]), np.abs(q0)),
                    np.maximum(np.abs(v[0]), np.abs(v0)),
                ]
            ),
            criteria["newmark_relative"],
            min(criteria["newmark_absolute_m"], criteria["newmark_absolute_ms"]),
            min(criteria["newmark_absolute_m"], criteria["newmark_absolute_ms"]),
            "stored q[0]/v[0] vs the declared initial state",
        )
    )
    arrays["balance_max_residual_n"] = float(np.max(np.abs(recomputed_residual)))


def _assess_free_decay(case, inputs, channels, checks, arrays):
    criteria = case.criteria
    stored_forces = np.asarray(inputs.forces, dtype=float)
    checks.append(
        {
            "name": "free_decay_zero_forcing",
            "unit": "N",
            "value": float(np.max(np.abs(stored_forces))),
            "limit": float(criteria["free_decay_external_absolute_n"]),
            "passed": bool(
                np.all(np.abs(stored_forces) <= criteria["free_decay_external_absolute_n"])
            ),
            "detail": "free decay requires zero external input forcing",
        }
    )
    mass_dry = np.asarray(inputs.mass_dry, dtype=float)
    ainf = np.asarray(inputs.added_mass_infinite, dtype=float)
    stiffness = np.asarray(inputs.stiffness, dtype=float)
    q = channels["q"]
    v = channels["v"]
    external = channels["force_external"]
    damp = channels["force_damping"]
    memory = channels["force_memory"]
    effective_mass = mass_dry + ainf
    kinetic = 0.5 * np.einsum("ni,ij,nj->n", v, effective_mass, v)
    potential = 0.5 * np.einsum("ni,ij,nj->n", q, stiffness, q)
    energy = kinetic + potential
    delta_q = inputs.dt * 0.5 * (v[1:] + v[:-1])

    def cumulative(channel):
        # Trapezoid work: the endpoint-averaged resisting force dotted with
        # the displacement increment of the SAME step, one scalar per step.
        averaged = 0.5 * (channel[1:] + channel[:-1])
        work = np.sum(averaged * delta_q, axis=1)
        return np.concatenate(([0.0], np.cumsum(work)))

    work_external = cumulative(external)
    work_damping = cumulative(damp)
    work_memory = cumulative(memory)
    energy_scale = max(
        float(energy[0]),
        float(criteria["energy_scale_j"]),
        float(criteria["nearzero_energy_floor_j"]),
    )
    tolerance = (
        criteria["energy_closure_absolute_j"]
        + criteria["energy_closure_relative"] * energy_scale
    )
    # Closure is checked over the WHOLE trajectory, not only at the final
    # time: a mid-trajectory violation that cancels at the end is a NoGo.
    closure_trajectory = (
        energy
        - energy[0]
        - work_external
        + work_damping
        + work_memory
    )
    closure = float(np.max(np.abs(closure_trajectory)))
    checks.append(
        {
            "name": "energy_closure",
            "unit": "J",
            "value": closure,
            "limit": float(tolerance),
            "passed": bool(closure <= tolerance),
            "detail": (
                "discrete closure |E_n - E_0 - W_ext + W_damp + W_mem| over "
                "the whole trajectory with trapezoid work accumulation"
            ),
        }
    )
    # force_memory is the POSITIVE resisting memory force, so cumulative
    # W_memory integrates resisting work. Zero-history passivity requires
    # the MINIMUM cumulative W_memory >= -tolerance: positive net work and
    # negative increments (fluid returning stored energy) are both admitted;
    # an active kernel that injects energy is a NoGo.
    worst_return = float(-np.min(work_memory))
    checks.append(
        {
            "name": "radiation_passivity",
            "unit": "J",
            "value": worst_return,
            "limit": float(tolerance),
            "passed": bool(worst_return <= tolerance),
            "detail": (
                "zero-history passivity: min cumulative memory work >= "
                "-tolerance; positive net work and negative increments "
                "(returning stored fluid energy) are NOT a failure"
            ),
        }
    )
    checks.append(
        {
            "name": "mechanical_peak_growth",
            "unit": "J",
            "value": float(np.max(energy) - energy[0]),
            "limit": float(tolerance),
            "passed": bool(np.max(energy) - energy[0] <= tolerance),
            "detail": "mechanical energy peak growth bound vs E0",
        }
    )
    arrays["energy"] = {
        "mechanical": [float(value) for value in energy],
        "work_external": [float(value) for value in work_external],
        "work_damping": [float(value) for value in work_damping],
        "work_memory": [float(value) for value in work_memory],
        "closure_trajectory_max_j": float(closure),
        "tolerance_j": float(tolerance),
        "min_cumulative_work_memory": float(np.min(work_memory)),
        "max_cumulative_work_memory": float(np.max(work_memory)),
        "min_work_memory_increment": float(
            np.min(np.diff(work_memory)) if len(work_memory) > 1 else 0.0
        ),
    }
    if case.reference["kind"] == REFERENCE_HYDRO_SOURCE:
        # A hydro-source free decay with a near-zero initial energy is
        # explicitly NOTMEASURED: a trivial zero-state "decay" is a NoGo,
        # never a PASS. This gate is new and applies only to the new
        # hydro_source reference; legacy analytic free-decay reports are
        # unchanged and stay reproducible without new fields.
        floor = float(criteria["nearzero_energy_floor_j"])
        initial = float(energy[0])
        checks.append(
            {
                "name": "initial_energy_measured",
                "unit": "J",
                "value": initial,
                "limit": floor,
                "passed": bool(initial > floor),
                "detail": (
                    "hydro-source free decay: E0 <= the registered near-zero "
                    "floor is NOTMEASURED and a NoGo, never a trivial "
                    "zero-state decay PASS"
                ),
            }
        )


def assess_result(case: AssessmentCase, result: ResultRecord,
                  inputs: LinearDevelopmentInputs, *, project=None,
                  provenance=None, project_root=None) -> AssessmentReport:
    """Assess one stored linear result under its registered case.

    Eligibility is proved first (exact kind, exact input hash binding, the
    public ``verify_linear_result`` completeness proof); a wrong-kind,
    stale, truncated or foreign record is a typed refusal and produces no
    report. A hydro_spectrum reference additionally requires the actual
    ``ProjectModel`` context and root: the assessed inputs must be the
    project's stored inputs, the public binding verifier and the public
    ``ApplicationService.verify_hydro_provenance`` re-run here, and the
    actual stored sources are re-proved. Physics failures are NOT
    refusals: they produce a NoGo report with every raw metric preserved.
    """

    if not isinstance(case, AssessmentCase):
        raise AssessmentCaseError("case must be an AssessmentCase")
    # Source-intent admission runs BEFORE any eligibility proof: a loaded
    # or directly constructed case cannot bypass the preparation guard by
    # declaring a non-hydro reference kind for hydro-sourced inputs.
    _admit_source_reference(
        inputs, case.mode, case.reference["kind"], AssessmentRefusalError
    )
    if result.kind != RESULT_KIND_LINEAR:
        raise AssessmentRefusalError(
            f"result kind {result.kind!r} is not {RESULT_KIND_LINEAR!r}; only "
            "exact bound linear development results are eligible"
        )
    if result.input_hash != case.input_hash:
        raise AssessmentRefusalError(
            "the result's input hash does not match the case's bound input "
            "hash; a stale or foreign result is refused"
        )
    if linear_input_hash(inputs) != case.input_hash:
        raise AssessmentRefusalError(
            "the stored linear inputs do not reproduce the case's bound "
            "input hash; the case does not describe these inputs"
        )
    if case.result_id is not None and result.result_id != case.result_id:
        raise AssessmentRefusalError(
            f"the case selects result {case.result_id!r} but the provided "
            f"result is {result.result_id!r}"
        )
    try:
        verify_linear_result(result, inputs)
    except Exception as error:
        raise AssessmentRefusalError(
            f"the result does not completely represent a run of the bound "
            f"inputs: {error}"
        ) from error
    spectrum = None
    source_proof = None
    if case.reference["kind"] == REFERENCE_HYDRO_SPECTRUM:
        provenance = _verify_hydro_project_context(
            inputs, project, provenance, project_root
        )
        spectrum = load_hydro_spectrum(case, inputs, provenance, project_root)
    elif case.reference["kind"] == REFERENCE_HYDRO_SOURCE:
        provenance = _verify_hydro_project_context(
            inputs, project, provenance, project_root
        )
        source_proof = load_hydro_source_proof(
            case, inputs, provenance, project, project_root
        )
    channels = _channels(result, inputs)
    checks: list = []
    arrays: dict = {}
    if case.mode == MODE_REGULAR_WAVE:
        _assess_regular_wave(case, inputs, channels, spectrum, checks, arrays)
    _assess_balance(case, inputs, result, channels, checks, arrays)
    if case.mode == MODE_FREE_DECAY:
        _assess_free_decay(case, inputs, channels, checks, arrays)
    if source_proof is not None:
        arrays["reference_source"] = source_proof
    return _build_report(case, result, checks, arrays)


def _same_physical_model(inputs_coarse, inputs_fine, ratio: int, history=None) -> None:
    for name in ("mass_dry", "added_mass_infinite", "damping", "stiffness"):
        coarse = np.asarray(getattr(inputs_coarse, name), dtype=float)
        fine = np.asarray(getattr(inputs_fine, name), dtype=float)
        if not np.array_equal(coarse, fine):
            raise AssessmentRefusalError(
                f"refinement compares the SAME physical model; {name} differs "
                "between the coarse and fine inputs"
            )
    if inputs_coarse.q0 != inputs_fine.q0 or inputs_coarse.v0 != inputs_fine.v0:
        raise AssessmentRefusalError(
            "refinement requires the same declared initial state q0/v0"
        )
    if inputs_coarse.t0 != inputs_fine.t0:
        raise AssessmentRefusalError("refinement requires the same t0")
    if (inputs_fine.n_steps - 1) != ratio * (inputs_coarse.n_steps - 1):
        raise AssessmentRefusalError(
            "the coarse/fine grids are not exactly nested with the declared "
            "integer dt ratio"
        )
    if abs(inputs_coarse.dt - ratio * inputs_fine.dt) > 1.0e-9 * max(
        inputs_coarse.dt, 1.0
    ):
        raise AssessmentRefusalError(
            "dt_coarse must equal ratio * dt_fine for the declared integer ratio"
        )
    fine_forces = np.asarray(inputs_fine.forces, dtype=float)
    coarse_forces = np.asarray(inputs_coarse.forces, dtype=float)
    if history is None:
        same = np.array_equal(coarse_forces, fine_forces[::ratio])
    else:
        # Declared periodic prehistory: each member stores F_wave - H with its own
        # kernel's history force H. The same physical (wave) forcing is compared after
        # adding each member's recomputed H back, to a roundoff identity bound.
        coarse_wave = coarse_forces + history[0]
        fine_wave = (fine_forces + history[1])[::ratio]
        bound = PREHISTORY_FORCING_IDENTITY_RELATIVE * max(
            float(np.max(np.abs(coarse_wave))), float(np.max(np.abs(history[0]))),
            float(np.max(np.abs(history[1]))), 1.0e-300)
        same = coarse_wave.shape == fine_wave.shape and bool(
            np.max(np.abs(coarse_wave - fine_wave)) <= bound)
    if not same:
        raise AssessmentRefusalError(
            "the sampled forcing differs at the shared times; refinement "
            "compares the same physical forcing"
        )


def _history_force(case: AssessmentCase, inputs) -> np.ndarray:
    return periodic_prehistory_force(
        np.asarray(inputs.radiation_kernel, dtype=float), inputs.dt, inputs.t0, inputs.n_steps,
        case.omega, declaration_phasor(case.reference["prehistory"]))


def assess_refinement(case_coarse: AssessmentCase, case_fine: AssessmentCase,
                      result_coarse: ResultRecord,
                      result_fine: ResultRecord,
                      inputs_coarse: LinearDevelopmentInputs,
                      inputs_fine: LinearDevelopmentInputs) -> AssessmentReport:
    """Compare two caller-explicit cases of the SAME physical model.

    The grids must be exactly nested with the declared integer dt ratio
    (>= 2); the physical model, initial state, forcing, reference and
    windows must be identical. Regular-wave cases compare the fitted
    complex phasors (< 2% of the finest per-DOF amplitude); free-decay
    cases compare q/v/a histories at the shared times with per-quantity
    per-DOF unit scales (no mixed-unit maximum).
    """

    if case_coarse.mode != case_fine.mode:
        raise AssessmentRefusalError("refinement requires the same mode")
    refinement_coarse = case_coarse.refinement
    refinement_fine = case_fine.refinement
    if refinement_coarse is None or refinement_fine is None:
        raise AssessmentRefusalError(
            "both cases must declare their refinement partner"
        )
    if refinement_coarse["role"] != "coarse" or refinement_fine["role"] != "fine":
        raise AssessmentRefusalError(
            "the refinement roles must be declared coarse and fine"
        )
    if refinement_coarse["dt_ratio"] != refinement_fine["dt_ratio"]:
        raise AssessmentRefusalError("the declared dt ratios disagree")
    ratio = int(refinement_coarse["dt_ratio"])
    if refinement_coarse["partner_case_hash"] != case_fine.case_hash:
        raise AssessmentRefusalError(
            "the coarse case's refinement partner hash does not match the "
            "fine case"
        )
    fine_partner = refinement_fine["partner_case_hash"]
    if fine_partner is not None and fine_partner != case_coarse.case_hash:
        # The partner hashes cannot be circular (each hash covers the other
        # field), so the fine case may declare no partner; a wrong partner
        # is still refused.
        raise AssessmentRefusalError(
            "the fine case's refinement partner hash does not match the "
            "coarse case"
        )
    if case_coarse.reference.get("prehistory") != case_fine.reference.get("prehistory"):
        raise AssessmentRefusalError(
            "refinement compares the same declared prehistory; it differs between the cases")
    history = None
    if case_coarse.reference.get("prehistory") is not None:
        history = (_history_force(case_coarse, inputs_coarse), _history_force(case_fine, inputs_fine))
    _same_physical_model(inputs_coarse, inputs_fine, ratio, history)
    if case_coarse.reference["kind"] != case_fine.reference["kind"]:
        raise AssessmentRefusalError("refinement requires the same reference kind")
    if case_coarse.reference["kind"] == REFERENCE_ANALYTIC_EXPONENTIAL:
        if case_coarse.reference["decay"] != case_fine.reference["decay"] or (
            case_coarse.reference["coefficient_matrix"]
            != case_fine.reference["coefficient_matrix"]
        ):
            raise AssessmentRefusalError(
                "the analytic reference descriptor differs between the cases"
            )
    else:
        for key in (
            "route",
            "omega",
            "heading",
            "wave_amplitude",
            "wave_phase",
            "dataset_content_hash",
            "derived_content_hash",
            "operators_raw_sha256",
        ):
            if case_coarse.reference.get(key) != case_fine.reference.get(key):
                raise AssessmentRefusalError(
                    f"the hydro source identity {key!r} differs between the "
                    "cases; refinement compares the same source"
                )
    if case_coarse.mode == MODE_REGULAR_WAVE:
        if case_coarse.omega != case_fine.omega:
            raise AssessmentRefusalError("refinement requires the same omega")
        if case_coarse.force_phasors != case_fine.force_phasors:
            raise AssessmentRefusalError(
                "refinement requires the same force phasors"
            )
        if case_coarse.windows != case_fine.windows:
            raise AssessmentRefusalError(
                "refinement requires the same predeclared absolute windows"
            )
    for case, result, inputs in (
        (case_coarse, result_coarse, inputs_coarse),
        (case_fine, result_fine, inputs_fine),
    ):
        if result.input_hash != case.input_hash or linear_input_hash(
            inputs
        ) != case.input_hash:
            raise AssessmentRefusalError(
                "a refinement member's result/inputs do not match its case"
            )
        try:
            verify_linear_result(result, inputs)
        except Exception as error:
            raise AssessmentRefusalError(
                f"a refinement member is not an eligible result: {error}"
            ) from error
    checks: list = []
    arrays: dict = {"refinement": {"dt_ratio": ratio, "role": "coarse_vs_fine"}}
    criteria = case_fine.criteria
    if case_coarse.mode == MODE_REGULAR_WAVE:
        times_coarse = np.asarray(inputs_coarse.times, dtype=float)
        times_fine = np.asarray(inputs_fine.times, dtype=float)
        phasors = []
        for case, inputs, times, result in (
            (case_coarse, inputs_coarse, times_coarse, result_coarse),
            (case_fine, inputs_fine, times_fine, result_fine),
        ):
            q = np.column_stack(
                [
                    np.asarray(result.channels[f"q:{label}"], dtype=float)
                    for label in inputs.labels
                ]
            )
            qhat, _, _, _, _, _, _ = _fit_window(
                times, q, case, case.windows["start"], case.windows["mid"]
            )
            phasors.append(qhat)
        qhat_coarse, qhat_fine = phasors
        arrays["refinement"]["qhat_coarse"] = [
            [float(value.real), float(value.imag)] for value in qhat_coarse
        ]
        arrays["refinement"]["qhat_fine"] = [
            [float(value.real), float(value.imag)] for value in qhat_fine
        ]
        deviation = np.abs(qhat_coarse - qhat_fine)
        scale = np.abs(qhat_fine)
        checks.append(
            _normalized(
                "refinement_phasor",
                "m",
                deviation,
                scale,
                criteria["refinement_relative"],
                criteria["refinement_floor"],
                criteria["refinement_absolute"],
                "coarse vs fine fitted complex phasor, scaled by the finest "
                "per-DOF amplitude",
            )
        )
    else:
        for prefix, unit in (("q", "m"), ("v", "m/s"), ("a", "m/s^2")):
            coarse = np.column_stack(
                [
                    np.asarray(
                        result_coarse.channels[f"{prefix}:{label}"], dtype=float
                    )
                    for label in inputs_coarse.labels
                ]
            )[::1]
            fine = np.column_stack(
                [
                    np.asarray(
                        result_fine.channels[f"{prefix}:{label}"], dtype=float
                    )
                    for label in inputs_fine.labels
                ]
            )[::ratio]
            deviation = np.abs(coarse - fine)
            scale = np.max(np.abs(fine), axis=0)[None, :]
            checks.append(
                _normalized(
                    f"refinement_{prefix}",
                    unit,
                    deviation,
                    np.broadcast_to(scale, deviation.shape),
                    criteria["refinement_relative"],
                    criteria["refinement_floor"],
                    criteria["refinement_absolute"],
                    f"coarse vs fine {prefix} history at the shared times "
                    "(own unit, per-DOF scale; no mixed-unit maximum)",
                )
            )
            arrays["refinement"][f"max_{prefix}_deviation"] = float(
                np.max(deviation)
            )
    return _build_report(case_fine, result_fine, checks, arrays)


# ------------------------------------------------------ artifacts and store


def _safe_name(case_id: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in case_id
    )


def write_case_artifact(case: AssessmentCase, path) -> Path:
    payload = {
        "artifact": "anymarine_linear_assessment_case",
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "case_hash": case.case_hash,
        "case": case.to_dict(),
    }
    target = Path(path)
    atomic_write_bytes(target, json_bytes(payload))
    return target


def read_case_artifact(path) -> AssessmentCase:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssessmentCaseError(
            f"cannot read the case artifact {path}: {error}"
        ) from error
    if not isinstance(data, dict) or set(data) != {
        "artifact",
        "schema_version",
        "case_hash",
        "case",
    }:
        raise AssessmentCaseError(
            f"the case artifact {path} is not a closed-world assessment case"
        )
    if data["artifact"] != "anymarine_linear_assessment_case":
        raise AssessmentCaseError(f"{path} is not an assessment case artifact")
    if data["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
        raise AssessmentCaseError(
            f"unsupported case artifact schema_version {data['schema_version']!r}"
        )
    case = load_case(data["case"])
    if case.case_hash != data["case_hash"]:
        raise AssessmentCaseError(
            f"the case artifact {path} was mutated (stored hash "
            f"{data['case_hash'][:12]}... != recomputed "
            f"{case.case_hash[:12]}...); tampered cases are refused"
        )
    return case


def write_report_artifact(report: AssessmentReport, path) -> Path:
    payload = {
        "artifact": "anymarine_linear_assessment_report",
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "report_hash": report.report_hash,
        "report": report.to_dict(),
    }
    target = Path(path)
    atomic_write_bytes(target, json_bytes(payload))
    return target


def read_report_artifact(path) -> AssessmentReport:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssessmentCaseError(
            f"cannot read the report artifact {path}: {error}"
        ) from error
    if not isinstance(data, dict) or set(data) != {
        "artifact",
        "schema_version",
        "report_hash",
        "report",
    }:
        raise AssessmentCaseError(
            f"the report artifact {path} is not a closed-world assessment report"
        )
    if data["artifact"] != "anymarine_linear_assessment_report":
        raise AssessmentCaseError(f"{path} is not an assessment report artifact")
    if data["schema_version"] != ASSESSMENT_SCHEMA_VERSION:
        raise AssessmentCaseError(
            f"unsupported report artifact schema_version {data['schema_version']!r}"
        )
    report = load_report(data["report"])
    if report.report_hash != data["report_hash"]:
        raise AssessmentCaseError(
            f"the report artifact {path} was mutated (stored hash "
            f"{data['report_hash'][:12]}... != recomputed "
            f"{report.report_hash[:12]}...); tampered reports are refused"
        )
    return report


class AssessmentStore:
    """Content-hashed case/report artifacts under an assessment root."""

    def __init__(self, root):
        self.root = Path(root)

    def save_case(self, case: AssessmentCase) -> Path:
        directory = self.root / "cases"
        directory.mkdir(parents=True, exist_ok=True)
        return write_case_artifact(
            case,
            directory / f"{_safe_name(case.case_id)}-{case.case_hash[:12]}.json",
        )

    def save_report(self, report: AssessmentReport) -> Path:
        directory = self.root / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        return write_report_artifact(
            report,
            directory
            / f"{_safe_name(report.report_id)}-{report.report_hash[:12]}.json",
        )

    def load_case(self, path) -> AssessmentCase:
        return read_case_artifact(path)

    def load_report(self, path) -> AssessmentReport:
        return read_report_artifact(path)


# Reproduction comparison: only the fresh report identity fields
# (report_id/created_utc; the report hash is derived from the payload) are
# excluded; every structural, string and boolean key and every numerical
# metric/array entry of the semantic payload must agree. Numeric scalars
# use one tight documented roundoff tolerance (relative 1e-12 + absolute
# 1e-12 per scalar) so backend roundoff cannot fail a persistence
# comparison; this is NOT a physics tolerance and never weakens any
# assessment criterion.
_REPRODUCTION_RELATIVE = 1.0e-12
_REPRODUCTION_ABSOLUTE = 1.0e-12
_FRESH_REPORT_KEYS = ("report_id", "created_utc")


def _payload_disagreement(stored, recomputed, path: str = "report"):
    """First semantic payload disagreement, or ``None`` when they agree."""

    if isinstance(stored, bool) or isinstance(recomputed, bool):
        # bool before int: a boolean must reproduce as a boolean.
        if isinstance(stored, bool) and isinstance(recomputed, bool):
            return None if stored is recomputed else path
        return path
    if isinstance(stored, dict):
        if not isinstance(recomputed, dict) or set(stored) != set(recomputed):
            return path
        for key in sorted(stored):
            found = _payload_disagreement(
                stored[key], recomputed[key], f"{path}.{key}"
            )
            if found is not None:
                return found
        return None
    if isinstance(stored, list):
        if not isinstance(recomputed, list) or len(stored) != len(recomputed):
            return path
        for index in range(len(stored)):
            found = _payload_disagreement(
                stored[index], recomputed[index], f"{path}[{index}]"
            )
            if found is not None:
                return found
        return None
    if isinstance(stored, (int, float)) and isinstance(recomputed, (int, float)):
        if math.isclose(
            float(stored),
            float(recomputed),
            rel_tol=_REPRODUCTION_RELATIVE,
            abs_tol=_REPRODUCTION_ABSOLUTE,
        ):
            return None
        return path
    if type(stored) is not type(recomputed) or stored != recomputed:
        return path
    return None


def adjudicate_report(case: AssessmentCase, stored_report: AssessmentReport,
                      result: ResultRecord, inputs: LinearDevelopmentInputs,
                      *, project=None, provenance=None,
                      project_root=None) -> dict:
    """Reproduce an assessment from the actual stored arrays and proofs.

    The stored report's booleans and hashes are necessary but not
    sufficient: the case is re-assessed from the actual stored inputs,
    result, reference and (for hydro references) the actual project
    context and source sidecars, and the recomputed report's full
    semantic payload must agree with the stored report: the verdict,
    every check with its actual metrics, and every recorded array. Only
    the fresh report identity (report_id/created_utc; the hash is
    derived) is excluded, so a re-hashed report whose numerical metrics
    were forged while the pass flags stayed unchanged is refused. Any
    disagreement is a typed refusal.
    """

    if stored_report.case_hash != case.case_hash:
        raise AssessmentRefusalError(
            "the stored report does not describe this case (case hash "
            "mismatch); adjudication is refused"
        )
    recomputed = assess_result(
        case,
        result,
        inputs,
        project=project,
        provenance=provenance,
        project_root=project_root,
    )
    if recomputed.verdict != stored_report.verdict:
        raise AssessmentRefusalError(
            f"the stored report verdict {stored_report.verdict!r} does not "
            f"reproduce from the actual arrays (recomputed {recomputed.verdict!r})"
        )
    stored_payload = stored_report.to_dict()
    recomputed_payload = recomputed.to_dict()
    for key in _FRESH_REPORT_KEYS:
        stored_payload.pop(key, None)
        recomputed_payload.pop(key, None)
    disagreement = _payload_disagreement(stored_payload, recomputed_payload)
    if disagreement is not None:
        raise AssessmentRefusalError(
            f"the stored report does not reproduce from the actual arrays "
            f"(first semantic payload disagreement at {disagreement}); a "
            "re-hashed report with forged numerical metrics and unchanged "
            "pass flags is refused"
        )
    return {
        "agrees": True,
        "verdict": recomputed.verdict,
        "recomputed_report": recomputed,
        "stored_report_hash": stored_report.report_hash,
        "recomputed_report_hash": recomputed.report_hash,
    }
