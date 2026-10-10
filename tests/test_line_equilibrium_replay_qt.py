import numpy as np
from PySide6.QtWidgets import QApplication
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.services import ApplicationService
from anymarine.line_equilibrium import LineEquilibriumInputs,project_from_snapshot,stages
from test_line_equilibrium_replay import imported


def test_bodyless_stale_static_replay_and_renderer_preserve_all_stored_stages():
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        for name in ('suspended-64','touchdown-64'):
            p=imported(name);r=p.results[0];d=p.line_equilibrium_inputs.to_dict()
            d['model']['lines'][0]['endpoints'][0]['position'][0]+=20.
            if 'seabed_contact' in d['model']:d['model']['seabed_contact']['elevation']-=1.
            edited=p.with_edits(line_equilibrium_inputs=LineEquilibriumInputs.from_dict(d))
            w._set_project(edited);w._set_result(r)
            assert ApplicationService().result_stale(w._project,r)
            assert w._replay_project.to_dict()==project_from_snapshot(r.native_snapshot).to_dict()
            assert 'stored line equilibrium' in w._result_status
            assert not w._handles and not w._member_scene.contact_handles
            for i in range(r.frame_count()):
                w._apply_frame(i);np.testing.assert_array_equal(w._member_scene.line_nodes,r.line_nodes[i])
                assert stages(r)[i]['name'] in w._frame_label.text()
                for line in w._member_scene.lines:
                    expected=[ep['position'] if ep['type']=='fixed' else r.line_nodes[i][ep['node_index']] for ep in line['endpoints']]
                    np.testing.assert_array_equal(w._member_scene.line_handles[line['line_id']].mesh.positions,expected)
                if name=='touchdown-64':np.testing.assert_array_equal(w._member_scene.floor_handle.mesh.positions[:,2],[-3.]*4)
            assert 'final audit' in w._frame_label.text()
            w._change_renderer('software');assert not w._member_scene.contact_handles
            np.testing.assert_array_equal(w._member_scene.line_nodes,r.line_nodes[-1])
            assert w._plot._abscissa=='stage'
            for channel,values in r.channels.items():
                w._channel_combo.setCurrentText(channel);assert w._plot.series_values()==values
    finally:w._dirty=False;w.close();app.processEvents()
