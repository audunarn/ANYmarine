import hashlib
from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from anymarine import RESULT_KIND_SYNTHETIC
from anymarine.services import ApplicationService, ServiceError
from anymarine.qtapp.mainwindow import MainWindow, run_app


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def bundled_bytes():
    import anymarine.services
    root = Path(anymarine.services.__file__).parent / 'examples' / 'two-body-replay'
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_example_is_self_contained_and_saves_without_changing_bundle(tmp_path):
    before = bundled_bytes()
    service = ApplicationService(tmp_path / 'old-project')
    project = service.load_example()
    assert service.root is None and service.store is None
    assert len(project.bodies) == 2 and len(project.results) == 1
    result = project.results[0]
    assert result.kind == RESULT_KIND_SYNTHETIC
    assert result.body_ids == tuple(b.body_id for b in project.bodies)
    assert result.frame_count() == 81
    assert result.times[0] == 0 and result.times[-1] == 20
    assert not service.result_stale(project, result)
    with pytest.raises(ServiceError, match='no project root'):
        service.save(project)
    destination = tmp_path / 'saved-copy'
    service.save(project, destination)
    loaded = ApplicationService().load(destination)
    assert loaded == project
    assert bundled_bytes() == before
    assert not (tmp_path / 'old-project').exists()


def test_menu_loads_ready_replay_and_preserves_discard_choice(app, monkeypatch):
    window = MainWindow()
    try:
        action = next(a for a in window.findChildren(QAction)
                      if 'example' in a.text())
        action.trigger()
        assert window._project_root is None
        result = window._current_result
        assert result is window._project.results[0]
        assert len(window._handles) == 2
        assert window._frame_slider.maximum() == 80
        assert window._channel_combo.count() == 4
        assert not window._job_manager.active_jobs()
        assert 'synthetic' in window.statusBar().currentMessage().lower()
        window._frame_slider.setValue(40)
        assert window._current_frame == 40
        window._toggle_play()
        assert window._play_timer.isActive()
        window._advance_frame()
        assert window._current_frame == 41
        window._play_timer.stop()
        original = window._project
        window._dirty = True
        monkeypatch.setattr(window, '_confirm_discard', lambda: False)
        action.trigger()
        assert window._project is original and window._dirty
    finally:
        window._dirty = False
        window.close()
        app.processEvents()


@pytest.mark.parametrize('explicit', [False, True])
def test_application_startup_selects_example_only_without_project(app, monkeypatch, tmp_path, explicit):
    shown = []
    original = MainWindow.show
    def show(window):
        shown.append(window)
        original(window)
        app.processEvents()
    monkeypatch.setattr(MainWindow, 'show', show)
    monkeypatch.setattr(QApplication, 'exec', lambda self: 7)
    project = ApplicationService().new_project('My saved project')
    if explicit:
        ApplicationService().save(project, tmp_path)
    try:
        assert run_app(str(tmp_path) if explicit else None) == 7
        window = shown[0]
        if explicit:
            assert window._project.name == 'My saved project'
            assert window._project_root == tmp_path
            assert window._current_result is None
        else:
            assert window._current_result.kind == RESULT_KIND_SYNTHETIC
            assert window._project_root is None
    finally:
        for window in shown:
            window._dirty = False
            window.close()
        app.processEvents()
