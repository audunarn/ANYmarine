"""Active-folder lifecycle regressions; no solver or numerical jobs."""
import pytest
from anymarine.native_examples import dry_two_body_project
from anymarine.persistence import ProjectStore, ProjectSaveError
from anymarine.services import ApplicationService


def test_save_current_adopts_folder_for_subsequent_implicit_save(tmp_path):
    service = ApplicationService()
    project = dry_two_body_project()
    root = tmp_path / 'active'
    assert service.save_current(project, root) == root
    assert service.root == root and service.store.root == root
    edited = project.with_edits(name='Edited after initial save')
    service.save_current(edited)
    assert ApplicationService().load(root).name == edited.name


def test_export_does_not_replace_active_folder(tmp_path):
    service = ApplicationService()
    project = dry_two_body_project()
    root = tmp_path / 'active'
    exported = tmp_path / 'export'
    service.save_current(project, root)
    store = service.store
    service.save(project, exported)
    assert service.root == root and service.store is store
    assert ApplicationService().load(exported).name == project.name


@pytest.mark.parametrize('previously_saved', [False, True])
def test_failed_save_current_preserves_folder_binding(tmp_path, monkeypatch, previously_saved):
    service = ApplicationService()
    project = dry_two_body_project()
    if previously_saved:
        service.save_current(project, tmp_path / 'active')
    previous = (service.root, service.store)
    def fail_save(store, project):
        raise ProjectSaveError('simulated manifest write failure')
    monkeypatch.setattr(ProjectStore, 'save', fail_save)
    with pytest.raises(ProjectSaveError):
        service.save_current(project, tmp_path / 'failed-destination')
    assert service.root == previous[0] and service.store is previous[1]


def test_new_project_detaches_previous_folder(tmp_path):
    service = ApplicationService()
    service.save_current(dry_two_body_project(), tmp_path / 'old-project')
    project = service.new_project('Unsaved new project')
    assert project.name == 'Unsaved new project'
    assert service.root is None and service.store is None


@pytest.mark.parametrize('previously_saved', [False, True])
@pytest.mark.parametrize('failure_type', [RuntimeError, KeyboardInterrupt])
def test_rejected_or_interrupted_load_restores_active_folder(
        tmp_path, monkeypatch, previously_saved, failure_type):
    service = ApplicationService()
    project = dry_two_body_project()
    if previously_saved:
        service.save_current(project, tmp_path / 'active')
    previous = (service.root, service.store)
    candidate_root = tmp_path / 'candidate'
    service.save(project.with_edits(name='Candidate project'), candidate_root)
    def refuse_candidate(candidate):
        # Source-free fault injection: no hydro import/solve/adjudication.
        assert service.root == candidate_root
        assert service.store.root == candidate_root
        raise failure_type('simulated post-load validation refusal')
    monkeypatch.setattr(service, 'verify_hydro_provenance', refuse_candidate)
    with pytest.raises(failure_type):
        service.load(candidate_root)
    assert service.root == previous[0] and service.store is previous[1]


@pytest.mark.parametrize('previously_saved', [False, True])
def test_missing_manifest_load_preserves_active_folder(tmp_path, previously_saved):
    from anymarine.persistence import ProjectLoadError
    service = ApplicationService()
    if previously_saved:
        service.save_current(dry_two_body_project(), tmp_path / 'active')
    previous = (service.root, service.store)
    with pytest.raises(ProjectLoadError):
        service.load(tmp_path / 'missing')
    assert service.root == previous[0] and service.store is previous[1]
