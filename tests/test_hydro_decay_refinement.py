"""Hydro-source free-decay and paired coarse/fine refinement tests.

Analytic development tests only: the hydro datasets are analytic
exponential-oracle fixtures (honestly marked development fixtures, never
BEM output) and the analytic pairs are explicit exponential rehearsals.
Solves go through the public ``ApplicationService.run_linear_local``
route; hydro sources go through the public import routes and the public
ANYhydro loaders; paired snapshots go through the public paired
refinement module and CLI. Nothing here is scientific qualification.
"""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from conftest import requires_solver

from anymarine.domain import (
    Body,
    EquilibriumMetadata,
    LinearBodyBinding,
    LinearDevelopmentInputs,
    LinearProvenance,
    ProjectModel,
    default_box_mesh,
)
from anymarine.hashing import equilibrium_hash, mesh_hash
from anymarine.linear import linear_input_hash
from anymarine.linear_assessment import (
    AssessmentRefusalError,
    AssessmentReport,
    AssessmentStore,
    MODE_FREE_DECAY,
    MODE_REGULAR_WAVE,
    REFERENCE_ANALYTIC_EXPONENTIAL,
    REFERENCE_HYDRO_SOURCE,
    assess_result,
    read_report_artifact,
    write_report_artifact,
)
from anymarine.paired_refinement import (
    PairedMember,
    PairedRefinementError,
    PairedRefinementRefusalError,
    PairedRefinementReport,
    adjudicate_paired_snapshot,
    assess_paired_refinement,
    read_paired_artifact,
    save_paired_snapshot,
)
from anymarine.services import ApplicationService, ServiceError

# ------------------------------------------------------------- criteria

CRITERIA = {
    "force_match_relative": 1.0e-10,
    "force_match_absolute_n": 1.0e-9,
    "channel_relative": 1.0e-10,
    "channel_absolute_n": 1.0e-9,
    "balance_relative": 1.0e-10,
    "balance_absolute_n": 1.0e-9,
    "memory_oracle_relative": 1.0e-10,
    "memory_oracle_absolute_n": 1.0e-9,
    "newmark_relative": 1.0e-10,
    "newmark_absolute_m": 1.0e-12,
    "newmark_absolute_ms": 1.0e-12,
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
    "energy_scale_j": 1.0e3,
    "nearzero_energy_floor_j": 1.0e-12,
    "free_decay_external_absolute_n": 1.0e-9,
    "kernel_match_relative": 1.0e-9,
    "small_excursion_length_scale_m": 1.0,
    "impedance_relative_limit": 0.01,
    "impedance_floor_j": 1.0e-9,
    "impedance_absolute_j": 1.0e-9,
    "response_relative_limit": 0.02,
    "response_floor": 1.0e-8,
    "response_absolute": 1.0e-8,
    "refinement_relative": 0.02,
    "refinement_floor": 1.0e-8,
    "refinement_absolute": 1.0e-8,
}

HYDRO_CRITERIA = {
    "omega_min": 0.5,
    "omega_max": 4.0,
    "added_mass_abs_tol": 50.0,
    "added_mass_rel_tol": 0.05,
    "damping_abs_tol": 50.0,
    "damping_rel_tol": 0.05,
}

# Coarse/fine registered integer dt ratio and grids (exactly nested).
DT_RATIO = 2
DT_FINE = 0.04
DT_COARSE = 0.08
N_STEPS_FINE = 501
N_STEPS_COARSE = 251
KERNEL_N_TIME = 601
# Nonzero heave offsets: a hydro-source free decay must actually decay a
# measured initial energy (E0 ~ 1.3e3 J here, far above the near-zero
# floor); a trivial zero state is NOTMEASURED and a NoGo.
DECAY_Q0 = [0.0, 0.05, 0.0, 0.04]
DECAY_V0 = [0.0, 0.0, 0.0, 0.0]


# --------------------------------------------------- analytic pair helpers

ANALYTIC_ALPHA = 1.5


def _analytic_project() -> ProjectModel:
    return ProjectModel(
        name="Analytic decay pair fixture",
        bodies=(
            Body(
                body_id="body-alpha",
                name="Alpha",
                reference_position=(0.0, 0.0, 0.0),
                mass=1.2e6,
                mesh=default_box_mesh((12.0, 6.0, 5.0)),
            ),
            Body(
                body_id="body-beta",
                name="Beta",
                reference_position=(12.0, 0.0, -0.5),
                mass=0.9e6,
                mesh=default_box_mesh((9.0, 5.0, 4.0)),
            ),
        ),
        equilibrium=EquilibriumMetadata(
            waterline_z=0.0,
            description="Analytic test equilibrium (development fixture).",
            source="synthetic",
        ),
    )


def _analytic_inputs(project, *, dt, n_steps, kernel_scale, q0):
    """Explicit exponential rehearsal inputs (analytic development only).

    ``kernel_scale`` is the diagonal of the exponential radiation matrix;
    a NEGATIVE scale is an honestly active (energy-injecting) kernel — a
    development counterexample, never a physical hydrodynamic truth.
    """

    radiation = np.diag(np.asarray(kernel_scale, dtype=float))
    ainf = radiation / ANALYTIC_ALPHA + np.diag([3.0e5, 4.0e5, 2.5e5, 3.5e5])
    mass_dry = np.diag([1.2e6, 1.2e6, 0.9e6, 0.9e6])
    stiffness = np.diag([0.0, 6.0e5, 0.0, 4.5e5])
    damping = np.diag([2.0e5, 3.0e5, 1.5e5, 2.5e5])
    times = np.arange(n_steps) * dt
    kernel = radiation[None, :, :] * np.exp(-ANALYTIC_ALPHA * times)[:, None, None]
    bindings = tuple(
        LinearBodyBinding(
            body_id=body.body_id,
            name=body.name,
            reference_position=body.reference_position,
            mass=float(body.mass),
            mesh_hash=mesh_hash(body.mesh),
            lid_mesh_hash=None,
        )
        for body in project.bodies
    )
    return LinearDevelopmentInputs(
        bindings=bindings,
        dt=dt,
        t0=0.0,
        n_steps=n_steps,
        forces=tuple(tuple(0.0 for _ in range(4)) for _ in range(n_steps)),
        mass_dry=tuple(tuple(float(v) for v in row) for row in mass_dry),
        added_mass_infinite=tuple(tuple(float(v) for v in row) for row in ainf),
        damping=tuple(tuple(float(v) for v in row) for row in damping),
        stiffness=tuple(tuple(float(v) for v in row) for row in stiffness),
        radiation_kernel=tuple(
            tuple(tuple(float(v) for v in row) for row in sample)
            for sample in kernel
        ),
        kernel_times=tuple(float(value) for value in times),
        equilibrium_hash=equilibrium_hash(project.equilibrium),
        q0=tuple(float(v) for v in q0),
        v0=(0.0, 0.0, 0.0, 0.0),
        provenance=LinearProvenance(
            source="explicit",
            description="Analytic exponential rehearsal inputs (development).",
        ),
    )


def _analytic_decay_spec(case_id, inputs, *, refinement, kernel_scale):
    return {
        "case_id": case_id,
        "mode": MODE_FREE_DECAY,
        "input_hash": linear_input_hash(inputs),
        "result_id": None,
        "reference": {
            "kind": REFERENCE_ANALYTIC_EXPONENTIAL,
            "coefficient_matrix": [
                [float(scale), 0.0, 0.0, 0.0]
                for scale in kernel_scale
            ],
            "decay": ANALYTIC_ALPHA,
        },
        "omega": None,
        "force_phasors": None,
        "windows": None,
        "fit_controls": {
            "intercepts": True,
            "drift": "none",
            "max_condition": 1.0e12,
        },
        "criteria": dict(CRITERIA),
        "refinement": refinement,
        "scope": {"development_only": True, "role": "analytic_rehearsal_only"},
    }


def _analytic_pair(tmp_path, *, dt_coarse, n_coarse, dt_fine, n_fine,
                   kernel_scale, q0=(0.0, 0.05, 0.0, 0.04)):
    """Build an analytic coarse/fine pair; cases prepared BEFORE solving.

    Two separate projects (one per member, like the hydro pair): a
    project holds exactly one stored linear inputs set.
    """

    service_coarse = ApplicationService(tmp_path / "coarse-project")
    project = _analytic_project()
    inputs_coarse = _analytic_inputs(
        project, dt=dt_coarse, n_steps=n_coarse,
        kernel_scale=kernel_scale, q0=q0,
    )
    project_coarse = service_coarse.set_linear_inputs(
        project, inputs_coarse
    )
    service_coarse.save(project_coarse)

    service_fine = ApplicationService(tmp_path / "fine-project")
    inputs_fine = _analytic_inputs(
        project, dt=dt_fine, n_steps=n_fine,
        kernel_scale=kernel_scale, q0=q0,
    )
    project_fine = service_fine.set_linear_inputs(project, inputs_fine)
    service_fine.save(project_fine)

    case_fine = service_fine.prepare_linear_assessment(
        project_fine,
        _analytic_decay_spec(
            "analytic-fine",
            inputs_fine,
            refinement={
                "partner_case_hash": None,
                "dt_ratio": DT_RATIO,
                "role": "fine",
            },
            kernel_scale=kernel_scale,
        ),
    )
    case_coarse = service_coarse.prepare_linear_assessment(
        project_coarse,
        _analytic_decay_spec(
            "analytic-coarse",
            inputs_coarse,
            refinement={
                "partner_case_hash": case_fine.case_hash,
                "dt_ratio": DT_RATIO,
                "role": "coarse",
            },
            kernel_scale=kernel_scale,
        ),
    )
    result_coarse = service_coarse.run_linear_local(project_coarse)
    project_coarse = service_coarse.add_result(project_coarse, result_coarse)
    service_coarse.save(project_coarse)
    result_fine = service_fine.run_linear_local(project_fine)
    project_fine = service_fine.add_result(project_fine, result_fine)
    service_fine.save(project_fine)
    return {
        "service_coarse": service_coarse,
        "service_fine": service_fine,
        "project_coarse": project_coarse,
        "project_fine": project_fine,
        "inputs_coarse": inputs_coarse,
        "inputs_fine": inputs_fine,
        "case_coarse": case_coarse,
        "case_fine": case_fine,
        "result_coarse": result_coarse,
        "result_fine": result_fine,
    }


# ------------------------------------------------------ hydro fixtures

def _hydro_project():
    return ProjectModel(
        name="Hydro decay fixture",
        bodies=(
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
        ),
        equilibrium=EquilibriumMetadata(
            waterline_z=0.0,
            description="Analytic test equilibrium (development fixture).",
            source="synthetic",
        ),
    )


def _hydro_fixture_arrays(scale=400.0, eps=1.2e-6):
    omegas = np.array([round(0.1 * k, 1) for k in range(1, 81)])
    shape = np.linspace(1.0, 2.5, 12)
    coupling = scale * np.outer(shape, shape)
    a_inf = 2000.0 * coupling / 400.0 + 50.0 * np.eye(12)
    added_mass = np.empty((omegas.size, 12, 12))
    damping = np.empty((omegas.size, 12, 12))
    lam = 1.5
    for index, omega in enumerate(omegas):
        added_mass[index] = a_inf - coupling / (lam**2 + omega**2)
        damping[index] = coupling * lam / (lam**2 + omega**2)
    damping[:, 0, 2] += eps
    stiffness = np.zeros((12, 12))
    for b in range(2):
        stiffness[2 + 6 * b, 2 + 6 * b] = 1025.0 * 9.81 * (72.0 if b == 0 else 45.0)
        stiffness[3 + 6 * b, 3 + 6 * b] = 5.0e3
        stiffness[4 + 6 * b, 4 + 6 * b] = 6.0e3
    return a_inf, added_mass, damping, stiffness, coupling


def _hydro_dataset(project, *, eps=1.2e-6):
    import anyhydro

    from anymarine import hydro

    a_inf, added_mass, damping, stiffness, _ = _hydro_fixture_arrays(eps=eps)
    excitation = np.empty((80, 3, 12), dtype=np.complex128)
    for o in range(80):
        for d in range(3):
            for i in range(12):
                magnitude = 5000.0 + 100.0 * i + 50.0 * o + 200.0 * d
                phase = 0.3 * i + 0.1 * o + 0.05 * d
                excitation[o, d, i] = magnitude * np.exp(1j * phase)
    bodies = []
    for body in project.bodies:
        bodies.append(
            anyhydro.HydroBody(
                body_id=body.body_id,
                name=body.name,
                mesh=hydro.expected_world_mesh(body),
                lid_mesh=None,
                rotation_center=tuple(float(v) for v in body.reference_position),
                center_of_mass=tuple(
                    value + (0.0, 0.0, 0.4)[axis]
                    for axis, value in enumerate(body.reference_position)
                ),
                enabled_dofs=anyhydro.body_dof_keys(body.body_id),
            )
        )
    request = anyhydro.HydroRequest(
        omegas=np.array([round(0.1 * k, 1) for k in range(1, 81)]),
        wave_directions=np.array([0.0, np.pi / 4.0, np.pi / 2.0]),
        water_depth=150.0,
        rho=1025.0,
        gravity=9.81,
        include_zero_frequency=False,
        include_infinite_frequency=False,
        backend_settings={"fixture": "decay-refinement-analytic"},
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
        tuple(bodies),
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


def _hydro_derived_settings():
    import anyhydro

    return anyhydro.DerivedOperatorSettings(
        correction_policy="reciprocal_average",
        retained_dofs=(
            "hydro-alpha::surge",
            "hydro-alpha::heave",
            "hydro-beta::surge",
            "hydro-beta::heave",
        ),
        normalization=anyhydro.NormalizationSpec(
            name="test-si-metres",
            unit_convention="SI base units (m, kg, s)",
            scales=[1.0, 1.0, 1.0, 1.0],
        ),
        criteria=anyhydro.DerivedOperatorCriteria(
            reciprocity_abs_tol=1.0e-5,
            reciprocity_rel_tol=0.0,
            reciprocity_floor=1.0e-6,
            correction_abs_tol=1.0e-5,
            correction_rel_tol=0.0,
            correction_floor=1.0e-6,
            derived_symmetry_tol=1.0e-9,
            damping_psd_tol=1.0e-5,
        ),
    )


def _hydro_kernel(artifact, dataset, *, dt, zfd_divisor=1.5,
                   n_time=KERNEL_N_TIME):
    import anyhydro

    projection = np.zeros((12, 4))
    for column, row in enumerate((0, 2, 6, 8)):
        projection[row, column] = 1.0
    _, _, _, _, coupling = _hydro_fixture_arrays()
    settings = anyhydro.RadiationSettings(
        dt=dt,
        n_time=n_time,
        symmetry_tol=1.0e-6,
        passivity_tol=1.0e-6,
        low_tail_policy="explicit_zero_sample",
        zero_frequency_damping=projection.T @ (coupling / zfd_divisor) @ projection,
    )
    return anyhydro.radiation_kernel_from_derived_operators(
        artifact, settings, source_dataset=dataset
    )


def _decay_case_spec(project, *, role, partner, route="derived_operators",
                     reference=None, mode=MODE_FREE_DECAY, refinement=None):
    inputs = project.linear_inputs
    provenance = project.hydro_provenance
    if reference is None:
        reference = {
            "kind": REFERENCE_HYDRO_SOURCE,
            "route": route,
            "dataset_content_hash": provenance.dataset_content_hash,
        }
        if route == "derived_operators":
            bundle = provenance.derived_bundle
            reference["derived_content_hash"] = bundle["derived_content_hash"]
            reference["operators_raw_sha256"] = bundle["operators_raw_sha256"]
    return {
        "case_id": f"hydro-decay-{role}",
        "mode": mode,
        "input_hash": linear_input_hash(inputs),
        "result_id": None,
        "reference": reference,
        "omega": None,
        "force_phasors": None,
        "windows": None,
        "fit_controls": {
            "intercepts": True,
            "drift": "none",
            "max_condition": 1.0e12,
        },
        "criteria": dict(CRITERIA),
        "refinement": refinement,
        "scope": {"development_only": True, "role": "analytic_rehearsal_only"},
    }


def _decay_refinement(role, partner):
    return {
        "partner_case_hash": partner,
        "dt_ratio": DT_RATIO,
        "role": role,
    }


@pytest.fixture(scope="module")
def decay_pair(tmp_path_factory):
    """Derived-route hydro free-decay coarse/fine pair.

    Same dataset/derived operators for both members; the kernels are the
    SAME physical kernel sampled on exactly nested lag grids (dt 0.08 /
    0.04, registered integer ratio 2). The cases are prepared BEFORE any
    solve; the runs go through the public local solver route.
    """

    import anyhydro

    root = tmp_path_factory.mktemp("decay-pair")
    project = _hydro_project()
    dataset = _hydro_dataset(project)
    artifact = anyhydro.derived_operators_from_dataset(
        dataset, _hydro_derived_settings()
    )
    assert artifact.admitted
    kernel_fine = _hydro_kernel(artifact, dataset, dt=DT_FINE)
    kernel_coarse = _hydro_kernel(artifact, dataset, dt=DT_COARSE)
    dataset_path = root / "dataset.nc"
    operators_path = root / "operators.npz"
    fine_kernel_path = root / "kernel-fine.nc"
    coarse_kernel_path = root / "kernel-coarse.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_derived_operators(artifact, operators_path)
    anyhydro.save_radiation_kernel(kernel_fine, fine_kernel_path)
    anyhydro.save_radiation_kernel(kernel_coarse, coarse_kernel_path)

    members = {}
    for role, kernel_path, n_steps in (
        ("fine", fine_kernel_path, N_STEPS_FINE),
        ("coarse", coarse_kernel_path, N_STEPS_COARSE),
    ):
        service = ApplicationService(root / f"{role}-project")
        service.save(project)
        options = {
            "n_steps": n_steps,
            "criteria": dict(HYDRO_CRITERIA),
            "q0": list(DECAY_Q0),
            "v0": list(DECAY_V0),
        }
        member = service.import_derived_hydro_inputs(
            project, dataset_path, operators_path, kernel_path, options
        )
        service.save(member)
        members[role] = {"service": service, "project": member}

    # Cases registered BEFORE any rehearsal solve.
    fine = members["fine"]
    coarse = members["coarse"]
    case_fine = fine["service"].prepare_linear_assessment(
        fine["project"],
        _decay_case_spec(
            fine["project"], role="fine", partner=None,
            refinement=_decay_refinement("fine", None),
        ),
    )
    case_coarse = coarse["service"].prepare_linear_assessment(
        coarse["project"],
        _decay_case_spec(
            coarse["project"], role="coarse", partner=case_fine.case_hash,
            refinement=_decay_refinement("coarse", case_fine.case_hash),
        ),
    )
    for role, data, case in (
        ("fine", fine, case_fine),
        ("coarse", coarse, case_coarse),
    ):
        result = data["service"].run_linear_local(data["project"])
        data["project"] = data["service"].add_result(data["project"], result)
        data["service"].save(data["project"])
        data["case"] = case
        data["result"] = result
        data["report"] = data["service"].assess_linear_result(
            data["project"], result, case
        )
    return {
        "root": root,
        "dataset_path": dataset_path,
        "operators_path": operators_path,
        "fine_kernel_path": fine_kernel_path,
        "coarse_kernel_path": coarse_kernel_path,
        "fine": fine,
        "coarse": coarse,
    }


def _member(data, role):
    return PairedMember(
        data[role]["project"],
        data[role]["result"],
        data[role]["case"],
        project_root=data[role]["service"].root,
    )


@pytest.fixture(scope="module")
def raw_decay(tmp_path_factory):
    """Raw-route hydro free-decay single member (public import route)."""

    import anyhydro

    root = tmp_path_factory.mktemp("raw-decay")
    project = _hydro_project()
    dataset = _hydro_dataset(project, eps=0.0)
    _, _, _, _, coupling = _hydro_fixture_arrays(eps=0.0)
    kernel = anyhydro.radiation_kernel_from_dataset(
        dataset,
        anyhydro.RadiationSettings(
            dt=0.05,
            n_time=KERNEL_N_TIME,
            symmetry_tol=1.0e-6,
            passivity_tol=1.0e-6,
            low_tail_policy="explicit_zero_sample",
            zero_frequency_damping=coupling / 1.5,
        ),
    )
    assert kernel.diagnostics.damping_symmetry_violations == 0
    dataset_path = root / "dataset.nc"
    kernel_path = root / "kernel.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)

    service = ApplicationService(root / "project")
    service.save(project)
    options = {
        "n_steps": 401,
        "criteria": dict(HYDRO_CRITERIA),
        "q0": list(DECAY_Q0),
        "v0": list(DECAY_V0),
    }
    project = service.import_hydro_inputs(project, dataset_path, kernel_path, options)
    service.save(project)
    case = service.prepare_linear_assessment(
        project,
        _decay_case_spec(
            project, role="raw", partner=None, route="raw_dataset",
            refinement=None,
        ),
    )
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)
    report = service.assess_linear_result(project, result, case)
    return {
        "root": root,
        "dataset_path": dataset_path,
        "kernel_path": kernel_path,
        "service": service,
        "project": project,
        "case": case,
        "result": result,
        "report": report,
    }


# ------------------------------------------------------------- pass flows

@requires_solver
def test_hydro_source_free_decay_members_pass(decay_pair):
    """Both hydro-source free-decay members pass their full assessments."""

    for role in ("coarse", "fine"):
        data = decay_pair[role]
        report = data["report"]
        assert report.verdict == "pass", (role, report.failed)
        source = report.arrays["reference_source"]
        assert source["kind"] == "hydro_source"
        assert source["route"] == "derived_operators"
        assert source["wave"] is None
        assert data["project"].hydro_provenance.wave is None
        forces = np.asarray(data["project"].linear_inputs.forces, dtype=float)
        assert np.all(forces == 0.0)
        measured = [
            check
            for check in report.checks
            if check["name"] == "initial_energy_measured"
        ]
        assert len(measured) == 1
        assert measured[0]["passed"] is True
        assert measured[0]["value"] > measured[0]["limit"]


@requires_solver
def test_raw_route_free_decay_pass(raw_decay):
    """Raw-route hydro free decay passes with the read-only recompile proof."""

    report = raw_decay["report"]
    assert report.verdict == "pass", report.failed
    source = report.arrays["reference_source"]
    assert source["route"] == "raw_dataset"
    assert source["wave"] is None
    measured = [
        check
        for check in report.checks
        if check["name"] == "initial_energy_measured"
    ]
    assert measured[0]["passed"] is True


@requires_solver
def test_paired_refinement_combined_pass(decay_pair):
    """The explicit coarse/fine pair combines to PASS only through both."""

    coarse = decay_pair["coarse"]
    fine = decay_pair["fine"]
    service = coarse["service"]
    paired = service.assess_paired_refinement(
        coarse["project"], coarse["result"], coarse["case"], service.root,
        fine["project"], fine["result"], fine["case"],
        fine["service"].root,
    )
    assert paired.combined_verdict == "pass", paired.failed
    assert paired.dt_ratio == DT_RATIO
    assert paired.members["coarse"]["verdict"] == "pass"
    assert paired.members["fine"]["verdict"] == "pass"
    hydro = paired.pairing["hydro_source"]
    provenance = coarse["project"].hydro_provenance
    assert hydro["dataset_content_hash"] == provenance.dataset_content_hash
    assert hydro["wave"] is None
    assert hydro["route"] == "derived_operators"
    # The SAME physical kernel: shared-lag values agree within the tight
    # roundoff identity in the kernel's own units (here bit-for-bit).
    assert (
        paired.pairing["max_shared_lag_kernel_deviation_kg_s2"]
        <= 1.0e-12
    )
    assert paired.pairing["max_shared_lag_time_deviation_s"] <= 1.0e-12


@requires_solver
@requires_solver
def test_same_hydro_source_compares_frozen_regular_wave_selections(decay_pair):
    """Regular-wave members carry a frozen (read-only) wave selection: equal ones pair, different ones refuse."""
    import dataclasses
    from types import MappingProxyType, SimpleNamespace
    from anymarine.paired_refinement import PairedRefinementRefusalError, _same_hydro_source

    def member(role, wave):
        project = decay_pair[role]["project"]
        provenance = dataclasses.replace(project.hydro_provenance, wave=wave)
        assert isinstance(provenance.wave, MappingProxyType)
        return SimpleNamespace(project=SimpleNamespace(hydro_provenance=provenance,
                                                       linear_inputs=project.linear_inputs))

    wave = dict(omega=2.0, heading=0.0, amplitude=0.02, phase=0.3)
    identity = _same_hydro_source(member("coarse", wave), member("fine", dict(wave)))
    assert identity["wave"] == wave
    json.dumps(identity, allow_nan=False)
    with pytest.raises(PairedRefinementRefusalError, match="wave selections differ"):
        _same_hydro_source(member("coarse", wave), member("fine", dict(wave, phase=0.4)))


def test_zero_energy_hydro_free_decay_notmeasured(decay_pair):
    """A zero-state hydro free decay is NOTMEASURED, never a trivial PASS."""

    service = ApplicationService(decay_pair["root"] / "zero-project")
    project = _hydro_project()
    service.save(project)
    options = {
        "n_steps": N_STEPS_COARSE,
        "criteria": dict(HYDRO_CRITERIA),
        "q0": [0.0, 0.0, 0.0, 0.0],
        "v0": [0.0, 0.0, 0.0, 0.0],
    }
    project = service.import_derived_hydro_inputs(
        project,
        decay_pair["dataset_path"],
        decay_pair["operators_path"],
        decay_pair["coarse_kernel_path"],
        options,
    )
    service.save(project)
    case = service.prepare_linear_assessment(
        project,
        _decay_case_spec(
            project, role="zero", partner=None, refinement=None
        ),
    )
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "nogo"
    assert "initial_energy_measured" in report.failed
    measured = [
        check
        for check in report.checks
        if check["name"] == "initial_energy_measured"
    ][0]
    assert measured["value"] == 0.0
    assert "NOTMEASURED" in measured["detail"]


# ------------------------------------------------------------ refusals

@requires_solver
def test_hydro_source_regular_wave_mode_refused(decay_pair):
    """A hydro_source reference is source-only FREE DECAY, never RAO."""

    coarse = decay_pair["coarse"]
    spec = _decay_case_spec(
        coarse["project"], role="rw", partner=None, refinement=None,
        mode=MODE_REGULAR_WAVE,
    )
    spec["omega"] = 1.5
    spec["force_phasors"] = [[1.0e5, 0.0]] * 4
    spec["windows"] = {"start": 9.0, "mid": 14.2, "end": 19.4}
    with pytest.raises(ServiceError, match="free_decay case"):
        coarse["service"].prepare_linear_assessment(coarse["project"], spec)


@requires_solver
def test_hydro_source_missing_project_context_refused(decay_pair):
    """No actual project context: no source proof, no assessment."""

    coarse = decay_pair["coarse"]
    inputs = coarse["project"].linear_inputs
    with pytest.raises(AssessmentRefusalError, match="actual ProjectModel"):
        assess_result(coarse["case"], coarse["result"], inputs)


@requires_solver
def test_raw_sidecar_tamper_and_missing_refused(raw_decay):
    """Tampered or missing authoritative sidecars are typed refusals."""

    service = raw_decay["service"]
    project = raw_decay["project"]
    provenance = project.hydro_provenance
    kernel_path = service.root / provenance.kernel_sidecar
    backup = kernel_path.read_bytes()

    kernel_path.write_bytes(backup + b"\x00")
    with pytest.raises(ServiceError):
        service.assess_linear_result(project, raw_decay["result"], raw_decay["case"])
    kernel_path.write_bytes(backup)

    kernel_path.unlink()
    with pytest.raises(ServiceError):
        service.assess_linear_result(project, raw_decay["result"], raw_decay["case"])
    kernel_path.write_bytes(backup)

    # Restored bytes: the assessment is admitted again.
    report = service.assess_linear_result(
        project, raw_decay["result"], raw_decay["case"]
    )
    assert report.verdict == "pass", report.failed


@requires_solver
def test_paired_different_kernel_refused(decay_pair, tmp_path):
    """Different physical kernels are never a dt refinement."""

    import anyhydro

    # SAME dataset/derived-operator bytes as the pair (identical source
    # identities); ONLY the radiation kernel differs (a different
    # zero-frequency damping sample): different kernels are never a dt
    # refinement, whatever the bookkeeping says.
    dataset = anyhydro.load_hydro_dataset(decay_pair["dataset_path"])
    artifact = anyhydro.load_derived_operators(decay_pair["operators_path"])
    kernel = _hydro_kernel(artifact, dataset, dt=DT_COARSE, zfd_divisor=1.7)
    root = tmp_path / "other-kernel"
    root.mkdir()
    kernel_path = root / "kernel.nc"
    anyhydro.save_radiation_kernel(kernel, kernel_path)

    service = ApplicationService(root / "project")
    project = _hydro_project()
    service.save(project)
    options = {
        "n_steps": N_STEPS_COARSE,
        "criteria": dict(HYDRO_CRITERIA),
        "q0": list(DECAY_Q0),
        "v0": list(DECAY_V0),
    }
    other = service.import_derived_hydro_inputs(
        project, decay_pair["dataset_path"],
        decay_pair["operators_path"], kernel_path, options,
    )
    service.save(other)
    case = service.prepare_linear_assessment(
        other,
        _decay_case_spec(
            other, role="coarse",
            partner=decay_pair["fine"]["case"].case_hash,
            refinement=_decay_refinement(
                "coarse", decay_pair["fine"]["case"].case_hash
            ),
        ),
    )
    result = service.run_linear_local(other)
    other = service.add_result(other, result)
    service.save(other)

    member_coarse = PairedMember(other, result, case, project_root=service.root)
    member_fine = _member(decay_pair, "fine")
    with pytest.raises(PairedRefinementRefusalError, match="kernel"):
        assess_paired_refinement(member_coarse, member_fine)


@requires_solver
def test_paired_stale_geometry_refused(decay_pair):
    """A member whose project geometry drifted after storage is refused."""

    coarse = decay_pair["coarse"]
    bodies = list(coarse["project"].bodies)
    edited = bodies[0].with_edits(mass=float(bodies[0].mass) + 1.0)
    stale = coarse["project"].with_edits(bodies=(edited, bodies[1]))
    member_coarse = PairedMember(
        stale,
        coarse["result"],
        coarse["case"],
        project_root=coarse["service"].root,
    )
    with pytest.raises(PairedRefinementRefusalError, match="actual project"):
        assess_paired_refinement(member_coarse, _member(decay_pair, "fine"))


@requires_solver
def test_paired_role_swap_refused(decay_pair):
    """Swapping the coarse/fine members is a typed refusal."""

    with pytest.raises(PairedRefinementRefusalError, match="roles"):
        assess_paired_refinement(
            _member(decay_pair, "fine"), _member(decay_pair, "coarse")
        )


@requires_solver
def test_paired_non_nested_grids_refused(tmp_path):
    """Grids that are not exactly nested with the declared ratio refuse."""

    pair = _analytic_pair(
        tmp_path,
        dt_coarse=0.05, n_coarse=401,
        dt_fine=0.04, n_fine=801,
        kernel_scale=[6.0e5, 5.0e5, 4.0e5, 3.0e5],
    )
    member_coarse = PairedMember(
        pair["project_coarse"], pair["result_coarse"], pair["case_coarse"],
        project_root=pair["service_coarse"].root,
    )
    member_fine = PairedMember(
        pair["project_fine"], pair["result_fine"], pair["case_fine"],
        project_root=pair["service_fine"].root,
    )
    with pytest.raises(PairedRefinementRefusalError, match="dt"):
        assess_paired_refinement(member_coarse, member_fine)


# --------------------------------------------------- composite NoGo pair

@requires_solver
def test_paired_composite_nogo_close_members(tmp_path):
    """Two close NoGo members stay a preserved composite NoGo.

    Both members carry the SAME mildly active analytic kernel (an honest
    development counterexample): each independently fails passivity, the
    raw dt-refinement diagnostic still passes (they are close), and the
    combined verdict is NoGo — never a refusal, and never a "pair pass
    because close". The composite NoGo record is preserved, snapshotted
    and reproduced.
    """

    # NEGATIVE diagonal: an honestly active (energy-injecting) kernel — the
    # same development counterexample scale as the single-member NoGo test.
    scale = [-6.0e5, -5.0e5, -4.0e5, -3.0e5]
    pair = _analytic_pair(
        tmp_path,
        dt_coarse=0.05, n_coarse=401,
        dt_fine=0.025, n_fine=801,
        kernel_scale=scale,
    )
    member_coarse = PairedMember(
        pair["project_coarse"], pair["result_coarse"], pair["case_coarse"],
        project_root=pair["service_coarse"].root,
    )
    member_fine = PairedMember(
        pair["project_fine"], pair["result_fine"], pair["case_fine"],
        project_root=pair["service_fine"].root,
    )
    paired = assess_paired_refinement(member_coarse, member_fine)
    assert paired.combined_verdict == "nogo"
    assert paired.members["coarse"]["verdict"] == "nogo"
    assert paired.members["fine"]["verdict"] == "nogo"
    assert "coarse:radiation_passivity" in paired.failed
    assert "fine:radiation_passivity" in paired.failed
    assert not any(name.startswith("refinement:") for name in paired.failed)

    snapshot = tmp_path / "nogo-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    outcome = adjudicate_paired_snapshot(snapshot)
    assert outcome["agrees"] is True
    assert outcome["combined_verdict"] == "nogo"

    # CLI: a NoGo pair is a nonzero outcome, never a silent success.
    from anymarine import cli

    coarse_case_path = AssessmentStore(tmp_path / "cases").save_case(
        pair["case_coarse"]
    )
    fine_case_path = AssessmentStore(tmp_path / "cases").save_case(
        pair["case_fine"]
    )
    code = cli.main(
        [
            "assess-refinement",
            "--coarse-project", str(pair["service_coarse"].root),
            "--coarse-case", str(coarse_case_path),
            "--coarse-result-id", pair["result_coarse"].result_id,
            "--fine-project", str(pair["service_fine"].root),
            "--fine-case", str(fine_case_path),
            "--fine-result-id", pair["result_fine"].result_id,
            "--snapshot", str(tmp_path / "cli-nogo-snapshot"),
        ]
    )
    assert code == 1


# ------------------------------------------------------ snapshot guards

@requires_solver
def test_cli_assess_refinement_end_to_end(decay_pair, tmp_path):
    """The real CLI workflow: explicit choices, snapshot, reproduction."""

    from anymarine import cli

    coarse = decay_pair["coarse"]
    fine = decay_pair["fine"]
    coarse_case_path = AssessmentStore(tmp_path / "cases-coarse").save_case(
        coarse["case"]
    )
    fine_case_path = AssessmentStore(tmp_path / "cases-fine").save_case(
        fine["case"]
    )
    snapshot = tmp_path / "cli-snapshot"
    code = cli.main(
        [
            "assess-refinement",
            "--coarse-project", str(coarse["service"].root),
            "--coarse-case", str(coarse_case_path),
            "--coarse-result-id", coarse["result"].result_id,
            "--fine-project", str(fine["service"].root),
            "--fine-case", str(fine_case_path),
            "--fine-result-id", fine["result"].result_id,
            "--snapshot", str(snapshot),
        ]
    )
    assert code == 0
    code = cli.main(["adjudicate-refinement", "--snapshot", str(snapshot)])
    assert code == 0


@requires_solver
def test_pair_artifact_tamper_refused(decay_pair, tmp_path):
    """A re-hashed paired record with forged metrics cannot reproduce."""

    member_coarse = _member(decay_pair, "coarse")
    member_fine = _member(decay_pair, "fine")
    paired = assess_paired_refinement(member_coarse, member_fine)
    snapshot = tmp_path / "tamper-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    pair_path = snapshot / "pair.json"
    original = pair_path.read_bytes()

    data = json.loads(pair_path.read_text(encoding="utf-8"))
    data["pair"]["members"]["coarse"]["report"]["checks"][0]["value"] += 1.0
    from anymarine.paired_refinement import PairedRefinementReport

    forged = PairedRefinementReport(data["pair"])
    data["pair_hash"] = forged.pair_hash
    pair_path.write_text(json.dumps(data), encoding="utf-8")
    # Refused — now even earlier: the captured member report artifact no
    # longer matches the forged stored member section (paired inventory
    # violation), before the full reproduction comparison would run.
    with pytest.raises(
        PairedRefinementRefusalError,
        match="does not reproduce|does not match the stored paired member "
        "section",
    ):
        adjudicate_paired_snapshot(snapshot)

    # A record mutated without re-hashing is refused by the stored hash.
    pair_path.write_bytes(original)
    data = json.loads(pair_path.read_text(encoding="utf-8"))
    data["pair"]["combined_verdict"] = "nogo"
    pair_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PairedRefinementRefusalError, match="mutated"):
        adjudicate_paired_snapshot(snapshot)


@requires_solver
def test_snapshot_member_ref_refusals(decay_pair, tmp_path):
    """Missing, escaping or unknown snapshot references are refused."""

    member_coarse = _member(decay_pair, "coarse")
    member_fine = _member(decay_pair, "fine")
    paired = assess_paired_refinement(member_coarse, member_fine)
    snapshot = tmp_path / "refs-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    pair_path = snapshot / "pair.json"
    original = pair_path.read_bytes()
    data = json.loads(pair_path.read_text(encoding="utf-8"))
    coarse_case_ref = data["members"]["coarse"]["case"]
    coarse_case_path = snapshot / coarse_case_ref
    case_backup = coarse_case_path.read_bytes()

    # Missing member reference.
    coarse_case_path.unlink()
    with pytest.raises(PairedRefinementRefusalError, match="does not exist"):
        adjudicate_paired_snapshot(snapshot)
    coarse_case_path.write_bytes(case_backup)

    # Escaping reference.
    data["members"]["coarse"]["case"] = "../escape.json"
    pair_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PairedRefinementRefusalError, match="unsafe"):
        adjudicate_paired_snapshot(snapshot)
    pair_path.write_bytes(original)
    data = json.loads(pair_path.read_text(encoding="utf-8"))

    # Unknown field inside a valid member role must also be refused.
    data["members"]["coarse"]["unexpected"] = "coarse/project"
    pair_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PairedRefinementRefusalError, match="exactly"):
        adjudicate_paired_snapshot(snapshot)
    pair_path.write_bytes(original)
    data = json.loads(pair_path.read_text(encoding="utf-8"))

    # Unknown role: not a closed-world member reference set.
    data["members"]["middle"] = data["members"]["coarse"]
    pair_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PairedRefinementRefusalError, match="roles"):
        adjudicate_paired_snapshot(snapshot)
    pair_path.write_bytes(original)


@requires_solver
def test_snapshot_target_preserved(decay_pair, tmp_path):
    """An existing snapshot target is refused BEFORE any write."""

    member_coarse = _member(decay_pair, "coarse")
    member_fine = _member(decay_pair, "fine")
    paired = assess_paired_refinement(member_coarse, member_fine)
    snapshot = tmp_path / "preserved-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    pair_path = snapshot / "pair.json"
    before = pair_path.read_bytes()
    with pytest.raises(PairedRefinementRefusalError, match="already exists"):
        save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    assert pair_path.read_bytes() == before


@requires_solver
def test_pair_record_closed_world_and_stable_under_mutation(decay_pair):
    """The paired record is closed-world and immutable at every boundary.

    The constructor refuses unknown or missing record fields, and the
    exposed members/refinement/pairing/scope/failed payloads are
    defensive copies: attempting to mutate an exposed payload cannot
    change the record content or its hash.
    """

    paired = assess_paired_refinement(
        _member(decay_pair, "coarse"), _member(decay_pair, "fine")
    )
    original = paired.to_dict()
    identity = paired.pair_hash
    for attribute in ("members", "refinement", "pairing", "scope", "failed"):
        value = getattr(paired, attribute)
        if isinstance(value, dict):
            value.clear()
        else:
            value.append("forged")
    assert paired.to_dict() == original
    assert paired.pair_hash == identity

    with pytest.raises(PairedRefinementError, match="closed-world"):
        PairedRefinementReport(dict(original, unexpected=True))
    with pytest.raises(PairedRefinementError, match="closed-world"):
        PairedRefinementReport(
            {
                key: value
                for key, value in original.items()
                if key != "pairing"
            }
        )


@requires_solver
def test_snapshot_refuses_forged_captured_member_report(decay_pair, tmp_path):
    """A re-hashed captured member report cannot sneak past reproduction.

    The captured member report artifact is rewritten through the PUBLIC
    report artifact writer while the pair's own embedded payload stays
    untouched. The flipped check flag is refused by the identity/hash
    inventory binding; a changed check metric whose hash is ALSO
    re-forged in pair.json (with the pair hash recomputed) is refused by
    the semantic payload binding — never a hash-only proof.
    """

    member_coarse = _member(decay_pair, "coarse")
    member_fine = _member(decay_pair, "fine")
    paired = assess_paired_refinement(member_coarse, member_fine)
    snapshot = tmp_path / "forged-report-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    pair_path = snapshot / "pair.json"
    report_path = snapshot / json.loads(
        pair_path.read_text(encoding="utf-8")
    )["members"]["coarse"]["report"]
    original_report_bytes = report_path.read_bytes()

    # Flipped check flag, rewritten with the public artifact writer: the
    # artifact is internally consistent, but it no longer matches the
    # stored paired member inventory.
    forged = read_report_artifact(report_path).to_dict()
    forged["checks"][0]["passed"] = not forged["checks"][0]["passed"]
    write_report_artifact(AssessmentReport(forged), report_path)
    with pytest.raises(
        PairedRefinementRefusalError, match="member inventory"
    ):
        adjudicate_paired_snapshot(snapshot)

    # Changed check metric with the stored member report hash re-forged
    # in pair.json and the pair hash recomputed: the hash binding alone
    # cannot catch this — the semantic payload binding must.
    report_path.write_bytes(original_report_bytes)
    forged = read_report_artifact(report_path).to_dict()
    forged["checks"][0]["value"] += 1.0
    forged_report = AssessmentReport(forged)
    write_report_artifact(forged_report, report_path)
    data = json.loads(pair_path.read_text(encoding="utf-8"))
    data["pair"]["members"]["coarse"]["report_hash"] = (
        forged_report.report_hash
    )
    data["pair_hash"] = PairedRefinementReport(data["pair"]).pair_hash
    pair_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(
        PairedRefinementRefusalError,
        match="does not match the stored paired member section",
    ):
        adjudicate_paired_snapshot(snapshot)


# ------------------------------------------------- self-contained reopen

@requires_solver
def test_paired_snapshot_reproduction_after_originals_removed(
    decay_pair, tmp_path
):
    """The snapshot reproduces with the originals deleted.

    DESTRUCTIVE for the fixture's originals (project roots and source
    sidecars): it relies on module test ordering and must stay LAST among
    the decay_pair tests.
    """

    member_coarse = _member(decay_pair, "coarse")
    member_fine = _member(decay_pair, "fine")
    paired = assess_paired_refinement(member_coarse, member_fine)
    snapshot = tmp_path / "pair-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)

    # Artifact roundtrip: the stored record reads back identically.
    stored = read_paired_artifact(snapshot / "pair.json")
    assert stored.pair_hash == paired.pair_hash
    assert stored.combined_verdict == "pass"

    # The snapshot must no longer depend on the original projects or the
    # original source files.
    shutil.rmtree(decay_pair["coarse"]["service"].root)
    shutil.rmtree(decay_pair["fine"]["service"].root)
    for path in (
        decay_pair["dataset_path"],
        decay_pair["operators_path"],
        decay_pair["fine_kernel_path"],
        decay_pair["coarse_kernel_path"],
    ):
        path.unlink()

    outcome = adjudicate_paired_snapshot(snapshot)
    assert outcome["agrees"] is True
    assert outcome["combined_verdict"] == "pass"
