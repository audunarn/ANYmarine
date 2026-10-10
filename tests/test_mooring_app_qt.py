import json
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.qtapp.native_inputs import NativeInputsDialog
from anymarine.native_examples import mooring_two_body_project
from anymarine.domain import ResultRecord
from test_mooring_app import actual


def assert_lines(w,positions):
    for line in w._member_scene.lines:
        expected=[]
        for ep in line['endpoints']:
            if ep['type']=='fixed':expected.append(ep['position'])
            elif ep['type']=='node':expected.append(positions[ep['node_index']])
            else:
                T=w._handles[ep['body_id']].transform
                expected.append(T[:3,:3]@ep['point_body']+T[:3,3])
        np.testing.assert_allclose(w._member_scene.line_handles[line['line_id']].mesh.positions,expected,atol=1e-12,rtol=0.)


def test_editor_load_replay_stale_snapshot_and_renderer_preserve_authoritative_nodes(actual):
    app=QApplication.instance() or QApplication([])
    w=MainWindow(viewer_backend='software')
    try:
        w.load_native_mooring_example()
        assert len(w._member_scene.line_handles)==3 and w._current_result is None
        assert_lines(w,w._project.native_inputs.mooring.initial_nodes['positions'])
        dialog=NativeInputsDialog(w._project)
        assert dialog.data()==w._project.native_inputs.to_dict()
        path=('mooring','data','lines',0,'axial_rigidity',0)
        cell=next(cell for cell,keys,_ in dialog._leaves if keys==path)
        cell.setText('45.')
        edited=w._service.load_native_inputs(w._project,dialog.data());dialog.close()
        assert edited.native_inputs.mooring.data['lines'][0]['axial_rigidity'][0]==45.
        record=actual[3]
        w._set_project(edited.with_edits(native_inputs=None,results=(record,)))
        w._set_result(record);w._apply_frame(3)
        assert w._service.result_stale(w._project,record)
        assert '/mooring mechanics' in w._result_status
        assert_lines(w,record.line_nodes[3])
        w._change_renderer('software')
        assert_lines(w,record.line_nodes[3])
        assert w._current_frame==3
        w._set_result(None)
        assert not w._member_scene.line_handles
    finally:
        w.close();app.processEvents()
