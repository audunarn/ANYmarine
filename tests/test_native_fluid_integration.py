"""Persisted native fluid DEVELOPMENT workflow; <=3steps per actual run."""
import copy
import hashlib
import json
import pathlib
import time
from dataclasses import replace

import pytest
from anymarine.services import ApplicationService, ServiceError
from anymarine.hashing import input_hash
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.native_jobs import project_from_snapshot, record_from_evidence
from anymarine.persistence import ProjectLoadError


def project():
    return ApplicationService().load_native_fluid_example()


def force_channel(body_id):
    return 'wrench'+json.dumps(['potential-flow-development',body_id],separators=(',',':'))+'/Fx'


@pytest.mark.dto
def test_legacy_dry_hash_and_document_unchanged_fluid_load_has_no_results():
    old=json.loads(pathlib.Path('C:/ANYworkspaceWork/anymarine-evidence/native-fluid-app-legacy-baseline.json').read_text())
    dry=ApplicationService().load_native_example()
    assert input_hash(dry)==old['input_hash'] and dry.to_dict()==old['project']
    assert 'potential_flow' not in dry.native_inputs.to_dict()
    p=project()
    assert not p.results and len(p.bodies)==2 and p.native_inputs.run_spec['steps']==3
    original=p.native_inputs.to_dict()
    restored=NativeMechanicalInputs.from_dict(original)
    assert restored.to_dict()==original
    original['potential_flow']['kernel'][0][0][0]=999.
    assert restored.potential_flow.data['kernel'][0][0][0]==1.
    with pytest.raises(TypeError):
        restored.potential_flow.data['dt']=7.
    altered=restored.to_dict()
    altered['potential_flow']['excitation_real'][0]+=.1
    changed=p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(altered))
    assert input_hash(changed)!=input_hash(p)
    service=ApplicationService()
    snapshot=service.native_input_snapshot(p,max_frames=4,wall_seconds=6.)
    assert project_from_snapshot(snapshot).native_inputs.to_dict()==p.native_inputs.to_dict()


@pytest.mark.dto
@pytest.mark.parametrize('failure',('unknown','version','basis','gravity','kind','dt','boolean','order','none'))
def test_data_only_input_refuses_discarded_unknown_or_malformed_declarations(failure):
    data=project().native_inputs.to_dict()
    if failure=='none':
        data['potential_flow']=None
    else:
        fluid=data['potential_flow']
        if failure=='unknown': fluid['provider']='arbitrary.module'
        elif failure=='version': fluid['api_version']='unrecognized'
        elif failure=='basis': fluid['force_basis']='physical_wrench'
        elif failure=='gravity': fluid['gravity_included']=True
        elif failure=='kind': fluid['source_kind']='bem'
        elif failure=='dt': fluid['dt']*=2.
        elif failure=='boolean': fluid['kernel'][0][0][0]=True
        else: fluid['frames'][0]['body_id']=fluid['frames'][1]['body_id']
    with pytest.raises(ValueError):
        NativeMechanicalInputs.from_dict(data)


@pytest.mark.compile
def test_shared_compiler_admits_named_fluid_and_refuses_stale_reference_or_initial_bounds():
    service=ApplicationService()
    p=project()
    model,states,spec=service.prepare_native_run(p)
    assert model.contributions[-1].contribution_id=='potential-flow-development'
    assert spec.steps==3 and tuple(states)==tuple(b.body_id for b in p.bodies)
    stale=p.with_edits(bodies=(p.bodies[0].with_edits(reference_position=(-1.,0.,0.)),p.bodies[1]))
    with pytest.raises(ValueError,match='equilibrium'):
        service.prepare_native_run(stale)
    bad=p.native_inputs.to_dict()
    bad['initial_states'][p.bodies[0].body_id]['position'][0]=-5.
    with pytest.raises(ServiceError,match='outside'):
        service.load_native_inputs(p,bad)
    bad=p.native_inputs.to_dict()
    bad['potential_flow']['equilibrium_hash']='0'*64
    with pytest.raises(ServiceError,match='binding'):
        service.load_native_inputs(p,bad)
    assert not p.results


@pytest.mark.compile
def test_missing_or_incompatible_optional_fluid_backend_is_typed_and_atomic(monkeypatch):
    import anymarine.mechanics as mechanics
    from types import SimpleNamespace
    original=mechanics.importlib.import_module
    p=project()
    for mode in ('missing','wrong'):
        def imported(name):
            if name=='anysolver.marine_potential_flow':
                if mode=='missing': raise ImportError('test missing optional fluid interface')
                return SimpleNamespace(API_VERSION='wrong')
            return original(name)
        monkeypatch.setattr(mechanics.importlib,'import_module',imported)
        with pytest.raises(mechanics.MechanicsUnavailableError):
            mechanics.prepare_native_run(p)
    assert not p.results


def worker_result(tmp_path,capsys):
    from anymarine.worker import run
    p=project()
    service=ApplicationService()
    snapshot=service.native_input_snapshot(p,max_frames=4,wall_seconds=6.)
    inp,out=tmp_path/'input.json',tmp_path/'output.json'
    service.write_job_input(snapshot,inp)
    assert run(inp,out,None)==0
    stdout=capsys.readouterr().out
    seal=[line.split()[1] for line in stdout.splitlines() if line.startswith('OUTPUT_SHA256 ')][0]
    record=service.load_job_output(out,snapshot['input_hash'],snapshot=snapshot,expected_output_seal=seal)
    return p,snapshot,json.loads(out.read_text()),record


@pytest.mark.production
def test_actual_worker_sealed_result_save_reopen_stale_and_missing_sidecar(tmp_path,capsys):
    p,snapshot,payload,record=worker_result(tmp_path,capsys)
    assert record.frame_count()==4 and not record.orientations
    assert 'potential-flow' in record.note and 'unqualified' in record.note
    ids=record.body_ids
    assert payload['evidence']['histories']['potential-flow-development']['step_index']==3
    assert len(payload['evidence']['histories']['potential-flow-development']['velocities'])==2
    assert all(record.channels[force_channel(bid)] for bid in ids)
    service=ApplicationService()
    root=tmp_path/'saved'
    stored=service.add_result(p,record)
    service.save_current(stored,root)
    reopened=service.load(root)
    assert reopened.results[0].to_dict()==record.to_dict()
    assert not service.result_stale(reopened,reopened.results[0])
    edited=reopened.native_inputs.to_dict()
    edited['potential_flow']['excitation_real'][0]+=.1
    changed=reopened.with_edits(native_inputs=NativeMechanicalInputs.from_dict(edited))
    assert service.result_stale(changed,record)
    producing=project_from_snapshot(record.native_snapshot)
    assert producing.native_inputs.potential_flow.to_dict()!=changed.native_inputs.potential_flow.to_dict()
    for corruption in ('dt','index','shape','missing'):
        bad=copy.deepcopy(payload['evidence'])
        memory=bad['histories']['potential-flow-development']
        if corruption=='dt': memory['dt']*=2.
        elif corruption=='index': memory['step_index']-=1
        elif corruption=='shape': memory['velocities'][-1].pop()
        else: del bad['histories']['potential-flow-development']
        with pytest.raises(ValueError):
            record_from_evidence(bad,snapshot,result_id='bad',note='',created_utc='')
    sidecars=list((root/'results').rglob('*'))
    files=[path for path in sidecars if path.is_file()]
    assert files
    files[0].unlink()
    with pytest.raises(ProjectLoadError):
        service.load(root)


@pytest.mark.production
def test_actual_fluid_cancelled_before_step_keeps_partial_and_never_seals(tmp_path,capsys):
    from anymarine.worker import run
    p=project()
    service=ApplicationService()
    snapshot=service.native_input_snapshot(p,max_frames=4,wall_seconds=6.)
    inp,out,cancel=tmp_path/'cancel-input.json',tmp_path/'cancel-output.json',tmp_path/'cancel'
    service.write_job_input(snapshot,inp)
    cancel.touch()
    assert run(inp,out,cancel)==3 and not out.exists()
    stdout=capsys.readouterr().out
    assert 'OUTPUT_SHA256' not in stdout
    partial=json.loads((tmp_path/'cancel-output.partial.json').read_text())
    assert partial['status']=='cancelled'


@pytest.mark.qt
def test_real_qt_loader_editor_qprocess_and_frozen_replay(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    from anymarine.qtapp.native_inputs import NativeInputsDialog
    from anymarine.qtapp.jobs import JobManager
    app=QApplication.instance() or QApplication([])
    service=ApplicationService()
    manager=JobManager(service)
    manager._workroot=tmp_path/'jobs'
    received=[]
    manager.result_ready.connect(lambda job_id,result:received.append(result))
    window=MainWindow()
    try:
        window.load_native_fluid_example()
        p=window._project
        assert p.native_inputs.potential_flow is not None and len(window._handles)==2
        assert window._current_result is None
        dialog=NativeInputsDialog(p)
        assert dialog.data()==p.native_inputs.to_dict()
        dialog.close()
        job_id=manager.submit_native(p,max_frames=4,wall_seconds=6.)
        data=p.native_inputs.to_dict()
        data['potential_flow']['excitation_real'][0]+=.1
        edited=p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
        window._set_project(edited)
        deadline=time.monotonic()+9.
        while manager.active_jobs() and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert manager.job(job_id).state=='completed',manager.job(job_id)
        assert len(received)==1 and received[0].frame_count()==4
        window._set_result(received[0])
        assert 'STALE' in window._result_status and 'coupled fluid' in window._result_status
        assert window._replay_project.native_inputs.to_dict()==p.native_inputs.to_dict()
        window._apply_frame(3)
        assert window._current_frame==3
        channel=force_channel(received[0].body_ids[0])
        window._channel_combo.setCurrentText(channel)
        assert window._plot.series_times()==received[0].times
        assert window._plot.series_values()==received[0].channels[channel]
    finally:
        manager.cancel_all()
        cleanup=time.monotonic()+3.
        while manager.active_jobs() and time.monotonic()<cleanup:
            app.processEvents()
            time.sleep(.01)
        window._dirty=False
        window.close()


@pytest.mark.dto
def test_project_transport_refuses_ghost_states_and_configured_real_hydro_sidecars():
    service=ApplicationService()
    snapshot=service.native_input_snapshot(project(),max_frames=4,wall_seconds=6.)
    ghost=copy.deepcopy(snapshot)
    del ghost['project']['native_inputs']['potential_flow']
    ghost['project']['native_inputs']['initial_states']['ghost'] = copy.deepcopy(
        next(iter(ghost['project']['native_inputs']['initial_states'].values())))
    with pytest.raises(ValueError,match='initial_states must cover exactly'):
        project_from_snapshot(ghost)
    sidecar=copy.deepcopy(snapshot)
    sidecar['project']['bodies'][0]['hydro_sidecar']='unadmitted.nc'
    with pytest.raises(ValueError,match='configured hydrodynamics'):
        project_from_snapshot(sidecar)


@pytest.mark.compile
def test_coincident_geometry_cannot_hide_permuted_coupled_body_order():
    from anysolver.marine_hydro_coordinates import EquilibriumHydroFrame
    from anysolver.marine_potential_flow import equilibrium_geometry_hash
    p=project()
    reference=p.bodies[0].reference_position
    data=p.native_inputs.to_dict()
    for state in data['initial_states'].values():
        state['position']=list(reference)
    for f in data['potential_flow']['frames']:
        f['equilibrium_position']=list(reference)
    data['potential_flow']['frames'].reverse()
    frames=tuple(EquilibriumHydroFrame(**f) for f in data['potential_flow']['frames'])
    data['potential_flow']['equilibrium_hash']=equilibrium_geometry_hash(frames)
    p=p.with_edits(bodies=(p.bodies[0],p.bodies[1].with_edits(reference_position=reference)),
                   native_inputs=NativeMechanicalInputs.from_dict(data))
    with pytest.raises(ValueError,match='body order'):
        ApplicationService().prepare_native_run(p)


@pytest.mark.failure
@pytest.mark.parametrize('failure',('missing-partial','history-validation'))
def test_public_solver_or_accepted_memory_validation_failure_keeps_unsealed_evidence(tmp_path,capsys,monkeypatch,failure):
    from anymarine.worker import run
    import anysolver.marine_mechanical as solver
    from anysolver.marine_potential_flow import PotentialFlowDevelopmentContribution
    p=project()
    service=ApplicationService()
    snapshot=service.native_input_snapshot(p,max_frames=4,wall_seconds=6.)
    inp,out=tmp_path/'failure-input.json',tmp_path/'failure-output.json'
    service.write_job_input(snapshot,inp)
    if failure=='missing-partial':
        def refused(*args,**kwargs):
            raise solver.MarineMechanicalSolveError('injected failed solver without partial record',
                reason='nonfinite',partial=None,logs=(),histories={})
        monkeypatch.setattr(solver,'solve_marine_mechanical',refused)
    else:
        def refused(*args,**kwargs):
            raise ValueError('injected accepted memory mismatch')
        monkeypatch.setattr(PotentialFlowDevelopmentContribution,'validate_accepted_history',refused)
    assert run(inp,out,None)==4 and not out.exists()
    stdout=capsys.readouterr().out
    assert 'OUTPUT_SHA256' not in stdout
    partial=json.loads((tmp_path/'failure-output.partial.json').read_text())
    assert partial['status']=='failed'
    assert partial['reason']=='injected failed solver without partial record' if failure=='missing-partial' else 'accepted memory mismatch' in partial['reason']
