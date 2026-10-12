"""Closed-world, hash-bound RAO case (design: docs/RAO_REPORTING.md); Qt-free.

A case fixes, before any solve, the frequency/heading/amplitude grid, the declared
linearizations, the fit controls and the criteria. Its category is derived from
the model features, never chosen. Criteria may be stricter than the ceilings
below, never looser. The additional ceilings (``PROPOSED_MAXIMA``) were adopted by
the owner on 2026-10-12 (IMPLEMENTATION_PLAN, RAO reporting criteria); without that
adoption ``criteria_adopted()`` would be False and no RAO verdict produced.
"""
import hashlib
import json
import math

from .hashing import canonical_json

SCHEMA_VERSION = 1
LINEARIZATION_SOURCES = ('declared_initial_state', 'adopted_equilibrium', 'static_solve_in_job')
RADIATION_SOURCES = ('none', 'dataset', 'kernel_discrete', 'both')
DRAG_LINEARIZATIONS = ('none', 'tangent_about_current', 'equivalent_harmonic')
CATEGORIES = ('matched_linearized', 'kernel_consistency_only', 'linearized_nonlinear', 'nonlinear_reported')

#: Governing ceilings from the plan / existing assessment contract.
GOVERNING_MAXIMA = {
    'rao_amplitude_relative': 0.03,
    'rao_phase_deg': 3.0,
    'steady_relative': 0.02,
    'fit_rms_relative': 0.02,
    'max_condition': 1.0e12,
}
#: Additional ceilings (docs/RAO_REPORTING.md section 3), adopted 2026-10-12.
PROPOSED_MAXIMA = {
    'linearity_relative': 0.01,
    'max_omega_dt': 0.05,
    'harmonic_distortion': 0.05,
    'power_closure_relative': 0.01,
    'advisory_amplitude_relative': 0.10,
    'advisory_phase_deg': 10.0,
}
#: Case-declared positive scales without a ceiling.
DECLARED_SCALES = ('nearzero_amplitude_absolute', 'small_angle_bound_rad', 'small_excursion_m')
CRITERIA_KEYS = frozenset(GOVERNING_MAXIMA) | frozenset(PROPOSED_MAXIMA) | frozenset(DECLARED_SCALES)
_CASE_ID_CHARACTERS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-')
_ADOPTED = True  # owner adoption 2026-10-12 (IMPLEMENTATION_PLAN, RAO reporting criteria)

_CASE_KEYS = ('schema_version', 'case_id', 'project_input_hash', 'linearization_point',
              'radiation_source', 'omegas', 'headings_degrees', 'amplitudes', 'phase',
              'drag_linearization', 'windows', 'fit_controls', 'criteria', 'execution_limits',
              'features', 'category', 'scope', 'created_utc')
_FEATURE_KEYS = ('potential_flow', 'any_drag', 'current', 'links', 'mooring', 'mooring_taut',
                 'contact_branch_risk', 'small_motion', 'linearization_audited')


class RaoCaseError(ValueError):
    """Malformed, looser-than-contract or inconsistent RAO case."""


def criteria_adopted():
    """True once the additional ceilings are adopted by the owner (2026-10-12)."""
    return _ADOPTED


def _require(condition, message):
    if not condition:
        raise RaoCaseError(message)


def _real(value, name, *, positive=False, nonnegative=False):
    _require(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value),
             f'{name} must be a finite number')
    _require(not positive or value > 0, f'{name} must be positive')
    _require(not nonnegative or value >= 0, f'{name} must be nonnegative')
    return float(value)


def _exact(data, keys, name):
    _require(isinstance(data, dict) and set(data) == set(keys),
             f'{name} needs exactly the fields {sorted(keys)}')


def derive_category(features, radiation_source, drag_linearization):
    """Deterministic category from model features (design section 3)."""
    _exact(features, _FEATURE_KEYS, 'features')
    for key in _FEATURE_KEYS:
        value = features[key]
        _require(type(value) is bool or (key == 'mooring_taut' and value is None),
                 f'feature {key} must be a boolean')
    _require(features['linearization_audited'], 'the linearization point is not an audited equilibrium')
    _require(features['mooring'] == (features['mooring_taut'] is not None),
             'mooring_taut is declared exactly when mooring is present')
    _require(features['potential_flow'] == (radiation_source != 'none'),
             'a radiation source is declared exactly when potential flow is present')
    if features['any_drag']:
        _require(drag_linearization != 'none', 'drag is present: declare a drag linearization')
    else:
        _require(drag_linearization == 'none', 'no drag is present: drag_linearization must be none')
    if (features['mooring'] and not features['mooring_taut']) or features['contact_branch_risk'] \
            or not features['small_motion']:
        return 'nonlinear_reported'
    if features['any_drag']:
        if features['current'] and drag_linearization == 'equivalent_harmonic':
            return 'nonlinear_reported'
        if not features['current'] and drag_linearization == 'tangent_about_current':
            return 'nonlinear_reported'
        return 'linearized_nonlinear'
    if features['potential_flow'] and radiation_source == 'kernel_discrete':
        return 'kernel_consistency_only'
    return 'matched_linearized'


def _criteria(data):
    _require(isinstance(data, dict) and set(data) <= CRITERIA_KEYS and data,
             f'criteria keys must be drawn from {sorted(CRITERIA_KEYS)}')
    out = {}
    for key, value in data.items():
        out[key] = _real(value, f'criteria.{key}', positive=True)
        ceiling = GOVERNING_MAXIMA.get(key, PROPOSED_MAXIMA.get(key))
        _require(ceiling is None or out[key] <= ceiling,
                 f'criteria.{key}={value} is looser than its ceiling {ceiling}')
    missing = (set(GOVERNING_MAXIMA) | set(PROPOSED_MAXIMA) | set(DECLARED_SCALES)) - set(out)
    _require(not missing, f'criteria missing {sorted(missing)}')
    return out


def _grid(values, name, *, positive):
    _require(isinstance(values, list) and values, f'{name} must be a nonempty list')
    out = [_real(v, f'{name}[]', positive=positive) for v in values]
    _require(len(set(out)) == len(out), f'{name} must be unique')
    return out


def prepare_rao_case(spec, *, project_input_hash, features, created_utc=''):
    """Validate a plain spec into a :class:`RaoCase`; derive its category."""
    _require(isinstance(spec, dict), 'spec must be a mapping')
    allowed = set(_CASE_KEYS) - {'schema_version', 'project_input_hash', 'features', 'category',
                                 'scope', 'created_utc'}
    _require(set(spec) == allowed, f'spec needs exactly the fields {sorted(allowed)}')
    _require(isinstance(spec['case_id'], str) and 0 < len(spec['case_id']) <= 120
             and all(c in _CASE_ID_CHARACTERS for c in spec['case_id']) and spec['case_id'][0] not in '.-',
             'case_id must be 1-120 characters of letters, digits, ".", "_" or "-" (it names files)')
    _require(isinstance(project_input_hash, str) and len(project_input_hash) == 64
             and all(c in '0123456789abcdef' for c in project_input_hash), 'project_input_hash must be sha256 hex')
    _exact(spec['linearization_point'], ('source',), 'linearization_point')
    _require(spec['linearization_point']['source'] in LINEARIZATION_SOURCES, 'unknown linearization source')
    _require(spec['radiation_source'] in RADIATION_SOURCES, 'unknown radiation source')
    _require(spec['drag_linearization'] in DRAG_LINEARIZATIONS, 'unknown drag linearization')
    omegas = _grid(spec['omegas'], 'omegas', positive=True)
    _require(omegas == sorted(omegas), 'omegas must increase')
    headings = _grid(spec['headings_degrees'], 'headings_degrees', positive=False)
    amplitudes = _grid(spec['amplitudes'], 'amplitudes', positive=True)
    _require(len(amplitudes) >= 2, 'at least two amplitudes are required (linearity check)')
    phase = _real(spec['phase'], 'phase')
    _exact(spec['windows'], ('transient_cycles', 'window_cycles'), 'windows')
    windows = dict(transient_cycles=_real(spec['windows']['transient_cycles'], 'transient_cycles', positive=True),
                   window_cycles=_real(spec['windows']['window_cycles'], 'window_cycles', positive=True))
    _require(windows['window_cycles'] >= 2, 'window_cycles must be at least 2')
    fit = spec['fit_controls']
    _exact(fit, ('harmonics', 'intercept', 'drift'), 'fit_controls')
    _require(type(fit['harmonics']) is int and 1 <= fit['harmonics'] <= 5, 'harmonics must be an integer in 1..5')
    _require(type(fit['intercept']) is bool and type(fit['drift']) is bool, 'intercept/drift must be booleans')
    limits = spec['execution_limits']
    _exact(limits, ('max_frames_per_run', 'wall_seconds_per_run', 'max_runs'), 'execution_limits')
    _require(type(limits['max_frames_per_run']) is int and 2 <= limits['max_frames_per_run'] <= 20001,
             'max_frames_per_run must be an integer in 2..20001')
    _real(limits['wall_seconds_per_run'], 'wall_seconds_per_run', positive=True)
    _require(limits['wall_seconds_per_run'] <= 600, 'wall_seconds_per_run must be at most 600')
    runs = len(omegas) * len(headings) * len(amplitudes)
    _require(type(limits['max_runs']) is int and runs <= limits['max_runs'],
             f'the grid needs {runs} runs, more than max_runs')
    criteria = _criteria(spec['criteria'])
    category = derive_category(dict(features), spec['radiation_source'], spec['drag_linearization'])
    data = dict(schema_version=SCHEMA_VERSION, case_id=spec['case_id'], project_input_hash=project_input_hash,
                linearization_point=dict(spec['linearization_point']), radiation_source=spec['radiation_source'],
                omegas=omegas, headings_degrees=headings, amplitudes=amplitudes, phase=phase,
                drag_linearization=spec['drag_linearization'], windows=windows, fit_controls=dict(fit),
                criteria=criteria, execution_limits=dict(limits), features=dict(features), category=category,
                scope=dict(development_only=True, scientific_qualification=False,
                           criteria_adopted=criteria_adopted()),
                created_utc=str(created_utc))
    return RaoCase(data)


class RaoCase:
    """Immutable case; the hash covers every field except ``created_utc``."""

    def __init__(self, data):
        _exact(data, _CASE_KEYS, 'RAO case')
        self._data = json.loads(json.dumps(data, allow_nan=False))

    def __getattr__(self, name):
        if name.startswith('_') or name not in _CASE_KEYS:
            raise AttributeError(name)
        return json.loads(json.dumps(self._data[name]))

    def to_dict(self):
        return json.loads(json.dumps(self._data, allow_nan=False))

    @property
    def case_hash(self):
        data = self.to_dict()
        data.pop('created_utc', None)
        return hashlib.sha256(canonical_json(data).encode('utf-8')).hexdigest()

    @classmethod
    def from_dict(cls, data, *, expected_hash=None):
        """Re-validate a stored case through :func:`prepare_rao_case`; refuse any drift."""
        _exact(data, _CASE_KEYS, 'RAO case')
        spec = {k: data[k] for k in _CASE_KEYS if k not in ('schema_version', 'project_input_hash', 'features',
                                                              'category', 'scope', 'created_utc')}
        _require(data['schema_version'] == SCHEMA_VERSION, 'unsupported RAO case schema')
        rebuilt = prepare_rao_case(spec, project_input_hash=data['project_input_hash'],
                                   features=data['features'], created_utc=data['created_utc'])
        _require(rebuilt.to_dict()['category'] == data['category'], 'stored category does not follow the features')
        scope = data['scope']
        _exact(scope, ('development_only', 'scientific_qualification', 'criteria_adopted'), 'scope')
        _require(scope['development_only'] is True and scope['scientific_qualification'] is False
                 and type(scope['criteria_adopted']) is bool, 'RAO case scope must stay development-only')
        stored = cls(data)
        _require(canonical_json({k: v for k, v in rebuilt.to_dict().items() if k != 'scope'})
                 == canonical_json({k: v for k, v in stored.to_dict().items() if k != 'scope'}),
                 'stored RAO case is not in canonical validated form')
        _require(expected_hash is None or stored.case_hash == expected_hash, 'RAO case hash mismatch')
        return stored


def write_case_artifact(case, path):
    """Write a case with its hash; refuse to overwrite evidence."""
    from pathlib import Path
    target = Path(path)
    if target.exists():
        raise RaoCaseError(f'{target} exists; case artifacts are never overwritten')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(case=case.to_dict(), case_hash=case.case_hash), indent=2,
                                 sort_keys=True), encoding='utf-8')
    return target


def read_case_artifact(path):
    """Read and fully re-validate a stored case artifact against its hash."""
    from pathlib import Path
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RaoCaseError(f'cannot read the RAO case artifact {path}: {error}') from error
    _exact(data, ('case', 'case_hash'), 'RAO case artifact')
    return RaoCase.from_dict(data['case'], expected_hash=data['case_hash'])
