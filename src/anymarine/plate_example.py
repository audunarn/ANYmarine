"""Loadable geometry example; no plate coefficient or qualification claim."""
from .domain import BodyMesh, default_two_body_project
from .surface_assignments import migrate_legacy_surface

def attached_plate_project():
    project = default_two_body_project('Two floaters with submerged plates — geometry preview')
    bodies = []
    for body in project.bodies:
        # One consistently oriented, zero-thickness midsurface per body.
        # Existing dry mass is retained; no fictitious volume or added inertia.
        bottom = min(p[2] for p in body.mesh.positions) - 2.
        mesh = BodyMesh(((-10., -7., bottom), (10., -7., bottom),
                         (10., 7., bottom), (-10., 7., bottom)), ((0, 1, 2), (0, 2, 3)))
        body = migrate_legacy_surface(body, role='hull', legacy_role='potential_flow',
            surface_id=body.body_id + '/hull', source_geometry_id=body.body_id + '/hull-geometry')
        body = migrate_legacy_surface(body, role='attached', legacy_role='potential_flow',
            surface_id=body.body_id + '/plate', source_geometry_id=body.body_id + '/plate-geometry',
            mesh=mesh, minus_side='lower-water', plus_side='upper-water')
        bodies.append(body)
    return project.with_edits(bodies=tuple(bodies))
