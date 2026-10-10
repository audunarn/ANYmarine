"""Pure-data native potential-flow development declaration, no solver imports."""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
import math
import re

from .native_inputs import _record, _identifier, _number, _vector, _matrix, _plain
from .result_rotations import validated_rotation_matrix

POTENTIAL_FLOW_VERSION = 'marine_potential_flow_development_v1'
POTENTIAL_FLOW_ID = 'potential-flow-development'
FIELDS = {'api_version','frames','equilibrium_hash','dt','kernel','added_mass_infinite',
          'restoring','baseline','excitation_real','excitation_imag','omega','motion_bounds',
          'source_identity','source_kind','force_basis','hydrostatics_role','gravity_included','prehistory'}


@dataclass(frozen=True)
class NativePotentialFlowInputs:
    """Immutable JSON intent; public ANYsolver owns final mathematical admission."""
    data: Mapping

    def __post_init__(self):
        fields = FIELDS | ({'excitation_series'} if 'excitation_series' in self.data else set())
        data = _record(self.data,fields,'potential-flow development inputs')
        declarations = {'api_version':POTENTIAL_FLOW_VERSION,'source_kind':'development_fixture',
                        'force_basis':'equilibrium_h_gradient','hydrostatics_role':'incremental_buoyancy',
                        'prehistory':'zero_before_t0'}
        for name,value in declarations.items():
            if type(data[name]) is not str or data[name] != value:
                raise ValueError(f'potential flow {name}: unsupported declaration')
        if data['gravity_included'] is not False:
            raise ValueError('potential-flow coefficients must exclude gravity')
        if not isinstance(data['equilibrium_hash'],str) or not re.fullmatch(r'[0-9a-f]{64}',data['equilibrium_hash']):
            raise ValueError('potential flow requires an exact equilibrium binding hash')
        _identifier(data['source_identity'],'potential-flow source identity')
        if not isinstance(data['frames'],(list,tuple)) or not data['frames']:
            raise ValueError('potential-flow equilibrium frames required')
        frame_fields = {'body_id','equilibrium_position','equilibrium_rotation','coefficient_axes','reference_point_body'}
        frames,ids = [],set()
        for raw in data['frames']:
            f = _record(raw,frame_fields,'equilibrium frame')
            bid = _identifier(f['body_id'],'equilibrium body identity')
            if bid in ids or '::' in bid:
                raise ValueError('duplicate or invalid potential-flow body identity')
            ids.add(bid)
            frames.append(MappingProxyType({'body_id':bid,
                'equilibrium_position':_vector(f['equilibrium_position'],3,'equilibrium origin [m]'),
                'reference_point_body':_vector(f['reference_point_body'],3,'material reference point [m]'),
                'equilibrium_rotation':validated_rotation_matrix(f['equilibrium_rotation']),
                'coefficient_axes':validated_rotation_matrix(f['coefficient_axes'])}))
        n = 6*len(frames)
        values = dict(declarations,gravity_included=False,equilibrium_hash=data['equilibrium_hash'],
                      source_identity=data['source_identity'],frames=tuple(frames),
                      dt=_number(data['dt'],'kernel dt [s]',positive=True),
                      omega=_number(data['omega'],'wave frequency [rad/s]'))
        if values['omega'] < 0.:
            raise ValueError('wave frequency must be nonnegative')
        for name in ('added_mass_infinite','restoring'):
            values[name] = _matrix(data[name],n,name)
        for name in ('baseline','excitation_real','excitation_imag'):
            values[name] = _vector(data[name],n,name)
        if not isinstance(data['kernel'],(list,tuple)) or len(data['kernel']) < 2:
            raise ValueError('at least two radiation lag samples required')
        values['kernel'] = tuple(_matrix(item,n,'radiation kernel') for item in data['kernel'])
        if not isinstance(data['motion_bounds'],(list,tuple)) or len(data['motion_bounds']) != len(frames):
            raise ValueError('one motion-bounds row per body required')
        bounds = tuple(_vector(row,2,'translation/rotation radius [m,rad]') for row in data['motion_bounds'])
        if any(row[0] <= 0. or not 0. < row[1] < math.pi-1e-6 for row in bounds):
            raise ValueError('motion bounds must be positive and inside the rotation-log guard')
        values['motion_bounds'] = bounds
        if 'excitation_series' in data:
            from .wave_inputs import excitation_intent
            keys=tuple(f'{f["body_id"]}::{d}' for f in frames for d in ('surge','sway','heave','roll','pitch','yaw'))
            values['excitation_series']=excitation_intent(data['excitation_series'],keys)
            if values['omega'] != 0. or any(values['excitation_real']) or any(values['excitation_imag']):
                raise ValueError('series cannot duplicate legacy harmonic forcing')
        object.__setattr__(self,'data',MappingProxyType(values))

    @property
    def body_ids(self):
        return tuple(frame['body_id'] for frame in self.data['frames'])

    def to_dict(self):
        return _plain(self.data)

    @classmethod
    def from_dict(cls,data):
        return cls(data)
