"""Project round trips and the actual shared mechanical consumer."""
import copy
import numpy as np
import pytest
from anymarine.domain import Body, BodyMassProperties
from anymarine.hashing import input_hash, is_stale
from anymarine.services import ApplicationService
from anymarine.mechanics import MechanicsInputError, MechanicsUnavailableError

def properties():
    return BodyMassProperties((.3,-.2,.7),((4e6,.2e6,.1e6),(.2e6,5e6,-.1e6),(.1e6,-.1e6,6e6)))

def declared_project():
    service=ApplicationService(); project=service.load_example()
    first=project.bodies[0].with_edits(mass_properties=properties())
    return service, project.with_edits(bodies=(first,project.bodies[1]))

def test_legacy_serialization_and_authoritative_example_hash():
    project=ApplicationService().load_example()
    assert project.results and not is_stale(project,project.results[0].input_hash)
    for body in project.bodies:
        data=body.to_dict()
        assert 'mass_properties' not in data
        assert Body.from_dict(data).to_dict()==data
        assert body.with_edits().to_dict()==data

def test_save_reopen_body_identity_and_stale_results(tmp_path):
    service=ApplicationService(); old=service.load_example()
    new=old.with_edits(bodies=(old.bodies[0].with_edits(mass_properties=properties()),old.bodies[1]))
    assert input_hash(new)!=input_hash(old)
    assert is_stale(new,new.results[0].input_hash)
    service.save(new,tmp_path/'project')
    reopened=ApplicationService().load(tmp_path/'project')
    assert reopened.bodies[0].body_id==old.bodies[0].body_id
    assert reopened.bodies[0].mass_properties==properties()
    assert input_hash(reopened)==input_hash(new)
    assert is_stale(reopened,reopened.results[0].input_hash)

def test_shared_public_consumer_compiles_mass_and_reference_without_mutation():
    service, project=declared_project(); original=input_hash(project)
    body=project.bodies[0]
    mechanical=service.prepare_rigid_body(project,body.body_id)
    assert mechanical.mass==body.mass
    assert mechanical.cg_body==properties().cg_body
    assert mechanical.reference_origin==body.reference_position
    M=mechanical.mass_matrix(mechanical.state())
    assert M.shape==(6,6) and np.linalg.eigvalsh(M).min()>0.
    assert input_hash(project)==original

def test_missing_unknown_and_invalid_physical_inputs_refuse_without_mutation():
    service=ApplicationService(); project=service.load_example(); original=input_hash(project)
    with pytest.raises(MechanicsInputError,match='explicit CG/inertia'):
        service.prepare_rigid_body(project,project.bodies[0].body_id)
    with pytest.raises(MechanicsInputError,match='identity'):
        service.prepare_rigid_body(project,'foreign-body')
    bad=BodyMassProperties((0.,0.,0.),((1.,0.,0.),(0.,1.,0.),(0.,0.,3.)))
    candidate=project.with_edits(bodies=(project.bodies[0].with_edits(mass_properties=bad),project.bodies[1]))
    with pytest.raises(MechanicsInputError,match='triangle'):
        service.prepare_rigid_body(candidate,candidate.bodies[0].body_id)
    assert input_hash(project)==original and len(candidate.results)==len(project.results)

def test_optional_solver_failure_is_actionable(monkeypatch):
    import anymarine.mechanics as bridge
    service,project=declared_project()
    def absent(name):
        raise ImportError('optional provider unavailable')
    monkeypatch.setattr(bridge.importlib,'import_module',absent)
    with pytest.raises(MechanicsUnavailableError,match='tested ANYsolver wheel'):
        service.prepare_rigid_body(project,project.bodies[0].body_id)

@pytest.mark.parametrize('payload', [[],{'version':'unsupported','cg_body':[0,0,0],'inertia_cg_body':[[1,0,0],[0,1,0],[0,0,1]]},
    {'cg_body':[0,0,0]}, {'version':'body_mass_properties_v1','cg_body':[float('nan'),0,0],'inertia_cg_body':[[1,0,0],[0,1,0],[0,0,1]]},
    {'version':'body_mass_properties_v1','cg_body':[0,0,0],'inertia_cg_body':[[1,0],[0,1]]}])
def test_malformed_recognized_metadata_is_not_ignored(payload):
    data=ApplicationService().load_example().bodies[0].to_dict()
    data['mass_properties']=payload
    with pytest.raises(ValueError): Body.from_dict(data)

def test_record_is_immutable_and_unknown_fields_refuse():
    cg=[.1,.2,.3]; I=[[4.,0.,0.],[0.,5.,0.],[0.,0.,6.]]
    record=BodyMassProperties(cg,I); cg[0]=99.; I[0][0]=99.
    assert record.cg_body[0]==.1 and record.inertia_cg_body[0][0]==4.
    payload=copy.deepcopy(record.to_dict()); payload['estimate']=True
    with pytest.raises(ValueError): BodyMassProperties.from_dict(payload)

