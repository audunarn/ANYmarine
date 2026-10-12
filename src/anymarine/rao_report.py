"""RAO table, time-domain campaign and comparison (docs/RAO_REPORTING.md); Qt-free.

The FD excitation and each TD run come from the SAME derived single-wave project
(same excitation series / Morison environment), so they share the excitation by
construction. Tangents come from the wave-free copy at the declared stationary
linearization point. While the proposed criteria are not adopted, every check
is computed and reported but the verdict is ``not_adjudicated``.
Mooring lines (design slice 7) and drag (slice 8) are refused for now.
"""
import csv
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from .hashing import canonical_json, input_hash
from . import rao_case as rc
from . import rao_extraction as rx
from . import rao_linearize as rl

REPORT_VERSION = 1
STATIC_FORCE_LIMIT_N = 1e-8 + 1e-8 * 100.0     # equilibrium_jobs DEFAULT_SPEC atol + rel * scale
STATIC_MOMENT_LIMIT_NM = 1e-8 + 1e-8 * 100.0
DRAG_BALANCE_TOLERANCE = 1e-8      # relative change of the response between harmonic-balance iterates
DRAG_BALANCE_ITERATIONS = 200


class RaoReportError(ValueError):
    """The project or case cannot produce the requested RAO artifact."""


def _morison(model):
    return [c for c in model.contributions if type(c).__name__ == 'MorisonMemberSetContribution']


def _environments(native):
    envs = []
    if native.morison is not None:
        envs.append(native.morison.data['environment'])
    if native.mooring is not None and native.mooring.has_hydrodynamics:
        envs.append(native.mooring.data['environment'])
    return envs


def _supported(project):
    native = project.native_inputs
    if native is None:
        raise RaoReportError('the project has no native mechanical inputs')
    if native.mooring is not None:
        if native.mooring.has_contact:
            raise RaoReportError('seabed contact switches branches: not linearized (reported only, later slice)')
        if native.mooring.has_hydrodynamics:
            for line in native.mooring.data['lines']:
                for section in line['hydrodynamics']:
                    if section['drag_normal'] != 0 or section['drag_tangential'] != 0:
                        raise RaoReportError('line drag needs a declared linearization (RAO design slice 8)')
    return native


def bound_dataset(project):
    """The hash-bound ANYhydro source of a shared-wave excitation series, or None."""
    flow = project.native_inputs.potential_flow
    if flow is None or flow.to_dict().get('excitation_series') is None:
        return None
    from anyhydro import dataset_content_hash
    from anyhydro.fixtures import make_two_body_fixture
    source = make_two_body_fixture()
    if flow.to_dict()['excitation_series']['hydro_content_hash'] != dataset_content_hash(source):
        raise RaoReportError('only the bundled development excitation source can be bound')
    return source


def run_project(project, *, omega, heading_degrees, amplitude, phase, steps):
    """Derived project forced by one regular wave; everything else unchanged."""
    from .native_inputs import NativeMechanicalInputs
    from .domain import AnalysisSettings
    data = project.native_inputs.to_dict()
    component = dict(amplitude=float(amplitude), omega=float(omega),
                     direction_degrees=float(heading_degrees), phase=float(phase))
    flow = data.get('potential_flow')
    if flow is not None and flow.get('excitation_series') is not None:
        from anyloads import AiryComponent, WaveRealization
        from anyhydro import build_excitation_series, UNIT_INCIDENT_AMPLITUDE
        source = bound_dataset(project)
        if np.count_nonzero(np.asarray(source['omega'].values) == omega) != 1:
            raise RaoReportError(f'omega {omega} is not an exact sample of the bound source (no interpolation)')
        waves = WaveRealization((AiryComponent(float(amplitude), float(omega), float(heading_degrees), float(phase)),))
        data['wave_realization'] = waves.to_dict()
        flow['excitation_series'] = build_excitation_series(
            source, waves, amplitude_normalization=UNIT_INCIDENT_AMPLITUDE, phase_origin_xy=(0., 0.)).to_dict()
        component = waves.to_dict()['components'][0]
    elif flow is not None:
        if float(omega) != float(flow['omega']) or heading_degrees != 0 or phase != 0:
            raise RaoReportError('a single-omega fixture excitation exists only at its declared omega, heading 0, phase 0')
        flow['excitation_real'] = [amplitude * x for x in flow['excitation_real']]
        flow['excitation_imag'] = [amplitude * x for x in flow['excitation_imag']]
    if data.get('morison') is not None:
        data['morison']['environment']['components'] = [dict(component)]
    mooring = data.get('mooring')
    if mooring is not None and 'environment' in mooring['data']:
        mooring['data']['environment']['components'] = [dict(component)]
    data['run_spec'].update(steps=int(steps))
    dt = data['run_spec']['dt']
    return project.with_edits(settings=AnalysisSettings(duration=steps * dt, dt=dt),
                              native_inputs=NativeMechanicalInputs.from_dict(data))


def _prepare(project):
    from .mechanics import prepare_native_run
    return prepare_native_run(project)


class _Linear:
    """Calm tangents and the stationary point of a project."""

    def __init__(self, project):
        native = _supported(project)
        self.project = project
        calm = rl.wave_free_project(project)
        model, states, spec = _prepare(calm)
        self.model, self.states, self.dt = model, states, spec.dt
        from .mechanics import prepare_native_nodes
        self.nodes = prepare_native_nodes(calm)
        self.linear = rl.linearize(model, states, dt=spec.dt, nodes=self.nodes)
        self.node_positions = None if self.nodes is None else np.asarray(self.nodes.positions)
        force = self.linear.static_force
        self.static_force = float(max(np.max(np.abs(force[i * 6:i * 6 + 3])) for i in range(len(model.body_ids))))
        self.static_moment = float(max(np.max(np.abs(force[i * 6 + 3:i * 6 + 6])) for i in range(len(model.body_ids))))
        self.audited = self.static_force <= STATIC_FORCE_LIMIT_N and self.static_moment <= STATIC_MOMENT_LIMIT_NM
        self.anchors = {bid: (np.asarray(states[bid].position), np.asarray(states[bid].anchor))
                        for bid in model.body_ids}
        self.dataset = bound_dataset(project)
        self.native = native
        self.drag = native.morison is not None and any(
            m['section']['drag_coefficient'] != 0 for m in native.morison.data['members'])
        if self.drag and any(any(any(v) for v in env['current']['velocities']) for env in _environments(native)):
            raise RaoReportError('member drag with current has no admitted linearization (reported only)')

    def excitation(self, omega, heading, phase, amplitude=1.0, *, with_model=False):
        """Per-contribution excitation of the derived run project at ``amplitude``.

        Member drag (quadratic) is excluded here and added by harmonic balance.
        """
        derived = run_project(self.project, omega=omega, heading_degrees=heading, amplitude=amplitude,
                              phase=phase, steps=2)
        model, states, _ = _prepare(derived)
        out = {}
        flow = rl.potential_flow_contribution(model)
        if flow is not None:
            x = rl.potential_flow_excitation(flow, states, omega)
            out[rl.POTENTIAL_FLOW_ID] = rl._pad(x, self.linear.size) if self.nodes is not None else x
        for member_set in _morison(model):
            x = rl.stateless_wave_excitation(member_set, model, states, omega, exclude_drag=self.drag)
            out[member_set.contribution_id] = rl._pad(x, self.linear.size) if self.nodes is not None else x
        if model.mooring is not None and derived.native_inputs.mooring.has_hydrodynamics:
            from .mechanics import prepare_native_nodes
            out['mooring'] = rl.mooring_wave_excitation(model, states, prepare_native_nodes(derived), omega)
        return (out, model, states) if with_model else out

    def radiation(self, omega, source, rate_omega=None):
        flow = rl.potential_flow_contribution(self.model)
        if flow is None:
            return {}
        z = rl.radiation_impedance(flow, self.states, omega, source, self.dataset, rate_omega=rate_omega)
        return {rl.POTENTIAL_FLOW_ID: rl._pad(z, self.linear.size) if self.nodes is not None else z}

    def response(self, omega, heading, phase, source, *, newmark=False, amplitude=1.0):
        """RAO (response per unit amplitude) and per-unit-amplitude contribution forces.

        Without drag the model is linear and ``amplitude`` is immaterial. With member
        drag the first harmonic of the drag is balanced iteratively at ``amplitude``
        (describing-function linearization); the iteration count is returned in
        ``self.last_iterations``; non-convergence is a refusal.
        """
        x, model, states = self.excitation(omega, heading, phase, amplitude, with_model=True)
        r = rl.newmark_effective_omega(omega, self.dt) if newmark else None
        extra = self.radiation(omega, source, rate_omega=r)
        total = sum(x.values(), np.zeros(self.linear.size, complex))
        q = self.linear.solve(omega, total, extra, rate_omega=r)
        self.last_iterations = 0
        if self.drag:
            members = _morison(model)
            n, nb = self.linear.size, 6 * len(model.body_ids)
            z = self.linear.impedance(omega, extra, rate_omega=r)

            def drag(response):
                f = sum((rl.harmonic_drag(m, model, states, response, omega) for m in members),
                        np.zeros(nb, complex))
                return rl._pad(f, n) if self.nodes is not None else f

            def residual(response):
                return z @ response - total - drag(response)

            def real(v):
                return np.concatenate((v.real, v.imag))

            # Newton on (Re q, Im q): exact linear part plus a finite-difference
            # derivative of the drag fundamental (body DOFs only), with backtracking.
            zr = np.block([[z.real, -z.imag], [z.imag, z.real]])
            current = residual(q)
            for iteration in range(1, DRAG_BALANCE_ITERATIONS + 1):
                scale = max(float(np.max(np.abs(q[:nb]))), 1e-9)
                jac = zr.copy()
                base = real(drag(q))
                for j in range(2 * nb):
                    step = 1e-6 * scale
                    dq = np.zeros(n, complex)
                    dq[j % nb] = step if j < nb else 1j * step
                    jac[:, (j % nb) + (0 if j < nb else n)] -= (real(drag(q + dq)) - base) / step
                delta = np.linalg.solve(jac, -real(current))
                delta = delta[:n] + 1j * delta[n:]
                factor, accepted = 1.0, False
                while factor > 1e-6:
                    trial = q + factor * delta
                    try:
                        trial_residual = residual(trial)
                    except ValueError:              # chart/bounds refusal: shorten the step
                        factor *= 0.5
                        continue
                    if np.linalg.norm(trial_residual) <= np.linalg.norm(current) or factor < 1e-3:
                        accepted = True
                        break
                    factor *= 0.5
                if not accepted:
                    raise RaoReportError(f'drag harmonic balance line search failed at omega {omega}')
                q, current = trial, trial_residual
                if np.linalg.norm(factor * delta) <= DRAG_BALANCE_TOLERANCE * max(np.linalg.norm(q), 1e-300):
                    break
            else:
                raise RaoReportError(f'drag harmonic balance did not converge at omega {omega}, amplitude {amplitude}')
            self.last_iterations = iteration
            member_id = members[0].contribution_id
            x[member_id] = x[member_id] + drag(q)
        forces = self.linear.contribution_forces(omega, q, x, extra) if not newmark else None
        if forces is not None:
            forces = {cid: f / amplitude for cid, f in forces.items()}
        return q / amplitude, forces


def features(project, spec):
    """Model features and the FD-predicted motion check for category derivation."""
    lin = _Linear(project)
    native = lin.native
    current = any(any(any(v) for v in env['current']['velocities']) for env in _environments(native))
    radiation = spec['radiation_source']
    small = True
    criteria = spec['criteria']
    taut = None
    if not lin.audited:
        # No FD response at a point that is not an equilibrium; the case derivation refuses it.
        return dict(potential_flow=native.potential_flow is not None, any_drag=bool(lin.drag), current=bool(current),
                    links=bool(native.links), mooring=native.mooring is not None,
                    mooring_taut=None if native.mooring is None else False, contact_branch_risk=False,
                    small_motion=False, linearization_audited=False), lin
    if native.mooring is not None:
        tension = lin.linear.mooring_tension
        taut = bool(np.all(tension > 0))
        gradient = rl.mooring_tension_gradient(lin.model, lin.states, lin.nodes) if taut else None
    for omega in spec['omegas']:
        for heading in spec['headings_degrees']:
            # Without potential flow no radiation block exists, whatever the source label.
            source = 'dataset' if radiation in ('dataset', 'both') else 'kernel_discrete'
            q, _ = lin.response(omega, heading, spec['phase'], source, amplitude=max(spec['amplitudes']))
            q = q * max(spec['amplitudes'])
            if taut:
                # Linearization valid only while every segment stays taut: the predicted
                # tension amplitude at the largest amplitude must stay below the static tension.
                if np.any(np.abs(gradient @ q) >= lin.linear.mooring_tension):
                    taut = False
            for i in range(len(lin.model.body_ids)):
                # Peak vector magnitude of Re{Q exp(-iwt)} is bounded by ||Q|| (all phases).
                if np.linalg.norm(q[6 * i:6 * i + 3]) > criteria['small_excursion_m'] \
                        or np.linalg.norm(q[6 * i + 3:6 * i + 6]) > criteria['small_angle_bound_rad']:
                    small = False
    return dict(potential_flow=native.potential_flow is not None, any_drag=bool(lin.drag), current=bool(current),
                links=bool(native.links), mooring=native.mooring is not None, mooring_taut=taut,
                contact_branch_risk=False,
                small_motion=small, linearization_audited=bool(lin.audited)), lin


def prepare_case(project, spec, *, created_utc=''):
    """Register a case: derive features/category from the project, bind its input hash."""
    if spec.get('linearization_point', {}).get('source') != 'declared_initial_state':
        raise RaoReportError('only declared_initial_state linearization points are implemented')
    if project.native_inputs is not None and project.native_inputs.potential_flow is None \
            and spec.get('radiation_source') != 'none':
        raise RaoReportError('a model without potential flow declares radiation_source none')
    if spec.get('radiation_source') in ('dataset', 'both') and bound_dataset(project) is None:
        raise RaoReportError('no bound dataset: declare kernel_discrete')
    feats, _ = features(project, spec)
    return rc.prepare_rao_case(spec, project_input_hash=input_hash(project), features=feats,
                               created_utc=created_utc)


def _c(z):
    return [[float(v.real), float(v.imag)] for v in np.atleast_1d(z)]


def fd_table(project, case):
    """FD RAO entries (unit amplitude) per omega/heading and radiation source."""
    if case.project_input_hash != input_hash(project):
        raise RaoReportError('the case belongs to different project inputs')
    lin = _Linear(project)
    if not lin.audited:
        raise RaoReportError('the linearization point is not an equilibrium within the static limits')
    sources = {'none': [None], 'dataset': ['dataset'], 'kernel_discrete': ['kernel_discrete'],
               'both': ['dataset', 'kernel_discrete']}[case.radiation_source]
    entries = []
    # Linear models: one entry per omega/heading/source. With drag the RAO depends on the
    # amplitude: one entry per declared amplitude (harmonic balance at that amplitude).
    amplitudes = case.amplitudes if lin.drag else [None]
    for omega in case.omegas:
        for heading in case.headings_degrees:
            for source in sources:
                for amplitude in amplitudes:
                    q, forces = lin.response(omega, heading, case.phase, source or 'kernel_discrete',
                                             amplitude=amplitude or 1.0)
                    power = {cid: float(0.5 * np.real(np.vdot(-1j * omega * q, f))) for cid, f in forces.items()}
                    entry = dict(omega=omega, heading_degrees=heading, source=source or 'mechanical',
                                 rao=_c(q), forces={cid: _c(f) for cid, f in forces.items()}, power_fd=power)
                    if amplitude is not None:
                        entry.update(amplitude=amplitude, drag_iterations=lin.last_iterations,
                                     drag_linearization='first-harmonic balance of the reported member drag')
                    entries.append(entry)
    table = dict(artifact='anymarine_rao_table', schema_version=REPORT_VERSION, case_hash=case.case_hash,
                 project_input_hash=case.project_input_hash, dof_keys=list(lin.linear.dof_keys),
                 phasor_convention='x(t) = Re{X exp(-i omega t)}; RAO per unit incident amplitude',
                 linearization=dict(source='declared_initial_state', static_force_n=lin.static_force,
                                    static_moment_nm=lin.static_moment,
                                    limits=[STATIC_FORCE_LIMIT_N, STATIC_MOMENT_LIMIT_NM]),
                 matrices=dict(stiffness=lin.linear.stiffness.tolist(), damping=lin.linear.damping.tolist(),
                               mass=lin.linear.mass.tolist()),
                 entries=entries, scope=dict(development_only=True, scientific_qualification=False))
    table['table_hash'] = _hash(table)
    return table


def _hash(data):
    body = {k: v for k, v in data.items() if k not in ('table_hash', 'report_hash', 'created_utc')}
    return hashlib.sha256(canonical_json(body).encode('utf-8')).hexdigest()


def run_steps(case, lin, omega):
    period = 2.0 * math.pi / omega
    memory = 0.0
    flow = rl.potential_flow_contribution(lin.model)
    if flow is not None:
        memory = (flow.data.kernel.shape[0] - 1) * flow.data.dt
    transient = max(case.windows['transient_cycles'] * period, memory)
    steps = int(math.ceil((transient + 2 * case.windows['window_cycles'] * period) / lin.dt))
    if steps + 1 > case.execution_limits['max_frames_per_run']:
        raise RaoReportError(f'omega {omega} needs {steps + 1} frames, more than max_frames_per_run')
    return transient, steps


def solve_record(project, *, wall_seconds, store=None):
    """Headless native solve through the worker's admission path, with a deadline.

    A run that does not complete (deadline or solver stop) is handed to ``store``
    as partial evidence before the typed refusal.
    """
    from .services import ApplicationService
    from .native_jobs import project_from_snapshot, record_from_evidence
    from .mechanics import prepare_native_nodes
    import anysolver.marine_mechanical as solver
    _supported(project)
    steps = project.native_inputs.run_spec['steps']
    snapshot = ApplicationService().native_input_snapshot(project, max_frames=steps + 1, wall_seconds=wall_seconds)
    produced = project_from_snapshot(snapshot)
    model, states, spec = _prepare(produced)
    start = time.monotonic()
    deadline = lambda: time.monotonic() - start > float(wall_seconds)
    try:
        result = solver.solve_marine_mechanical(model, states, spec, cancel=deadline,
                                                initial_nodes=prepare_native_nodes(produced))
    except solver.MarineMechanicalSolveError as error:
        if store is not None:
            partial = getattr(error, 'partial', None)
            payload = dict(input_hash=snapshot['input_hash'], error=str(error), reason=getattr(error, 'reason', None),
                           timed_out=deadline())
            try:
                payload['evidence'] = None if partial is None else solver.result_to_dict(partial)
            except Exception as dump_error:          # keep the failure record even if the partial cannot be dumped
                payload['evidence_error'] = str(dump_error)
            store('failure', payload)
        raise RaoReportError(f'native run failed: {error}') from error
    evidence = solver.result_to_dict(result)
    if result.status != 'completed':
        if store is not None:
            store('partial', dict(input_hash=snapshot['input_hash'], status=result.status,
                                  timed_out=deadline(), evidence=evidence))
        raise RaoReportError(f'native run did not complete: {result.status}'
                             + (' (wall limit reached)' if deadline() else ''))
    return record_from_evidence(evidence, snapshot,
                                result_id=hashlib.sha256(snapshot['input_hash'].encode()).hexdigest()[:32],
                                note='RAO campaign run; development, unqualified', created_utc='')


def run_campaign(project, case, *, solve=solve_record, progress=None, store=None):
    """TD runs and extractions over the case grid; returns (records, extractions).

    ``store(kind, payload)`` receives every record as soon as it exists
    ('record'), partial or failed runs ('partial'/'failure'), so a later
    failure never discards earlier evidence.
    """
    if case.project_input_hash != input_hash(project):
        raise RaoReportError('the case belongs to different project inputs')
    lin = _Linear(project)
    records, extractions = [], []
    fit = case.fit_controls
    for omega in case.omegas:
        transient, steps = run_steps(case, lin, omega)
        for heading in case.headings_degrees:
            for amplitude in case.amplitudes:
                derived = run_project(project, omega=omega, heading_degrees=heading, amplitude=amplitude,
                                      phase=case.phase, steps=steps)
                record = solve(derived, wall_seconds=case.execution_limits['wall_seconds_per_run'], store=store)
                if store is not None:
                    store('record', record)
                ex = rx.extract_run(record, omega=omega, amplitude=amplitude, anchors=lin.anchors,
                                    transient_seconds=transient, window_cycles=case.windows['window_cycles'],
                                    harmonics=fit['harmonics'], intercept=fit['intercept'], drift=fit['drift'],
                                    max_condition=case.criteria['max_condition'], nodes=lin.node_positions)
                ex.update(heading_degrees=heading, run_input_hash=input_hash(derived), result_id=record.result_id,
                          omega_dt=omega * lin.dt)
                records.append(record)
                extractions.append(ex)
                if progress is not None:
                    progress(len(extractions))
    return records, extractions


def _check(name, value, limit, detail=''):
    return dict(name=name, value=float(value), limit=float(limit), passed=bool(value <= limit), detail=detail)


def _power(forces, response, omega, n_body):
    """Mean power per contribution 1/2 Re(F . conj(v)), v = -i w q (body DOFs).

    The stored ``dry`` wrench is the internal inertial covector (residual =
    external - dry), so it enters with the opposite sign: the powers then sum to zero.
    """
    v = -1j * omega * np.asarray(response)[:n_body]
    return {cid: (-1.0 if cid == 'dry' else 1.0) * float(0.5 * np.real(np.vdot(v, np.asarray(f['phasor'])[:n_body])))
            for cid, f in forces.items()}


def compare(case, table, extractions):
    """Checks per run against the category's FD reference; verdict per the case.

    Refuses a table of another case and an incomplete or duplicated campaign. A
    DOF is resolved when its FD reference response reaches the declared near-zero
    amplitude; unresolved DOFs get absolute checks on everything they contain.
    Observed motion outside the small-motion bounds demotes the case (matched/
    advisory verdicts become nogo); it is never silently re-categorized.
    """
    if table['case_hash'] != case.case_hash:
        raise RaoReportError('the FD table belongs to a different case')
    expected = sorted((w, h, a) for w in case.omegas for h in case.headings_degrees for a in case.amplitudes)
    got = sorted((x['omega'], x['heading_degrees'], x['amplitude']) for x in extractions)
    if got != expected:
        raise RaoReportError('the campaign does not cover the case grid exactly once per run')
    if any(list(x['dof_keys']) != list(table['dof_keys']) for x in extractions):
        raise RaoReportError('extraction DOF keys differ from the FD table (every table DOF is compared)')
    crit = case.criteria
    category = case.category
    nz = crit['nearzero_amplitude_absolute']
    reference_source = {'matched_linearized': ('dataset', 'mechanical'),
                        'kernel_consistency_only': ('kernel_discrete',),
                        'linearized_nonlinear': ('dataset', 'kernel_discrete', 'mechanical'),
                        'nonlinear_reported': ('dataset', 'kernel_discrete', 'mechanical')}[category]
    fd = {}
    for entry in table['entries']:
        if entry['source'] in reference_source:
            key = (entry['omega'], entry['heading_degrees'], entry.get('amplitude'))
            fd.setdefault(key, np.array([complex(*v) for v in entry['rao']]))
    per_amplitude = any(k[2] is not None for k in fd)
    advisory = category in ('linearized_nonlinear', 'nonlinear_reported')
    amp_limit = crit['advisory_amplitude_relative'] if advisory else crit['rao_amplitude_relative']
    phase_limit = crit['advisory_phase_deg'] if advisory else crit['rao_phase_deg']
    checks, by_point, demotions = [], {}, []
    for ex in extractions:
        key = (ex['omega'], ex['heading_degrees'])
        n = len(ex['dof_keys'])
        ref = fd[key + ((ex['amplitude'],) if per_amplitude else (None,))][:n]
        td = np.asarray(ex['rao'])
        a = ex['amplitude']
        label = f"omega={ex['omega']} heading={ex['heading_degrees']} a={a}"
        resolved = np.abs(ref) * a >= nz
        safe = np.where(resolved, ref, 1.0)
        amp_err = np.where(resolved, np.abs(np.abs(td) - np.abs(ref)) / np.abs(safe), 0.0)
        phase_err = np.where(resolved, np.abs(np.degrees(np.angle(td / safe))), 0.0)
        checks.append(_check('rao_amplitude_relative', amp_err.max(initial=0), amp_limit, label))
        checks.append(_check('rao_phase_deg', phase_err.max(initial=0), phase_limit, label))
        checks.append(_check('nearzero_amplitude_absolute',
                             float(np.max(np.where(~resolved, np.abs(td - ref) * a, 0.0), initial=0)), nz, label))
        response = np.asarray(ex['response'])
        steady = np.asarray(ex['steadiness'])
        rms = np.asarray(ex['residual_rms'])
        thd = np.asarray(ex['harmonic_distortion'])
        higher = np.asarray(ex['higher_absolute'])
        change = np.abs(response - np.asarray(ex['response_first']))
        checks.append(_check('steady_relative', float(np.max(steady[resolved], initial=0)), crit['steady_relative'], label))
        checks.append(_check('fit_rms_relative', float(np.max((rms / np.maximum(np.abs(response), nz))[resolved],
                                                               initial=0)), crit['fit_rms_relative'], label))
        checks.append(_check('harmonic_distortion', float(np.max(thd[resolved], initial=0)),
                             crit['harmonic_distortion'], label))
        checks.append(_check('unresolved_content_absolute',
                             float(np.max(np.concatenate((higher[~resolved], change[~resolved])), initial=0)),
                             nz, label + ' (higher harmonics, residual and window change of unresolved DOFs)'))
        checks.append(_check('max_omega_dt', ex['omega_dt'], crit['max_omega_dt'], label))
        trans = max(p[0] for p in ex['body_peaks'])
        rot = max(p[1] for p in ex['body_peaks'])
        for name, value, limit in (('observed_excursion_m', trans, crit['small_excursion_m']),
                                   ('observed_rotation_rad', rot, crit['small_angle_bound_rad'])):
            check = _check(name, value, limit, label)
            checks.append(check)
            if not check['passed']:
                demotions.append(f'{name} {value:.4g} > {limit:.4g} ({label})')
        power = _power(ex['forces'], response, ex['omega'], 6 * len(ex['body_peaks']))
        scale = sum(abs(v) for v in power.values())
        closure = abs(sum(power.values())) / scale if scale > 0 else 0.0
        checks.append(_check('power_closure_relative', closure, crit['power_closure_relative'],
                             label + ' (sum of mean contribution powers incl. dry inertia)'))
        by_point.setdefault(key, []).append(ex)
    for key, runs in by_point.items():
        runs = sorted(runs, key=lambda r: -r['amplitude'])
        top = runs[0]
        base = np.asarray(top['rao'])
        for run in runs[1:]:
            other = np.asarray(run['rao'])
            both = (np.abs(np.asarray(top['response'])) >= nz) & (np.abs(np.asarray(run['response'])) >= nz)
            rel = np.abs(other - base)[both] / np.abs(base)[both]
            detail = f'omega={key[0]} heading={key[1]} a={run["amplitude"]} vs a={top["amplitude"]}'
            checks.append(_check('linearity_relative', float(np.max(rel, initial=0)), crit['linearity_relative'], detail))
            checks.append(_check('linearity_absolute', float(np.max((np.abs(other - base) * run['amplitude'])[~both],
                                                                     initial=0)), nz, detail))
    failed = [c for c in checks if not c['passed']]
    # Linearized-nonlinear cases gate on convergence and balance only (design section 3);
    # RAO differences, amplitude dependence and harmonic content are reported.
    gating_nonlinear = ('steady_relative', 'power_closure_relative', 'max_omega_dt')
    if not rc.criteria_adopted():
        verdict = 'not_adjudicated'
    elif category == 'nonlinear_reported':
        verdict = 'reported_only'
    elif demotions:
        verdict = 'nogo'
    elif advisory:
        verdict = 'pass' if not [c for c in failed if c['name'] in gating_nonlinear] else 'nogo'
    else:
        verdict = 'pass' if not failed else 'nogo'
    report = dict(artifact='anymarine_rao_report', schema_version=REPORT_VERSION, case_hash=case.case_hash,
                  table_hash=table['table_hash'], category=category, reference_sources=list(reference_source),
                  criteria_adopted=rc.criteria_adopted(), demoted=demotions, checks=checks,
                  failed=[c['name'] for c in failed], verdict=verdict,
                  runs=[_jsonable_extraction(x) for x in extractions],
                  scope=dict(development_only=True, scientific_qualification=False))
    report['report_hash'] = _hash(report)
    return report


def _jsonable_extraction(ex):
    out = {}
    for key, value in ex.items():
        if key == 'forces':
            out[key] = {cid: dict(phasor=_c(v['phasor']), mean=np.asarray(v.get('mean', [])).tolist())
                        for cid, v in value.items()}
        elif isinstance(value, np.ndarray) and np.iscomplexobj(value):
            out[key] = _c(value)
        elif isinstance(value, np.ndarray):
            out[key] = value.tolist()
        else:
            out[key] = value
    return out


def write_csv(directory, table, report=None):
    """rao_table.csv, contribution_forces.csv and (with a report) rao_td.csv, comparison.csv."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    keys = table['dof_keys']
    with (root / 'rao_table.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['omega', 'heading_deg', 'source', 'dof', 'amplitude_per_m', 'phase_deg', 're', 'im'])
        for e in table['entries']:
            for key, (re, im) in zip(keys, e['rao']):
                z = complex(re, im)
                w.writerow([e['omega'], e['heading_degrees'], e['source'], key, abs(z), math.degrees(math.atan2(im, re)), re, im])
    with (root / 'contribution_forces.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['omega', 'heading_deg', 'source', 'contribution', 'dof', 'fd_re', 'fd_im', 'power_fd_w'])
        for e in table['entries']:
            for cid, values in e['forces'].items():
                for key, (re, im) in zip(keys, values):
                    w.writerow([e['omega'], e['heading_degrees'], e['source'], cid, key, re, im, e['power_fd'][cid]])
    if report is not None:
        with (root / 'rao_td.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['omega', 'heading_deg', 'amplitude', 'dof', 'amplitude_per_m', 'phase_deg', 'steadiness'])
            for run in report['runs']:
                for key, (re, im), s in zip(keys, run['rao'], run['steadiness']):
                    w.writerow([run['omega'], run['heading_degrees'], run['amplitude'], key, abs(complex(re, im)),
                                math.degrees(math.atan2(im, re)), s])
        with (root / 'comparison.csv').open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['check', 'value', 'limit', 'passed', 'detail'])
            for c in report['checks']:
                w.writerow([c['name'], c['value'], c['limit'], c['passed'], c['detail']])
    return root
