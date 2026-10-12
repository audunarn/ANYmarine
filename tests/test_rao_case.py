"""RAO case: schema, ceilings, category derivation, hashing (RAO slice 1)."""
import copy

import pytest

from anymarine import rao_case as rc

HASH = 'a' * 64
LINEAR = dict(potential_flow=True, any_drag=False, current=False, links=True, mooring=False,
              mooring_taut=None, contact_branch_risk=False, small_motion=True, linearization_audited=True)


def spec(**over):
    criteria = {**rc.GOVERNING_MAXIMA, **rc.PROPOSED_MAXIMA,
                'nearzero_amplitude_absolute': 1e-6, 'small_angle_bound_rad': 0.05, 'small_excursion_m': 0.1}
    data = dict(case_id='rao-test', linearization_point=dict(source='declared_initial_state'),
                radiation_source='dataset', omegas=[0.8, 1.2], headings_degrees=[0.0], amplitudes=[0.1, 0.05],
                phase=0.0, drag_linearization='none', windows=dict(transient_cycles=10, window_cycles=3),
                fit_controls=dict(harmonics=3, intercept=True, drift=False), criteria=criteria,
                execution_limits=dict(max_frames_per_run=5000, wall_seconds_per_run=60, max_runs=4))
    data.update(over)
    return data


def test_valid_case_hash_and_round_trip():
    case = rc.prepare_rao_case(spec(), project_input_hash=HASH, features=LINEAR, created_utc='t0')
    assert case.category == 'matched_linearized' and case.scope['criteria_adopted'] is True
    later = rc.prepare_rao_case(spec(), project_input_hash=HASH, features=LINEAR, created_utc='t1')
    assert later.case_hash == case.case_hash
    back = rc.RaoCase.from_dict(case.to_dict(), expected_hash=case.case_hash)
    assert back.to_dict() == case.to_dict()
    assert rc.criteria_adopted()          # owner adoption 2026-10-12


@pytest.mark.parametrize('features, source, drag, category', [
    (LINEAR, 'dataset', 'none', 'matched_linearized'),
    (LINEAR, 'both', 'none', 'matched_linearized'),
    (LINEAR, 'kernel_discrete', 'none', 'kernel_consistency_only'),
    (dict(LINEAR, potential_flow=False), 'none', 'none', 'matched_linearized'),
    (dict(LINEAR, any_drag=True), 'dataset', 'equivalent_harmonic', 'linearized_nonlinear'),
    (dict(LINEAR, any_drag=True, current=True), 'dataset', 'tangent_about_current', 'linearized_nonlinear'),
    (dict(LINEAR, any_drag=True, current=True), 'dataset', 'equivalent_harmonic', 'nonlinear_reported'),
    (dict(LINEAR, any_drag=True), 'dataset', 'tangent_about_current', 'nonlinear_reported'),
    (dict(LINEAR, mooring=True, mooring_taut=False), 'dataset', 'none', 'nonlinear_reported'),
    (dict(LINEAR, mooring=True, mooring_taut=True), 'dataset', 'none', 'matched_linearized'),
    (dict(LINEAR, contact_branch_risk=True), 'dataset', 'none', 'nonlinear_reported'),
    (dict(LINEAR, small_motion=False), 'dataset', 'none', 'nonlinear_reported'),
])
def test_category_table(features, source, drag, category):
    assert rc.derive_category(features, source, drag) == category


@pytest.mark.parametrize('features, source, drag, match', [
    (dict(LINEAR, linearization_audited=False), 'dataset', 'none', 'audited'),
    (dict(LINEAR, any_drag=True), 'dataset', 'none', 'declare a drag'),
    (LINEAR, 'dataset', 'equivalent_harmonic', 'must be none'),
    (LINEAR, 'none', 'none', 'radiation source'),
    (dict(LINEAR, mooring=True), 'dataset', 'none', 'mooring_taut'),
])
def test_inconsistent_features_refused(features, source, drag, match):
    with pytest.raises(rc.RaoCaseError, match=match):
        rc.derive_category(features, source, drag)


@pytest.mark.parametrize('mutate, match', [
    (lambda s: s['criteria'].update(rao_phase_deg=3.5), 'looser'),
    (lambda s: s['criteria'].update(max_omega_dt=0.2), 'looser'),
    (lambda s: s['criteria'].pop('linearity_relative'), 'missing'),
    (lambda s: s['criteria'].update(unknown=1.0), 'drawn from'),
    (lambda s: s.update(amplitudes=[0.1]), 'two amplitudes'),
    (lambda s: s.update(omegas=[1.2, 0.8]), 'increase'),
    (lambda s: s.update(omegas=[0.8, 0.8]), 'unique'),
    (lambda s: s['windows'].update(window_cycles=1), 'at least 2'),
    (lambda s: s['execution_limits'].update(max_runs=3), 'more than max_runs'),
    (lambda s: s['execution_limits'].update(max_frames_per_run=30000), '2..20001'),
    (lambda s: s['fit_controls'].update(harmonics=True), 'harmonics'),
    (lambda s: s.update(drag_linearization='quadratic'), 'unknown drag'),
    (lambda s: s.update(extra=1), 'exactly the fields'),
])
def test_spec_refusals(mutate, match):
    data = spec()
    mutate(data)
    with pytest.raises(rc.RaoCaseError, match=match):
        rc.prepare_rao_case(data, project_input_hash=HASH, features=LINEAR)


def test_stored_case_tampering_refused():
    case = rc.prepare_rao_case(spec(), project_input_hash=HASH, features=LINEAR)
    for mutate in (lambda d: d.update(category='nonlinear_reported'),
                   lambda d: d['criteria'].update(rao_phase_deg=9.0),
                   lambda d: d['scope'].update(scientific_qualification=True),
                   lambda d: d.update(omegas=[0.8, 1.2000001])):
        data = copy.deepcopy(case.to_dict())
        mutate(data)
        with pytest.raises(rc.RaoCaseError):
            rc.RaoCase.from_dict(data, expected_hash=case.case_hash)
    with pytest.raises(rc.RaoCaseError, match='hash'):
        rc.RaoCase.from_dict(case.to_dict(), expected_hash='b' * 64)
