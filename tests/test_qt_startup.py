"""Host lifecycle regressions; no GPU dependency, BEM or numerical jobs."""
import time
import numpy as np
import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication
import anymarine.qtapp.mainwindow as module

@pytest.fixture
def app():return QApplication.instance() or QApplication([])

def record_creation(monkeypatch):
    original=module.create_viewer;calls=[]
    def create(parent,backend,**options):
        calls.append((backend,parent.isVisible(),bool(parent.windowHandle() and parent.windowHandle().isExposed())))
        return original(parent,'software',**options)
    monkeypatch.setattr(module,'create_viewer',create);return calls

def settle(app,predicate):
    end=time.monotonic()+1.
    while not predicate() and time.monotonic()<end:app.processEvents();time.sleep(.005)
    assert predicate()

@pytest.mark.parametrize('backend',['auto','gpu'])
def test_gpu_host_waits_for_visible_exposed_window_preserves_stored_replay(app,monkeypatch,backend):
    calls=record_creation(monkeypatch);w=module.MainWindow(viewer_backend=backend)
    try:
        assert calls==[('software',False,False)] and w._startup_renderer_pending
        w.load_example();w._apply_frame(3)
        poses={bid:np.array(h.transform,copy=True) for bid,h in w._handles.items()}
        result=w._current_result;project=w._project
        w.show();settle(app,lambda:not w._startup_renderer_pending)
        assert len(calls)==2 and calls[-1]==(backend,True,True)
        assert w._project is project and w._current_result is result and w._current_frame==3
        for bid,h in w._handles.items():np.testing.assert_array_equal(h.transform,poses[bid])
        assert not w._job_manager.jobs()
        w.hide();w.show();app.processEvents();assert len(calls)==2
    finally:w._dirty=False;w.close();app.processEvents()

def test_explicit_software_never_defers(app,monkeypatch):
    calls=record_creation(monkeypatch);w=module.MainWindow(viewer_backend='software')
    try:
        w.load_example();w.show();app.processEvents()
        assert len(calls)==1 and calls[0][0]=='software' and not w._startup_renderer_pending
    finally:w._dirty=False;w.close();app.processEvents()

def test_explicit_renderer_choice_cancels_pending_start(app,monkeypatch):
    calls=record_creation(monkeypatch);w=module.MainWindow(viewer_backend='auto')
    try:
        w.load_example();w._change_renderer('software');w.show();app.processEvents()
        assert len(calls)==2 and all(c[0]=='software' for c in calls)
        assert w._requested_viewer_backend=='software' and not w._startup_renderer_pending
        assert not w._startup_renderer_timer.isActive()
    finally:w._dirty=False;w.close();app.processEvents()

def test_hide_before_start_resumes_once_on_reshow(app,monkeypatch):
    calls=record_creation(monkeypatch);w=module.MainWindow(viewer_backend='auto')
    try:
        w.load_example();w.show();w.hide();app.processEvents()
        assert len(calls)==1 and w._startup_renderer_pending and not w._startup_renderer_timer.isActive()
        w.show();settle(app,lambda:not w._startup_renderer_pending)
        assert len(calls)==2 and calls[-1][1:]==(True,True)
    finally:w._dirty=False;w.close();app.processEvents()

def test_unexposed_window_waits_for_expose_without_polling(app,monkeypatch):
    calls=record_creation(monkeypatch);w=module.MainWindow(viewer_backend='auto')
    exposure={'value':False}
    class NativeWindow(QObject):
        def isExposed(self):return exposure['value']
    native=NativeWindow()
    monkeypatch.setattr(w,'_watch_startup_window',lambda:native)
    w._startup_window=native
    try:
        w.load_example();w.show();app.processEvents()
        assert len(calls)==1 and w._startup_renderer_pending and not w._startup_renderer_timer.isActive()
        exposure['value']=True;w.eventFilter(native,QEvent(QEvent.Type.Expose))
        settle(app,lambda:not w._startup_renderer_pending)
        assert len(calls)==2
    finally:w._dirty=False;w.close();app.processEvents()

def test_close_before_exposure_never_constructs_gpu(app,monkeypatch):
    calls=record_creation(monkeypatch);w=module.MainWindow(viewer_backend='auto')
    w.load_example();w.show();assert w.close();app.processEvents()
    assert len(calls)==1 and not w.isVisible() and not w._startup_renderer_timer.isActive()
