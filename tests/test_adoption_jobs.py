"""Frozen adoption transport; sealed trajectory reused, no new integrations."""
from pathlib import Path
import hashlib,json,copy
import pytest
from anymarine.services import ApplicationService
from anymarine.domain import ResultRecord
from anymarine.hashing import input_hash
from anymarine.adoption_jobs import (input_snapshot,snapshot_records,load_output,
    regeneration_from_data,current_submission,_project)
from anymarine.equilibrium_transition import AdoptionDomain,EquilibriumAdoptionError

def records():
    from anymarine.equilibrium_jobs import project_from_snapshot,decode_output
    root=Path(__file__).parent/'fixtures/equilibrium-transition'
    provenance=json.loads((root/'provenance.json').read_text())
    for n,h in provenance['sha256'].items():assert hashlib.sha256((root/n).read_bytes()).hexdigest()==h
    snapshot=json.loads((root/'input.json').read_text());raw=(root/'output.json').read_bytes()
    p=project_from_snapshot(snapshot);r=decode_output(raw,snapshot,snapshot['input_hash'],provenance['sha256']['output.json'])
    p=p.with_edits(results=(r,))
    dynamic=ResultRecord.from_dict(json.loads((root/'dynamic-output.json').read_text())['result'])
    q=_project(dynamic.native_snapshot['project']).with_edits(results=(r,))
    regenerated=regeneration_from_data(dict(potential_flow=q.native_inputs.potential_flow.to_dict(),lids={}))
    return p,r,q,regenerated

def job():
    p,r,q,g=records()
    return p,r,q,g,input_snapshot(p,r,domain=AdoptionDomain(10.),regeneration=g,wall_seconds=8.)

def output(tmp_path,q,snapshot,**changes):
    d=dict(schema_version=1,kind=snapshot['kind'],input_hash=snapshot['input_hash'],project=q.to_dict(),project_hash=input_hash(q))
    d.update(changes);raw=json.dumps(d,allow_nan=False).encode();path=tmp_path/'output.json';path.write_bytes(raw)
    return path,hashlib.sha256(raw).hexdigest()

def test_input_snapshot_roundtrip_immutable_authority():
    p,r,q,g,s=job();rebuilt,result,domain,regenerated=snapshot_records(s)
    assert rebuilt.to_dict()==p.to_dict() and result.to_dict()==r.to_dict()
    assert regenerated.potential_flow.to_dict()==g.potential_flow.to_dict()
    assert current_submission(p,s)
    s['project']['settings']['dt']=.2
    assert p.settings.dt!=.2
    with pytest.raises(ValueError,match='hash mismatch'):snapshot_records(s)

def test_sealed_output_and_exact_allowed_mutations(tmp_path,monkeypatch):
    p,r,q,g,s=job();path,seal=output(tmp_path,q,s)
    # Output transport must not call the numerical adoption service on the GUI thread.
    monkeypatch.setattr(ApplicationService,'adopt_equilibrium',lambda *a,**k:pytest.fail('numerical GUI transport'))
    assert load_output(path,s,seal).to_dict()==q.to_dict()
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='unsealed or changed'):load_output(path,s,seal)

@pytest.mark.parametrize('mutation',['name','color','mass','settings','result','constitutive','domain','initial'])
def test_resealed_output_cannot_change_protected_intent(tmp_path,mutation):
    p,r,q,g,s=job();d=q.to_dict()
    if mutation=='name':d['name']='replacement'
    elif mutation=='color':d['bodies'][0]['color']='#ff0000'
    elif mutation=='mass':d['bodies'][0]['mass']*=2
    elif mutation=='settings':d['settings']['dt']*=2
    elif mutation=='result':d['results']=[]
    elif mutation=='constitutive':d['native_inputs']['links'][0]['preload'][0]=.1
    elif mutation=='domain':d['equilibrium']['waterline_z']=.25
    else:d['native_inputs']['initial_states'][p.bodies[0].body_id]['velocity_world'][0]=.1
    altered=_project(d)
    path,seal=output(tmp_path,altered,s)
    with pytest.raises(ValueError):load_output(path,s,seal)

def test_current_commit_guard_protects_display_edits_and_new_results():
    p,r,q,g,s=job()
    assert not current_submission(p.with_edits(name='renamed'),s)
    b=p.bodies[0].with_edits(color='#ff0000');changed=p.with_edits(bodies=(b,p.bodies[1]))
    assert input_hash(changed)==input_hash(p) and not current_submission(changed,s)
    assert not current_submission(p.with_edits(results=()),s)

def test_old_dependencies_missing_regeneration_and_stale_result_refused():
    p,r,q,g=records()
    with pytest.raises(ValueError,match='regenerated potential-flow'):input_snapshot(p,r,domain=AdoptionDomain(10.))
    changed=p.with_edits(settings=p.settings.with_edits(dt=.2))
    with pytest.raises(ValueError,match='current static'):input_snapshot(changed,r,domain=AdoptionDomain(10.),regeneration=g)

@pytest.mark.parametrize('limits',[True,0.,601.,float('nan')])
def test_invalid_job_limits_refused(limits):
    p,r,q,g=records()
    with pytest.raises(ValueError):input_snapshot(p,r,domain=AdoptionDomain(10.),regeneration=g,wall_seconds=limits)

@pytest.mark.parametrize('group',['force','moment'])
def test_resealed_audit_cannot_relax_static_tolerances(tmp_path,group):
    p,r,q,g,s=job();d=q.to_dict();d['equilibrium_adoption']['audit'][group+'_limit']*=2
    altered=_project(d);path,seal=output(tmp_path,altered,s)
    with pytest.raises(ValueError,match='original static tolerance'):load_output(path,s,seal)

@pytest.mark.parametrize('mutation',['unknown','bool_position','bool_triangle','null_positions','null_triangles'])
def test_lid_artifact_does_not_discard_semantic_intent(mutation):
    lid=dict(positions=[[0.,0.,-.1],[1.,0.,-.1],[0.,1.,-.1]],triangles=[[0,1,2]])
    if mutation=='unknown':lid['ownership']='physical_hull'
    elif mutation=='bool_position':lid['positions'][0][0]=True
    elif mutation=='bool_triangle':lid['triangles'][0][0]=False
    elif mutation=='null_positions':lid['positions']=None
    else:lid['triangles']=None
    with pytest.raises(ValueError):regeneration_from_data(dict(potential_flow=None,lids={'body':lid}))

@pytest.mark.parametrize('mode',['cancel','deadline'])
def test_late_cancel_or_deadline_during_validation_never_publishes(tmp_path,monkeypatch,capsys,mode):
    import anymarine.adoption_jobs as jobs
    from types import SimpleNamespace
    p,r,q,g,s=job();cancel=tmp_path/'cancel';clock=[0.]
    monkeypatch.setattr(jobs,'time',SimpleNamespace(perf_counter=lambda:clock[0]))
    monkeypatch.setattr(ApplicationService,'adopt_equilibrium',lambda *a,**k:q)
    load=jobs.load_output
    def finish_validation(*args):
        validated=load(*args)
        if mode=='cancel':cancel.touch()
        else:clock[0]=100.
        return validated
    monkeypatch.setattr(jobs,'load_output',finish_validation)
    path=tmp_path/'candidate.json';code=jobs.run_job(s,path,cancel)
    assert code==(3 if mode=='cancel' else 4)
    assert 'OUTPUT_SHA256' not in capsys.readouterr().out
    failure=json.loads(path.with_suffix('.failure.json').read_text())
    assert failure['status']==('cancelled' if mode=='cancel' else 'failed') and path.exists()
