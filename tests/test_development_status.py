"""Qt-free loader tests for the bundled development status snapshot.

These tests never import Qt, Capytaine, ANYsolver or any numerical backend,
and never run calculations. Corrupt or missing data must fail clearly.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from pathlib import Path

import pytest

from anymarine.development_status import (
    DevelopmentStatusError,
    bundled_status_path,
    load_development_status,
    startup_summary,
    validate_development_status,
)


def test_bundled_snapshot_loads_and_validates():
    status = load_development_status()
    assert status["format"] == "anymarine-development-status-v1"
    assert status["schema_version"] == 1
    assert status["snapshot_date"] == "2026-10-10"


def test_bundled_file_exists_next_to_loader():
    assert bundled_status_path().is_file()


def test_capabilities_cover_menus_with_valid_statuses():
    status = load_development_status()
    capabilities = status["capabilities"]
    assert capabilities
    ids = [entry["id"] for entry in capabilities]
    assert len(ids) == len(set(ids))
    for entry in capabilities:
        assert entry["status"] in ("available", "development", "unsupported")
        assert entry["area"]
        assert entry["feature"]
        assert entry["note"]
    # Key routes the File menu actually exposes must be present.
    joined = " ".join(entry["feature"] for entry in capabilities)
    for fragment in (
        "synthetic replay",
        "attached-plate",
        "seeded-wave",
        "Morison",
        "static equilibrium",
        "HAMS",
    ):
        assert fragment.lower() in joined.lower()


def test_hydrodynamic_status_is_truthful_unqualified_snapshot():
    status = load_development_status()
    hydro = status["hydrodynamic_status"]
    assert hydro["roadmap_state"] == "incomplete"
    assert hydro["w5_decision"] == "NO_GO"
    assert hydro["general_numerical_admission"] is False
    assert hydro["qualified"] is False
    energy = hydro["energy_point"]
    assert energy["summary_status"] == "energy_point_comparison_failed_unqualified"
    assert energy["computation_state"] == "completed_unqualified"
    assert energy["registered_comparison"] == "failed"
    assert set(energy["registered_predicates"]) == {
        "added_mass_block",
        "radiation_damping_block",
        "mean_power",
    }
    assert all(v == "failed" for v in energy["registered_predicates"].values())
    assert energy["added_mass_counts"] == {"passed": 10, "failed": 6}
    assert energy["radiation_damping_counts"] == {"passed": 8, "failed": 8}
    assert energy["candidate_mean_power_w"] == pytest.approx(0.0008646219627273671)
    assert energy["hams_reference_mean_power_w"] == pytest.approx(0.0015879742100973749)
    assert energy["mean_power_comparison"] == "failed"
    audit = hydro["independent_audit"]
    assert audit["state"] == "COMPLETE"
    assert (audit["checks_passed"], audit["checks_total"]) == (100, 100)
    assert hydro["checker_mock_tests"]["recorded_result"] == "50 passed"


def test_all_numbers_finite():
    status = load_development_status()

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, float):
            assert math.isfinite(node)

    walk(status)


def test_no_absolute_working_paths_in_portable_data():
    status = load_development_status()

    def walk(node, where="root"):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")
        elif isinstance(node, str):
            assert not node.startswith(("C:", "D:", "/")), f"{where}: {node!r}"
            assert "C:/" not in node and "C:\\\\" not in node, f"{where}: {node!r}"

    walk(status)


def test_provenance_links_identifiers_not_raw_files():
    status = load_development_status()
    provenance = status["provenance"]
    assert "do not ship" in provenance["data_kind"]
    assert provenance["public_document"] == "docs/DEVELOPMENT_STATUS.md"
    ids = [source["id"] for source in provenance["sources"]]
    assert any("energy-point-02" in identifier for identifier in ids)
    assert any("audit02" in identifier for identifier in ids)


def test_frozen_native_case_and_audit_provenance_identity():
    status = load_development_status()
    sources = {
        source["id"]: source for source in status["provenance"]["sources"]
    }
    case = sources["anyhydro-w5-e1-energy-check-20261010-energy-point-02-summary"]
    audit = sources["anyhydro-w5-e1-energy-check-20261010-audit02-run-02"]
    assert case["case_hash"] == (
        "4c5309232d7e549b548ba3274ee5edfd17351f5b5ac4edc4fb06c9b2f68c214b"
    )
    assert audit["audit_script_sha256"] == (
        "6139d28d3ea9ea065964ce057d24243b7cd4d6afeac9da592b9b32b7d23b1b66"
    )


def test_startup_summary_is_compact_and_truthful():
    status = load_development_status()
    line = startup_summary(status)
    assert "2026-10-10" in line
    assert "unqualified" in line
    assert "NO_GO" in line
    assert "\n" not in line


def test_missing_file_fails_clearly(tmp_path):
    with pytest.raises(DevelopmentStatusError, match="missing"):
        load_development_status(tmp_path / "absent.json")


def test_corrupt_json_fails_clearly(tmp_path):
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(DevelopmentStatusError, match="corrupt"):
        load_development_status(target)


def test_non_finite_number_rejected(tmp_path):
    target = tmp_path / "nan.json"
    target.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(DevelopmentStatusError, match="non-finite"):
        load_development_status(target)


def test_non_utf8_file_refused(tmp_path):
    target = tmp_path / "not-utf8.json"
    target.write_bytes(b'{"summary": "\xff\xfe\xfd not utf-8"}')
    with pytest.raises(DevelopmentStatusError, match="UTF-8"):
        load_development_status(target)


def test_oversized_integer_literal_refused(tmp_path):
    target = tmp_path / "huge-int.json"
    target.write_text('{"a": 1' + "0" * 5000 + "}", encoding="utf-8")
    with pytest.raises(DevelopmentStatusError, match="corrupt"):
        load_development_status(target)


def test_huge_integer_float_field_refused():
    status = load_development_status()
    status["hydrodynamic_status"]["energy_point"]["candidate_mean_power_w"] = 10 ** 400
    with pytest.raises(DevelopmentStatusError, match="finite"):
        validate_development_status(status)


def _mutated(mutator) -> dict:
    status = load_development_status()
    mutator(status)
    return status


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda s: s.__setitem__("format", "wrong-format-v9"), "format"),
        (lambda s: s.__setitem__("schema_version", 2), "schema_version"),
        (lambda s: s.__setitem__("snapshot_date", "not-a-date"), "snapshot_date"),
        (lambda s: s.pop("capabilities"), "capabilities"),
        (lambda s: s.__setitem__("capabilities", []), "non-empty list"),
        (
            lambda s: s["capabilities"][0].__setitem__("status", "eligible"),
            "status",
        ),
        (
            lambda s: s["capabilities"].append(copy.deepcopy(s["capabilities"][0])),
            "duplicate capability id",
        ),
        (
            lambda s: s["hydrodynamic_status"].__setitem__("qualified", True),
            "qualified",
        ),
        (
            lambda s: s["hydrodynamic_status"].__setitem__(
                "general_numerical_admission", True
            ),
            "general_numerical_admission",
        ),
        (
            lambda s: s["hydrodynamic_status"]["energy_point"].__setitem__(
                "summary_status", "qualified"
            ),
            "summary_status",
        ),
        (
            lambda s: s["hydrodynamic_status"]["energy_point"].__setitem__(
                "candidate_mean_power_w", float("inf")
            ),
            "finite",
        ),
        (
            lambda s: s["hydrodynamic_status"]["energy_point"].pop(
                "registered_predicates"
            ),
            "registered_predicates",
        ),
        (
            lambda s: s["provenance"].__setitem__(
                "public_document", "C:/Github/ANYmarine/docs/DEVELOPMENT_STATUS.md"
            ),
            "absolute paths",
        ),
        (
            lambda s: s["summary"].__setitem__("headline", "/var/tmp/leaked"),
            "absolute paths",
        ),
    ],
)
def test_schema_violations_fail_clearly(mutator, message):
    with pytest.raises(DevelopmentStatusError, match=message):
        validate_development_status(_mutated(mutator))


def test_loader_module_has_no_qt_or_backend_side_effects():
    # Fresh subprocess: importing the loader must not pull Qt or numerical
    # backends into sys.modules and must not run calculations.
    import subprocess

    import anymarine.development_status as own_loader

    src_root = str(Path(own_loader.__file__).resolve().parent.parent)
    code = (
        "import sys\n"
        "import anymarine.development_status as ds\n"
        "banned = [name for name in sys.modules if name.split('.')[0] in "
        "('PySide6', 'capytaine', 'anysolver', 'anyhydro', 'numpy', 'scipy')]\n"
        "assert not banned, banned\n"
        "status = ds.load_development_status()\n"
        "assert status['snapshot_date'] == '2026-10-10'\n"
        "print('clean')\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = src_root
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_bundled_json_is_valid_json_document():
    text = bundled_status_path().read_text(encoding="utf-8")
    data = json.loads(text)
    assert isinstance(data, dict)
