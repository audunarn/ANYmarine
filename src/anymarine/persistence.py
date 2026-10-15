"""Robust project/results persistence.

Layout on disk (all sidecar references are relative and checksummed):

    <root>/project.json                 manifest (the single commit point)
    <root>/bodies/<sha256>.json         immutable body record incl. mesh
    <root>/results/<sha256>.json        immutable result artifact

Sidecars are content-addressed: a sidecar's file name is the sha256 of its
exact bytes, so a given sidecar identity can never hold different bytes and
re-saving never overwrites an existing artifact with new content.

Guarantees:

- Atomic writes: sidecars and manifest are written to a temporary file in the
  same directory, flushed, fsynced, then ``os.replace``d into place.
- The manifest is the single commit point, written last and swapped atomically.
  A failure before or during the manifest swap leaves the previous manifest in
  place, and every sidecar it references still holds the previous bytes, so
  the old project remains fully loadable. Orphaned sidecars from a failed save
  are harmless immutable content and are left in place as failed-save evidence.
- Loading validates every reference and checksum before returning anything;
  a missing or corrupt sidecar raises and leaves the caller's model untouched.
- Loading validates manifest container types and requires the body/result
  identity inside each sidecar to agree with the manifest entry; a mismatched
  identity is refused, never silently replaced.
- Sidecar references must be relative, contain no ``..``/absolute/drive
  components, and must not traverse symlinks out of the project root.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

from . import RESULT_KIND_LINEAR, RESULT_KIND_MECHANICAL, RESULT_KIND_EQUILIBRIUM
from .domain import ProjectModel, ResultRecord
from .surface_assignments import has_surface_intent

# Legacy manifests (synthetic-only projects) stay at version 2 and remain
# loadable. Projects carrying linear development intent (linear inputs or
# linear results) are written at version 3: an older application that only
# knows version 2 rejects the manifest instead of silently ignoring and
# erasing the linear intent on its next save. Projects carrying hydrodynamic
# import intent (structured hydro provenance with authoritative raw
# dataset/kernel artifacts) are written at version 4 for the same reason.
SCHEMA_VERSION = 2
SCHEMA_VERSION_LINEAR = 3
SCHEMA_VERSION_HYDRO = 4
SCHEMA_VERSION_NATIVE = 5
SCHEMA_VERSION_LINE_EQUILIBRIUM = 6
SCHEMA_VERSION_SURFACES = 7
SCHEMA_VERSION_LID_POLICY = 8
SCHEMA_VERSION_EQUILIBRIUM_ADOPTION = 9
# ADR-015: projects whose linear inputs use the rigid_subset_v1 layout. Older readers refuse it
# (they would read the channels as surge/heave); it is otherwise a superset of version 9.
SCHEMA_VERSION_RIGID_LINEAR = 10

def has_adoption_intent(project):
    def contains(value):
        if isinstance(value,dict):
            return "reference_orientation" in value or "equilibrium_adoption" in value or any(contains(v) for v in value.values())
        if isinstance(value,(list,tuple)):return any(contains(v) for v in value)
        return False
    return contains(project.to_dict())

def has_rigid_linear_intent(project):
    inputs = project.linear_inputs
    return inputs is not None and getattr(inputs, "layout", "two_body_surge_heave_v1") != "two_body_surge_heave_v1"


def has_lid_policy_intent(project):
    def contains(value):
        if isinstance(value, dict):
            return "hydro_lid_policy" in value or "auxiliary_lid_policy" in value or any(contains(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains(v) for v in value)
        return False
    return contains(project.to_dict())
SUPPORTED_SCHEMA_VERSIONS = (
    SCHEMA_VERSION,
    SCHEMA_VERSION_LINEAR,
    SCHEMA_VERSION_HYDRO,
    SCHEMA_VERSION_NATIVE,
    SCHEMA_VERSION_LINE_EQUILIBRIUM,
    SCHEMA_VERSION_SURFACES,
    SCHEMA_VERSION_LID_POLICY,
    SCHEMA_VERSION_EQUILIBRIUM_ADOPTION,
    SCHEMA_VERSION_RIGID_LINEAR,
)
MANIFEST_NAME = "project.json"
HYDRO_ARTIFACT_ROLES = ("dataset", "kernel")
HYDRO_DERIVED_ARTIFACT_ROLES = ("dataset", "operators", "kernel")


def _hydro_artifact_roles(provenance) -> tuple[tuple[str, str, str], ...]:
    """The authoritative (role, sidecar reference, raw sha256) triples.

    Legacy raw imports carry exactly the dataset and kernel roles; a derived
    import additionally carries the derived operators artifact. The role
    registry is strict: exactly 2 roles for the raw route, exactly 3 for the
    derived route.
    """

    roles = (
        ("dataset", provenance.dataset_sidecar, provenance.dataset_raw_sha256),
        ("kernel", provenance.kernel_sidecar, provenance.kernel_raw_sha256),
    )
    bundle = provenance.derived_bundle
    if bundle is not None:
        roles = roles + (
            ("operators", bundle["operators_sidecar"], bundle["operators_raw_sha256"]),
        )
    return roles


class ProjectError(Exception):
    """Base error for persistence failures (actionable message attached)."""


class ProjectLoadError(ProjectError):
    """Raised when a project cannot be loaded; the caller's model is unchanged."""


class ProjectSaveError(ProjectError):
    """Raised when a project cannot be saved; existing files are preserved."""


class UnsafePathError(ProjectError):
    """Raised when a sidecar reference escapes the project root."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to *path* atomically; existing content survives failures."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.name)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data) -> None:
    payload = json_bytes(data)
    atomic_write_bytes(path, payload)


def json_bytes(data) -> bytes:
    """Exact canonical bytes used for both checksums and sidecar files."""

    return json.dumps(data, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")


def check_relative_reference(reference: str) -> PurePosixPath:
    """Validate a sidecar reference and return its normalized relative path.

    Rejects absolute paths, Windows drive letters, UNC paths, empty and
    traversal (``..``) references before any filesystem access.
    """

    if not isinstance(reference, str) or not reference.strip():
        raise UnsafePathError("sidecar reference must be a non-empty relative path")
    text = reference.strip().replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("//"):
        raise UnsafePathError(f"sidecar reference {reference!r} must be relative")
    if len(text) > 1 and text[1] == ":":
        raise UnsafePathError(f"sidecar reference {reference!r} must not contain a drive letter")
    if "//" in text or text.startswith("./") or "/./" in text or text.endswith("/."):
        raise UnsafePathError(f"sidecar reference {reference!r} is not a normalized relative path")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise UnsafePathError(f"sidecar reference {reference!r} must not traverse upwards")
    if any(part in ("", ".") for part in pure.parts):
        raise UnsafePathError(f"sidecar reference {reference!r} is not a normalized relative path")
    return pure


def resolve_sidecar(root: Path, reference: str) -> Path:
    """Resolve a validated reference under *root*, refusing symlink escapes."""

    relative = check_relative_reference(reference)
    # Do not erase a linked root's identity while resolving sidecars. This
    # keeps the same fail-closed contract for roots and child components.
    if root.is_symlink():
        raise UnsafePathError("project root must not be a symbolic link")
    root_real = root.resolve()
    candidate = root / Path(*relative.parts)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root_real)
    except ValueError as error:
        raise UnsafePathError(
            f"sidecar reference {reference!r} resolves outside the project root"
        ) from error
    # Refuse symlinked components that leave the root even when they resolve
    # back inside it via links (defence in depth against link-based escapes).
    current = root_real
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise UnsafePathError(
                f"sidecar reference {reference!r} traverses the symlink {current}"
            )
    return candidate


class ProjectStore:
    """Load/save a complete ProjectModel with checksummed sidecars."""

    def __init__(self, root: os.PathLike | str):
        self.root = Path(root)

    # ------------------------------------------------------------------ save

    def save(self, project: ProjectModel) -> None:
        """Persist *project*; on any failure existing files are preserved.

        Content-addressed sidecars are written first; the manifest is then
        swapped atomically as the single commit point. A failure at any stage
        (including an injected manifest-write failure) leaves the previous
        manifest and all sidecars it references untouched, so the previously
        saved project remains loadable byte-for-byte.
        """

        try:
            # A hydro-intent project may only be saved when its authoritative
            # raw dataset/kernel artifacts are present and hash-correct under
            # this root. The check runs before anything is written, so a
            # missing or corrupt artifact refuses the save with the existing
            # manifest (and every existing sidecar) left untouched instead of
            # publishing a manifest that references a dangling sidecar.
            if project.hydro_provenance is not None:
                provenance = project.hydro_provenance
                for role, reference, expected in _hydro_artifact_roles(provenance):
                    path = resolve_sidecar(self.root, reference)
                    try:
                        raw = path.read_bytes()
                    except FileNotFoundError as error:
                        raise ProjectSaveError(
                            f"the hydrodynamic {role} artifact {reference!r} is "
                            f"missing under {self.root}; refusing to publish a "
                            "manifest that references a missing sidecar"
                        ) from error
                    except OSError as error:
                        raise ProjectSaveError(
                            f"cannot verify the hydrodynamic {role} artifact "
                            f"{reference!r}: {error}"
                        ) from error
                    if sha256_bytes(raw) != expected:
                        raise ProjectSaveError(
                            f"the hydrodynamic {role} artifact {reference!r} is "
                            "corrupt: sha256 mismatch against the hydro "
                            "provenance record; refusing the save"
                        )
            if project.linear_inputs is not None and project.hydro_provenance is None:
                from .domain import LINEAR_SOURCE_HYDRO

                if project.linear_inputs.provenance.source == LINEAR_SOURCE_HYDRO:
                    raise ProjectSaveError(
                        f"the stored linear inputs declare {LINEAR_SOURCE_HYDRO!r} "
                        "provenance but the project carries no structured hydro "
                        "provenance; refusing to write a manifest whose "
                        "hydrodynamic intent could be silently stripped"
                    )
            body_payloads = [
                (body.body_id, json_bytes(body.to_dict())) for body in project.bodies
            ]
            result_payloads = [
                (result.result_id, json_bytes(result.to_dict()))
                for result in project.results
            ]
            linear_intent = project.linear_inputs is not None or any(
                result.kind == RESULT_KIND_LINEAR for result in project.results
            )
            hydro_intent = project.hydro_provenance is not None
            manifest = {
                "schema_version": (
                    SCHEMA_VERSION_RIGID_LINEAR if has_rigid_linear_intent(project) else
                    SCHEMA_VERSION_EQUILIBRIUM_ADOPTION if has_adoption_intent(project) else
                    SCHEMA_VERSION_LID_POLICY if has_lid_policy_intent(project) else
                    SCHEMA_VERSION_SURFACES if has_surface_intent(project) else
                    SCHEMA_VERSION_LINE_EQUILIBRIUM if (project.line_equilibrium_inputs is not None or any(r.kind=='line_equilibrium_development' for r in project.results)) else
                    SCHEMA_VERSION_NATIVE if (project.native_inputs is not None or any(r.kind in (RESULT_KIND_MECHANICAL,RESULT_KIND_EQUILIBRIUM) for r in project.results)) else
                    SCHEMA_VERSION_HYDRO
                    if hydro_intent
                    else (SCHEMA_VERSION_LINEAR if linear_intent else SCHEMA_VERSION)
                ),
                **({"equilibrium_adoption": project.to_dict()["equilibrium_adoption"]} if project.equilibrium_adoption is not None else {}),
                "name": project.name,
                "equilibrium": project.equilibrium.to_dict(),
                "settings": project.settings.to_dict(),
                "body_order": [body.body_id for body in project.bodies],
                "bodies": {},
                "results": [],
            }
            if project.hydro_provenance is not None:
                provenance = project.hydro_provenance
                provenance_payload = json_bytes(provenance.to_dict())
                provenance_digest = sha256_bytes(provenance_payload)
                provenance_reference = f"hydro/{provenance_digest}.json"
                self._write_content_addressed(
                    provenance_reference, provenance_payload, provenance_digest
                )
                manifest["hydro_provenance"] = {
                    "sidecar": provenance_reference,
                    "sha256": provenance_digest,
                }
                # The authoritative raw dataset/kernel files (and, on the
                # derived route, the derived operators artifact) were copied
                # into the project at import time; the manifest records
                # their content-addressed references and raw byte hashes,
                # checked again on every load. The role registry is strict:
                # exactly 2 roles for the raw route, exactly 3 for the
                # derived route.
                manifest["hydro_artifacts"] = [
                    {"role": role, "sidecar": reference, "sha256": expected}
                    for role, reference, expected in _hydro_artifact_roles(provenance)
                ]
            if project.linear_inputs is not None:
                linear_payload = json_bytes(project.linear_inputs.to_dict())
                linear_digest = sha256_bytes(linear_payload)
                linear_reference = f"linear_inputs/{linear_digest}.json"
                self._write_content_addressed(linear_reference, linear_payload, linear_digest)
                manifest["linear_inputs"] = {
                    "sidecar": linear_reference,
                    "sha256": linear_digest,
                }
            if project.line_equilibrium_inputs is not None:
                line_payload=json_bytes(project.line_equilibrium_inputs.to_dict())
                line_digest=sha256_bytes(line_payload)
                line_reference=f'line_equilibrium_inputs/{line_digest}.json'
                self._write_content_addressed(line_reference,line_payload,line_digest)
                manifest['line_equilibrium_inputs']={'sidecar':line_reference,'sha256':line_digest}
            if project.native_inputs is not None:
                native_payload = json_bytes(project.native_inputs.to_dict())
                native_digest = sha256_bytes(native_payload)
                native_reference = f"native_inputs/{native_digest}.json"
                self._write_content_addressed(native_reference, native_payload, native_digest)
                manifest["native_inputs"] = {"sidecar": native_reference, "sha256": native_digest}
            for body_id, payload in body_payloads:
                digest = sha256_bytes(payload)
                reference = f"bodies/{digest}.json"
                self._write_content_addressed(reference, payload, digest)
                manifest["bodies"][body_id] = {
                    "sidecar": reference,
                    "sha256": digest,
                }
            for result_id, payload in result_payloads:
                digest = sha256_bytes(payload)
                reference = f"results/{digest}.json"
                self._write_content_addressed(reference, payload, digest)
                manifest["results"].append(
                    {
                        "result_id": result_id,
                        "sidecar": reference,
                        "sha256": digest,
                    }
                )
            # Single atomic commit: until this swap succeeds, the old manifest
            # still describes the old sidecars and the old project loads.
            atomic_write_json(self.root / MANIFEST_NAME, manifest)
        except ProjectError:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise ProjectSaveError(f"failed to save project: {error}") from error

    def _write_content_addressed(self, reference: str, payload: bytes, digest: str) -> None:
        """Write an immutable sidecar whose name is the sha256 of its bytes.

        If the sidecar already exists it must already hold exactly *payload*;
        identical bytes are accepted (idempotent re-save) and any difference
        is refused, so an existing artifact is never overwritten with
        different bytes at the same identity.
        """

        path = resolve_sidecar(self.root, reference)
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as error:
                raise ProjectSaveError(
                    f"cannot verify existing sidecar {reference!r}: {error}"
                ) from error
            if existing != payload:
                raise ProjectSaveError(
                    f"sidecar {reference!r} already exists with different bytes; "
                    "refusing to overwrite an existing artifact"
                )
            return
        atomic_write_bytes(path, payload)

    def _sidecar_path(self, reference: str) -> Path:
        return resolve_sidecar(self.root, reference)

    def store_hydro_artifact(self, payload: bytes, suffix: str = ".nc") -> tuple[str, str]:
        """Store one authoritative raw hydro artifact under ``hydro/``.

        The artifact is content-addressed under ``hydro/<sha256><suffix>``
        (datasets/kernels are ``.nc``; derived-operator artifacts are
        ``.npz``): an existing sidecar with the same identity must already
        hold exactly *payload* (idempotent re-import) and any difference is
        refused. Returns ``(reference, sha256)``.
        """

        if suffix not in (".nc", ".npz"):
            raise ProjectSaveError(
                f"unsupported hydro artifact suffix {suffix!r}; expected '.nc' or '.npz'"
            )
        digest = sha256_bytes(payload)
        reference = f"hydro/{digest}{suffix}"
        self._write_content_addressed(reference, payload, digest)
        return reference, digest

    # ------------------------------------------------------------------ load

    def load(self) -> ProjectModel:
        """Load and fully validate a project; raise without side effects."""

        manifest_path = self.root / MANIFEST_NAME
        try:
            raw = manifest_path.read_bytes()
        except FileNotFoundError as error:
            raise ProjectLoadError(
                f"project manifest not found at {manifest_path}"
            ) from error
        except OSError as error:
            raise ProjectLoadError(f"cannot read {manifest_path}: {error}") from error
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectLoadError(f"manifest is not valid JSON: {error}") from error
        if not isinstance(manifest, dict):
            raise ProjectLoadError("manifest must be a JSON object")
        version = manifest.get("schema_version")
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ProjectLoadError(
                f"unsupported manifest schema_version {version!r}; expected one "
                f"of {SUPPORTED_SCHEMA_VERSIONS}. A newer or older application "
                "must refuse an unknown manifest version rather than silently "
                "dropping intent it does not understand."
            )
        # schema 10 = schema 9 semantics plus rigid_subset_v1 linear inputs (checked after loading)
        rigid_manifest = version == SCHEMA_VERSION_RIGID_LINEAR
        if rigid_manifest:
            version = SCHEMA_VERSION_EQUILIBRIUM_ADOPTION
        if version not in (SCHEMA_VERSION_NATIVE,SCHEMA_VERSION_LINE_EQUILIBRIUM,SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION) and "native_inputs" in manifest:
            raise ProjectLoadError("native intent requires schema_version 5; downgrade is refused")
        if version in (SCHEMA_VERSION_NATIVE,SCHEMA_VERSION_LINE_EQUILIBRIUM):
            if not manifest.get("native_inputs") and not manifest.get("line_equilibrium_inputs") and not manifest.get("results"):
                raise ProjectLoadError("schema_version 5 requires native input or result intent")
            if "linear_inputs" in manifest:
                raise ProjectLoadError("native dry manifests cannot carry linear input intent")
        if version == SCHEMA_VERSION and "linear_inputs" in manifest:
            raise ProjectLoadError(
                "legacy schema_version 2 manifests cannot carry linear "
                "development intent; refusing the manifest instead of silently "
                "ignoring the linear inputs (re-save the project with a current "
                "application to write schema_version 3)"
            )
        if version not in (SCHEMA_VERSION_HYDRO,SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION) and (
            "hydro_provenance" in manifest or "hydro_artifacts" in manifest
        ):
            raise ProjectLoadError(
                f"legacy schema_version {version} manifests cannot carry "
                "hydrodynamic import intent; refusing the manifest instead of "
                "silently ignoring the hydro provenance (re-save the project "
                "with a current application to write schema_version 4)"
            )

        bodies_entry = manifest.get("bodies", {})
        if not isinstance(bodies_entry, dict):
            raise ProjectLoadError("manifest 'bodies' must be a JSON object")
        results_entry = manifest.get("results", [])
        if not isinstance(results_entry, list):
            raise ProjectLoadError("manifest 'results' must be a JSON array")

        # Validate everything into fresh objects before returning anything.
        bodies = []
        body_order = manifest.get("body_order", list(bodies_entry))
        if (not isinstance(body_order, list) or not all(isinstance(key, str) for key in body_order)
                or len(body_order) != len(bodies_entry) or set(body_order) != set(bodies_entry)):
            raise ProjectLoadError("manifest body_order must list each body exactly once")
        for body_id in body_order:
            entry = bodies_entry[body_id]
            data = self._load_sidecar_json(entry, f"body {body_id}")
            sidecar_body_id = data.get("body_id")
            if sidecar_body_id != body_id:
                raise ProjectLoadError(
                    f"body sidecar for manifest key {body_id!r} declares "
                    f"body_id {sidecar_body_id!r}; identity mismatch is refused"
                )
            try:
                from .domain import Body

                if version in (SCHEMA_VERSION_NATIVE,SCHEMA_VERSION_LINE_EQUILIBRIUM):
                    from .native_inputs import _number
                    _number(data["mass"], "native stored body mass", positive=True)
                if 'hydro_surfaces' in data and version not in (SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION):
                    raise ValueError('surface intent requires schema_version 7; downgrade refused')
                if 'hydro_lid_policy' in data and version not in (SCHEMA_VERSION_LID_POLICY, SCHEMA_VERSION_EQUILIBRIUM_ADOPTION):
                    raise ValueError('explicit lid policy requires schema_version 8; downgrade refused')
                bodies.append(Body.from_dict(data))
            except (KeyError, ValueError, TypeError, OverflowError) as error:
                raise ProjectLoadError(f"body {body_id} sidecar is invalid: {error}") from error

        results = []
        for entry in results_entry:
            if not isinstance(entry, dict):
                raise ProjectLoadError("manifest 'results' entries must be JSON objects")
            result_id = entry.get("result_id")
            data = self._load_sidecar_json(entry, f"result {result_id}")
            sidecar_result_id = data.get("result_id")
            if sidecar_result_id != result_id:
                raise ProjectLoadError(
                    f"result sidecar for manifest entry {result_id!r} declares "
                    f"result_id {sidecar_result_id!r}; identity mismatch is refused"
                )
            try:
                results.append(ResultRecord.from_dict(data))
            except (KeyError, ValueError, TypeError) as error:
                raise ProjectLoadError(
                    f"result {entry.get('result_id')} sidecar is invalid: {error}"
                ) from error

        # Optional linear development inputs sidecar. The manifest key is
        # absent for legacy (synthetic-only) projects, which keep loading
        # unchanged; schema_version stays 2 for the same reason.
        linear_inputs = None
        linear_entry = manifest.get("linear_inputs")
        if linear_entry is not None:
            data = self._load_sidecar_json(linear_entry, "linear inputs")
            try:
                from .domain import LinearDevelopmentInputs

                linear_inputs = LinearDevelopmentInputs.from_dict(data)
            except (KeyError, ValueError, TypeError) as error:
                raise ProjectLoadError(
                    f"linear inputs sidecar is invalid: {error}"
                ) from error
            # Hydro-imported inputs may only live in a schema_version 4
            # hydro-intent manifest with structured hydro provenance. A
            # manifest that claims an older schema while storing hydro-source
            # inputs has had its hydrodynamic intent stripped and is refused
            # instead of silently loading as a plain linear project.
            if version != SCHEMA_VERSION_HYDRO and not (version in (SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION) and manifest.get("hydro_provenance")):
                from .domain import LINEAR_SOURCE_HYDRO

                if linear_inputs.provenance.source == LINEAR_SOURCE_HYDRO:
                    raise ProjectLoadError(
                        "the stored linear inputs declare an ANYhydro dataset "
                        f"import ({LINEAR_SOURCE_HYDRO!r}) but the manifest is "
                        f"schema_version {version} without hydrodynamic intent; "
                        "stripped hydrodynamic intent is refused"
                    )

        # Optional hydrodynamic import provenance. Version 4 manifests must
        # carry it (with their authoritative raw dataset/kernel artifacts);
        # older manifests must not (checked above).
        hydro_provenance = None
        if version == SCHEMA_VERSION_HYDRO or (version in (SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION) and ("hydro_provenance" in manifest or "hydro_artifacts" in manifest)):
            hydro_entry = manifest.get("hydro_provenance")
            if hydro_entry is None:
                raise ProjectLoadError(
                    "schema_version 4 manifests must carry hydro_provenance; a "
                    "hydro-intent project without provenance is refused"
                )
            data = self._load_sidecar_json(hydro_entry, "hydro provenance")
            try:
                from .domain import HydroBridgeProvenance

                hydro_provenance = HydroBridgeProvenance.from_dict(data)
            except (KeyError, ValueError, TypeError) as error:
                raise ProjectLoadError(
                    f"hydro provenance sidecar is invalid: {error}"
                ) from error
            artifacts_entry = manifest.get("hydro_artifacts")
            if not isinstance(artifacts_entry, list) or not artifacts_entry:
                raise ProjectLoadError(
                    "schema_version 4 manifests must list their authoritative "
                    "hydro artifacts (dataset and kernel sidecars)"
                )
            # Strict role registry: the raw route lists exactly the dataset
            # and kernel roles; the derived route additionally lists the
            # derived operators artifact. An operators role without a
            # provenance derived bundle (stripped intent) or a derived
            # bundle without its operators role is refused.
            bundle = hydro_provenance.derived_bundle
            expected_roles = set(
                HYDRO_DERIVED_ARTIFACT_ROLES if bundle is not None else HYDRO_ARTIFACT_ROLES
            )
            seen_roles = set()
            for entry in artifacts_entry:
                if not isinstance(entry, dict):
                    raise ProjectLoadError("hydro_artifacts entries must be objects")
                role = entry.get("role")
                if role not in expected_roles or role in seen_roles:
                    raise ProjectLoadError(
                        f"hydro_artifacts must list each role {sorted(expected_roles)} "
                        "exactly once"
                    )
                seen_roles.add(role)
                reference = entry.get("sidecar")
                expected_digest = entry.get("sha256")
                if not isinstance(reference, str) or not isinstance(expected_digest, str):
                    raise ProjectLoadError(
                        "hydro_artifacts entries need 'sidecar' and 'sha256'"
                    )
                path = resolve_sidecar(self.root, reference)
                try:
                    raw = path.read_bytes()
                except FileNotFoundError as error:
                    raise ProjectLoadError(
                        f"hydro artifact {reference!r} is missing (looked for {path})"
                    ) from error
                except OSError as error:
                    raise ProjectLoadError(
                        f"cannot read hydro artifact {reference!r}: {error}"
                    ) from error
                actual = sha256_bytes(raw)
                if actual != expected_digest:
                    raise ProjectLoadError(
                        f"hydro artifact {reference!r} is corrupt: sha256 mismatch "
                        f"(manifest {expected_digest[:12]}..., file {actual[:12]}...)"
                    )
                if role == "dataset":
                    if reference != hydro_provenance.dataset_sidecar or expected_digest != hydro_provenance.dataset_raw_sha256:
                        raise ProjectLoadError(
                            "the dataset hydro artifact does not match the hydro "
                            "provenance record; identity mismatch is refused"
                        )
                elif role == "kernel":
                    if reference != hydro_provenance.kernel_sidecar or expected_digest != hydro_provenance.kernel_raw_sha256:
                        raise ProjectLoadError(
                            "the kernel hydro artifact does not match the hydro "
                            "provenance record; identity mismatch is refused"
                        )
                else:
                    if reference != bundle["operators_sidecar"] or expected_digest != bundle["operators_raw_sha256"]:
                        raise ProjectLoadError(
                            "the operators hydro artifact does not match the hydro "
                            "provenance derived bundle; identity mismatch is refused"
                        )
            if seen_roles != expected_roles:
                raise ProjectLoadError(
                    f"hydro_artifacts must list each role {sorted(expected_roles)} "
                    "exactly once"
                )
            if linear_inputs is None:
                raise ProjectLoadError(
                    "a hydro-intent project must also store the linear inputs the "
                    "provenance describes"
                )
            from .linear import linear_input_hash

            if linear_input_hash(linear_inputs) != hydro_provenance.linear_input_hash:
                raise ProjectLoadError(
                    "the stored linear inputs do not reproduce the hydro "
                    "provenance's declared input hash; the hydro intent is "
                    "inconsistent and refused"
                )

        native_records = any(result.kind in (RESULT_KIND_MECHANICAL,RESULT_KIND_EQUILIBRIUM,'line_equilibrium_development') for result in results)
        if native_records and version not in (SCHEMA_VERSION_NATIVE,SCHEMA_VERSION_LINE_EQUILIBRIUM,SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION):
            raise ProjectLoadError("native result intent requires schema_version 5")
        if version in (SCHEMA_VERSION_NATIVE,SCHEMA_VERSION_LINE_EQUILIBRIUM) and manifest.get("native_inputs") is None and manifest.get("line_equilibrium_inputs") is None and not native_records:
            raise ProjectLoadError("schema_version 5 has no native input or result intent")
        line_records=any(r.kind=='line_equilibrium_development' for r in results)
        if version not in (SCHEMA_VERSION_LINE_EQUILIBRIUM,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION) and (line_records or 'line_equilibrium_inputs' in manifest):
            raise ProjectLoadError('line equilibrium intent/results require schema_version6; downgrade refused')
        if version==SCHEMA_VERSION_LINE_EQUILIBRIUM and not line_records and not manifest.get('line_equilibrium_inputs'):
            raise ProjectLoadError('schema_version6 requires line equilibrium intent or results')
        line_equilibrium_inputs=None
        if manifest.get('line_equilibrium_inputs') is not None:
            try:
                from .line_equilibrium import LineEquilibriumInputs
                line_equilibrium_inputs=LineEquilibriumInputs.from_dict(self._load_sidecar_json(manifest['line_equilibrium_inputs'],'line equilibrium inputs'))
            except (KeyError,ValueError,TypeError) as error:
                raise ProjectLoadError(f'line equilibrium input sidecar invalid: {error}') from error
        native_inputs = None
        if version in (SCHEMA_VERSION_NATIVE,SCHEMA_VERSION_LINE_EQUILIBRIUM,SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION) and manifest.get("native_inputs") is not None:
            data = self._load_sidecar_json(manifest["native_inputs"], "native inputs")
            try:
                from .native_inputs import NativeMechanicalInputs
                native_inputs = NativeMechanicalInputs.from_dict(data)
            except (KeyError, ValueError, TypeError) as error:
                raise ProjectLoadError(f"native inputs sidecar is invalid: {error}") from error
        try:
            from .domain import AnalysisSettings, EquilibriumMetadata

            project = ProjectModel(
                name=str(manifest["name"]),
                bodies=tuple(bodies),
                equilibrium=EquilibriumMetadata.from_dict(
                    manifest.get("equilibrium", {}) or {}
                ),
                settings=AnalysisSettings.from_dict(manifest.get("settings", {}) or {}),
                results=tuple(results),
                linear_inputs=linear_inputs,
                hydro_provenance=hydro_provenance,
                native_inputs=native_inputs,
                line_equilibrium_inputs=line_equilibrium_inputs,
                equilibrium_adoption=manifest.get("equilibrium_adoption"),
            )
        except (KeyError, ValueError, TypeError, OverflowError) as error:
            raise ProjectLoadError(f"manifest is invalid: {error}") from error
        intent = has_surface_intent(project)
        if intent and version not in (SCHEMA_VERSION_SURFACES,SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION):
            raise ProjectLoadError('retained surface intent requires schema_version 7; downgrade refused')
        if version == SCHEMA_VERSION_SURFACES and not intent:
            raise ProjectLoadError('schema_version 7 requires current or retained surface intent')
        lid_intent = has_lid_policy_intent(project)
        if lid_intent and version not in (SCHEMA_VERSION_LID_POLICY,SCHEMA_VERSION_EQUILIBRIUM_ADOPTION):
            raise ProjectLoadError('retained lid policy requires schema_version 8; downgrade refused')
        if version == SCHEMA_VERSION_LID_POLICY and not lid_intent:
            raise ProjectLoadError('schema_version 8 requires current or retained lid policy')
        adoption_intent=has_adoption_intent(project)
        if adoption_intent and version != SCHEMA_VERSION_EQUILIBRIUM_ADOPTION:
            raise ProjectLoadError("reference orientation/adoption in current or retained intent requires schema_version 9; downgrade refused")
        if version == SCHEMA_VERSION_EQUILIBRIUM_ADOPTION and not adoption_intent and not rigid_manifest:
            raise ProjectLoadError("schema_version 9 requires current or retained adoption/orientation intent")
        if has_rigid_linear_intent(project) != rigid_manifest:
            raise ProjectLoadError("rigid_subset_v1 linear inputs require schema_version 10 and schema_version 10 "
                                   "requires them; downgrade or reinterpretation is refused")
        return project

    def _load_sidecar_json(self, entry: dict, label: str) -> dict:
        if not isinstance(entry, dict):
            raise ProjectLoadError(f"{label} manifest entry must be an object")
        reference = entry.get("sidecar")
        expected = entry.get("sha256")
        if not isinstance(reference, str) or not isinstance(expected, str):
            raise ProjectLoadError(f"{label} manifest entry needs 'sidecar' and 'sha256'")
        path = resolve_sidecar(self.root, reference)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as error:
            raise ProjectLoadError(
                f"{label} sidecar {reference!r} is missing (looked for {path})"
            ) from error
        except OSError as error:
            raise ProjectLoadError(f"cannot read {label} sidecar {reference!r}: {error}") from error
        actual = sha256_bytes(raw)
        if actual != expected:
            raise ProjectLoadError(
                f"{label} sidecar {reference!r} is corrupt: sha256 mismatch "
                f"(manifest {expected[:12]}..., file {actual[:12]}...)"
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProjectLoadError(f"{label} sidecar {reference!r} is not valid JSON: {error}") from error
        if not isinstance(data, dict):
            raise ProjectLoadError(f"{label} sidecar {reference!r} must be a JSON object")
        return data

    # ------------------------------------------------------------- hydro refs

    def verify_hydro_reference(self, reference: str) -> Path:
        """Validate a generic hydrodynamic sidecar reference (no ANYhydro import).

        The reference must be relative, safe, exist and be a readable file.
        """

        path = resolve_sidecar(self.root, reference)
        if not path.is_file():
            raise ProjectLoadError(f"hydro sidecar {reference!r} not found at {path}")
        return path
