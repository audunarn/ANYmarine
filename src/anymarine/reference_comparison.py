"""External reference comparison artifact (``anymarine_reference_comparison_v1``); Qt-free.

A reference comparison sets displacement RAOs from different programs side by side (for example an
OrcaWave reference and ANYhydro coefficients) for one body. It is evidence about external tools, never
an ANYmarine RAO case: it carries no project input hash, is not bound to a project and is always shown as
development, unqualified. Each source states its own provenance.

Layout: ``raos[source][heading][period][dof] = [re, im]`` per unit incident wave amplitude under the
ANYmarine convention ``x(t) = Re{X exp(-i omega t)}`` (a builder converts other conventions before
writing), translations in m/m and rotations in rad/m, about the body origin. The artifact hash is the
SHA-256 of the canonical JSON of every other field.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .rao_view import GRID, Series, _value, _x

ARTIFACT = 'anymarine_reference_comparison_v1'
DOFS = ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')
_KEYS = ('artifact', 'title', 'notes', 'body', 'periods_s', 'headings_deg', 'dofs', 'sources', 'artifact_hash')
_SOURCE_KEYS = ('label', 'program', 'version', 'method', 'details', 'raos')
_MAX_BYTES = 64 * 1024 * 1024


class ReferenceComparisonError(ValueError):
    """The artifact is missing, malformed or fails its hash."""


def _canonical(data: dict) -> bytes:
    body = {k: v for k, v in data.items() if k != 'artifact_hash'}
    return json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def artifact_hash(data: dict) -> str:
    try:
        return hashlib.sha256(_canonical(data)).hexdigest()
    except (TypeError, ValueError, OverflowError) as error:
        raise ReferenceComparisonError(f'artifact cannot be hashed: {error}') from error


def _finite(x) -> bool:
    if type(x) not in (int, float):          # bool is refused (type(True) is bool)
        return False
    try:
        return math.isfinite(float(x))
    except OverflowError:
        return False


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceComparisonError(message)


def build(*, title: str, notes: list[str], body: dict, periods_s: list[float], headings_deg: list[float],
          sources: list[dict], environment: dict | None = None) -> dict:
    """A complete, hashed artifact; validated exactly as :func:`load` would.

    ``environment`` (optional): ``{'water_depth_m': depth or None for infinite depth}`` for the animated
    incident wave; without it the view assumes deep water.
    """
    data = dict(artifact=ARTIFACT, title=title, notes=list(notes), body=body, periods_s=list(periods_s),
                headings_deg=list(headings_deg), dofs=list(DOFS), sources=list(sources))
    if environment is not None:
        data['environment'] = dict(environment)
    data['artifact_hash'] = artifact_hash(data)
    validate(data)
    return data


def write(data: dict, path) -> Path:
    validate(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, allow_nan=False), encoding='utf-8')
    return path


def load(path) -> dict:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ReferenceComparisonError(f'cannot read {path}: {error}') from error
    _require(len(raw) <= _MAX_BYTES, 'reference comparison file is too large')
    try:
        data = json.loads(raw.decode('utf-8'), parse_constant=lambda name: (_ for _ in ()).throw(
            ReferenceComparisonError(f'non-finite number {name} in the artifact')))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        if isinstance(error, ReferenceComparisonError):
            raise
        raise ReferenceComparisonError(f'not a JSON artifact: {error}') from error
    validate(data)
    return data


def validate(data) -> None:
    _require(isinstance(data, dict) and all(k in data for k in _KEYS), 'artifact lacks required fields')
    _require(data['artifact'] == ARTIFACT, f"not an {ARTIFACT} artifact")
    _require(isinstance(data['artifact_hash'], str), 'artifact hash must be a string')
    _require(artifact_hash(data) == data['artifact_hash'], 'artifact fails its hash verification')
    _require(isinstance(data['title'], str) and data['title'].strip() != '', 'title must be a non-empty string')
    _require(isinstance(data['notes'], list) and all(isinstance(n, str) for n in data['notes']), 'notes must be strings')
    _require(data['dofs'] == list(DOFS), 'dofs must be surge, sway, heave, roll, pitch, yaw')
    periods, headings = data['periods_s'], data['headings_deg']
    _require(isinstance(periods, list) and periods and all(_finite(t) and t > 0 for t in periods),
             'periods must be positive finite numbers')
    _require(len(set(periods)) == len(periods), 'periods must be distinct')
    _require(isinstance(headings, list) and headings and all(_finite(h) for h in headings),
             'headings must be finite numbers')
    _require(len(set(headings)) == len(headings), 'headings must be distinct')
    body = data['body']
    _require(isinstance(body, dict) and isinstance(body.get('name'), str) and body['name'].strip() != '',
             'body needs a name')
    _require(_finite(body.get('mass_kg')) and body['mass_kg'] > 0, 'body mass must be positive')
    cog = body.get('centre_of_mass_m')
    _require(isinstance(cog, list) and len(cog) == 3 and all(_finite(v) for v in cog),
             'body centre of mass must be three finite numbers')
    mesh = body.get('display_mesh')
    _require(isinstance(mesh, dict) and isinstance(mesh.get('positions'), list) and isinstance(mesh.get('triangles'), list)
             and mesh['positions'] and mesh['triangles'], 'body display mesh is missing')
    n_nodes = len(mesh['positions'])
    _require(all(isinstance(p, list) and len(p) == 3 and all(_finite(v) for v in p) for p in mesh['positions']),
             'display mesh positions must be finite (x, y, z)')
    _require(all(isinstance(t, list) and len(t) == 3 and all(type(i) is int and 0 <= i < n_nodes for i in t)
                 for t in mesh['triangles']), 'display mesh triangles must index the positions')
    if 'environment' in data:
        env = data['environment']
        _require(isinstance(env, dict) and set(env) == {'water_depth_m'}, 'environment holds water_depth_m only')
        depth = env['water_depth_m']
        _require(depth is None or (_finite(depth) and depth > 0), 'water depth must be positive or null (infinite)')
    sources = data['sources']
    _require(isinstance(sources, list) and len(sources) >= 1, 'at least one source is required')
    labels = set()
    for source in sources:
        _require(isinstance(source, dict) and all(k in source for k in _SOURCE_KEYS), 'source lacks required fields')
        for key in ('label', 'program', 'version', 'method'):
            _require(isinstance(source[key], str) and source[key].strip() != '', f'source {key} must be a string')
        _require(source['label'] not in labels, 'source labels must be distinct')
        labels.add(source['label'])
        _require(isinstance(source['details'], dict), 'source details must be an object')
        raos = source['raos']
        _require(isinstance(raos, list) and len(raos) == len(headings), 'raos must have one block per heading')
        for block in raos:
            _require(isinstance(block, list) and len(block) == len(periods), 'raos must have one row per period')
            for row in block:
                _require(isinstance(row, list) and len(row) == len(DOFS) and all(
                    isinstance(z, list) and len(z) == 2 and all(_finite(v) for v in z) for z in row),
                    'each RAO row needs six finite [re, im] pairs')


def water_depth(data: dict):
    """Declared water depth in m, or None for infinite (also when the artifact declares none)."""
    return data.get('environment', {}).get('water_depth_m')


def motion_rao(data: dict, *, source: int, heading: float, period: float) -> list[complex]:
    """Six complex displacement RAOs (m/m, rad/m) of one source at one heading and period."""
    h = data['headings_deg'].index(heading)
    p = data['periods_s'].index(period)
    return [complex(*z) for z in data['sources'][source]['raos'][h][p]]


def pose(rao: list[complex], *, amplitude: float, omega: float, time: float):
    """Translation (m) and rotation vector (rad) at ``time``: Re{X A exp(-i w t)} per DOF."""
    phasor = complex(math.cos(omega * time), -math.sin(omega * time))
    values = [(x * amplitude * phasor).real for x in rao]
    return values[:3], values[3:]


def rigid_motion(translation, rotation):
    """4 x 4 transform: rotation by the rotation vector (Rodrigues; exact, also for small RAO angles) about the
    body origin, then the translation."""
    import numpy as np
    vector = np.asarray(rotation, dtype=float)
    angle = float(np.linalg.norm(vector))
    matrix = np.eye(4)
    if angle > 0.0:
        k = vector / angle
        K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
        matrix[:3, :3] = np.eye(3) + math.sin(angle) * K + (1.0 - math.cos(angle)) * (K @ K)
    matrix[:3, 3] = np.asarray(translation, dtype=float)
    return matrix


def series(data: dict, *, heading: float, display: str = 'amplitude', x_axis: str = 'period') -> dict:
    """``{dof: [Series]}`` (one line per source) for the 3 x 2 RAO grid at one heading."""
    h = data['headings_deg'].index(heading)
    omegas = [2.0 * math.pi / t for t in data['periods_s']]
    order = sorted(range(len(omegas)), key=lambda p: _x(omegas[p], x_axis))
    out = {}
    for row in GRID:
        for dof in row:
            k = DOFS.index(dof)
            out[dof] = [Series(source['label'], 'line', tuple(_x(omegas[p], x_axis) for p in order),
                               tuple(_value(complex(*source['raos'][h][p][k]), dof, 'displacement', display)
                                     for p in order))
                        for source in data['sources']]
    return out
