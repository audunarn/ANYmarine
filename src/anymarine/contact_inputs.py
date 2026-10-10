"""Qt/backend-free contact intent and evidence transport; solver owns physics."""
import json
import math
from collections.abc import Mapping
from .native_inputs import _record,_number,_vector

VERSION='marine_mooring_contact_v1'
BED_VERSION='marine_seabed_contact_v1'
BED_CONTINUOUS_VERSION='marine_seabed_contact_v2'
COMPONENTS=('contact_elastic','contact_damping')
SCALARS=('max_penetration','potential_energy','dissipation')
DIAG_FIELDS={'segment_indices','endpoint_indices','gaps','active',*SCALARS,'seabed_reaction'}


def seabed_from_data(raw):
    version=raw.get('api_version') if isinstance(raw,Mapping) else None
    if version not in (BED_VERSION,BED_CONTINUOUS_VERSION):raise ValueError('unsupported seabed contact API')
    fields={'api_version','elevation','normal_stiffness','normal_damping'}
    if version==BED_CONTINUOUS_VERSION:fields.add('activation_depth_m')
    data=_record(raw,fields,'seabed contact')
    result=dict(api_version=version,elevation=_number(data['elevation'],'seabed elevation'),
                normal_stiffness=_number(data['normal_stiffness'],'normal stiffness',positive=True),
                normal_damping=_number(data['normal_damping'],'normal damping'))
    if result['normal_damping']<0.:raise ValueError('negative contact damping')
    if version==BED_CONTINUOUS_VERSION:
        result['activation_depth_m']=_number(data['activation_depth_m'],'activation depth',positive=True)
    return result


def contact_lumps(inputs):
    """Deterministic segment-end identities, each with its own explicit diameter."""
    lumps=[];segment=0;endpoint=0
    for line in inputs.data['lines']:
        for i in range(len(line['rest_lengths'])):
            if 'contact_diameters' in line:
                for j in (i,i+1):
                    lumps.append((segment+i,endpoint+j,line['contact_diameters'][i],line['endpoints'][j]))
        segment+=len(line['rest_lengths']);endpoint+=len(line['endpoints'])
    return tuple(lumps)


def endpoint_world(ep,positions,rotations,nodes):
    if ep['type']=='fixed':return ep['position']
    if ep['type']=='node':return nodes[ep['node_index']]
    bid=ep['body_id'];p=_vector(positions[bid],3,'body origin');R=rotations[bid]
    if not isinstance(R,(tuple,list)) or len(R)!=3:raise ValueError('rotation row inventory mismatch')
    rows=tuple(_vector(row,3,'rotation row') for row in R)
    return tuple(p[i]+sum(rows[i][j]*ep['point_body'][j] for j in range(3)) for i in range(3))


def _same(actual,expected,label):
    if not math.isfinite(expected) or abs(actual-expected)>1e-12*max(1.,abs(actual),abs(expected)):
        raise ValueError(label+' differs from producing geometry/inventory')


def validate_contact_diagnostics(raw,inputs,positions,rotations,nodes):
    data=_record(raw,DIAG_FIELDS,'contact diagnostics');lumps=contact_lumps(inputs)
    for key,column in (('segment_indices',0),('endpoint_indices',1)):
        values=data[key]
        if (not isinstance(values,(list,tuple)) or any(type(i) is not int for i in values)
                or tuple(values)!=tuple(lump[column] for lump in lumps)):
            raise ValueError('contact lump identity inventory mismatch')
    if any(not isinstance(data[key],(tuple,list)) or len(data[key])!=len(lumps) for key in ('gaps','active')):
        raise ValueError('contact lump sample inventory mismatch')
    gaps=tuple(_number(v,'contact gap') for v in data['gaps'])
    if any(type(flag) is not bool for flag in data['active']):raise ValueError('boolean contact active flags required')
    active=tuple(data['active']);bed=inputs.data['seabed_contact']['elevation']
    for gap,flag,(_,_,diameter,ep) in zip(gaps,active,lumps):
        point=endpoint_world(ep,positions,rotations,nodes)
        _same(gap,point[2]-bed-.5*diameter,'contact gap')
        if flag and gap>=0.:raise ValueError('active contact cannot have a nonnegative surface gap')
    numbers={name:_number(data[name],name) for name in SCALARS}
    if any(value<0. for value in numbers.values()):raise ValueError('negative contact energy/dissipation/penetration')
    _same(numbers['max_penetration'],max(0.,-min(gaps)),'maximum contact penetration')
    reaction=_vector(data['seabed_reaction'],3,'seabed reaction')
    if reaction[0]!=0. or reaction[1]!=0. or reaction[2]>0.:raise ValueError('invalid frictionless world seabed reaction')
    return dict(segment_indices=tuple(data['segment_indices']),endpoint_indices=tuple(data['endpoint_indices']),
                gaps=gaps,active=active,seabed_reaction=reaction,**numbers)


def contact_channels(diag):
    values={'mooring/contact/'+name:diag[name] for name in SCALARS}
    values.update({f'mooring/contact/seabed_reaction {axis}':v for axis,v in zip(('x','y','z'),diag['seabed_reaction'])})
    for i,(gap,flag) in enumerate(zip(diag['gaps'],diag['active'])):
        values[f'mooring/contact/lump{i}/gap']=gap
        values[f'mooring/contact/lump{i}/active']=float(flag)
    return values


def required_contact_channels(inputs):
    names=['mooring/contact/'+name for name in SCALARS]
    names+=[f'mooring/contact/seabed_reaction {axis}' for axis in ('x','y','z')]
    names+=[f'mooring/contact/lump{i}/{name}' for i in range(len(contact_lumps(inputs))) for name in ('gap','active')]
    names+=[f'mooring/node{node}/{component} {axis}' for node in range(inputs.data['node_count'])
            for component in COMPONENTS for axis in ('x','y','z')]
    for bid in inputs.data['body_ids']:
        for component in COMPONENTS:
            identity=json.dumps(['mooring:'+component,bid],separators=(',',':'))
            names.extend(f'wrench{identity}/{axis}' for axis in ('Fx','Fy','Fz','Mx','My','Mz'))
    return tuple(names)


def validate_stored_contact(inputs,channels,times,positions,rotations,nodes):
    if any(name not in channels or len(channels[name])!=len(times) for name in required_contact_channels(inputs)):
        raise ValueError('stored contact channel inventory mismatch')
    lumps=contact_lumps(inputs)
    for index in range(len(times)):
        raw={name:channels['mooring/contact/'+name][index] for name in SCALARS}
        flags=[channels[f'mooring/contact/lump{i}/active'][index] for i in range(len(lumps))]
        if any(flag not in (0.,1.) for flag in flags):raise ValueError('stored contact flag must be zero or one')
        raw.update(segment_indices=[l[0] for l in lumps],endpoint_indices=[l[1] for l in lumps],
                   gaps=[channels[f'mooring/contact/lump{i}/gap'][index] for i in range(len(lumps))],
                   active=[bool(flag) for flag in flags],seabed_reaction=[channels[f'mooring/contact/seabed_reaction {axis}'][index] for axis in ('x','y','z')])
        validate_contact_diagnostics(raw,inputs,{bid:rows[index] for bid,rows in positions.items()},
                                     {bid:rows[index] for bid,rows in rotations.items()},nodes[index])
