"""Regression coverage for coupled linear job admission and persistence."""
import json
import numpy as np
import pytest
from anymarine.domain import default_two_body_project
from anymarine.services import ApplicationService, ServiceError
from anymarine.persistence import ProjectLoadError

@pytest.fixture(scope='module')
def case():
    service = ApplicationService()
    p = default_two_body_project()
    inputs = service.linear_demo_inputs(p)
    p = service.set_linear_inputs(p, inputs)
    return service,p,service.run_linear_local(p)

@pytest.mark.parametrize('damage',['wrong_kind','wrong_body','truncated','missing_channel','wrong_replay'])
def test_result_refused_against_actual_job(case,tmp_path,damage):
    service,p,result = case
    data = result.to_dict()
    if damage=='wrong_kind': data['kind']='synthetic_unqualified'
    elif damage=='wrong_body': data['body_ids'].reverse()
    elif damage=='truncated':
        data['times']=data['times'][:1]
        for mapping in ('positions','orientations','channels'):
            data[mapping]={k:v[:1] for k,v in data[mapping].items()}
    elif damage=='missing_channel': data['channels'].pop(next(iter(data['channels'])))
    else: data['positions'][p.bodies[0].body_id][0][0]+=1
    path=tmp_path/'output.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ServiceError):
        service.load_job_output(path,result.input_hash,snapshot=service.linear_job_snapshot(p))

def test_linear_schema_and_future_refusal(case,tmp_path):
    service,p,_ = case
    service.save(p,tmp_path)
    path=tmp_path/'project.json'
    data=json.loads(path.read_text())
    assert data['schema_version']==3
    for version in (2,999):
        data['schema_version']=version
        path.write_text(json.dumps(data),encoding='utf-8')
        with pytest.raises(ProjectLoadError): service.load(tmp_path)

def test_coupled_demo_exercises_link(case):
    _,p,result = case
    inputs=p.linear_inputs
    for matrix in (inputs.added_mass_infinite,inputs.radiation_kernel[0]):
        a=np.asarray(matrix)
        assert np.any(np.abs(a[:2,2:])>0)
        assert np.linalg.eigvalsh(a).min()>=-1e-8
    relative=np.asarray(result.channels['q:'+inputs.labels[0]])-np.asarray(result.channels['q:'+inputs.labels[2]])
    assert np.max(np.abs(relative))>1e-8
