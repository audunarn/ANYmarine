"""Periodic radiation prehistory: history force vs brute force; solver stays periodic."""
import numpy as np
import pytest

from conftest import requires_solver
from anymarine import prehistory as ph

OMEGA = 2.0
DT = 0.01


def kernel(n_lags, dt=DT, alpha=1.0):
    r = np.array([[200.0, 20.0, 15.0, 5.0], [20.0, 300.0, 5.0, 10.0],
                  [15.0, 5.0, 250.0, 25.0], [5.0, 10.0, 25.0, 280.0]])
    return r[None] * np.exp(-alpha * dt * np.arange(n_lags))[:, None, None]


def brute_force(K, dt, t0, n_steps, omega, Q, V):
    """Per-step sums written out with the solver's documented index form."""
    def v_p(t):
        return (V * np.exp(-1j * omega * t)).real
    n_lags = K.shape[0]
    H = np.zeros((n_steps, Q.size))
    for n in range(n_steps):
        tn = t0 + n * dt
        periodic = sum((0.5 if k in (0, n_lags - 1) else 1.0) * dt * K[k] @ v_p(tn - k * dt)
                       for k in range(n_lags))
        if n == 0:
            own = 0.0
        else:
            v = [v_p(t0 + j * dt) for j in range(n + 1)]
            own = dt * (0.5 * K[n] @ v[0] + sum(K[n - j] @ v[j] for j in range(1, n)) + 0.5 * K[0] @ v[n])
        H[n] = periodic - own
    return H


def test_history_force_matches_the_brute_force_sums():
    K = kernel(300)
    Q = np.array([0.01 + 0.02j, -0.003j, 0.015, 0.002 - 0.001j])
    V = -1j * OMEGA * Q
    H = ph.periodic_prehistory_force(K, DT, 0.7, 120, OMEGA, Q)
    expected = brute_force(K, DT, 0.7, 120, OMEGA, Q, V)
    assert np.max(np.abs(H - expected)) <= 1e-12 * np.max(np.abs(expected))
    W = V * (1 + 1e-3j)
    H2 = ph.periodic_prehistory_force(K, DT, 0.7, 120, OMEGA, Q, velocity_phasor=W)
    assert np.max(np.abs(H2 - brute_force(K, DT, 0.7, 120, OMEGA, Q, W))) <= 1e-12 * np.max(np.abs(expected))


def test_refusals():
    K = kernel(10)
    Q = np.ones(4, complex)
    with pytest.raises(ph.PrehistoryError, match='cover'):
        ph.periodic_prehistory_force(K, DT, 0.0, 11, OMEGA, Q)
    with pytest.raises(ph.PrehistoryError, match='finite'):
        ph.periodic_prehistory_force(K, DT, 0.0, 5, OMEGA, Q * np.nan)
    with pytest.raises(ph.PrehistoryError, match='positive'):
        ph.periodic_prehistory_force(K, DT, 0.0, 5, -1.0, Q)
    with pytest.raises(ph.PrehistoryError, match='phasor size'):
        ph.periodic_prehistory_force(K, DT, 0.0, 5, OMEGA, Q[:3])


def test_declaration_is_closed_world_and_finite():
    good = {'kind': 'periodic', 'qhat': [[0.1, 0.0], [0, 1], [2.0, -1.0], [0.0, 0.0]]}
    assert ph.validated_declaration(good)['qhat'][1] == [0.0, 1.0]
    for bad in ({'kind': 'periodic'}, dict(good, extra=1), dict(good, kind='ramp'),
                dict(good, qhat=good['qhat'][:3]), dict(good, qhat=[[True, 0]] + good['qhat'][1:]),
                dict(good, qhat=[[float('nan'), 0]] + good['qhat'][1:]), dict(good, qhat=[[1, 2, 3]] * 4)):
        with pytest.raises(ph.PrehistoryError):
            ph.validated_declaration(bad)


@requires_solver
def test_solver_started_on_the_discrete_steady_state_stays_periodic_only_with_the_history():
    """Acceptance (plan slice 1): F - H keeps the exact discrete steady state; F alone does not."""
    import anysolver.marine_linear as ml
    n_steps, t0 = 3000, 0.3
    K = kernel(6000)
    mass = np.diag([1000.0, 1200.0, 900.0, 1100.0])
    a_inf = np.diag([100.0, 150.0, 120.0, 130.0])
    link = 50.0
    stiffness = np.diag([link, 1.0e4, link, 1.2e4])
    stiffness[0, 2] = stiffness[2, 0] = -link
    damping = np.diag([0.0, 10.0, 0.0, 10.0])
    F = np.array([300.0 + 100j, 2000.0, -250.0 + 80j, 1500.0 - 300j])
    # Exact Newmark (beta 1/4, gamma 1/2) discrete steady state with the periodic memory.
    z = np.exp(-1j * OMEGA * DT)
    alpha = 0.5 * DT * (1 + z) / (z - 1)                      # V = alpha A
    beta = (DT * alpha + 0.25 * DT * DT * (1 + z)) / (z - 1)  # Q = beta A
    w = np.full(K.shape[0], DT)
    w[0] = w[-1] = 0.5 * DT
    G = np.einsum('k,kij->ij', w * np.exp(1j * OMEGA * DT * np.arange(K.shape[0])), K)
    A = np.linalg.solve(mass + a_inf + (damping + G) * alpha + stiffness * beta, F)
    V, Q = alpha * A, beta * A
    times = t0 + DT * np.arange(n_steps)
    wave = (F[None, :] * np.exp(-1j * OMEGA * times)[:, None]).real
    H = ph.periodic_prehistory_force(K, DT, t0, n_steps, OMEGA, Q, velocity_phasor=V)
    q0, v0 = (Q * np.exp(-1j * OMEGA * t0)).real, (V * np.exp(-1j * OMEGA * t0)).real
    expected = (Q[None, :] * np.exp(-1j * OMEGA * times)[:, None]).real

    def run(forces):
        spec = ml.MarineLinearSpec(labels=('a:surge', 'a:heave', 'b:surge', 'b:heave'), dt=DT, times=times,
                                   forces=forces, mass_dry=mass, added_mass_infinite=a_inf, damping=damping,
                                   stiffness=stiffness, radiation_kernel=K, kernel_times=DT * np.arange(K.shape[0]),
                                   initial_displacement=q0, initial_velocity=v0)
        return np.asarray(ml.solve_marine_linear(spec).displacements)

    scale = np.max(np.abs(Q))
    assert np.max(np.abs(run(wave - H) - expected)) <= 1e-6 * scale
    assert np.max(np.abs(run(wave) - expected)) >= 1e-2 * scale


def test_refinement_compares_the_wave_part_of_prehistory_members():
    """Pair check: each member stores F_wave - H_own; the wave part must agree, H may differ."""
    from types import SimpleNamespace
    from anymarine.linear_assessment import AssessmentRefusalError, _same_physical_model
    Q = np.array([0.01 + 0.02j, -0.003j, 0.015, 0.002 - 0.001j])
    t0, n_fine = 0.3, 201
    fine_times = t0 + DT * np.arange(n_fine)
    F = np.array([300.0 + 100j, 2000.0, -250.0 + 80j, 1500.0 - 300j])
    wave_fine = (F[None, :] * np.exp(-1j * OMEGA * fine_times)[:, None]).real
    K_fine, K_coarse = kernel(n_fine), kernel(101, dt=2 * DT)[:, :, :] * 1.01     # different kernels
    H_fine = ph.periodic_prehistory_force(K_fine, DT, t0, n_fine, OMEGA, Q)
    H_coarse = ph.periodic_prehistory_force(K_coarse, 2 * DT, t0, 101, OMEGA, Q)
    common = dict(mass_dry=np.eye(4), added_mass_infinite=np.eye(4), damping=np.eye(4), stiffness=np.eye(4),
                  q0=(0.0,) * 4, v0=(0.0,) * 4, t0=t0)
    fine = SimpleNamespace(dt=DT, n_steps=n_fine, forces=wave_fine - H_fine, **common)
    coarse = SimpleNamespace(dt=2 * DT, n_steps=101, forces=wave_fine[::2] - H_coarse, **common)
    _same_physical_model(coarse, fine, 2, history=(H_coarse, H_fine))
    with pytest.raises(AssessmentRefusalError, match='sampled forcing'):
        _same_physical_model(coarse, fine, 2)                         # H differs between members
    shifted = SimpleNamespace(**dict(vars(coarse), forces=coarse.forces * (1 + 1e-9)))
    with pytest.raises(AssessmentRefusalError, match='sampled forcing'):
        _same_physical_model(shifted, fine, 2, history=(H_coarse, H_fine))
