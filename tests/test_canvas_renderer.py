"""ANY3dView host recovery and loading help; no numerical execution."""
import time
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
import anymarine.qtapp.mainwindow as ui

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture()
def window(app):
    w=ui.MainWindow()
    w._notify_warning=lambda *args: None
    yield w
    w._dirty=False
    w.close()
    app.processEvents()

def settle(app, window):
    deadline=time.monotonic()+1.
    while time.monotonic()<deadline:
        app.processEvents()
        if not window._fit_pending:
            break
        time.sleep(.01)
    app.processEvents()

def test_default_and_explicit_renderer_precedence(app,monkeypatch):
    monkeypatch.delenv('ANYMARINE_VIEWER_BACKEND',raising=False)
    w=ui.MainWindow()
    try:
        assert w.viewer.backend_name=='software'
        assert w._requested_viewer_backend=='auto' and w._renderer_actions['auto'].isChecked()
        assert w._viewer_backend_label.text()=='ANY3dView: software'
    finally:
        w.close()
    monkeypatch.setenv('ANYMARINE_VIEWER_BACKEND','gpu')
    w=ui.MainWindow(viewer_backend='software')
    try:
        assert w.viewer.backend_name=='software'
    finally:
        w.close()

def test_renderer_change_keeps_replay_pose_camera_and_project(app,window):
    window.load_example()
    window.resize(1800,1000)
    window.show()
    settle(app,window)
    window._frame_slider.setValue(40)
    highlighted=window._project.bodies[0].body_id
    window.viewer.set_highlight([highlighted])
    window._dirty=True
    window._toggle_play()
    original=window.viewer
    state=original.export_view_state()
    transforms={key:np.array(h.transform,copy=True) for key,h in window._handles.items()}
    project,result=window._project,window._current_result
    times=window._plot.series_times()
    channel=window._channel_combo.currentText()
    original_width=window.viewer.size().width()
    window._renderer_actions['software'].trigger()
    assert window.viewer is not original
    assert window.viewer.highlighted_tags()==frozenset([highlighted])
    assert original._closed
    updated=window.viewer.export_view_state()
    for name in ('camera_position','camera_target','camera_world_up'):
        old_point,new_point=getattr(state,name),getattr(updated,name)
        np.testing.assert_allclose([new_point.x,new_point.y,new_point.z],
                                   [old_point.x,old_point.y,old_point.z],rtol=0,atol=1e-14)
    for name in ('fov','near','far','background','visibility','semantic_selection'):
        assert getattr(updated,name)==getattr(state,name)
    assert window._project is project and window._current_result is result
    assert window._current_frame==40 and window._frame_slider.value()==40
    assert window._dirty and window._play_timer.isActive()
    assert window._plot.series_times()==times
    assert window._channel_combo.currentText()==channel
    for key,h in window._handles.items():
        np.testing.assert_array_equal(h.transform,transforms[key])
    window._play_timer.stop()
    app.processEvents()
    assert abs(window.viewer.size().width()-original_width)<=2
    pixels=np.asarray(window.viewer.capture_image().convert('RGB'),dtype=int)
    assert np.count_nonzero((pixels[:,:,2]-pixels[:,:,0]>35)&(pixels[:,:,0]<150))>100
    assert np.count_nonzero((pixels[:,:,0]-pixels[:,:,2]>50)&(pixels[:,:,0]>130))>100
    new=window.viewer
    window._dirty=False
    window.load_example()
    assert window.viewer is new

@pytest.mark.parametrize('stage',('create','populate','state'))
def test_failed_renderer_selection_retains_current_view(app,window,monkeypatch,stage):
    window.load_example()
    window.show()
    settle(app,window)
    original,handles=window.viewer,dict(window._handles)
    project,result=window._project,window._current_result
    warnings=[]
    window._notify_warning=lambda *args: warnings.append(args)
    def fail(*args,**kwargs):
        raise RuntimeError('injected renderer failure')
    if stage=='create':
        monkeypatch.setattr(ui,'create_viewer',fail)
    elif stage=='populate':
        create=ui.create_viewer
        monkeypatch.setattr(ui,'create_viewer',lambda parent,backend,**kw: create(parent,'software',**kw))
        monkeypatch.setattr(window,'_add_viewer_body',fail)
    else:
        create=ui.create_viewer
        monkeypatch.setattr(ui,'create_viewer',lambda parent,backend,**kw: create(parent,'software',**kw))
        monkeypatch.setattr(type(original),'apply_view_state',fail)
    window._renderer_actions['gpu'].trigger()
    assert window.viewer is original and window.centralWidget() is original
    assert window._handles==handles and not original._closed
    assert window._project is project and window._current_result is result
    assert window._renderer_actions['software'].isChecked()
    assert warnings and 'current view is retained' in warnings[0][1]
    assert 'injected renderer failure' in window._diagnostics.toPlainText()

def test_loading_help_is_available_in_menu(window):
    notices=[]
    window._notify_info=lambda *args: notices.append(args)
    menu=next(m for m in window._menus if m.title()=='&Help')
    action=next(a for a in menu.actions() if a.text()=='Loading models...')
    action.trigger()
    assert notices and 'project.json' in notices[0][1]
    assert 'CAD/mesh-file import is not available yet' in notices[0][1]
    assert 'View > Renderer > Software' in notices[0][1]

def test_renderer_label_tracks_automatic_fallback(window):
    window.viewer.backend_changed.emit('software')
    assert window._viewer_backend_label.text()=='ANY3dView: software'
