"""Hydro-source free-decay paired refinement rehearsal.

Role: ``analytic_rehearsal_only``. The hydro dataset is an analytic
exponential-oracle ANYhydro fixture (honestly marked development fixture,
never BEM output); nothing here is a W5 qualification claim. The solver
is reached only through the public ``ApplicationService.run_linear_local``
route and the hydro sources only through the public import routes.

Workflow (the acceptance boundary of this milestone):

same analytic dataset/derived operators -> two projects (coarse dt 0.08 /
fine dt 0.04, registered integer ratio 2, SAME physical kernel on nested
lag grids) -> hydro_source free-decay cases registered BEFORE the solves
-> actual results -> full member assessments (source proofs re-run) ->
paired combined verdict -> EXCLUSIVE self-contained paired snapshot ->
originals removed -> snapshot-only reproduction/adjudication.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import numpy as np

from anymarine.domain import (
    Body,
    EquilibriumMetadata,
    ProjectModel,
    default_box_mesh,
)
from anymarine.linear import linear_input_hash
from anymarine.linear_assessment import (
    MODE_FREE_DECAY,
    REFERENCE_HYDRO_SOURCE,
)
from anymarine.paired_refinement import (
    PairedMember,
    adjudicate_paired_snapshot,
    assess_paired_refinement,
    save_paired_snapshot,
)
from anymarine.services import ApplicationService

DT_RATIO = 2
DT_FINE = 0.04
DT_COARSE = 0.08
N_STEPS_FINE = 501
N_STEPS_COARSE = 251
KERNEL_N_TIME = 601
DECAY_Q0 = [0.0, 0.05, 0.0, 0.04]
DECAY_V0 = [0.0, 0.0, 0.0, 0.0]

HYDRO_CRITERIA = {
    "omega_min": 0.5,
    "omega_max": 4.0,
    "added_mass_abs_tol": 50.0,
    "added_mass_rel_tol": 0.05,
    "damping_abs_tol": 50.0,
    "damping_rel_tol": 0.05,
}

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


def _project() -> ProjectModel:
    return ProjectModel(
        name="Hydro decay refinement example",
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


def _fixture_arrays():
    omegas = np.array([round(0.1 * k, 1) for k in range(1, 81)])
    shape = np.linspace(1.0, 2.5, 12)
    coupling = 400.0 * np.outer(shape, shape)
    a_inf = 2000.0 * coupling / 400.0 + 50.0 * np.eye(12)
    added_mass = np.empty((omegas.size, 12, 12))
    damping = np.empty((omegas.size, 12, 12))
    lam = 1.5
    for index, omega in enumerate(omegas):
        added_mass[index] = a_inf - coupling / (lam**2 + omega**2)
        damping[index] = coupling * lam / (lam**2 + omega**2)
    stiffness = np.zeros((12, 12))
    for b in range(2):
        stiffness[2 + 6 * b, 2 + 6 * b] = 1025.0 * 9.81 * (72.0 if b == 0 else 45.0)
        stiffness[3 + 6 * b, 3 + 6 * b] = 5.0e3
        stiffness[4 + 6 * b, 4 + 6 * b] = 6.0e3
    return a_inf, added_mass, damping, stiffness, coupling


def _dataset(project):
    import anyhydro

    from anymarine import hydro

    a_inf, added_mass, damping, stiffness, _ = _fixture_arrays()
    excitation = np.empty((80, 3, 12), dtype=np.complex128)
    for o in range(80):
        for d in range(3):
            for i in range(12):
                excitation[o, d, i] = (
                    5000.0 + 100.0 * i + 50.0 * o + 200.0 * d
                ) * np.exp(1j * (0.3 * i + 0.1 * o + 0.05 * d))
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
        backend_settings={"fixture": "decay-refinement-example-analytic"},
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


def _derived_settings():
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


def _kernel(artifact, dataset, *, dt):
    import anyhydro

    projection = np.zeros((12, 4))
    for column, row in enumerate((0, 2, 6, 8)):
        projection[row, column] = 1.0
    _, _, _, _, coupling = _fixture_arrays()
    settings = anyhydro.RadiationSettings(
        dt=dt,
        n_time=KERNEL_N_TIME,
        symmetry_tol=1.0e-6,
        passivity_tol=1.0e-6,
        low_tail_policy="explicit_zero_sample",
        zero_frequency_damping=projection.T @ (coupling / 1.5) @ projection,
    )
    return anyhydro.radiation_kernel_from_derived_operators(
        artifact, settings, source_dataset=dataset
    )


def _case_spec(project, *, role, partner):
    inputs = project.linear_inputs
    provenance = project.hydro_provenance
    bundle = provenance.derived_bundle
    return {
        "case_id": f"hydro-decay-{role}",
        "mode": MODE_FREE_DECAY,
        "input_hash": linear_input_hash(inputs),
        "result_id": None,
        "reference": {
            "kind": REFERENCE_HYDRO_SOURCE,
            "route": "derived_operators",
            "dataset_content_hash": provenance.dataset_content_hash,
            "derived_content_hash": bundle["derived_content_hash"],
            "operators_raw_sha256": bundle["operators_raw_sha256"],
        },
        "omega": None,
        "force_phasors": None,
        "windows": None,
        "fit_controls": {"intercepts": True, "drift": "none", "max_condition": 1.0e12},
        "criteria": dict(CRITERIA),
        "refinement": {
            "partner_case_hash": partner,
            "dt_ratio": DT_RATIO,
            "role": role,
        },
        "scope": {"development_only": True, "role": "analytic_rehearsal_only"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="output root (default: a fresh temporary directory)",
    )
    args = parser.parse_args()
    root = Path(args.out) if args.out else Path(
        tempfile.mkdtemp(prefix="hydro-decay-refinement-example-")
    )
    root.mkdir(parents=True, exist_ok=True)

    import anyhydro

    print(f"output root: {root}")
    print(
        "role: analytic_rehearsal_only — analytic exponential-oracle ANYhydro "
        "fixture, development output, unqualified; not BEM, not a W5 claim"
    )

    project = _project()
    dataset = _dataset(project)
    artifact = anyhydro.derived_operators_from_dataset(dataset, _derived_settings())
    assert artifact.admitted
    kernel_fine = _kernel(artifact, dataset, dt=DT_FINE)
    kernel_coarse = _kernel(artifact, dataset, dt=DT_COARSE)
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
        # Free decay: NO wave selection; the stored external forcing is zero.
        member = service.import_derived_hydro_inputs(
            project, dataset_path, operators_path, kernel_path, options
        )
        service.save(member)
        members[role] = {"service": service, "project": member}

    # Cases registered BEFORE any rehearsal solve (fine first, then the
    # coarse case bound to the fine case hash).
    fine = members["fine"]
    coarse = members["coarse"]
    case_fine = fine["service"].prepare_linear_assessment(
        fine["project"], _case_spec(fine["project"], role="fine", partner=None)
    )
    case_coarse = coarse["service"].prepare_linear_assessment(
        coarse["project"],
        _case_spec(
            coarse["project"], role="coarse", partner=case_fine.case_hash
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
        print(f"{role} member assessment: {data['report'].verdict}")

    member_coarse = PairedMember(
        coarse["project"], coarse["result"], coarse["case"],
        project_root=coarse["service"].root,
    )
    member_fine = PairedMember(
        fine["project"], fine["result"], fine["case"],
        project_root=fine["service"].root,
    )
    paired = assess_paired_refinement(member_coarse, member_fine)
    print(f"paired combined verdict: {paired.combined_verdict}")
    print(f"dt ratio: {paired.dt_ratio}")
    print(
        "shared-lag kernel identity (kg/s^2): "
        f"{paired.pairing['max_shared_lag_kernel_deviation_kg_s2']:.3e}"
    )

    snapshot = root / "paired-snapshot"
    save_paired_snapshot(member_coarse, member_fine, paired, snapshot)
    print(f"exclusive paired snapshot: {snapshot}")

    # The snapshot must reproduce with the originals removed: only the
    # snapshot's own projects/results/cases/sidecars remain.
    shutil.rmtree(coarse["service"].root)
    shutil.rmtree(fine["service"].root)
    for path in (dataset_path, operators_path, fine_kernel_path, coarse_kernel_path):
        path.unlink()
    outcome = adjudicate_paired_snapshot(snapshot)
    print(
        "snapshot-only reproduction: "
        f"agrees={outcome['agrees']} verdict={outcome['combined_verdict']}"
    )
    print(
        "scope: development only, unqualified; never a qualified marine "
        "analysis"
    )
    ok = (
        paired.combined_verdict == "pass"
        and outcome["agrees"]
        and outcome["combined_verdict"] == "pass"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
