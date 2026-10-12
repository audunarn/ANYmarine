"""RAO slice 4: Morison inertia FD terms against an analytic vertical cylinder."""
import math

import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mechanics import prepare_native_run
from anymarine.native_examples import morison_two_body_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine import rao_linearize as rl

A_WAVE, OMEGA, PHASE, DEPTH, RHO, G, DIAM, CA = 0.2, 1.0, 0.3, 10.0, 1025.0, 9.81, 0.06, 1.0


def cylinder_project():
    p = morison_two_body_project()
    data = p.native_inputs.to_dict()
    pos = {b.body_id: list(b.reference_position) for b in p.bodies}
    for bid, state in data['initial_states'].items():
        state['position'] = pos[bid]
    m = data['morison']
    m['environment'].update(water_depth=DEPTH, density=RHO, gravity=G,
                            components=[dict(amplitude=A_WAVE, omega=OMEGA, direction_degrees=0.0, phase=PHASE)],
                            current=dict(depths=[0.0, DEPTH], velocities=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]))
    member = next(x for x in m['members'] if x['member_id'] == 'vertical-member-a')
    member['quadrature_order'] = 16
    member['section'] = dict(diameter=DIAM, drag_coefficient=0.0, added_mass_coefficient=CA)
    m['members'] = [member]
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


def wavenumber():
    k = OMEGA ** 2 / G
    for _ in range(100):
        k = OMEGA ** 2 / (G * math.tanh(k * DEPTH))
    return k


def morison(model):
    return next(c for c in model.contributions if type(c).__name__ == 'MorisonMemberSetContribution')


@requires_solver
def test_vertical_cylinder_excitation_and_added_mass():
    p = cylinder_project()
    model, states, spec = prepare_native_run(p)
    body = model.body_ids[0]
    x0 = float(np.asarray(states[body].position)[0])
    k = wavenumber()
    area = math.pi * DIAM ** 2 / 4
    m_f, m_a = RHO * area, CA * RHO * area
    theta = k * x0 + PHASE
    amp = -1j * A_WAVE * OMEGA ** 2 / math.sinh(k * DEPTH) * np.exp(1j * theta)
    lo = -3.0
    int_c = (math.sinh(k * DEPTH) - math.sinh(k * (DEPTH + lo))) / k
    prim = lambda z: z * math.sinh(k * (z + DEPTH)) / k - math.cosh(k * (z + DEPTH)) / k ** 2
    int_zc = prim(0.0) - prim(lo)
    x = rl.stateless_wave_excitation(morison(model), model, states, OMEGA)
    np.testing.assert_allclose(x[0], (m_f + m_a) * amp * int_c, rtol=1e-10)
    np.testing.assert_allclose(x[4], (m_f + m_a) * amp * int_zc, rtol=1e-10)
    np.testing.assert_allclose(np.delete(x, [0, 4]), 0.0, atol=1e-12)

    calm_model, calm_states, calm_spec = prepare_native_run(rl.wave_free_project(p))
    L = rl.linearize(calm_model, calm_states, dt=calm_spec.dt)
    np.testing.assert_allclose(L.static_force, 0.0, atol=1e-12)
    M = L.contributions[morison(calm_model).contribution_id]['mass']
    expected = {(0, 0): 3 * m_a, (0, 4): -4.5 * m_a, (4, 4): 9 * m_a,
                (1, 1): 3 * m_a, (1, 3): 4.5 * m_a, (3, 3): 9 * m_a}
    for (i, j), value in expected.items():
        assert M[i, j] == pytest.approx(value, rel=1e-12), (i, j)
        assert M[j, i] == pytest.approx(value, rel=1e-12), (j, i)
    np.testing.assert_allclose(L.contributions[morison(calm_model).contribution_id]['damping'], 0.0, atol=1e-14)


@requires_solver
def test_wave_free_copy_and_calm_tangents():
    p = cylinder_project()
    calm = rl.wave_free_project(p)
    assert all(c['amplitude'] == 0.0 for c in calm.native_inputs.morison.data['environment']['components'])
    assert p.native_inputs.morison.data['environment']['components'][0]['amplitude'] == A_WAVE
    # Waves change calm tangents only through second-order terms: the wavy and
    # wave-free initialization stiffness differ, which is why linearization is calm.
    m1, s1, sp1 = prepare_native_run(p)
    m2, s2, sp2 = prepare_native_run(calm)
    wavy = rl.linearize(m1, s1, dt=sp1.dt)
    flat = rl.linearize(m2, s2, dt=sp2.dt)
    np.testing.assert_allclose(wavy.mass, flat.mass, atol=1e-12)
