"""Transport tests use a fabricated fixture, never scientific response evidence.

The separate worker tests call the actual native API and preserve producer status.
"""
import copy
import hashlib
import json

import pytest

from anymarine.domain import ResultRecord
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.native_jobs import NativeJobError, decode_output, project_from_snapshot, record_from_evidence
from anymarine.persistence import ProjectLoadError
from anymarine.services import ApplicationService, ServiceError
from anymarine.transforms import matrix_frame_transform


def short_project():
    project = ApplicationService().load_native_example()
    data = project.native_inputs.to_dict()
    data['run_spec']['steps'] = 2
    return project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


def transport_fixture():
    """Fabricated shape/transport fixture; not a solved physical trajectory."""
    project = short_project()
    snapshot = ApplicationService().native_input_snapshot(project)
    ids = [body.body_id for body in project.bodies]
    frames = []
    for step in range(3):
        frames.append(dict(step=step, time=step*.01,
            positions={bid: list(project.native_inputs.initial_states[bid]['position']) for bid in ids},
            rotations={bid: [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]] for bid in ids},
            world_velocities={bid:[0.]*6 for bid in ids}, world_accelerations={bid:[0.]*6 for bid in ids},
            kinetic_energy=0., link_potential_energy=0., link_damping_power=0., residual_norm=0.,
            newton_iterations=0, force_coordinate_system='world_wrench_at_body_origin',
            force_decomposition={cid:{bid:[0.]*6 for bid in ids} for cid in ('dry','mechanical-links')}))
    evidence = dict(api_version='marine_mechanical_v1', status='completed', body_ids=ids,
        dt=.01, steps_requested=2, accepted=frames, histories={'mechanical-links':None},
        force_signs={'dry':'internal','contributions':'external'}, logs=[])
    record = record_from_evidence(evidence, snapshot, result_id='transport-fixture',
        note='Fabricated transport unit fixture, not solved scientific evidence.', created_utc='')
    payload = dict(schema_version=1, kind='mechanical_development', input_hash=snapshot['input_hash'],
        evidence=evidence, result=record.to_dict())
    return project, snapshot, payload, record


def test_native_matrix_result_roundtrip_and_deep_frozen_producing_geometry(tmp_path):
    project, snapshot, payload, record = transport_fixture()
    assert not record.orientations
    assert ResultRecord.from_dict(record.to_dict()).to_dict() == record.to_dict()
    with pytest.raises(TypeError):
        record.native_snapshot['project']['bodies'][0]['reference_position'][0] = 3.
    with pytest.raises(TypeError):
        record.rotation_matrices[record.body_ids[0]][0][0][0] = 2.
    # Removing current native intent must not erase the producing snapshot.
    stored = project.with_edits(native_inputs=None, results=(record,))
    ApplicationService().save(stored, tmp_path/'project')
    reopened = ApplicationService().load(tmp_path/'project')
    assert reopened.results[0].to_dict() == record.to_dict()
    assert project_from_snapshot(reopened.results[0].native_snapshot).native_inputs is not None


def test_native_producer_seal_and_authoritative_record_agreement(tmp_path):
    project, snapshot, payload, record = transport_fixture()
    raw = json.dumps(payload).encode()
    seal = hashlib.sha256(raw).hexdigest()
    assert decode_output(raw, snapshot, snapshot['input_hash'], seal).to_dict() == record.to_dict()
    with pytest.raises(NativeJobError, match='seal'):
        decode_output(raw, snapshot, snapshot['input_hash'], None)
    payload['result']['positions'][record.body_ids[0]][1][0] += .1
    altered = json.dumps(payload).encode()
    with pytest.raises(NativeJobError, match='seal'):
        decode_output(altered, snapshot, snapshot['input_hash'], seal)
    with pytest.raises(NativeJobError, match='authoritative'):
        decode_output(altered, snapshot, snapshot['input_hash'], hashlib.sha256(altered).hexdigest())


@pytest.mark.parametrize('failure', ['status','missing-frame','order','time','matrix','initial-state','force-sign',
    'missing-force','boolean-force','wrong-wrench-origin','unknown-field','histories','residual'])
def test_native_non_success_or_malformed_evidence_is_refused(failure):
    project, snapshot, payload, record = transport_fixture()
    data = copy.deepcopy(payload['evidence'])
    bid = record.body_ids[0]
    if failure == 'status': data['status'] = 'cancelled'
    elif failure == 'missing-frame': data['accepted'].pop()
    elif failure == 'order': data['body_ids'].reverse()
    elif failure == 'time': data['accepted'][1]['time'] += .1
    elif failure == 'matrix': data['accepted'][1]['rotations'][bid][0][0] = -1.
    elif failure == 'initial-state': data['accepted'][0]['positions'][bid][0] += .1
    elif failure == 'force-sign': data['force_signs']['dry'] = 'external'
    elif failure == 'missing-force': del data['accepted'][1]['force_decomposition']['dry']
    elif failure == 'boolean-force': data['accepted'][1]['force_decomposition']['dry'][bid][0] = True
    elif failure == 'wrong-wrench-origin': data['accepted'][1]['force_coordinate_system'] = 'world_origin'
    elif failure == 'unknown-field': data['provider'] = 'arbitrary.module'
    elif failure == 'histories': data['histories'] = {}
    else: data['accepted'][1]['residual_norm'] = 1e-5
    with pytest.raises(ValueError):
        record_from_evidence(data, snapshot, result_id='invalid', note='', created_utc='')


def test_native_record_cannot_carry_fake_euler_or_partial_timeline():
    project, snapshot, payload, record = transport_fixture()
    data = record.to_dict()
    data['orientations'] = {bid:[[0.,0.,0.]]*3 for bid in record.body_ids}
    with pytest.raises(ValueError): ResultRecord.from_dict(data)
    data = record.to_dict()
    data['times'] = [.0]
    with pytest.raises(ValueError): ResultRecord.from_dict(data)


def test_native_snapshot_limits_and_unsupported_body_semantics_refuse():
    project, snapshot, payload, record = transport_fixture()
    data = copy.deepcopy(snapshot)
    data['project']['bodies'][0]['force_provider'] = 'arbitrary.module'
    with pytest.raises(ValueError): project_from_snapshot(data)
    with pytest.raises((ValueError, ServiceError)):
        ApplicationService().native_input_snapshot(project, max_frames=2)


def test_native_replay_transform_embeds_rotation_without_euler():
    rotation = [[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]]
    matrix = matrix_frame_transform([2.,3.,4.], rotation)
    assert matrix[:3,:3].tolist() == rotation
    assert matrix[:3,3].tolist() == [2.,3.,4.]


def test_native_stale_result_keeps_original_mesh_and_transforms():
    project, snapshot, payload, record = transport_fixture()
    original_mesh = project.bodies[0].mesh.to_dict()
    body = project.bodies[0].with_edits(reference_position=(50.,0.,0.))
    current = project.with_edits(bodies=(body,project.bodies[1]), results=(record,))
    assert ApplicationService().result_stale(current, record)
    producing = project_from_snapshot(record.native_snapshot)
    assert producing.bodies[0].mesh.to_dict() == original_mesh
    assert producing.bodies[0].reference_position != body.reference_position


@pytest.mark.parametrize('mode', ['completed','cancelled','deadline'])
def test_actual_native_worker_status_and_seal(tmp_path, capsys, mode):
    from anymarine.worker import run
    project = short_project()
    service = ApplicationService()
    snapshot = service.native_input_snapshot(project, wall_seconds=.000001 if mode=='deadline' else 60.)
    inp, out, cancel = tmp_path/'input.json', tmp_path/'output.json', tmp_path/'cancel'
    service.write_job_input(snapshot, inp)
    if mode == 'cancelled': cancel.touch()
    code = run(inp, out, cancel)
    stdout = capsys.readouterr().out
    if mode == 'completed':
        assert code == 0 and out.exists()
        seal = [line.split()[1] for line in stdout.splitlines() if line.startswith('OUTPUT_SHA256 ')][0]
        record = service.load_job_output(out, snapshot['input_hash'], snapshot=snapshot, expected_output_seal=seal)
        assert record.frame_count() == 3 and not record.orientations
    else:
        assert code == (3 if mode=='cancelled' else 4) and not out.exists()
        evidence = json.loads((tmp_path/'output.partial.json').read_text())
        assert evidence['status'] == ('cancelled' if mode=='cancelled' else 'failed')
        assert 'OUTPUT_SHA256' not in stdout
