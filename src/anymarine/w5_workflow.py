"""Prospective retained W5 orchestration; numerical owners remain unchanged.

Preparing a case does not execute a backend. Execution requires an explicitly
supplied backend and its limits, in a separately supervised headless process.
Checks here never confer scientific qualification: frequency/tail adequacy and
independent adjudication are separate, explicitly open obligations.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path


class W5Error(Exception):
    """Invalid, stale, incomplete or failed W5 workflow."""


class W5Cancelled(W5Error):
    """Cancellation preserves all evidence produced so far."""


def _require(condition, message):
    if not condition:
        raise W5Error(message)


def _keys(data, expected, name):
    _require(isinstance(data, dict) and set(data) == set(expected),
             f'{name} requires exactly {sorted(expected)}')


def _number(value, name, *, positive=False):
    _require(not isinstance(value, bool) and isinstance(value, (int, float))
             and math.isfinite(value), f'{name} must be a finite number')
    _require(not positive or value > 0, f'{name} must be positive')
    return float(value)


def _integer(value, name, minimum=2):
    _require(type(value) is int and value >= minimum,
             f'{name} must be an integer >= {minimum}')
    return value


def _vector(value, name):
    _require(isinstance(value, list) and len(value) == 4,
             f'{name} must contain four retained coordinates')
    return [_number(v, name) for v in value]


def _json(data):
    try:
        return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise W5Error(f'case must contain only finite JSON values: {error}') from error


_FIELDS = (
    'schema_version', 'case_id', 'source_case', 'kernel_settings',
    'reconstruction_criteria', 'frames', 'linear_execution_limits',
    'dt_ratio', 't0', 'surge_link_stiffness', 'free_decay_initial_states',
    'regular_waves', 'assessment_criteria', 'fit_controls', 'declarations',
)
_DECLARATIONS = (
    'frequency_band', 'low_tail', 'high_tail', 'infinite_added_mass',
    'kernel_horizon', 'mesh_refinement', 'directional_diagnostics', 'correction_effect',
)
_OPEN_GATES = (
    'frequency_band_and_tail_adequacy', 'off_diagonal_radiation_force_observability',
    'independent_scientific_assessment',
)


@dataclass(frozen=True)
class W5Case:
    """Deeply immutable JSON case, validated prospectively by public owners."""

    _canonical: str

    def __post_init__(self):
        # No public constructor can bypass the same validation as from_dict.
        try:
            normalized = _validate(json.loads(self._canonical))
        except (ValueError, TypeError) as error:
            raise W5Error(f'invalid canonical W5 case: {error}') from error
        object.__setattr__(self, '_canonical', _json(normalized))

    @classmethod
    def from_dict(cls, data):
        return cls(_json(data))

    def to_dict(self):
        return json.loads(self._canonical)

    @property
    def case_hash(self):
        return hashlib.sha256(b'anymarine-retained-w5-v1\0' +
                              self._canonical.encode('utf-8')).hexdigest()


def _validate(data):
    _keys(data, _FIELDS, 'W5 case')
    _require(type(data['schema_version']) is int and data['schema_version'] == 1,
             'unsupported W5 schema_version')
    _require(isinstance(data['case_id'], str) and data['case_id'].strip(),
             'case_id must be nonempty')
    # Optional dependencies are reached only on explicit preparation/execution.
    try:
        from anyhydro.source_study_case import SourceStudyCase
        from anyhydro.radiation import RadiationSettings, reconstruction_criteria_from_dict
        from .domain import LinearExecutionLimits, LINEAR_MAX_FRAMES
        from .linear_assessment import validate_assessment_controls
        source = SourceStudyCase.from_dict(data['source_case'])
        _require(source.include_infinite_frequency, 'W5 requires explicit source A_inf')
        _require(source.n_jobs == source.n_threads == 1, 'W5 requires one job/thread')
        _keys(data['kernel_settings'], ('coarse', 'fine'), 'kernel_settings')
        kernels = {r: RadiationSettings.from_dict(data['kernel_settings'][r])
                   for r in ('coarse', 'fine')}
        criteria = reconstruction_criteria_from_dict(data['reconstruction_criteria'])
        controls = validate_assessment_controls(data['assessment_criteria'], data['fit_controls'])
        _keys(data['frames'], ('coarse', 'fine'), 'frames')
        _keys(data['linear_execution_limits'], ('coarse', 'fine'), 'linear_execution_limits')
        ratio = _integer(data['dt_ratio'], 'dt_ratio')
        _require(math.isclose(kernels['coarse'].dt, ratio * kernels['fine'].dt,
                              rel_tol=1e-12, abs_tol=1e-12), 'dt grids are not nested')
        durations, horizons = {}, {}
        for role, kernel in kernels.items():
            frames = _integer(data['frames'][role], f'frames.{role}')
            limit_data = data['linear_execution_limits'][role]
            limit = None if limit_data is None else LinearExecutionLimits.from_dict(limit_data)
            _require(frames <= (LINEAR_MAX_FRAMES if limit is None else limit.max_frames),
                     f'frames.{role} exceeds declared execution limits')
            _require(kernel.n_time >= frames, 'kernel must cover the declared response grid')
            durations[role] = (frames - 1) * kernel.dt
            horizons[role] = (kernel.n_time - 1) * kernel.dt
        _require(data['frames']['fine'] - 1 == ratio * (data['frames']['coarse'] - 1),
                 'response durations are not identical on nested grids')
        _require(math.isclose(horizons['coarse'], horizons['fine'], rel_tol=1e-12, abs_tol=1e-12),
                 'paired kernels must have the same physical horizon')
        coarse, fine = kernels['coarse'].to_dict(), kernels['fine'].to_dict()
        for value in (coarse, fine):
            for key in ('dt', 'n_time'):
                value.pop(key, None)
        _require(coarse == fine, 'paired kernels differ in physical/tail settings or execution-limit identity')
        _require(source.omegas[0] <= criteria.omega_min <= criteria.omega_max <= source.omegas[-1],
                 'reconstruction window lies outside the source band')
        if kernels['fine'].a_inf_policy == 'band_consistent':
            _require(kernels['fine'].a_inf_calibration_max < criteria.omega_min,
                     'the A_inf calibration band must lie below the reconstruction window')
        t0 = _number(data['t0'], 't0')
        link = _number(data['surge_link_stiffness'], 'surge_link_stiffness', positive=True)
        _require(isinstance(data['free_decay_initial_states'], list)
                 and data['free_decay_initial_states'], 'at least one free-decay case is required')
        for state in data['free_decay_initial_states']:
            _keys(state, ('q0', 'v0'), 'free-decay initial state')
            q, v = _vector(state['q0'], 'q0'), _vector(state['v0'], 'v0')
            _require(any(q) or any(v), 'a zero initial state cannot demonstrate free decay')
        _require(isinstance(data['regular_waves'], list) and data['regular_waves'],
                 'at least one regular-wave case is required')
        for wave in data['regular_waves']:
            _keys(wave, ('omega', 'heading', 'amplitude', 'phase', 'windows', 'q0', 'v0'), 'wave')
            omega = _number(wave['omega'], 'omega', positive=True)
            heading = _number(wave['heading'], 'heading')
            _require(omega in source.omegas and heading in source.wave_directions,
                     'wave must select an exact registered source frequency/heading')
            _require(criteria.omega_min <= omega <= criteria.omega_max,
                     'wave frequency lies outside the reconstruction window')
            _number(wave['amplitude'], 'amplitude', positive=True)
            _number(wave['phase'], 'phase')
            _vector(wave['q0'], 'wave.q0'); _vector(wave['v0'], 'wave.v0')
            _require(omega * kernels['coarse'].dt < math.pi, 'wave exceeds coarse Nyquist limit')
            _keys(wave['windows'], ('start', 'mid', 'end'), 'wave.windows')
            start, mid, end = [_number(wave['windows'][k], k) for k in ('start', 'mid', 'end')]
            _require(t0 < start < mid < end <= t0 + durations['fine'] + 1e-9,
                     'declare positive transient exclusion and fit windows inside the grid')
            _require(math.isclose(mid-start, end-mid, abs_tol=1e-9, rel_tol=0),
                     'fit windows must be adjacent and equal')
            _require(mid-start >= 4*math.pi/omega - 1e-9, 'each fit window needs at least two cycles')
        _keys(data['declarations'], _DECLARATIONS, 'declarations')
        _require(all(isinstance(v, str) and v.strip() for v in data['declarations'].values()),
                 'all scientific declarations must be explicit nonempty strings')
        normalized = json.loads(_json(data))
        normalized['source_case'] = source.to_dict()
        normalized['kernel_settings'] = {r: k.to_dict() for r, k in kernels.items()}
        normalized['reconstruction_criteria'] = criteria.to_dict()
        normalized['assessment_criteria'] = controls['criteria']
        normalized['fit_controls'] = controls['fit_controls']
        normalized['t0'], normalized['surge_link_stiffness'] = t0, link
        return normalized
    except W5Error:
        raise
    except Exception as error:
        raise W5Error(f'W5 public-owner contract refusal: {error}') from error


def _write(path, data):
    from .persistence import atomic_write_bytes, json_bytes
    atomic_write_bytes(Path(path), json_bytes(data))


def _state(root, case, state, **evidence):
    payload = dict(schema_version=1, case_hash=case.case_hash, state=state,
                   development_only=True, scientific_qualification=False,
                   open_qualification_gates=list(_OPEN_GATES), **evidence)
    _write(root / 'status.json', payload)
    return payload


def prepare_w5_case(spec, out_dir):
    """Validate the entire prospective case before creating exclusive evidence."""
    case = W5Case.from_dict(spec)
    from anyhydro.source_study_case import SourceStudyCase
    from anyhydro.source_study import prepare_source_study
    root = Path(out_dir)
    _require(not root.exists(), 'W5 output already exists; evidence is never overwritten')
    try:
        root.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise W5Error(f'cannot create exclusive W5 output: {error}') from error
    _write(root / 'case.json', {'case_hash': case.case_hash, 'case': case.to_dict()})
    try:
        prepare_source_study(SourceStudyCase.from_dict(case.to_dict()['source_case']), root / 'source')
        return _state(root, case, 'prepared')
    except Exception as error:
        _state(root, case, 'failed', phase='source_preparation', error=str(error))
        raise W5Error(f'W5 source preparation failed; evidence retained: {error}') from error


def load_w5_case(out_dir):
    root = Path(out_dir)
    try:
        record = json.loads((root / 'case.json').read_text(encoding='utf-8'))
        _keys(record, ('case_hash', 'case'), 'saved W5 case')
        case = W5Case.from_dict(record['case'])
        _require(record['case_hash'] == case.case_hash, 'saved W5 case hash mismatch')
        status = json.loads((root / 'status.json').read_text(encoding='utf-8'))
        _require(status['case_hash'] == case.case_hash, 'W5 status belongs to another case')
        _require(status.get('schema_version') == 1
                 and status.get('development_only') is True
                 and status.get('scientific_qualification') is False
                 and status.get('open_qualification_gates') == list(_OPEN_GATES)
                 and status.get('state') in ('prepared', 'running', 'no_go', 'failed', 'cancelled', 'checks_completed'),
                 'invalid or promoted W5 status')
        from anyhydro.source_study import load_source_study
        source = load_source_study(root / 'source')
        from anyhydro.source_study_case import SourceStudyCase
        _require(source['case_hash'] == SourceStudyCase.from_dict(case.to_dict()['source_case']).content_hash(),
                 'W5 source registration belongs to another case')
        return case, status
    except W5Error:
        raise
    except Exception as error:
        raise W5Error(f'cannot load complete W5 registration: {error}') from error


def _project(source_case, level):
    from anyhydro.source_study_case import build_study_bodies, study_lid_policy
    from anyhydro.first_slice import FIRST_SLICE_DRY_MASS
    from .domain import Body, BodyMesh, EquilibriumMetadata, ProjectModel
    bodies = []
    for hydro_body in build_study_bodies(source_case, level):
        ref = hydro_body.rotation_center
        def local(mesh):
            _require(all(int(n) == 3 for n in mesh.face_sizes), 'first-slice mesh must be triangular')
            return BodyMesh(
                positions=tuple(tuple(float(p[k])-ref[k] for k in range(3)) for p in mesh.vertices),
                triangles=tuple(tuple(int(i) for i in face[:3]) for face in mesh.faces))
        bodies.append(Body(hydro_body.body_id, hydro_body.name, tuple(ref),
                           FIRST_SLICE_DRY_MASS, local(hydro_body.mesh),
                           hydro_lid=local(hydro_body.lid_mesh), hydro_lid_policy=study_lid_policy(source_case)))
    return ProjectModel(name='Retained W5 registered first slice', bodies=tuple(bodies),
                        equilibrium=EquilibriumMetadata(waterline_z=0.,
                            description='Fixed registered first-slice equilibrium', source='declared'))


def _case_spec(case, project, run, role, partner, artifact):
    from .linear import linear_input_hash
    data = case.to_dict()
    provenance = project.hydro_provenance
    bundle = provenance.derived_bundle
    reference = dict(kind='hydro_source' if run['mode'] == 'free_decay' else 'hydro_spectrum',
                     route='derived_operators', dataset_content_hash=provenance.dataset_content_hash,
                     derived_content_hash=bundle['derived_content_hash'],
                     operators_raw_sha256=bundle['operators_raw_sha256'])
    phasors = None
    omega = None
    if run['mode'] == 'regular_wave':
        import numpy as np
        wave = run['wave']
        omega = wave['omega']
        reference.update(omega=omega, heading=wave['heading'], wave_amplitude=wave['amplitude'],
                         wave_phase=wave['phase'])
        wi = list(artifact.omegas).index(omega)
        di = list(artifact.wave_directions).index(wave['heading'])
        force = artifact.excitation_force[wi, di] * wave['amplitude'] * np.exp(1j * wave['phase'])
        phasors = [[float(v.real), float(v.imag)] for v in force]
    return dict(case_id=f"{data['case_id']}-{run['id']}-{role}", mode=run['mode'],
                input_hash=linear_input_hash(project.linear_inputs), result_id=None, reference=reference,
                omega=omega, force_phasors=phasors,
                windows=run['wave']['windows'] if omega is not None else None,
                fit_controls=data['fit_controls'], criteria=data['assessment_criteria'],
                refinement=dict(role=role, partner_case_hash=partner, dt_ratio=data['dt_ratio']),
                scope=dict(development_only=True, role='physical_retained_w5_checks', w5_case_hash=case.case_hash))


def retained_direction_diagnostics(loaded, source_case):
    """Report mesh changes in the finest retained damping eigenbasis.

    Uses declared normalized coordinates. A fixed finest basis per frequency
    avoids pretending independently sorted eigenvectors are matched modes.
    Degenerate modes can rotate within their subspace: the full projected delta
    is retained, and no per-mode convergence/passivity threshold is inferred.
    """
    import numpy as np
    finest = max(source_case.levels)
    reference = loaded['levels'][str(finest)]['artifact']
    scales = np.asarray(source_case.normalization.scales, dtype=float)
    scale = np.outer(scales, scales)
    reports = []
    for index, omega in enumerate(reference.omegas):
        B = np.asarray(reference.derived_damping[index]) * scale
        eigenvalues, basis = np.linalg.eigh(0.5*(B+B.T))
        item = dict(omega=float(omega), finest_eigenvalues=eigenvalues.tolist(),
                    finest_basis=basis.tolist(), levels={})
        for level in source_case.levels:
            artifact = loaded['levels'][str(level)]['artifact']
            _require(np.array_equal(artifact.omegas, reference.omegas),
                     'direction diagnostics require identical frequency grids')
            value = np.asarray(artifact.derived_damping[index]) * scale
            delta = basis.T @ (0.5*(value+value.T)-0.5*(B+B.T)) @ basis
            item['levels'][str(level)] = dict(
                projected_delta=delta.tolist(), spectral_delta=float(np.linalg.norm(delta, ord=2)),
                minimum_eigenvalue=float(np.linalg.eigvalsh(0.5*(value+value.T))[0]))
        reports.append(item)
    return dict(scope='retained directional reporting only; no new acceptance threshold',
                normalized_scales=scales.tolist(), finest_level=finest,
                basis_convention='fixed finest eigenbasis at each frequency; degenerate subspaces may rotate',
                frequencies=reports)


def execute_w5_case(out_dir, *, backend, execution_limits, should_cancel=None):
    """Run the registered route once under the caller's explicit supervisor.

    Requires an explicit backend/limits, never constructs one implicitly. Raw
    source, kernels, pre-solve cases, stored results and paired reports survive
    refusal/cancellation/failure. No result here constitutes W5 qualification.
    """
    root = Path(out_dir)
    case, status = load_w5_case(root)
    _require(status['state'] == 'prepared', 'only a fresh prepared W5 case can execute; no automatic retry')
    data = case.to_dict()
    phase = 'source'
    cancel_requested = False
    def cancelled():
        nonlocal cancel_requested
        # Once observed, cancellation cannot be withdrawn. Callback failures
        # propagate into the ordinary failed path, without a second invocation.
        if not cancel_requested and should_cancel is not None:
            cancel_requested = bool(should_cancel())
        return cancel_requested
    def check_cancel():
        if cancelled():
            raise W5Cancelled('W5 cancelled; incomplete evidence retained')
    _state(root, case, 'running', phase=phase)
    try:
        check_cancel()
        from anyhydro.source_study_case import SourceStudyCase
        from anyhydro.source_study import execute_source_study, load_source_study, adjudicate_source_study
        source_case = SourceStudyCase.from_dict(data['source_case'])
        execute_source_study(source_case, backend, execution_limits, root / 'source',
                             should_cancel=lambda level: cancelled())
        check_cancel()
        verdict = adjudicate_source_study(root / 'source')
        _write(root / 'source-adjudication.json', verdict)
        if verdict['outcome'] == 'cancelled':
            return _state(root, case, 'cancelled', phase=phase, reason='source study cancelled')
        if verdict['outcome'] == 'failed':
            return _state(root, case, 'failed', phase=phase, reason='source study failed')
        if verdict['outcome'] != 'source_admission_passed_development_only':
            return _state(root, case, 'no_go', phase=phase, reason='source admission failed')
        loaded = load_source_study(root / 'source')
        level = max(source_case.levels)
        entry = loaded['levels'][str(level)]
        artifact, dataset = entry['artifact'], entry['dataset']
        _write(root / 'directional-damping.json', retained_direction_diagnostics(loaded, source_case))
        report = entry['level_report']
        source_root = root / 'source' / f'level-{level:02d}'
        dataset_path, operators_path = source_root / report['raw_file'], source_root / report['derived_artifact']
        from anyhydro.radiation import RadiationSettings, radiation_kernel_from_derived_operators, save_radiation_kernel
        from .services import ApplicationService
        from .linear_assessment import write_case_artifact, write_report_artifact
        kernels = {}
        phase = 'radiation'
        _state(root, case, 'running', phase=phase)
        for role in ('coarse', 'fine'):
            check_cancel()
            kernel = radiation_kernel_from_derived_operators(artifact,
                RadiationSettings.from_dict(data['kernel_settings'][role]), source_dataset=dataset)
            path = root / f'kernel-{role}.nc'
            save_radiation_kernel(kernel, path)
            kernels[role] = path
        runs = [dict(id=f'decay-{i}', mode='free_decay', state=s, wave=None)
                for i, s in enumerate(data['free_decay_initial_states'])]
        runs += [dict(id=f'wave-{i}', mode='regular_wave', state=dict(q0=w['q0'], v0=w['v0']), wave=w)
                 for i, w in enumerate(data['regular_waves'])]
        pairs = []
        registrations = []
        for run in runs:
            phase = f"registration:{run['id']}"
            _state(root, case, 'running', phase=phase)
            members = {}
            # Every member case persists before ANY dynamics solve starts.
            for role in ('fine', 'coarse'):
                check_cancel()
                member_root = root / run['id'] / role
                service = ApplicationService()
                project = _project(source_case, level)
                service.save_current(project, member_root)
                wave = None if run['wave'] is None else {k: run['wave'][k] for k in ('omega', 'heading', 'amplitude', 'phase')}
                options = dict(n_steps=data['frames'][role], t0=data['t0'],
                    criteria=data['reconstruction_criteria'], q0=run['state']['q0'], v0=run['state']['v0'],
                    surge_link_stiffness=data['surge_link_stiffness'], wave=wave,
                    execution_limits=data['linear_execution_limits'][role])
                project = service.import_derived_hydro_inputs(project, dataset_path, operators_path, kernels[role], options)
                service.save(project)
                partner = None if role == 'fine' else members['fine']['case'].case_hash
                registered = service.prepare_linear_assessment(project,
                    _case_spec(case, project, run, role, partner, artifact))
                write_case_artifact(registered, member_root / 'registered-case.json')
                members[role] = dict(project=project, service=service, case=registered, root=member_root)
            registrations.append((run, members))
        for run, members in registrations:
            phase = f"response:{run['id']}"
            _state(root, case, 'running', phase=phase)
            for role in ('fine', 'coarse'):
                check_cancel()
                member = members[role]
                service = member['service']
                result = service.run_linear_local(member['project'], cancel=cancelled)
                member['project'] = service.add_result(member['project'], result)
                service.save(member['project'])
                member['result'] = result
                assessment = service.assess_linear_result(member['project'], result, member['case'])
                write_report_artifact(assessment, member['root'] / 'assessment.json')
                if assessment.verdict != 'pass':
                    return _state(root, case, 'no_go', phase=phase,
                                  reason='response assessment failed', report=str(member['root'] / 'assessment.json'))
            check_cancel()
            from .paired_refinement import PairedMember, assess_paired_refinement, save_paired_snapshot
            coarse, fine = [members[r] for r in ('coarse', 'fine')]
            cm = PairedMember(coarse['project'], coarse['result'], coarse['case'], project_root=coarse['root'])
            fm = PairedMember(fine['project'], fine['result'], fine['case'], project_root=fine['root'])
            paired = assess_paired_refinement(cm, fm)
            pair_path = root / run['id'] / 'paired-snapshot'
            save_paired_snapshot(cm, fm, paired, pair_path)
            pairs.append(dict(run=run['id'], verdict=paired.combined_verdict, snapshot=str(pair_path)))
            _write(root / 'pairs.json', pairs)
            if paired.combined_verdict != 'pass':
                return _state(root, case, 'no_go', phase=phase, reason='paired refinement failed', pairs=pairs)
        return _state(root, case, 'checks_completed', pairs=pairs,
                      note='Declared development checks complete; open gates prevent scientific qualification')
    except Exception as error:
        state = 'cancelled' if isinstance(error, W5Cancelled) or cancel_requested else 'failed'
        _state(root, case, state, phase=phase, error=str(error))
        if state == 'cancelled':
            raise W5Cancelled(str(error)) from error
        raise W5Error(f'W5 {phase} failed; evidence retained: {error}') from error


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'inspect'))
    parser.add_argument('--out', required=True)
    parser.add_argument('--spec')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            _require(args.spec is not None, 'prepare requires --spec')
            result = prepare_w5_case(json.loads(Path(args.spec).read_text(encoding='utf-8')), args.out)
        else:
            _, result = load_w5_case(args.out)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(dict(error=str(error), scientific_qualification=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
