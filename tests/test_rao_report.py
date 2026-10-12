"""RAO slice 6a: case registration from projects, FD table, TD campaign, comparison, CSV."""
import copy
import csv
import math

import numpy as np
import pytest

from conftest import requires_solver
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine import rao_case as rc
from anymarine import rao_report as rr
from test_rao_extraction import oscillator_project


def spec(**over):
    criteria = {**rc.GOVERNING_MAXIMA, **rc.PROPOSED_MAXIMA,
                'nearzero_amplitude_absolute': 5e-4, 'small_angle_bound_rad': 0.05, 'small_excursion_m': 0.05}
    data = dict(case_id='rao-oscillator-cylinder', linearization_point=dict(source='declared_initial_state'),
                radiation_source='kernel_discrete', omegas=[1.0], headings_degrees=[0.0], amplitudes=[0.05, 0.025],
                phase=0.0, drag_linearization='none', windows=dict(transient_cycles=6, window_cycles=3),
                fit_controls=dict(harmonics=2, intercept=True, drift=False), criteria=criteria,
                execution_limits=dict(max_frames_per_run=4000, wall_seconds_per_run=300, max_runs=2))
    data.update(over)
    return data


def oscillator_with_cylinder():
    """Linear oscillator fixture plus one inertia-only vertical Morison member on body A."""
    from anymarine.native_examples import morison_two_body_project
    p = oscillator_project()
    data = p.native_inputs.to_dict()
    morison = copy.deepcopy(morison_two_body_project().native_inputs.to_dict()['morison'])
    member = next(x for x in morison['members'] if x['member_id'] == 'vertical-member-a')
    bodies = [b.body_id for b in p.bodies]
    member['end_a']['body_id'] = member['end_b']['body_id'] = bodies[0]
    member['section'] = dict(diameter=0.06, drag_coefficient=0.0, added_mass_coefficient=1.0)
    morison['members'] = [member]
    morison['environment'].update(components=[dict(amplitude=0.2, omega=1.0, direction_degrees=0.0, phase=0.0)],
                                  current=dict(depths=[0.0, 10.0], velocities=[[0.0] * 3, [0.0] * 3]))
    data['morison'] = morison
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


@requires_solver
def test_case_table_campaign_and_comparison(tmp_path):
    project = oscillator_with_cylinder()
    case = rr.prepare_case(project, spec())
    assert case.category == 'kernel_consistency_only'
    assert case.features['potential_flow'] and case.features['small_motion'] and case.features['linearization_audited']
    table = rr.fd_table(project, case)
    entry = table['entries'][0]
    assert set(entry['forces']) == {'potential-flow-development', 'morison-members-development', 'link', 'dry-inertia'} \
        or 'dry-inertia' in entry['forces']
    total = sum(np.array([complex(*v) for v in values]) for values in entry['forces'].values())
    np.testing.assert_allclose(total, 0.0, atol=1e-10)   # contributions + dry inertia balance
    records, extractions = rr.run_campaign(project, case)
    assert len(records) == 2 and all(r.kind == 'mechanical_development' for r in records)
    report = rr.compare(case, table, extractions)
    assert report['verdict'] == 'not_adjudicated' and not report['criteria_adopted']
    values = {(c['name'], c['detail']): c for c in report['checks']}
    rao_amp = [c for c in report['checks'] if c['name'] == 'rao_amplitude_relative']
    assert all(c['value'] < 1e-3 for c in rao_amp)          # Newmark-only difference at w dt = 0.05
    # Above the declared near-zero scale every check passes. Unresolved DOFs carry
    # second-order Morison content (measured 1.7e-4 m at a = 0.05, 4.2e-5 m at a = 0.025,
    # scaling with a^2), below this test's declared 5e-4 m scale; at 1e-5 m it is flagged.
    assert all(c['passed'] for c in report['checks']), [c for c in report['checks'] if not c['passed']]
    out = rr.write_csv(tmp_path / 'csv', table, report)
    rows = list(csv.reader((out / 'rao_table.csv').open(encoding='utf-8')))
    assert rows[0][:4] == ['omega', 'heading_deg', 'source', 'dof'] and len(rows) == 1 + 12
    assert (out / 'comparison.csv').exists() and (out / 'contribution_forces.csv').exists()
    assert report['report_hash'] == rr._hash(report) and table['table_hash'] == rr._hash(table)


@requires_solver
def test_refusals():
    project = oscillator_with_cylinder()
    case = rr.prepare_case(project, spec())
    other = oscillator_project()
    with pytest.raises(rr.RaoReportError, match='different project inputs'):
        rr.fd_table(other, case)
    with pytest.raises(rr.RaoReportError, match='declared omega'):
        rr.prepare_case(project, spec(omegas=[1.3]))
    with pytest.raises(rr.RaoReportError, match='bound dataset'):
        rr.prepare_case(project, spec(radiation_source='dataset'))
    with pytest.raises(rr.RaoReportError, match='declared_initial_state'):
        rr.prepare_case(project, spec(linearization_point=dict(source='adopted_equilibrium')))
    data = project.native_inputs.to_dict()
    data['morison']['members'][0]['section']['drag_coefficient'] = 1.0
    dragged = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    with pytest.raises(rr.RaoReportError, match='drag'):
        rr.prepare_case(dragged, spec())
    from anymarine.native_examples import mooring_two_body_project
    with pytest.raises(rr.RaoReportError, match='mooring'):
        rr.prepare_case(mooring_two_body_project(), spec(radiation_source='none'))
    # large response -> not small motion -> nonlinear_reported
    big = rr.prepare_case(project, spec(amplitudes=[50.0, 25.0]))
    assert big.category == 'nonlinear_reported' and not big.features['small_motion']
