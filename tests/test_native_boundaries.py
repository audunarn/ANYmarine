"""Native boundary refusals are non-numerical; no fluid import is invoked."""
import copy
import pytest
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.native_jobs import NativeJobError, project_from_snapshot
from anymarine.services import ApplicationService, ServiceError


@pytest.mark.parametrize('route', ['demo','explicit','store','local','snapshot','raw-import','derived-import'])
def test_native_dry_intent_refuses_linear_and_fluid_routes_before_work(tmp_path, monkeypatch, route):
    import anymarine.services as services
    service = ApplicationService(tmp_path/'project')
    project = service.load_native_example()
    # Root is explicit so rejection is the intent guard, not a missing-root error.
    service.root = tmp_path/'project'
    def forbidden(*args, **kwargs):
        raise AssertionError('incompatible route performed work before native intent refusal')
    monkeypatch.setattr(services, '_hydro_import', forbidden)
    monkeypatch.setattr(services, '_hydro_derived_import', forbidden)
    monkeypatch.setattr(services, 'analytic_demo_inputs', forbidden)
    monkeypatch.setattr(services, 'run_linear', forbidden)
    calls = {
        'demo': lambda: service.linear_demo_inputs(project),
        'explicit': lambda: service.load_explicit_linear_inputs(project, {}),
        'store': lambda: service.set_linear_inputs(project, None),
        'local': lambda: service.run_linear_local(project),
        'snapshot': lambda: service.linear_job_snapshot(project),
        'raw-import': lambda: service.import_hydro_inputs(project, 'absent.nc', 'absent-kernel.nc', {}),
        'derived-import': lambda: service.import_derived_hydro_inputs(project, 'absent.nc', 'absent.npz', 'absent-kernel.nc', {}),
    }
    with pytest.raises(ServiceError, match='native dry inputs'):
        calls[route]()
    assert not (tmp_path/'project').exists()


@pytest.mark.parametrize('field', ['dt','velocity','coefficient','body-reference'])
def test_native_integer_overflow_and_invalid_identity_refuse_as_value_errors(field):
    project = ApplicationService().load_native_example()
    data = project.native_inputs.to_dict()
    if field == 'dt': data['run_spec']['dt'] = 10 ** 400
    elif field == 'velocity': data['initial_states'][project.bodies[0].body_id]['velocity_world'][0] = 10 ** 400
    elif field == 'coefficient': data['links'][0]['stiffness'][0][0] = 10 ** 400
    else: data['links'][0]['body_a'] = []
    with pytest.raises(ValueError):
        NativeMechanicalInputs.from_dict(data)


def test_native_snapshot_mass_overflow_has_typed_non_success():
    service = ApplicationService()
    snapshot = service.native_input_snapshot(service.load_native_example())
    malformed = copy.deepcopy(snapshot)
    malformed['project']['bodies'][0]['mass'] = 10 ** 400
    with pytest.raises(NativeJobError):
        project_from_snapshot(malformed)


def test_native_saved_body_numeric_overflow_is_project_load_non_success(tmp_path):
    import hashlib
    import json
    from anymarine.persistence import ProjectLoadError
    service = ApplicationService()
    root = tmp_path/'project'
    project = service.load_native_example()
    service.save(project, root)
    manifest_path = root/'project.json'
    manifest = json.loads(manifest_path.read_text())
    entry = manifest['bodies'][project.bodies[0].body_id]
    body = json.loads((root/entry['sidecar']).read_text())
    body['mass'] = 10 ** 400
    raw = json.dumps(body).encode()
    digest = hashlib.sha256(raw).hexdigest()
    reference = f'bodies/{digest}.json'
    (root/reference).write_bytes(raw)
    entry.update(sidecar=reference, sha256=digest)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectLoadError, match='body'):
        ApplicationService().load(root)



def test_native_boolean_mass_cannot_be_serialized_into_valid_physics():
    project = ApplicationService().load_native_example()
    body = project.bodies[0].with_edits(mass=True)
    with pytest.raises(ValueError, match='body mass'):
        project.with_edits(bodies=(body, project.bodies[1]))


def test_native_saved_boolean_mass_is_rejected_before_legacy_float_coercion(tmp_path):
    import hashlib
    import json
    from anymarine.persistence import ProjectLoadError
    service = ApplicationService()
    root = tmp_path/'project'
    project = service.load_native_example()
    service.save(project, root)
    path = root/'project.json'
    manifest = json.loads(path.read_text())
    entry = manifest['bodies'][project.bodies[0].body_id]
    data = json.loads((root/entry['sidecar']).read_text())
    data['mass'] = True
    raw = json.dumps(data).encode()
    digest = hashlib.sha256(raw).hexdigest()
    reference = f'bodies/{digest}.json'
    (root/reference).write_bytes(raw)
    entry.update(sidecar=reference, sha256=digest)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectLoadError, match='native stored body mass'):
        ApplicationService().load(root)

