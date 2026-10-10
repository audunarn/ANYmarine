"""Bodyless static evidence import/replay. Qt/backend-free; never executes a solve.

SPDX-License-Identifier: MPL-2.0
"""
from dataclasses import dataclass
from collections.abc import Mapping
import hashlib,json,math
from .native_inputs import _record,_number,_plain
from .mooring_inputs import NativeMooringInputs,node_rows,_freeze

KIND='line_equilibrium_development'
VERSION='line_equilibrium_inputs_v1'


class _BodylessMooringData(NativeMooringInputs):
    _allow_empty_body_ids=True


def _controls(raw):
    data=_record(raw,{'force_scale','force_atol','force_rtol','load_fractions','max_iterations','max_line_search'},'line static controls')
    value={k:_number(data[k],k,positive=k=='force_scale') for k in ('force_scale','force_atol','force_rtol')}
    if min(value['force_atol'],value['force_rtol'])<0.:raise ValueError('negative static tolerance')
    limit=value['force_atol']+value['force_rtol']*value['force_scale']
    if not math.isfinite(limit) or limit<=0.:raise ValueError('invalid derived line static force limit')
    f=data['load_fractions']
    if not isinstance(f,(tuple,list)) or not 1<=len(f)<=32:raise ValueError('invalid static load fractions')
    f=tuple(_number(x,'load fraction',positive=True) for x in f)
    if f[-1]!=1. or any(b<=a for a,b in zip(f,f[1:])):raise ValueError('static fractions must increase to one')
    value['load_fractions']=f
    for key in ('max_iterations','max_line_search'):
        if type(data[key]) is not int or not 1<=data[key]<=100:raise ValueError('invalid static iteration/search cap')
        value[key]=data[key]
    return value


@dataclass(frozen=True)
class LineEquilibriumInputs:
    model: Mapping
    controls: Mapping

    def __post_init__(self):
        if not isinstance(self.model,Mapping) or self.model.get('body_ids') not in ([],()):
            raise ValueError('line equilibrium requires explicitly empty body identities')
        if 'environment' in self.model:raise ValueError('current-loaded line equilibrium replay is not supported by this schema')
        count=self.model.get('node_count')
        if type(count) is not int or count<1:raise ValueError('line equilibrium requires free nodes')
        # Shared coefficient/endpoint validation only; these zeros are never an
        # initial guess, serialized producing input or displayed geometry.
        checked=_BodylessMooringData(self.model,dict(positions=[[0.,0.,0.]]*count,velocities=[[0.,0.,0.]]*count))
        object.__setattr__(self,'model',checked.data)
        object.__setattr__(self,'controls',_freeze(_controls(self.controls)))

    @property
    def initial_nodes(self):return None

    def to_dict(self):return dict(version=VERSION,model=_plain(self.model),controls=_plain(self.controls))

    @classmethod
    def from_dict(cls,raw):
        data=_record(raw,{'version','model','controls'},'line equilibrium inputs')
        if data['version']!=VERSION:raise ValueError('unknown line equilibrium version')
        return cls(data['model'],data['controls'])


def _text_json(text,label):
    if not isinstance(text,str):raise ValueError(label+' must preserve original JSON text')
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError(label+' must be a JSON object')
    from .native_jobs import _finite_json
    _finite_json(value)
    return value


def _sha(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()


def project_from_snapshot(snapshot):
    from .domain import ProjectModel,AnalysisSettings,EquilibriumMetadata
    from .hashing import input_hash
    data=_record(snapshot,{'schema_version','kind','project','source_input_json','source_candidate_json','source_identity','input_hash'},'line equilibrium snapshot')
    if type(data['schema_version']) is not int or data['schema_version']!=1 or data['kind']!=KIND:raise ValueError('unknown line equilibrium snapshot')
    p=_record(data['project'],{'name','bodies','equilibrium','settings','results','linear_inputs','hydro_provenance','line_equilibrium_inputs'},'line equilibrium project')
    if p['bodies'] or p['results'] or p['linear_inputs'] is not None or p['hydro_provenance'] is not None:
        raise ValueError('line equilibrium snapshot must be bodyless and result-free')
    intent=LineEquilibriumInputs.from_dict(p['line_equilibrium_inputs'])
    project=ProjectModel(name=p['name'],bodies=(),equilibrium=EquilibriumMetadata.from_dict(p['equilibrium']),
                         settings=AnalysisSettings.from_dict(p['settings']),line_equilibrium_inputs=intent)
    if input_hash(project)!=data['input_hash']:raise ValueError('line equilibrium imported-project input hash mismatch')
    source=_text_json(data['source_input_json'],'source input')
    _record(source,{'model','states','controls','inputs','first','last','reference','force_scale'},'supported original line solver input')
    if source.get('states')!={} or source.get('model')!=_plain(intent.model) or source.get('controls')!=_plain(intent.controls):
        raise ValueError('line equilibrium source inputs differ from imported intent')
    identity=_record(data['source_identity'],{'case_id','input_sha256','accepted_sha256','candidate_sha256'},'source identity')
    if not isinstance(identity['case_id'],str) or not identity['case_id'].strip():raise ValueError('source case identity required')
    for key in ('input_sha256','accepted_sha256','candidate_sha256'):
        if not isinstance(identity[key],str) or len(identity[key])!=64 or any(c not in '0123456789abcdef' for c in identity[key]):raise ValueError('source SHA256 required')
    _text_json(data['source_candidate_json'],'source candidate')
    if _sha(data['source_input_json'])!=identity['input_sha256'] or _sha(data['source_candidate_json'])!=identity['candidate_sha256']:
        raise ValueError('original line evidence input/candidate seal mismatch')
    return project


def _accepted(snapshot,raw):
    producing=project_from_snapshot(snapshot);intent=producing.line_equilibrium_inputs
    evidence=_record(raw,{'source_accepted_json'},'line equilibrium evidence')
    _text_json(evidence['source_accepted_json'],'accepted source')
    if _sha(evidence['source_accepted_json'])!=snapshot['source_identity']['accepted_sha256']:
        raise ValueError('original accepted static evidence seal mismatch')
    accepted=_record(_text_json(evidence['source_accepted_json'],'accepted source'),{'status','stages','message','audit'},'accepted static source')
    if accepted['status']!='converged' or not isinstance(accepted['message'],str):raise ValueError('only converged line equilibrium evidence can be imported')
    expected=[('weight',f) for f in intent.controls['load_fractions']]
    if 'seabed_contact' in intent.model:expected.append(('contact',1.))
    stages=accepted['stages']
    if not isinstance(stages,list) or len(stages)!=len(expected):raise ValueError('complete original static stage inventory required')
    limit=intent.controls['force_atol']+intent.controls['force_rtol']*intent.controls['force_scale']
    rows=[];channels={name:[] for name in ('line equilibrium/residual N','line equilibrium/normalized residual',
                                       'line equilibrium/load fraction','line equilibrium/iterations','line equilibrium/least norm steps')}
    for stage,(name,fraction) in zip(stages,expected):
        _record(stage,{'name','fraction','nodes','residual_norm','normalized_residual','iterations','least_norm_steps'},'static stage')
        if stage['name']!=name or _number(stage['fraction'],'stage fraction')!=fraction:raise ValueError('static stage differs from producing controls')
        nodes=_record(stage['nodes'],{'positions','velocities','accelerations'},'static nodes')
        positions=node_rows(nodes['positions'],intent.model['node_count'],'static positions');rows.append(positions)
        for key in ('velocities','accelerations'):
            values=node_rows(nodes[key],intent.model['node_count'],'static '+key)
            if any(v!=0. for row in values for v in row):raise ValueError('static source cannot carry moving nodes')
        residual=_number(stage['residual_norm'],'static residual');normalized=_number(stage['normalized_residual'],'normalized residual')
        if residual<0. or normalized<0. or residual>limit or abs(normalized-residual/intent.controls['force_scale'])>1e-12*max(1.,abs(normalized)):
            raise ValueError('static stage residual/tolerance mismatch')
        for key in ('iterations','least_norm_steps'):
            if type(stage[key]) is not int or not 0<=stage[key]<=intent.controls['max_iterations']:raise ValueError('static iteration evidence mismatch')
        if stage['least_norm_steps']>stage['iterations']:raise ValueError('least norm steps exceed iterations')
        for key,value in zip(channels,(residual,normalized,fraction,stage['iterations'],stage['least_norm_steps'])):channels[key].append(float(value))
    audit=_record(accepted['audit'],{'residual_norm','normalized_residual','force_limit','component_error','min_tension','max_penetration','anchor_reactions','fixed_body_generalized_forces'},'final static audit')
    if audit['fixed_body_generalized_forces']!=[]:raise ValueError('bodyless final audit must have no body forces')
    anchor_count=sum(ep['type']=='fixed' for line in intent.model['lines'] for ep in line['endpoints'])
    node_rows(audit['anchor_reactions'],anchor_count,'final anchor reactions')
    for key in ('residual_norm','normalized_residual','force_limit','component_error','min_tension','max_penetration'):
        if _number(audit[key],'final '+key)<0.:raise ValueError('negative static final audit')
    if audit['residual_norm']!=stages[-1]['residual_norm'] or audit['normalized_residual']!=stages[-1]['normalized_residual']:
        raise ValueError('final audit differs from final accepted stage')
    if audit['force_limit']!=limit or audit['component_error']>limit:raise ValueError('static final force audit failed')
    return producing,accepted,tuple(rows),channels


def validate_and_freeze(record):
    from .domain import _freeze_json_value
    producing,accepted,rows,channels=_accepted(record.native_snapshot,record.equilibrium_evidence)
    if tuple(record.body_ids) or record.positions!={} or record.orientations!={} or record.rotation_matrices!={}:
        raise ValueError('line-only result must not contain body transforms')
    if record.input_hash!=record.native_snapshot['input_hash']:raise ValueError('line-only result snapshot hash mismatch')
    if any(isinstance(t,bool) for t in record.times) or tuple(record.times)!=tuple(range(len(rows))):raise ValueError('static replay abscissa must be original stage indices')
    actual=tuple(node_rows(frame,producing.line_equilibrium_inputs.model['node_count'],'stored static nodes') for frame in record.line_nodes)
    _record(record.channels,set(channels),'stored static channel inventory')
    for values in record.channels.values():
        if not isinstance(values,(tuple,list)):raise ValueError('stored static channels must be sample sequences')
        for value in values:_number(value,'stored static channel')
    if actual!=rows or _plain(record.channels)!=channels:raise ValueError('stored line geometry/diagnostics differ from original accepted source')
    if not isinstance(record.note,str) or not isinstance(record.created_utc,str):raise ValueError('line result metadata must be strings')
    for name,value in (('body_ids',()),('times',tuple(float(i) for i in range(len(rows)))),('line_nodes',rows),
                       ('positions',{}),('orientations',{}),('rotation_matrices',{}),('channels',channels),
                       ('native_snapshot',record.native_snapshot),('equilibrium_evidence',record.equilibrium_evidence)):
        object.__setattr__(record,name,_freeze_json_value(value,name))


def import_evidence(source_input,source_accepted,source_candidate,*,case_id):
    """Import existing exact solver artifacts, without changing/rerunning physics."""
    from .domain import ProjectModel,ResultRecord
    from .hashing import input_hash
    texts=[value.decode('utf-8') if isinstance(value,bytes) else value for value in (source_input,source_accepted,source_candidate)]
    raw=_text_json(texts[0],'source input')
    intent=LineEquilibriumInputs(raw['model'],raw['controls'])
    project=ProjectModel(name='Stored line equilibrium: '+case_id,bodies=(),line_equilibrium_inputs=intent)
    snapshot=dict(schema_version=1,kind=KIND,project=project.to_dict(),input_hash=input_hash(project),
                  source_input_json=texts[0],source_candidate_json=texts[2],
                  source_identity=dict(case_id=case_id,input_sha256=_sha(texts[0]),accepted_sha256=_sha(texts[1]),candidate_sha256=_sha(texts[2])))
    evidence=dict(source_accepted_json=texts[1]);_,accepted,rows,channels=_accepted(snapshot,evidence)
    record=ResultRecord(result_id='line-'+_sha(texts[1])[:20],kind=KIND,input_hash=snapshot['input_hash'],
                        body_ids=(),times=tuple(range(len(rows))),positions={},orientations={},channels=channels,
                        rotation_matrices={},native_snapshot=snapshot,line_nodes=rows,equilibrium_evidence=evidence,
                        note='Imported stored solver equilibrium, development and unqualified; no initial/coupled stage or per-stage forces recorded.')
    return project.with_edits(results=(record,))


def stages(record):return json.loads(record.equilibrium_evidence['source_accepted_json'])['stages']
def final_audit(record):return json.loads(record.equilibrium_evidence['source_accepted_json'])['audit']
