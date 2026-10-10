"""Qt-free public loader for the bundled development status snapshot.

Loads and validates ``development_status.json`` so wheel builds contain the
same data as the checkout. Pure standard library: importing this module must
never pull in Qt, Capytaine, ANYsolver or any numerical backend, and loading
never runs calculations. Corrupt or missing data fails with a clear
:class:`DevelopmentStatusError`.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

FORMAT = "anymarine-development-status-v1"
SCHEMA_VERSION = 1
CAPABILITY_STATUSES = ("available", "development", "unsupported")
PREDICATE_STATUSES = ("passed", "failed", "unresolved")

_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^[A-Za-z]:[\\/]|^/|^[\\/]{2})")


class DevelopmentStatusError(Exception):
    """Raised when the bundled development status data is missing or invalid."""


def _reject_constant(token: str) -> Any:
    raise DevelopmentStatusError(
        f"non-finite JSON number {token!r} is not allowed in development status data"
    )


def _require(mapping: Any, key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise DevelopmentStatusError(f"missing required key {where}.{key}")
    return mapping[key]


def _require_str(mapping: Any, key: str, where: str) -> str:
    value = _require(mapping, key, where)
    if not isinstance(value, str) or not value.strip():
        raise DevelopmentStatusError(f"{where}.{key} must be a non-empty string")
    return value


def _require_bool(mapping: Any, key: str, where: str) -> bool:
    value = _require(mapping, key, where)
    if not isinstance(value, bool):
        raise DevelopmentStatusError(f"{where}.{key} must be a boolean")
    return value


def _require_int(mapping: Any, key: str, where: str) -> int:
    value = _require(mapping, key, where)
    if not isinstance(value, int) or isinstance(value, bool):
        raise DevelopmentStatusError(f"{where}.{key} must be an integer")
    return value


def _require_finite_float(mapping: Any, key: str, where: str) -> float:
    value = _require(mapping, key, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DevelopmentStatusError(f"{where}.{key} must be a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise DevelopmentStatusError(f"{where}.{key} must be finite") from exc
    if not math.isfinite(number):
        raise DevelopmentStatusError(f"{where}.{key} must be finite")
    return number


def _check_portable_string(value: str, where: str) -> None:
    if _ABSOLUTE_PATH_PATTERN.search(value):
        raise DevelopmentStatusError(
            f"{where} must not contain stale working absolute paths: {value!r}"
        )


def _validate_snapshot_date(raw: Any) -> str:
    if not isinstance(raw, str):
        raise DevelopmentStatusError("snapshot_date must be a string")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise DevelopmentStatusError(f"snapshot_date is not an ISO date: {raw!r}") from exc
    if parsed.isoformat() != raw:
        raise DevelopmentStatusError(f"snapshot_date must be a strict ISO date: {raw!r}")
    return raw


def _validate_summary(data: dict) -> None:
    summary = _require(data, "summary", "root")
    for key in ("headline", "scope_note", "next_development_question"):
        _check_portable_string(_require_str(summary, key, "summary"), f"summary.{key}")


def _validate_capabilities(data: dict) -> list[dict]:
    capabilities = _require(data, "capabilities", "root")
    if not isinstance(capabilities, list) or not capabilities:
        raise DevelopmentStatusError("capabilities must be a non-empty list")
    seen_ids: set[str] = set()
    for index, entry in enumerate(capabilities):
        where = f"capabilities[{index}]"
        if not isinstance(entry, dict):
            raise DevelopmentStatusError(f"{where} must be an object")
        identifier = _require_str(entry, "id", where)
        if identifier in seen_ids:
            raise DevelopmentStatusError(f"duplicate capability id {identifier!r}")
        seen_ids.add(identifier)
        for key in ("area", "feature", "note"):
            _check_portable_string(_require_str(entry, key, where), f"{where}.{key}")
        status = _require_str(entry, "status", where)
        if status not in CAPABILITY_STATUSES:
            raise DevelopmentStatusError(
                f"{where}.status must be one of {CAPABILITY_STATUSES}, got {status!r}"
            )
        menu_path = entry.get("menu_path", "")
        if not isinstance(menu_path, str):
            raise DevelopmentStatusError(f"{where}.menu_path must be a string")
    return capabilities


def _validate_hydrodynamic_status(data: dict) -> None:
    hydro = _require(data, "hydrodynamic_status", "root")
    where = "hydrodynamic_status"
    if _require_str(hydro, "roadmap_state", where) != "incomplete":
        raise DevelopmentStatusError(f"{where}.roadmap_state must be 'incomplete'")
    _require_str(hydro, "broader_numerical_development", where)
    if _require_str(hydro, "w5_decision", where) != "NO_GO":
        raise DevelopmentStatusError(f"{where}.w5_decision must be 'NO_GO'")
    if _require_bool(hydro, "general_numerical_admission", where):
        raise DevelopmentStatusError(
            f"{where}.general_numerical_admission must be false in a truthful snapshot"
        )
    if _require_bool(hydro, "qualified", where):
        raise DevelopmentStatusError(f"{where}.qualified must be false in a truthful snapshot")

    energy = _require(hydro, "energy_point", where)
    energy_where = f"{where}.energy_point"
    _require_str(energy, "label", energy_where)
    _require_str(energy, "run_state", energy_where)
    if _require_str(energy, "summary_status", energy_where) != "energy_point_comparison_failed_unqualified":
        raise DevelopmentStatusError(
            f"{energy_where}.summary_status must be 'energy_point_comparison_failed_unqualified'"
        )
    if _require_str(energy, "computation_state", energy_where) != "completed_unqualified":
        raise DevelopmentStatusError(f"{energy_where}.computation_state must be 'completed_unqualified'")
    if _require_str(energy, "registered_comparison", energy_where) != "failed":
        raise DevelopmentStatusError(f"{energy_where}.registered_comparison must be 'failed'")
    predicates = _require(energy, "registered_predicates", energy_where)
    if not isinstance(predicates, dict) or not predicates:
        raise DevelopmentStatusError(f"{energy_where}.registered_predicates must be a non-empty object")
    for name, verdict in predicates.items():
        if verdict not in PREDICATE_STATUSES:
            raise DevelopmentStatusError(
                f"{energy_where}.registered_predicates.{name} must be one of {PREDICATE_STATUSES}"
            )
    for counts_key in ("added_mass_counts", "radiation_damping_counts"):
        counts = _require(energy, counts_key, energy_where)
        counts_where = f"{energy_where}.{counts_key}"
        passed = _require_int(counts, "passed", counts_where)
        failed = _require_int(counts, "failed", counts_where)
        if passed < 0 or failed < 0:
            raise DevelopmentStatusError(f"{counts_where} must be non-negative")
    _require_str(energy, "retained_dofs", energy_where)
    _require_finite_float(energy, "candidate_mean_power_w", energy_where)
    _require_finite_float(energy, "hams_reference_mean_power_w", energy_where)
    if _require_str(energy, "mean_power_comparison", energy_where) != "failed":
        raise DevelopmentStatusError(f"{energy_where}.mean_power_comparison must be 'failed'")
    _require_str(energy, "sign_note", energy_where)

    checker = _require(hydro, "checker_mock_tests", where)
    checker_where = f"{where}.checker_mock_tests"
    _require_str(checker, "recorded_result", checker_where)
    _require_str(checker, "receipt", checker_where)
    _require_str(checker, "scope", checker_where)

    audit = _require(hydro, "independent_audit", where)
    audit_where = f"{where}.independent_audit"
    if _require_str(audit, "state", audit_where) != "COMPLETE":
        raise DevelopmentStatusError(f"{audit_where}.state must be 'COMPLETE'")
    total = _require_int(audit, "checks_total", audit_where)
    passed = _require_int(audit, "checks_passed", audit_where)
    if total < 0 or passed < 0 or passed > total:
        raise DevelopmentStatusError(f"{audit_where} counts must satisfy 0 <= passed <= total")
    _require_str(audit, "interpretation", audit_where)
    _require_str(audit, "handoff_note", audit_where)


def _validate_provenance(data: dict) -> None:
    provenance = _require(data, "provenance", "root")
    where = "provenance"
    _check_portable_string(_require_str(provenance, "data_kind", where), f"{where}.data_kind")
    _check_portable_string(_require_str(provenance, "public_document", where), f"{where}.public_document")
    sources = _require(provenance, "sources", where)
    if not isinstance(sources, list) or not sources:
        raise DevelopmentStatusError(f"{where}.sources must be a non-empty list")
    for index, source in enumerate(sources):
        source_where = f"{where}.sources[{index}]"
        _check_portable_string(
            _require_str(source, "id", source_where), f"{source_where}.id"
        )
        _require_str(source, "format", source_where)


def validate_development_status(data: Any) -> dict:
    """Validate a parsed development status document; return it unchanged."""
    if not isinstance(data, dict):
        raise DevelopmentStatusError("development status document must be a JSON object")
    if _require_str(data, "format", "root") != FORMAT:
        raise DevelopmentStatusError(f"format must be {FORMAT!r}")
    if _require_int(data, "schema_version", "root") != SCHEMA_VERSION:
        raise DevelopmentStatusError(f"schema_version must be {SCHEMA_VERSION}")
    _validate_snapshot_date(_require(data, "snapshot_date", "root"))
    _validate_summary(data)
    _validate_capabilities(data)
    _validate_hydrodynamic_status(data)
    _validate_provenance(data)
    return data


def bundled_status_path() -> Path:
    """Path of the bundled development status JSON next to this module."""
    return Path(__file__).resolve().parent / "development_status.json"


def load_development_status(path: str | Path | None = None) -> dict:
    """Load and validate the bundled (or given) development status snapshot.

    Raises :class:`DevelopmentStatusError` for missing files, non-UTF-8 data,
    JSON syntax errors (including unparseable oversized numbers), non-finite
    or overflowing numbers, stale absolute paths and schema violations.
    """
    target = Path(path) if path is not None else bundled_status_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DevelopmentStatusError(
            f"development status data file is missing: {target}"
        ) from exc
    except OSError as exc:
        raise DevelopmentStatusError(
            f"development status data file cannot be read: {target}: {exc}"
        ) from exc
    except UnicodeDecodeError as exc:
        raise DevelopmentStatusError(
            f"development status data file is not valid UTF-8 text: {target}: {exc}"
        ) from exc
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise DevelopmentStatusError(
            f"development status data is corrupt (invalid JSON): {target}: {exc}"
        ) from exc
    return validate_development_status(data)


def startup_summary(status: dict) -> str:
    """Compact one-line startup status derived from a validated snapshot."""
    snapshot_date = status["snapshot_date"]
    hydro = status["hydrodynamic_status"]
    available = sum(
        1 for entry in status["capabilities"] if entry["status"] == "available"
    )
    return (
        f"Development snapshot {snapshot_date}: {available} loadable example/feature routes, "
        "hydrodynamics unqualified (W5 NO_GO) - details under Help > Development status"
    )
