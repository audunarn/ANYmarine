"""rigid_subset_v1 linear runs (ADR-015 slice L6-2): analytic table (section 8), result mapping, decoder, controls.

Analytic cases use a zero radiation kernel and a body whose CG is at the reference point with diagonal inertia
(uncoupled DOFs) unless stated; the coupled case uses an offset CG (surge-pitch dry-mass coupling).
"""
from __future__ import annotations

import dataclasses
import math
import threading
import time

import numpy as np
import pytest

from conftest import requires_solver

from anymarine.domain import (
    LINEAR_LAYOUT_RIGID, LinearDevelopmentInputs, LinearExecutionLimits, LinearProvenance, ResultRecord,
    default_two_body_project,
)
from anymarine.hashing import equilibrium_hash
from anymarine.linear import (
    LinearCancelledError, LinearResultIncomplete, LinearWallTimeExceeded, rigid_binding, rigid_dry_mass,
    rotation_from_vector, run_linear, verify_linear_result,
)
from anymarine.mass_properties import BodyMassProperties
from anymarine.persistence import ProjectStore

pytestmark = requires_solver

ALL = ("surge", "sway", "heave", "roll", "pitch", "yaw")
DIAGONAL_INERTIA = ((4.0e5, 0.0, 0.0), (0.0, 6.0e5, 0.0), (0.0, 0.0, 7.0e5))


def _one_body_project(cg=(0.0, 0.0, 0.0), inertia=DIAGONAL_INERTIA, orientation=None):
    project = default_two_body_project()
    body = dataclasses.replace(project.bodies[0], mass_properties=BodyMassProperties(cg, inertia))
    if orientation is not None:
        body = dataclasses.replace(body, reference_orientation=orientation)
    return dataclasses.replace(project, bodies=(body,))


def _inputs(project, dofs, *, dt, n_steps, stiffness, damping, forces=None, q0=None, v0=None, limits=None):
    bindings = (rigid_binding(project.bodies[0], dofs),)
    M = rigid_dry_mass(bindings)
    n = M.shape[0]
    frames = max(n_steps, 2)
    limits = limits or LinearExecutionLimits(max_frames=max(frames, 1001))
    return LinearDevelopmentInputs(
        bindings=bindings, dt=dt, n_steps=n_steps,
        forces=(np.zeros((n_steps, n)) if forces is None else forces).tolist(), mass_dry=M.tolist(),
        added_mass_infinite=np.zeros((n, n)).tolist(), damping=np.asarray(damping).tolist(),
        stiffness=np.asarray(stiffness).tolist(), radiation_kernel=np.zeros((n_steps, n, n)).tolist(),
        kernel_times=[k * dt for k in range(n_steps)], equilibrium_hash=equilibrium_hash(project.equilibrium),
        provenance=LinearProvenance(source="explicit", description="ADR-015 section 8 analytic case"),
        q0=None if q0 is None else list(q0), v0=None if v0 is None else list(v0),
        execution_limits=limits, layout=LINEAR_LAYOUT_RIGID)


def _q(result, inputs):
    return np.column_stack([np.asarray(result.channels[f"q:{label}"]) for label in inputs.labels])


def _rotation(axis, angle):
    k = np.asarray(axis, float) / np.linalg.norm(axis)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return tuple(tuple(float(v) for v in row) for row in np.eye(3) + math.sin(angle) * K
                 + (1 - math.cos(angle)) * K @ K)


# ---------------------------------------------------------------- (i) undamped free oscillation

@pytest.mark.parametrize("dof", ["heave", "roll"])
def test_undamped_free_oscillation_conserves_amplitude_and_newmark_period(dof):
    T = 10.0
    dt = T / 200.0
    n_steps = 40 * 200 + 1
    project = _one_body_project()
    M = rigid_dry_mass((rigid_binding(project.bodies[0], ALL),))
    K = np.diag(np.diag(M)) * (2 * math.pi / T) ** 2
    amplitude = 0.05 if dof in ("surge", "sway", "heave") else 0.005
    q0 = np.zeros(6)
    q0[ALL.index(dof)] = amplitude
    inputs = _inputs(project, ALL, dt=dt, n_steps=n_steps, stiffness=K, damping=np.zeros((6, 6)), q0=q0)
    result = run_linear(inputs, producing_bodies=project.bodies)
    q = _q(result, inputs)[:, ALL.index(dof)]
    phi = 2.0 * math.atan(math.pi / T * dt)                  # trapezoidal Newmark: tan(phi/2) = w dt / 2
    n = np.arange(n_steps)
    assert np.max(np.abs(q - amplitude * np.cos(n * phi))) <= 1e-10 * amplitude
    cos_phi = np.sum(q[1:-1] * (q[2:] + q[:-2])) / (2.0 * np.sum(q[1:-1] ** 2))
    measured_period = 2 * math.pi * dt / math.acos(cos_phi)
    assert measured_period == pytest.approx(2 * math.pi * dt / phi, rel=1e-9)
    others = np.delete(_q(result, inputs), ALL.index(dof), axis=1)
    assert np.max(np.abs(others)) == 0.0                        # uncoupled


# ---------------------------------------------------------------- (ii)/(iii) damped forced response

def _fit_phasor(q, omega, times):
    """Least-squares X with q = Re{X exp(-i w t)} = Re X cos(wt) + Im X sin(wt)."""
    A = np.column_stack([np.cos(omega * times), np.sin(omega * times)])
    coeff, *_ = np.linalg.lstsq(A, q, rcond=None)
    return complex(coeff[0], coeff[1])


def _forced_case(project, dofs, M, K, C, F, omega, dt, n_steps):
    r_n = 2.0 / dt * math.tan(omega * dt / 2.0)
    X_d = np.linalg.solve(K - r_n ** 2 * M - 1j * r_n * C, F)
    X_c = np.linalg.solve(K - omega ** 2 * M - 1j * omega * C, F)
    times = dt * np.arange(n_steps)
    forces = np.real(F[None, :] * np.exp(-1j * omega * times)[:, None])
    inputs = _inputs(project, dofs, dt=dt, n_steps=n_steps, stiffness=K, damping=C, forces=forces,
                     q0=np.real(X_d), v0=np.real(-1j * r_n * X_d))
    result = run_linear(inputs, producing_bodies=project.bodies)
    q = _q(result, inputs)
    fitted = np.array([_fit_phasor(q[:, k], omega, times) for k in range(len(dofs))])
    return X_d, X_c, fitted, q, times


# ADR-015 section 8 (ii): the exact predicted difference |X_d|/|X| - 1 between the discrete (Newmark) and the
# continuous transfer function, tabulated before running (normalised by the continuous amplitude |X|; zeta 0.05,
# dt = T/200). TD vs continuous must reproduce it within 1e-6 relative to the tabulated difference itself.
PREDICTED_DISCRETE_DIFFERENCE = {0.5: 1.3556805114278703e-05, 0.9: 4.5194418880578624e-04,
                                 1.5: -6.591423029602694e-04}


@pytest.mark.parametrize("dof", ["surge", "pitch"])
@pytest.mark.parametrize("ratio", [0.5, 0.9, 1.5])
def test_damped_forced_response_equals_the_discrete_particular_solution(dof, ratio):
    project = _one_body_project()
    M = rigid_dry_mass((rigid_binding(project.bodies[0], (dof,)),))
    T = 12.0
    wn = 2 * math.pi / T
    K = M * wn ** 2
    C = 2 * 0.05 * np.sqrt(K * M)
    F = np.array([0.3 * K[0, 0] * (0.05 if dof == "surge" else 0.005) * (1 + 0.5j)])
    omega = ratio * wn
    dt = T / 200.0
    X_d, X_c, fitted, q, times = _forced_case(project, (dof,), M, K, C, F, omega, dt, 20 * 200 + 1)
    assert abs(abs(fitted[0]) - abs(X_d[0])) <= 1e-9 * abs(X_d[0])
    assert abs(np.angle(fitted[0] / X_d[0])) <= 1e-7
    assert np.max(np.abs(q[:, 0] - np.real(X_d[0] * np.exp(-1j * omega * times)))) <= 1e-9 * abs(X_d[0])
    predicted = PREDICTED_DISCRETE_DIFFERENCE[ratio]
    assert abs(X_d[0]) / abs(X_c[0]) - 1.0 == pytest.approx(predicted, rel=1e-12)     # the frozen table holds
    assert abs((abs(fitted[0]) / abs(X_c[0]) - 1.0) - predicted) <= 1e-6 * abs(predicted)


def test_coupled_two_dof_forced_response_matches_independent_phasor_solution():
    project = _one_body_project(cg=(0.0, 0.0, -1.5))         # offset CG couples surge and pitch in the dry mass
    dofs = ("surge", "pitch")
    M = rigid_dry_mass((rigid_binding(project.bodies[0], dofs),))
    assert abs(M[0, 1]) > 0
    K = np.diag([M[0, 0] * (2 * math.pi / 20.0) ** 2, M[1, 1] * (2 * math.pi / 9.0) ** 2])
    C = 0.05 * np.diag(2 * np.sqrt(np.diag(K) * np.diag(M)))
    F = np.array([2.0e4 + 1.0e4j, 5.0e4 - 2.0e4j])
    omega = 2 * math.pi / 11.0
    dt = 9.0 / 200.0
    X_d, X_c, fitted, q, times = _forced_case(project, dofs, M, K, C, F, omega, dt, 4001)
    scale = np.max(np.abs(X_d))
    assert np.max(np.abs(fitted - X_d)) <= 1e-9 * scale
    predicted = np.abs(X_d) / np.abs(X_c) - 1.0                     # computed before comparing with the run
    assert np.max(np.abs((np.abs(fitted) / np.abs(X_c) - 1.0) - predicted) / np.abs(predicted)) <= 1e-6


# ---------------------------------------------------------------- mapping, decoder, persistence

def _mixed_run(orientation=None, amplitude=(0.2, -0.1, 0.05, 0.02, -0.03, 0.04), n_steps=201):
    project = _one_body_project(cg=(0.3, -0.2, -1.0), orientation=orientation)
    M = rigid_dry_mass((rigid_binding(project.bodies[0], ALL),))
    K = np.diag(np.diag(M)) * (2 * math.pi / 8.0) ** 2
    inputs = _inputs(project, ALL, dt=0.05, n_steps=n_steps, stiffness=K,
                     damping=0.02 * np.diag(np.diag(M)), q0=amplitude)
    return project, inputs, run_linear(inputs, producing_bodies=project.bodies)


def test_mixed_axis_rotations_map_to_exp_theta_r0_about_the_reference_point():
    from scipy.spatial.transform import Rotation
    R0 = _rotation((0.2, 1.0, -0.4), 0.3)
    project, inputs, result = _mixed_run(orientation=R0)
    assert result.result_layout == LINEAR_LAYOUT_RIGID and dict(result.orientations) == {}
    body = project.bodies[0]
    q = _q(result, inputs)
    for frame in (0, 57, 200):
        expected = Rotation.from_rotvec(q[frame, 3:]).as_matrix() @ np.asarray(R0)
        assert np.allclose(np.asarray(result.rotation_matrices[body.body_id][frame]), expected, rtol=0, atol=1e-14)
        assert np.allclose(result.positions[body.body_id][frame], np.asarray(body.reference_position) + q[frame, :3],
                           rtol=0, atol=0)
    assert np.allclose(rotation_from_vector([1e-10, 0, 0]), Rotation.from_rotvec([1e-10, 0, 0]).as_matrix(),
                       rtol=0, atol=1e-16)


def test_rigid_result_round_trips_through_a_schema_10_project(tmp_path):
    project, inputs, result = _mixed_run()
    project = dataclasses.replace(project, linear_inputs=inputs, results=(result,))
    ProjectStore(tmp_path).save(project)
    loaded = ProjectStore(tmp_path).load()
    assert loaded.results[0] == result
    verify_linear_result(loaded.results[0], loaded.linear_inputs)
    # the retained result alone keeps schema 10 when the inputs are cleared
    ProjectStore(tmp_path).save(dataclasses.replace(loaded, linear_inputs=None))
    import json
    assert json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))["schema_version"] == 10


@pytest.mark.parametrize("tamper", ["matrix", "position", "flag", "truncate", "snapshot_scale"])
def test_tampered_or_truncated_rigid_results_are_refused(tamper):
    _, inputs, result = _mixed_run()
    data = result.to_dict()
    body = result.body_ids[0]
    if tamper == "matrix":
        data["rotation_matrices"][body][10] = [list(r) for r in rotation_from_vector([0.0, 0.0, 0.01])]
    elif tamper == "position":
        data["positions"][body][10][0] += 1e-9
    elif tamper == "flag":
        data["channels"][f"envelope_exceeded:{body}"][10] = 1.0
    elif tamper == "truncate":
        for key in ("times",):
            data[key] = data[key][:-1]
        data["positions"][body] = data["positions"][body][:-1]
        data["rotation_matrices"][body] = data["rotation_matrices"][body][:-1]
        data["channels"] = {k: v[:-1] for k, v in data["channels"].items()}
    else:
        data["linear_snapshot"]["envelope"]["length_scale_m"][body] *= 2
    # refused at the decoding boundary (kinematics) or by the run verifier (snapshot/envelope binding)
    with pytest.raises((ValueError, LinearResultIncomplete)):
        verify_linear_result(ResultRecord.from_dict(data), inputs)


@pytest.mark.parametrize("tamper", ["matrix", "position", "flag"])
def test_contradictory_kinematics_are_refused_when_decoding(tamper):
    """Review round 1: persistence/worker decoding itself refuses q/matrix/position contradictions."""
    _, _, result = _mixed_run()
    data = result.to_dict()
    body = result.body_ids[0]
    if tamper == "matrix":
        data["rotation_matrices"][body][10] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    elif tamper == "position":
        data["positions"][body][10][2] += 1e-6
    else:
        data["channels"][f"envelope_exceeded:{body}"][3] = 1.0
    with pytest.raises(ValueError):
        ResultRecord.from_dict(data)


def _consistent_motionless(result, data):
    """A record with zero q/v/a and kinematics consistent with zero motion (passes the decoding boundary)."""
    body = result.body_ids[0]
    snapshot = result.linear_snapshot
    frames = len(result.times)
    for name in list(data["channels"]):
        if name.split(":", 1)[0] in ("q", "v", "a", "envelope_exceeded"):
            data["channels"][name] = [0.0] * frames
    item = snapshot["bodies"][0]
    data["positions"][body] = [list(item["reference_position"])] * frames
    R0 = item.get("reference_orientation", [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    data["rotation_matrices"][body] = [[list(map(float, row)) for row in R0]] * frames
    return data


@pytest.mark.parametrize("tamper", ["motionless", "velocity", "acceleration", "memory", "external"])
def test_histories_that_are_not_the_run_are_refused(tamper):
    """Review round 1: the verifier proves the stored history is the Newmark solution of the inputs."""
    _, inputs, result = _mixed_run()
    data = result.to_dict()
    label = inputs.labels[3]
    if tamper == "motionless":
        data = _consistent_motionless(result, data)
    elif tamper == "velocity":
        data["channels"][f"v:{label}"][50] += 1e-6
    elif tamper == "acceleration":
        data["channels"][f"a:{label}"][50] *= 1.001
    elif tamper == "memory":
        data["channels"][f"force_memory:{label}"][50] += 1.0
    else:
        data["channels"][f"force_external:{label}"][50] += 1.0
    with pytest.raises(LinearResultIncomplete):
        verify_linear_result(ResultRecord.from_dict(data), inputs)


def test_small_angle_envelope_labels_frames_outside_it():
    _, inputs, result = _mixed_run(amplitude=(0.0, 0.0, 0.0, 0.3, 0.0, 0.0))
    flags = result.channels[f"envelope_exceeded:{result.body_ids[0]}"]
    assert flags[0] == 1.0 and 0.0 in flags                     # 0.3 rad > 0.2 rad at start, inside later on
    assert result.linear_snapshot["envelope"]["max_rotation_rad"] == 0.2


def test_rigid_runs_need_the_producing_bodies():
    from anymarine.linear import LinearBindingError
    project, inputs, _ = _mixed_run()
    with pytest.raises(LinearBindingError, match="producing bodies"):
        run_linear(inputs)
    edited = dataclasses.replace(project.bodies[0], mass=project.bodies[0].mass * 2)
    with pytest.raises(LinearBindingError):
        run_linear(inputs, producing_bodies=(edited,))


# ---------------------------------------------------------------- controls (ADR-015 section 9)

def test_cancellation_produces_no_result():
    project, inputs, _ = _mixed_run()
    with pytest.raises(LinearCancelledError):
        run_linear(inputs, cancel=lambda: True, producing_bodies=project.bodies)


def _large_inputs(limits):
    project = _one_body_project(cg=(0.3, -0.2, -1.0))
    M = rigid_dry_mass((rigid_binding(project.bodies[0], ALL),))
    K = np.diag(np.diag(M)) * (2 * math.pi / 10.0) ** 2
    n_steps = 8801                                         # L6-F1 coarse size: 880 s at dt 0.1 s
    times = 0.1 * np.arange(n_steps)
    kernel = (np.exp(-times / 8.0) * np.cos(0.6 * times))[:, None, None] * (0.05 * np.diag(np.diag(M)))[None]
    inputs = LinearDevelopmentInputs(
        bindings=(rigid_binding(project.bodies[0], ALL),), dt=0.1, n_steps=n_steps,
        forces=np.zeros((n_steps, 6)).tolist(), mass_dry=M.tolist(), added_mass_infinite=np.zeros((6, 6)).tolist(),
        damping=(0.01 * np.diag(np.diag(M))).tolist(), stiffness=K.tolist(), radiation_kernel=kernel.tolist(),
        kernel_times=times.tolist(), equilibrium_hash=equilibrium_hash(project.equilibrium),
        provenance=LinearProvenance(source="explicit", description="L6-2 control test"),
        q0=[0.05, 0, 0, 0.005, 0, 0], execution_limits=limits, layout=LINEAR_LAYOUT_RIGID)
    return project, inputs


def test_cancellation_latency_is_below_one_second_at_the_feasibility_size():
    project, inputs = _large_inputs(LinearExecutionLimits(max_frames=8801))
    event = threading.Event()
    stamp = {}

    def fire():
        stamp["request"] = time.perf_counter()
        event.set()
    timer = threading.Timer(0.5, fire)
    timer.start()
    try:
        with pytest.raises(LinearCancelledError):
            run_linear(inputs, cancel=event.is_set, producing_bodies=project.bodies)
    finally:
        timer.cancel()
    assert "request" in stamp and time.perf_counter() - stamp["request"] <= 1.0


def test_wall_time_also_bounds_result_materialisation(monkeypatch):
    """Review round 1: a run whose solve finishes in time but whose post-processing does not is refused."""
    import anymarine.linear as linear_module
    project, inputs, _ = _mixed_run()
    inputs = dataclasses.replace(inputs, execution_limits=LinearExecutionLimits(max_frames=1001, max_wall_seconds=0.2))
    original = linear_module.rigid_kinematics

    def slow(*args, **kwargs):
        time.sleep(0.15)
        return original(*args, **kwargs)
    monkeypatch.setattr(linear_module, "rigid_kinematics", slow)
    with pytest.raises(LinearWallTimeExceeded):
        run_linear(inputs, producing_bodies=project.bodies)


def test_cli_child_deadline_follows_the_wall_time_limit(monkeypatch, tmp_path):
    import subprocess
    import anymarine.cli as cli
    from anymarine.services import ApplicationService, ServiceError
    project, inputs, _ = _mixed_run()
    inputs = dataclasses.replace(inputs, execution_limits=LinearExecutionLimits(max_frames=1001, max_wall_seconds=600))
    project = dataclasses.replace(project, linear_inputs=inputs)
    seen = {}

    def fake_run(command, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    with pytest.raises(ServiceError, match="deadline"):
        cli._run_linear_child_job(ApplicationService(), project, inputs)
    assert seen["timeout"] == 600 + cli.WORKER_TIMEOUT_SECONDS


def test_wall_time_limit_is_enforced():
    project, inputs = _large_inputs(LinearExecutionLimits(max_frames=8801, max_wall_seconds=0.05))
    with pytest.raises(LinearWallTimeExceeded, match="max_wall_seconds"):
        run_linear(inputs, producing_bodies=project.bodies)


# ---------------------------------------------------------------- worker path

def test_worker_runs_and_the_service_decodes_a_rigid_job(tmp_path):
    from anymarine.services import ApplicationService
    from test_worker_linear import run_worker, worker_env
    project, inputs, _ = _mixed_run()
    project = dataclasses.replace(project, linear_inputs=inputs)
    service = ApplicationService()
    snapshot = service.linear_job_snapshot(project, inputs)
    input_path, output_path = tmp_path / "input.json", tmp_path / "output.json"
    service.write_job_input(snapshot, input_path)
    completed = run_worker(input_path, output_path, None, worker_env())
    assert completed.returncode == 0, completed.stderr
    result = service.load_job_output(output_path, snapshot["input_hash"])
    assert result.result_layout == LINEAR_LAYOUT_RIGID
    verify_linear_result(result, inputs)
