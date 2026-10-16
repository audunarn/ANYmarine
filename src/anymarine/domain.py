"""Qt-free domain records for the ANYmarine project model.

All records are plain dataclasses validated at construction. Nothing in this
module imports Qt, numerical backends or viewer code.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Optional

from . import RESULT_KIND_SYNTHETIC, RESULT_KIND_MECHANICAL, RESULT_KIND_EQUILIBRIUM, RESULT_KINDS
from .mass_properties import BodyMassProperties
from .native_inputs import NativeMechanicalInputs
from .surface_assignments import HydroSurfaceAssignment, body_surface_hash

LINEAR_SOURCE_ANALYTIC_DEMO = "analytic_demo"
LINEAR_SOURCE_EXPLICIT = "explicit"
LINEAR_SOURCE_HYDRO = "hydro_dataset"
LINEAR_SOURCES = (
    LINEAR_SOURCE_ANALYTIC_DEMO,
    LINEAR_SOURCE_EXPLICIT,
    LINEAR_SOURCE_HYDRO,
)
LINEAR_DOF_COUNT = 4
LINEAR_MAX_FRAMES = 1001
# Relative tolerance for admitting the sampled kernel grid k*dt; the same
# contract the solver enforces, checked early so malformed grids fail closed
# at input construction, before any solver import.
LINEAR_GRID_RELATIVE_TOLERANCE = 1.0e-9
# Hard ceiling for explicit linear execution limits: no constructor or JSON
# form may admit a longer development job than this, and the ceiling itself is
# never a qualification, only an execution bound.
LINEAR_MAX_FRAMES_CEILING = 20001
# Linear layouts (ADR-015 section 1). The legacy layout is never serialised, so
# legacy records keep their exact bytes and input hashes.
LINEAR_LAYOUT_LEGACY = "two_body_surge_heave_v1"
LINEAR_LAYOUT_RIGID = "rigid_subset_v1"
LINEAR_LAYOUTS = (LINEAR_LAYOUT_LEGACY, LINEAR_LAYOUT_RIGID)
RIGID_DOFS = ("surge", "sway", "heave", "roll", "pitch", "yaw")
# ADR-015 section 9: N > 12 retained DOFs is refused until assessed.
LINEAR_RIGID_MAX_DOFS = 12
# The stored dry mass must equal the solver-owned RigidBody6D assembly.
LINEAR_DRY_MASS_RELATIVE_TOLERANCE = 1.0e-12
_IDENTITY_ORIENTATION = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
EXTERNAL_MATRIX_KINDS = ("damping", "stiffness")


def new_body_id() -> str:
    return str(uuid.uuid4())


def _finite_floats(value, name: str, count: int) -> tuple[float, ...]:
    try:
        items = tuple(float(item) for item in value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain {count} finite numbers") from error
    if len(items) != count or any(not math.isfinite(item) for item in items):
        raise ValueError(f"{name} must contain {count} finite numbers")
    return items


def _hash_digest(value, name: str) -> str:
    """Validate a 64-character sha256 hex digest field (fail closed)."""

    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} must be a 64-character sha256 hex string")
    return value


@dataclass(frozen=True)
class BodyMesh:
    """Triangular hull mesh in the body frame (metres).

    Data is defensively copied and frozen at construction: the mesh never
    aliases caller-owned lists, and triangle indices must be exact integers
    (fractional indices are rejected, never silently truncated).
    """

    positions: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        if not self.positions:
            raise ValueError("mesh positions must not be empty")
        if not self.triangles:
            raise ValueError("mesh triangles must not be empty")
        frozen_positions = tuple(
            _finite_floats(position, f"mesh position {index}", 3)
            for index, position in enumerate(self.positions)
        )
        node_count = len(frozen_positions)
        frozen_triangles = []
        for index, triangle in enumerate(self.triangles):
            try:
                corners = tuple(triangle)
            except TypeError as error:
                raise ValueError(
                    f"mesh triangle {index} must contain 3 integer indices"
                ) from error
            if len(corners) != 3:
                raise ValueError(f"mesh triangle {index} must contain 3 indices")
            ints = []
            for corner in corners:
                if isinstance(corner, bool) or not isinstance(corner, (int, float)):
                    raise ValueError(
                        f"mesh triangle {index} must contain integer indices; "
                        f"got {corner!r}"
                    )
                if isinstance(corner, float):
                    if not corner.is_integer():
                        raise ValueError(
                            f"mesh triangle {index} contains the non-integer "
                            f"index {corner!r}; fractional indices are rejected"
                        )
                    corner = int(corner)
                ints.append(corner)
            if any(corner < 0 or corner >= node_count for corner in ints):
                raise ValueError(f"mesh triangle {index} references a missing position")
            frozen_triangles.append(tuple(ints))
        # Freeze copies so later mutation of caller-owned containers cannot
        # change the geometry.
        object.__setattr__(self, "positions", frozen_positions)
        object.__setattr__(self, "triangles", tuple(frozen_triangles))

    def to_dict(self) -> dict:
        return {
            "positions": [list(position) for position in self.positions],
            "triangles": [list(triangle) for triangle in self.triangles],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BodyMesh":
        # No index coercion here: __post_init__ validates and rejects
        # fractional or non-numeric indices instead of truncating them.
        return cls(
            positions=[tuple(row) for row in data["positions"]],
            triangles=[tuple(row) for row in data["triangles"]],
        )


@dataclass(frozen=True)
class Body:
    """One mechanical floating body with stable persistent identity."""

    body_id: str
    name: str
    reference_position: tuple[float, float, float]
    mass: float
    mesh: BodyMesh
    color: str = "#4f83c9"
    hydro_sidecar: Optional[str] = None  # relative path to a generic hydro sidecar
    # Optional numerical lid: body-local triangles only. It is geometry for
    # hydrodynamic imports (a lid at the equilibrium waterplane), never a
    # display, gravity or buoyancy feature. Appended optional field: None is
    # omitted from serialization so existing records keep their exact
    # serialized identities, and positional construction is unchanged.
    hydro_lid: Optional[BodyMesh] = None
    mass_properties: Optional[BodyMassProperties] = None
    hydro_surfaces: tuple[HydroSurfaceAssignment, ...] = ()
    # Omitted legacy default: an opt-in numerical recipe, never a physical plate.
    hydro_lid_policy: str = "free_surface_v1"
    reference_orientation: tuple = ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))

    def __post_init__(self) -> None:
        if not isinstance(self.body_id, str) or not self.body_id:
            raise ValueError("body_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("body name must be a non-empty string")
        object.__setattr__(self, "reference_position", _finite_floats(self.reference_position, "reference_position", 3))
        from .result_rotations import validated_rotation_matrix
        object.__setattr__(self, "reference_orientation", validated_rotation_matrix(self.reference_orientation))
        mass = float(self.mass)
        if mass != mass or mass <= 0.0 or mass == float("inf"):
            raise ValueError("body mass must be a positive finite number")
        if not isinstance(self.mesh, BodyMesh):
            raise TypeError("body mesh must be a BodyMesh")
        if not isinstance(self.color, str) or not self.color.startswith("#"):
            raise ValueError("body color must be a '#rrggbb' string")
        if self.hydro_lid is not None and not isinstance(self.hydro_lid, BodyMesh):
            raise TypeError("body hydro_lid must be a BodyMesh or None")
        if self.hydro_lid_policy not in ("free_surface_v1", "submerged_0p1m_v1"):
            raise ValueError("unsupported hydro_lid_policy")
        if self.hydro_lid_policy != "free_surface_v1" and self.hydro_lid is None:
            raise ValueError("explicit hydro_lid_policy requires numerical lid geometry")
        if self.mass_properties is not None and not isinstance(self.mass_properties, BodyMassProperties):
            raise TypeError("body mass_properties must be a BodyMassProperties or None")

        surfaces = tuple(self.hydro_surfaces)
        ids = []
        roles = []
        for surface in surfaces:
            if not isinstance(surface, HydroSurfaceAssignment):
                raise ValueError('body hydro_surfaces require explicit owned assignments')
            if surface.semantics.owning_body_id != self.body_id:
                raise ValueError('surface owning_body_id does not match its body')
            if surface.role == 'lid' and self.hydro_lid is None:
                raise ValueError('lid assignment requires existing hydro_lid geometry')
            ids.append(surface.semantics.surface_id)
            roles.append(surface.role)
        if len(ids) != len(set(ids)) or roles.count('hull') > 1 or roles.count('lid') > 1:
            raise ValueError('duplicate surface identity or hull/lid assignment')
        object.__setattr__(self, 'hydro_surfaces', surfaces)

    def with_edits(self, **changes) -> "Body":
        data = {
            "body_id": self.body_id,
            "name": self.name,
            "reference_position": self.reference_position,
            "reference_orientation": self.reference_orientation,
            "mass": self.mass,
            "mesh": self.mesh,
            "color": self.color,
            "hydro_sidecar": self.hydro_sidecar,
            "hydro_lid": self.hydro_lid,
            "mass_properties": self.mass_properties,
            "hydro_surfaces": self.hydro_surfaces,
            "hydro_lid_policy": self.hydro_lid_policy,
        }
        data.update(changes)
        return Body(**data)

    def to_dict(self) -> dict:
        data = {
            "body_id": self.body_id,
            "name": self.name,
            "reference_position": list(self.reference_position),
            "mass": float(self.mass),
            "color": self.color,
            "hydro_sidecar": self.hydro_sidecar,
            "mesh": self.mesh.to_dict(),
        }
        if self.reference_orientation != ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)):
            data["reference_orientation"] = [list(row) for row in self.reference_orientation]
        if self.hydro_lid is not None:
            # Omitted when None: existing serialized bodies keep their exact
            # bytes, hashes and identities.
            data["hydro_lid"] = self.hydro_lid.to_dict()
        if self.mass_properties is not None:
            data["mass_properties"] = self.mass_properties.to_dict()
        if self.hydro_lid_policy != "free_surface_v1":
            data["hydro_lid_policy"] = self.hydro_lid_policy
        if self.hydro_surfaces:
            data["hydro_surfaces"] = [s.to_dict() for s in self.hydro_surfaces]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Body":
        if 'hydro_surfaces' in data and (not isinstance(data['hydro_surfaces'], list) or not data['hydro_surfaces']):
            raise ValueError('hydro_surfaces must contain explicit assignments; refusing instead of dropping intent')
        return cls(
            body_id=str(data["body_id"]),
            name=str(data["name"]),
            reference_position=tuple(float(x) for x in data["reference_position"]),
            reference_orientation=data.get("reference_orientation", ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))),
            mass=float(data["mass"]),
            mesh=BodyMesh.from_dict(data["mesh"]),
            color=str(data.get("color", "#4f83c9")),
            hydro_sidecar=data.get("hydro_sidecar"),
            hydro_lid_policy=data.get("hydro_lid_policy", "free_surface_v1"),
            hydro_surfaces=tuple(HydroSurfaceAssignment.from_dict(s) for s in data.get("hydro_surfaces", [])),
            mass_properties=(BodyMassProperties.from_dict(data["mass_properties"])
                             if data.get("mass_properties") is not None else None),
            hydro_lid=(
                BodyMesh.from_dict(data["hydro_lid"])
                if data.get("hydro_lid") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class EquilibriumMetadata:
    """Equilibrium about which analyses are linearised or demonstrated."""

    waterline_z: float = 0.0
    description: str = ""
    source: str = "synthetic"

    def __post_init__(self) -> None:
        _finite_floats((self.waterline_z,), "waterline_z", 1)
        if not isinstance(self.description, str):
            raise ValueError("equilibrium description must be a string")

    def with_edits(self, **changes) -> "EquilibriumMetadata":
        data = {
            "waterline_z": self.waterline_z,
            "description": self.description,
            "source": self.source,
        }
        data.update(changes)
        return EquilibriumMetadata(**data)

    def to_dict(self) -> dict:
        return {
            "waterline_z": float(self.waterline_z),
            "description": self.description,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EquilibriumMetadata":
        return cls(
            waterline_z=float(data.get("waterline_z", 0.0)),
            description=str(data.get("description", "")),
            source=str(data.get("source", "synthetic")),
        )


@dataclass(frozen=True)
class AnalysisSettings:
    """Replay/analysis parameters. Currently only the synthetic replay uses them."""

    duration: float = 20.0
    dt: float = 0.25
    wave_amplitude: float = 0.8
    wave_period: float = 6.0

    def __post_init__(self) -> None:
        for name, value in (
            ("duration", self.duration),
            ("dt", self.dt),
            ("wave_amplitude", self.wave_amplitude),
            ("wave_period", self.wave_period),
        ):
            number = float(value)
            if number != number or number <= 0.0 or number == float("inf"):
                raise ValueError(f"analysis {name} must be a positive finite number")

    def with_edits(self, **changes) -> "AnalysisSettings":
        data = {
            "duration": self.duration,
            "dt": self.dt,
            "wave_amplitude": self.wave_amplitude,
            "wave_period": self.wave_period,
        }
        data.update(changes)
        return AnalysisSettings(**data)

    def to_dict(self) -> dict:
        return {
            "duration": float(self.duration),
            "dt": float(self.dt),
            "wave_amplitude": float(self.wave_amplitude),
            "wave_period": float(self.wave_period),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisSettings":
        return cls(
            duration=float(data.get("duration", 20.0)),
            dt=float(data.get("dt", 0.25)),
            wave_amplitude=float(data.get("wave_amplitude", 0.8)),
            wave_period=float(data.get("wave_period", 6.0)),
        )


@dataclass(frozen=True)
class ResultRecord:
    """One stored, immutable result artifact.

    kind is always explicit; the only current kind is
    ``synthetic_unqualified``: a deterministic synthetic demonstration that is
    not BEM output and not a dynamics solution.

    Records are deeply immutable: nested mappings are defensively copied and
    frozen at construction, so mutating a container passed to the constructor
    (or to :func:`dataclasses.replace`) can never change a stored result.
    """

    result_id: str
    kind: str
    input_hash: str
    body_ids: tuple[str, ...]
    times: tuple[float, ...]
    positions: dict[str, tuple[tuple[float, float, float], ...]]
    orientations: dict[str, tuple[tuple[float, float, float], ...]]
    channels: dict[str, tuple[float, ...]]
    note: str = ""
    created_utc: str = ""
    rotation_matrices: Optional[Mapping] = None
    native_snapshot: Optional[Mapping] = None
    line_nodes: Optional[tuple] = None
    equilibrium_evidence: Optional[Mapping] = None
    surface_snapshot: Optional[Mapping] = None
    # ADR-015 section 6: rigid_subset_v1 linear results carry their layout, the
    # producing snapshot and authoritative BODY-to-WORLD rotation matrices
    # (Euler orientations empty). Absent for every other result (legacy bytes).
    result_layout: Optional[str] = None
    linear_snapshot: Optional[Mapping] = None

    def __post_init__(self) -> None:
        if self.kind not in RESULT_KINDS:
            raise ValueError(f"unknown result kind {self.kind!r}")
        if not isinstance(self.result_id, str) or not self.result_id.strip():
            raise ValueError("result_id must be a non-empty string")
        if not isinstance(self.input_hash, str) or len(self.input_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.input_hash
        ):
            raise ValueError("input_hash must be a 64-character sha256 hex string")
        if self.surface_snapshot is not None:
            from . import RESULT_KIND_LINEAR
            from .surface_assignments import project_from_surface_snapshot
            from .linear import linear_input_hash
            producing_surface = project_from_surface_snapshot(self.surface_snapshot)
            if self.kind != RESULT_KIND_LINEAR or self.input_hash != linear_input_hash(producing_surface.linear_inputs):
                raise ValueError('surface replay snapshot must belong to this linear result input hash')
            if tuple(self.body_ids) != tuple(b.body_id for b in producing_surface.bodies):
                raise ValueError('surface replay body identities do not match the result')
            object.__setattr__(self, 'surface_snapshot', _freeze_json_value(self.surface_snapshot, 'surface_snapshot'))
        if self.kind == 'line_equilibrium_development':
            from .line_equilibrium import validate_and_freeze
            validate_and_freeze(self)
            return
        body_ids = tuple(self.body_ids)
        if not body_ids:
            raise ValueError("result must reference at least one body")
        for body_id in body_ids:
            if not isinstance(body_id, str) or not body_id:
                raise ValueError("result body_ids must be non-empty strings")
        if len(set(body_ids)) != len(body_ids):
            raise ValueError("result contains duplicate body ids")
        native = self.kind in (RESULT_KIND_MECHANICAL, RESULT_KIND_EQUILIBRIUM)
        if native:
            from .native_inputs import _number, _vector
            if self.kind == RESULT_KIND_EQUILIBRIUM:
                from .equilibrium_jobs import project_from_snapshot
            else:
                from .native_jobs import project_from_snapshot
            if not isinstance(self.note, str) or not isinstance(self.created_utc, str):
                raise ValueError("native result note/timestamp must be strings")
            producing = project_from_snapshot(self.native_snapshot)
            if (self.input_hash != self.native_snapshot['input_hash']
                    or body_ids != tuple(body.body_id for body in producing.bodies)):
                raise ValueError("native result must bind its exact producing snapshot")
            if not isinstance(self.positions, Mapping) or not isinstance(self.channels, Mapping):
                raise ValueError("native positions/channels must be mappings")
            for rows in self.positions.values():
                for row in rows:
                    _vector(row, 3, "native position")
            for values in self.channels.values():
                for value in values:
                    _number(value, "native channel")
            times = tuple(_number(t, "native time") for t in self.times)
            object.__setattr__(self, 'native_snapshot', _freeze_json_value(self.native_snapshot, 'native_snapshot'))
        else:
            rigid = self.result_layout is not None
            if self.native_snapshot is not None or (self.rotation_matrices is not None and not rigid):
                raise ValueError("legacy result kinds cannot carry native matrix/snapshot intent")
            if rigid:
                from . import RESULT_KIND_LINEAR
                if self.kind != RESULT_KIND_LINEAR or self.result_layout != LINEAR_LAYOUT_RIGID:
                    raise ValueError("result_layout is only admitted as rigid_subset_v1 on linear results")
                object.__setattr__(self, "linear_snapshot", _validated_linear_snapshot(
                    self.linear_snapshot, self.input_hash, body_ids))
            elif self.linear_snapshot is not None:
                raise ValueError("a linear snapshot requires result_layout rigid_subset_v1")
            times = tuple(float(t) for t in self.times)
        if not times:
            raise ValueError("result must contain at least one time sample")
        if self.kind == RESULT_KIND_MECHANICAL:
            spec = producing.native_inputs.run_spec
            if len(times) != spec['steps'] + 1 or any(t != i * spec['dt'] for i, t in enumerate(times)):
                raise ValueError("native result must contain its complete producing timeline")
            mooring=producing.native_inputs.mooring
            if (mooring is None)!=(self.line_nodes is None):raise ValueError('stored node data must match producing mooring intent')
            if mooring is not None:
                from .mooring_inputs import node_rows,validate_node_step
                if not isinstance(self.line_nodes,(list,tuple)) or len(self.line_nodes)!=len(times):raise ValueError('stored node timeline mismatch')
                rows=tuple(node_rows(frame,mooring.data['node_count'],'stored node positions') for frame in self.line_nodes)
                if rows[0]!=mooring.initial_nodes['positions']:raise ValueError('stored initial nodes differ from snapshot')
                node_count=mooring.data['node_count'];previous=None
                names=('velocities','accelerations')
                for node in range(node_count):
                    for name in names:
                        for axis in ('x','y','z'):
                            values=self.channels.get(f'mooring/node{node}/{name} {axis}')
                            if not isinstance(values,(list,tuple)) or len(values)!=len(times):
                                raise ValueError('stored node kinematic channel inventory mismatch')
                for index,positions_frame in enumerate(rows):
                    current=dict(positions=positions_frame)
                    for name in names:
                        current[name]=tuple(tuple(self.channels[f'mooring/node{node}/{name} {axis}'][index]
                                                  for axis in ('x','y','z')) for node in range(node_count))
                    if index==0 and current['velocities']!=mooring.initial_nodes['velocities']:
                        raise ValueError('stored initial node velocity differs from snapshot')
                    if previous is not None:validate_node_step(current,previous,spec['dt'])
                    previous=current
                object.__setattr__(self,'line_nodes',rows)
                if mooring.has_hydrodynamics:
                    from .mooring_inputs import HYDRO_CHANNELS,HYDRO_COMPONENTS
                    required=['mooring/'+name for name in HYDRO_CHANNELS]
                    required+=[f'mooring/node{node}/{name} {axis}' for node in range(node_count)
                               for name in HYDRO_COMPONENTS for axis in ('x','y','z')]
                    for bid in body_ids:
                        for name in HYDRO_COMPONENTS:
                            identity=json.dumps(['mooring:'+name,bid],separators=(',',':'))
                            required.extend(f'wrench{identity}/{axis}' for axis in ('Fx','Fy','Fz','Mx','My','Mz'))
                    if any(name not in self.channels or len(self.channels[name])!=len(times) for name in required):
                        raise ValueError('stored line hydrodynamic channel inventory mismatch')
                    if any(value<0. for name in HYDRO_CHANNELS for value in self.channels['mooring/'+name]):
                        raise ValueError('negative stored line hydro energy/drag power')
        elif self.kind == RESULT_KIND_EQUILIBRIUM:
            if self.line_nodes is not None:
                from .mooring_inputs import node_rows
                if producing.native_inputs.mooring is None:raise ValueError('static nodes without mooring')
                object.__setattr__(self,'line_nodes',tuple(node_rows(rows,producing.native_inputs.mooring.data['node_count'],'static nodes') for rows in self.line_nodes))
        elif self.line_nodes is not None:raise ValueError('legacy results cannot carry mooring nodes')
        if any(not math.isfinite(t) for t in times):
            raise ValueError("result times must be finite numbers")
        for previous, current in zip(times, times[1:]):
            if not current > previous:
                raise ValueError("result times must be strictly increasing")
        positions = self._freeze_samples(self.positions, "positions", len(times))
        if native or self.result_layout is not None:
            from .result_rotations import freeze_rotation_samples
            if not isinstance(self.orientations, Mapping) or self.orientations:
                raise ValueError("native and rigid linear results require empty Euler orientations; matrices are "
                                 "authoritative")
            orientations = {}
            rotations = freeze_rotation_samples(self.rotation_matrices, body_ids, len(times))
            object.__setattr__(self, 'rotation_matrices', rotations)
            sample_mappings = (("positions", positions),)
        else:
            orientations = self._freeze_samples(self.orientations, "orientations", len(times))
            sample_mappings = (("positions", positions), ("orientations", orientations))
        for name, mapping in sample_mappings:
            if set(mapping) != set(body_ids):
                missing = sorted(set(body_ids) - set(mapping))
                extra = sorted(set(mapping) - set(body_ids))
                raise ValueError(
                    f"result {name} keys must match body_ids exactly "
                    f"(missing {missing}, unexpected {extra})"
                )
        if not isinstance(self.channels, Mapping):
            raise ValueError("result channels must be a mapping")
        frozen_channels = {}
        for name, values in self.channels.items():
            if not isinstance(name, str) or not name:
                raise ValueError("channel names must be non-empty strings")
            frozen = tuple(float(value) for value in values)
            if len(frozen) != len(times):
                raise ValueError(f"channel {name} does not match times")
            if any(not math.isfinite(value) for value in frozen):
                raise ValueError(f"channel {name} must contain finite numbers")
            frozen_channels[name] = frozen
        if native and producing.native_inputs.mooring is not None and producing.native_inputs.mooring.has_contact:
            from .contact_inputs import validate_stored_contact
            validate_stored_contact(producing.native_inputs.mooring,frozen_channels,times,positions,rotations,self.line_nodes)
        if self.kind == RESULT_KIND_EQUILIBRIUM:
            from .equilibrium_jobs import validate_record
            data=validate_record(self,producing)
            object.__setattr__(self,'equilibrium_evidence',_freeze_json_value(data,'equilibrium evidence'))
        elif self.equilibrium_evidence is not None:raise ValueError('equilibrium evidence on another result kind')
        # Freeze defensive copies so a frozen record can never be mutated
        # through caller-owned containers passed to the constructor.
        object.__setattr__(self, "body_ids", body_ids)
        object.__setattr__(self, "times", times)
        object.__setattr__(self, "positions", MappingProxyType(positions))
        object.__setattr__(self, "orientations", MappingProxyType(orientations))
        object.__setattr__(self, "channels", MappingProxyType(frozen_channels))
        if self.result_layout is not None:
            # decoding boundary (persistence, worker output, replay): kinematics must follow from q and the
            # producing snapshot - a record with contradictory q/matrices/positions is refused here
            from .linear import check_rigid_result_kinematics
            check_rigid_result_kinematics(self)

    @staticmethod
    def _freeze_samples(mapping, name: str, sample_count: int) -> dict:
        """Validate and defensively copy one body->rows sample mapping."""

        if not isinstance(mapping, Mapping):
            raise ValueError(f"result {name} must be a mapping")
        frozen = {}
        for body_id, rows in mapping.items():
            if not isinstance(body_id, str) or not body_id:
                raise ValueError(f"result {name} keys must be non-empty strings")
            frozen_rows = tuple(rows)
            if len(frozen_rows) != sample_count:
                raise ValueError(
                    f"{name} samples for body {body_id} do not match times"
                )
            frozen[body_id] = tuple(
                _finite_floats(row, f"{name} sample for body {body_id}", 3)
                for row in frozen_rows
            )
        return frozen

    def frame_count(self) -> int:
        return len(self.times)

    def to_dict(self) -> dict:
        return {
            **({'surface_snapshot':_jsonable_value(self.surface_snapshot)} if self.surface_snapshot is not None else {}),
            **({'line_nodes':_jsonable_value(self.line_nodes)} if self.line_nodes is not None else {}),
            **({"rotation_matrices": {
                bid: [[list(row) for row in matrix] for matrix in samples]
                for bid, samples in self.rotation_matrices.items()},
                "native_snapshot": _jsonable_value(self.native_snapshot)}
                if self.kind in (RESULT_KIND_MECHANICAL,RESULT_KIND_EQUILIBRIUM,'line_equilibrium_development') else {}),
            **({"equilibrium_evidence":_jsonable_value(self.equilibrium_evidence)} if self.kind in (RESULT_KIND_EQUILIBRIUM,'line_equilibrium_development') else {}),
            **({"result_layout": self.result_layout, "linear_snapshot": _jsonable_value(self.linear_snapshot),
                "rotation_matrices": {bid: [[list(row) for row in matrix] for matrix in samples]
                                      for bid, samples in self.rotation_matrices.items()}}
               if self.result_layout is not None else {}),
            "result_id": self.result_id,
            "kind": self.kind,
            "input_hash": self.input_hash,
            "body_ids": list(self.body_ids),
            "times": [float(t) for t in self.times],
            "positions": {
                body_id: [list(row) for row in rows] for body_id, rows in self.positions.items()
            },
            "orientations": {
                body_id: [list(row) for row in rows]
                for body_id, rows in self.orientations.items()
            },
            "channels": {name: [float(v) for v in values] for name, values in self.channels.items()},
            "note": self.note,
            "created_utc": self.created_utc,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ResultRecord":
        kind = str(data["kind"])
        if kind in (RESULT_KIND_MECHANICAL,RESULT_KIND_EQUILIBRIUM,'line_equilibrium_development'):
            fields = {"result_id", "kind", "input_hash", "body_ids", "times", "positions",
                      "orientations", "channels", "note", "created_utc", "rotation_matrices", "native_snapshot"}
            if kind in (RESULT_KIND_EQUILIBRIUM,'line_equilibrium_development'):fields.add('equilibrium_evidence')
            if 'line_nodes' in data:fields.add('line_nodes')
            if set(data) != fields:
                raise ValueError("native result fields must exactly match the matrix schema")
            return cls(**data)
        if "result_layout" in data or "linear_snapshot" in data:
            fields = {"result_id", "kind", "input_hash", "body_ids", "times", "positions", "orientations",
                      "channels", "note", "created_utc", "rotation_matrices", "result_layout", "linear_snapshot"}
            if "surface_snapshot" in data:
                fields.add("surface_snapshot")
            if set(data) != fields:
                raise ValueError("rigid linear result fields must exactly match the rigid_subset_v1 schema")
            return cls(**data)
        if "rotation_matrices" in data or "native_snapshot" in data or 'line_nodes' in data:
            raise ValueError("legacy kind cannot silently discard native result intent")
        if kind not in RESULT_KINDS:
            raise ValueError(f"unknown result kind {kind!r}")
        return cls(
            result_id=str(data["result_id"]),
            kind=kind,
            surface_snapshot=data.get("surface_snapshot"),
            input_hash=str(data["input_hash"]),
            body_ids=tuple(str(item) for item in data["body_ids"]),
            times=tuple(float(t) for t in data["times"]),
            positions={
                str(body_id): [tuple(float(x) for x in row) for row in rows]
                for body_id, rows in data["positions"].items()
            },
            orientations={
                str(body_id): [tuple(float(x) for x in row) for row in rows]
                for body_id, rows in data["orientations"].items()
            },
            channels={
                str(name): tuple(float(v) for v in values)
                for name, values in data.get("channels", {}).items()
            },
            note=str(data.get("note", "")),
            created_utc=str(data.get("created_utc", "")),
        )


LINEAR_SNAPSHOT_FORMAT = "linear_rigid_snapshot_v1"
_LINEAR_SNAPSHOT_KEYS = ("format", "layout", "input_hash", "labels", "bodies", "envelope")


def _validated_linear_snapshot(snapshot, input_hash: str, body_ids) -> Mapping:
    """Structural admission of a rigid linear result's producing snapshot (ADR-015 section 6).

    Binds the layout, the producing input hash, the ordered DOF labels, the producing bodies (complete Body
    records in result body order, so replay never depends on the current project) and the declared
    small-angle envelope. Numerical consistency with the stored channels is proved by
    ``linear.verify_linear_result``.
    """
    if not isinstance(snapshot, Mapping) or set(snapshot) != set(_LINEAR_SNAPSHOT_KEYS):
        raise ValueError(f"linear snapshot must contain exactly {list(_LINEAR_SNAPSHOT_KEYS)}")
    if snapshot["format"] != LINEAR_SNAPSHOT_FORMAT or snapshot["layout"] != LINEAR_LAYOUT_RIGID:
        raise ValueError("unsupported linear snapshot format/layout")
    if snapshot["input_hash"] != input_hash:
        raise ValueError("linear snapshot must belong to this result's input hash")
    labels = snapshot["labels"]
    if not isinstance(labels, (list, tuple)) or not labels or any(not isinstance(x, str) or ":" not in x
                                                                  for x in labels):
        raise ValueError("linear snapshot labels must be body:dof strings")
    bodies = snapshot["bodies"]
    if not isinstance(bodies, (list, tuple)):
        raise ValueError("linear snapshot bodies must be a list")
    parsed = tuple(Body.from_dict(_jsonable_value(item)) for item in bodies)
    if tuple(body.body_id for body in parsed) != tuple(body_ids):
        raise ValueError("linear snapshot bodies must be the result bodies in order")
    if {label.split(":", 1)[0] for label in labels} - set(body_ids):
        raise ValueError("linear snapshot labels must belong to the result bodies")
    envelope = snapshot["envelope"]
    if (not isinstance(envelope, Mapping) or set(envelope) != {"max_rotation_rad", "max_translation_fraction",
                                                                "length_scale_m"}
            or any(isinstance(envelope[k], bool) or not isinstance(envelope[k], (int, float))
                   or not math.isfinite(envelope[k]) or envelope[k] <= 0
                   for k in ("max_rotation_rad", "max_translation_fraction"))
            or not isinstance(envelope["length_scale_m"], Mapping)
            or set(envelope["length_scale_m"]) != set(body_ids)
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                   for v in envelope["length_scale_m"].values())):
        raise ValueError("linear snapshot envelope must declare positive max_rotation_rad, "
                         "max_translation_fraction and a length scale per body")
    return _freeze_json_value(snapshot, "linear snapshot")


@dataclass(frozen=True)
class LinearBodyBinding:
    """Binding of one project body to its two linear development DOFs.

    The binding captures the body identity, display name, equilibrium
    reference position, mass and the immutable hull mesh identity (sha256
    over the canonical mesh data) at input creation. DOF order is
    significant: binding 0 owns DOFs 0 (surge) and 1 (heave), binding 1
    owns DOFs 2 (surge) and 3 (heave). Any later project edit to these
    fields - including a mesh vertex edit - makes a stored linear result
    stale; the binding is never silently rebound.
    """

    body_id: str
    name: str
    reference_position: tuple[float, float, float]
    mass: float
    mesh_hash: str
    # Optional application-LOCAL lid mesh hash (mesh_hash(body.hydro_lid)).
    # The hydrodynamic reference is bound separately (hull mesh hash and
    # reference position); this field only proves which project lid geometry
    # the inputs were built with, so a lid edit/removal/replacement makes the
    # stored inputs and results stale exactly like a hull edit. Omitted when
    # None so existing bindings keep their exact serialized identities and
    # positional construction is unchanged.
    lid_mesh_hash: Optional[str] = None
    surface_hash: Optional[str] = None
    hydro_lid_policy: str = "free_surface_v1"
    # rigid_subset_v1 only (ADR-015 sections 1-2): the retained DOFs in canonical
    # order, the dry mass record and the reference orientation. All omitted from
    # the serialised form when absent/identity, so legacy bindings keep their bytes.
    dofs: Optional[tuple] = None
    mass_properties: Optional[BodyMassProperties] = None
    reference_orientation: tuple = _IDENTITY_ORIENTATION

    def __post_init__(self) -> None:
        if not isinstance(self.body_id, str) or not self.body_id:
            raise ValueError("linear body binding body_id must be a non-empty string")
        if self.dofs is not None:
            dofs = tuple(self.dofs)
            if not dofs or any(not isinstance(d, str) or d not in RIGID_DOFS for d in dofs):
                raise ValueError(f"linear binding dofs must be a non-empty subset of {RIGID_DOFS}")
            if list(dofs) != sorted(set(dofs), key=RIGID_DOFS.index):
                raise ValueError("linear binding dofs must be distinct and in canonical order "
                                 "(surge, sway, heave, roll, pitch, yaw)")
            object.__setattr__(self, "dofs", dofs)
            if self.mass_properties is None:
                raise ValueError("rigid linear bindings require explicit mass_properties (CG and CG inertia)")
        if self.mass_properties is not None and not isinstance(self.mass_properties, BodyMassProperties):
            raise TypeError("linear binding mass_properties must be a BodyMassProperties record")
        if self.mass_properties is not None and self.dofs is None:
            raise ValueError("linear binding mass_properties are only admitted with retained dofs")
        from .result_rotations import validated_rotation_matrix
        object.__setattr__(self, "reference_orientation", validated_rotation_matrix(self.reference_orientation))
        if self.dofs is None and self.reference_orientation != _IDENTITY_ORIENTATION:
            raise ValueError("legacy linear bindings do not support nonidentity reference orientation")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("linear body binding name must be a non-empty string")
        _finite_floats(self.reference_position, "linear binding reference_position", 3)
        mass = float(self.mass)
        if mass != mass or mass <= 0.0 or mass == float("inf"):
            raise ValueError("linear body binding mass must be a positive finite number")
        object.__setattr__(
            self,
            "reference_position",
            _finite_floats(self.reference_position, "linear binding reference_position", 3),
        )
        object.__setattr__(self, "mesh_hash", _hash_digest(self.mesh_hash, "linear binding mesh_hash"))
        if self.hydro_lid_policy not in ("free_surface_v1", "submerged_0p1m_v1"):
            raise ValueError("unsupported binding hydro_lid_policy")
        if self.hydro_lid_policy != "free_surface_v1" and self.lid_mesh_hash is None:
            raise ValueError("explicit binding lid policy requires lid mesh hash")
        if self.surface_hash is not None:
            _hash_digest(self.surface_hash, 'linear binding surface_hash')
        if self.lid_mesh_hash is not None:
            object.__setattr__(
                self,
                "lid_mesh_hash",
                _hash_digest(self.lid_mesh_hash, "linear binding lid_mesh_hash"),
            )

    def to_dict(self) -> dict:
        data = {
            "body_id": self.body_id,
            "name": self.name,
            "reference_position": list(self.reference_position),
            "mass": float(self.mass),
            "mesh_hash": self.mesh_hash,
        }
        if self.lid_mesh_hash is not None:
            # Omitted when None: existing bindings keep their exact bytes and
            # the inputs they belong to keep their exact input hashes.
            data["lid_mesh_hash"] = self.lid_mesh_hash
        if self.hydro_lid_policy != "free_surface_v1":
            data["hydro_lid_policy"] = self.hydro_lid_policy
        if self.surface_hash is not None:
            data["surface_hash"] = self.surface_hash
        if self.dofs is not None:
            data["dofs"] = list(self.dofs)
            data["mass_properties"] = self.mass_properties.to_dict()
        if self.reference_orientation != _IDENTITY_ORIENTATION:
            data["reference_orientation"] = [list(row) for row in self.reference_orientation]
        return data

    @property
    def rigid(self) -> bool:
        return self.dofs is not None

    @classmethod
    def from_dict(cls, data: dict) -> "LinearBodyBinding":
        return cls(
            body_id=str(data["body_id"]),
            name=str(data["name"]),
            reference_position=tuple(float(x) for x in data["reference_position"]),
            mass=float(data["mass"]),
            mesh_hash=str(data["mesh_hash"]),
            lid_mesh_hash=data.get("lid_mesh_hash"),
            surface_hash=data.get("surface_hash"),
            hydro_lid_policy=data.get("hydro_lid_policy", "free_surface_v1"),
            dofs=tuple(data["dofs"]) if data.get("dofs") is not None else None,
            mass_properties=(BodyMassProperties.from_dict(data["mass_properties"])
                             if data.get("mass_properties") is not None else None),
            reference_orientation=data.get("reference_orientation", _IDENTITY_ORIENTATION),
        )


@dataclass(frozen=True)
class ExternalMatrixRecord:
    """Explicit constant external linear damping or stiffness (ADR-015 section 5).

    Ordered solver DOF labels (``body:dof``), SI units, world axes, each body's
    reference point (its binding reference position), additive semantics,
    constant scope and the source identity. The record is provenance: the
    effective solver matrices already contain it; it is hashed with the inputs
    and never folded silently into hydrodynamic coefficients.
    """

    kind: str
    dofs: tuple
    matrix: tuple
    source: str
    axes: str = "world"
    reference: str = "binding_reference_position"
    semantics: str = "additive"
    scope: str = "constant"
    units: str = "SI"

    _FIXED = dict(axes="world", reference="binding_reference_position", semantics="additive",
                  scope="constant", units="SI")
    _KEYS = ("kind", "dofs", "matrix", "source", "axes", "reference", "semantics", "scope", "units")

    def __post_init__(self) -> None:
        if self.kind not in EXTERNAL_MATRIX_KINDS:
            raise ValueError(f"external matrix kind must be one of {EXTERNAL_MATRIX_KINDS}")
        dofs = tuple(self.dofs)
        if not dofs or any(not isinstance(d, str) or ":" not in d for d in dofs) or len(set(dofs)) != len(dofs):
            raise ValueError("external matrix dofs must be distinct solver labels 'body:dof'")
        object.__setattr__(self, "dofs", dofs)
        object.__setattr__(self, "matrix", _frozen_matrix("external matrix", self.matrix, len(dofs), len(dofs)))
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("external matrix source must be a non-empty string")
        for key, value in self._FIXED.items():
            if getattr(self, key) != value:
                raise ValueError(f"external matrix {key} must be {value!r}")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "dofs": list(self.dofs), "matrix": [list(row) for row in self.matrix],
                "source": self.source, **self._FIXED}

    @classmethod
    def from_dict(cls, data) -> "ExternalMatrixRecord":
        if not isinstance(data, Mapping) or set(data) != set(cls._KEYS):
            raise ValueError(f"external matrix record must contain exactly {list(cls._KEYS)}")
        return cls(kind=data["kind"], dofs=tuple(data["dofs"]),
                   matrix=tuple(tuple(float(x) for x in row) for row in data["matrix"]), source=data["source"],
                   **{key: data[key] for key in cls._FIXED})


@dataclass(frozen=True)
class LinearProvenance:
    """Where the effective linear numerical inputs came from.

    ``details`` is an optional frozen JSON-like structure carrying the
    immutable input-owned import evidence (full source identity, criteria,
    report, settings). It is serialized only when populated, so existing
    explicit/demo inputs hash exactly as before; for hydro-imported inputs
    it is required and covered by ``linear_input_hash``. It must never
    contain external paths, wall-clock timestamps or a circular
    ``linear_input_hash``.
    """

    source: str
    description: str = ""
    created_utc: str = ""
    details: object = None

    def __post_init__(self) -> None:
        if self.source not in LINEAR_SOURCES:
            raise ValueError(
                f"linear provenance source must be one of {LINEAR_SOURCES}; "
                f"received {self.source!r}"
            )
        if not isinstance(self.description, str):
            raise ValueError("linear provenance description must be a string")
        if self.details is not None:
            object.__setattr__(
                self, "details", _freeze_json_value(self.details, "linear provenance details")
            )

    def to_dict(self) -> dict:
        data = {
            "source": self.source,
            "description": self.description,
            "created_utc": self.created_utc,
        }
        if self.details is not None:
            # Serialized only when populated: existing explicit/demo inputs
            # keep their exact bytes and hashes.
            data["details"] = _jsonable_value(self.details)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "LinearProvenance":
        return cls(
            source=str(data["source"]),
            description=str(data.get("description", "")),
            created_utc=str(data.get("created_utc", "")),
            details=data.get("details"),
        )


HYDRO_PROVENANCE_KEYS = (
    "source",    "dataset_content_hash",
    "kernel_content_hash",
    "dataset_raw_sha256",
    "kernel_raw_sha256",
    "dataset_sidecar",
    "kernel_sidecar",
    "dataset_source_path",
    "kernel_source_path",
    "request_hash",
    "body_ids",
    "dof_keys",
    "mesh_hashes",
    "a_inf_source",
    "phasor_convention",
    "source_qualification_status",
    "source_schema_version",
    "source_backend",
    "reference_transformations",
    "criteria",
    "reconstruction_report",
    "kernel_settings",
    "diagnostics",
    "assumptions",
    "wave",
    "surge_link_stiffness",
    "dt",
    "t0",
    "n_steps",
    "linear_input_hash",
    "created_utc",
)

# Import route labels: the legacy raw dataset/kernel route and the explicit
# derived-operator route are distinct public identities; the derived route is
# never inferred from a failed raw import and never used as a fallback.
HYDRO_ROUTE_RAW = "raw_dataset"
HYDRO_ROUTE_DERIVED = "derived_operators"
# Required keys of the optional frozen derived-operator provenance bundle.
DERIVED_BUNDLE_KEYS = (
    "route",
    "derived_content_hash",
    "operators_raw_sha256",
    "operators_sidecar",
    "operators_source_path",
    "derived_settings",
    "derived_report",
    "derived_admitted",
    # Declared initial state and execution limits: the effective inputs are
    # recompiled from the stored sources under these declared controls, so
    # the bundle must carry them explicitly (never re-defaulted).
    "q0",
    "v0",
    "execution_limits",
)
# Rigid_subset_v1 route (ADR-015 L6-3 M-c): raw dataset + kernel re-run from the
# stored sidecars under the bundle's plain caller options.
HYDRO_ROUTE_RIGID = "rigid_raw_v1"
RIGID_BUNDLE_KEYS = ("route", "layout", "format", "options")


def _freeze_json_value(value, name: str):
    """Recursively freeze a JSON-like value into immutable containers."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json_value(item, f"{name}[{key!r}]") for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, f"{name}[{index}]") for index, item in enumerate(value))
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        return float(value)
    raise ValueError(f"{name} must contain only JSON-like values; got {type(value).__name__}")


def _jsonable_value(value):
    """Plain JSON-safe copy of a frozen structure (for canonical hashing)."""

    if isinstance(value, Mapping):
        return {str(key): _jsonable_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_value(item) for item in value]
    return value


@dataclass(frozen=True)
class HydroBridgeProvenance:
    """Immutable structured provenance of an ANYhydro dataset/kernel import.

    Records exactly what the import admitted and where it came from: source
    and content hashes, the raw project sidecars holding the authoritative
    dataset/kernel files, the exact selected DOF keys and body order, the
    caller reconstruction criteria and the recomputed report, kernel
    settings and diagnostics, the optional regular-wave selection, and the
    hash of the effective linear inputs this provenance describes. The
    record is deeply immutable: nested structures are frozen recursively.
    It is development provenance and never a qualification claim.
    """

    dataset_content_hash: str
    kernel_content_hash: str
    dataset_raw_sha256: str
    kernel_raw_sha256: str
    dataset_sidecar: str
    kernel_sidecar: str
    dataset_source_path: str
    kernel_source_path: str
    request_hash: str
    body_ids: tuple[str, ...]
    dof_keys: tuple[str, ...]
    mesh_hashes: tuple[str, ...]
    a_inf_source: str
    phasor_convention: str
    source_qualification_status: str
    source_schema_version: str
    source_backend: str
    reference_transformations: object
    criteria: object
    reconstruction_report: object
    kernel_settings: object
    diagnostics: object
    assumptions: tuple[str, ...]
    wave: object
    surge_link_stiffness: float
    dt: float
    t0: float
    n_steps: int
    linear_input_hash: str
    created_utc: str = ""
    # Optional per-body world-space lid mesh hashes (None per body without a
    # lid). Additive optional field: omitted entirely (None) when the import
    # involved no lid, so legacy provenance records keep their exact
    # serialized identities; old records without the key keep loading.
    lid_mesh_hashes: Optional[tuple[Optional[str], ...]] = None
    # Optional frozen derived-operator provenance bundle, present only for
    # the explicit derived import route. Additive optional field: omitted
    # entirely (None) for the legacy raw route so legacy provenance records
    # keep their exact serialized identities and input hashes; old records
    # without the key keep loading. No partial derived intent is allowed:
    # when present, every key below is required.
    derived_bundle: Optional[Mapping] = None
    auxiliary_lid_policy: Optional[str] = None
    # Optional rigid_subset_v1 bundle (ADR-015 L6-3 M-c): the route, layout,
    # import format and the plain caller options, re-run from the stored
    # sidecars on verification. Additive optional field: omitted entirely
    # (None) for legacy records, which keep their exact serialized
    # identities. When present the record describes N bodies with canonical
    # rigid DOF keys and no surge link, lid or derived route.
    rigid_bundle: Optional[Mapping] = None

    def __post_init__(self) -> None:
        if self.auxiliary_lid_policy is not None and self.auxiliary_lid_policy != "submerged_0p1m_v1":
            raise ValueError("unsupported provenance auxiliary_lid_policy")
        rigid = self.rigid_bundle is not None
        for name in (
            "dataset_content_hash",
            "kernel_content_hash",
            "dataset_raw_sha256",
            "kernel_raw_sha256",
            "linear_input_hash",
        ):
            object.__setattr__(self, name, _hash_digest(getattr(self, name), f"hydro {name}"))
        for name in ("dataset_sidecar", "kernel_sidecar"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"hydro {name} must be a non-empty relative sidecar reference")
        for name in (
            "dataset_source_path",
            "kernel_source_path",
            "request_hash",
            "a_inf_source",
            "phasor_convention",
            "source_qualification_status",
            "source_schema_version",
            "source_backend",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"hydro {name} must be a non-empty string")
        body_ids = tuple(str(item) for item in self.body_ids)
        if rigid:
            if not body_ids or len(set(body_ids)) != len(body_ids) or not all(body_ids):
                raise ValueError("rigid hydro provenance body_ids must be distinct non-empty ids")
        elif len(body_ids) != 2 or len(set(body_ids)) != 2 or not all(body_ids):
            raise ValueError("hydro provenance body_ids must be exactly 2 distinct ids")
        object.__setattr__(self, "body_ids", body_ids)
        dof_keys = tuple(str(item) for item in self.dof_keys)
        if rigid:
            allowed = {f"{body}::{dof}" for body in body_ids for dof in RIGID_DOFS}
            if not dof_keys or len(set(dof_keys)) != len(dof_keys) or not set(dof_keys) <= allowed:
                raise ValueError("rigid hydro provenance dof_keys must be distinct 'body::dof' keys of its bodies")
        elif len(dof_keys) != LINEAR_DOF_COUNT or not all(dof_keys):
            raise ValueError(
                f"hydro provenance dof_keys must contain exactly {LINEAR_DOF_COUNT} "
                "ordered DOF keys"
            )
        object.__setattr__(self, "dof_keys", dof_keys)
        mesh_hashes = tuple(_hash_digest(item, "hydro mesh hash") for item in self.mesh_hashes)
        if len(mesh_hashes) != len(body_ids):
            raise ValueError(f"hydro provenance mesh_hashes must contain exactly {len(body_ids)} entries")
        object.__setattr__(self, "mesh_hashes", mesh_hashes)
        object.__setattr__(self, "assumptions", tuple(str(item) for item in self.assumptions))
        for name in ("reference_transformations", "criteria", "reconstruction_report",
                     "kernel_settings", "diagnostics", "wave"):
            object.__setattr__(
                self, name, _freeze_json_value(getattr(self, name), f"hydro {name}")
            )
        if self.wave is not None:
            wave = dict(self.wave)
            for key in ("omega", "heading", "amplitude", "phase"):
                if key not in wave:
                    raise ValueError(f"hydro provenance wave selection needs {key!r}")
        link = float(self.surge_link_stiffness)
        if link != link or link < 0.0 or link == float("inf"):
            raise ValueError("hydro surge_link_stiffness must be a nonnegative finite number")
        object.__setattr__(self, "surge_link_stiffness", link)
        dt = float(self.dt)
        if dt != dt or dt <= 0.0 or dt == float("inf"):
            raise ValueError("hydro dt must be a positive finite number")
        object.__setattr__(self, "dt", dt)
        t0 = float(self.t0)
        if not math.isfinite(t0):
            raise ValueError("hydro t0 must be a finite number")
        object.__setattr__(self, "t0", t0)
        n_steps = self.n_steps
        if isinstance(n_steps, bool) or not isinstance(n_steps, int) or n_steps < 2:
            raise ValueError("hydro n_steps must be an integer >= 2")
        object.__setattr__(self, "n_steps", int(n_steps))
        if not isinstance(self.created_utc, str):
            raise ValueError("hydro created_utc must be a string")
        if self.lid_mesh_hashes is not None:
            lid_hashes = tuple(
                None if item is None else _hash_digest(item, "hydro lid mesh hash")
                for item in self.lid_mesh_hashes
            )
            if len(lid_hashes) != len(body_ids) or (rigid and all(item is None for item in lid_hashes)):
                raise ValueError(
                    f"hydro provenance lid_mesh_hashes must contain exactly {len(body_ids)} entries"
                    + (" and at least one lid (omitted when lidless)" if rigid else "")
                )
            object.__setattr__(self, "lid_mesh_hashes", lid_hashes)
        if self.derived_bundle is not None:
            bundle = _freeze_json_value(self.derived_bundle, "hydro derived bundle")
            if not isinstance(bundle, Mapping):
                raise ValueError("hydro derived bundle must be a JSON object")
            missing = sorted(set(DERIVED_BUNDLE_KEYS) - set(bundle))
            if missing:
                raise ValueError(
                    "hydro derived bundle is missing key(s) "
                    f"{missing}; no partial derived intent is allowed"
                )
            for key in ("derived_content_hash", "operators_raw_sha256"):
                _hash_digest(bundle[key], f"hydro derived bundle {key}")
            for key in ("operators_sidecar", "operators_source_path"):
                if not isinstance(bundle[key], str) or not str(bundle[key]).strip():
                    raise ValueError(
                        f"hydro derived bundle {key} must be a non-empty string"
                    )
            if bundle["route"] != HYDRO_ROUTE_DERIVED:
                raise ValueError(
                    "hydro derived bundle route must be "
                    f"{HYDRO_ROUTE_DERIVED!r}; received {bundle['route']!r}"
                )
            for key in ("q0", "v0"):
                vector = bundle[key]
                if (
                    not isinstance(vector, (list, tuple))
                    or len(vector) != LINEAR_DOF_COUNT
                ):
                    raise ValueError(
                        f"hydro derived bundle {key} must contain exactly "
                        f"{LINEAR_DOF_COUNT} entries"
                    )
                if any(
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not math.isfinite(float(item))
                    for item in vector
                ):
                    raise ValueError(
                        f"hydro derived bundle {key} must contain finite "
                        "numbers"
                    )
            limits = bundle["execution_limits"]
            if limits is not None and not isinstance(limits, Mapping):
                raise ValueError(
                    "hydro derived bundle execution_limits must be a JSON "
                    "object or null"
                )
            object.__setattr__(self, "derived_bundle", bundle)
        if rigid:
            bundle = _freeze_json_value(self.rigid_bundle, "hydro rigid bundle")
            if not isinstance(bundle, Mapping) or set(bundle) != set(RIGID_BUNDLE_KEYS):
                raise ValueError(f"hydro rigid bundle must contain exactly {list(RIGID_BUNDLE_KEYS)}")
            if bundle["route"] != HYDRO_ROUTE_RIGID or bundle["layout"] != LINEAR_LAYOUT_RIGID:
                raise ValueError(f"hydro rigid bundle must declare route {HYDRO_ROUTE_RIGID!r} and layout "
                                 f"{LINEAR_LAYOUT_RIGID!r}")
            if not isinstance(bundle["format"], str) or not isinstance(bundle["options"], Mapping):
                raise ValueError("hydro rigid bundle format must be a string and options a JSON object")
            if (self.surge_link_stiffness != 0.0 or self.derived_bundle is not None
                    or self.auxiliary_lid_policy is not None):
                raise ValueError("a rigid hydro provenance carries no surge link, auxiliary lid or derived route")
            object.__setattr__(self, "rigid_bundle", bundle)

    def to_dict(self) -> dict:
        data = {
            "source": LINEAR_SOURCE_HYDRO,
            "dataset_content_hash": self.dataset_content_hash,
            "kernel_content_hash": self.kernel_content_hash,
            "dataset_raw_sha256": self.dataset_raw_sha256,
            "kernel_raw_sha256": self.kernel_raw_sha256,
            "dataset_sidecar": self.dataset_sidecar,
            "kernel_sidecar": self.kernel_sidecar,
            "dataset_source_path": self.dataset_source_path,
            "kernel_source_path": self.kernel_source_path,
            "request_hash": self.request_hash,
            "body_ids": list(self.body_ids),
            "dof_keys": list(self.dof_keys),
            "mesh_hashes": list(self.mesh_hashes),
            "a_inf_source": self.a_inf_source,
            "phasor_convention": self.phasor_convention,
            "source_qualification_status": self.source_qualification_status,
            "source_schema_version": self.source_schema_version,
            "source_backend": self.source_backend,
            "reference_transformations": _jsonable_value(self.reference_transformations),
            "criteria": _jsonable_value(self.criteria),
            "reconstruction_report": _jsonable_value(self.reconstruction_report),
            "kernel_settings": _jsonable_value(self.kernel_settings),
            "diagnostics": _jsonable_value(self.diagnostics),
            "assumptions": list(self.assumptions),
            "wave": None if self.wave is None else _jsonable_value(self.wave),
            "surge_link_stiffness": float(self.surge_link_stiffness),
            "dt": float(self.dt),
            "t0": float(self.t0),
            "n_steps": int(self.n_steps),
            "linear_input_hash": self.linear_input_hash,
            "created_utc": self.created_utc,
        }
        if self.lid_mesh_hashes is not None:
            # Omitted when None: lidless imports keep their exact legacy
            # serialized provenance identities.
            data["lid_mesh_hashes"] = [
                None if item is None else item for item in self.lid_mesh_hashes
            ]
        if self.auxiliary_lid_policy is not None:
            data["auxiliary_lid_policy"] = self.auxiliary_lid_policy
        if self.derived_bundle is not None:
            # Omitted when None: raw-route provenance records keep their
            # exact legacy serialized identities.
            data["derived_bundle"] = _jsonable_value(self.derived_bundle)
        if self.rigid_bundle is not None:
            # Omitted when None: legacy records keep their exact identities.
            data["rigid_bundle"] = _jsonable_value(self.rigid_bundle)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "HydroBridgeProvenance":
        expected = set(HYDRO_PROVENANCE_KEYS)
        received = set(data)
        # Additive optional fields: records saved before lid/derived support
        # carry neither key and keep loading unchanged; nothing unknown is
        # ever silently ignored.
        optional = {"lid_mesh_hashes", "derived_bundle", "auxiliary_lid_policy", "rigid_bundle"}
        unknown = sorted(received - expected - optional)
        if unknown:
            raise ValueError(
                f"hydro provenance contains unknown key(s) {unknown}; expected "
                f"exactly {sorted(expected)}"
            )
        missing = sorted(expected - received)
        if missing:
            raise ValueError(
                f"hydro provenance must contain exactly {sorted(expected)}; "
                f"missing {missing}"
            )
        if data["source"] != LINEAR_SOURCE_HYDRO:
            raise ValueError(
                f"hydro provenance source must be {LINEAR_SOURCE_HYDRO!r}; "
                f"received {data['source']!r}"
            )
        return cls(
            dataset_content_hash=str(data["dataset_content_hash"]),
            auxiliary_lid_policy=data.get("auxiliary_lid_policy"),
            rigid_bundle=data.get("rigid_bundle"),
            kernel_content_hash=str(data["kernel_content_hash"]),
            dataset_raw_sha256=str(data["dataset_raw_sha256"]),
            kernel_raw_sha256=str(data["kernel_raw_sha256"]),
            dataset_sidecar=str(data["dataset_sidecar"]),
            kernel_sidecar=str(data["kernel_sidecar"]),
            dataset_source_path=str(data["dataset_source_path"]),
            kernel_source_path=str(data["kernel_source_path"]),
            request_hash=str(data["request_hash"]),
            body_ids=tuple(str(item) for item in data["body_ids"]),
            dof_keys=tuple(str(item) for item in data["dof_keys"]),
            mesh_hashes=tuple(str(item) for item in data["mesh_hashes"]),
            a_inf_source=str(data["a_inf_source"]),
            phasor_convention=str(data["phasor_convention"]),
            source_qualification_status=str(data["source_qualification_status"]),
            source_schema_version=str(data["source_schema_version"]),
            source_backend=str(data["source_backend"]),
            reference_transformations=data["reference_transformations"],
            criteria=data["criteria"],
            reconstruction_report=data["reconstruction_report"],
            kernel_settings=data["kernel_settings"],
            diagnostics=data["diagnostics"],
            assumptions=tuple(str(item) for item in data["assumptions"]),
            wave=data["wave"],
            surge_link_stiffness=float(data["surge_link_stiffness"]),
            dt=float(data["dt"]),
            t0=float(data["t0"]),
            n_steps=data["n_steps"],
            linear_input_hash=str(data["linear_input_hash"]),
            created_utc=str(data.get("created_utc", "")),
            lid_mesh_hashes=(
                tuple(
                    None if item is None else str(item)
                    for item in data["lid_mesh_hashes"]
                )
                if data.get("lid_mesh_hashes") is not None
                else None
            ),
            derived_bundle=(
                data["derived_bundle"]
                if data.get("derived_bundle") is not None
                else None
            ),
        )


def _frozen_matrix(name: str, value, rows: int, columns: int) -> tuple[tuple[float, ...], ...]:
    """Validate and freeze one rectangular float matrix as nested tuples."""

    try:
        rows_list = list(value)
    except TypeError as error:
        raise ValueError(f"{name} must be a {rows}x{columns} numeric matrix") from error
    if len(rows_list) != rows:
        raise ValueError(f"{name} must have exactly {rows} rows; received {len(rows_list)}")
    frozen = []
    for row in rows_list:
        frozen.append(tuple(_finite_floats(row, f"{name} row", columns)))
    return tuple(frozen)


@dataclass(frozen=True)
class LinearExecutionLimits:
    """Frozen public execution limits for one linear development job.

    The default preserves the historical development job bound of
    ``LINEAR_MAX_FRAMES`` (1001) frames. An explicit ``max_frames`` admits
    longer development jobs up to the hard ceiling
    ``LINEAR_MAX_FRAMES_CEILING`` (20001). Every control is validated at
    construction and in the explicit JSON form: booleans, fractional,
    non-finite, non-positive, over-ceiling and unknown values are rejected
    before any allocation, import or job starts. These are execution bounds
    only: a larger limit confers no qualification and never weakens any
    scientific tolerance.
    """

    max_frames: int = LINEAR_MAX_FRAMES
    # Optional explicit controls (ADR-015 section 9); omitted when None so
    # existing records keep their bytes. Radiation samples are enforced at input
    # construction; wall time by the worker/CLI (L6-2).
    max_radiation_samples: Optional[int] = None
    max_wall_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        if self.max_radiation_samples is not None:
            samples = self.max_radiation_samples
            if isinstance(samples, bool) or not isinstance(samples, int) or samples < 2:
                raise ValueError("linear execution limits max_radiation_samples must be an integer >= 2")
            if samples > LINEAR_MAX_FRAMES_CEILING:
                raise ValueError(f"linear execution limits max_radiation_samples exceeds the hard ceiling of "
                                 f"{LINEAR_MAX_FRAMES_CEILING}")
        if self.max_wall_seconds is not None:
            seconds = self.max_wall_seconds
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) \
                    or seconds <= 0:
                raise ValueError("linear execution limits max_wall_seconds must be a positive finite number")
            object.__setattr__(self, "max_wall_seconds", float(seconds))
        value = self.max_frames
        if isinstance(value, bool):
            raise ValueError(
                "linear execution limits max_frames must be an integer; "
                "booleans are rejected"
            )
        if isinstance(value, int):
            frames = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(
                    "linear execution limits max_frames must be a finite "
                    "integer; non-finite values are rejected"
                )
            if not value.is_integer():
                raise ValueError(
                    f"linear execution limits max_frames must be an integer; "
                    f"fractional frame counts are rejected, never truncated "
                    f"(received {value!r})"
                )
            frames = int(value)
        else:
            raise ValueError(
                "linear execution limits max_frames must be an integer; "
                f"received {value!r}"
            )
        if frames <= 1:
            raise ValueError(
                f"linear execution limits max_frames must be > 1; received {frames}"
            )
        if frames > LINEAR_MAX_FRAMES_CEILING:
            raise ValueError(
                f"linear execution limits max_frames {frames} exceeds the hard "
                f"ceiling of {LINEAR_MAX_FRAMES_CEILING} frames; longer "
                "development jobs are refused"
            )
        object.__setattr__(self, "max_frames", int(frames))

    def to_dict(self) -> dict:
        data = {"max_frames": int(self.max_frames)}
        if self.max_radiation_samples is not None:
            data["max_radiation_samples"] = int(self.max_radiation_samples)
        if self.max_wall_seconds is not None:
            data["max_wall_seconds"] = float(self.max_wall_seconds)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "LinearExecutionLimits":
        if not isinstance(data, Mapping):
            raise ValueError(
                "linear execution limits must be a JSON object with a "
                "max_frames control"
            )
        allowed = {"max_frames", "max_radiation_samples", "max_wall_seconds"}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(
                f"linear execution limits contain unknown control(s) {unknown}; "
                f"only {sorted(allowed)} are supported and nothing is silently ignored"
            )
        if "max_frames" not in data:
            raise ValueError(
                "linear execution limits must declare max_frames explicitly"
            )
        return cls(max_frames=data["max_frames"], max_radiation_samples=data.get("max_radiation_samples"),
                   max_wall_seconds=data.get("max_wall_seconds"))


@dataclass(frozen=True)
class LinearDevelopmentInputs:
    """Immutable four-DOF two-body surge/heave linear development inputs.

    All numerical input needed by the linear route is captured here exactly
    once and frozen: the fixed grid (``t0``, ``dt``, ``n_steps``), the full
    coupled matrices ``mass_dry``/``added_mass_infinite``/``damping``/
    ``stiffness`` (4x4, cross-DOF terms retained), the sampled causal
    radiation kernel with its grid, the external forcing and the initial
    state ``q0``/``v0``. DOF order is [body0 surge, body0 heave, body1
    surge, body1 heave]; surge maps to x and heave to z about the bound
    bodies' fixed equilibrium reference positions with zero rotation.

    Construction fails closed on non-finite values, malformed shapes,
    incompatible grids (kernel_times[k] must equal k*dt) and out-of-bound
    job sizes (``n_steps`` <= ``LINEAR_MAX_FRAMES``). The record is deeply
    immutable: every container is rebuilt as nested tuples.
    """

    bindings: tuple[LinearBodyBinding, ...]
    dt: float
    n_steps: int
    forces: tuple[tuple[float, float, float, float], ...]
    mass_dry: tuple[tuple[float, float, float, float], ...]
    added_mass_infinite: tuple[tuple[float, float, float, float], ...]
    damping: tuple[tuple[float, float, float, float], ...]
    stiffness: tuple[tuple[float, float, float, float], ...]
    radiation_kernel: tuple[tuple[tuple[float, float, float, float], ...], ...]
    kernel_times: tuple[float, ...]
    equilibrium_hash: str
    provenance: LinearProvenance
    t0: float = 0.0
    q0: Optional[tuple[float, float, float, float]] = None
    v0: Optional[tuple[float, float, float, float]] = None
    # Optional explicit execution limits. None preserves the legacy default
    # bound (LINEAR_MAX_FRAMES), the exact legacy serialization and the exact
    # legacy input hash; an explicit record is serialized and covered by the
    # input hash, and its max_frames is the enforced job bound.
    execution_limits: Optional[LinearExecutionLimits] = None
    surface_geometry: Optional[Mapping] = None
    # ADR-015: the legacy layout is never serialised (exact legacy bytes/hashes).
    layout: str = LINEAR_LAYOUT_LEGACY
    external_matrices: tuple = ()

    def __post_init__(self) -> None:
        if self.layout not in LINEAR_LAYOUTS:
            raise ValueError(f"unknown linear layout {self.layout!r}; supported: {LINEAR_LAYOUTS}")
        bindings = tuple(self.bindings)
        for binding in bindings:
            if not isinstance(binding, LinearBodyBinding):
                raise TypeError("linear bindings must be LinearBodyBinding records")
        if self.layout == LINEAR_LAYOUT_LEGACY:
            if len(bindings) != 2:
                raise ValueError(
                    f"linear development inputs bind exactly 2 bodies in order; "
                    f"received {len(bindings)} binding(s)"
                )
            if any(binding.rigid for binding in bindings):
                raise ValueError("the legacy two-body surge/heave layout refuses bindings with retained dofs "
                                 "or mass properties (use rigid_subset_v1)")
        else:
            if not bindings or not all(binding.rigid for binding in bindings):
                raise ValueError("rigid_subset_v1 needs at least one binding, each with retained dofs and "
                                 "mass properties")
            ndof = sum(len(binding.dofs) for binding in bindings)
            if ndof > LINEAR_RIGID_MAX_DOFS:
                raise ValueError(f"rigid_subset_v1 retains {ndof} DOFs; more than {LINEAR_RIGID_MAX_DOFS} is "
                                 "refused until assessed (ADR-015 section 9)")
        ids = [binding.body_id for binding in bindings]
        if len(set(ids)) != len(ids):
            raise ValueError("linear bindings must reference distinct body ids")
        object.__setattr__(self, "bindings", bindings)

        dt = float(self.dt)
        if dt != dt or dt <= 0.0 or dt == float("inf"):
            raise ValueError("linear dt must be a positive finite number")
        object.__setattr__(self, "dt", dt)
        t0 = float(self.t0)
        if not math.isfinite(t0):
            raise ValueError(
                "linear t0 must be a finite number (both signs); infinite "
                "origins are refused"
            )
        object.__setattr__(self, "t0", t0)

        n_steps = self.n_steps
        if isinstance(n_steps, bool) or not isinstance(n_steps, int):
            raise ValueError(
                f"linear n_steps must be an integer; received {n_steps!r} "
                "(fractional frame counts are rejected, never truncated)"
            )
        if n_steps < 2:
            raise ValueError(
                f"linear n_steps must be >= 2 (initial state plus one step); "
                f"received {n_steps}"
            )
        if self.execution_limits is not None and not isinstance(
            self.execution_limits, LinearExecutionLimits
        ):
            raise TypeError(
                "linear execution_limits must be a LinearExecutionLimits record "
                "or None"
            )
        # Execution bound enforced before any allocation: the effective job
        # bound is the explicit record's max_frames when provided, otherwise
        # the legacy default. This is an execution bound, never a
        # qualification.
        effective_max_frames = (
            self.execution_limits.max_frames
            if self.execution_limits is not None
            else LINEAR_MAX_FRAMES
        )
        if n_steps > effective_max_frames:
            raise ValueError(
                f"linear n_steps {n_steps} exceeds the development job bound of "
                f"{effective_max_frames} frames"
            )
        object.__setattr__(self, "n_steps", int(n_steps))
        ndof = self.ndof
        # The derived time grid t0 + k*dt must stay finite: an overflowing
        # dt*n product is refused at construction, not discovered later.
        if not math.isfinite(t0 + (n_steps - 1) * dt):
            raise ValueError(
                "the derived linear time grid t0 + k*dt is not finite "
                "(dt*n_steps overflows); refusing the inputs"
            )

        forces = _frozen_matrix("linear forces", self.forces, n_steps, ndof)
        object.__setattr__(self, "forces", forces)
        for name in ("mass_dry", "added_mass_infinite", "damping", "stiffness"):
            object.__setattr__(
                self, name, _frozen_matrix(f"linear {name}", getattr(self, name), ndof, ndof)
            )

        kernel = tuple(self.radiation_kernel)
        if len(kernel) < n_steps:
            raise ValueError(
                f"linear radiation_kernel has {len(kernel)} samples but must cover "
                f"every lag of the {n_steps}-step grid"
            )
        if (self.execution_limits is not None and self.execution_limits.max_radiation_samples is not None
                and len(kernel) > self.execution_limits.max_radiation_samples):
            raise ValueError(
                f"linear radiation_kernel has {len(kernel)} samples, more than the declared "
                f"max_radiation_samples {self.execution_limits.max_radiation_samples}"
            )
        frozen_kernel = tuple(
            _frozen_matrix("linear radiation_kernel sample", sample, ndof, ndof)
            for sample in kernel
        )
        object.__setattr__(self, "radiation_kernel", frozen_kernel)

        kernel_times = _finite_floats(self.kernel_times, "linear kernel_times", len(kernel))
        for index, value in enumerate(kernel_times):
            expected = index * dt
            if abs(value - expected) > LINEAR_GRID_RELATIVE_TOLERANCE * max(1.0, abs(expected)) + 1.0e-12:
                raise ValueError(
                    f"linear kernel_times[{index}] = {value!r} does not match the "
                    f"fixed grid k*dt = {expected!r}; incompatible grids are refused"
                )
        object.__setattr__(self, "kernel_times", kernel_times)

        q0 = self.q0 if self.q0 is not None else (0.0,) * ndof
        v0 = self.v0 if self.v0 is not None else (0.0,) * ndof
        object.__setattr__(self, "q0", _finite_floats(q0, "linear q0", ndof))
        object.__setattr__(self, "v0", _finite_floats(v0, "linear v0", ndof))

        externals = tuple(self.external_matrices)
        if externals and self.layout == LINEAR_LAYOUT_LEGACY:
            raise ValueError("external matrix records require the rigid_subset_v1 layout")
        for record in externals:
            if not isinstance(record, ExternalMatrixRecord):
                raise TypeError("linear external_matrices must be ExternalMatrixRecord records")
            unknown = [d for d in record.dofs if d not in self.labels]
            if unknown:
                raise ValueError(f"external matrix dofs {unknown} are not retained DOFs of these inputs")
        object.__setattr__(self, "external_matrices", externals)
        if self.layout == LINEAR_LAYOUT_RIGID:
            self._verify_rigid_dry_mass()

        if not isinstance(self.provenance, LinearProvenance):
            raise TypeError("linear provenance must be a LinearProvenance record")
        object.__setattr__(
            self,
            "equilibrium_hash",
            _hash_digest(self.equilibrium_hash, "linear equilibrium_hash"),
        )

        has_bound_surfaces = any(b.surface_hash is not None or b.hydro_lid_policy != "free_surface_v1" for b in self.bindings)
        if has_bound_surfaces != (self.surface_geometry is not None):
            raise ValueError('surface-bound inputs require immutable producing geometry; hull-only inputs cannot carry it')
        if self.surface_geometry is not None:
            from .surface_assignments import geometry_project
            from .linear import verify_linear_binding, LinearBindingError
            try:
                verify_linear_binding(geometry_project(self.surface_geometry, self), self)
            except LinearBindingError as error:
                raise ValueError(f'invalid producing surface geometry: {error}') from error
            object.__setattr__(self, 'surface_geometry', _freeze_json_value(self.surface_geometry, 'surface_geometry'))

    # ---------------------------------------------------------------- views

    @property
    def ndof(self) -> int:
        if self.layout == LINEAR_LAYOUT_LEGACY:
            return LINEAR_DOF_COUNT
        return sum(len(binding.dofs) for binding in self.bindings)

    def _verify_rigid_dry_mass(self) -> None:
        """mass_dry must be the solver-owned RigidBody6D assembly (ADR-015 section 2)."""
        from .linear import rigid_dry_mass
        try:
            expected = rigid_dry_mass(self.bindings)
        except Exception as error:   # solver unavailable or inadmissible mass record: fail closed
            raise ValueError(f"rigid_subset_v1 dry mass cannot be assembled: {error}") from error
        if not all(math.isfinite(v) for row in expected for v in row):
            raise ValueError("rigid_subset_v1 dry mass assembly is not finite (CG/inertia/mass out of range)")
        scale = max(abs(v) for row in expected for v in row) or 1.0
        worst = max(abs(a - b) for row_a, row_b in zip(self.mass_dry, expected) for a, b in zip(row_a, row_b))
        if worst > LINEAR_DRY_MASS_RELATIVE_TOLERANCE * scale:
            raise ValueError(
                f"rigid_subset_v1 mass_dry differs from the RigidBody6D dry mass assembly by {worst:.3e} "
                f"(> {LINEAR_DRY_MASS_RELATIVE_TOLERANCE:g} x {scale:.3e}); dry mass is solver-owned (ADR-003)"
            )

    @property
    def labels(self) -> tuple[str, ...]:
        if self.layout == LINEAR_LAYOUT_RIGID:
            return tuple(f"{binding.body_id}:{dof}" for binding in self.bindings for dof in binding.dofs)
        body_a, body_b = self.bindings
        return (
            f"{body_a.body_id}:surge",
            f"{body_a.body_id}:heave",
            f"{body_b.body_id}:surge",
            f"{body_b.body_id}:heave",
        )

    @property
    def times(self) -> tuple[float, ...]:
        """Fixed grid t0 + k*dt, recomputed from the integer step count."""

        return tuple(self.t0 + index * self.dt for index in range(self.n_steps))

    def to_dict(self) -> dict:
        data = {
            "bindings": [binding.to_dict() for binding in self.bindings],
            "dt": float(self.dt),
            "t0": float(self.t0),
            "n_steps": int(self.n_steps),
            "forces": [list(row) for row in self.forces],
            "mass_dry": [list(row) for row in self.mass_dry],
            "added_mass_infinite": [list(row) for row in self.added_mass_infinite],
            "damping": [list(row) for row in self.damping],
            "stiffness": [list(row) for row in self.stiffness],
            "radiation_kernel": [[list(row) for row in sample] for sample in self.radiation_kernel],
            "kernel_times": [float(value) for value in self.kernel_times],
            "equilibrium_hash": self.equilibrium_hash,
            "q0": list(self.q0),
            "v0": list(self.v0),
            "provenance": self.provenance.to_dict(),
        }
        if self.surface_geometry is not None:
            data["surface_geometry"] = _jsonable_value(self.surface_geometry)
        if self.execution_limits is not None:
            # Omitted when None: legacy inputs keep their exact bytes and
            # input hashes.
            data["execution_limits"] = self.execution_limits.to_dict()
        if self.layout != LINEAR_LAYOUT_LEGACY:
            data["layout"] = self.layout
        if self.external_matrices:
            data["external_matrices"] = [record.to_dict() for record in self.external_matrices]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "LinearDevelopmentInputs":
        return cls(
            bindings=tuple(LinearBodyBinding.from_dict(item) for item in data["bindings"]),
            dt=float(data["dt"]),
            t0=float(data.get("t0", 0.0)),
            # Raw value, no int(...) coercion: fractional or boolean frame
            # counts are rejected by the constructor instead of truncated.
            n_steps=data["n_steps"],
            forces=tuple(tuple(float(x) for x in row) for row in data["forces"]),
            mass_dry=tuple(tuple(float(x) for x in row) for row in data["mass_dry"]),
            added_mass_infinite=tuple(
                tuple(float(x) for x in row) for row in data["added_mass_infinite"]
            ),
            damping=tuple(tuple(float(x) for x in row) for row in data["damping"]),
            stiffness=tuple(tuple(float(x) for x in row) for row in data["stiffness"]),
            radiation_kernel=tuple(
                tuple(tuple(float(x) for x in row) for row in sample)
                for sample in data["radiation_kernel"]
            ),
            kernel_times=tuple(float(x) for x in data["kernel_times"]),
            equilibrium_hash=str(data["equilibrium_hash"]),
            q0=tuple(float(x) for x in data["q0"]) if data.get("q0") is not None else None,
            v0=tuple(float(x) for x in data["v0"]) if data.get("v0") is not None else None,
            provenance=LinearProvenance.from_dict(data["provenance"]),
            surface_geometry=data.get("surface_geometry"),
            execution_limits=(
                LinearExecutionLimits.from_dict(data["execution_limits"])
                if data.get("execution_limits") is not None
                else None
            ),
            layout=data.get("layout", LINEAR_LAYOUT_LEGACY),
            external_matrices=tuple(ExternalMatrixRecord.from_dict(item)
                                    for item in data.get("external_matrices", ())),
        )


@dataclass(frozen=True)
class ProjectModel:
    """Complete marine project intent: bodies, equilibrium, settings, results."""

    name: str
    bodies: tuple[Body, ...]
    equilibrium: EquilibriumMetadata = field(default_factory=EquilibriumMetadata)
    settings: AnalysisSettings = field(default_factory=AnalysisSettings)
    results: tuple[ResultRecord, ...] = ()
    linear_inputs: Optional[LinearDevelopmentInputs] = None
    hydro_provenance: Optional[HydroBridgeProvenance] = None
    native_inputs: Optional[NativeMechanicalInputs] = None
    line_equilibrium_inputs: Optional[object] = None
    equilibrium_adoption: Optional[Mapping] = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("project name must be a non-empty string")
        if self.equilibrium_adoption is not None:
            from .equilibrium_transition import validate_receipt
            object.__setattr__(self,"equilibrium_adoption",validate_receipt(self.equilibrium_adoption))
        if self.equilibrium.source == "adopted_static_development_v1" and self.equilibrium_adoption is None:
            raise ValueError("adopted equilibrium requires its transition receipt")
        if (self.linear_inputs is not None and getattr(self.linear_inputs, "layout", None) == LINEAR_LAYOUT_LEGACY
                and any(b.reference_orientation != ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)) for b in self.bodies)):
            raise ValueError("legacy linear inputs do not support nonidentity body reference orientation")
        ids = [body.body_id for body in self.bodies]
        if len(ids) != len(set(ids)):
            raise ValueError("project contains duplicate body ids")
        surface_ids = [s.semantics.surface_id for b in self.bodies for s in b.hydro_surfaces]
        if len(surface_ids) != len(set(surface_ids)):
            raise ValueError('duplicate surface_id across project bodies')
        for body in self.bodies:
            for s in body.hydro_surfaces:
                if s.role == 'attached' and any(sum(body.reference_orientation[2][j]*p[j] for j in range(3)) + body.reference_position[2] >= self.equilibrium.waterline_z for p in s.mesh.positions):
                    raise ValueError('attached sheet must be fully submerged at fixed equilibrium; free-surface sheet admission is unsupported')
                if self.native_inputs is not None and (s.semantics.primary_hydrodynamics != 'none' or s.semantics.viscous_correction != 'none'):
                    raise ValueError('native application surface force adapter is unsupported; sheet/hull loads cannot be omitted or counted twice')
        result_ids = [result.result_id for result in self.results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("project contains duplicate result ids")
        if self.linear_inputs is not None and not isinstance(
            self.linear_inputs, LinearDevelopmentInputs
        ):
            raise TypeError(
                "project linear_inputs must be a LinearDevelopmentInputs record or None"
            )
        if self.hydro_provenance is not None and not isinstance(
            self.hydro_provenance, HydroBridgeProvenance
        ):
            raise TypeError(
                "project hydro_provenance must be a HydroBridgeProvenance record or None"
            )
        if self.line_equilibrium_inputs is not None:
            from .line_equilibrium import LineEquilibriumInputs
            if type(self.line_equilibrium_inputs) is not LineEquilibriumInputs:
                raise TypeError('line equilibrium inputs require the explicit named record')
            if self.bodies or any(value is not None for value in (self.native_inputs,self.linear_inputs,self.hydro_provenance)):
                raise ValueError('line-only equilibrium intent cannot carry body or other solver intent')
        if self.native_inputs is not None:
            if type(self.native_inputs) is not NativeMechanicalInputs:
                raise TypeError("project native_inputs must be a NativeMechanicalInputs record or None")
            from .native_inputs import _number
            for body in self.bodies:
                _number(body.mass, "native body mass", positive=True)
            if set(self.native_inputs.initial_states) != set(ids):
                raise ValueError("native initial_states must cover exactly the project body identities")
            if (self.linear_inputs is not None or self.hydro_provenance is not None
                    or any(body.hydro_sidecar is not None for body in self.bodies)):
                raise ValueError("native dry intent cannot coexist with configured hydrodynamics")
        if self.linear_inputs is not None and self.linear_inputs.layout != LINEAR_LAYOUT_LEGACY:
            # A linear result bound to rigid_subset_v1 inputs must carry the rigid result layout: a record
            # decoded with the legacy surge/heave mapping is refused (persistence, add_result and replay all
            # construct this record). Full numerical consistency: linear.verify_linear_result.
            from . import RESULT_KIND_LINEAR
            from .linear import linear_input_hash
            producing_hash = linear_input_hash(self.linear_inputs)
            if any(result.kind == RESULT_KIND_LINEAR and result.input_hash == producing_hash
                   and result.result_layout != self.linear_inputs.layout for result in self.results):
                raise ValueError("a linear result of rigid_subset_v1 inputs must carry the rigid result layout; "
                                 "refusing the result")
        if self.linear_inputs is not None and self.linear_inputs.surface_geometry is not None:
            from . import RESULT_KIND_LINEAR
            from .linear import linear_input_hash
            producing_hash = linear_input_hash(self.linear_inputs)
            for result in self.results:
                if result.kind == RESULT_KIND_LINEAR and result.input_hash == producing_hash and result.surface_snapshot is None:
                    raise ValueError('surface-bound linear result requires its producing surface snapshot; stripped intent is refused')
        if self.hydro_provenance is not None:
            if self.linear_inputs is None:
                raise ValueError(
                    "a project with hydro provenance must also store the linear "
                    "inputs that provenance describes"
                )
            rigid_inputs = self.linear_inputs.layout == LINEAR_LAYOUT_RIGID
            if rigid_inputs != (self.hydro_provenance.rigid_bundle is not None):
                raise ValueError("hydro provenance must carry a rigid bundle exactly when the linear inputs use the "
                                 f"{LINEAR_LAYOUT_RIGID} layout (ADR-015 L6-3)")
            if self.linear_inputs.provenance.source != LINEAR_SOURCE_HYDRO:
                raise ValueError(
                    "hydro provenance may only accompany linear inputs whose "
                    f"source is {LINEAR_SOURCE_HYDRO!r}"
                )

    def body(self, body_id: str) -> Optional[Body]:
        for body in self.bodies:
            if body.body_id == body_id:
                return body
        return None

    def with_edits(self, **changes) -> "ProjectModel":
        data = {
            "name": self.name,
            "bodies": self.bodies,
            "equilibrium": self.equilibrium,
            "settings": self.settings,
            "results": self.results,
            "linear_inputs": self.linear_inputs,
            "hydro_provenance": self.hydro_provenance,
            "native_inputs": self.native_inputs,
            "line_equilibrium_inputs": self.line_equilibrium_inputs,
            "equilibrium_adoption": self.equilibrium_adoption,
        }
        data.update(changes)
        return ProjectModel(**data)

    def to_dict(self) -> dict:
        return {
            **({"equilibrium_adoption": _jsonable_value(self.equilibrium_adoption)} if self.equilibrium_adoption is not None else {}),
            **({"line_equilibrium_inputs": self.line_equilibrium_inputs.to_dict()} if self.line_equilibrium_inputs is not None else {}),
            **({"native_inputs": self.native_inputs.to_dict()} if self.native_inputs is not None else {}),
            "name": self.name,
            "bodies": [body.to_dict() for body in self.bodies],
            "equilibrium": self.equilibrium.to_dict(),
            "settings": self.settings.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "linear_inputs": (
                self.linear_inputs.to_dict() if self.linear_inputs is not None else None
            ),
            "hydro_provenance": (
                self.hydro_provenance.to_dict() if self.hydro_provenance is not None else None
            ),
        }


def default_box_mesh(size: tuple[float, float, float]) -> BodyMesh:
    """Axis-aligned box mesh centred on the origin, for synthetic demonstration."""

    sx, sy, sz = (float(s) / 2.0 for s in size)
    positions = tuple(
        (x * sx, y * sy, z * sz)
        for x in (-1.0, 1.0)
        for y in (-1.0, 1.0)
        for z in (-1.0, 1.0)
    )
    triangles = (
        (0, 1, 3), (0, 3, 2),  # -x
        (4, 6, 7), (4, 7, 5),  # +x
        (0, 2, 6), (0, 6, 4),  # -y
        (1, 5, 7), (1, 7, 3),  # +y
        (0, 4, 5), (0, 5, 1),  # -z
        (2, 3, 7), (2, 7, 6),  # +z
    )
    return BodyMesh(positions=positions, triangles=triangles)


def default_two_body_project(name: str = "Two-body demonstrator") -> ProjectModel:
    """Synthetic two-body demo project. Explicitly not a marine solver input."""

    return ProjectModel(
        name=name,
        bodies=(
            Body(
                body_id=new_body_id(),
                name="Body A",
                reference_position=(-15.0, 0.0, 0.0),
                mass=2.4e6,
                mesh=default_box_mesh((12.0, 6.0, 5.0)),
                color="#3b82c4",
            ),
            Body(
                body_id=new_body_id(),
                name="Body B",
                reference_position=(15.0, 0.0, 0.0),
                mass=1.8e6,
                mesh=default_box_mesh((9.0, 5.0, 4.0)),
                color="#c46a3b",
            ),
        ),
        equilibrium=EquilibriumMetadata(
            waterline_z=0.0,
            description="Synthetic demonstration equilibrium (not a solved equilibrium).",
            source="synthetic",
        ),
        settings=AnalysisSettings(),
    )


SYNTHETIC_RESULT_NOTE = (
    "Synthetic deterministic demonstration replay. Not boundary-element-method "
    "output and not a dynamics integration; not qualified for production use."
)
