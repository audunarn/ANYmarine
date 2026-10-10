"""Analytic rehearsal of the registered linear assessment workflow.

Role: ``analytic_rehearsal_only``. The radiation kernel is an explicit
exponential ``R exp(-alpha t)`` analytic construction with an independent
closed-form frequency-domain reference; it is NOT BEM output and nothing
here is a W5 qualification claim. The solver is reached only through the
public ``ApplicationService.run_linear_local`` route.

Workflow (the acceptance boundary of the slice):

stored project -> registered case (BEFORE the solve) -> actual result ->
assessment report -> self-contained snapshot -> reopen -> adjudication,
plus a coarse/fine dt refinement comparison, for both a regular wave and a
free decay.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np

from anymarine.domain import LinearBodyBinding, LinearProvenance
from anymarine.hashing import equilibrium_hash, mesh_hash
from anymarine.linear import linear_input_hash
from anymarine.linear_assessment import (
    MODE_FREE_DECAY,
    MODE_REGULAR_WAVE,
    REFERENCE_ANALYTIC_EXPONENTIAL,
    assess_refinement,
)
from anymarine.services import ApplicationService

ALPHA = 1.5
OMEGA = 2.5
DT_COARSE = 0.05
DT_FINE = 0.025
N_COARSE = 401
N_FINE = 801
T0 = 1.0
WINDOWS = {"start": 9.0, "mid": 14.2, "end": 19.4}
F_HAT = np.array(
    [
        3.0e5 + 1.0e5j,
        -2.0e5 + 0.5e5j,
        1.5e5 - 0.7e5j,
        2.2e5 + 1.1e5j,
    ],
    dtype=np.complex128,
)

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


def _matrices():
    shape = np.array([1.0, 0.8, 0.9, 0.7])
    radiation = 6.0e5 * np.outer(shape, shape)
    ainf = radiation / ALPHA + np.diag([3.0e5, 4.0e5, 2.5e5, 3.5e5])
    mass_dry = np.diag([1.2e6, 1.2e6, 0.9e6, 0.9e6])
    stiffness = np.diag([0.0, 6.0e5, 0.0, 4.5e5])
    # Heavier mechanical damping than the first fixture draft: the wave
    # rehearsal starts from the steady FD state with zero memory
    # prehistory, and the resulting startup transient must decay below
    # the (unchanged) steadiness/fit criteria by the first window. This
    # is a development-fixture choice, not a criteria change.
    damping = np.diag([1.0e6, 1.5e6, 0.75e6, 1.25e6])
    return radiation, ainf, mass_dry, stiffness, damping


def _analytic_qhat(f_hat=None):
    radiation, ainf, _, stiffness, damping = _matrices()
    f_hat = F_HAT if f_hat is None else f_hat
    b_omega = ALPHA * radiation / (ALPHA * ALPHA + OMEGA * OMEGA)
    a_omega = ainf - radiation / (ALPHA * ALPHA + OMEGA * OMEGA)
    impedance = (
        stiffness
        - OMEGA * OMEGA * (np.diag([1.2e6, 1.2e6, 0.9e6, 0.9e6]) + a_omega)
        - 1j * OMEGA * (b_omega + damping)
    )
    return np.linalg.solve(impedance, f_hat)


def _build_inputs(project, *, dt, n_steps, forces, q0, v0):
    radiation, ainf, mass_dry, stiffness, damping = _matrices()
    times = np.arange(n_steps) * dt
    kernel = radiation[None, :, :] * np.exp(-ALPHA * times)[:, None, None]
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
    from anymarine.domain import LinearDevelopmentInputs

    return LinearDevelopmentInputs(
        bindings=bindings,
        dt=dt,
        t0=T0,
        n_steps=n_steps,
        forces=tuple(tuple(float(value) for value in row) for row in forces),
        mass_dry=tuple(tuple(float(v) for v in row) for row in mass_dry),
        added_mass_infinite=tuple(tuple(float(v) for v in row) for row in ainf),
        damping=tuple(tuple(float(v) for v in row) for row in damping),
        stiffness=tuple(tuple(float(v) for v in row) for row in stiffness),
        radiation_kernel=tuple(
            tuple(tuple(float(v) for v in row) for row in sample) for sample in kernel
        ),
        kernel_times=tuple(float(value) for value in times),
        equilibrium_hash=equilibrium_hash(project.equilibrium),
        q0=tuple(float(value) for value in q0),
        v0=tuple(float(value) for value in v0),
        provenance=LinearProvenance(
            source="explicit",
            description=(
                "Analytic exponential-radiation rehearsal inputs "
                "(analytic_rehearsal_only; development, unqualified)"
            ),
        ),
    )


def _regular_wave_inputs(project, *, dt, n_steps):
    times = T0 + np.arange(n_steps) * dt
    phasor = np.exp(-1j * OMEGA * times)
    forces = (F_HAT[None, :] * phasor[:, None]).real
    qhat = _analytic_qhat()
    q0 = (qhat * np.exp(-1j * OMEGA * T0)).real
    v0 = (-1j * OMEGA * qhat * np.exp(-1j * OMEGA * T0)).real
    return _build_inputs(
        project, dt=dt, n_steps=n_steps, forces=forces, q0=q0, v0=v0
    )


def _free_decay_inputs(project):
    n_steps = N_COARSE
    forces = np.zeros((n_steps, 4))
    q0 = np.array([0.0, 0.05, 0.0, 0.04])
    v0 = np.zeros(4)
    return _build_inputs(
        project,
        dt=DT_COARSE,
        n_steps=n_steps,
        forces=forces,
        q0=q0,
        v0=v0,
    )


def _reference():
    radiation = _matrices()[0]
    return {
        "kind": REFERENCE_ANALYTIC_EXPONENTIAL,
        "coefficient_matrix": [
            [float(value) for value in row] for row in radiation
        ],
        "decay": ALPHA,
    }


def _spec(case_id, mode, inputs, *, windows=None, refinement=None):
    data = {
        "case_id": case_id,
        "mode": mode,
        "input_hash": linear_input_hash(inputs),
        "result_id": None,
        "reference": _reference(),
        "omega": OMEGA if mode == MODE_REGULAR_WAVE else None,
        "force_phasors": (
            [[float(value.real), float(value.imag)] for value in F_HAT]
            if mode == MODE_REGULAR_WAVE
            else None
        ),
        "windows": windows,
        "fit_controls": {"intercepts": True, "drift": "none", "max_condition": 1.0e12},
        "criteria": dict(CRITERIA),
        "refinement": refinement,
        "scope": {"development_only": True, "role": "analytic_rehearsal_only"},
    }
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="output root (default: a fresh temporary directory)",
    )
    args = parser.parse_args()
    root = Path(args.out) if args.out else Path(
        tempfile.mkdtemp(prefix="linear-assessment-example-")
    )
    root.mkdir(parents=True, exist_ok=True)
    service = ApplicationService()

    print(f"output root: {root}")
    print(
        "role: analytic_rehearsal_only — analytic exponential radiation, "
        "development output, unqualified; not BEM, not a W5 claim"
    )

    # ---------------------------------------------------------- regular wave
    project_coarse = service.new_project("Rehearsal regular wave (coarse)")
    inputs_coarse = _regular_wave_inputs(project_coarse, dt=DT_COARSE, n_steps=N_COARSE)
    project_coarse = service.set_linear_inputs(project_coarse, inputs_coarse)
    project_fine = service.new_project("Rehearsal regular wave (fine)")
    inputs_fine = _regular_wave_inputs(project_fine, dt=DT_FINE, n_steps=N_FINE)
    project_fine = service.set_linear_inputs(project_fine, inputs_fine)

    # Cases are registered BEFORE the rehearsal solve.
    case_fine = service.prepare_linear_assessment(
        project_fine,
        _spec(
            "rehearsal-regular-fine",
            MODE_REGULAR_WAVE,
            inputs_fine,
            windows=WINDOWS,
            refinement={"partner_case_hash": None, "dt_ratio": 2, "role": "fine"},
        ),
    )
    case_coarse = service.prepare_linear_assessment(
        project_coarse,
        _spec(
            "rehearsal-regular-coarse",
            MODE_REGULAR_WAVE,
            inputs_coarse,
            windows=WINDOWS,
            refinement={
                "partner_case_hash": case_fine.case_hash,
                "dt_ratio": 2,
                "role": "coarse",
            },
        ),
    )
    result_coarse = service.run_linear_local(project_coarse)
    result_fine = service.run_linear_local(project_fine)
    report_coarse = service.assess_linear_result(
        project_coarse, result_coarse, case_coarse
    )
    report_fine = service.assess_linear_result(project_fine, result_fine, case_fine)
    refinement = assess_refinement(
        case_coarse,
        case_fine,
        result_coarse,
        result_fine,
        inputs_coarse,
        inputs_fine,
    )
    print(f"regular wave coarse: {report_coarse.verdict}")
    print(f"regular wave fine:   {report_fine.verdict}")
    print(f"refinement:          {refinement.verdict}")

    # ------------------------------------------------------------ free decay
    project_decay = service.new_project("Rehearsal free decay")
    inputs_decay = _free_decay_inputs(project_decay)
    project_decay = service.set_linear_inputs(project_decay, inputs_decay)
    case_decay = service.prepare_linear_assessment(
        project_decay,
        _spec("rehearsal-free-decay", MODE_FREE_DECAY, inputs_decay),
    )
    result_decay = service.run_linear_local(project_decay)
    report_decay = service.assess_linear_result(
        project_decay, result_decay, case_decay
    )
    print(f"free decay:          {report_decay.verdict}")

    # --------------------------------------------- snapshot, reopen, adjudicate
    snapshot_root = root / "snapshot"
    service.save_assessment_snapshot(
        project_coarse, result_coarse, case_coarse, report_coarse, snapshot_root
    )
    case_path = next((snapshot_root / "cases").glob("*.json"))
    report_path = next((snapshot_root / "reports").glob("*.json"))
    reloaded = ApplicationService()
    outcome = reloaded.adjudicate_linear_assessment(
        snapshot_root, case_path, report_path
    )
    print(
        f"snapshot adjudication: agrees={outcome['agrees']} "
        f"verdict={outcome['verdict']}"
    )
    print(
        "all outputs are DEVELOPMENT ONLY and unqualified; never a qualified "
        "marine analysis"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
