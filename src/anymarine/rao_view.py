"""RAO graph series from verified RAO artifacts; Qt-free.

Turns the FD table (``rao_table.json``) and, when present, the TD report
(``rao_report.json``) into per-DOF series for a 3 x 2 graph grid: translations in
the left column, rotations in the right (surge/roll, sway/pitch, heave/yaw). Mooring
nodes have translations only. Values are per unit incident amplitude under the
artifacts' convention ``x(t) = Re{X exp(-i omega t)}``; the phase shown is ``arg X``.

Units follow OrcaWave's graph conventions so results can be set side by side:
displacement RAOs in m/m and deg/m, contribution forces in kN/m and kN.m/m. A
contribution force is the contribution's total force ``X_c - Z_c q`` (excitation
minus its impedance reaction), not an excitation-only load RAO.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

GRID = (('surge', 'roll'), ('sway', 'pitch'), ('heave', 'yaw'))
NODE_GRID = (('x', None), ('y', None), ('z', None))
ROTATIONS = ('roll', 'pitch', 'yaw')
SOURCES = ('frequency', 'time', 'compare')
QUANTITIES = ('displacement', 'force')
DISPLAYS = ('amplitude', 'phase')
X_AXES = ('period', 'frequency')


@dataclass(frozen=True)
class Series:
    label: str
    kind: str          # 'line' (FD) or 'marker' (TD)
    xs: tuple
    ys: tuple


def _owners(table):
    owners = []
    for key in table['dof_keys']:
        owner = key.split('::', 1)[0]
        if owner not in owners:
            owners.append(owner)
    return owners


def available(table, report=None) -> dict:
    """What the artifacts can show: owners (bodies, then nodes), headings, amplitudes, contributions."""
    entries = table['entries'] if table is not None else []
    runs = report['runs'] if report is not None else []
    headings = sorted({e['heading_degrees'] for e in entries} | {r['heading_degrees'] for r in runs})
    amplitudes = sorted({e['amplitude'] for e in entries if e.get('amplitude') is not None}
                        | {r['amplitude'] for r in runs})
    contributions = sorted({c for e in entries for c in e['forces']} | {c for r in runs for c in r.get('forces', {})})
    return dict(owners=[] if table is None else _owners(table), headings=headings, amplitudes=amplitudes,
                contributions=contributions, has_frequency=bool(entries), has_time=bool(runs))


def grid_for(owner: str):
    return NODE_GRID if owner.startswith('node') else GRID


def unit(dof: str, quantity: str, display: str) -> str:
    if display == 'phase':
        return 'deg'
    rotation = dof in ROTATIONS
    if quantity == 'displacement':
        return 'deg/m' if rotation else 'm/m'
    return 'kN.m/m' if rotation else 'kN/m'


def _value(z: complex, dof: str, quantity: str, display: str) -> float:
    if display == 'phase':
        return math.degrees(math.atan2(z.imag, z.real))
    magnitude = abs(z)
    if quantity == 'displacement':
        return math.degrees(magnitude) if dof in ROTATIONS else magnitude
    return magnitude / 1000.0


def _x(omega: float, x_axis: str) -> float:
    return 2.0 * math.pi / omega if x_axis == 'period' else omega


def series(table, report, *, owner, heading, quantity='displacement', contribution=None, source='compare',
           display='amplitude', x_axis='period', amplitude=None) -> dict:
    """``{dof: [Series]}`` for every DOF of ``owner`` in its grid.

    ``source``: 'frequency' (FD table lines, one per table source and amplitude),
    'time' (TD run markers, one series per amplitude) or 'compare' (both). A source
    without data contributes nothing. ``amplitude`` selects one wave amplitude (FD
    entries without an amplitude are amplitude-independent and always shown).
    """
    for name, value, allowed in (('source', source, SOURCES), ('quantity', quantity, QUANTITIES),
                                 ('display', display, DISPLAYS), ('x_axis', x_axis, X_AXES)):
        if value not in allowed:
            raise ValueError(f'{name} must be one of {allowed}')
    if quantity == 'force' and contribution is None:
        raise ValueError('a contribution is required for contribution forces')
    keys = list(table['dof_keys'])
    out = {}
    for row in grid_for(owner):
        for dof in row:
            if dof is None:
                continue
            key = f'{owner}::{dof}'
            if key not in keys:
                continue
            k = keys.index(key)
            found = []
            if source in ('frequency', 'compare'):
                curves = {}
                for e in table['entries']:
                    if e['heading_degrees'] != heading:
                        continue
                    if amplitude is not None and e.get('amplitude') not in (None, amplitude):
                        continue
                    values = e['rao'] if quantity == 'displacement' else e['forces'].get(contribution)
                    if values is None:
                        continue
                    curves.setdefault((e['source'], e.get('amplitude')), []).append(
                        (_x(e['omega'], x_axis), _value(complex(*values[k]), dof, quantity, display)))
                for (fd_source, a), points in curves.items():
                    points.sort()
                    label = f'FD {fd_source}' + ('' if a is None else f' a={a:g} m')
                    found.append(Series(label, 'line', tuple(p[0] for p in points), tuple(p[1] for p in points)))
            if source in ('time', 'compare') and report is not None:
                markers = {}
                for run in report['runs']:
                    if run['heading_degrees'] != heading or (amplitude is not None and run['amplitude'] != amplitude):
                        continue
                    if quantity == 'displacement':
                        values = run['rao']
                    else:
                        force = run.get('forces', {}).get(contribution)
                        values = None if force is None else force['phasor']
                    if values is None:
                        continue
                    markers.setdefault(run['amplitude'], []).append(
                        (_x(run['omega'], x_axis), _value(complex(*values[k]), dof, quantity, display)))
                for a, points in sorted(markers.items()):
                    points.sort()
                    found.append(Series(f'TD a={a:g} m', 'marker', tuple(p[0] for p in points),
                                        tuple(p[1] for p in points)))
            out[dof] = found
    return out
