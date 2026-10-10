"""Four-DOF two-body linear development route: inputs, demo and solver bridge.

This module owns the application side of the linear development slice. It
builds and validates immutable input records (see
:mod:`anymarine.domain`), provides one clearly labelled analytic demo with a
linear link, and bridges to the public ``anysolver.marine_linear`` API. It
never duplicates the numerical integration: the equation of motion, signs,
direct causal convolution and zero prehistory convention live entirely in the
solver and are called as-is.

Scope boundaries (fail closed): no Capytaine conversion, no nonlinear
mechanics, no BEM runs, no qualification claims. The solver is an optional
dependency; every entry point fails closed with an actionable error when it
is unavailable. Only a complete, non-cancelled, finite solver result becomes
a ``linear_development`` result record; surge maps to x and heave to z about
the bound bodies' fixed equilibrium reference positions with zero rotation.
"""

from __future__ import annotations

from .surface_assignments import body_surface_hash, surface_result_snapshot, capture_surface_geometry

import datetime as _datetime
import uuid
from collections.abc import Mapping
from typing import Optional

import numpy as np

from . import RESULT_KIND_LINEAR
from .domain import (
    HYDRO_ROUTE_DERIVED,
    LINEAR_SOURCE_ANALYTIC_DEMO,
    LINEAR_SOURCE_HYDRO,
    LINEAR_SOURCES,
    LinearBodyBinding,
    LinearDevelopmentInputs,
    LinearExecutionLimits,
    LinearProvenance,
    ProjectModel,
    ResultRecord,
)
from .hashing import canonical_json, equilibrium_hash, mesh_hash

__all__ = [
    "LINEAR_RESULT_NOTE",
    "LINEAR_RESULT_NOTE_HYDRO",
    "LINEAR_RESULT_NOTE_HYDRO_DERIVED",
    "LINEAR_RESULT_CHANNEL_PREFIXES",
    "LinearBindingError",
    "LinearCancelledError",
    "LinearError",
    "LinearInputRejected",
    "LinearResultIncomplete",
    "LinearSolveFailed",
    "LinearSolverUnavailableError",
    "analytic_demo_inputs",
    "linear_input_hash",
    "linear_result_source",
    "linear_result_stale",
    "run_linear",
    "verify_body_binding",
    "verify_linear_binding",
    "verify_linear_result",
]

LINEAR_RESULT_NOTE = (
    "Linear development dynamics (four-DOF two-body surge/heave) computed by "
    "the public anysolver.marine_linear route (Newmark, direct causal radiation "
    "convolution, zero prehistory). Development route only: not boundary-"
    "element-method output, not a qualified marine analysis."
)

LINEAR_RESULT_NOTE_HYDRO = (
    "Linear development dynamics (four-DOF two-body surge/heave) computed by "
    "the public anysolver.marine_linear route (Newmark, direct causal radiation "
    "convolution, zero prehistory) from effective matrices projected from an "
    "imported ANYhydro hydrodynamic dataset and radiation kernel. Development "
    "source, unqualified: not a qualified marine analysis."
)

LINEAR_RESULT_NOTE_HYDRO_DERIVED = (
    "Linear development dynamics (four-DOF two-body surge/heave) computed by "
    "the public anysolver.marine_linear route (Newmark, direct causal radiation "
    "convolution, zero prehistory) from effective matrices admitted through "
    "the explicit ANYhydro derived-operator import (admitted derived Ainf, "
    "projected unmodified hydrostatics, derived radiation kernel). "
    "Development source, unqualified: not a qualified marine analysis."
)


class LinearError(Exception):
    """Base class for linear development route failures."""


class LinearSolverUnavailableError(LinearError):
    """The optional anysolver.marine_linear backend is not importable."""


class LinearBindingError(LinearError):
    """The stored body binding no longer matches the project bodies."""


class LinearInputRejected(LinearError):
    """The solver refused the admitted inputs (admission failure)."""


class LinearSolveFailed(LinearError):
    """The solver failed numerically inside the integration."""


class LinearCancelledError(LinearError):
    """The run was cancelled or did not complete; no result is produced."""


class LinearResultIncomplete(LinearError):
    """A result record does not completely represent its own linear run."""


def _import_solver():
    """Lazily import the optional solver module (never at package import)."""

    try:
        from anysolver import marine_linear
    except Exception as error:  # ImportError or a broken optional install
        raise LinearSolverUnavailableError(
            "the optional solver module anysolver.marine_linear is not available "
            f"({type(error).__name__}: {error}); install ANYsolver or extend "
            "PYTHONPATH / ANYMARINE_SOLVER_PATH with its src directory - the "
            "linear development route fails closed without it"
        ) from error
    return marine_linear


def linear_input_hash(inputs: LinearDevelopmentInputs) -> str:
    """sha256 over every effective numerical input of the linear job.

    Covers the body bindings (identity, order, reference position, mass),
    the full coupled matrices, the sampled kernel and its grid, forcing,
    q0/v0, the grid parameters and the provenance source/description. Only
    ``created_utc`` is excluded: wall-clock creation time is not an effective
    numerical input, so two otherwise identical input sets hash identically.
    """

    import hashlib

    data = inputs.to_dict()
    provenance = dict(data["provenance"])
    provenance.pop("created_utc", None)
    data["provenance"] = provenance
    payload = canonical_json(data).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_body_binding(bodies, inputs: LinearDevelopmentInputs) -> None:
    """Fail closed unless *inputs* bind exactly *bodies*, in order.

    Identity, order, display name, equilibrium reference position, mass, the
    immutable hull mesh identity (mesh hash) and the immutable numerical lid
    identity (local lid mesh hash, when a lid is bound) must all match
    exactly (these values round-trip through JSON without arithmetic, so
    exact equality is the correct check). A stale binding is never silently
    rebound to edited bodies; a mesh vertex edit or a lid edit/removal/
    replacement is as stale as a mass edit.
    """

    bindings = inputs.bindings
    body_tuple = tuple(bodies)
    if any(b.reference_orientation != ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)) for b in body_tuple):
        raise LinearBindingError("legacy linear bindings do not support nonidentity body reference orientation")
    if len(body_tuple) != len(bindings):
        raise LinearBindingError(
            f"linear inputs bind {len(bindings)} body/bodies but the project "
            f"has {len(body_tuple)}; the body binding is stale"
        )
    for binding, body in zip(bindings, body_tuple):
        if binding.body_id != body.body_id:
            raise LinearBindingError(
                f"linear inputs bind body {binding.body_id!r} but the project "
                f"has {body.body_id!r} in that position; stale body identity or "
                "order is refused"
            )
        if binding.name != body.name:
            raise LinearBindingError(
                f"bound body {binding.body_id} was renamed "
                f"({binding.name!r} -> {body.name!r}); the stored binding is stale"
            )
        if binding.reference_position != body.reference_position:
            raise LinearBindingError(
                f"bound body {binding.body_id} reference position changed "
                f"({binding.reference_position} -> {body.reference_position}); "
                "the stored binding is stale"
            )
        if float(binding.mass) != float(body.mass):
            raise LinearBindingError(
                f"bound body {binding.body_id} mass changed "
                f"({binding.mass} -> {body.mass}); the stored binding is stale"
            )
        if binding.surface_hash != body_surface_hash(body):
            raise LinearBindingError('bound hydrodynamic surface geometry/semantics changed; coefficients are stale and never silently rebound')
        if binding.mesh_hash != mesh_hash(body.mesh):
            raise LinearBindingError(
                f"bound body {binding.body_id} mesh geometry changed "
                f"({binding.mesh_hash[:12]}... -> {mesh_hash(body.mesh)[:12]}...); "
                "the stored binding is stale and coefficients are never silently "
                "rebound to edited geometry"
            )
        # The numerical lid is bound exactly like the hull: a lid edit,
        # removal or replacement makes the stored inputs and every result they
        # produced stale; the lid is never silently rebound.
        if binding.hydro_lid_policy != body.hydro_lid_policy:
            raise LinearBindingError("bound auxiliary lid policy changed; coefficients are stale")
        current_lid_hash = mesh_hash(body.hydro_lid) if body.hydro_lid is not None else None
        if binding.lid_mesh_hash != current_lid_hash:
            if binding.lid_mesh_hash is None:
                raise LinearBindingError(
                    f"bound body {binding.body_id} gained a numerical lid "
                    f"({current_lid_hash[:12]}...); the stored binding predates "
                    "the lid and is stale"
                )
            if current_lid_hash is None:
                raise LinearBindingError(
                    f"bound body {binding.body_id} numerical lid was removed "
                    f"(bound {binding.lid_mesh_hash[:12]}...); the stored binding "
                    "is stale"
                )
            raise LinearBindingError(
                f"bound body {binding.body_id} numerical lid geometry changed "
                f"({binding.lid_mesh_hash[:12]}... -> {current_lid_hash[:12]}...); "
                "the stored binding is stale and coefficients are never silently "
                "rebound to edited lid geometry"
            )


def verify_linear_binding(project: ProjectModel, inputs: LinearDevelopmentInputs) -> None:
    """Fail closed unless *inputs* bind the project's complete equilibrium.

    Covers the body binding (identity, order, name, reference position,
    mass, mesh geometry) and the project equilibrium metadata (waterline
    etc): the linear route is linearised about that equilibrium, so an
    equilibrium edit makes stored coefficients stale exactly like a body
    edit, even for development user-supplied matrices.
    """

    verify_body_binding(project.bodies, inputs)
    current = equilibrium_hash(project.equilibrium)
    if inputs.equilibrium_hash != current:
        raise LinearBindingError(
            f"linear inputs were built about a different project equilibrium "
            f"({inputs.equilibrium_hash[:12]}... -> {current[:12]}...); the "
            "stored equilibrium binding is stale and coefficients are never "
            "silently rebound"
        )


def linear_result_stale(project: ProjectModel, result_input_hash: str) -> bool:
    """True when a stored linear result no longer matches the project.

    A linear result is stale when the project holds no linear inputs, when
    the stored inputs no longer bind the current bodies (identity, order,
    name, reference position, mass, mesh geometry) or the current project
    equilibrium (waterline etc), or when the stored inputs' hash differs
    from the hash recorded with the result.
    """

    inputs = project.linear_inputs
    if inputs is None:
        return True
    try:
        verify_linear_binding(project, inputs)
    except LinearBindingError:
        return True
    return linear_input_hash(inputs) != result_input_hash


def linear_result_source(result: ResultRecord) -> Optional[str]:
    """Provenance source recorded in a linear result's note, or None.

    ``run_linear`` records ``Source: <source>.`` in every linear result
    note. GUI labels use this so an explicitly supplied input path is never
    presented as the analytic demo. Returns None for non-linear records or
    notes without a recognised source; callers must label that case
    neutrally, never as the analytic demo.
    """

    if result.kind != RESULT_KIND_LINEAR:
        return None
    marker = "Source: "
    index = result.note.find(marker)
    if index < 0:
        return None
    source = result.note[index + len(marker):].split(".", 1)[0].strip()
    if source in LINEAR_SOURCES:
        return source
    return None


# --------------------------------------------------------------------- demo

_DEMO_DT = 0.05
_DEMO_N_STEPS = 201
_DEMO_HEAVE_PERIOD = 8.0
_DEMO_SURGE_PERIOD = 60.0
_DEMO_LINK_STIFFNESS_RATIO = 0.5
_DEMO_KERNEL_DECAY = 0.6
_DEMO_WAVE_PERIOD = 7.0
_DEMO_WAVE_FORCE_RATIO = 0.02
# Full-coupling demo terms: a symmetric positive-semidefinite rank-one outer
# product, scaled with the square roots of the bound body masses, is added to
# the diagonal added-mass and radiation-kernel coefficient matrices. Small
# positive coefficients across all DOFs preserve passivity and generate
# off-diagonal cross-body terms, so the existing heave forcing excites surge
# and the mechanical link. Demo-only analytic choice, not solver physics.
_DEMO_COUPLING_AINF = 0.02
_DEMO_COUPLING_KERNEL = 0.01


def analytic_demo_inputs(
    project: ProjectModel,
    dt: float = _DEMO_DT,
    n_steps: int = _DEMO_N_STEPS,
    execution_limits: Optional[LinearExecutionLimits] = None,
) -> LinearDevelopmentInputs:
    """Build the labelled analytic two-body surge/heave demo inputs.

    The matrices are deterministic closed-form functions of the two bound
    bodies' masses: diagonal dry mass, instantaneous added mass and damping,
    heave/mooring stiffness plus a linear mechanical link between the two
    surge DOFs (off-diagonal stiffness coupling), an exponential causal
    radiation kernel sampled on the same fixed grid, and harmonic heave
    forcing. A symmetric positive-semidefinite rank-one outer product
    scaled with the square roots of the body masses is added to the diagonal
    added-mass and radiation-kernel coefficient matrices, so the demo
    exercises full hydrodynamic coupling (nonzero off-diagonal and
    cross-body terms) and the link: the heave forcing excites surge motion
    through the coupled inertia and memory terms. This is an explicitly
    labelled analytic development demo, not boundary-element output and not
    a qualified marine analysis. The kernel is constructed from its analytic
    formula (input data, not integration); the time integration itself is
    only ever performed by the solver.

    *execution_limits* optionally admits a longer development job (an
    execution bound, never a qualification); None preserves the legacy
    default 1001-frame bound, serialization and hash.
    """

    bodies = tuple(project.bodies)
    if len(bodies) != 2:
        raise LinearBindingError(
            f"the linear analytic demo binds exactly 2 bodies; this project has "
            f"{len(bodies)}"
        )
    if any(b.hydro_surfaces for b in bodies):
        raise LinearBindingError('analytic hull-only coefficients cannot represent explicit hydrodynamic surfaces; import exact matching coupled coefficients')
    bindings = tuple(
        LinearBodyBinding(
            body_id=body.body_id,
            name=body.name,
            reference_position=body.reference_position,
            mass=float(body.mass),
            mesh_hash=mesh_hash(body.mesh),
            lid_mesh_hash=mesh_hash(body.hydro_lid) if body.hydro_lid is not None else None,
            hydro_lid_policy=body.hydro_lid_policy,
        )
        for body in bodies
    )
    mass_a = float(bodies[0].mass)
    mass_b = float(bodies[1].mass)

    omega_heave = 2.0 * np.pi / _DEMO_HEAVE_PERIOD
    omega_surge = 2.0 * np.pi / _DEMO_SURGE_PERIOD
    k_heave_a = mass_a * omega_heave**2
    k_heave_b = mass_b * omega_heave**2
    k_surge_a = mass_a * omega_surge**2
    k_surge_b = mass_b * omega_surge**2
    k_link = _DEMO_LINK_STIFFNESS_RATIO * min(mass_a, mass_b)

    mass_dry = (
        (mass_a, 0.0, 0.0, 0.0),
        (0.0, mass_a, 0.0, 0.0),
        (0.0, 0.0, mass_b, 0.0),
        (0.0, 0.0, 0.0, mass_b),
    )
    # Per-DOF coupling scale: square roots of the bound body masses. The
    # rank-one outer product scale_i*scale_j is symmetric positive
    # semidefinite and generates positive off-diagonal terms across every
    # DOF pair, including cross-body surge/heave coupling.
    scale = (
        float(np.sqrt(mass_a)),
        float(np.sqrt(mass_a)),
        float(np.sqrt(mass_b)),
        float(np.sqrt(mass_b)),
    )

    def _coupled(diagonal, epsilon):
        return tuple(
            tuple(
                float(diagonal[row][column] + epsilon * scale[row] * scale[column])
                for column in range(4)
            )
            for row in range(4)
        )

    added_mass_infinite = _coupled(
        (
            (0.10 * mass_a, 0.0, 0.0, 0.0),
            (0.0, 0.60 * mass_a, 0.0, 0.0),
            (0.0, 0.0, 0.10 * mass_b, 0.0),
            (0.0, 0.0, 0.0, 0.60 * mass_b),
        ),
        _DEMO_COUPLING_AINF,
    )
    damping = (
        (0.02 * mass_a, 0.0, 0.0, 0.0),
        (0.0, 0.25 * mass_a, 0.0, 0.0),
        (0.0, 0.0, 0.02 * mass_b, 0.0),
        (0.0, 0.0, 0.0, 0.25 * mass_b),
    )
    stiffness = (
        (k_surge_a + k_link, 0.0, -k_link, 0.0),
        (0.0, k_heave_a, 0.0, 0.0),
        (-k_link, 0.0, k_surge_b + k_link, 0.0),
        (0.0, 0.0, 0.0, k_heave_b),
    )

    kernel_coefficient = _coupled(
        (
            (0.0, 0.0, 0.0, 0.0),
            (0.0, 0.05 * mass_a, 0.0, 0.0),
            (0.0, 0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 0.05 * mass_b),
        ),
        _DEMO_COUPLING_KERNEL,
    )
    factors = tuple(
        float(np.exp(-_DEMO_KERNEL_DECAY * index * dt)) for index in range(n_steps)
    )
    radiation_kernel = tuple(
        tuple(tuple(float(value * factor) for value in row) for row in kernel_coefficient)
        for factor in factors
    )
    kernel_times = tuple(float(index * dt) for index in range(n_steps))

    omega_wave = 2.0 * np.pi / _DEMO_WAVE_PERIOD
    force_a = _DEMO_WAVE_FORCE_RATIO * mass_a * 9.81
    force_b = _DEMO_WAVE_FORCE_RATIO * mass_b * 9.81
    forces = []
    for index in range(n_steps):
        t = index * dt
        forces.append(
            (
                0.0,
                float(force_a * np.sin(omega_wave * t)),
                0.0,
                float(0.8 * force_b * np.sin(omega_wave * t + np.pi / 3.0)),
            )
        )

    return LinearDevelopmentInputs(
        bindings=bindings,
        surface_geometry=capture_surface_geometry(project),
        dt=float(dt),
        t0=0.0,
        n_steps=int(n_steps),
        forces=tuple(forces),
        mass_dry=mass_dry,
        added_mass_infinite=added_mass_infinite,
        damping=damping,
        stiffness=stiffness,
        radiation_kernel=radiation_kernel,
        kernel_times=kernel_times,
        equilibrium_hash=equilibrium_hash(project.equilibrium),
        q0=(0.0, 0.0, 0.0, 0.0),
        v0=(0.0, 0.0, 0.0, 0.0),
        execution_limits=execution_limits,
        provenance=LinearProvenance(
            source=LINEAR_SOURCE_ANALYTIC_DEMO,
            description=(
                "Analytic two-body surge/heave development demo with full "
                "hydrodynamic coupling (symmetric positive-semidefinite rank-one "
                "added-mass and radiation-kernel terms scaled with square roots "
                "of the bound body masses) and a linear mechanical link; "
                "deterministic matrices derived from the bound body masses. "
                "Clearly labelled demo input, not BEM output."
            ),
            created_utc=_datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        ),
    )


# ------------------------------------------------------------------- solver

LINEAR_RESULT_CHANNEL_PREFIXES = (
    "q:",
    "v:",
    "a:",
    "force_external:",
    "force_dry_inertia:",
    "force_added_inertia:",
    "force_memory:",
    "force_damping:",
    "force_stiffness:",
    "force_residual:",
)


def verify_linear_result(result: ResultRecord, inputs: LinearDevelopmentInputs) -> None:
    """Fail closed unless *result* completely represents the run of *inputs*.

    Generic ``ResultRecord`` structural validation proves shape, not
    completeness for a particular run. This check binds the record to the
    immutable inputs: exact result kind, exact ordered body IDs, the exact
    time grid, every required q/v/a/force channel for each of the four DOF
    labels, and replay positions/rotations that agree with the stored
    channels about the bound reference positions (surge to x, heave to z,
    zero rotation). A one-frame, wrong-body or synthetic record carrying the
    right input hash is refused.
    """

    if result.to_dict().get('surface_snapshot') != surface_result_snapshot(inputs):
        raise LinearResultIncomplete('linear result producing surface geometry is missing or different')
    if result.kind != RESULT_KIND_LINEAR:
        raise LinearResultIncomplete(
            f"result kind {result.kind!r} is not {RESULT_KIND_LINEAR!r}; a "
            "record of another kind never becomes a completed linear job"
        )
    expected_body_ids = tuple(binding.body_id for binding in inputs.bindings)
    if result.body_ids != expected_body_ids:
        raise LinearResultIncomplete(
            f"result body order {result.body_ids} does not match the bound "
            f"body order {expected_body_ids}; a wrong-body record is refused"
        )
    if result.times != inputs.times:
        raise LinearResultIncomplete(
            f"result time grid ({len(result.times)} samples) does not exactly "
            f"match the input grid ({inputs.n_steps} samples); a truncated or "
            "re-gridded record is refused"
        )
    for label in inputs.labels:
        for prefix in LINEAR_RESULT_CHANNEL_PREFIXES:
            name = f"{prefix}{label}"
            if name not in result.channels:
                raise LinearResultIncomplete(
                    f"result is missing the required channel {name!r}"
                )
    for body_index, binding in enumerate(inputs.bindings):
        surge = result.channels[f"q:{inputs.labels[2 * body_index]}"]
        heave = result.channels[f"q:{inputs.labels[2 * body_index + 1]}"]
        reference = binding.reference_position
        rows = result.positions[binding.body_id]
        for frame, row in enumerate(rows):
            expected = (
                reference[0] + surge[frame],
                reference[1],
                reference[2] + heave[frame],
            )
            if row != expected:
                raise LinearResultIncomplete(
                    f"replay position for body {binding.body_id} frame {frame} "
                    f"{row} disagrees with the stored q channels about the "
                    f"bound reference position ({expected})"
                )
        if any(row != (0.0, 0.0, 0.0) for row in result.orientations[binding.body_id]):
            raise LinearResultIncomplete(
                f"replay orientations for body {binding.body_id} are not zero; "
                "the linear development route has no rotation"
            )


def _require_finite(name: str, array: np.ndarray) -> None:
    if not np.all(np.isfinite(array)):
        raise LinearSolveFailed(
            f"solver returned non-finite {name} values; no non-finite output is "
            "admitted as a linear development result"
        )


def run_linear(
    inputs: LinearDevelopmentInputs,
    cancel=None,
    result_id: Optional[str] = None,
    created_utc: Optional[str] = None,
) -> ResultRecord:
    """Run the linear route through the public solver API and store the result.

    Calls ``anysolver.marine_linear.solve_marine_linear`` with the frozen
    inputs; the solver owns the equation, signs, direct convolution and zero
    prehistory. Only a complete, non-cancelled result with finite q/v/a and
    force decomposition becomes a ``linear_development`` ResultRecord: q, v,
    a and every force decomposition channel are stored as authoritative
    channels, surge maps to x and heave to z at the bound bodies' fixed
    equilibrium reference positions and rotation is zero.
    """

    marine_linear = _import_solver()
    spec = marine_linear.MarineLinearSpec(
        labels=inputs.labels,
        dt=inputs.dt,
        times=np.asarray(inputs.times, dtype=float),
        forces=np.asarray(inputs.forces, dtype=float),
        mass_dry=np.asarray(inputs.mass_dry, dtype=float),
        added_mass_infinite=np.asarray(inputs.added_mass_infinite, dtype=float),
        damping=np.asarray(inputs.damping, dtype=float),
        stiffness=np.asarray(inputs.stiffness, dtype=float),
        radiation_kernel=np.asarray(inputs.radiation_kernel, dtype=float),
        kernel_times=np.asarray(inputs.kernel_times, dtype=float),
        initial_displacement=np.asarray(inputs.q0, dtype=float),
        initial_velocity=np.asarray(inputs.v0, dtype=float),
    )
    try:
        solved = marine_linear.solve_marine_linear(spec, cancel=cancel)
    except marine_linear.MarineLinearInputError as error:
        raise LinearInputRejected(str(error)) from error
    except marine_linear.MarineLinearSolveError as error:
        raise LinearSolveFailed(str(error)) from error
    except marine_linear.MarineLinearError as error:
        raise LinearSolveFailed(f"unexpected solver failure: {error}") from error

    if solved.cancelled or not solved.complete:
        raise LinearCancelledError(
            "the linear run was cancelled or did not complete; incomplete output "
            "never becomes a linear_development result"
        )
    for name in ("displacements", "velocities", "accelerations"):
        _require_finite(name, getattr(solved, name))
    for name in (
        "external_forces",
        "dry_inertia_forces",
        "added_inertia_forces",
        "memory_forces",
        "damping_forces",
        "stiffness_forces",
        "residual_forces",
    ):
        _require_finite(name, getattr(solved, name))

    times = tuple(float(value) for value in solved.times)
    q = solved.displacements
    labels = solved.labels

    def channel(array) -> tuple[float, ...]:
        return tuple(float(array[index]) for index in range(array.shape[0]))

    channels: dict[str, tuple[float, ...]] = {}
    for dof in range(4):
        label = labels[dof]
        channels[f"q:{label}"] = channel(q[:, dof])
        channels[f"v:{label}"] = channel(solved.velocities[:, dof])
        channels[f"a:{label}"] = channel(solved.accelerations[:, dof])
        channels[f"force_external:{label}"] = channel(solved.external_forces[:, dof])
        channels[f"force_dry_inertia:{label}"] = channel(solved.dry_inertia_forces[:, dof])
        channels[f"force_added_inertia:{label}"] = channel(
            solved.added_inertia_forces[:, dof]
        )
        channels[f"force_memory:{label}"] = channel(solved.memory_forces[:, dof])
        channels[f"force_damping:{label}"] = channel(solved.damping_forces[:, dof])
        channels[f"force_stiffness:{label}"] = channel(solved.stiffness_forces[:, dof])
        channels[f"force_residual:{label}"] = channel(solved.residual_forces[:, dof])

    positions = {}
    orientations = {}
    for body_index, binding in enumerate(inputs.bindings):
        surge_column = 2 * body_index
        heave_column = 2 * body_index + 1
        reference = binding.reference_position
        positions[binding.body_id] = tuple(
            (
                reference[0] + float(q[frame, surge_column]),
                reference[1],
                reference[2] + float(q[frame, heave_column]),
            )
            for frame in range(q.shape[0])
        )
        orientations[binding.body_id] = tuple(
            (0.0, 0.0, 0.0) for _frame in range(q.shape[0])
        )

    # Source-aware note: an imported ANYhydro dataset is a hydrodynamic
    # dataset source and must not be labelled "not boundary-element-method
    # output"; every source is still development, never qualification.
    if inputs.provenance.source == LINEAR_SOURCE_HYDRO:
        details = inputs.provenance.details
        route = details.get("route") if isinstance(details, Mapping) else None
        if route == HYDRO_ROUTE_DERIVED:
            base_note = LINEAR_RESULT_NOTE_HYDRO_DERIVED
        else:
            base_note = LINEAR_RESULT_NOTE_HYDRO
    else:
        base_note = LINEAR_RESULT_NOTE
    note = (
        f"{base_note} Source: {inputs.provenance.source}. "
        f"{inputs.provenance.description}"
    )
    record = ResultRecord(
        result_id=result_id or str(uuid.uuid4()),
        kind=RESULT_KIND_LINEAR,
        surface_snapshot=surface_result_snapshot(inputs),
        input_hash=linear_input_hash(inputs),
        body_ids=tuple(binding.body_id for binding in inputs.bindings),
        times=times,
        positions=positions,
        orientations=orientations,
        channels=channels,
        note=note,
        created_utc=created_utc
        or _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    )
    # Self-check: the record must completely represent this run before it is
    # handed to any caller (worker, CLI, GUI).
    verify_linear_result(record, inputs)
    return record
