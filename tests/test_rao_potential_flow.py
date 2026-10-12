"""RAO slice 3: potential-flow FD terms (kernel transfer, dataset pullback, excitation)."""
import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mechanics import prepare_native_run
from anymarine.native_examples import fluid_two_body_project, seeded_wave_two_body_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine import rao_linearize as rl


def at_equilibrium(project):
    """Initial states at the potential-flow frames' equilibrium poses (zero rates)."""
    data = project.native_inputs.to_dict()
    for frame in data['potential_flow']['frames']:
        data['initial_states'][frame['body_id']]['position'] = list(frame['equilibrium_position'])
        data['initial_states'][frame['body_id']]['velocity_world'] = [0.0] * 6
    return project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


def test_kernel_transfer_equals_the_exact_trapezoid_sum():
    lam, c, dt, n = 0.8, 2.0, 0.05, 400
    t = np.arange(n) * dt
    kernel = (c * np.exp(-lam * t))[:, None, None]
    for w in (0.3, 1.0, 4.0):
        z = np.exp((-lam + 1j * w) * dt)
        L = n - 1
        exact = dt * c * ((1 - z ** (L + 1)) / (1 - z) - 0.5 * (1 + z ** L))
        np.testing.assert_allclose(rl.kernel_transfer(kernel, dt, w)[0, 0], exact, rtol=1e-13)
        # approaches the continuous transfer C/(lam - i w): trapezoid error ~ (|lam - i w| dt)^2 / 12
        bound = 1.5 * abs(lam - 1j * w) ** 2 * dt ** 2 / 12
        assert abs(exact - c / (lam - 1j * w)) < bound * abs(c / (lam - 1j * w))


@requires_solver
def test_single_omega_fixture_terms():
    p = at_equilibrium(fluid_two_body_project())
    model, states, spec = prepare_native_run(p)
    pf = rl.potential_flow_contribution(model)
    D, keys = rl.potential_flow_jacobian(pf, states)
    np.testing.assert_allclose(D, np.eye(12), atol=1e-15)
    L = rl.linearize(model, states, dt=spec.dt)
    np.testing.assert_allclose(L.contributions[rl.POTENTIAL_FLOW_ID]['mass'], pf.data.added_mass_infinite, atol=1e-14)
    np.testing.assert_allclose(L.contributions[rl.POTENTIAL_FLOW_ID]['stiffness'], pf.data.restoring, atol=1e-12)
    np.testing.assert_allclose(L.static_force, 0.0, atol=1e-12)
    w = pf.data.omega
    x = rl.potential_flow_excitation(pf, states, w)
    np.testing.assert_allclose(x, pf.data.excitation_real + 1j * pf.data.excitation_imag, atol=1e-15)
    z = rl.radiation_impedance(pf, states, w, 'kernel_discrete')
    np.testing.assert_allclose(z, -1j * w * rl.kernel_transfer(pf.data.kernel, pf.data.dt, w), atol=1e-14)
    q = L.solve(w, x, extra={rl.POTENTIAL_FLOW_ID: z})
    forces = L.contribution_forces(w, q, {rl.POTENTIAL_FLOW_ID: x}, {rl.POTENTIAL_FLOW_ID: z})
    np.testing.assert_allclose(sum(forces.values()), 0.0, atol=1e-12)
    with pytest.raises(rl.RaoLinearizationError, match='declared at'):
        rl.potential_flow_excitation(pf, states, w + 0.1)
    with pytest.raises(rl.RaoLinearizationError, match='bound dataset'):
        rl.radiation_impedance(pf, states, w, 'dataset')


@requires_solver
def test_dataset_source_and_series_excitation():
    from anyhydro.fixtures import make_two_body_fixture
    p = at_equilibrium(seeded_wave_two_body_project())
    model, states, spec = prepare_native_run(p)
    pf = rl.potential_flow_contribution(model)
    ds = make_two_body_fixture()
    D, keys = rl.potential_flow_jacobian(pf, states)
    np.testing.assert_allclose(D, np.eye(12), atol=1e-15)
    w = 1.1
    i = int(np.flatnonzero(ds['omega'].values == w)[0])
    dofs = [str(k) for k in ds['influenced_dof'].values]
    idx = [dofs.index(k) for k in keys]
    A = ds['added_mass'].values[i][np.ix_(idx, idx)]
    B = ds['radiation_damping'].values[i][np.ix_(idx, idx)]
    expected = -1j * w * B - w * w * (A - pf.data.added_mass_infinite)
    np.testing.assert_allclose(rl.radiation_impedance(pf, states, w, 'dataset', ds), expected, atol=1e-12)
    with pytest.raises(rl.RaoLinearizationError, match='exact dataset frequency'):
        rl.radiation_impedance(pf, states, 1.0, 'dataset', ds)
    series = pf.data.excitation_series
    hits = np.flatnonzero(series.omegas == w)
    assert hits.size >= 1
    weights = (series.weighted_real + 1j * series.weighted_imag)[hits].sum(axis=0)
    sk = list(series.dof_keys)
    np.testing.assert_allclose(rl.potential_flow_excitation(pf, states, w), weights[[sk.index(k) for k in keys]])
    with pytest.raises(rl.RaoLinearizationError, match='no term'):
        rl.potential_flow_excitation(pf, states, 1.0)
