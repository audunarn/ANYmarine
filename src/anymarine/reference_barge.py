"""Portable saved recipe3 development studies; no geometry or GUI imports on load."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from itertools import islice
import hashlib,json,os
from pathlib import Path
from types import SimpleNamespace
from .services import ServiceError

DEFINITION='7a92cde10e542413eee16b4786122e201df9ebd1d81a9aaa9650f38710a50e6a'
DATASET='b42da7fa9a75689bf2a4d3b633dc22e96cfdd018bd03c82bb1e362353f263c89'
CASES=('coupled','upright','combined','opposite','raked')
MODEL_FILES=('definition.json','manifest.json','preview.svg','HULL.geometry.json','PUMPROOM.geometry.json',*(f'WB{i}.geometry.json' for i in range(1,9)),*(f'V{i}.geometry.json' for i in range(1,5)))
FILES=('dataset.json','calibration.json','upright.json','combined.json','opposite.json','raked.json','coupled-request.json','coupled-result.json',*('model/'+n for n in MODEL_FILES))
_VALIDATED_DATASET_CACHE={}  # Exact bytes + public validator identities, never version-only admission.
CAP_KEYS=('max_iterations','max_evaluations','max_owner_queries','max_wall_seconds','max_backtracks')

class ReferenceBargeError(ServiceError):
 def __init__(self,code,message):self.code=str(code);super().__init__(self.code+': '+message)

def _hash(b):return hashlib.sha256(b).hexdigest()
@contextmanager
def _owner():
 try:
  a=SimpleNamespace(**{key:import_module('anystability.'+value) for key,value in {'barge':'reference.barge','tanks':'reference.tanks','filling':'reference.filling','attitude':'reference.attitude_tanks','coupled':'reference.coupled','domain':'domain','pose':'conventions','v':'validation','p':'persistence','errors':'errors'}.items()})
 except ImportError as error:raise ReferenceBargeError('PROVIDER_UNAVAILABLE','Install matching ANYstability core and private ANYstability-local-reference add-on wheels in the local environment') from error
 try:yield a
 except a.errors.StabilityError as error:raise ReferenceBargeError(error.code,str(error)) from error
 except (OSError,KeyError,TypeError,ValueError,OverflowError) as error:raise ReferenceBargeError('STUDY_INVALID',str(error)) from error

def _path(path):
 p=Path(path).absolute()
 if '..' in p.parts or any(x.is_symlink() or getattr(x,'is_junction',lambda:False)() for x in (p,*p.parents)):
  raise ReferenceBargeError('IO_ERROR','symlink/junction/traversal study paths refused')
 return p

def _same(a,x,y,message):
 if a.v.canonical_bytes(x)!=a.v.canonical_bytes(y):raise ReferenceBargeError('SOURCE_STALE',message)

def _pose(a,p):return a.pose.Pose(p['heel_rad'],p['trim_rad'],p['origin_z_m'])
def _make_coupled(a,root,spec):
 return a.coupled.make_coupled_request(root/'model',a.domain.LoadingCondition.from_dict(spec['base_condition']),_pose(a,spec['initial_pose']),spec['pose_bounds'],spec['tank_template']['targets_m3'],spec['tank_template']['densities_kg_m3'],allow_reference=True,
  residual_tolerances=spec['residual_tolerances'],fd_steps=spec['fd_steps'],volume_tolerance_m3=spec['tank_template']['volume_tolerance_m3'],**{k:spec[k] for k in CAP_KEYS})

def _validated(root,a):
 raw={name:a.p.read_bytes(_path(root/name)) for name in FILES}
 if _hash(raw['dataset.json'])!=DATASET:raise ReferenceBargeError('SOURCE_STALE','dataset identity differs from accepted private evidence')
 index=a.v.decode_json(raw['dataset.json'])
 if set(index['files'])!=set(FILES)-{'dataset.json'}:raise ReferenceBargeError('STUDY_INVALID','dataset file inventory differs')
 for name,info in index['files'].items():
  if info!={'bytes':len(raw[name]),'sha256':_hash(raw[name])}:raise ReferenceBargeError('INTEGRITY_ERROR','dataset file differs: '+name)
 if sum(map(len,raw.values()))>10_000_000:raise ReferenceBargeError('WORK_BUDGET_EXCEEDED','dataset aggregate limit')
 key=(tuple(sorted(raw.items())),a.coupled.CoupledResult,getattr(a.coupled,'coupled_loading_condition',None),a.tanks.UprightCalibration,a.attitude.AttitudeLoadingBundle,a.filling.TankLoadingBundle)
 if key in _VALIDATED_DATASET_CACHE:return raw,a.v.decode_json(_VALIDATED_DATASET_CACHE[key])
 source=a.barge.load_barge(root/'model')
 if source['manifest']['definition_identity']!=DEFINITION:raise ReferenceBargeError('SOURCE_STALE','only accepted recipe3 is supported')
 calibration=a.tanks.load_upright_calibration(root/'calibration.json');reg=calibration.to_dict()['request']
 rebuilt=a.tanks.make_upright_request(root/'model',allow_reference=True,tank_ids=list(reg['tanks']),height_fractions=[.25,.45,.75],max_partial_queries=reg['max_partial_queries'],max_wall_seconds=reg['max_wall_seconds'])
 _same(a,rebuilt.to_dict(),reg,'calibration/source/request differs')
 records={'calibration':calibration.to_dict()}
 for name in ('upright','combined','opposite','raked'):
  obj=a.filling.load_loading_bundle(root/(name+'.json')) if name=='upright' else a.attitude.load_attitude_loading(root/(name+'.json'))
  d=obj.to_dict();records[name]=d
  if name=='upright':
   spec=d['fill_result']['request']
   request=a.filling.make_inverse_request(root/'model',calibration,spec['targets_m3'],allow_reference=True,**{k:spec[k] for k in ('volume_tolerance_m3','max_owner_queries','max_wall_seconds')})
  else:
   spec=d['result']['request']
   request=a.attitude.make_attitude_request(root/'model',_pose(a,spec['pose']),spec['targets_m3'],spec['densities_kg_m3'],allow_reference=True,**{k:spec[k] for k in ('volume_tolerance_m3','max_owner_queries','max_wall_seconds')})
  _same(a,request.to_dict(),spec,'loading request/source differs: '+name)
 request=a.coupled.load_coupled_request(root/'coupled-request.json');result=a.coupled.load_coupled_result(root/'coupled-result.json')
 _same(a,_make_coupled(a,root,request.to_dict()).to_dict(),request.to_dict(),'coupled request/source differs')
 _same(a,result.to_dict()['request'],request.to_dict(),'coupled result/request differs')
 if not callable(getattr(a.coupled,'coupled_loading_condition',None)):raise ReferenceBargeError('PROVIDER_UNAVAILABLE','ANYstability coupled_loading_condition public API is required')
 a.coupled.coupled_loading_condition(result,allow_reference=True)
 records.update(coupled=result.to_dict(),coupled_request=request.to_dict(),source_manifest=source['manifest'],definition=source['files']['definition.json'])
 # A stable snapshot, rather than a second read silently binding changed files.
 for name,b in raw.items():
  if a.p.read_bytes(root/name)!=b:raise ReferenceBargeError('SOURCE_STALE','dataset changed during validation')
 _VALIDATED_DATASET_CACHE.clear();_VALIDATED_DATASET_CACHE[key]=a.v.canonical_bytes(records)
 return raw,records

@dataclass(frozen=True)
class ReferenceBargeStudy:
 _root:Path
 _files:tuple
 _records:bytes
 _selection:bytes
 def __post_init__(self):
  with _owner() as a:
   if type(self._files) is not tuple or any(type(row) is not tuple or len(row)!=2 or type(row[0]) is not str or type(row[1]) is not bytes for row in self._files):
    raise ReferenceBargeError('STUDY_INVALID','immutable fixed file snapshot required')
   if len(self._files)!=len(FILES) or {n for n,_ in self._files}!=set(FILES):raise ReferenceBargeError('STUDY_INVALID','fixed file whitelist required')
   if type(self._records) is not bytes or type(self._selection) is not bytes:raise ReferenceBargeError('STUDY_INVALID','immutable record/selection bytes required')
   raw,records=_validated(_path(self._root),a)
   _same(a,{n:_hash(b) for n,b in self._files},{n:_hash(b) for n,b in raw.items()},'snapshot/source files differ')
   if a.v.canonical_bytes(records)!=self._records:raise ReferenceBargeError('STUDY_INVALID','derived records differ from public validated dataset')
   selection=a.v.decode_json(self._selection)
   if a.v.canonical_bytes(selection)!=self._selection:raise ReferenceBargeError('STUDY_INVALID','canonical selection required')
   _validate_selection(a,self._root,records,selection)
 def to_dict(self):return json.loads(self._selection)
 @property
 def identity(self):return _hash(self._selection)
 @property
 def selected_case(self):return self.to_dict()['selected_case']

def _validate_selection(a,root,records,selection):
 a.v.exact(selection,{'schema_version','dataset_identity','selected_case','request','result'},'reference study selection')
 if type(selection['schema_version']) is not int or selection['schema_version']!=1 or selection['dataset_identity']!=DATASET or selection['selected_case'] not in CASES:
  raise ReferenceBargeError('SOURCE_STALE','invalid dataset/case selection')
 if selection['request'] is not None:
  request=a.coupled.CoupledRequest(a.v.canonical_bytes(selection['request']))
  _same(a,_make_coupled(a,root,request.to_dict()).to_dict(),request.to_dict(),'edited request/source differs')
  _controls_checked(a,records['coupled_request'],request.to_dict())
 if selection['result'] is not None:
  result=a.coupled.CoupledResult(a.v.canonical_bytes(selection['result']))
  _same(a,result.to_dict()['request'],selection['request'] or records['coupled_request'],'new result/request differs')
  a.coupled.coupled_loading_condition(result,allow_reference=True)

def _study(a,root,raw,records,selection):
 return ReferenceBargeStudy(root,tuple(raw.items()),a.v.canonical_bytes(records),a.v.canonical_bytes(selection))

def load_reference_barge(path=None):
 if path is None:
  configured=os.environ.get('ANYMARINE_REFERENCE_BARGE_DIR')
  if not configured:raise ReferenceBargeError('LOCAL_DATA_UNAVAILABLE','Set ANYMARINE_REFERENCE_BARGE_DIR to the private local recipe3 dataset; no private assets are installed')
  candidate=Path(configured)
  if not candidate.is_absolute() or not candidate.is_dir():raise ReferenceBargeError('LOCAL_DATA_UNAVAILABLE','ANYMARINE_REFERENCE_BARGE_DIR must name an existing absolute private dataset directory')
 with _owner() as a:
  root=_path(path if path is not None else configured)
  raw,records=_validated(root,a)
  if path is not None and not (root/'study.json').is_file():raise ReferenceBargeError('INTEGRITY_ERROR','portable study commit marker missing')
  selection={'schema_version':1,'dataset_identity':DATASET,'selected_case':'coupled','request':None,'result':None}
  if (root/'study.json').exists():
   envelope=a.v.decode_json(a.p.read_bytes(root/'study.json'));a.v.exact(envelope,{'selection','integrity'},'study envelope')
   if a.v.content_hash(envelope['selection'])!=envelope['integrity']:raise ReferenceBargeError('INTEGRITY_ERROR','study selection integrity differs')
   selection=envelope['selection']
  expected=set(n.split('/')[0] for n in FILES)|({'study.json'} if (root/'study.json').exists() else set())
  if {p.name for p in islice(root.iterdir(),len(expected)+1)}!=expected:raise ReferenceBargeError('INTEGRITY_ERROR','unexpected/partial study files')
  return _study(a,root,raw,records,selection)

def select_reference_case(study,name):
 if not isinstance(study,ReferenceBargeStudy) or name not in CASES:raise ReferenceBargeError('INPUT_INVALID','unknown saved case')
 with _owner() as a:
  d=study.to_dict();d['selected_case']=name
  return ReferenceBargeStudy(study._root,study._files,study._records,a.v.canonical_bytes(d))

def reference_controls(study):
 r=study.to_dict()['request'] or json.loads(study._records)['coupled_request']
 return {**{k:r[k] for k in CAP_KEYS},'residual_tolerances':r['residual_tolerances'],'volume_tolerance_m3':r['tank_template']['volume_tolerance_m3']}

def _controls_checked(a,baseline,new):
 protected={k:v for k,v in baseline.items() if k not in (*CAP_KEYS,'residual_tolerances','tank_template')}
 _same(a,protected,{k:new[k] for k in protected},'fixed loading/pose/model controls changed')
 oldtank=baseline['tank_template'].copy();newtank=new['tank_template'].copy();oldvol=oldtank.pop('volume_tolerance_m3');newvol=newtank.pop('volume_tolerance_m3')
 oldwall=oldtank.pop('max_wall_seconds');newwall=newtank.pop('max_wall_seconds')
 if newwall!=new['max_wall_seconds'] or newwall>oldwall:raise ReferenceBargeError('INPUT_INVALID','invalid nested/global wall cap')
 _same(a,oldtank,newtank,'fixed tank quantities/source changed')
 for k in CAP_KEYS:
  if new[k]>baseline[k]:raise ReferenceBargeError('INPUT_INVALID','cannot raise registered work cap: '+k)
 for k,v in baseline['residual_tolerances'].items():
  if new['residual_tolerances'][k]>v:raise ReferenceBargeError('INPUT_INVALID','cannot weaken registered residual tolerance')
 if newvol>oldvol:raise ReferenceBargeError('INPUT_INVALID','cannot weaken liquid volume tolerance')

def with_reference_controls(study,controls):
 with _owner() as a:
  if isinstance(controls,str):controls=a.v.decode_json(controls.encode('utf8'))
  a.v.exact(controls,{*CAP_KEYS,'residual_tolerances','volume_tolerance_m3'},'coupled controls')
  original=json.loads(study._records)['coupled_request'];spec=json.loads(json.dumps(original))
  for k in CAP_KEYS:spec[k]=controls[k]
  spec['residual_tolerances']=controls['residual_tolerances'];spec['tank_template']['volume_tolerance_m3']=controls['volume_tolerance_m3']
  request=_make_coupled(a,study._root,spec);_controls_checked(a,original,request.to_dict())
  selection=study.to_dict()
  if (selection['request'] or original)!=request.to_dict():selection['result']=None
  selection['request']=None if request.to_dict()==original else request.to_dict()
  return ReferenceBargeStudy(study._root,study._files,study._records,a.v.canonical_bytes(selection))

def save_reference_barge(study,path):
 if not isinstance(study,ReferenceBargeStudy):raise ReferenceBargeError('INPUT_INVALID','typed reference study required')
 study.__post_init__() # Validate before destination reservation; never publish unchecked private fields.
 with _owner() as a:
  root=_path(path)
  if root.exists() or not root.parent.is_dir():raise ReferenceBargeError('IO_ERROR','select a NEW study directory under an existing parent')
  envelope=a.v.canonical_bytes({'selection':study.to_dict(),'integrity':study.identity})
  # Preflight foundation limits before reserving any directory.
  if len(envelope)>a.v.MAX_JSON_BYTES:raise ReferenceBargeError('WORK_BUDGET_EXCEEDED','study selection/result exceeds2MB')
  root.mkdir();(root/'model').mkdir()
  for name,b in study._files:a.p.atomic_write(root/name,b)
  a.p.atomic_write(root/'study.json',envelope) # Commit marker last, partial retained on failure.
  return load_reference_barge(root)

def reference_barge_view(study):
 with _owner() as a:
  records=json.loads(study._records);selection=study.to_dict();name=study.selected_case
  manifest=records['source_manifest'];definition=records['definition']
  if name=='coupled':
   result=selection['result'] or (records['coupled'] if selection['request'] is None else None)
   spec=selection['request'] or records['coupled_request'];base=spec['base_condition']
   state=None if result is None or result['accepted_evaluation'] is None else result['evaluations'][result['accepted_evaluation']]['state']
   condition=None if state is None else a.coupled.coupled_loading_condition(a.coupled.CoupledResult(a.v.canonical_bytes(result)),allow_reference=True).to_dict()
   summary=None if state is None else state['mass_summary'];pose=spec['initial_pose'] if state is None else state['pose']
   solutions={} if state is None else {n:{**x,'sample':None if x['query_index'] is None else result['queries'][x['query_index']]['sample']} for n,x in state['tank_diagnostics'].items()}
   targets=spec['tank_template']['targets_m3'];densities=spec['tank_template']['densities_kg_m3']
   status='unpaired_controls_edited' if result is None else result['status'];diagnostics=state
   mass_basis='fixed_target_volume_times_density; achieved volume is a geometric residual'
  else:
   load=records[name];base=load['base_condition'];condition=load['condition'];summary=load['mass_summary']
   result=load['fill_result'] if name=='upright' else load['result'];spec=result['request'];solutions=result['solutions']
   pose=None if name=='upright' else spec['pose'];status=result['status'];diagnostics=load.get('weight_wrench')
   targets=spec['targets_m3'];densities=load['densities_kg_m3'] if name=='upright' else spec['densities_kg_m3']
   mass_basis='density_times_achieved_volume' if name=='upright' else 'fixed_target_volume_times_density'
  return {'case':name,'study_identity':study.identity,'definition_identity':DEFINITION,'dataset_identity':DATASET,'status':status,
   'evidence':('ATTACHED OWNER RESULT EVIDENCE - execution details remain in the supplied Run journal' if name=='coupled' and selection['result'] is not None else 'LOCAL SAVED DEVELOPMENT EVIDENCE - not a new calculation'),'loading_label':'ILLUSTRATIVE dry fixture; not historical110t/as-built loading','mass_basis':mass_basis,
   'targets_m3':targets,'densities_kg_m3':densities,'condition':condition,'base_condition':base,'mass_summary':summary,'pose':pose,'tank_solutions':solutions,'diagnostics':diagnostics,
   'calibration_rows':records['calibration']['rows'],'capacities':{n:manifest['capacity_comparisons'][n] for n in a.tanks.TANKS},
   'controls':reference_controls(study),'source_manifest':manifest,'limitations':['Reference reconstruction assumed; production_qualified=false','WB1..8 storage only; pump/void excluded','No certified geometry/continuous GZ/criteria','Actual liquid CG only; no additional FSM','Cached coupled residual convergence is not stability/root certification','Upright/attitude loading states are not floating equilibria','Development reference Run; no certified geometry, stability proof or continuous-curve qualification. Use matching local verification evidence.']}

def reference_preview(study):return dict(study._files)['model/preview.svg']

def attach_coupled_result(study,path):
 with _owner() as a:
  if study.selected_case!='coupled':raise ReferenceBargeError('INPUT_INVALID','only coupled case supports Run')
  result=a.coupled.load_coupled_result(path);selection=study.to_dict();expected=selection['request'] or json.loads(study._records)['coupled_request']
  _same(a,result.to_dict()['request'],expected,'returned result belongs to another request')
  if result.to_dict()['status']!='converged':raise ReferenceBargeError('EQUILIBRIUM_UNRESOLVED','worker evidence is not converged; retained separately')
  a.coupled.coupled_loading_condition(result,allow_reference=True)
  selection['result']=result.to_dict()
  return ReferenceBargeStudy(study._root,study._files,study._records,a.v.canonical_bytes(selection))
