from pathlib import Path
import hashlib,json,time
import numpy as np
import pytest
pytest.importorskip('PySide6')
pytest.importorskip('any3dview')
from PySide6.QtWidgets import QApplication
from anymarine.equilibrium_jobs import decode_output,project_from_snapshot
from anymarine.qtapp.mainwindow import MainWindow

@pytest.fixture
def app():return QApplication.instance() or QApplication([])

def actual():
    root=Path(__file__).parent/'fixtures/static-equilibrium'
    snap=json.loads((root/'input.json').read_text(encoding='utf-8'));raw=(root/'output.json').read_bytes()
    return project_from_snapshot(snap),decode_output(raw,snap,snap['input_hash'],hashlib.sha256(raw).hexdigest())

def test_loader_and_stored_static_replay_without_time_or_waves(app):
    w=MainWindow(viewer_backend='software')
    try:
        w.load_equilibrium_example();p,r=actual();w._set_project(p);w._set_result(r)
        w._apply_frame(8);assert 'stage 8' in w._frame_label.text() and 't=' not in w._frame_label.text()
        assert 'regeneration' in w._frame_label.text() and w._plot._abscissa=='stage'
        assert not w._waves.active
        for bid,h in w._handles.items():np.testing.assert_allclose(h.transform[:3,3],r.positions[bid][-1])
        for frame in (0,5,8):
            w._apply_frame(frame)
            np.testing.assert_array_equal(w._member_scene.line_nodes,r.line_nodes[frame])
        w._change_renderer('software');w._apply_frame(8)
        assert w._current_result is r
    finally:w._dirty=False;w.close();app.processEvents()

def test_actual_qprocess_static_job_stale_edit_and_snapshot_replay(app):
    w=MainWindow(viewer_backend='software')
    warnings=[];w._notify_warning=lambda *args:warnings.append(args);w._notify_info=lambda *args:None;w._notify_critical=lambda *args:warnings.append(args)
    try:
        w.load_equilibrium_example();original=w._project
        jid=w._job_manager.submit_equilibrium(original)
        w._project=original.with_edits(native_inputs=None)
        deadline=time.monotonic()+15.
        while w._job_manager.job(jid).state in ('queued','running','cancelling') and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert w._job_manager.job(jid).state=='completed',dict(warnings=warnings,path=str(w._job_manager.job(jid).stderr_path))
        assert w._current_result.kind=='equilibrium_development'
        assert w._service.result_stale(w._project,w._current_result)
        w._apply_frame(8);assert 'stale' in w._frame_label.text() and w._replay_project.native_inputs is not None
    finally:
        w._job_manager.cancel_all()
        deadline=time.monotonic()+5.
        while w._job_manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert not w._job_manager.active_jobs()
        w._dirty=False;w.close();app.processEvents()

@pytest.mark.parametrize('mode',[1,2,3])
def test_static_replay_refuses_wave_clock_and_preserves_body_node_stage(app,mode):
    w=MainWindow(viewer_backend='software')
    try:
        p,r=actual();w._set_project(p);w._set_result(r);w._apply_frame(8)
        poses={bid:np.array(h.transform,copy=True) for bid,h in w._handles.items()}
        w._waves.mode.setCurrentIndex(mode);app.processEvents()
        assert w._waves.mode.currentIndex()==0 and not w._waves.active
        assert 'no wave time' in w._waves.status.text()
        w._apply_frame(5);w._waves.reset();w._apply_frame(8)
        for bid,h in w._handles.items():np.testing.assert_array_equal(h.transform,poses[bid])
        np.testing.assert_array_equal(w._member_scene.line_nodes,r.line_nodes[8])
    finally:w._dirty=False;w.close();app.processEvents()

@pytest.mark.parametrize('mode',['cancel','crash','close'])
def test_actual_static_owned_job_failure_and_closure(app,tmp_path,monkeypatch,mode):
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QMessageBox
    if mode=='crash':
        original=QProcess.start
        def start(process,*args):
            process.started.connect(process.kill);original(process,*args)
        monkeypatch.setattr(QProcess,'start',start)
    w=MainWindow(viewer_backend='software');manager=w._job_manager;manager._workroot=tmp_path
    try:
        w.load_equilibrium_example();jid=manager.submit_equilibrium(w._project);record=manager.job(jid)
        assert record.state=='running'
        if mode=='cancel':manager.request_cancel(jid)
        if mode=='close':
            monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
            w._dirty=False;assert w.close()
        deadline=time.monotonic()+6.
        while manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert not manager.active_jobs()
        assert record.state==('failed' if mode=='crash' else 'cancelled')
        assert not w._project.results and not record.output_path.exists()
        assert record.snapshot['kind']=='equilibrium_development'
        assert record.stdout_path.is_file() and record.stderr_path.is_file()
        if mode!='crash':assert record.output_path.with_suffix('.incomplete.json').is_file()
    finally:
        manager.cancel_all();deadline=time.monotonic()+5.
        while manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert not manager.active_jobs()
        w._dirty=False;w.close();app.processEvents()
