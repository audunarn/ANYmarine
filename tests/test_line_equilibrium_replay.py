"""Bodyless stored-result admission, persistence, stale provenance and refusal."""
import copy,json
from pathlib import Path
import pytest
from anymarine.domain import ResultRecord
from anymarine.services import ApplicationService
from anymarine.line_equilibrium import LineEquilibriumInputs,project_from_snapshot,final_audit
from anymarine.mooring_inputs import NativeMooringInputs

FIXTURE=Path(__file__).parent/'fixtures/line-equilibrium'


def imported(name='touchdown-64'):
    root=FIXTURE/name
    return ApplicationService().import_line_equilibrium_evidence((root/'input.json').read_bytes(),
        (root/'accepted.json').read_bytes(),(FIXTURE/'identity.json').read_bytes(),case_id=name)


@pytest.mark.parametrize('name',['suspended-64','touchdown-64'])
def test_exact_bodyless_stages_save_reopen_and_stale_original_snapshot(name,tmp_path):
    service=ApplicationService();p=imported(name);r=p.results[0]
    source=json.loads((FIXTURE/name/'accepted.json').read_bytes())
    assert not p.bodies and not r.body_ids and not r.positions and r.rotation_matrices=={}
    assert p.line_equilibrium_inputs.initial_nodes is None and p.native_inputs is None
    assert r.line_nodes==tuple(tuple(tuple(row) for row in s['nodes']['positions']) for s in source['stages'])
    assert r.times==tuple(float(i) for i in range(len(source['stages'])))
    assert final_audit(r)==source['audit'] and 'contact' not in ' '.join(r.channels)
    assert not service.result_stale(p,r)
    service.save(p,tmp_path/'project');loaded=service.load(tmp_path/'project')
    assert loaded.to_dict()==p.to_dict()
    assert json.loads((tmp_path/'project/project.json').read_text())['schema_version']==6
    d=p.line_equilibrium_inputs.to_dict();d['model']['lines'][0]['endpoints'][0]['position'][0]+=1.
    edited=p.with_edits(line_equilibrium_inputs=LineEquilibriumInputs.from_dict(d))
    assert service.result_stale(edited,r)
    assert project_from_snapshot(r.native_snapshot).line_equilibrium_inputs.to_dict()==p.line_equilibrium_inputs.to_dict()
    with pytest.raises(TypeError):r.native_snapshot['source_identity']['case_id']='changed'
    with pytest.raises(TypeError):p.line_equilibrium_inputs.controls['force_scale']=1.


@pytest.mark.parametrize('failure',['input','candidate','accepted','nodes','channels','time','body','fraction','norm','iteration','velocity','audit'])
def test_original_seal_stage_and_array_mutation_refused(failure):
    r=imported().results[0];d=r.to_dict()
    if failure=='input':d['native_snapshot']['source_input_json']+=' '
    elif failure=='candidate':d['native_snapshot']['source_candidate_json']+=' '
    elif failure=='accepted':d['equilibrium_evidence']['source_accepted_json']+=' '
    elif failure=='nodes':d['line_nodes'][0][0][0]+=.001
    elif failure=='channels':d['channels']['line equilibrium/residual N'][0]+=1.
    elif failure=='time':d['times'][0]=True
    elif failure=='body':d['body_ids']=['invented']
    else:
        import hashlib
        raw=json.loads(d['equilibrium_evidence']['source_accepted_json'])
        if failure=='fraction':raw['stages'][0]['fraction']=.5
        elif failure=='norm':raw['stages'][0]['normalized_residual']=.1
        elif failure=='iteration':raw['stages'][0]['iterations']=41
        elif failure=='velocity':raw['stages'][0]['nodes']['velocities'][0][0]=1.
        else:raw['audit']['force_limit']=1.
        text=json.dumps(raw);d['equilibrium_evidence']['source_accepted_json']=text
        d['native_snapshot']['source_identity']['accepted_sha256']=hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(ValueError):ResultRecord.from_dict(d)


@pytest.mark.parametrize('failure',['controls','version','body','hydro','missing-source'])
def test_intent_and_snapshot_refusal(failure):
    p=imported();d=p.line_equilibrium_inputs.to_dict()
    if failure=='missing-source':
        snapshot=copy.deepcopy(p.results[0].to_dict()['native_snapshot']);del snapshot['source_input_json']
        with pytest.raises(ValueError):project_from_snapshot(snapshot)
        return
    if failure=='version':d['version']='unknown'
    elif failure=='controls':d['controls']['force_scale']=0.
    elif failure=='body':d['model']['body_ids']=['fake']
    else:d['model']['environment']={}
    with pytest.raises(ValueError):LineEquilibriumInputs.from_dict(d)


def test_legacy_mooring_still_requires_body_identity():
    p=imported();count=p.line_equilibrium_inputs.model['node_count']
    with pytest.raises(ValueError,match='body identities'):
        NativeMooringInputs(p.line_equilibrium_inputs.model,dict(positions=[[0.,0.,0.]]*count,velocities=[[0.,0.,0.]]*count))


@pytest.mark.parametrize('failure',['downgrade','missing-sidecar'])
def test_manifest_refusal_without_silent_intent_loss(failure,tmp_path):
    from anymarine.persistence import ProjectLoadError
    service=ApplicationService();service.save(imported(),tmp_path/'project')
    manifest_path=tmp_path/'project/project.json';manifest=json.loads(manifest_path.read_text())
    if failure=='downgrade':manifest['schema_version']=5;manifest_path.write_text(json.dumps(manifest))
    else:(tmp_path/'project'/manifest['line_equilibrium_inputs']['sidecar']).unlink()
    with pytest.raises(ProjectLoadError):service.load(tmp_path/'project')


@pytest.mark.parametrize('key',['initial_nodes','future_execution_control'])
def test_original_unknown_execution_inputs_cannot_be_silently_discarded(key):
    import hashlib
    snapshot=imported().results[0].to_dict()['native_snapshot']
    raw=json.loads(snapshot['source_input_json']);raw[key]={}
    text=json.dumps(raw);snapshot['source_input_json']=text
    snapshot['source_identity']['input_sha256']=hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(ValueError,match='supported original line solver input'):
        project_from_snapshot(snapshot)


@pytest.mark.parametrize('failure',['bool-channel','bool-fraction','negative-normalized','iteration-count','least-norm-count'])
def test_numeric_channel_and_iteration_evidence_refusals(failure):
    import hashlib
    d=imported().results[0].to_dict()
    if failure=='bool-channel':
        d['channels']['line equilibrium/least norm steps'][0]=False
    elif failure=='bool-fraction':
        d['channels']['line equilibrium/load fraction'][3]=True
    else:
        raw=json.loads(d['equilibrium_evidence']['source_accepted_json'])
        if failure=='negative-normalized':raw['stages'][0]['normalized_residual']=-1e-15
        elif failure=='iteration-count':raw['stages'][0]['iterations']=0;raw['stages'][0]['least_norm_steps']=1
        else:raw['stages'][0]['least_norm_steps']=raw['stages'][0]['iterations']+1
        text=json.dumps(raw);d['equilibrium_evidence']['source_accepted_json']=text
        d['native_snapshot']['source_identity']['accepted_sha256']=hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(ValueError):ResultRecord.from_dict(d)
