"""Shared N-DOF frequency-domain tools of the linear route (ADR-015 section 7, slice L6-A).

Qt-free and independent of the assessment API: every function takes explicit arrays. Development evidence only.

- ``discrete_reference``: the exact steady state of the solver (trapezoidal Newmark + trapezoidal causal memory) at
  the actual forcing frequency, in the finite-memory steady regime.
- ``continuous_reference``: the dataset RAO with the same restoring, external damping and excitation conventions.
- ``memory_symbol_min_eigenvalue``: diagnostic of the stored kernel's Toeplitz symbol (passivity sanity check).
- ``symmetrise`` / ``structural_admission``: the declared symmetrisation and the structural stability admission.
- ``fit_phasors`` / ``settling_time``: N-channel harmonic window fitting and free-decay settling (section 8).
- ``memory_work``: cumulative memory work of a stored run (energy note, docs/linear_memory_energy_note.md).
- ``diagnostic_poles``: matrix-pencil + Newton poles of the stored-kernel system (diagnostic only).

Phasor convention x = Re{X exp(-i w t)} throughout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EPS = float(np.finfo(float).eps)
SKEW_BOUND = 1.0e-2
PSD_ROUNDOFF = 64.0


class LinearFdError(ValueError):
    """Inputs are malformed or a declared admission fails (typed refusal)."""


def _square(name, value, n=None):
    a = np.asarray(value, dtype=float)
    if a.ndim != 2 or a.shape[0] != a.shape[1] or (n is not None and a.shape[0] != n):
        raise LinearFdError(f"{name} must be a square {n or 'N'} x {n or 'N'} matrix")
    if not np.all(np.isfinite(a)):
        raise LinearFdError(f"{name} must be finite")
    return a


# ---------------------------------------------------------------------------------------------- references

def trapezoidal_rate(omega: float, dt: float) -> float:
    """r_N = (2/dt) tan(w dt / 2): the trapezoidal rule's effective rate at the actual frequency."""
    if not (0.0 < omega * dt < math.pi):
        raise LinearFdError("the forcing frequency must satisfy 0 < w dt < pi (below Nyquist)")
    return 2.0 / dt * math.tan(omega * dt / 2.0)


def memory_transfer(kernel, dt: float, omega: float, cutoff_index: int | None = None) -> np.ndarray:
    """G_d(w) = dt sum_{l=0..L_H} w_l K_l exp(i w l dt) with w_0 = 1/2 and w_l = 1 for l >= 1.

    The finite-memory steady regime of the solver's trapezoidal convolution: once n > L_H the starting-endpoint
    term K_n v_0 / 2 has dropped out and every retained lag carries weight 1 except the current step (1/2).
    ``cutoff_index`` L_H defaults to the last stored sample; samples after it must be (and are taken as) zero.
    """
    K = np.asarray(kernel, dtype=float)
    if K.ndim != 3 or K.shape[1] != K.shape[2]:
        raise LinearFdError("kernel must have shape (n_samples, N, N)")
    last = K.shape[0] - 1 if cutoff_index is None else int(cutoff_index)
    if not 0 <= last < K.shape[0]:
        raise LinearFdError("cutoff_index outside the stored kernel")
    if np.any(K[last + 1:]):
        raise LinearFdError("kernel samples after the declared cutoff must be zero")
    lags = np.arange(last + 1)
    weights = np.ones(last + 1)
    weights[0] = 0.5
    phase = weights * np.exp(1j * omega * lags * dt)
    return dt * np.einsum("l,lij->ij", phase, K[:last + 1])


def discrete_reference(mass_dry, added_mass_infinite, damping, stiffness, kernel, dt, omega, force,
                       cutoff_index=None) -> np.ndarray:
    """X_d = Z_d^-1 F with Z_d = K - r_N^2 (M_dry + A_inf) - i r_N [C + G_d(w)] (ADR-015 section 7 (a))."""
    F = np.asarray(force, dtype=complex)
    n = F.shape[0]
    M = _square("mass_dry", mass_dry, n) + _square("added_mass_infinite", added_mass_infinite, n)
    C, K = _square("damping", damping, n), _square("stiffness", stiffness, n)
    r = trapezoidal_rate(omega, dt)
    Z = K - r * r * M - 1j * r * (C + memory_transfer(kernel, dt, omega, cutoff_index))
    return np.linalg.solve(Z, F)


def continuous_reference(mass_dry, added_mass, damping_radiation, damping, stiffness, omega, force) -> np.ndarray:
    """X = Z(w)^-1 F with Z = K - w^2 (M_dry + A(w)) - i w (B(w) + C) (ADR-015 section 7 (b))."""
    F = np.asarray(force, dtype=complex)
    n = F.shape[0]
    M = _square("mass_dry", mass_dry, n) + _square("added_mass", added_mass, n)
    B = _square("damping_radiation", damping_radiation, n) + _square("damping", damping, n)
    Z = _square("stiffness", stiffness, n) - omega * omega * M - 1j * omega * B
    return np.linalg.solve(Z, F)


def memory_symbol_min_eigenvalue(kernel, dt: float, n_theta: int = 4096) -> float:
    """min over a theta grid of the smallest eigenvalue of dt (K_0 + 2 sum_{l>=1} K_l cos(l theta)) - diagnostic only.

    This is twice the Hermitian part of G_d on the discrete frequency circle; it is >= 0 for a passive stored kernel
    (energy note). A finite grid can miss narrow negative intervals; it is never an admission proof.
    """
    K = np.asarray(kernel, dtype=float)
    theta = np.linspace(0.0, math.pi, n_theta)
    lags = np.arange(K.shape[0])
    weights = 2.0 * np.cos(np.outer(theta, lags))
    weights[:, 0] = 1.0
    symbol = dt * np.einsum("tl,lij->tij", weights, K)
    symbol = 0.5 * (symbol + np.swapaxes(symbol, 1, 2))
    return float(np.min(np.linalg.eigvalsh(symbol)))


# ---------------------------------------------------------------------------------------------- operators

def normalisation(dof_kinds, length_scale: float) -> np.ndarray:
    """S = diag(1 for translations, 1/L for rotations); q = S q_hat (ADR-015 section 4)."""
    if not length_scale > 0:
        raise LinearFdError("the length scale must be positive")
    return np.diag([1.0 if kind == "translation" else 1.0 / length_scale for kind in dof_kinds])


@dataclass(frozen=True)
class Symmetrised:
    symmetric: np.ndarray            # X_s in SI (the solver input)
    skew_ratio: float                # ||X_k|| / ||X|| in normalised coordinates (Frobenius)


def symmetrise(name: str, matrix, scale) -> Symmetrised:
    """Declared symmetrisation (ADR-015 section 8): split S^T X S into symmetric and skew parts, refuse a skew part
    above 1e-2 of the operator, return the SI symmetric part and the recorded ratio."""
    X = _square(name, matrix)
    S = _square("normalisation", scale, X.shape[0])
    Xn = S.T @ X @ S
    norm = float(np.linalg.norm(Xn))
    skew = 0.5 * (Xn - Xn.T)
    ratio = float(np.linalg.norm(skew)) / norm if norm > 0 else 0.0
    if ratio > SKEW_BOUND:
        raise LinearFdError(f"{name}: skew part {ratio:.3g} of the operator exceeds the declared bound {SKEW_BOUND:g}")
    return Symmetrised(symmetric=0.5 * (X + X.T), skew_ratio=ratio)


def _is_psd(name, matrix, scale, *, strict=False):
    Xn = scale.T @ matrix @ scale
    Xn = 0.5 * (Xn + Xn.T)
    if strict:
        try:
            np.linalg.cholesky(Xn)
        except np.linalg.LinAlgError as error:
            raise LinearFdError(f"{name} is not positive definite in normalised coordinates") from error
        return
    eig = np.linalg.eigvalsh(Xn)
    floor = -PSD_ROUNDOFF * EPS * max(float(np.linalg.norm(Xn, 2)), 1e-300)
    if eig.min() < floor:
        raise LinearFdError(f"{name} is not positive semi-definite (smallest eigenvalue {eig.min():.3e})")


def structural_admission(mass_dry, added_mass_infinite, stiffness_total, damping, damping_samples, scale) -> None:
    """ADR-015 section 8 structural admission on symmetrised operators (normalised): (i) M_eff PD, (ii) K_total PD,
    (iii) C PSD, (iv)(a) every damping sample B(w_i) PSD. Raises LinearFdError on the first failure."""
    S = np.asarray(scale, dtype=float)
    _is_psd("M_eff = M_dry + A_inf", _square("mass_dry", mass_dry) + _square("A_inf", added_mass_infinite), S,
            strict=True)
    _is_psd("K_total", _square("stiffness_total", stiffness_total), S, strict=True)
    _is_psd("C", _square("damping", damping), S)
    for index, sample in enumerate(np.asarray(damping_samples, dtype=float)):
        _is_psd(f"B sample {index}", sample, S)


# ---------------------------------------------------------------------------------------------- fitting

@dataclass(frozen=True)
class PhasorFit:
    phasors: np.ndarray              # per channel, from the last window
    previous: np.ndarray             # per channel, from the window before it
    residual: np.ndarray             # per channel, max abs residual of the last window
    accepted: bool
    reason: str


def _window_fit(times, q, omega):
    A = np.column_stack([np.cos(omega * times), np.sin(omega * times)])
    coeff, *_ = np.linalg.lstsq(A, q, rcond=None)
    residual = np.max(np.abs(q - A @ coeff), axis=0)
    return coeff[0] - 0.0j + 1j * coeff[1], residual


def fit_phasors(times, q, omega, t_fit, scales, floors, periods_per_window: int = 5) -> PhasorFit:
    """ADR-015 section 8 fitting: the last two consecutive windows of exactly 5 wave periods (physical time, ending at
    the record end) must both lie after t_fit; they must agree within max(0.002 s_k, 0.1 f_k) and the last residual
    must be <= max(0.01 |X_k|, 0.1 f_k). Windows are closed intervals in time; samples are those inside them."""
    t = np.asarray(times, dtype=float)
    Q = np.asarray(q, dtype=float).reshape(t.size, -1)
    s, f = np.asarray(scales, dtype=float), np.asarray(floors, dtype=float)
    width = periods_per_window * 2.0 * math.pi / omega
    end = float(t[-1])
    tol = 1e-9 * max(1.0, abs(end))
    nan = np.full(Q.shape[1], np.nan)
    if end - 2.0 * width < t_fit - tol or end - 2.0 * width < float(t[0]) - tol:
        return PhasorFit(nan + 0j, nan + 0j, nan, False, "fewer than two complete windows after t_fit")
    last = (t >= end - width - tol) & (t <= end + tol)
    prev = (t >= end - 2.0 * width - tol) & (t <= end - width + tol)
    X1, r1 = _window_fit(t[last], Q[last], omega)
    X0, _ = _window_fit(t[prev], Q[prev], omega)
    agree = np.abs(X1 - X0) <= np.maximum(0.002 * s, 0.1 * f)
    small = r1 <= np.maximum(0.01 * np.abs(X1), 0.1 * f)
    ok = bool(np.all(agree) and np.all(small))
    reason = "accepted" if ok else ("windows disagree" if not np.all(agree) else "fit residual too large")
    return PhasorFit(X1, X0, r1, ok, reason)


def judged_channels(reference, scales, floors):
    """Which channels are judged on amplitude/phase (|RAO_k| >= max(0.1 s_k, f_k)) and which are spurious-motion
    channels (s_k < f_k: must stay below 10 f_k)."""
    ref = np.abs(np.asarray(reference))
    s, f = np.asarray(scales, dtype=float), np.asarray(floors, dtype=float)
    return ref >= np.maximum(0.1 * s, f), s < f


def settling_time(times, q, amplitudes, t_max_period: float, hold_windows: int = 3):
    """ADR-015 section 8 free-decay settling: e(t) = max_k |q_k| / a_k; complete windows [2 w T_max, 2 (w+1) T_max] in
    physical time from t = 0 (a trailing partial window is ignored); t_s is the start of the first window after which
    every later complete window has e <= max(1e-3 max e, 1e-9), with at least ``hold_windows`` such windows.
    Returns t_s (a window boundary) or None (unresolved)."""
    t = np.asarray(times, dtype=float)
    Q = np.asarray(q, dtype=float).reshape(t.size, -1)
    a = np.asarray(amplitudes, dtype=float)
    e = np.max(np.abs(Q) / a, axis=1)
    level = max(1e-3 * float(np.max(e)), 1e-9)
    width = 2.0 * t_max_period
    tol = 1e-9 * max(1.0, abs(float(t[-1])))
    windows = int(math.floor((float(t[-1]) + tol) / width))           # complete windows only
    peaks = []
    for w in range(windows):
        inside = (t >= w * width - tol) & (t <= (w + 1) * width + tol)
        peaks.append(float(np.max(e[inside])))
    for w in range(windows):
        if windows - w >= hold_windows and all(p <= level for p in peaks[w:]):
            return w * width
    return None


def memory_work(dt: float, velocities, memory_forces) -> np.ndarray:
    """Cumulative memory work W_mem(n) = sum_{k<n} dt vbar_k . mbar_k (trapezoidal step averages)."""
    v = np.asarray(velocities, dtype=float)
    m = np.asarray(memory_forces, dtype=float)
    vbar = 0.5 * (v[1:] + v[:-1])
    mbar = 0.5 * (m[1:] + m[:-1])
    return np.concatenate([[0.0], np.cumsum(dt * np.einsum("ki,ki->k", vbar, mbar))])


# ---------------------------------------------------------------------------------------------- poles (diagnostic)

def stored_kernel_transform(kernel, dt, s, cutoff_index=None) -> np.ndarray:
    """Ktilde(s) = dt sum_{l=0..L_H} w_l K_l exp(-s l dt) with the solver's steady weights (w_0 = 1/2)."""
    K = np.asarray(kernel, dtype=float)
    last = K.shape[0] - 1 if cutoff_index is None else int(cutoff_index)
    lags = np.arange(last + 1)
    weights = np.ones(last + 1, dtype=complex)
    weights[0] = 0.5
    return dt * np.einsum("l,lij->ij", weights * np.exp(-s * lags * dt), K[:last + 1])


def matrix_pencil(samples, dt, order, rank_tolerance: float = 1e-10):
    """Exponents s_m of a sum of ``order`` complex exponentials fitted to a uniformly sampled scalar sequence.

    The order must not exceed the numerical rank of the Hankel matrix (singular values above rank_tolerance x the
    largest); otherwise the extra modes are unidentifiable and the fit is refused (LinearFdError).
    """
    y = np.asarray(samples, dtype=float)
    if order < 1 or 2 * order + 1 > y.size:
        raise LinearFdError("matrix pencil order must be >= 1 and leave enough samples")
    L = y.size // 2
    Y = np.array([y[i:i + L + 1] for i in range(y.size - L)])
    U, sigma, Vh = np.linalg.svd(Y, full_matrices=False)
    rank = int(np.sum(sigma > rank_tolerance * sigma[0])) if sigma[0] > 0 else 0
    if order > rank:
        raise LinearFdError(f"matrix pencil order {order} exceeds the numerical rank {rank} of the samples")
    V = Vh[:order].conj().T
    V1, V2 = V[:-1], V[1:]
    z = np.linalg.eigvals(np.linalg.pinv(V1) @ V2)
    return np.log(z.astype(complex)) / dt


@dataclass(frozen=True)
class PoleResult:
    s: complex
    converged: bool
    relative_singular_value: float       # sigma_min of the row-scaled system matrix at s (unit rows at s0)


def newton_pole(mass, damping, stiffness, kernel, dt, s0, cutoff_index=None, iterations=60,
                root_tolerance: float = 1e-9) -> PoleResult:
    """Refine a pole of det[s^2 M + s (C + Ktilde(s)) + K] = 0 by Newton (diagnostic only).

    The determinant is evaluated on the system matrix scaled by a fixed diagonal from the start point (no
    under/overflow for badly scaled units); the result reports convergence and the scale-free residual (smallest
    singular value of that row-scaled matrix); ``converged`` requires a vanishing Newton step and residual <= tolerance.
    """
    M, C, K = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (mass, damping, stiffness))

    def system(s):
        return s * s * M + s * (C + stored_kernel_transform(kernel, dt, s, cutoff_index)) + K
    rows = np.linalg.norm(system(complex(s0)), axis=1)
    rows = np.where(rows > 0, rows, 1.0)
    D = np.diag(1.0 / rows)

    def f(s):
        return np.linalg.det(D @ system(s))

    def residual(s):
        # smallest singular value of the row-scaled matrix (unit rows at the start point): scale-free, and it stays
        # meaningful when every entry vanishes together at a multiple root
        return float(np.linalg.svd(D @ system(s), compute_uv=False)[-1])
    s = complex(s0)
    stepped = False
    for _ in range(iterations):
        h = 1e-7 * max(1.0, abs(s))
        value = f(s)
        derivative = (f(s + h) - f(s - h)) / (2 * h)
        if derivative == 0 or not np.isfinite(derivative):
            break
        step = value / derivative
        s -= step
        if abs(step) <= 1e-13 * max(1.0, abs(s)):
            stepped = True
            break
    rel = residual(s)
    return PoleResult(s=s, converged=bool(stepped and rel <= root_tolerance), relative_singular_value=rel)


def augmented_poles(mass, damping, stiffness, residues, rates):
    """Poles of M q'' + C q' + K q + sum_m R_m exp(-rate_m t) * q' (exponential kernel realisation): eigenvalues of
    the augmented first-order state matrix (scalar residues R_m on a 1-DOF system or matrices R_m)."""
    M, C, K = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (mass, damping, stiffness))
    n = M.shape[0]
    terms = [(np.atleast_2d(np.asarray(R, dtype=complex)), complex(a)) for R, a in zip(residues, rates)]
    size = 2 * n + n * len(terms)
    A = np.zeros((size, size), dtype=complex)
    Minv = np.linalg.inv(M)
    A[:n, n:2 * n] = np.eye(n)
    A[n:2 * n, :n] = -Minv @ K
    A[n:2 * n, n:2 * n] = -Minv @ C
    for m, (R, a) in enumerate(terms):
        block = slice(2 * n + m * n, 2 * n + (m + 1) * n)
        A[n:2 * n, block] = -Minv           # memory state z_m: z_m' = -a z_m + R v; force = z_m
        A[block, n:2 * n] = R
        A[block, block] = -a * np.eye(n)
    return np.linalg.eigvals(A)


def bartlett_taper(kernel, dt: float, cutoff_seconds: float) -> tuple[np.ndarray, int]:
    """Multiply a sampled kernel by the Bartlett window 1 - t/H on [0, H], zero after (ADR-015 section 8 (iv)(d)).

    Returns the tapered full-length kernel and the cutoff index L_H = round(H / dt) (where the taper is zero).
    Oracle/evidence helper; production kernels come from ANYhydro's time-window option (L6-4).
    """
    K = np.asarray(kernel, dtype=float)
    cutoff = int(round(cutoff_seconds / dt))
    if cutoff < 1 or K.shape[0] <= cutoff:
        raise LinearFdError("the stored kernel must cover the whole taper support [0, H] (samples through L_H)")
    t = dt * np.arange(K.shape[0])
    window = np.clip(1.0 - t / (cutoff * dt), 0.0, None)
    window[cutoff:] = 0.0
    return K * window[:, None, None], cutoff


def memory_oracle(kernel, velocities, dt: float) -> np.ndarray:
    """Independent N-DOF memory oracle (generalised assessment ``_memory_oracle``): the solver's trapezoidal causal
    convolution with zero prehistory, by FFT; non-finite recomputation refuses (shared with the result decoder)."""
    from .linear import LinearResultIncomplete, _trapezoidal_memory
    try:
        return _trapezoidal_memory(np.asarray(kernel, dtype=float), np.asarray(velocities, dtype=float), dt)[0]
    except LinearResultIncomplete as error:
        raise LinearFdError(str(error)) from error
