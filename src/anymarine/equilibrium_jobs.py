"""Pure static-job transport; backend execution is isolated in run_job only."""
from collections.abc import Mapping
import hashlib,json,math
from .domain import ResultRecord,_jsonable_value
from .hashing import input_hash,canonical_json
from .native_jobs import NativeJobError,_exact,_real,execution_limits
from .native_jobs import _vector,_finite_json

KIND='equilibrium_development'
SOLVER_API='marine_coupled_equilibrium_development_v1'
DEFAULT_SPEC=dict(force_scale=100.,moment_scale=100.,force_atol=1e-8,moment_atol=1e-8,
    relative_tolerance=1e-8,release_fractions=[.25,.5,.75,1.],max_iterations=40,max_line_search=20)


def validate_spec(data):
    data=_jsonable_value(_exact(data,set(DEFAULT_SPEC),'equilibrium spec'))
    for name in ('force_scale','moment_scale','force_atol','moment_atol','relative_tolerance'):
        value=_real(data[name],name)
        if value<0. or (name.endswith('scale') and value<=0.):raise NativeJobError('invalid static scale/tolerance')
    for group in ('force','moment'):
        limit=data[group+'_atol']+data['relative_tolerance']*data[group+'_scale']
        if not math.isfinite(limit) or limit<=0.:raise NativeJobError('nonfinite/zero derived static tolerance')
    f=data['release_fractions']
    if not isinstance(f,list) or not f or f[-1]!=1. or any(_real(x,'fraction')<=0. for x in f) or any(b<=a for a,b in zip(f,f[1:])):
        raise NativeJobError('release fractions must increase to one')
    if len(f)>32:raise NativeJobError('at most32static release stages')
    for name in ('max_iterations','max_line_search'):
        if type(data[name]) is not int or not 1<=data[name]<=100:
            raise NativeJobError('static iterations/searches must be in1..100')
    return data


def _hash(snapshot):
    return hashlib.sha256(canonical_json({k:v for k,v in snapshot.items() if k!='input_hash'}).encode()).hexdigest()


def input_snapshot(project,*,static_spec=None,max_frames=1001,wall_seconds=60.):
    if project.native_inputs is None:raise NativeJobError('static job requires native mechanical inputs')
    payload=project.to_dict();payload['results']=[]
    snapshot=dict(schema_version=1,kind=KIND,solver_api=SOLVER_API,project=payload,
        project_input_hash=input_hash(project),static_spec=validate_spec(DEFAULT_SPEC if static_spec is None else static_spec),
        execution_limits=execution_limits(max_frames,wall_seconds))
    snapshot['input_hash']=_hash(snapshot)
    project_from_snapshot(snapshot)
    return snapshot


def project_from_snapshot(snapshot):
    _exact(snapshot,{'schema_version','kind','solver_api','project','project_input_hash',
                     'static_spec','execution_limits','input_hash'},'static snapshot')
    plain=_jsonable_value(snapshot)
    if type(plain['schema_version']) is not int or plain['schema_version']!=1 or plain['kind']!=KIND or plain['solver_api']!=SOLVER_API:
        raise NativeJobError('unsupported equilibrium snapshot')
    validate_spec(plain['static_spec']);execution_limits(**plain['execution_limits'])
    if plain['input_hash']!=_hash(plain):raise NativeJobError('static run hash differs from complete inputs')
    from .native_jobs import project_from_snapshot as native_project
    proxy={k:v for k,v in plain.items() if k not in ('project_input_hash','static_spec')}
    proxy.update(kind='mechanical_development',solver_api='marine_mechanical_v1',input_hash=plain['project_input_hash'])
    return native_project(proxy)


def stale(project,result):
    try:
        return input_snapshot(project,static_spec=result.native_snapshot['static_spec'],
            **result.native_snapshot['execution_limits'])['input_hash']!=result.input_hash
    except (ValueError,TypeError,KeyError):return True


def validate_record(record,project):
    """Transport evidence only; never re-solves or verifies constitutive truth."""
    data=_jsonable_value(_exact(record.equilibrium_evidence,{'api_version','status','frames','audit'},'static evidence'))
    if data['api_version']!=SOLVER_API or data['status']!='converged':raise NativeJobError('static result is not converged')
    frames=data['frames'];n=len(record.times)
    if not isinstance(frames,list) or n<2 or len(frames)!=n or tuple(record.times)!=tuple(float(i) for i in range(n)):
        raise NativeJobError('static abscissa must be complete continuation stage indices')
    if n>record.native_snapshot['execution_limits']['max_frames']:raise NativeJobError('static frame limit exceeded')
    for i,f in enumerate(frames):
        _exact(f,{'kind','fraction'},'static frame')
        if f['kind'] not in ('initial','weight','current','contact','coupled') or not 0.<=_real(f['fraction'],'fraction')<=1.:
            raise NativeJobError('invalid continuation frame identity')
        if (i==0)!=(f['kind']=='initial'):raise NativeJobError('initial static frame inventory mismatch')
    if frames[0]['fraction']!=0. or frames[-1]!=dict(kind='coupled',fraction=1.):raise NativeJobError('static stage completion mismatch')
    release=[f['fraction'] for f in frames if f['kind']=='coupled']
    if release!=list(record.native_snapshot['static_spec']['release_fractions']):raise NativeJobError('static release stages differ from input')
    for bid in record.body_ids:
        if tuple(record.positions[bid][0])!=tuple(project.native_inputs.initial_states[bid]['position']):
            raise NativeJobError('static initial body positions differ from input')
        if tuple(tuple(r) for r in record.rotation_matrices[bid][0])!=project.native_inputs.initial_states[bid]['rotation']:
            raise NativeJobError('static initial body rotation differs from input')
    # Fixed-fairlead stages move nodes only; they retain the input body poses.
    for i,frame in enumerate(frames):
        if frame['kind'] not in ('weight','current','contact'):continue
        for bid in record.body_ids:
            if (record.positions[bid][i]!=record.positions[bid][0] or
                    record.rotation_matrices[bid][i]!=record.rotation_matrices[bid][0]):
                raise NativeJobError('fixed-fairlead static stage changed body geometry')
    mooring=project.native_inputs.mooring
    expected_frames=[dict(kind='initial',fraction=0.)]
    if mooring is not None:
        expected_frames += [dict(kind='weight',fraction=f) for f in (.25,.5,.75,1.)]
        if mooring.has_hydrodynamics:expected_frames += [dict(kind='current',fraction=f) for f in (.25,.5,.75,1.)]
        if mooring.has_contact:expected_frames.append(dict(kind='contact',fraction=1.))
    expected_frames += [dict(kind='coupled',fraction=f) for f in record.native_snapshot['static_spec']['release_fractions']]
    if frames!=expected_frames:raise NativeJobError('static accepted stage inventory differs from producing intent')
    if (mooring is None)!=(record.line_nodes is None):raise NativeJobError('static node inventory mismatch')
    if mooring is not None:
        from .mooring_inputs import node_rows
        if len(record.line_nodes)!=n:raise NativeJobError('static node frame inventory mismatch')
        for rows in record.line_nodes:node_rows(rows,mooring.data['node_count'],'static node frame')
        if record.line_nodes[0]!=mooring.initial_nodes['positions']:raise NativeJobError('static initial nodes differ from input')
    audit=data['audit'];spec=record.native_snapshot['static_spec']
    required={'force_residual','moment_residual','tolerance_ratio','normalized_residual','force_limit','moment_limit',
        'body_world_residual','node_residual','component_error','temporary_support_fraction','potential_geometry','components','max_penetration'}
    _exact(audit,required,'static final audit')
    _finite_json(audit)
    if len(audit['body_world_residual'])!=len(record.body_ids):raise NativeJobError('static body residual inventory')
    body=[_vector(row,6,'static body residual') for row in audit['body_world_residual']]
    count=0 if mooring is None else mooring.data['node_count']
    if len(audit['node_residual'])!=count:raise NativeJobError('static node residual inventory')
    node=[_vector(row,3,'static node residual') for row in audit['node_residual']]
    native=project.native_inputs
    names=set(w['contribution_id'] for w in native.world_wrenches)
    if native.links:names.add('mechanical-links')
    if native.gravity_world is not None:names.update('dry-gravity:'+bid for bid in record.body_ids)
    if native.potential_flow is not None:
        from .potential_flow_inputs import POTENTIAL_FLOW_ID
        names.add(POTENTIAL_FLOW_ID)
    if native.morison is not None:
        from .morison_inputs import MORISON_ID
        names.add(MORISON_ID)
    if mooring is not None:
        names.update('mooring:'+name for name in ('elastic_force','damping_force','weight','inertia'))
        if mooring.has_hydrodynamics:
            names.update('mooring:'+name for name in ('drag_normal','drag_tangential','fluid_inertia','added_mass'))
        if mooring.has_contact:
            names.update('mooring:'+name for name in ('contact_elastic','contact_damping'))
    components=_exact(audit['components'],names,'static component inventory')
    size=6*len(record.body_ids)+3*count
    vectors=[_vector(v,size,'static component') for v in components.values()]
    target=[x for row in body for x in row]+[x for row in node for x in row]
    # Accepted states are recharted: generalized forces are WORLD-conjugate.
    # JSON key sorting can change addition order. Bound only floating-point
    # summation roundoff; this is not a constitutive or scientific tolerance.
    error=_real(audit['component_error'],'static component error')
    for j,value in enumerate(target):
        absolute=sum(abs(v[j]) for v in vectors)
        roundoff=(len(vectors)+1)*math.ulp(max(1.,absolute))
        difference=abs(sum(v[j] for v in vectors)-value)
        if not math.isfinite(absolute) or difference>error+roundoff:
            raise NativeJobError('static components differ from final residual arrays')
        group='moment' if j<6*len(record.body_ids) and j%6>=3 else 'force'
        if difference>audit[group+'_limit']+roundoff:
            raise NativeJobError('static component decomposition exceeds residual tolerance')
    f=max([abs(x) for row in body for x in row[:3]]+[abs(x) for row in node for x in row]+[0.])
    m=max([abs(x) for row in body for x in row[3:]]+[0.])
    if audit['force_residual']!=f or audit['moment_residual']!=m:raise NativeJobError('static residual arrays differ from audit')
    for name in ('force_residual','moment_residual','tolerance_ratio','normalized_residual','component_error','max_penetration'):
        if _real(audit[name],name)<0.:raise NativeJobError('negative static audit value')
    for group in ('force','moment'):
        limit=spec[group+'_atol']+spec['relative_tolerance']*spec[group+'_scale']
        if audit[group+'_limit']!=limit or audit[group+'_residual']>limit:raise NativeJobError('static force/moment audit failed')
        _real(audit[group+'_limit'],'static limit')
    if audit['tolerance_ratio']!=max(f/audit['force_limit'],m/audit['moment_limit']) or audit['normalized_residual']!=max(f/spec['force_scale'],m/spec['moment_scale']):
        raise NativeJobError('static normalized residual differs from audit arrays')
    if audit['tolerance_ratio']>1. or _real(audit['temporary_support_fraction'],'support fraction')!=0.:
        raise NativeJobError('static audit retains imbalance or support')
    required_channels=('equilibrium/force_residual N','equilibrium/moment_residual Nm','equilibrium/fraction')
    for key in required_channels:
        if key not in record.channels or len(record.channels[key])!=n:raise NativeJobError('missing static diagnostic channel')
    if (record.channels[required_channels[0]][-1]!=audit['force_residual'] or
        record.channels[required_channels[1]][-1]!=audit['moment_residual'] or
        tuple(record.channels[required_channels[2]])!=tuple(f['fraction'] for f in frames)):
        raise NativeJobError('static final channels differ from authoritative audit')
    potential=project.native_inputs.potential_flow
    if potential is None:
        if audit['potential_geometry']!=[]:raise NativeJobError('static geometry without source')
    else:
        source=potential.to_dict();newframes=[]
        for f in source['frames']:
            bid=f['body_id'];newframes.append({**f,'equilibrium_position':list(record.positions[bid][-1]),
                'equilibrium_rotation':[list(row) for row in record.rotation_matrices[bid][-1]]})
        final=hashlib.sha256(canonical_json(newframes).encode()).hexdigest()
        expected=[dict(source_equilibrium_hash=source['equilibrium_hash'],final_equilibrium_hash=final,
            coefficient_rebind_required=final!=source['equilibrium_hash'])]
        if audit['potential_geometry']!=expected:raise NativeJobError('static geometry binding/rebind evidence mismatch')
        if type(audit['potential_geometry'][0]['coefficient_rebind_required']) is not bool:raise NativeJobError('static geometry flag must be boolean')
    return data


def decode_output(raw,snapshot,expected_hash,seal):
    if not seal or hashlib.sha256(raw).hexdigest()!=seal:raise NativeJobError('unsealed or changed static output')
    project_from_snapshot(snapshot)
    data=json.loads(raw.decode('utf-8'));_exact(data,{'schema_version','kind','input_hash','result'},'static output')
    if data['schema_version']!=1 or data['kind']!=KIND or data['input_hash']!=expected_hash or snapshot['input_hash']!=expected_hash:
        raise NativeJobError('static output identity mismatch')
    result=ResultRecord.from_dict(data['result'])
    if _jsonable_value(result.native_snapshot)!=_jsonable_value(snapshot):raise NativeJobError('static output changes input snapshot')
    return result


def _plain(value):
    if isinstance(value,Mapping):return {k:_plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_plain(v) for v in value]
    if hasattr(value,'tolist'):return _plain(value.tolist())
    return value


def run_job(snapshot,output_path,cancel_path):
    """Only worker path imports/exercises the numerical owner."""
    from datetime import datetime,timezone
    import time,uuid
    from .persistence import atomic_write_json
    from .worker import _emit
    start=time.perf_counter();partial=None
    cancelled=lambda:bool(cancel_path and cancel_path.exists())
    deadline=lambda:time.perf_counter()-start>snapshot['execution_limits']['wall_seconds']
    try:
        project=project_from_snapshot(snapshot)
        from .mechanics import prepare_native_run,prepare_native_nodes
        from anysolver import marine_coupled_equilibrium as solver
        if solver.API_VERSION!=SOLVER_API:raise NativeJobError('installed static solver API differs')
        model,states,_=prepare_native_run(project);nodes=prepare_native_nodes(project)
        _emit('PROGRESS 0.0000')
        partial=solver.solve_coupled_marine_equilibrium(model,states,nodes,
            solver.CoupledStaticSpec(**snapshot['static_spec']),cancel=lambda:cancelled() or deadline())
        if partial.status!='converged' or cancelled() or deadline():raise NativeJobError(partial.message)
        frames=[dict(kind='initial',fraction=0.)];poses=[states];nodeframes=[nodes]
        if partial.line_result is not None:
            for stage in partial.line_result.stages:
                frames.append(dict(kind=stage.name,fraction=stage.fraction));poses.append(states);nodeframes.append(stage.nodes)
        for stage in partial.stages:
            frames.append(dict(kind='coupled',fraction=stage.fraction));poses.append(stage.states);nodeframes.append(stage.nodes)
        channels={'equilibrium/fraction':[f['fraction'] for f in frames],
            'equilibrium/force_residual N':[],'equilibrium/moment_residual Nm':[]}
        for ss,nn in zip(poses,nodeframes):
            out=solver.evaluate_marine_static(model,ss,nn)
            w=out.force[:6*len(model.bodies)].reshape(-1,6)
            channels['equilibrium/force_residual N'].append(float(max(abs(w[:,:3]).max(),abs(out.force[6*len(model.bodies):]).max(initial=0.))))
            channels['equilibrium/moment_residual Nm'].append(float(abs(w[:,3:]).max()))
            if out.mooring is not None and out.mooring.contact_diagnostics is not None:
                from .contact_inputs import contact_channels,COMPONENTS
                samples=contact_channels(out.mooring.contact_diagnostics)
                for name in COMPONENTS:
                    f=out.mooring.force_components[name]
                    for i,bid in enumerate(model.body_ids):
                        identity=json.dumps(['mooring:'+name,bid],separators=(',',':'))
                        for j,axis in enumerate(('Fx','Fy','Fz','Mx','My','Mz')):samples[f'wrench{identity}/{axis}']=float(f[6*i+j])
                    for i in range(model.mooring.node_count):
                        for j,axis in enumerate(('x','y','z')):samples[f'mooring/node{i}/{name} {axis}']=float(f[6*len(model.bodies)+3*i+j])
                for key,value in samples.items():channels.setdefault(key,[]).append(value)
        data=dict(api_version=SOLVER_API,status='converged',frames=frames,audit=_plain(partial.audit))
        record=ResultRecord(result_id=str(uuid.uuid4()),kind=KIND,input_hash=snapshot['input_hash'],
            body_ids=model.body_ids,times=tuple(float(i) for i in range(len(frames))),
            positions={bid:[_plain(ss[bid].position) for ss in poses] for bid in model.body_ids},
            rotation_matrices={bid:[_plain(ss[bid].rotation) for ss in poses] for bid in model.body_ids},orientations={},
            channels=channels,native_snapshot=snapshot,line_nodes=None if nodes is None else [_plain(nn.positions) for nn in nodeframes],
            equilibrium_evidence=data,note='Static continuation stages, not time or dynamics; development/unqualified. Changed source geometry requires coefficient regeneration.',
            created_utc=datetime.now(timezone.utc).isoformat())
        if cancelled() or deadline():raise NativeJobError('late static cancellation/deadline')
        atomic_write_json(output_path,dict(schema_version=1,kind=KIND,input_hash=snapshot['input_hash'],result=record.to_dict()))
        _emit('OUTPUT_SHA256 '+hashlib.sha256(output_path.read_bytes()).hexdigest());_emit('PROGRESS 1.0000');_emit('DONE')
        return 0
    except Exception as error:
        line=None if partial is None else partial.line_result
        evidence=dict(status='cancelled' if cancelled() else 'failed',message=str(error),
            line_result=None if line is None else dict(status=line.status,message=line.message,audit=_plain(line.audit),
                accepted=[dict(kind=s.name,fraction=s.fraction,nodes=_plain(s.nodes.positions),
                    residual_norm=s.residual_norm,normalized_residual=s.normalized_residual,
                    iterations=s.iterations,least_norm_steps=s.least_norm_steps) for s in line.stages]),
            accepted=[] if partial is None else [dict(fraction=s.fraction,states={bid:dict(position=_plain(v.position),rotation=_plain(v.rotation)) for bid,v in s.states.items()},
                nodes=None if s.nodes is None else _plain(s.nodes.positions),metrics=_plain(s.metrics),
                iterations=s.iterations,least_norm_steps=s.least_norm_steps) for s in partial.stages])
        atomic_write_json(output_path.with_suffix('.incomplete.json'),evidence)
        import sys
        print('Static equilibrium failed: '+str(error),file=sys.stderr)
        return 3 if cancelled() else 4
