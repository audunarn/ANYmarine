"""Frequency-domain linearization of the native model (docs/RAO_REPORTING.md section 1).

Tangents come from the same public ANYsolver calls the time-domain solver uses
(ContributionSet in the 'initialization' phase plus RigidBody6D.dry_tangents),
evaluated at a stationary linearization point; nothing is re-derived by hand.
Convention: chart coordinates about the anchor, ``x(t) = Re{X exp(-i w t)}``,
so ``Z(w) = K - i w C - w^2 M`` and ``Z q = X``. Qt-free; ANYsolver is
imported lazily. Mooring lines add 3 DOFs per free node after the body block,
with the assembler's exact sparse tangents (MooringLineAssembler.evaluate).
"""
from dataclasses import dataclass
import importlib
import math

import numpy as np

DOF_NAMES = ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')


class RaoLinearizationError(ValueError):
    """The model or point cannot be linearized under the declared contract."""


@dataclass(frozen=True)
class LinearModel:
    """Total and per-contribution tangents at one stationary point."""
    body_ids: tuple
    dof_keys: tuple
    stiffness: np.ndarray
    damping: np.ndarray
    mass: np.ndarray
    static_force: np.ndarray
    contributions: dict
    dry: dict
    mooring_tension: np.ndarray = None

    @property
    def size(self):
        return self.stiffness.shape[0]

    def impedance(self, omega, extra=None, *, rate_omega=None):
        """``K - i r C - r^2 M`` plus frequency-dependent blocks ``extra[cid]``.

        ``r`` is ``omega`` (physical) or a declared rate frequency such as the
        Newmark effective frequency (:func:`newmark_effective_omega`).
        """
        r = omega if rate_omega is None else rate_omega
        z = self.stiffness - 1j * r * self.damping - r ** 2 * self.mass
        for block in (extra or {}).values():
            z = z + block
        return z

    def solve(self, omega, excitation, extra=None, *, rate_omega=None):
        """Response phasor q for the complex excitation vector X."""
        x = np.asarray(excitation, dtype=complex)
        if x.shape != (self.size,):
            raise RaoLinearizationError(f'excitation must have shape ({self.size},)')
        try:
            return np.linalg.solve(self.impedance(float(omega), extra, rate_omega=rate_omega), x)
        except np.linalg.LinAlgError as error:
            raise RaoLinearizationError(f'singular impedance at omega={omega}: {error}') from error

    def contribution_forces(self, omega, response, excitation=None, extra=None):
        """Force phasors per contribution: F_c = X_c - (K_c - i w C_c - w^2 M_c + Z_c(w)) q.

        ``excitation`` maps contribution id to its wave excitation phasor and
        ``extra`` to its frequency-dependent impedance (absent = 0). The dry
        inertial force is ``+w^2 M_dry q``; with an external force X they sum to -X.
        """
        q = np.asarray(response, dtype=complex)
        out = {}
        for cid, block in self.contributions.items():
            z = block['stiffness'] - 1j * omega * block['damping'] - omega ** 2 * block['mass']
            if extra is not None and cid in extra:
                z = z + extra[cid]
            x = np.zeros(self.size, complex) if excitation is None or cid not in excitation \
                else np.asarray(excitation[cid], dtype=complex)
            out[cid] = x - z @ q
        dry = np.zeros(self.size, complex)
        for bid, block in self.dry.items():
            i = 6 * self.body_ids.index(bid)
            z = block['stiffness'] - 1j * omega * block['damping'] - omega ** 2 * block['mass']
            dry[i:i + 6] = -(z @ q[i:i + 6])
        out['dry-inertia'] = dry
        return out


def _solver():
    try:
        return (importlib.import_module('anysolver.marine_contributions'),
                importlib.import_module('anysolver.marine_rigid'))
    except ImportError as error:
        raise RaoLinearizationError('install the tested ANYsolver marine mechanics') from error


def stationary_states(states):
    """Zero-rate states at the declared poses (chart origin at the anchor)."""
    _, rigid = _solver()
    out = {}
    for bid, state in states.items():
        if np.any(np.asarray(state.velocity)) or np.any(np.asarray(state.acceleration)):
            raise RaoLinearizationError(f'linearization point of {bid} is not stationary (nonzero rates)')
        if np.any(np.asarray(state.rotation_vector)):
            raise RaoLinearizationError(f'linearization point of {bid} must sit at its chart anchor')
        out[bid] = rigid.RigidBodyState(position=state.position, anchor=state.anchor)
    return out


def _owner_contributions(model, contributions):
    owner = []
    if model.links:
        owner.append(contributions.MechanicalLinkSetContribution(model.links))
    if model.gravity_world is not None:
        owner.extend(contributions.DryGravityContribution(body, model.gravity_world) for body in model.bodies)
    owner.extend(model.contributions)
    return owner


def _evaluate(contributions_module, body_ids, items, states, dt):
    owner = contributions_module.ContributionSet(body_ids, items)
    step = owner.prepare_step(contributions_module.StepInformation(0, dt, 'initialization'))
    try:
        return owner.evaluate_trial(step, states)
    finally:
        owner.abort_step(step)


def stationary_nodes(nodes):
    """Mooring node state with zero rates (refused otherwise)."""
    if nodes is None:
        raise RaoLinearizationError('a mooring model needs its node state at the linearization point')
    if np.any(np.asarray(nodes.velocities)) or np.any(np.asarray(nodes.accelerations)):
        raise RaoLinearizationError('mooring nodes are not stationary at the linearization point')
    return nodes


def _pad(matrix, size):
    """Zero-pad a body-layout vector/matrix to ``size`` (dtype preserved, complex included)."""
    out = np.zeros((size, size) if np.ndim(matrix) == 2 else (size,), dtype=np.result_type(matrix, float))
    n = np.shape(matrix)[0]
    if np.ndim(matrix) == 2:
        out[:n, :n] = matrix
    else:
        out[:n] = matrix
    return out


def linearize(model, states, *, dt, nodes=None):
    """Tangents of the native model at the stationary point ``states`` (and ``nodes``).

    ``dt`` is the run time step (the initialization phase is time zero).
    """
    mooring = getattr(model, 'mooring', None)
    if mooring is not None:
        nodes = stationary_nodes(nodes)
    contributions, _ = _solver()
    body_ids = tuple(model.body_ids)
    states = stationary_states({bid: states[bid] for bid in body_ids})
    items = _owner_contributions(model, contributions)
    total = _evaluate(contributions, body_ids, items, states, dt)
    per = {}
    for item in items:
        trial = _evaluate(contributions, body_ids, [item], states, dt)
        cid = item.contribution_id
        if cid in per:  # one dry-gravity contribution per body shares an id
            for key, value in (('stiffness', trial.stiffness), ('damping', trial.damping),
                               ('mass', trial.mass), ('force', trial.external_force)):
                per[cid][key] = per[cid][key] + np.asarray(value)
        else:
            per[cid] = dict(stiffness=np.array(trial.stiffness), damping=np.array(trial.damping),
                            mass=np.array(trial.mass), force=np.array(trial.external_force))
    stiffness, damping, mass = (np.array(total.stiffness), np.array(total.damping), np.array(total.mass))
    # Calm water only: the potential-flow pullback carries the excitation into
    # its geometric stiffness (-dD^T/dq X), so a live excitation would make the
    # tangents wave-dependent. Linearize the wave-free copy (wave_free_project).
    force = np.array(total.external_force)
    flow = potential_flow_contribution(model)
    if flow is not None:
        data = flow.data
        if data.excitation_series is None:
            declared = np.any(np.asarray(data.excitation_real)) or np.any(np.asarray(data.excitation_imag))
        else:
            declared = (np.any(np.asarray(data.excitation_series.weighted_real))
                        or np.any(np.asarray(data.excitation_series.weighted_imag)))
        if declared:
            raise RaoLinearizationError('the potential-flow excitation is active: linearize the wave-free project')
    dry = {}
    for i, body in enumerate(model.bodies):
        tangents = body.dry_tangents(states[body.body_id])
        sl = slice(6 * i, 6 * i + 6)
        stiffness[sl, sl] += tangents.stiffness
        damping[sl, sl] += tangents.damping
        mass[sl, sl] += tangents.mass
        force[sl] -= body.inertial_force(states[body.body_id])
        dry[body.body_id] = dict(stiffness=np.array(tangents.stiffness), damping=np.array(tangents.damping),
                                 mass=np.array(tangents.mass))
    dof_keys = tuple(f'{bid}::{name}' for bid in body_ids for name in DOF_NAMES)
    tension = None
    if mooring is not None:
        size = mooring.size
        stiffness, damping, mass, force = (_pad(stiffness, size), _pad(damping, size), _pad(mass, size),
                                           _pad(force, size))
        for block in per.values():
            for key in ('stiffness', 'damping', 'mass', 'force'):
                block[key] = _pad(block[key], size)
        assembly = mooring.evaluate(states, nodes, time=0.0)
        per['mooring'] = dict(stiffness=assembly.stiffness.toarray(), damping=assembly.damping.toarray(),
                              mass=assembly.mass.toarray(), force=np.asarray(assembly.force, dtype=float))
        stiffness = stiffness + per['mooring']['stiffness']
        damping = damping + per['mooring']['damping']
        mass = mass + per['mooring']['mass']
        force = force + per['mooring']['force']
        tension = np.asarray(assembly.tensions, dtype=float)
        dof_keys += tuple(f'node{i}::{axis}' for i in range(mooring.node_count) for axis in 'xyz')
    return LinearModel(body_ids, dof_keys, stiffness, damping, mass, force, per, dry, tension)


# --------------------------------------------------------------- potential flow
POTENTIAL_FLOW_ID = 'potential-flow-development'


def potential_flow_contribution(model):
    """The unique potential-flow contribution of the model, or None."""
    found = [c for c in model.contributions if getattr(c, 'contribution_id', None) == POTENTIAL_FLOW_ID]
    if len(found) > 1:
        raise RaoLinearizationError('more than one potential-flow contribution')
    return found[0] if found else None


def potential_flow_jacobian(contribution, states):
    """``D = dh/dq`` at the stationary point and the h-coordinate keys."""
    evaluation = contribution.validate_states(stationary_states(states))
    return np.asarray(evaluation.jacobian), tuple(evaluation.dof_keys)


def kernel_transfer(kernel, dt, omega):
    """Steady-state transfer of the solver's trapezoidal radiation convolution.

    ``R_n = dt (K_0 v_n / 2 + sum_{l=1}^{L-1} K_l v_{n-l} + K_L v_{n-L} / 2)``
    (anysolver marine_potential_flow, valid once n >= L = n_k - 1) gives
    ``R = Z_rad V`` with ``Z_rad = dt sum_l w_l K_l exp(i w l dt)``.
    """
    k = np.asarray(kernel, dtype=float)
    lags = np.arange(k.shape[0])
    weights = np.ones(k.shape[0])
    weights[0] = weights[-1] = 0.5
    phase = np.exp(1j * omega * dt * lags) * weights
    return dt * np.tensordot(phase, k, axes=(0, 0))


def _reorder(matrix_keys, keys, values, *, square):
    index = [list(matrix_keys).index(k) for k in keys]
    values = np.asarray(values)
    return values[np.ix_(index, index)] if square else values[index]


def newmark_effective_omega(omega, dt):
    """Average-acceleration Newmark steady state: rates act as ``-i r`` with
    ``r = (2/dt) tan(w dt / 2)`` while sampled phase shifts keep ``w``.

    With ``q_n = Q exp(-i w t_n)`` the update ``v_{n+1} = v_n + dt (a_n + a_{n+1})/2``,
    ``q_{n+1} = q_n + dt v_n + dt^2 (a_n + a_{n+1})/4`` gives ``V = -i r Q``, ``A = (-i r)^2 Q``.
    """
    if not 0.0 < omega * dt < math.pi:
        raise RaoLinearizationError('omega dt must lie in (0, pi)')
    return 2.0 / dt * math.tan(0.5 * omega * dt)


def radiation_impedance(contribution, states, omega, source, dataset=None, *, rate_omega=None):
    """Chart impedance block of radiation beyond the A_inf already in M.

    ``kernel_discrete``: ``-i w D^T Z_rad D`` (predicts the time-domain steady
    state; never a reference). ``dataset``: ``D^T (-i w B(w) - w^2 (A(w) - A_inf)) D``
    from the bound dataset at an exact sample (no interpolation), with A_inf the
    time-domain model's own.
    """
    data = contribution.data
    D, keys = potential_flow_jacobian(contribution, states)
    if source == 'kernel_discrete':
        r = omega if rate_omega is None else rate_omega
        z_h = -1j * r * kernel_transfer(data.kernel, data.dt, omega)
    elif source == 'dataset':
        if rate_omega is not None:
            raise RaoLinearizationError('dataset radiation is continuous physics; no rate frequency applies')
        if dataset is None:
            raise RaoLinearizationError('the dataset radiation source needs the bound dataset')
        omegas = np.asarray(dataset['omega'].values, dtype=float)
        hits = np.flatnonzero(omegas == omega)
        if hits.size != 1:
            raise RaoLinearizationError(f'omega {omega} is not an exact dataset frequency')
        influenced = tuple(str(k) for k in dataset['influenced_dof'].values)
        radiating = tuple(str(k) for k in dataset['radiating_dof'].values)
        if influenced != radiating or not set(keys) <= set(influenced):
            raise RaoLinearizationError('dataset DOF keys do not cover the potential-flow frames')
        a = _reorder(influenced, keys, dataset['added_mass'].values[hits[0]], square=True)
        b = _reorder(influenced, keys, dataset['radiation_damping'].values[hits[0]], square=True)
        z_h = -1j * omega * b - omega ** 2 * (a - np.asarray(data.added_mass_infinite))
    else:
        raise RaoLinearizationError(f'unknown radiation source {source!r}')
    return D.T @ z_h @ D


def potential_flow_excitation(contribution, states, omega):
    """Chart excitation phasor ``D^T X_h`` of the declared wave (per its own amplitude)."""
    data = contribution.data
    D, keys = potential_flow_jacobian(contribution, states)
    if data.excitation_series is None:
        if float(omega) != float(data.omega):
            raise RaoLinearizationError(f'the single-omega excitation is declared at {data.omega}, not {omega}')
        x_h = np.asarray(data.excitation_real) + 1j * np.asarray(data.excitation_imag)
    else:
        series = data.excitation_series
        hits = np.flatnonzero(np.asarray(series.omegas) == omega)
        if hits.size == 0:
            raise RaoLinearizationError(f'the excitation series has no term at omega {omega}')
        weights = (np.asarray(series.weighted_real) + 1j * np.asarray(series.weighted_imag))[hits].sum(axis=0)
        if not set(keys) <= set(series.dof_keys):
            raise RaoLinearizationError('excitation series DOF keys do not cover the frames')
        x_h = _reorder(series.dof_keys, keys, weights, square=False)
    return D.T @ x_h


# ------------------------------------------------------------ wave-free model
def wave_free_project(project):
    """Copy of a native project in calm water (every wave amplitude zero).

    Calm-water tangents must contain no wave-dependent term: wave-kinematics
    gradients (Morison/lines) and the potential-flow pullback of the excitation
    are second order in a linearization about calm water. Member/line components
    and a single-omega excitation are zeroed; a shared wave realization is
    replaced by the same components with zero amplitude and its excitation series
    is rebuilt from the bound source, so the calm project stays self-consistent
    (Morison components equal the realization) and its excitation is exactly zero.
    """
    from .native_inputs import NativeMechanicalInputs
    data = project.native_inputs.to_dict()
    if data.get('wave_realization') is not None:
        from anyloads import AiryComponent, WaveRealization
        from anyhydro import UNIT_INCIDENT_AMPLITUDE, build_excitation_series
        from anyhydro.fixtures import make_two_body_fixture
        saved = WaveRealization.from_dict(data['wave_realization'])
        calm = WaveRealization(tuple(AiryComponent(0.0, c.omega, c.direction_degrees, c.phase)
                                     for c in saved.components))
        data['wave_realization'] = calm.to_dict()
        data['potential_flow']['excitation_series'] = build_excitation_series(
            make_two_body_fixture(), calm, amplitude_normalization=UNIT_INCIDENT_AMPLITUDE,
            phase_origin_xy=(0., 0.)).to_dict()
    environments = []
    if data.get('morison') is not None:
        environments.append(data['morison']['environment'])
    mooring = data.get('mooring')
    if mooring is not None and 'environment' in mooring['data']:
        environments.append(mooring['data']['environment'])
    for env in environments:
        for component in env['components']:
            component['amplitude'] = 0.0
    flow = data.get('potential_flow')
    if flow is not None and flow.get('excitation_series') is None:
        flow['excitation_real'] = [0.0] * len(flow['excitation_real'])
        flow['excitation_imag'] = [0.0] * len(flow['excitation_imag'])
    return project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))


def _morison_drag(result):
    """Summed chart drag covector of a Morison member-set evaluation."""
    members = result.diagnostics.get('members', {})
    return sum((np.asarray(m['components']['drag'], dtype=float) for m in members.values()),
               np.zeros(np.shape(result.external_force)))


def stateless_wave_excitation(contribution, model, states, omega, *, exclude_drag=False):
    """Exact excitation phasor of a stateless contribution linear in the waves.

    At the fixed stationary geometry ``F(t) = F_s + a cos(w t) + b sin(w t)``
    when the force is linear in Airy kinematics (Morison inertia): three public
    evaluations at t = 0, T/4, T/2 give ``X = a + i b`` with the solver's own
    quadrature. Drag is quadratic: with ``exclude_drag`` its reported component is
    removed first (it is then treated by :func:`harmonic_drag`).
    """
    contributions, _ = _solver()
    body_ids = tuple(model.body_ids)
    layout = contributions.BodyLayout(body_ids)
    states = stationary_states({bid: states[bid] for bid in body_ids})
    quarter = math.pi / (2.0 * float(omega))

    def at(step):
        if step == 0:
            info = contributions.StepInformation(0, quarter, 'initialization')
        else:
            info = contributions.StepInformation(step, quarter, 'step', states)
        result = contribution.evaluate_trial_with_context(contributions.CommittedHistory(), states, layout, info)
        force = np.asarray(result.external_force, dtype=float)
        return force - _morison_drag(result) if exclude_drag else force

    f0, f1, f2 = at(0), at(1), at(2)
    mean = 0.5 * (f0 + f2)
    return (f0 - mean) + 1j * (f1 - mean)


# ------------------------------------------------------------------- mooring
def _perturbed(states, nodes, body_ids, q, v=None):
    """Body/node state displaced by the chart increment q, with chart rates v (default zero)."""
    _, rigid = _solver()
    from anysolver.marine_mooring import MooringNodeState
    v = np.zeros_like(q) if v is None else v
    out = {}
    for i, bid in enumerate(body_ids):
        base = states[bid]
        out[bid] = rigid.RigidBodyState(position=np.asarray(base.position) + q[6 * i:6 * i + 3],
                                        rotation_vector=q[6 * i + 3:6 * i + 6], velocity=v[6 * i:6 * i + 6],
                                        anchor=base.anchor)
    n = 6 * len(body_ids)
    positions = np.asarray(nodes.positions) + q[n:].reshape(-1, 3)
    return out, MooringNodeState(positions, v[n:].reshape(-1, 3), np.zeros_like(positions))


def mooring_tension_gradient(model, states, nodes, *, step=1e-6, rates=False, min_step=1e-13,
                             agreement=1e-6):
    """d(segment tension)/dq (or d/dqdot with ``rates``) by central differences of the public assembly.

    Tension is clipped at zero, so a step that crosses the clipping boundary averages
    two branches. The step is reduced tenfold until two successive steps agree to
    ``agreement`` (relative to the gradient scale); if no step down to ``min_step``
    agrees, the point is too close to slack for a local derivative and is refused.
    """
    mooring = model.mooring
    body_ids = tuple(model.body_ids)
    states = stationary_states({bid: states[bid] for bid in body_ids})
    nodes = stationary_nodes(nodes)
    zero = np.zeros(mooring.size)

    def gradient(h):
        columns = []
        for j in range(mooring.size):
            e = np.zeros(mooring.size)
            e[j] = h
            args = ((zero, e), (zero, -e)) if rates else ((e, None), (-e, None))
            plus = mooring.evaluate(*_perturbed(states, nodes, body_ids, *args[0]), time=0.0).tensions
            minus = mooring.evaluate(*_perturbed(states, nodes, body_ids, *args[1]), time=0.0).tensions
            columns.append((np.asarray(plus) - np.asarray(minus)) / (2 * h))
        return np.column_stack(columns)

    previous = gradient(step)
    h = step
    while h / 10.0 >= min_step:
        h /= 10.0
        current = gradient(h)
        scale = max(float(np.max(np.abs(current))), float(np.max(np.abs(previous))), 1e-300)
        if float(np.max(np.abs(current - previous))) <= agreement * scale:
            return current
        previous = current
    raise RaoLinearizationError('segment tension is not differentiable at the linearization point '
                                '(near slack); the taut linearization is not admitted')


def mooring_linear_tension_minimum(model, states, nodes, response, omega, static_tension):
    """Exact minimum of each segment's linearized tension ``T_s + Re{T exp(-i w t)}``.

    ``T = G q + G_v (-i w q)`` with the position and rate gradients of the public
    assembly (stiffness and axial damping); the minimum over a period is
    ``T_s - |T|`` exactly, without sampling.
    """
    q = np.asarray(response, dtype=complex)
    g = mooring_tension_gradient(model, states, nodes)
    gv = mooring_tension_gradient(model, states, nodes, rates=True)
    return np.asarray(static_tension, dtype=float) - np.abs(g @ q + gv @ (-1j * omega * q))


def mooring_wave_excitation(model, states, nodes, omega):
    """Exact line wave-excitation phasor (fluid inertia, zero drag) by three public force evaluations."""
    mooring = model.mooring
    body_ids = tuple(model.body_ids)
    states = stationary_states({bid: states[bid] for bid in body_ids})
    nodes = stationary_nodes(nodes)
    quarter = math.pi / (2.0 * float(omega))
    f0, f1, f2 = (np.asarray(mooring.force(states, nodes, time=k * quarter), dtype=float) for k in (0, 1, 2))
    mean = 0.5 * (f0 + f2)
    return (f0 - mean) + 1j * (f1 - mean)


# ---------------------------------------------------------------------- drag
def harmonic_drag(contribution, model, states, response, omega, *, samples=64):
    """First-harmonic phasor of the Morison drag covector for body motion ``response``.

    The stateless public contribution is evaluated at ``samples`` instants of one
    period with the bodies moving as ``q(t) = Re{q exp(-i w t)}`` (chart position,
    rate and acceleration) in the contribution's own wave field; the fundamental of
    its reported drag component is returned (describing-function linearization:
    for a single scalar relative velocity it equals ``(8/3pi) b |w| w``).
    """
    contributions, rigid = _solver()
    body_ids = tuple(model.body_ids)
    layout = contributions.BodyLayout(body_ids)
    base = stationary_states({bid: states[bid] for bid in body_ids})
    q = np.asarray(response, dtype=complex)[:6 * len(body_ids)]
    dt = 2.0 * math.pi / float(omega) / samples
    total = np.zeros(6 * len(body_ids), dtype=complex)
    for k in range(samples):
        t = k * dt
        e = np.exp(-1j * omega * t)
        pos, vel, acc = (q * e).real, (-1j * omega * q * e).real, (-omega ** 2 * q * e).real
        moving = {bid: rigid.RigidBodyState(position=np.asarray(base[bid].position) + pos[6 * i:6 * i + 3],
                                            rotation_vector=pos[6 * i + 3:6 * i + 6],
                                            velocity=vel[6 * i:6 * i + 6], acceleration=acc[6 * i:6 * i + 6],
                                            anchor=base[bid].anchor)
                  for i, bid in enumerate(body_ids)}
        info = (contributions.StepInformation(0, dt, 'initialization') if k == 0
                else contributions.StepInformation(k, dt, 'step', moving))
        result = contribution.evaluate_trial_with_context(contributions.CommittedHistory(), moving, layout, info)
        total += _morison_drag(result) * np.conj(e)
    return 2.0 * total / samples


def mooring_orbit_tensions(model, states, nodes, response, omega, *, samples=1024, refine=48,
                           max_local_minima=16):
    """Per-segment smallest tension of the full assembly along ``q(t) = Re{q exp(-i w t)}``.

    The public assembly is evaluated at moving body/node states (positions and
    rates), so finite rotations, finite orbits and axial damping are included.
    Every discrete local minimum of each segment's sampled tension (periodic) is
    refined by golden-section search between its neighbouring samples. This is a
    sampled check of the nonlinear tension; the linear model's own minimum is
    exact (:func:`mooring_linear_tension_minimum`).
    """
    _, rigid = _solver()
    from anysolver.marine_mooring import MooringNodeState
    mooring = model.mooring
    body_ids = tuple(model.body_ids)
    base = stationary_states({bid: states[bid] for bid in body_ids})
    nodes = stationary_nodes(nodes)
    q = np.asarray(response, dtype=complex)
    n = 6 * len(body_ids)
    period = 2.0 * math.pi / omega

    def tensions(t):
        e = np.exp(-1j * omega * t)
        pos, vel = (q * e).real, (-1j * omega * q * e).real
        moving = {bid: rigid.RigidBodyState(position=np.asarray(base[bid].position) + pos[6 * i:6 * i + 3],
                                            rotation_vector=pos[6 * i + 3:6 * i + 6], velocity=vel[6 * i:6 * i + 6],
                                            anchor=base[bid].anchor)
                  for i, bid in enumerate(body_ids)}
        node_state = MooringNodeState(np.asarray(nodes.positions) + pos[n:].reshape(-1, 3),
                                      vel[n:].reshape(-1, 3), np.zeros_like(np.asarray(nodes.positions)))
        return np.asarray(mooring.evaluate(moving, node_state, time=0.0).tensions, dtype=float)

    dt = period / samples
    table = np.array([tensions(k * dt) for k in range(samples)])
    lowest = table.min(axis=0)
    golden = (math.sqrt(5.0) - 1.0) / 2.0
    for segment in range(table.shape[1]):
        column = table[:, segment]
        if lowest[segment] <= 0.0:
            continue                       # already slack at a sample: refinement cannot change that
        # strict discrete local minima (periodic), lowest first; a flat run is one candidate
        local = np.flatnonzero((column <= np.roll(column, 1)) & (column < np.roll(column, -1)))
        local = local[np.argsort(column[local])][:max_local_minima]
        for k in local:
            a, b = (k - 1) * dt, (k + 1) * dt
            c, d = b - golden * (b - a), a + golden * (b - a)
            fc, fd = tensions(c)[segment], tensions(d)[segment]
            for _ in range(refine):
                if fc < fd:
                    b, d, fd = d, c, fc
                    c = b - golden * (b - a)
                    fc = tensions(c)[segment]
                else:
                    a, c, fc = c, d, fd
                    d = a + golden * (b - a)
                    fd = tensions(d)[segment]
            lowest[segment] = min(lowest[segment], fc, fd)
    return lowest


def mooring_orbit_min_tension(model, states, nodes, response, omega, *, samples=1024):
    """Smallest tension of any segment along the predicted orbit (see mooring_orbit_tensions)."""
    return float(np.min(mooring_orbit_tensions(model, states, nodes, response, omega, samples=samples)))
