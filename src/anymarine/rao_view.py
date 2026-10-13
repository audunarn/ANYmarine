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
# Contribution identities that differ between the FD table and the TD runs: the TD
# ``dry`` wrench is the internal inertial covector, the FD ``dry-inertia`` force its
# external equivalent (same mapping as the report's power closure).
TD_ALIASES = {'dry': ('dry-inertia', -1.0)}


@dataclass(frozen=True)
class Series:
    label: str
    kind: str          # 'line' (FD) or 'marker' (TD)
    xs: tuple
    ys: tuple


def amplitude_label(a) -> str:
    """Exact, round-trip label of a wave amplitude (distinct values never share a label)."""
    return f'{float(a)!r} m'


BODY, NODE = 'body', 'node'
_BODY_DOFS = {'surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'}


def _owners(table):
    """``(name, kind)`` owners in DOF order; a body and a mooring node may share a name."""
    owners = []
    for key in table['dof_keys']:
        name, dof = key.split('::', 1)
        owner = (name, BODY if dof in _BODY_DOFS else NODE)
        if owner not in owners:
            owners.append(owner)
    return owners


def owner_label(owner) -> str:
    name, kind = owner
    return f"{name} ({'body' if kind == BODY else 'mooring node'})"


def _resolve(owner, dof_keys):
    """A ``(name, kind)`` owner, or a plain name when exactly one owner carries it."""
    if isinstance(owner, (tuple, list)) and len(owner) == 2:     # Qt returns combo data as a list
        return (str(owner[0]), str(owner[1]))
    matches = [o for o in _owners(dict(dof_keys=list(dof_keys))) if o[0] == owner]
    if not matches:
        raise ValueError(f'no DOFs of {owner!r} in the artifacts')
    if len(matches) > 1:
        raise ValueError(f'owner {owner!r} is ambiguous (a body and a mooring node); pass (name, kind)')
    return matches[0]


def available(table, report=None) -> dict:
    """What the artifacts can show: owners (bodies, then nodes), headings, amplitudes, contributions."""
    entries = table['entries'] if table is not None else []
    runs = report['runs'] if report is not None else []
    headings = sorted({e['heading_degrees'] for e in entries} | {r['heading_degrees'] for r in runs})
    amplitudes = sorted({e['amplitude'] for e in entries if e.get('amplitude') is not None}
                        | {r['amplitude'] for r in runs})
    contributions = sorted({c for e in entries for c in e['forces']}
                           | {TD_ALIASES.get(c, (c, 1.0))[0] for r in runs for c in r.get('forces', {})})
    return dict(owners=[] if table is None else _owners(table), headings=headings, amplitudes=amplitudes,
                contributions=contributions, has_frequency=bool(entries), has_time=bool(runs))


def grid_for(owner, dof_keys=None):
    """The owner's grid (rigid body: surge..yaw; mooring node: x, y, z) from its kind or DOF keys."""
    if dof_keys is not None:
        name, kind = _resolve(owner, dof_keys)
        key = f'{name}::surge' if kind == BODY else f'{name}::x'
        if key not in dof_keys:
            raise ValueError(f'no DOFs of {owner!r} in the artifacts')
        return GRID if kind == BODY else NODE_GRID
    if isinstance(owner, (tuple, list)) and len(owner) == 2:
        return GRID if owner[1] == BODY else NODE_GRID
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
    owner = _resolve(owner, keys)
    out = {}
    for row in grid_for(owner, keys):
        for dof in row:
            if dof is None:
                continue
            key = f'{owner[0]}::{dof}'
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
                    label = f'FD {fd_source}' + ('' if a is None else f' a={amplitude_label(a)}')
                    found.append(Series(label, 'line', tuple(p[0] for p in points), tuple(p[1] for p in points)))
            if source in ('time', 'compare') and report is not None:
                markers = {}
                for run in report['runs']:
                    if run['heading_degrees'] != heading or (amplitude is not None and run['amplitude'] != amplitude):
                        continue
                    sign = 1.0
                    if quantity == 'displacement':
                        values = run['rao']
                    else:
                        # TD wrenches are recorded for body DOFs only (no mooring-node channel).
                        td_id = next((c for c, (alias, _) in TD_ALIASES.items() if alias == contribution), contribution)
                        force = run.get('forces', {}).get(td_id)
                        if td_id != contribution:
                            sign = TD_ALIASES[td_id][1]
                        values = None if force is None else force['phasor']
                        if values is not None and k >= len(values):
                            values = None
                    if values is None:
                        continue
                    markers.setdefault(run['amplitude'], []).append(
                        (_x(run['omega'], x_axis), _value(sign * complex(*values[k]), dof, quantity, display)))
                for a, points in sorted(markers.items()):
                    points.sort()
                    found.append(Series(f'TD a={amplitude_label(a)}', 'marker', tuple(p[0] for p in points),
                                        tuple(p[1] for p in points)))
            out[dof] = found
    return out
