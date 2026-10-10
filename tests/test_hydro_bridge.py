"""Hydrodynamic application bridge tests: admission, projection, persistence.

Small analytic full-12x12 PSD coupled fixture with proven geometry and
caller-declared reconstruction criteria. Development tests only: nothing
here is scientific qualification, and the fixture is an analytic
construction, not BEM output.
"""

from __future__ import annotations

import cmath
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

import anyhydro

from conftest import requires_qt, requires_solver

from anymarine import hydro
from anymarine.domain import (
    LINEAR_SOURCE_HYDRO,
    Body,
    EquilibriumMetadata,
    ProjectModel,
    default_box_mesh,
)
from anymarine.linear import linear_input_hash, linear_result_source
from anymarine.persistence import ProjectError, ProjectLoadError, ProjectStore
from anymarine.services import ApplicationService, ServiceError

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

LAMBDA = 1.5
DT = 0.05
N_TIME = 401
N_STEPS = 201
OMEGAS = np.array([round(0.1 * k, 1) for k in range(1, 81)], dtype=np.float64)
DIRECTIONS = np.array([0.0, np.pi / 4.0, np.pi / 2.0], dtype=np.float64)
RHO = 1025.0
GRAVITY = 9.81
WATER_DEPTH = 150.0

CRITERIA = {
    "omega_min": 0.5,
    "omega_max": 4.0,
    "added_mass_abs_tol": 50.0,
    "added_mass_rel_tol": 0.05,
    "damping_abs_tol": 50.0,
    "damping_rel_tol": 0.05,
}


def _fixture_project() -> ProjectModel:
    bodies = (
        Body(
            body_id="hydro-alpha",
            name="Alpha",
            reference_position=(0.0, 0.0, 0.0),
            mass=2.4e6,
            mesh=default_box_mesh((12.0, 6.0, 5.0)),
        ),
        Body(
            body_id="hydro-beta",
            name="Beta",
            reference_position=(12.0, 0.0, -0.5),
            mass=1.8e6,
            mesh=default_box_mesh((9.0, 5.0, 4.0)),
        ),
    )
    return ProjectModel(
        name="Hydro bridge fixture",
        bodies=bodies,
        equilibrium=EquilibriumMetadata(
            waterline_z=0.0,
            description="Analytic test equilibrium (development fixture).",
            source="synthetic",
        ),
    )


def _coupling_matrix() -> np.ndarray:
    """Symmetric positive-semidefinite rank-one full-coupling matrix."""
    scale = np.linspace(1.0, 2.5, 12)
    return 400.0 * np.outer(scale, scale)


def _fixture_arrays():
    coupling = _coupling_matrix()
    n_omega = OMEGAS.size
    n_dir = DIRECTIONS.size
    n_dof = 12
    a_inf = 2000.0 * coupling / 400.0 + 50.0 * np.eye(n_dof)
    added_mass = np.empty((n_omega, n_dof, n_dof))
    damping = np.empty((n_omega, n_dof, n_dof))
    for index, omega in enumerate(OMEGAS):
        added_mass[index] = a_inf - coupling / (LAMBDA**2 + omega**2)
        damping[index] = coupling * LAMBDA / (LAMBDA**2 + omega**2)
    excitation = np.empty((n_omega, n_dir, n_dof), dtype=np.complex128)
    for o in range(n_omega):
        for d in range(n_dir):
            for i in range(n_dof):
                magnitude = 5000.0 + 100.0 * i + 50.0 * o + 200.0 * d
                phase = 0.3 * i + 0.1 * o + 0.05 * d
                excitation[o, d, i] = magnitude * np.exp(1j * phase)
    stiffness = np.zeros((n_dof, n_dof))
    for b in range(2):
        heave = 2 + 6 * b
        roll = 3 + 6 * b
        pitch = 4 + 6 * b
        stiffness[heave, heave] = RHO * GRAVITY * (72.0 if b == 0 else 45.0)
        stiffness[roll, roll] = 5.0e3
        stiffness[pitch, pitch] = 6.0e3
    return a_inf, added_mass, damping, excitation, stiffness


def _fixture_bodies(project: ProjectModel):
    bodies = []
    for body in project.bodies:
        mesh = hydro.expected_world_mesh(body)
        bodies.append(
            anyhydro.HydroBody(
                body_id=body.body_id,
                name=body.name,
                mesh=mesh,
                lid_mesh=None,
                rotation_center=tuple(float(v) for v in body.reference_position),
                # COM deliberately different from the reference: a
                # translation-only reduction does not need COM == reference.
                center_of_mass=tuple(
                    value + (0.0, 0.0, 0.4)[axis]
                    for axis, value in enumerate(body.reference_position)
                ),
                enabled_dofs=anyhydro.body_dof_keys(body.body_id),
            )
        )
    return tuple(bodies)


def _fixture_dataset(project: ProjectModel, *, asymmetric_damping=False, lid=False,
                     rotation_offset=0.0):
    a_inf, added_mass, damping, excitation, stiffness = _fixture_arrays()
    if asymmetric_damping:
        damping[:, 0, 1] += 1.0
    bodies = _fixture_bodies(project)
    if rotation_offset:
        # Shift the declared rotation centers only: the panel meshes (and so
        # the mesh hashes) are unchanged, so the refusal must come from the
        # rotation-center check, not the geometry proof.
        bodies = tuple(
            anyhydro.HydroBody(
                body_id=body.body_id,
                name=body.name,
                mesh=body.mesh,
                lid_mesh=body.lid_mesh,
                rotation_center=tuple(
                    value + (0.0, 0.0, rotation_offset)[axis]
                    for axis, value in enumerate(body.rotation_center)
                ),
                center_of_mass=body.center_of_mass,
                enabled_dofs=body.enabled_dofs,
            )
            for body in bodies
        )
    if lid:
        first = bodies[0]
        lid_mesh = anyhydro.HydroPanelMesh(
            vertices=np.asarray(first.mesh.vertices, dtype=np.float64) + (0, 0, 0.4),
            faces=np.asarray(first.mesh.faces, dtype=np.int32)[:2],
            face_sizes=np.full(2, 3, dtype=np.uint8),
            body_id=first.body_id,
            outward_normals_verified=False,
            source_geometry_refs=("test-lid",),
            mesh_hash="",
        )
        bodies = (
            anyhydro.HydroBody(
                body_id=first.body_id,
                name=first.name,
                mesh=first.mesh,
                lid_mesh=lid_mesh,
                rotation_center=first.rotation_center,
                center_of_mass=first.center_of_mass,
                enabled_dofs=first.enabled_dofs,
            ),
        ) + bodies[1:]
    request = anyhydro.HydroRequest(
        omegas=OMEGAS.copy(),
        wave_directions=DIRECTIONS.copy(),
        water_depth=WATER_DEPTH,
        rho=RHO,
        gravity=GRAVITY,
        include_zero_frequency=False,
        include_infinite_frequency=False,
        backend_settings={"fixture": "hydro-bridge-analytic"},
    )
    rotation_center = np.asarray(
        [body.rotation_center for body in bodies], dtype=np.float64
    )
    center_of_mass = np.asarray(
        [body.center_of_mass for body in bodies], dtype=np.float64
    )
    center_of_buoyancy = rotation_center - np.asarray(
        [[0.0, 0.0, 0.2], [0.0, 0.0, 0.1]], dtype=np.float64
    )
    return anyhydro.assemble_hydro_dataset(
        bodies,
        request,
        added_mass=added_mass,
        radiation_damping=damping,
        excitation_force=excitation,
        hydrostatic_stiffness=stiffness,
        rotation_center=rotation_center,
        center_of_mass=center_of_mass,
        center_of_buoyancy=center_of_buoyancy,
        added_mass_infinite=a_inf,
        added_mass_infinite_source="analytic-exponential-oracle-fixture",
        backend="analytic-test-fixture",
        backend_version="1.0.0",
        backend_commit="unknown",
        qualification_status="development_fixture",
    )


def _fixture_kernel(dataset, *, tamper_a_inf=False):
    coupling = _coupling_matrix()
    settings = anyhydro.RadiationSettings(
        dt=DT,
        n_time=N_TIME,
        symmetry_tol=1.0e-6,
        passivity_tol=1.0e-6,
        low_tail_policy="explicit_zero_sample",
        zero_frequency_damping=coupling / LAMBDA,
    )
    kernel = anyhydro.radiation_kernel_from_dataset(dataset, settings)
    if tamper_a_inf:
        import dataclasses

        tampered = np.array(kernel.added_mass_infinite, dtype=np.float64)
        tampered[0, 0] += 1.0
        kernel = dataclasses.replace(kernel, added_mass_infinite=tampered)
    return kernel


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    """Dataset + kernel files and the matching project, written once."""
    root = tmp_path_factory.mktemp("hydro-artifacts")
    project = _fixture_project()
    dataset = _fixture_dataset(project)
    kernel = _fixture_kernel(dataset)
    dataset_path = root / "dataset.nc"
    kernel_path = root / "kernel.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    return {
        "project": project,
        "dataset": dataset,
        "kernel": kernel,
        "dataset_path": dataset_path,
        "kernel_path": kernel_path,
    }


def _base_options(**overrides) -> dict:
    options = {"n_steps": N_STEPS, "criteria": dict(CRITERIA)}
    options.update(overrides)
    return options


def _service_with_project(tmp_path, project) -> ApplicationService:
    root = tmp_path / "proj"
    service = ApplicationService(root)
    service.save(project)
    return service


def _import(tmp_path, artifacts, options, project=None):
    service = _service_with_project(tmp_path, project or artifacts["project"])
    new_project = service.import_hydro_inputs(
        project or artifacts["project"],
        artifacts["dataset_path"],
        artifacts["kernel_path"],
        options,
    )
    return service, new_project


# --------------------------------------------------------------- admission


def test_import_admits_full_coupled_projection(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    inputs = project.linear_inputs
    assert inputs is not None
    assert inputs.provenance.source == LINEAR_SOURCE_HYDRO
    # ANYmarine's public label convention is single-colon (body:dof); the
    # dataset's DOF keys and the provenance record use ANYhydro's public
    # double-colon convention (body::dof). Both conventions are preserved
    # exactly and never conflated.
    assert inputs.labels == (
        "hydro-alpha:surge",
        "hydro-alpha:heave",
        "hydro-beta:surge",
        "hydro-beta:heave",
    )
    assert inputs.n_steps == N_STEPS
    assert inputs.dt == DT
    # No extra frequency-domain damping: the radiation memory supplies it.
    assert inputs.damping == ((0.0,) * 4,) * 4
    # Dry translational diagonal body masses.
    assert inputs.mass_dry[0][0] == 2.4e6
    assert inputs.mass_dry[1][1] == 2.4e6
    assert inputs.mass_dry[2][2] == 1.8e6
    assert inputs.mass_dry[3][3] == 1.8e6
    assert all(
        inputs.mass_dry[i][j] == 0.0
        for i in range(4)
        for j in range(4)
        if i != j
    )
    # Zero forcing (free decay) with explicit zero initial state.
    assert all(row == (0.0, 0.0, 0.0, 0.0) for row in inputs.forces)
    assert inputs.q0 == (0.0, 0.0, 0.0, 0.0)
    assert inputs.v0 == (0.0, 0.0, 0.0, 0.0)
    provenance = project.hydro_provenance
    assert provenance is not None
    # The provenance records the exact dataset DOF keys (double colon); the
    # inputs' own labels follow the project's single-colon convention.
    assert provenance.dof_keys == (
        "hydro-alpha::surge",
        "hydro-alpha::heave",
        "hydro-beta::surge",
        "hydro-beta::heave",
    )
    assert tuple(inputs.labels) == tuple(
        key.replace("::", ":") for key in provenance.dof_keys
    )
    assert provenance.body_ids == ("hydro-alpha", "hydro-beta")
    assert provenance.linear_input_hash == linear_input_hash(inputs)
    assert provenance.wave is None
    assert provenance.reconstruction_report["overall_pass"] is True
    assert provenance.source_qualification_status == "development_fixture"
    # The inputs carry their own immutable input-owned import evidence: the
    # full source identity and the caller criteria, covered by the input
    # hash, with no paths, timestamps or circular hashes.
    details = inputs.provenance.details
    assert details is not None
    assert details["dataset_content_hash"] == provenance.dataset_content_hash
    assert details["kernel_content_hash"] == provenance.kernel_content_hash
    assert details["request_hash"] == provenance.request_hash
    assert details["a_inf_source"] == provenance.a_inf_source
    assert details["dof_keys"] == provenance.dof_keys
    assert details["mesh_hashes"] == provenance.mesh_hashes
    assert details["criteria"]["added_mass_abs_tol"] == CRITERIA["added_mass_abs_tol"]
    assert details["criteria"]["damping_abs_tol"] == CRITERIA["damping_abs_tol"]
    assert details["reconstruction_report"]["overall_pass"] is True
    assert details["wave"] is None
    assert details["surge_link_stiffness"] == 0.0
    assert "linear_input_hash" not in details
    assert "dataset_source_path" not in details
    assert "created_utc" not in details
    service.verify_hydro_provenance(project)


def test_projection_oracle(tmp_path, artifacts):
    """Independent selector oracle: projected operators are exact submatrices."""
    _, project = _import(tmp_path, artifacts, _base_options())
    inputs = project.linear_inputs
    dataset = artifacts["dataset"]
    influenced = [str(v) for v in dataset["influenced_dof"].values.tolist()]
    radiating = [str(v) for v in dataset["radiating_dof"].values.tolist()]
    # The dataset's DOF keys use the double-colon convention; the inputs'
    # labels use the single-colon convention. Index the dataset with its
    # own keys (the provenance records them exactly).
    keys = list(project.hydro_provenance.dof_keys)
    rows = [influenced.index(key) for key in keys]
    columns = [radiating.index(key) for key in keys]
    a_inf = np.asarray(dataset["added_mass_infinite"].values)
    stiffness = np.asarray(dataset["hydrostatic_stiffness"].values)
    assert np.array_equal(np.asarray(inputs.added_mass_infinite), a_inf[rows][:, columns])
    assert np.array_equal(np.asarray(inputs.stiffness), stiffness[rows][:, columns])
    kernel = np.asarray(artifacts["kernel"].kernel)
    # Independent submatrix oracle: rows select the influenced axis and
    # columns the radiating axis of each 12x12 kernel sample.
    projected = kernel[:N_STEPS][:, rows][:, :, columns]
    assert np.array_equal(np.asarray(inputs.radiation_kernel), projected)
    # The projected submatrix retains full coupling: no zero off-diagonal
    # blocks and nonzero cross-body terms.
    a4 = np.asarray(inputs.added_mass_infinite)
    assert np.count_nonzero(a4 - np.diag(np.diag(a4))) == 12
    assert a4[0, 2] != 0.0 and a4[1, 3] != 0.0


def test_surge_link_stiffness(tmp_path, artifacts):
    _, project = _import(
        tmp_path, artifacts, _base_options(surge_link_stiffness=1250.0)
    )
    inputs = project.linear_inputs
    stiffness = np.asarray(inputs.stiffness)
    dataset = artifacts["dataset"]
    influenced = [str(v) for v in dataset["influenced_dof"].values.tolist()]
    keys = list(project.hydro_provenance.dof_keys)
    rows = [influenced.index(key) for key in keys]
    base = np.asarray(dataset["hydrostatic_stiffness"].values)[rows][:, rows]
    assert stiffness[0, 0] == base[0, 0] + 1250.0
    assert stiffness[2, 2] == base[2, 2] + 1250.0
    assert stiffness[0, 2] == base[0, 2] - 1250.0
    assert stiffness[2, 0] == base[2, 0] - 1250.0
    assert project.hydro_provenance.surge_link_stiffness == 1250.0


def test_wave_forcing_phase_and_phasor(tmp_path, artifacts):
    wave = {
        "omega": 1.0,
        "heading": 0.0,
        "amplitude": 2.5,
        "phase": 0.3,
    }
    _, project = _import(tmp_path, artifacts, _base_options(wave=wave))
    inputs = project.linear_inputs
    dataset = artifacts["dataset"]
    influenced = [str(v) for v in dataset["influenced_dof"].values.tolist()]
    keys = list(project.hydro_provenance.dof_keys)
    rows = [influenced.index(key) for key in keys]
    omega_index = int(np.nonzero(OMEGAS == 1.0)[0][0])
    direction_index = 0
    f_hat = np.asarray(
        dataset["excitation_force"].values[omega_index, direction_index, :]
    )[rows]
    # Independent per-component phasor oracle at absolute run times.
    for frame in range(N_STEPS):
        t = 0.0 + frame * DT
        expected = [
            2.5 * (f_hat[d] * cmath.exp(-1j * 1.0 * t + 1j * 0.3)).real
            for d in range(4)
        ]
        assert list(inputs.forces[frame]) == pytest.approx(expected, rel=1e-12, abs=1e-9)
    provenance = project.hydro_provenance
    assert provenance.wave["omega"] == 1.0
    assert provenance.wave["omega_index"] == omega_index
    assert provenance.wave["direction_index"] == direction_index
    assert provenance.wave["amplitude"] == 2.5
    assert provenance.wave["phase"] == 0.3


# ---------------------------------------------------------------- refusals


def _refusal(tmp_path, artifacts, options, project=None, dataset_path=None,
             kernel_path=None, fragment=""):
    service = _service_with_project(tmp_path, project or artifacts["project"])
    with pytest.raises(ServiceError) as error:
        service.import_hydro_inputs(
            project or artifacts["project"],
            dataset_path or artifacts["dataset_path"],
            kernel_path or artifacts["kernel_path"],
            options,
        )
    assert fragment in str(error.value)
    return service


def test_refuses_mesh_edit(tmp_path, artifacts):
    project = artifacts["project"]
    body = project.bodies[0]
    positions = list(body.mesh.positions)
    positions[0] = (positions[0][0] + 0.01, positions[0][1], positions[0][2])
    edited = body.with_edits(
        mesh=type(body.mesh)(positions=tuple(positions), triangles=body.mesh.triangles)
    )
    project = project.with_edits(
        bodies=(edited, project.bodies[1])
    )
    _refusal(tmp_path, artifacts, _base_options(), project=project,
             fragment="mesh hash")


def test_refuses_reference_edit(tmp_path, artifacts):
    dataset = _fixture_dataset(artifacts["project"], rotation_offset=0.25)
    dataset_path = artifacts["dataset_path"].parent / "dataset-rot.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    _refusal(tmp_path, artifacts, _base_options(), dataset_path=dataset_path,
             fragment="rotation_center")


def test_refuses_waterline(tmp_path, artifacts):
    project = artifacts["project"].with_edits(
        equilibrium=EquilibriumMetadata(waterline_z=0.5, description="shifted", source="synthetic")
    )
    _refusal(tmp_path, artifacts, _base_options(), project=project,
             fragment="waterline_z")


def test_refuses_body_order(tmp_path, artifacts):
    project = artifacts["project"]
    project = project.with_edits(bodies=(project.bodies[1], project.bodies[0]))
    _refusal(tmp_path, artifacts, _base_options(), project=project,
             fragment="body order")


def test_refuses_lid(tmp_path, artifacts):
    dataset = _fixture_dataset(artifacts["project"], lid=True)
    lid_path = artifacts["dataset_path"].parent / "dataset-lid.nc"
    anyhydro.save_hydro_dataset(dataset, lid_path)
    _refusal(tmp_path, artifacts, _base_options(), dataset_path=lid_path,
             fragment="lid")


def test_refuses_asymmetric_damping_diagnostics(tmp_path, artifacts):
    dataset = _fixture_dataset(artifacts["project"], asymmetric_damping=True)
    kernel = _fixture_kernel(dataset)
    dataset_path = artifacts["dataset_path"].parent / "dataset-asym.nc"
    kernel_path = artifacts["kernel_path"].parent / "kernel-asym.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    _refusal(tmp_path, artifacts, _base_options(), dataset_path=dataset_path,
             kernel_path=kernel_path, fragment="symmetry")


def test_refuses_kernel_from_other_dataset(tmp_path, artifacts):
    other = _fixture_dataset(artifacts["project"])
    other["excitation_force"].values[:] = 0.0
    other_kernel = _fixture_kernel(other)
    other_kernel_path = artifacts["kernel_path"].parent / "kernel-other.nc"
    anyhydro.save_radiation_kernel(other_kernel, other_kernel_path)
    _refusal(tmp_path, artifacts, _base_options(), kernel_path=other_kernel_path,
             fragment="was not built from this dataset")


def test_refuses_tampered_a_inf(tmp_path, artifacts):
    kernel = _fixture_kernel(artifacts["dataset"], tamper_a_inf=True)
    kernel_path = artifacts["kernel_path"].parent / "kernel-tampered.nc"
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    _refusal(tmp_path, artifacts, _base_options(), kernel_path=kernel_path,
             fragment="added_mass_infinite")


def test_refuses_failing_criteria(tmp_path, artifacts):
    # Both the absolute and the relative caller tolerances are tightened:
    # the admission threshold is abs_tol + rel_tol * scale, so tightening
    # only the absolute tolerance would leave the relative term dominant.
    strict = dict(CRITERIA)
    strict.update(
        added_mass_abs_tol=1.0e-9,
        added_mass_rel_tol=1.0e-9,
        damping_abs_tol=1.0e-9,
        damping_rel_tol=1.0e-9,
    )
    _refusal(tmp_path, artifacts, _base_options(criteria=strict),
             fragment="caller criteria")


def test_refuses_insufficient_kernel(tmp_path, artifacts):
    _refusal(tmp_path, artifacts, _base_options(n_steps=N_TIME + 1),
             fragment="insufficient")


@pytest.mark.parametrize(
    "wave, fragment",
    [
        ({"omega": 0.55, "heading": 0.0, "amplitude": 1.0, "phase": 0.0}, "not an exact stored dataset frequency"),
        ({"omega": 0.2, "heading": 0.0, "amplitude": 1.0, "phase": 0.0}, "outside the declared"),
        ({"omega": 1.0, "heading": 9.9, "amplitude": 1.0, "phase": 0.0}, "not an exact stored dataset heading"),
        ({"omega": 1.0, "heading": 0.0, "amplitude": -1.0, "phase": 0.0}, "nonnegative"),
        ({"omega": 1.0, "heading": 0.0, "amplitude": 1.0}, "missing required option"),
    ],
)
def test_refuses_invalid_waves(tmp_path, artifacts, wave, fragment):
    _refusal(tmp_path, artifacts, _base_options(wave=wave), fragment=fragment)


@pytest.mark.parametrize(
    "options, fragment",
    [
        ({"n_steps": N_STEPS, "criteria": dict(CRITERIA), "bogus": 1}, "unknown option"),
        ({"criteria": dict(CRITERIA)}, "n_steps"),
        ({"n_steps": N_STEPS}, "criteria"),
        ({"n_steps": 10.5, "criteria": dict(CRITERIA)}, "integer"),
        ({"n_steps": 1, "criteria": dict(CRITERIA)}, ">= 2"),
        ({"n_steps": N_STEPS, "criteria": {"omega_min": 0.5}}, "missing required option"),
    ],
)
def test_refuses_invalid_options(tmp_path, artifacts, options, fragment):
    _refusal(tmp_path, artifacts, options, fragment=fragment)


def test_refuses_without_project_root(tmp_path, artifacts):
    service = ApplicationService()
    with pytest.raises(ServiceError) as error:
        service.import_hydro_inputs(
            artifacts["project"],
            artifacts["dataset_path"],
            artifacts["kernel_path"],
            _base_options(),
        )
    assert "project root" in str(error.value)


def test_refuses_three_body_project(tmp_path, artifacts):
    project = artifacts["project"]
    extra = Body(
        body_id="hydro-gamma",
        name="Gamma",
        reference_position=(-12.0, 0.0, 0.0),
        mass=1.0e6,
        mesh=default_box_mesh((6.0, 4.0, 3.0)),
    )
    project = project.with_edits(bodies=project.bodies + (extra,))
    _refusal(tmp_path, artifacts, _base_options(), project=project,
             fragment="exactly 2 bodies")


# ------------------------------------------------- persistence and staleness


def test_persistence_round_trip_without_external_files(tmp_path, artifacts):
    # Disposable per-test copies of the shared fixture artifacts: only these
    # copies are deleted. The session-shared dataset/kernel evidence that
    # every other test imports from is never touched.
    external = tmp_path / "external"
    external.mkdir()
    dataset_copy = external / "dataset.nc"
    kernel_copy = external / "kernel.nc"
    shutil.copy2(artifacts["dataset_path"], dataset_copy)
    shutil.copy2(artifacts["kernel_path"], kernel_copy)
    service = _service_with_project(tmp_path, artifacts["project"])
    project = service.import_hydro_inputs(
        artifacts["project"], dataset_copy, kernel_copy, _base_options()
    )
    service.save(project)
    # The external source files disappear; the project must still reproduce.
    dataset_copy.unlink()
    kernel_copy.unlink()
    reloaded_service = ApplicationService()
    reloaded = reloaded_service.load(service.root)
    assert reloaded.hydro_provenance is not None
    assert reloaded.linear_inputs is not None
    assert reloaded.linear_inputs.to_dict() == project.linear_inputs.to_dict()
    assert reloaded.hydro_provenance.to_dict() == project.hydro_provenance.to_dict()
    reloaded_service.verify_hydro_provenance(reloaded)
    # The raw artifacts live inside the project, content-addressed.
    provenance = reloaded.hydro_provenance
    assert (service.root / provenance.dataset_sidecar).is_file()
    assert (service.root / provenance.kernel_sidecar).is_file()
    manifest = json.loads((service.root / "project.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 4
    assert {entry["role"] for entry in manifest["hydro_artifacts"]} == {"dataset", "kernel"}


def test_snapshot_carries_full_import_identity(tmp_path, artifacts):
    """The job snapshot embeds the immutable structured import provenance."""

    service, project = _import(tmp_path, artifacts, _base_options())
    snapshot = service.linear_job_snapshot(project)
    encoded = json.dumps(snapshot, sort_keys=True)
    provenance = project.hydro_provenance
    assert provenance.dataset_content_hash in encoded
    assert provenance.kernel_content_hash in encoded
    assert provenance.request_hash in encoded
    assert provenance.a_inf_source in encoded
    assert "added_mass_abs_tol" in encoded and "damping_abs_tol" in encoded
    assert (
        snapshot["hydro_provenance"]["dataset_content_hash"]
        == provenance.dataset_content_hash
    )
    # The input-owned details travel inside the materialised inputs and the
    # declared hash still reproduces them exactly.
    from anymarine.domain import LinearDevelopmentInputs

    rebuilt = LinearDevelopmentInputs.from_dict(snapshot["inputs"])
    assert linear_input_hash(rebuilt) == snapshot["input_hash"]
    assert rebuilt.provenance.details["criteria"]["added_mass_abs_tol"] == (
        CRITERIA["added_mass_abs_tol"]
    )
    assert rebuilt.provenance.details["dataset_content_hash"] == (
        provenance.dataset_content_hash
    )


def test_criteria_change_changes_effective_input_hash(tmp_path, artifacts):
    """The caller criteria are part of the hashed effective input identity."""

    _, project = _import(tmp_path, artifacts, _base_options())
    changed_criteria = dict(CRITERIA)
    changed_criteria["added_mass_abs_tol"] = 3.0
    _, changed = _import(
        tmp_path, artifacts, _base_options(criteria=changed_criteria)
    )
    assert (
        linear_input_hash(changed.linear_inputs)
        != linear_input_hash(project.linear_inputs)
    )


def test_save_as_is_self_contained(tmp_path, artifacts, monkeypatch):
    """A save-as project reproduces and runs without the optional backend."""

    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    destination = tmp_path / "saved-as"
    service.save(project, destination)
    import builtins

    original_import = builtins.__import__

    def no_optional_backend(name, *args, **kwargs):
        if name == "anyhydro" or name.startswith("anyhydro."):
            raise ImportError("optional backend deliberately absent in test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_optional_backend)
    reopened = service.load(destination)
    assert reopened.linear_inputs == project.linear_inputs
    assert (
        reopened.hydro_provenance.dataset_raw_sha256
        == project.hydro_provenance.dataset_raw_sha256
    )
    assert (destination / reopened.hydro_provenance.dataset_sidecar).is_file()
    assert (destination / reopened.hydro_provenance.kernel_sidecar).is_file()


@requires_solver
def test_save_as_runs_without_optional_backend(tmp_path, artifacts, monkeypatch):
    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    destination = tmp_path / "saved-as-run"
    service.save(project, destination)
    import builtins

    original_import = builtins.__import__

    def no_optional_backend(name, *args, **kwargs):
        if name == "anyhydro" or name.startswith("anyhydro."):
            raise ImportError("optional backend deliberately absent in test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_optional_backend)
    reopened = service.load(destination)
    result = service.run_linear_local(reopened)
    assert not service.result_stale(reopened, result)


def test_missing_artifact_save_refuses_before_manifest_commit(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    manifest = service.root / "project.json"
    before = manifest.read_bytes()
    (service.root / project.hydro_provenance.dataset_sidecar).unlink()
    with pytest.raises((ServiceError, ProjectError)):
        service.save(project)
    assert manifest.read_bytes() == before


def test_source_mutation_between_decode_and_raw_hash_refused(
    tmp_path, artifacts, monkeypatch
):
    """A source file rewritten after decode is refused, never adopted."""

    # Disposable per-test copy: the mutation oracle rewrites only this copy.
    source = tmp_path / "mutation-dataset.nc"
    shutil.copy2(artifacts["dataset_path"], source)
    replacement = tmp_path / "replacement.nc"
    dataset = anyhydro.load_hydro_dataset(source)
    dataset.attrs["mutation_evidence"] = "changed after decode before raw identity"
    anyhydro.save_hydro_dataset(dataset, replacement)
    service = _service_with_project(tmp_path, artifacts["project"])
    original_hash = hydro._sha256_file
    mutated = []

    def change_before_hash(path):
        if Path(path) == source and not mutated:
            shutil.copy2(replacement, source)
            mutated.append(True)
        return original_hash(path)

    monkeypatch.setattr(hydro, "_sha256_file", change_before_hash)
    with pytest.raises(ServiceError):
        service.import_hydro_inputs(
            artifacts["project"], source, artifacts["kernel_path"], _base_options()
        )
    assert mutated, "mutation oracle did not execute"
    # The project was never adopted: the saved project still has no inputs.
    assert service.load(service.root).linear_inputs is None


def test_missing_sidecar_refused(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    (service.root / project.hydro_provenance.dataset_sidecar).unlink()
    with pytest.raises(ProjectLoadError) as error:
        ApplicationService().load(service.root)
    assert "missing" in str(error.value)


def test_corrupt_sidecar_refused(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    target = service.root / project.hydro_provenance.kernel_sidecar
    payload = bytearray(target.read_bytes())
    payload[-1] ^= 0xFF
    target.write_bytes(bytes(payload))
    with pytest.raises(ProjectLoadError) as error:
        ApplicationService().load(service.root)
    assert "corrupt" in str(error.value)


def test_legacy_manifest_cannot_carry_hydro_intent(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    manifest_path = service.root / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 3
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(ProjectLoadError) as error:
        ApplicationService().load(service.root)
    assert "hydrodynamic import intent" in str(error.value)


def test_hydro_manifest_requires_provenance(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    service.save(project)
    manifest_path = service.root / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["hydro_provenance"]
    del manifest["hydro_artifacts"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(ProjectLoadError) as error:
        ApplicationService().load(service.root)
    assert "hydro_provenance" in str(error.value)


def test_staleness_after_body_edit(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    edited = project.bodies[0].with_edits(mass=2.5e6)
    edited_project = project.with_edits(bodies=(edited, project.bodies[1]))
    assert service.result_stale(edited_project, _fake_result(project)) is True


def _fake_result(project):
    """A stale-by-construction result carrying the current input hash."""
    from anymarine.domain import ResultRecord

    return ResultRecord(
        result_id="stale-probe",
        kind="linear_development",
        input_hash=linear_input_hash(project.linear_inputs),
        body_ids=tuple(b.body_id for b in project.bodies),
        times=tuple(float(t) for t in project.linear_inputs.times),
        positions={b.body_id: [(0.0, 0.0, 0.0)] * project.linear_inputs.n_steps for b in project.bodies},
        orientations={b.body_id: [(0.0, 0.0, 0.0)] * project.linear_inputs.n_steps for b in project.bodies},
        channels={},
        note="",
    )


def test_hydro_provenance_immutability(tmp_path, artifacts):
    _, project = _import(tmp_path, artifacts, _base_options())
    provenance = project.hydro_provenance
    with pytest.raises(TypeError):
        provenance.criteria["omega_min"] = 0.0
    with pytest.raises((TypeError, AttributeError)):
        provenance.assumptions.append("x")
    with pytest.raises(TypeError):
        project.linear_inputs.provenance.details["criteria"]["omega_min"] = 0.0
    with pytest.raises((AttributeError, TypeError)):
        project.linear_inputs.provenance.source = "explicit"


def test_demo_inputs_clear_hydro_provenance(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    demo = service.linear_demo_inputs(project)
    project = service.set_linear_inputs(project, demo)
    assert project.hydro_provenance is None


# ------------------------------------------------------- solver-backed paths


def _run_local(project):
    service = ApplicationService()
    return service.run_linear_local(project)


@requires_solver
def test_result_note_is_truthful_about_hydro_source(tmp_path, artifacts):
    _, project = _import(tmp_path, artifacts, _base_options())
    result = _run_local(project)
    assert linear_result_source(result) == LINEAR_SOURCE_HYDRO
    assert "ANYhydro" in result.note
    assert "not boundary-element-method output" not in result.note
    assert "unqualified" in result.note


@requires_solver
def test_replay_reproduces_after_round_trip(tmp_path, artifacts):
    service, project = _import(tmp_path, artifacts, _base_options())
    first = _run_local(project)
    service.save(project)
    reloaded = ApplicationService().load(service.root)
    second = _run_local(reloaded)
    assert first.channels == second.channels
    assert first.times == second.times
    assert first.input_hash == second.input_hash


@requires_solver
def test_cli_import_and_child_job(tmp_path, artifacts):
    root = tmp_path / "cli-proj"
    env = {
        "PYTHONPATH": str(SRC) + ";" + str(Path("C:/Github/ANYsolver/src")),
        "ANYMARINE_SOLVER_PATH": "C:/Github/ANYsolver/src",
        "QT_QPA_PLATFORM": "offscreen",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    options_path = tmp_path / "options.json"
    options_path.write_text(json.dumps(_base_options()), encoding="utf-8")

    def run_cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "anymarine.cli", *args],
            cwd=str(REPO_ROOT),
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )

    # The CLI operates on a saved project; seed it with the fixture bodies
    # (the same geometry the dataset was built from) through the service.
    ApplicationService(root).save(
        _fixture_project().with_edits(name="CLI hydro bridge")
    )
    imported = run_cli(
        "import-hydro",
        "--project", str(root),
        "--dataset", str(artifacts["dataset_path"]),
        "--kernel", str(artifacts["kernel_path"]),
        "--options", str(options_path),
    )
    assert imported.returncode == 0, imported.stderr
    assert "development source, unqualified" in imported.stdout
    run = run_cli("run-linear", "--project", str(root))
    assert run.returncode == 0, run.stderr
    assert "kind=linear_development" in run.stdout
    listing = run_cli("list-results", "--project", str(root))
    assert listing.returncode == 0, listing.stderr
    assert "STALE" not in listing.stdout
    validated = run_cli("validate", "--project", str(root))
    assert validated.returncode == 0, validated.stderr
    assert "hydro import" in validated.stdout


@requires_qt
@requires_solver
def test_qt_menu_import_and_replay_label(tmp_path, artifacts):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    # Test-only notification collection: the production modal notification
    # methods are replaced so an unexpected error is an actionable assertion
    # failure instead of a blocked offscreen dialog. Production code is
    # unchanged and still shows its warnings outside tests.
    notified = []

    def collect_notification(title, text):
        notified.append((title, text))

    window._notify_critical = collect_notification
    window._notify_warning = collect_notification
    window._notify_info = collect_notification
    root = tmp_path / "qt-proj"
    window._service.save(window._service.new_project("Qt hydro"), root)
    window.load_project(str(root))
    # Replace the project bodies with the fixture bodies (same geometry as
    # the dataset) by direct service edits and save.
    project = _fixture_project().with_edits(name="Qt hydro")
    window._service.save(project, root)
    window.load_project(str(root))

    options_path = tmp_path / "qt-options.json"
    options_path.write_text(json.dumps(_base_options()), encoding="utf-8")
    answers = iter(
        [
            str(artifacts["dataset_path"]),
            str(artifacts["kernel_path"]),
            str(options_path),
        ]
    )
    window._ask_open_file = lambda title, name_filter: next(answers)
    window._import_hydro_inputs()
    assert window._project.hydro_provenance is not None
    assert "ANYhydro" in window.statusBar().currentMessage()

    window._run_linear_job()
    deadline = time.monotonic() + 60.0
    while window._job_manager.active_jobs() and time.monotonic() < deadline:
        app.processEvents()
    assert not window._job_manager.active_jobs()
    assert window._current_result is not None
    label = window._frame_label.text()
    assert "ANYhydro dataset import" in label
    assert "not boundary-element-method" not in window._current_result.note
    # Regression: seeking used to overwrite the label with frame/time only,
    # erasing the truthful source/unqualified provenance status.
    window._on_slider_moved(1)
    label = window._frame_label.text()
    assert "ANYhydro dataset import" in label
    assert "unqualified" in label
    assert "frame " in label
    # Editing a body mass through the existing edit hook makes the stored
    # result stale; a further seek must keep the stale warning truthful.
    body = window._project.bodies[0]
    window._apply_body_edit(body.body_id, {"mass": body.mass + 1.0})
    assert window._dirty
    window._on_slider_moved(2)
    label = window._frame_label.text()
    assert "ANYhydro dataset import" in label
    assert "unqualified" in label
    assert "STALE" in label
    # No error/warning/info notification may have fired: failures are
    # asserted, never suppressed.
    assert not notified, f"unexpected GUI notifications: {notified}"
    # Discard this disposable test edit without opening a close-confirmation dialog.
    window._dirty = False
    window.close()
