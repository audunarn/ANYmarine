"""Qt-free frozen native jobs and transport admission; no solver imports."""
from collections.abc import Mapping
import hashlib
import json
import math
from numbers import Real

from . import RESULT_KIND_MECHANICAL
from .domain import AnalysisSettings, Body, EquilibriumMetadata, ProjectModel, ResultRecord, _jsonable_value
from .hashing import input_hash
from .native_inputs import NativeMechanicalInputs
from .result_rotations import validated_rotation_matrix

SOLVER_API = 'marine_mechanical_v1'
MAX_NATIVE_FRAMES = 20001


class NativeJobError(ValueError):
    """Non-success native input or output transport."""


def _exact(data, keys, label):
    if not isinstance(data, Mapping) or set(data) != set(keys):
        raise NativeJobError(f'{label} fields must exactly match the native schema')
    return data


def _real(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise NativeJobError(f'{label} must be a finite real number')
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise NativeJobError(f'{label} is not representable as a finite real number') from error
    if not math.isfinite(converted):
        raise NativeJobError(f'{label} must be a finite real number')
    return converted


def _vector(value, size, label):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise NativeJobError(f'{label} requires {size} entries')
    return tuple(_real(item, label) for item in value)


def execution_limits(max_frames=1001, wall_seconds=60.):
    if type(max_frames) is not int or not 2 <= max_frames <= MAX_NATIVE_FRAMES:
        raise NativeJobError(f'max_frames must be an integer in 2..{MAX_NATIVE_FRAMES}')
    wall_seconds = _real(wall_seconds, 'wall_seconds')
    if not 0. < wall_seconds <= 600.:
        raise NativeJobError('wall_seconds must be in (0,600]')
    return {'max_frames': max_frames, 'wall_seconds': wall_seconds}


def project_from_snapshot(snapshot):
    _exact(snapshot, {'schema_version', 'kind', 'solver_api', 'input_hash', 'project',
                      'execution_limits'}, 'native snapshot')
    snapshot = _jsonable_value(snapshot)
    if (type(snapshot['schema_version']) is not int or snapshot['schema_version'] != 1
            or snapshot['kind'] != RESULT_KIND_MECHANICAL or snapshot['solver_api'] != SOLVER_API):
        raise NativeJobError('unsupported native snapshot kind/version/API')
    limits = _exact(snapshot['execution_limits'], {'max_frames', 'wall_seconds'}, 'execution limits')
    limits = execution_limits(**limits)
    data = _exact(snapshot['project'], {'name', 'bodies', 'equilibrium', 'settings',
        'results', 'linear_inputs', 'hydro_provenance', 'native_inputs'} | ({'equilibrium_adoption'} if 'equilibrium_adoption' in snapshot['project'] else set()), 'native project')
    if data['results'] != [] or data['linear_inputs'] is not None or data['hydro_provenance'] is not None:
        raise NativeJobError('native snapshot must exclude results and configured hydrodynamics')
    if not isinstance(data['bodies'], list):
        raise NativeJobError('native bodies must be a JSON array')
    _exact(data['equilibrium'], {'waterline_z', 'description', 'source'}, 'equilibrium')
    _exact(data['settings'], {'duration', 'dt', 'wave_amplitude', 'wave_period'}, 'synthetic replay settings')
    _real(data['equilibrium']['waterline_z'], 'waterline_z')
    for value in data['settings'].values():
        _real(value, 'synthetic replay setting')
    for body in data['bodies']:
        required = {'body_id', 'name', 'reference_position', 'mass', 'color', 'hydro_sidecar', 'mesh'}
        if (not isinstance(body, Mapping) or not required <= set(body)
                or not set(body) <= required | {'hydro_lid', 'hydro_lid_policy', 'mass_properties', 'reference_orientation'}):
            raise NativeJobError('native body fields must match the public body schema')
        _real(body['mass'], 'body mass')
        _vector(body['reference_position'], 3, 'reference position')
        for mesh_name in ('mesh', 'hydro_lid'):
            mesh = body.get(mesh_name)
            if mesh is None:
                continue
            _exact(mesh, {'positions', 'triangles'}, 'body mesh')
            if not isinstance(mesh['positions'], (list, tuple)) or not isinstance(mesh['triangles'], (list, tuple)):
                raise NativeJobError('mesh positions/triangles must be sequences')
            for point in mesh['positions']:
                _vector(point, 3, 'mesh position')
            for triangle in mesh['triangles']:
                if (not isinstance(triangle, (list, tuple)) or len(triangle) != 3
                        or any(type(index) is not int for index in triangle)):
                    raise NativeJobError('mesh triangles require integer indices, never booleans')
    try:
        project = ProjectModel(name=data['name'], bodies=tuple(Body.from_dict(item) for item in data['bodies']),
            equilibrium=EquilibriumMetadata.from_dict(data['equilibrium']),
            settings=AnalysisSettings.from_dict(data['settings']),
            native_inputs=NativeMechanicalInputs.from_dict(data['native_inputs']),
            equilibrium_adoption=data.get('equilibrium_adoption'))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise NativeJobError(f'invalid native project snapshot: {error}') from error
    if project.to_dict() != data:
        raise NativeJobError('native project snapshot contains discarded or coerced intent')
    if input_hash(project) != snapshot['input_hash']:
        raise NativeJobError('native snapshot does not reproduce its declared input hash')
    if project.native_inputs.run_spec['steps'] + 1 > limits['max_frames']:
        raise NativeJobError('native run exceeds its declared frame limit')
    return project


def _finite_json(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise NativeJobError('native evidence keys must be strings')
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_json(item)
    elif value is None or isinstance(value, (str, bool, int)):
        return
    elif isinstance(value, float) and math.isfinite(value):
        return
    else:
        raise NativeJobError('native evidence contains a non-JSON or nonfinite value')


def record_from_evidence(evidence, snapshot, *, result_id, note, created_utc):
    """Validate transport and derive replay/channels from authoritative arrays."""
    project = project_from_snapshot(snapshot)
    inputs = project.native_inputs
    spec = inputs.run_spec
    evidence_fields={'api_version', 'status', 'body_ids', 'dt', 'steps_requested','accepted', 'histories', 'force_signs', 'logs'}
    if inputs.mooring is not None:evidence_fields.add('node_count')
    _exact(evidence,evidence_fields,'native evidence')
    _finite_json(evidence)
    ids = tuple(body.body_id for body in project.bodies)
    expected_api=SOLVER_API if inputs.mooring is None else 'marine_mechanical_mooring_v1'
    if inputs.mooring is not None and (type(evidence['node_count']) is not int or evidence['node_count']!=inputs.mooring.data['node_count']):
        raise NativeJobError('node result inventory differs from producing inputs')
    if (evidence['api_version'] != expected_api or evidence['status'] != 'completed'
            or evidence['body_ids'] != list(ids) or _real(evidence['dt'], 'dt') != spec['dt']
            or type(evidence['steps_requested']) is not int or evidence['steps_requested'] != spec['steps']):
        raise NativeJobError('native evidence is incomplete or bound to other inputs')
    if evidence['force_signs'] != {'dry': 'internal', 'contributions': 'external'}:
        raise NativeJobError('native evidence force sign convention is unsupported')
    frames = evidence['accepted']
    if not isinstance(frames, list) or len(frames) != spec['steps'] + 1:
        raise NativeJobError('completed native evidence requires every requested frame')
    expected_components = {'dry'}
    if inputs.links:
        expected_components.add('mechanical-links')
    if inputs.gravity_world is not None:
        expected_components.update('dry-gravity:' + bid for bid in ids)
    expected_components.update(item['contribution_id'] for item in inputs.world_wrenches)
    if inputs.potential_flow is not None:
        expected_components.add('potential-flow-development')
    if inputs.morison is not None:
        expected_components.add(inputs.morison.data['contribution_id'])
    if (not isinstance(evidence['histories'], Mapping)
            or set(evidence['histories']) != expected_components - {'dry'}
            or not isinstance(evidence['logs'], list)):
        raise NativeJobError('native evidence history/log inventory is invalid')
    if inputs.morison is not None and evidence['histories'][inputs.morison.data['contribution_id']] is not None:
        raise NativeJobError('stateless Morison evidence cannot contain committed history')
    if inputs.potential_flow is not None:
        fluid = inputs.potential_flow.data
        memory = _exact(evidence['histories']['potential-flow-development'],
                        {'dt','step_index','velocities'},'potential-flow memory')
        if (type(memory['step_index']) is not int or memory['step_index'] != spec['steps']
                or _real(memory['dt'],'radiation dt') != spec['dt']):
            raise NativeJobError('potential-flow memory clock differs from accepted run')
        count = min(spec['steps']+1,len(fluid['kernel'])-1)
        if not isinstance(memory['velocities'],(list,tuple)) or len(memory['velocities']) != count:
            raise NativeJobError('potential-flow memory sample inventory is incomplete')
        for row in memory['velocities']:
            _vector(row,6*len(ids),'fixed-equilibrium radiation velocity')
    for log in evidence['logs']:
        _exact(log, {'step', 'event', 'detail', 'residual_norm'}, 'native log')
        if type(log['step']) is not int or not isinstance(log['event'], str) or not isinstance(log['detail'], str):
            raise NativeJobError('native log step/event/detail is invalid')
        if log['residual_norm'] is not None:
            _real(log['residual_norm'], 'log residual')
    if inputs.mooring is not None:
        from .mooring_inputs import FORCE_COMPONENTS,validate_frame
        expected_components.update('mooring:'+name for name in inputs.mooring.force_components)
    line_nodes=[]
    positions, rotations = {bid: [] for bid in ids}, {bid: [] for bid in ids}
    channels = {}
    times = []
    def sample(key, value):
        channels.setdefault(key, []).append(value)
    fields = {'step', 'time', 'positions', 'rotations', 'world_velocities', 'world_accelerations',
              'kinetic_energy', 'link_potential_energy', 'link_damping_power',
              'force_decomposition', 'residual_norm', 'newton_iterations', 'force_coordinate_system'}
    if inputs.mooring is not None:fields.update(('nodes','mooring_diagnostics'))
    previous_nodes=None
    for index, frame in enumerate(frames):
        _exact(frame, fields, 'accepted native frame')
        if (type(frame['step']) is not int or frame['step'] != index
                or _real(frame['time'], 'time') != index * spec['dt']
                or frame['force_coordinate_system'] != 'world_wrench_at_body_origin'
                or type(frame['newton_iterations']) is not int
                or not 0 <= frame['newton_iterations'] <= spec['max_newton_iterations']):
            raise NativeJobError('native frame step/time/wrench convention is invalid')
        times.append(frame['time'])
        if inputs.mooring is not None:
            try:
                from .mooring_inputs import validate_node_step
                nodes=validate_frame(frame,inputs.mooring,index,spec['residual_tolerance'])
                if previous_nodes is not None:validate_node_step(nodes,previous_nodes,spec['dt'])
                previous_nodes=nodes
            except ValueError as error:raise NativeJobError('invalid mooring evidence: '+str(error)) from error
            line_nodes.append(nodes['positions'])
            for node in range(inputs.mooring.data['node_count']):
                for name in ('velocities','accelerations'):
                    for axis,value in zip(('x','y','z'),nodes[name][node]):sample(f'mooring/node{node}/{name} {axis}',value)
            diag=frame['mooring_diagnostics']
            if inputs.mooring.has_contact:
                from .contact_inputs import contact_channels
                for key,value in contact_channels(diag['contact']).items():sample(key,value)
            for name in ('elastic_energy','kinetic_energy','damping_dissipation'):sample('mooring/'+name,diag[name])
            if inputs.mooring.has_hydrodynamics:
                from .mooring_inputs import HYDRO_CHANNELS
                for name in HYDRO_CHANNELS:sample('mooring/'+name,diag[name])
            offset=0
            for line in inputs.mooring.data['lines']:
                for segment in range(len(line['rest_lengths'])):
                    tag='mooring'+json.dumps([line['line_id'],segment],separators=(',',':'))
                    for name in ('tensions','lengths','elastic_tensions'):sample(tag+'/'+name,diag[name][offset+segment])
                offset+=len(line['rest_lengths'])
            for name in ('endpoint_forces','anchor_reactions'):
                for row,vector in enumerate(diag[name]):
                    for axis,value in zip(('x','y','z'),vector):sample(f'mooring/{name}{row}/{axis}',value)
            for name,rows in diag['node_force_components'].items():
                for node,vector in enumerate(rows):
                    for axis,value in zip(('x','y','z'),vector):sample(f'mooring/node{node}/{name} {axis}',value)
        for key in ('positions', 'rotations', 'world_velocities', 'world_accelerations'):
            _exact(frame[key], ids, key)
        _exact(frame['force_decomposition'], expected_components, 'force components')
        for component in expected_components:
            _exact(frame['force_decomposition'][component], ids, 'component body identities')
        for bid in ids:
            p = _vector(frame['positions'][bid], 3, 'position')
            R = validated_rotation_matrix(frame['rotations'][bid])
            v = _vector(frame['world_velocities'][bid], 6, 'WORLD velocity')
            a = _vector(frame['world_accelerations'][bid], 6, 'WORLD acceleration')
            initial = inputs.initial_states[bid]
            if index == 0 and (p != initial['position'] or R != initial['rotation'] or v != initial['velocity_world']):
                raise NativeJobError('native initial frame differs from declared physical state')
            positions[bid].append(p)
            rotations[bid].append(R)
            for axis, value in zip(('vx', 'vy', 'vz', 'wx', 'wy', 'wz'), v):
                sample(f'{bid}/WORLD {axis}', value)
            for axis, value in zip(('ax', 'ay', 'az', 'alphax', 'alphay', 'alphaz'), a):
                sample(f'{bid}/WORLD {axis}', value)
            total = [0.] * 6
            for component in sorted(expected_components):
                wrench = _vector(frame['force_decomposition'][component][bid], 6, 'WORLD wrench')
                identity = json.dumps([component, bid], separators=(',', ':'))
                for i, (axis, value) in enumerate(zip(('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'), wrench)):
                    sample(f'wrench{identity}/{axis}', value)
                    if component != 'dry':
                        total[i] += value
            for axis, value in zip(('Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz'), total):
                sample(f'{bid}/total external {axis}', value)
        for key in ('kinetic_energy', 'link_potential_energy', 'link_damping_power', 'residual_norm'):
            value = _real(frame[key], key)
            if key == 'residual_norm' and (value < 0. or (index and value > spec['residual_tolerance'])):
                raise NativeJobError('accepted native residual is inconsistent with run controls')
            sample(key, value)
    return ResultRecord(result_id=result_id, kind=RESULT_KIND_MECHANICAL,
        input_hash=snapshot['input_hash'], body_ids=ids, times=times, positions=positions,
        orientations={}, rotation_matrices=rotations, channels=channels,
        note=note, created_utc=created_utc, native_snapshot=snapshot,
        line_nodes=line_nodes if inputs.mooring is not None else None)


def decode_output(raw, snapshot, expected_input_hash, producer_seal):
    if (not isinstance(producer_seal, str) or len(producer_seal) != 64
            or hashlib.sha256(raw).hexdigest() != producer_seal):
        raise NativeJobError('native producer output seal is missing or mismatched')
    try:
        payload = json.loads(raw.decode('utf-8'))
        _exact(payload, {'schema_version', 'kind', 'input_hash', 'evidence', 'result'}, 'native output')
        if (type(payload['schema_version']) is not int or payload['schema_version'] != 1
                or payload['kind'] != RESULT_KIND_MECHANICAL
                or payload['input_hash'] != expected_input_hash or snapshot['input_hash'] != expected_input_hash):
            raise NativeJobError('native output kind/version/input hash is mismatched')
        result = ResultRecord.from_dict(payload['result'])
        derived = record_from_evidence(payload['evidence'], snapshot, result_id=result.result_id,
            note=result.note, created_utc=result.created_utc)
        if result.to_dict() != derived.to_dict():
            raise NativeJobError('native stored record does not match authoritative evidence')
        return result
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise NativeJobError(f'invalid native output: {error}') from error
