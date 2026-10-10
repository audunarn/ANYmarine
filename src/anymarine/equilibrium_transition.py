"""Atomic, headless static-to-dynamic adoption (development, unqualified).

No numerical work at import. Optional solver admission is used only during
adoption. Regenerators receive detached intent; they never receive an active
store or authority to save, run a backend, or change mechanical constitutive data.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from types import MappingProxyType
from .domain import (Body, BodyMesh, ProjectModel, ResultRecord, EquilibriumMetadata,
                     AnalysisSettings, _jsonable_value, _freeze_json_value)
from .hashing import input_hash, canonical_json
from .native_inputs import NativeMechanicalInputs, _number

VERSION='equilibrium_adoption_development_v1'
DEPENDENCIES=('panels','lids','frames','coefficients','radiation_kernel','excitation')

class EquilibriumAdoptionError(ValueError):
    """No adopted project was committed; original intent remains authoritative."""

class EquilibriumAdoptionCancelled(EquilibriumAdoptionError):
    pass

@dataclass(frozen=True)
class AdoptionDomain:
    water_depth: float
    waterline_z: float = 0.

    def __post_init__(self):
        object.__setattr__(self,'water_depth',_number(self.water_depth,'adoption water depth',positive=True))
        object.__setattr__(self,'waterline_z',_number(self.waterline_z,'adoption waterline'))

    def to_dict(self):return dict(water_depth=self.water_depth,waterline_z=self.waterline_z)

@dataclass(frozen=True)
class EquilibriumRegeneration:
    """Explicit regenerated development fluid and numerical lids, never a GO proof."""
    potential_flow: object = None
    lids: Mapping = None

    def __post_init__(self):
        from .potential_flow_inputs import NativePotentialFlowInputs
        if self.potential_flow is not None and type(self.potential_flow) is not NativePotentialFlowInputs:
            raise EquilibriumAdoptionError('regenerated potential flow requires explicit admitted development inputs')
        values={} if self.lids is None else dict(self.lids)
        if any(type(v) is not BodyMesh for v in values.values()):
            raise EquilibriumAdoptionError('regenerated lids require explicit BodyMesh records')
        object.__setattr__(self,'lids',MappingProxyType({k:BodyMesh.from_dict(v.to_dict()) for k,v in values.items()}))

def _digest(value):return hashlib.sha256(canonical_json(_jsonable_value(value)).encode()).hexdigest()

def _binding(project):
    data=project.native_inputs.to_dict();data.pop('run_spec')
    return (_digest([b.to_dict() for b in project.bodies]),_digest(data))

def validate_receipt(value):
    data=_jsonable_value(value)
    fields={'version','parent_input_hash','result_id','result_content_hash','geometry_hash',
            'regenerated_inputs_hash','domain','invalidated_dependencies','audit','status'}
    if not isinstance(data,dict) or set(data)!=fields or data['version']!=VERSION or data['status']!='development_unqualified':
        raise EquilibriumAdoptionError('unsupported/incomplete equilibrium adoption receipt')
    for name in ('parent_input_hash','result_content_hash','geometry_hash','regenerated_inputs_hash'):
        h=data[name]
        if type(h) is not str or len(h)!=64 or any(c not in '0123456789abcdef' for c in h):
            raise EquilibriumAdoptionError('invalid receipt digest: '+name)
    if type(data['result_id']) is not str or not data['result_id']:
        raise EquilibriumAdoptionError('missing adopted result identity')
    if data['invalidated_dependencies']!=list(DEPENDENCIES):
        raise EquilibriumAdoptionError('incomplete invalidated dependency inventory')
    domain=data['domain']
    if not isinstance(domain,dict) or set(domain)!={'water_depth','waterline_z'}:
        raise EquilibriumAdoptionError('invalid adoption domain')
    AdoptionDomain(**domain)
    audit=data['audit']
    names={'force_residual','moment_residual','force_limit','moment_limit','component_continuity_ratio'}
    if not isinstance(audit,dict) or set(audit)!=names:
        raise EquilibriumAdoptionError('invalid adoption balance audit')
    for name,value in audit.items():
        if _number(value,name)<0.:raise EquilibriumAdoptionError('negative adoption audit')
    if (audit['force_limit']<=0. or audit['moment_limit']<=0. or audit['force_residual']>audit['force_limit']
            or audit['moment_residual']>audit['moment_limit'] or audit['component_continuity_ratio']>1.):
        raise EquilibriumAdoptionError('adoption audit did not pass')
    return _freeze_json_value(data,'equilibrium adoption receipt')

def verify_adoption_binding(project):
    if project.equilibrium_adoption is None:return
    receipt=validate_receipt(project.equilibrium_adoption)
    if project.native_inputs is None or _binding(project)!=(receipt['geometry_hash'],receipt['regenerated_inputs_hash']):
        raise EquilibriumAdoptionError('adopted equilibrium dependencies are stale; regenerate and re-audit')
    _domain(project,AdoptionDomain(**dict(receipt['domain'])))

def _copy(project):
    d=project.to_dict()
    return ProjectModel(name=d['name'],bodies=tuple(Body.from_dict(b) for b in d['bodies']),
        equilibrium=EquilibriumMetadata.from_dict(d['equilibrium']),settings=AnalysisSettings.from_dict(d['settings']),
        results=tuple(ResultRecord.from_dict(r) for r in d['results']),
        native_inputs=NativeMechanicalInputs.from_dict(d['native_inputs']),equilibrium_adoption=d.get('equilibrium_adoption'))

def _domain(project,domain):
    """Intersection: fixed wetting, finite-depth centers, compliant contact unchanged."""
    import numpy as np
    if project.equilibrium.waterline_z!=domain.waterline_z:
        raise EquilibriumAdoptionError('adoption waterline does not match project equilibrium')
    bottom=domain.waterline_z-domain.water_depth
    def wet(points,name):
        z=np.asarray(points,dtype=float)[:,2]
        if np.any(z>=domain.waterline_z) or np.any(z<bottom):
            raise EquilibriumAdoptionError(name+' outside fully submerged finite-depth adoption domain; no wetting or penetration clamp')
    for b in project.bodies:
        wet(np.asarray(b.mesh.positions)@np.asarray(b.reference_orientation).T+np.asarray(b.reference_position),'body '+b.body_id)
    data=project.native_inputs.to_dict()
    def environment(env):
        if env['water_depth']!=domain.water_depth or env['still_water_level']!=domain.waterline_z:
            raise EquilibriumAdoptionError('fluid environment differs from declared adoption domain')
    if 'morison' in data:
        m=data['morison'];environment(m['environment'])
        for member in m['members']:
            ends=[]
            for name in ('end_a','end_b'):
                end=member[name];b=project.body(end['body_id'])
                ends.append(np.asarray(b.reference_position)+np.asarray(b.reference_orientation)@np.asarray(end['point_body']))
            wet(ends,'Morison member')
    if 'mooring' in data:
        m=data['mooring'];md=m['data']
        if md['still_water_level']!=domain.waterline_z:
            raise EquilibriumAdoptionError('mooring waterline differs from declared domain')
        if 'environment' in md:environment(md['environment'])
        if 'seabed_contact' in md and md['seabed_contact']['elevation']!=bottom:
            raise EquilibriumAdoptionError('contact and finite-depth seabed differ; combined domain unsupported')
        points=list(m['initial_nodes']['positions'])
        for line in md['lines']:
            for end in line['endpoints']:
                if end['type']=='fixed':points.append(end['position'])
                elif end['type']=='rigid':
                    b=project.body(end['body_id']);points.append(np.asarray(b.reference_position)+np.asarray(b.reference_orientation)@np.asarray(end['point_body']))
        if points:wet(points,'line centers/endpoints')

def adopt_equilibrium(project,result,*,domain,regenerate=None,should_cancel=None):
    """Return a complete immutable adopted project, or raise with no commit.

    The static result is development evidence. Physical ANYhydro source admission
    remains unsupported by the native route and is never inferred from this audit.
    Save is a separate existing atomic operation; callbacks receive no active store.
    """
    from .equilibrium_jobs import stale,validate_record
    if type(project) is not ProjectModel or type(result) is not ResultRecord or type(domain) is not AdoptionDomain:
        raise EquilibriumAdoptionError('explicit project, result and AdoptionDomain required')
    if project.native_inputs is None or result.kind!='equilibrium_development' or not project.bodies:
        raise EquilibriumAdoptionError('complete body equilibrium with native intent required')
    if project.native_inputs.wave_realization is not None:
        raise EquilibriumAdoptionError('shared-wave equilibrium adoption is unsupported; saved wave and excitation intent must be retained')
    if any(b.hydro_surfaces for b in project.bodies):
        raise EquilibriumAdoptionError('surface-force regeneration is unsupported; intent is not dropped')
    if stale(project,result):raise EquilibriumAdoptionError('static result does not match current immutable inputs')
    validate_record(result,project)
    parent_hash=input_hash(project);result_hash=_digest(result.to_dict())
    def cancel():
        if should_cancel is not None and should_cancel():raise EquilibriumAdoptionCancelled('equilibrium adoption cancelled; original project retained')
    cancel()
    detached=_copy(project)
    data=detached.native_inputs.to_dict()
    for b in detached.bodies:
        data['initial_states'][b.body_id]=dict(position=list(result.positions[b.body_id][-1]),
            rotation=[list(row) for row in result.rotation_matrices[b.body_id][-1]],velocity_world=[0.]*6)
    if 'mooring' in data:
        data['mooring']['initial_nodes']=dict(positions=_jsonable_value(result.line_nodes[-1]),
            velocities=[[0.,0.,0.] for _ in result.line_nodes[-1]])
    fluid_required='potential_flow' in data
    old_lids={b.body_id for b in detached.bodies if b.hydro_lid is not None}
    data.pop('potential_flow',None)
    bodies=tuple(b.with_edits(reference_position=data['initial_states'][b.body_id]['position'],
        reference_orientation=data['initial_states'][b.body_id]['rotation'],hydro_lid=None,hydro_lid_policy='free_surface_v1') for b in detached.bodies)
    candidate=detached.with_edits(bodies=bodies,native_inputs=NativeMechanicalInputs.from_dict(data),equilibrium_adoption=None,
        equilibrium=detached.equilibrium.with_edits(source='pending_static_adoption_development_v1'))
    _domain(candidate,domain)
    if (fluid_required or old_lids) and regenerate is None:
        raise EquilibriumAdoptionError('geometry changed: explicit fluid/lid/coefficient/kernel/excitation regeneration required')
    request=_freeze_json_value(dict(version=VERSION,parent_input_hash=parent_hash,result_id=result.result_id,
        result_content_hash=result_hash,domain=domain.to_dict(),invalidated_dependencies=list(DEPENDENCIES)), 'adoption regeneration request')
    cancel()
    regenerated=EquilibriumRegeneration() if regenerate is None else regenerate(_copy(candidate),request)
    if type(regenerated) is not EquilibriumRegeneration:
        raise EquilibriumAdoptionError('regenerator must return EquilibriumRegeneration, not arbitrary project edits')
    if (regenerated.potential_flow is not None)!=fluid_required or set(regenerated.lids)!=old_lids:
        raise EquilibriumAdoptionError('regenerated dependency inventory does not match original declared intent')
    if fluid_required:data['potential_flow']=regenerated.potential_flow.to_dict()
    bodies=tuple(b.with_edits(hydro_lid=regenerated.lids.get(b.body_id),
        hydro_lid_policy=detached.body(b.body_id).hydro_lid_policy if b.body_id in old_lids else 'free_surface_v1') for b in candidate.bodies)
    candidate=candidate.with_edits(bodies=bodies,native_inputs=NativeMechanicalInputs.from_dict(data))
    cancel();_domain(candidate,domain)
    # Public stationary assemblies preserve each physical component separately.
    from .mechanics import prepare_native_run,prepare_native_nodes
    from anysolver.marine_rigid import RigidBodyState
    from anysolver.marine_coupled_equilibrium import evaluate_marine_static
    import numpy as np
    old_model,_,_=prepare_native_run(detached)
    new_model,new_states,_=prepare_native_run(candidate)
    nodes=prepare_native_nodes(candidate)
    final_states={bid:RigidBodyState(position=result.positions[bid][-1],anchor=result.rotation_matrices[bid][-1]) for bid in result.body_ids}
    before=evaluate_marine_static(old_model,final_states,nodes)
    after=evaluate_marine_static(new_model,new_states,nodes)
    spec=result.native_snapshot['static_spec'];n=6*len(project.bodies)
    force_limit=spec['force_atol']+spec['relative_tolerance']*spec['force_scale']
    moment_limit=spec['moment_atol']+spec['relative_tolerance']*spec['moment_scale']
    moments=np.arange(len(after.force))<n;moments &= np.arange(len(after.force))%6>=3
    force_error=float(np.max(np.abs(after.force[~moments]),initial=0.));moment_error=float(np.max(np.abs(after.force[moments]),initial=0.))
    if force_error>force_limit or moment_error>moment_limit:
        raise EquilibriumAdoptionError('regenerated equilibrium does not balance physical forces and moments')
    if set(before.components)!=set(after.components):
        raise EquilibriumAdoptionError('regeneration changed physical force ownership')
    continuity=0.
    for name,old in before.components.items():
        new=after.components[name];old=np.asarray(old);new=np.asarray(new)
        limits=np.where(moments,spec['moment_atol'],spec['force_atol'])+spec['relative_tolerance']*np.maximum(np.abs(old),np.abs(new))
        if np.any(limits<=0.):raise EquilibriumAdoptionError('component continuity requires positive registered absolute tolerance')
        continuity=max(continuity,float(np.max(np.abs(old-new)/limits,initial=0.)))
    if continuity>1.:raise EquilibriumAdoptionError('regenerated component forces changed; restoring preload cannot be erased or hidden')
    geometry_hash,native_hash=_binding(candidate)
    receipt=dict(version=VERSION,parent_input_hash=parent_hash,result_id=result.result_id,result_content_hash=result_hash,
        geometry_hash=geometry_hash,regenerated_inputs_hash=native_hash,domain=domain.to_dict(),invalidated_dependencies=list(DEPENDENCIES),
        status='development_unqualified',audit=dict(force_residual=force_error,moment_residual=moment_error,
            force_limit=force_limit,moment_limit=moment_limit,component_continuity_ratio=continuity))
    kept=detached.results
    if result.result_id not in {r.result_id for r in kept}:kept=kept+(ResultRecord.from_dict(result.to_dict()),)
    elif any(r.result_id==result.result_id and _digest(r.to_dict())!=result_hash for r in kept):
        raise EquilibriumAdoptionError('stored result identity has conflicting contents')
    adopted=candidate.with_edits(results=kept,equilibrium_adoption=receipt,
        equilibrium=candidate.equilibrium.with_edits(source='adopted_static_development_v1',description='Adopted solved development equilibrium; regenerated and statically audited, unqualified.'))
    adopted=_copy(adopted);verify_adoption_binding(adopted)
    cancel()
    if input_hash(project)!=parent_hash or _digest(result.to_dict())!=result_hash:
        raise EquilibriumAdoptionError('original input/result identity changed during regeneration')
    return adopted
