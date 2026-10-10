"""Qt-free admission of stored authoritative rotation matrices (no solver)."""
from collections.abc import Mapping
from types import MappingProxyType
from numbers import Real
import numpy as np

ROTATION_MATRIX_TOLERANCE = 1e-12


def validated_rotation_matrix(value):
    try:
        raw = np.asarray(value, dtype=object)
        if raw.shape != (3, 3) or any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Real) for v in raw.flat):
            raise ValueError("requires a numeric 3x3 matrix")
        R = np.asarray(value, dtype=float)
        if not np.isfinite(R).all():
            raise ValueError("requires finite entries")
        if (np.linalg.norm(R.T @ R - np.eye(3), ord=np.inf) > ROTATION_MATRIX_TOLERANCE
                or abs(np.linalg.det(R) - 1.) > ROTATION_MATRIX_TOLERANCE):
            raise ValueError("requires a proper SO(3) matrix; no normalization")
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f"stored rotation: {error}") from error
    return tuple(tuple(float(v) for v in row) for row in R)


def freeze_rotation_samples(mapping, body_ids, sample_count):
    if not isinstance(mapping, Mapping) or set(mapping) != set(body_ids):
        raise ValueError("rotation_matrices must cover exactly the result body_ids")
    frozen = {}
    for body_id in body_ids:
        try:
            samples = tuple(mapping[body_id])
        except TypeError as error:
            raise ValueError(f"rotation samples for {body_id} must be a sequence") from error
        if len(samples) != sample_count:
            raise ValueError(f"rotation samples for {body_id} do not match times")
        frozen[body_id] = tuple(validated_rotation_matrix(R) for R in samples)
    return MappingProxyType(frozen)
