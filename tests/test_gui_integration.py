import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('ANYMARINE_VIEWER_BACKEND','software')
from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication
import pytest
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.services import ApplicationService

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

def test_body_tree_exposes_editable_bodies(app):
    window=MainWindow()
    try:
        project=ApplicationService().new_project('tree')
        window._set_project(project)
        model=window._tree_model
        bodies=model.index(0,0,QModelIndex())
        assert model.rowCount(bodies)==len(project.bodies)
    finally:
        window._dirty=False
        window.close()
        app.processEvents()

def test_initial_result_populates_plot(app):
    window=MainWindow()
    try:
        service=ApplicationService()
        project=service.new_project('plot')
        result=service.run_synthetic_local(project)
        window._set_project(project)
        calls=[]
        original=window._plot.set_series
        def record(name,times,values):
            calls.append((name,list(times),list(values)))
            return original(name,times,values)
        window._plot.set_series=record
        window._set_result(result)
        assert any(len(times)==len(result.times) for _,times,_ in calls)
    finally:
        window._dirty=False
        window.close()
        app.processEvents()

def test_failed_start_transitions_once_and_advances_queue(app, monkeypatch):
    import time
    from PySide6.QtCore import QProcess
    from anymarine.qtapp.jobs import JobManager, JOB_STATE_FAILED
    service = ApplicationService()
    manager = JobManager(service)
    original = QProcess.start
    def fail_start(process, *args):
        process.setProgram('anymarine-deliberately-missing-executable')
        return original(process, *args)
    monkeypatch.setattr(QProcess, 'start', fail_start)
    failures = []
    manager.job_failed.connect(lambda identity, message: failures.append(identity))
    first = manager.submit(service.new_project('first'))
    second = manager.submit(service.new_project('second'))
    deadline = time.monotonic() + 3
    while len(failures) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert manager.job(first).state == manager.job(second).state == JOB_STATE_FAILED
    assert sorted(failures) == sorted([first,second])
    assert not manager.active_jobs()

def test_result_keeps_viewport_visible_at_desktop_size(app):
    window = MainWindow()
    try:
        service = ApplicationService()
        project = service.new_project('layout')
        result = service.run_synthetic_local(project)
        window._set_project(project)
        window._set_result(result)
        window._details.show_result(result.result_id)
        window.resize(1280, 800)
        window.show()
        app.processEvents()
        assert window.viewer.isVisible()
        assert window.viewer.geometry().width() >= 480
        assert window.viewer.geometry().height() >= 300
        assert window.viewer.geometry().right() < window.width()
    finally:
        window._dirty = False
        window.close()
        app.processEvents()
