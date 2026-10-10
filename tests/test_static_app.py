from pathlib import Path
import copy,hashlib,json
import pytest
from anymarine.native_examples import equilibrium_project
from anymarine.equilibrium_jobs import input_snapshot,decode_output,project_from_snapshot
from anymarine.domain import ResultRecord
from anymarine.services import ApplicationService,ServiceError
from anymarine.persistence import ProjectStore,ProjectLoadError
def save_project(p,path):ProjectStore(path).save(p)
def load_project(path):return ProjectStore(path).load()

@pytest.fixture
def actual():
    root=Path(__file__).parent/'fixtures/static-equilibrium'
    snap=json.loads((root/'input.json').read_text(encoding='utf-8'));raw=(root/'output.json').read_bytes()
    return snap,raw,decode_output(raw,snap,snap['input_hash'],hashlib.sha256(raw).hexdigest())

def test_roundtrip_stale_project_and_detached_static_result(actual,tmp_path):
    snap,raw,r=actual;p=project_from_snapshot(snap);svc=ApplicationService()
    assert not svc.result_stale(p,r)
    assert r.frame_count()==9 and r.kind=='equilibrium_development'
    assert r.equilibrium_evidence['audit']['potential_geometry'][0]['coefficient_rebind_required']
    p=p.with_edits(results=(r,));save_project(p,tmp_path/'saved');reopened=load_project(tmp_path/'saved')
    assert reopened.results[0].to_dict()==r.to_dict()
    changed=p.with_edits(native_inputs=None)
    assert svc.result_stale(changed,r)
    save_project(changed,tmp_path/'detached');assert load_project(tmp_path/'detached').results[0].to_dict()==r.to_dict()
    with pytest.raises(TypeError):r.equilibrium_evidence['audit']['force_limit']=9.
    with pytest.raises(TypeError):r.line_nodes[0][0][0]=9.

@pytest.mark.parametrize('mutation',['kind','hash','spec','limits'])
def test_changed_snapshot_controls_and_execution_identity_refused(actual,mutation):
    snap,_,_=actual;s=copy.deepcopy(snap)
    if mutation=='kind':s['kind']='mechanical_development'
    if mutation=='hash':s['project_input_hash']='0'*64
    if mutation=='spec':s['static_spec']['force_scale']+=1.
    if mutation=='limits':s['execution_limits']['wall_seconds']+=1.
    with pytest.raises(ValueError):project_from_snapshot(s)

@pytest.mark.parametrize('mutation',['stage','initial','support','rebind','residual','channels','timeline','matrix'])
def test_structurally_invalid_static_records_refused(actual,mutation):
    _,_,r=actual;d=r.to_dict()
    if mutation=='stage':d['equilibrium_evidence']['frames'][1]['kind']='coupled'
    if mutation=='initial':d['positions'][r.body_ids[0]][0][0]+=1.
    if mutation=='support':d['equilibrium_evidence']['audit']['temporary_support_fraction']=.1
    if mutation=='rebind':d['equilibrium_evidence']['audit']['potential_geometry'][0]['coefficient_rebind_required']=False
    if mutation=='residual':d['equilibrium_evidence']['audit']['body_world_residual'][0][0]=1.
    if mutation=='channels':del d['channels']['equilibrium/force_residual N']
    if mutation=='timeline':d['times'][1]=.1
    if mutation=='matrix':d['rotation_matrices'][r.body_ids[0]][0][0][0]=2.
    with pytest.raises(ValueError):ResultRecord.from_dict(d)

def test_changed_native_output_bytes_require_original_producer_seal(actual):
    snap,raw,r=actual;d=json.loads(raw);d['result']['note']='changed'
    altered=json.dumps(d).encode()
    with pytest.raises(ValueError):decode_output(altered,snap,snap['input_hash'],hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):decode_output(raw,snap,snap['input_hash'],None)

def test_complete_run_hash_covers_static_controls(actual):
    snap,_,_=actual;p=project_from_snapshot(snap);s=copy.deepcopy(snap['static_spec']);s['moment_scale']*=2.
    other=input_snapshot(p,static_spec=s)
    assert other['project_input_hash']==snap['project_input_hash'] and other['input_hash']!=snap['input_hash']

def test_service_attaches_static_result_without_mutating_producing_project(actual):
    snap,_,r=actual;p=project_from_snapshot(snap);svc=ApplicationService()
    updated=svc.add_result(p,r)
    assert p.results==() and updated.results==(r,)
    with pytest.raises(ServiceError):svc.add_result(updated,r)

@pytest.mark.parametrize('mutation',['erase','shape','sum','line_position','line_matrix'])
def test_static_stage_and_component_transport_refusals(actual,mutation):
    _,_,r=actual;d=r.to_dict();components=d['equilibrium_evidence']['audit']['components']
    if mutation=='erase':del components['potential-flow-development']
    if mutation=='shape':components['potential-flow-development'].pop()
    if mutation=='sum':components['potential-flow-development'][0]+=100.
    if mutation=='line_position':d['positions'][r.body_ids[0]][1][0]+=.01
    if mutation=='line_matrix':
        d['rotation_matrices'][r.body_ids[0]][1]=[[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]]
    with pytest.raises(ValueError):ResultRecord.from_dict(d)

@pytest.mark.parametrize('cancelled',[False,True])
def test_incomplete_static_preserves_accepted_line_and_coupled_evidence(actual,tmp_path,monkeypatch,cancelled):
    from types import SimpleNamespace as S
    from anymarine.equilibrium_jobs import run_job
    solver=pytest.importorskip('anysolver.marine_coupled_equilibrium')
    snap,_,_=actual;output=tmp_path/'result.json';cancel=tmp_path/'cancel'
    def stopped(model,states,nodes,spec,**options):
        if cancelled:cancel.touch()
        line_stage=S(name='weight',fraction=.25,nodes=nodes,residual_norm=.1,normalized_residual=.001,iterations=2,least_norm_steps=1)
        line=S(status='converged',message='accepted line checkpoint',audit={'residual_norm':.1},stages=(line_stage,))
        stage=S(fraction=.25,states=states,nodes=nodes,metrics={'force_residual':.2},iterations=3,least_norm_steps=0)
        return S(status='cancelled' if cancelled else 'failed',message='injected failure',line_result=line,stages=(stage,))
    monkeypatch.setattr(solver,'solve_coupled_marine_equilibrium',stopped)
    assert run_job(snap,output,cancel)==(3 if cancelled else 4)
    assert not output.exists()
    data=json.loads(output.with_suffix('.incomplete.json').read_text(encoding='utf-8'))
    assert data['status']==('cancelled' if cancelled else 'failed')
    assert data['line_result']['accepted'][0]['kind']=='weight'
    assert len(data['line_result']['accepted'][0]['nodes'])==3
    assert data['accepted'][0]['metrics']=={'force_residual':.2}

def test_cli_static_run_uses_shared_services_and_saved_snapshot(actual,tmp_path,capsys):
    from anymarine.cli import main
    snap,_,_=actual;p=project_from_snapshot(snap);save_project(p,tmp_path/'project')
    assert main(['run-equilibrium','--project',str(tmp_path/'project'),'--wall-seconds','10'])==0
    reopened=load_project(tmp_path/'project');assert len(reopened.results)==1
    r=reopened.results[0]
    assert r.kind=='equilibrium_development' and r.frame_count()==9
    assert not ApplicationService().result_stale(reopened,r)
    assert 'unqualified' in capsys.readouterr().out

def test_missing_static_sidecar_is_refused(actual,tmp_path):
    snap,_,r=actual;p=project_from_snapshot(snap).with_edits(results=(r,))
    target=tmp_path/'project';save_project(p,target)
    sidecars=list((target/'results').glob('*.json'));assert sidecars
    sidecars[0].unlink()
    with pytest.raises(ProjectLoadError):load_project(target)
