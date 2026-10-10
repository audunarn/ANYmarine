import copy,hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
import pytest
from anymarine.contact_inputs import contact_lumps,required_contact_channels
from anymarine.domain import ResultRecord
from anymarine.native_examples import mooring_contact_project,mooring_hydrodynamic_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.services import ApplicationService
from anymarine.native_jobs import decode_output,record_from_evidence
from anymarine.mechanics import MechanicsUnavailableError


def test_contact_intent_immutable_public_compile_save_reopen(tmp_path):
    p=mooring_contact_project();data=p.native_inputs.to_dict();service=ApplicationService()
    assert p.native_inputs.mooring.has_contact and not p.native_inputs.mooring.has_hydrodynamics
    assert len(contact_lumps(p.native_inputs.mooring))==12 and not p.results
    assert NativeMechanicalInputs.from_dict(copy.deepcopy(data)).to_dict()==data
    with pytest.raises(TypeError):p.native_inputs.mooring.data['seabed_contact']['elevation']=0.
    model,states,spec=service.prepare_native_run(p)
    assert model.mooring.seabed_contact.elevation==-4. and spec.steps==3
    service.save(p,tmp_path/'project');assert service.load(tmp_path/'project').native_inputs.to_dict()==data


def combined_project():
    p=mooring_contact_project();d=p.native_inputs.to_dict()
    hydro=mooring_hydrodynamic_project().native_inputs.mooring.to_dict()['data']
    env=copy.deepcopy(hydro['environment']);old_depth=env['water_depth'];env['water_depth']=4.
    env['current']['depths']=[depth*4./old_depth for depth in env['current']['depths']]
    d['mooring']['data']['environment']=env
    section=copy.deepcopy(hydro['lines'][0]['hydrodynamics'][0]);section['diameter']=.125
    for line in d['mooring']['data']['lines']:line['hydrodynamics']=[copy.deepcopy(section) for _ in line['rest_lengths']]
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(d))


def test_combined_contact_hydro_preserves_inner_intent_and_installed_compiler(tmp_path):
    p=combined_project();assert p.native_inputs.mooring.has_hydrodynamics and p.native_inputs.mooring.has_contact
    service=ApplicationService();model,_,_=service.prepare_native_run(p)
    assert model.mooring.environment.water_depth==4. and model.mooring.seabed_contact.elevation==-4.
    service.save(p,tmp_path/'combined');assert service.load(tmp_path/'combined').native_inputs.to_dict()==p.native_inputs.to_dict()


@pytest.mark.parametrize('failure',['version','missing-bed','bed-api','unknown','stiffness','damping',
                                   'elevation','count','bool','missing-diameters','hydro-diameter','depth'])
def test_malformed_contact_intent_refused(failure):
    p=combined_project() if failure in ('hydro-diameter','depth') else mooring_contact_project()
    data=p.native_inputs.to_dict();m=data['mooring']['data']
    if failure=='version':m['api_version']='marine_mooring_v1'
    elif failure=='missing-bed':del m['seabed_contact']
    elif failure=='bed-api':m['seabed_contact']['api_version']='unknown'
    elif failure=='unknown':m['seabed_contact']['friction']=.2
    elif failure=='stiffness':m['seabed_contact']['normal_stiffness']=0.
    elif failure=='damping':m['seabed_contact']['normal_damping']=-1.
    elif failure=='elevation':m['seabed_contact']['elevation']=0.
    elif failure=='count':m['lines'][0]['contact_diameters']=[.125]
    elif failure=='bool':m['lines'][0]['contact_diameters'][0]=True
    elif failure=='missing-diameters':
        for line in m['lines']:del line['contact_diameters']
    elif failure=='hydro-diameter':m['lines'][0]['contact_diameters'][0]=.2
    else:m['seabed_contact']['elevation']=-3.
    with pytest.raises(ValueError):NativeMechanicalInputs.from_dict(data)


def test_missing_contact_backend_feature_fails_before_job(monkeypatch):
    import anymarine.mechanics as module
    original=module.importlib.import_module
    def load(name):return SimpleNamespace(API_VERSION='old') if name=='anysolver.marine_seabed' else original(name)
    monkeypatch.setattr(module.importlib,'import_module',load)
    with pytest.raises(MechanicsUnavailableError,match='contact APIs'):
        ApplicationService().prepare_native_run(mooring_contact_project())


@pytest.fixture(scope='module')
def actual_contact(tmp_path_factory):
    from anymarine.worker import run
    configured=os.environ.get('CONTACT_APP_ARTIFACTS')
    root=Path(configured) if configured else tmp_path_factory.mktemp('contact')/'worker'
    p=mooring_contact_project();snapshot=ApplicationService().native_input_snapshot(p,max_frames=4,wall_seconds=12.)
    inp,out=root/'input.json',root/'output.json'
    if os.environ.get('CONTACT_APP_REUSE')=='1':assert json.loads(inp.read_text(encoding='utf-8'))==snapshot
    else:
        root.mkdir(exist_ok=False);inp.write_text(json.dumps(snapshot),encoding='utf-8');assert run(inp,out,None)==0
    raw=out.read_bytes();payload=json.loads(raw)
    result=decode_output(raw,snapshot,snapshot['input_hash'],hashlib.sha256(raw).hexdigest())
    return p,snapshot,payload,result,raw


def test_actual_contact_worker_channels_authoritative_geometry_and_reopen(actual_contact,tmp_path):
    p,snapshot,payload,result=actual_contact[:4]
    assert result.frame_count()==4 and result.line_nodes[-1]!=result.line_nodes[0]
    assert all(name in result.channels for name in required_contact_channels(p.native_inputs.mooring))
    assert result.channels['mooring/contact/max_penetration'][0]==.03125
    assert any(v>0. for v in result.channels['mooring/contact/dissipation'])
    assert result.channels['mooring/contact/potential_energy'][0]>result.channels['mooring/contact/potential_energy'][-1]
    assert result.channels['mooring/contact/lump0/active']==(0.,)*4
    assert result.channels['mooring/contact/lump1/active']==(1.,)*4
    assert ResultRecord.from_dict(result.to_dict()).to_dict()==result.to_dict()
    service=ApplicationService();saved=p.with_edits(native_inputs=None,results=(result,))
    service.save(saved,tmp_path/'result');loaded=service.load(tmp_path/'result')
    assert service.result_stale(loaded,result) and loaded.results[0].to_dict()==result.to_dict()


@pytest.mark.parametrize('failure',['identity','gap','flag','maximum','energy','dissipation','reaction','body','node'])
def test_actual_contact_evidence_refusal(actual_contact,failure):
    p,snapshot,payload,result=actual_contact[:4];evidence=copy.deepcopy(payload['evidence']);f=evidence['accepted'][1]
    contact=f['mooring_diagnostics']['contact']
    if failure=='identity':contact['endpoint_indices'][0]=1
    elif failure=='gap':contact['gaps'][1]+=.001
    elif failure=='flag':contact['active'][1]=1
    elif failure=='maximum':contact['max_penetration']+=.01
    elif failure=='energy':contact['potential_energy']=-1.
    elif failure=='dissipation':contact['dissipation']=-1.
    elif failure=='reaction':contact['seabed_reaction'][0]=1.
    elif failure=='body':del f['force_decomposition']['mooring:contact_elastic']
    else:del f['mooring_diagnostics']['node_force_components']['contact_damping']
    with pytest.raises(ValueError):record_from_evidence(evidence,snapshot,result_id='refuse',note='',created_utc='')


@pytest.mark.parametrize('name',['mooring/contact/max_penetration','mooring/contact/potential_energy',
    'mooring/contact/dissipation','mooring/contact/seabed_reaction z','mooring/contact/lump0/active',
    'mooring/contact/lump1/gap','mooring/node0/contact_elastic z',
    'wrench["mooring:contact_damping","native-demo-body-b"]/Mx'])
def test_stored_contact_channel_erasure_refused(actual_contact,name):
    data=actual_contact[3].to_dict();del data['channels'][name]
    with pytest.raises(ValueError,match='contact channel inventory'):ResultRecord.from_dict(data)


@pytest.mark.parametrize('failure',['gap','flag','inactive-gap','maximum','energy','reaction'])
def test_stored_contact_channel_mutation_refused(actual_contact,failure):
    data=actual_contact[3].to_dict();channels=data['channels']
    if failure=='gap':channels['mooring/contact/lump1/gap'][1]+=.001
    elif failure=='flag':channels['mooring/contact/lump1/active'][1]=.5
    elif failure=='inactive-gap':channels['mooring/contact/lump0/active'][1]=1.
    elif failure=='maximum':channels['mooring/contact/max_penetration'][1]+=.01
    elif failure=='energy':channels['mooring/contact/potential_energy'][1]=-1.
    else:channels['mooring/contact/seabed_reaction x'][1]=1.
    with pytest.raises(ValueError):ResultRecord.from_dict(data)


@pytest.mark.parametrize('failure',['active','positive-energy','negative-reaction'])
def test_saved_contact_finite_value_tampering_refused_by_artifact_checksum(actual_contact,tmp_path,failure):
    from anymarine.persistence import ProjectLoadError
    p,_,_,result=actual_contact[:4];service=ApplicationService();root=tmp_path/'saved'
    stored=p.with_edits(results=(result,));service.save(stored,root)
    path=next((root/'results').glob('*.json'));original=path.read_bytes();data=json.loads(original)
    if failure=='active':
        data['channels']['mooring/contact/lump1/active'][1]=0.
        data['channels']['mooring/contact/lump2/active'][1]=0.
    elif failure=='positive-energy':data['channels']['mooring/contact/potential_energy'][1]*=1.1
    else:data['channels']['mooring/contact/seabed_reaction z'][1]*=1.1
    # Parsing checks transport admissibility, not the owner's constitutive law.
    ResultRecord.from_dict(data)
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ProjectLoadError):service.load(root)
    assert stored.results[0].to_dict()==result.to_dict() and path.read_bytes()!=original


@pytest.mark.parametrize('name',['mooring/contact/lump1/active','mooring/contact/potential_energy','mooring/contact/seabed_reaction z'])
def test_contact_output_finite_value_tampering_refused_by_producer_seal(actual_contact,name):
    _,snapshot,payload,_,original=actual_contact;altered=copy.deepcopy(payload)
    altered['result']['channels'][name][1]=0.
    raw=json.dumps(altered).encode()
    # The original identity is independently supplied by the producer's success
    # event; it cannot be renewed by rewriting the output artifact.
    with pytest.raises(ValueError):decode_output(raw,snapshot,snapshot['input_hash'],hashlib.sha256(original).hexdigest())
