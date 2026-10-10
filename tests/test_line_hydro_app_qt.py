import json,time
import numpy as np
from PySide6.QtWidgets import QApplication
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.qtapp.native_inputs import NativeInputsDialog
from test_line_hydro_app import actual_hydro


def check_frame(w,result,index):
    assert w._waves.field['kind']=='mechanical_incident' and w._waves.time==result.times[index]
    assert not w._waves.timer.isActive()
    expected=w._waves.mechanical_environment.surface_elevation(w._waves.field['vertices'][:,:2],result.times[index])
    np.testing.assert_allclose(w._waves.handle.mesh.positions[:,2],expected,atol=1e-12,rtol=0.)
    for line in w._member_scene.lines:
        expected=[]
        for ep in line['endpoints']:
            if ep['type']=='fixed':expected.append(ep['position'])
            elif ep['type']=='node':expected.append(result.line_nodes[index][ep['node_index']])
            else:
                T=w._handles[ep['body_id']].transform;expected.append(T[:3,:3]@ep['point_body']+T[:3,3])
        np.testing.assert_allclose(w._member_scene.line_handles[line['line_id']].mesh.positions,expected,atol=1e-12,rtol=0.)


def test_qt_editor_saved_wave_replay_and_renderer(actual_hydro):
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_line_hydro_example();assert len(w._member_scene.line_handles)==3
        assert len(w._member_scene.handles)==2 and w._waves.field['kind']=='mechanical_incident'
        dialog=NativeInputsDialog(w._project);path=('mooring','data','lines',0,'hydrodynamics',0,'drag_normal')
        cell=next(cell for cell,keys,_ in dialog._leaves if keys==path);cell.setText('1.2')
        edited=w._service.load_native_inputs(w._project,dialog.data());dialog.close()
        assert edited.native_inputs.mooring.data['lines'][0]['hydrodynamics'][0]['drag_normal']==1.2
        result=actual_hydro[3];w._set_project(edited.with_edits(results=(result,)))
        w._waves.mode.setCurrentIndex(3);w._set_result(result)
        assert w._service.result_stale(w._project,result)
        for frame in (3,1,2):w._apply_frame(frame);check_frame(w,result,frame)
        w._change_renderer('software');check_frame(w,result,2)
        w._waves.advance();check_frame(w,result,2)
        transforms={bid:handle.transform.copy() for bid,handle in w._handles.items()}
        lines={lid:handle.mesh.positions.copy() for lid,handle in w._member_scene.line_handles.items()}
        w._waves.mode.setCurrentIndex(0)
        assert w._current_frame==2
        for bid,expected in transforms.items():np.testing.assert_array_equal(w._handles[bid].transform,expected)
        for lid,expected in lines.items():np.testing.assert_array_equal(w._member_scene.line_handles[lid].mesh.positions,expected)
        w._waves.mode.setCurrentIndex(3);w._apply_frame(1);check_frame(w,result,1)
        transforms={bid:handle.transform.copy() for bid,handle in w._handles.items()}
        w._waves.reset()
        assert w._current_frame==1
        for bid,expected in transforms.items():np.testing.assert_array_equal(w._handles[bid].transform,expected)
    finally:w._dirty=False;w.close();app.processEvents()


def test_actual_qprocess_job_edit_during_execution_keeps_producing_environment(tmp_path):
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_line_hydro_example();p=w._project;manager=w._job_manager;manager._workroot=tmp_path/'jobs'
        jid=manager.submit_native(p,max_frames=4,wall_seconds=12.)
        data=p.native_inputs.to_dict();data['mooring']['data']['lines'][0]['hydrodynamics'][0]['drag_normal']=1.3
        data['mooring']['data']['environment']['components'][0]['phase']=.5
        data['morison']['environment']['components'][0]['phase']=.5
        edited=w._service.load_native_inputs(p,data);w._mark_edited(edited)
        deadline=time.monotonic()+14.
        while manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        job=manager.job(jid);assert job.state=='completed',job.diagnostics
        result=w._current_result;assert result.frame_count()==4 and w._service.result_stale(w._project,result)
        assert w._replay_project.native_inputs.to_dict()==p.native_inputs.to_dict()
        assert 'mooring/relative_drag_power' in result.channels
        w._apply_frame(3);w._waves.mode.setCurrentIndex(3);check_frame(w,result,3)
        assert w._waves.mechanical_environment.components[0].phase==p.native_inputs.morison.data['environment']['components'][0]['phase']
        w._apply_frame(1);check_frame(w,result,1)
        channel='mooring/node0/drag_normal x';w._channel_combo.setCurrentText(channel)
        assert w._plot.series_values()==result.channels[channel]
        payload=json.loads(job.output_path.read_text());assert len(payload['evidence']['accepted'])==4
    finally:
        if w._job_manager.active_jobs():
            w._job_manager.cancel_all();deadline=time.monotonic()+3.
            while w._job_manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        w._dirty=False;w.close();app.processEvents()
