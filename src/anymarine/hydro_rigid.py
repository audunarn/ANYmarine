"""Six-DOF (rigid_subset_v1) import of an ANYhydro dataset + radiation kernel (ADR-015 slice L6-3, M-a/M-b).

Builds ``LinearDevelopmentInputs`` with the rigid_subset_v1 layout from a bound ANYhydro source, entirely through
declared contracts (ADR-015 sections 2-5, 8; ADR-004). Every array is addressed by DOF IDENTITY (``body::dof``
labels), never by position:

- geometry/identity proof: project body order, world meshes at the reference pose (incl. orientation), rotation
  centres, no numerical lid on either side (not admitted yet), GLOBAL coefficient axes;
- radiation: the kernel must be bound to the dataset (content hash, DOF identity and order, A_inf - exact for the
  computed policy, verified for the band-consistent policy), passive by construction (piecewise_linear_exact,
  linear-to-zero or explicit PSD B(0) low tail, truncated high tail, Bartlett taper, zero producer passivity and
  symmetry violations), cover the run at the run dt and pass the NORMALISED full-block gate on the raw source;
- restoring: buoyancy from the bound hydrostatic contract through its implemented chart conversion, gravity by the
  ADR-004 origin formula with ONE declared g that must equal the source's (contract provenance), plus declared
  external stiffness records; a per-body equilibrium check (force <= 1e-3 W, moment <= 1e-3 W L);
- dry mass: the solver-owned RigidBody6D assembly;
- declared symmetrisation PER OPERATOR (buoyancy chart, each external record, A_inf, each kernel sample; skew
  <= 1e-2 in normalised coordinates, recorded) and the structural admission (M_eff PD, K_total PD, every external
  damping record PSD, every damping sample incl. B(0) PSD) before any inputs exist;
- forcing: an optional regular wave at a dataset grid frequency INSIDE the admitted reconstruction window and a
  dataset heading.

Nothing is silently defaulted; every refusal is a typed ``HydroImportError``. Development evidence only.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

import numpy as np

from . import linear_fd as fd
from .domain import (LINEAR_LAYOUT_RIGID, LINEAR_SOURCE_HYDRO, RIGID_DOFS, ExternalMatrixRecord,
                     LinearDevelopmentInputs, LinearExecutionLimits, LinearProvenance, ProjectModel)
from .hashing import equilibrium_hash
from .hydro import (HydroAdmissionError, HydroGeometryError, HydroImportError, HydroOptionsError, _import_anyhydro,
                    _read_raw_source, _require_global_source_axes)
from .linear import rigid_binding, rigid_dry_mass

RIGID_IMPORT_FORMAT = "anymarine-rigid-hydro-import-v1"
EQUILIBRIUM_RELATIVE = 1.0e-3
_TRANSLATIONS = ("surge", "sway", "heave")
_OPTION_KEYS = {"dofs", "length_scale", "criteria", "n_steps", "gravity", "wave", "external_matrices",
                "execution_limits", "q0", "v0"}
_CRITERIA_KEYS = {"omega_min", "omega_max", "block_rel_tol", "modal_rel_tol", "energetic_fraction", "cluster_fraction"}
_RECORD_KEYS = set(ExternalMatrixRecord._KEYS)


def _skew(v):
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])


def _positive(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError) as error:
        raise HydroOptionsError(f"{name} must be a number") from error
    if isinstance(value, bool) or not (math.isfinite(out) and out > 0):
        raise HydroOptionsError(f"{name} must be a positive finite number")
    return out


def _finite(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError) as error:
        raise HydroOptionsError(f"{name} must be a number") from error
    if isinstance(value, bool) or not math.isfinite(out):
        raise HydroOptionsError(f"{name} must be a finite number")
    return out


def _source_gravity_declarations(dataset, contract) -> dict:
    """Every explicit gravity the source declares: the hydrostatic contract provenance (required), and the wave
    environment and scalar ``g`` of a schema 1.3 dataset when present. All must equal the run's one declared g."""
    found = {"hydrostatic contract provenance": contract.provenance.get("gravity")}
    environment = _json_attr(dataset, "wave_environment")
    if environment is not None:
        found["wave_environment attribute"] = environment.get("g") if isinstance(environment, dict) else None
    if "g" in dataset.data_vars:
        found["dataset scalar g"] = dataset["g"].values.item() if dataset["g"].size == 1 else None
    return found


def _parse(project: ProjectModel, options) -> dict:
    if not isinstance(options, Mapping):
        raise HydroOptionsError("rigid import options must be a JSON object")
    unknown = set(options) - _OPTION_KEYS
    if unknown:
        raise HydroOptionsError(f"unknown rigid import option(s) {sorted(unknown)}")
    for key in ("dofs", "length_scale", "criteria", "n_steps", "gravity"):
        if key not in options:
            raise HydroOptionsError(f"rigid import option {key!r} is required (no defaults)")
    dofs = options["dofs"]
    ids = [body.body_id for body in project.bodies]
    if not isinstance(dofs, Mapping) or list(dofs) != ids or not all(
            isinstance(v, (list, tuple)) for v in dofs.values()):
        raise HydroOptionsError(f"'dofs' must map every project body in order {ids} to a list of retained DOFs")
    criteria = options["criteria"]
    if not isinstance(criteria, Mapping) or set(criteria) != _CRITERIA_KEYS:
        raise HydroOptionsError(f"criteria must contain exactly {sorted(_CRITERIA_KEYS)}")
    n_steps = options["n_steps"]
    if isinstance(n_steps, bool) or not isinstance(n_steps, int) or n_steps < 2:
        raise HydroOptionsError("n_steps must be an integer >= 2")
    records = options.get("external_matrices", ())
    if not isinstance(records, (list, tuple)) or not all(isinstance(r, Mapping) for r in records):
        raise HydroOptionsError("external_matrices must be a list of record objects")
    for record in records:
        extra = set(record) - _RECORD_KEYS
        if extra or not {"kind", "dofs", "matrix", "source"} <= set(record):
            raise HydroOptionsError(f"external record keys must be within {sorted(_RECORD_KEYS)} and include kind, "
                                    "dofs, matrix, source")
    wave = options.get("wave")
    if wave is not None and (not isinstance(wave, Mapping) or not {"omega", "heading", "amplitude"} <= set(wave)
                             or set(wave) - {"omega", "heading", "amplitude", "phase"}):
        raise HydroOptionsError("wave must contain omega, heading, amplitude and optionally phase")
    return dict(options, length_scale=_positive(options["length_scale"], "length_scale"),
                gravity=_positive(options["gravity"], "gravity"), external_matrices=tuple(records))


def _world_mesh(anyhydro, body):
    """Expected world-space triangle mesh at the reference pose: R0 x_body + r0 (incl. reference orientation)."""
    from .surface_assignments import body_surface_hash
    if body_surface_hash(body) is not None:
        raise HydroGeometryError(f"body {body.name!r} carries hydrodynamic sheet surfaces; not supported by the rigid "
                                 "import yet")
    R0 = np.asarray(body.reference_orientation, dtype=np.float64)
    vertices = np.asarray(body.mesh.positions, dtype=np.float64) @ R0.T + np.asarray(body.reference_position)
    triangles = np.asarray(body.mesh.triangles, dtype=np.int32)
    return anyhydro.HydroPanelMesh(vertices=vertices, faces=triangles,
                                   face_sizes=np.full(triangles.shape[0], 3, dtype=np.uint8), body_id=body.body_id,
                                   outward_normals_verified=False,
                                   source_geometry_refs=("anymarine-body-mesh+reference-pose",), mesh_hash="")


def _json_attr(dataset, name):
    value = dataset.attrs.get(name)
    return json.loads(value) if isinstance(value, str) else (dict(value) if value is not None else None)


def _prove_geometry(anyhydro, project, dataset):
    ids = [body.body_id for body in project.bodies]
    if [str(b) for b in dataset["body"].values.tolist()] != ids:
        raise HydroGeometryError("dataset body order must equal the project body order exactly")
    identity = _json_attr(dataset, "body_identity") or {}
    for body in project.bodies:
        entry = identity.get(body.body_id)
        if not isinstance(entry, Mapping):
            raise HydroGeometryError(f"dataset body identity of {body.body_id!r} is missing")
        if [float(v) for v in entry["rotation_center"]] != [float(v) for v in body.reference_position]:
            raise HydroGeometryError(f"dataset rotation centre of {body.body_id!r} is not the body reference point")
        if entry["mesh_hash"] != _world_mesh(anyhydro, body).mesh_hash:
            raise HydroGeometryError(f"dataset mesh of {body.body_id!r} is not the project's world-space hull at the "
                                     "reference pose")
        if body.hydro_lid is not None or entry.get("lid_mesh_hash") is not None:
            raise HydroGeometryError("numerical lids are not admitted by the rigid import yet (neither on the project "
                                     "nor on the dataset side)")
    _require_global_source_axes(anyhydro, dataset)


def _require_kernel_contract(anyhydro, kernel, dataset, keys, n_steps):
    from anyhydro.radiation import (A_INF_BAND_CONSISTENT, HIGH_TAIL_TRUNCATE, LOW_TAIL_EXPLICIT_ZERO,
                                    LOW_TAIL_LINEAR_TO_ZERO, QUADRATURE_PIECEWISE_LINEAR, TIME_WINDOW_BARTLETT,
                                    verify_band_consistent_added_mass_infinite)
    settings = kernel.settings
    if kernel.provenance.get("source_dataset_hash") != anyhydro.dataset_content_hash(dataset):
        raise HydroAdmissionError("the kernel is not bound to this dataset (source content hash mismatch)")
    if [str(k) for k in kernel.dof_keys] != keys:
        raise HydroAdmissionError("the kernel DOF identities/order differ from the dataset's")
    if [str(b) for b in kernel.body_ids] != [str(b) for b in dataset["body"].values]:
        raise HydroAdmissionError("the kernel body identities/order differ from the dataset's")
    a_inf_source = np.asarray(dataset["added_mass_infinite"].values, dtype=np.float64)
    if settings.a_inf_policy == A_INF_BAND_CONSISTENT:
        verify_band_consistent_added_mass_infinite(kernel, a_inf_source, np.asarray(dataset["omega"].values),
                                                   np.asarray(dataset["added_mass"].values))
    elif not np.array_equal(np.asarray(kernel.added_mass_infinite, dtype=np.float64), a_inf_source):
        raise HydroAdmissionError("the kernel A_inf is not the dataset's (computed A_inf policy)")
    reasons = []
    if settings.quadrature_method != QUADRATURE_PIECEWISE_LINEAR:
        reasons.append("quadrature must be piecewise_linear_exact")
    if settings.low_tail_policy not in (LOW_TAIL_LINEAR_TO_ZERO, LOW_TAIL_EXPLICIT_ZERO):
        reasons.append("low tail must be linear_to_zero_extension or explicit_zero_sample")
    if settings.high_tail_policy != HIGH_TAIL_TRUNCATE:
        reasons.append("high tail must be truncate")
    if settings.time_window is None or getattr(settings, "time_window_shape", None) != TIME_WINDOW_BARTLETT:
        reasons.append("the memory cutoff must be a Bartlett taper (time_window_shape 'bartlett')")
    diagnostics = kernel.diagnostics
    if diagnostics.passivity_violations or diagnostics.damping_symmetry_violations:
        reasons.append(f"the producer reports {diagnostics.passivity_violations} passivity and "
                       f"{diagnostics.damping_symmetry_violations} damping-symmetry violations")
    if reasons:
        raise HydroAdmissionError("the kernel is not passive by construction (ADR-015 section 8 (iv)): "
                                  + "; ".join(reasons))
    if np.asarray(kernel.time).size < n_steps:
        raise HydroAdmissionError(f"the kernel ({np.asarray(kernel.time).size} samples) must cover every lag of the "
                                  f"{n_steps}-step run at the run dt")
    zero = None
    if settings.low_tail_policy == LOW_TAIL_EXPLICIT_ZERO:
        zero = np.asarray(settings.zero_frequency_damping, dtype=np.float64)
    return zero


def _symmetrise(name, matrix, scale):
    try:
        return fd.symmetrise(name, matrix, scale)
    except fd.LinearFdError as error:
        raise HydroAdmissionError(f"declared symmetrisation refused (ADR-015 section 8): {error}") from error


def import_rigid_hydro_inputs(project: ProjectModel, dataset_path, kernel_path, options):
    """Admit an ANYhydro dataset + kernel as rigid_subset_v1 inputs. Returns (inputs, details)."""
    anyhydro = _import_anyhydro()
    from anyhydro.hydrostatic_contract import chart_stiffness, read_hydrostatic_contract, \
        require_hydrostatic_composition
    opt = _parse(project, options)
    if float(project.equilibrium.waterline_z) != 0.0:
        raise HydroGeometryError("the rigid import requires project waterline_z == 0")
    dataset_payload = _read_raw_source(Path(dataset_path), "dataset")
    kernel_payload = _read_raw_source(Path(kernel_path), "kernel")
    dataset = anyhydro.load_hydro_dataset(dataset_path)
    kernel = anyhydro.load_radiation_kernel(kernel_path)
    _prove_geometry(anyhydro, project, dataset)

    bodies = tuple(project.bodies)
    try:
        bindings = tuple(rigid_binding(body, tuple(opt["dofs"][body.body_id])) for body in bodies)
    except (ValueError, TypeError) as error:
        raise HydroOptionsError(f"invalid retained DOFs: {error}") from error
    keys = [str(k) for k in dataset["influenced_dof"].values]
    if [str(k) for k in dataset["radiating_dof"].values] != keys:
        raise HydroAdmissionError("dataset influenced and radiating DOF identities differ")
    labels = [f"{b.body_id}:{d}" for b in bindings for d in b.dofs]
    try:
        index = [keys.index(f"{b.body_id}::{d}") for b in bindings for d in b.dofs]
    except ValueError as error:
        raise HydroAdmissionError(f"a retained DOF is not in the dataset: {error}") from error
    sel = np.ix_(index, index)
    S = np.diag([1.0 if label.split(":")[1] in _TRANSLATIONS else 1.0 / opt["length_scale"] for label in labels])

    dt = float(kernel.settings.dt)
    n_steps = int(opt["n_steps"])
    zero_damping = _require_kernel_contract(anyhydro, kernel, dataset, keys, n_steps)

    # ---------------------------------------------------------------- normalised full-block gate (raw source)
    scales_full = tuple(1.0 if k.split("::")[1] in _TRANSLATIONS else 1.0 / opt["length_scale"] for k in keys)
    try:
        criteria = anyhydro.FullBlockReconstructionCriteria(dof_scales=scales_full, **opt["criteria"])
    except anyhydro.HydroError as error:
        raise HydroOptionsError(f"invalid criteria: {error}") from error
    try:
        report = anyhydro.compare_reconstruction(kernel, dataset, criteria)
    except anyhydro.HydroError as error:
        raise HydroAdmissionError(f"the normalised full-block reconstruction gate could not be evaluated: "
                                  f"{error}") from error
    summary = report.summary()
    if not summary["overall_pass"]:
        raise HydroAdmissionError(f"the normalised full-block reconstruction gate failed: "
                                  f"max impedance error {summary['max_impedance_rel_error']:.4g}")

    # ---------------------------------------------------------------- restoring (ADR-004 / ADR-015 section 3)
    require_hydrostatic_composition(dataset)
    contract = read_hydrostatic_contract(dataset)
    if contract.role != "BUOYANCY_ONLY":
        raise HydroAdmissionError("the rigid import composes BUOYANCY_ONLY sources with separately owned gravity only")
    g = opt["gravity"]
    for where, source_g in _source_gravity_declarations(dataset, contract).items():
        if isinstance(source_g, bool) or not isinstance(source_g, (int, float)) or float(source_g) != g:
            raise HydroAdmissionError(f"one declared g: the run's gravity {g!r} must equal the source's gravity "
                                      f"{source_g!r} ({where})")
    K_source_full = np.asarray(dataset["hydrostatic_stiffness"].values, dtype=np.float64)
    n_full = len(keys)
    K_buoy = np.zeros((n_full, n_full))
    K_grav = np.zeros((n_full, n_full))
    residuals = {}
    for body in bodies:
        canonical = [keys.index(f"{body.body_id}::{d}") for d in RIGID_DOFS]
        block = np.ix_(canonical, canonical)
        K_buoy[block] = chart_stiffness(contract, body.body_id, K_source_full[block])
        W = float(body.mass) * g
        R0 = np.asarray(body.reference_orientation, dtype=float)
        c = R0 @ np.asarray(body.mass_properties.cg_body, dtype=float)
        tau = -W * np.cross(c, [0.0, 0.0, 1.0])
        gravity_block = np.zeros((6, 6))
        gravity_block[3:, 3:] = -W * (c[2] * np.eye(3) - np.outer(c, [0.0, 0.0, 1.0])) - 0.5 * _skew(tau)
        K_grav[block] = gravity_block
        baseline = np.asarray(contract.baseline_wrench[body.body_id], dtype=float)
        force = baseline[:3] + np.array([0.0, 0.0, -W])
        moment = baseline[3:] + tau
        length = opt["length_scale"]
        residuals[body.body_id] = {"force": float(np.linalg.norm(force)), "moment": float(np.linalg.norm(moment)),
                                   "weight": W, "length": length}
        if np.linalg.norm(force) > EQUILIBRIUM_RELATIVE * W or np.linalg.norm(moment) > EQUILIBRIUM_RELATIVE * W * length:
            raise HydroAdmissionError(
                f"body {body.body_id!r} is not in equilibrium: |F| {np.linalg.norm(force):.6g} N, |M| "
                f"{np.linalg.norm(moment):.6g} N m (limits {EQUILIBRIUM_RELATIVE:g} W, {EQUILIBRIUM_RELATIVE:g} W L)")

    # ---------------------------------------------------------------- per-operator symmetrisation + admission
    skew = {}
    K_buoy_s = _symmetrise("K_buoyancy_chart", K_buoy[sel], S)
    K_grav_sel = K_grav[sel]
    if not np.allclose(K_grav_sel, K_grav_sel.T, rtol=0, atol=1e-12 * max(1.0, float(np.abs(K_grav_sel).max()))):
        raise HydroAdmissionError("the gravity chart stiffness is not symmetric (internal invariant)")
    skew["K_buoyancy_chart"] = K_buoy_s.skew_ratio
    records = []
    C_ext = np.zeros((len(labels), len(labels)))
    K_ext = np.zeros((len(labels), len(labels)))
    for n, raw in enumerate(opt["external_matrices"]):
        try:
            record = ExternalMatrixRecord(**{**{k: raw[k] for k in raw if k not in ("dofs", "matrix")},
                                             "dofs": tuple(raw["dofs"]),
                                             "matrix": tuple(tuple(float(x) for x in row) for row in raw["matrix"])})
        except (ValueError, TypeError) as error:
            raise HydroOptionsError(f"external record {n} is not admissible: {error}") from error
        where = [labels.index(d) if d in labels else None for d in record.dofs]
        if None in where:
            raise HydroOptionsError(f"external record {n} dofs {record.dofs} are not all retained")
        sub = np.ix_(where, where)
        part = _symmetrise(f"external {record.kind} record {n}", np.asarray(record.matrix), S[sub])
        skew[f"external_{n}"] = part.skew_ratio
        if record.kind == "damping":
            try:
                fd.structural_admission(np.eye(len(where)), np.zeros((len(where),) * 2), np.eye(len(where)),
                                        part.symmetric, [], S[sub])
            except fd.LinearFdError as error:
                raise HydroAdmissionError(f"external damping record {n} must be PSD on its own: {error}") from error
            C_ext[sub] += part.symmetric
        else:
            K_ext[sub] += part.symmetric
        records.append(record)
    A_s = _symmetrise("A_inf", np.asarray(kernel.added_mass_infinite, dtype=np.float64)[sel], S)
    skew["A_inf"] = A_s.skew_ratio
    K_total = K_buoy_s.symmetric + K_grav_sel + K_ext
    stored = np.asarray(kernel.kernel, dtype=np.float64)[:n_steps]
    kernel_s = np.empty((n_steps, len(labels), len(labels)))
    worst = 0.0
    for l in range(n_steps):
        sample = stored[l][sel]
        if np.any(sample):
            sym = _symmetrise(f"kernel sample {l}", sample, S)
            kernel_s[l] = sym.symmetric
            worst = max(worst, sym.skew_ratio)
        else:
            kernel_s[l] = 0.0
    skew["kernel_max"] = worst
    B = np.asarray(dataset["radiation_damping"].values, dtype=np.float64)[:, index][:, :, index]
    if zero_damping is not None:
        B = np.concatenate([zero_damping[sel][None], B])
    B = 0.5 * (B + np.transpose(B, (0, 2, 1)))
    M_dry = rigid_dry_mass(bindings)
    try:
        fd.structural_admission(M_dry, A_s.symmetric, K_total, C_ext, B, S)
    except fd.LinearFdError as error:
        raise HydroAdmissionError(f"structural admission failed (ADR-015 section 8): {error}") from error

    # ---------------------------------------------------------------- forcing
    forces = np.zeros((n_steps, len(labels)))
    wave_selection = None
    if opt.get("wave") is not None:
        wave = opt["wave"]
        omega = _positive(wave["omega"], "wave omega")
        if not (criteria.omega_min <= omega <= criteria.omega_max):
            raise HydroOptionsError("the wave frequency must lie inside the admitted reconstruction window "
                                    f"[{criteria.omega_min}, {criteria.omega_max}]")
        omegas = np.asarray(dataset["omega"].values, dtype=np.float64)
        headings = np.asarray(dataset["wave_direction"].values, dtype=np.float64)
        o = np.flatnonzero(omegas == omega)
        heading = _finite(wave["heading"], "wave heading")
        amplitude = _finite(wave["amplitude"], "wave amplitude")
        h = np.flatnonzero(headings == heading)
        if o.size != 1 or h.size != 1:
            raise HydroOptionsError("the wave omega and heading must be exact dataset grid values (no interpolation)")
        F = np.asarray(dataset["excitation_force"].values[o[0], h[0], :], dtype=np.complex128)[index]
        times = dt * np.arange(n_steps)
        phase = _finite(wave.get("phase", 0.0), "wave phase")
        phasor = np.exp(-1j * omega * times + 1j * phase)
        forces = amplitude * (F[None, :] * phasor[:, None]).real
        wave_selection = dict(omega=omega, heading=heading, amplitude=amplitude,
                              phase=phase)

    details = {
        "format": RIGID_IMPORT_FORMAT, "route": "raw_dataset",
        "dataset_raw_sha256": hashlib.sha256(dataset_payload).hexdigest(),
        "kernel_raw_sha256": hashlib.sha256(kernel_payload).hexdigest(),
        "dataset_content_hash": anyhydro.dataset_content_hash(dataset), "kernel_content_hash": kernel.content_hash(),
        "labels": labels, "length_scale": opt["length_scale"], "criteria": criteria.to_dict(),
        "max_impedance_rel_error": float(summary["max_impedance_rel_error"]),
        "skew_ratios": skew, "equilibrium_residuals": residuals,
        "hydrostatic_contract": contract.to_json(), "gravity": g, "kernel_settings": kernel.settings.to_dict(),
        "wave": wave_selection,
    }
    limits = opt.get("execution_limits")
    try:
        inputs = LinearDevelopmentInputs(
            bindings=bindings, dt=dt, n_steps=n_steps, forces=forces.tolist(), mass_dry=M_dry.tolist(),
            added_mass_infinite=A_s.symmetric.tolist(), damping=C_ext.tolist(), stiffness=K_total.tolist(),
            radiation_kernel=kernel_s.tolist(), kernel_times=(dt * np.arange(n_steps)).tolist(),
            equilibrium_hash=equilibrium_hash(project.equilibrium),
            provenance=LinearProvenance(source=LINEAR_SOURCE_HYDRO, description=(
                "six-DOF rigid_subset_v1 inputs from an ANYhydro dataset and Bartlett-tapered kernel through the bound "
                "hydrostatic contract (chart conversion), ADR-004 gravity, declared external records and declared "
                "per-operator symmetrisation; development, unqualified"), details=details),
            q0=opt.get("q0"), v0=opt.get("v0"), external_matrices=tuple(records),
            execution_limits=LinearExecutionLimits.from_dict(limits) if limits is not None else
            LinearExecutionLimits(max_frames=max(n_steps, 1001), max_radiation_samples=max(n_steps, 2)),
            layout=LINEAR_LAYOUT_RIGID)
    except (ValueError, TypeError) as error:
        raise HydroAdmissionError(f"the assembled inputs are not admissible: {error}") from error
    return inputs, details
