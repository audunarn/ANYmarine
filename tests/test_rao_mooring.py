"""RAO slice 7: taut dynamic mooring lines in the FD/TD RAO (node DOFs kept)."""
import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mechanics import prepare_native_nodes, prepare_native_run
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine import rao_case as rc
from anymarine import rao_linearize as rl
from anymarine import rao_report as rr
from test_rao_extraction import oscillator_project

EA, REST, SPAN = 40.0, 2.4, 2.5
TENSION = EA * (SPAN - REST) / REST


def taut_project():
    """Oscillator fixture plus one pretensioned line per body along x, balanced by the link.

    Each body pulls on a straight two-segment line (anchor, mid node, fairlead) with
    tension T; the link rest length is shortened by T/k so the link carries T. With
    zero gravity the declared state is an exact static equilibrium.
    """
    p = oscillator_project()
    data = p.native_inputs.to_dict()
    a, b = (body.body_id for body in p.bodies)
    lines = []
    # Lines fully submerged at z = -1 (the assembler refuses partial wetting); the link
    # attaches at the same depth so line and link forces are collinear (no moment).
    for i, (bid, anchor, node, point) in enumerate(((a, [-8.0, 0.0, -1.0], [-5.5, 0.0, -1.0], [-1.0, 0.0, -1.0]),
                                                     (b, [8.0, 0.0, -1.0], [5.5, 0.0, -1.0], [1.0, 0.0, -1.0]))):
        lines.append(dict(line_id=f'taut-line-{i}', endpoints=[dict(type='fixed', position=anchor),
                          dict(type='node', node_index=i), dict(type='rigid', body_id=bid, point_body=point)],
                          rest_lengths=[REST, REST], axial_rigidity=[EA, EA], axial_damping=[1.0, 1.0],
                          mass_per_length=[2.0, 2.0], displaced_area=[0.001, 0.001]))
    data['mooring'] = dict(version='native_mooring_inputs_v1', data=dict(
        api_version='marine_mooring_v1', body_ids=[a, b], lines=lines, node_count=2, gravity_world=[0.0, 0.0, 0.0],
        fluid_density=1025.0, still_water_level=0.0),
        initial_nodes=dict(positions=[[-5.5, 0.0, -1.0], [5.5, 0.0, -1.0]], velocities=[[0.0] * 3] * 2))
    link = data['links'][0]
    k = link['stiffness'][0][0]
    link['rest_translation'] = [4.0 - TENSION / k, 0.0, 0.0]
    link['rho_a'] = link['rho_b'] = [0.0, 0.0, -1.0]
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


def spec(**over):
    criteria = {**rc.GOVERNING_MAXIMA, **rc.PROPOSED_MAXIMA,
                'nearzero_amplitude_absolute': 5e-4, 'small_angle_bound_rad': 0.05, 'small_excursion_m': 0.05}
    data = dict(case_id='rao-taut-mooring', linearization_point=dict(source='declared_initial_state'),
                radiation_source='kernel_discrete', omegas=[1.0], headings_degrees=[0.0], amplitudes=[1.0, 0.5],
                phase=0.0, drag_linearization='none', windows=dict(transient_cycles=6, window_cycles=3),
                fit_controls=dict(harmonics=2, intercept=True, drift=False), criteria=criteria,
                execution_limits=dict(max_frames_per_run=4000, wall_seconds_per_run=300, max_runs=2))
    data.update(over)
    return data


@requires_solver
def test_taut_lines_linearize_exactly():
    calm = rl.wave_free_project(taut_project())
    model, states, spec_ = prepare_native_run(calm)
    nodes = prepare_native_nodes(calm)
    L = rl.linearize(model, states, dt=spec_.dt, nodes=nodes)
    assert L.size == 18 and L.dof_keys[12:15] == ('node0::x', 'node0::y', 'node0::z')
    np.testing.assert_allclose(L.static_force, 0.0, atol=1e-12)
    np.testing.assert_allclose(L.mooring_tension, TENSION, rtol=1e-12)
    # mooring tangent = -dF/dq of the public line force (finite differences)
    h, n = 1e-6, L.size
    numeric = np.zeros((n, n))
    for j in range(n):
        e = np.zeros(n)
        e[j] = h
        plus = model.mooring.force(*rl._perturbed(states, nodes, tuple(model.body_ids), e), time=0.0)
        minus = model.mooring.force(*rl._perturbed(states, nodes, tuple(model.body_ids), -e), time=0.0)
        numeric[:, j] = -(np.asarray(plus) - np.asarray(minus)) / (2 * h)
    np.testing.assert_allclose(L.contributions['mooring']['stiffness'], numeric, rtol=0, atol=1e-6)
    # axial tension sensitivity: body A surge stretches its fairlead segment by EA/L0
    G = rl.mooring_tension_gradient(model, states, nodes)
    assert G[1, 0] == pytest.approx(EA / REST, rel=1e-6) and G[0, 0] == pytest.approx(0.0, abs=1e-6)
    with pytest.raises(rl.RaoLinearizationError, match='node state'):
        rl.linearize(model, states, dt=spec_.dt)


@requires_solver
def test_taut_mooring_campaign_matches_the_fd_table():
    project = taut_project()
    case = rr.prepare_case(project, spec())
    assert case.features['mooring'] and case.features['mooring_taut'] and case.category == 'kernel_consistency_only'
    table = rr.fd_table(project, case)
    assert table['dof_keys'][-1] == 'node1::z' and 'mooring' in table['entries'][0]['forces']
    total = sum(np.array([complex(*v) for v in values]) for values in table['entries'][0]['forces'].values())
    np.testing.assert_allclose(total, 0.0, atol=1e-10)
    records, extractions = rr.run_campaign(project, case)
    assert len(extractions[0]['dof_keys']) == 18
    report = rr.compare(case, table, extractions)
    assert report['verdict'] == 'pass', [c for c in report['checks'] if not c['passed']]


@requires_solver
def test_untaut_or_unbalanced_lines():
    from anymarine.native_examples import mooring_two_body_project
    # the bundled example's lines are not at static equilibrium with the bodies
    with pytest.raises(rc.RaoCaseError, match='audited'):
        rr.prepare_case(mooring_two_body_project(), spec(radiation_source='none'))


def test_padding_keeps_complex_values():
    z = np.array([1 + 2j, 3 - 1j])
    out = rl._pad(z, 4)
    assert out.dtype.kind == 'c' and out[1] == 3 - 1j and out[3] == 0
    m = rl._pad(np.array([[1j]]), 2)
    assert m[0, 0] == 1j and m.dtype.kind == 'c'
