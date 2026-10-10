"""Qt-free transport for isolated, explicit equilibrium adoption.

Numerical stationary audits occur only in run_job. Transport validation never
regenerates coefficients or launches an analysis. No physical source admission.
"""
from pathlib import Path
import hashlib,json,time
from .domain import (ProjectModel,Body,BodyMesh,ResultRecord,EquilibriumMetadata,
                     AnalysisSettings,_jsonable_value)
from .hashing import input_hash,canonical_json
from .native_inputs import NativeMechanicalInputs
from .native_jobs import NativeJobError,_exact,_real,_vector,project_from_snapshot as native_project
from .equilibrium_transition import (AdoptionDomain,EquilibriumRegeneration,
    EquilibriumAdoptionError,EquilibriumAdoptionCancelled,verify_adoption_binding)

KIND='equilibrium_adoption_development'

def _digest(data):return hashlib.sha256(canonical_json(_jsonable_value(data)).encode()).hexdigest()

def _project(data):
    data=_jsonable_value(data)
    if data.get('linear_inputs') is not None or data.get('hydro_provenance') is not None or 'line_equilibrium_inputs' in data:
        raise EquilibriumAdoptionError('adoption transport requires native-only project intent; other configured analysis intent is not discarded')
    p=ProjectModel(name=data['name'],bodies=tuple(Body.from_dict(b) for b in data['bodies']),
        equilibrium=EquilibriumMetadata.from_dict(data['equilibrium']),settings=AnalysisSettings.from_dict(data['settings']),
        native_inputs=NativeMechanicalInputs.from_dict(data['native_inputs']),
        results=tuple(ResultRecord.from_dict(r) for r in data['results']),equilibrium_adoption=data.get('equilibrium_adoption'))
    if p.to_dict()!=data:raise EquilibriumAdoptionError('adoption project contains discarded or coerced intent')
    stripped=p.to_dict();stripped['results']=[]
    proxy=dict(schema_version=1,kind='mechanical_development',solver_api='marine_mechanical_v1',
        input_hash=input_hash(p),project=stripped,execution_limits=dict(max_frames=20001,wall_seconds=60.))
    native_project(proxy)
    return p

def regeneration_from_data(data):
    """Public artifact format: exactly potential_flow and body-keyed local lids."""
    from .potential_flow_inputs import NativePotentialFlowInputs
    data=_jsonable_value(_exact(data,{'potential_flow','lids'},'adoption regeneration artifact'))
    if not isinstance(data['lids'],dict):raise EquilibriumAdoptionError('regenerated lids require a body-keyed mapping')
    for lid in data['lids'].values():
        _exact(lid,{'positions','triangles'},'regenerated lid')
        if not isinstance(lid['positions'],(list,tuple)) or not isinstance(lid['triangles'],(list,tuple)):
            raise EquilibriumAdoptionError('regenerated lid positions/triangles must be arrays')
        for point in lid['positions']:_vector(point,3,'regenerated lid position')
        for triangle in lid['triangles']:
            if not isinstance(triangle,(list,tuple)) or len(triangle)!=3 or any(type(i) is not int for i in triangle):
                raise EquilibriumAdoptionError('regenerated lid triangles require three integer indices, never booleans')
    return EquilibriumRegeneration(None if data['potential_flow'] is None else NativePotentialFlowInputs.from_dict(data['potential_flow']),
        {k:BodyMesh.from_dict(v) for k,v in data['lids'].items()})

def regeneration_to_data(regeneration):
    if regeneration is None:regeneration=EquilibriumRegeneration()
    if type(regeneration) is not EquilibriumRegeneration:raise EquilibriumAdoptionError('explicit EquilibriumRegeneration required')
    return dict(potential_flow=None if regeneration.potential_flow is None else regeneration.potential_flow.to_dict(),
        lids={k:v.to_dict() for k,v in regeneration.lids.items()})

def snapshot_records(snapshot):
    _exact(snapshot,{'schema_version','kind','project','project_input_hash','result','domain',
        'regeneration','execution_limits','input_hash'},'adoption snapshot')
    d=_jsonable_value(snapshot)
    if type(d['schema_version']) is not int or d['schema_version']!=1 or d['kind']!=KIND:
        raise EquilibriumAdoptionError('unsupported adoption snapshot')
    limits=_exact(d['execution_limits'],{'wall_seconds'},'adoption limits')
    if not 0.<_real(limits['wall_seconds'],'adoption wall_seconds')<=600.:
        raise EquilibriumAdoptionError('adoption wall_seconds must be in (0,600]')
    if d['input_hash']!=_digest({k:v for k,v in d.items() if k!='input_hash'}):
        raise EquilibriumAdoptionError('adoption snapshot input hash mismatch')
    project=_project(d['project']);result=ResultRecord.from_dict(d['result'])
    if input_hash(project)!=d['project_input_hash']:raise EquilibriumAdoptionError('adoption parent identity mismatch')
    from .equilibrium_jobs import stale,validate_record
    if result.kind!='equilibrium_development' or stale(project,result):
        raise EquilibriumAdoptionError('select a complete current static equilibrium result')
    validate_record(result,project)
    if project.native_inputs.wave_realization is not None or any(b.hydro_surfaces for b in project.bodies):
        raise EquilibriumAdoptionError('shared-wave or attached-surface adoption is unsupported; intent is retained')
    if any(r.result_id==result.result_id and r.to_dict()!=result.to_dict() for r in project.results):
        raise EquilibriumAdoptionError('selected static result differs from its stored identity')
    domain=AdoptionDomain(**d['domain']);regeneration=regeneration_from_data(d['regeneration'])
    if (regeneration.potential_flow is not None)!=(project.native_inputs.potential_flow is not None):
        raise EquilibriumAdoptionError('explicit regenerated potential-flow dependencies required')
    if set(regeneration.lids)!={b.body_id for b in project.bodies if b.hydro_lid is not None}:
        raise EquilibriumAdoptionError('explicit regenerated lid inventory required')
    return project,result,domain,regeneration

def input_snapshot(project,result,*,domain,regeneration=None,wall_seconds=30.):
    if type(domain) is not AdoptionDomain:raise EquilibriumAdoptionError('explicit AdoptionDomain required')
    data=dict(schema_version=1,kind=KIND,project=project.to_dict(),project_input_hash=input_hash(project),
        result=result.to_dict(),domain=domain.to_dict(),regeneration=regeneration_to_data(regeneration),
        execution_limits=dict(wall_seconds=wall_seconds))
    data['input_hash']=_digest(data);snapshot_records(data)
    return _jsonable_value(data)

def _expected_project(project,result,domain,regeneration,receipt):
    """Exact permitted mutation inventory; no stationary solver on the GUI thread."""
    if (receipt['parent_input_hash']!=input_hash(project) or receipt['result_id']!=result.result_id
            or receipt['result_content_hash']!=_digest(result.to_dict()) or dict(receipt['domain'])!=domain.to_dict()):
        raise EquilibriumAdoptionError('adoption receipt changes input/result/domain identity')
    spec=result.native_snapshot['static_spec']
    for group in ('force','moment'):
        limit=spec[group+'_atol']+spec['relative_tolerance']*spec[group+'_scale']
        if receipt['audit'][group+'_limit']!=limit:
            raise EquilibriumAdoptionError('adoption audit changes original static tolerance')
    data=project.native_inputs.to_dict()
    for bid in result.body_ids:
        data['initial_states'][bid]=dict(position=list(result.positions[bid][-1]),
            rotation=[list(row) for row in result.rotation_matrices[bid][-1]],velocity_world=[0.]*6)
    if 'mooring' in data:
        data['mooring']['initial_nodes']=dict(positions=_jsonable_value(result.line_nodes[-1]),
            velocities=[[0.,0.,0.] for _ in result.line_nodes[-1]])
    if regeneration.potential_flow is not None:data['potential_flow']=regeneration.potential_flow.to_dict()
    bodies=tuple(b.with_edits(reference_position=data['initial_states'][b.body_id]['position'],
        reference_orientation=data['initial_states'][b.body_id]['rotation'],hydro_lid=regeneration.lids.get(b.body_id),
        hydro_lid_policy=b.hydro_lid_policy if b.body_id in regeneration.lids else 'free_surface_v1') for b in project.bodies)
    results=project.results if any(r.result_id==result.result_id for r in project.results) else (*project.results,result)
    return project.with_edits(bodies=bodies,native_inputs=NativeMechanicalInputs.from_dict(data),results=results,
        equilibrium=project.equilibrium.with_edits(source='adopted_static_development_v1',
            description='Adopted solved development equilibrium; regenerated and statically audited, unqualified.'),equilibrium_adoption=receipt)

def load_output(path,snapshot,seal):
    raw=Path(path).read_bytes()
    if not seal or hashlib.sha256(raw).hexdigest()!=seal:
        raise EquilibriumAdoptionError('unsealed or changed adoption output')
    project,result,domain,regeneration=snapshot_records(snapshot)
    d=json.loads(raw.decode('utf-8'))
    _exact(d,{'schema_version','kind','input_hash','project','project_hash'},'adoption output')
    if type(d['schema_version']) is not int or d['schema_version']!=1 or d['kind']!=KIND or d['input_hash']!=snapshot['input_hash']:
        raise EquilibriumAdoptionError('adoption output run identity mismatch')
    q=_project(d['project'])
    if q.equilibrium_adoption is None or input_hash(q)!=d['project_hash']:
        raise EquilibriumAdoptionError('adoption output lacks a bound receipt')
    expected=_expected_project(project,result,domain,regeneration,q.equilibrium_adoption)
    if q.to_dict()!=expected.to_dict():raise EquilibriumAdoptionError('adoption output changes protected project intent')
    verify_adoption_binding(q)
    return q

def current_submission(project,snapshot):
    """Protect all edits/results, including display metadata outside input_hash."""
    return project is not None and project.to_dict()==_jsonable_value(snapshot['project'])

def run_job(snapshot,output_path,cancel_path):
    from .services import ApplicationService
    from .persistence import atomic_write_json
    from .worker import _emit
    start=time.perf_counter();output_path=Path(output_path)
    def cancelled():
        if time.perf_counter()-start>snapshot['execution_limits']['wall_seconds']:
            raise EquilibriumAdoptionError('adoption deadline exceeded')
        return bool(cancel_path and Path(cancel_path).exists())
    try:
        project,result,domain,regeneration=snapshot_records(snapshot)
        _emit('PROGRESS 0.1')
        q=ApplicationService().adopt_equilibrium(project,result,domain=domain,
            regenerate=lambda candidate,request:regeneration,should_cancel=cancelled)
        payload=dict(schema_version=1,kind=KIND,input_hash=snapshot['input_hash'],project=q.to_dict(),project_hash=input_hash(q))
        if cancelled():raise EquilibriumAdoptionCancelled('adoption cancelled before output commit')
        atomic_write_json(output_path,payload)
        seal=hashlib.sha256(output_path.read_bytes()).hexdigest()
        load_output(output_path,snapshot,seal)
        if cancelled():raise EquilibriumAdoptionCancelled('adoption cancelled during output validation')
        _emit('OUTPUT_SHA256 '+seal);_emit('PROGRESS 1.0')
        return 0
    except Exception as error:
        is_cancelled=isinstance(error,EquilibriumAdoptionCancelled)
        atomic_write_json(output_path.with_suffix('.failure.json'),dict(kind=KIND,
            input_hash=snapshot.get('input_hash'),status='cancelled' if is_cancelled else 'failed',message=str(error)))
        _emit(('CANCELLED ' if is_cancelled else 'FAILED ')+str(error))
        return 3 if is_cancelled else 4
