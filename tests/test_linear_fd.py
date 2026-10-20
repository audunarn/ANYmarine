"""anymarine.linear_fd (ADR-015 slice L6-A): references, operators, fitting, settling, energy, diagnostic poles."""
from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import requires_solver

from anymarine import linear_fd as fd


def _solve(M, A, C, K, kernel, dt, forces, q0=None):
    from anysolver.marine_linear import MarineLinearSpec, solve_marine_linear
    n_steps, n = forces.shape
    times = dt * np.arange(n_steps)
    spec = MarineLinearSpec(labels=[f"b:{k}" for k in range(n)], dt=dt, times=times, forces=forces, mass_dry=M,
                            added_mass_infinite=A, damping=C, stiffness=K, radiation_kernel=kernel[:n_steps],
                            kernel_times=times.copy(), initial_displacement=q0)
    result = solve_marine_linear(spec)
    assert result.complete
    return times, result


def _system():
    M = np.array([[2.0e6, 1.0e5], [1.0e5, 8.0e6]])
    A = np.array([[5.0e5, 2.0e4], [2.0e4, 1.0e6]])
    K = np.diag([2.0e6 * (2 * math.pi / 9.0) ** 2, 9.0e6 * (2 * math.pi / 14.0) ** 2])
    C = 0.03 * np.diag(2 * np.sqrt(np.diag(K) * np.diag(M + A)))
    S = np.array([[3.0e5, 5.0e4], [5.0e4, 6.0e5]])               # PSD amplitude of the oracle kernel
    return M, A, C, K, S


def _passive_kernel(S, dt, n, decay=0.4, frequency=0.7):
    t = dt * np.arange(n)
    return (np.exp(-decay * t) * np.cos(frequency * t))[:, None, None] * S[None]


def _fit_last_window(times, q, omega, dt):
    window = slice(times.size - int(round(5 * 2 * math.pi / omega / dt)), times.size)
    design = np.column_stack([np.cos(omega * times[window]), np.sin(omega * times[window])])
    coeff, *_ = np.linalg.lstsq(design, q[window], rcond=None)
    return coeff[0] + 1j * coeff[1]


# ---------------------------------------------------------------------------------------------- A1 references

def test_zero_kernel_discrete_reference_is_the_newmark_transfer():
    M, A, C, K, _ = _system()
    dt, omega = 0.05, 2 * math.pi / 10.0
    F = np.array([1e5 + 2e4j, -3e4j])
    r = 2 / dt * math.tan(omega * dt / 2)
    expected = np.linalg.solve(K - r * r * (M + A) - 1j * r * C, F)
    got = fd.discrete_reference(M, A, C, K, np.zeros((5, 2, 2)), dt, omega, F)
    assert np.allclose(got, expected, rtol=1e-14, atol=0)


@requires_solver
def test_discrete_reference_is_the_solver_steady_state_with_a_tapered_kernel():
    M, A, C, K, S = _system()
    dt, omega, H = 0.05, 2 * math.pi / 11.0, 30.0
    n_steps = 12001                                              # 600 s: transients decay far below 1e-10
    kernel, cutoff = fd.bartlett_taper(_passive_kernel(S, dt, n_steps), dt, H)
    F = np.array([2.0e5 + 1.0e5j, -1.5e5 + 3.0e5j])
    times = dt * np.arange(n_steps)
    forces = np.real(F[None, :] * np.exp(-1j * omega * times)[:, None])
    _, result = _solve(M, A, C, K, kernel, dt, forces)
    X_d = fd.discrete_reference(M, A, C, K, kernel, dt, omega, F, cutoff_index=cutoff)
    fitted = _fit_last_window(times, result.displacements, omega, dt)
    assert np.max(np.abs(fitted - X_d)) <= 1e-8 * np.max(np.abs(X_d))


@requires_solver
def test_weights_oracle_nonzero_last_retained_sample():
    """A kernel cut (not tapered) with a nonzero last retained sample: the steady weight there is 1, not 1/2."""
    M, A, C, K, S = _system()
    C = 5.0 * C                     # a rectangular cut is not exactly passive: damping dominates it so the run settles
    dt, omega, H = 0.05, 2 * math.pi / 11.0, 20.0
    n_steps = 12001
    cutoff = int(round(H / dt))
    kernel = 0.01 * _passive_kernel(S, dt, n_steps, decay=0.1, frequency=0.0)
    kernel[cutoff + 1:] = 0.0
    assert np.abs(kernel[cutoff]).max() > 0.1 * np.abs(kernel[0]).max()
    F = np.array([2.0e5, 1.0e5j])
    times = dt * np.arange(n_steps)
    forces = np.real(F[None, :] * np.exp(-1j * omega * times)[:, None])
    _, result = _solve(M, A, C, K, kernel, dt, forces)
    fitted = _fit_last_window(times, result.displacements, omega, dt)
    X_d = fd.discrete_reference(M, A, C, K, kernel, dt, omega, F, cutoff_index=cutoff)
    tolerance = 1e-8 * np.max(np.abs(X_d))
    assert np.max(np.abs(fitted - X_d)) <= tolerance
    halved = kernel.copy()
    halved[cutoff] *= 0.5                                        # i.e. a trapezoid end weight 1/2 at L_H
    X_wrong = fd.discrete_reference(M, A, C, K, halved, dt, omega, F, cutoff_index=cutoff)
    assert np.max(np.abs(fitted - X_wrong)) > 100 * tolerance


def test_continuous_reference_and_input_refusals():
    M, A, C, K, S = _system()
    w = 0.6
    X = fd.continuous_reference(M, A, 0.1 * S, C, K, w, np.array([1.0, 0.0]))
    Z = K - w ** 2 * (M + A) - 1j * w * (0.1 * S + C)
    assert np.allclose(Z @ X, [1.0, 0.0], rtol=0, atol=1e-15)
    one = fd.continuous_reference([[2.0]], [[1.0]], [[0.5]], [[0.25]], [[9.0]], 1.0, np.array([3.0]))
    assert one[0] == pytest.approx(3.0 / (9.0 - 3.0 - 0.75j), rel=1e-15)
    with pytest.raises(fd.LinearFdError, match="after the declared cutoff"):
        fd.memory_transfer(np.ones((4, 1, 1)), 0.1, 0.5, cutoff_index=1)
    with pytest.raises(fd.LinearFdError, match="Nyquist"):
        fd.trapezoidal_rate(70.0, 0.05)


# ---------------------------------------------------------------------------------------------- A2 operators

def test_declared_symmetrisation_and_skew_bound():
    S = fd.normalisation(["translation", "rotation"], 50.0)
    X = np.array([[1.0e6, 2.0e7], [2.0e7 * (1 + 1e-3), 3.0e9]])
    out = fd.symmetrise("A_inf", X, S)
    assert np.allclose(out.symmetric, out.symmetric.T) and 0 < out.skew_ratio < 1e-2
    with pytest.raises(fd.LinearFdError, match="skew part"):
        fd.symmetrise("A_inf", np.array([[1.0, 0.5], [-0.5, 1.0]]), np.eye(2))


def test_structural_admission_refusals():
    I = np.eye(2)
    fd.structural_admission(I, I, I, 0 * I, [0 * I, I], I)
    with pytest.raises(fd.LinearFdError, match="M_eff"):
        fd.structural_admission(-I, 0 * I, I, 0 * I, [], I)
    with pytest.raises(fd.LinearFdError, match="K_total"):
        fd.structural_admission(I, I, np.diag([1.0, 0.0]), 0 * I, [], I)     # neutral mode refused
    with pytest.raises(fd.LinearFdError, match="C is not"):
        fd.structural_admission(I, I, I, np.diag([1.0, -1e-3]), [], I)
    with pytest.raises(fd.LinearFdError, match="B sample 1"):
        fd.structural_admission(I, I, I, 0 * I, [I, np.diag([1.0, -1.0])], I)


def test_damping_noise_floor_admits_near_zero_samples_only_down_to_the_declared_fraction():
    """F2b (ADR-015 section 8 (iv)(a) amendment): a near-zero sample's BEM noise is judged against the largest
    sample of the set, not its own norm; it is recorded; a non-passive part above the fraction is still refused."""
    I = np.eye(2)
    assert fd.structural_admission(I, I, I, 0 * I, [I, 0 * I, 2 * I], I) == {}          # clean: nothing recorded
    noisy = np.diag([1e-6, -1e-16])              # -1e-10 of its own norm: below 64 eps, above the 1e-8 floor
    record = fd.structural_admission(I, I, I, 0 * I, [2 * I, noisy], I)
    assert record["fraction"] == fd.DAMPING_NOISE_FRACTION and record["basis"] == "own_sample_norm_normalised"
    index, ratio, own = record["samples"][0]
    assert index == 1 and ratio == pytest.approx(-1e-10) and own == pytest.approx(1e-6)
    with pytest.raises(fd.LinearFdError, match="B sample 1"):                          # -1e-7 of its own norm
        fd.structural_admission(I, I, I, 0 * I, [2 * I, np.diag([1e-6, -1e-13])], I)
    with pytest.raises(fd.LinearFdError, match="B sample 1"):                          # review round 1: a huge sample
        fd.structural_admission(I, I, I, 0 * I, [np.diag([1e12, 0.0]), np.diag([1e6, -1e3])], I)   # masks nothing
    with pytest.raises(fd.LinearFdError, match="C is not"):                            # C keeps its own floor
        fd.structural_admission(I, I, I, np.diag([1.0, -1e-12]), [2 * I], I)
    S = np.diag([1.0, 0.1])                       # normalised axes: own norm 1 (raw 100), ratio -1e-10 admitted
    record = fd.structural_admission(I, I, I, 0 * I, [np.diag([1.0, 100.0]), np.diag([1.0, -1e-8])], S)
    index, ratio, own = record["samples"][0]
    assert index == 1 and own == pytest.approx(1.0) and ratio == pytest.approx(-1e-10)
    with pytest.raises(fd.LinearFdError, match="B sample 0"):                          # -1e-8 of the normalised 1:
        fd.structural_admission(I, I, I, 0 * I, [np.diag([1.0, -1e-4])], S)            # -1e-6 of own norm, refused


# ---------------------------------------------------------------------------------------------- A3 fitting/settling

def test_fit_phasors_accepts_steady_and_rejects_transient_or_contaminated_signals():
    dt, omega = 0.05, 2 * math.pi / 8.0
    t = dt * np.arange(4001)
    X = np.array([0.3 - 0.1j, 0.01j])
    steady = np.real(X[None, :] * np.exp(-1j * omega * t)[:, None])
    scales, floors = np.abs(X), np.array([1e-4, 1e-5])
    ok = fd.fit_phasors(t, steady, omega, 50.0, scales, floors)
    assert ok.accepted and np.allclose(ok.phasors, X, rtol=1e-10)
    drifting = steady * (1.0 + 0.2 * np.exp(-t / 100.0))[:, None]          # amplitude still converging
    assert fd.fit_phasors(t, drifting, omega, 50.0, scales, floors).reason == "windows disagree"
    contaminated = steady + np.outer(np.cos(3.1 * omega * t), [0.02, 0.0])
    assert fd.fit_phasors(t, contaminated, omega, 50.0, scales, floors).reason == "fit residual too large"
    assert not fd.fit_phasors(t, steady, omega, 190.0, scales, floors).accepted   # fewer than two windows left


def test_judged_channels_floors():
    judged, spurious = fd.judged_channels([0.5, 0.01, 1e-6], [1.0, 1.0, 5e-6], [1e-4, 1e-4, 1e-5])
    assert list(judged) == [True, False, False] and list(spurious) == [False, False, True]


def test_settling_time_rules():
    dt = 0.05
    t = dt * np.arange(20001)
    decay = np.exp(-t / 20.0) * np.cos(0.8 * t)
    # e <= 1e-3 once exp(-t/20) <= 1e-3 (t >= 138.2 s); 20 s windows from t = 0 -> the window starting at 140 s
    assert fd.settling_time(t, decay, [1.0], 10.0) == pytest.approx(140.0)
    assert fd.settling_time(t, np.cos(0.8 * t), [1.0], 10.0) is None              # never settles


# ---------------------------------------------------------------------------------------------- A4 energy

@requires_solver
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_memory_work_is_non_negative_for_passive_tapered_kernels(seed):
    M, A, C, K, S = _system()
    dt, n_steps = 0.05, 6001
    kernel, cutoff = fd.bartlett_taper(_passive_kernel(S, dt, n_steps), dt, 30.0)
    assert fd.memory_symbol_min_eigenvalue(kernel[:cutoff + 1], dt) >= -1e-9 * np.abs(kernel).max() * dt
    rng = np.random.default_rng(seed)
    forces = rng.normal(size=(n_steps, 2)) * 1e5
    _, result = _solve(M, A, C, K, kernel, dt, forces, q0=np.array([0.1, -0.02]))
    W = fd.memory_work(dt, result.velocities, result.memory_forces)
    scale = float(np.sum(np.abs(dt * np.einsum("ki,ki->k", result.velocities, result.memory_forces))))
    assert np.min(W) >= -1e-12 * scale


@requires_solver
def test_memory_work_discriminates_a_non_passive_kernel():
    M, A, C, K, S = _system()
    dt, n_steps = 0.05, 4001
    kernel, cutoff = fd.bartlett_taper(-_passive_kernel(S, dt, n_steps), dt, 30.0)    # active (negative spectrum)
    assert fd.memory_symbol_min_eigenvalue(kernel[:cutoff + 1], dt) < 0
    forces = np.zeros((n_steps, 2))
    forces[:200, 0] = 1e5
    _, result = _solve(M, A, C, K, kernel, dt, forces)
    assert np.min(fd.memory_work(dt, result.velocities, result.memory_forces)) < 0


# ---------------------------------------------------------------------------------------------- A5 poles

def test_pole_oracle_one_dof_exponential_kernel():
    poles = fd.augmented_poles(1.0, 0.2, 1.0, [1.0], [0.01])
    expected = np.roots([1, 0.21, 2.002, 0.01])
    assert all(np.min(np.abs(expected - p)) <= 1e-12 for p in poles)
    oscillatory = [p for p in poles if abs(p.imag) > 0.1][0]
    real = [p for p in poles if abs(p.imag) < 1e-9][0]
    assert -1 / oscillatory.real == pytest.approx(9.76, abs=0.01) and -1 / real.real == pytest.approx(200.1, abs=0.1)


# decays from the analytic geometric-sum determinant (ADR-015 section 8 oracle (ii)); a half weight at the last
# retained sample would give 171.4312 / 187.9093
@pytest.mark.parametrize("H, decay", [(300.0, 171.4354), (450.0, 187.9110)])
def test_pole_oracle_sampled_and_cut_kernel(H, decay):
    dt = 0.05
    n = int(round(H / dt)) + 1
    kernel = np.exp(-0.01 * dt * np.arange(n))[:, None, None]
    pole = fd.newton_pole(np.eye(1), 0.2 * np.eye(1), np.eye(1), kernel, dt, -0.005)
    assert pole.converged and pole.relative_singular_value <= 1e-9
    assert -1 / pole.s.real == pytest.approx(decay, abs=1e-3)
    # independent stored-kernel transform: the analytic finite geometric sum with the solver's weights
    s = pole.s
    r = np.exp(-(0.01 + s) * dt)
    geometric = dt * ((1 - r ** n) / (1 - r) - 0.5)
    assert abs(fd.stored_kernel_transform(kernel, dt, s)[0, 0] - geometric) <= 1e-12 * abs(geometric)
    assert abs(s * s + s * (0.2 + geometric) + 1) <= 1e-9


def test_matrix_pencil_recovers_kernel_exponents():
    dt = 0.05
    t = dt * np.arange(400)
    samples = np.exp(-0.3 * t) * np.cos(0.9 * t)
    exponents = fd.matrix_pencil(samples, dt, 2)
    assert np.allclose(np.sort_complex(exponents), np.sort_complex(np.array([-0.3 + 0.9j, -0.3 - 0.9j])), atol=1e-8)


@requires_solver
def test_memory_oracle_reproduces_the_solver_memory_force():
    M, A, C, K, S = _system()
    dt, n_steps = 0.05, 3001
    kernel, _ = fd.bartlett_taper(_passive_kernel(S, dt, n_steps), dt, 30.0)
    forces = np.random.default_rng(5).normal(size=(n_steps, 2)) * 1e5
    _, result = _solve(M, A, C, K, kernel, dt, forces)
    oracle = fd.memory_oracle(kernel, result.velocities, dt)
    assert np.max(np.abs(oracle - result.memory_forces)) <= 1e-10 * np.max(np.abs(result.memory_forces))


# ---------------------------------------------------------------------------------------------- review round 1 regressions

def test_fit_windows_are_whole_periods_in_physical_time():
    """Codex: with T = 1.0009 s two 5-period windows need 10.009 s; a 10 s record must not be accepted."""
    dt, T = 0.01, 1.0009
    omega = 2 * math.pi / T
    t = dt * np.arange(1001)                                          # 0 .. 10.0 s
    q = np.cos(omega * t)[:, None]
    assert not fd.fit_phasors(t, q, omega, 0.0, [1.0], [1e-4]).accepted
    t2 = dt * np.arange(1002)                                         # 0 .. 10.01 s: two windows fit
    assert fd.fit_phasors(t2, np.cos(omega * t2)[:, None], omega, 0.0, [1.0], [1e-4]).accepted


def test_settling_counts_only_complete_physical_windows():
    """Codex: T_max = 1.0009 s, record to 8.0 s: three complete 2.0018 s windows, two settled -> unresolved."""
    dt = 0.01
    t = dt * np.arange(801)
    q = np.zeros(t.size)
    q[0] = 1.0
    assert fd.settling_time(t, q, [1.0], 1.0009) is None
    t_long = dt * np.arange(1001)                                     # four complete windows, three settled
    q_long = np.zeros(t_long.size)
    q_long[0] = 1.0
    assert fd.settling_time(t_long, q_long, [1.0], 1.0009) == pytest.approx(2.0018)


def test_newton_reports_non_convergence_and_is_scale_free():
    bad = fd.newton_pole(np.eye(1), np.zeros((1, 1)), np.eye(1), np.zeros((2, 1, 1)), 0.1, 0.0)
    assert not bad.converged and bad.relative_singular_value > 0.5
    mass = 1e-60 * np.eye(6)
    stiffness = 1e-60 * np.diag(np.arange(1.0, 7.0) ** 2)               # roots +/- i k, k = 1..6 (simple)
    root = fd.newton_pole(mass, np.zeros((6, 6)), stiffness, np.zeros((2, 6, 6)), 0.1, 0.2 + 0.9j)
    assert root.converged and abs(root.s - 1j) <= 1e-9                  # despite determinant underflow (1e-360)


def test_matrix_pencil_refuses_unidentifiable_orders():
    dt = 0.05
    t = dt * np.arange(200)
    with pytest.raises(fd.LinearFdError, match="numerical rank"):
        fd.matrix_pencil(np.exp(-0.3 * t), dt, 4)
    with pytest.raises(fd.LinearFdError, match="numerical rank"):
        fd.matrix_pencil(np.zeros(t.size), dt, 1)
    with pytest.raises(fd.LinearFdError, match="no finite continuous exponent"):
        fd.matrix_pencil([1.0, 0, 0, 0, 0, 0, 0, 0, 0], 0.1, 1)


def test_bartlett_taper_needs_the_whole_support():
    with pytest.raises(fd.LinearFdError, match="whole taper support"):
        fd.bartlett_taper(np.exp(-0.1 * np.arange(2))[:, None, None], 1.0, 3.0)
