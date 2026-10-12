"""RAO slice 8: Morison drag by first-harmonic (describing-function) balance."""
import math

import numpy as np
import pytest

from conftest import requires_solver
from anymarine.mechanics import prepare_native_run
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine import rao_case as rc
from anymarine import rao_linearize as rl
from anymarine import rao_report as rr
from test_rao_morison import A_WAVE, DEPTH, DIAM, G, OMEGA, PHASE, RHO, cylinder_project, morison, wavenumber
from test_rao_report import oscillator_with_cylinder

CD = 1.2


def drag_cylinder():
    p = cylinder_project()
    data = p.native_inputs.to_dict()
    data['morison']['members'][0]['section'].update(drag_coefficient=CD, added_mass_coefficient=0.0)
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


@requires_solver
def test_describing_function_matches_the_analytic_vertical_cylinder():
    p = drag_cylinder()
    model, states, spec = prepare_native_run(p)
    body = model.body_ids[0]
    x0 = float(np.asarray(states[body].position)[0])
    k = wavenumber()
    b = 0.5 * RHO * CD * DIAM
    u0 = A_WAVE * OMEGA / math.sinh(k * DEPTH)
    lo = -3.0
    s2 = lambda z: math.sinh(2 * k * (z + DEPTH))
    c2 = lambda z: math.cosh(2 * k * (z + DEPTH))
    int_c2 = (0.0 - lo) / 2 + (s2(0.0) - s2(lo)) / (4 * k)                       # int cosh^2
    prim = lambda z: z * z / 4 + (z * s2(z) / (2 * k) - c2(z) / (4 * k * k)) / 2  # int z cosh^2
    int_zc2 = prim(0.0) - prim(lo)
    phase = np.exp(1j * (k * x0 + PHASE))
    f = rl.harmonic_drag(morison(model), model, states, np.zeros(12), OMEGA, samples=512)
    coefficient = 8 / (3 * math.pi) * b * u0 ** 2
    np.testing.assert_allclose(f[0], coefficient * int_c2 * phase, rtol=1e-6)
    np.testing.assert_allclose(f[4], coefficient * int_zc2 * phase, rtol=1e-6)
    np.testing.assert_allclose(np.delete(f, [0, 4]), 0.0, atol=1e-9)


def spec(**over):
    criteria = {**rc.GOVERNING_MAXIMA, **rc.PROPOSED_MAXIMA,
                'nearzero_amplitude_absolute': 5e-4, 'small_angle_bound_rad': 0.05, 'small_excursion_m': 0.05}
    data = dict(case_id='rao-drag', linearization_point=dict(source='declared_initial_state'),
                radiation_source='kernel_discrete', omegas=[1.0], headings_degrees=[0.0], amplitudes=[0.05, 0.025],
                phase=0.0, drag_linearization='equivalent_harmonic', windows=dict(transient_cycles=6, window_cycles=3),
                fit_controls=dict(harmonics=3, intercept=True, drift=False), criteria=criteria,
                execution_limits=dict(max_frames_per_run=4000, wall_seconds_per_run=300, max_runs=2))
    data.update(over)
    return data


def dragged_oscillator():
    p = oscillator_with_cylinder()
    data = p.native_inputs.to_dict()
    data['morison']['members'][0]['section']['drag_coefficient'] = 5.0
    return p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


@requires_solver
def test_harmonic_balance_table_and_campaign():
    project = dragged_oscillator()
    case = rr.prepare_case(project, spec())
    assert case.features['any_drag'] and case.category == 'linearized_nonlinear'
    table = rr.fd_table(project, case)
    assert [e['amplitude'] for e in table['entries']] == [0.05, 0.025]
    assert all(1 <= e['drag_iterations'] < rr.DRAG_BALANCE_ITERATIONS for e in table['entries'])
    for e in table['entries']:
        total = sum(np.array([complex(*v) for v in f]) for f in e['forces'].values())
        np.testing.assert_allclose(total, 0.0, atol=1e-9)        # balance incl. linearized drag
    records, extractions = rr.run_campaign(project, case)
    report = rr.compare(case, table, extractions)
    assert report['category'] == 'linearized_nonlinear'
    assert report['verdict'] == 'pass', [c for c in report['checks'] if not c['passed']]
    rao = [c for c in report['checks'] if c['name'] == 'rao_amplitude_relative']
    assert all(c['value'] < 0.10 for c in rao)                    # advisory band, reported
    with pytest.raises(rc.RaoCaseError, match='drag'):
        rr.prepare_case(project, spec(drag_linearization='none'))
