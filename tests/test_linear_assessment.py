"""Registered linear assessment slice tests.

Analytic/mocked development tests only: the radiation kernel is an explicit
exponential construction or an analytic ANYhydro fixture, never BEM output,
and nothing here is scientific qualification. Solver runs go through the
public ``ApplicationService.run_linear_local`` route; hydro sources go
through the public import routes and the public ANYhydro loaders.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_solver

from anymarine import RESULT_KIND_SYNTHETIC
from anymarine.domain import (
    LINEAR_SOURCE_HYDRO,
    LinearBodyBinding,
    LinearDevelopmentInputs,
    LinearProvenance,
    ProjectModel,
    default_box_mesh,
    Body,
    EquilibriumMetadata,
)
from anymarine.hashing import equilibrium_hash, mesh_hash
from anymarine.linear import linear_input_hash, run_linear
from anymarine.linear_assessment import (
    AssessmentCase,
    AssessmentCaseError,
    AssessmentRefusalError,
    AssessmentStore,
    REFERENCE_ANALYTIC_EXPONENTIAL,
    REFERENCE_HYDRO_SPECTRUM,
    MODE_FREE_DECAY,
    MODE_REGULAR_WAVE,
    adjudicate_report,
    assess_refinement,
    assess_result,
    hydro_reciprocal_sensitivity,
    prepare_case,
    read_case_artifact,
    read_report_artifact,
    write_case_artifact,
    write_report_artifact,
)
from anymarine.services import ApplicationService, ServiceError

ALPHA = 1.5
OMEGA = 2.5
DT = 0.05
N_STEPS = 401
T0 = 1.0
WINDOWS = {"start": 9.0, "mid": 14.2, "end": 19.4}
F_HAT = np.array(
    [3.0e5 + 1.0e5j, -2.0e5 + 0.5e5j, 1.5e5 - 0.7e5j, 2.2e5 + 1.1e5j],
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
    damping = np.diag([2.0e5, 3.0e5, 1.5e5, 2.5e5])
    return radiation, ainf, mass_dry, stiffness, damping


def _rw_matrices():
    """Regular-wave fixture: heavier mechanical damping.

    The solver starts the memory force from zero prehistory, so the exact
    steady-state initial condition still carries a startup transient; the
    heavier C (used consistently by the FD reference too) decays it fast
    enough that the predeclared windows, 8 s into the run, are steady to
    the 2% gate.
    """

    radiation, ainf, mass_dry, stiffness, _ = _matrices()
    damping = np.diag([2.0e6, 2.5e6, 1.5e6, 2.0e6])
    return radiation, ainf, mass_dry, stiffness, damping


def _analytic_qhat(f_hat=None, matrices=None):
    radiation, ainf, mass_dry, stiffness, damping = (
        matrices or _matrices()
    )
    f_hat = F_HAT if f_hat is None else f_hat
    b_omega = ALPHA * radiation / (ALPHA * ALPHA + OMEGA * OMEGA)
    a_omega = ainf - radiation / (ALPHA * ALPHA + OMEGA * OMEGA)
    impedance = (
        stiffness
        - OMEGA * OMEGA * (mass_dry + a_omega)
        - 1j * OMEGA * (b_omega + damping)
    )
    return np.linalg.solve(impedance, f_hat)


def _project() -> ProjectModel:
    return ProjectModel(
        name="Linear assessment fixture",
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


def _inputs(project, *, dt=DT, n_steps=N_STEPS, forces=None, q0=None, v0=None,
            matrices=None):
    radiation, ainf, mass_dry, stiffness, damping = matrices or _matrices()
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
    return LinearDevelopmentInputs(
        bindings=bindings,
        dt=dt,
        t0=T0,
        n_steps=n_steps,
        forces=tuple(tuple(float(v) for v in row) for row in forces),
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
        q0=None if q0 is None else tuple(float(v) for v in q0),
        v0=None if v0 is None else tuple(float(v) for v in v0),
        provenance=LinearProvenance(
            source="explicit",
            description="Analytic exponential rehearsal inputs (development).",
        ),
    )


def _regular_wave_inputs(project, **kwargs):
    kwargs.setdefault("matrices", _rw_matrices())
    times = T0 + np.arange(kwargs.get("n_steps", N_STEPS)) * kwargs.get("dt", DT)
    phasor = np.exp(-1j * OMEGA * times)
    forces = (F_HAT[None, :] * phasor[:, None]).real
    qhat = _analytic_qhat(matrices=kwargs.get("matrices"))
    q0 = (qhat * np.exp(-1j * OMEGA * T0)).real
    v0 = (-1j * OMEGA * qhat * np.exp(-1j * OMEGA * T0)).real
    return _inputs(project, forces=forces, q0=q0, v0=v0, **kwargs)


def _free_decay_inputs(project, **kwargs):
    n_steps = kwargs.pop("n_steps", N_STEPS)
    forces = kwargs.pop("forces", np.zeros((n_steps, 4)))
    q0 = kwargs.pop("q0", np.array([0.0, 0.05, 0.0, 0.04]))
    return _inputs(
        project, n_steps=n_steps, forces=forces, q0=q0,
        v0=kwargs.pop("v0", np.zeros(4)), **kwargs,
    )


def _reference(matrices=None):
    radiation = (matrices or _matrices())[0]
    return {
        "kind": REFERENCE_ANALYTIC_EXPONENTIAL,
        "coefficient_matrix": [[float(v) for v in row] for row in radiation],
        "decay": ALPHA,
    }


def _spec(case_id, mode, inputs, *, windows=None, refinement=None,
          criteria=None, reference=None, force_phasors=None, omega=OMEGA):
    regular = mode == MODE_REGULAR_WAVE
    return {
        "case_id": case_id,
        "mode": mode,
        "input_hash": linear_input_hash(inputs),
        "result_id": None,
        "reference": reference or _reference(),
        "omega": omega if regular else None,
        "force_phasors": (
            force_phasors
            if force_phasors is not None
            else ([[float(v.real), float(v.imag)] for v in F_HAT] if regular else None)
        ),
        "windows": windows,
        "fit_controls": {
            "intercepts": True,
            "drift": "none",
            "max_condition": 1.0e12,
        },
        "criteria": dict(CRITERIA if criteria is None else criteria),
        "refinement": refinement,
        "scope": {"development_only": True, "role": "analytic_rehearsal_only"},
    }


# ------------------------------------------------------------- guard tests


def test_base_import_guard(tmp_path):
    """Base import stays Qt/Capytaine/solver-free in a blocked subprocess.

    The in-process sys.modules check cannot work here: conftest itself
    imports PySide6 to compute the Qt availability markers, so the guard
    runs in a clean subprocess with PySide6/any3dview blocked.
    """

    import os
    import subprocess
    import sys

    block_dir = tmp_path / "block"
    block_dir.mkdir()
    (block_dir / "sitecustomize.py").write_text(
        "import sys\n"
        "class _Blocker:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('PySide6', 'any3dview', 'shiboken6'):\n"
        "            raise ImportError('blocked for headless test: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocker())\n",
        encoding="utf-8",
    )
    code = (
        "import sys; import anymarine.linear_assessment; "
        "leaked = [m for m in ('PySide6', 'Capytaine', 'anysolver', 'any3dview') "
        "if m in sys.modules]; "
        "assert not leaked, leaked; print('guard-ok')"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([
        str(Path(__file__).resolve().parent.parent / "src"),
        str(block_dir),
    ])
    env.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "guard-ok" in result.stdout


def test_prepare_case_binds_hash_and_closed_world():
    project = _project()
    inputs = _regular_wave_inputs(project)
    spec = _spec("case-ok", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    case = prepare_case(spec, inputs)
    assert case.input_hash == linear_input_hash(inputs)
    assert case.case_hash and len(case.case_hash) == 64

    wrong_hash = dict(spec)
    wrong_hash["input_hash"] = "0" * 64
    with pytest.raises(AssessmentCaseError, match="input_hash"):
        prepare_case(wrong_hash, inputs)

    unknown = dict(spec)
    unknown["surprise"] = 1
    with pytest.raises(AssessmentCaseError, match="unknown field"):
        prepare_case(unknown, inputs)

    looser = json.loads(json.dumps(spec))
    looser["criteria"]["balance_relative"] = 1.0e-9
    with pytest.raises(AssessmentCaseError, match="never looser"):
        prepare_case(looser, inputs)

    stricter = json.loads(json.dumps(spec))
    stricter["criteria"]["balance_relative"] = 1.0e-11
    assert prepare_case(stricter, inputs).criteria["balance_relative"] == 1.0e-11


def test_kernel_quadrature_reference_refused():
    project = _project()
    inputs = _regular_wave_inputs(project)
    spec = _spec("case-kq", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    spec["reference"] = {"kind": "kernel_quadrature"}
    with pytest.raises(AssessmentCaseError, match="never a qualification reference"):
        prepare_case(spec, inputs)


def test_hydro_spectrum_reference_validation():
    project = _project()
    inputs = _regular_wave_inputs(project)
    base = _spec("case-hydro", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    good = {
        "kind": REFERENCE_HYDRO_SPECTRUM,
        "route": "derived_operators",
        "omega": OMEGA,
        "heading": 0.0,
        "wave_amplitude": 0.35,
        "wave_phase": 0.4,
        "dataset_content_hash": "a" * 64,
        "derived_content_hash": "b" * 64,
        "operators_raw_sha256": "c" * 64,
    }
    spec = json.loads(json.dumps(base))
    spec["reference"] = dict(good)
    case = prepare_case(spec, inputs)
    assert case.reference["route"] == "derived_operators"

    missing = json.loads(json.dumps(base))
    missing["reference"] = {k: v for k, v in good.items() if k != "heading"}
    with pytest.raises(AssessmentCaseError, match="heading"):
        prepare_case(missing, inputs)

    raw_with_derived = json.loads(json.dumps(base))
    raw_with_derived["reference"] = dict(
        good, route="raw_dataset", derived_content_hash=None
    )
    raw_with_derived["reference"].pop("derived_content_hash")
    raw_with_derived["reference"]["derived_content_hash"] = "b" * 64
    with pytest.raises(AssessmentCaseError, match="raw-route"):
        prepare_case(raw_with_derived, inputs)

    omega_mismatch = json.loads(json.dumps(base))
    omega_mismatch["reference"] = dict(good, omega=OMEGA + 1.0)
    with pytest.raises(AssessmentCaseError, match="source frequency"):
        prepare_case(omega_mismatch, inputs)

    decay_inputs = _free_decay_inputs(project)
    decay_spec = _spec("case-hydro-fd", MODE_FREE_DECAY, decay_inputs)
    decay_spec["reference"] = dict(good)
    with pytest.raises(AssessmentCaseError, match="regular_wave"):
        prepare_case(decay_spec, decay_inputs)


def test_window_and_nyquist_refusals():
    project = _project()
    inputs = _regular_wave_inputs(project)

    short = _spec("case-short", MODE_REGULAR_WAVE, inputs,
                  windows={"start": 9.0, "mid": 10.0, "end": 11.0})
    with pytest.raises(AssessmentCaseError, match="two cycles"):
        prepare_case(short, inputs)

    unequal = _spec("case-unequal", MODE_REGULAR_WAVE, inputs,
                    windows={"start": 9.0, "mid": 14.2, "end": 18.0})
    with pytest.raises(AssessmentCaseError, match="equal duration"):
        prepare_case(unequal, inputs)

    outside = _spec("case-outside", MODE_REGULAR_WAVE, inputs,
                    windows={"start": 9.0, "mid": 14.2, "end": 99.0})
    with pytest.raises(AssessmentCaseError, match="grid"):
        prepare_case(outside, inputs)

    nyquist = _spec("case-ny", MODE_REGULAR_WAVE, inputs, windows=WINDOWS,
                    omega=np.pi / DT)
    with pytest.raises(AssessmentCaseError, match="Nyquist"):
        prepare_case(nyquist, inputs)


def test_drift_rule_gates():
    project = _project()
    inputs = _regular_wave_inputs(project)
    spec = _spec("case-drift", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    spec["fit_controls"]["drift"] = "common_surge_linear"
    # The fixture stiffness resists the common surge direction (heave
    # stiffness couples through K u with u = (1,0,1,0)/sqrt2? no: K u has
    # zero heave entries only if heave rows are decoupled from surge; here
    # K is diagonal in heave so K u = 0 exactly).
    case = prepare_case(json.loads(json.dumps(spec)), inputs)
    assert case.fit_controls["drift"] == "common_surge_linear"

    matrices = _matrices()
    stiffness = matrices[3].copy()
    stiffness[1, 0] = 5.0e4  # heave-surge coupling resists the drift mode
    coupled = (matrices[0], matrices[1], matrices[2], stiffness, matrices[4])
    resisting = _regular_wave_inputs(project, matrices=coupled)
    spec2 = _spec("case-drift-2", MODE_REGULAR_WAVE, resisting, windows=WINDOWS)
    spec2["fit_controls"]["drift"] = "common_surge_linear"
    with pytest.raises(AssessmentCaseError, match="K u = 0"):
        prepare_case(spec2, resisting)

    # No per-DOF or heave drift is ever admitted: the drift rule is a
    # closed-world enum with exactly the shared common-surge option.
    heave = _spec("case-heave", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    heave["fit_controls"]["drift"] = "heave_linear"
    with pytest.raises(AssessmentCaseError, match="must be one of"):
        prepare_case(heave, inputs)

    relative = _spec("case-rel", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    relative["fit_controls"]["drift"] = "per_dof_linear"
    with pytest.raises(AssessmentCaseError, match="must be one of"):
        prepare_case(relative, inputs)


def test_fit_window_badwindow_and_rank_refusals():
    from anymarine.linear_assessment import _fit_window

    project = _project()
    inputs = _regular_wave_inputs(project)
    times = np.asarray(inputs.times, dtype=float)
    q = np.zeros((inputs.n_steps, 4))
    case = prepare_case(
        _spec("case-fit", MODE_REGULAR_WAVE, inputs, windows=WINDOWS), inputs
    )
    # A two-sample window holds fewer samples than the three per-DOF fit
    # columns (cos/sin/intercept): a bad window is a typed refusal.
    with pytest.raises(AssessmentRefusalError, match="inadequate"):
        _fit_window(times, q, case, times[10], times[11])

    # A rank-deficient stacked design: no intercepts, drift on, two samples
    # give 8 stacked rows for 9 columns (4 DOFs x 2 + 1 shared drift).
    drift_spec = _spec(
        "case-fit-drift", MODE_REGULAR_WAVE, inputs, windows=WINDOWS
    )
    drift_spec["fit_controls"]["drift"] = "common_surge_linear"
    drift_spec["fit_controls"]["intercepts"] = False
    drift_case = prepare_case(drift_spec, inputs)
    with pytest.raises(AssessmentRefusalError, match="rank deficient"):
        _fit_window(times, q, drift_case, times[10], times[11])


def test_memory_oracle_direct_sum_nonzero_v0():
    """The FFT oracle equals the direct causal sum for a coupled kernel.

    Nonzero initial velocity and the full coupled off-diagonal fixture
    kernel exercise both endpoint contractions (K[n] v_0 and K[0] v_n).
    """

    from anymarine.linear_assessment import _memory_oracle

    project = _project()
    n_steps = 61
    inputs = _inputs(
        project,
        n_steps=n_steps,
        forces=np.zeros((n_steps, 4)),
        v0=np.array([0.4, -0.2, 0.6, 0.1]),
    )
    velocities = 0.05 + 0.1 * np.sin(0.7 * np.arange(n_steps))[:, None] * np.array(
        [1.0, -2.0, 0.5, 3.0]
    )
    assert np.any(velocities[0] != 0.0)
    oracle = _memory_oracle(inputs, velocities)
    kernel = np.asarray(inputs.radiation_kernel, dtype=float)
    assert np.any(kernel[0] != 0.0)
    assert np.count_nonzero(kernel[0]) > 4  # fully coupled off-diagonals
    dt = inputs.dt
    direct = np.zeros((n_steps, 4))
    for n in range(1, n_steps):
        total = 0.5 * (kernel[n] @ velocities[0])
        for j in range(1, n):
            total = total + kernel[n - j] @ velocities[j]
        total = total + 0.5 * (kernel[0] @ velocities[n])
        direct[n] = dt * total
    deviation = float(np.max(np.abs(oracle - direct)))
    scale = max(float(np.max(np.abs(direct))), 1.0)
    assert deviation <= 1.0e-9 * scale


def test_case_artifact_tamper_refused(tmp_path):
    project = _project()
    inputs = _regular_wave_inputs(project)
    case = prepare_case(
        _spec("case-artifact", MODE_REGULAR_WAVE, inputs, windows=WINDOWS), inputs
    )
    path = write_case_artifact(case, tmp_path / "case.json")
    assert read_case_artifact(path).case_hash == case.case_hash

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["case"]["omega"] = 1.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AssessmentCaseError, match="mutated"):
        read_case_artifact(path)


# --------------------------------------------------------- solver-required


@requires_solver
def test_regular_wave_full_pass(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Regular wave rehearsal")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project,
        _spec("rw-pass", MODE_REGULAR_WAVE, inputs, windows=WINDOWS),
    )
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    names = {check["name"] for check in report.checks}
    assert {
        "input_force_match",
        "steadiness",
        "fit_rms_first",
        "fit_rms_second",
        "rao_amplitude_first",
        "rao_phase_first",
        "force_balance",
        "stored_residual_channel",
        "external_channel_vs_inputs",
        "memory_oracle",
        "newmark_q_update",
        "newmark_v_update",
        "initial_state",
        "small_excursion",
    } <= names
    assert report.arrays["reference_source"]["kind"] == REFERENCE_ANALYTIC_EXPONENTIAL
    assert len(report.arrays["fits"]) == 2
    assert report.scope["development_only"] is True


@requires_solver
def test_common_surge_drift_fit_pass(tmp_path):
    """The common-surge drift fits ONE shared coefficient across 4 DOFs."""

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Common surge drift rehearsal")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    spec = _spec("rw-drift", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    spec["fit_controls"]["drift"] = "common_surge_linear"
    case = service.prepare_linear_assessment(project, spec)
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    for fit in report.arrays["fits"]:
        # Exactly ONE shared drift coefficient per window. The fixture
        # signal is a pure sinusoid, but the zero-prehistory startup
        # transient leaves a small tail that projects onto the shared
        # drift: it must stay negligible against the fitted amplitude
        # over the window span, not against an arbitrary absolute bound.
        assert fit["shared_drift_coefficient"] is not None
        amplitude = max(
            abs(complex(re, im)) for re, im in fit["qhat"]
        )
        span = WINDOWS["mid"] - WINDOWS["start"]
        assert abs(fit["shared_drift_coefficient"]) * span < 0.02 * amplitude


@requires_solver
def test_assessment_binding_refusals(tmp_path):
    """Assessment re-verifies the body/equilibrium binding, not just hashes.

    Changing the actual body mass (or mesh/equilibrium) without updating
    the stored inputs refuses preparation and assessment even though the
    stale result/input hashes still match: the public run_linear_local
    gate alone does not establish later assessment eligibility.
    """

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Binding refusals")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("rw-bind", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    result = service.run_linear_local(project)

    heavier = tuple(
        dataclasses.replace(body, mass=body.mass * 1.5)
        for body in project.bodies
    )
    edited = project.with_edits(bodies=heavier)
    with pytest.raises(ServiceError, match="mass changed"):
        service.prepare_linear_assessment(
            edited,
            _spec("rw-bind-2", MODE_REGULAR_WAVE, inputs, windows=WINDOWS),
        )
    with pytest.raises(ServiceError, match="mass changed"):
        service.assess_linear_result(edited, result, case)

    remeshed = tuple(
        dataclasses.replace(body, mesh=default_box_mesh((13.0, 6.0, 5.0)))
        for body in project.bodies
    )
    edited_mesh = project.with_edits(bodies=remeshed)
    with pytest.raises(ServiceError, match="mesh"):
        service.assess_linear_result(edited_mesh, result, case)


@requires_solver
def test_wrong_result_refusals(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Refusals")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("rw-refuse", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    result = service.run_linear_local(project)

    synthetic = service.run_synthetic_local(project)
    assert synthetic.kind == RESULT_KIND_SYNTHETIC
    # The service gate refuses the foreign hash first; the core refuses
    # the wrong kind before any hash comparison.
    with pytest.raises(ServiceError, match="stale or foreign"):
        service.assess_linear_result(project, synthetic, case)
    with pytest.raises(AssessmentRefusalError, match="linear_development"):
        assess_result(case, synthetic, inputs)

    truncated_channels = {
        key: value for key, value in result.channels.items()
        if key != f"a:{inputs.labels[3]}"
    }
    truncated = dataclasses.replace(result, channels=truncated_channels)
    with pytest.raises(ServiceError, match="does not completely represent"):
        service.assess_linear_result(project, truncated, case)

    foreign = dataclasses.replace(
        result, input_hash="0" * 64
    )
    with pytest.raises(ServiceError, match="stale or foreign"):
        service.assess_linear_result(project, foreign, case)


@requires_solver
def test_force_and_memory_forgery_nogo(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Forgery")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("rw-forgery", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    result = service.run_linear_local(project)

    labels = inputs.labels
    forged_external = {
        **result.channels,
        **{
            f"force_external:{label}": tuple(
                1.01 * np.asarray(result.channels[f"force_external:{label}"])
            )
            for label in labels
        },
    }
    forged = dataclasses.replace(result, channels=forged_external)
    report = service.assess_linear_result(project, forged, case)
    assert report.verdict == "nogo"
    assert "force_balance" in report.failed
    assert "external_channel_vs_inputs" in report.failed

    forged_memory = {
        **result.channels,
        **{
            f"force_memory:{label}": tuple(
                1.01 * np.asarray(result.channels[f"force_memory:{label}"])
            )
            for label in labels
        },
    }
    forged_mem = dataclasses.replace(result, channels=forged_memory)
    report_mem = service.assess_linear_result(project, forged_mem, case)
    assert report_mem.verdict == "nogo"
    assert "memory_oracle" in report_mem.failed


@requires_solver
def test_free_decay_pass_and_energy_arrays(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Free decay rehearsal")
    inputs = _free_decay_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("fd-pass", MODE_FREE_DECAY, inputs)
    )
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    names = {check["name"] for check in report.checks}
    assert {
        "free_decay_zero_forcing",
        "energy_closure",
        "radiation_passivity",
        "mechanical_peak_growth",
    } <= names
    energy = report.arrays["energy"]
    assert len(energy["mechanical"]) == inputs.n_steps
    assert len(energy["work_memory"]) == inputs.n_steps
    # Returning stored radiation energy is allowed: no monotonicity demand.
    # Positive net work AND actual negative increments are both admitted.
    assert energy["max_cumulative_work_memory"] > 0.0
    assert energy["min_work_memory_increment"] < 0.0
    assert energy["min_cumulative_work_memory"] >= -energy["tolerance_j"]
    passivity = [
        check for check in report.checks
        if check["name"] == "radiation_passivity"
    ][0]
    assert passivity["passed"] is True
    # Passivity is the MIN cumulative bound: the reported value is the
    # worst energy return, not the maximum cumulative work.
    assert passivity["value"] == -energy["min_cumulative_work_memory"]


@requires_solver
def test_free_decay_active_kernel_nogo(tmp_path):
    """An active (energy-injecting) kernel violates passivity: NoGo."""

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Active kernel rehearsal")
    radiation, _, mass_dry, stiffness, damping = _matrices()
    active = -np.diag([6.0e5, 5.0e5, 4.0e5, 3.0e5])
    ainf_active = active / ALPHA + np.diag([3.0e5, 4.0e5, 2.5e5, 3.5e5])
    matrices = (active, ainf_active, mass_dry, stiffness, damping)
    inputs = _free_decay_inputs(project, matrices=matrices)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project,
        _spec(
            "fd-active",
            MODE_FREE_DECAY,
            inputs,
            reference=_reference(matrices=matrices),
        ),
    )
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "nogo"
    assert "radiation_passivity" in report.failed
    energy = report.arrays["energy"]
    assert energy["min_cumulative_work_memory"] < -energy["tolerance_j"]


@requires_solver
def test_free_decay_nonzero_forcing_nogo(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Forced decay")
    inputs = _free_decay_inputs(
        project, forces=np.full((N_STEPS, 4), 1.0e-3)
    )
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("fd-forced", MODE_FREE_DECAY, inputs)
    )
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "nogo"
    assert "free_decay_zero_forcing" in report.failed


@requires_solver
def test_energy_closure_counterexample_nogo(tmp_path):
    """A forged position channel breaks the discrete closure: NoGo."""

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Closure counterexample")
    inputs = _free_decay_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("fd-closure", MODE_FREE_DECAY, inputs)
    )
    result = service.run_linear_local(project)
    # Slightly scaled damping-force channel: the work accounting no longer
    # closes against the (unchanged) energy trajectory. The channel is
    # verified for presence but not cross-checked, so this is an honest
    # physics NoGo, not an eligibility refusal.
    forged_damping = {
        **result.channels,
        **{
            f"force_damping:{label}": tuple(
                1.001 * np.asarray(result.channels[f"force_damping:{label}"])
            )
            for label in inputs.labels
        },
    }
    forged = dataclasses.replace(result, channels=forged_damping)
    report = service.assess_linear_result(project, forged, case)
    assert report.verdict == "nogo"
    assert "energy_closure" in report.failed


@requires_solver
def test_nearzero_dof_phase_not_measured(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Near-zero rehearsal")
    radiation, ainf, mass_dry, stiffness, damping = _rw_matrices()
    # Decouple DOF 1 (body-alpha heave) and make it effectively rigid:
    # its FD reference amplitude is exactly zero, so the phase is NOT
    # measured and the amplitude uses the absolute near-zero bound.
    radiation[1, :] = 0.0
    radiation[:, 1] = 0.0
    ainf[1, :] = 0.0
    ainf[:, 1] = 0.0
    ainf[1, 1] = 4.0e5
    stiffness[1, 1] = 1.0e10
    matrices = (radiation, ainf, mass_dry, stiffness, damping)
    f_hat = F_HAT.copy()
    f_hat[1] = 0.0
    inputs = _regular_wave_inputs(project, matrices=matrices)
    # rebuild forcing with the modified phasor
    times = T0 + np.arange(N_STEPS) * DT
    forces = (f_hat[None, :] * np.exp(-1j * OMEGA * times)[:, None]).real
    qhat = _analytic_qhat(f_hat=f_hat, matrices=matrices)
    q0 = (qhat * np.exp(-1j * OMEGA * T0)).real
    v0 = (-1j * OMEGA * qhat * np.exp(-1j * OMEGA * T0)).real
    inputs = _inputs(
        project, forces=forces, q0=q0, v0=v0, matrices=matrices
    )
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    spec = _spec(
        "rw-nearzero",
        MODE_REGULAR_WAVE,
        inputs,
        windows=WINDOWS,
        reference=_reference(matrices=matrices),
        force_phasors=[[float(v.real), float(v.imag)] for v in f_hat],
    )
    case = service.prepare_linear_assessment(project, spec)
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    phase = [c for c in report.checks if c["name"] == "rao_phase_second"][0]
    assert "NOT measured" in phase["detail"]


@requires_solver
def test_refinement_pass_and_guards(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project_coarse = service.new_project("Refinement coarse")
    inputs_coarse = _regular_wave_inputs(project_coarse)
    project_coarse = service.set_linear_inputs(project_coarse, inputs_coarse)
    project_fine = service.new_project("Refinement fine")
    inputs_fine = _regular_wave_inputs(project_fine, dt=DT / 2, n_steps=2 * N_STEPS - 1)
    project_fine = service.set_linear_inputs(project_fine, inputs_fine)

    case_fine = service.prepare_linear_assessment(
        project_fine,
        _spec(
            "ref-fine",
            MODE_REGULAR_WAVE,
            inputs_fine,
            windows=WINDOWS,
            refinement={"partner_case_hash": None, "dt_ratio": 2, "role": "fine"},
        ),
    )
    case_coarse = service.prepare_linear_assessment(
        project_coarse,
        _spec(
            "ref-coarse",
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
    report = assess_refinement(
        case_coarse, case_fine, result_coarse, result_fine,
        inputs_coarse, inputs_fine,
    )
    assert report.verdict == "pass", report.failed

    wrong_partner = service.prepare_linear_assessment(
        project_coarse,
        _spec(
            "ref-wrong",
            MODE_REGULAR_WAVE,
            inputs_coarse,
            windows=WINDOWS,
            refinement={
                "partner_case_hash": "d" * 64,
                "dt_ratio": 2,
                "role": "coarse",
            },
        ),
    )
    with pytest.raises(AssessmentRefusalError, match="partner"):
        assess_refinement(
            wrong_partner, case_fine, result_coarse, result_fine,
            inputs_coarse, inputs_fine,
        )

    matrices = _matrices()
    perturbed = list(matrices)
    perturbed[3] = matrices[3].copy()
    perturbed[3][0, 0] += 1.0
    perturbed = tuple(perturbed)
    inputs_other = _regular_wave_inputs(project_fine, matrices=perturbed)
    with pytest.raises(AssessmentRefusalError, match="SAME physical model"):
        assess_refinement(
            case_coarse, case_fine, result_coarse, result_fine,
            inputs_coarse, inputs_other,
        )


@requires_solver
def test_snapshot_reopen_adjudicate_and_tamper(tmp_path):
    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Snapshot rehearsal")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("rw-snapshot", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)
    report = service.assess_linear_result(project, result, case)

    snapshot_root = tmp_path / "snapshot"
    service.save_assessment_snapshot(
        project, result, case, report, snapshot_root
    )
    case_path = next((snapshot_root / "cases").glob("*.json"))
    report_path = next((snapshot_root / "reports").glob("*.json"))

    reloaded = ApplicationService()
    outcome = reloaded.adjudicate_linear_assessment(
        snapshot_root, case_path, report_path
    )
    assert outcome["agrees"] is True
    assert outcome["verdict"] == "pass"

    # A re-hashed tampered report passes artifact validation but cannot
    # survive adjudication: stored booleans are necessary, not sufficient.
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["report"]["verdict"] = "nogo"
    from anymarine.linear_assessment import load_report

    tampered_report = load_report(payload["report"])
    payload["report_hash"] = tampered_report.report_hash
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ServiceError, match="does not reproduce"):
        reloaded.adjudicate_linear_assessment(snapshot_root, case_path, report_path)


@requires_solver
def test_rehashed_metric_forgery_refused_by_adjudication_and_snapshot(tmp_path):
    """A re-hashed report with forged metrics and unchanged flags is refused.

    The stored FD reference metric is shifted by +0.02 and the report is
    re-hashed (a fresh load recomputes its hash), so every pass flag and
    the verdict stay unchanged: reproduction must still refuse, because it
    compares the full semantic payload (checks and actual arrays/metrics),
    not just the flags. The snapshot refuses the forged report before
    creating anything.
    """

    from anymarine.linear_assessment import load_report

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Metric forgery rehearsal")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case = service.prepare_linear_assessment(
        project, _spec("forgery-case", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed

    payload = report.to_dict()
    payload["arrays"]["fd_reference"]["qhat"][0][0] += 0.02
    forged = load_report(payload)
    assert forged.report_hash != report.report_hash
    assert forged.verdict == report.verdict

    with pytest.raises(AssessmentRefusalError, match="does not reproduce"):
        adjudicate_report(case, forged, result, inputs)

    target = tmp_path / "refused-snapshot"
    with pytest.raises(ServiceError, match="does not reproduce"):
        service.save_assessment_snapshot(project, result, case, forged, target)
    assert not target.exists()


@requires_solver
def test_snapshot_target_and_correspondence_refusals(tmp_path):
    """Existing targets are refused; only corresponding artifacts snapshot."""

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("Snapshot refusals")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    case_a = service.prepare_linear_assessment(
        project, _spec("snap-a", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    )
    case_b = service.prepare_linear_assessment(
        project,
        _spec(
            "snap-b",
            MODE_REGULAR_WAVE,
            inputs,
            windows={"start": 9.5, "mid": 14.65, "end": 19.8},
        ),
    )
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)
    report_a = service.assess_linear_result(project, result, case_a)

    # An existing target is refused BEFORE any write; old outputs (e.g. a
    # preserved NoGo report) survive untouched.
    existing = tmp_path / "existing-snapshot"
    existing.mkdir()
    (existing / "keep.txt").write_text("old failed output", encoding="utf-8")
    with pytest.raises(ServiceError, match="already exists"):
        service.save_assessment_snapshot(
            project, result, case_a, report_a, existing
        )
    assert (existing / "keep.txt").read_text(encoding="utf-8") == (
        "old failed output"
    )
    assert not (existing / "project").exists()

    # A report from another case does not correspond; nothing is written.
    fresh = tmp_path / "fresh-snapshot"
    with pytest.raises(ServiceError, match="do not correspond"):
        service.save_assessment_snapshot(project, result, case_b, report_a, fresh)
    assert not fresh.exists()

    # Only the selected result is copied; an unrelated second result is
    # excluded from the snapshot project.
    other = service.run_linear_local(project)
    assert other.result_id != result.result_id
    project = service.add_result(project, other)
    service.save(project)
    good = tmp_path / "good-snapshot"
    service.save_assessment_snapshot(project, result, case_a, report_a, good)
    reloaded = ApplicationService()
    snap_project = reloaded.load(good / "project")
    assert [record.result_id for record in snap_project.results] == [
        result.result_id
    ]


@requires_solver
def test_cli_end_to_end(tmp_path, capsys):
    from anymarine import cli

    service = ApplicationService(tmp_path / "project")
    project = service.new_project("CLI rehearsal")
    inputs = _regular_wave_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    service.save(project)
    result = service.run_linear_local(project)
    project = service.add_result(project, result)
    service.save(project)

    spec = _spec("cli-case", MODE_REGULAR_WAVE, inputs, windows=WINDOWS)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    case_path = tmp_path / "case.json"
    report_path = tmp_path / "report.json"

    assert cli.main([
        "prepare-assessment",
        "--project", str(tmp_path / "project"),
        "--spec", str(spec_path),
        "--out", str(case_path),
    ]) == 0
    assert cli.main([
        "assess-linear",
        "--project", str(tmp_path / "project"),
        "--case", str(case_path),
        "--result-id", result.result_id,
        "--out", str(report_path),
    ]) == 0

    snapshot_root = tmp_path / "snapshot"
    case = read_case_artifact(case_path)
    report = read_report_artifact(report_path)
    service.save_assessment_snapshot(
        project, result, case, report, snapshot_root
    )
    snap_case = next((snapshot_root / "cases").glob("*.json"))
    snap_report = next((snapshot_root / "reports").glob("*.json"))
    assert cli.main([
        "adjudicate-assessment",
        "--snapshot", str(snapshot_root),
        "--case", str(snap_case),
        "--report", str(snap_report),
    ]) == 0

    assert cli.main([
        "assess-linear",
        "--project", str(tmp_path / "project"),
        "--case", str(case_path),
        "--result-id", "missing-result",
        "--out", str(tmp_path / "report2.json"),
    ]) == 1


# ------------------------------------------- source-intent admission guard

def _hydro_relabelled_inputs(inputs):
    """Same numerical inputs, hydro source intent (admission guard fixture)."""
    return dataclasses.replace(
        inputs,
        provenance=LinearProvenance(
            source=LINEAR_SOURCE_HYDRO,
            description="Hydro-source intent relabel (admission guard fixture).",
        ),
    )


def test_hydro_source_intent_refuses_analytic_regular_wave_reference():
    project = _project()
    hydro_inputs = _hydro_relabelled_inputs(_regular_wave_inputs(project))
    spec = _spec("case-hydro-analytic-bypass", MODE_REGULAR_WAVE, hydro_inputs)
    with pytest.raises(AssessmentCaseError, match="hydro-sourced"):
        prepare_case(spec, hydro_inputs)


def test_hydro_source_free_decay_refused_at_registration():
    project = _project()
    hydro_inputs = _hydro_relabelled_inputs(_free_decay_inputs(project))
    spec = _spec("case-hydro-fd-analytic-bypass", MODE_FREE_DECAY, hydro_inputs)
    with pytest.raises(AssessmentCaseError, match="hydro_source"):
        prepare_case(spec, hydro_inputs)


def test_hydro_source_direct_case_refused_at_assessment():
    project = _project()
    inputs = _regular_wave_inputs(project)
    hydro_inputs = _hydro_relabelled_inputs(inputs)
    # A directly constructed (or pre-guard loaded) case whose bound hash
    # matches the hydro-sourced inputs cannot bypass preparation.
    base = prepare_case(
        _spec("case-direct-bypass", MODE_REGULAR_WAVE, inputs, windows=WINDOWS),
        inputs,
    )
    direct = AssessmentCase(
        dict(base.to_dict(), input_hash=linear_input_hash(hydro_inputs))
    )
    result = run_linear(hydro_inputs)
    with pytest.raises(AssessmentRefusalError, match="hydro-sourced"):
        assess_result(direct, result, hydro_inputs)


def test_hydro_source_free_decay_case_refused_at_assessment():
    project = _project()
    inputs = _free_decay_inputs(project)
    hydro_inputs = _hydro_relabelled_inputs(inputs)
    base = prepare_case(_spec("case-direct-fd", MODE_FREE_DECAY, inputs), inputs)
    direct = AssessmentCase(
        dict(base.to_dict(), input_hash=linear_input_hash(hydro_inputs))
    )
    result = run_linear(hydro_inputs)
    with pytest.raises(AssessmentRefusalError, match="hydro_source"):
        assess_result(direct, result, hydro_inputs)


# ------------------------------------------------------------- hydro route

HYDRO_OMEGA = 1.5
HYDRO_HEADING = 0.0
HYDRO_AMPLITUDE = 0.35
HYDRO_PHASE = 0.4
HYDRO_DT = 0.05
HYDRO_N_STEPS = 401
HYDRO_WINDOWS = {"start": 1.0, "mid": 9.5, "end": 18.0}
HYDRO_CRITERIA = {
    "omega_min": 0.5,
    "omega_max": 4.0,
    "added_mass_abs_tol": 50.0,
    "added_mass_rel_tol": 0.05,
    "damping_abs_tol": 50.0,
    "damping_rel_tol": 0.05,
}


def _hydro_project():
    return ProjectModel(
        name="Hydro assessment fixture",
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
    import anyhydro

    omegas = np.array([round(0.1 * k, 1) for k in range(1, 81)])
    directions = np.array([0.0, np.pi / 4.0, np.pi / 2.0])
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
    excitation = np.empty((omegas.size, directions.size, 12), dtype=np.complex128)
    for o in range(omegas.size):
        for d in range(directions.size):
            for i in range(12):
                magnitude = 5000.0 + 100.0 * i + 50.0 * o + 200.0 * d
                phase = 0.3 * i + 0.1 * o + 0.05 * d
                excitation[o, d, i] = magnitude * np.exp(1j * phase)
    stiffness = np.zeros((12, 12))
    for b in range(2):
        stiffness[2 + 6 * b, 2 + 6 * b] = 1025.0 * 9.81 * (72.0 if b == 0 else 45.0)
        stiffness[3 + 6 * b, 3 + 6 * b] = 5.0e3
        stiffness[4 + 6 * b, 4 + 6 * b] = 6.0e3
    return a_inf, added_mass, damping, excitation, stiffness, coupling


def _hydro_dataset(project, *, eps=1.2e-6):
    import anyhydro

    from anymarine import hydro

    a_inf, added_mass, damping, excitation, stiffness, _ = _hydro_fixture_arrays(
        eps=eps
    )
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
        backend_settings={"fixture": "assessment-analytic"},
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


def _hydro_kernel(artifact, dataset, n_time=601):
    import anyhydro

    projection = np.zeros((12, 4))
    for column, row in enumerate((0, 2, 6, 8)):
        projection[row, column] = 1.0
    _, _, _, _, _, coupling = _hydro_fixture_arrays()
    settings = anyhydro.RadiationSettings(
        dt=HYDRO_DT,
        n_time=n_time,
        symmetry_tol=1.0e-6,
        passivity_tol=1.0e-6,
        low_tail_policy="explicit_zero_sample",
        zero_frequency_damping=projection.T @ (coupling / 1.5) @ projection,
    )
    return anyhydro.radiation_kernel_from_derived_operators(
        artifact, settings, source_dataset=dataset
    )


def _hydro_steady_initial_state(artifact, omega_index, direction_index, mass_dry):
    """FD steady-state q0/v0 from the actual derived spectrum sample."""

    a_omega = np.asarray(artifact.derived_added_mass[omega_index], dtype=float)
    b_omega = np.asarray(artifact.derived_damping[omega_index], dtype=float)
    stiffness = np.asarray(artifact.hydrostatic_stiffness, dtype=float)
    f_hat = HYDRO_AMPLITUDE * np.exp(1j * HYDRO_PHASE) * np.asarray(
        artifact.excitation_force[omega_index, direction_index, :],
        dtype=np.complex128,
    )
    impedance = (
        stiffness
        - HYDRO_OMEGA**2 * (mass_dry + a_omega)
        - 1j * HYDRO_OMEGA * b_omega
    )
    qhat = np.linalg.solve(impedance, f_hat)
    q0 = qhat.real
    v0 = (-1j * HYDRO_OMEGA * qhat).real
    return q0, v0, f_hat


def _hydro_case_spec(project, inputs, provenance, f_hat, *, criteria=None,
                     route="derived_operators", dataset_hash=None,
                     derived_hash=None, operators_sha=None, omega=HYDRO_OMEGA):
    bundle = provenance.derived_bundle
    return {
        "case_id": "hydro-spectrum-case",
        "mode": MODE_REGULAR_WAVE,
        "input_hash": linear_input_hash(inputs),
        "result_id": None,
        "reference": {
            "kind": REFERENCE_HYDRO_SPECTRUM,
            "route": route,
            "omega": omega,
            "heading": HYDRO_HEADING,
            "wave_amplitude": HYDRO_AMPLITUDE,
            "wave_phase": HYDRO_PHASE,
            "dataset_content_hash": dataset_hash
            or provenance.dataset_content_hash,
            "derived_content_hash": derived_hash
            or (bundle["derived_content_hash"] if bundle is not None else None),
            "operators_raw_sha256": operators_sha
            or (bundle["operators_raw_sha256"] if bundle is not None else None),
        },
        "omega": omega,
        "force_phasors": [
            [float(value.real), float(value.imag)] for value in f_hat
        ],
        "windows": HYDRO_WINDOWS,
        "fit_controls": {
            "intercepts": True,
            "drift": "none",
            "max_condition": 1.0e12,
        },
        "criteria": dict(CRITERIA if criteria is None else criteria),
        "refinement": None,
        "scope": {"development_only": True, "role": "analytic_rehearsal_only"},
    }


@pytest.fixture(scope="module")
def hydro_flow(tmp_path_factory):
    """Full derived-route hydro flow, built once: import, run, assess."""
    import anyhydro

    root = tmp_path_factory.mktemp("hydro-assessment")
    project = _hydro_project()
    dataset = _hydro_dataset(project)
    artifact = anyhydro.derived_operators_from_dataset(
        dataset, _hydro_derived_settings()
    )
    assert artifact.admitted
    kernel = _hydro_kernel(artifact, dataset)
    dataset_path = root / "dataset.nc"
    operators_path = root / "operators.npz"
    kernel_path = root / "kernel.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_derived_operators(artifact, operators_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)

    omegas = np.asarray(artifact.omegas, dtype=float)
    directions = np.asarray(artifact.wave_directions, dtype=float)
    omega_index = int(np.nonzero(omegas == HYDRO_OMEGA)[0][0])
    direction_index = int(np.nonzero(directions == HYDRO_HEADING)[0][0])
    mass_dry = np.diag(
        [2.4e6, 2.4e6, 1.8e6, 1.8e6]
    )
    q0, v0, f_hat = _hydro_steady_initial_state(
        artifact, omega_index, direction_index, mass_dry
    )
    qhat = q0 + 1j * v0 / HYDRO_OMEGA      # q0 = Re(qhat), v0 = Re(-i w qhat) = w Im(qhat)
    service = ApplicationService(root / "project")
    service.save(project)
    options = {
        "n_steps": HYDRO_N_STEPS,
        "criteria": dict(HYDRO_CRITERIA),
        "wave": {
            "omega": HYDRO_OMEGA,
            "heading": HYDRO_HEADING,
            "amplitude": HYDRO_AMPLITUDE,
            "phase": HYDRO_PHASE,
        },
        "q0": [float(v) for v in q0],
        "v0": [float(v) for v in v0],
    }
    project = service.import_derived_hydro_inputs(
        project, dataset_path, operators_path, kernel_path, options
    )
    service.save(project)
    inputs = project.linear_inputs
    provenance = project.hydro_provenance
    spec = _hydro_case_spec(project, inputs, provenance, f_hat)
    case = service.prepare_linear_assessment(project, spec)
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    return {
        "service": service,
        "project": project,
        "inputs": inputs,
        "provenance": provenance,
        "case": case,
        "result": result,
        "report": report,
        "f_hat": f_hat,
        "qhat": qhat,
        "q0_v0_fhat": (q0, v0, f_hat),
        "artifact": artifact,
        "dataset": dataset,
        "dataset_path": dataset_path,
        "operators_path": operators_path,
        "kernel_path": kernel_path,
        "root": root,
    }


@pytest.fixture(scope="module")
def prehistory_flow(hydro_flow):
    """The same derived source imported with a declared periodic prehistory."""
    from anymarine.prehistory import periodic_state
    q0, v0, f_hat = hydro_flow["q0_v0_fhat"]
    qhat = hydro_flow["qhat"]
    t0 = 0.35
    declaration = {"kind": "periodic", "qhat": [[float(v.real), float(v.imag)] for v in qhat]}
    q_start, v_start = periodic_state(qhat, HYDRO_OMEGA, t0)
    service = ApplicationService(hydro_flow["root"] / "prehistory-project")
    project = _hydro_project()
    service.save(project)
    options = {
        "n_steps": HYDRO_N_STEPS,
        "criteria": dict(HYDRO_CRITERIA),
        "t0": t0,
        "wave": {"omega": HYDRO_OMEGA, "heading": HYDRO_HEADING, "amplitude": HYDRO_AMPLITUDE,
                 "phase": HYDRO_PHASE, "prehistory": declaration},
        "q0": [float(v) for v in q_start],
        "v0": [float(v) for v in v_start],
    }
    paths = (hydro_flow["dataset_path"], hydro_flow["operators_path"], hydro_flow["kernel_path"])
    project = service.import_derived_hydro_inputs(project, *paths, options)
    service.save(project)
    return dict(service=service, project=project, options=options, paths=paths, f_hat=f_hat,
                declaration=declaration, t0=t0, qhat=qhat, baseline_fits=hydro_flow["report"].arrays["fits"])


def _prehistory_case_spec(flow, declaration):
    project = flow["project"]
    spec = _hydro_case_spec(project, project.linear_inputs, project.hydro_provenance, flow["f_hat"])
    spec["windows"] = {key: value + flow["t0"] for key, value in HYDRO_WINDOWS.items()}
    if declaration is not None:
        spec["reference"]["prehistory"] = declaration
    return spec


@requires_solver
def test_prehistory_import_stores_wave_minus_history_force(prehistory_flow):
    from test_prehistory import brute_force
    inputs = prehistory_flow["project"].linear_inputs
    qhat = np.array([complex(*p) for p in prehistory_flow["declaration"]["qhat"]])
    times = np.asarray(inputs.times, dtype=float)
    wave = (prehistory_flow["f_hat"][None, :] * np.exp(-1j * HYDRO_OMEGA * times)[:, None]).real
    import anyhydro
    # Every stored lag of the admitted kernel artifact (the run-length kernel is its prefix).
    kernel = np.asarray(anyhydro.load_radiation_kernel(prehistory_flow["paths"][2]).kernel, dtype=float)
    assert kernel.shape[0] >= inputs.n_steps
    assert np.array_equal(kernel[: inputs.n_steps], np.asarray(inputs.radiation_kernel))
    history = brute_force(kernel, inputs.dt, inputs.t0, inputs.n_steps, HYDRO_OMEGA, qhat, -1j * HYDRO_OMEGA * qhat)
    stored = np.asarray(inputs.forces, dtype=float)
    tolerance = 1e-9 * np.max(np.abs(wave))
    assert np.max(np.abs(history)) > 1e3 * tolerance        # the check distinguishes F from F - H
    assert np.max(np.abs(stored - (wave - history))) <= tolerance
    provenance = prehistory_flow["project"].hydro_provenance
    assert provenance.to_dict()["wave"]["prehistory"] == prehistory_flow["declaration"]
    prehistory_flow["service"].verify_hydro_provenance(prehistory_flow["project"])   # reload reproduces


@requires_solver
def test_prehistory_import_refuses_a_start_state_off_the_declared_motion(prehistory_flow):
    service = ApplicationService(prehistory_flow["service"].root.parent / "prehistory-refused")
    project = _hydro_project()
    service.save(project)
    options = json.loads(json.dumps(prehistory_flow["options"]))
    options["q0"][1] += 1e-6
    with pytest.raises(ServiceError, match="periodic prehistory state"):
        service.import_derived_hydro_inputs(project, *prehistory_flow["paths"], options)


@requires_solver
def test_prehistory_assessment_recomputes_the_history_force_and_binds_the_declaration(prehistory_flow):
    service, project = prehistory_flow["service"], prehistory_flow["project"]
    declaration = prehistory_flow["declaration"]
    case = service.prepare_linear_assessment(project, _prehistory_case_spec(prehistory_flow, declaration))
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    match = next(c for c in report.checks if c["name"] == "input_force_match")
    assert match["passed"] and "prehistory" in match["detail"]
    assert report.arrays["prehistory"]["max_abs_history_force_n"] > 0.0
    # Less start transient than the same continuous FD start without the prehistory (the rest
    # is the continuous-vs-discrete start-state difference of this coarse fixture).
    for fit, baseline in zip(report.arrays["fits"], prehistory_flow["baseline_fits"]):
        assert max(fit["residual_rms"]) < 0.75 * max(baseline["residual_rms"])
    # The declaration is bound both ways and exactly.
    with pytest.raises((AssessmentRefusalError, ServiceError), match="prehistory"):
        service.assess_linear_result(project, result, service.prepare_linear_assessment(
            project, _prehistory_case_spec(prehistory_flow, None)))
    other = json.loads(json.dumps(declaration))
    other["qhat"][0][0] *= 1.001
    with pytest.raises((AssessmentRefusalError, ServiceError), match="prehistory"):
        service.assess_linear_result(project, result, service.prepare_linear_assessment(
            project, _prehistory_case_spec(prehistory_flow, other)))


def test_prehistory_reference_is_hydro_spectrum_only_and_validated():
    from anymarine.linear_assessment import _validate_reference
    declaration = {"kind": "periodic", "qhat": [[0.1, 0.0]] * 4}
    with pytest.raises(AssessmentCaseError, match="only a hydro_spectrum"):
        _validate_reference({"kind": REFERENCE_ANALYTIC_EXPONENTIAL, "coefficient_matrix": np.eye(4).tolist(),
                             "decay": 1.0, "prehistory": declaration})
    with pytest.raises(AssessmentCaseError, match="reference.prehistory"):
        _validate_reference({"kind": REFERENCE_HYDRO_SPECTRUM, "route": "derived_operators", "omega": 1.0,
                             "heading": 0.0, "wave_amplitude": 1.0, "wave_phase": 0.0,
                             "dataset_content_hash": "a" * 64, "derived_content_hash": "b" * 64,
                             "operators_raw_sha256": "c" * 64, "prehistory": dict(declaration, kind="ramp")})


@requires_solver
def test_hydro_spectrum_flow_pass(hydro_flow):
    report = hydro_flow["report"]
    assert report.verdict == "pass", report.failed
    source = report.arrays["reference_source"]
    assert source["kind"] == REFERENCE_HYDRO_SPECTRUM
    assert source["route"] == "derived_operators"
    assert source["dataset_content_hash"] == (
        hydro_flow["provenance"].dataset_content_hash
    )
    assert source["omega_index"] == 14
    assert source["direction_index"] == 0
    names = {check["name"] for check in report.checks}
    assert "hydro_impedance_sensitivity" in names
    assert "hydro_response_sensitivity" in names
    assert "kernel_self_consistency" in report.arrays
    assert report.arrays["kernel_self_consistency"][
        "note"
    ].startswith("self-consistency diagnostic only")


@requires_solver
def test_hydro_spectrum_refusals(hydro_flow, tmp_path):
    service = hydro_flow["service"]
    project = hydro_flow["project"]
    inputs = hydro_flow["inputs"]
    provenance = hydro_flow["provenance"]
    result = hydro_flow["result"]
    f_hat = hydro_flow["f_hat"]

    # No real project context: a generic case without the actual
    # ProjectModel is refused (no project, then no project root).
    case = prepare_case(
        _hydro_case_spec(project, inputs, provenance, f_hat), inputs
    )
    with pytest.raises(AssessmentRefusalError, match="ProjectModel"):
        assess_result(case, result, inputs)
    with pytest.raises(AssessmentRefusalError, match="ProjectModel"):
        assess_result(case, result, inputs, provenance=provenance)
    with pytest.raises(AssessmentRefusalError, match="project root"):
        assess_result(case, result, inputs, project=project)

    # Wrong route: the project is a derived import (the TD solve consumed
    # the processed derived arrays); a raw-route case refuses.
    raw_spec = _hydro_case_spec(
        project, inputs, provenance, f_hat, route="raw_dataset"
    )
    raw_spec["reference"].pop("derived_content_hash")
    raw_spec["reference"].pop("operators_raw_sha256")
    raw_case = prepare_case(raw_spec, inputs)
    with pytest.raises(ServiceError, match="raw route"):
        service.assess_linear_result(project, result, raw_case)

    # Wrong bound dataset identity refuses.
    wrong_hash_spec = _hydro_case_spec(
        project, inputs, provenance, f_hat, dataset_hash="e" * 64
    )
    wrong_hash_case = prepare_case(wrong_hash_spec, inputs)
    with pytest.raises(ServiceError, match="dataset content hash"):
        service.assess_linear_result(project, result, wrong_hash_case)

    # A substituted force phasor refuses.
    forged_phasors = json.loads(json.dumps(
        _hydro_case_spec(project, inputs, provenance, f_hat)
    ))
    forged_phasors["force_phasors"][0][0] += 1.0
    forged_case = prepare_case(forged_phasors, inputs)
    with pytest.raises(ServiceError, match="substituted phasor"):
        service.assess_linear_result(project, result, forged_case)

    # A missing authoritative sidecar refuses.
    sidecar = (tmp_path / "missing-sidecar")
    sidecar.mkdir()
    stored = service.store.root / provenance.dataset_sidecar
    backup = sidecar / "dataset.nc"
    shutil.move(str(stored), str(backup))
    try:
        with pytest.raises(ServiceError, match="sidecar"):
            service.assess_linear_result(project, result, case)
    finally:
        shutil.move(str(backup), str(stored))


@requires_solver
def test_hydro_sensitivity_counterexample(hydro_flow):
    service = hydro_flow["service"]
    project = hydro_flow["project"]
    inputs = hydro_flow["inputs"]
    provenance = hydro_flow["provenance"]
    result = hydro_flow["result"]
    f_hat = hydro_flow["f_hat"]
    stricter = json.loads(json.dumps(CRITERIA))
    stricter["impedance_relative_limit"] = 1.0e-14
    stricter["response_relative_limit"] = 1.0e-14
    spec = _hydro_case_spec(
        project, inputs, provenance, f_hat, criteria=stricter
    )
    case = service.prepare_linear_assessment(project, spec)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "nogo"
    assert "hydro_impedance_sensitivity" in report.failed
    sensitivity = report.arrays["hydro_sensitivity"]
    assert sensitivity["applicable"] is True
    assert sensitivity["policy"] == "reciprocal_average"
    assert len(sensitivity["q_raw"]) == 4 and len(sensitivity["q_processed"]) == 4


@requires_solver
def test_hydro_impedance_sensitivity_full_block_form(hydro_flow):
    """Prospective full-block form: ||Z_raw - Z_proc||_F / ||Z_raw||_F against the declared limit."""
    service, project = hydro_flow["service"], hydro_flow["project"]
    inputs, provenance = hydro_flow["inputs"], hydro_flow["provenance"]
    result, f_hat = hydro_flow["result"], hydro_flow["f_hat"]
    reports = {}
    for limit in (0.01, 1.0e-14):
        criteria = json.loads(json.dumps(CRITERIA))
        criteria["impedance_full_block_relative"] = limit
        case = service.prepare_linear_assessment(
            project, _hydro_case_spec(project, inputs, provenance, f_hat, criteria=criteria))
        reports[limit] = service.assess_linear_result(project, result, case)
    loose = reports[0.01].arrays["hydro_sensitivity"]["impedance"]
    assert loose["form"] == "full_block" and loose["value"] <= 0.01
    assert "hydro_impedance_sensitivity" not in reports[0.01].failed
    assert "hydro_impedance_sensitivity" in reports[1.0e-14].failed
    check = next(c for c in reports[1.0e-14].checks if c["name"] == "hydro_impedance_sensitivity")
    assert check["unit"] == "relative (Frobenius)" and "max coefficient deviation" in check["detail"]
    # The serialized report keeps the relative value's own unit next to the J diagnostics.
    stored = json.loads(json.dumps(reports[1.0e-14].arrays["hydro_sensitivity"]["impedance"]))
    assert stored["unit"].startswith("J") and stored["value_unit"].startswith("relative (Frobenius")
    # A case without the key keeps the historical per-coefficient field set.
    historical = hydro_flow["report"].arrays["hydro_sensitivity"]["impedance"]
    assert not {"form", "value", "value_unit", "max_coefficient_deviation"} & set(historical)


def test_full_block_impedance_is_frobenius_of_work_conjugate_blocks():
    """Independent norm on non-unit scales; per-coefficient fail vs full-block pass; abs allowance."""
    inputs = _regular_wave_inputs(_project())
    mass_dry = np.asarray(inputs.mass_dry, dtype=float)
    stiffness = np.asarray(inputs.stiffness, dtype=float)
    damping = np.asarray(inputs.damping, dtype=float)
    omega = HYDRO_OMEGA
    scales = np.array([1.0, 2.5, 0.4, 10.0])
    a_proc = np.diag([1.0e5, 1.2e5, 0.8e5, 1.1e5])
    b_proc = np.diag([2.0e4, 2.2e4, 1.8e4, 2.1e4])
    a_raw = a_proc.copy()
    a_raw[0, 2] = a_raw[2, 0] = 40.0   # a small coupling entry absent from the processed block
    mock = SimpleNamespace(
        raw_added_mass=a_raw[None], raw_damping=b_proc[None],
        derived_added_mass=a_proc[None], derived_damping=b_proc[None],
        settings=SimpleNamespace(correction_policy="reciprocal_average",
                                 normalization=SimpleNamespace(scales=[float(v) for v in scales])))
    f_hat = np.array([1.0e5, 2.0e5, 1.5e5, 3.0e5], dtype=np.complex128)

    def z(a_matrix, b_matrix):
        block = stiffness - omega * omega * (mass_dry + a_matrix) - 1j * omega * (b_matrix + damping)
        return np.diag(scales) @ block @ np.diag(scales)

    expected = np.linalg.norm(z(a_raw, b_proc) - z(a_proc, b_proc)) / np.linalg.norm(z(a_raw, b_proc))
    assert 0.0 < expected < 1.0e-3

    def run(**changes):
        criteria = dict(CRITERIA)
        criteria.update(changes)
        return hydro_reciprocal_sensitivity(mock, inputs, omega, 0, f_hat, criteria)["impedance"]

    per_coefficient = run()
    assert per_coefficient["passed"] is False and "form" not in per_coefficient
    full = run(impedance_full_block_relative=0.01)
    assert full["passed"] is True and full["form"] == "full_block"
    assert full["value"] == pytest.approx(expected, rel=1e-12)
    assert full["max_coefficient_deviation"] == pytest.approx(per_coefficient["max_deviation"], rel=1e-12)
    assert run(impedance_full_block_relative=0.5 * expected)["passed"] is False
    # The declared absolute allowance (J) is added to the full-block bound.
    deviation = expected * np.linalg.norm(z(a_raw, b_proc))
    assert run(impedance_full_block_relative=0.5 * expected, impedance_absolute_j=deviation)["passed"] is True


@requires_solver
def test_fd_reference_response_refuses_inputs_of_another_model(hydro_flow):
    from anymarine.linear_assessment import fd_reference_response
    case, inputs = hydro_flow["case"], hydro_flow["inputs"]
    provenance, root = hydro_flow["provenance"], hydro_flow["service"].root
    qhat = fd_reference_response(case, inputs, provenance, root)
    assert qhat.shape == (4,) and np.all(np.isfinite(qhat))
    other = dataclasses.replace(inputs, stiffness=np.asarray(inputs.stiffness, dtype=float) * 2.0)
    with pytest.raises(AssessmentRefusalError, match="input_hash"):
        fd_reference_response(case, other, provenance, root)


def test_full_block_impedance_criterion_is_optional_and_bounded():
    from anymarine.linear_assessment import validate_assessment_controls, AssessmentCaseError
    fit = {"intercepts": True, "drift": "none", "max_condition": 1.0e12}
    criteria = json.loads(json.dumps(CRITERIA))
    assert "impedance_full_block_relative" not in validate_assessment_controls(criteria, fit)["criteria"]
    criteria["impedance_full_block_relative"] = 0.005
    assert validate_assessment_controls(criteria, fit)["criteria"]["impedance_full_block_relative"] == 0.005
    criteria["impedance_full_block_relative"] = 0.02
    with pytest.raises(AssessmentCaseError, match="looser"):
        validate_assessment_controls(criteria, fit)


def test_hydro_sensitivity_singular_raw_refusal(hydro_flow):
    artifact = hydro_flow["artifact"]
    a_proc = np.asarray(artifact.derived_added_mass[0], dtype=float)
    b_proc = np.asarray(artifact.derived_damping[0], dtype=float)
    omega = 1.0
    # Zero physical matrices and a zero raw sample make Z_raw EXACTLY the
    # zero matrix (a typed singular non-success, never skip=>pass), while
    # the processed system stays regular.
    zero_inputs = SimpleNamespace(
        mass_dry=np.zeros((4, 4)),
        stiffness=np.zeros((4, 4)),
        damping=np.zeros((4, 4)),
    )
    mock = SimpleNamespace(
        raw_added_mass=np.zeros((1, 4, 4)),
        raw_damping=np.zeros((1, 4, 4)),
        derived_added_mass=a_proc[None],
        derived_damping=b_proc[None],
        settings=SimpleNamespace(
            correction_policy="reciprocal_average",
            normalization=SimpleNamespace(scales=[1.0, 1.0, 1.0, 1.0]),
        ),
    )
    f_hat = np.ones(4, dtype=np.complex128)
    with pytest.raises(AssessmentRefusalError, match="singular"):
        hydro_reciprocal_sensitivity(
            mock, zero_inputs, omega, 0, f_hat, dict(CRITERIA)
        )


def test_hydro_sensitivity_nonunit_scales():
    """The response change is the DIMENSIONLESS S^-1 qhat, not retained SI.

    A non-unit normalization fixture shows the proper response scale/units:
    the compared deviation is |S^-1(q_raw - q_processed)| with the declared
    dimensionless floor/abs, while the retained SI solutions are recorded
    separately and the work-conjugate impedance stays in J.
    """

    inputs = _regular_wave_inputs(_project())
    mass_dry = np.asarray(inputs.mass_dry, dtype=float)
    stiffness = np.asarray(inputs.stiffness, dtype=float)
    damping = np.asarray(inputs.damping, dtype=float)
    omega = HYDRO_OMEGA
    scales = np.array([1.0, 2.5, 0.4, 10.0])
    a_proc = np.diag([1.0e5, 1.2e5, 0.8e5, 1.1e5])
    b_proc = np.diag([2.0e4, 2.2e4, 1.8e4, 2.1e4])
    a_raw = a_proc.copy()
    a_raw[3, 3] *= 1.001  # perturb the DOF whose normalization scale is 10
    b_raw = b_proc.copy()
    mock = SimpleNamespace(
        raw_added_mass=a_raw[None],
        raw_damping=b_raw[None],
        derived_added_mass=a_proc[None],
        derived_damping=b_proc[None],
        settings=SimpleNamespace(
            correction_policy="reciprocal_average",
            normalization=SimpleNamespace(
                scales=[float(value) for value in scales]
            ),
        ),
    )
    f_hat = np.array([1.0e5, 2.0e5, 1.5e5, 3.0e5], dtype=np.complex128)

    def impedance(a_matrix, b_matrix):
        return (
            stiffness
            - omega * omega * (mass_dry + a_matrix)
            - 1j * omega * (b_matrix + damping)
        )

    q_raw_si = np.linalg.solve(impedance(a_raw, b_raw), f_hat)
    q_processed_si = np.linalg.solve(impedance(a_proc, b_proc), f_hat)
    result = hydro_reciprocal_sensitivity(
        mock, inputs, omega, 0, f_hat, dict(CRITERIA)
    )
    assert result["response"]["unit"] == "dimensionless (S^-1 qhat)"
    assert result["impedance"]["unit"] == "J (work-conjugate S^T Z S entries)"
    assert result["normalization_scales"] == [float(v) for v in scales]
    stored_raw_si = np.asarray(
        [complex(*pair) for pair in result["q_raw_si"]], dtype=complex
    )
    stored_processed_si = np.asarray(
        [complex(*pair) for pair in result["q_processed_si"]], dtype=complex
    )
    np.testing.assert_allclose(stored_raw_si, q_raw_si, rtol=1.0e-9)
    np.testing.assert_allclose(stored_processed_si, q_processed_si, rtol=1.0e-9)
    stored_raw = np.asarray(
        [complex(*pair) for pair in result["q_raw"]], dtype=complex
    )
    stored_processed = np.asarray(
        [complex(*pair) for pair in result["q_processed"]], dtype=complex
    )
    np.testing.assert_allclose(stored_raw, q_raw_si / scales, rtol=1.0e-9)
    np.testing.assert_allclose(
        stored_processed, q_processed_si / scales, rtol=1.0e-9
    )
    expected_dimensionless = float(
        np.max(np.abs((q_raw_si - q_processed_si) / scales))
    )
    assert result["response"]["max_deviation"] == pytest.approx(
        expected_dimensionless, rel=1.0e-9
    )
    # Per-DOF scale/units proof: the dimensionless deviation on each DOF
    # is the retained SI deviation divided by that DOF's declared scale,
    # so a non-unit S fixture compares the proper dimensionless response.
    si_per_dof = np.abs(q_raw_si - q_processed_si)
    dim_per_dof = np.abs((q_raw_si - q_processed_si) / scales)
    np.testing.assert_allclose(
        np.asarray(
            [complex(*pair) for pair in result["q_raw"]]
        ) - np.asarray([complex(*pair) for pair in result["q_processed"]]),
        (q_raw_si - q_processed_si) / scales,
        rtol=1.0e-9,
    )
    assert float(np.max(si_per_dof)) >= expected_dimensionless
    # The perturbed DOF (scale 10) shows the scale/units relationship
    # explicitly: its SI deviation is exactly 10x the compared one.
    assert si_per_dof[3] == pytest.approx(10.0 * dim_per_dof[3], rel=1.0e-12)


@requires_solver
def test_hydro_core_source_proof_self_sufficient(hydro_flow, tmp_path):
    """The generic core re-proves the actual sources; no caller gate needed.

    A direct assess_result call with the real project context performs the
    actual dataset/operators/kernel source verification itself, and a
    tampered kernel sidecar is refused by the core even though the
    provenance bookkeeping still matches.
    """

    service = hydro_flow["service"]
    project = hydro_flow["project"]
    inputs = hydro_flow["inputs"]
    provenance = hydro_flow["provenance"]
    case = hydro_flow["case"]
    result = hydro_flow["result"]

    report = assess_result(
        case, result, inputs,
        project=project, provenance=provenance, project_root=service.root,
    )
    assert report.verdict == "pass", report.failed
    assert report.arrays["reference_source"]["kind"] == REFERENCE_HYDRO_SPECTRUM

    kernel_sidecar = service.store.root / provenance.kernel_sidecar
    backup = tmp_path / "kernel-backup.nc"
    shutil.copy(str(kernel_sidecar), str(backup))
    payload = bytearray(kernel_sidecar.read_bytes())
    payload[-1] ^= 0xFF
    kernel_sidecar.write_bytes(bytes(payload))
    try:
        with pytest.raises(AssessmentRefusalError, match="raw sha256"):
            assess_result(
                case, result, inputs,
                project=project, provenance=provenance,
                project_root=service.root,
            )
    finally:
        shutil.copy(str(backup), str(kernel_sidecar))


@requires_solver
def test_hydro_core_refuses_rehashed_substituted_matrices(hydro_flow):
    """Re-hashed bookkeeping over substituted matrices is refused.

    The actual dataset/operators/kernel sidecars stay valid and the case
    and provenance hashes are recomputed consistently over a substituted
    mechanical damping, with a real core result of the substituted
    inputs: the core still refuses, because a hydro assessment requires
    the actual ProjectModel whose stored inputs are the assessed inputs
    and whose provenance re-verifies against the authoritative sources.
    """

    service = hydro_flow["service"]
    project = hydro_flow["project"]
    inputs = hydro_flow["inputs"]
    provenance = hydro_flow["provenance"]
    f_hat = hydro_flow["f_hat"]

    forged_inputs = dataclasses.replace(inputs, damping=np.eye(4) * 10000.0)
    forged_provenance = dataclasses.replace(
        provenance, linear_input_hash=linear_input_hash(forged_inputs)
    )
    spec = json.loads(json.dumps(
        _hydro_case_spec(project, forged_inputs, forged_provenance, f_hat)
    ))
    case = prepare_case(spec, forged_inputs)
    from anymarine.linear import run_linear

    result = run_linear(forged_inputs)
    # Without the actual project the generic core refuses.
    with pytest.raises(AssessmentRefusalError, match="ProjectModel"):
        assess_result(
            case, result, forged_inputs,
            provenance=forged_provenance, project_root=service.root,
        )
    # With the actual project the substituted effective matrices are
    # refused: the assessed inputs are not the project's stored inputs.
    with pytest.raises(AssessmentRefusalError, match="stored linear inputs"):
        assess_result(
            case, result, forged_inputs,
            project=project, provenance=forged_provenance,
            project_root=service.root,
        )


@requires_solver
def test_hydro_snapshot_self_contained(hydro_flow, tmp_path):
    service = hydro_flow["service"]
    project = hydro_flow["project"]
    case = hydro_flow["case"]
    result = hydro_flow["result"]
    report = hydro_flow["report"]

    snapshot_root = tmp_path / "hydro-snapshot"
    service.save_assessment_snapshot(
        project, result, case, report, snapshot_root
    )
    case_path = next((snapshot_root / "cases").glob("*.json"))
    report_path = next((snapshot_root / "reports").glob("*.json"))

    # The snapshot must no longer depend on the original project or the
    # original source files.
    shutil.rmtree(service.store.root)
    for path in (
        hydro_flow["dataset_path"],
        hydro_flow["operators_path"],
        hydro_flow["kernel_path"],
    ):
        path.unlink()

    reloaded = ApplicationService()
    outcome = reloaded.adjudicate_linear_assessment(
        snapshot_root, case_path, report_path
    )
    assert outcome["agrees"] is True
    assert outcome["verdict"] == report.verdict


@requires_solver
def test_hydro_raw_route_spectrum(tmp_path):
    import anyhydro

    project = _hydro_project()
    dataset = _hydro_dataset(project, eps=0.0)
    _, _, _, _, _, coupling = _hydro_fixture_arrays(eps=0.0)
    kernel = anyhydro.radiation_kernel_from_dataset(
        dataset,
        anyhydro.RadiationSettings(
            dt=HYDRO_DT,
            n_time=601,
            symmetry_tol=1.0e-6,
            passivity_tol=1.0e-6,
            low_tail_policy="explicit_zero_sample",
            zero_frequency_damping=coupling / 1.5,
        ),
    )
    assert kernel.diagnostics.damping_symmetry_violations == 0
    root = tmp_path / "raw"
    root.mkdir()
    dataset_path = root / "dataset.nc"
    kernel_path = root / "kernel.nc"
    anyhydro.save_hydro_dataset(dataset, dataset_path)
    anyhydro.save_radiation_kernel(kernel, kernel_path)

    omegas = np.asarray(dataset["omega"].values, dtype=float)
    directions = np.asarray(
        dataset["wave_direction"].values, dtype=float
    )
    omega_index = int(np.nonzero(omegas == HYDRO_OMEGA)[0][0])
    direction_index = int(np.nonzero(directions == HYDRO_HEADING)[0][0])
    projection = np.zeros((12, 4))
    for column, row in enumerate((0, 2, 6, 8)):
        projection[row, column] = 1.0
    a_omega = projection.T @ np.asarray(
        dataset["added_mass"].values[omega_index]
    ) @ projection
    b_omega = projection.T @ np.asarray(
        dataset["radiation_damping"].values[omega_index]
    ) @ projection
    f_hat_12 = np.asarray(
        dataset["excitation_force"].values[omega_index, direction_index, :],
        dtype=np.complex128,
    )
    f_hat = HYDRO_AMPLITUDE * np.exp(1j * HYDRO_PHASE) * (
        projection.T @ f_hat_12
    )
    stiffness = projection.T @ np.asarray(
        dataset["hydrostatic_stiffness"].values
    ) @ projection
    mass_dry = np.diag([2.4e6, 2.4e6, 1.8e6, 1.8e6])
    impedance = (
        stiffness
        - HYDRO_OMEGA**2 * (mass_dry + a_omega)
        - 1j * HYDRO_OMEGA * b_omega
    )
    qhat = np.linalg.solve(impedance, f_hat)
    q0 = qhat.real
    v0 = (-1j * HYDRO_OMEGA * qhat).real

    service = ApplicationService(tmp_path / "raw-project")
    service.save(project)
    options = {
        "n_steps": HYDRO_N_STEPS,
        "criteria": dict(HYDRO_CRITERIA),
        "wave": {
            "omega": HYDRO_OMEGA,
            "heading": HYDRO_HEADING,
            "amplitude": HYDRO_AMPLITUDE,
            "phase": HYDRO_PHASE,
        },
        "q0": [float(v) for v in q0],
        "v0": [float(v) for v in v0],
    }
    project = service.import_hydro_inputs(
        project, dataset_path, kernel_path, options
    )
    service.save(project)
    inputs = project.linear_inputs
    provenance = project.hydro_provenance
    spec = _hydro_case_spec(
        project, inputs, provenance, f_hat, route="raw_dataset"
    )
    spec["reference"].pop("derived_content_hash")
    spec["reference"].pop("operators_raw_sha256")
    case = service.prepare_linear_assessment(project, spec)
    result = service.run_linear_local(project)
    report = service.assess_linear_result(project, result, case)
    assert report.verdict == "pass", report.failed
    assert report.arrays["reference_source"]["route"] == "raw_dataset"
    sensitivity = [
        c for c in report.checks if c["name"] == "hydro_reciprocal_sensitivity"
    ][0]
    assert "not applicable" in sensitivity["detail"]
