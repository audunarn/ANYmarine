"""Input hashing and stale-result detection."""

from __future__ import annotations

from anymarine.domain import default_two_body_project


def test_hash_is_stable_and_save_load_invariant(service, project, tmp_path):
    root = tmp_path / "proj"
    service.save(project, root)
    loaded = service.load(root)
    assert service.input_hash(project) == service.input_hash(loaded)


def test_hash_is_deterministic_across_instances(service):
    a = service.new_project("A")
    b = default_two_body_project("A")
    # Different UUIDs mean different hashes; identical inputs mean identical hashes.
    assert service.input_hash(a) != service.input_hash(b)
    assert service.input_hash(a) == service.input_hash(a)


def test_edit_changes_hash_and_flags_result_stale(service, project):
    result = service.run_synthetic_local(project)
    project = service.add_result(project, result)
    assert service.stale_result_ids(project) == ()

    edited_body = project.bodies[0].with_edits(mass=project.bodies[0].mass * 1.5)
    edited = project.with_edits(
        bodies=tuple(
            edited_body if b.body_id == edited_body.body_id else b for b in project.bodies
        )
    )
    assert service.input_hash(edited) != service.input_hash(project)
    assert service.stale_result_ids(edited) == (result.result_id,)


def test_rename_changes_hash(service, project):
    before = service.input_hash(project)
    edited = project.with_edits(name="Renamed")
    # Project name is metadata, not a result input: hash must not change.
    assert service.input_hash(edited) == before
    body = edited.bodies[0].with_edits(name="Renamed body")
    edited2 = edited.with_edits(bodies=(body,) + edited.bodies[1:])
    assert service.input_hash(edited2) != before


def test_settings_edit_flags_stale(service, project):
    result = service.run_synthetic_local(project)
    project = service.add_result(project, result)
    edited = project.with_edits(
        settings=project.settings.with_edits(duration=project.settings.duration + 1.0)
    )
    assert service.stale_result_ids(edited) == (result.result_id,)
