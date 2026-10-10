"""Body-owned surface assignments using the public ANYhydro semantics contract.

Importing the application does not require ANYhydro. Explicit hydrodynamic
surface construction does require the tested neutral package, never a BEM solver.
Hull/lid assignments refer to existing geometry; only attached sheets carry mesh.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json

ASSIGNMENT_SCHEMA = 'anymarine-owned-surface-v1'

def neutral_api():
    try:
        import anyhydro
    except ImportError as error:
        raise ValueError('Explicit hydrodynamic surfaces require the tested ANYhydro package; install it before loading this project') from error
    for name in ('SurfaceSemantics', 'HydroPanelMesh', 'surface_identity', 'migrate_surface_role'):
        if not hasattr(anyhydro, name):
            raise ValueError('Installed ANYhydro lacks the explicit surface contract; install the tested wheel')
    return anyhydro

@dataclass(frozen=True)
class HydroSurfaceAssignment:
    role: str
    semantics: object
    mesh: object = None

    def __post_init__(self):
        from .domain import BodyMesh
        h = neutral_api()
        if self.role not in ('hull', 'lid', 'attached'):
            raise ValueError('surface role must be hull, lid or attached')
        semantics = self.semantics
        try:
            if isinstance(semantics, dict):
                semantics = h.SurfaceSemantics.from_dict(semantics)
            if not isinstance(semantics, h.SurfaceSemantics):
                raise ValueError('surface semantics must use the public ANYhydro contract')
            expected = {'hull': 'body_boundary', 'lid': 'numerical_lid', 'attached': 'thin_sheet'}[self.role]
            if semantics.surface_kind != expected:
                raise ValueError('surface role disagrees with explicit surface_kind; no shell/lid inference')
            if self.role == 'attached':
                if not isinstance(self.mesh, BodyMesh):
                    raise ValueError('attached thin_sheet requires a body-local BodyMesh')
                panel_mesh(self.mesh, semantics)
            elif self.mesh is not None:
                raise ValueError('hull/lid assignments refer to existing Body geometry; duplicate mesh is refused')
        except h.HydroError as error:
            raise ValueError(str(error)) from error
        object.__setattr__(self, 'semantics', semantics)

    def to_dict(self):
        return dict(schema=ASSIGNMENT_SCHEMA, role=self.role,
                    semantics=self.semantics.to_dict(),
                    mesh=None if self.mesh is None else self.mesh.to_dict())

    @classmethod
    def from_dict(cls, data):
        from .domain import BodyMesh
        if not isinstance(data, dict) or set(data) != {'schema', 'role', 'semantics', 'mesh'}:
            raise ValueError('surface assignment fields are incomplete or unknown; refusing instead of dropping intent')
        if data['schema'] != ASSIGNMENT_SCHEMA:
            raise ValueError('unsupported owned surface assignment schema')
        return cls(data['role'], data['semantics'],
                   None if data['mesh'] is None else BodyMesh.from_dict(data['mesh']))

def panel_mesh(mesh, semantics, reference=(0., 0., 0.)):
    """Exact body-local geometry to world translation; never infer or alter winding."""
    import numpy as np
    h = neutral_api()
    return h.HydroPanelMesh(np.asarray(mesh.positions, dtype=np.float64) + np.asarray(reference),
        np.asarray(mesh.triangles), np.full(len(mesh.triangles), 3, dtype=np.uint8),
        semantics.owning_body_id, False, (semantics.source_geometry_id,), '', surface=semantics)

def assignment_for(body, role):
    return next((s for s in body.hydro_surfaces if s.role == role), None)

def body_surface_hash(body):
    if not body.hydro_surfaces:
        return None
    payload = json.dumps([s.to_dict() for s in body.hydro_surfaces], sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()

def has_surface_intent(project):
    """Keep reader-version protection for edited bodies and retained evidence."""
    def contains(data):
        if isinstance(data, dict):
            if 'hydro_surfaces' in data or data.get('surface_hash') is not None:
                return True
            if data.get('source_schema_version') == '1.2.0':
                return True
            return any(contains(v) for v in data.values())
        if isinstance(data, (tuple, list)):
            return any(contains(v) for v in data)
        return False
    return contains(project.to_dict())

def display_body_mesh(body):
    """One rigid display mesh/handle per body, including its attached plates only."""
    from .domain import BodyMesh
    positions = list(body.mesh.positions)
    triangles = list(body.mesh.triangles)
    for s in body.hydro_surfaces:
        if s.role != 'attached':
            continue
        offset = len(positions)
        positions.extend(s.mesh.positions)
        triangles.extend(tuple(i + offset for i in face) for face in s.mesh.triangles)
    return BodyMesh(tuple(positions), tuple(triangles))

def capture_surface_geometry(project):
    if not any(b.hydro_surfaces or b.hydro_lid_policy != "free_surface_v1" for b in project.bodies):
        return None
    return dict(schema='anymarine-surface-geometry-v1',
                bodies=[b.to_dict() for b in project.bodies],
                equilibrium=project.equilibrium.to_dict())

def geometry_project(data, inputs=None):
    from .domain import Body, EquilibriumMetadata, ProjectModel, _jsonable_value
    data = _jsonable_value(data)
    if not isinstance(data, dict) or set(data) != {'schema', 'bodies', 'equilibrium'} or data['schema'] != 'anymarine-surface-geometry-v1':
        raise ValueError('invalid producing surface geometry schema')
    return ProjectModel('Producing surface geometry', tuple(Body.from_dict(b) for b in data['bodies']),
                        equilibrium=EquilibriumMetadata.from_dict(data['equilibrium']), linear_inputs=inputs)

def surface_result_snapshot(inputs):
    if inputs.surface_geometry is None:
        return None
    return dict(schema='anymarine-surface-replay-v1', linear_inputs=inputs.to_dict())

def project_from_surface_snapshot(data):
    from .domain import LinearDevelopmentInputs, _jsonable_value
    from .linear import verify_linear_binding
    data = _jsonable_value(data)
    if not isinstance(data, dict) or set(data) != {'schema', 'linear_inputs'} or data['schema'] != 'anymarine-surface-replay-v1':
        raise ValueError('invalid producing surface replay schema')
    inputs = LinearDevelopmentInputs.from_dict(data['linear_inputs'])
    if inputs.surface_geometry is None:
        raise ValueError('surface replay requires producing geometry')
    project = geometry_project(inputs.surface_geometry, inputs)
    verify_linear_binding(project, inputs)
    return project

def migrate_legacy_surface(body, *, role, legacy_role, surface_id,
                           source_geometry_id, mesh=None, minus_side=None, plus_side=None):
    """Explicit migration; old projects are read unchanged and never inferred."""
    h = neutral_api()
    try:
        semantics = h.migrate_surface_role(legacy_role, surface_kind={
            'hull': 'body_boundary', 'lid': 'numerical_lid', 'attached': 'thin_sheet'}[role],
            surface_id=surface_id, owning_body_id=body.body_id,
            source_geometry_id=source_geometry_id, minus_side=minus_side, plus_side=plus_side)
        assignment = HydroSurfaceAssignment(role, semantics, mesh)
        return body.with_edits(hydro_surfaces=body.hydro_surfaces + (assignment,))
    except (KeyError, h.HydroError) as error:
        raise ValueError('Ambiguous legacy surface conversion; explicitly select role, primary owner and sides') from error
