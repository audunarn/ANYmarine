import copy,hashlib,json,os
from pathlib import Path
import pytest
from anymarine.native_examples import mooring_two_body_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.services import ApplicationService
from anymarine.domain import ResultRecord
from anymarine.native_jobs import record_from_evidence,decode_output,NativeJobError


def test_inputs_loading_is_immutable_and_lossless(tmp_path):
    p=mooring_two_body_project();data=p.native_inputs.to_dict()
    assert data==NativeMechanicalInputs.from_dict(data).to_dict()
    assert not p.results and p.native_inputs.run_spec['steps']==3
    with pytest.raises(TypeError):p.native_inputs.mooring.initial_nodes['positions'][0][0]=1.
    service=ApplicationService();service.save(p,tmp_path/'project')
    assert service.load(tmp_path/'project').to_dict()==p.to_dict()


@pytest.fixture(scope='module')
def actual(tmp_path_factory):
    from anymarine.worker import run
    configured=os.environ.get('MOORING_APP_ARTIFACTS')
    root=Path(configured) if configured else tmp_path_factory.mktemp('mooring-worker')/'artifacts'
    p=mooring_two_body_project();service=ApplicationService()
    snapshot=service.native_input_snapshot(p,max_frames=4,wall_seconds=10.)
    input_path=root/'input.json';output_path=root/'output.json'
    if os.environ.get('MOORING_APP_REUSE')=='1':
        assert json.loads(input_path.read_text())==snapshot
    else:
        root.mkdir(exist_ok=False)
        input_path.write_text(json.dumps(snapshot),encoding='utf-8')
        assert run(input_path,output_path,None)==0
    raw=output_path.read_bytes();payload=json.loads(raw)
    record=decode_output(raw,snapshot,snapshot['input_hash'],hashlib.sha256(raw).hexdigest())
    return p,snapshot,payload,record


def test_real_worker_saved_nodes_and_reopening_preserve_producing_geometry(actual,tmp_path):
    p,snapshot,payload,record=actual
    assert len(record.line_nodes)==4 and len(record.line_nodes[0])==3
    assert record.line_nodes[-1]!=record.line_nodes[0]
    assert ResultRecord.from_dict(record.to_dict()).to_dict()==record.to_dict()
    assert 'mooring/elastic_energy' in record.channels
    with pytest.raises(TypeError):record.line_nodes[1][0][0]=4.
    service=ApplicationService()
    stored=p.with_edits(native_inputs=None,results=(record,))
    service.save(stored,tmp_path/'saved')
    reopened=service.load(tmp_path/'saved')
    assert reopened.results[0].to_dict()==record.to_dict()
    assert reopened.results[0].to_dict()['native_snapshot']['project']['native_inputs']['mooring']==p.native_inputs.mooring.to_dict()


@pytest.mark.parametrize('failure',['count','initial','position','velocity','missing-nodes','missing-component','residual','length','steps','nonfinite','boolean'])
def test_bad_node_transport_refused(actual,failure):
    p,snapshot,payload,record=actual
    evidence=copy.deepcopy(payload['evidence']);f=evidence['accepted'][1]
    if failure=='count':evidence['node_count']+=1
    elif failure=='initial':evidence['accepted'][0]['nodes']['positions'][0][0]+=.1
    elif failure=='position':f['nodes']['positions'][0][0]+=.1
    elif failure=='velocity':f['nodes']['velocities'][0][0]+=.1
    elif failure=='missing-nodes':del f['nodes']
    elif failure=='missing-component':del f['mooring_diagnostics']['node_force_components']['inertia']
    elif failure=='residual':f['mooring_diagnostics']['node_force_components']['inertia'][0][0]+=1.
    elif failure=='length':f['mooring_diagnostics']['lengths'].pop()
    elif failure=='steps':f['mooring_diagnostics']['body_derivative_steps'][record.body_ids[0]][0]=.1
    elif failure=='nonfinite':f['nodes']['positions'][0][0]=float('nan')
    else:f['nodes']['positions'][0][0]=True
    with pytest.raises((ValueError,NativeJobError)):
        record_from_evidence(evidence,snapshot,result_id='refusal',note='',created_utc='')


def test_stored_nodes_cannot_be_erased_or_changed_without_authoritative_agreement(actual):
    p,snapshot,payload,record=actual
    encoded=record.to_dict();del encoded['line_nodes']
    with pytest.raises(ValueError):ResultRecord.from_dict(encoded)
    altered=copy.deepcopy(payload);altered['result']['line_nodes'][1][0][0]+=.1
    raw=json.dumps(altered).encode()
    with pytest.raises(NativeJobError):decode_output(raw,snapshot,snapshot['input_hash'],hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('failure',['position','velocity','missing-channel','initial-velocity'])
def test_reopened_record_checks_node_kinematics(actual,failure):
    encoded=actual[3].to_dict()
    if failure=='position':encoded['line_nodes'][1][0][0]+=.1
    elif failure=='velocity':encoded['channels']['mooring/node0/velocities x'][1]+=.1
    elif failure=='initial-velocity':encoded['channels']['mooring/node0/velocities x'][0]+=.1
    else:del encoded['channels']['mooring/node0/accelerations x']
    with pytest.raises(ValueError):ResultRecord.from_dict(encoded)


@pytest.mark.parametrize('failure',['none','unknown','boolean','duplicate-node','duplicate-line','wrong-body','nonfinite'])
def test_input_schema_refuses_malformed_intent(failure):
    data=mooring_two_body_project().native_inputs.to_dict();m=data['mooring']
    if failure=='none':data['mooring']=None
    elif failure=='unknown':m['data']['lines'][0]['endpoints'][0]['type']='code'
    elif failure=='boolean':m['initial_nodes']['positions'][0][0]=True
    elif failure=='duplicate-node':m['data']['lines'][1]['endpoints'][1]['node_index']=0
    elif failure=='duplicate-line':m['data']['lines'][1]['line_id']=m['data']['lines'][0]['line_id']
    elif failure=='wrong-body':m['data']['body_ids'][0]='other'
    else:m['data']['fluid_density']=float('nan')
    with pytest.raises(ValueError):NativeMechanicalInputs.from_dict(data)


def test_application_refuses_reserved_mooring_load_identity():
    data=mooring_two_body_project().native_inputs.to_dict()
    data['world_wrenches']=[dict(contribution_id='mooring:elastic_force',body_id=next(iter(data['initial_states'])),
                               point_body=[0.,0.,0.],force_world=[1.,0.,0.],moment_world=[0.,0.,0.])]
    with pytest.raises(ValueError,match='reserved'):NativeMechanicalInputs.from_dict(data)
