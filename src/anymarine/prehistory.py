"""Periodic radiation prehistory for regular-wave starts; Qt-free and solver-free.

The public linear solver (``anysolver.marine_linear``) evaluates the radiation memory
with a direct causal trapezoid sum and ZERO prehistory before t0::

    memory_n = dt*(0.5*K[n] v_0 + sum(j=1..n-1, K[n-j] v_j) + 0.5*K[0] v_n),  memory_0 = 0

A run declared to have been moving periodically before t0, ``q_p(t) = Re{q exp(-i w t)}``,
has a nonzero memory at every step. The difference is a known history force::

    H_n = M_periodic,n - M_solver,n

with ``M_periodic,n`` the trapezoid memory of ``v_p`` over every stored kernel lag
(half weights at lags 0 and N-1) and ``M_solver,n`` the part the solver computes itself
from the periodic velocities on steps 0..n (lags 0..n, half weights at 0 and n;
zero at n = 0). Passing ``F_n - H_n`` as the external force reproduces the periodic
memory exactly while the response follows ``q_p``; the solver itself is unchanged.
"""
from __future__ import annotations

import numpy as np

PREHISTORY_PERIODIC = "periodic"
PREHISTORY_KINDS = (PREHISTORY_PERIODIC,)


class PrehistoryError(ValueError):
    """A prehistory declaration or its inputs are invalid."""


def periodic_state(qhat, omega: float, t: float):
    """``(q, v)`` of ``q_p(t) = Re{q exp(-i w t)}`` at time ``t``."""
    qhat = np.asarray(qhat, dtype=complex)
    phase = np.exp(-1j * float(omega) * float(t))
    return (qhat * phase).real, (-1j * float(omega) * qhat * phase).real


def periodic_prehistory_force(kernel, dt: float, t0: float, n_steps: int, omega: float, qhat,
                              velocity_phasor=None) -> np.ndarray:
    """History force ``H`` (n_steps x n) of a periodic prehistory, solver trapezoid convention.

    The prehistory velocity is ``Re{V exp(-i w t)}`` with ``V = -i w q`` unless an explicit
    ``velocity_phasor`` is given (e.g. a discrete-time steady state).
    """
    kernel = np.asarray(kernel, dtype=float)
    qhat = np.asarray(qhat, dtype=complex)
    dt, omega, n_steps = float(dt), float(omega), int(n_steps)
    if kernel.ndim != 3 or kernel.shape[1:] != (qhat.size, qhat.size):
        raise PrehistoryError("the kernel must be (n_lags, n, n) matching the phasor size")
    if n_steps < 1 or kernel.shape[0] < n_steps:
        raise PrehistoryError("the kernel must cover every requested step lag")
    if not (dt > 0.0 and np.isfinite(dt) and omega > 0.0 and np.isfinite(omega)):
        raise PrehistoryError("dt and omega must be positive finite numbers")
    if not (np.all(np.isfinite(kernel)) and np.all(np.isfinite(qhat))):
        raise PrehistoryError("the kernel and phasor must be finite")
    n_lags = kernel.shape[0]
    velocity = -1j * omega * qhat if velocity_phasor is None else np.asarray(velocity_phasor, dtype=complex)
    if velocity.shape != qhat.shape or not np.all(np.isfinite(velocity)):
        raise PrehistoryError("the velocity phasor must be finite and match the phasor size")
    # v_p(t_n - k dt) = Re{V e^{i w k dt} e^{-i w t_n}}: every sum is a lag-weighted
    # complex matrix applied to V, then rotated to t_n.
    lag_phasors = kernel * np.exp(1j * omega * dt * np.arange(n_lags))[:, None, None]
    weights = np.full(n_lags, dt)
    weights[0] = weights[-1] = 0.5 * dt
    periodic = np.einsum("k,kij->ij", weights, lag_phasors) @ velocity
    # Solver part at n >= 1: dt*(sum_{k=0..n} L_k) - dt/2*(L_0 + L_n), L_k = lag phasor @ V.
    lag_terms = lag_phasors[:n_steps] @ velocity
    cumulative = dt * np.cumsum(lag_terms, axis=0)
    solver = cumulative - 0.5 * dt * (lag_terms[0][None, :] + lag_terms)
    solver[0] = 0.0
    times = t0 + dt * np.arange(n_steps)
    return ((periodic[None, :] - solver) * np.exp(-1j * omega * times)[:, None]).real


def validated_declaration(data, size: int = 4) -> dict:
    """Closed-world ``{'kind': 'periodic', 'qhat': [[re, im], ...]}`` with finite values."""
    if not isinstance(data, dict) or set(data) != {"kind", "qhat"}:
        raise PrehistoryError("prehistory must be exactly {'kind', 'qhat'}")
    if data["kind"] not in PREHISTORY_KINDS:
        raise PrehistoryError(f"prehistory.kind must be one of {PREHISTORY_KINDS}")
    pairs = data["qhat"]
    if not isinstance(pairs, (list, tuple)) or len(pairs) != size:
        raise PrehistoryError(f"prehistory.qhat must hold {size} [re, im] pairs")
    out = []
    for pair in pairs:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in pair)):
            raise PrehistoryError("prehistory.qhat entries must be numeric [re, im] pairs")
        re, im = float(pair[0]), float(pair[1])
        if not (np.isfinite(re) and np.isfinite(im)):
            raise PrehistoryError("prehistory.qhat must be finite")
        out.append([re, im])
    return {"kind": str(data["kind"]), "qhat": out}


def declaration_phasor(declaration) -> np.ndarray:
    return np.array([complex(re, im) for re, im in declaration["qhat"]], dtype=complex)
