"""Shared wave application development contracts; real native runs <=3 steps."""
import copy
import json
import time
import numpy as np
import pytest
from anymarine.services import ApplicationService,ServiceError
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.native_jobs import project_from_snapshot,decode_output
from anymarine.hashing import input_hash

def project():return ApplicationService().load_native_sea_state_example()

def test_saved_example_compiler_common_identity_and_no_alias():
    p=project();assert not p.results and p.native_inputs.run_spec['steps']==3
    data=p.native_inputs.to_dict();waves=p.native_inputs.wave_realization
    assert data['morison']['environment']['components']==data['wave_realization']['components']
    assert data['potential_flow']['excitation_series']['wave_realization_hash']==waves.content_hash
    model,states,spec=ApplicationService().prepare_native_run(p)
    assert spec.steps==3 and len(model.contributions)==2
    assert model.contributions[0].data.excitation_series.wave_realization_hash==waves.content_hash
    original=data['wave_realization']['components'][0]['phase'];data['wave_realization']['components'][0]['phase']+=1.
    assert waves.data['components'][0]['phase']==original
    with pytest.raises(TypeError):waves.data['seed']=99

def test_explicit_generation_and_save_reopen_keeps_materialized_phases(tmp_path,monkeypatch):
    service=ApplicationService();p=project()
    updated=service.replace_native_sea_state(p,significant_height=.03,peak_period=9.,seed=18)
    assert input_hash(updated)!=input_hash(p)
    assert updated.native_inputs.wave_realization.content_hash!=p.native_inputs.wave_realization.content_hash
    data=updated.native_inputs.to_dict()
    assert data['morison']['environment']['components']==data['wave_realization']['components']
    service.save_current(updated,tmp_path/'saved')
    monkeypatch.setattr(np.random,'PCG64',lambda *args:pytest.fail('reopen regenerated phases'))
    reopened=service.load(tmp_path/'saved')
    assert reopened.native_inputs.to_dict()==updated.native_inputs.to_dict()
    assert input_hash(reopened)==input_hash(updated)
    snapshot=service.native_input_snapshot(reopened,max_frames=4,wall_seconds=8.)
    assert project_from_snapshot(snapshot).native_inputs.to_dict()==data

@pytest.mark.parametrize('change',[
    lambda d:d['wave_realization'].update(provider='python'),
    lambda d:d['wave_realization'].update(seed=True),
    lambda d:d['wave_realization']['components'][0].update(phase=1.),
    lambda d:d['morison']['environment']['components'][0].update(amplitude=.9),
    lambda d:d['potential_flow']['excitation_series'].update(wave_realization_hash='0'*64),
    lambda d:d['potential_flow']['excitation_series']['weighted_real'][0].__setitem__(0,True),
    lambda d:d['potential_flow'].update(omega=1.),
    lambda d:d.update(wave_realization=None),
])
def test_unknown_stale_duplicate_and_mutation_refused(change):
    p=project();data=p.native_inputs.to_dict();change(data)
    with pytest.raises(ValueError):NativeMechanicalInputs.from_dict(data)
    assert not p.results

def test_source_mismatch_and_invalid_generation_atomic():
    service=ApplicationService();p=project();before=p.to_dict()
    with pytest.raises((ValueError,ServiceError)):
        service.replace_native_sea_state(p,significant_height=-1.,peak_period=8.,seed=17)
    data=p.native_inputs.to_dict();data['potential_flow']['excitation_series']['hydro_content_hash']='0'*64
    bad=p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    with pytest.raises(ServiceError,match='exact bundled'):
        service.replace_native_sea_state(bad,significant_height=.03,peak_period=8.,seed=17)
    assert p.to_dict()==before

def test_qt_editor_and_saved_incident_layer(tmp_path):
    from PySide6.QtWidgets import QApplication,QDialog
    from anymarine.qtapp.mainwindow import MainWindow
    from anymarine.qtapp.native_inputs import NativeInputsDialog
    from anymarine.qtapp.sea_state_dialog import SeaStateDialog
    from anyloads import WaveRealization
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_sea_state_example();p=w._project
        assert len(w._handles)==2 and len(w._member_scene.handles)==2
        dialog=NativeInputsDialog(p)
        for cell,path,kind in dialog._leaves:
            if 'wave_realization' in path or 'excitation_series' in path or 'components' in path:
                assert not cell.isEditable()
        assert dialog.data()==p.native_inputs.to_dict();dialog.close()
        sea=SeaStateDialog(p);sea.height.setValue(.03);sea.seed.setValue(18)
        updated=w._service.replace_native_sea_state(p,**sea.parameters());sea.close();w._mark_edited(updated)
        w._waves.job_id='obsolete-wave-job'
        w._waves.cancel=lambda:None
        w._waves.mode.setCurrentIndex(3)
        assert w._waves.job_id is None
        assert not w._waves.amplitude.isEnabled() and not w._waves.depth.isEnabled()
        w._waves.field_ready('obsolete-wave-job',{})
        w._waves.job_state('obsolete-wave-job','completed')
        assert w._waves.mode.currentIndex()==3 and not w._waves.compute.isEnabled()
        assert w._waves.active and w._waves.handle is not None and not w._waves.compute.isEnabled(),w._waves.status.text()
        waves=WaveRealization.from_dict(updated.native_inputs.wave_realization.to_dict())
        xy=w._waves.field['vertices'][:,:2]
        env=waves.environment(updated.native_inputs.morison.data['environment']['water_depth'])
        np.testing.assert_allclose(w._waves.positions()[:,2],env.surface_elevation(xy,w._waves.time),rtol=1e-12,atol=1e-12)
        poses={bid:np.array(h.transform) for bid,h in w._handles.items()}
        w._waves.advance();w._change_renderer('software')
        assert w._waves.handle is not None
        for bid,h in w._handles.items():np.testing.assert_array_equal(h.transform,poses[bid])
        w._waves.mode.setCurrentIndex(0);assert w._waves.compute.isEnabled() and not w._waves.active
    finally:w._dirty=False;w.close();app.processEvents()

def test_actual_qprocess_stale_producing_waves_saved_arrays_and_replay(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    try:
        w.load_native_sea_state_example();p=w._project;manager=w._job_manager
        manager._workroot=tmp_path/'jobs';jid=manager.submit_native(p,max_frames=4,wall_seconds=8.)
        changed=w._service.replace_native_sea_state(p,significant_height=.03,peak_period=8.,seed=18)
        w._mark_edited(changed)
        deadline=time.monotonic()+10.
        while manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        job=manager.job(jid);assert job.state=='completed',job.diagnostics
        result=w._current_result;assert result.frame_count()==4 and 'STALE' in w._result_status
        assert w._replay_project.native_inputs.to_dict()==p.native_inputs.to_dict()
        raw=job.output_path.read_bytes();payload=json.loads(raw)
        assert payload['evidence']['histories']['morison-members-development'] is None
        mutated=copy.deepcopy(payload);mutated['result']['positions'][result.body_ids[0]][-1][0]+=1.
        import hashlib
        damaged=json.dumps(mutated).encode()
        with pytest.raises(ValueError,match='authoritative evidence'):
            decode_output(damaged,job.snapshot,job.input_hash,hashlib.sha256(damaged).hexdigest())
        producing=project_from_snapshot(result.native_snapshot);service=ApplicationService()
        service.save_current(service.add_result(producing,result),tmp_path/'saved')
        reopened=service.load(tmp_path/'saved')
        assert reopened.results[0].to_dict()==result.to_dict() and not service.result_stale(reopened,result)
        channel='wrench'+json.dumps(['potential-flow-development',result.body_ids[0]],separators=(',',':'))+'/Fx'
        w._channel_combo.setCurrentText(channel);assert w._plot.series_values()==result.channels[channel]
        w._apply_frame(3);w._waves.mode.setCurrentIndex(3)
        assert w._waves.field['wave_realization_hash']==p.native_inputs.wave_realization.content_hash
        assert w._waves.time==result.times[3] and not w._waves.timer.isActive()
        w._change_renderer('software')
        assert w._waves.time==result.times[3] and not w._waves.timer.isActive()
    finally:
        if w._job_manager.active_jobs():
            w._job_manager.cancel_all();deadline=time.monotonic()+3.
            while w._job_manager.active_jobs() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        w._dirty=False;w.close();app.processEvents()

def test_worker_precancel_preserves_unsealed_partial(tmp_path,capsys):
    from anymarine.worker import run
    service=ApplicationService();snapshot=service.native_input_snapshot(project(),max_frames=4,wall_seconds=8.)
    inp,out,cancel=tmp_path/'input.json',tmp_path/'output.json',tmp_path/'cancel'
    service.write_job_input(snapshot,inp);cancel.touch()
    assert run(inp,out,cancel)==3 and not out.exists()
    assert 'OUTPUT_SHA256' not in capsys.readouterr().out
    assert json.loads(out.with_suffix('.partial.json').read_text())['status']=='cancelled'


def test_finite_excitation_mutation_refused_by_production_compiler():
    p=project();data=p.native_inputs.to_dict()
    data['potential_flow']['excitation_series']['weighted_real'][0][0]+=1.
    changed=p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    with pytest.raises(ValueError,match='differs from the saved waves'):
        ApplicationService().prepare_native_run(changed)


def test_non_jonswap_editor_refuses_cleanly():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.sea_state_dialog import SeaStateDialog
    app=QApplication.instance() or QApplication([])
    record=SimpleNamespace(native_inputs=SimpleNamespace(wave_realization=SimpleNamespace(data={'jonswap':None,'seed':None})))
    with pytest.raises(ValueError,match='seeded JONSWAP'):
        SeaStateDialog(record)
