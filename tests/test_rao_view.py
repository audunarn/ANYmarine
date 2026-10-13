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
    assert info['owners'] == [('hull', rv.BODY), ('node0', rv.NODE)] and info['headings'] == [0.0, 90.0]
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
    assert labels == ['TD a=0.5 m', 'TD a=1.0 m'] and all(x.kind == 'marker' for x in time_only['heave'])
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


# ---------------------------------------------------------------- review round 10
def _with(entries=None, runs=None, keys=KEYS):
    return dict(dof_keys=keys, entries=entries if entries is not None else TABLE['entries']), dict(runs=runs or [])


def test_g2_td_forces_exist_for_body_dofs_only():
    body_only = [dict(r, forces={'potential_flow': dict(phasor=r['forces']['potential_flow']['phasor'][:6], mean=[])})
                 for r in REPORT['runs']]
    table, report = _with(runs=body_only)
    s = rv.series(table, report, owner='node0', heading=0.0, quantity='force', contribution='potential_flow')
    assert all(x.kind == 'line' for series in s.values() for x in series)      # FD only, no IndexError
    hull = rv.series(table, report, owner='hull', heading=0.0, quantity='force', contribution='potential_flow')
    assert [x.kind for x in hull['surge']][-1] == 'marker'


def test_g3_td_dry_wrench_maps_to_fd_dry_inertia_with_opposite_sign():
    F = [pair(complex(100.0 * (i + 1), 50.0)) for i in range(len(KEYS))]
    entries = [dict(e, forces={'dry-inertia': F}) for e in TABLE['entries']]
    runs = [dict(r, forces={'dry': dict(phasor=[[-a, -b] for a, b in F], mean=[])}) for r in REPORT['runs']]
    table, report = _with(entries, runs)
    assert rv.available(table, report)['contributions'] == ['dry-inertia']
    s = rv.series(table, report, owner='hull', heading=0.0, quantity='force', contribution='dry-inertia',
                  display='phase', x_axis='frequency', amplitude=1.0)
    fd, td = s['heave']
    assert td.ys[0] == pytest.approx(dict(zip(fd.xs, fd.ys))[td.xs[0]])


def test_g4_owner_kind_comes_from_its_dof_keys():
    keys = [k.replace('hull::', 'nodeship::') for k in KEYS]
    assert rv.grid_for('nodeship', keys) == rv.GRID and rv.grid_for('node0', keys) == rv.NODE_GRID
    with pytest.raises(ValueError, match='no DOFs'):
        rv.grid_for('ghost', keys)


def test_g5_amplitude_labels_are_exact():
    assert rv.amplitude_label(0.05000001) != rv.amplitude_label(0.05000002)
    assert rv.amplitude_label(0.5) == '0.5 m'


def test_g4b_a_body_and_a_node_sharing_a_name_stay_distinct():
    """Review G4 remainder: a rigid body named node0 next to mooring node node0."""
    keys = [f'node0::{d}' for d in ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')] + ['node0::x', 'node0::y', 'node0::z']
    table = dict(dof_keys=keys, entries=TABLE['entries'])
    owners = rv.available(table)['owners']
    assert owners == [('node0', rv.BODY), ('node0', rv.NODE)]
    assert rv.grid_for(owners[0], keys) == rv.GRID and rv.grid_for(owners[1], keys) == rv.NODE_GRID
    assert set(rv.series(table, None, owner=owners[1], heading=0.0)) == {'x', 'y', 'z'}
    assert set(rv.series(table, None, owner=owners[0], heading=0.0)) == {'surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'}
    with pytest.raises(ValueError, match='ambiguous'):
        rv.series(table, None, owner='node0', heading=0.0)
    assert rv.owner_label(owners[1]) == 'node0 (mooring node)'


def test_g4c_body_ids_containing_the_delimiter():
    """Review round 11: a body named 'namespace::body' is a body, not a node named 'namespace'."""
    keys = [f'namespace::body::{d}' for d in ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')] + ['node0::x', 'node0::y', 'node0::z']
    table = dict(dof_keys=keys, entries=TABLE['entries'])
    assert rv.available(table)['owners'] == [('namespace::body', rv.BODY), ('node0', rv.NODE)]
    assert set(rv.series(table, None, owner=('namespace::body', rv.BODY), heading=0.0)) == {
        'surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'}
