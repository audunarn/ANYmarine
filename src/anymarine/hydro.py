"""ANYhydro dataset/kernel import bridge: admission, projection, provenance.

Qt-free core of the hydrodynamic application bridge. The public ``anyhydro``
API is an optional dependency imported lazily: every entry point fails
closed with an actionable error when it is unavailable, and nothing in this
module imports Qt.

Contract highlights (development only, unqualified):

- Geometry proof: ANYmarine ``BodyMesh.positions`` are body-local, the
  backend ``HydroPanelMesh.vertices`` are equilibrium WORLD positions. The
  expected neutral triangle panel mesh is built through the public
  ``HydroPanelMesh`` (positions + reference_position, identical triangle
  connectivity, per-face sizes 3, body_id projected into the hash) and must
  match the dataset mesh hash exactly. Rotation center must equal the
  reference position, transformed geometry is refused, and the project
  waterline must be z=0 because the backend uses the global z=0 free
  surface. A numerical lid is admitted only through an exact proof: the
  dataset's per-body ``lid_mesh_hash`` must equal the hash of the expected
  world-space project lid (exact vertex translation, waterplane z, non-
  degenerate faces, DOWN normals); a missing, mismatched, upward, degenerate
  or tilted/off-plane lid is a typed refusal, and both sides absent is
  supported. Nothing is silently adopted: a mismatch is an error, never a
  rebinding.
- Identity mapping: the dataset body order must equal the project body
  order exactly; the four retained DOFs are the exact ordered keys
  ``[body0::surge, body0::heave, body1::surge, body1::heave]``. No
  relabeling, no interpolation.
- Work-conjugate projection: the selector P embeds the four retained DOFs
  into the full 12-DOF space (q_full = P q); operators are projected as
  ``P.T O P`` and forces as ``P.T F``, with independently selected
  influenced (row) and radiating (column) axes.
- Effective matrices: dry translational diagonal body masses, dataset
  buoyancy-only hydrostatic stiffness, explicit optional surge linear-link
  stiffness, the dataset's explicit ``added_mass_infinite``, and zero extra
  frequency-domain damping (the radiation memory kernel already supplies
  it). No duplicate inertia or buoyancy is introduced.
- Admission: the kernel must be the dataset's own (content-hash bound,
  matching Ainf and DOF identity); ``compare_reconstruction`` is recomputed
  against the source dataset with explicit caller criteria and BOTH added
  mass and damping must pass; all symmetry/passivity diagnostic counts must
  be zero; the kernel's fixed time grid must be compatible with and at
  least as long as the run grid.
- Forcing: optional regular waves use ONLY an exact stored dataset
  frequency and heading with a real nonnegative finite amplitude and an
  explicit phase,
  ``F(t) = Re{amplitude * F_hat * exp(-i*omega*t + i*phase)}`` at absolute
  run times; the wave frequency must lie inside the declared reconstruction
  window. Without waves the run is zero-forcing free decay with explicit
  q0/v0. No interpolation, no hidden default scientific criteria.
- Raw identity: the source files' exact bytes are captured and hashed
  BEFORE anything is decoded, and the loaders run on private byte-identical
  copies, so the raw hashes provably describe the decoded content. The
  original files are re-checked against the captured snapshot before the
  import returns; a source mutated between decode and the raw identity
  check is refused. The full effective source identity (content hashes,
  request hash, criteria, report, settings, diagnostics, assumptions, DOF
  selection, wave selection) is frozen into the inputs' own provenance
  ``details`` and covered by ``linear_input_hash``; no external paths,
  timestamps or circular hashes enter it.
"""

from __future__ import annotations

from .surface_assignments import assignment_for, panel_mesh, body_surface_hash, capture_surface_geometry

import datetime as _datetime
import hashlib
import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from .domain import (
    HYDRO_ROUTE_DERIVED,
    LINEAR_DOF_COUNT,
    LINEAR_MAX_FRAMES,
    LINEAR_SOURCE_HYDRO,
    LinearBodyBinding,
    LinearDevelopmentInputs,
    LinearExecutionLimits,
    LinearProvenance,
    ProjectModel,
)
from .hashing import equilibrium_hash, mesh_hash
from .linear import linear_input_hash

__all__ = [
    "HydroAdmissionError",
    "HydroGeometryError",
    "HydroImportError",
    "HydroLidGeometryError",
    "HydroOptionsError",
    "HydroUnavailableError",
    "expected_world_lid",
    "expected_world_mesh",
    "hydro_dof_keys",
    "import_derived_hydro_inputs",
    "import_hydro_inputs",
    "recompile_derived_inputs",
]

HYDRO_DOF_ORDER = ("surge", "heave")
_HYDRO_PROVENANCE_SOURCE_LABEL = "ANYhydro hydrodynamic dataset/kernel import"
# Absolute tolerance for the lid waterplane proof: every world-space lid
# vertex must lie on the equilibrium waterplane within this tolerance.
# Tilted or off-plane lids are refused, never re-fitted.
_LID_PLANE_TOLERANCE = 1.0e-9
# Cross-product magnitude below this marks a degenerate (zero-area) lid
# triangle; degenerate faces are refused, never repaired.
_LID_DEGENERATE_TOLERANCE = 1.0e-12

_OPTION_KEYS = (
    "n_steps",
    "criteria",
    "t0",
    "q0",
    "v0",
    "surge_link_stiffness",
    "wave",
    "execution_limits",
)
_CRITERIA_KEYS = (
    "omega_min",
    "omega_max",
    "added_mass_abs_tol",
    "added_mass_rel_tol",
    "damping_abs_tol",
    "damping_rel_tol",
)
_FULL_BLOCK_CRITERIA_KEYS = (
    "form",
    "omega_min",
    "omega_max",
    "block_rel_tol",
    "modal_rel_tol",
    "energetic_fraction",
    "cluster_fraction",
)
_WAVE_KEYS = ("omega", "heading", "amplitude", "phase")


class HydroImportError(Exception):
    """Base class for hydrodynamic import failures (actionable message)."""


class HydroUnavailableError(HydroImportError):
    """The optional anyhydro package is not importable."""


class HydroOptionsError(HydroImportError):
    """The explicit import options are invalid."""


class HydroGeometryError(HydroImportError):
    """The dataset geometry does not provably match the project bodies."""


class HydroLidGeometryError(HydroGeometryError):
    """The numerical lid geometry is invalid or does not provably match.

    Typed refusal for every lid-specific failure: a dataset lid without a
    matching project lid, a project lid the dataset does not carry, an exact
    lid mesh hash mismatch, upward-facing lid normals, degenerate lid faces
    and lids that are tilted or off the equilibrium waterplane. The lid
    geometry is never flipped, rounded, re-fitted or adopted.
    """


class HydroAdmissionError(HydroImportError):
    """The dataset/kernel pair fails a development admission check."""


def _import_anyhydro():
    """Lazily import the optional anyhydro package (never at module import)."""

    try:
        import anyhydro
    except Exception as error:  # ImportError or a broken optional install
        raise HydroUnavailableError(
            "the optional anyhydro package is not available "
            f"({type(error).__name__}: {error}); the hydrodynamic import "
            "route fails closed without it"
        ) from error
    return anyhydro


def _require_global_source_axes(anyhydro, dataset) -> None:
    """Refuse local coefficient axes in the fixed global surge/heave bridge."""
    checker = getattr(anyhydro, "require_global_dof_axes", None)
    if not callable(checker):
        raise HydroUnavailableError(
            "the installed ANYhydro lacks coefficient-frame admission support; "
            "install the tested ANYhydro wheel before importing hydrodynamic inputs"
        )
    try:
        checker(dataset)
    except anyhydro.HydroError as error:
        raise HydroGeometryError(
            "the four-DOF marine bridge requires GLOBAL coefficient axes; "
            f"rotated or ambiguous source DOFs cannot be used as global surge/heave: {error}"
        ) from error

def hydro_dof_keys(project: ProjectModel) -> tuple[str, ...]:
    """The exact ordered retained DOF keys [b0::surge, b0::heave, ...]."""

    return tuple(
        f"{body.body_id}::{dof}" for body in project.bodies for dof in HYDRO_DOF_ORDER
    )


def expected_world_mesh(body):
    """Expected neutral world-space triangle panel mesh of one project body.

    ``BodyMesh.positions`` are body-local; the backend stores equilibrium
    WORLD positions, so the expected vertices are ``positions +
    reference_position``. Triangle connectivity is identical to the project
    mesh, every face has size 3, and the body_id is projected through the
    public ``HydroPanelMesh`` so it participates in the mesh hash exactly
    the way the backend computed it. Outward normals are NOT verified here
    and never claimed.
    """

    if body.reference_orientation != ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)):
        raise HydroImportError("legacy hydrodynamic geometry import does not support body reference orientation; regenerate through a supported full-frame route")
    anyhydro = _import_anyhydro()
    assignment = assignment_for(body, 'hull')
    if assignment is not None:
        return panel_mesh(body.mesh, assignment.semantics, body.reference_position)
    reference = np.asarray(body.reference_position, dtype=np.float64)
    vertices = np.asarray(body.mesh.positions, dtype=np.float64) + reference[None, :]
    triangles = np.asarray(body.mesh.triangles, dtype=np.int32)
    sizes = np.full(triangles.shape[0], 3, dtype=np.uint8)
    return anyhydro.HydroPanelMesh(
        vertices=vertices,
        faces=triangles,
        face_sizes=sizes,
        body_id=body.body_id,
        outward_normals_verified=False,
        source_geometry_refs=("anymarine-body-mesh+reference-position",),
        mesh_hash="",
    )


def expected_world_lid(body, waterline_z: float = 0.0):
    """Expected neutral world-space lid panel mesh of one project body.

    The numerical lid is body-local ``BodyMesh`` geometry: the expected
    vertices are the EXACT ``hydro_lid.positions + reference_position``
    translation (no rounding, no flipping, no re-fitting), triangle
    connectivity is identical to the project lid, every face has size 3 and
    the body_id is projected through the public ``HydroPanelMesh`` so the
    resulting ``mesh_hash`` proves the same source triangle identity the
    backend hashed.

    The lid geometry itself is proven here and fails closed with
    :class:`HydroLidGeometryError` before any comparison: every world vertex
    must lie on the equilibrium waterplane ``waterline_z`` (the current
    import only supports 0), every face must be nondegenerate, and every
    face normal must point DOWN (the pinned backend lid convention). A lid
    is numerical geometry only: no display, gravity or buoyancy is implied.
    """

    if body.hydro_lid is None:
        raise HydroLidGeometryError(
            f"body {body.body_id!r} has no numerical lid (hydro_lid); nothing "
            "to prove"
        )
    anyhydro = _import_anyhydro()
    reference = np.asarray(body.reference_position, dtype=np.float64)
    vertices = np.asarray(body.hydro_lid.positions, dtype=np.float64) + reference[None, :]
    triangles = np.asarray(body.hydro_lid.triangles, dtype=np.int32)
    elevation = float(waterline_z)
    if body.hydro_lid_policy == "submerged_0p1m_v1":
        physical = np.asarray(body.mesh.positions) + reference[None, :]
        if (waterline_z != 0. or reference[2] != 0. or
            not np.allclose(physical.min(axis=0), reference + [-.5,-.5,-.5], atol=1e-12, rtol=0) or
            not np.allclose(physical.max(axis=0), reference + [.5,.5,0.], atol=1e-12, rtol=0)):
            raise HydroLidGeometryError("submerged_0p1m_v1 requires the fixed boxes at waterline z=0")
        from math import isqrt
        from anyhydro.first_slice import make_first_slice_bodies
        n = isqrt(len(body.mesh.triangles) // 10)
        expected = make_first_slice_bodies(subdivisions=n, lid_policy=body.hydro_lid_policy)
        target = next((b for b in expected if b.body_id == body.body_id), None)
        if target is None or expected_world_mesh(body).mesh_hash != target.mesh.mesh_hash:
            raise HydroLidGeometryError("submerged_0p1m_v1 requires exact fixed first-slice physical hull geometry")
        elevation = -0.1
        lo=physical.min(axis=0);hi=physical.max(axis=0)
        if np.any(vertices[:,:2] < lo[:2] - 1e-12) or np.any(vertices[:,:2] > hi[:2] + 1e-12):
            raise HydroLidGeometryError("auxiliary lid lies outside the fixed-box interior footprint")
    # Prove the declared plane; never infer a policy from the geometry.
    for index, vertex in enumerate(vertices):
        if abs(float(vertex[2]) - elevation) > _LID_PLANE_TOLERANCE:
            raise HydroLidGeometryError(
                f"numerical lid vertex {index} of body {body.body_id!r} lies at "
                f"world z={float(vertex[2])!r}, off the equilibrium waterplane "
                f"z={elevation!r} (tolerance {_LID_PLANE_TOLERANCE!r}); "
                "tilted or off-plane lids are refused, never re-fitted"
            )
    # Face proof: nondegenerate triangles with DOWN normals. The geometry is
    # never flipped to satisfy the convention - an upward lid is a refusal.
    for index, triangle in enumerate(triangles.tolist()):
        a, b, c = (vertices[i] for i in triangle)
        normal = np.cross(b - a, c - a)
        magnitude = float(np.linalg.norm(normal))
        if magnitude <= _LID_DEGENERATE_TOLERANCE:
            raise HydroLidGeometryError(
                f"numerical lid face {index} of body {body.body_id!r} is "
                "degenerate (zero area); degenerate lid faces are refused, "
                "never repaired"
            )
        if float(normal[2]) > 0.0:
            raise HydroLidGeometryError(
                f"numerical lid face {index} of body {body.body_id!r} has an "
                "UPWARD normal; the backend lid convention requires DOWN "
                "normals and the geometry is never flipped to satisfy it"
            )
        if float(normal[2]) >= 0.0:
            raise HydroLidGeometryError(
                f"numerical lid face {index} of body {body.body_id!r} has a "
                "non-downward normal; the backend lid convention requires "
                "DOWN normals"
            )
    sizes = np.full(triangles.shape[0], 3, dtype=np.uint8)
    result = anyhydro.HydroPanelMesh(
        vertices=vertices,
        faces=triangles,
        face_sizes=sizes,
        body_id=body.body_id,
        outward_normals_verified=False,
        source_geometry_refs=((assignment_for(body, 'lid').semantics.source_geometry_id,) if assignment_for(body, 'lid') is not None else ('anymarine-body-lid+reference-position',)),
        surface=(assignment_for(body, 'lid').semantics if assignment_for(body, 'lid') is not None else None),
        mesh_hash="",
    )

    if body.hydro_lid_policy == "submerged_0p1m_v1" and result.mesh_hash != target.lid_mesh.mesh_hash:
        raise HydroLidGeometryError("submerged_0p1m_v1 requires exact fixed numerical lid geometry")
    return result


def _require_mapping(value, name: str, keys: tuple) -> dict:
    if not isinstance(value, Mapping):
        raise HydroOptionsError(f"{name} must be a JSON object")
    data = dict(value)
    unknown = sorted(set(data) - set(keys))
    if unknown:
        raise HydroOptionsError(
            f"{name} contains unknown option(s) {unknown}; allowed options are "
            f"{list(keys)} - nothing is silently ignored"
        )
    missing = sorted(set(keys) - set(data))
    if missing:
        raise HydroOptionsError(f"{name} is missing required option(s) {missing}")
    return data


def _finite_float(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise HydroOptionsError(f"{name} must be a number") from error
    if number != number or number in (float("inf"), float("-inf")):
        raise HydroOptionsError(f"{name} must be a finite number")
    return number


def _finite_vector(value, name: str, count: int) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise HydroOptionsError(f"{name} must contain exactly {count} numbers")
    return tuple(_finite_float(item, f"{name}[{index}]") for index, item in enumerate(value))


def _parse_options(options) -> dict:
    """Validate the explicit caller options; no hidden scientific defaults."""

    if not isinstance(options, Mapping):
        raise HydroOptionsError("import options must be a JSON object")
    data = dict(options)
    unknown = sorted(set(data) - set(_OPTION_KEYS))
    if unknown:
        raise HydroOptionsError(
            f"import options contain unknown option(s) {unknown}; allowed options "
            f"are {list(_OPTION_KEYS)} - nothing is silently ignored"
        )
    # n_steps and criteria are required; the rest are optional explicit values.
    if "n_steps" not in data:
        raise HydroOptionsError("import options must declare n_steps explicitly")
    if "criteria" not in data:
        raise HydroOptionsError(
            "import options must declare caller reconstruction criteria explicitly"
        )
    n_steps = data["n_steps"]
    if isinstance(n_steps, bool) or not isinstance(n_steps, int):
        raise HydroOptionsError(
            f"n_steps must be an integer; received {n_steps!r} (fractional frame "
            "counts are rejected, never truncated)"
        )
    if n_steps < 2:
        raise HydroOptionsError(f"n_steps must be >= 2; received {n_steps}")
    # Optional explicit execution limits: an execution bound for longer
    # development jobs, never a qualification. Unknown controls, booleans,
    # fractional, non-finite, non-positive and over-ceiling values are
    # rejected here, before anything is imported or allocated.
    execution_limits = None
    if data.get("execution_limits") is not None:
        try:
            execution_limits = LinearExecutionLimits.from_dict(data["execution_limits"])
        except (ValueError, TypeError) as error:
            raise HydroOptionsError(f"execution_limits is invalid: {error}") from error
    effective_max_frames = (
        execution_limits.max_frames if execution_limits is not None else LINEAR_MAX_FRAMES
    )
    if n_steps > effective_max_frames:
        raise HydroOptionsError(
            f"n_steps {n_steps} exceeds the development job bound of "
            f"{effective_max_frames} frames; declare execution_limits "
            "explicitly (e.g. {\"max_frames\": <frames>}) to admit a longer "
            "development job - an execution bound, never a qualification"
        )
    raw_criteria = data["criteria"]
    if isinstance(raw_criteria, Mapping) and raw_criteria.get("form") is not None:
        criteria = _require_mapping(raw_criteria, "criteria", _FULL_BLOCK_CRITERIA_KEYS)
        if criteria["form"] != "full_block":
            raise HydroOptionsError(f"unknown criteria form {criteria['form']!r}")
        criteria_data = {"form": "full_block"}
        criteria_data.update({
            key: _finite_float(criteria[key], f"criteria.{key}")
            for key in _FULL_BLOCK_CRITERIA_KEYS if key != "form"})
    else:
        criteria = _require_mapping(raw_criteria, "criteria", _CRITERIA_KEYS)
        criteria_data = {
            key: _finite_float(criteria[key], f"criteria.{key}") for key in _CRITERIA_KEYS
        }
    t0 = _finite_float(data.get("t0", 0.0), "t0")
    q0 = _finite_vector(data.get("q0", (0.0,) * LINEAR_DOF_COUNT), "q0", LINEAR_DOF_COUNT)
    v0 = _finite_vector(data.get("v0", (0.0,) * LINEAR_DOF_COUNT), "v0", LINEAR_DOF_COUNT)
    link = _finite_float(data.get("surge_link_stiffness", 0.0), "surge_link_stiffness")
    if link < 0.0:
        raise HydroOptionsError("surge_link_stiffness must be a nonnegative finite number")
    wave = None
    if data.get("wave") is not None:
        wave_data = _require_mapping(data["wave"], "wave", _WAVE_KEYS)
        wave = {
            "omega": _finite_float(wave_data["omega"], "wave.omega"),
            "heading": _finite_float(wave_data["heading"], "wave.heading"),
            "amplitude": _finite_float(wave_data["amplitude"], "wave.amplitude"),
            "phase": _finite_float(wave_data["phase"], "wave.phase"),
        }
        if wave["amplitude"] < 0.0:
            raise HydroOptionsError(
                "wave amplitude must be a real nonnegative finite number; "
                f"received {wave['amplitude']!r}"
            )
    return {
        "n_steps": int(n_steps),
        "criteria": criteria_data,
        "t0": t0,
        "q0": q0,
        "v0": v0,
        "surge_link_stiffness": link,
        "wave": wave,
        "execution_limits": execution_limits,
    }


def _decode_json_attr(attrs: Mapping, key: str):
    value = attrs[key]
    if isinstance(value, str):
        return json.loads(value)
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_raw_source(path: Path, label: str) -> bytes:
    """Read one source file's exact bytes before anything is decoded."""

    try:
        return path.read_bytes()
    except OSError as error:
        raise HydroImportError(
            f"cannot read the ANYhydro {label} source file {path}: {error}"
        ) from error


def expected_surface_inventory(project):
    """Public neutral inventory of exact fixed-equilibrium application geometry."""
    h = _import_anyhydro()
    bodies = []
    for body in project.bodies:
        if body.reference_orientation != ((1.,0.,0.),(0.,1.,0.),(0.,0.,1.)):
            raise HydroImportError("legacy hydrodynamic surface inventory does not support nonidentity reference orientation")
        attached = tuple(panel_mesh(s.mesh, s.semantics, body.reference_position)
                         for s in body.hydro_surfaces if s.role == 'attached')
        bodies.append(h.HydroBody(body.body_id, body.name, expected_world_mesh(body),
            expected_world_lid(body, project.equilibrium.waterline_z) if body.hydro_lid is not None else None,
            body.reference_position, body.reference_position,
            tuple(body.body_id + '::' + d for d in ('surge','sway','heave','roll','pitch','yaw')),
            attached_surfaces=attached))
    return h.surface_identity(tuple(bodies))

def _prove_surface_inventory(project, dataset):
    try:
        expected = expected_surface_inventory(project)
    except (ValueError, _import_anyhydro().HydroError) as error:
        raise HydroGeometryError(f'project surface geometry is invalid: {error}') from error
    actual = _decode_json_attr(dataset.attrs, 'surface_identity') if 'surface_identity' in dataset.attrs else None
    if actual != expected:
        raise HydroGeometryError('explicit surface inventory does not match exact project geometry, sides, owner or source identity; no sheet is omitted or relabeled')
    for body in project.bodies:
        for surface in body.hydro_surfaces:
            semantics = surface.semantics
            if surface.role != 'lid' and semantics.primary_hydrodynamics != 'potential_flow':
                raise HydroGeometryError('imported coupled coefficients require potential_flow ownership of each declared physical surface')
            if semantics.viscous_correction != 'none':
                raise HydroGeometryError('application surface viscous correction is not implemented; refusing omitted or duplicate drag')


def _prove_source_geometry(anyhydro, project: ProjectModel, dataset):
    """Shared hull/lid/reference geometry proof for both import routes.

    Returns ``(project_ids, mesh_hashes, lid_world_hashes, transforms)``.
    The dataset body order must equal the project body order exactly, every
    dataset mesh hash must equal the expected world-space project mesh, the
    rotation centers must equal the project reference positions, transformed
    geometry is refused and the numerical lid is admitted only through an
    exact world-space proof (see :func:`expected_world_lid`). A mismatch is
    a typed refusal, never a rebinding.
    """

    _prove_surface_inventory(project, dataset)
    policies = {b.hydro_lid_policy for b in project.bodies}
    declared = dataset.attrs.get("auxiliary_lid_policy", "free_surface_v1")
    if declared not in ("free_surface_v1", "submerged_0p1m_v1") or policies != {declared}:
        raise HydroLidGeometryError("dataset auxiliary lid policy does not match every project body")
    if declared == "submerged_0p1m_v1" and max(dataset["omega"].values) > 8.:
        raise HydroLidGeometryError("submerged_0p1m_v1 band above 8 rad/s is unsupported")
    bodies = tuple(project.bodies)
    dataset_bodies = [str(value) for value in dataset["body"].values.tolist()]
    project_ids = [body.body_id for body in bodies]
    if dataset_bodies != project_ids:
        raise HydroGeometryError(
            f"dataset body order {dataset_bodies} does not equal the project body "
            f"order {project_ids}; identity mapping is exact and never relabeled"
        )
    mesh_hashes = _decode_json_attr(dataset.attrs, "mesh_hashes")
    body_identity = _decode_json_attr(dataset.attrs, "body_identity")
    rotation_centers = np.asarray(dataset["rotation_center"].values, dtype=np.float64)
    expected_hashes = []
    lid_world_hashes = []
    waterline_z = float(project.equilibrium.waterline_z)
    for index, body in enumerate(bodies):
        expected = expected_world_mesh(body)
        expected_hashes.append(expected.mesh_hash)
        where = f"body {body.body_id!r}"
        if mesh_hashes.get(body.body_id) != expected.mesh_hash:
            raise HydroGeometryError(
                f"dataset mesh hash for {where} ({mesh_hashes.get(body.body_id)!r}) does "
                f"not match the expected world-space project mesh "
                f"({expected.mesh_hash!r}); geometry is never silently adopted"
            )
        identity = body_identity.get(body.body_id)
        if not isinstance(identity, dict):
            raise HydroGeometryError(f"dataset body_identity is missing {where}")
        if identity.get("mesh_hash") != expected.mesh_hash:
            raise HydroGeometryError(
                f"dataset body_identity mesh hash for {where} does not match the "
                "expected world-space project mesh"
            )
        # Numerical lid proof. Both sides absent is supported. A lid on
        # either side alone, or an exact hash mismatch, is a typed refusal:
        # the lid is proven against the project geometry or the import is
        # refused - never merely deleted, flipped, rounded or adopted.
        dataset_lid_hash = identity.get("lid_mesh_hash")
        if dataset_lid_hash is None:
            if body.hydro_lid is not None:
                raise HydroLidGeometryError(
                    f"the project body {where} carries a numerical lid but the "
                    "dataset body_identity declares no lid mesh; a lid the "
                    "dataset does not carry cannot be imported"
                )
            lid_world_hashes.append(None)
        else:
            if body.hydro_lid is None:
                raise HydroLidGeometryError(
                    f"the dataset body {where} has a lid mesh "
                    f"({dataset_lid_hash!r}) but the project body has no "
                    "numerical lid (hydro_lid); an unprovable lid is refused, "
                    "never silently dropped"
                )
            expected_lid = expected_world_lid(body, waterline_z=waterline_z)
            if dataset_lid_hash != expected_lid.mesh_hash:
                raise HydroLidGeometryError(
                    f"dataset lid mesh hash for {where} ({dataset_lid_hash!r}) does "
                    f"not match the expected world-space project lid "
                    f"({expected_lid.mesh_hash!r}); lid geometry is never "
                    "silently adopted"
                )
            lid_world_hashes.append(expected_lid.mesh_hash)
        reference = [float(value) for value in body.reference_position]
        if [float(value) for value in identity.get("rotation_center", ())] != reference:
            raise HydroGeometryError(
                f"dataset rotation_center for {where} "
                f"({identity.get('rotation_center')!r}) does not equal the project "
                f"reference_position ({reference!r})"
            )
        if [float(value) for value in rotation_centers[index]] != reference:
            raise HydroGeometryError(
                f"dataset rotation_center variable for {where} "
                f"({rotation_centers[index].tolist()!r}) does not equal the project "
                f"reference_position ({reference!r})"
            )
    transforms = dataset.attrs.get("reference_transformations")
    if isinstance(transforms, str):
        transforms = _decode_json_attr(dataset.attrs, "reference_transformations")
    if transforms:
        raise HydroGeometryError(
            "the dataset declares reference transformations "
            f"({transforms!r}); transformed geometry is unsupported by this proof"
        )
    return project_ids, expected_hashes, lid_world_hashes, transforms


def import_hydro_inputs(
    project: ProjectModel,
    dataset_path,
    kernel_path,
    options,
) -> tuple[LinearDevelopmentInputs, dict]:
    """Admit one ANYhydro dataset/kernel pair as effective linear inputs.

    Validates everything before returning anything: geometry proof, exact
    identity mapping, kernel/dataset binding, reconstruction criteria,
    diagnostics, grid compatibility and forcing options. Returns the
    immutable ``LinearDevelopmentInputs`` and the plain provenance data
    (the full ``HydroBridgeProvenance`` field set) for the caller to freeze
    after copying the authoritative raw artifacts into the project. The
    project is never mutated here; callers adopt the result explicitly.
    """

    anyhydro = _import_anyhydro()
    opt = _parse_options(options)

    bodies = tuple(project.bodies)
    if len(bodies) != 2:
        raise HydroGeometryError(
            f"the hydrodynamic import binds exactly 2 bodies; this project has {len(bodies)}"
        )
    if float(project.equilibrium.waterline_z) != 0.0:
        raise HydroGeometryError(
            "the hydrodynamic import requires project waterline_z == 0 "
            f"(received {project.equilibrium.waterline_z!r}); the backend uses "
            "the global z=0 free surface and no waterline shift is supported"
        )

    dataset_file = Path(dataset_path)
    kernel_file = Path(kernel_path)
    # Capture a consistent raw snapshot BEFORE loading: the raw sha256 below
    # is computed over exactly the bytes that are then decoded (the loaders
    # run on private byte-identical copies), so a source file mutated during
    # the import can never attach different content to the admitted
    # coefficients. The original files are re-checked against this snapshot
    # before the import returns.
    dataset_payload = _read_raw_source(dataset_file, "dataset")
    kernel_payload = _read_raw_source(kernel_file, "kernel")
    dataset_raw_sha256 = hashlib.sha256(dataset_payload).hexdigest()
    kernel_raw_sha256 = hashlib.sha256(kernel_payload).hexdigest()
    with tempfile.TemporaryDirectory(prefix="anymarine-hydro-import-") as temp_dir:
        dataset_copy = Path(temp_dir) / "dataset.nc"
        kernel_copy = Path(temp_dir) / "kernel.nc"
        dataset_copy.write_bytes(dataset_payload)
        kernel_copy.write_bytes(kernel_payload)
        try:
            dataset = anyhydro.load_hydro_dataset(dataset_copy)
            _require_global_source_axes(anyhydro, dataset)
            _prove_source_geometry(anyhydro, project, dataset)
        except HydroImportError:
            raise
        except Exception as error:
            raise HydroImportError(
                f"cannot load ANYhydro dataset {dataset_file}: {error}"
            ) from error
        try:
            kernel = anyhydro.load_radiation_kernel(kernel_copy)
        except Exception as error:
            raise HydroImportError(
                f"cannot load ANYhydro radiation kernel {kernel_file}: {error}"
            ) from error

    # ---------------------------------------------------------- identity map
    project_ids, expected_hashes, lid_world_hashes, transforms = (
        _prove_source_geometry(anyhydro, project, dataset)
    )

    # ------------------------------------------------------------- selection
    influenced = [str(value) for value in dataset["influenced_dof"].values.tolist()]
    radiating = [str(value) for value in dataset["radiating_dof"].values.tolist()]
    dof_keys = list(hydro_dof_keys(project))
    row_index = {key: position for position, key in enumerate(influenced)}
    column_index = {key: position for position, key in enumerate(radiating)}
    for key in dof_keys:
        if key not in row_index or key not in column_index:
            raise HydroAdmissionError(
                f"the dataset does not contain the retained DOF {key!r}; the "
                "retained DOF set is fixed and never relabeled"
            )
    # Work-conjugate selector P: q_full = P q (12x4). Rows follow the
    # influenced axis, columns the radiating axis; the two are selected
    # independently even though the admitted kernel shares their order.
    selector_rows = np.zeros((len(influenced), LINEAR_DOF_COUNT), dtype=np.float64)
    selector_columns = np.zeros((len(radiating), LINEAR_DOF_COUNT), dtype=np.float64)
    for local, key in enumerate(dof_keys):
        selector_rows[row_index[key], local] = 1.0
        selector_columns[column_index[key], local] = 1.0

    # --------------------------------------------------- kernel/dataset binding
    if tuple(kernel.dof_keys) != tuple(influenced):
        raise HydroAdmissionError(
            "kernel DOF identity/order does not match the dataset influenced axis; "
            "identity is checked, never reordered"
        )
    if tuple(kernel.body_ids) != tuple(project_ids):
        raise HydroAdmissionError(
            "kernel body identity/order does not match the dataset body axis"
        )
    kernel_provenance = dict(kernel.provenance)
    dataset_hash = anyhydro.dataset_content_hash(dataset)
    if kernel_provenance.get("source_dataset_hash") != dataset_hash:
        raise HydroAdmissionError(
            "the radiation kernel was not built from this dataset "
            f"({kernel_provenance.get('source_dataset_hash')!r} != {dataset_hash!r})"
        )
    if "added_mass_infinite" not in dataset.data_vars:
        raise HydroAdmissionError(
            "the dataset has no explicit added_mass_infinite; the time-domain "
            "route refuses to substitute a finite-frequency sample"
        )
    a_inf_dataset = np.asarray(dataset["added_mass_infinite"].values, dtype=np.float64)
    if not np.array_equal(np.asarray(kernel.added_mass_infinite, dtype=np.float64), a_inf_dataset):
        raise HydroAdmissionError(
            "the kernel's added_mass_infinite does not exactly match the dataset's "
            "explicit added_mass_infinite"
        )
    a_inf_source = str(dataset["added_mass_infinite"].attrs["source"])
    if not a_inf_source.strip():
        raise HydroAdmissionError("the dataset's added_mass_infinite source is empty")

    # ------------------------------------------------------------- admission
    diagnostics = kernel.diagnostics
    for name in (
        "damping_symmetry_violations",
        "added_mass_symmetry_violations",
        "passivity_violations",
    ):
        count = int(getattr(diagnostics, name))
        if count != 0:
            raise HydroAdmissionError(
                f"kernel diagnostics report {name}={count}; all symmetry/passivity "
                "diagnostic counts must be zero for admission"
            )
    if dict(opt["criteria"]).get("form") is not None:
        raise HydroOptionsError(
            "the raw-dataset route supports only the per-coefficient reconstruction "
            "criteria; full-block criteria apply to the derived route")
    criteria = anyhydro.ReconstructionCriteria(**opt["criteria"])
    try:
        report = anyhydro.compare_reconstruction(kernel, dataset, criteria)
    except Exception as error:
        raise HydroAdmissionError(
            f"reconstruction comparison against the source dataset failed: {error}"
        ) from error
    if not report.overall_pass:
        summary = report.summary()
        raise HydroAdmissionError(
            "reconstruction does not satisfy the caller criteria for BOTH added "
            f"mass and damping (max A error {summary['max_added_mass_error']:.6g}, "
            f"max B error {summary['max_damping_error']:.6g}); the artifact is "
            "refused rather than admitted with weakened criteria"
        )

    # ------------------------------------------------------- grid compatibility
    dt = float(kernel.settings.dt)
    n_steps = opt["n_steps"]
    if kernel.time.size < n_steps:
        raise HydroAdmissionError(
            f"the kernel has {kernel.time.size} samples but the run needs {n_steps} "
            "lags; the kernel duration is insufficient"
        )

    # ------------------------------------------------------ effective matrices
    mass_dry = np.zeros((LINEAR_DOF_COUNT, LINEAR_DOF_COUNT), dtype=np.float64)
    for local, body in enumerate(bodies):
        mass_dry[2 * local, 2 * local] = float(body.mass)
        mass_dry[2 * local + 1, 2 * local + 1] = float(body.mass)
    a_inf_12 = a_inf_dataset
    stiffness_12 = np.asarray(dataset["hydrostatic_stiffness"].values, dtype=np.float64)
    a_inf_4 = selector_rows.T @ a_inf_12 @ selector_columns
    stiffness_4 = selector_rows.T @ stiffness_12 @ selector_columns
    link = opt["surge_link_stiffness"]
    if link > 0.0:
        # Explicit optional linear mechanical link between the two surge DOFs.
        stiffness_4[0, 0] += link
        stiffness_4[2, 2] += link
        stiffness_4[0, 2] -= link
        stiffness_4[2, 0] -= link
    damping_4 = np.zeros((LINEAR_DOF_COUNT, LINEAR_DOF_COUNT), dtype=np.float64)
    kernel_12 = np.asarray(kernel.kernel, dtype=np.float64)[:n_steps]
    kernel_4 = np.einsum("ki,tkl,lj->tij", selector_rows, kernel_12, selector_columns)
    kernel_times = tuple(float(value) for value in np.asarray(kernel.time)[:n_steps])

    # -------------------------------------------------------------- forcing
    wave_selection = None
    if opt["wave"] is not None:
        wave = opt["wave"]
        omegas = np.asarray(dataset["omega"].values, dtype=np.float64)
        directions = np.asarray(dataset["wave_direction"].values, dtype=np.float64)
        omega_matches = np.nonzero(omegas == wave["omega"])[0]
        if omega_matches.size != 1:
            raise HydroOptionsError(
                f"wave omega {wave['omega']!r} is not an exact stored dataset "
                "frequency; no interpolation is performed"
            )
        direction_matches = np.nonzero(directions == wave["heading"])[0]
        if direction_matches.size != 1:
            raise HydroOptionsError(
                f"wave heading {wave['heading']!r} is not an exact stored dataset "
                "heading; no interpolation is performed"
            )
        omega_index = int(omega_matches[0])
        direction_index = int(direction_matches[0])
        if not (criteria.omega_min <= wave["omega"] <= criteria.omega_max):
            raise HydroOptionsError(
                f"wave omega {wave['omega']!r} lies outside the declared "
                f"reconstruction window [{criteria.omega_min!r}, {criteria.omega_max!r}]"
            )
        f_hat_12 = np.asarray(
            dataset["excitation_force"].values[omega_index, direction_index, :],
            dtype=np.complex128,
        )
        f_hat_4 = selector_rows.T @ f_hat_12
        times = np.asarray(
            [opt["t0"] + index * dt for index in range(n_steps)], dtype=np.float64
        )
        phasor = np.exp(-1j * wave["omega"] * times + 1j * wave["phase"])
        forces = wave["amplitude"] * (f_hat_4[None, :] * phasor[:, None]).real
        wave_selection = {
            "omega": wave["omega"],
            "heading": wave["heading"],
            "amplitude": wave["amplitude"],
            "phase": wave["phase"],
            "omega_index": omega_index,
            "direction_index": direction_index,
        }
    else:
        forces = np.zeros((n_steps, LINEAR_DOF_COUNT), dtype=np.float64)

    # --------------------------------------------------------------- inputs
    bindings = tuple(
        LinearBodyBinding(
            body_id=body.body_id,
            name=body.name,
            reference_position=body.reference_position,
            mass=float(body.mass),
            mesh_hash=mesh_hash(body.mesh),
            # Application-LOCAL lid identity: proves which project lid
            # geometry these inputs were built with, so a later lid edit,
            # removal or replacement makes the stored inputs stale exactly
            # like a hull edit. The hydrodynamic reference stays bound
            # separately through the hull mesh hash and reference position.
            lid_mesh_hash=mesh_hash(body.hydro_lid) if body.hydro_lid is not None else None,
            hydro_lid_policy=body.hydro_lid_policy,
            surface_hash=body_surface_hash(body),
        )
        for body in bodies
    )
    has_lid = any(item is not None for item in lid_world_hashes)
    description = (
        f"Effective four-DOF inputs projected from the ANYhydro dataset "
        f"(content hash {dataset_hash[:12]}...) and radiation kernel "
        f"(content hash {kernel.content_hash()[:12]}...) with the exact "
        "work-conjugate selector; dry diagonal body masses, dataset "
        "buoyancy-only hydrostatic stiffness"
        + (f", explicit surge link stiffness {link!r}" if link > 0.0 else "")
        + ", zero extra frequency-domain damping"
        + (
            (", proven numerical auxiliary lid at z=-0.1 m (submerged_0p1m_v1)"
             if project.bodies[0].hydro_lid_policy == "submerged_0p1m_v1"
             else ", proven numerical lid at the equilibrium waterplane")
            if has_lid
            else ""
        )
        + (
            f", regular-wave forcing at the exact stored frequency "
            f"{wave_selection['omega']!r} and heading {wave_selection['heading']!r}"
            if wave_selection is not None
            else ", zero forcing (free decay)"
        )
        + ". Development import, unqualified."
    )
    # Input-owned immutable import evidence, frozen into the inputs' own
    # provenance and covered by linear_input_hash: the FULL effective source
    # identity and admission criteria. No external paths, no wall-clock
    # timestamps, no circular linear_input_hash. The lid world hashes are
    # carried only when a lid is involved, so lidless imports keep their
    # exact legacy input hashes.
    details = {
        "dataset_content_hash": dataset_hash,
        "kernel_content_hash": kernel.content_hash(),
        "request_hash": str(dataset.attrs["request_hash"]),
        "a_inf_source": a_inf_source,
        "dof_keys": dof_keys,
        "mesh_hashes": expected_hashes,
        "criteria": criteria.to_dict(),
        "reconstruction_report": report.summary(),
        "kernel_settings": kernel.settings.to_dict(),
        "diagnostics": diagnostics.scalars(),
        "assumptions": [str(item) for item in kernel.assumptions],
        "wave": wave_selection,
        "surge_link_stiffness": link,
    }
    if has_lid:
        details["lid_mesh_hashes"] = lid_world_hashes
    inputs = LinearDevelopmentInputs(
        bindings=bindings,
        surface_geometry=capture_surface_geometry(project),
        dt=dt,
        t0=opt["t0"],
        n_steps=n_steps,
        forces=tuple(tuple(float(value) for value in row) for row in forces),
        mass_dry=tuple(tuple(float(value) for value in row) for row in mass_dry),
        added_mass_infinite=tuple(tuple(float(value) for value in row) for row in a_inf_4),
        damping=tuple(tuple(float(value) for value in row) for row in damping_4),
        stiffness=tuple(tuple(float(value) for value in row) for row in stiffness_4),
        radiation_kernel=tuple(
            tuple(tuple(float(value) for value in row) for row in sample)
            for sample in kernel_4
        ),
        kernel_times=kernel_times,
        equilibrium_hash=equilibrium_hash(project.equilibrium),
        q0=opt["q0"],
        v0=opt["v0"],
        # Explicit execution limits travel with the inputs (into snapshots,
        # persistence and the input hash); None preserves the legacy default
        # bound, serialization and hash.
        execution_limits=opt["execution_limits"],
        provenance=LinearProvenance(
            source=LINEAR_SOURCE_HYDRO,
            description=description,
            created_utc=_datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            details=details,
        ),
    )

    # The original source files must still be the captured raw snapshot when
    # the import returns: a file that changed between decode and this raw
    # identity check makes the provenance inconsistent and the import is
    # refused, never adopted.
    for source_file, captured, label in (
        (dataset_file, dataset_raw_sha256, "dataset"),
        (kernel_file, kernel_raw_sha256, "kernel"),
    ):
        try:
            current = _sha256_file(source_file)
        except OSError as error:
            raise HydroImportError(
                f"cannot re-read the ANYhydro {label} source file {source_file} "
                f"for the raw identity check: {error}"
            ) from error
        if current != captured:
            raise HydroImportError(
                f"the ANYhydro {label} source file {source_file} changed while "
                "the import was running (raw sha256 "
                f"{current[:12]}... != captured {captured[:12]}...); the "
                "admitted inputs describe the captured raw snapshot and "
                "inconsistent provenance is refused"
            )

    provenance_data = {
        "source": LINEAR_SOURCE_HYDRO,
        "dataset_content_hash": dataset_hash,
        "kernel_content_hash": kernel.content_hash(),
        "dataset_raw_sha256": dataset_raw_sha256,
        "kernel_raw_sha256": kernel_raw_sha256,
        "dataset_sidecar": f"hydro/{dataset_raw_sha256}.nc",
        "kernel_sidecar": f"hydro/{kernel_raw_sha256}.nc",
        "dataset_source_path": str(dataset_file.resolve()),
        "kernel_source_path": str(kernel_file.resolve()),
        "request_hash": str(dataset.attrs["request_hash"]),
        "body_ids": project_ids,
        "dof_keys": dof_keys,
        "mesh_hashes": expected_hashes,
        "a_inf_source": a_inf_source,
        "phasor_convention": str(dataset.attrs["phasor_convention"]),
        "source_qualification_status": str(dataset.attrs["qualification_status"]),
        "source_schema_version": str(dataset.attrs["schema_version"]),
        "source_backend": str(dataset.attrs["backend"]),
        "reference_transformations": transforms,
        "criteria": criteria.to_dict(),
        "reconstruction_report": report.summary(),
        "kernel_settings": kernel.settings.to_dict(),
        "diagnostics": diagnostics.scalars(),
        "assumptions": [str(item) for item in kernel.assumptions],
        "wave": wave_selection,
        "surge_link_stiffness": link,
        "dt": dt,
        "t0": opt["t0"],
        "n_steps": n_steps,
        "linear_input_hash": linear_input_hash(inputs),
        "created_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
    }
    if has_lid:
        # Full source provenance carries the lid identity (world-space lid
        # mesh hashes per body); omitted when no lid is involved so legacy
        # provenance records keep their exact serialized identities.
        provenance_data["lid_mesh_hashes"] = lid_world_hashes
    if project.bodies[0].hydro_lid_policy == "submerged_0p1m_v1":
        provenance_data["auxiliary_lid_policy"] = "submerged_0p1m_v1"
    return inputs, provenance_data



def _load_derived_triple(
    anyhydro,
    project,
    dataset_payload: bytes,
    operators_payload: bytes,
    kernel_payload: bytes,
    source_files,
):
    """Decode private byte-identical copies of the captured raw triple.

    The public derived loader runs with the actual raw dataset, so the full
    raw-source proof (complete rederivation under the stored settings,
    never a recorded hash alone) executes before anything is consumed; a
    foreign raw hash or forged projected arrays is a typed refusal. The
    import and the provenance recompilation share this single loading
    path so both always prove the same sources.
    """

    dataset_file, operators_file, kernel_file = source_files
    with tempfile.TemporaryDirectory(
        prefix="anymarine-hydro-import-"
    ) as temp_dir:
        dataset_copy = Path(temp_dir) / "dataset.nc"
        operators_copy = Path(temp_dir) / "operators.npz"
        kernel_copy = Path(temp_dir) / "kernel.nc"
        dataset_copy.write_bytes(dataset_payload)
        operators_copy.write_bytes(operators_payload)
        kernel_copy.write_bytes(kernel_payload)
        try:
            dataset = anyhydro.load_hydro_dataset(dataset_copy)
            _require_global_source_axes(anyhydro, dataset)
            _prove_source_geometry(anyhydro, project, dataset)
        except HydroImportError:
            raise
        except Exception as error:
            raise HydroImportError(
                f"cannot load ANYhydro dataset {dataset_file}: {error}"
            ) from error
        try:
            # Public loader with the actual raw source: the full raw-source
            # proof (complete rederivation, never hash-only) runs here. A
            # foreign raw hash or forged projected arrays is a typed refusal.
            artifact = anyhydro.load_derived_operators(
                operators_copy, source_dataset=dataset
            )
        except Exception as error:
            raise HydroImportError(
                f"cannot load/verify the ANYhydro derived operators "
                f"{operators_file}: {error}"
            ) from error
        try:
            kernel = anyhydro.load_radiation_kernel(kernel_copy)
        except Exception as error:
            raise HydroImportError(
                f"cannot load ANYhydro radiation kernel {kernel_file}: {error}"
            ) from error

    return dataset, artifact, kernel


def _assemble_derived_inputs(
    anyhydro,
    project: ProjectModel,
    dataset,
    artifact,
    kernel,
    opt: Mapping,
    operators_raw_sha256: str,
):
    """Shared admission and assembly path of the explicit derived route.

    Both the import and the provenance recompilation call this one
    function: the admission gates (geometry proof, retained identity,
    admitted artifact, kernel binding, zero diagnostics, BOTH A+B
    reconstruction, grid compatibility) and the effective SI mechanical
    array/forcing assembly exist exactly once, never cloned. The returned
    evidence carries every structured provenance field, so a recompiled
    record that disagrees with the stored inputs is authoritative evidence
    of tampering rather than a second numerical truth.
    """

    bodies = tuple(project.bodies)
    if len(bodies) != 2:
        raise HydroGeometryError(
            f"the hydrodynamic import binds exactly 2 bodies; this project has {len(bodies)}"
        )
    if float(project.equilibrium.waterline_z) != 0.0:
        raise HydroGeometryError(
            "the hydrodynamic import requires project waterline_z == 0 "
            f"(received {project.equilibrium.waterline_z!r}); the backend uses "
            "the global z=0 free surface and no waterline shift is supported"
        )

    # ------------------------------------------------------ geometry proof
    project_ids, expected_hashes, lid_world_hashes, transforms = (
        _prove_source_geometry(anyhydro, project, dataset)
    )

    # ------------------------------------------------- retained identity
    dof_keys = list(hydro_dof_keys(project))
    if tuple(artifact.retained_dofs) != tuple(dof_keys):
        raise HydroAdmissionError(
            f"the derived artifact retained DOF order {list(artifact.retained_dofs)} "
            f"does not equal the exact project retained keys {dof_keys}; the "
            "retained DOF set is fixed and never relabeled or reordered"
        )
    if tuple(artifact.source_body_order) != tuple(project_ids):
        raise HydroAdmissionError(
            f"the derived artifact source body order {list(artifact.source_body_order)} "
            f"does not equal the project body order {project_ids}"
        )
    if not artifact.admitted:
        raise HydroAdmissionError(
            "the derived operator artifact is not admitted: at least one declared "
            "criterion failed (see the artifact report); a non-admitted artifact "
            "is refused even when its arrays are otherwise well formed"
        )

    # --------------------------------------------------- kernel binding
    dataset_hash = anyhydro.dataset_content_hash(dataset)
    kernel_provenance = dict(kernel.provenance)
    if kernel_provenance.get("source_kind") != "derived_operators":
        raise HydroAdmissionError(
            "the radiation kernel was not built from a derived operator artifact "
            f"(source_kind={kernel_provenance.get('source_kind')!r}); the derived "
            "route refuses kernels from any other source"
        )
    if kernel_provenance.get("derived_content_hash") != artifact.content_hash():
        raise HydroAdmissionError(
            "the radiation kernel was not built from this derived artifact "
            f"({kernel_provenance.get('derived_content_hash')!r} != "
            f"{artifact.content_hash()!r})"
        )
    if kernel_provenance.get("source_dataset_hash") != dataset_hash:
        raise HydroAdmissionError(
            "the radiation kernel was not built from this raw dataset "
            f"({kernel_provenance.get('source_dataset_hash')!r} != {dataset_hash!r})"
        )
    if tuple(kernel.dof_keys) != tuple(dof_keys):
        raise HydroAdmissionError(
            "kernel DOF identity/order does not match the retained project keys; "
            "identity is checked, never reordered"
        )
    if tuple(kernel.body_ids) != tuple(project_ids):
        raise HydroAdmissionError(
            "kernel body identity/order does not match the project body order"
        )
    a_inf_4 = np.asarray(artifact.derived_added_mass_infinite, dtype=np.float64)
    if getattr(kernel.settings, "a_inf_policy", "computed") == "band_consistent":
        # Ogilvie-consistent time-domain A_inf: proved against the artifact's
        # computed A_inf and its own calibration band, never trusted.
        try:
            a_inf_4 = np.asarray(anyhydro.verify_band_consistent_added_mass_infinite(
                kernel, artifact.derived_added_mass_infinite, artifact.omegas,
                artifact.derived_added_mass), dtype=np.float64)
        except Exception as error:
            raise HydroAdmissionError(
                f"the kernel's band-consistent added_mass_infinite is not proved: {error}"
            ) from error
    elif not np.array_equal(np.asarray(kernel.added_mass_infinite, dtype=np.float64), a_inf_4):
        raise HydroAdmissionError(
            "the kernel's added_mass_infinite does not exactly match the derived "
            "artifact's admitted derived added_mass_infinite"
        )

    # ------------------------------------------------------------- admission
    diagnostics = kernel.diagnostics
    for name in (
        "damping_symmetry_violations",
        "added_mass_symmetry_violations",
        "passivity_violations",
    ):
        count = int(getattr(diagnostics, name))
        if count != 0:
            raise HydroAdmissionError(
                f"kernel diagnostics report {name}={count}; all symmetry/passivity "
                "diagnostic counts must be zero for admission"
            )
    if dict(opt["criteria"]).get("form") is None:
        criteria = anyhydro.ReconstructionCriteria(**opt["criteria"])
    else:
        try:
            criteria = anyhydro.reconstruction_criteria_from_dict(opt["criteria"])
        except Exception as error:
            raise HydroOptionsError(f"invalid reconstruction criteria: {error}") from error
    try:
        # BOTH A+B recomputed against the DERIVED reference through the
        # public comparator, with the actual raw source supplied so the
        # full source proof runs again before the comparison.
        report = anyhydro.compare_derived_reconstruction(
            kernel, artifact, criteria, source_dataset=dataset
        )
    except Exception as error:
        raise HydroAdmissionError(
            f"derived reconstruction comparison failed: {error}"
        ) from error
    if not report.overall_pass:
        summary = report.summary()
        raise HydroAdmissionError(
            "derived reconstruction does not satisfy the caller criteria for BOTH "
            f"added mass and damping (max A error {summary['max_added_mass_error']:.6g}, "
            f"max B error {summary['max_damping_error']:.6g}); the artifact is "
            "refused rather than admitted with weakened criteria"
        )

    # ------------------------------------------------------- grid compatibility
    dt = float(kernel.settings.dt)
    n_steps = opt["n_steps"]
    if kernel.time.size < n_steps:
        raise HydroAdmissionError(
            f"the kernel has {kernel.time.size} samples but the run needs {n_steps} "
            "lags; the kernel duration is insufficient"
        )

    # ------------------------------------------------------ effective matrices
    mass_dry = np.zeros((LINEAR_DOF_COUNT, LINEAR_DOF_COUNT), dtype=np.float64)
    for local, body in enumerate(bodies):
        mass_dry[2 * local, 2 * local] = float(body.mass)
        mass_dry[2 * local + 1, 2 * local + 1] = float(body.mass)
    # Effective SI mechanical arrays: the admitted derived Ainf and the
    # artifact's projected unmodified hydrostatic stiffness. The declared
    # normalization affects the artifact's admission diagnostics only; the
    # stored mechanical arrays are never rescaled.
    stiffness_4 = np.array(artifact.hydrostatic_stiffness, dtype=np.float64)
    link = opt["surge_link_stiffness"]
    if link > 0.0:
        # Explicit optional linear mechanical link between the two surge DOFs.
        stiffness_4[0, 0] += link
        stiffness_4[2, 2] += link
        stiffness_4[0, 2] -= link
        stiffness_4[2, 0] -= link
    damping_4 = np.zeros((LINEAR_DOF_COUNT, LINEAR_DOF_COUNT), dtype=np.float64)
    kernel_4 = np.asarray(kernel.kernel, dtype=np.float64)[:n_steps]
    kernel_times = tuple(float(value) for value in np.asarray(kernel.time)[:n_steps])

    # -------------------------------------------------------------- forcing
    wave_selection = None
    if opt["wave"] is not None:
        wave = opt["wave"]
        omegas = np.asarray(artifact.omegas, dtype=np.float64)
        directions = np.asarray(artifact.wave_directions, dtype=np.float64)
        omega_matches = np.nonzero(omegas == wave["omega"])[0]
        if omega_matches.size != 1:
            raise HydroOptionsError(
                f"wave omega {wave['omega']!r} is not an exact stored artifact "
                "frequency; no interpolation is performed"
            )
        direction_matches = np.nonzero(directions == wave["heading"])[0]
        if direction_matches.size != 1:
            raise HydroOptionsError(
                f"wave heading {wave['heading']!r} is not an exact stored artifact "
                "heading; no interpolation is performed"
            )
        omega_index = int(omega_matches[0])
        direction_index = int(direction_matches[0])
        if not (criteria.omega_min <= wave["omega"] <= criteria.omega_max):
            raise HydroOptionsError(
                f"wave omega {wave['omega']!r} lies outside the declared "
                f"reconstruction window [{criteria.omega_min!r}, {criteria.omega_max!r}]"
            )
        # Exact stored projected complex excitation of the derived artifact,
        # with the same negative-exponent/time/phase convention as the raw
        # route; no new private hydrodynamic truth is introduced here.
        f_hat_4 = np.asarray(
            artifact.excitation_force[omega_index, direction_index, :],
            dtype=np.complex128,
        )
        times = np.asarray(
            [opt["t0"] + index * dt for index in range(n_steps)], dtype=np.float64
        )
        phasor = np.exp(-1j * wave["omega"] * times + 1j * wave["phase"])
        forces = wave["amplitude"] * (f_hat_4[None, :] * phasor[:, None]).real
        wave_selection = {
            "omega": wave["omega"],
            "heading": wave["heading"],
            "amplitude": wave["amplitude"],
            "phase": wave["phase"],
            "omega_index": omega_index,
            "direction_index": direction_index,
        }
    else:
        forces = np.zeros((n_steps, LINEAR_DOF_COUNT), dtype=np.float64)

    # --------------------------------------------------------------- inputs
    bindings = tuple(
        LinearBodyBinding(
            body_id=body.body_id,
            name=body.name,
            reference_position=body.reference_position,
            mass=float(body.mass),
            mesh_hash=mesh_hash(body.mesh),
            lid_mesh_hash=mesh_hash(body.hydro_lid) if body.hydro_lid is not None else None,
            hydro_lid_policy=body.hydro_lid_policy,
            surface_hash=body_surface_hash(body),
        )
        for body in bodies
    )
    has_lid = any(item is not None for item in lid_world_hashes)
    description = (
        f"Effective four-DOF inputs from the ANYhydro derived operator artifact "
        f"(content hash {artifact.content_hash()[:12]}..., raw dataset content hash "
        f"{dataset_hash[:12]}..., radiation kernel content hash "
        f"{kernel.content_hash()[:12]}...) with the exact retained DOF order; "
        "admitted derived Ainf, projected unmodified hydrostatic stiffness, dry "
        "diagonal body masses"
        + (f", explicit surge link stiffness {link!r}" if link > 0.0 else "")
        + ", zero extra frequency-domain damping"
        + (
            (", proven numerical auxiliary lid at z=-0.1 m (submerged_0p1m_v1)"
             if project.bodies[0].hydro_lid_policy == "submerged_0p1m_v1"
             else ", proven numerical lid at the equilibrium waterplane")
            if has_lid
            else ""
        )
        + (
            f", regular-wave forcing at the exact stored frequency "
            f"{wave_selection['omega']!r} and heading {wave_selection['heading']!r}"
            if wave_selection is not None
            else ", zero forcing (free decay)"
        )
        + ". Explicit derived-operator development import, unqualified."
    )
    # Input-owned immutable import evidence: the full effective source
    # identity of the derived route, mirroring every meaningful derived field
    # of the structured provenance record. No external paths, no wall-clock
    # timestamps, no circular linear_input_hash.
    details = {
        "route": HYDRO_ROUTE_DERIVED,
        "dataset_content_hash": dataset_hash,
        "kernel_content_hash": kernel.content_hash(),
        "derived_content_hash": artifact.content_hash(),
        "operators_raw_sha256": operators_raw_sha256,
        "derived_settings": artifact.settings.to_dict(),
        "derived_report": artifact.report.summary(),
        "derived_admitted": True,
        "request_hash": str(dataset.attrs["request_hash"]),
        "a_inf_source": str(dataset["added_mass_infinite"].attrs["source"]),
        "dof_keys": dof_keys,
        "mesh_hashes": expected_hashes,
        "criteria": criteria.to_dict(),
        "reconstruction_report": report.summary(),
        "kernel_settings": kernel.settings.to_dict(),
        "diagnostics": diagnostics.scalars(),
        "assumptions": [str(item) for item in kernel.assumptions],
        "wave": wave_selection,
        "surge_link_stiffness": link,
    }
    if has_lid:
        details["lid_mesh_hashes"] = lid_world_hashes
    inputs = LinearDevelopmentInputs(
        bindings=bindings,
        surface_geometry=capture_surface_geometry(project),
        dt=dt,
        t0=opt["t0"],
        n_steps=n_steps,
        forces=tuple(tuple(float(value) for value in row) for row in forces),
        mass_dry=tuple(tuple(float(value) for value in row) for row in mass_dry),
        added_mass_infinite=tuple(tuple(float(value) for value in row) for row in a_inf_4),
        damping=tuple(tuple(float(value) for value in row) for row in damping_4),
        stiffness=tuple(tuple(float(value) for value in row) for row in stiffness_4),
        radiation_kernel=tuple(
            tuple(tuple(float(value) for value in row) for row in sample)
            for sample in kernel_4
        ),
        kernel_times=kernel_times,
        equilibrium_hash=equilibrium_hash(project.equilibrium),
        q0=opt["q0"],
        v0=opt["v0"],
        execution_limits=opt["execution_limits"],
        provenance=LinearProvenance(
            source=LINEAR_SOURCE_HYDRO,
            description=description,
            created_utc=_datetime.datetime.now(_datetime.timezone.utc).isoformat(),
            details=details,
        ),
    )

    evidence = {
        "dataset_content_hash": dataset_hash,
        "kernel_content_hash": kernel.content_hash(),
        "request_hash": str(dataset.attrs["request_hash"]),
        "body_ids": project_ids,
        "dof_keys": dof_keys,
        "mesh_hashes": expected_hashes,
        "a_inf_source": str(dataset["added_mass_infinite"].attrs["source"]),
        "phasor_convention": str(dataset.attrs["phasor_convention"]),
        "source_qualification_status": str(dataset.attrs["qualification_status"]),
        "source_schema_version": str(dataset.attrs["schema_version"]),
        "source_backend": str(dataset.attrs["backend"]),
        "reference_transformations": transforms,
        "criteria": criteria.to_dict(),
        "reconstruction_report": report.summary(),
        "kernel_settings": kernel.settings.to_dict(),
        "diagnostics": diagnostics.scalars(),
        "assumptions": [str(item) for item in kernel.assumptions],
        "wave": wave_selection,
        "surge_link_stiffness": link,
        "dt": dt,
        "t0": opt["t0"],
        "n_steps": n_steps,
        "derived_content_hash": artifact.content_hash(),
        "derived_settings": artifact.settings.to_dict(),
        "derived_report": artifact.report.summary(),
        "linear_input_hash": linear_input_hash(inputs),
    }
    if has_lid:
        evidence["lid_mesh_hashes"] = lid_world_hashes
    return inputs, evidence


def recompile_derived_inputs(
    project: ProjectModel,
    provenance_data: Mapping,
    dataset_payload: bytes,
    operators_payload: bytes,
    kernel_payload: bytes,
) -> LinearDevelopmentInputs:
    """Rebuild the effective derived inputs from the authoritative bytes.

    Consumer-side source ownership proof: the exact raw bytes of the stored
    dataset/operators/kernel sidecars are decoded again through the public
    ANYhydro loaders (full raw-source proof) and the complete shared
    admission and assembly path re-runs under the provenance record's
    declared controls (criteria, wave, surge link, grid, initial state,
    execution limits). The returned inputs are what the authoritative
    sources actually say; comparing their full input hash against the
    record's stored hash detects any effective-operator tampering that
    re-hashed the bookkeeping. The raw route carries no derived bundle and
    no recompilation: this is the derived route's source proof.
    """

    anyhydro = _import_anyhydro()
    bundle = provenance_data["derived_bundle"]
    if bundle is None:
        raise HydroImportError(
            "recompilation requires a derived-operator provenance bundle; a "
            "raw-route record carries no derived source evidence to recompile"
        )
    limits_data = bundle.get("execution_limits")
    try:
        execution_limits = (
            None
            if limits_data is None
            else LinearExecutionLimits.from_dict(dict(limits_data))
        )
    except (ValueError, TypeError) as error:
        raise HydroImportError(
            f"the derived bundle execution_limits is invalid: {error}"
        ) from error
    wave_data = provenance_data["wave"]
    wave = None
    if wave_data is not None:
        wave = {key: float(wave_data[key]) for key in _WAVE_KEYS}
    opt = {
        "n_steps": int(provenance_data["n_steps"]),
        "criteria": {
            key: (str(value) if key == "form" else float(value))
            for key, value in dict(provenance_data["criteria"]).items()
        },
        "t0": float(provenance_data["t0"]),
        "q0": tuple(float(value) for value in bundle["q0"]),
        "v0": tuple(float(value) for value in bundle["v0"]),
        "surge_link_stiffness": float(provenance_data["surge_link_stiffness"]),
        "wave": wave,
        "execution_limits": execution_limits,
    }
    operators_raw_sha256 = hashlib.sha256(operators_payload).hexdigest()
    dataset, artifact, kernel = _load_derived_triple(
        anyhydro,
        project,
        dataset_payload,
        operators_payload,
        kernel_payload,
        (
            "<stored dataset sidecar>",
            "<stored operators sidecar>",
            "<stored kernel sidecar>",
        ),
    )
    inputs, evidence = _assemble_derived_inputs(
        anyhydro,
        project,
        dataset,
        artifact,
        kernel,
        opt,
        operators_raw_sha256,
    )
    # The recompiled evidence must be the frozen record's evidence. These
    # identities are stable strings/integers/tuples, so exact comparison is
    # the correct check; the caller's full input-hash comparison covers
    # every remaining field (arrays, controls, reports, settings).
    for key in (
        "dataset_content_hash",
        "kernel_content_hash",
        "request_hash",
        "a_inf_source",
        "phasor_convention",
        "source_qualification_status",
        "source_schema_version",
        "source_backend",
    ):
        if str(evidence[key]) != str(provenance_data[key]):
            raise HydroImportError(
                f"the authoritative derived sources recompile {key}="
                f"{evidence[key]!r} but the hydro provenance record declares "
                f"{provenance_data[key]!r}; the stored evidence does not "
                "describe the stored sources"
            )
    if str(evidence["derived_content_hash"]) != str(bundle["derived_content_hash"]):
        raise HydroImportError(
            "the authoritative derived sources recompile derived_content_hash="
            f"{evidence['derived_content_hash']!r} but the hydro provenance "
            f"bundle declares {bundle['derived_content_hash']!r}; the stored "
            "evidence does not describe the stored sources"
        )
    for key in ("body_ids", "dof_keys"):
        if tuple(evidence[key]) != tuple(provenance_data[key]):
            raise HydroImportError(
                f"the authoritative derived sources recompile {key}="
                f"{list(evidence[key])!r} but the hydro provenance record "
                f"declares {list(provenance_data[key])!r}; the stored "
                "evidence does not describe the stored sources"
            )
    if int(evidence["n_steps"]) != int(provenance_data["n_steps"]):
        raise HydroImportError(
            f"the authoritative derived sources recompile n_steps="
            f"{evidence['n_steps']} but the hydro provenance record declares "
            f"{provenance_data['n_steps']}"
        )
    return inputs


def import_derived_hydro_inputs(
    project: ProjectModel,
    dataset_path,
    operators_path,
    kernel_path,
    options,
) -> tuple[LinearDevelopmentInputs, dict]:
    """Admit one ANYhydro raw/derived/kernel triple as effective inputs.

    Explicit derived-operator route, selected only by calling this function
    (never a fallback of the raw route). The exact raw bytes of all three
    source files are captured and hashed BEFORE anything is decoded, the
    loaders run on private byte-identical copies, and the original files are
    re-checked against the captured snapshot before the import returns.

    Admission (development only, unqualified):

    - The derived artifact is loaded through the public
      ``load_derived_operators`` with the actual raw dataset, so the full
      raw-source proof (complete rederivation under the stored settings,
      never the recorded hash alone) runs before anything is consumed; a
      foreign raw hash or forged projected arrays are typed refusals.
    - ``artifact.admitted`` is required: the explicit policy/criteria verdict
      of the artifact itself, recomputed by the public loader.
    - The retained DOF keys must equal the exact project body order
      surge/heave keys; the source body order must equal the project body
      order; the hull/lid/reference geometry proof is shared with the raw
      route.
    - The kernel must be the retained kernel built from exactly this derived
      artifact (public comparator identity: derived content hash, raw source
      hash, retained DOF order, correction policy, normalization and
      criteria) and its ``added_mass_infinite`` must equal the artifact's
      derived Ainf exactly; all symmetry/passivity diagnostic counts must be
      zero; BOTH added mass and damping reconstruction are recomputed
      against the derived reference through the public comparator with the
      raw source supplied; the kernel must cover ``n_steps`` samples with no
      truncation or interpolation.
    - Effective SI mechanical arrays: the admitted derived Ainf, the
      artifact's projected unmodified hydrostatic stiffness, dry diagonal
      body masses, the optional explicit surge link and zero extra
      frequency-domain damping. The declared normalization affects the
      artifact's admission diagnostics only; the stored mechanical arrays
      are never rescaled.
    - Regular-wave forcing uses ONLY an exact stored artifact frequency and
      heading with the same negative-exponent/time/phase convention as the
      raw route, from the artifact's projected complex excitation.

    Returns the immutable ``LinearDevelopmentInputs`` and the plain
    provenance data (the full ``HydroBridgeProvenance`` field set plus the
    frozen derived bundle) for the caller to freeze after copying the
    authoritative raw artifacts into the project.
    """

    anyhydro = _import_anyhydro()
    opt = _parse_options(options)

    dataset_file = Path(dataset_path)
    operators_file = Path(operators_path)
    kernel_file = Path(kernel_path)
    # Capture a consistent raw snapshot of ALL THREE sources BEFORE loading:
    # the raw sha256 values below are computed over exactly the bytes that
    # are then decoded (the loaders run on private byte-identical copies).
    dataset_payload = _read_raw_source(dataset_file, "dataset")
    operators_payload = _read_raw_source(operators_file, "derived operators")
    kernel_payload = _read_raw_source(kernel_file, "kernel")
    dataset_raw_sha256 = hashlib.sha256(dataset_payload).hexdigest()
    operators_raw_sha256 = hashlib.sha256(operators_payload).hexdigest()
    kernel_raw_sha256 = hashlib.sha256(kernel_payload).hexdigest()
    dataset, artifact, kernel = _load_derived_triple(
        anyhydro,
        project,
        dataset_payload,
        operators_payload,
        kernel_payload,
        (dataset_file, operators_file, kernel_file),
    )
    inputs, evidence = _assemble_derived_inputs(
        anyhydro, project, dataset, artifact, kernel, opt, operators_raw_sha256
    )
    # The original source files must still be the captured raw snapshot when
    # the import returns: a file that changed between decode and this raw
    # identity check makes the provenance inconsistent and the import is
    # refused, never adopted.
    for source_file, captured, label in (
        (dataset_file, dataset_raw_sha256, "dataset"),
        (operators_file, operators_raw_sha256, "derived operators"),
        (kernel_file, kernel_raw_sha256, "kernel"),
    ):
        try:
            current = _sha256_file(source_file)
        except OSError as error:
            raise HydroImportError(
                f"cannot re-read the ANYhydro {label} source file {source_file} "
                f"for the raw identity check: {error}"
            ) from error
        if current != captured:
            raise HydroImportError(
                f"the ANYhydro {label} source file {source_file} changed while "
                "the import was running (raw sha256 "
                f"{current[:12]}... != captured {captured[:12]}...); the "
                "admitted inputs describe the captured raw snapshot and "
                "inconsistent provenance is refused"
            )

    provenance_data = {
        "source": LINEAR_SOURCE_HYDRO,
        "dataset_content_hash": evidence["dataset_content_hash"],
        "kernel_content_hash": evidence["kernel_content_hash"],
        "dataset_raw_sha256": dataset_raw_sha256,
        "kernel_raw_sha256": kernel_raw_sha256,
        "dataset_sidecar": f"hydro/{dataset_raw_sha256}.nc",
        "kernel_sidecar": f"hydro/{kernel_raw_sha256}.nc",
        "dataset_source_path": str(dataset_file.resolve()),
        "kernel_source_path": str(kernel_file.resolve()),
        "request_hash": evidence["request_hash"],
        "body_ids": evidence["body_ids"],
        "dof_keys": evidence["dof_keys"],
        "mesh_hashes": evidence["mesh_hashes"],
        "a_inf_source": evidence["a_inf_source"],
        "phasor_convention": evidence["phasor_convention"],
        "source_qualification_status": evidence["source_qualification_status"],
        "source_schema_version": evidence["source_schema_version"],
        "source_backend": evidence["source_backend"],
        "reference_transformations": evidence["reference_transformations"],
        "criteria": evidence["criteria"],
        "reconstruction_report": evidence["reconstruction_report"],
        "kernel_settings": evidence["kernel_settings"],
        "diagnostics": evidence["diagnostics"],
        "assumptions": evidence["assumptions"],
        "wave": evidence["wave"],
        "surge_link_stiffness": evidence["surge_link_stiffness"],
        "dt": evidence["dt"],
        "t0": evidence["t0"],
        "n_steps": evidence["n_steps"],
        "linear_input_hash": evidence["linear_input_hash"],
        "created_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        # Frozen derived-operator provenance bundle: present only on the
        # explicit derived route, carrying the artifact identity, the raw
        # operators snapshot, the authoritative sidecar, the producing
        # source path, the complete explicit settings and report, and the
        # declared initial state and execution limits so the effective
        # inputs can be recompiled from the stored sources alone. No
        # partial derived intent is allowed.
        "derived_bundle": {
            "route": HYDRO_ROUTE_DERIVED,
            "derived_content_hash": evidence["derived_content_hash"],
            "operators_raw_sha256": operators_raw_sha256,
            "operators_sidecar": f"hydro/{operators_raw_sha256}.npz",
            "operators_source_path": str(operators_file.resolve()),
            "derived_settings": evidence["derived_settings"],
            "derived_report": evidence["derived_report"],
            "derived_admitted": True,
            "q0": [float(value) for value in opt["q0"]],
            "v0": [float(value) for value in opt["v0"]],
            "execution_limits": (
                None
                if opt["execution_limits"] is None
                else opt["execution_limits"].to_dict()
            ),
        },
    }
    if "lid_mesh_hashes" in evidence:
        # Full source provenance carries the lid identity (world-space lid
        # mesh hashes per body); omitted when no lid is involved so legacy
        # provenance records keep their exact serialized identities.
        provenance_data["lid_mesh_hashes"] = evidence["lid_mesh_hashes"]
    if project.bodies[0].hydro_lid_policy == "submerged_0p1m_v1":
        provenance_data["auxiliary_lid_policy"] = "submerged_0p1m_v1"
    return inputs, provenance_data
