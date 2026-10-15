"""Six-DOF (rigid_subset_v1) import of an ANYhydro dataset + radiation kernel (ADR-015 slice L6-3, M-a/M-b).

Builds ``LinearDevelopmentInputs`` with the rigid_subset_v1 layout from a bound ANYhydro source, entirely through
declared contracts (ADR-015 sections 2-5, 8; ADR-004):

- geometry/identity proof: project body order, world meshes (reference pose incl. orientation), rotation centres;
- radiation: the kernel must be bound to the dataset, built with piecewise_linear_exact quadrature, a
  linear-to-zero or explicit low tail, a truncated high tail and a Bartlett taper (passive by construction), cover
  the run at the run dt, and pass the NORMALISED full-block reconstruction gate on the raw source;
- restoring: buoyancy from the bound hydrostatic contract through its implemented chart conversion
  (``anyhydro.hydrostatic_contract.chart_stiffness``), gravity by the ADR-004 origin formula with the run's g, plus
  declared external stiffness records (e.g. the owner's weak mooring for neutral modes); a per-body equilibrium
  check (force <= 1e-3 W, moment <= 1e-3 W L);
- dry mass: the solver-owned RigidBody6D assembly (rigid_dry_mass);
- declared symmetrisation of every source operator (skew ratio <= 1e-2 in normalised coordinates, recorded) and the
  structural admission (M_eff PD, K_total PD, C PSD, every damping sample PSD) before any inputs exist;
- forcing: an optional regular wave from the dataset excitation at a grid frequency and the dataset heading.

Nothing is silently defaulted; every refusal is typed. Development evidence only, never qualification.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Mapping

import numpy as np

from . import linear_fd as fd
from .domain import (LINEAR_LAYOUT_RIGID, LINEAR_SOURCE_HYDRO, RIGID_DOFS, ExternalMatrixRecord, LinearDevelopmentInputs,
                     LinearExecutionLimits, LinearProvenance, ProjectModel)
from .hashing import equilibrium_hash
from .hydro import HydroAdmissionError, HydroGeometryError, HydroImportError, HydroOptionsError, _import_anyhydro, \
    _read_raw_source
from .linear import rigid_binding, rigid_dry_mass

RIGID_IMPORT_FORMAT = "anymarine-rigid-hydro-import-v1"
EQUILIBRIUM_RELATIVE = 1.0e-3
_TRANSLATIONS = ("surge", "sway", "heave")
_OPTION_KEYS = {"dofs", "length_scale", "criteria", "n_steps", "gravity", "wave", "external_matrices",
                "execution_limits", "q0", "v0"}
_CRITERIA_KEYS = {"omega_min", "omega_max", "block_rel_tol", "modal_rel_tol", "energetic_fraction", "cluster_fraction"}


def _skew(v):
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])


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
    if not isinstance(dofs, Mapping) or list(dofs) != ids:
        raise HydroOptionsError(f"'dofs' must map every project body in order {ids} to its retained DOFs")
    length = float(options["length_scale"])
    if not (math.isfinite(length) and length > 0):
        raise HydroOptionsError("length_scale must be positive")
    criteria = options["criteria"]
    if not isinstance(criteria, Mapping) or set(criteria) != _CRITERIA_KEYS:
        raise HydroOptionsError(f"criteria must contain exactly {sorted(_CRITERIA_KEYS)}")
    n_steps = options["n_steps"]
    if isinstance(n_steps, bool) or not isinstance(n_steps, int) or n_steps < 2:
        raise HydroOptionsError("n_steps must be an integer >= 2")
    gravity = float(options["gravity"])
    if not (math.isfinite(gravity) and gravity > 0):
        raise HydroOptionsError("gravity (the run's declared g) must be positive")
    return dict(options, length_scale=length, gravity=gravity)


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


def _prove_geometry(anyhydro, project, dataset):
    ids = [body.body_id for body in project.bodies]
    if [str(b) for b in dataset["body"].values.tolist()] != ids:
        raise HydroGeometryError("dataset body order must equal the project body order exactly")
    centres = np.asarray(dataset["rotation_center"].values, dtype=np.float64).reshape(-1, 3)
    hashes = dataset.attrs.get("mesh_hashes")
    if isinstance(hashes, str):
        import json
        hashes = json.loads(hashes)
    for k, body in enumerate(project.bodies):
        if tuple(float(v) for v in centres[k]) != tuple(float(v) for v in body.reference_position):
            raise HydroGeometryError(f"dataset rotation centre of {body.body_id!r} is not the body reference point")
        expected = _world_mesh(anyhydro, body).mesh_hash
        actual = hashes.get(body.body_id) if isinstance(hashes, Mapping) else None
        if actual != expected:
            raise HydroGeometryError(f"dataset mesh of {body.body_id!r} is not the project's world-space hull at the "
                                     "reference pose")
        if body.hydro_lid is not None:
            raise HydroGeometryError("numerical lids are not admitted by the rigid import yet (L6-3 scope)")


def _require_kernel_contract(anyhydro, kernel, dataset, dt_steps):
    from anyhydro.radiation import (HIGH_TAIL_TRUNCATE, LOW_TAIL_EXPLICIT_ZERO, LOW_TAIL_LINEAR_TO_ZERO,
                                    QUADRATURE_PIECEWISE_LINEAR, TIME_WINDOW_BARTLETT)
    settings = kernel.settings
    if kernel.provenance.get("source_dataset_hash") != anyhydro.dataset_content_hash(dataset):
        raise HydroAdmissionError("the kernel is not bound to this dataset (source content hash mismatch)")
    reasons = []
    if settings.quadrature_method != QUADRATURE_PIECEWISE_LINEAR:
        reasons.append("quadrature must be piecewise_linear_exact")
    if settings.low_tail_policy not in (LOW_TAIL_LINEAR_TO_ZERO, LOW_TAIL_EXPLICIT_ZERO):
        reasons.append("low tail must be linear_to_zero_extension or explicit_zero_sample")
    if settings.high_tail_policy != HIGH_TAIL_TRUNCATE:
        reasons.append("high tail must be truncate")
    if settings.time_window is None or getattr(settings, "time_window_shape", None) != TIME_WINDOW_BARTLETT:
        reasons.append("the memory cutoff must be a Bartlett taper (time_window_shape 'bartlett')")
    if reasons:
        raise HydroAdmissionError("the kernel is not passive by construction (ADR-015 section 8 (iv)): "
                                  + "; ".join(reasons))
    if np.asarray(kernel.time).size < dt_steps:
        raise HydroAdmissionError(f"the kernel ({np.asarray(kernel.time).size} samples) must cover every lag of the "
                                  f"{dt_steps}-step run at the run dt")


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
    bindings = tuple(rigid_binding(body, tuple(opt["dofs"][body.body_id])) for body in bodies)
    labels = [f"{b.body_id}:{d}" for b in bindings for d in b.dofs]
    full_keys = [str(k) for k in dataset["influenced_dof"].values]
    index = [full_keys.index(f"{b.body_id}::{d}") for b in bindings for d in b.dofs]
    sel = np.ix_(index, index)
    S = np.diag([1.0 if label.split(":")[1] in _TRANSLATIONS else 1.0 / opt["length_scale"] for label in labels])

    dt = float(kernel.settings.dt)
    n_steps = int(opt["n_steps"])
    _require_kernel_contract(anyhydro, kernel, dataset, n_steps)

    # ---------------------------------------------------------------- normalised full-block gate (raw source)
    scales_full = []
    for key in full_keys:
        scales_full.append(1.0 if key.split("::")[1] in _TRANSLATIONS else 1.0 / opt["length_scale"])
    criteria = anyhydro.FullBlockReconstructionCriteria(dof_scales=tuple(scales_full), **opt["criteria"])
    report = anyhydro.compare_reconstruction(kernel, dataset, criteria)
    summary = report.summary()
    if not summary["overall_pass"]:
        raise HydroAdmissionError(f"the normalised full-block reconstruction gate failed: {summary}")

    # ---------------------------------------------------------------- restoring (ADR-004 / ADR-015 section 3)
    require_hydrostatic_composition(dataset)
    contract = read_hydrostatic_contract(dataset)
    if contract.role != "BUOYANCY_ONLY":
        raise HydroAdmissionError("the rigid import composes BUOYANCY_ONLY sources with separately owned gravity only")
    K_source_full = np.asarray(dataset["hydrostatic_stiffness"].values, dtype=np.float64)
    g = opt["gravity"]          # ADR-015 section 3: one declared g; an inconsistent g fails the equilibrium check
    K_buoy = np.zeros_like(K_source_full)
    K_grav = np.zeros_like(K_source_full)
    residuals = {}
    for k, body in enumerate(bodies):
        block = slice(6 * k, 6 * k + 6)
        K_buoy[block, block] = chart_stiffness(contract, body.body_id, K_source_full[block, block])
        W = float(body.mass) * g
        R0 = np.asarray(body.reference_orientation, dtype=float)
        c = R0 @ np.asarray(body.mass_properties.cg_body, dtype=float)
        tau = -W * np.cross(c, [0.0, 0.0, 1.0])
        K_rot = -W * (c[2] * np.eye(3) - np.outer(c, [0.0, 0.0, 1.0])) - 0.5 * _skew(tau)
        K_grav[6 * k + 3:6 * k + 6, 6 * k + 3:6 * k + 6] = K_rot
        baseline = np.asarray(contract.baseline_wrench[body.body_id], dtype=float)
        force = baseline[:3] + np.array([0.0, 0.0, -W])
        moment = baseline[3:] + tau
        length = float(opt["length_scale"])
        residuals[body.body_id] = {"force": float(np.linalg.norm(force)), "moment": float(np.linalg.norm(moment)),
                                   "weight": W, "length": length}
        if np.linalg.norm(force) > EQUILIBRIUM_RELATIVE * W or np.linalg.norm(moment) > EQUILIBRIUM_RELATIVE * W * length:
            raise HydroAdmissionError(
                f"body {body.body_id!r} is not in equilibrium: |F| {np.linalg.norm(force):.6g} N, |M| "
                f"{np.linalg.norm(moment):.6g} N m (limits {EQUILIBRIUM_RELATIVE:g} W, {EQUILIBRIUM_RELATIVE:g} W L)")

    # ---------------------------------------------------------------- external records (additive, declared)
    records = tuple(ExternalMatrixRecord(kind=r["kind"], dofs=tuple(r["dofs"]),
                                         matrix=tuple(tuple(float(x) for x in row) for row in r["matrix"]),
                                         source=r["source"]) for r in opt.get("external_matrices", ()))
    C_ext = np.zeros((len(labels), len(labels)))
    K_ext = np.zeros((len(labels), len(labels)))
    for record in records:
        where = [labels.index(d) if d in labels else None for d in record.dofs]
        if None in where:
            raise HydroOptionsError(f"external record dofs {record.dofs} are not all retained")
        target = C_ext if record.kind == "damping" else K_ext
        target[np.ix_(where, where)] += np.asarray(record.matrix)

    # ---------------------------------------------------------------- symmetrisation + admission
    A_inf = np.asarray(kernel.added_mass_infinite, dtype=np.float64)[sel]
    K_total = (K_buoy + K_grav)[sel] + K_ext
    skew = {}
    A_s = fd.symmetrise("A_inf", A_inf, S)
    K_s = fd.symmetrise("K_total", K_total, S)
    C_s = fd.symmetrise("C", C_ext, S) if np.any(C_ext) else fd.Symmetrised(C_ext, 0.0)
    skew.update(A_inf=A_s.skew_ratio, K_total=K_s.skew_ratio, C=C_s.skew_ratio)
    stored = np.asarray(kernel.kernel, dtype=np.float64)[:n_steps]
    kernel_s = np.empty((n_steps, len(labels), len(labels)))
    worst = 0.0
    for l in range(n_steps):
        sample = stored[l][sel]
        if np.any(sample):
            sym = fd.symmetrise(f"kernel sample {l}", sample, S)
            kernel_s[l] = sym.symmetric
            worst = max(worst, sym.skew_ratio)
        else:
            kernel_s[l] = 0.0
    skew["kernel_max"] = worst
    B_samples = np.asarray(dataset["radiation_damping"].values, dtype=np.float64)[:, index][:, :, index]
    B_samples = 0.5 * (B_samples + np.transpose(B_samples, (0, 2, 1)))
    M_dry = rigid_dry_mass(bindings)
    try:
        fd.structural_admission(M_dry, A_s.symmetric, K_s.symmetric, C_s.symmetric, B_samples, S)
    except fd.LinearFdError as error:
        raise HydroAdmissionError(f"structural admission failed (ADR-015 section 8): {error}") from error

    # ---------------------------------------------------------------- forcing
    forces = np.zeros((n_steps, len(labels)))
    wave_selection = None
    if opt.get("wave") is not None:
        wave = opt["wave"]
        omegas = np.asarray(dataset["omega"].values, dtype=np.float64)
        headings = np.asarray(dataset["wave_direction"].values, dtype=np.float64)
        o = np.flatnonzero(omegas == float(wave["omega"]))
        h = np.flatnonzero(headings == float(wave["heading"]))
        if o.size != 1 or h.size != 1:
            raise HydroOptionsError("the wave omega and heading must be exact dataset grid values (no interpolation)")
        F = np.asarray(dataset["excitation_force"].values[o[0], h[0], :], dtype=np.complex128)[index]
        times = dt * np.arange(n_steps)
        phasor = np.exp(-1j * float(wave["omega"]) * times + 1j * float(wave.get("phase", 0.0)))
        forces = float(wave["amplitude"]) * (F[None, :] * phasor[:, None]).real
        wave_selection = dict(omega=float(wave["omega"]), heading=float(wave["heading"]),
                              amplitude=float(wave["amplitude"]), phase=float(wave.get("phase", 0.0)))

    details = {
        "format": RIGID_IMPORT_FORMAT, "route": "raw_dataset",
        "dataset_raw_sha256": hashlib.sha256(dataset_payload).hexdigest(),
        "kernel_raw_sha256": hashlib.sha256(kernel_payload).hexdigest(),
        "dataset_content_hash": anyhydro.dataset_content_hash(dataset), "kernel_content_hash": kernel.content_hash(),
        "labels": labels, "length_scale": opt["length_scale"], "criteria": criteria.to_dict(),
        "reconstruction_summary": {k: (v if not isinstance(v, (np.floating, np.integer, np.bool_)) else v.item())
                                   for k, v in summary.items() if isinstance(v, (int, float, bool, str, np.generic))},
        "skew_ratios": skew, "equilibrium_residuals": residuals,
        "hydrostatic_contract": contract.to_json(), "gravity": g, "kernel_settings": kernel.settings.to_dict(),
        "wave": wave_selection,
    }
    limits = opt.get("execution_limits")
    inputs = LinearDevelopmentInputs(
        bindings=bindings, dt=dt, n_steps=n_steps, forces=forces.tolist(), mass_dry=M_dry.tolist(),
        added_mass_infinite=A_s.symmetric.tolist(), damping=C_s.symmetric.tolist(), stiffness=K_s.symmetric.tolist(),
        radiation_kernel=kernel_s.tolist(), kernel_times=(dt * np.arange(n_steps)).tolist(),
        equilibrium_hash=equilibrium_hash(project.equilibrium),
        provenance=LinearProvenance(source=LINEAR_SOURCE_HYDRO, description=(
            "six-DOF rigid_subset_v1 inputs from an ANYhydro dataset and Bartlett-tapered kernel through the bound "
            "hydrostatic contract (chart conversion), ADR-004 gravity, declared external records and declared "
            "symmetrisation; development, unqualified"), details=details),
        q0=opt.get("q0"), v0=opt.get("v0"), external_matrices=records,
        execution_limits=LinearExecutionLimits.from_dict(limits) if limits is not None else
        LinearExecutionLimits(max_frames=max(n_steps, 1001), max_radiation_samples=max(n_steps, 2)),
        layout=LINEAR_LAYOUT_RIGID)
    return inputs, details
