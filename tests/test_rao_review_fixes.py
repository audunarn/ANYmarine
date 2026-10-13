"""RAO review findings F1-F9 (Codex round 1 on slices 1-6)."""
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mechanics import prepare_native_run
from anymarine.native_examples import seeded_wave_two_body_project
from anymarine import rao_case as rc
from anymarine import rao_linearize as rl
from anymarine import rao_report as rr
from test_rao_potential_flow import at_equilibrium
from test_rao_extraction import oscillator_project

N = 12
KEYS = [f'{b}::{d}' for b in ('a', 'b') for d in rl.DOF_NAMES]
LINEAR = dict(potential_flow=True, any_drag=False, current=False, links=True, mooring=False, mooring_taut=None,
              contact_branch_risk=False, small_motion=True, linearization_audited=True)


def case(**over):
    criteria = {**rc.GOVERNING_MAXIMA, **rc.PROPOSED_MAXIMA,
                'nearzero_amplitude_absolute': 1e-6, 'small_angle_bound_rad': 0.05, 'small_excursion_m': 0.05}
    spec = dict(case_id='synthetic', linearization_point=dict(source='declared_initial_state'),
                radiation_source='kernel_discrete', omegas=[1.0], headings_degrees=[0.0], amplitudes=[0.1, 0.05],
                phase=0.0, drag_linearization='none', windows=dict(transient_cycles=5, window_cycles=3),
                fit_controls=dict(harmonics=2, intercept=True, drift=False), criteria=criteria,
                execution_limits=dict(max_frames_per_run=4000, wall_seconds_per_run=60, max_runs=4))
    spec.update(over)
    return rc.prepare_rao_case(spec, project_input_hash='c' * 64, features=LINEAR)


def table(c, rao):
    return dict(case_hash=c.case_hash, table_hash='t', dof_keys=KEYS,
                entries=[dict(omega=1.0, heading_degrees=0.0, source='kernel_discrete',
                              rao=[[z.real, z.imag] for z in rao], forces={}, power_fd={})])


def extraction(a, rao, **over):
    rao = np.asarray(rao, complex)
    ex = dict(omega=1.0, heading_degrees=0.0, amplitude=a, dof_keys=KEYS, rao=rao, response=rao * a,
              response_first=rao * a, steadiness=np.zeros(N), residual_rms=np.zeros(N), higher_absolute=np.zeros(N),
              harmonic_distortion=np.zeros(N), mean=np.zeros(N), drift=np.zeros(N), omega_dt=0.05,
              body_peaks=[[0.01, 0.001], [0.01, 0.001]],
              forces={'x': dict(phasor=np.zeros(N, complex)), 'dry': dict(phasor=np.zeros(N, complex))})
    ex.update(over)
    return ex


BASE = np.zeros(N, complex)
BASE[0] = 0.2 + 0.1j


def names(report, passed):
    return {c['name'] for c in report['checks'] if c['passed'] is passed}


def test_f3_unresolved_channels_get_absolute_checks():
    c = case()
    hidden = np.zeros(N)
    hidden[2] = 1.0                                   # 1 m of 2nd harmonic in an unresolved DOF
    report = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, higher_absolute=hidden), extraction(0.05, BASE)])
    assert 'unresolved_content_absolute' in names(report, False)


def test_f4_linearity_is_order_independent():
    c = case(amplitudes=[1e-5, 0.05])
    small = BASE.copy()
    big = BASE * 1.02
    runs = [extraction(1e-5, small), extraction(0.05, big)]
    one = rr.compare(c, table(c, BASE), runs)
    two = rr.compare(c, table(c, BASE), runs[::-1])
    value = lambda r: [x['value'] for x in r['checks'] if x['name'].startswith('linearity')]
    assert value(one) == value(two)


def test_f6_observed_motion_demotes_and_power_closure_is_checked():
    c = case()
    report = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, body_peaks=[[0.2, 0.001], [0.0, 0.0]]),
                                            extraction(0.05, BASE)])
    assert report['demoted'] and 'observed_excursion_m' in names(report, False) and report['verdict'] == 'nogo'
    assert 'power_closure_relative' in names(report, True) | names(report, False)


def test_f7_incomplete_or_foreign_comparisons_are_refused():
    c = case()
    with pytest.raises(rr.RaoReportError, match='grid'):
        rr.compare(c, table(c, BASE), [extraction(0.1, BASE)])
    with pytest.raises(rr.RaoReportError, match='grid'):
        rr.compare(c, table(c, BASE), [])
    other = dict(table(c, BASE), case_hash='d' * 64)
    with pytest.raises(rr.RaoReportError, match='different case'):
        rr.compare(c, other, [extraction(0.1, BASE), extraction(0.05, BASE)])


@requires_solver
def test_f1_f2_calm_shared_wave_model_is_consistent_and_excitation_free():
    p = at_equilibrium(seeded_wave_two_body_project())
    calm = rl.wave_free_project(p)
    model, states, spec = prepare_native_run(calm)               # F1: no mismatch refusal
    pf = rl.potential_flow_contribution(model)
    series = pf.data.excitation_series
    assert not np.any(series.weighted_real) and not np.any(series.weighted_imag)
    L = rl.linearize(model, states, dt=spec.dt)                  # F2: calm tangents
    assert L.size == 12
    wavy_model, wavy_states, wavy_spec = prepare_native_run(p)
    with pytest.raises(rl.RaoLinearizationError, match='excitation is active'):
        rl.linearize(wavy_model, wavy_states, dt=wavy_spec.dt)


@requires_solver
def test_f8_f9_deadline_refuses_and_keeps_partial_evidence():
    stored = []
    with pytest.raises(rr.RaoReportError, match='did not complete|failed'):
        rr.solve_record(oscillator_project(), wall_seconds=1e-6, store=lambda kind, payload: stored.append(kind))
    assert stored and stored[0] in ('partial', 'failure')


# ---------------------------------------------------------------- round 2
@requires_solver
def test_n1_declared_excitation_is_refused_even_if_zero_at_t0():
    from anymarine.native_inputs import NativeMechanicalInputs
    p = rl.wave_free_project(at_equilibrium(oscillator_project()))
    data = p.native_inputs.to_dict()
    data['potential_flow']['excitation_imag'][0] = 1.0     # sin(w t): zero force at t = 0
    sine = p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    model, states, spec = prepare_native_run(sine)
    with pytest.raises(rl.RaoLinearizationError, match='excitation is active'):
        rl.linearize(model, states, dt=spec.dt)


def test_n2_dry_inertia_power_enters_with_internal_sign():
    q = np.zeros(N, complex)
    q[0] = 1.0
    force = np.zeros(N, complex)
    force[0] = 1j
    power = rr._power({'x': dict(phasor=force), 'dry': dict(phasor=force)}, q, 1.0, N)
    assert abs(sum(power.values())) < 1e-15 and power['x'] != 0


def test_n3_every_table_dof_is_compared():
    c = case()
    short = [dict(extraction(a, BASE), dof_keys=KEYS[:1], rao=BASE[:1], response=BASE[:1] * a)
             for a in (0.1, 0.05)]
    with pytest.raises(rr.RaoReportError, match='DOF keys'):
        rr.compare(c, table(c, BASE), short)


@requires_solver
def test_n4_campaign_is_bound_to_the_case_project():
    from test_rao_report import oscillator_with_cylinder, spec
    c = rr.prepare_case(oscillator_with_cylinder(), spec())
    with pytest.raises(rr.RaoReportError, match='different project'):
        rr.run_campaign(oscillator_project(), c, solve=lambda *a, **k: pytest.fail('solver reached'))


# ---------------------------------------------------------------- round 3
@requires_solver
def test_r1_node_residuals_are_audited():
    from anymarine.native_inputs import NativeMechanicalInputs
    from test_rao_mooring import taut_project, spec as mooring_spec
    p = taut_project()
    data = p.native_inputs.to_dict()
    data['mooring']['data']['lines'][0]['rest_lengths'][0] = 2.3     # node no longer in equilibrium
    unbalanced = p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    with pytest.raises(rc.RaoCaseError, match='audited'):
        rr.prepare_case(unbalanced, mooring_spec())


@requires_solver
def test_r2_orbit_slack_is_predicted():
    from test_rao_mooring import taut_project, spec as mooring_spec
    c = rr.prepare_case(taut_project(), mooring_spec(amplitudes=[400.0, 200.0],
                                                     criteria={**mooring_spec()['criteria'], 'small_excursion_m': 10.0,
                                                               'small_angle_bound_rad': 1.0}))
    assert c.features['mooring_taut'] is False and c.category == 'nonlinear_reported'


def test_r3_observed_slack_demotes():
    c = case()
    report = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, min_tension=0.0), extraction(0.05, BASE, min_tension=0.5)])
    assert report['verdict'] == 'nogo' and any('slack' in d for d in report['demoted'])


def test_r5_unresolved_window_change_gates_nonlinear_cases():
    spec_keys = ('case_id', 'linearization_point', 'radiation_source', 'omegas', 'headings_degrees', 'amplitudes',
                 'phase', 'windows', 'fit_controls', 'criteria', 'execution_limits')
    spec = {k: v for k, v in case().to_dict().items() if k in spec_keys}
    spec['drag_linearization'] = 'equivalent_harmonic'
    c = rc.prepare_rao_case(spec, project_input_hash='c' * 64, features=dict(LINEAR, any_drag=True))
    assert c.category == 'linearized_nonlinear'
    first = BASE * 0.1
    first[3] = 0.01                                        # unresolved DOF changes by 0.01 m between windows
    report = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, response_first=first), extraction(0.05, BASE)])
    assert 'steady_absolute' in names(report, False) and report['verdict'] == 'nogo'


def test_r7_r8_power_closure_evidence_and_roundoff():
    c = case()
    missing = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, forces={}), extraction(0.05, BASE)])
    assert 'power_closure_relative' in names(missing, False)
    f = np.zeros(N, complex)
    f[0] = 1.0                                             # conservative (in phase with q): zero mean power
    tiny = dict(x=dict(phasor=f), dry=dict(phasor=f + 1e-17))
    ok = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, forces=tiny), extraction(0.05, BASE, forces=tiny)])
    assert 'power_closure_relative' in names(ok, True)


def test_r9_csv_keeps_the_incident_amplitude(tmp_path):
    import csv
    c = case()
    t = table(c, BASE)
    t['entries'] = [dict(t['entries'][0], amplitude=a) for a in (0.1, 0.05)]
    rr.write_csv(tmp_path, t)
    rows = list(csv.reader((tmp_path / 'rao_table.csv').open(encoding='utf-8')))
    assert rows[0][2] == 'incident_amplitude' and {r[2] for r in rows[1:]} == {'0.1', '0.05'}


# ---------------------------------------------------------------- round 4
def test_m2_case_id_cannot_name_a_path():
    for bad in ('C:/Github/ANYhydro/unexpected', '../escape', '.hidden', 'a/b', ''):
        with pytest.raises(rc.RaoCaseError, match='case_id'):
            case(case_id=bad)


def test_m6_mooring_case_without_tension_evidence_is_demoted():
    feats = dict(LINEAR, mooring=True, mooring_taut=True)
    spec_keys = ('case_id', 'linearization_point', 'radiation_source', 'omegas', 'headings_degrees', 'amplitudes',
                 'phase', 'drag_linearization', 'windows', 'fit_controls', 'criteria', 'execution_limits')
    c = rc.prepare_rao_case({k: v for k, v in case().to_dict().items() if k in spec_keys},
                            project_input_hash='c' * 64, features=feats)
    report = rr.compare(c, table(c, BASE), [extraction(0.1, BASE), extraction(0.05, BASE)])
    assert report['verdict'] == 'nogo' and any('no segment-tension evidence' in d for d in report['demoted'])


def test_m8_non_finite_power_arithmetic_fails():
    c = case()
    q = np.zeros(N, complex)
    q[0] = 1e-160
    f = np.zeros(N, complex)
    f[0] = 1e160j
    huge = dict(x=dict(phasor=f), dry=dict(phasor=np.zeros(N, complex)))
    report = rr.compare(c, table(c, BASE), [extraction(0.1, BASE, response=q, forces=huge),
                                            extraction(0.05, BASE, forces=huge)])
    assert 'power_closure_relative' in names(report, False)


# ---------------------------------------------------------------- round 5
def test_p6_case_id_is_ascii_only():
    for bad in ('é', '١', 'naïve-case'):
        with pytest.raises(rc.RaoCaseError, match='case_id'):
            case(case_id=bad)


# ---------------------------------------------------------------- round 7
@requires_solver
def test_v1_near_slack_gradient_stays_on_the_active_branch():
    from anymarine.native_inputs import NativeMechanicalInputs
    from anymarine.mechanics import prepare_native_nodes
    from test_rao_mooring import taut_project, SPAN, EA
    p = taut_project()
    data = p.native_inputs.to_dict()
    rest = SPAN - 1e-7                                     # static extension far below the default FD step
    for line in data['mooring']['data']['lines']:
        line['rest_lengths'] = [rest, rest]
    k = data['links'][0]['stiffness'][0][0]
    data['links'][0]['rest_translation'][0] = 4.0 - EA * (SPAN - rest) / rest / k
    calm = rl.wave_free_project(p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data)))
    model, states, spec = prepare_native_run(calm)
    G = rl.mooring_tension_gradient(model, states, prepare_native_nodes(calm))
    assert G[1, 0] == pytest.approx(EA / rest, rel=1e-6)     # not the branch-averaged ~EA/(2 L0)


# ---------------------------------------------------------------- round 8
class _TwoSegmentMooring:
    """Two segments from fixed anchors to one free node, with the public assembly's clipping."""

    def __init__(self, anchors, rest, ea, damping=0.0):
        self.anchors, self.rest, self.ea, self.c = (np.asarray(x, float) for x in (anchors, rest, ea, damping))
        self.size = 3

    def evaluate(self, body_states, nodes, time):
        p = np.asarray(nodes.positions, float)[0]
        v = np.asarray(nodes.velocities, float)[0]
        delta = p - self.anchors
        length = np.linalg.norm(delta, axis=1)
        rate = (delta / length[:, None]) @ v
        stretch = np.maximum(length - self.rest, 0.0)
        elastic = self.ea * stretch / self.rest
        raw = elastic + np.where(stretch > 0.0, self.c, 0.0) * rate
        return SimpleNamespace(tensions=np.maximum(raw, 0.0), elastic_tensions=elastic)


def _two_segment(rest, ea, damping=0.0):
    from anysolver.marine_mooring import MooringNodeState
    mooring = _TwoSegmentMooring([[-2.5, 0, 0], [2.5, 0, 0]], rest, ea, damping)
    node = MooringNodeState(np.zeros((1, 3)), np.zeros((1, 3)), np.zeros((1, 3)))
    return SimpleNamespace(mooring=mooring, body_ids=()), {}, node


@requires_solver
def test_v1b_weak_segment_gradient_is_not_hidden_by_a_stiff_one():
    """Review counterexample: a 1e-8 m extension next to a stiff pretensioned segment."""
    rest = [2.5 - 1e-8, 2.5 - 1e-3]
    model, states, node = _two_segment(rest, [40.0, 1e7])
    G = rl.mooring_tension_gradient(model, states, node)
    # Node moves +x: segment 0 (anchor at -2.5) lengthens, segment 1 shortens.
    assert G[0, 0] == pytest.approx(40.0 / rest[0], rel=1e-5)
    assert G[1, 0] == pytest.approx(-1e7 / rest[1], rel=1e-5)


@requires_solver
def test_v1b_extension_below_every_admissible_step_is_refused():
    """Review counterexample: a 1e-14 m extension; no step stays on the branch, so not taut."""
    model, states, node = _two_segment([2.5 - 1e-14, 2.5 - 1e-3], [40.0, 1e7])
    with pytest.raises(rl.RaoLinearizationError, match='near slack'):
        rl.mooring_tension_gradient(model, states, node)


@requires_solver
def test_v1b_rate_gradient_respects_the_damping_branch():
    rest = [2.5 - 1e-3, 2.5 - 1e-3]
    model, states, node = _two_segment(rest, [40.0, 40.0], damping=[3.0, 5.0])
    G = rl.mooring_tension_gradient(model, states, node, rates=True)
    assert G[0, 0] == pytest.approx(3.0, rel=1e-6) and G[1, 0] == pytest.approx(-5.0, rel=1e-6)
    # Damping strong enough that the default step turns the raw tension negative.
    weak = [2.5 - 1e-9, 2.5 - 1e-3]
    model, states, node = _two_segment(weak, [40.0, 40.0], damping=[1e3, 5.0])
    G = rl.mooring_tension_gradient(model, states, node, rates=True)
    assert G[0, 0] == pytest.approx(1e3, rel=1e-6)


def test_v2_non_finite_or_boolean_numbers_are_refused():
    from anymarine.qtapp import rao_panel
    assert not rao_panel._is_vector([[True, False]], 1)
    assert not rao_panel._is_vector([[10 ** 1000, 0]], 1)
    assert not rao_panel._is_vector([[float('nan'), 0.0]], 1)
    assert rao_panel._is_vector([[1, 0.5]], 1)


# ---------------------------------------------------------------- round 9
def _taut_parts(rest=None):
    from anymarine.native_inputs import NativeMechanicalInputs
    from anymarine.mechanics import prepare_native_nodes
    from test_rao_mooring import taut_project, SPAN, EA
    p = taut_project()
    if rest is not None:
        data = p.native_inputs.to_dict()
        for line in data['mooring']['data']['lines']:
            line['rest_lengths'] = [rest, rest]
        k = data['links'][0]['stiffness'][0][0]
        data['links'][0]['rest_translation'][0] = 4.0 - EA * (SPAN - rest) / rest / k
        p = p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    calm = rl.wave_free_project(p)
    model, states, _ = prepare_native_run(calm)
    return p, model, states, prepare_native_nodes(calm), calm.native_inputs.mooring.data['lines']


@requires_solver
def test_v1c_tension_bound_is_exact_at_rest_and_analytic_for_node_and_rotation_reach():
    from test_rao_mooring import EA, REST, SPAN, TENSION
    _, model, states, nodes, lines = _taut_parts()
    q = np.zeros(18, complex)
    assert rl.mooring_tension_lower_bound(model, lines, states, nodes, q, 1.0) == pytest.approx([TENSION] * 4, rel=1e-12)
    q[12] = 0.01                                    # node 0, x: both segments of line 0 reach 0.01 m
    bound = rl.mooring_tension_lower_bound(model, lines, states, nodes, q, 2.0)
    expected = EA / REST * (SPAN - REST - 0.01) - 1.0 * 2.0 * 0.01      # axial damping 1.0, w = 2
    assert bound[:2] == pytest.approx([expected] * 2, rel=1e-12) and bound[2:] == pytest.approx([TENSION] * 2, rel=1e-12)
    q[:] = 0.0
    q[5] = 0.002j                                   # body a yaw: the fairlead point reaches |theta| |rho|
    bound = rl.mooring_tension_lower_bound(model, lines, states, nodes, q, 1.0)
    reach = 0.002 * np.linalg.norm(lines[0]['endpoints'][2]['point_body'])
    assert bound[1] == pytest.approx(EA / REST * (SPAN - REST - reach) - reach, rel=1e-12)
    assert bound[0] == pytest.approx(TENSION, rel=1e-12)


@requires_solver
def test_v1c_tension_bound_never_exceeds_the_sampled_nonlinear_orbit_minimum():
    _, model, states, nodes, lines = _taut_parts()
    rng = np.random.default_rng(7)
    for scale, omega in ((1e-3, 0.5), (1e-2, 1.0), (3e-2, 3.0)):
        q = scale * (rng.standard_normal(18) + 1j * rng.standard_normal(18))
        bound = rl.mooring_tension_lower_bound(model, lines, states, nodes, q, omega)
        orbit = rl.mooring_orbit_tensions(model, states, nodes, q, omega, samples=256)
        assert np.all(bound <= orbit + 1e-9)


@requires_solver
def test_v1c_lines_that_do_not_reproduce_the_assembly_are_refused():
    project, model, states, nodes, _ = _taut_parts()
    altered = project.native_inputs.to_dict()['mooring']['data']['lines']        # plain JSON copies
    altered[1] = dict(altered[1], rest_lengths=[2.39, 2.4])
    with pytest.raises(rl.RaoLinearizationError, match='do not reproduce'):
        rl.mooring_tension_lower_bound(model, altered, states, nodes, np.zeros(18, complex), 1.0)


@requires_solver
def test_v1c_near_slack_line_is_never_admitted_taut():
    """Review V1 family: an extension far below the orbit reach is not taut, without any derivative."""
    from test_rao_mooring import spec as mooring_spec, SPAN
    p, *_ = _taut_parts(rest=SPAN - 1e-7)
    c = rr.prepare_case(p, mooring_spec())
    assert c.features['mooring_taut'] is False and c.category == 'nonlinear_reported'


# ---------------------------------------------------------------- round 10
def _artifacts(tmp_path, mutate_table=None, mutate_report=None):
    """A complete hash-consistent case/table/report set, optionally mutated and re-hashed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    import json
    c = case()
    t = table(c, BASE)
    t.update(artifact='anymarine_rao_table', project_input_hash=c.project_input_hash)
    t['table_hash'] = rr._hash(t)
    report = rr.compare(c, t, [extraction(0.1, BASE), extraction(0.05, BASE)])
    report = json.loads(json.dumps(report))
    if mutate_table:                                 # after the report: the loader checks entries first
        mutate_table(t)
        t['table_hash'] = rr._hash(t)
    if mutate_report:
        mutate_report(report)
        report['report_hash'] = rr._hash(report)
    (tmp_path / 'case.json').write_text(json.dumps(dict(case=c.to_dict(), case_hash=c.case_hash)), encoding='utf-8')
    (tmp_path / 'rao_table.json').write_text(json.dumps(t), encoding='utf-8')
    (tmp_path / 'rao_report.json').write_text(json.dumps(report), encoding='utf-8')
    return tmp_path


def test_g1_loader_validates_every_field_the_graphs_read(tmp_path):
    from anymarine.qtapp import rao_panel
    rao_panel.load_results(_artifacts(tmp_path / 'ok'))            # the unmutated set loads
    cases = {
        'omega_zero': (lambda t: t['entries'][0].update(omega=0.0), None, 'entries'),
        'heading_off_grid': (lambda t: t['entries'][0].update(heading_degrees=45.0), None, 'entries'),
        'amplitude_bool': (lambda t: t['entries'][0].update(amplitude=True), None, 'entries'),
        'missing_phasor': (None, lambda r: r['runs'][0]['forces']['x'].pop('phasor'), 'grid'),
        'boolean_phasor': (None, lambda r: r['runs'][0]['forces']['x'].update(phasor=[[True, False]] * N), 'grid'),
        'short_phasor': (None, lambda r: r['runs'][0]['forces']['x'].update(phasor=[[0.0, 0.0]] * 3), 'grid'),
        'huge_integer': (None, lambda r: r['runs'][0]['forces']['x'].update(phasor=[[10 ** 400, 0]] * N), 'grid'),
    }
    for name, (mt, mr, match) in cases.items():
        with pytest.raises(rao_panel.RaoResultError, match=match):
            rao_panel.load_results(_artifacts(tmp_path / name, mt, mr))


def test_b1_reach_norm_does_not_underflow():
    assert rl._norm([1e-170, 0.0, 0.0]) == 1e-170
    assert rl._norm(np.array([3e-200, 4e-200j])) == pytest.approx(5e-200, rel=1e-15)
    assert rl._norm([3e200, 4e200]) == pytest.approx(5e200, rel=1e-15)
    assert rl._norm([]) == 0.0


@requires_solver
def test_b1_tiny_reach_with_huge_damping_is_not_certified():
    """Review B1: node phasor 1e-170 m with axial damping 1e171 N.s/m must lower the bound (c w D = 10 N)."""
    project, model, states, nodes, _ = _taut_parts()
    lines = project.native_inputs.to_dict()['mooring']['data']['lines']
    for line in lines:
        line['axial_damping'] = [1e171, 1e171]
    q = np.zeros(18, complex)
    q[12] = 1e-170
    bound = rl.mooring_tension_lower_bound(model, lines, states, nodes, q, 1.0)
    from test_rao_mooring import TENSION
    assert bound[0] < TENSION - 9.0 and bound[1] < TENSION - 9.0
