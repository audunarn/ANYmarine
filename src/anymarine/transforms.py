"""Rigid-frame transform helpers (Qt-free, numpy only)."""

from __future__ import annotations

import numpy as np


def rotation_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """ZYX (yaw-pitch-roll) intrinsic rotation as a 3x3 matrix."""

    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def frame_transform(position, orientation) -> np.ndarray:
    """4x4 transform from a stored position and Euler orientation sample."""

    matrix = np.eye(4)
    matrix[:3, :3] = rotation_matrix(orientation[0], orientation[1], orientation[2])
    matrix[:3, 3] = position
    return matrix


def matrix_frame_transform(position, rotation):
    """4x4 transform from authoritative BODY-to-WORLD R; no Euler conversion."""
    from .native_inputs import _vector
    from .result_rotations import validated_rotation_matrix
    matrix = np.eye(4)
    matrix[:3, :3] = validated_rotation_matrix(rotation)
    matrix[:3, 3] = _vector(position, 3, 'stored position [m]')
    return matrix
