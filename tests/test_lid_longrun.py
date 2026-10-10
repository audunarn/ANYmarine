"""Focused lid geometry and longer development job tests.

Covers the lid-equipped equilibrium geometry across import, immutable
snapshots, save/reopen/save-as and edit staleness, and the explicit
execution limits for longer development jobs across the common CLI/Qt/
services/child path while the default 1001-frame guard remains.

Every fixture here is an analytic development fixture; nothing in this file
is scientific qualification, and no BEM run is performed.
"""

from __future__ import annotations

import copy
import os
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
    LINEAR_MAX_FRAMES,
    LINEAR_MAX_FRAMES_CEILING,
    Body,
    BodyMesh,
    EquilibriumMetadata,
    LinearDevelopmentInputs,
    LinearExecutionLimits,
    ProjectModel,
    default_box_mesh,
)
from anymarine.hashing import input_hash, mesh_hash
from anymarine.linear import (
    LinearBindingError,
    analytic_demo_inputs,
    linear_input_hash,
    linear_result_stale,
    verify_linear_binding,
)
from anymarine.persistence import ProjectStore
from anymarine.services import ApplicationService, ServiceError

LAMBDA = 1.5
DT = 0.05
# The installed public anyhydro caps radiation kernels at 1001 time samples,
# so hydro-imported jobs are supply-bounded to 1001 frames; longer
# development jobs run through the demo/explicit inputs path.
N_TIME = 1001
N_STEPS = 201
LONG_STEPS = 1002
LONG_LIMITS = {"max_frames": 1201}
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


# ----------------------------------------------------------------- geometry


def _lid_mesh(z: float, half_width: float, half_length: float) -> BodyMesh:
    """Flat rectangular lid at local height *z* with DOWN normals."""
    positions = (
        (-half_width, -half_length, z),
        (half_width, -half_length, z),
        (half_width, half_length, z),
        (-half_width, half_length, z),
    )
    # (0, 2, 1) and (0, 3, 2) give cross products pointing in -z.
    triangles = ((0, 2, 1), (0, 3, 2))
    return BodyMesh(positions=positions, triangles=triangles)


def _lid_project() -> ProjectModel:
    """Two-body project whose lids sit exactly on the world z=0 waterplane."""
    bodies = (
        Body(
            body_id="lid-alpha",
            name="Alpha",
            reference_position=(0.0, 0.0, 0.0),
            mass=2.4e6,
            mesh=default_box_mesh((12.0, 6.0, 5.0)),
            hydro_lid=_lid_mesh(0.0, 3.0, 2.0),
        ),
        Body(
            body_id="lid-beta",
            name="Beta",
            reference_position=(12.0, 0.0, -0.5),
            mass=1.8e6,
            mesh=default_box_mesh((9.0, 5.0, 4.0)),
            # Local lid height +0.5 translates with the reference to world z=0.
            hydro_lid=_lid_mesh(0.5, 2.0, 1.5),
        ),
    )
    return ProjectModel(
        name="Lid longrun fixture",
        bodies=bodies,
        equilibrium=EquilibriumMetadata(
            waterline_z=0.0,
            description="Analytic lid test equilibrium (development fixture).",
            source="synthetic",
        ),
    )


def _coupling_matrix() -> np.ndarray:
    scale = np.linspace(1.0, 2.5, 12)
    return 400.0 * np.outer(scale, scale)


def _fixture_arrays():
    coupling = _coupling_matrix()
    n_omega = OMEGAS.size
    n_dof = 12
    a_inf = 2000.0 * coupling / 400.0 + 50.0 * np.eye(n_dof)
    added_mass = np.empty((n_omega, n_dof, n_dof))
    damping = np.empty((n_omega, n_dof, n_dof))
    for index, omega in enumerate(OMEGAS):
        added_mass[index] = a_inf - coupling / (LAMBDA**2 + omega**2)
        damping[index] = coupling * LAMBDA / (LAMBDA**2 + omega**2)
    excitation = np.empty((n_omega, DIRECTIONS.size, n_dof), dtype=np.complex128)
    for o in range(n_omega):
        for d in range(DIRECTIONS.size):
            for i in range(n_dof):
                excitation[o, d, i] = (5000.0 + 100.0 * i) * np.exp(1j * 0.3 * i)
    stiffness = np.zeros((n_dof, n_dof))
    for b in range(2):
        stiffness[2 + 6 * b, 2 + 6 * b] = RHO * GRAVITY * (72.0 if b == 0 else 45.0)
    return a_inf, added_mass, damping, excitation, stiffness


def _fixture_bodies(project: ProjectModel, *, lids: str):
    bodies = []
    for body in project.bodies:
        lid = None
        if lids == "match" and body.hydro_lid is not None:
            lid = hydro.expected_world_lid(body)
        elif lids == "mismatch" and body.hydro_lid is not None:
            # Same waterplane, different source triangles: the exact hash
            # proof must refuse it.
            lid = hydro.expected_world_lid(
                body.with_edits(hydro_lid=_lid_mesh(
                    -body.reference_position[2], 1.5, 1.0
                ))
            )
        bodies.append(
            anyhydro.HydroBody(
                body_id=body.body_id,
                name=body.name,
                mesh=hydro.expected_world_mesh(body),
                lid_mesh=lid,
                rotation_center=tuple(float(v) for v in body.reference_position),
                center_of_mass=tuple(float(v) for v in body.reference_position),
                enabled_dofs=anyhydro.body_dof_keys(body.body_id),
            )
        )
    return bodies


def _fixture_dataset(project: ProjectModel, *, lids: str):
    a_inf, added_mass, damping, excitation, stiffness = _fixture_arrays()
    bodies = _fixture_bodies(project, lids=lids)
    request = anyhydro.HydroRequest(
        omegas=OMEGAS.copy(),
        wave_directions=DIRECTIONS.copy(),
        water_depth=WATER_DEPTH,
        rho=RHO,
        gravity=GRAVITY,
        include_zero_frequency=False,
        include_infinite_frequency=False,
        backend_settings={"fixture": "lid-longrun-analytic"},
    )
    rotation_center = np.asarray(
        [body.rotation_center for body in bodies], dtype=np.float64
    )
    return anyhydro.assemble_hydro_dataset(
        bodies,
        request,
        added_mass=added_mass,
        radiation_damping=damping,
        excitation_force=excitation,
        hydrostatic_stiffness=stiffness,
        rotation_center=rotation_center,
        center_of_mass=rotation_center,
        center_of_buoyancy=rotation_center,
        added_mass_infinite=a_inf,
        added_mass_infinite_source="analytic-exponential-oracle-fixture",
        backend="analytic-test-fixture",
        backend_version="1.0.0",
        backend_commit="unknown",
        qualification_status="development_fixture",
    )


def _fixture_kernel(dataset):
    settings = anyhydro.RadiationSettings(
        dt=DT,
        n_time=N_TIME,
        symmetry_tol=1.0e-6,
        passivity_tol=1.0e-6,
        low_tail_policy="explicit_zero_sample",
        zero_frequency_damping=_coupling_matrix() / LAMBDA,
    )
    return anyhydro.radiation_kernel_from_dataset(dataset, settings)


@pytest.fixture(scope="module")
def lid_artifacts(tmp_path_factory):
    """Lid-equipped dataset/kernel files matching the lid project, once."""
    root = tmp_path_factory.mktemp("lid-artifacts")
    project = _lid_project()
    dataset = _fixture_dataset(project, lids="match")
    kernel = _fixture_kernel(dataset)
    dataset_path = root / "dataset.nc"
    kernel_path = root / "kernel.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    return {
        "project": project,
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


# ------------------------------------------------------- body lid records


def test_body_lid_round_trip_and_legacy_hash_identity(tmp_path):
    project = _lid_project()
    body = project.bodies[0]
    # Serialized identity: hydro_lid omitted when None, present when set.
    lidless = body.with_edits(hydro_lid=None)
    assert "hydro_lid" not in lidless.to_dict()
    assert "hydro_lid" in body.to_dict()
    assert Body.from_dict(body.to_dict()).hydro_lid == body.hydro_lid
    assert Body.from_dict(lidless.to_dict()).hydro_lid is None
    # with_edits carries the lid; unrelated edits preserve it.
    renamed = body.with_edits(name="Renamed")
    assert renamed.hydro_lid == body.hydro_lid
    # The project input hash covers the lid exactly like the hull: a lid
    # edit changes it, and a lidless project keeps its legacy hash.
    lidless_project = project.with_edits(
        bodies=tuple(b.with_edits(hydro_lid=None) for b in project.bodies)
    )
    assert input_hash(lidless_project) != input_hash(project)
    edited_lid = body.with_edits(hydro_lid=_lid_mesh(0.0, 2.5, 2.0))
    edited_project = project.with_edits(bodies=(edited_lid, project.bodies[1]))
    assert input_hash(edited_project) != input_hash(project)
    # Persistence round trip preserves the lid and the hash.
    root = tmp_path / "roundtrip"
    store = ProjectStore(root)
    store.save(project)
    loaded = store.load()
    assert loaded.bodies[0].hydro_lid == body.hydro_lid
    assert input_hash(loaded) == input_hash(project)


def test_expected_world_lid_proves_exact_translation():
    project = _lid_project()
    body = project.bodies[1]
    lid = hydro.expected_world_lid(body)
    # Exact vertex translation with the reference position: the world lid
    # sits on the equilibrium waterplane z=0.
    expected_vertices = np.asarray(body.hydro_lid.positions, dtype=np.float64) + np.asarray(
        body.reference_position, dtype=np.float64
    )[None, :]
    assert np.array_equal(lid.vertices, expected_vertices)
    assert float(lid.vertices[0][2]) == 0.0
    assert tuple(lid.faces[0]) == tuple(body.hydro_lid.triangles[0])
    # The neutral public mesh hash proves the same source triangle identity
    # the backend stored (body_id projected into the hash).
    neutral = anyhydro.HydroPanelMesh(
        vertices=expected_vertices,
        faces=np.asarray(body.hydro_lid.triangles, dtype=np.int32),
        face_sizes=np.full(len(body.hydro_lid.triangles), 3, dtype=np.uint8),
        body_id=body.body_id,
        outward_normals_verified=False,
        source_geometry_refs=("anymarine-body-lid+reference-position",),
        mesh_hash="",
    )
    assert lid.mesh_hash == neutral.mesh_hash


def test_expected_world_lid_typed_refusals():
    project = _lid_project()
    body = project.bodies[0]
    with pytest.raises(hydro.HydroLidGeometryError, match="no numerical lid"):
        hydro.expected_world_lid(body.with_edits(hydro_lid=None))
    # Upward normals: the geometry is never flipped to satisfy the convention.
    upward = _lid_mesh(0.0, 3.0, 2.0)
    upward = BodyMesh(positions=upward.positions, triangles=((0, 1, 2), (0, 2, 3)))
    with pytest.raises(hydro.HydroLidGeometryError, match="UPWARD"):
        hydro.expected_world_lid(body.with_edits(hydro_lid=upward))
    # Degenerate face: zero-area triangles are refused, never repaired.
    degenerate = BodyMesh(
        positions=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        triangles=((0, 1, 2), (0, 2, 3)),
    )
    with pytest.raises(hydro.HydroLidGeometryError, match="degenerate"):
        hydro.expected_world_lid(body.with_edits(hydro_lid=degenerate))
    # Tilted lid: one vertex off the waterplane.
    flat = _lid_mesh(0.0, 3.0, 2.0)
    tilted = BodyMesh(
        positions=((flat.positions[0][0], flat.positions[0][1], 0.05),)
        + tuple(flat.positions[1:]),
        triangles=flat.triangles,
    )
    with pytest.raises(hydro.HydroLidGeometryError, match="waterplane"):
        hydro.expected_world_lid(body.with_edits(hydro_lid=tilted))
    # Off-plane lid: the whole lid above the waterplane.
    with pytest.raises(hydro.HydroLidGeometryError, match="waterplane"):
        hydro.expected_world_lid(body.with_edits(hydro_lid=_lid_mesh(0.3, 3.0, 2.0)))


# ------------------------------------------------------ execution limits


def test_execution_limits_validation():
    # Frozen public record: default preserves the historical 1001 bound.
    assert LinearExecutionLimits().max_frames == LINEAR_MAX_FRAMES == 1001
    assert LinearExecutionLimits(max_frames=1201).max_frames == 1201
    assert LinearExecutionLimits.from_dict({"max_frames": 5000}).max_frames == 5000
    assert LinearExecutionLimits().to_dict() == {"max_frames": 1001}
    for bad in (True, 1.5, float("inf"), float("nan"), 0, 1, -100, 20002):
        with pytest.raises(ValueError):
            LinearExecutionLimits(max_frames=bad)
    with pytest.raises(ValueError, match="unknown"):
        LinearExecutionLimits.from_dict({"max_frames": 100, "threads": 2})
    with pytest.raises(ValueError, match="max_frames"):
        LinearExecutionLimits.from_dict({})
    with pytest.raises(ValueError, match="JSON object"):
        LinearExecutionLimits.from_dict(1001)
    # The hard ceiling is exactly 20001.
    assert LINEAR_MAX_FRAMES_CEILING == 20001
    assert LinearExecutionLimits(max_frames=20001).max_frames == 20001


def _demo_binding_inputs(project, **kwargs):
    return analytic_demo_inputs(project, **kwargs)


def test_inputs_execution_limits_default_and_explicit(tmp_path):
    project = _lid_project()
    # Legacy: no execution_limits field in serialization, default bound kept.
    legacy = _demo_binding_inputs(project)
    assert "execution_limits" not in legacy.to_dict()
    assert legacy.execution_limits is None
    assert legacy.n_steps <= LINEAR_MAX_FRAMES
    # Round trip keeps the legacy form and the exact hash.
    assert linear_input_hash(LinearDevelopmentInputs.from_dict(legacy.to_dict())) == (
        linear_input_hash(legacy)
    )
    # The default guard remains: 1002 frames are refused without limits.
    with pytest.raises(ValueError, match="exceeds the development job bound"):
        _demo_binding_inputs(project, n_steps=LONG_STEPS)
    # Explicit limits admit the longer job and enter the serialization/hash.
    longer = _demo_binding_inputs(
        project, n_steps=LONG_STEPS, execution_limits=LinearExecutionLimits(1201)
    )
    assert longer.n_steps == LONG_STEPS
    assert longer.execution_limits.max_frames == 1201
    assert longer.to_dict()["execution_limits"] == {"max_frames": 1201}
    rebuilt = LinearDevelopmentInputs.from_dict(longer.to_dict())
    assert rebuilt.execution_limits.max_frames == 1201
    assert linear_input_hash(rebuilt) == linear_input_hash(longer)
    # Explicit controls are effective inputs: different limits, different hash.
    other = _demo_binding_inputs(
        project, n_steps=LONG_STEPS, execution_limits=LinearExecutionLimits(2001)
    )
    assert linear_input_hash(other) != linear_input_hash(longer)
    # The explicit bound is enforced before allocation: a job longer than
    # the declared limits is refused at construction.
    with pytest.raises(ValueError, match="exceeds the development job bound"):
        _demo_binding_inputs(
            project, n_steps=LONG_STEPS + 1, execution_limits=LinearExecutionLimits(LONG_STEPS)
        )


# ---------------------------------------------------------- lid admission


def test_import_admits_proven_lid(tmp_path, lid_artifacts):
    service, project = _import(tmp_path, lid_artifacts, _base_options())
    inputs = project.linear_inputs
    assert inputs is not None
    alpha, beta = project.bodies
    # The binding carries the application-LOCAL lid identity.
    assert inputs.bindings[0].lid_mesh_hash == mesh_hash(alpha.hydro_lid)
    assert inputs.bindings[1].lid_mesh_hash == mesh_hash(beta.hydro_lid)
    assert "lid_mesh_hash" in inputs.to_dict()["bindings"][0]
    # The structured provenance carries the world-space lid identity.
    provenance = project.hydro_provenance
    assert provenance.lid_mesh_hashes == (
        hydro.expected_world_lid(alpha).mesh_hash,
        hydro.expected_world_lid(beta).mesh_hash,
    )
    assert provenance.to_dict()["lid_mesh_hashes"] == list(provenance.lid_mesh_hashes)
    # The input-owned evidence carries the lid identity too. The details are
    # frozen deep-immutable (lists become tuples), so the expectation is the
    # frozen tuple, matching the structured provenance record exactly.
    details = inputs.provenance.details
    assert details["lid_mesh_hashes"] == provenance.lid_mesh_hashes
    # Default guards remain: no execution limits were declared.
    assert inputs.execution_limits is None
    assert "execution_limits" not in inputs.to_dict()


def test_import_lid_refusals(tmp_path, lid_artifacts):
    project = lid_artifacts["project"]
    # Dataset lid, no project lid: typed refusal, never silently dropped.
    lidless = project.with_edits(
        bodies=tuple(b.with_edits(hydro_lid=None) for b in project.bodies)
    )
    _refusal(
        tmp_path, lid_artifacts, _base_options(), project=lidless,
        fragment="no numerical lid",
    )
    # Project lid, no dataset lid: the dataset cannot strip the lid intent.
    lidless_dataset = _fixture_dataset(project, lids="none")
    lidless_kernel = _fixture_kernel(lidless_dataset)
    dataset_path = lid_artifacts["dataset_path"].parent / "dataset-nolid.nc"
    kernel_path = lid_artifacts["kernel_path"].parent / "kernel-nolid.nc"
    anyhydro.save_hydro_dataset(lidless_dataset, dataset_path)
    anyhydro.save_radiation_kernel(lidless_kernel, kernel_path)
    _refusal(
        tmp_path, lid_artifacts, _base_options(),
        dataset_path=dataset_path, kernel_path=kernel_path,
        fragment="declares no lid mesh",
    )
    # Exact hash mismatch: lid geometry is never adopted.
    mismatch_dataset = _fixture_dataset(project, lids="mismatch")
    mismatch_kernel = _fixture_kernel(mismatch_dataset)
    mismatch_dataset_path = lid_artifacts["dataset_path"].parent / "dataset-mismatch.nc"
    mismatch_kernel_path = lid_artifacts["kernel_path"].parent / "kernel-mismatch.nc"
    anyhydro.save_hydro_dataset(mismatch_dataset, mismatch_dataset_path)
    anyhydro.save_radiation_kernel(mismatch_kernel, mismatch_kernel_path)
    _refusal(
        tmp_path, lid_artifacts, _base_options(),
        dataset_path=mismatch_dataset_path, kernel_path=mismatch_kernel_path,
        fragment="lid mesh hash",
    )


def test_lid_edit_removal_replacement_staleness(tmp_path, lid_artifacts):
    service, project = _import(tmp_path, lid_artifacts, _base_options())
    inputs = project.linear_inputs
    declared_hash = linear_input_hash(inputs)
    assert not linear_result_stale(project, declared_hash)
    alpha = project.bodies[0]
    # Lid replacement invalidates the binding exactly like a hull edit.
    replaced = project.with_edits(
        bodies=(alpha.with_edits(hydro_lid=_lid_mesh(0.0, 2.5, 2.0)), project.bodies[1])
    )
    with pytest.raises(LinearBindingError, match="lid geometry changed"):
        verify_linear_binding(replaced, inputs)
    assert linear_result_stale(replaced, declared_hash)
    with pytest.raises(ServiceError, match="lid geometry changed"):
        service.run_linear_local(replaced, inputs)
    # Lid removal invalidates the binding.
    removed = project.with_edits(
        bodies=(alpha.with_edits(hydro_lid=None), project.bodies[1])
    )
    with pytest.raises(LinearBindingError, match="lid was removed"):
        verify_linear_binding(removed, inputs)
    assert linear_result_stale(removed, declared_hash)
    # Lid addition on a lidless binding invalidates it too.
    lidless_inputs = analytic_demo_inputs(
        project.with_edits(
            bodies=tuple(b.with_edits(hydro_lid=None) for b in project.bodies)
        )
    )
    with pytest.raises(LinearBindingError, match="gained a numerical lid"):
        verify_linear_binding(project, lidless_inputs)


def test_lid_project_save_reopen_save_as(tmp_path, lid_artifacts):
    service, project = _import(tmp_path, lid_artifacts, _base_options())
    root = service.root
    inputs = project.linear_inputs
    declared_hash = linear_input_hash(inputs)
    # Save/reopen: the lid identity and hashes survive exactly.
    service.save(project)
    reopened = service.load(root)
    assert reopened.linear_inputs is not None
    assert linear_input_hash(reopened.linear_inputs) == declared_hash
    assert reopened.hydro_provenance.lid_mesh_hashes == (
        project.hydro_provenance.lid_mesh_hashes
    )
    assert reopened.bodies[0].hydro_lid == project.bodies[0].hydro_lid
    assert not linear_result_stale(reopened, declared_hash)
    # Save-as: the destination is self-contained and reproduces the hashes.
    other_root = tmp_path / "save-as"
    service.save(reopened, other_root)
    other_service = ApplicationService(other_root)
    copied = other_service.load(other_root)
    assert linear_input_hash(copied.linear_inputs) == declared_hash
    assert copied.hydro_provenance.lid_mesh_hashes == (
        project.hydro_provenance.lid_mesh_hashes
    )
    assert copied.bodies[1].hydro_lid == project.bodies[1].hydro_lid


def test_import_execution_limits_option(tmp_path, lid_artifacts):
    # Default guard remains: 1002 frames are refused without explicit limits.
    _refusal(
        tmp_path, lid_artifacts,
        _base_options(n_steps=LONG_STEPS),
        fragment="exceeds the development job bound",
    )
    # The public kernel supply bound stays fail-closed: the installed ANYhydro
    # caps radiation kernels at 1001 samples, so a 1002-frame hydro job is
    # refused on kernel duration even with explicit limits (no truncation).
    _refusal(
        tmp_path, lid_artifacts,
        _base_options(n_steps=LONG_STEPS, execution_limits=dict(LONG_LIMITS)),
        fragment="kernel duration is insufficient",
    )
    # Explicit limits are admitted for jobs inside the kernel supply and
    # travel with the inputs into the serialization and the snapshot.
    service, project = _import(
        tmp_path, lid_artifacts,
        _base_options(execution_limits=dict(LONG_LIMITS)),
    )
    inputs = project.linear_inputs
    assert inputs.n_steps == N_STEPS
    assert inputs.execution_limits.max_frames == LONG_LIMITS["max_frames"]
    assert inputs.to_dict()["execution_limits"] == LONG_LIMITS
    # The snapshot (CLI/Qt/child path) carries the limits and the lid.
    snapshot = service.linear_job_snapshot(project, inputs)
    assert snapshot["inputs"]["execution_limits"] == LONG_LIMITS
    assert snapshot["inputs"]["bindings"][0]["lid_mesh_hash"] == inputs.bindings[0].lid_mesh_hash
    # Invalid limits objects are refused before anything is imported.
    for bad in ({"max_frames": True}, {"max_frames": 1.5}, {"max_frames": 0},
                {"frames": 2000}, {"max_frames": 30000}):
        _refusal(
            tmp_path, lid_artifacts,
            _base_options(execution_limits=bad),
            fragment="execution_limits",
        )


def test_snapshot_cannot_strip_lid_or_limits(tmp_path, lid_artifacts):
    service = _service_with_project(tmp_path, lid_artifacts["project"])
    project = lid_artifacts["project"]
    inputs = analytic_demo_inputs(
        project,
        n_steps=LONG_STEPS,
        execution_limits=LinearExecutionLimits(LONG_LIMITS["max_frames"]),
    )
    snapshot = service.linear_job_snapshot(project, inputs)
    declared = snapshot["input_hash"]
    # The unmodified snapshot rebuilds exactly.
    rebuilt = LinearDevelopmentInputs.from_dict(snapshot["inputs"])
    assert linear_input_hash(rebuilt) == declared
    # Stripping the lid identity from a saved/child input changes the hash:
    # the worker refuses exactly this mismatch, so lid identity cannot be
    # stripped from child inputs.
    stripped_lid = copy.deepcopy(snapshot)
    del stripped_lid["inputs"]["bindings"][0]["lid_mesh_hash"]
    mismatch_lid = LinearDevelopmentInputs.from_dict(stripped_lid["inputs"])
    assert linear_input_hash(mismatch_lid) != declared
    # Stripping the explicit execution limits makes the child inputs
    # unconstructible: the default 1001-frame guard refuses them, so longer
    # jobs can never shed their declared bound.
    stripped_limits = copy.deepcopy(snapshot)
    del stripped_limits["inputs"]["execution_limits"]
    with pytest.raises(ValueError, match="exceeds the development job bound"):
        LinearDevelopmentInputs.from_dict(stripped_limits["inputs"])


# ------------------------------------------------- longer child / Qt jobs


@requires_solver
def test_longer_child_job_above_default_bound(tmp_path, lid_artifacts):
    """Tiny public child run just above the 1001-frame default guard.

    The installed public ANYhydro radiation builder caps radiation kernels at
    1001 time samples, so a hydro import cannot supply a 1002-frame job: it
    is refused on kernel duration even with explicit limits (covered in
    test_import_execution_limits_option). This run therefore uses explicit
    development inputs on the imported lid project: it proves the
    application limit, snapshot and worker behaviour above the default bound
    only, NOT an actual long hydro import.
    """
    service = _service_with_project(tmp_path, lid_artifacts["project"])
    project = lid_artifacts["project"]
    inputs = analytic_demo_inputs(
        project,
        n_steps=LONG_STEPS,
        execution_limits=LinearExecutionLimits(LONG_LIMITS["max_frames"]),
    )
    # The same shared production path as the GUI: demo inputs are stored as
    # the project's linear inputs before the job is submitted.
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    snapshot = service.linear_job_snapshot(project, inputs)
    workdir = tmp_path / "job"
    workdir.mkdir()
    input_path = workdir / "input.json"
    output_path = workdir / "output.json"
    service.write_job_input(snapshot, input_path)
    environment = os.environ.copy()
    src_parent = str(Path(__file__).resolve().parent.parent / "src")
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = src_parent + (
        os.pathsep + existing if existing else ""
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "anymarine.worker",
            str(input_path),
            str(output_path),
            str(workdir / "cancel"),
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=environment,
        timeout=15,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = service.load_job_output(output_path, snapshot["input_hash"], snapshot=snapshot)
    assert result.frame_count() == LONG_STEPS
    assert result.times == inputs.times
    assert result.kind == "linear_development"
    # The completed longer result is stale the moment the lid is edited.
    assert not linear_result_stale(project, result.input_hash)
    edited = project.with_edits(
        bodies=(
            project.bodies[0].with_edits(hydro_lid=_lid_mesh(0.0, 2.5, 2.0)),
            project.bodies[1],
        )
    )
    assert linear_result_stale(edited, result.input_hash)


@requires_qt
@requires_solver
def test_qt_longer_linear_job(tmp_path, lid_artifacts):
    """The Qt job path runs the same shared-service longer development job.

    Explicit development inputs above the 1001-frame default guard (the
    public kernel supply bound makes an actual long hydro import impossible
    today): this proves the Qt job/child behaviour only, NOT an actual long
    hydro import.
    """
    from PySide6.QtWidgets import QApplication

    from anymarine.qtapp.jobs import JOB_STATE_COMPLETED, JobManager

    app = QApplication.instance() or QApplication([])
    service = _service_with_project(tmp_path, lid_artifacts["project"])
    project = lid_artifacts["project"]
    inputs = analytic_demo_inputs(
        project,
        n_steps=LONG_STEPS,
        execution_limits=LinearExecutionLimits(LONG_LIMITS["max_frames"]),
    )
    # The same shared production path as the GUI's _run_linear_job.
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    manager = JobManager(service)
    results = []
    manager.result_ready.connect(lambda job_id, result: results.append(result))
    job_id = manager.submit(project, linear_inputs=inputs)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        app.processEvents()
        if manager.job(job_id).state in (JOB_STATE_COMPLETED, "failed"):
            break
        time.sleep(0.02)
    assert manager.job(job_id).state == JOB_STATE_COMPLETED, (
        manager.job(job_id).diagnostics
    )
    assert len(results) == 1
    assert results[0].frame_count() == LONG_STEPS
    manager.cancel_all()
