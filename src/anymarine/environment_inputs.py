"""Pure shared marine environment records; public ANYloads owns evaluation."""
from .native_inputs import _record,_number,_vector


def _sequence(value,name):
    if not isinstance(value,(list,tuple)):raise ValueError(name+' must be a sequence')
    return value


def environment_from_data(raw):
    env=_record(raw,{'api_version','water_depth','components','current','density','gravity',
                     'still_water_level','acceleration_convention'},'marine environment')
    if env['api_version']!='marine_loads_v1' or env['acceleration_convention']!='eulerian_local':
        raise ValueError('unsupported marine environment contract or acceleration convention')
    environment=dict(api_version=env['api_version'],acceleration_convention=env['acceleration_convention'])
    for key in ('water_depth','density','gravity','still_water_level'):
        environment[key]=_number(env[key],key,positive=key!='still_water_level')
    components=[]
    for c in _sequence(env['components'],'Airy components'):
        c=_record(c,{'amplitude','omega','direction_degrees','phase'},'Airy component')
        components.append({key:_number(value,key) for key,value in c.items()})
    environment['components']=components
    current=_record(env['current'],{'depths','velocities'},'current profile')
    environment['current']={
        'depths':[_number(item,'current depth [m]') for item in _sequence(current['depths'],'current depths')],
        'velocities':[_vector(item,3,'current velocity [m/s]') for item in _sequence(current['velocities'],'current velocities')]}
    return environment
