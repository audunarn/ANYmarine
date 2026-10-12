"""RAO slice 2: tangents of dry bodies, links and gravity; analytic oscillator."""
import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mass_properties import BodyMassProperties
from anymarine.mechanics import prepare_native_run
from anymarine.native_examples import dry_two_body_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine import rao_linearize as rl


def project(*, link_damping=0.0, gravity=False, cg=(0.0, 0.0, 0.0)):
    p = dry_two_body_project()
    data = p.native_inputs.to_dict()
    pos = {b.body_id: list(b.reference_position) for b in p.bodies}
    for bid, state in data['initial_states'].items():
        state['position'] = pos[bid]          # link at rest length: static equilibrium
    data['links'][0]['damping'] = [[link_damping if i == j == 0 else 0.0 for j in range(6)] for i in range(6)]
    if gravity:
        data['gravity_world'] = [0.0, 0.0, -9.81]
    inertia = ((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 8.0))
    bodies = tuple(b.with_edits(mass_properties=BodyMassProperties(cg, inertia)) for b in p.bodies)
    return p.with_edits(bodies=bodies, native_inputs=NativeMechanicalInputs.from_dict(data))


def lin(p):
    model, states, spec = prepare_native_run(p)
    return model, states, spec, rl.linearize(model, states, dt=spec.dt)


@requires_solver
@pytest.mark.parametrize('c', [0.0, 2.5])
def test_two_mass_link_oscillator_matches_closed_form(c):
    model, states, spec, L = lin(project(link_damping=c))
    k, m = 10.0, 12.0
    np.testing.assert_allclose(L.static_force, 0.0, atol=1e-12)
    for w in (0.3, 1.0, 1.29, 2.0):
        X = np.zeros(12, complex)
        X[0] = 1.0                       # unit surge force phasor on body A
        q = L.solve(w, X)
        z = np.array([[k - 1j * w * c - w * w * m, -(k - 1j * w * c)],
                      [-(k - 1j * w * c), k - 1j * w * c - w * w * m]])
        expected = np.linalg.solve(z, [1.0, 0.0])
        np.testing.assert_allclose(q[[0, 6]], expected, rtol=1e-12, atol=1e-14)
        others = np.delete(q, [0, 6])
        np.testing.assert_allclose(others, 0.0, atol=1e-14)
        # The external unit force X plus every contribution and dry inertia balance.
        total = sum(L.contribution_forces(w, q).values()) + X
        np.testing.assert_allclose(total, 0.0, atol=1e-12)


@requires_solver
def test_per_contribution_blocks_sum_to_the_assembled_tangents():
    model, states, spec, L = lin(project(link_damping=1.0, gravity=True, cg=(0.1, 0.0, -0.3)))
    for key in ('stiffness', 'damping', 'mass'):
        total = sum(block[key] for block in L.contributions.values())
        for bid, block in L.dry.items():
            i = 6 * L.body_ids.index(bid)
            total[i:i + 6, i:i + 6] += block[key]
        np.testing.assert_allclose(total, getattr(L, key), rtol=0, atol=1e-12)
    assert L.dof_keys[0].endswith('::surge') and L.dof_keys[-1].endswith('::yaw')


def _residual(model, states, dt, q, v, a):
    import anysolver.marine_contributions as mc
    from anysolver.marine_rigid import RigidBodyState
    items = rl._owner_contributions(model, mc)
    trial_states = {}
    for i, bid in enumerate(model.body_ids):
        s = slice(6 * i, 6 * i + 6)
        base = states[bid]
        trial_states[bid] = RigidBodyState(position=np.asarray(base.position) + q[s][:3], rotation_vector=q[s][3:],
                                           velocity=v[s], acceleration=a[s], anchor=base.anchor)
    owner = mc.ContributionSet(tuple(model.body_ids), items)
    step = owner.prepare_step(mc.StepInformation(0, dt, 'initialization'))
    try:
        trial = owner.evaluate_trial(step, trial_states)
    finally:
        owner.abort_step(step)
    r = np.array(trial.external_force)
    for i, body in enumerate(model.bodies):
        r[6 * i:6 * i + 6] -= body.inertial_force(trial_states[body.body_id])
    return r


@requires_solver
def test_tangents_match_central_differences_of_the_public_residual():
    p = project(link_damping=1.5, gravity=True, cg=(0.1, 0.05, -0.3))
    model, states, spec, L = lin(p)
    n, h = L.size, 1e-6
    zero = np.zeros(n)
    for name, slot in (('stiffness', 0), ('damping', 1), ('mass', 2)):
        numeric = np.zeros((n, n))
        for j in range(n):
            e = np.zeros(n)
            e[j] = h
            plus = [zero, zero, zero]
            minus = [zero, zero, zero]
            plus[slot], minus[slot] = e, -e
            numeric[:, j] = -(_residual(model, states, spec.dt, *plus) - _residual(model, states, spec.dt, *minus)) / (2 * h)
        exact = getattr(L, name)
        scale = max(1.0, float(np.max(np.abs(exact))))
        np.testing.assert_allclose(numeric, exact, rtol=0, atol=1e-8 * scale)


@requires_solver
def test_refusals():
    model, states, spec = prepare_native_run(project())
    moving = dict(states)
    bid = model.body_ids[0]
    from anysolver.marine_rigid import RigidBodyState
    moving[bid] = RigidBodyState(position=states[bid].position, velocity=(0.1, 0, 0, 0, 0, 0),
                                 anchor=states[bid].anchor)
    with pytest.raises(rl.RaoLinearizationError, match='stationary'):
        rl.linearize(model, moving, dt=spec.dt)
    L = rl.linearize(model, states, dt=spec.dt)
    with pytest.raises(rl.RaoLinearizationError, match='shape'):
        L.solve(1.0, np.zeros(3))
