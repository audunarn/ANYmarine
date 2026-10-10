"""Independent application-boundary development checks (not qualification)."""
from dataclasses import replace
import numpy as np
import pytest
from scipy.linalg import expm
from anymarine.domain import default_two_body_project, LinearDevelopmentInputs
from anymarine.linear import analytic_demo_inputs, linear_input_hash
from anymarine.services import ApplicationService, ServiceError


def bound():
    p = default_two_body_project()
    return p.with_edits(linear_inputs=analytic_demo_inputs(p))


@pytest.mark.parametrize('change', ['equilibrium', 'mesh'])
def test_geometry_equilibrium_invalidate_and_refuse(change):
    p = bound()
    h = linear_input_hash(p.linear_inputs)
    if change == 'equilibrium':
        edited = p.with_edits(equilibrium=replace(p.equilibrium, waterline_z=0.3))
    else:
        b = p.bodies[0]
        vertices = list(b.mesh.positions)
        vertices[0] = tuple(x + 0.1 for x in vertices[0])
        edited = p.with_edits(bodies=(b.with_edits(mesh=replace(b.mesh, positions=vertices)), p.bodies[1]))
    from anymarine.linear import linear_result_stale
    assert linear_result_stale(edited, h)
    with pytest.raises(ServiceError):
        ApplicationService().linear_job_snapshot(edited)


def test_fractional_frame_count_refused_at_json_boundary():
    data = bound().linear_inputs.to_dict()
    data['n_steps'] += 0.5
    with pytest.raises((ValueError, TypeError)):
        LinearDevelopmentInputs.from_dict(data)


def test_negative_infinite_origin_refused():
    with pytest.raises(ValueError):
        replace(bound().linear_inputs, t0=-float('inf'))


def test_coupled_memory_solution_against_augmented_matrix_exponential():
    p = bound()
    n, dt, decay = 301, 0.01, 0.7
    M = np.diag([2., 3., 4., 5.])
    A = np.array([[.4,.02,.06,.01],[.02,.6,.03,.05],[.06,.03,.7,.04],[.01,.05,.04,.8]])
    C = 0.12*np.eye(4)
    K = np.array([[3.,.1,-.4,.2],[.1,4.,.2,-.5],[-.4,.2,5.,.15],[.2,-.5,.15,6.]])
    R = .2*np.eye(4)+.03*np.ones((4,4))
    q0 = np.array([.1,-.05,.08,.03])
    v0 = np.array([.02,.01,-.01,.03])
    inputs = replace(p.linear_inputs, dt=dt, n_steps=n,
        mass_dry=M, added_mass_infinite=A, damping=C, stiffness=K,
        forces=np.zeros((n,4)), q0=q0, v0=v0,
        kernel_times=np.arange(n)*dt,
        radiation_kernel=np.exp(-decay*np.arange(n)*dt)[:,None,None]*R)
    p = p.with_edits(linear_inputs=inputs)
    result = ApplicationService().run_linear_local(p)
    inv = np.linalg.inv(M+A)
    L = np.block([[np.zeros((4,4)),np.eye(4),np.zeros((4,4))],
                  [-inv@K,-inv@C,-inv],
                  [np.zeros((4,4)),R,-decay*np.eye(4)]])
    E = expm(dt*L)
    state = np.r_[q0,v0,np.zeros(4)]
    expected = []
    for _ in range(n):
        expected.append(state[:8].copy())
        state = E@state
    expected = np.array(expected)
    q = np.column_stack([result.channels['q:'+label] for label in inputs.labels])
    v = np.column_stack([result.channels['v:'+label] for label in inputs.labels])
    assert np.max(np.abs(q-expected[:,:4])) < 2e-5
    assert np.max(np.abs(v-expected[:,4:])) < 2e-5
    for i,b in enumerate(p.bodies):
        positions = np.array(result.positions[b.body_id])
        np.testing.assert_allclose(positions[:,0],b.reference_position[0]+q[:,2*i])
        np.testing.assert_allclose(positions[:,2],b.reference_position[2]+q[:,2*i+1])
        np.testing.assert_allclose(result.orientations[b.body_id],0)
