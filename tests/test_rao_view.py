"""RAO graph series (Qt-free): grid, sources, units, axes."""
import math

import pytest

from anymarine import rao_view as rv

KEYS = [f'hull::{d}' for d in ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')] + ['node0::x', 'node0::y', 'node0::z']


def pair(z):
    return [z.real, z.imag]


def entry(omega, heading=0.0, source='dataset', scale=1.0, amplitude=None):
    rao = [pair(complex(0.1 * (i + 1) * scale * omega, 0.05)) for i in range(len(KEYS))]
    force = [pair(complex(1000.0 * (i + 1) * omega, 0.0)) for i in range(len(KEYS))]
    e = dict(omega=omega, heading_degrees=heading, source=source, rao=rao,
             forces={'potential_flow': force, 'mooring': force}, power_fd={})
    if amplitude is not None:
        e['amplitude'] = amplitude
    return e


TABLE = dict(dof_keys=KEYS, entries=[entry(w) for w in (1.0, 0.5, 2.0)] + [entry(1.0, heading=90.0)])
REPORT = dict(runs=[dict(omega=w, heading_degrees=0.0, amplitude=a,
                         rao=[pair(complex(0.1 * (i + 1) * w * 1.01, 0.05)) for i in range(len(KEYS))],
                         forces={'potential_flow': dict(phasor=[pair(complex(1000.0 * (i + 1) * w, 0.0))
                                                                for i in range(len(KEYS))], mean=[])})
                    for w in (0.5, 2.0) for a in (0.5, 1.0)])


def test_grid_is_three_rows_two_columns_translations_left():
    assert rv.GRID == (('surge', 'roll'), ('sway', 'pitch'), ('heave', 'yaw'))
    assert rv.grid_for('node0') == (('x', None), ('y', None), ('z', None))


def test_available_lists_owners_headings_amplitudes_and_sources():
    info = rv.available(TABLE, REPORT)
    assert info['owners'] == ['hull', 'node0'] and info['headings'] == [0.0, 90.0]
    assert info['amplitudes'] == [0.5, 1.0] and info['contributions'] == ['mooring', 'potential_flow']
    assert info['has_frequency'] and info['has_time']
    assert not rv.available(TABLE)['has_time']


def test_frequency_series_on_a_period_axis_sorted_with_units():
    s = rv.series(TABLE, None, owner='hull', heading=0.0, source='compare')
    assert set(s) == {'surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'}
    (surge,) = s['surge']                                  # no report: compare shows FD only
    assert surge.kind == 'line' and surge.label == 'FD dataset'
    assert surge.xs == tuple(sorted(2 * math.pi / w for w in (1.0, 0.5, 2.0)))
    by_period = dict(zip(surge.xs, surge.ys))
    assert by_period[2 * math.pi] == pytest.approx(abs(complex(0.1, 0.05)))
    roll = rv.series(TABLE, None, owner='hull', heading=0.0)['roll'][0]
    assert dict(zip(roll.xs, roll.ys))[2 * math.pi] == pytest.approx(math.degrees(abs(complex(0.4, 0.05))))
    assert rv.unit('roll', 'displacement', 'amplitude') == 'deg/m' and rv.unit('surge', 'force', 'amplitude') == 'kN/m'
    assert rv.unit('yaw', 'force', 'amplitude') == 'kN.m/m' and rv.unit('surge', 'displacement', 'phase') == 'deg'


def test_time_and_compare_and_amplitude_selection():
    time_only = rv.series(TABLE, REPORT, owner='hull', heading=0.0, source='time', x_axis='frequency')
    labels = [x.label for x in time_only['heave']]
    assert labels == ['TD a=0.5 m', 'TD a=1 m'] and all(x.kind == 'marker' for x in time_only['heave'])
    assert time_only['heave'][0].xs == (0.5, 2.0)
    both = rv.series(TABLE, REPORT, owner='hull', heading=0.0, source='compare', amplitude=1.0)
    assert [x.kind for x in both['surge']] == ['line', 'marker']
    assert rv.series(TABLE, REPORT, owner='hull', heading=90.0, source='time')['surge'] == []


def test_contribution_forces_in_kilonewton_and_phase_display():
    s = rv.series(TABLE, REPORT, owner='hull', heading=0.0, quantity='force', contribution='potential_flow',
                  source='compare', x_axis='frequency', amplitude=0.5)
    fd, td = s['sway']
    assert dict(zip(fd.xs, fd.ys))[2.0] == pytest.approx(2000.0 * 2.0 / 1000.0)
    assert dict(zip(td.xs, td.ys))[2.0] == pytest.approx(4.0)
    mooring = rv.series(TABLE, REPORT, owner='hull', heading=0.0, quantity='force', contribution='mooring',
                        source='compare')
    assert [x.kind for x in mooring['surge']] == ['line']          # no TD mooring force recorded
    phase = rv.series(TABLE, None, owner='hull', heading=0.0, display='phase', x_axis='frequency')['surge'][0]
    assert dict(zip(phase.xs, phase.ys))[1.0] == pytest.approx(math.degrees(math.atan2(0.05, 0.1)))


def test_node_owner_and_refusals():
    s = rv.series(TABLE, REPORT, owner='node0', heading=0.0, source='frequency')
    assert set(s) == {'x', 'y', 'z'}
    with pytest.raises(ValueError, match='source'):
        rv.series(TABLE, None, owner='hull', heading=0.0, source='both')
    with pytest.raises(ValueError, match='contribution'):
        rv.series(TABLE, None, owner='hull', heading=0.0, quantity='force')
