"""Shared Qt-free builders/transport for unqualified wave visualization jobs."""
import hashlib
import json
import math
import time
from pathlib import Path
import numpy as np
from collections.abc import Mapping
from .hashing import input_hash

WAVE_JOB_KIND='wave_preview'

def preview_request(project,transforms,*,omega=1.4,heading_degrees=0.,amplitude=.3,water_depth=20.):
    if any(b.hydro_surfaces for b in project.bodies):
        raise ValueError('Native wave preview does not support explicit hydrodynamic surfaces; plates cannot be omitted from disturbance. Use the plate geometry preview or a plate-free model.')
    from anyhydro.wave_preview import validate_request
    bodies=[]
    for body in project.bodies:
        matrix=np.asarray(transforms[body.body_id],dtype=float)
        if matrix.shape!=(4,4) or not np.isfinite(matrix).all():
            raise ValueError('captured wave body transforms must be finite4x4')
        points=np.asarray(body.mesh.positions)@matrix[:3,:3].T+matrix[:3,3]
        bodies.append(dict(body_id=body.body_id,vertices=points.tolist(),triangles=[list(t) for t in body.mesh.triangles]))
    all_points=np.concatenate([b['vertices'] for b in bodies])
    low=all_points.min(axis=0);high=all_points.max(axis=0)
    padding=max(6.,.5*float(np.max(high[:2]-low[:2])))
    return validate_request(dict(bodies=bodies,omega=omega,heading_degrees=heading_degrees,
        amplitude=amplitude,phase=0.,water_depth=water_depth,
        bounds=[low[0]-padding,high[0]+padding,low[1]-padding,high[1]+padding],nx=33,ny=25,
        project_input_hash=input_hash(project)))

def wave_input_snapshot(request,*,wall_seconds=20.):
    from anyhydro.wave_preview import validate_request,request_hash
    if type(wall_seconds) not in (int,float) or not math.isfinite(wall_seconds) or not 0<wall_seconds<=20:
        raise ValueError('wave preview wall limit must be positive and at most20s')
    r=validate_request(request)
    return dict(kind=WAVE_JOB_KIND,input_hash=request_hash(r),request=r,
                execution_limits=dict(wall_seconds=float(wall_seconds)))

def validate_snapshot(snapshot):
    def plain(value):
        if isinstance(value,Mapping):
            return {k:plain(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)):
            return [plain(v) for v in value]
        return value
    snapshot=plain(snapshot)
    if type(snapshot) is not dict or set(snapshot)!={'kind','input_hash','request','execution_limits'}:
        raise ValueError('wave job snapshot fields must match the named schema')
    limits=snapshot['execution_limits']
    if type(limits) is not dict or set(limits)!={'wall_seconds'}:
        raise ValueError('wave execution limits must match the named schema')
    expected=wave_input_snapshot(snapshot['request'],**limits)
    if snapshot!=expected:
        raise ValueError('wave job snapshot/hash does not match its captured inputs')
    return expected

def load_wave_output(path,snapshot,seal):
    from anyhydro.wave_preview import validate_field
    snap=validate_snapshot(snapshot)
    raw=Path(path).read_bytes()
    if not seal or hashlib.sha256(raw).hexdigest()!=seal:
        raise ValueError('wave output seal missing or mismatched')
    payload=json.loads(raw.decode('utf-8'))
    if (type(payload) is not dict or set(payload)!={'kind','input_hash','field'}
            or payload['kind']!=WAVE_JOB_KIND or payload['input_hash']!=snap['input_hash']):
        raise ValueError('wave output belongs to a different job')
    field=validate_field(payload['field'],snap['request'])
    if field['kind']!='fixed_body_diffraction_preview':
        raise ValueError('wave calculation output must contain actual body diffraction')
    return field

def run_wave_job(snapshot,output_path,cancel_path):
    from .persistence import atomic_write_json
    start=time.perf_counter()
    def cancelled():
        return ((cancel_path is not None and Path(cancel_path).exists())
                or time.perf_counter()-start>snap['execution_limits']['wall_seconds'])
    try:
        from anyhydro.wave_preview import compute_diffraction_preview
        snap=validate_snapshot(snapshot)
        print('PROGRESS 0.05',flush=True)
        field=compute_diffraction_preview(snap['request'],cancel=cancelled)
        if cancelled():
            raise InterruptedError('wave cancellation/deadline before publication')
        atomic_write_json(output_path,dict(kind=WAVE_JOB_KIND,input_hash=snap['input_hash'],field=field))
        if cancelled():
            # Retain the late artifact but do not seal it as success.
            raise InterruptedError('late wave cancellation/deadline before seal')
        print('OUTPUT_SHA256 '+hashlib.sha256(Path(output_path).read_bytes()).hexdigest(),flush=True)
        print('PROGRESS 1.0',flush=True)
        return 0
    except Exception as error:
        atomic_write_json(Path(output_path).with_suffix('.incomplete.json'),
            dict(kind=WAVE_JOB_KIND,status='cancelled' if isinstance(error,InterruptedError) else 'failed',
                 reason=f'{type(error).__name__}: {error}',input_hash=snapshot.get('input_hash')))
        print(f'wave preview failed: {error}',flush=True)
        return 3 if isinstance(error,InterruptedError) else 4
