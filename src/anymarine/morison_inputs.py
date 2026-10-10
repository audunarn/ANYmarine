"""Immutable native member/environment intent; no Qt or owner-package imports.

This is a named data envelope. ANYloads and ANYsolver own physical admission.
Loading a project does not construct a wave backend or execute mechanics.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .native_inputs import _record, _identifier, _number, _integer, _vector, _plain

MORISON_VERSION = 'marine_morison_development_v1'
MORISON_ID = 'morison-members-development'


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _sequence(value, name):
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{name} must be a sequence')
    return value


@dataclass(frozen=True)
class NativeMorisonInputs:
    data: Mapping

    def __post_init__(self):
        raw = _record(self.data, {'api_version', 'contribution_id', 'environment', 'members'}, 'Morison inputs')
        if raw['api_version'] != MORISON_VERSION or raw['contribution_id'] != MORISON_ID:
            raise ValueError('unsupported Morison contract or reserved contribution identity')
        from .environment_inputs import environment_from_data
        environment=environment_from_data(raw['environment'])
        members, ids = [], set()
        for m in _sequence(raw['members'], 'Morison members'):
            m = _record(m, {'member_id', 'end_a', 'end_b', 'section', 'ownership', 'quadrature_order'}, 'Morison member')
            mid = _identifier(m['member_id'], 'member identity')
            if mid in ids:
                raise ValueError('duplicate Morison member identity')
            ids.add(mid)
            value = dict(member_id=mid, quadrature_order=_integer(m['quadrature_order'], 'quadrature order'))
            for key in ('end_a', 'end_b'):
                endpoint = _record(m[key], {'body_id', 'point_body'}, 'member endpoint')
                value[key] = dict(body_id=_identifier(endpoint['body_id'], 'endpoint body identity'),
                                  point_body=_vector(endpoint['point_body'], 3, 'endpoint BODY point [m]'))
            section = _record(m['section'], {'diameter', 'drag_coefficient', 'added_mass_coefficient'}, 'Morison section')
            value['section'] = {key: _number(item, key) for key, item in section.items()}
            owner = _record(m['ownership'], {'primary_role', 'bem_included', 'drag_correction', 'buoyancy_owner'}, 'member ownership')
            for key in ('bem_included', 'drag_correction'):
                if type(owner[key]) is not bool:
                    raise ValueError(f'{key} must be a boolean')
            value['ownership'] = dict(primary_role=_identifier(owner['primary_role'], 'primary role'),
                buoyancy_owner=_identifier(owner['buoyancy_owner'], 'buoyancy owner'),
                bem_included=owner['bem_included'], drag_correction=owner['drag_correction'])
            members.append(value)
        if not members:
            raise ValueError('at least one Morison member is required')
        object.__setattr__(self, 'data', _freeze(dict(api_version=MORISON_VERSION,
            contribution_id=MORISON_ID, environment=environment, members=members)))

    @property
    def body_ids(self):
        return frozenset(member[end]['body_id'] for member in self.data['members'] for end in ('end_a', 'end_b'))

    def to_dict(self):
        return _plain(self.data)

    @classmethod
    def from_dict(cls, data):
        return cls(data)
