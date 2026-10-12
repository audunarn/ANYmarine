"""RAO slice 5: time-domain extraction against the FD solve of the same linear model."""
import math

import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mechanics import prepare_native_nodes, prepare_native_run
from anymarine.native_examples import fluid_two_body_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.domain import AnalysisSettings
from anymarine import rao_extraction as rx
from anymarine import rao_linearize as rl

OMEGA, DT, B, K_REST, WINDOW_CYCLES, TRANSIENT = 1.0, 0.05, 24.0, 100.0, 3, 40.0


def oscillator_project():
    p = fluid_two_body_project()
    data = p.native_inputs.to_dict()
    flow = data['potential_flow']
    n = 12
    eye = np.eye(n)
    for frame in flow['frames']:
        data['initial_states'][frame['body_id']].update(position=list(frame['equilibrium_position']),
                                                        velocity_world=[0.0] * 6)
    # Two-sample kernel K0 = 2B/dt: radiation dt/2 K0 v = B v, a pure linear damper.
    flow.update(dt=DT, omega=OMEGA, added_mass_infinite=np.zeros((n, n)).tolist(),
                restoring=(K_REST * eye).tolist(),
                kernel=[(2 * B / DT * eye).tolist(), np.zeros((n, n)).tolist()],
                excitation_real=[0.4 if i == 0 else 0.0 for i in range(n)],
                excitation_imag=[0.1 if i == 0 else 0.0 for i in range(n)])
    length = TRANSIENT + 2 * WINDOW_CYCLES * 2 * math.pi / OMEGA
    steps = int(math.ceil(length / DT))
    data['run_spec'].update(dt=DT, steps=steps)
    return p.with_edits(settings=AnalysisSettings(duration=steps * DT, dt=DT),
                        native_inputs=NativeMechanicalInputs.from_dict(data))


def solve_record(project):
    from anymarine.services import ApplicationService
    from anymarine.native_jobs import project_from_snapshot, record_from_evidence
    import anysolver.marine_mechanical as solver
    steps = project.native_inputs.run_spec['steps']
    snapshot = ApplicationService().native_input_snapshot(project, max_frames=steps + 1, wall_seconds=600.)
    produced = project_from_snapshot(snapshot)
    model, states, spec = prepare_native_run(produced)
    result = solver.solve_marine_mechanical(model, states, spec, initial_nodes=prepare_native_nodes(produced))
    return record_from_evidence(solver.result_to_dict(result), snapshot, result_id='rao-oscillator',
                                note='test', created_utc='')


@requires_solver
def test_time_domain_matches_the_newmark_consistent_fd_solve():
    project = oscillator_project()
    record = solve_record(project)
    flow = project.native_inputs.potential_flow.to_dict()
    anchors = {f['body_id']: (f['equilibrium_position'], np.eye(3)) for f in flow['frames']}
    ex = rx.extract_run(record, omega=OMEGA, amplitude=1.0, anchors=anchors,
                        transient_seconds=TRANSIENT, window_cycles=WINDOW_CYCLES, harmonics=2)

    calm_model, calm_states, calm_spec = prepare_native_run(rl.wave_free_project(project))
    L = rl.linearize(calm_model, calm_states, dt=calm_spec.dt)
    model, states, spec = prepare_native_run(project)
    pf = rl.potential_flow_contribution(model)
    X = rl.potential_flow_excitation(pf, states, OMEGA)
    r = rl.newmark_effective_omega(OMEGA, DT)
    z_newmark = rl.radiation_impedance(pf, states, OMEGA, 'kernel_discrete', rate_omega=r)
    q_newmark = L.solve(OMEGA, X, {rl.POTENTIAL_FLOW_ID: z_newmark}, rate_omega=r)
    scale = np.max(np.abs(q_newmark))
    np.testing.assert_allclose(ex['response'], q_newmark, rtol=0, atol=1e-7 * scale)
    assert np.max(ex['steadiness'][np.abs(q_newmark) > 1e-3 * scale]) < 1e-8

    # The physical FD solve differs only by the Newmark rate error O((w dt)^2).
    z = rl.radiation_impedance(pf, states, OMEGA, 'kernel_discrete')
    q = L.solve(OMEGA, X, {rl.POTENTIAL_FLOW_ID: z})
    gap = np.max(np.abs(ex['response'] - q)) / scale
    assert 0 < gap < (OMEGA * DT) ** 2

    # Force phasors: the stored potential-flow wrench equals X - Z_pf q (Newmark rates).
    forces = L.contribution_forces(OMEGA, q_newmark, {rl.POTENTIAL_FLOW_ID: X},
                                   {rl.POTENTIAL_FLOW_ID: z_newmark})
    stored = ex['forces'][rl.POTENTIAL_FLOW_ID]['phasor']
    pf_block = L.contributions[rl.POTENTIAL_FLOW_ID]
    fd = X - (pf_block['stiffness'] - 1j * r * pf_block['damping'] - r ** 2 * pf_block['mass'] + z_newmark) @ q_newmark
    np.testing.assert_allclose(stored, fd, rtol=0, atol=1e-6 * np.max(np.abs(fd)))
    assert forces[rl.POTENTIAL_FLOW_ID].shape == (12,)


def test_rotation_vector_and_refusals():
    angle = 0.3
    axis = np.array([1.0, 2.0, 2.0]) / 3.0
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    R = np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * K @ K
    np.testing.assert_allclose(rx.rotation_vector(R), angle * axis, atol=1e-14)
    with pytest.raises(rx.RaoExtractionError, match='near pi'):
        rx.rotation_vector(np.diag([1.0, -1.0, -1.0]))
    with pytest.raises(rl.RaoLinearizationError):
        rl.newmark_effective_omega(100.0, 0.05)
