"""Least-squares harmonic fits of sampled responses; Qt-free and solver-free.

Phasor convention (as linear_assessment): ``x(t) = Re{X exp(-i k omega t)}``, so
``x ~ a cos(k w t) + b sin(k w t)`` gives ``X_k = a + i b``. One design matrix is
shared by every channel of a window: optional intercept, harmonics 1..K and an
optional linear drift. Rank deficiency or a condition number above the declared
bound is a typed refusal, never a silently regularized fit.
"""
from dataclasses import dataclass
import math

import numpy as np


class HarmonicFitError(ValueError):
    """Inadequate window, rank deficiency or conditioning refusal."""


@dataclass(frozen=True)
class HarmonicFit:
    """Fit of one window; ``phasors[k-1, c]`` is harmonic k of channel c."""
    omega: float
    start: float
    end: float
    samples: int
    phasors: np.ndarray
    mean: np.ndarray
    drift: np.ndarray
    residual_rms: np.ndarray
    condition: float
    rank: int

    @property
    def fundamental(self):
        return self.phasors[0]

    def harmonic_distortion(self):
        """sqrt(sum_k>=2 |X_k|^2) / |X_1| per channel (inf for a zero fundamental)."""
        higher = np.sqrt(np.sum(np.abs(self.phasors[1:]) ** 2, axis=0))
        base = np.abs(self.phasors[0])
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.where(base > 0, higher / np.where(base > 0, base, 1.0),
                            np.where(higher > 0, np.inf, 0.0))


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise HarmonicFitError(f'{name} must be finite and positive')
    return float(value)


def window_indices(times, start, end):
    """Sample indices with start <= t <= end (tolerant to grid round-off)."""
    t = np.asarray(times, dtype=float)
    span = max(abs(float(start)), abs(float(end)), 1.0)
    eps = 1e-9 * span
    return np.flatnonzero((t >= start - eps) & (t <= end + eps))


def fit_window(times, values, omega, start, end, *, harmonics=1, intercept=True,
               drift=False, max_condition=1e12, min_cycles=2.0, min_samples_per_cycle=8):
    """Fit ``values`` (n_t, n_channels) or (n_t,) over ``[start, end]``.

    The window must span at least ``min_cycles`` periods of the fundamental with
    at least ``min_samples_per_cycle`` samples per period of the highest
    harmonic, and the highest harmonic must stay below the sampling Nyquist rate.
    """
    omega = _positive(omega, 'omega')
    if type(harmonics) is not int or harmonics < 1:
        raise HarmonicFitError('harmonics must be a positive integer')
    max_condition = _positive(max_condition, 'max_condition')
    t = np.asarray(times, dtype=float)
    x = np.asarray(values, dtype=float)
    single = x.ndim == 1
    if single:
        x = x[:, None]
    if t.ndim != 1 or x.ndim != 2 or x.shape[0] != t.size:
        raise HarmonicFitError('times must be 1-D and values (n_t,) or (n_t, n_channels)')
    if not (np.all(np.isfinite(t)) and np.all(np.isfinite(x))):
        raise HarmonicFitError('times and values must be finite')
    if not end > start:
        raise HarmonicFitError('window end must exceed its start')
    idx = window_indices(t, start, end)
    period = 2.0 * math.pi / omega
    if end - start < min_cycles * period * (1 - 1e-12):
        raise HarmonicFitError(f'window spans fewer than {min_cycles} fundamental periods')
    if idx.size < 2:
        raise HarmonicFitError('window holds fewer than two samples')
    dt = float(np.min(np.diff(t[idx])))
    if harmonics * omega * dt >= math.pi:
        raise HarmonicFitError('highest harmonic reaches the sampling Nyquist frequency')
    if idx.size < min_samples_per_cycle * harmonics * (end - start) / period:
        raise HarmonicFitError('too few samples per period of the highest harmonic')
    tw, xw = t[idx], x[idx]
    columns = []
    if intercept:
        columns.append(np.ones_like(tw))
    for k in range(1, harmonics + 1):
        columns.append(np.cos(k * omega * tw))
        columns.append(np.sin(k * omega * tw))
    if drift:
        columns.append(tw - 0.5 * (start + end))
    design = np.column_stack(columns)
    singular = np.linalg.svd(design, compute_uv=False)
    rank = int(np.sum(singular > singular[0] * max(design.shape) * np.finfo(float).eps))
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else math.inf
    if rank < design.shape[1]:
        raise HarmonicFitError(f'rank-deficient harmonic design ({rank} < {design.shape[1]})')
    if not condition <= max_condition:
        raise HarmonicFitError(f'harmonic design condition {condition:.3g} exceeds {max_condition:.3g}')
    coef, *_ = np.linalg.lstsq(design, xw, rcond=None)
    residual = xw - design @ coef
    col = 0
    mean = coef[col] if intercept else np.zeros(x.shape[1])
    col += 1 if intercept else 0
    phasors = np.empty((harmonics, x.shape[1]), dtype=complex)
    for k in range(harmonics):
        phasors[k] = coef[col] + 1j * coef[col + 1]
        col += 2
    slope = coef[col] if drift else np.zeros(x.shape[1])
    return HarmonicFit(omega=omega, start=float(start), end=float(end), samples=int(idx.size),
                       phasors=phasors, mean=np.asarray(mean, dtype=float),
                       drift=np.asarray(slope, dtype=float),
                       residual_rms=np.sqrt(np.mean(residual ** 2, axis=0)),
                       condition=condition, rank=rank)


def steady_windows(t_end, omega, window_cycles):
    """Two adjacent equal windows ending at ``t_end``: ((s1, e1), (s2, e2))."""
    omega = _positive(omega, 'omega')
    cycles = _positive(window_cycles, 'window_cycles')
    length = cycles * 2.0 * math.pi / omega
    return (t_end - 2.0 * length, t_end - length), (t_end - length, t_end)


def phasor_change(first, second, floor):
    """|X2 - X1| / max(|X2|, floor) per channel (window-to-window steadiness)."""
    first, second = np.asarray(first), np.asarray(second)
    return np.abs(second - first) / np.maximum(np.abs(second), _positive(floor, 'floor'))
