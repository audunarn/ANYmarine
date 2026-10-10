import copy,hashlib,json,os
from pathlib import Path
import pytest
from anymarine.native_examples import mooring_hydrodynamic_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.services import ApplicationService
from anymarine.domain import ResultRecord
from anymarine.native_jobs import record_from_evidence,decode_output


def test_lossless_immutable_hydro_intent_and_public_compilation(tmp_path):
    p=mooring_hydrodynamic_project();data=p.native_inputs.to_dict();m=p.native_inputs.mooring
    assert m.has_hydrodynamics and len(m.force_components)==8 and not p.results
    assert NativeMechanicalInputs.from_dict(data).to_dict()==data
    with pytest.raises(TypeError):m.data['lines'][0]['hydrodynamics'][0]['drag_normal']=2.
    model,states,spec=ApplicationService().prepare_native_run(p)
    assert len(model.contributions)==1 and model.mooring.environment==model.contributions[0].environment
    assert spec.steps==3 and len(model.mooring.lines)==3
    service=ApplicationService();service.save(p,tmp_path/'saved')
    assert service.load(tmp_path/'saved').native_inputs.to_dict()==data


@pytest.mark.parametrize('failure',['version','environment','coefficient','boolean','sections','unknown',
                                   'density','datum','phase','current','ownership'])
def test_new_intent_refuses_lossy_invalid_and_conflicting_inputs(failure):
    d=mooring_hydrodynamic_project().native_inputs.to_dict();m=d['mooring']['data']
    if failure=='version':m['api_version']='marine_mooring_v1'
    elif failure=='environment':del m['environment']
    elif failure=='coefficient':m['lines'][0]['hydrodynamics'][0]['added_mass_normal']=-1.
    elif failure=='boolean':m['lines'][0]['hydrodynamics'][0]['diameter']=True
    elif failure=='sections':m['lines'][0]['hydrodynamics'].pop()
    elif failure=='unknown':m['lines'][0]['hydrodynamics'][0]['code']='exec'
    elif failure=='density':m['environment']['density']=1000.
    elif failure=='datum':m['environment']['still_water_level']=1.
    elif failure=='phase':m['environment']['components'][0]['phase']+=.1
    elif failure=='current':m['environment']['current']['velocities'][0][0]+=.1
    else:m['lines'][0]['line_id']=d['morison']['members'][0]['member_id']
    with pytest.raises(ValueError):NativeMechanicalInputs.from_dict(d)


def test_missing_compatible_backend_is_typed_before_job(monkeypatch):
    import anymarine.mechanics as mechanics
    from types import SimpleNamespace
    real=mechanics.importlib.import_module
    def unavailable(name,*a,**kw):
        return SimpleNamespace(API_VERSION='old') if name=='anyloads.marine_line' else real(name,*a,**kw)
    monkeypatch.setattr(mechanics.importlib,'import_module',unavailable)
    with pytest.raises(mechanics.MechanicsUnavailableError,match='incompatible'):
        mechanics.prepare_native_run(mooring_hydrodynamic_project())


def seeded_with_lines():
    p=ApplicationService().load_native_sea_state_example();data=p.native_inputs.to_dict()
    m=mooring_hydrodynamic_project().native_inputs.mooring.to_dict()
    old=m['data']['body_ids'];new=list(data['initial_states']);mapping=dict(zip(old,new))
    m['data']['body_ids']=new;m['data']['environment']=data['morison']['environment']
    for line in m['data']['lines']:
        for ep in line['endpoints']:
            if ep['type']=='rigid':ep['body_id']=mapping[ep['body_id']]
    data['mooring']=m
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


def test_seeded_regeneration_binds_lines_members_and_excitation(tmp_path):
    p=seeded_with_lines();service=ApplicationService()
    updated=service.replace_native_sea_state(p,significant_height=.03,peak_period=8.,seed=18)
    d=updated.native_inputs.to_dict();components=d['wave_realization']['components']
    assert d['mooring']['data']['environment']['components']==components==d['morison']['environment']['components']
    assert d['potential_flow']['excitation_series']['wave_realization_hash']==updated.native_inputs.wave_realization.content_hash
    assert p.native_inputs.wave_realization.content_hash!=updated.native_inputs.wave_realization.content_hash
    service.prepare_native_run(updated)
    service.save(updated,tmp_path/'seeded')
    assert service.load(tmp_path/'seeded').native_inputs.to_dict()==d


@pytest.fixture(scope='module')
def actual_hydro(tmp_path_factory):
    from anymarine.worker import run
    configured=os.environ.get('LINE_HYDRO_APP_ARTIFACTS')
    root=Path(configured) if configured else tmp_path_factory.mktemp('line-hydro')/'worker'
    p=mooring_hydrodynamic_project();service=ApplicationService()
    snapshot=service.native_input_snapshot(p,max_frames=4,wall_seconds=12.)
    inp,out=root/'input.json',root/'output.json'
    if os.environ.get('LINE_HYDRO_APP_REUSE')=='1':assert json.loads(inp.read_text())==snapshot
    else:
        root.mkdir(exist_ok=False);inp.write_text(json.dumps(snapshot));assert run(inp,out,None)==0
    raw=out.read_bytes();payload=json.loads(raw)
    result=decode_output(raw,snapshot,snapshot['input_hash'],hashlib.sha256(raw).hexdigest())
    return p,snapshot,payload,result


def test_real_hydro_worker_stored_arrays_and_reopening(actual_hydro,tmp_path):
    p,snapshot,payload,result=actual_hydro
    assert result.frame_count()==4 and result.line_nodes[-1]!=result.line_nodes[0]
    assert 'mooring/relative_drag_power' in result.channels
    assert all(v>=0. for v in result.channels['mooring/added_mass_kinetic_energy'])
    assert any(v!=0. for v in result.channels['mooring/node0/drag_normal x'])
    assert any(v!=0. for v in result.channels['mooring/node0/fluid_inertia x'])
    assert ResultRecord.from_dict(result.to_dict()).to_dict()==result.to_dict()
    service=ApplicationService();stored=p.with_edits(native_inputs=None,results=(result,))
    service.save(stored,tmp_path/'results');loaded=service.load(tmp_path/'results')
    assert loaded.results[0].to_dict()==result.to_dict() and service.result_stale(loaded,result)


@pytest.mark.parametrize('failure',['body-component','node-component','power','energy','residual','position'])
def test_actual_hydro_evidence_refusal(actual_hydro,failure):
    p,snapshot,payload,result=actual_hydro;evidence=copy.deepcopy(payload['evidence']);f=evidence['accepted'][1]
    if failure=='body-component':del f['force_decomposition']['mooring:fluid_inertia']
    elif failure=='node-component':del f['mooring_diagnostics']['node_force_components']['added_mass']
    elif failure=='power':del f['mooring_diagnostics']['relative_drag_power']
    elif failure=='energy':f['mooring_diagnostics']['added_mass_kinetic_energy']=-1.
    elif failure=='residual':f['mooring_diagnostics']['node_force_components']['drag_normal'][0][0]+=1.
    else:f['nodes']['positions'][0][0]+=.1
    with pytest.raises(ValueError):record_from_evidence(evidence,snapshot,result_id='refuse',note='',created_utc='')


@pytest.mark.parametrize('name',['mooring/relative_drag_power','mooring/added_mass_kinetic_energy','mooring/node0/added_mass x'])
def test_stored_hydro_diagnostics_cannot_be_erased(actual_hydro,name):
    data=actual_hydro[3].to_dict();del data['channels'][name]
    with pytest.raises(ValueError):ResultRecord.from_dict(data)


@pytest.mark.parametrize('component',['drag_normal','drag_tangential','fluid_inertia','added_mass'])
@pytest.mark.parametrize('axis',['Fx','Fy','Fz','Mx','My','Mz'])
def test_stored_body_hydro_diagnostics_cannot_be_erased(actual_hydro,component,axis):
    data=actual_hydro[3].to_dict()
    identity=json.dumps(['mooring:'+component,actual_hydro[0].bodies[0].body_id],separators=(',',':'))
    del data['channels'][f'wrench{identity}/{axis}']
    with pytest.raises(ValueError,match='hydrodynamic channel inventory'):ResultRecord.from_dict(data)
