"""Frozen legacy four-DOF linear route (ADR-015 section 1): numerics and hashes reproduce exactly.

Fixtures were captured from main 85b22fb by tests/fixtures/linear-legacy/generate_fixtures.py before the six-DOF
layout work; they must never be regenerated on a branch that changes the linear route.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from conftest import requires_solver

from anymarine.domain import LinearDevelopmentInputs, ResultRecord, default_two_body_project
from anymarine.linear import analytic_demo_inputs, linear_input_hash, run_linear
from anymarine.persistence import ProjectStore

FIXTURES = Path(__file__).parent / "fixtures" / "linear-legacy"
VOLATILE = ("result_id", "report_id", "created_utc")


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _canonical(data):
    return json.loads(json.dumps(data, sort_keys=True))


def _stable(data):
    """Canonical copy without volatile identities/timestamps (compared separately where they matter)."""
    if isinstance(data, dict):
        return {k: _stable(v) for k, v in data.items() if k not in VOLATILE}
    if isinstance(data, list):
        return [_stable(v) for v in data]
    return data


def test_demo_inputs_serialise_and_hash_exactly():
    frozen = _load("demo_inputs.json")
    inputs = LinearDevelopmentInputs.from_dict(frozen["inputs"])
    assert _canonical(inputs.to_dict()) == frozen["inputs"]
    assert linear_input_hash(inputs) == frozen["input_hash"]
    # body ids are random per project: rebuild the demo on the frozen body identities
    project = default_two_body_project()
    ids = [binding["body_id"] for binding in frozen["inputs"]["bindings"]]
    project = dataclasses.replace(project, bodies=tuple(
        dataclasses.replace(body, body_id=body_id) for body, body_id in zip(project.bodies, ids)))
    rebuilt = _canonical(analytic_demo_inputs(project).to_dict())
    rebuilt["provenance"]["created_utc"] = frozen["inputs"]["provenance"]["created_utc"]
    assert rebuilt == frozen["inputs"]
    assert linear_input_hash(LinearDevelopmentInputs.from_dict(rebuilt)) == frozen["input_hash"]


def test_demo_result_record_round_trips_exactly():
    frozen = _load("demo_result.json")
    assert _canonical(ResultRecord.from_dict(frozen).to_dict()) == frozen


@requires_solver
def test_demo_result_numerics_reproduce_bit_identically():
    frozen = _load("demo_result.json")
    inputs = LinearDevelopmentInputs.from_dict(_load("demo_inputs.json")["inputs"])
    result = run_linear(inputs, result_id=frozen["result_id"], created_utc=frozen["created_utc"])
    assert _canonical(result.to_dict()) == frozen


def test_saved_legacy_project_loads_and_resaves_byte_identically(tmp_path):
    source = tmp_path / "frozen"
    shutil.copytree(FIXTURES / "rw_project", source)
    project = ProjectStore(source).load()
    assert project.linear_inputs is not None and len(project.results) == 1
    assert linear_input_hash(project.linear_inputs) == _load("rw_assessment.json")["input_hash"]
    target = tmp_path / "resaved"
    ProjectStore(target).save(project)
    frozen_files = sorted(p.relative_to(source) for p in source.rglob("*") if p.is_file())
    resaved_files = sorted(p.relative_to(target) for p in target.rglob("*") if p.is_file())
    assert resaved_files == frozen_files
    for relative in frozen_files:
        assert (target / relative).read_bytes() == (source / relative).read_bytes(), relative


@requires_solver
def test_regular_wave_assessment_reproduces(tmp_path):
    from test_linear_assessment import MODE_REGULAR_WAVE, WINDOWS, _regular_wave_inputs, _spec
    from anymarine.services import ApplicationService

    frozen = _load("rw_assessment.json")
    root = tmp_path / "project"
    shutil.copytree(FIXTURES / "rw_project", root)
    service = ApplicationService(root)
    project = service.load(root)
    inputs = project.linear_inputs
    assert linear_input_hash(inputs) == frozen["input_hash"]
    # the test helper still rebuilds the same inputs on the frozen bodies
    rebuilt = _canonical(_regular_wave_inputs(project).to_dict())
    rebuilt["provenance"]["created_utc"] = inputs.provenance.created_utc
    assert rebuilt == _canonical(inputs.to_dict())
    case = service.prepare_linear_assessment(
        project, _spec("rw-pass", MODE_REGULAR_WAVE, inputs, windows=WINDOWS))
    assert case.case_hash == frozen["case_hash"]
    assert _stable(case.to_dict()) == _stable(frozen["case"])
    result = service.run_linear_local(project)
    stored = {k: v for k, v in frozen["result"].items() if k not in VOLATILE}
    assert {k: v for k, v in _canonical(result.to_dict()).items() if k not in VOLATILE} == stored
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass"
    assert _stable(report.to_dict()) == _stable(frozen["report"])
