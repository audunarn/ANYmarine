"""Pure data envelopes for shared waves; public owners admit physics lazily."""
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from .native_inputs import _record,_number,_vector,_plain

SERIES_VERSION='hydro_excitation_series_development_v1'
WAVE_VERSION='marine_wave_realization_v1'
SERIES_FIELDS={'api_version','dof_keys','omegas','weighted_real','weighted_imag',
    'wave_realization_hash','hydro_content_hash','source_qualification_status',
    'amplitude_normalization','phase_origin_xy'}
WAVE_FIELDS={'api_version','phase_algorithm','components','source_kind','seed',
    'spectral_bins','directions','jonswap'}


def freeze(value):
    if isinstance(value,Mapping):return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value,(list,tuple)):return tuple(freeze(v) for v in value)
    return value


def sequence(value,name):
    if not isinstance(value,(list,tuple)) or not value:raise ValueError(name+' needs a nonempty sequence')
    return value


def vector(value,name,positive=False):
    return tuple(_number(x,name,positive=positive) for x in sequence(value,name))


def excitation_intent(raw,keys):
    raw=_record(raw,SERIES_FIELDS,'excitation series')
    if (raw['api_version']!=SERIES_VERSION or raw['source_qualification_status']!='development_fixture'
            or raw['amplitude_normalization']!='unit_incident_elevation_amplitude_m'):
        raise ValueError('only the named development excitation series is supported')
    if tuple(sequence(raw['dof_keys'],'excitation DOFs'))!=tuple(keys):
        raise ValueError('excitation series full DOF order must match frames')
    if _vector(raw['phase_origin_xy'],2,'phase origin')!=(0.,0.):
        raise ValueError('only global zero phase origin supported')
    for name in ('wave_realization_hash','hydro_content_hash'):
        if type(raw[name]) is not str or re.fullmatch('[0-9a-f]{64}',raw[name]) is None:
            raise ValueError('canonical excitation source/wave hashes required')
    values=dict(raw);values['omegas']=vector(raw['omegas'],'omega',positive=True)
    for name in ('weighted_real','weighted_imag'):
        rows=sequence(raw[name],name)
        if len(rows)!=len(values['omegas']):raise ValueError('excitation term inventory mismatch')
        values[name]=tuple(_vector(row,len(keys),name) for row in rows)
    values['dof_keys']=tuple(keys);values['phase_origin_xy']=(0.,0.)
    return freeze(values)


@dataclass(frozen=True)
class NativeWaveInputs:
    data: Mapping

    def __post_init__(self):
        raw=_record(self.data,WAVE_FIELDS,'wave realization')
        if raw['api_version']!=WAVE_VERSION:raise ValueError('unsupported wave realization version')
        values=dict(raw);components=[]
        for c in sequence(raw['components'],'wave components'):
            c=_record(c,{'amplitude','omega','direction_degrees','phase'},'wave component')
            value={k:_number(x,k,positive=k=='omega') for k,x in c.items()}
            if value['amplitude']<0.:raise ValueError('nonnegative wave amplitude required')
            components.append(value)
        values['components']=components
        if raw['source_kind']=='declared_components':
            if raw['phase_algorithm']!='declared_materialized' or any(raw[k] is not None for k in ('seed','spectral_bins','directions','jonswap')):
                raise ValueError('declared components cannot claim seeded spectrum metadata')
        elif raw['source_kind']=='seeded_spectral_bins':
            if (type(raw['seed']) is not int or raw['seed']<0
                    or raw['phase_algorithm']!='PCG64_uniform_0_2pi_materialized_v1'):
                raise ValueError('named phase algorithm and nonnegative integer seed required')
            bins=_record(raw['spectral_bins'],{'omegas','density_per_radian','bandwidths'},'spectral bins')
            values['spectral_bins']={k:vector(v,k,positive=k!='density_per_radian') for k,v in bins.items()}
            if len({len(v) for v in values['spectral_bins'].values()})!=1:
                raise ValueError('spectral vectors need equal lengths')
            directions=_record(raw['directions'],{'headings_radians','weights'},'directions')
            values['directions']={k:vector(v,k) for k,v in directions.items()}
            if len(values['directions']['headings_radians'])!=len(values['directions']['weights']):
                raise ValueError('direction vectors need equal lengths')
            if raw['jonswap'] is not None:
                spectrum=_record(raw['jonswap'],{'significant_height','peak_period','gamma','sigma_low','sigma_high','gravity'},'JONSWAP')
                values['jonswap']={k:_number(v,k,positive=k!='significant_height') for k,v in spectrum.items()}
        else:raise ValueError('unsupported wave realization source kind')
        object.__setattr__(self,'data',freeze(values))

    def to_dict(self):return _plain(self.data)

    @classmethod
    def from_dict(cls,data):return cls(data)

    @property
    def content_hash(self):
        return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
