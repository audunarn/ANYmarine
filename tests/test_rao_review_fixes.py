"""RAO review findings F1-F9 (Codex round 1 on slices 1-6)."""
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
    assert report['demoted'] and 'observed_excursion_m' in names(report, False)
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
