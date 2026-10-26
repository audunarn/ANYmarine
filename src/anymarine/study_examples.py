"""Pure study factories. Local licensed inputs are read, never bundled or solved."""
from dataclasses import dataclass
from pathlib import Path
import math

from .domain import Body, BodyMesh, ProjectModel, EquilibriumMetadata, AnalysisSettings
from .mass_properties import BodyMassProperties
from .native_inputs import NativeMechanicalInputs
from .mooring_inputs import NativeMooringInputs
from .reference_comparison import load

PRIMARY_EXAMPLES = (
    ('l01', 'L01 — single vessel'),
    ('l01_pair', 'L01 × 2 — multibody'),
    ('l01_mooring', 'L01 — polyester spread mooring, 200 m depth'),
    ('cylinder', 'Cylinder — bottom dipole heave plate'),
    ('l01_stability', 'L01 — simple tanks and stability'),
)

@dataclass(frozen=True)
class RunMode:
    key: str
    label: str
    available: bool
    diagnostic: str

@dataclass(frozen=True)
class StudyExample:
    project: ProjectModel
    reference: dict
    notes: tuple[str, ...]


def polyester_spread(body: Body) -> NativeMooringInputs:
    """Four educational lumped lines; 1000 m EACH, no pretension claim.

    EA=100 MN, dry mass=8 kg/m, diameter=.10 m, axial damping=10 kN s
    per segment. These are declared constants, not a manufacturer's rope law.
    Two 500 m elements per line; initial straight chord is shorter than rest.
    Seabed anchors z=-200 m, fairleads z=-5 m, horizontal reach 950 m.
    No seabed contact is selected; sag/contact require a refined owner model.
    """
    xs=[p[0] for p in body.mesh.positions]; ys=[p[1] for p in body.mesh.positions]
    length=max(xs)-min(xs); beam=max(ys)-min(ys)
    lines=[]; nodes=[]
    for i,(sx,sy) in enumerate(((-1,-1),(-1,1),(1,-1),(1,1))):
        fairlead=(sx*.4*length,sy*.4*beam,-5.)
        f=tuple(a+b for a,b in zip(body.reference_position,fairlead))
        direction=(sx/math.sqrt(2),sy/math.sqrt(2))
        anchor=(f[0]+950*direction[0],f[1]+950*direction[1],-200.)
        nodes.append([(a+b)/2 for a,b in zip(anchor,f)])
        lines.append(dict(line_id=f'polyester-{i+1}',endpoints=[
            dict(type='fixed',position=list(anchor)),dict(type='node',node_index=i),
            dict(type='rigid',body_id=body.body_id,point_body=list(fairlead))],
            rest_lengths=[500.,500.],axial_rigidity=[1e8,1e8],
            axial_damping=[1e4,1e4],mass_per_length=[8.,8.],
            displaced_area=[math.pi*.1**2/4]*2))
    return NativeMooringInputs(data=dict(api_version='marine_mooring_v1',
        body_ids=[body.body_id],lines=lines,node_count=4,gravity_world=[0.,0.,-9.81],
        fluid_density=1025.,still_water_level=0.),initial_nodes=dict(
        positions=nodes,velocities=[[0.,0.,0.] for _ in nodes]))


def l01_study(reference_path, *, count=1, moored=False, mass_properties=None):
    """Build L01 intent from a hash-validated local comparison.

    Optional full mass properties must be explicit CG tensor inputs, never
    estimated from a display mesh. Reference RAOs only belong to single L01.
    """
    if type(count) is not int or count not in (1,2) or type(moored) is not bool or (moored and count != 1):
        raise ValueError('L01 supports one, two, or one spread-moored vessel')
    data=load(reference_path); raw=data['body']
    if raw['name'].strip().casefold() != 'l01 default vessel':
        raise ValueError('Expected the L01 default vessel reference, not another vessel')
    mesh=BodyMesh(positions=tuple(tuple(p) for p in raw['display_mesh']['positions']),
                  triangles=tuple(tuple(t) for t in raw['display_mesh']['triangles']))
    if mass_properties is None and 'mass_properties' in raw:
        mass_properties=BodyMassProperties.from_dict(raw['mass_properties'])
    if mass_properties is not None and not isinstance(mass_properties,BodyMassProperties):
        raise TypeError('explicit BodyMassProperties required')
    if mass_properties is not None and any(abs(a-b)>1e-12 for a,b in
            zip(mass_properties.cg_body, raw['centre_of_mass_m'])):
        raise ValueError('Explicit mass properties CG does not match the L01 reference frame')
    width=max(p[1] for p in mesh.positions)-min(p[1] for p in mesh.positions)
    bodies=tuple(Body(body_id=f'l01-{i+1}',name=f"{raw['name']} {i+1}",
        reference_position=(0.,(i-.5)*(width+20.) if count==2 else 0.,0.),
        mass=float(raw['mass_kg']),mesh=mesh,mass_properties=mass_properties,
        color=('#8796ab','#c18a54')[i]) for i in range(count))
    identity=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))
    native=NativeMechanicalInputs(run_spec=dict(dt=.05,steps=20,residual_tolerance=1e-8,
        max_newton_iterations=30,line_search=True),initial_states={b.body_id:dict(
        position=b.reference_position,rotation=identity,velocity_world=(0.,)*6) for b in bodies},
        mooring=polyester_spread(bodies[0]) if moored else None)
    title=dict(l01='L01 — single vessel',l01_pair='L01 × 2 — multibody',
               l01_mooring='L01 — polyester spread mooring, 200 m depth')[
                   'l01_mooring' if moored else 'l01_pair' if count==2 else 'l01']
    notes=('Local licensed display geometry and reference RAOs; development, unqualified.',
           'No hydrodynamic coefficients or restoring have been synthesized.',
           'Native dry free motion uses zero body gravity; marine equilibrium needs physical restoring and loads.',
           'Two vessels require newly computed interacting hydrodynamics; single-vessel RAOs cannot be reused.'
           if count==2 else 'Reference RAOs and incident-wave animation are available for the original single vessel.')
    if moored:
        notes += ('Four educational polyester lines, 1000 m each; 950 m horizontal reach, anchors z=-200 m. '
                  'Initial chord ~970 m < rest length: slack initialization, not pretensioned equilibrium. '
                  'EA=100 MN, 8 kg/m, diameter=.10 m, segment damping=10 kN s. '
                  'No contact, manufacturer calibration, catenary equilibrium or mooring qualification claimed.',)
    project=ProjectModel(name=title,bodies=bodies,native_inputs=native,
        settings=AnalysisSettings(duration=1.,dt=.05),
        equilibrium=EquilibriumMetadata(description='\n'.join(notes)))
    return StudyExample(project,data,notes)


def run_modes(project, *, reference_available=False):
    """Input readiness only. Public owners still validate when a user runs."""
    native=project.native_inputs
    mass=all(b.mass_properties is not None for b in project.bodies) and bool(project.bodies)
    linear=project.linear_inputs is not None and project.hydro_provenance is not None
    return (
        RunMode('reference','Reference RAOs and wave animation',reference_available,
                'Available for original single vessel only; stored external comparison, no new solve.'),
        RunMode('native','Native dry / configured transient',bool(native and mass),
                'Run declared native inputs; installed ANYsolver validates admission.' if native and mass else
                'Import full CG inertia in body axes and native inputs; mass alone does not define rigid mechanics.'),
        RunMode('rao','Frequency-domain / time-domain RAO study (configure)',bool(native and mass),
                'Open RAO: register a bound case, produce an FD table or run its TD campaign. '
                'Physical loads, radiation and declared run limits must pass the existing case admission.'),
        RunMode('linear','Linear hydrodynamic response',linear,
                'Run bound imported coefficients.' if linear else
                'Jobs > Import hydrodynamic inputs: provide matched physical coefficients, full mass and provenance.'),
        RunMode('static','Coupled static equilibrium',bool(native and mass and native.potential_flow),
                'Requires full mass and physical restoring/buoyancy native inputs; dry free-body intent is not equilibrium.'),
        RunMode('hydro','HAMS wave fields (configure)',True,
                'Choose explicit trusted HAMS configuration; display geometry is not solver-ready evidence.'),
    )


def l01_mass_properties(extraction_path, reference_body):
    """Recover CG tensor from OrcaWave SI spatial mass about body origin.

    This reads an explicit local extraction; parallel-axis subtraction retains
    full off-diagonal inertia. Matching mass/CG prevents accidental other-vessel use.
    """
    import json
    import numpy as np
    raw=json.loads(Path(extraction_path).read_text(encoding='utf-8'))['body']
    m=float(raw['mass_kg']); cg=np.asarray(raw['centre_of_mass_m'],dtype=float)
    if m != float(reference_body['mass_kg']) or not np.allclose(
            cg,reference_body['centre_of_mass_m'],rtol=0.,atol=1e-12):
        raise ValueError('local mass extraction does not match reference vessel mass/CG')
    matrix=np.asarray(raw['inertia_matrix_si'],dtype=float)
    skew=np.array(((0.,-cg[2],cg[1]),(cg[2],0.,-cg[0]),(-cg[1],cg[0],0.)))
    if (matrix.shape!=(6,6) or not np.isfinite(matrix).all()
            or not np.allclose(matrix,matrix.T,rtol=1e-12,atol=1e-6)
            or not np.allclose(matrix[:3,:3],m*np.eye(3),rtol=1e-12,atol=1e-6)
            or not np.allclose(matrix[:3,3:],-m*skew,rtol=1e-12,atol=1e-6)):
        raise ValueError('not a matching SI body-origin spatial mass matrix')
    tensor=matrix[3:,3:]-m*((cg@cg)*np.eye(3)-np.outer(cg,cg))
    if np.linalg.eigvalsh(tensor).min()<=0:
        raise ValueError('nonpositive CG inertia from local extraction')
    return BodyMassProperties(tuple(cg),tuple(tuple(row) for row in tensor))
