"""Six-DOF hydro import through the application service: provenance, sidecars, persistence (ADR-015 L6-3 M-c).

Uses the analytic box fixture of test_hydro_rigid_import.py. The rigid provenance bundle stores the plain caller
options; every verification re-runs the import from the stored sidecars and must reproduce the stored inputs and
every source-derived provenance field.
"""
from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from conftest import requires_solver

pytest.importorskip("anyhydro")
pytest.importorskip("anyhydro.hydrostatics_exact")

from anymarine.domain import HydroBridgeProvenance, LINEAR_LAYOUT_RIGID  # noqa: E402
from anymarine.linear import linear_input_hash  # noqa: E402
from anymarine.persistence import resolve_sidecar  # noqa: E402
from anymarine.services import ApplicationService, ServiceError  # noqa: E402

from test_hydro_rigid_import import _body, _options, _project, _source  # noqa: E402

pytestmark = requires_solver


def _imported(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    root = tmp_path / "project"
    project = _project((_body("vessel", cb_offset=(1.0, -0.5)),))
    ds_path, k_path, _, _ = _source(source_dir, project)
    service = ApplicationService(root)
    service.save(project, root=root)
    imported = service.import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=401))
    return service, root, imported, (ds_path, k_path)


def test_import_save_reopen_and_run(tmp_path):
    service, root, project, _ = _imported(tmp_path)
    provenance = project.hydro_provenance
    assert project.linear_inputs.layout == LINEAR_LAYOUT_RIGID
    assert provenance.rigid_bundle["route"] == "rigid_raw_v1"
    assert provenance.dof_keys == tuple(f"vessel::{d}" for d in ("surge", "sway", "heave", "roll", "pitch", "yaw"))
    assert provenance.linear_input_hash == linear_input_hash(project.linear_inputs)
    for reference in (provenance.dataset_sidecar, provenance.kernel_sidecar):
        assert resolve_sidecar(root, reference).is_file()
    service.save(project)
    reopened = ApplicationService(root).load(root)
    assert reopened.hydro_provenance.to_dict() == provenance.to_dict()
    assert linear_input_hash(reopened.linear_inputs) == provenance.linear_input_hash
    record = ApplicationService(root).run_linear_local(reopened)
    assert record.input_hash is not None


def test_job_snapshot_runs_in_the_worker(tmp_path):
    from anymarine import worker
    service, root, project, _ = _imported(tmp_path)
    snapshot = service.linear_job_snapshot(project)
    assert snapshot["hydro_provenance"]["rigid_bundle"]["route"] == "rigid_raw_v1"
    path, out = tmp_path / "job.json", tmp_path / "out.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert worker.run(path, out, None) == worker.EXIT_SUCCESS
    assert json.loads(out.read_text(encoding="utf-8"))["input_hash"] == project.hydro_provenance.linear_input_hash


def test_multi_body_project_with_unsorted_ids_reopens(tmp_path):
    """Review round 1: the manifest sorts JSON keys; the stored per-body DOF mapping is replayed in body order."""
    source_dir, root = tmp_path / "source", tmp_path / "project"
    source_dir.mkdir()
    project = _project((_body("z"), _body("a", x0=30.0, cb_offset=(0.5, 0.0))))
    ds_path, k_path, _, _ = _source(source_dir, project)
    service = ApplicationService(root)
    service.save(project, root=root)
    imported = service.import_rigid_hydro_inputs(project, ds_path, k_path, _options(project, n_steps=401))
    service.save(imported)
    reopened = ApplicationService(root).load(root)
    assert reopened.hydro_provenance.body_ids == ("z", "a")
    assert linear_input_hash(reopened.linear_inputs) == imported.hydro_provenance.linear_input_hash


def test_save_as_copies_the_sources_and_reopens_without_the_originals(tmp_path):
    import shutil
    service, root, project, sources = _imported(tmp_path)
    other = tmp_path / "copy"
    service.save(project, root=other)
    shutil.rmtree(tmp_path / "source")
    shutil.rmtree(root)
    reopened = ApplicationService(other).load(other)
    assert reopened.hydro_provenance.to_dict() == project.hydro_provenance.to_dict()


def test_legacy_provenance_serialisation_has_no_rigid_key(tmp_path):
    _, _, project, _ = _imported(tmp_path)
    data = project.hydro_provenance.to_dict()
    legacy = dict(data, body_ids=["a", "b"], dof_keys=["a::surge", "a::heave", "b::surge", "b::heave"],
                  mesh_hashes=[data["mesh_hashes"][0]] * 2)
    del legacy["rigid_bundle"]
    assert "rigid_bundle" not in HydroBridgeProvenance.from_dict(legacy).to_dict()


def test_rigid_bundle_shape_is_strict(tmp_path):
    _, _, project, _ = _imported(tmp_path)
    data = project.hydro_provenance.to_dict()
    for change in (dict(rigid_bundle=dict(data["rigid_bundle"], route="raw_dataset")),
                   dict(rigid_bundle=dict(data["rigid_bundle"], extra=1)),
                   dict(surge_link_stiffness=1.0),
                   dict(dof_keys=["vessel::surge", "other::heave"]),
                   dict(mesh_hashes=data["mesh_hashes"] * 2)):
        with pytest.raises(ValueError):
            HydroBridgeProvenance.from_dict(dict(data, **change))
    stripped = dict(data)
    del stripped["rigid_bundle"]
    with pytest.raises(ValueError):
        dataclasses.replace(project, hydro_provenance=HydroBridgeProvenance.from_dict(
            dict(stripped, body_ids=["vessel", "x"], dof_keys=["vessel::surge", "vessel::heave", "x::surge",
                                                               "x::heave"],
                 mesh_hashes=data["mesh_hashes"] * 2)))


def test_tampered_inputs_with_rehashed_bookkeeping_are_refused(tmp_path):
    service, root, project, _ = _imported(tmp_path)
    inputs = project.linear_inputs
    K = np.array(inputs.stiffness)
    K[2, 2] *= 1.01
    tampered = dataclasses.replace(inputs, stiffness=tuple(tuple(row) for row in K))
    data = dict(project.hydro_provenance.to_dict(), linear_input_hash=linear_input_hash(tampered))
    forged = dataclasses.replace(project, linear_inputs=tampered,
                                 hydro_provenance=HydroBridgeProvenance.from_dict(data))
    with pytest.raises(ServiceError, match="re-imported"):
        service.verify_hydro_provenance(forged)


def test_changed_sidecar_and_changed_options_are_refused(tmp_path):
    service, root, project, _ = _imported(tmp_path)
    data = project.hydro_provenance.to_dict()
    options = dict(data["rigid_bundle"]["options"], length_scale=13.0)
    forged = dataclasses.replace(project, hydro_provenance=HydroBridgeProvenance.from_dict(
        dict(data, rigid_bundle=dict(data["rigid_bundle"], options=options))))
    with pytest.raises(ServiceError, match="re-import|reproduce"):
        service.verify_hydro_provenance(forged)
    sidecar = resolve_sidecar(root, data["kernel_sidecar"])
    sidecar.write_bytes(sidecar.read_bytes() + b"\0")
    with pytest.raises(ServiceError, match="sidecar"):
        service.verify_hydro_provenance(project)


def test_route_guards(tmp_path):
    service, root, project, sources = _imported(tmp_path)
    with pytest.raises(ServiceError, match="six-DOF layouts"):
        service.import_hydro_inputs(project, *sources, {})
    with pytest.raises(ServiceError, match="project root"):
        ApplicationService(None).import_rigid_hydro_inputs(project, *sources, _options(project))
    with pytest.raises(ServiceError, match="explicit load path|verified hydro import"):
        service.load_explicit_linear_inputs(project, json.loads(json.dumps(project.linear_inputs.to_dict())))


def test_dependency_and_io_failures_are_typed(tmp_path, monkeypatch):
    """Review round 2: ANYhydro refusals and private-copy I/O failures cross the service boundary typed."""
    import anyhydro.hydrostatic_contract as contract_module
    import tempfile
    service, root, project, sources = _imported(tmp_path)

    def unresolved(_dataset):
        raise contract_module.HydrostaticContractError("unresolved hydrostatic contract (test)")

    with monkeypatch.context() as patch:
        patch.setattr(contract_module, "require_hydrostatic_composition", unresolved)
        with pytest.raises(ServiceError, match="unresolved hydrostatic contract"):
            service.import_rigid_hydro_inputs(project, *sources, _options(project, n_steps=401))
        with pytest.raises(ServiceError, match="unresolved hydrostatic contract"):
            service.verify_hydro_provenance(project)

    def no_temp(*args, **kwargs):
        raise PermissionError("no temporary directory (test)")

    with monkeypatch.context() as patch:
        patch.setattr(tempfile, "mkdtemp", no_temp)
        patch.setattr(tempfile, "TemporaryDirectory", no_temp)
        with pytest.raises(ServiceError, match="no temporary directory"):
            service.verify_hydro_provenance(project)
        with pytest.raises(ServiceError, match="no temporary directory"):
            service.import_rigid_hydro_inputs(project, *sources, _options(project, n_steps=401))
