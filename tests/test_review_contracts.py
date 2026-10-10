"""Independent adversarial review of the application contracts."""
from dataclasses import replace
import json

import pytest

from anymarine.domain import AnalysisSettings, default_two_body_project
from anymarine.services import ApplicationService
from anymarine.persistence import ProjectStore, ProjectSaveError
from anymarine.hashing import input_hash


def test_interrupted_save_preserves_previous_project(tmp_path, monkeypatch):
    import anymarine.persistence as persistence
    store = ProjectStore(tmp_path/'project')
    original = default_two_body_project('saved')
    store.save(original)
    edited = original.with_edits(bodies=(original.bodies[0].with_edits(name='edited'), *original.bodies[1:]))
    write = persistence.atomic_write_json
    def fail_manifest(path, data):
        if path.name == persistence.MANIFEST_NAME:
            raise OSError('injected failure after sidecar writes')
        return write(path, data)
    monkeypatch.setattr(persistence, 'atomic_write_json', fail_manifest)
    with pytest.raises(ProjectSaveError):
        store.save(edited)
    assert store.load().to_dict() == original.to_dict() if hasattr(original, 'to_dict') else store.load() == original


def test_effective_settings_are_part_of_job_identity():
    service = ApplicationService()
    project = default_two_body_project('settings')
    changed = replace(project.settings, duration=project.settings.duration*2)
    snapshot = service.job_input_snapshot(project, settings=changed)
    assert snapshot['input_hash'] != input_hash(project)
    assert snapshot['input_hash'] == input_hash(project.with_edits(settings=changed))


def test_nonfinite_result_is_rejected():
    result = ApplicationService().run_synthetic_local(default_two_body_project('finite'))
    with pytest.raises(ValueError):
        replace(result, times=(float('nan'), *result.times[1:]))


def test_noninteger_mesh_indices_are_rejected():
    from anymarine.domain import BodyMesh
    with pytest.raises(ValueError):
        BodyMesh(positions=((0.,0.,0.),(1.,0.,0.),(0.,1.,0.)), triangles=((0.9,1,2),))


def test_result_source_mapping_cannot_be_mutated():
    result = ApplicationService().run_synthetic_local(default_two_body_project('immutability'))
    source = dict(result.positions)
    copy = replace(result, positions=source)
    source.clear()
    assert len(copy.positions) == len(copy.body_ids)
