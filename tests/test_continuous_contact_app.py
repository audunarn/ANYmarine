"""Explicit constitutive intent, legacy persistence and backend feature refusal."""
import copy
from types import SimpleNamespace, MappingProxyType
import pytest
from anymarine.contact_inputs import seabed_from_data
from anymarine.native_examples import mooring_contact_project
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.services import ApplicationService
from anymarine.mechanics import MechanicsUnavailableError


def bed(version='marine_seabed_contact_v2'):
    value=dict(api_version=version,elevation=-4.,normal_stiffness=5000.,normal_damping=100.)
    if version.endswith('v2'):value['activation_depth_m']=.001
    return value


@pytest.mark.parametrize('version',['marine_seabed_contact_v1','marine_seabed_contact_v2'])
def test_transport_mapping_and_saved_intent_no_automatic_migration(version,tmp_path):
    p=mooring_contact_project();data=p.native_inputs.to_dict()
    data['mooring']['data']['seabed_contact']=bed(version)
    assert seabed_from_data(MappingProxyType(bed(version)))==bed(version)
    p=p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(copy.deepcopy(data)))
    service=ApplicationService();service.save(p,tmp_path/'project')
    loaded=service.load(tmp_path/'project')
    assert loaded.native_inputs.to_dict()==data
    model,_,_=service.prepare_native_run(loaded)
    assert model.mooring.seabed_contact.to_dict()==bed(version)
    assert ('activation_depth_m' in data['mooring']['data']['seabed_contact'])==version.endswith('v2')


@pytest.mark.parametrize('failure',['missing','zero','negative','bool','string','nan','infinite','v1-extra','unknown'])
def test_invalid_continuous_depth_refused(failure):
    value=bed()
    if failure=='missing':del value['activation_depth_m']
    elif failure=='v1-extra':value['api_version']='marine_seabed_contact_v1'
    elif failure=='unknown':value['api_version']='marine_seabed_contact_v3'
    else:value['activation_depth_m']={'zero':0.,'negative':-.1,'bool':True,'string':'.001',
                                     'nan':float('nan'),'infinite':float('inf')}[failure]
    with pytest.raises(ValueError):seabed_from_data(value)


def test_example_explicit_depth_and_missing_continuous_backend_fails_before_job(monkeypatch):
    import anymarine.mechanics as module
    p=mooring_contact_project()
    assert dict(p.native_inputs.mooring.data['seabed_contact'])==bed()
    original=module.importlib.import_module
    def load(name):
        return SimpleNamespace(API_VERSION='marine_seabed_contact_v1') if name=='anysolver.marine_seabed' else original(name)
    monkeypatch.setattr(module.importlib,'import_module',load)
    with pytest.raises(MechanicsUnavailableError,match='continuous seabed contact v2'):
        ApplicationService().prepare_native_run(p)
