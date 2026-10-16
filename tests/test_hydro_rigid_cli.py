"""Command-line entry of the six-DOF hydro import and its frequency-domain references (ADR-015 L6-3)."""
from __future__ import annotations

import json

import pytest

from conftest import requires_solver

pytest.importorskip("anyhydro")
pytest.importorskip("anyhydro.hydrostatics_exact")

from anymarine import cli  # noqa: E402
from anymarine.services import ApplicationService  # noqa: E402

from test_hydro_rigid_import import LAM, OMEGAS, _blocks, _body, _options, _project, _source  # noqa: E402

pytestmark = requires_solver


def _setup(tmp_path, wave=True):
    source_dir, root = tmp_path / "source", tmp_path / "project"
    source_dir.mkdir()
    project = _project((_body("vessel", cb_offset=(1.0, -0.5)),))
    _, C, _, _, _ = _blocks(project.bodies[0])
    ds_path, k_path, _, _ = _source(source_dir, project, explicit_b0=C / LAM)
    ApplicationService(root).save(project, root=root)
    options = _options(project, n_steps=401)
    if not wave:
        options["wave"] = None
    options_path = tmp_path / "options.json"
    options_path.write_text(json.dumps(options), encoding="utf-8")
    return root, ds_path, k_path, options_path


def test_import_run_and_references_from_the_command_line(tmp_path, capsys):
    root, ds_path, k_path, options_path = _setup(tmp_path)
    assert cli.main(["import-rigid-hydro", "--project", str(root), "--dataset", str(ds_path),
                     "--kernel", str(k_path), "--options", str(options_path)]) == 0
    out = capsys.readouterr().out
    assert "rigid_subset_v1" in out and "development source, unqualified" in out
    assert "vessel:surge vessel:sway vessel:heave vessel:roll vessel:pitch vessel:yaw" in out
    project = ApplicationService(root).load(root)
    input_hash = project.hydro_provenance.linear_input_hash
    assert f"input_hash={input_hash}" in out
    assert cli.main(["run-linear", "--project", str(root), "--local"]) == 0
    capsys.readouterr()
    refs_path = tmp_path / "refs.json"
    assert cli.main(["rigid-references", "--project", str(root), "--output", str(refs_path)]) == 0
    refs = json.loads(refs_path.read_text(encoding="utf-8"))
    assert refs["linear_input_hash"] == input_hash
    assert refs["wave"]["omega"] == float(OMEGAS[40])
    assert refs["normalised_max_difference"] <= 0.02
    assert f"wrote frequency-domain references to {refs_path}" in capsys.readouterr().out
    assert cli.main(["rigid-references", "--project", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == refs


def test_command_line_refusals_are_reported_not_raised(tmp_path, capsys):
    root, ds_path, k_path, options_path = _setup(tmp_path, wave=False)
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert cli.main(["import-rigid-hydro", "--project", str(root), "--dataset", str(ds_path),
                     "--kernel", str(k_path), "--options", str(bad)]) == 2
    assert "must be a JSON object" in capsys.readouterr().err
    assert cli.main(["rigid-references", "--project", str(root)]) == 1
    assert "rigid_subset_v1 hydro import" in capsys.readouterr().err
    assert cli.main(["import-rigid-hydro", "--project", str(root), "--dataset", str(ds_path),
                     "--kernel", str(k_path), "--options", str(options_path)]) == 0
    capsys.readouterr()
    assert cli.main(["rigid-references", "--project", str(root)]) == 1
    assert "free decay" in capsys.readouterr().err


def test_malformed_out_of_range_and_unwritable_inputs_exit_cleanly(tmp_path, capsys):
    """CLI review round 1: every refusal path ends in an exit code, never a traceback."""
    root, ds_path, k_path, options_path = _setup(tmp_path)
    args = ["import-rigid-hydro", "--project", str(root), "--dataset", str(ds_path), "--kernel", str(k_path)]
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json", encoding="utf-8")
    assert cli.main(args + ["--options", str(malformed)]) == 2
    assert "cannot read import options" in capsys.readouterr().err
    assert cli.main(args + ["--options", str(tmp_path / "missing.json")]) == 2
    assert "cannot read import options" in capsys.readouterr().err
    huge = json.loads(options_path.read_text(encoding="utf-8"))
    huge["length_scale"] = 10 ** 400
    huge_path = tmp_path / "huge.json"
    huge_path.write_text(json.dumps(huge), encoding="utf-8")
    assert cli.main(args + ["--options", str(huge_path)]) == 1
    assert "length_scale" in capsys.readouterr().err
    assert cli.main(args + ["--options", str(options_path)]) == 0
    capsys.readouterr()
    assert cli.main(["rigid-references", "--project", str(root), "--output", str(tmp_path)]) == 2
    assert "cannot write" in capsys.readouterr().err


def test_references_of_a_legacy_project_are_refused(tmp_path, capsys):
    from anymarine.domain import default_two_body_project
    from anymarine.linear import analytic_demo_inputs
    root = tmp_path / "legacy"
    project = default_two_body_project()
    service = ApplicationService(root)
    service.save(project.with_edits(linear_inputs=analytic_demo_inputs(project)), root=root)
    assert cli.main(["rigid-references", "--project", str(root)]) == 1
    assert "rigid_subset_v1 hydro import" in capsys.readouterr().err
