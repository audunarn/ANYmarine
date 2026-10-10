import json,time
import numpy as np
from PySide6.QtWidgets import QApplication
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.qtapp.native_inputs import NativeInputsDialog
from anymarine.native_jobs import project_from_snapshot
from anymarine.contact_inputs import contact_lumps
from test_mooring_contact_app import actual_contact


def check_frame(w,result,index):
    source=project_from_snapshot(result.native_snapshot);inputs=source.native_inputs.mooring
    scene=w._member_scene;bed=inputs.data['seabed_contact']['elevation']
    np.testing.assert_array_equal(scene.floor_handle.mesh.positions[:,2],[bed]*4)
    expected={}
    for i,(_,endpoint,_,ep) in enumerate(contact_lumps(inputs)):
        if result.channels[f'mooring/contact/lump{i}/active'][index]!=1.:continue
        if ep['type']=='fixed':point=ep['position']
        elif ep['type']=='node':point=result.line_nodes[index][ep['node_index']]
        else:
            bid=ep['body_id'];point=np.asarray(result.rotation_matrices[bid][index])@ep['point_body']+result.positions[bid][index]
        expected[endpoint]=point
    assert set(scene.contact_handles)==set(expected)
    for endpoint,point in expected.items():np.testing.assert_allclose(scene.contact_handles[endpoint].mesh.positions.mean(axis=0),point,rtol=0,atol=1e-12)
    for line in scene.lines:
        points=[]
        for ep in line['endpoints']:
            if ep['type']=='fixed':points.append(ep['position'])
            elif ep['type']=='node':points.append(result.line_nodes[index][ep['node_index']])
            else:
                bid=ep['body_id'];points.append(np.asarray(result.rotation_matrices[bid][index])@ep['point_body']+result.positions[bid][index])
        np.testing.assert_allclose(scene.line_handles[line['line_id']].mesh.positions,points,rtol=0,atol=1e-12)
    assert 'max penetration' in w._frame_label.text()


def test_contact_editor_stale_replay_renderer_and_stored_plot(actual_contact):
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_contact_example();assert w._member_scene.floor_handle is not None
        assert len(w._member_scene.line_handles)==3 and not w._member_scene.contact_handles
        dialog=NativeInputsDialog(w._project);path=('mooring','data','seabed_contact','elevation')
        cell=next(cell for cell,keys,_ in dialog._leaves if keys==path);cell.setText('-3.5')
        edited=w._service.load_native_inputs(w._project,dialog.data());dialog.close()
        result=actual_contact[3];w._set_project(edited.with_edits(results=(result,)));w._set_result(result)
        assert w._service.result_stale(w._project,result)
        assert w._member_scene.contact_inputs.data['seabed_contact']['elevation']==-4.
        for index in (3,0,2):w._apply_frame(index);check_frame(w,result,index)
        w._change_renderer('software');check_frame(w,result,2)
        channel='mooring/contact/max_penetration';w._channel_combo.setCurrentText(channel)
        assert w._plot.series_values()==result.channels[channel]
        w._waves.reset();check_frame(w,result,2)
    finally:w._dirty=False;w.close();app.processEvents()


def test_synthetic_replay_carries_no_invented_contact_flags():
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_contact_example();result=w._service.run_synthetic_local(w._project)
        w._set_result(result);w._apply_frame(2)
        assert w._member_scene.floor_handle is not None and not w._member_scene.contact_handles
        assert 'max penetration' not in w._frame_label.text()
    finally:w._dirty=False;w.close();app.processEvents()


def test_actual_contact_qprocess_stale_producer_geometry_and_replay(tmp_path):
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_contact_example();p=w._project;manager=w._job_manager;manager._workroot=tmp_path/'jobs'
        jid=manager.submit_native(p,max_frames=4,wall_seconds=12.)
        data=p.native_inputs.to_dict();data['mooring']['data']['seabed_contact']['elevation']=-3.5
        w._mark_edited(w._service.load_native_inputs(p,data))
        deadline=time.monotonic()+14.
        while manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        job=manager.job(jid);assert job.state=='completed',job.diagnostics
        result=w._current_result;assert result.frame_count()==4 and w._service.result_stale(w._project,result)
        assert w._replay_project.native_inputs.to_dict()==p.native_inputs.to_dict()
        for index in (3,1):w._apply_frame(index);check_frame(w,result,index)
        w._change_renderer('software');check_frame(w,result,1)
        channel='mooring/contact/potential_energy';w._channel_combo.setCurrentText(channel)
        assert w._plot.series_values()==result.channels[channel]
        payload=json.loads(job.output_path.read_text(encoding='utf-8'));assert len(payload['evidence']['accepted'])==4
    finally:
        if w._job_manager.active_jobs():
            w._job_manager.cancel_all();deadline=time.monotonic()+3.
            while w._job_manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        w._dirty=False;w.close();app.processEvents()
