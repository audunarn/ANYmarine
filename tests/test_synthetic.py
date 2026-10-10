"""Synthetic replay generator: determinism, labelling, stored-array replay."""

from __future__ import annotations

import numpy as np

from anymarine import RESULT_KIND_SYNTHETIC
from anymarine.synthetic import generate_synthetic_replay, sample_times


def test_result_is_explicitly_synthetic_unqualified(service, project):
    result = service.run_synthetic_local(project)
    assert result.kind == RESULT_KIND_SYNTHETIC
    assert "not" in result.note
    assert "BEM" in result.note or "boundary" in result.note
    assert "dynamics" in result.note


def test_deterministic_two_runs_identical(service, project):
    hash_value = service.input_hash(project)
    a = service.run_synthetic_local(project)
    b = service.run_synthetic_local(project)

    def motion(result):
        data = result.to_dict()
        data.pop("result_id")  # random identity, not motion content
        data.pop("created_utc")  # bookkeeping timestamp, not motion content
        return data

    assert motion(a) == motion(b)
    assert a.input_hash == hash_value == b.input_hash


def test_arrays_match_times_and_bodies(service, project):
    result = service.run_synthetic_local(project)
    times = np.asarray(result.times)
    assert len(times) == result.frame_count() >= 2
    assert np.all(np.diff(times) > 0)
    assert set(result.body_ids) == {body.body_id for body in project.bodies}
    for body_id in result.body_ids:
        positions = np.asarray(result.positions[body_id])
        orientations = np.asarray(result.orientations[body_id])
        assert positions.shape == (len(times), 3)
        assert orientations.shape == (len(times), 3)
        assert np.isfinite(positions).all() and np.isfinite(orientations).all()
    for name, values in result.channels.items():
        assert len(values) == len(times)


def test_replay_uses_stored_arrays_only(service, project):
    """Frame transforms must derive from the stored arrays, nothing else."""

    from anymarine.transforms import frame_transform

    result = service.run_synthetic_local(project)
    index = 3
    for body_id in result.body_ids:
        matrix = frame_transform(
            result.positions[body_id][index], result.orientations[body_id][index]
        )
        assert matrix.shape == (4, 4)
        assert np.allclose(matrix[:3, 3], result.positions[body_id][index])
        # rotation block is orthonormal
        rotation = matrix[:3, :3]
        assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)


def test_sample_times_exact_endpoints():
    from anymarine.domain import AnalysisSettings

    times = sample_times(AnalysisSettings(duration=20.0, dt=0.25))
    assert times[0] == 0.0
    assert abs(times[-1] - 20.0) < 1e-9
    assert len(times) == 81


def test_different_inputs_give_different_results(service, project):
    a = service.run_synthetic_local(project)
    edited = project.with_edits(
        settings=project.settings.with_edits(wave_amplitude=project.settings.wave_amplitude + 0.5)
    )
    b = service.run_synthetic_local(edited)
    assert a.input_hash != b.input_hash
    assert a.to_dict() != b.to_dict()
