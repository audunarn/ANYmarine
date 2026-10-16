"""Shared Qt-free application services used by both the CLI and the GUI.

Nothing here imports Qt. GUI and command-line entry points call the same
service layer so project semantics cannot drift between interfaces.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path

from . import RESULT_KIND_LINEAR, RESULT_KIND_SYNTHETIC, RESULT_KIND_MECHANICAL, RESULT_KIND_EQUILIBRIUM
from .domain import (
    AnalysisSettings,
    HydroBridgeProvenance,
    LinearDevelopmentInputs,
    LINEAR_LAYOUT_RIGID,
    LINEAR_SOURCE_HYDRO,
    HYDRO_ROUTE_DERIVED,
    ProjectModel,
    ResultRecord,
    _jsonable_value,
    default_two_body_project,
)
from .hashing import input_hash, is_stale
from .hydro import (
    HydroImportError,
    import_derived_hydro_inputs as _hydro_derived_import,
    import_hydro_inputs as _hydro_import,
    recompile_derived_inputs as _hydro_derived_recompile,
)
from .linear import (
    LinearBindingError,
    LinearError,
    LinearResultIncomplete,
    analytic_demo_inputs,
    linear_input_hash,
    linear_result_stale,
    run_linear,
    verify_linear_binding,
    verify_linear_result,
)
from .linear_assessment import (
    AssessmentCase,
    AssessmentCaseError,
    AssessmentError,
    AssessmentReport,
    AssessmentStore,
    adjudicate_report as _assessment_adjudicate,
    assess_result as _assessment_assess,
    prepare_case as _assessment_prepare,
    read_case_artifact as _read_case_artifact,
    read_report_artifact as _read_report_artifact,
)
from .persistence import ProjectStore, UnsafePathError, resolve_sidecar, sha256_bytes
from .synthetic import generate_synthetic_replay

JOB_INPUT_SCHEMA_VERSION = 1


class ServiceError(Exception):
    """Actionable service-layer error."""


@dataclass(frozen=True)
class ProducingDatasetMetadata:
    """Read-only producing-dataset display metadata; never a qualification.

    ``available`` is true only when the exact content-addressed dataset
    sidecar under the active project root was read, matched the expected
    raw sha256 and loaded through the public neutral loader. Attributes the
    producing dataset does not store stay ``None`` and must be displayed as
    unavailable; ``diagnostic`` carries the actionable reason whenever
    ``available`` is false.
    """

    available: bool
    diagnostic: str = ""
    pressure_jump_convention: str | None = None
    source_capability_status: str | None = None
    sheet_source_backend: str | None = None
    pressure_jump_contract_sign: str | None = None
    #: Identity-verified ``excitation_source_contract`` (version, conventions,
    #: backend and dataset binding checked by ANYhydro); ``None`` when absent
    #: or refused, with the reason in ``excitation_source_diagnostic``.
    excitation_source_contract: Mapping | None = None
    excitation_source_diagnostic: str = ""


def _verified_declaration(neutral, dataset) -> tuple[Mapping | None, str]:
    """Identity-verified excitation-source declaration, or (None, reason)."""

    if dataset.attrs.get("excitation_source_contract") is None:
        return None, ""
    verifier = getattr(neutral, "verify_excitation_source_declaration", None)
    if verifier is None:
        return None, (
            "the installed ANYhydro cannot verify excitation-source declarations; "
            "install the tested wheel"
        )
    try:
        contract, _request = verifier(dataset)
    except Exception as error:  # refused declaration: report, never use
        return None, f"the producing dataset's excitation-source declaration is refused: {error}"
    return contract, ""


def _stored_json_attr(value) -> Mapping | None:
    """Decode one stored dataset attribute without inventing content."""

    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return None
        return decoded if isinstance(decoded, Mapping) else None
    return None


class ApplicationService:
    """Project lifecycle, synthetic runs, job snapshots and result catalog."""

    def __init__(self, root: os.PathLike | str | None = None):
        self.root = Path(root) if root is not None else None
        self.store = ProjectStore(self.root) if self.root is not None else None

    def prepare_w5_case(self, spec: dict, out_dir):
        """Register the retained headless W5 route; no backend is executed."""
        from .w5_workflow import prepare_w5_case, W5Error
        try:
            return prepare_w5_case(spec, out_dir)
        except W5Error as error:
            raise ServiceError(str(error)) from error

    def execute_w5_case(self, out_dir, *, backend, execution_limits, should_cancel=None):
        """Explicit headless route; Qt callers must use an isolated supervised process."""
        from .w5_workflow import execute_w5_case, W5Error
        try:
            return execute_w5_case(out_dir, backend=backend, execution_limits=execution_limits,
                                   should_cancel=should_cancel)
        except W5Error as error:
            raise ServiceError(str(error)) from error

    # ------------------------------------------------------------- lifecycle

    def new_project(self, name: str) -> ProjectModel:
        project = default_two_body_project(name)
        self.root = None
        self.store = None
        return project

    def prepare_rigid_body(self, project: ProjectModel, body_id: str):
        """Prepare explicit six-DOF dry mechanics; does not run dynamics."""
        from .mechanics import prepare_rigid_body
        return prepare_rigid_body(project, body_id)

    def prepare_mechanical_model(self, project: ProjectModel, *, links=(), gravity_world=None):
        """Compile native dry/link inputs through the optional public solver."""
        from .mechanics import prepare_mechanical_model
        return prepare_mechanical_model(project, links=links, gravity_world=gravity_world)

    def solve_native_mechanics(self, project: ProjectModel, initial_states, run_spec, **options):
        """Synchronous headless service; GUI callers must use an isolated process."""
        from .mechanics import solve_native_mechanics
        return solve_native_mechanics(project, initial_states, run_spec, **options)

    def prepare_native_run(self, project: ProjectModel):
        """Compile saved native intent without executing numerical work."""
        from .mechanics import prepare_native_run
        return prepare_native_run(project)

    def load_native_mooring_example(self):
        from .native_examples import mooring_two_body_project
        return mooring_two_body_project()

    def load_native_line_hydro_example(self):
        from .native_examples import mooring_hydrodynamic_project
        self.root=None;self.store=None
        return mooring_hydrodynamic_project()

    def load_native_contact_example(self):
        from .native_examples import mooring_contact_project
        self.root=None;self.store=None
        return mooring_contact_project()

    def solve_configured_native_mechanics(self, project: ProjectModel, **options):
        """Synchronous headless route; GUI execution requires an isolated job."""
        from .mechanics import solve_configured_native_mechanics
        return solve_configured_native_mechanics(project, **options)

    def adoption_input_snapshot(self,project,result,**options):
        from .adoption_jobs import input_snapshot
        return input_snapshot(project,result,**options)

    def adopt_equilibrium(self, project, result, *, domain, regenerate=None, should_cancel=None):
        """Return an atomic adopted development project; never save or launch a job."""
        from .equilibrium_transition import adopt_equilibrium
        return adopt_equilibrium(project,result,domain=domain,regenerate=regenerate,should_cancel=should_cancel)

    def equilibrium_input_snapshot(self,project,**options):
        from .equilibrium_jobs import input_snapshot
        return input_snapshot(project,**options)

    def load_equilibrium_example(self):
        from .native_examples import equilibrium_project
        return equilibrium_project()

    def native_input_snapshot(self, project: ProjectModel, *, max_frames=1001, wall_seconds=60.) -> dict:
        """Detached, versioned native intent for the subsequent worker route.

        Does not import a solver or execute a job. Numerical controls are
        project owned; results are excluded. Execution limits are explicit
        and separate from scientific qualification criteria.
        """
        if not isinstance(project, ProjectModel) or project.native_inputs is None:
            raise ServiceError("project has no declared native mechanical inputs")
        from .hashing import canonical_json
        from .native_jobs import execution_limits
        limits = execution_limits(max_frames, wall_seconds)
        if project.native_inputs.run_spec['steps'] + 1 > limits['max_frames']:
            raise ServiceError("native run exceeds its declared frame limit")
        payload = project.to_dict()
        payload['results'] = []
        snapshot = {'schema_version': JOB_INPUT_SCHEMA_VERSION,
                    'kind': 'mechanical_development', 'solver_api': 'marine_mechanical_v1',
                    'input_hash': input_hash(project), 'project': payload,
                    'execution_limits': limits}
        return json.loads(canonical_json(snapshot))

    def load_native_inputs(self, project: ProjectModel, data: dict) -> ProjectModel:
        """Atomic input admission through the shared public compiler; no solve."""
        from .native_inputs import NativeMechanicalInputs
        from .mechanics import prepare_native_run
        try:
            candidate = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
            prepare_native_run(candidate)
        except (TypeError, ValueError) as error:
            raise ServiceError(f"native inputs refused: {error}") from error
        return candidate

    def load_native_example(self) -> ProjectModel:
        """Load declared dry example inputs; no solver imports or job execution."""
        from .native_examples import dry_two_body_project
        self.root = None
        self.store = None
        return dry_two_body_project()

    def load_native_fluid_example(self) -> ProjectModel:
        """Load declared development fluid inputs, without integration/backend imports."""
        from .native_examples import fluid_two_body_project
        project = fluid_two_body_project()
        self.root = None
        self.store = None
        return project

    def load_native_morison_example(self) -> ProjectModel:
        """Load declared waves/current and members without integration or backend imports."""
        from .native_examples import morison_two_body_project
        project = morison_two_body_project()
        self.root = None
        self.store = None
        return project

    def load_calculated_wave_example(self):
        from .wave_examples import load_calculated_wave_example
        try:project,field=load_calculated_wave_example()
        except (ImportError,OSError,ValueError) as error:
            raise ServiceError('calculated wave example refused: '+str(error)) from error
        self.root=None;self.store=None
        return project,field

    def hams_input_snapshot(self, project, config):
        from .hams_jobs import hams_input_snapshot
        try:return hams_input_snapshot(project, config)
        except Exception as error:raise ServiceError('HAMS field job refused: '+str(error)) from error

    def load_hams_job_output(self, path, snapshot, seal):
        from .hams_jobs import load_hams_output
        try:return load_hams_output(path, snapshot, seal)
        except Exception as error:raise ServiceError('HAMS field output refused: '+str(error)) from error

    def load_hams_wave_bundle(self, directory=None):
        """Admit a portable producing project and stored source-specific waves."""
        from .hams_wave_replay import load_hams_wave_bundle,load_hams_wave_example
        try:record=load_hams_wave_example() if directory is None else load_hams_wave_bundle(directory)
        except (ImportError,OSError,ValueError) as error:
            raise ServiceError('stored HAMS wave bundle refused: '+str(error)) from error
        self.root=None;self.store=None
        return record.project,record

    def save_hams_wave_bundle(self, directory, record):
        from .hams_wave_replay import save_hams_wave_replay
        try:return save_hams_wave_replay(directory,record)
        except (ImportError,OSError,ValueError) as error:
            raise ServiceError('stored HAMS wave bundle save refused: '+str(error)) from error

    def load_native_sea_state_example(self) -> ProjectModel:
        from .native_examples import seeded_wave_two_body_project
        project=seeded_wave_two_body_project()
        self.root=None; self.store=None
        return project

    def replace_native_sea_state(self, project, *, significant_height, peak_period, seed):
        from .sea_state_service import replace_development_sea_state
        try:
            candidate=replace_development_sea_state(project,significant_height=significant_height,
                peak_period=peak_period,seed=seed)
            self.prepare_native_run(candidate)
        except ImportError as error:
            raise ServiceError('install tested shared-wave owner packages') from error
        except ValueError as error:
            raise ServiceError('shared sea state refused: '+str(error)) from error
        return candidate

    def load_plate_example(self) -> ProjectModel:
        from .plate_example import attached_plate_project
        project = attached_plate_project()
        self.root = None
        self.store = None
        return project

    def load_example(self) -> ProjectModel:
        """Load the bundled synthetic replay as an unsaved editable project.

        Stored arrays go through normal checksum and identity validation.
        Detaching the store ensures Save cannot overwrite package resources.
        No numerical job or optional backend is started.
        """
        example_root = Path(__file__).parent / "examples" / "two-body-replay"
        project = ProjectStore(example_root).load()
        self.root = None
        self.store = None
        return project

    def load_regular_wave_example(self) -> ProjectModel:
        """Load two moored cubes with their stored solved regular-wave replay.

        The stored result was produced from exactly these saved inputs
        (tools/build_regular_wave_example.py); loading never solves and
        Save cannot overwrite package resources. Development, unqualified.
        """
        example_root = Path(__file__).parent / "examples" / "regular-wave-replay"
        project = ProjectStore(example_root).load()
        self.root = None
        self.store = None
        return project

    def save(self, project: ProjectModel, root: os.PathLike | str | None = None) -> Path:
        store = ProjectStore(root) if root is not None else self.store
        if store is None:
            raise ServiceError("no project root configured for save")
        if root is not None and (
            self.root is None or Path(root).resolve() != self.root.resolve()
        ):
            # Save-as: the destination must be self-contained, so the
            # authoritative raw hydro sidecars are copied (and checked)
            # from the current project root before the destination manifest
            # is committed.
            self._copy_hydro_artifacts_for_save_as(project, store)
        store.save(project)
        return store.root

    def save_current(self, project: ProjectModel, root: os.PathLike | str | None = None) -> Path:
        """Save the active project and adopt its folder only after success.

        ``save`` remains an export operation: snapshot exports must not change
        the active project. GUI Save uses this explicit lifecycle operation so
        later imports and job results use the successfully saved folder.
        """
        saved_root = self.save(project, root)
        store = ProjectStore(saved_root)
        self.root = store.root
        self.store = store
        return store.root

    def _copy_hydro_artifacts_for_save_as(self, project: ProjectModel, store: ProjectStore) -> None:
        """Copy the authoritative raw hydro artifacts into a save-as root.

        The bytes come from the current project root's content-addressed
        sidecars and are checked against the hydro provenance's raw sha256
        before the destination manifest is written; a missing or corrupt
        sidecar refuses the save instead of publishing a dangling manifest.
        """

        provenance = project.hydro_provenance
        if provenance is None:
            return
        if self.root is None:
            raise ServiceError(
                "cannot save a hydrodynamic project to a different root: the "
                "current project root holding the authoritative dataset and "
                "kernel sidecars is unknown (load the project first)"
            )
        for role, reference, expected in self._hydro_artifact_roles(provenance):
            # Validate the reference before any read: a forged provenance
            # pointing outside the source project is refused here instead
            # of reading (or copying) an external file.
            source = resolve_sidecar(self.root, reference)
            try:
                payload = source.read_bytes()
            except OSError as error:
                raise ServiceError(
                    f"cannot copy the authoritative {role} sidecar "
                    f"{reference!r} from {self.root} for a self-contained "
                    f"save: {error}"
                ) from error
            digest = sha256_bytes(payload)
            if digest != expected:
                raise ServiceError(
                    f"the {role} sidecar {reference!r} does not match the "
                    "hydro provenance raw sha256; refusing the save"
                )
            # The copy must reproduce the exact content-addressed reference,
            # including the artifact-type suffix (datasets/kernels are .nc,
            # derived-operator artifacts are .npz).
            suffix = reference[reference.rfind(".") :]

            stored_reference, stored_digest = store.store_hydro_artifact(
                payload, suffix=suffix
            )
            if stored_reference != reference or stored_digest != expected:
                raise ServiceError(
                    f"the copied {role} artifact does not reproduce its "
                    "content-addressed reference; refusing the save"
                )

    @staticmethod
    def _hydro_artifact_roles(provenance: HydroBridgeProvenance) -> list[tuple[str, str, str]]:
        """The authoritative (role, sidecar reference, raw sha256) triples.

        Legacy raw imports carry exactly the dataset and kernel roles; a
        derived import additionally carries the derived operators artifact.
        """

        roles = [
            ("dataset", provenance.dataset_sidecar, provenance.dataset_raw_sha256),
            ("kernel", provenance.kernel_sidecar, provenance.kernel_raw_sha256),
        ]
        bundle = provenance.derived_bundle
        if bundle is not None:
            roles.append(
                (
                    "operators",
                    bundle["operators_sidecar"],
                    bundle["operators_raw_sha256"],
                )
            )
        return roles

    def producing_dataset_metadata(
        self, project: ProjectModel | None
    ) -> ProducingDatasetMetadata:
        """Public read-only producing-dataset metadata for display.

        Reads the exact content-addressed ``dataset_sidecar`` under the
        active project root (``resolve_sidecar``) and checks it against the
        expected ``dataset_raw_sha256`` before the public neutral loader
        ``load_hydro_dataset`` reads the stored JSON attributes. Never
        falls back to the unverified external ``dataset_source_path``;
        never runs provenance verification, derived reconstruction or any
        solver; never mutates the project. A missing root or sidecar,
        mutated bytes or an absent optional neutral package produce an
        actionable unavailable diagnostic instead of metadata.
        """

        provenance = (
            getattr(project, "hydro_provenance", None)
            if project is not None
            else None
        )
        if provenance is None:
            return ProducingDatasetMetadata(
                False,
                "no bound ANYhydro dataset is stored in this project; import "
                "hydrodynamic inputs to bind a producing dataset",
            )
        if self.root is None:
            return ProducingDatasetMetadata(
                False,
                "the project root holding the authoritative dataset sidecar "
                "is unknown; load or save the project so the stored producing "
                "dataset can be displayed",
            )
        reference = provenance.dataset_sidecar
        try:
            source = resolve_sidecar(self.root, reference)
        except UnsafePathError as error:
            return ProducingDatasetMetadata(
                False,
                f"the stored dataset sidecar reference {reference!r} cannot "
                f"be used under the project root {self.root}: {error}",
            )
        try:
            payload = source.read_bytes()
        except OSError as error:
            return ProducingDatasetMetadata(
                False,
                f"cannot read the authoritative dataset sidecar {reference!r} "
                f"under the project root {self.root}: {error}",
            )
        if sha256_bytes(payload) != provenance.dataset_raw_sha256:
            return ProducingDatasetMetadata(
                False,
                f"the dataset sidecar {reference!r} does not match the stored "
                "raw sha256; the producing dataset is unavailable (re-import "
                "the hydrodynamic inputs to rebind an authoritative dataset)",
            )
        try:
            from .surface_assignments import neutral_api

            import tempfile

            neutral = neutral_api()
            loader = getattr(neutral, "load_hydro_dataset", None)
            if loader is None:
                raise ValueError(
                    "the installed ANYhydro lacks the public load_hydro_dataset "
                    "loader; install the tested wheel"
                )
            # The public loader must read the already hash-verified bytes, not
            # the mutable original sidecar path: an external mutation between
            # the hash check and the load can never change what is validated.
            # The isolated temporary snapshot is removed afterwards; the
            # authoritative project bytes are never modified.
            suffix = Path(reference).suffix or ".nc"
            with tempfile.TemporaryDirectory(prefix="anymarine-dataset-") as folder:
                snapshot = Path(folder) / ("producing-dataset-snapshot" + suffix)
                snapshot.write_bytes(payload)
                dataset = loader(snapshot)
                declaration, declaration_diagnostic = _verified_declaration(neutral, dataset)
        except ValueError as error:
            return ProducingDatasetMetadata(
                False,
                f"the stored producing dataset cannot be displayed: {error}",
            )
        except Exception as error:
            return ProducingDatasetMetadata(
                False,
                f"the stored dataset sidecar {reference!r} failed the public "
                f"neutral load/validation contract: {error}",
            )
        source_provenance = _stored_json_attr(
            dataset.attrs.get("sheet_source_provenance")
        )
        contract = _stored_json_attr(dataset.attrs.get("pressure_jump_contract"))
        return ProducingDatasetMetadata(
            True,
            "",
            pressure_jump_convention=(source_provenance or {}).get(
                "pressure_jump_convention"
            ),
            source_capability_status=(source_provenance or {}).get(
                "source_capability_status"
            ),
            sheet_source_backend=(source_provenance or {}).get("backend"),
            pressure_jump_contract_sign=(contract or {}).get("sign"),
            excitation_source_contract=declaration,
            excitation_source_diagnostic=declaration_diagnostic,
        )

    def result_recorded_wave(self, project: ProjectModel | None, result: ResultRecord):
        """The forcing-wave selection recorded by a linear result's producing inputs.

        The producing inputs are the result's hash-bound surface snapshot, or
        the project's current linear inputs when they reproduce the result's
        input hash. Returns ``(inputs_details, wave)``; ``wave`` is None when
        the producing inputs recorded no wave. Refuses when the producing
        inputs are unavailable.
        """
        from .wave_match import recorded_wave

        if result is None or result.kind != RESULT_KIND_LINEAR:
            raise ServiceError("only linear results record a regular forcing wave")
        if result.surface_snapshot is not None:
            # Hash-bound to result.input_hash by ResultRecord itself.
            inputs = LinearDevelopmentInputs.from_dict(
                _jsonable_value(result.surface_snapshot)["linear_inputs"]
            )
        else:
            inputs = None if project is None else project.linear_inputs
            if inputs is None or linear_input_hash(inputs) != result.input_hash:
                raise ServiceError(
                    "the project's linear inputs are not this result's producing "
                    "inputs and the result stores no producing snapshot; its "
                    "forcing wave cannot be shown"
                )
        details = inputs.provenance.details
        details = None if details is None else _jsonable_value(details)
        try:
            return details, recorded_wave(details)
        except ValueError as error:
            raise ServiceError(str(error)) from error

    def result_incident_wave(self, project: ProjectModel | None, result: ResultRecord):
        """The exact regular wave that forced a stored linear result.

        Uses the recorded wave of the result's producing inputs
        (:meth:`result_recorded_wave`). Depth and gravity come from the
        identity-verified excitation-source declaration of the project's bound
        dataset, read from the hash-verified sidecar, which must be the
        producing dataset. Display only; refuses rather than guesses.
        """
        from .wave_match import RegularIncidentWave, incident_request

        details, wave = self.result_recorded_wave(project, result)
        if wave is None:
            raise ServiceError("this linear result was not forced by an incident wave")
        provenance = None if project is None else project.hydro_provenance
        if provenance is None or provenance.dataset_content_hash != details.get(
            "dataset_content_hash"
        ):
            raise ServiceError(
                "the project's bound hydrodynamic dataset is not this result's "
                "producing dataset; its forcing-wave depth cannot be read"
            )
        metadata = self.producing_dataset_metadata(project)
        if not metadata.available:
            raise ServiceError(metadata.diagnostic)
        if metadata.excitation_source_contract is None:
            raise ServiceError(
                metadata.excitation_source_diagnostic
                or "the producing dataset declares no excitation-source request; "
                "its water depth and gravity are unknown"
            )
        try:
            depth, gravity = incident_request(metadata.excitation_source_contract)
            return RegularIncidentWave(
                amplitude=wave["amplitude"], omega=wave["omega"],
                heading=wave["heading"], phase=wave["phase"],
                water_depth=depth, gravity=gravity,
            )
        except (TypeError, ValueError) as error:
            raise ServiceError(f"the forcing wave cannot be reconstructed: {error}") from error

    def load(self, root: os.PathLike | str) -> ProjectModel:
        store = ProjectStore(root)
        project = store.load()
        previous_root, previous_store = self.root, self.store
        self.root = store.root
        self.store = store
        # Provenance validation needs the candidate's artifact folder. Keep
        # the existing validation and restore the active binding on any
        # failure, including interruption, before returning control to callers.
        try:
            self.verify_hydro_provenance(project)
        except BaseException:
            self.root, self.store = previous_root, previous_store
            raise
        return project

    # ---------------------------------------------------------------- hashes

    def input_hash(self, project: ProjectModel) -> str:
        return input_hash(project)

    def import_line_equilibrium_evidence(self, source_input, source_accepted, source_candidate, *, case_id):
        from .line_equilibrium import import_evidence
        return import_evidence(source_input,source_accepted,source_candidate,case_id=case_id)

    def result_stale(self, project: ProjectModel, result: ResultRecord) -> bool:
        if result.kind=='equilibrium_development':
            from .equilibrium_jobs import stale
            return stale(project,result)
        if result.kind == RESULT_KIND_LINEAR:
            return linear_result_stale(project, result.input_hash)
        return is_stale(project, result.input_hash)

    def stale_result_ids(self, project: ProjectModel) -> tuple[str, ...]:
        return tuple(
            result.result_id for result in project.results if self.result_stale(project, result)
        )

    # -------------------------------------------------------- synthetic runs

    def run_synthetic_local(
        self,
        project: ProjectModel,
        settings: AnalysisSettings | None = None,
    ) -> ResultRecord:
        """Run the synthetic replay in-process (headless path, no child job).

        The result's input hash covers the *effective* settings: when
        *settings* overrides the project's stored settings, the hash identifies
        the inputs that actually produced this result.
        """

        effective = settings if settings is not None else project.settings
        return generate_synthetic_replay(
            project,
            settings=effective,
            input_hash=input_hash(project, effective),
        )

    def add_result(self, project: ProjectModel, result: ResultRecord) -> ProjectModel:
        """Return a new project with the immutable result attached."""

        if result.result_id in {existing.result_id for existing in project.results}:
            raise ServiceError(f"result {result.result_id} already exists in the project")
        if result.kind not in (RESULT_KIND_SYNTHETIC, RESULT_KIND_LINEAR, RESULT_KIND_MECHANICAL, RESULT_KIND_EQUILIBRIUM):
            raise ServiceError(f"unsupported result kind {result.kind!r}")
        return project.with_edits(results=project.results + (result,))

    # ------------------------------------------------------ linear development

    @staticmethod
    def _require_linear_route(project: ProjectModel) -> None:
        if project.native_inputs is not None:
            raise ServiceError("configured native dry inputs cannot coexist with linear or hydrodynamic routes")

    def linear_demo_inputs(self, project: ProjectModel) -> LinearDevelopmentInputs:
        """Build the labelled analytic linear demo inputs for this project."""

        self._require_linear_route(project)
        try:
            return analytic_demo_inputs(project)
        except LinearBindingError as error:
            raise ServiceError(str(error)) from error

    def load_explicit_linear_inputs(
        self, project: ProjectModel, data: dict
    ) -> LinearDevelopmentInputs:
        """Admit explicitly supplied matrices/kernel/forcing inputs.

        *data* is the plain-dict form of ``LinearDevelopmentInputs`` (full
        coupled Mdry/Ainf/C/K, sampled causal kernel and grid, forcing,
        q0/v0, provenance). The body binding must match the current project
        bodies exactly; a stale binding is refused, never rebound.
        """

        self._require_linear_route(project)
        try:
            inputs = LinearDevelopmentInputs.from_dict(data)
        except (KeyError, ValueError, TypeError) as error:
            raise ServiceError(f"invalid explicit linear inputs: {error}") from error
        if inputs.provenance.source == LINEAR_SOURCE_HYDRO:
            raise ServiceError(
                "hydrodynamic linear inputs may only be admitted through the "
                "verified hydro import (import_hydro_inputs); the explicit "
                "load path refuses them"
            )
        self._verify_linear_binding(project, inputs)
        return inputs

    def set_linear_inputs(
        self, project: ProjectModel, inputs: LinearDevelopmentInputs
    ) -> ProjectModel:
        """Return a new project with the immutable linear inputs stored.

        Hydro-sourced inputs are refused: they may only be admitted through
        the verified hydro import (import_hydro_inputs), which attaches the
        structured hydro provenance record. Any other stored inputs clear
        the old hydro provenance, so it can never describe inputs it did
        not produce.
        """

        self._require_linear_route(project)
        if inputs.provenance.source == LINEAR_SOURCE_HYDRO:
            raise ServiceError(
                "hydrodynamic linear inputs may only be admitted through the "
                "verified hydro import (import_hydro_inputs); set_linear_inputs "
                "refuses them so the hydro intent cannot be stripped"
            )
        self._verify_linear_binding(project, inputs)
        return project.with_edits(linear_inputs=inputs, hydro_provenance=None)

    # ------------------------------------------------------ hydro import

    @staticmethod
    def _require_legacy_linear_layout(project: ProjectModel, what: str) -> None:
        """Never silently replace rigid_subset_v1 inputs with a legacy import (ADR-015 section 7)."""
        inputs = project.linear_inputs
        if inputs is not None and inputs.layout != "two_body_surge_heave_v1":
            raise ServiceError(
                f"{what} builds legacy two-body surge/heave inputs and would replace the project's "
                f"{inputs.layout} inputs; it is not available for six-DOF layouts yet (ADR-015 section 7) - "
                "clear the linear inputs first")

    def import_hydro_inputs(
        self,
        project: ProjectModel,
        dataset_path: os.PathLike | str,
        kernel_path: os.PathLike | str,
        options: dict,
    ) -> ProjectModel:
        """Admit an ANYhydro dataset/kernel pair as stored linear inputs.

        Shared preparation/admission method for the CLI and the Qt shell.
        Everything is validated before the project changes: geometry proof,
        exact identity mapping, kernel/dataset binding, caller reconstruction
        criteria, diagnostics, grid compatibility and forcing options. The
        authoritative raw dataset/kernel files are copied into the project as
        content-addressed sidecars so the saved project reproduces without
        the original external paths. Returns the new project; the caller
        adopts (and saves) it explicitly.
        """

        self._require_legacy_linear_layout(project, "the hydrodynamic import")
        self._require_linear_route(project)
        if self.root is None:
            raise ServiceError(
                "the hydrodynamic import requires a project root: open or save "
                "the project first, because the authoritative dataset/kernel "
                "files are copied into the project"
            )
        try:
            inputs, provenance_data = _hydro_import(
                project, dataset_path, kernel_path, options
            )
        except HydroImportError as error:
            raise ServiceError(str(error)) from error
        # Copy the authoritative raw artifacts into the project before the
        # provenance record is adopted; the content-addressed reference is
        # the raw sha256, so it must agree with the provenance record.
        store = ProjectStore(self.root)
        for source, expected_reference, expected_digest in (
            (dataset_path, provenance_data["dataset_sidecar"],
             provenance_data["dataset_raw_sha256"]),
            (kernel_path, provenance_data["kernel_sidecar"],
             provenance_data["kernel_raw_sha256"]),
        ):
            try:
                payload = Path(source).read_bytes()
            except OSError as error:
                raise ServiceError(
                    f"cannot read the hydrodynamic source file {source}: {error}"
                ) from error
            reference, digest = store.store_hydro_artifact(payload)
            if reference != expected_reference or digest != expected_digest:
                raise ServiceError(
                    f"the hydrodynamic source file {source} changed while the "
                    "import was running; refusing inconsistent provenance"
                )
        provenance = HydroBridgeProvenance.from_dict(provenance_data)
        new_project = project.with_edits(
            linear_inputs=inputs, hydro_provenance=provenance
        )
        self._verify_linear_binding(new_project, inputs)
        self.verify_hydro_provenance(new_project)
        return new_project

    def import_rigid_hydro_inputs(
        self,
        project: ProjectModel,
        dataset_path: os.PathLike | str,
        kernel_path: os.PathLike | str,
        options: dict,
    ) -> ProjectModel:
        """Admit an ANYhydro dataset/kernel pair as stored rigid_subset_v1 inputs (ADR-015 L6-3).

        Everything is validated by ``anymarine.hydro_rigid.import_rigid_hydro_inputs`` before the project changes.
        The authoritative raw dataset/kernel files are copied into the project as content-addressed sidecars and the
        structured hydro provenance (with its rigid bundle: route, layout, format and the plain caller options) is
        attached; verification re-runs the import from those sidecars. Legacy two-body inputs are never silently
        replaced (ADR-015 section 7): clear them first. Returns the new project; the caller adopts it explicitly.
        """

        from .hydro_rigid import import_rigid_hydro_inputs as _rigid_import

        inputs = project.linear_inputs
        if inputs is not None and inputs.layout != LINEAR_LAYOUT_RIGID:
            raise ServiceError(
                "the six-DOF hydrodynamic import would replace the project's legacy two-body inputs; clear the "
                "linear inputs first (ADR-015 section 7)")
        self._require_linear_route(project)
        if self.root is None:
            raise ServiceError(
                "the hydrodynamic import requires a project root: open or save the project first, because the "
                "authoritative dataset/kernel files are copied into the project")
        try:
            inputs, provenance_data = _rigid_import(project, dataset_path, kernel_path, options)
        except HydroImportError as error:
            raise ServiceError(str(error)) from error
        store = ProjectStore(self.root)
        for source, expected_reference, expected_digest in (
            (dataset_path, provenance_data["dataset_sidecar"], provenance_data["dataset_raw_sha256"]),
            (kernel_path, provenance_data["kernel_sidecar"], provenance_data["kernel_raw_sha256"]),
        ):
            try:
                payload = Path(source).read_bytes()
            except OSError as error:
                raise ServiceError(f"cannot read the hydrodynamic source file {source}: {error}") from error
            reference, digest = store.store_hydro_artifact(payload)
            if reference != expected_reference or digest != expected_digest:
                raise ServiceError(
                    f"the hydrodynamic source file {source} changed while the import was running; refusing "
                    "inconsistent provenance")
        try:
            provenance = HydroBridgeProvenance.from_dict(provenance_data)
            new_project = project.with_edits(linear_inputs=inputs, hydro_provenance=provenance)
        except ValueError as error:
            raise ServiceError(f"the rigid hydro import produced an inadmissible record: {error}") from error
        self._verify_linear_binding(new_project, inputs)
        self.verify_hydro_provenance(new_project)
        return new_project

    def _verify_rigid_reimport(self, project: ProjectModel, provenance: HydroBridgeProvenance) -> None:
        """Re-run the rigid import from the stored sidecars under the record's options; it must reproduce the
        stored inputs (full input hash) and every source-derived provenance field."""

        from .hydro_rigid import import_rigid_hydro_inputs as _rigid_import
        import shutil
        import tempfile

        inputs = project.linear_inputs
        if inputs.layout != LINEAR_LAYOUT_RIGID:
            raise ServiceError("a rigid hydro provenance may only accompany rigid_subset_v1 inputs")
        if linear_input_hash(inputs) != provenance.linear_input_hash:
            raise ServiceError(
                "the stored linear inputs do not reproduce the hydro provenance's declared input hash; the hydro "
                "intent is stale")
        if self.root is None:
            raise ServiceError(
                "a rigid hydro provenance can only be verified against the project root holding the authoritative "
                "dataset/kernel sidecars; no project root is configured")
        payloads = {}
        for role, reference, expected in self._hydro_artifact_roles(provenance):
            try:
                payload = resolve_sidecar(self.root, reference).read_bytes()
            except (OSError, UnsafePathError) as error:
                raise ServiceError(f"cannot read the authoritative {role} sidecar {reference!r}: {error}") from error
            if sha256_bytes(payload) != expected:
                raise ServiceError(
                    f"the {role} sidecar {reference!r} does not match the hydro provenance raw sha256; the "
                    "authoritative source evidence is inconsistent and the project is refused")
            payloads[role] = payload
        try:
            temp_dir = tempfile.mkdtemp(prefix="anymarine-rigid-verify-")
        except OSError as error:
            raise ServiceError(f"cannot create the private copies for the rigid source proof: {error}") from error
        try:
            dataset_copy, kernel_copy = Path(temp_dir) / "dataset.nc", Path(temp_dir) / "kernel.nc"
            try:
                dataset_copy.write_bytes(payloads["dataset"])
                kernel_copy.write_bytes(payloads["kernel"])
            except OSError as error:
                raise ServiceError(f"cannot write the private copies for the rigid source proof: {error}") from error
            # JSON objects are unordered (the manifest is written with sorted keys): the per-body DOF mapping is
            # replayed in the project body order, which the record's body_ids must equal.
            options = _jsonable_value(provenance.rigid_bundle["options"])
            ids = [body.body_id for body in project.bodies]
            if list(provenance.body_ids) != ids:
                raise ServiceError("the rigid hydro provenance body order is not the project body order")
            dofs = options.get("dofs")
            if isinstance(dofs, dict) and set(dofs) == set(ids):
                options["dofs"] = {body_id: dofs[body_id] for body_id in ids}
            try:
                recompiled, data = _rigid_import(project, dataset_copy, kernel_copy, options)
            except HydroImportError as error:
                raise ServiceError(f"the authoritative rigid sources do not re-verify: {error}") from error
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if linear_input_hash(recompiled) != provenance.linear_input_hash:
            raise ServiceError(
                "the stored linear inputs do not reproduce the inputs re-imported from the authoritative "
                "dataset/kernel sidecars; effective-operator tampering is not cured by re-hashing the provenance "
                "bookkeeping and the project is refused")
        stored = provenance.to_dict()
        for key, value in HydroBridgeProvenance.from_dict(data).to_dict().items():
            if key in ("dataset_source_path", "kernel_source_path", "created_utc"):
                continue
            if stored.get(key) != value:
                raise ServiceError(
                    f"the hydro provenance field {key!r} does not match the re-import from the authoritative "
                    "sidecars; the project is refused")

    def rigid_frequency_references(self, project: ProjectModel) -> dict:
        """Frequency-domain references (discrete solver steady state and dataset RAO) of a rigid hydro import.

        The project's hydro provenance is verified first (the import is re-run from the stored sidecars); the
        references are then built from the stored inputs and the authoritative dataset sidecar at the provenance's
        regular wave (ADR-015 section 7, slice L6-3 M-d). Returns plain JSON; development evidence, unqualified.
        """

        from .hydro_rigid import rigid_frequency_references as _references
        import shutil
        import tempfile

        provenance = project.hydro_provenance
        if provenance is None or provenance.rigid_bundle is None:
            raise ServiceError("frequency-domain references need a rigid_subset_v1 hydro import with its provenance")
        self.verify_hydro_provenance(project)
        try:
            payload = resolve_sidecar(self.root, provenance.dataset_sidecar).read_bytes()
        except (OSError, UnsafePathError) as error:
            raise ServiceError(f"cannot read the authoritative dataset sidecar: {error}") from error
        if sha256_bytes(payload) != provenance.dataset_raw_sha256:
            raise ServiceError("the dataset sidecar does not match the hydro provenance raw sha256")
        try:
            temp_dir = tempfile.mkdtemp(prefix="anymarine-rigid-references-")
        except OSError as error:
            raise ServiceError(f"cannot create the private dataset copy: {error}") from error
        try:
            copy = Path(temp_dir) / "dataset.nc"
            try:
                copy.write_bytes(payload)
            except OSError as error:
                raise ServiceError(f"cannot write the private dataset copy: {error}") from error
            try:
                return _references(project.linear_inputs, provenance, copy)
            except HydroImportError as error:
                raise ServiceError(str(error)) from error
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def import_derived_hydro_inputs(
        self,
        project: ProjectModel,
        dataset_path: os.PathLike | str,
        operators_path: os.PathLike | str,
        kernel_path: os.PathLike | str,
        options: dict,
    ) -> ProjectModel:
        """Admit an ANYhydro raw/derived/kernel triple as stored inputs.

        Explicit derived-operator route, shared by the CLI and the Qt shell.
        Never a fallback of the raw route: it is selected only by calling
        this method. Everything is validated before the project changes
        (raw-source proof, admitted artifact, retained identity, kernel
        binding, BOTH A+B derived reconstruction, diagnostics, grid and
        forcing options); the authoritative raw dataset, derived operators
        and kernel files are copied into the project as content-addressed
        sidecars so the saved project reproduces without the original
        external paths. Returns the new project; the caller adopts (and
        saves) it explicitly.
        """

        self._require_legacy_linear_layout(project, "the derived hydrodynamic import")
        self._require_linear_route(project)
        if self.root is None:
            raise ServiceError(
                "the derived hydrodynamic import requires a project root: open or "
                "save the project first, because the authoritative "
                "dataset/operators/kernel files are copied into the project"
            )
        try:
            inputs, provenance_data = _hydro_derived_import(
                project, dataset_path, operators_path, kernel_path, options
            )
        except HydroImportError as error:
            raise ServiceError(str(error)) from error
        bundle = provenance_data["derived_bundle"]
        store = ProjectStore(self.root)
        for source, expected_reference, expected_digest, suffix in (
            (dataset_path, provenance_data["dataset_sidecar"],
             provenance_data["dataset_raw_sha256"], ".nc"),
            (operators_path, bundle["operators_sidecar"],
             bundle["operators_raw_sha256"], ".npz"),
            (kernel_path, provenance_data["kernel_sidecar"],
             provenance_data["kernel_raw_sha256"], ".nc"),
        ):
            try:
                payload = Path(source).read_bytes()
            except OSError as error:
                raise ServiceError(
                    f"cannot read the hydrodynamic source file {source}: {error}"
                ) from error
            reference, digest = store.store_hydro_artifact(payload, suffix=suffix)
            if reference != expected_reference or digest != expected_digest:
                raise ServiceError(
                    f"the hydrodynamic source file {source} changed while the "
                    "import was running; refusing inconsistent provenance"
                )
        provenance = HydroBridgeProvenance.from_dict(provenance_data)
        new_project = project.with_edits(
            linear_inputs=inputs, hydro_provenance=provenance
        )
        self._verify_linear_binding(new_project, inputs)
        self.verify_hydro_provenance(new_project)
        return new_project

    def verify_hydro_provenance(self, project: ProjectModel) -> None:
        """Fail closed unless the hydro provenance matches the stored inputs."""

        provenance = project.hydro_provenance
        if provenance is None:
            if (
                project.linear_inputs is not None
                and project.linear_inputs.provenance.source == LINEAR_SOURCE_HYDRO
            ):
                raise ServiceError(
                    "the stored linear inputs declare an ANYhydro source but "
                    "the project carries no hydro provenance record; the "
                    "hydro intent cannot be verified and the project is refused"
                )
            return
        inputs = project.linear_inputs
        if inputs is None:
            raise ServiceError(
                "hydro provenance is stored but the project has no linear inputs"
            )
        if inputs.provenance.source != LINEAR_SOURCE_HYDRO:
            raise ServiceError(
                "hydro provenance may only accompany linear inputs imported from "
                "an ANYhydro dataset"
            )
        if (inputs.layout == LINEAR_LAYOUT_RIGID) != (provenance.rigid_bundle is not None):
            raise ServiceError(
                "hydro provenance must carry a rigid bundle exactly when the linear inputs use the "
                f"{LINEAR_LAYOUT_RIGID} layout; stripped or misplaced rigid intent is refused")
        if provenance.rigid_bundle is not None:
            # Authoritative source ownership for the six-DOF route: the import is re-run from the stored sidecars.
            self._verify_rigid_reimport(project, provenance)
            return
        wave = provenance.to_dict()["wave"]
        if provenance.derived_bundle is None and wave is not None and wave.get("prehistory") is not None:
            # The import refuses a prehistory on the raw route; a stored raw project
            # carrying one (e.g. edited and re-hashed) is refused here too.
            raise ServiceError(
                "a periodic prehistory is supported on the derived-operator route "
                "only; a raw-route project declaring one is refused"
            )
        if linear_input_hash(inputs) != provenance.linear_input_hash:
            raise ServiceError(
                "the stored linear inputs do not reproduce the hydro "
                "provenance's declared input hash; the hydro intent is stale"
            )
        # The provenance records the exact dataset DOF keys (body::surge,
        # double colon); the inputs' own labels use the project's single-colon
        # convention, so the expected keys are derived from the bindings.
        expected_keys = tuple(
            f"{binding.body_id}::{dof}"
            for binding in inputs.bindings
            for dof in ("surge", "heave")
        )
        if expected_keys != tuple(provenance.dof_keys):
            raise ServiceError(
                "the stored linear inputs' DOF order does not match the hydro "
                "provenance record"
            )
        if tuple(binding.body_id for binding in inputs.bindings) != tuple(provenance.body_ids):
            raise ServiceError(
                "the stored linear inputs' body order does not match the hydro "
                "provenance record"
            )
        # The inputs carry their own immutable input-owned import evidence
        # (full source identity, criteria, report, settings) in the
        # provenance details; no hydro source is admitted without it, and it
        # must agree with the project's structured hydro provenance record.
        details = inputs.provenance.details
        if details is None:
            raise ServiceError(
                "hydro-imported linear inputs must carry their input-owned "
                "import evidence in the provenance details (full source "
                "identity and criteria); no hydro source is admitted without it"
            )
        details_data = _jsonable_value(details)
        if not isinstance(details_data, dict):
            raise ServiceError("linear provenance details must be a JSON object")
        expected_details = {
            "dataset_content_hash": provenance.dataset_content_hash,
            "kernel_content_hash": provenance.kernel_content_hash,
            "request_hash": provenance.request_hash,
            "a_inf_source": provenance.a_inf_source,
            "dof_keys": list(provenance.dof_keys),
            "mesh_hashes": list(provenance.mesh_hashes),
            "criteria": _jsonable_value(provenance.criteria),
            "reconstruction_report": _jsonable_value(provenance.reconstruction_report),
            "kernel_settings": _jsonable_value(provenance.kernel_settings),
            "diagnostics": _jsonable_value(provenance.diagnostics),
            "assumptions": list(provenance.assumptions),
            "wave": None if provenance.wave is None else _jsonable_value(provenance.wave),
            "surge_link_stiffness": provenance.surge_link_stiffness,
        }
        if provenance.lid_mesh_hashes is not None:
            # Lid imports carry their world-space lid identity in the
            # input-owned evidence too; a snapshot or saved input that
            # stripped the lid identity is refused.
            expected_details["lid_mesh_hashes"] = [
                None if item is None else item for item in provenance.lid_mesh_hashes
            ]
        # Derived-route mirror: the structured provenance's frozen derived
        # bundle and the inputs' own evidence must agree on every meaningful
        # derived field. A stripped bundle, a stripped details route or a
        # metadata-only mismatch is refused; raw-route records must not
        # declare a derived route.
        bundle = provenance.derived_bundle
        declared_route = details_data.get("route")
        if bundle is None:
            if declared_route == HYDRO_ROUTE_DERIVED:
                raise ServiceError(
                    "the stored linear inputs declare a derived-operator import "
                    "but the hydro provenance record carries no derived bundle; "
                    "stripped derived intent is refused"
                )
        else:
            if declared_route != HYDRO_ROUTE_DERIVED:
                raise ServiceError(
                    "the hydro provenance record carries a derived bundle but "
                    "the stored linear inputs do not declare the derived-operator "
                    "route; the input-owned derived evidence is inconsistent and "
                    "the project is refused"
                )
            for key in (
                "derived_content_hash",
                "operators_raw_sha256",
                "derived_settings",
                "derived_report",
                "derived_admitted",
            ):
                if key not in details_data:
                    raise ServiceError(
                        f"linear provenance details is missing the input-owned "
                        f"derived evidence key {key!r}"
                    )
                if details_data[key] != _jsonable_value(bundle[key]):
                    raise ServiceError(
                        f"linear provenance details {key!r} does not match the "
                        "hydro provenance derived bundle; the input-owned derived "
                        "evidence is inconsistent and the project is refused"
                    )
        for key, expected in expected_details.items():
            if key not in details_data:
                raise ServiceError(
                    f"linear provenance details is missing the input-owned "
                    f"evidence key {key!r}"
                )
            if details_data[key] != expected:
                raise ServiceError(
                    f"linear provenance details {key!r} does not match the "
                    "hydro provenance record; the input-owned evidence is "
                    "inconsistent and the project is refused"
                )
        if bundle is not None:
            # Authoritative source ownership: metadata mirrors and the
            # bookkeeping hash alone prove nothing about the effective
            # arrays, so the effective inputs are recompiled from the
            # stored dataset/operators/kernel sidecars through the public
            # ANYhydro source proof and must reproduce the record's full
            # input hash exactly. Effective-operator tampering that
            # re-hashes the bookkeeping is refused here.
            self._verify_derived_recompilation(project, provenance)

    def _verify_derived_recompilation(
        self, project: ProjectModel, provenance: HydroBridgeProvenance
    ) -> None:
        """Recompile the derived effective inputs from the stored sources.

        The exact raw bytes of the three authoritative sidecars are read
        from the project root, checked against the provenance's raw sha256
        values and re-verified through the public ANYhydro loaders and the
        shared admission/assembly path under the record's declared
        controls. The recompiled inputs are what the stored sources
        actually say; any disagreement with the record's full input hash
        is refused as effective-operator tampering.
        """

        if self.root is None:
            raise ServiceError(
                "a derived hydro provenance can only be verified against the "
                "project root holding the authoritative dataset/operators/"
                "kernel sidecars; no project root is configured and the "
                "source evidence cannot be re-verified"
            )
        payloads = {}
        for role, reference, expected in self._hydro_artifact_roles(provenance):
            source = resolve_sidecar(self.root, reference)
            try:
                payload = source.read_bytes()
            except OSError as error:
                raise ServiceError(
                    f"cannot read the authoritative {role} sidecar "
                    f"{reference!r} for the derived source proof: {error}"
                ) from error
            digest = sha256_bytes(payload)
            if digest != expected:
                raise ServiceError(
                    f"the {role} sidecar {reference!r} does not match the "
                    "hydro provenance raw sha256; the authoritative source "
                    "evidence is inconsistent and the project is refused"
                )
            payloads[role] = payload
        try:
            recompiled = _hydro_derived_recompile(
                project,
                provenance.to_dict(),
                payloads["dataset"],
                payloads["operators"],
                payloads["kernel"],
            )
        except HydroImportError as error:
            raise ServiceError(
                f"the authoritative derived sources do not re-verify: {error}"
            ) from error
        if linear_input_hash(recompiled) != provenance.linear_input_hash:
            raise ServiceError(
                "the stored linear inputs do not reproduce the effective "
                "inputs recompiled from the authoritative dataset/operators/"
                "kernel sidecars; effective-operator tampering is not cured "
                "by re-hashing the provenance bookkeeping and the project "
                "is refused"
            )

    def run_linear_local(
        self,
        project: ProjectModel,
        inputs: LinearDevelopmentInputs | None = None,
        cancel=None,
    ) -> ResultRecord:
        """Run the linear route in-process (headless path, no child job)."""

        self._require_linear_route(project)
        inputs = inputs if inputs is not None else project.linear_inputs
        if inputs is None:
            raise ServiceError(
                "the project has no stored linear inputs; create the analytic "
                "demo or load explicit inputs first"
            )
        self._verify_linear_binding(project, inputs)
        # A hydro-sourced effective-input override is only admitted when it
        # reproduces the verified project hydro record's full input hash;
        # anything else would run unverified hydrodynamic material.
        self._verify_effective_hydro_inputs(project, inputs)
        # A stored hydro provenance must still describe the inputs being
        # run; a stale or evidence-free hydro intent fails closed here.
        self.verify_hydro_provenance(project)
        try:
            return run_linear(inputs, cancel=cancel, producing_bodies=project.bodies)
        except LinearError as error:
            raise ServiceError(str(error)) from error

    def linear_job_snapshot(
        self,
        project: ProjectModel,
        inputs: LinearDevelopmentInputs | None = None,
    ) -> dict:
        """Plain JSON snapshot of every effective linear numerical input.

        Contains only primitive data. The worker rebuilds the inputs from
        this dict, re-verifies the body binding against the snapshot's
        project bodies and re-computes the input hash before running.
        """

        self._require_linear_route(project)
        inputs = inputs if inputs is not None else project.linear_inputs
        if inputs is None:
            raise ServiceError(
                "the project has no stored linear inputs; create the analytic "
                "demo or load explicit inputs first"
            )
        self._verify_linear_binding(project, inputs)
        # A hydro-sourced effective-input override is only admitted when it
        # reproduces the verified project hydro record's full input hash;
        # the snapshot must never carry unverified hydrodynamic material.
        self._verify_effective_hydro_inputs(project, inputs)
        # The snapshot must carry the full immutable import identity: the
        # structured hydro provenance is verified against the inputs and
        # embedded so the job's effective source identity (hashes, criteria,
        # report, settings) travels with the materialised inputs.
        self.verify_hydro_provenance(project)
        snapshot = {
            "schema_version": JOB_INPUT_SCHEMA_VERSION,
            "kind": RESULT_KIND_LINEAR,
            "input_hash": linear_input_hash(inputs),
            "inputs": inputs.to_dict(),
            "project": {
                "name": project.name,
                "bodies": [body.to_dict() for body in project.bodies],
                "equilibrium": project.equilibrium.to_dict(),
            },
        }
        if project.hydro_provenance is not None:
            snapshot["hydro_provenance"] = project.hydro_provenance.to_dict()
            if project.hydro_provenance.derived_bundle is not None:
                # A detached derived-operator snapshot carries its own
                # authoritative source evidence: the exact raw bytes of the
                # three sidecars (checked against the provenance's raw
                # sha256 values) travel with the materialised inputs so the
                # worker can recompile and re-verify them even after the
                # original files and the saved sidecars disappear.
                payloads = {}
                for role, reference, expected in self._hydro_artifact_roles(
                    project.hydro_provenance
                ):
                    source = resolve_sidecar(self.root, reference)
                    try:
                        payload = source.read_bytes()
                    except OSError as error:
                        raise ServiceError(
                            f"cannot read the authoritative {role} sidecar "
                            f"{reference!r} for the job snapshot: {error}"
                        ) from error
                    if sha256_bytes(payload) != expected:
                        raise ServiceError(
                            f"the {role} sidecar {reference!r} does not match "
                            "the hydro provenance raw sha256; refusing the "
                            "job snapshot"
                        )
                    payloads[role] = base64.b64encode(payload).decode("ascii")
                snapshot["hydro_source_payloads"] = payloads
        return snapshot

    @staticmethod
    def _verify_linear_binding(project: ProjectModel, inputs: LinearDevelopmentInputs) -> None:
        try:
            verify_linear_binding(project, inputs)
        except LinearBindingError as error:
            raise ServiceError(str(error)) from error

    @staticmethod
    def _verify_effective_hydro_inputs(
        project: ProjectModel, inputs: LinearDevelopmentInputs
    ) -> None:
        """Refuse hydro-sourced effective inputs without the verified record.

        Nonhydro demo/explicit overrides keep the normal override API. A
        hydro-sourced override is only admitted when the project carries a
        structured hydro provenance record and the override reproduces that
        record's full linear input hash, i.e. it is the verified hydro
        import itself rather than unverified hydrodynamic material.
        """

        if inputs.provenance.source != LINEAR_SOURCE_HYDRO:
            return
        provenance = project.hydro_provenance
        if provenance is None or linear_input_hash(inputs) != provenance.linear_input_hash:
            raise ServiceError(
                "hydrodynamic linear inputs may only run as the project's "
                "verified hydro import record; a hydro-sourced input override "
                "that does not reproduce the record's full input hash is refused"
            )

    # ------------------------------------------------------------ job bridge

    def job_input_snapshot(self, project: ProjectModel, settings: AnalysisSettings | None = None) -> dict:
        """Plain JSON snapshot handed to an isolated worker process.

        Contains only primitive data: no pickles, no backend objects, no
        object references. The worker rebuilds everything from this dict and
        must verify ``input_hash`` against its own reconstruction. The hash
        covers the *effective* settings of the job, not just the project's
        stored settings, so the snapshot identifies the actual inputs.
        """

        effective = settings if settings is not None else project.settings
        return {
            "schema_version": JOB_INPUT_SCHEMA_VERSION,
            "kind": RESULT_KIND_SYNTHETIC,
            "input_hash": input_hash(project, effective),
            "project": {
                "name": project.name,
                "bodies": [body.to_dict() for body in project.bodies],
                "equilibrium": project.equilibrium.to_dict(),
            },
            "settings": effective.to_dict(),
        }

    def write_job_input(
        self,
        snapshot: dict,
        path: os.PathLike | str,
    ) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False)
        target.write_text(payload, encoding="utf-8")
        return target

    def load_job_output(
        self,
        path: os.PathLike | str,
        expected_input_hash: str,
        snapshot: dict | None = None,
        expected_output_seal: str | None = None,
    ) -> ResultRecord:
        """Validate a worker output artifact; refuse partial or mismatched data.

        The record's self-declared ``input_hash`` alone proves nothing about
        completeness. When the immutable job input *snapshot* is supplied
        (every CLI and QProcess caller passes it), a ``linear_development``
        output is additionally verified against the snapshot's rebuilt
        inputs: result kind, exact ordered body IDs, exact time grid, the
        required q/v/a/force channels and replay positions/rotations agreeing
        with those channels about the bound reference positions. A one-frame,
        wrong-body or synthetic record carrying the right hash is refused.
        """

        target = Path(path)
        try:
            raw = target.read_bytes()
        except FileNotFoundError as error:
            raise ServiceError(f"worker output {target} was not produced") from error
        except OSError as error:
            raise ServiceError(f"cannot read worker output {target}: {error}") from error
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServiceError(f"worker output {target} is not valid JSON: {error}") from error
        if (isinstance(snapshot,Mapping) and snapshot.get('kind')=='equilibrium_development') or (isinstance(data,dict) and data.get('kind')=='equilibrium_development'):
            from .equilibrium_jobs import decode_output
            try:
                if snapshot is None:raise ValueError('static output needs its input snapshot')
                return decode_output(raw,snapshot,expected_input_hash,expected_output_seal)
            except (ValueError,TypeError,KeyError) as error:raise ServiceError(str(error)) from error
        if ((isinstance(snapshot, Mapping) and snapshot.get('kind') == RESULT_KIND_MECHANICAL)
                or (isinstance(data, dict) and data.get('kind') == RESULT_KIND_MECHANICAL)):
            from .native_jobs import decode_output, NativeJobError
            if snapshot is None:
                raise ServiceError("native output requires its frozen input snapshot")
            try:
                return decode_output(raw, snapshot, expected_input_hash, expected_output_seal)
            except NativeJobError as error:
                raise ServiceError(str(error)) from error
        try:
            result = ResultRecord.from_dict(data)
        except (KeyError, ValueError, TypeError) as error:
            raise ServiceError(f"worker output {target} is invalid: {error}") from error
        if result.input_hash != expected_input_hash:
            raise ServiceError(
                f"worker output input_hash {result.input_hash[:12]}... does not match the "
                f"job input snapshot {expected_input_hash[:12]}...; refusing the result"
            )
        if snapshot is not None and str(snapshot.get("kind")) == RESULT_KIND_LINEAR:
            try:
                inputs = LinearDevelopmentInputs.from_dict(snapshot["inputs"])
                if linear_input_hash(inputs) != expected_input_hash:
                    raise LinearResultIncomplete(
                        "the snapshot inputs no longer reproduce the declared "
                        "input hash"
                    )
                verify_linear_result(result, inputs)
            except LinearResultIncomplete as error:
                raise ServiceError(
                    f"worker output {target} does not completely represent the "
                    f"linear job: {error}"
                ) from error
            except (KeyError, ValueError, TypeError) as error:
                raise ServiceError(
                    f"the job input snapshot is invalid: {error}"
                ) from error
        return result

    # ---------------------------------------------------- linear assessment

    def prepare_linear_assessment(self, project: ProjectModel, spec: dict) -> AssessmentCase:
        """Validate and freeze an assessment case against the stored inputs.

        The case is registered BEFORE any rehearsal solve: it is bound to the
        exact effective ``linear_input_hash`` and the actual input grid, and
        every prospective criterion is validated here (stricter than the
        governing contract minima, never looser).
        """

        inputs = project.linear_inputs
        if inputs is None:
            raise ServiceError(
                "the project has no stored linear inputs; there is nothing to "
                "bind an assessment case to"
            )
        # The stored inputs must still describe the actual project bodies/
        # mesh/equilibrium; a project whose bodies changed after the inputs
        # were stored is refused even if the bookkeeping hashes still match.
        self._verify_linear_binding(project, inputs)
        # A source-bound (hydro) registration additionally requires the
        # existing hydro provenance to re-verify against the authoritative
        # sidecars here, before the case is admitted.
        self.verify_hydro_provenance(project)
        try:
            return _assessment_prepare(spec, inputs)
        except AssessmentCaseError as error:
            raise ServiceError(str(error)) from error

    def assess_linear_result(
        self, project: ProjectModel, result: ResultRecord, case: AssessmentCase
    ) -> AssessmentReport:
        """Assess one stored linear result under its registered case.

        The shared hydro provenance gate runs BEFORE any assessment sidecar
        load; a hydro_spectrum reference then re-proves the actual stored
        dataset/operator sources through the public loaders. Physics
        failures produce a NoGo report; eligibility failures are refusals.
        """

        inputs = project.linear_inputs
        if inputs is None:
            raise ServiceError("the project has no stored linear inputs")
        # The stored inputs must still describe the actual project bodies/
        # mesh/equilibrium at assessment time, not only at run time: the
        # public run_linear_local gate alone does not establish later
        # assessment eligibility.
        self._verify_linear_binding(project, inputs)
        if result.input_hash != linear_input_hash(inputs):
            raise ServiceError(
                "the result does not match the stored linear inputs (stale or "
                "foreign result); refusing the assessment"
            )
        self.verify_hydro_provenance(project)
        try:
            return _assessment_assess(
                case,
                result,
                inputs,
                project=project,
                provenance=project.hydro_provenance,
                project_root=self.root,
            )
        except AssessmentError as error:
            raise ServiceError(str(error)) from error

    def save_assessment_snapshot(
        self,
        project: ProjectModel,
        result: ResultRecord,
        case: AssessmentCase,
        report: AssessmentReport,
        out_root: os.PathLike | str,
    ) -> Path:
        """Write a SELF-CONTAINED assessment snapshot under *out_root*.

        The selected project/result is saved through the existing public
        save-as route (which copies and checks the real hydro dataset/
        operators/kernel sidecars into the snapshot), next to the hashed
        case/report artifacts. Only the selected result is copied; unrelated
        results and campaign artifacts are not. Reproduction from this
        snapshot no longer depends on the original source files or project.
        """

        out = Path(out_root)
        if out.exists():
            raise ServiceError(
                f"the assessment snapshot target {out} already exists; "
                "existing outputs (including NoGo/failed reports) are "
                "preserved and never overwritten"
            )
        inputs = project.linear_inputs
        if inputs is None:
            raise ServiceError(
                "the project has no stored linear inputs; a snapshot is "
                "bound to the actual assessed inputs"
            )
        if case.input_hash != linear_input_hash(inputs):
            raise ServiceError(
                "the case does not describe the project's stored linear "
                "inputs; the case/result/report do not correspond"
            )
        if result.input_hash != case.input_hash:
            raise ServiceError(
                "the result does not match the case's bound input hash; "
                "the case/result/report do not correspond"
            )
        if case.result_id is not None and result.result_id != case.result_id:
            raise ServiceError(
                "the case selects a different result than the one being "
                "snapshotted; the case/result/report do not correspond"
            )
        if report.case_hash != case.case_hash:
            raise ServiceError(
                "the report does not describe this case; the "
                "case/result/report do not correspond"
            )
        if report.input_hash != case.input_hash:
            raise ServiceError(
                "the report does not describe the case's bound inputs; "
                "the case/result/report do not correspond"
            )
        if report.result_id != result.result_id:
            raise ServiceError(
                "the report does not describe this result; the "
                "case/result/report do not correspond"
            )
        # The report must REPRODUCE from the actual stored arrays before
        # anything is created: the case is re-assessed here and the full
        # semantic payload (verdict, checks, metrics, arrays) must agree
        # with the report being snapshotted. A re-hashed report with
        # forged metrics is refused before any write; NoGo reports that
        # reproduce are snapshotted and preserved like any other result.
        try:
            _assessment_adjudicate(
                case,
                report,
                result,
                inputs,
                project=project,
                provenance=project.hydro_provenance,
                project_root=self.root,
            )
        except AssessmentError as error:
            raise ServiceError(
                f"the report does not reproduce from the actual arrays "
                f"before snapshot creation: {error}"
            ) from error
        selected = project.with_edits(results=(result,))
        self.save(selected, out / "project")
        store = AssessmentStore(out)
        store.save_case(case)
        store.save_report(report)
        return out

    def adjudicate_linear_assessment(
        self,
        snapshot_root: os.PathLike | str,
        case_path: os.PathLike | str,
        report_path: os.PathLike | str,
    ) -> dict:
        """Reproduce a snapshot assessment from the captured actual data.

        Loads the snapshot's saved project (the provenance verification and
        source proof re-run on the snapshot's own sidecar copies), selects
        the case's stored result, re-assesses from the actual arrays and
        compares with the stored report. Stored booleans/hashes alone are
        necessary but not sufficient; any disagreement is a refusal.
        """

        root = Path(snapshot_root)
        project = self.load(root / "project")
        case = _read_case_artifact(case_path)
        stored = _read_report_artifact(report_path)
        inputs = project.linear_inputs
        if inputs is None:
            raise ServiceError("the snapshot project has no stored linear inputs")
        if case.result_id is None:
            # A case registered BEFORE the solve cannot know the run's
            # result id; a snapshot holds exactly the selected result,
            # so an unbound case may adjudicate the single stored result.
            if len(project.results) != 1:
                raise ServiceError(
                    "the case selects no result_id and the snapshot does "
                    "not hold exactly one result; pass an explicit selection"
                )
            selected_result = project.results[0]
        else:
            selected_result = None
            for record in project.results:
                if record.result_id == case.result_id:
                    selected_result = record
                    break
            if selected_result is None:
                raise ServiceError(
                    f"the snapshot holds no result with id {case.result_id!r}"
                )
        try:
            return _assessment_adjudicate(
                case,
                stored,
                selected_result,
                inputs,
                project=project,
                provenance=project.hydro_provenance,
                project_root=self.root,
            )
        except AssessmentError as error:
            raise ServiceError(str(error)) from error

    # ---------------------------------------------------- paired refinement

    def assess_paired_refinement(
        self,
        coarse_project,
        coarse_result,
        coarse_case,
        coarse_root,
        fine_project,
        fine_result,
        fine_case,
        fine_root,
    ):
        """Assess an explicit coarse/fine pair of the SAME physical model.

        Each member independently passes the binding/source/completeness
        eligibility and is fully assessed under its registered case; the
        combined verdict is PASS only if BOTH member full reports and the
        refinement report pass. Valid NoGo members produce a preserved
        composite NoGo record. Hydro-sourced members REQUIRE their actual
        project root (the source proofs re-run against the authoritative
        sidecars); analytic roots are optional. The assessment is
        read-only: the projects, roots and sources are never mutated and
        the active service root is never changed.
        """

        from .paired_refinement import (
            PairedMember,
            PairedRefinementError,
            assess_paired_refinement as _assess_pair,
        )

        coarse = PairedMember(
            coarse_project, coarse_result, coarse_case, project_root=coarse_root
        )
        fine = PairedMember(
            fine_project, fine_result, fine_case, project_root=fine_root
        )
        try:
            return _assess_pair(coarse, fine)
        except PairedRefinementError as error:
            raise ServiceError(str(error)) from error

    def save_paired_refinement_snapshot(
        self,
        coarse_project,
        coarse_result,
        coarse_case,
        coarse_root,
        fine_project,
        fine_result,
        fine_case,
        fine_root,
        out_root,
    ) -> Path:
        """Assess the pair and write an EXCLUSIVE self-contained snapshot.

        The paired record must reproduce from the actual arrays before
        anything is created; each selected-result project is then saved
        through the existing public save-as route (real hydro sidecars
        copied and checked), the case/report artifacts through the
        existing public AssessmentStore, and the paired artifact records
        validated relative references. An existing target is refused
        BEFORE any write; failed/NoGo evidence is preserved.
        """

        from .paired_refinement import (
            PairedMember,
            PairedRefinementError,
            assess_paired_refinement as _assess_pair,
            save_paired_snapshot as _save_pair,
        )

        coarse = PairedMember(
            coarse_project, coarse_result, coarse_case, project_root=coarse_root
        )
        fine = PairedMember(
            fine_project, fine_result, fine_case, project_root=fine_root
        )
        try:
            paired_report = _assess_pair(coarse, fine)
            return _save_pair(coarse, fine, paired_report, Path(out_root))
        except PairedRefinementError as error:
            raise ServiceError(str(error)) from error

    def adjudicate_paired_refinement_snapshot(self, snapshot_root) -> dict:
        """Reproduce a paired snapshot from its own captured actual data.

        Uses ONLY the snapshot's own projects/results/cases/sidecars (the
        original projects and external source files may be absent or
        renamed); re-runs BOTH members' complete physical/source proofs
        and the refinement and compares the actual semantic payload
        against the stored paired record. Any disagreement is a refusal.
        """

        from .paired_refinement import (
            PairedRefinementError,
            adjudicate_paired_snapshot as _adjudicate_pair,
        )

        try:
            return _adjudicate_pair(snapshot_root)
        except PairedRefinementError as error:
            raise ServiceError(str(error)) from error


