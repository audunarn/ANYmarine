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
          sources: list[dict]) -> dict:
    """A complete, hashed artifact; validated exactly as :func:`load` would."""
    data = dict(artifact=ARTIFACT, title=title, notes=list(notes), body=body, periods_s=list(periods_s),
                headings_deg=list(headings_deg), dofs=list(DOFS), sources=list(sources))
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
