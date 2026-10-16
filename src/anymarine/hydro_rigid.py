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
  dataset heading, or an irregular sea (slice N3): a canonical ANYloads WaveRealization whose component frequencies
  all lie inside that window, synthesised by ANYhydro's first-order excitation series (coefficients interpolated
  inside the stored range, never extrapolated) plus, optionally, the declared Newman slow-drift series (single body,
  unidirectional, from the dataset's far-field mean drift) on the retained surge/sway/yaw DOFs.

Nothing is silently defaulted; every refusal is a typed ``HydroImportError``. Development evidence only.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Mapping

import numpy as np

from . import linear_fd as fd
from .domain import (HYDRO_ROUTE_RIGID, LINEAR_LAYOUT_RIGID, LINEAR_SOURCE_HYDRO, RIGID_DOFS, ExternalMatrixRecord,
                     LinearDevelopmentInputs, LinearExecutionLimits, LinearProvenance, ProjectModel)
from .hashing import equilibrium_hash
from .hydro import (_LID_DEGENERATE_TOLERANCE, _LID_PLANE_TOLERANCE, HydroAdmissionError, HydroGeometryError,
                    HydroImportError, HydroLidGeometryError, HydroOptionsError, HydroUnavailableError, _import_anyhydro,
                    _read_raw_source, _require_global_source_axes, _sha256_file)
from .linear import linear_input_hash, rigid_binding, rigid_dry_mass

RIGID_IMPORT_FORMAT = "anymarine-rigid-hydro-import-v1"
#: the six-DOF route needs the ANYhydro L6-4 features (hydrostatic contract rate map/chart conversion, exact
#: hydrostatics, Bartlett window, normalised full-block gate), first released in 0.2.1
MIN_ANYHYDRO_VERSION = (0, 2, 1)
#: Newman slow drift of an irregular sea needs the drift series and stored mean-drift datasets (N2/N2b), 0.2.2
MIN_ANYHYDRO_DRIFT_VERSION = (0, 2, 2)
#: route of the hydro provenance rigid bundle: raw dataset + kernel, re-run from the stored sidecars on verification
RIGID_PROVENANCE_ROUTE = HYDRO_ROUTE_RIGID
EQUILIBRIUM_RELATIVE = 1.0e-3
_TRANSLATIONS = ("surge", "sway", "heave")
_OPTION_KEYS = {"dofs", "length_scale", "criteria", "n_steps", "gravity", "wave", "external_matrices",
                "execution_limits", "q0", "v0"}
_CRITERIA_KEYS = {"omega_min", "omega_max", "block_rel_tol", "modal_rel_tol", "energetic_fraction", "cluster_fraction"}
_RECORD_KEYS = set(ExternalMatrixRecord._KEYS)
_IRREGULAR_KEYS = {"kind", "realization", "slow_drift"}
_NEWMAN_METHODS = ("geometric", "arithmetic")


def _skew(v):
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])


def _positive(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise HydroOptionsError(f"{name} must be a number") from error
    if isinstance(value, bool) or not (math.isfinite(out) and out > 0):
        raise HydroOptionsError(f"{name} must be a positive finite number")
    return out


def _finite(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as error:
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
    if isinstance(wave, Mapping) and "kind" in wave:
        if wave["kind"] != "irregular" or set(wave) != _IRREGULAR_KEYS or not isinstance(wave["realization"], Mapping):
            raise HydroOptionsError("an irregular wave must be exactly {kind: 'irregular', realization: <ANYloads "
                                    "WaveRealization dict>, slow_drift: null | 'geometric' | 'arithmetic'}")
        if wave["slow_drift"] is not None and wave["slow_drift"] not in _NEWMAN_METHODS:
            raise HydroOptionsError(f"slow_drift must be null or one of {list(_NEWMAN_METHODS)}")
    elif wave is not None and (not isinstance(wave, Mapping) or not {"omega", "heading", "amplitude"} <= set(wave)
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


def _waterline_segments(vertices, triangles, tol):
    """The hull's waterline in the plane z = 0 as a segment soup (xy), for any hull mesh:

    - the cut of every triangle with vertices strictly on both sides of z = 0 (a vertex on the plane is a cut point);
    - every triangle edge lying on z = 0 whose adjacent triangles have an ODD number of third vertices below the
      plane: the wet side changes across it (the open boundary of a wet hull cut at z = 0, a wet/dry edge pair such as
      a moonpool rim, a closed hull with a face exactly on z = 0); folds and coplanar interiors cancel.
    Returns an (m, 2, 2) array."""
    vertices = np.asarray(vertices, dtype=np.float64)
    z = vertices[:, 2]
    segments, below_count = [], {}
    for tri in np.asarray(triangles, dtype=np.int64).tolist():
        zs = z[tri]
        on = np.abs(zs) <= tol
        for a, b, c in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
            if on[a] and on[b]:
                key = tuple(sorted((tri[a], tri[b])))
                below_count[key] = below_count.get(key, 0) + (1 if zs[c] < -tol else 0)
        if np.any(zs > tol) and np.any(zs < -tol):
            cut = [vertices[tri[k]][:2] for k in range(3) if on[k]]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                if not on[a] and not on[b] and (zs[a] > 0) != (zs[b] > 0):
                    t = zs[a] / (zs[a] - zs[b])
                    cut.append((vertices[tri[a]] + t * (vertices[tri[b]] - vertices[tri[a]]))[:2])
            if len(cut) == 2 and float(np.linalg.norm(cut[1] - cut[0])) > 0.0:
                segments.append(cut)
    for (i, j), count in below_count.items():
        if count % 2 == 1 and float(np.linalg.norm(vertices[j][:2] - vertices[i][:2])) > 0.0:
            segments.append([vertices[i][:2], vertices[j][:2]])
    return np.asarray(segments, dtype=np.float64).reshape(-1, 2, 2)


def _point_in_region(point, segments):
    """Even-odd test of a point against the waterline segment soup (several loops and holes)."""
    a, b = segments[:, 0], segments[:, 1]
    crosses = (a[:, 1] > point[1]) != (b[:, 1] > point[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        x_cross = a[:, 0] + (point[1] - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return bool(np.count_nonzero(crosses & (x_cross > point[0])) % 2)


#: lid faces must be resolvable at the containment tolerance: inradius above this multiple of tol
LID_MIN_INRADIUS_FACTOR = 100.0


def _distance_to_segments(point, segments):
    a, b = segments[:, 0], segments[:, 1]
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-300), 0.0, 1.0)
    return float(np.min(np.linalg.norm(a + t[:, None] * ab - point, axis=1)))


def require_lid_inside_waterline(lid_vertices, lid_triangles, segments, body_id, tol):
    """Every lid face lies inside the hull waterplane (ADR-015 L6-4a). For a triangle T and the waterline W:
    T is inside iff no part of W enters the OPEN interior of T and one interior point of T (its centroid) is inside
    the region bounded by W. W entering the interior is tested per segment by clipping it to T (Cyrus-Beck on T's
    three half-planes): the clipped chord's midpoint lies strictly inside T (distance above ``tol`` from every edge)
    exactly when the chord is not contained in T's boundary. Covers concave waterlines, holes and boundary contacts.
    The criterion needs faces resolvable at ``tol``: a face with inradius at most LID_MIN_INRADIUS_FACTOR x tol is
    refused (a sliver could otherwise lie entirely inside the tolerance band), and every vertex must lie inside the
    region or within ``tol`` of the waterline. Raises HydroLidGeometryError."""
    segments = np.asarray(segments, dtype=np.float64).reshape(-1, 2, 2)
    # exactly zero-length segments bound nothing (they would read as chords through a face); short but nonzero
    # segments are kept: many of them can bound a resolved hole (review round 5), never filter by tolerance
    segments = segments[np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1) > 0.0]
    if segments.shape[0] < 3:
        raise HydroLidGeometryError(f"the hull of {body_id!r} has no closed waterline at z = 0 to bound its lid")
    xy = np.asarray(lid_vertices, dtype=np.float64)[:, :2]
    seg_lo, seg_hi = segments.min(axis=1), segments.max(axis=1)
    for index, tri in enumerate(np.asarray(lid_triangles).tolist()):
        # every test in coordinates relative to the face (no cancellation at large world offsets)
        origin = xy[tri[0]].copy()
        p = xy[tri] - origin
        rel = segments - origin
        a, d = rel[:, 0], rel[:, 1] - rel[:, 0]
        area2 = (p[1, 0] - p[0, 0]) * (p[2, 1] - p[0, 1]) - (p[1, 1] - p[0, 1]) * (p[2, 0] - p[0, 0])
        perimeter = sum(float(np.linalg.norm(p[(k + 1) % 3] - p[k])) for k in range(3))
        if not abs(area2) / perimeter > LID_MIN_INRADIUS_FACTOR * tol:
            raise HydroLidGeometryError(f"numerical lid face {index} of body {body_id!r} is a sliver (inradius "
                                        f"{abs(area2) / perimeter:.3g} m) that cannot be resolved against the hull "
                                        "waterline; sliver lid faces are refused")
        for vertex in p:
            if not _point_in_region(vertex, rel) and _distance_to_segments(vertex, rel) > tol:
                raise HydroLidGeometryError(f"numerical lid face {index} of body {body_id!r} has a vertex outside "
                                            "the hull waterplane")
        orient = 1.0 if area2 > 0 else -1.0
        # inward unit normals n_k and offsets: f_k(x) = n_k . x - c_k >= 0 inside, f_k = distance to edge k
        normals, offsets = [], []
        for k in range(3):
            e = p[(k + 1) % 3] - p[k]
            n = orient * np.array([-e[1], e[0]]) / float(np.linalg.norm(e))
            normals.append(n)
            offsets.append(float(n @ p[k]))
        normals, offsets = np.array(normals), np.array(offsets)
        lo, hi = xy[tri].min(axis=0) - tol, xy[tri].max(axis=0) + tol
        near = np.flatnonzero(np.all(seg_hi >= lo, axis=1) & np.all(seg_lo <= hi, axis=1))
        if near.size:
            fa = a[near] @ normals.T - offsets                        # (m, 3) at t = 0
            fd = d[near] @ normals.T                                  # change over t in [0, 1]
            t0, t1 = np.zeros(near.size), np.ones(near.size)
            empty = np.zeros(near.size, dtype=bool)
            for k in range(3):
                parallel = np.abs(fd[:, k]) <= 1e-300
                empty |= parallel & (fa[:, k] < 0.0)
                with np.errstate(divide="ignore", invalid="ignore"):
                    root = -fa[:, k] / fd[:, k]
                entering = ~parallel & (fd[:, k] > 0)
                leaving = ~parallel & (fd[:, k] < 0)
                t0 = np.where(entering, np.maximum(t0, root), t0)
                t1 = np.where(leaving, np.minimum(t1, root), t1)
            keep = ~empty & (t1 > t0)
            if np.any(keep):
                mid = a[near][keep] + (0.5 * (t0[keep] + t1[keep]))[:, None] * d[near][keep]
                if np.any(np.min(mid @ normals.T - offsets, axis=1) > tol):
                    raise HydroLidGeometryError(f"numerical lid face {index} of body {body_id!r} covers part of the "
                                                "hull waterline (it extends outside the waterplane or over a hole)")
        if not _point_in_region(p.mean(axis=0), rel):
            raise HydroLidGeometryError(f"numerical lid face {index} of body {body_id!r} lies outside the hull "
                                        "waterplane (the lid must cover only the interior of the waterline)")


def _world_lid(anyhydro, body):
    """Expected world-space numerical lid at the reference pose (ADR-015 L6-4a): R0 x_lid + r0, every vertex on the
    equilibrium waterplane z = 0, nondegenerate faces with DOWN normals (the backend lid convention). The geometry is
    proven, never re-fitted or flipped. Only the free-surface lid policy is admitted for rigid imports."""
    if body.hydro_lid_policy != "free_surface_v1":
        raise HydroLidGeometryError(f"body {body.body_id!r}: the auxiliary lid policy {body.hydro_lid_policy!r} is not "
                                    "admitted by the rigid import")
    R0 = np.asarray(body.reference_orientation, dtype=np.float64)
    vertices = np.asarray(body.hydro_lid.positions, dtype=np.float64) @ R0.T + np.asarray(body.reference_position)
    triangles = np.asarray(body.hydro_lid.triangles, dtype=np.int32)
    off_plane = np.flatnonzero(np.abs(vertices[:, 2]) > _LID_PLANE_TOLERANCE)
    if off_plane.size:
        raise HydroLidGeometryError(f"numerical lid vertex {int(off_plane[0])} of body {body.body_id!r} lies at world z="
                                    f"{float(vertices[off_plane[0], 2])!r}, off the equilibrium waterplane z = 0")
    for index, (i, j, k) in enumerate(triangles.tolist()):
        normal = np.cross(vertices[j] - vertices[i], vertices[k] - vertices[i])
        if float(np.linalg.norm(normal)) <= _LID_DEGENERATE_TOLERANCE:
            raise HydroLidGeometryError(f"numerical lid face {index} of body {body.body_id!r} is degenerate")
        if not float(normal[2]) < 0.0:
            raise HydroLidGeometryError(f"numerical lid face {index} of body {body.body_id!r} does not point DOWN (the "
                                        "backend lid convention); lids are never flipped")
    hull = np.asarray(body.mesh.positions, dtype=np.float64) @ R0.T + np.asarray(body.reference_position)
    extent = float(np.max(np.ptp(hull, axis=0)))
    segments = _waterline_segments(hull, np.asarray(body.mesh.triangles, dtype=np.int64), _LID_PLANE_TOLERANCE)
    require_lid_inside_waterline(vertices, triangles, segments, body.body_id, 1e-9 * max(extent, 1.0))
    return anyhydro.HydroPanelMesh(vertices=vertices, faces=triangles,
                                   face_sizes=np.full(triangles.shape[0], 3, dtype=np.uint8), body_id=body.body_id,
                                   outward_normals_verified=False,
                                   source_geometry_refs=("anymarine-body-lid+reference-pose",), mesh_hash="")


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
        declared = entry.get("lid_mesh_hash")
        if body.hydro_lid is None:
            if declared is not None:
                raise HydroGeometryError(f"the dataset carries a numerical lid for {body.body_id!r} but the project body "
                                         "has none (lid on one side only)")
        elif declared is None:
            raise HydroGeometryError(f"the project body {body.body_id!r} has a numerical lid but the dataset was solved "
                                     "without one (lid on one side only)")
        elif declared != _world_lid(anyhydro, body).mesh_hash:
            raise HydroGeometryError(f"dataset lid of {body.body_id!r} is not the project's world-space lid at the "
                                     "reference pose")
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


def _anyhydro_release(anyhydro) -> tuple:
    """(X, Y, Z) of a final ANYhydro release, () otherwise."""
    raw = str(getattr(anyhydro, "__version__", ""))
    # final releases only (PEP 440 release segment X.Y.Z, optional +local): pre-, post- and dev releases and
    # malformed strings are refused rather than guessed (0.2.1.dev0 precedes 0.2.1)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(\+[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)?", raw)
    return tuple(int(part) for part in match.groups()[:3]) if match else ()


def _rigid_anyhydro():
    """The installed ANYhydro, refused (typed) when older than MIN_ANYHYDRO_VERSION."""
    anyhydro = _import_anyhydro()
    raw = str(getattr(anyhydro, "__version__", ""))
    version = _anyhydro_release(anyhydro)
    if len(version) != 3 or version < MIN_ANYHYDRO_VERSION:
        required = ".".join(str(v) for v in MIN_ANYHYDRO_VERSION)
        raise HydroUnavailableError(f"the six-DOF hydro import needs anyhydro >= {required}; the installed anyhydro is "
                                    f"{raw or 'of unknown version'} (install the ANYmarine 'waves' extra)")
    return anyhydro


def _plain_options(options) -> dict:
    """The caller options as plain JSON (stored in the provenance rigid bundle and re-run on verification)."""
    try:
        plain = json.loads(json.dumps(options, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise HydroOptionsError(f"rigid import options must be plain finite JSON: {error}") from error
    if not isinstance(plain, dict):
        raise HydroOptionsError("rigid import options must be a JSON object")
    return plain


def import_rigid_hydro_inputs(project: ProjectModel, dataset_path, kernel_path, options):
    """Admit an ANYhydro dataset + kernel as rigid_subset_v1 inputs.

    Returns ``(inputs, provenance_data)``: the immutable inputs and the full ``HydroBridgeProvenance`` field set with
    the rigid bundle (route and the plain caller options), for the caller to freeze after copying the authoritative
    raw files into the project. The dataset and kernel are decoded from private copies of the captured raw bytes and
    the original files are re-checked against that snapshot before returning (as the legacy import). Every refusal,
    including ANYhydro's own (e.g. an unresolved hydrostatic contract) and I/O failures of the private copies, is a
    typed ``HydroImportError`` with the cause preserved.
    """
    anyhydro = _rigid_anyhydro()
    try:
        return _import_rigid(anyhydro, project, dataset_path, kernel_path, options)
    except HydroImportError:
        raise
    except anyhydro.HydroError as error:
        raise HydroAdmissionError(f"the ANYhydro source was refused: {error}") from error
    except OSError as error:
        raise HydroImportError(f"I/O failure during the rigid import: {error}") from error
    except OverflowError as error:
        raise HydroOptionsError(f"a numeric rigid import option is out of range: {error}") from error


def _import_rigid(anyhydro, project: ProjectModel, dataset_path, kernel_path, options):
    from anyhydro.hydrostatic_contract import chart_stiffness, read_hydrostatic_contract, \
        require_hydrostatic_composition
    plain_options = _plain_options(options)
    opt = _parse(project, plain_options)
    if float(project.equilibrium.waterline_z) != 0.0:
        raise HydroGeometryError("the rigid import requires project waterline_z == 0")
    dataset_file, kernel_file = Path(dataset_path), Path(kernel_path)
    dataset_payload = _read_raw_source(dataset_file, "dataset")
    kernel_payload = _read_raw_source(kernel_file, "kernel")
    with tempfile.TemporaryDirectory(prefix="anymarine-rigid-import-") as temp_dir:
        dataset_copy, kernel_copy = Path(temp_dir) / "dataset.nc", Path(temp_dir) / "kernel.nc"
        dataset_copy.write_bytes(dataset_payload)
        kernel_copy.write_bytes(kernel_payload)
        try:
            dataset = anyhydro.load_hydro_dataset(dataset_copy)
        except Exception as error:
            raise HydroImportError(f"cannot load ANYhydro dataset {dataset_file}: {error}") from error
        try:
            kernel = anyhydro.load_radiation_kernel(kernel_copy)
        except Exception as error:
            raise HydroImportError(f"cannot load ANYhydro radiation kernel {kernel_file}: {error}") from error
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
    wave_selection = irregular_selection = None
    if opt.get("wave") is not None and "kind" in opt["wave"]:
        run_keys = [f"{b.body_id}::{d}" for b in bindings for d in b.dofs]
        forces, irregular_selection = _irregular_forcing(anyhydro, dataset, opt["wave"], criteria, run_keys,
                                                         len(bodies), dt, n_steps)
    elif opt.get("wave") is not None:
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
        **({"irregular_wave": irregular_selection} if irregular_selection is not None else {}),
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

    dataset_raw_sha256 = details["dataset_raw_sha256"]
    kernel_raw_sha256 = details["kernel_raw_sha256"]
    for source_file, captured, label in ((dataset_file, dataset_raw_sha256, "dataset"),
                                         (kernel_file, kernel_raw_sha256, "kernel")):
        try:
            current = _sha256_file(source_file)
        except OSError as error:
            raise HydroImportError(f"cannot re-read the ANYhydro {label} source file {source_file} for the raw "
                                   f"identity check: {error}") from error
        if current != captured:
            raise HydroImportError(f"the ANYhydro {label} source file {source_file} changed while the import was "
                                   "running; inconsistent provenance is refused")
    provenance_data = {
        "source": LINEAR_SOURCE_HYDRO,
        "dataset_content_hash": details["dataset_content_hash"],
        "kernel_content_hash": details["kernel_content_hash"],
        "dataset_raw_sha256": dataset_raw_sha256,
        "kernel_raw_sha256": kernel_raw_sha256,
        "dataset_sidecar": f"hydro/{dataset_raw_sha256}.nc",
        "kernel_sidecar": f"hydro/{kernel_raw_sha256}.nc",
        "dataset_source_path": str(dataset_file.resolve()),
        "kernel_source_path": str(kernel_file.resolve()),
        "request_hash": str(dataset.attrs["request_hash"]),
        "body_ids": [body.body_id for body in bodies],
        "dof_keys": [f"{b.body_id}::{d}" for b in bindings for d in b.dofs],
        "mesh_hashes": [_world_mesh(anyhydro, body).mesh_hash for body in bodies],
        **({"lid_mesh_hashes": [None if body.hydro_lid is None else _world_lid(anyhydro, body).mesh_hash
                                for body in bodies]} if any(body.hydro_lid is not None for body in bodies) else {}),
        "a_inf_source": str(dataset["added_mass_infinite"].attrs["source"]),
        "phasor_convention": str(dataset.attrs["phasor_convention"]),
        "source_qualification_status": str(dataset.attrs["qualification_status"]),
        "source_schema_version": str(dataset.attrs["schema_version"]),
        "source_backend": str(dataset.attrs["backend"]),
        "reference_transformations": None,
        "criteria": criteria.to_dict(),
        "reconstruction_report": summary,
        "kernel_settings": kernel.settings.to_dict(),
        "diagnostics": kernel.diagnostics.scalars(),
        "assumptions": [str(item) for item in kernel.assumptions],
        "wave": wave_selection,
        "surge_link_stiffness": 0.0,
        "dt": dt,
        "t0": float(inputs.t0),
        "n_steps": n_steps,
        "linear_input_hash": linear_input_hash(inputs),
        "created_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "rigid_bundle": {"route": RIGID_PROVENANCE_ROUTE, "layout": LINEAR_LAYOUT_RIGID, "format": RIGID_IMPORT_FORMAT,
                         "options": plain_options},
    }
    return inputs, provenance_data


def _irregular_forcing(anyhydro, dataset, wave, criteria, run_keys, n_bodies, dt, n_steps):
    """Forces (n_steps, retained DOFs) of an irregular sea and the selection record (slice N3).

    First order: ANYhydro's excitation series, F(t) = Re{sum_j a_j e^{i phi_j} X(w_j) e^{-i w_j t}} - the regular
    wave's convention; second order (optional): the Newman slow-drift series of the same realization, added to the
    retained surge/sway/yaw DOFs (the others are dropped, as the first-order forces of non-retained DOFs, and
    recorded).
    """
    try:
        from anyloads import LoadError, WaveRealization
    except ImportError as error:
        raise HydroUnavailableError("an irregular sea needs the ANYloads sea-state support (install the ANYmarine "
                                    "'waves' extra)") from error
    raw = wave["realization"]
    try:
        waves = WaveRealization.from_dict(dict(raw))
    except (LoadError, TypeError, ValueError) as error:
        raise HydroOptionsError(f"the irregular wave realization is not admissible: {error}") from error
    if json.loads(json.dumps(waves.to_dict(), allow_nan=False)) != raw:
        raise HydroOptionsError("the irregular wave realization must be canonical ANYloads data (its to_dict form)")
    omegas = np.array([c.omega for c in waves.components], dtype=np.float64)
    if not np.all((omegas >= criteria.omega_min) & (omegas <= criteria.omega_max)):
        raise HydroOptionsError("every wave component frequency must lie inside the admitted reconstruction window "
                                f"[{criteria.omega_min}, {criteria.omega_max}]")
    times = dt * np.arange(n_steps)
    series = anyhydro.build_excitation_series(dataset, waves, amplitude_normalization=anyhydro.UNIT_INCIDENT_AMPLITUDE,
                                              phase_origin_xy=(0.0, 0.0))
    series_keys = list(series.dof_keys)
    forces = np.asarray(series.evaluate(times), dtype=np.float64)[:, [series_keys.index(k) for k in run_keys]]
    selection = {"kind": "irregular", "realization_hash": waves.content_hash, "n_components": len(omegas),
                 "omega_range": [float(omegas.min()), float(omegas.max())],
                 "excitation_series_hash": series.content_hash, "slow_drift": None}
    method = wave["slow_drift"]
    if method is not None:
        if _anyhydro_release(anyhydro) < MIN_ANYHYDRO_DRIFT_VERSION:
            required = ".".join(str(v) for v in MIN_ANYHYDRO_DRIFT_VERSION)
            raise HydroUnavailableError(f"Newman slow drift needs anyhydro >= {required}; the installed anyhydro is "
                                        f"{getattr(anyhydro, '__version__', 'of unknown version')}")
        try:
            from anyhydro.slow_drift import build_drift_series
        except ImportError as error:
            raise HydroUnavailableError("Newman slow drift needs an ANYhydro with the drift series (slice N2, "
                                        "anyhydro.slow_drift.build_drift_series)") from error
        if n_bodies != 1:
            raise HydroOptionsError("Newman slow drift is single-body only (no multibody interaction terms)")
        if "mean_drift_far_field" not in dataset.data_vars:
            raise HydroOptionsError("slow drift needs the dataset's far-field mean drift (mean_drift_far_field); the "
                                    "source has none")
        drift = build_drift_series(dataset, waves, method)
        F2 = drift.evaluate(times)
        applied, dropped = [], []
        for k, key in enumerate(drift.dof_keys):
            if key in run_keys:
                forces[:, run_keys.index(key)] += F2[:, k]
                applied.append(key)
            else:
                dropped.append(key)
        if not applied:
            raise HydroOptionsError("slow drift acts on surge, sway and yaw; none of them is a retained DOF")
        record = dataset.attrs["mean_drift_far_field"]          # JSON text (computed) or decoded (loaded)
        record = json.loads(record) if isinstance(record, str) else dict(record)
        selection["slow_drift"] = {"method": method, "drift_series_hash": drift.content_hash,
                                   "heading": drift.heading_radians, "applied_dofs": applied, "dropped_dofs": dropped,
                                   "mean": [float(v) for v in drift.mean()],
                                   "source_method": record.get("method"), "source_motions": record.get("motions")}
    return forces, selection


RIGID_REFERENCE_FORMAT = "anymarine-rigid-frequency-references-v1"


def rigid_frequency_references(inputs: LinearDevelopmentInputs, provenance, dataset_path) -> dict:
    """The L6-A frequency-domain references of an imported rigid run, bound to its hydro provenance (ADR-015 s7).

    ``inputs`` and ``provenance`` (a ``HydroBridgeProvenance`` with a rigid bundle) must already be verified by the
    caller (the service re-imports from the sidecars); ``dataset_path`` is the authoritative dataset sidecar. At the
    provenance's regular wave:

    - discrete: X_d = Z_d^-1 F from the STORED run inputs (M_dry, A_inf, C, K, stored kernel, dt) - the exact
      steady state of the solver in the finite-memory regime;
    - continuous: X = Z(w)^-1 F with the dataset A(w), B(w) at the exact stored frequency (retained DOFs by
      identity, declared per-operator symmetrisation as the import) and the same K, C and F.

    F is the run's forcing phasor amplitude * F_exc(w, heading) * exp(i phase). Free-decay imports have no forcing
    frequency and are refused. Returns plain JSON.
    """
    anyhydro = _rigid_anyhydro()
    try:
        return _rigid_frequency_references(anyhydro, inputs, provenance, dataset_path)
    except HydroImportError:
        raise
    except (fd.LinearFdError, np.linalg.LinAlgError) as error:
        raise HydroAdmissionError(f"the frequency-domain references are not admissible (e.g. an undamped mode at "
                                  f"exact resonance makes the impedance singular): {error}") from error
    except anyhydro.HydroError as error:
        raise HydroAdmissionError(f"the ANYhydro source was refused: {error}") from error
    except OSError as error:
        raise HydroImportError(f"I/O failure while building the frequency-domain references: {error}") from error


def _rigid_frequency_references(anyhydro, inputs, provenance, dataset_path) -> dict:
    bundle = getattr(provenance, "rigid_bundle", None)
    if bundle is None or inputs.layout != LINEAR_LAYOUT_RIGID:
        raise HydroOptionsError("frequency-domain references are built for rigid_subset_v1 hydro imports only")
    if linear_input_hash(inputs) != provenance.linear_input_hash:
        raise HydroAdmissionError("the inputs are not the ones the hydro provenance describes (input hash)")
    wave = provenance.wave
    options_wave = bundle["options"].get("wave")
    if wave is None and isinstance(options_wave, Mapping) and "kind" in options_wave:
        raise HydroOptionsError("the import is forced by an irregular sea; frequency-domain references need a "
                                "regular wave")
    if wave is None:
        raise HydroOptionsError("the import has no regular-wave forcing (free decay): no forcing frequency to "
                                "reference")
    dataset = anyhydro.load_hydro_dataset(dataset_path)
    if anyhydro.dataset_content_hash(dataset) != provenance.dataset_content_hash:
        raise HydroAdmissionError("the dataset is not the hydro provenance's source (content hash)")
    omega, heading = float(wave["omega"]), float(wave["heading"])
    amplitude, phase = float(wave["amplitude"]), float(wave["phase"])
    keys = [str(k) for k in dataset["influenced_dof"].values]
    labels = list(inputs.labels)
    # dataset keys from the run's bindings (body ids may contain ':'; never parsed back out of labels)
    run_keys = [f"{binding.body_id}::{dof}" for binding in inputs.bindings for dof in binding.dofs]
    kinds = [dof for binding in inputs.bindings for dof in binding.dofs]
    if run_keys != list(provenance.dof_keys) or len(run_keys) != len(labels):
        raise HydroAdmissionError("the run DOFs differ from the hydro provenance DOF keys")
    try:
        index = [keys.index(key) for key in run_keys]
    except ValueError as error:
        raise HydroAdmissionError(f"a run DOF is not in the dataset: {error}") from error
    omegas = np.asarray(dataset["omega"].values, dtype=np.float64)
    headings = np.asarray(dataset["wave_direction"].values, dtype=np.float64)
    o, h = np.flatnonzero(omegas == omega), np.flatnonzero(headings == heading)
    if o.size != 1 or h.size != 1:
        raise HydroAdmissionError("the provenance wave is not an exact dataset grid value")
    sel = np.ix_(index, index)
    length_scale = _positive(bundle["options"]["length_scale"], "length_scale")
    S = np.diag([1.0 if kind in _TRANSLATIONS else 1.0 / length_scale for kind in kinds])
    A_w = fd.symmetrise("A(w)", np.asarray(dataset["added_mass"].values[o[0]], dtype=np.float64)[sel], S)
    B_w = fd.symmetrise("B(w)", np.asarray(dataset["radiation_damping"].values[o[0]], dtype=np.float64)[sel], S)
    F = (amplitude * np.exp(1j * phase)
         * np.asarray(dataset["excitation_force"].values[o[0], h[0], :], dtype=np.complex128)[index])
    M_dry = np.asarray(inputs.mass_dry, dtype=np.float64)
    C = np.asarray(inputs.damping, dtype=np.float64)
    K = np.asarray(inputs.stiffness, dtype=np.float64)
    X = fd.continuous_reference(M_dry, A_w.symmetric, B_w.symmetric, C, K, omega, F)
    X_d = fd.discrete_reference(M_dry, np.asarray(inputs.added_mass_infinite, dtype=np.float64), C, K,
                                np.asarray(inputs.radiation_kernel, dtype=np.float64), float(inputs.dt), omega, F)
    # motions in normalised coordinates: q = S q_hat, so q_hat = S^-1 q (rotations times L)
    S_inv = np.diag(1.0 / np.diag(S))
    peak = float(np.max(np.abs(S_inv @ X)))
    difference = float(np.max(np.abs(S_inv @ (X - X_d)))) / peak if peak > 0 else 0.0

    def pairs(z):
        return [[float(v.real), float(v.imag)] for v in z]

    return {
        "format": RIGID_REFERENCE_FORMAT,
        "linear_input_hash": provenance.linear_input_hash,
        "dataset_content_hash": provenance.dataset_content_hash,
        "dataset_raw_sha256": provenance.dataset_raw_sha256,
        "labels": labels,
        "wave": {"omega": omega, "heading": heading, "amplitude": amplitude, "phase": phase},
        "phasor_convention": "x(t) = Re{X exp(-i w t)}",
        "force": pairs(F),
        "discrete": pairs(X_d),
        "continuous": pairs(X),
        "skew_ratios": {"A(w)": A_w.skew_ratio, "B(w)": B_w.skew_ratio},
        "normalised_max_difference": difference,
        "note": "development references, unqualified; discrete = solver steady state (finite-memory regime), "
                "continuous = dataset RAO with the run's restoring, external damping and forcing",
    }

