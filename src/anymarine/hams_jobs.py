# SPDX-License-Identifier: MPL-2.0
"""Qt-free, source-bound native HAMS field jobs (development, unqualified)."""
from collections.abc import Mapping
from pathlib import Path
import hashlib
import json
import math
import os
import sys
import time

from .hashing import input_hash
from .hams_wave_replay import _project_data, _project_load, _regular

KIND = 'hams_field_development'
CONFIG_FORMAT = 'anymarine-hams-field-job-v1'
_MECHANICAL = {'center_of_gravity', 'mass', 'linear_damping', 'quadratic_damping',
               'hydrostatic_restoring', 'external_restoring'}


class HamsJobError(ValueError):
    pass


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _keys(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise HamsJobError(label + ' schema differs')


def _numbers(value, label):
    if isinstance(value, list):
        for item in value:
            _numbers(item, label)
    elif type(value) not in (int, float) or not math.isfinite(value):
        raise HamsJobError(label + ' requires finite numbers, without booleans or coercion')


def _verified_hull(mesh, lid):
    """Prove closed consistent winding with a reversed numerical lid as cap.

    This check does not alter either mesh or assign physical buoyancy to a lid.
    Exact coincident vertices are joined for topology only. Self-intersection
    and hydrodynamic accuracy remain separate geometry/qualification obligations.
    """
    import numpy as np
    from dataclasses import replace
    vertices = []; index = {}; faces = []; edges = {}
    for panel, reverse in ((mesh, False), (lid, True)):
        if panel is None:
            continue
        for row, size in zip(panel.faces, panel.face_sizes):
            points = [tuple(float(x) for x in panel.vertices[int(i)]) for i in row[:int(size)]]
            if reverse:
                points.reverse()
            ids = []
            for point in points:
                if point not in index:
                    index[point] = len(vertices); vertices.append(point)
                ids.append(index[point])
            if len(set(ids)) != len(ids):
                raise HamsJobError('Repeated hull/lid face vertex')
            for j in range(1, len(ids)-1):
                tri = (ids[0], ids[j], ids[j+1]); face_id = len(faces); faces.append(tri)
                a, b, c = np.asarray([vertices[k] for k in tri])
                if not np.linalg.norm(np.cross(b-a, c-a)) > 0.:
                    raise HamsJobError('Degenerate hull/lid verification face')
                for u, v in zip(tri, tri[1:]+tri[:1]):
                    edges.setdefault(tuple(sorted((u,v))), []).append((u,v,face_id))
    neighbours = [set() for _ in faces]
    for linked in edges.values():
        if len(linked) != 2 or linked[0][:2] != linked[1][:2][::-1]:
            raise HamsJobError('Hull winding requires an exactly closed, consistently oriented hull/cap topology')
        a,b=linked[0][2],linked[1][2];neighbours[a].add(b);neighbours[b].add(a)
    unseen=set(range(len(faces)));verts=np.asarray(vertices)
    while unseen:
        component={unseen.pop()};queue=list(component)
        while queue:
            for other in neighbours[queue.pop()]:
                if other in unseen:
                    unseen.remove(other);component.add(other);queue.append(other)
        origin=verts[list(faces[next(iter(component))])].mean(axis=0)
        terms=[]
        for face_id in component:
            a,b,c=verts[list(faces[face_id])]-origin
            terms.append(float(np.dot(a,np.cross(b,c))/6.))
        volume=math.fsum(terms)
        if not math.isfinite(volume) or volume <= 0.:
            raise HamsJobError('Hull component is inward or has no positive enclosed volume')
    return replace(mesh, outward_normals_verified=True)


def validate_config(project, config, *, require_native=False):
    """Capture explicit declarations; infer neither a producer nor mechanics."""
    from anyhydro.records import HydroBody, HydroRequest
    from anyhydro.hams_producer import HamsProducerBuild, GRAVITY_SOURCE_IDENTITY
    from anyhydro.hams_backend import HamsExecutionLimits, HamsBackend
    from anyhydro.hams_field_points import admit_field_points
    from anyfileio.hams import HamsMechanicalInput
    from .hydro import expected_world_mesh, expected_world_lid

    _project_data(project)
    c = json.loads(_bytes(_plain(config)))
    _keys(c, {'format', 'producer', 'request', 'mechanics', 'center_of_buoyancy',
              'field_points', 'execution_limits', 'irregular_frequency_removal',
              'allow_mechanical_rounding'}, 'HAMS configuration')
    if c['format'] != CONFIG_FORMAT or project.equilibrium.waterline_z != 0.:
        raise HamsJobError('Named HAMS field profile and waterline zero required')
    _keys(c['producer'], {'identity', 'executable_path'}, 'Producer')
    path = c['producer']['executable_path']
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise HamsJobError('Explicit absolute trusted producer path required')
    build = HamsProducerBuild.from_identity(c['producer']['identity'], executable_path=path)
    if build.source_tree_sha256 != GRAVITY_SOURCE_IDENTITY:
        raise HamsJobError('HAMS fields require exact gravity-consistent finite source')
    if c['producer']['identity'] != build.identity() or str(build.executable_path) != path:
        raise HamsJobError('Producer declaration must use canonical identity and resolved path')
    if require_native:
        if os.name != 'nt':
            if not sys.platform.startswith('linux'):
                raise HamsJobError('HAMS worker requires supported native Windows or Linux supervision')
            report=getattr(HamsBackend,'native_supervision_status',lambda:None)()
            if (not isinstance(report,dict) or report.get('platform')!='linux' or
                report.get('contract')!='linux-owned-session-guard-v1' or report.get('available') is not True):
                reason=report.get('reason') if isinstance(report,dict) else 'missing verified backend ownership contract'
                raise HamsJobError('Linux HAMS worker supervision unavailable: '+str(reason))
        build.verify(require_native=True)
    _keys(c['request'], {'omegas', 'wave_directions', 'water_depth', 'rho', 'gravity'}, 'Request')
    req = c['request']
    for key, value in req.items():
        if key != 'water_depth' or value != 'infinite':
            _numbers(value, 'Request')
    request = HydroRequest(req['omegas'], req['wave_directions'],
                           math.inf if req['water_depth'] == 'infinite' else req['water_depth'],
                           req['rho'], req['gravity'], False, False, {})
    canonical = dict(omegas=request.omegas.tolist(), wave_directions=request.wave_directions.tolist(),
                     water_depth='infinite' if math.isinf(request.water_depth) else request.water_depth,
                     rho=request.rho, gravity=request.gravity)
    if req != canonical:
        raise HamsJobError('Request did not preserve the exact declared values')
    _keys(c['execution_limits'], HamsExecutionLimits().identity(), 'Execution limits')
    limits = HamsExecutionLimits(**c['execution_limits'])
    caps = HamsExecutionLimits().identity()
    if any(getattr(limits, k) > v for k, v in caps.items()):
        raise HamsJobError('Application HAMS limits exceed admitted finite job bounds')
    for key in ('irregular_frequency_removal', 'allow_mechanical_rounding'):
        if type(c[key]) is not bool:
            raise HamsJobError(key + ' requires an explicit boolean')
    ids = {b.body_id for b in project.bodies}
    _keys(c['mechanics'], ids, 'Mechanical inventory')
    _keys(c['center_of_buoyancy'], ids, 'CB inventory')
    mechanics = {}
    bodies = []
    for body in project.bodies:
        if any(s.role == 'attached' or (s.role != 'lid' and
               (s.semantics.primary_hydrodynamics != 'potential_flow' or
                s.semantics.viscous_correction != 'none')) for s in body.hydro_surfaces):
            raise HamsJobError('This HAMS job refuses sheets or omitted/duplicate physical corrections')
        value = c['mechanics'][body.body_id]
        _keys(value, _MECHANICAL, 'Mechanical record')
        for numbers in value.values():
            _numbers(numbers, 'Mechanical record')
        mechanical = HamsMechanicalInput(**value)
        if {k: _plain(getattr(mechanical, k)) for k in _MECHANICAL} != value:
            raise HamsJobError('Mechanical declaration discarded intent')
        mechanics[body.body_id] = mechanical
        lid = expected_world_lid(body) if body.hydro_lid is not None else None
        if (c['irregular_frequency_removal'] and lid is None) or (not c['irregular_frequency_removal'] and lid is not None):
            raise HamsJobError('Explicit numerical lid and irregular-frequency policy must agree')
        hull = _verified_hull(expected_world_mesh(body), lid)
        bodies.append(HydroBody(body.body_id, body.name, hull, lid,
                               body.reference_position, mechanical.center_of_gravity,
                               tuple(body.body_id + '::' + d for d in
                                     ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'))))
    centers = c['center_of_buoyancy']
    for numbers in centers.values():
        _numbers(numbers, 'CB')
    points = admit_field_points(tuple(bodies), request, c['field_points'])
    if not any(p[2] == 0. for p in points):
        raise HamsJobError('At least one saved surface elevation point required')
    if [list(p) for p in points] != c['field_points']:
        raise HamsJobError('Field-point declaration discarded intent')
    if (len(bodies) > limits.max_bodies or len(request.omegas) > limits.max_frequencies or
        len(request.wave_directions) > limits.max_headings or len(points) > limits.max_field_points or
        sum(len(m.faces) for b in bodies for m in (b.mesh, b.lid_mesh) if m is not None) > limits.max_panels):
        raise HamsJobError('Requested inventory exceeds declared application execution limits')
    # Public construction validates explicit CB/mechanics without executing.
    HamsBackend(build, mechanics, centers, workroot=Path.cwd(), limits=limits, field_points=points,
                irregular_frequency_removal=c['irregular_frequency_removal'],
                allow_mechanical_rounding=c['allow_mechanical_rounding'])
    return c, tuple(bodies), request, build, mechanics, centers, points, limits


def hams_input_snapshot(project, config, *, require_native=True):
    c, *_ = validate_config(project, config, require_native=require_native)
    payload = dict(kind=KIND, project=_project_data(project), project_input_hash=input_hash(project),
                   config=c, execution_limits=dict(wall_seconds=c['execution_limits']['wall_seconds']))
    return dict(payload, input_hash=hashlib.sha256(_bytes(payload)).hexdigest())


def validate_snapshot(snapshot, *, require_native=True):
    snap = _plain(snapshot)
    _keys(snap, {'kind', 'project', 'project_input_hash', 'config', 'execution_limits', 'input_hash'}, 'HAMS snapshot')
    project = _project_load(snap['project'])
    expected = hams_input_snapshot(project, snap['config'], require_native=require_native)
    if snap != expected:
        raise HamsJobError('HAMS snapshot/hash differs from captured inputs')
    return expected, project


def load_hams_output(path, snapshot, seal):
    """Reopen producer receipts and raw semantics, then the portable GUI bundle."""
    from anyhydro.hams_backend import load_completed_hams_run, load_completed_hams_fields
    from anyhydro.hams_inputs import HamsPreparedDeck
    from anyhydro.hashing import dataset_content_hash
    from .hams_wave_replay import load_hams_wave_bundle
    snap, project = validate_snapshot(snapshot)
    c, bodies, request, build, mechanics, centers, points, limits = validate_config(project, snap['config'], require_native=True)
    path = Path(path)
    for directory in (path.parent, path.parent/'hams-runs'):
        if not _regular(directory, directory=True):
            raise HamsJobError('Owned HAMS directory is missing or a reparse point')
    if not _regular(path) or not seal or _sha(path) != seal:
        raise HamsJobError('Missing or changed HAMS worker output seal')
    payload = json.loads(path.read_text(encoding='utf-8'))
    _keys(payload, {'kind', 'input_hash', 'project_input_hash', 'run_directory', 'bundle_directory'}, 'HAMS output')
    if payload['kind'] != KIND or payload['input_hash'] != snap['input_hash'] or payload['project_input_hash'] != snap['project_input_hash']:
        raise HamsJobError('HAMS output belongs to different captured inputs')
    name = payload['run_directory']
    if (not isinstance(name, str) or not name.startswith('hams-runs/hams-') or
        len(name) != len('hams-runs/hams-') + 32 or
        any(c not in '0123456789abcdef' for c in name[len('hams-runs/hams-'):]) or
        payload['bundle_directory'] != 'hams-wave-bundle'):
        raise HamsJobError('HAMS artifact reference escapes its owned job directory')
    run = path.parent / name
    receipt = json.loads((run / 'run.json').read_text(encoding='utf-8'))
    if receipt.get('field_centers') != centers:
        raise HamsJobError('Native CB centers differ from captured application inputs')
    native_limits = receipt.get('execution_limits')
    from anyhydro.hams_backend import HamsExecutionLimits
    if type(native_limits) is dict:
        HamsExecutionLimits(**native_limits)
    if (type(native_limits) is not dict or set(native_limits) != set(limits.identity()) or
        type(native_limits['wall_seconds']) not in (int,float) or
        not 0. < native_limits['wall_seconds'] <= limits.wall_seconds or
        any(native_limits[k] != v for k,v in limits.identity().items() if k != 'wall_seconds')):
        raise HamsJobError('Native execution controls differ from captured application limits')
    manifest = HamsPreparedDeck(run, receipt['case_hash']).verify(bodies=bodies, request=request,
        mechanics=mechanics, executable_path=build.executable_path, producer_build=build, field_points=points)
    if any(manifest.get(k) != c[k] for k in ('irregular_frequency_removal','allow_mechanical_rounding')) or manifest.get('threads') != limits.threads:
        raise HamsJobError('Native deck controls differ from captured application inputs')
    dataset = load_completed_hams_run(run, bodies=bodies, request=request, producer_build=build)
    fields = load_completed_hams_fields(run, bodies=bodies, request=request, producer_build=build)
    if dataset.center_of_buoyancy.values.tolist() != [centers[b.body_id] for b in bodies]:
        raise HamsJobError('Canonical CB differs from captured application inputs')
    record = load_hams_wave_bundle(path.parent / payload['bundle_directory'])
    if (record.project_hash != snap['project_input_hash'] or
        record.parent_hash != dataset_content_hash(dataset) or record.fields.content_hash() != fields.content_hash()):
        raise HamsJobError('HAMS native evidence and portable replay differ')
    return record


def run_hams_job(snapshot, output_path, cancel_path):
    from .persistence import atomic_write_json
    from .hams_wave_replay import save_hams_wave_bundle
    from dataclasses import replace
    start = time.perf_counter()
    cancel_types = ()
    try:
        from anyhydro.hams_backend import HamsBackend, HamsCancelledError, HamsDeadlineError, load_completed_hams_fields
        cancel_types = (HamsCancelledError,)
        snap, project = validate_snapshot(snapshot)
        c, bodies, request, build, mechanics, centers, points, limits = validate_config(project, snap['config'], require_native=True)
        deadline = start + limits.wall_seconds
        def checkpoint():
            if cancel_path is not None and Path(cancel_path).exists():
                raise HamsCancelledError('Application HAMS cancellation requested')
            if time.perf_counter() >= deadline:
                raise HamsDeadlineError('Application HAMS wall deadline exceeded')
        def cancelled():
            checkpoint()
            return False
        checkpoint()
        output = Path(output_path)
        print('PROGRESS 0.05', flush=True)
        backend = HamsBackend(build, mechanics, centers, workroot=output.parent / 'hams-runs',
            limits=replace(limits, wall_seconds=deadline - time.perf_counter()), should_cancel=cancelled,
            field_points=points, irregular_frequency_removal=c['irregular_frequency_removal'],
            allow_mechanical_rounding=c['allow_mechanical_rounding'])
        dataset = backend.solve(bodies, request)
        checkpoint()
        print('PROGRESS 0.75', flush=True)
        fields = load_completed_hams_fields(backend.last_run_directory, bodies=bodies, request=request, producer_build=build)
        checkpoint()
        save_hams_wave_bundle(output.parent / 'hams-wave-bundle', project, dataset, fields)
        checkpoint()
        atomic_write_json(output, dict(kind=KIND, input_hash=snap['input_hash'], project_input_hash=snap['project_input_hash'],
            run_directory=backend.last_run_directory.relative_to(output.parent).as_posix(), bundle_directory='hams-wave-bundle'))
        seal = _sha(output)
        load_hams_output(output, snap, seal)
        checkpoint()
        print('OUTPUT_SHA256 ' + seal, flush=True)
        print('PROGRESS 1.0', flush=True)
        return 0
    except Exception as error:
        cancelled = isinstance(error, cancel_types)
        atomic_write_json(Path(output_path).with_suffix('.incomplete.json'),
            dict(kind=KIND, status='cancelled' if cancelled else 'failed',
                 reason=f'{type(error).__name__}: {error}', input_hash=_plain(snapshot).get('input_hash')))
        print(f'HAMS field job failed: {error}', flush=True)
        return 3 if cancelled else 4
