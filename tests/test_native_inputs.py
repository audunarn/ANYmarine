"""Native intent persistence and the actual public compiler; no trajectories."""
import copy
import json

import pytest

from anymarine.domain import ProjectModel, BodyMassProperties
from anymarine.hashing import input_hash
from anymarine.mechanics import MechanicsInputError, MechanicsUnavailableError
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.persistence import ProjectLoadError
from anymarine.services import ApplicationService


IDENTITY = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]


def configured_project():
    project = ApplicationService().load_example()
    properties = BodyMassProperties((0., 0., 0.), IDENTITY)
    bodies = tuple(body.with_edits(mass=1., mass_properties=properties) for body in project.bodies)
    a, b = bodies
    link = dict(link_id='link-1', body_a=a.body_id, body_b=b.body_id,
        rho_a=[0., 0., 0.], rho_b=[0., 0., 0.], axes_a=IDENTITY, axes_b=IDENTITY,
        rest_translation=[b.reference_position[i] - a.reference_position[i] for i in range(3)],
        rest_rotation=IDENTITY, stiffness=[[1. if i == j else 0. for j in range(6)] for i in range(6)],
        damping=[[0. for j in range(6)] for i in range(6)], preload=[0.] * 6)
    inputs = NativeMechanicalInputs(
        run_spec=dict(dt=.01, steps=3, residual_tolerance=1e-10, max_newton_iterations=20, line_search=True),
        initial_states={body.body_id: dict(position=list(body.reference_position),
            rotation=IDENTITY, velocity_world=[0., 0., 0., .1, .2, .3]) for body in bodies},
        links=[link], gravity_world=[0., 0., -9.81], world_wrenches=[dict(
            contribution_id='push', body_id=a.body_id, point_body=[.2, 0., 0.],
            force_world=[1., 2., 3.], moment_world=[.1, .2, .3])])
    return project.with_edits(bodies=bodies, results=(), native_inputs=inputs)


def test_legacy_example_identity_and_missing_native_serialization():
    project = ApplicationService().load_example()
    assert 'native_inputs' not in project.to_dict()
    assert input_hash(project) == project.results[0].input_hash
    assert project.with_edits().to_dict() == project.to_dict()


def test_native_save_reopen_hash_and_sidecar_identity(tmp_path):
    project = configured_project()
    root = tmp_path / 'project'
    ApplicationService().save(project, root)
    manifest = json.loads((root / 'project.json').read_text())
    assert manifest['schema_version'] == 5
    assert manifest['native_inputs']['sidecar'].startswith('native_inputs/')
    reopened = ApplicationService().load(root)
    assert reopened.native_inputs.to_dict() == project.native_inputs.to_dict()
    assert input_hash(reopened) == input_hash(project)
    assert [body.body_id for body in reopened.bodies] == [body.body_id for body in project.bodies]


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'downgrade', 'stripped', 'linear', 'hydro'])
def test_native_manifest_failure_is_refused(tmp_path, failure):
    root = tmp_path / failure
    ApplicationService().save(configured_project(), root)
    path = root / 'project.json'
    manifest = json.loads(path.read_text())
    sidecar = root / manifest['native_inputs']['sidecar']
    if failure == 'missing':
        sidecar.unlink()
    elif failure == 'corrupt':
        sidecar.write_text('{}')
    elif failure == 'downgrade':
        manifest['schema_version'] = 2
    elif failure == 'stripped':
        del manifest['native_inputs']
    elif failure == 'linear':
        manifest['linear_inputs'] = None
    else:
        manifest['hydro_artifacts'] = []
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectLoadError):
        ApplicationService().load(root)


def test_native_inputs_deep_copy_and_immutable_history_free_intent():
    project = configured_project()
    payload = project.native_inputs.to_dict()
    inputs = NativeMechanicalInputs.from_dict(payload)
    bid = project.bodies[0].body_id
    payload['initial_states'][bid]['position'][0] += 1.
    payload['links'][0]['stiffness'][0][0] += 100.
    assert inputs.to_dict() == project.native_inputs.to_dict()
    with pytest.raises(TypeError):
        inputs.initial_states[bid]['velocity_world'][0] = 1.
    with pytest.raises(TypeError):
        inputs.links[0]['stiffness'] = ((0.,) * 6,) * 6
    with pytest.raises(TypeError):
        inputs.run_spec['steps'] = 1


@pytest.mark.parametrize('failure', ['unknown', 'code', 'acceleration', 'boolean', 'string', 'reflection', 'duplicate', 'foreign'])
def test_unsupported_native_intent_refuses(failure):
    project = configured_project()
    payload = project.native_inputs.to_dict()
    state = payload['initial_states'][project.bodies[0].body_id]
    if failure == 'unknown':
        payload['version'] = 'unknown'
    elif failure == 'code':
        payload['world_wrenches'][0]['provider'] = 'arbitrary.module'
    elif failure == 'acceleration':
        state['acceleration'] = [0.] * 6
    elif failure == 'boolean':
        state['velocity_world'][0] = True
    elif failure == 'string':
        state['rotation'][0][0] = '1.0'
    elif failure == 'reflection':
        state['rotation'][0][0] = -1.
    elif failure == 'duplicate':
        payload['links'].append(copy.deepcopy(payload['links'][0]))
    else:
        payload['links'][0]['body_b'] = 'foreign'
    with pytest.raises(ValueError):
        NativeMechanicalInputs.from_dict(payload)


def test_native_project_identity_and_hydro_omission_refuse():
    project = configured_project()
    with pytest.raises(ValueError, match='exactly'):
        project.with_edits(bodies=project.bodies[:1])
    with pytest.raises(ValueError, match='hydrodynamics'):
        project.with_edits(bodies=(project.bodies[0].with_edits(hydro_sidecar='hydro/declared.nc'), project.bodies[1]))


def test_native_snapshot_is_detached_and_edits_invalidate_hash():
    service = ApplicationService()
    project = configured_project()
    snapshot = service.native_input_snapshot(project)
    old_hash = snapshot['input_hash']
    payload = project.native_inputs.to_dict()
    payload['run_spec']['steps'] += 1
    changed = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(payload))
    assert input_hash(changed) != old_hash
    snapshot['project']['native_inputs']['links'][0]['preload'][0] = 999.
    assert input_hash(project) == old_hash
    assert service.native_input_snapshot(project)['project']['native_inputs']['links'][0]['preload'][0] == 0.
    assert snapshot['project']['results'] == []


def test_public_native_compiler_uses_project_mass_and_world_initial_rates():
    project = configured_project()
    before = input_hash(project)
    model, states, spec = ApplicationService().prepare_native_run(project)
    assert tuple(body.body_id for body in model.bodies) == tuple(body.body_id for body in project.bodies)
    assert all(body.mass == 1. for body in model.bodies)
    assert len(model.links) == 1 and model.links[0].psd_tolerance == 1e-12
    assert len(model.contributions) == 1 and model.gravity_world == (0., 0., -9.81)
    for body in project.bodies:
        assert tuple(states[body.body_id].world_velocity) == (0., 0., 0., .1, .2, .3)
    assert spec.steps == 3 and spec.dt == .01 and input_hash(project) == before


def test_physical_stiffness_admission_stays_with_public_solver():
    project = configured_project()
    payload = project.native_inputs.to_dict()
    payload['links'][0]['stiffness'][0][0] = -1.
    candidate = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(payload))
    with pytest.raises(MechanicsInputError):
        ApplicationService().prepare_native_run(candidate)


def test_optional_solver_failure_is_actionable(monkeypatch):
    import anymarine.mechanics as bridge
    def absent(name):
        raise ImportError('optional provider unavailable')
    monkeypatch.setattr(bridge.importlib, 'import_module', absent)
    with pytest.raises(MechanicsUnavailableError):
        ApplicationService().prepare_native_run(configured_project())
