"""Explicit ANYhydro derived-operator import route tests.

Small analytic full-12x12 coupled fixture with a tiny retained asymmetric
damping perturbation: the legacy raw route refuses it (symmetry
diagnostics), while the explicit derived route admits it because the
declared reciprocal-average policy corrects the retained projection and
every declared criterion passes. Development tests only: nothing here is
scientific qualification, and the fixture is an analytic construction,
not BEM output.
"""

from __future__ import annotations

import hashlib
import json
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
    HYDRO_ROUTE_DERIVED,
    LINEAR_SOURCE_HYDRO,
    Body,
    EquilibriumMetadata,
    HydroBridgeProvenance,
    ProjectModel,
    default_box_mesh,
)
from anymarine.linear import (
    LinearDevelopmentInputs,
    linear_input_hash,
    linear_result_source,
    linear_result_stale,
    verify_linear_result,
)
from anymarine.persistence import ProjectError
from anymarine.services import ApplicationService, ServiceError

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
SOLVER_SRC = Path("C:/Github/ANYsolver/src")

LAMBDA = 1.5
DT = 0.05
N_TIME = 401
LONG_TIME = 1003
N_STEPS = 201
LONG_STEPS = 1002
LONG_LIMITS = {"max_frames": 1201}
# Tiny retained asymmetric damping perturbation: strictly above the raw
# route's symmetry tolerance (so the raw route refuses) and small enough
# that the reciprocal-average corrected derived operators stay inside the
# declared passivity tolerance (so the derived route admits).
EPS = 1.2e-6
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

RETAINED = (
    "hydro-alpha::surge",
    "hydro-alpha::heave",
    "hydro-beta::surge",
    "hydro-beta::heave",
)
RETAINED_SWAPPED = (
    "hydro-alpha::heave",
    "hydro-alpha::surge",
    "hydro-beta::surge",
    "hydro-beta::heave",
)
# Full-12 indices of the retained DOFs (surge=0, heave=2 per body block).
FULL_RETAINED_ROWS = (0, 2, 6, 8)


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
        name="Derived import fixture",
        bodies=bodies,
        equilibrium=EquilibriumMetadata(
            waterline_z=0.0,
            description="Analytic test equilibrium (development fixture).",
            source="synthetic",
        ),
    )


def _coupling_matrix(scale: float = 400.0) -> np.ndarray:
    """Symmetric positive-semidefinite rank-one full-coupling matrix."""
    shape = np.linspace(1.0, 2.5, 12)
    return scale * np.outer(shape, shape)


def _fixture_arrays(scale: float = 400.0):
    coupling = _coupling_matrix(scale)
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
                center_of_mass=tuple(
                    value + (0.0, 0.0, 0.4)[axis]
                    for axis, value in enumerate(body.reference_position)
                ),
                enabled_dofs=anyhydro.body_dof_keys(body.body_id),
            )
        )
    return tuple(bodies)


def _fixture_dataset(project: ProjectModel, *, perturbed=True, scale: float = 400.0):
    a_inf, added_mass, damping, excitation, stiffness = _fixture_arrays(scale)
    if perturbed:
        # Retained b0 surge-heave asymmetry, identical at every frequency.
        damping[:, 0, 2] += EPS
    bodies = _fixture_bodies(project)
    request = anyhydro.HydroRequest(
        omegas=OMEGAS.copy(),
        wave_directions=DIRECTIONS.copy(),
        water_depth=WATER_DEPTH,
        rho=RHO,
        gravity=GRAVITY,
        include_zero_frequency=False,
        include_infinite_frequency=False,
        backend_settings={"fixture": "derived-import-analytic"},
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


def _retained_projection() -> np.ndarray:
    projection = np.zeros((12, 4), dtype=np.float64)
    for column, row in enumerate(FULL_RETAINED_ROWS):
        projection[row, column] = 1.0
    return projection


def _derived_criteria(**overrides) -> "anyhydro.DerivedOperatorCriteria":
    data = {
        "reciprocity_abs_tol": 1.0e-5,
        "reciprocity_rel_tol": 0.0,
        "reciprocity_floor": 1.0e-6,
        "correction_abs_tol": 1.0e-5,
        "correction_rel_tol": 0.0,
        "correction_floor": 1.0e-6,
        "derived_symmetry_tol": 1.0e-9,
        "damping_psd_tol": 1.0e-5,
    }
    data.update(overrides)
    return anyhydro.DerivedOperatorCriteria(**data)


def _derived_settings(
    retained=RETAINED, scales=None, criteria=None
) -> "anyhydro.DerivedOperatorSettings":
    return anyhydro.DerivedOperatorSettings(
        correction_policy="reciprocal_average",
        retained_dofs=retained,
        normalization=anyhydro.NormalizationSpec(
            name="test-si-metres",
            unit_convention="SI base units (m, kg, s)",
            scales=[1.0] * len(retained) if scales is None else scales,
        ),
        criteria=criteria if criteria is not None else _derived_criteria(),
    )


def _derived_kernel(artifact, dataset, *, n_time=N_TIME, limits=None):
    projection = _retained_projection()
    coupling = _coupling_matrix()
    settings = anyhydro.RadiationSettings(
        dt=DT,
        n_time=n_time,
        symmetry_tol=1.0e-6,
        passivity_tol=1.0e-6,
        low_tail_policy="explicit_zero_sample",
        zero_frequency_damping=projection.T @ (coupling / LAMBDA) @ projection,
        execution_limits=limits,
    )
    return anyhydro.radiation_kernel_from_derived_operators(
        artifact, settings, source_dataset=dataset
    )


@pytest.fixture(scope="module")
def derived_artifacts(tmp_path_factory):
    """Core admitted triple (raw dataset, derived operators, kernel) files."""
    root = tmp_path_factory.mktemp("derived-artifacts")
    project = _fixture_project()
    dataset = _fixture_dataset(project)
    artifact = anyhydro.derived_operators_from_dataset(dataset, _derived_settings())
    assert artifact.admitted, "fixture artifact must be admitted"
    kernel = _derived_kernel(artifact, dataset)
    assert kernel.diagnostics.damping_symmetry_violations == 0
    assert kernel.diagnostics.passivity_violations == 0
    dataset_path = root / "dataset.nc"
    operators_path = root / "operators.npz"
    kernel_path = root / "kernel.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_derived_operators(artifact, operators_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)
    return {
        "project": project,
        "dataset": dataset,
        "artifact": artifact,
        "kernel": kernel,
        "dataset_path": dataset_path,
        "operators_path": operators_path,
        "kernel_path": kernel_path,
    }


@pytest.fixture(scope="module")
def variant_artifacts(tmp_path_factory, derived_artifacts):
    """Refusal/evidence variants built once: foreign raw source, wrong
    retained order, second admitted artifact (different normalization),
    non-admitted artifact, and the raw kernel of the perturbed dataset."""
    root = tmp_path_factory.mktemp("derived-variants")
    project = derived_artifacts["project"]
    dataset = derived_artifacts["dataset"]
    foreign = _fixture_dataset(project, scale=500.0)
    foreign_path = root / "dataset-foreign.nc"
    anyhydro.save_hydro_dataset(foreign, foreign_path)
    order_artifact = anyhydro.derived_operators_from_dataset(
        dataset, _derived_settings(retained=RETAINED_SWAPPED)
    )
    order_path = root / "operators-order.npz"
    anyhydro.save_derived_operators(order_artifact, order_path)
    artifact_b = anyhydro.derived_operators_from_dataset(
        dataset, _derived_settings(scales=[2.0, 1.0, 1.0, 1.0])
    )
    assert artifact_b.admitted
    assert artifact_b.content_hash() != derived_artifacts["artifact"].content_hash()
    kernel_b = _derived_kernel(artifact_b, dataset)
    operators_b_path = root / "operators-b.npz"
    kernel_b_path = root / "kernel-b.nc"
    anyhydro.save_derived_operators(artifact_b, operators_b_path)
    anyhydro.save_radiation_kernel(kernel_b, kernel_b_path)
    strict_artifact = anyhydro.derived_operators_from_dataset(
        dataset,
        _derived_settings(criteria=_derived_criteria(damping_psd_tol=1.0e-9)),
    )
    assert not strict_artifact.admitted, "strict fixture artifact must fail PSD"
    strict_path = root / "operators-strict.npz"
    anyhydro.save_derived_operators(strict_artifact, strict_path)
    raw_kernel = anyhydro.radiation_kernel_from_dataset(
        dataset,
        anyhydro.RadiationSettings(
            dt=DT,
            n_time=N_TIME,
            symmetry_tol=1.0e-6,
            passivity_tol=1.0e-6,
            low_tail_policy="explicit_zero_sample",
            zero_frequency_damping=_coupling_matrix() / LAMBDA,
        ),
    )
    assert raw_kernel.diagnostics.damping_symmetry_violations > 0
    raw_kernel_path = root / "raw-kernel.nc"
    anyhydro.save_radiation_kernel(raw_kernel, raw_kernel_path)
    return {
        "foreign_dataset_path": foreign_path,
        "order_operators_path": order_path,
        "operators_b_path": operators_b_path,
        "kernel_b_path": kernel_b_path,
        "strict_operators_path": strict_path,
        "raw_kernel_path": raw_kernel_path,
    }


@pytest.fixture(scope="module")
def long_artifacts(tmp_path_factory, derived_artifacts):
    """Long derived kernel (1003 samples) for the explicit 1002-frame route."""
    root = tmp_path_factory.mktemp("derived-long")
    kernel = _derived_kernel(
        derived_artifacts["artifact"],
        derived_artifacts["dataset"],
        n_time=LONG_TIME,
        limits=anyhydro.RadiationExecutionLimits(max_time_samples=LONG_TIME),
    )
    path = root / "kernel-long.nc"
    anyhydro.save_radiation_kernel(kernel, path)
    return {"kernel_path": path, "kernel": kernel}


def _base_options(**overrides) -> dict:
    options = {"n_steps": N_STEPS, "criteria": dict(CRITERIA)}
    options.update(overrides)
    return options


def _service_with_project(tmp_path, project) -> ApplicationService:
    root = tmp_path / "proj"
    service = ApplicationService(root)
    service.save(project)
    return service


def _import(
    tmp_path,
    artifacts,
    options,
    project=None,
    dataset_path=None,
    operators_path=None,
    kernel_path=None,
):
    service = _service_with_project(tmp_path, project or artifacts["project"])
    new_project = service.import_derived_hydro_inputs(
        project or artifacts["project"],
        dataset_path or artifacts["dataset_path"],
        operators_path or artifacts["operators_path"],
        kernel_path or artifacts["kernel_path"],
        options,
    )
    return service, new_project


def _refusal(
    tmp_path,
    artifacts,
    options,
    project=None,
    dataset_path=None,
    operators_path=None,
    kernel_path=None,
    fragment="refused",
):
    service = _service_with_project(tmp_path, project or artifacts["project"])
    with pytest.raises(ServiceError, match=fragment):
        service.import_derived_hydro_inputs(
            project or artifacts["project"],
            dataset_path or artifacts["dataset_path"],
            operators_path or artifacts["operators_path"],
            kernel_path or artifacts["kernel_path"],
            options,
        )


# --------------------------------------------------------------- admission


def test_derived_import_admits_perturbed_source_raw_route_refuses(
    tmp_path, derived_artifacts
):
    service, project = _import(tmp_path, derived_artifacts, _base_options())
    inputs = project.linear_inputs
    provenance = project.hydro_provenance
    assert provenance is not None and provenance.derived_bundle is not None
    artifact = derived_artifacts["artifact"]
    details = inputs.provenance.details
    assert details["route"] == HYDRO_ROUTE_DERIVED
    assert details["derived_content_hash"] == artifact.content_hash()
    assert details["derived_admitted"] is True
    assert details["derived_settings"]["correction_policy"] == "reciprocal_average"
    operators_sha = hashlib.sha256(
        derived_artifacts["operators_path"].read_bytes()
    ).hexdigest()
    assert details["operators_raw_sha256"] == operators_sha
    bundle = provenance.derived_bundle
    assert bundle["derived_content_hash"] == artifact.content_hash()
    assert bundle["operators_raw_sha256"] == operators_sha
    assert bundle["operators_sidecar"] == f"hydro/{operators_sha}.npz"
    assert bundle["derived_settings"] == details["derived_settings"]
    assert bundle["derived_report"] == details["derived_report"]


def test_raw_route_still_refuses_perturbed_dataset(
    tmp_path, derived_artifacts, variant_artifacts
):
    # The legacy raw route still refuses the same perturbed dataset on its
    # own symmetry diagnostics: the derived route is a separate explicit
    # admission, never a fallback or an averaging repair of the raw route.
    service = _service_with_project(tmp_path, derived_artifacts["project"])
    with pytest.raises(ServiceError, match="symmetry"):
        service.import_hydro_inputs(
            derived_artifacts["project"],
            derived_artifacts["dataset_path"],
            variant_artifacts["raw_kernel_path"],
            _base_options(),
        )


def test_public_source_proof_refuses_foreign_raw_source(
    tmp_path, derived_artifacts, variant_artifacts
):
    # The consumer supplies the actual raw source; a foreign dataset with
    # the same geometry but different arrays fails the full rederivation
    # proof inside the public loader, before anything is consumed.
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(),
        dataset_path=variant_artifacts["foreign_dataset_path"],
        fragment="was not derived from this dataset",
    )


def test_forged_operators_bytes_refused(tmp_path, derived_artifacts):
    forged = tmp_path / "forged.npz"
    payload = bytearray(derived_artifacts["operators_path"].read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    forged.write_bytes(bytes(payload))
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(),
        operators_path=forged,
        fragment="cannot load/verify",
    )


def test_retained_dof_order_mismatch_refused(
    tmp_path, derived_artifacts, variant_artifacts
):
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(),
        operators_path=variant_artifacts["order_operators_path"],
        fragment="retained DOF order",
    )


def test_kernel_from_other_derived_artifact_refused(
    tmp_path, derived_artifacts, variant_artifacts
):
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(),
        kernel_path=variant_artifacts["kernel_b_path"],
        fragment="was not built from this derived artifact",
    )


def test_non_admitted_artifact_refused(
    tmp_path, derived_artifacts, variant_artifacts
):
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(),
        operators_path=variant_artifacts["strict_operators_path"],
        fragment="not admitted",
    )


def test_reconstruction_criteria_failure_refused(tmp_path, derived_artifacts):
    strict = dict(CRITERIA)
    strict["damping_abs_tol"] = 1.0e-12
    strict["damping_rel_tol"] = 0.0
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(criteria=strict),
        fragment="caller criteria",
    )


def test_base_import_refusal_checks(tmp_path, derived_artifacts):
    # No project root: the authoritative files cannot be copied into the
    # project, so the import is refused before anything is decoded.
    service = ApplicationService()
    with pytest.raises(ServiceError, match="requires a project root"):
        service.import_derived_hydro_inputs(
            derived_artifacts["project"],
            derived_artifacts["dataset_path"],
            derived_artifacts["operators_path"],
            derived_artifacts["kernel_path"],
            _base_options(),
        )
    # Unknown options are refused exactly like the raw route.
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(invented_option=1),
        fragment="unknown option",
    )
    # A missing source file is a typed refusal.
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(),
        operators_path=tmp_path / "does-not-exist.npz",
        fragment="cannot read",
    )


# ------------------------------------------------- effective SI arrays


def test_exact_effective_si_arrays(tmp_path, derived_artifacts):
    q0 = (0.1, -0.2, 0.3, -0.4)
    v0 = (0.01, 0.0, -0.02, 0.0)
    service, project = _import(
        tmp_path, derived_artifacts, _base_options(t0=0.5, q0=q0, v0=v0)
    )
    inputs = project.linear_inputs
    artifact = derived_artifacts["artifact"]
    kernel = derived_artifacts["kernel"]
    assert inputs.dt == DT and inputs.t0 == 0.5 and inputs.n_steps == N_STEPS
    assert inputs.q0 == q0 and inputs.v0 == v0
    # Admitted derived Ainf, unmodified projected hydrostatics, zero extra
    # frequency-domain damping: the declared normalization never rescales
    # the stored mechanical arrays.
    np.testing.assert_array_equal(
        np.asarray(inputs.added_mass_infinite),
        np.asarray(artifact.derived_added_mass_infinite),
    )
    np.testing.assert_array_equal(
        np.asarray(inputs.stiffness), np.asarray(artifact.hydrostatic_stiffness)
    )
    np.testing.assert_array_equal(np.asarray(inputs.damping), np.zeros((4, 4)))
    np.testing.assert_array_equal(
        np.asarray(inputs.radiation_kernel), np.asarray(kernel.kernel)[:N_STEPS]
    )
    assert inputs.kernel_times == tuple(
        float(value) for value in np.asarray(kernel.time)[:N_STEPS]
    )
    mass = np.asarray(inputs.mass_dry)
    assert mass[0, 0] == 2.4e6 and mass[1, 1] == 2.4e6
    assert mass[2, 2] == 1.8e6 and mass[3, 3] == 1.8e6
    np.testing.assert_array_equal(mass - np.diag(np.diag(mass)), np.zeros((4, 4)))


def test_surge_link_and_wave_phasor_exact(tmp_path, derived_artifacts):
    link = 250.0
    wave = {"omega": 1.3, "heading": 0.0, "amplitude": 0.8, "phase": 0.25}
    _, project = _import(
        tmp_path,
        derived_artifacts,
        _base_options(surge_link_stiffness=link, wave=wave),
    )
    inputs = project.linear_inputs
    artifact = derived_artifacts["artifact"]
    stiffness = np.asarray(artifact.hydrostatic_stiffness, dtype=np.float64).copy()
    stiffness[0, 0] += link
    stiffness[2, 2] += link
    stiffness[0, 2] -= link
    stiffness[2, 0] -= link
    np.testing.assert_array_equal(np.asarray(inputs.stiffness), stiffness)
    # Exact stored projected complex excitation with the shared
    # negative-exponent/time/phase convention; the forcing is computed
    # exactly once from the artifact grid (no interpolation, no double
    # counting).
    omegas = np.asarray(artifact.omegas, dtype=np.float64)
    directions = np.asarray(artifact.wave_directions, dtype=np.float64)
    omega_index = int(np.nonzero(omegas == wave["omega"])[0][0])
    direction_index = int(np.nonzero(directions == wave["heading"])[0][0])
    f_hat = np.asarray(
        artifact.excitation_force[omega_index, direction_index, :],
        dtype=np.complex128,
    )
    times = np.asarray(
        [inputs.t0 + index * DT for index in range(N_STEPS)], dtype=np.float64
    )
    phasor = np.exp(-1j * wave["omega"] * times + 1j * wave["phase"])
    expected = wave["amplitude"] * (f_hat[None, :] * phasor[:, None]).real
    np.testing.assert_allclose(
        np.asarray(inputs.forces), expected, rtol=0.0, atol=1.0e-12
    )
    details = inputs.provenance.details
    assert details["wave"]["omega"] == wave["omega"]
    assert details["wave"]["omega_index"] == omega_index
    assert details["wave"]["direction_index"] == direction_index
    # An off-grid frequency is refused: no interpolation is performed.
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(wave=dict(wave, omega=1.31)),
        fragment="not an exact stored artifact frequency",
    )


# ------------------------------------------------ provenance / ownership


def test_provenance_bundle_frozen_and_legacy_omitted(tmp_path, derived_artifacts):
    _, project = _import(tmp_path, derived_artifacts, _base_options())
    provenance = project.hydro_provenance
    with pytest.raises(Exception):
        provenance.derived_bundle = None
    data = provenance.to_dict()
    assert data["derived_bundle"]["route"] == HYDRO_ROUTE_DERIVED
    # A legacy record without the bundle keeps loading and serializes
    # without the key: old records and their serialized hashes are intact.
    legacy = {key: value for key, value in data.items() if key != "derived_bundle"}
    rebuilt = HydroBridgeProvenance.from_dict(legacy)
    assert rebuilt.derived_bundle is None
    assert "derived_bundle" not in rebuilt.to_dict()
    assert rebuilt.to_dict() == legacy


def test_stripped_or_forged_derived_evidence_refused(tmp_path, derived_artifacts):
    service, project = _import(tmp_path, derived_artifacts, _base_options())
    inputs = project.linear_inputs

    # Stripped bundle with derived-route details: partial derived intent.
    record = project.hydro_provenance.to_dict()
    del record["derived_bundle"]
    stripped_bundle = project.with_edits(
        hydro_provenance=HydroBridgeProvenance.from_dict(record)
    )
    with pytest.raises(ServiceError, match="stripped derived intent"):
        service.verify_hydro_provenance(stripped_bundle)

    # Stripped input-owned route with a forged matching input hash: the
    # details/provenance mirror still refuses the forgery.
    data = inputs.to_dict()
    del data["provenance"]["details"]["route"]
    stripped_details = LinearDevelopmentInputs.from_dict(data)
    record = project.hydro_provenance.to_dict()
    record["linear_input_hash"] = linear_input_hash(stripped_details)
    forged = project.with_edits(
        linear_inputs=stripped_details,
        hydro_provenance=HydroBridgeProvenance.from_dict(record),
    )
    with pytest.raises(ServiceError, match="derived"):
        service.verify_hydro_provenance(forged)

    # Tampered derived content hash in the input-owned details with the
    # provenance hash forged to match: a metadata-only mismatch refuses.
    data = inputs.to_dict()
    data["provenance"]["details"]["derived_content_hash"] = "0" * 64
    tampered = LinearDevelopmentInputs.from_dict(data)
    record = project.hydro_provenance.to_dict()
    record["linear_input_hash"] = linear_input_hash(tampered)
    mismatch = project.with_edits(
        linear_inputs=tampered,
        hydro_provenance=HydroBridgeProvenance.from_dict(record),
    )
    with pytest.raises(ServiceError, match="derived"):
        service.verify_hydro_provenance(mismatch)


def test_effective_operator_tamper_not_cured_by_rehash(tmp_path, derived_artifacts):
    # Changing an effective SI array and re-hashing the provenance
    # bookkeeping is refused: the effective inputs are recompiled from the
    # authoritative dataset/operators/kernel sidecars through the public
    # ANYhydro source proof and must reproduce the record's full input
    # hash. Metadata mirrors alone are not source ownership.
    service, project = _import(tmp_path, derived_artifacts, _base_options())
    for field in ("mass_dry", "added_mass_infinite", "stiffness"):
        data = project.linear_inputs.to_dict()
        data[field][0][0] += 1.0
        forged = LinearDevelopmentInputs.from_dict(data)
        record = project.hydro_provenance.to_dict()
        record["linear_input_hash"] = linear_input_hash(forged)
        candidate = project.with_edits(
            linear_inputs=forged,
            hydro_provenance=HydroBridgeProvenance.from_dict(record),
        )
        with pytest.raises(ServiceError, match="tampering"):
            service.verify_hydro_provenance(candidate)


def test_new_artifact_selects_new_hash_and_staleness(
    tmp_path, derived_artifacts, variant_artifacts
):
    project = derived_artifacts["project"]
    service_a = _service_with_project(tmp_path / "a", project)
    project_a = service_a.import_derived_hydro_inputs(
        project,
        derived_artifacts["dataset_path"],
        derived_artifacts["operators_path"],
        derived_artifacts["kernel_path"],
        _base_options(),
    )
    service_b = _service_with_project(tmp_path / "b", project)
    project_b = service_b.import_derived_hydro_inputs(
        project,
        derived_artifacts["dataset_path"],
        variant_artifacts["operators_b_path"],
        variant_artifacts["kernel_b_path"],
        _base_options(),
    )
    hash_a = linear_input_hash(project_a.linear_inputs)
    hash_b = linear_input_hash(project_b.linear_inputs)
    assert hash_a != hash_b
    # The active job's inputs are immutable: editing a body does not mutate
    # them, it only makes a stored result of the old inputs stale.
    assert not linear_result_stale(project_a, hash_a)
    edited = project_a.with_edits(
        bodies=(
            project_a.bodies[0].with_edits(mass=project_a.bodies[0].mass + 1.0),
            project_a.bodies[1],
        )
    )
    assert linear_result_stale(edited, hash_a)
    assert linear_input_hash(project_a.linear_inputs) == hash_a


def test_snapshot_carries_derived_bundle(tmp_path, derived_artifacts):
    service, project = _import(tmp_path, derived_artifacts, _base_options())
    inputs = project.linear_inputs
    snapshot = service.linear_job_snapshot(project, inputs)
    bundle = snapshot["hydro_provenance"]["derived_bundle"]
    assert bundle["derived_content_hash"] == derived_artifacts["artifact"].content_hash()
    assert bundle["operators_raw_sha256"] == inputs.provenance.details[
        "operators_raw_sha256"
    ]
    # The snapshot inputs rebuild exactly: the child/resume evidence is
    # self-contained and hash-consistent.
    rebuilt = LinearDevelopmentInputs.from_dict(snapshot["inputs"])
    assert linear_input_hash(rebuilt) == snapshot["input_hash"]


@pytest.mark.parametrize(
    "removed",
    [
        "hydro_provenance",
        "derived_bundle",
        "hydro_source_payloads",
        "both_markers",
    ],
)
def test_worker_refuses_stripped_derived_intent(tmp_path, derived_artifacts, removed):
    # Either derived-intent claim (the inputs' own provenance route or the
    # structured provenance bundle) requires the complete intent; stripping
    # one side of the claim must not bypass the worker's source proof. The
    # both_markers variant strips BOTH explicit markers and re-hashes the
    # snapshot bookkeeping (a legitimate counterexample to a hash-only
    # guard): the remaining input-owned derived evidence (derived_* fields,
    # operators raw identity) and the embedded source payloads still claim
    # the derived route, so the closed-world gate refuses instead of
    # falling back to an unproven linear solve.
    from anymarine.worker import EXIT_INVALID_INPUT, run

    service, project = _import(tmp_path, derived_artifacts, _base_options())
    snapshot = service.linear_job_snapshot(project)
    if removed == "both_markers":
        snapshot["inputs"]["provenance"]["details"].pop("route")
        snapshot["hydro_provenance"].pop("derived_bundle")
        rebuilt = LinearDevelopmentInputs.from_dict(snapshot["inputs"])
        digest = linear_input_hash(rebuilt)
        snapshot["input_hash"] = digest
        snapshot["hydro_provenance"]["linear_input_hash"] = digest
    elif removed == "derived_bundle":
        snapshot["hydro_provenance"].pop("derived_bundle")
    else:
        snapshot.pop(removed)
    input_path = tmp_path / "stripped-input.json"
    output_path = tmp_path / "stripped-output.json"
    input_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert run(input_path, output_path, None) == EXIT_INVALID_INPUT
    assert not output_path.exists()


@pytest.mark.parametrize("role", ["dataset", "operators", "kernel"])
def test_worker_refuses_inconsistent_raw_byte_identity(
    tmp_path, derived_artifacts, role
):
    # Declared raw sha256 bookkeeping is not proof: the worker hashes the
    # actual embedded payload bytes and requires the record's identity
    # before anything is decoded or recompiled.
    from anymarine.worker import EXIT_INVALID_INPUT, run

    service, project = _import(tmp_path, derived_artifacts, _base_options())
    snapshot = service.linear_job_snapshot(project)
    provenance = snapshot["hydro_provenance"]
    target = provenance["derived_bundle"] if role == "operators" else provenance
    target[role + "_raw_sha256"] = "0" * 64
    input_path = tmp_path / "bad-identity-input.json"
    output_path = tmp_path / "bad-identity-output.json"
    input_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert run(input_path, output_path, None) == EXIT_INVALID_INPUT
    assert not output_path.exists()


def test_worker_admits_unmodified_derived_snapshot(tmp_path, derived_artifacts):
    # Positive control on the same production path: the untouched snapshot
    # passes the intent gate, the frozen-record validation, the raw byte
    # identity check and the recompilation, and the job completes.
    from anymarine.worker import EXIT_SUCCESS, run

    service, project = _import(tmp_path, derived_artifacts, _base_options())
    snapshot = service.linear_job_snapshot(project)
    input_path = tmp_path / "clean-input.json"
    output_path = tmp_path / "clean-output.json"
    input_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert run(input_path, output_path, None) == EXIT_SUCCESS
    assert output_path.exists()


def test_detached_worker_refuses_rehashed_operator_forgery(tmp_path, derived_artifacts):
    # The detached snapshot carries its own authoritative source bytes; a
    # forged effective array with re-hashed snapshot bookkeeping is
    # refused by the worker even after the original files and the saved
    # sidecars disappear. The deleted originals are per-test private
    # copies: the module-scoped fixture sources are shared evidence for
    # the rest of the module and must survive this test.
    from anymarine.worker import EXIT_INVALID_INPUT, run

    sources = tmp_path / "sources"
    sources.mkdir()
    dataset_copy = sources / "dataset.nc"
    operators_copy = sources / "operators.npz"
    kernel_copy = sources / "kernel.nc"
    dataset_copy.write_bytes(derived_artifacts["dataset_path"].read_bytes())
    operators_copy.write_bytes(derived_artifacts["operators_path"].read_bytes())
    kernel_copy.write_bytes(derived_artifacts["kernel_path"].read_bytes())
    service, project = _import(
        tmp_path,
        derived_artifacts,
        _base_options(),
        dataset_path=dataset_copy,
        operators_path=operators_copy,
        kernel_path=kernel_copy,
    )
    service.save(project)
    snapshot = service.linear_job_snapshot(project)
    assert "hydro_source_payloads" in snapshot
    data = snapshot["inputs"]
    data["added_mass_infinite"][0][0] += 1.0
    forged = LinearDevelopmentInputs.from_dict(data)
    digest = linear_input_hash(forged)
    snapshot["input_hash"] = digest
    snapshot["hydro_provenance"]["linear_input_hash"] = digest
    for path in (dataset_copy, operators_copy, kernel_copy):
        path.unlink()
    for path in (service.root / "hydro").iterdir():
        path.unlink()
    input_path = tmp_path / "forged-input.json"
    output_path = tmp_path / "forged-output.json"
    input_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert run(input_path, output_path, None) == EXIT_INVALID_INPUT
    assert not output_path.exists()


# ------------------------------------------------------------ persistence


def test_roles_save_reopen_and_deleting_originals(tmp_path, derived_artifacts):
    # Import from private copies of the authoritative sources, then delete
    # the copies: the saved project keeps the exact source evidence.
    sources = tmp_path / "sources"
    sources.mkdir()
    dataset_copy = sources / "dataset.nc"
    operators_copy = sources / "operators.npz"
    kernel_copy = sources / "kernel.nc"
    dataset_copy.write_bytes(derived_artifacts["dataset_path"].read_bytes())
    operators_copy.write_bytes(derived_artifacts["operators_path"].read_bytes())
    kernel_copy.write_bytes(derived_artifacts["kernel_path"].read_bytes())
    service, project = _import(
        tmp_path,
        derived_artifacts,
        _base_options(),
        dataset_path=dataset_copy,
        operators_path=operators_copy,
        kernel_path=kernel_copy,
    )
    service.save(project)
    manifest = json.loads(
        (service.root / "project.json").read_text(encoding="utf-8")
    )
    roles = {entry["role"]: entry for entry in manifest["hydro_artifacts"]}
    assert set(roles) == {"dataset", "operators", "kernel"}
    bundle = project.hydro_provenance.derived_bundle
    assert roles["operators"]["sidecar"] == bundle["operators_sidecar"]
    assert roles["operators"]["sha256"] == bundle["operators_raw_sha256"]
    # The original source files may disappear after a successful import.
    for path in (dataset_copy, operators_copy, kernel_copy):
        path.unlink()
    reopened = ApplicationService(service.root).load(service.root)
    assert reopened.hydro_provenance.derived_bundle is not None
    ApplicationService(service.root).verify_hydro_provenance(reopened)


def test_missing_corrupt_and_traversal_sidecars(tmp_path, derived_artifacts):
    service, project = _import(tmp_path, derived_artifacts, _base_options())
    service.save(project)
    bundle = project.hydro_provenance.derived_bundle
    operators_sidecar = service.root / bundle["operators_sidecar"]
    assert operators_sidecar.is_file()
    # Missing operators sidecar: refused on reopen.
    operators_sidecar.unlink()
    with pytest.raises(ProjectError, match="missing"):
        ApplicationService(service.root).load(service.root)
    # Corrupt operators sidecar: sha256 mismatch is refused.
    service, project = _import(tmp_path / "again", derived_artifacts, _base_options())
    service.save(project)
    bundle = project.hydro_provenance.derived_bundle
    operators_sidecar = service.root / bundle["operators_sidecar"]
    operators_sidecar.write_bytes(b"corrupted-bytes")
    with pytest.raises(ProjectError, match="corrupt"):
        ApplicationService(service.root).load(service.root)
    # Traversal sidecar reference in the manifest: refused before any
    # filesystem access outside the project root.
    service, project = _import(tmp_path / "third", derived_artifacts, _base_options())
    service.save(project)
    manifest_path = service.root / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["hydro_artifacts"]:
        if entry["role"] == "operators":
            entry["sidecar"] = "../outside.npz"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProjectError):
        ApplicationService(service.root).load(service.root)


def test_save_as_copies_all_three_artifacts(tmp_path, derived_artifacts):
    service, project = _import(tmp_path, derived_artifacts, _base_options())
    service.save(project)
    other = tmp_path / "copy"
    service.save(project, other)
    bundle = project.hydro_provenance.derived_bundle
    copied = ApplicationService(other).load(other)
    assert copied.hydro_provenance.derived_bundle is not None
    assert (other / bundle["operators_sidecar"]).is_file()
    assert (other / project.hydro_provenance.dataset_sidecar).is_file()
    assert (other / project.hydro_provenance.kernel_sidecar).is_file()
    # The original root still loads independently.
    ApplicationService(service.root).load(service.root)


# ----------------------------------------------------------- local runs


def _assert_result_complete(result, inputs, *, exact_forces=True):
    verify_linear_result(result, inputs)
    assert len(result.channels) == 40
    forces = np.asarray(inputs.forces)
    for dof, label in enumerate(inputs.labels):
        external = np.asarray(result.channels[f"force_external:{label}"])
        if exact_forces:
            np.testing.assert_array_equal(external, forces[:, dof])
        else:
            np.testing.assert_allclose(external, forces[:, dof], atol=1e-9)
        total = sum(
            np.asarray(result.channels[f"force_{kind}:{label}"])
            for kind in (
                "dry_inertia",
                "added_inertia",
                "memory",
                "damping",
                "stiffness",
                "residual",
            )
        )
        scale = max(1.0, float(np.max(np.abs(external))))
        np.testing.assert_allclose(external, total, rtol=0.0, atol=1e-6 * scale)
    for body_index, binding in enumerate(inputs.bindings):
        surge = result.channels[f"q:{inputs.labels[2 * body_index]}"]
        heave = result.channels[f"q:{inputs.labels[2 * body_index + 1]}"]
        for frame in (0, 1, inputs.n_steps // 2, inputs.n_steps - 1):
            expected = (
                binding.reference_position[0] + surge[frame],
                binding.reference_position[1],
                binding.reference_position[2] + heave[frame],
            )
            assert result.positions[binding.body_id][frame] == expected


@requires_solver
def test_result_note_force_balance_and_replay_round_trip(tmp_path, derived_artifacts):
    wave = {"omega": 1.3, "heading": 0.0, "amplitude": 0.8, "phase": 0.25}
    service, project = _import(
        tmp_path,
        derived_artifacts,
        _base_options(
            wave=wave,
            q0=(0.05, 0.0, -0.05, 0.0),
            surge_link_stiffness=180.0,
        ),
    )
    inputs = project.linear_inputs
    result = service.run_linear_local(project)
    assert linear_result_source(result) == LINEAR_SOURCE_HYDRO
    assert "derived-operator import" in result.note
    assert "unqualified" in result.note
    _assert_result_complete(result, inputs)
    # Every meaningful channel carries actual nonzero content, except the
    # derived route's frequency-domain damping: the contract stores zero
    # extra damping (the dissipative content is the radiation memory
    # kernel), so the damping channel must be exactly zero.
    for name, channel in result.channels.items():
        if name.startswith("force_residual:"):
            continue
        if name.startswith("force_damping:"):
            assert not any(abs(value) > 0.0 for value in channel), name
        else:
            assert any(abs(value) > 0.0 for value in channel), name
    # Save/reopen/replay reproduces the exact channels and input hash.
    service.save(project)
    reloaded = ApplicationService(service.root).load(service.root)
    second = ApplicationService(service.root).run_linear_local(reloaded)
    assert second.channels == result.channels
    assert second.times == result.times
    assert second.input_hash == result.input_hash


# ------------------------------------------------------------- CLI route


@requires_solver
def test_cli_import_and_run(tmp_path, derived_artifacts):
    root = tmp_path / "cli-proj"
    env = {
        "PYTHONPATH": str(SRC) + ";" + str(SOLVER_SRC),
        "ANYMARINE_SOLVER_PATH": str(SOLVER_SRC),
        "QT_QPA_PLATFORM": "offscreen",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    options_path = tmp_path / "options.json"
    options = _base_options(q0=(0.05, 0.0, -0.05, 0.0))
    options_path.write_text(json.dumps(options), encoding="utf-8")

    def run_cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "anymarine.cli", *args],
            cwd=str(REPO_ROOT),
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )

    ApplicationService(root).save(
        _fixture_project().with_edits(name="CLI derived import")
    )
    imported = run_cli(
        "import-derived-hydro",
        "--project", str(root),
        "--dataset", str(derived_artifacts["dataset_path"]),
        "--operators", str(derived_artifacts["operators_path"]),
        "--kernel", str(derived_artifacts["kernel_path"]),
        "--options", str(options_path),
    )
    assert imported.returncode == 0, imported.stderr
    assert "derived artifact content hash" in imported.stdout
    assert "unqualified" in imported.stdout
    run = run_cli("run-linear", "--project", str(root))
    assert run.returncode == 0, run.stderr
    assert "kind=linear_development" in run.stdout
    listing = run_cli("list-results", "--project", str(root))
    assert listing.returncode == 0, listing.stderr
    assert "STALE" not in listing.stdout


# -------------------------------------------------------------- Qt route


@requires_qt
@requires_solver
def test_qt_derived_import_job_and_replay(tmp_path, derived_artifacts, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow

    monkeypatch.setenv("ANYMARINE_SOLVER_PATH", str(SOLVER_SRC))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    notified = []

    def collect_notification(title, text):
        notified.append((title, text))

    window._notify_critical = collect_notification
    window._notify_warning = collect_notification
    window._notify_info = collect_notification
    root = tmp_path / "qt-proj"
    project = _fixture_project().with_edits(name="Qt derived")
    window._service.save(project, root)
    window.load_project(str(root))

    options_path = tmp_path / "qt-options.json"
    wave = {"omega": 1.3, "heading": 0.0, "amplitude": 0.8, "phase": 0.25}
    options_path.write_text(
        json.dumps(_base_options(wave=wave, q0=(0.05, 0.0, -0.05, 0.0))),
        encoding="utf-8",
    )
    answers = iter(
        [
            str(derived_artifacts["dataset_path"]),
            str(derived_artifacts["operators_path"]),
            str(derived_artifacts["kernel_path"]),
            str(options_path),
        ]
    )
    window._ask_open_file = lambda title, name_filter: next(answers)
    window._import_derived_hydro_inputs()
    assert window._project.hydro_provenance is not None
    assert window._project.hydro_provenance.derived_bundle is not None
    assert "derived" in window.statusBar().currentMessage()

    window._run_linear_job()
    deadline = time.monotonic() + 60.0
    while window._job_manager.active_jobs() and time.monotonic() < deadline:
        app.processEvents()
    assert not window._job_manager.active_jobs()
    assert window._current_result is not None, "child job did not complete"
    result = window._current_result
    assert "derived-operator import" in result.note
    assert "unqualified" in result.note
    assert len(result.channels) == 40
    label = window._frame_label.text()
    assert "derived-operator import" in label
    assert "unqualified" in label
    # Replay/seek keeps the truthful derived source label.
    window._on_slider_moved(1)
    assert "derived-operator import" in window._frame_label.text()
    assert not notified, f"unexpected GUI notifications: {notified}"
    window._dirty = False
    window.close()


# ------------------------------------------------- explicit long route


def test_long_import_limit_refusals(tmp_path, derived_artifacts):
    # The default frame guard still applies to the derived route.
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(n_steps=LONG_STEPS),
        fragment="exceeds the development job bound",
    )
    # Explicit limits cannot conjure kernel duration: no truncation.
    _refusal(
        tmp_path,
        derived_artifacts,
        _base_options(n_steps=LONG_STEPS, execution_limits=dict(LONG_LIMITS)),
        fragment="kernel duration is insufficient",
    )


@requires_solver
def test_long_derived_child_job_1002_frames(
    tmp_path, derived_artifacts, long_artifacts
):
    wave = {"omega": 1.3, "heading": 0.0, "amplitude": 0.8, "phase": 0.25}
    service, project = _import(
        tmp_path,
        derived_artifacts,
        _base_options(
            n_steps=LONG_STEPS,
            execution_limits=dict(LONG_LIMITS),
            wave=wave,
            q0=(0.05, 0.0, -0.05, 0.0),
            surge_link_stiffness=180.0,
        ),
        kernel_path=long_artifacts["kernel_path"],
    )
    inputs = project.linear_inputs
    assert inputs.n_steps == LONG_STEPS
    assert inputs.execution_limits.max_frames == LONG_LIMITS["max_frames"]
    service.save(project)
    snapshot = service.linear_job_snapshot(project, inputs)
    assert snapshot["hydro_provenance"]["derived_bundle"] is not None
    workdir = tmp_path / "job"
    workdir.mkdir()
    input_path = workdir / "input.json"
    output_path = workdir / "output.json"
    service.write_job_input(snapshot, input_path)
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH", "")
    parts = [str(SRC), str(SOLVER_SRC)]
    if existing:
        parts.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(parts)
    environment["ANYMARINE_SOLVER_PATH"] = str(SOLVER_SRC)
    completed = subprocess.run(
        [
            sys.executable,
            "-m", "anymarine.worker",
            str(input_path),
            str(output_path),
            str(workdir / "cancel"),
        ],
        cwd=str(REPO_ROOT),
        env=environment,
        timeout=15,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = service.load_job_output(
        output_path, snapshot["input_hash"], snapshot=snapshot
    )
    assert result.frame_count() == LONG_STEPS
    assert result.times == inputs.times
    assert result.kind == "linear_development"
    # Actual nonzero content on all 40 channels (residual is the balance
    # remainder and is checked for smallness instead). The derived route's
    # frequency-domain damping is the one exception: the contract stores
    # zero extra damping (the dissipative content is the radiation memory
    # kernel), so those four channels must be exactly zero.
    assert len(result.channels) == 40
    for name, channel in result.channels.items():
        if name.startswith("force_residual:"):
            continue
        if name.startswith("force_damping:"):
            assert not any(abs(value) > 0.0 for value in channel), name
        else:
            assert any(abs(value) > 0.0 for value in channel), name
    # Parent-side reference checks: force balance and stored poses against q.
    _assert_result_complete(result, inputs, exact_forces=False)
    # The completed long result goes stale the moment the project changes.
    assert not linear_result_stale(project, result.input_hash)
    edited = project.with_edits(
        bodies=(
            project.bodies[0].with_edits(mass=project.bodies[0].mass + 1.0),
            project.bodies[1],
        )
    )
    assert linear_result_stale(edited, result.input_hash)
