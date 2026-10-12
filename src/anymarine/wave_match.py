"""The regular incident wave that drove a stored linear result; display only.

A hydro-imported linear result records the exact wave it was forced with
(amplitude, omega, heading, phase) in its input-owned provenance, using
F(t) = A Re{F_hat exp(-i omega t + i phase)} with the producing dataset's
unit-amplitude excitation, phase origin at the global (x, y) origin. The
matching undisturbed incident elevation is therefore

    eta(x, y, t) = A cos(k (x cos(heading) + y sin(heading)) - omega t + phase)

with k from the producing dataset's own depth and gravity. No diffraction,
radiation or body disturbance is represented. Qt-free and solver-free.
"""
from dataclasses import dataclass
import math

import numpy as np


def dispersion_wavenumber(omega, water_depth, gravity):
    """Airy wavenumber: omega^2 = g k tanh(k h); deep water when h is inf."""
    for name, value in (('omega', omega), ('gravity', gravity)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} must be finite and positive')
    if isinstance(water_depth, bool) or not isinstance(water_depth, (int, float)) or not (water_depth > 0):
        raise ValueError('water depth must be positive (finite) or inf')
    try:
        deep = omega * omega / gravity
        target = deep * water_depth if math.isfinite(water_depth) else deep   # y tanh(y), y = k h
    except OverflowError:
        target = math.inf
    if not (math.isfinite(target) and target > 0 and deep > 0):
        raise ValueError('wave dispersion is outside the finite supported range')
    if water_depth == math.inf:
        return deep
    # Bracket with a bounded ratio: tanh(y) <= min(1, y) gives y >= max(t, sqrt t);
    # y - y tanh(y) < 0.28 gives y < t + 0.5 when t >= 0.5, and for t < 0.5 the
    # root has y < 1 where y tanh(y) >= 2y^2/3, so y <= sqrt(1.5 t).
    low = max(target, math.sqrt(target))
    high = target + .5 if target >= .5 else math.sqrt(1.5 * target)
    for _ in range(200):                 # bisection to machine precision
        if high - low <= 4e-16 * high:
            break
        mid = .5 * (low + high)
        if mid * math.tanh(mid) < target:
            low = mid
        else:
            high = mid
    else:
        raise ValueError('wave dispersion did not converge')
    k = .5 * (low + high) / water_depth
    if not (math.isfinite(k) and k > 0):
        raise ValueError('wave dispersion is outside the finite supported range')
    return k


@dataclass(frozen=True)
class RegularIncidentWave:
    """One recorded regular wave; heading in radians (propagation direction)."""
    amplitude: float
    omega: float
    heading: float
    phase: float
    water_depth: float
    gravity: float
    still_water_level: float = 0.

    def __post_init__(self):
        for name in ('amplitude', 'heading', 'phase'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'recorded wave {name} must be a finite number')
        if self.amplitude < 0:
            raise ValueError('recorded wave amplitude must be nonnegative')
        object.__setattr__(self, '_k', dispersion_wavenumber(self.omega, self.water_depth, self.gravity))

    @property
    def wavenumber(self):
        return self._k

    @property
    def components(self):
        """The single regular component (amplitude bound for colour scales)."""
        return (self,)

    def surface_elevation(self, points_xy, time):
        xy = np.asarray(points_xy, dtype=float)
        along = xy[:, 0] * math.cos(self.heading) + xy[:, 1] * math.sin(self.heading)
        return self.still_water_level + self.amplitude * np.cos(self._k * along - self.omega * float(time) + self.phase)


def recorded_wave(details):
    """The recorded wave selection of hydro linear inputs, or None."""
    if not isinstance(details, dict):
        return None
    wave = details.get('wave')
    if wave is None:
        return None
    if not isinstance(wave, dict) or not {'amplitude', 'omega', 'heading', 'phase'} <= set(wave):
        raise ValueError('the recorded wave selection is incomplete')
    return wave


def incident_request(contract):
    """(water_depth, gravity) from a stored excitation-source contract request."""
    if not isinstance(contract, dict) or not isinstance(contract.get('request'), dict):
        raise ValueError('the producing dataset declares no excitation-source request; '
                         'its water depth and gravity are unknown')
    request = contract['request']
    kind, depth = request.get('depth_kind'), request.get('water_depth')
    if kind == 'infinite' and depth is None:
        depth = math.inf
    elif kind != 'finite' or isinstance(depth, bool) or not isinstance(depth, (int, float)):
        raise ValueError('the excitation-source request has no explicit water depth')
    return float(depth), request.get('gravity')
