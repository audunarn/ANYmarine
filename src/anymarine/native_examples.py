"""Declared development examples; no job runs or backend imports while loading."""
from .domain import AnalysisSettings, Body, ProjectModel, default_box_mesh
from .mass_properties import BodyMassProperties
from .native_inputs import NativeMechanicalInputs
import hashlib
import json


def equilibrium_project():
    """Pure static intent: two bodies/link/three lines and synthetic hydrostatics."""
    project=fluid_two_body_project();data=project.native_inputs.to_dict()
    data['gravity_world']=[0.,0.,-9.81]
    mooring=mooring_two_body_project().native_inputs.mooring.to_dict()
    mooring['data']['gravity_world']=[0.,0.,-9.81]
    data['mooring']=mooring
    fluid=data['potential_flow'];fluid['baseline'][2]=fluid['baseline'][8]=12.*9.81
    fluid['restoring']=[[float(200. if i==j and i%6<3 else 100. if i==j else .5 if abs(i-j)==6 else 0.) for j in range(12)] for i in range(12)]
    fluid['motion_bounds']=[[2.,1.],[2.,1.]]
    return project.with_edits(name='Coupled static equilibrium example — development, unqualified',
        native_inputs=NativeMechanicalInputs.from_dict(data))


def mooring_contact_project():
    """Three lines with explicit compliant penetration; loading never solves."""
    project=mooring_two_body_project();data=project.native_inputs.to_dict();mooring=data['mooring']
    bed=-4.;diameter=.125;penetration=.03125
    mooring['data'].update(api_version='marine_mooring_contact_v1',seabed_contact=dict(
        api_version='marine_seabed_contact_v2',elevation=bed,normal_stiffness=5000.,normal_damping=100.,
        activation_depth_m=.001))  # Explicit uncalibrated development constitutive input.
    for line in mooring['data']['lines']:
        line['contact_diameters']=[diameter]*len(line['rest_lengths'])
        line['endpoints'][0]['position'][2]=bed+.5*diameter
    for point in mooring['initial_nodes']['positions']:point[2]=bed+.5*diameter-penetration
    return project.with_edits(name='Three lines / compliant seabed contact — development, unqualified',
        native_inputs=NativeMechanicalInputs.from_dict(data))


def mooring_hydrodynamic_project():
    """Two members and three lines share explicit Airy/current; loading is pure."""
    project=morison_two_body_project();data=project.native_inputs.to_dict()
    mooring=mooring_two_body_project().native_inputs.mooring.to_dict()
    mooring['data'].update(api_version='marine_mooring_hydrodynamics_v1',
                          environment=data['morison']['environment'])
    section=dict(diameter=.035,drag_normal=1.,drag_tangential=.02,
                 added_mass_normal=1.,added_mass_tangential=.05,inertia_normal=2.,inertia_tangential=.05)
    for line in mooring['data']['lines']:line['hydrodynamics']=[dict(section) for _ in line['rest_lengths']]
    data['mooring']=mooring;data['run_spec'].update(dt=.01,steps=3)
    return project.with_edits(name='Wave/current members and three dynamic lines — development, unqualified',
        settings=AnalysisSettings(duration=.03,dt=.01),native_inputs=NativeMechanicalInputs.from_dict(data))


def regular_wave_mooring_project():
    """Two moored cubes forced only by one regular Airy wave; loading never solves.

    The wave/current mooring example with the current removed, the wave set
    to amplitude 0.3 m and omega 2 rad/s (heading 0, phase 0), bodies starting
    at rest at their reference positions and line rest lengths equal to the
    initial anchor-node-fairlead spans (no pretension). Members and lines use
    the same saved environment, so the displayed incident wave is the exact
    forcing wave. Ten seconds at dt 0.04 s. Development, unqualified.
    """
    project=mooring_hydrodynamic_project();data=project.native_inputs.to_dict()
    dt,steps=.04,250
    data['run_spec'].update(dt=dt,steps=steps)
    for env in (data['morison']['environment'],data['mooring']['data']['environment']):
        env['components']=[dict(amplitude=.3,omega=2.,direction_degrees=0.,phase=0.)]
        env['current']=dict(depths=[0.,env['water_depth']],velocities=[[0.,0.,0.],[0.,0.,0.]])
    position={body.body_id:[float(x) for x in body.reference_position] for body in project.bodies}
    for bid,state in data['initial_states'].items():state['position']=list(position[bid])
    nodes=data['mooring']['initial_nodes']['positions']
    def span(p,q):return sum((a-b)**2 for a,b in zip(p,q))**.5
    for line in data['mooring']['data']['lines']:
        anchor,node,fairlead=line['endpoints']
        n=nodes[node['node_index']]
        f=[a+b for a,b in zip(position[fairlead['body_id']],fairlead['point_body'])]
        line['rest_lengths']=[span(n,anchor['position']),span(f,n)]
    bodies=tuple(body.with_edits(name='Moored cube '+('A' if i==0 else 'B'))
                 for i,body in enumerate(project.bodies))
    return project.with_edits(name='Two moored cubes in a regular wave — development, unqualified',
        bodies=bodies,settings=AnalysisSettings(duration=dt*steps,dt=dt),
        native_inputs=NativeMechanicalInputs.from_dict(data))


def mooring_two_body_project():
    """Load two bodies/three anchored lines; three development steps, no solve."""
    project=dry_two_body_project();data=project.native_inputs.to_dict()
    data['run_spec'].update(dt=.01,steps=3)
    a,b=(body.body_id for body in project.bodies)
    lines=[];positions=[]
    for i,(bid,anchor,point,node) in enumerate((
        (a,[-6.,-2.,-4.],[0.,-1.,-1.],[-4.,-1.5,-2.5]),
        (a,[-6.,2.,-4.],[0.,1.,-1.],[-4.,1.5,-2.5]),
        (b,[6.,0.,-4.],[0.,0.,-1.],[4.,0.,-2.5]))):
        positions.append(node)
        lines.append(dict(line_id='demo-line-'+str(i),endpoints=[dict(type='fixed',position=anchor),
            dict(type='node',node_index=i),dict(type='rigid',body_id=bid,point_body=point)],
            rest_lengths=[2.,2.],axial_rigidity=[40.,40.],axial_damping=[1.,1.],mass_per_length=[2.,2.],displaced_area=[.001,.001]))
    data['mooring']=dict(version='native_mooring_inputs_v1',data=dict(api_version='marine_mooring_v1',body_ids=[a,b],
        lines=lines,node_count=3,gravity_world=[0.,0.,0.],fluid_density=1025.,still_water_level=0.),
        initial_nodes=dict(positions=positions,velocities=[[0.,0.,0.]]*3))
    return project.with_edits(name='Two bodies / three anchored lines — development, unqualified',
        native_inputs=NativeMechanicalInputs.from_dict(data),settings=AnalysisSettings(duration=.03,dt=.01))


def dry_two_body_project():
    """Two uniform 2 m cubes, mass12 kg each, with one axial 10 N/m spring.

    Explicit CG inertia8 kg m² follows m*(2²+2²)/12 for the declared uniform
    cubes. The initial extension is0.2 m; gravity and fluid loads are not
    selected. These are educational development inputs, not qualified marine
    data. Loading never integrates or fabricates result arrays.
    """
    R = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    props = BodyMassProperties((0., 0., 0.), ((8., 0., 0.), (0., 8., 0.), (0., 0., 8.)))
    bodies = tuple(Body(body_id=bid, name=name, reference_position=(x, 0., 0.),
        mass=12., mesh=default_box_mesh((2., 2., 2.)), mass_properties=props, color=color)
        for bid, name, x, color in (
            ('native-demo-body-a', 'Dry cube A', -2., '#3b82c4'),
            ('native-demo-body-b', 'Dry cube B', 2., '#ed9a30')))
    K = tuple(tuple(10. if i == j == 0 else 0. for j in range(6)) for i in range(6))
    C = ((0.,) * 6,) * 6
    inputs = NativeMechanicalInputs(
        run_spec={'dt': .01, 'steps': 100, 'residual_tolerance': 1e-10,
                  'max_newton_iterations': 20, 'line_search': True},
        initial_states={body.body_id: {'position': (x, 0., 0.), 'rotation': R,
                                      'velocity_world': (0.,) * 6}
                        for body, x in zip(bodies, (-2.1, 2.1))},
        links=({'link_id': 'native-demo-axial-link', 'body_a': bodies[0].body_id,
                'body_b': bodies[1].body_id, 'rho_a': (0., 0., 0.), 'rho_b': (0., 0., 0.),
                'axes_a': R, 'axes_b': R, 'rest_translation': (4., 0., 0.), 'rest_rotation': R,
                'stiffness': K, 'damping': C, 'preload': (0.,) * 6},))
    return ProjectModel(name='Native dry two-body example — unqualified', bodies=bodies,
                        settings=AnalysisSettings(duration=1., dt=.01), native_inputs=inputs)


def fluid_two_body_project():
    """Load bounded full-coupled DEVELOPMENT inputs; no solve or replay fabricated.

    Three native steps permit a quick job. A/C/K arrays are synthetic declared
    operators, not BEM coefficients or scientifically qualified marine data.
    """
    project = dry_two_body_project()
    data = project.native_inputs.to_dict()
    data['run_spec'].update(dt=.1,steps=3)
    identity = [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]
    frames = [{'body_id':body.body_id,'equilibrium_position':list(body.reference_position),
               'equilibrium_rotation':identity,'coefficient_axes':identity,
               'reference_point_body':[0.,0.,0.]} for body in project.bodies]
    binding = hashlib.sha256(json.dumps(frames,sort_keys=True,separators=(',',':'),
                                       allow_nan=False).encode()).hexdigest()
    n = 12
    def matrix(diagonal,cross):
        return [[(diagonal if i == j else 0.)+
                 (cross if abs(i-j)==6 else 0.) for j in range(n)] for i in range(n)]
    fluid = {'api_version':'marine_potential_flow_development_v1','frames':frames,
        'equilibrium_hash':binding,'dt':.1,'added_mass_infinite':matrix(2.,.2),
        'restoring':matrix(3.,.3),'kernel':[matrix(1.,.1),matrix(.5,.05),matrix(.25,.025)],
        'baseline':[0.]*n,'excitation_real':[.4 if i in (0,6) else 0. for i in range(n)],
        'excitation_imag':[.1 if i in (0,6) else 0. for i in range(n)],'omega':2.,
        'motion_bounds':[[.5,.2],[.5,.2]],'source_identity':'synthetic full-coupled native development example',
        'source_kind':'development_fixture','force_basis':'equilibrium_h_gradient',
        'hydrostatics_role':'incremental_buoyancy','gravity_included':False,'prehistory':'zero_before_t0'}
    data['potential_flow'] = fluid
    return project.with_edits(name='Native coupled fluid example — development, unqualified',
        settings=AnalysisSettings(duration=.3,dt=.1),native_inputs=NativeMechanicalInputs.from_dict(data))



def seeded_wave_two_body_project():
    """Load saved synthetic source/sea-state intent; no solve or owner imports."""
    from pathlib import Path
    from .native_jobs import project_from_snapshot
    path=Path(__file__).parent/'examples/shared-sea-state-development/snapshot.json'
    return project_from_snapshot(json.loads(path.read_text(encoding='utf-8')))


def morison_two_body_project():
    """Declared Airy/current input and two straight members; loading never solves.

    One vertical member belongs to cube A; a second spans the two bodies.
    Cubes have explicit dry mass/inertia and an axial spring. Selected member
    inertia and combined wave/current relative drag are external Morison loads.
    No buoyancy/gravity baseline is selected: this is a driven development
    example, not a floating equilibrium or qualified full marine model.
    """
    from .morison_inputs import MORISON_ID, MORISON_VERSION
    project = dry_two_body_project()
    data = project.native_inputs.to_dict()
    data['run_spec'].update(dt=.02, steps=3)
    a, b = (body.body_id for body in project.bodies)
    def member(mid, aid, pa, bid, pb):
        return dict(member_id=mid, end_a=dict(body_id=aid, point_body=pa),
            end_b=dict(body_id=bid, point_body=pb), quadrature_order=4,
            section=dict(diameter=.06, drag_coefficient=1., added_mass_coefficient=1.),
            ownership=dict(primary_role='morison_full', bem_included=False,
                           drag_correction=False, buoyancy_owner='none'))
    data['morison'] = dict(api_version=MORISON_VERSION, contribution_id=MORISON_ID,
        environment=dict(api_version='marine_loads_v1', water_depth=10., density=1025.,
            gravity=9.81, still_water_level=0., acceleration_convention='eulerian_local',
            components=[dict(amplitude=.1, omega=2., direction_degrees=0., phase=0.)],
            current=dict(depths=[0., 1., 10.], velocities=[[.15,0.,0.],[.1,0.,0.],[.05,0.,0.]])),
        members=[member('vertical-member-a', a, [0.,0.,.5], a, [0.,0.,-3.]),
                 member('spanning-member-ab', a, [0.,0.,-1.5], b, [0.,0.,-2.])])
    bodies = tuple(body.with_edits(name='Wave/current cube ' + ('A' if i == 0 else 'B'))
                   for i, body in enumerate(project.bodies))
    return project.with_edits(name='Native wave/current Morison example — development, unqualified',
        bodies=bodies, settings=AnalysisSettings(duration=.06, dt=.02),
        native_inputs=NativeMechanicalInputs.from_dict(data))
