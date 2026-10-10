"""Qt-free named mooring intent and transport checks; ANYsolver owns physics."""
from dataclasses import dataclass
from collections.abc import Mapping
from types import MappingProxyType
import math
from .native_inputs import _record,_identifier,_number,_vector,_plain

VERSION='native_mooring_inputs_v1'
FORCE_COMPONENTS=('elastic_force','damping_force','weight','inertia')
HYDRO_VERSION='marine_mooring_hydrodynamics_v1'
HYDRO_COMPONENTS=('drag_normal','drag_tangential','fluid_inertia','added_mass')
HYDRO_CHANNELS=('relative_drag_power','added_mass_kinetic_energy')


def _freeze(value):
    if isinstance(value,Mapping):return MappingProxyType({k:_freeze(v) for k,v in value.items()})
    if isinstance(value,(tuple,list)):return tuple(_freeze(v) for v in value)
    return value


def node_rows(value,count,label):
    if not isinstance(value,(tuple,list)) or len(value)!=count:raise ValueError(label+' node inventory mismatch')
    return tuple(_vector(row,3,label) for row in value)


@dataclass(frozen=True)
class NativeMooringInputs:
    data: Mapping
    initial_nodes: Mapping

    def __post_init__(self):
        fields={'api_version','body_ids','lines','node_count','gravity_world','fluid_density','still_water_level'}
        from .contact_inputs import VERSION as CONTACT_VERSION,seabed_from_data
        contact=isinstance(self.data,Mapping) and self.data.get('api_version')==CONTACT_VERSION
        hydro=isinstance(self.data,Mapping) and (self.data.get('api_version')==HYDRO_VERSION or (contact and 'environment' in self.data))
        if hydro:fields.add('environment')
        if contact:fields.add('seabed_contact')
        data=_record(self.data,fields,'mooring data')
        if data['api_version'] not in ('marine_mooring_v1',HYDRO_VERSION,CONTACT_VERSION):raise ValueError('unsupported mooring API')
        count=data['node_count']
        if type(count) is not int or count<0:raise ValueError('nonnegative node_count required')
        ids=data['body_ids']
        if not isinstance(ids,(tuple,list)) or len(ids)!=len(set(ids)) or (not ids and not getattr(self,'_allow_empty_body_ids',False)):raise ValueError('ordered body identities required')
        for bid in ids:_identifier(bid,'mooring body')
        gravity=_vector(data['gravity_world'],3,'line gravity')
        density=_number(data['fluid_density'],'fluid density',positive=True)
        swl=_number(data['still_water_level'],'still water level')
        if not isinstance(data['lines'],(tuple,list)) or not data['lines']:raise ValueError('named lines required')
        lines=[];line_ids=set();nodes=[]
        names=('rest_lengths','axial_rigidity','axial_damping','mass_per_length','displaced_area')
        for raw in data['lines']:
            line_fields={'line_id','endpoints',*names}
            if hydro and isinstance(raw,Mapping) and 'hydrodynamics' in raw:line_fields.add('hydrodynamics')
            if contact and isinstance(raw,Mapping) and 'contact_diameters' in raw:line_fields.add('contact_diameters')
            line=_record(raw,line_fields,'line')
            lid=_identifier(line['line_id'],'line_id')
            if lid in line_ids:raise ValueError('duplicate line identity')
            line_ids.add(lid);endpoints=[]
            if not isinstance(line['endpoints'],(list,tuple)) or len(line['endpoints'])<2:raise ValueError('endpoint inventory required')
            for ep in line['endpoints']:
                if not isinstance(ep,Mapping):raise ValueError('named endpoint required')
                kind=ep.get('type')
                if kind=='fixed':
                    _record(ep,{'type','position'},'fixed endpoint');endpoints.append(dict(type=kind,position=_vector(ep['position'],3,'anchor')))
                elif kind=='rigid':
                    _record(ep,{'type','body_id','point_body'},'rigid endpoint')
                    if ep['body_id'] not in ids:raise ValueError('unknown fairlead body')
                    endpoints.append(dict(type=kind,body_id=ep['body_id'],point_body=_vector(ep['point_body'],3,'fairlead')))
                elif kind=='node':
                    _record(ep,{'type','node_index'},'node endpoint');index=ep['node_index']
                    if type(index) is not int or not 0<=index<count:raise ValueError('invalid node index')
                    nodes.append(index);endpoints.append(dict(type=kind,node_index=index))
                else:raise ValueError('unknown endpoint type')
            item=dict(line_id=lid,endpoints=endpoints)
            for name in names:
                values=line[name]
                if not isinstance(values,(tuple,list)) or len(values)!=len(endpoints)-1:raise ValueError('segment coefficient inventory mismatch')
                item[name]=tuple(_number(v,name,positive=name in ('rest_lengths','axial_rigidity','mass_per_length')) for v in values)
                if any(v<0. for v in item[name]):raise ValueError('nonnegative coefficients required')
            if 'hydrodynamics' in line:
                sections=line['hydrodynamics'];keys={'diameter','drag_normal','drag_tangential',
                    'added_mass_normal','added_mass_tangential','inertia_normal','inertia_tangential'}
                if not isinstance(sections,(list,tuple)) or len(sections)!=len(endpoints)-1:
                    raise ValueError('one hydrodynamic section per segment required')
                item['hydrodynamics']=[]
                for section in sections:
                    section=_record(section,keys,'line hydrodynamic section')
                    values={name:_number(v,name,positive=name=='diameter') for name,v in section.items()}
                    if any(v<0. for v in values.values()):raise ValueError('negative line hydro coefficient')
                    item['hydrodynamics'].append(values)
            if 'contact_diameters' in line:
                values=line['contact_diameters']
                if not isinstance(values,(tuple,list)) or len(values)!=len(endpoints)-1:raise ValueError('contact diameter inventory mismatch')
                item['contact_diameters']=tuple(_number(v,'contact diameter',positive=True) for v in values)
                if 'hydrodynamics' in item and any(d!=section['diameter'] for d,section in zip(item['contact_diameters'],item['hydrodynamics'])):
                    raise ValueError('contact and hydrodynamic diameters must agree')
            lines.append(item)
        if sorted(nodes)!=list(range(count)):raise ValueError('each node must occur exactly once')
        initial=_record(self.initial_nodes,{'positions','velocities'},'initial nodes')
        object.__setattr__(self,'initial_nodes',_freeze({name:node_rows(initial[name],count,name) for name in initial}))
        saved=dict(api_version=data['api_version'],body_ids=list(ids),lines=lines,node_count=count,
                   gravity_world=gravity,fluid_density=density,still_water_level=swl)
        if hydro:
            from .environment_inputs import environment_from_data
            if not any('hydrodynamics' in line for line in lines):raise ValueError('hydro environment requires line hydrodynamic sections')
            env=environment_from_data(data['environment'])
            if env['density']!=density or env['still_water_level']!=swl:raise ValueError('line/environment density or datum differs')
            if any(gravity) and gravity!=(0.,0.,-env['gravity']):raise ValueError('selected line/environment gravity differs')
            saved['environment']=env
        if contact:
            if not any('contact_diameters' in line for line in lines):raise ValueError('seabed law requires contact diameter intent')
            bed=seabed_from_data(data['seabed_contact'])
            if bed['elevation']>=swl:raise ValueError('seabed must lie below still water')
            if hydro and bed['elevation']!=swl-env['water_depth']:raise ValueError('seabed/environment depth or datum differs')
            saved['seabed_contact']=bed
        object.__setattr__(self,'data',_freeze(saved))

    @property
    def has_hydrodynamics(self):return 'environment' in self.data

    @property
    def has_contact(self):return 'seabed_contact' in self.data

    @property
    def force_components(self):
        from .contact_inputs import COMPONENTS
        return FORCE_COMPONENTS+(HYDRO_COMPONENTS if self.has_hydrodynamics else ())+(COMPONENTS if self.has_contact else ())

    def to_dict(self):return dict(version=VERSION,data=_plain(self.data),initial_nodes=_plain(self.initial_nodes))

    @classmethod
    def from_dict(cls,data):
        _record(data,{'version','data','initial_nodes'},'mooring inputs')
        if data['version']!=VERSION:raise ValueError('unsupported mooring inputs version')
        return cls(data['data'],data['initial_nodes'])


def validate_node_step(current,previous,dt):
    """Audit the v1 fixed Newmark beta=1/4, gamma=1/2 transport contract.

    The SI unit floors and 1e-12 relative allowance cover serialization/arithmetic
    roundoff; this is not a force-law or scientific qualification tolerance.
    """
    for q0,v0,a0,q1,v1,a1 in zip(previous['positions'],previous['velocities'],previous['accelerations'],
                                current['positions'],current['velocities'],current['accelerations']):
        for axis in range(3):
            q=q0[axis]+dt*v0[axis]+.25*dt*dt*(a0[axis]+a1[axis])
            v=v0[axis]+.5*dt*(a0[axis]+a1[axis])
            for actual,expected,label in ((q1[axis],q,'position'),(v1[axis],v,'velocity')):
                if not math.isfinite(expected) or abs(actual-expected)>1e-12*max(1.,abs(actual),abs(expected)):
                    raise ValueError('stored node '+label+' violates fixed-step kinematics')


def validate_frame(frame,inputs,index,tolerance):
    """Validate finite inventories and node force residual; no line physics."""
    count=inputs.data['node_count'];nodes=_record(frame['nodes'],{'positions','velocities','accelerations'},'accepted nodes')
    nodes={name:node_rows(value,count,name) for name,value in nodes.items()}
    if index==0 and any(nodes[name]!=inputs.initial_nodes[name] for name in ('positions','velocities')):
        raise ValueError('initial node state differs from producing snapshot')
    fields={'tensions','lengths','elastic_tensions','elastic_energy','kinetic_energy','damping_dissipation',
            'endpoint_forces','anchor_reactions','body_derivative_steps','node_force_components'}
    if inputs.has_hydrodynamics:fields.update(HYDRO_CHANNELS)
    if inputs.has_contact:fields.add('contact')
    diag=_record(frame['mooring_diagnostics'],fields,'mooring diagnostics')
    segments=sum(len(line['rest_lengths']) for line in inputs.data['lines'])
    for name in ('tensions','lengths','elastic_tensions'):
        values=diag[name]
        if not isinstance(values,(list,tuple)) or len(values)!=segments:raise ValueError('segment result inventory mismatch')
        for value in values:
            if _number(value,name)<0. or (name=='lengths' and value==0.):raise ValueError('invalid segment result')
    for name in ('elastic_energy','kinetic_energy','damping_dissipation')+(HYDRO_CHANNELS if inputs.has_hydrodynamics else ()):
        if _number(diag[name],name)<0.:raise ValueError('negative line energy/dissipation')
    endpoints=sum(len(line['endpoints']) for line in inputs.data['lines'])
    anchors=sum(ep['type']=='fixed' for line in inputs.data['lines'] for ep in line['endpoints'])
    node_rows(diag['endpoint_forces'],endpoints,'endpoint forces');node_rows(diag['anchor_reactions'],anchors,'anchor reactions')
    bodies={ep['body_id'] for line in inputs.data['lines'] for ep in line['endpoints'] if ep['type']=='rigid'}
    steps=_record(diag['body_derivative_steps'],bodies,'body derivative steps')
    for value in steps.values():
        if not isinstance(value,(list,tuple)) or len(value)!=2 or any(_number(x,'derivative step',positive=True)>2e-6 for x in value):
            raise ValueError('invalid derivative settings')
    components=_record(diag['node_force_components'],set(inputs.force_components),'node force components')
    components={name:node_rows(value,count,name) for name,value in components.items()}
    residual=math.hypot(*(sum(components[name][i][j] for name in inputs.force_components) for i in range(count) for j in range(3)))
    if residual>tolerance:raise ValueError('node force residual exceeds producing run tolerance')
    if inputs.has_contact:
        from .contact_inputs import validate_contact_diagnostics
        validate_contact_diagnostics(diag['contact'],inputs,frame['positions'],frame['rotations'],nodes['positions'])
    return nodes
