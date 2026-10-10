# SPDX-License-Identifier: MPL-2.0
"""Qt-free, source-discriminated replay of saved HAMS point responses.

Portable visualization bundles are content-bound development records, not
producer attestation or completed mechanics results. No producer executes here.
"""
from dataclasses import dataclass,field
from pathlib import Path
import hashlib,json,math,stat
import numpy as np
from .domain import ProjectModel,Body,EquilibriumMetadata,AnalysisSettings
from .hashing import input_hash

SOURCE='hams_canonical_point_fields_v1'
FORMAT='anymarine_stored_hams_wave_bundle_v1'
_FILES={'project.json','coefficients.nc','fields.json'}
_BUNDLE=Path(__file__).parent/'examples/hams-wave-points'

def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _regular(path,*,directory=False):
    info=path.lstat()
    return (not getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400)
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))
def _plain(value):
    from collections.abc import Mapping
    if isinstance(value,Mapping):return {k:_plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [_plain(v) for v in value]
    return value

def _project_data(project):
    if project.results or project.native_inputs is not None or project.linear_inputs is not None or project.hydro_provenance is not None or project.line_equilibrium_inputs is not None or project.equilibrium_adoption is not None:
        raise ValueError('Saved wave bundle is a producing display project, not a mechanics workflow')
    return dict(name=project.name,bodies=[b.to_dict() for b in project.bodies],equilibrium=project.equilibrium.to_dict(),settings=project.settings.to_dict())

def _project_load(value):
    if type(value) is not dict or set(value)!={'name','bodies','equilibrium','settings'}:raise ValueError('Saved wave producing project schema differs')
    project=ProjectModel(name=value['name'],bodies=tuple(Body.from_dict(b) for b in value['bodies']),equilibrium=EquilibriumMetadata.from_dict(value['equilibrium']),settings=AnalysisSettings.from_dict(value['settings']))
    if _project_data(project)!=value:raise ValueError('Saved wave producing project discarded intent')
    return project

@dataclass(frozen=True)
class HamsWaveReplay:
    project: ProjectModel
    fields: object
    parent_hash: str
    _dataset: object=field(repr=False,compare=False)

    @property
    def source_kind(self):return SOURCE
    @property
    def project_hash(self):return input_hash(self.project)
    @property
    def surface_indices(self):return tuple(i for i,p in enumerate(self.fields.points) if p[2]==0.)
    @property
    def vertices(self):return np.asarray([self.fields.points[i] for i in self.surface_indices],dtype=float)

    def matches(self,project):return input_hash(project)==self.project_hash

    def response(self,component,*,frequency=0,heading=0,amplitude=.3,phase=0.,dof=0,displacement=.01):
        """Elevation at saved surface nodes only; radiation multiplies m/rad."""
        f=self.fields
        if type(frequency) is not int or not 0<=frequency<len(f.omegas) or type(heading) is not int or not 0<=heading<len(f.headings):raise ValueError('Stored wave coordinate index differs')
        for name,value in (('amplitude',amplitude),('phase',phase),('displacement',displacement)):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(name+' must be finite')
        if amplitude<0:raise ValueError('Nonnegative incident amplitude required')
        indices=list(self.surface_indices)
        if component=='radiation':
            if type(dof) is not int or not 0<=dof<len(f.dof_keys):raise ValueError('Stored radiation DOF differs')
            values=f.radiation[frequency,indices,dof]*displacement
        elif component=='scattered':values=(f.total[frequency,heading,indices]-f.incident[frequency,heading,indices])*amplitude
        elif component in ('incident','total'):values=getattr(f,component)[frequency,heading,indices]*amplitude
        else:raise ValueError('Unknown saved HAMS component')
        values=values*np.exp(1j*phase)
        if not np.isfinite(values).all():raise ValueError('Saved wave response exceeds finite representation')
        return values

    def positions(self,component,time,**selection):
        if isinstance(time,bool) or not isinstance(time,(int,float)) or not math.isfinite(time):raise ValueError('Finite saved wave time required')
        values=self.response(component,**selection);omega=self.fields.omegas[selection.get('frequency',0)]
        angle=-omega*time
        if not math.isfinite(angle):raise ValueError('Saved wave time exceeds finite representation')
        points=self.vertices.copy();points[:,2]=np.real(values*np.exp(1j*angle));return points

def bind_hams_wave_replay(project,dataset,fields):
    """Prove parent geometry and response coordinates before display/persistence."""
    import anyhydro
    from anyhydro.schema import validate_hydro_dataset
    from anyhydro.frames import coefficient_frames
    from anyhydro.hashing import dataset_content_hash
    from anyhydro.hams_fields import HamsFieldData
    from .hydro import _prove_surface_inventory,expected_world_mesh,expected_world_lid
    _project_data(project);validate_hydro_dataset(dataset)
    if type(fields) is not HamsFieldData:raise ValueError('Canonical source-specific HAMS fields required')
    parent=dataset_content_hash(dataset)
    if parent!=fields.parent_dataset_hash:raise ValueError('Saved wave parent differs')
    provenance=dataset.attrs.get('wamit_import')
    if isinstance(provenance,str):provenance=json.loads(provenance)
    if not isinstance(provenance,dict) or not isinstance(provenance.get('native_source'),dict):raise ValueError('Saved wave requires parent HAMS source provenance')
    meta=_plain(fields.metadata);native=provenance['native_source']
    pairs={'source_profile':'profile','executable_sha256':'executable_sha256','input_sha256':'input_sha256',
           'request_hash':'source_request_hash','source_body_order':'source_body_order','source_frames':'source_frames',
           'reference_length_m':'reference_length_m','rho':'source_rho','gravity':'source_gravity'}
    depth='infinite' if provenance.get('source_depth_kind')=='infinite' and provenance.get('source_water_depth') is None else provenance.get('source_water_depth')
    if (any(meta[k]!=provenance.get(v) for k,v in pairs.items())
        or meta['source_tree_sha256']!=native.get('source_tree_sha256')
        or meta['raw_field_sha256']!=native.get('field_file_sha256')
        or [list(p) for p in fields.points]!=native.get('source_field_points')
        or meta['water_depth']!=depth or meta['request_hash']!=dataset.attrs.get('request_hash')):
        raise ValueError('Saved wave field and parent source declarations disagree')
    # Wave values live at fixed WORLD points. The coefficient frame may have
    # changed without changing those points or the producing hull geometry.
    # Keep the legacy mechanical-import geometry proof unchanged.
    _prove_surface_inventory(project,dataset)
    if tuple(str(b) for b in dataset.body.values)!=tuple(b.body_id for b in project.bodies) or project.equilibrium.waterline_z!=0.:
        raise ValueError('Saved wave producing body order/waterline differs')
    identity=dataset.attrs['body_identity']
    if isinstance(identity,str):identity=json.loads(identity)
    for body in project.bodies:
        if expected_world_mesh(body).mesh_hash!=identity[body.body_id]['mesh_hash']:raise ValueError('Saved wave producing hull geometry differs')
        lid=expected_world_lid(body,waterline_z=0.) if body.hydro_lid is not None else None
        if (None if lid is None else lid.mesh_hash)!=identity[body.body_id]['lid_mesh_hash']:raise ValueError('Saved wave producing lid geometry differs')
    frames=_plain(fields.metadata['source_frames'])
    history=dataset.attrs.get('reference_transformations',[])
    if isinstance(history,str):history=json.loads(history)
    if not isinstance(history,list):raise ValueError('Unknown saved wave frame history')
    from anyhydro.frames import CoefficientFrame
    for entry in history:
        if not isinstance(entry,dict) or entry.get('kind')!='coefficient_frame_change' or entry.get('map_id')!='equilibrium_linear_v1' or entry.get('body_id') not in frames:raise ValueError('Unknown saved wave frame transformation')
        key=entry['body_id'];old=CoefficientFrame(entry['old_axes'],tuple(entry['old_reference_center']));new=CoefficientFrame(entry['new_axes'],tuple(entry['new_reference_center']))
        if old.to_json()!=frames[key]:raise ValueError('Saved wave frame history is discontinuous')
        frames[key]=new.to_json()
    if frames!=_plain(fields.metadata['target_frames']):raise ValueError('Saved wave target frame history differs')
    if (tuple(b.body_id for b in project.bodies)!=tuple(fields.metadata['target_body_order'])
        or tuple(str(k) for k in dataset.radiating_dof.values)!=fields.dof_keys
        or tuple(float(w) for w in dataset.omega.values)!=fields.omegas
        or tuple(float(h) for h in dataset.wave_direction.values)!=fields.headings
        or {k:v.to_json() for k,v in coefficient_frames(dataset).items()}!=_plain(fields.metadata['target_frames'])):
        raise ValueError('Saved wave body/DOF/frame/frequency coordinates differ')
    record=HamsWaveReplay(project,fields,parent,dataset.copy(deep=True))
    if dataset_content_hash(record._dataset)!=parent:raise ValueError('Saved wave parent changed while capturing snapshot')
    if not record.surface_indices:raise ValueError('No saved surface elevation points; submerged pressure cannot become height')
    return record

def save_hams_wave_bundle(directory,project,dataset,fields):
    """Publish a new portable visualization bundle; incomplete writes are unsealed."""
    from anyhydro.persistence import save_hydro_dataset
    from anyhydro.hams_fields import save_hams_fields
    from .persistence import atomic_write_json
    record=bind_hams_wave_replay(project,dataset,fields);path=Path(directory)
    path.mkdir(parents=True,exist_ok=False)
    atomic_write_json(path/'project.json',_project_data(record.project));save_hydro_dataset(record._dataset,path/'coefficients.nc');save_hams_fields(record.fields,path/'fields.json')
    manifest=dict(format=FORMAT,source_kind=SOURCE,status='development_unqualified',project_hash=record.project_hash,parent_hash=record.parent_hash,field_hash=fields.content_hash(),files={name:_sha(path/name) for name in sorted(_FILES)})
    atomic_write_json(path/'bundle.json',manifest)
    return load_hams_wave_bundle(path)

def load_hams_wave_bundle(directory):
    from anyhydro.persistence import load_hydro_dataset
    from anyhydro.hams_fields import load_hams_fields
    from anyhydro.errors import HydroError
    path=Path(directory)
    try:
        if not _regular(path,directory=True) or {p.name for p in path.iterdir()}!=_FILES|{'bundle.json'}:raise ValueError('Incomplete saved wave bundle inventory or reparse directory')
        for name in _FILES|{'bundle.json'}:
            p=path/name
            if not _regular(p):raise ValueError('Saved wave artifact must be a regular local file, not a reparse point')
        m=json.loads((path/'bundle.json').read_text(encoding='utf-8'))
        if (type(m) is not dict or set(m)!={'format','source_kind','status','project_hash','parent_hash','field_hash','files'}
            or m['format']!=FORMAT or m['source_kind']!=SOURCE or m['status']!='development_unqualified' or type(m['files']) is not dict or set(m['files'])!=_FILES):raise ValueError('Unknown saved wave source/schema/status')
        if any(_sha(path/name)!=m['files'][name] for name in _FILES):raise ValueError('Saved wave artifact bytes changed')
        project=_project_load(json.loads((path/'project.json').read_text(encoding='utf-8')))
        dataset=load_hydro_dataset(path/'coefficients.nc');fields=load_hams_fields(path/'fields.json',expected_parent_hash=m['parent_hash'])
        record=bind_hams_wave_replay(project,dataset,fields)
        if record.project_hash!=m['project_hash'] or record.parent_hash!=m['parent_hash'] or fields.content_hash()!=m['field_hash']:raise ValueError('Saved wave bundle identities differ')
        return record
    except (OSError,KeyError,TypeError,ValueError,HydroError) as error:raise ValueError('Stored HAMS waves refused: '+str(error)) from error

def save_hams_wave_replay(directory,record):
    return save_hams_wave_bundle(directory,record.project,record._dataset,record.fields)

def load_hams_wave_example():return load_hams_wave_bundle(_BUNDLE)
