"""Deterministic synthetic two-body replay generator.

This module produces explicitly-labelled synthetic demonstration results. The
motion is a closed-form deterministic function of the project inputs and time:
it is NOT boundary-element-method output and NOT a dynamics integration, and
it must never be presented as production numerical evidence.
"""

from __future__ import annotations

import datetime as _datetime
import math
import uuid

from . import RESULT_KIND_SYNTHETIC
from .domain import (
    SYNTHETIC_RESULT_NOTE,
    AnalysisSettings,
    ProjectModel,
    ResultRecord,
)


def sample_times(settings: AnalysisSettings) -> tuple[float, ...]:
    """Deterministic, strictly increasing time grid including both endpoints."""

    duration = float(settings.duration)
    dt = float(settings.dt)
    steps = max(1, int(round(duration / dt)))
    # Guard against float accumulation: recompute from the integer step count.
    grid = tuple(round(index * duration / steps, 10) for index in range(steps + 1))
    return grid


def _body_response(body_index: int, body_mass: float, settings: AnalysisSettings, t: float):
    """Closed-form deterministic response amplitudes for one body."""

    omega = 2.0 * math.pi / float(settings.wave_period)
    # Mass-scaled amplitude keeps the demo deterministic and input-dependent.
    scale = float(settings.wave_amplitude) * (1.0e6 / body_mass) ** 0.5
    phase = 0.0 if body_index == 0 else math.pi / 3.0
    decay = math.exp(-0.02 * t)
    heave = scale * decay * math.sin(omega * t + phase)
    surge = 0.6 * scale * decay * math.sin(omega * t * 0.5 + phase + 0.4)
    sway = 0.15 * scale * decay * math.sin(omega * t * 0.25 + phase)
    roll = 0.02 * decay * math.sin(omega * t + phase + 0.8)
    pitch = 0.015 * decay * math.sin(omega * t * 0.5 + phase)
    yaw = 0.008 * decay * math.sin(omega * t * 0.125 + phase)
    return surge, sway, heave, roll, pitch, yaw


def generate_synthetic_replay(
    project: ProjectModel,
    settings: AnalysisSettings | None = None,
    input_hash: str = "",
    result_id: str | None = None,
    created_utc: str | None = None,
) -> ResultRecord:
    """Build a deterministic synthetic replay result for every project body."""

    identity=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))
    if any(body.reference_orientation!=identity for body in project.bodies):
        raise ValueError('synthetic replay does not support rotated body references; use stored native replay')

    settings = settings or project.settings
    times = sample_times(settings)
    positions: dict[str, list] = {}
    orientations: dict[str, list] = {}
    channels: dict[str, list] = {}

    for index, body in enumerate(project.bodies):
        position_rows: list[tuple[float, float, float]] = []
        orientation_rows: list[tuple[float, float, float]] = []
        for t in times:
            surge, sway, heave, roll, pitch, yaw = _body_response(
                index, body.mass, settings, t
            )
            position_rows.append(
                (
                    body.reference_position[0] + surge,
                    body.reference_position[1] + sway,
                    body.reference_position[2] + heave,
                )
            )
            orientation_rows.append((roll, pitch, yaw))
        positions[body.body_id] = position_rows
        orientations[body.body_id] = orientation_rows
        channels[f"heave:{body.body_id}"] = [row[2] for row in position_rows]
        channels[f"surge:{body.body_id}"] = [row[0] for row in position_rows]

    return ResultRecord(
        result_id=result_id or str(uuid.uuid4()),
        kind=RESULT_KIND_SYNTHETIC,
        input_hash=input_hash,
        body_ids=tuple(body.body_id for body in project.bodies),
        times=times,
        positions={key: tuple(rows) for key, rows in positions.items()},
        orientations={key: tuple(rows) for key, rows in orientations.items()},
        channels={key: tuple(values) for key, values in channels.items()},
        note=SYNTHETIC_RESULT_NOTE,
        created_utc=created_utc
        or _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    )
