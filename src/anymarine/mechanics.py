"""Lazy public adapter to the solver-owned instantaneous rigid-body API."""
import importlib
from .domain import ProjectModel


class MechanicsError(ValueError):
    """Non-success preparing mechanical inputs."""


class MechanicsInputError(MechanicsError):
    """Missing, invalid or unsupported declared mechanical inputs."""


class MechanicsUnavailableError(MechanicsError):
    """The tested optional solver interface is unavailable."""


def prepare_rigid_body(project, body_id):
    """Compile explicit inputs using public ANYsolver validation.

    Returns RigidBody6D, never a result or integrator. Existing project mesh
    axes at the reference pose are GLOBAL; the initial orientation is identity.
    Missing CG/inertia is unknown, never an estimate or automatic zero.
    """
    if not isinstance(project, ProjectModel):
        raise MechanicsInputError("project must be a ProjectModel")
    bodies = [body for body in project.bodies if body.body_id == body_id]
    if len(bodies) != 1:
        raise MechanicsInputError(f"unknown or ambiguous body identity {body_id!r}")
    body = bodies[0]
    if body.mass_properties is None:
        raise MechanicsInputError(f"body {body.name!r} has no explicit CG/inertia; supply mass properties first")
    try:
        solver = importlib.import_module("anysolver.marine_rigid")
    except ImportError as error:
        raise MechanicsUnavailableError("install the tested ANYsolver wheel with marine_rigid_v1") from error
    if getattr(solver, "API_VERSION", None) != "marine_rigid_v1":
        raise MechanicsUnavailableError("the installed ANYsolver rigid-body API is incompatible; install the tested wheel")
    try:
        from .native_inputs import _number
        mass = _number(body.mass, "declared body mass", positive=True)
        return solver.RigidBody6D(body_id=body.body_id, mass=mass,
                                 cg_body=body.mass_properties.cg_body,
                                 inertia_cg_body=body.mass_properties.inertia_cg_body,
                                 reference_origin=body.reference_position,
                                 reference_orientation=body.reference_orientation)
    except ValueError as error:
        raise MechanicsInputError(f"body {body.name!r}: {error}") from error


def prepare_mechanical_model(project, *, links=(), gravity_world=None):
    """Compile the explicitly selected native dry-body/link development route.

    Fluid coefficients are never silently dropped or combined with dry
    gravity. This returns inputs only; numerical execution belongs in a
    headless caller or isolated job process, never a widget callback.
    """
    if not isinstance(project, ProjectModel):
        raise MechanicsInputError("project must be a ProjectModel")
    if (project.linear_inputs is not None or project.hydro_provenance is not None
            or any(body.hydro_sidecar is not None for body in project.bodies)):
        raise MechanicsInputError("native dry mechanics cannot consume or silently omit configured hydrodynamics")
    try:
        solver = importlib.import_module('anysolver.marine_mechanical')
    except ImportError as error:
        raise MechanicsUnavailableError("install the tested ANYsolver wheel with marine_mechanical_v1") from error
    if getattr(solver, 'API_VERSION', None) != 'marine_mechanical_v1':
        raise MechanicsUnavailableError("the installed native mechanics API is incompatible")
    bodies = tuple(prepare_rigid_body(project, body.body_id) for body in project.bodies)
    try:
        return solver.MarineMechanicalModel(bodies, tuple(links), gravity_world)
    except solver.MarineMechanicalInputError as error:
        raise MechanicsInputError(str(error)) from error


def solve_native_mechanics(project, initial_states, run_spec, *, links=(), gravity_world=None,
                           cancel=None, progress=None):
    """Shared headless service; returns authoritative native evidence.

    No result is attached to the editable project. GUI execution and
    persisted native-run input/result admission are separate integration
    obligations; this synchronous service must not run on the GUI thread.
    """
    model = prepare_mechanical_model(project, links=links, gravity_world=gravity_world)
    solver = importlib.import_module('anysolver.marine_mechanical')
    return solver.solve_marine_mechanical(model, initial_states, run_spec,
                                         cancel=cancel, progress=progress)



def prepare_native_run(project):
    """Compile saved native intent using public ANYsolver contracts only.

    Returns (model, initial_states, run_spec), without integration or Qt.
    Initial chart vectors are zero with the declared physical rotation as
    anchor; chart rates then equal the declared WORLD velocities exactly.
    """
    if not isinstance(project, ProjectModel) or project.native_inputs is None:
        raise MechanicsInputError("project has no declared native mechanical inputs")
    from .equilibrium_transition import verify_adoption_binding
    verify_adoption_binding(project)
    if project.native_inputs.wave_realization is not None:
        try:
            from anyloads import WaveRealization
        except ImportError as error:
            raise MechanicsUnavailableError('install tested ANYloads shared sea-state support') from error
        try:
            declared=project.native_inputs.wave_realization
            waves=WaveRealization.from_dict(declared.to_dict())
            if waves.content_hash != declared.content_hash:
                raise ValueError('saved realization is not canonical owner data')
            from anyhydro import build_excitation_series,UNIT_INCIDENT_AMPLITUDE,dataset_content_hash
            from anyhydro.fixtures import make_two_body_fixture
            source=make_two_body_fixture()
            series=project.native_inputs.potential_flow.to_dict()['excitation_series']
            if series['hydro_content_hash']!=dataset_content_hash(source):
                raise ValueError('shared waves require the exact bundled development excitation source')
            expected=build_excitation_series(source,waves,
                amplitude_normalization=UNIT_INCIDENT_AMPLITUDE,phase_origin_xy=(0.,0.)).to_dict()
            if series!=expected:
                raise ValueError('excitation series differs from the saved waves and bundled source')
        except ImportError as error:
            raise MechanicsUnavailableError('install tested ANYhydro shared excitation support') from error
        except ValueError as error:
            raise MechanicsInputError('shared waves refused: '+str(error)) from error
    base = prepare_mechanical_model(project)
    solver = importlib.import_module('anysolver.marine_mechanical')
    try:
        rigid = importlib.import_module('anysolver.marine_rigid')
    except ImportError as error:
        raise MechanicsUnavailableError("install the tested ANYsolver rigid-body wheel") from error
    if getattr(rigid, 'API_VERSION', None) != 'marine_rigid_v1':
        raise MechanicsUnavailableError("the installed rigid-body API is incompatible")
    data = project.native_inputs.to_dict()
    try:
        model_data = solver.model_to_dict(base)
        model_data['links'] = [dict(link, psd_tolerance=1e-12) for link in data['links']]
        model_data['gravity_world'] = data['gravity_world']
        model_data['contributions'] = [dict(wrench, type='world_wrench')
                                       for wrench in data['world_wrenches']]
        if 'potential_flow' in data:
            try:
                fluid_api = importlib.import_module('anysolver.marine_potential_flow')
            except ImportError as error:
                raise MechanicsUnavailableError('install the tested ANYsolver wheel with native potential-flow development support') from error
            if getattr(fluid_api,'API_VERSION',None) != 'marine_potential_flow_development_v1':
                raise MechanicsUnavailableError('the installed potential-flow development API is incompatible')
            model_data['contributions'].append({'type':'potential_flow_development','data':data['potential_flow']})
        if 'morison' in data:
            try:
                morison_api = importlib.import_module('anysolver.marine_morison')
            except ImportError as error:
                raise MechanicsUnavailableError('install the tested ANYsolver wheel with native Morison support') from error
            if getattr(morison_api, 'API_VERSION', None) != 'marine_morison_development_v1':
                raise MechanicsUnavailableError('the installed Morison development API is incompatible')
            model_data['contributions'].append({'type': 'morison_members_development', 'data': data['morison']})
        if 'mooring' in data:
            if getattr(solver,'MOORING_API_VERSION',None)!='marine_mechanical_mooring_v1':
                raise MechanicsUnavailableError('install the tested ANYsolver common mooring driver')
            if project.native_inputs.mooring.has_contact:
                try:
                    contact_api=importlib.import_module('anysolver.marine_seabed')
                    mooring_api=importlib.import_module('anysolver.marine_mooring')
                except ImportError as error:
                    raise MechanicsUnavailableError('install the tested ANYsolver seabed contact wheel') from error
                if (getattr(contact_api,'API_VERSION',None)!='marine_seabed_contact_v1'
                        or getattr(mooring_api,'CONTACT_API_VERSION',None)!='marine_mooring_contact_v1'):
                    raise MechanicsUnavailableError('installed seabed contact APIs are incompatible')
                if (data['mooring']['data']['seabed_contact']['api_version']=='marine_seabed_contact_v2'
                        and getattr(contact_api,'CONTINUOUS_API_VERSION',None)!='marine_seabed_contact_v2'):
                    raise MechanicsUnavailableError('install the tested ANYsolver continuous seabed contact v2 wheel')
            if project.native_inputs.mooring.has_hydrodynamics:
                try:
                    line_api=importlib.import_module('anyloads.marine_line')
                    mooring_api=importlib.import_module('anysolver.marine_mooring')
                except ImportError as error:
                    raise MechanicsUnavailableError('install the tested ANYloads/ANYsolver line hydrodynamics wheels') from error
                if (getattr(line_api,'API_VERSION',None)!='marine_line_loads_v1'
                        or getattr(mooring_api,'HYDRO_API_VERSION',None)!='marine_mooring_hydrodynamics_v1'):
                    raise MechanicsUnavailableError('installed line hydrodynamics APIs are incompatible')
            model_data['api_version']=solver.MOORING_API_VERSION
            model_data['mooring']=data['mooring']['data']
        model = solver.model_from_dict(model_data)
        if 'potential_flow' in data:
            from .potential_flow_inputs import POTENTIAL_FLOW_ID
            fluid = next(c for c in model.contributions if c.contribution_id == POTENTIAL_FLOW_ID)
            for frame,body in zip(fluid.data.frames,base.bodies):
                if (frame.equilibrium_position != body.reference_origin
                        or frame.equilibrium_rotation != body.reference_orientation):
                    raise MechanicsInputError('potential-flow equilibrium does not match the body reference geometry; regenerate bound data')
        spec = solver.spec_from_dict(dict(data['run_spec'], api_version=solver.API_VERSION))
        states = {}
        for body in project.bodies:
            state = data['initial_states'][body.body_id]
            states[body.body_id] = rigid.RigidBodyState(
                position=state['position'], anchor=state['rotation'],
                velocity=state['velocity_world'])
        if 'potential_flow' in data:
            fluid.validate_states(states)
        return model, states, spec
    except MechanicsUnavailableError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise MechanicsInputError(f"native run input compilation failed: {error}") from error


def solve_configured_native_mechanics(project, *, cancel=None, progress=None):
    """Headless execution of immutable saved intent; never a widget callback.

    Outputs retain native status and evidence. This does not register project
    results, qualify physics, or convert rotations to Euler angles.
    """
    model, states, spec = prepare_native_run(project)
    solver = importlib.import_module('anysolver.marine_mechanical')
    return solver.solve_marine_mechanical(model, states, spec, cancel=cancel, progress=progress,
                                        initial_nodes=prepare_native_nodes(project))


def prepare_native_nodes(project):
    """Compile saved node intent through the public owner, without execution."""
    if project.native_inputs.mooring is None:return None
    try:
        from anysolver.marine_mooring import MooringNodeState
        import numpy as np
        value=project.native_inputs.mooring
        count=value.data['node_count']
        return MooringNodeState(np.asarray(value.initial_nodes['positions']).reshape(count,3),
                               np.asarray(value.initial_nodes['velocities']).reshape(count,3),np.zeros((count,3)))
    except (ImportError,TypeError,ValueError) as error:
        raise MechanicsInputError(f'native node compilation failed: {error}') from error
