"""Time-domain RAO extraction from stored native results (docs/RAO_REPORTING.md section 2).

Deterministic and Qt-free: reads an admitted mechanical ``ResultRecord`` only.
Body DOFs are chart coordinates about the linearization anchor (translation of
the body origin; rotation vector of ``R R0^T``), matching rao_linearize. The
fundamental of the second of two adjacent steady windows is the response
phasor; the first gives steadiness. Contribution force phasors come from the
stored WORLD wrenches at the body origin.
"""
import json
import math

import numpy as np

from .harmonic_fit import fit_window, phasor_change, steady_windows

DOF_NAMES = ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')


class RaoExtractionError(ValueError):
    """The stored run cannot support the declared extraction."""


def rotation_vector(R):
    """Principal log of a proper rotation (refused near pi)."""
    R = np.asarray(R, dtype=float)
    cosine = max(-1.0, min(1.0, (np.trace(R) - 1.0) / 2.0))
    angle = math.acos(cosine)
    vee = 0.5 * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if angle > math.pi - 1e-6:
        raise RaoExtractionError('rotation near pi has no unique small-angle chart')
    if angle < 1e-8:
        return vee
    return vee * (angle / math.sin(angle))


def dof_series(record, anchors):
    """``(times, q)`` with q of shape (n_t, 6 n_bodies) in the record's body order."""
    if record.kind != 'mechanical_development' or record.rotation_matrices is None:
        raise RaoExtractionError('RAO extraction needs a native mechanical result')
    times = np.asarray(record.times, dtype=float)
    q = np.zeros((times.size, 6 * len(record.body_ids)))
    for i, bid in enumerate(record.body_ids):
        r0, R0 = (np.asarray(x, dtype=float) for x in anchors[bid])
        q[:, 6 * i:6 * i + 3] = np.asarray(record.positions[bid], dtype=float) - r0
        for n, R in enumerate(record.rotation_matrices[bid]):
            q[n, 6 * i + 3:6 * i + 6] = rotation_vector(np.asarray(R, dtype=float) @ R0.T)
    return times, q


def contribution_channels(record):
    """``{cid: (n_t, 6 n_bodies)}`` from the stored ``wrench[cid, bid]/F*`` channels."""
    out = {}
    axes = ('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz')
    for key, values in record.channels.items():
        if not key.startswith('wrench['):
            continue
        identity, axis = key[len('wrench'):].rsplit('/', 1)
        cid, bid = json.loads(identity)
        block = out.setdefault(cid, np.zeros((len(record.times), 6 * len(record.body_ids))))
        block[:, 6 * record.body_ids.index(bid) + axes.index(axis)] = values
    return out


def extract_run(record, *, omega, amplitude, anchors, transient_seconds, window_cycles,
                harmonics=1, intercept=True, drift=False, max_condition=1e12, steady_floor=1e-12):
    """Response and force phasors of one regular-wave run (per unit amplitude)."""
    times, q = dof_series(record, anchors)
    (s1, e1), (s2, e2) = steady_windows(float(times[-1]), omega, window_cycles)
    if s1 < transient_seconds - 1e-9:
        raise RaoExtractionError('the run is too short for the declared transient and two windows')
    kw = dict(harmonics=harmonics, intercept=intercept, drift=drift, max_condition=max_condition)
    first = fit_window(times, q, omega, s1, e1, **kw)
    second = fit_window(times, q, omega, s2, e2, **kw)
    forces = {}
    for cid, values in contribution_channels(record).items():
        fit = fit_window(times, values, omega, s2, e2, **kw)
        forces[cid] = dict(phasor=fit.fundamental / amplitude, mean=fit.mean, residual_rms=fit.residual_rms,
                           harmonic_distortion=fit.harmonic_distortion())
    return dict(omega=float(omega), amplitude=float(amplitude), windows=[[s1, e1], [s2, e2]],
                dof_keys=[f'{bid}::{name}' for bid in record.body_ids for name in DOF_NAMES],
                response=second.fundamental, rao=second.fundamental / amplitude,
                steadiness=phasor_change(first.fundamental, second.fundamental, steady_floor),
                mean=second.mean, drift=second.drift, residual_rms=second.residual_rms,
                harmonic_distortion=second.harmonic_distortion(), condition=second.condition,
                forces=forces)
