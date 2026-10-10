"""Immutable Qt-free native-run intent; ANYsolver owns physical admission.

Only data are persisted. Initial rotation is BODY-to-WORLD, velocity is
WORLD [linear, angular], and the solver computes consistent acceleration.
No chart history, executable provider, estimated inertia or fluid fallback
can be encoded in this application input contract.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from types import MappingProxyType
import math

from .result_rotations import validated_rotation_matrix

NATIVE_INPUTS_VERSION = "native_mechanical_inputs_v1"


def _record(value, fields, name):
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError(f"{name} must contain exactly {', '.join(sorted(fields))}")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty identity")
    return value


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} is not representable as a finite real number") from error
    if not math.isfinite(converted) or (positive and converted <= 0):
        raise ValueError(f"{name} must be a {'positive ' if positive else ''}finite real number")
    return converted


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _vector(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{name} must contain {size} finite real numbers")
    return tuple(_number(item, name) for item in value)


def _matrix(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f"{name} must be a finite {size}x{size} matrix")
    return tuple(_vector(row, size, name) for row in value)


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class NativeMechanicalInputs:
    """Declared dry mechanics data, not a solver model or accepted result.

    Stiffness/inertia physics and link-law admissibility are checked by the
    public ANYsolver compiler. Fixed PSD tolerance is supplied there rather
    than accepting a saved tolerance that can weaken physical validation.
    """
    run_spec: Mapping
    initial_states: Mapping
    links: tuple = ()
    gravity_world: tuple | None = None
    world_wrenches: tuple = ()
    potential_flow: object | None = None
    morison: object | None = None
    wave_realization: object | None = None
    mooring: object | None = None

    def __post_init__(self):
        spec = _record(self.run_spec, {"dt", "steps", "residual_tolerance",
            "max_newton_iterations", "line_search"}, "native run_spec")
        if type(spec["line_search"]) is not bool:
            raise ValueError("line_search must be a boolean")
        object.__setattr__(self, "run_spec", MappingProxyType({
            "dt": _number(spec["dt"], "dt", positive=True),
            "steps": _integer(spec["steps"], "steps"),
            "residual_tolerance": _number(spec["residual_tolerance"], "residual_tolerance", positive=True),
            "max_newton_iterations": _integer(spec["max_newton_iterations"], "max_newton_iterations"),
            "line_search": spec["line_search"],
        }))
        if not isinstance(self.initial_states, Mapping) or not self.initial_states:
            raise ValueError("initial_states must be a non-empty body identity mapping")
        states = {}
        for body_id, value in self.initial_states.items():
            _identifier(body_id, "initial body_id")
            state = _record(value, {"position", "rotation", "velocity_world"}, "initial state")
            states[body_id] = MappingProxyType({
                "position": _vector(state["position"], 3, "initial position [m]"),
                "rotation": validated_rotation_matrix(state["rotation"]),
                "velocity_world": _vector(state["velocity_world"], 6, "initial WORLD velocity [m/s, rad/s]"),
            })
        object.__setattr__(self, "initial_states", MappingProxyType(states))
        if not isinstance(self.links, (list, tuple)):
            raise ValueError("links must be a sequence")
        links, link_ids = [], set()
        fields = {"link_id", "body_a", "body_b", "rho_a", "rho_b", "axes_a", "axes_b",
            "rest_translation", "rest_rotation", "stiffness", "damping", "preload"}
        for value in self.links:
            link = _record(value, fields, "mechanical link")
            lid = _identifier(link["link_id"], "link_id")
            if lid in link_ids:
                raise ValueError("duplicate mechanical link identity")
            link_ids.add(lid)
            a, b = _identifier(link["body_a"], "body_a"), _identifier(link["body_b"], "body_b")
            if a not in states or b not in states or a == b:
                raise ValueError("link must reference two distinct declared body identities")
            links.append(MappingProxyType({
                "link_id": lid, "body_a": a, "body_b": b,
                "rho_a": _vector(link["rho_a"], 3, "rho_a [m]"),
                "rho_b": _vector(link["rho_b"], 3, "rho_b [m]"),
                "axes_a": validated_rotation_matrix(link["axes_a"]),
                "axes_b": validated_rotation_matrix(link["axes_b"]),
                "rest_translation": _vector(link["rest_translation"], 3, "rest_translation [m]"),
                "rest_rotation": validated_rotation_matrix(link["rest_rotation"]),
                "stiffness": _matrix(link["stiffness"], 6, "link stiffness"),
                "damping": _matrix(link["damping"], 6, "link damping"),
                "preload": _vector(link["preload"], 6, "link preload"),
            }))
        object.__setattr__(self, "links", tuple(links))
        if self.gravity_world is not None:
            object.__setattr__(self, "gravity_world", _vector(self.gravity_world, 3, "gravity [m/s^2]"))
        if not isinstance(self.world_wrenches, (list, tuple)):
            raise ValueError("world_wrenches must be a sequence")
        wrenches, contribution_ids = [], set()
        for value in self.world_wrenches:
            wrench = _record(value, {"contribution_id", "body_id", "point_body",
                "force_world", "moment_world"}, "world wrench")
            cid = _identifier(wrench["contribution_id"], "contribution_id")
            if cid in contribution_ids or cid in {"dry", "mechanical-links"} or cid.startswith("dry-gravity:"):
                raise ValueError("duplicate or reserved contribution identity")
            contribution_ids.add(cid)
            _identifier(wrench["body_id"], "wrench body_id")
            if wrench["body_id"] not in states:
                raise ValueError("world wrench references an undeclared body identity")
            wrenches.append(MappingProxyType({
                "contribution_id": cid, "body_id": wrench["body_id"],
                "point_body": _vector(wrench["point_body"], 3, "point_body [m]"),
                "force_world": _vector(wrench["force_world"], 3, "force_world [N]"),
                "moment_world": _vector(wrench["moment_world"], 3, "moment_world [Nm]"),
            }))
        object.__setattr__(self, "world_wrenches", tuple(wrenches))
        if self.potential_flow is not None:
            from .potential_flow_inputs import NativePotentialFlowInputs, POTENTIAL_FLOW_ID
            fluid = (self.potential_flow if type(self.potential_flow) is NativePotentialFlowInputs
                     else NativePotentialFlowInputs.from_dict(self.potential_flow))
            if set(fluid.body_ids) != set(states) or fluid.data['dt'] != self.run_spec['dt']:
                raise ValueError('potential-flow bodies/kernel dt must match native inputs')
            if POTENTIAL_FLOW_ID in contribution_ids:
                raise ValueError('potential-flow contribution identity is reserved')
            object.__setattr__(self,'potential_flow',fluid)

        if self.morison is not None:
            from .morison_inputs import NativeMorisonInputs, MORISON_ID
            members = (self.morison if type(self.morison) is NativeMorisonInputs
                       else NativeMorisonInputs.from_dict(self.morison))
            if not members.body_ids.issubset(states):
                raise ValueError('Morison endpoint references an undeclared body identity')
            if MORISON_ID in contribution_ids:
                raise ValueError('Morison contribution identity is reserved')
            object.__setattr__(self, 'morison', members)
        if self.wave_realization is not None:
            from .wave_inputs import NativeWaveInputs
            waves=(self.wave_realization if type(self.wave_realization) is NativeWaveInputs
                   else NativeWaveInputs.from_dict(self.wave_realization))
            if self.potential_flow is None or 'excitation_series' not in self.potential_flow.data or self.morison is None:
                raise ValueError('shared waves require both declared excitation series and Morison environment')
            if self.potential_flow.data['excitation_series']['wave_realization_hash'] != waves.content_hash:
                raise ValueError('shared wave hash and excitation series disagree; resynthesize after edits')
            if _plain(self.morison.data['environment']['components']) != waves.to_dict()['components']:
                raise ValueError('Morison and excitation must use the exact same saved wave components')
            object.__setattr__(self,'wave_realization',waves)

        if self.mooring is not None:
            from .mooring_inputs import NativeMooringInputs
            mooring=self.mooring if type(self.mooring) is NativeMooringInputs else NativeMooringInputs.from_dict(self.mooring)
            if set(mooring.data['body_ids'])!=set(states):raise ValueError('mooring body inventory differs from native states')
            if any(w['contribution_id'].startswith('mooring:') for w in self.world_wrenches):
                raise ValueError('mooring force diagnostic identities are reserved')
            if self.gravity_world is not None and mooring.data['gravity_world']!=self.gravity_world:raise ValueError('body/line gravity differs')
            object.__setattr__(self,'mooring',mooring)
            if mooring.has_hydrodynamics:
                env=mooring.data['environment']
                if self.morison is not None:
                    if _plain(env)!=_plain(self.morison.data['environment']):raise ValueError('members and lines must use the same declared environment')
                    line_ids={line['line_id'] for line in mooring.data['lines'] if 'hydrodynamics' in line}
                    if line_ids.intersection(m['member_id'] for m in self.morison.data['members']):
                        raise ValueError('duplicate physical line/member hydrodynamic ownership')
                if self.wave_realization is not None and _plain(env['components'])!=self.wave_realization.to_dict()['components']:
                    raise ValueError('lines and shared realization must use the same saved wave components')

    def to_dict(self):
        return {"version": NATIVE_INPUTS_VERSION, "run_spec": _plain(self.run_spec),
            "initial_states": _plain(self.initial_states), "links": _plain(self.links),
            "gravity_world": _plain(self.gravity_world), "world_wrenches": _plain(self.world_wrenches),
            **({'potential_flow':self.potential_flow.to_dict()} if self.potential_flow is not None else {}),
            **({'morison':self.morison.to_dict()} if self.morison is not None else {}),
            **({'wave_realization':self.wave_realization.to_dict()} if self.wave_realization is not None else {}),
            **({'mooring':self.mooring.to_dict()} if self.mooring is not None else {})}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data,Mapping):
            raise ValueError('native inputs must be a mapping')
        fields = {"version", "run_spec", "initial_states", "links", "gravity_world", "world_wrenches"}
        for optional in ('potential_flow', 'morison', 'wave_realization','mooring'):
            if optional in data:
                fields.add(optional)
                if data[optional] is None:
                    raise ValueError(f'{optional} must contain declared data or be omitted')
        _record(data,fields,"native inputs")
        if data["version"] != NATIVE_INPUTS_VERSION:
            raise ValueError("unsupported native inputs version")
        return cls(**{key: value for key, value in data.items() if key != "version"})
