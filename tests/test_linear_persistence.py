"""Persistence of linear development inputs and results, legacy compatibility."""

from __future__ import annotations

import json
import uuid

import pytest

from anymarine import RESULT_KIND_LINEAR
from anymarine.domain import LinearDevelopmentInputs
from anymarine.linear import linear_input_hash
from anymarine.persistence import ProjectLoadError, ProjectStore


def linear_result(project, inputs, input_hash):
    """Small hand-built linear-kind result for persistence round-trips."""

    from anymarine.domain import ResultRecord

    times = (0.0, 0.1)
    body_ids = [binding.body_id for binding in inputs.bindings]
    channels = {
        f"q:{inputs.labels[dof]}": (0.0, 0.1 * dof)
        for dof in range(4)
    }
    return ResultRecord(
        result_id=str(uuid.uuid4()),
        kind=RESULT_KIND_LINEAR,
        input_hash=input_hash,
        body_ids=tuple(body_ids),
        times=times,
        positions={
            body_id: tuple((0.0, 0.0, 0.0) for _ in times) for body_id in body_ids
        },
        orientations={
            body_id: tuple((0.0, 0.0, 0.0) for _ in times) for body_id in body_ids
        },
        channels=channels,
        note="linear persistence test result",
        created_utc="2026-10-08T00:00:00+00:00",
    )


def test_save_load_roundtrip_linear_inputs(service, project, tmp_path):
    inputs = service.linear_demo_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    root = tmp_path / "proj"
    service.save(project, root)
    manifest = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert "linear_inputs" in manifest
    loaded = service.load(root)
    assert loaded.linear_inputs is not None
    assert linear_input_hash(loaded.linear_inputs) == linear_input_hash(inputs)
    assert loaded.linear_inputs.to_dict() == inputs.to_dict()


def test_legacy_project_without_linear_inputs_loads(service, project, tmp_path):
    root = tmp_path / "legacy"
    service.save(project, root)
    manifest_path = root / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "linear_inputs" not in manifest
    loaded = service.load(root)
    assert loaded.linear_inputs is None


def test_corrupt_linear_sidecar_refused(service, project, tmp_path):
    inputs = service.linear_demo_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    root = tmp_path / "proj"
    service.save(project, root)
    manifest_path = root / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sidecar = root / manifest["linear_inputs"]["sidecar"]
    sidecar.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(ProjectLoadError, match="sha256 mismatch"):
        ProjectStore(root).load()


def test_linear_result_roundtrip_and_staleness(service, project, tmp_path):
    inputs = service.linear_demo_inputs(project)
    project = service.set_linear_inputs(project, inputs)
    result = linear_result(project, inputs, linear_input_hash(inputs))
    project = service.add_result(project, result)
    root = tmp_path / "proj"
    service.save(project, root)
    loaded = service.load(root)
    assert [r.kind for r in loaded.results] == [RESULT_KIND_LINEAR]
    assert service.stale_result_ids(loaded) == ()

    edited_body = loaded.bodies[0].with_edits(
        reference_position=(loaded.bodies[0].reference_position[0] + 1.0, 0.0, 0.0)
    )
    edited = loaded.with_edits(
        bodies=tuple(
            edited_body if b.body_id == edited_body.body_id else b for b in loaded.bodies
        )
    )
    assert service.stale_result_ids(edited) == (result.result_id,)


def test_from_dict_rejects_malformed_linear_payload():
    with pytest.raises((KeyError, ValueError, TypeError)):
        LinearDevelopmentInputs.from_dict({"bindings": []})
