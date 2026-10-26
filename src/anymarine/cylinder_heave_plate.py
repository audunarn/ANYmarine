"""Cylinder and an explicitly two-sided, rigidly attached bottom heave plate.

Dimensions follow the 2016 New & Renewable Energy experiment, model 2.
The experimental 2 mm plate is idealised as a zero-thickness dipole surface;
viscous damping and the ballast/support hardware are not reproduced.
"""
from __future__ import annotations

import math

REFERENCE_URL = 'https://www.journalksnre.com/_PR/view/index.php?aidx=6137&bidx=487'
REFERENCE = {
    'title': 'Experimental Study on the Suppression of a Vertical Circular Cylinder with a Heave Plate',
    'url': REFERENCE_URL, 'year': 2016, 'model': 2,
    'cylinder_diameter_m': .060, 'cylinder_height_m': .300,
    'draft_m': .150, 'measured_mass_kg': .418,
    'plate_diameter_m': .080, 'experimental_plate_thickness_m': .002,
    'plate_gap_below_keel_m': .065,
    'period_range_s': (.51, 1.24),
}


def _disk(radius, z, sectors, rings, *, upward=True):
    """One shared centre and concentric rings, with no coincident faces."""
    from .domain import BodyMesh
    points = [(0., 0., z)]
    for ring in range(1, rings + 1):
        r = radius * ring / rings
        points.extend((r * math.cos(2 * math.pi * j / sectors),
                       r * math.sin(2 * math.pi * j / sectors), z)
                      for j in range(sectors))
    triangles = [(0, 1+j, 1+(j+1) % sectors) for j in range(sectors)]
    for ring in range(1, rings):
        a, b = 1+(ring-1)*sectors, 1+ring*sectors
        for j in range(sectors):
            k = (j+1) % sectors
            triangles.extend(((a+j, b+j, b+k), (a+j, b+k, a+k)))
    if not upward:
        triangles = [(a, c, b) for a, b, c in triangles]
    return BodyMesh(tuple(points), tuple(triangles))


def cylinder_heave_plate_project(*, sectors=12, vertical_panels=3, plate_rings=3,
                                with_plate=True):
    """Pure factory. Six shared rigid-body modes, no solve on loading.

    The hydro mesh stops at the equilibrium waterplane. Declared uniform-cylinder
    inertia is educational, not the experiment's unreported ballast inertia.
    The measured mass and reported draft are retained independently; the model
    is a fixed-equilibrium coefficient case, not an equilibrated loading case.
    """
    from .domain import Body, BodyMesh, EquilibriumMetadata, ProjectModel
    from .mass_properties import BodyMassProperties
    from .surface_assignments import migrate_legacy_surface
    for name, value, minimum, maximum in (
            ('sectors', sectors, 8, 64), ('vertical_panels', vertical_panels, 1, 16),
            ('plate_rings', plate_rings, 2, 12)):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    if type(with_plate) is not bool:
        raise ValueError('with_plate must be boolean')
    radius, draft, height, mass = .030, .150, .300, .418
    points = []
    for level in range(vertical_panels+1):
        z = -draft + draft*level/vertical_panels
        points.extend((radius*math.cos(2*math.pi*j/sectors),
                       radius*math.sin(2*math.pi*j/sectors), z) for j in range(sectors))
    triangles = []
    for level in range(vertical_panels):
        a, b = level*sectors, (level+1)*sectors
        for j in range(sectors):
            k = (j+1) % sectors
            triangles.extend(((a+j, a+k, b+k), (a+j, b+k, b+j)))
    points.append((0., 0., -draft))
    centre = len(points)-1
    triangles.extend((centre, (j+1) % sectors, j) for j in range(sectors))
    inertia_transverse = mass*(3*radius**2+height**2)/12
    props = BodyMassProperties((0., 0., 0.),
        ((inertia_transverse, 0., 0.), (0., inertia_transverse, 0.),
         (0., 0., mass*radius**2/2)))
    body = Body('cylinder-heave-plate', 'Cylinder (2016 experiment dimensions)',
                (0., 0., 0.), mass, BodyMesh(tuple(points), tuple(triangles)),
                mass_properties=props)
    body = migrate_legacy_surface(body, role='hull', legacy_role='potential_flow',
        surface_id=body.body_id+'/hull', source_geometry_id=body.body_id+'/cylinder-v1')
    if with_plate:
        body = migrate_legacy_surface(body, role='attached', legacy_role='potential_flow',
            surface_id=body.body_id+'/plate', source_geometry_id=body.body_id+'/disk-v1',
            mesh=_disk(.040, -draft-.065, sectors, plate_rings),
            minus_side='lower-water', plus_side='upper-water')
    return ProjectModel(name='Cylinder + dipole heave plate — development' if with_plate else
        'Cylinder without plate — development control', bodies=(body,),
        equilibrium=EquilibriumMetadata(description=
            'Fixed reported draft; zero-thickness model-2 plate 65 mm below keel. '
            'One rigid body; mass/inertia not a reproduced experimental loading. '+REFERENCE_URL))


def solve_cylinder_heave_plate(*, periods_s=(.78, 1.0), sectors=12,
                             vertical_panels=3, plate_rings=3, with_plate=True):
    """Run the public owner mixed-surface frequency-domain route, never a fallback."""
    project = cylinder_heave_plate_project(sectors=sectors, vertical_panels=vertical_panels,
                                           plate_rings=plate_rings, with_plate=with_plate)
    return solve_project_coefficients(project, periods_s=periods_s)


def solve_project_coefficients(project, *, periods_s=(.78, 1.0)):
    """Solve the exact selected project's surfaces; never regenerate edited inputs."""
    from anyhydro import MixedHullSheetBackend, HydroRequest, HydroBody
    from .hydro import expected_world_mesh
    from .surface_assignments import panel_mesh
    from dataclasses import replace
    if len(project.bodies) != 1 or project.bodies[0].body_id != 'cylinder-heave-plate':
        raise ValueError('Load the cylinder study before running its coefficient workflow')
    body = project.bodies[0]
    if body.mass_properties is None:
        raise ValueError('Cylinder study requires explicit declared mass properties')
    # Only attest winding of this exact authored primitive recipe. An edited or
    # imported display mesh never acquires a blanket outward-normal assertion.
    sectors = sum(p[2] == 0. for p in body.mesh.positions)
    levels, remainder = divmod(len(body.mesh.positions)-1, sectors or 1)
    if remainder or not 8 <= sectors <= 64 or not 1 <= levels-1 <= 16:
        raise ValueError('Cylinder hull is not an authored supported cylinder recipe')
    authored = cylinder_heave_plate_project(sectors=sectors, vertical_panels=levels-1,
                                           with_plate=False).bodies[0].mesh
    if authored != body.mesh:
        raise ValueError('Edited hull winding requires owner verification; rebuild the cylinder recipe')
    hull = replace(expected_world_mesh(body), outward_normals_verified=True, mesh_hash='')
    plate = tuple(panel_mesh(s.mesh, s.semantics, body.reference_position)
                  for s in body.hydro_surfaces if s.role == 'attached')
    hydro_body = HydroBody(body.body_id, body.name, hull, None,
        body.reference_position, tuple(a+b for a,b in zip(body.reference_position, body.mass_properties.cg_body)),
        tuple(body.body_id+'::'+d for d in ('surge','sway','heave','roll','pitch','yaw')),
        attached_surfaces=plate)
    raw_periods = tuple(periods_s)
    if any(type(t) not in (int, float) for t in raw_periods):
        raise ValueError('periods_s must contain positive finite real periods')
    periods = tuple(float(t) for t in raw_periods)
    if not periods or any(not math.isfinite(t) or t <= 0 for t in periods):
        raise ValueError('periods_s must contain positive finite periods')
    request = HydroRequest(omegas=sorted(2*math.pi/t for t in periods), wave_directions=[0.],
                           water_depth=math.inf, rho=1000., gravity=9.81,
                           include_zero_frequency=False, include_infinite_frequency=False,
                           backend_settings={})
    return MixedHullSheetBackend().solve((hydro_body,), request)
