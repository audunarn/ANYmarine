"""Isolated job worker: plain JSON in, plain JSON out, no Qt, no pickles.

Usage:
    python -m anymarine.worker <input.json> <output.json> [cancel-file]

Protocol:
- stdout carries bounded progress lines: ``PROGRESS <fraction>`` (at most
  ``MAX_PROGRESS_LINES`` per run) and a final ``DONE``.
- The optional cancel file enables cooperative cancellation: the worker checks
  it between steps and exits with code 3 without writing any output.
- Output is written atomically only after the full result is complete; there
  is no partial success.
- The worker verifies the snapshot's declared input_hash against a hash
  recomputed from the reconstructed inputs; a mismatch refuses the job.
- Job kinds: ``synthetic_unqualified`` (deterministic synthetic replay),
  ``linear_development`` (four-DOF two-body linear route through the public
  ``anysolver.marine_linear`` API) and the ``assessment_*`` job kinds
  (case registration, single/paired assessment, snapshot adjudication),
  which dispatch to the Qt-free :mod:`anymarine.assessment_jobs` executor. The linear path additionally re-verifies
  the complete equilibrium binding (identity, order, name, reference
  position, mass, mesh geometry, project equilibrium) against the snapshot's
  project and fails closed when the optional solver is unavailable; a
  cancelled or incomplete solver run never produces output.
- Process policy: synthetic and mechanics workers stay in one process.
  Explicit HAMS field jobs supervise a bound native producer through ANYhydro;
  Windows atomic kill-on-close Job Object containment owns its descendants.
  This Qt worker route refuses Linux until supervisor-death containment is admitted.
- Exit codes: 0 success, 2 invalid input, 3 cancelled, 4 internal failure.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from . import RESULT_KIND_LINEAR, RESULT_KIND_SYNTHETIC, RESULT_KIND_MECHANICAL
from .assessment_jobs import ASSESSMENT_JOB_KINDS
from .domain import (
    HYDRO_ROUTE_DERIVED,
    AnalysisSettings,
    Body,
    EquilibriumMetadata,
    HydroBridgeProvenance,
    ProjectModel,
)
from .hashing import input_hash
from .hydro import HydroImportError, recompile_derived_inputs
from .linear import (
    LinearBindingError,
    LinearCancelledError,
    LinearError,
    LinearInputRejected,
    LinearSolveFailed,
    LinearSolverUnavailableError,
    LinearDevelopmentInputs,
    linear_input_hash,
    run_linear,
    verify_linear_binding,
)
from .persistence import atomic_write_json
from .synthetic import generate_synthetic_replay, sample_times

MAX_PROGRESS_LINES = 20
EXIT_SUCCESS = 0
EXIT_INVALID_INPUT = 2
EXIT_CANCELLED = 3
EXIT_FAILURE = 4


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def run(input_path: Path, output_path: Path, cancel_path: Path | None) -> int:
    try:
        raw = input_path.read_bytes()
        snapshot = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"invalid input snapshot: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    if not isinstance(snapshot, dict):
        print("invalid input snapshot: not a JSON object", file=sys.stderr)
        return EXIT_INVALID_INPUT

    kind = snapshot.get("kind")
    if kind == 'equilibrium_adoption_development':
        from .adoption_jobs import run_job
        return run_job(snapshot,output_path,cancel_path)
    if kind == 'equilibrium_development':
        from .equilibrium_jobs import run_job
        return run_job(snapshot,output_path,cancel_path)
    if kind == 'hams_field_development':
        from .hams_jobs import run_hams_job
        return run_hams_job(snapshot, output_path, cancel_path)
    if kind == 'wave_preview':
        from .wave_jobs import run_wave_job
        return run_wave_job(snapshot, output_path, cancel_path)
    if kind == RESULT_KIND_SYNTHETIC:
        return _run_synthetic(snapshot, output_path, cancel_path)
    if kind == RESULT_KIND_LINEAR:
        return _run_linear(snapshot, output_path, cancel_path)
    if kind == RESULT_KIND_MECHANICAL:
        return _run_native(snapshot, output_path, cancel_path)
    if kind in ASSESSMENT_JOB_KINDS:
        # Assessment jobs (case registration, single/paired assessment,
        # snapshot adjudication) run headless in this process through the
        # public application services; the executor owns the full
        # freeze/transport contract and never spawns children.
        from .assessment_jobs import execute as _execute_assessment

        return _execute_assessment(snapshot, output_path, cancel_path)
    print(f"unsupported job kind {kind!r}", file=sys.stderr)
    return EXIT_INVALID_INPUT


def _verify_derived_snapshot_sources(
    snapshot: dict, project: ProjectModel, declared_hash: str
) -> None:
    """Recompile the derived effective inputs from the snapshot's own bytes.

    The detached snapshot embeds the exact raw bytes of its authoritative
    dataset/operators/kernel sources; the worker re-runs the public
    ANYhydro source proof and the shared admission and assembly path on
    private copies and requires the recompiled inputs to reproduce the
    declared input hash. Anything else is effective-operator tampering
    that re-hashed the snapshot bookkeeping and is refused.

    The structured provenance is first validated as a frozen
    HydroBridgeProvenance (derived bundle included) and every embedded
    payload's actual raw sha256 must match the record's declared
    identity before anything is decoded; declared bookkeeping alone is
    never accepted as source proof.
    """

    payloads_data = snapshot.get("hydro_source_payloads")
    if not isinstance(payloads_data, dict):
        raise ValueError(
            "a derived-operator job snapshot must carry its authoritative "
            "dataset/operators/kernel source payloads "
            "(hydro_source_payloads); source-less derived material is refused"
        )
    payloads = {}
    for role in ("dataset", "operators", "kernel"):
        encoded = payloads_data.get(role)
        if not isinstance(encoded, str) or not encoded:
            raise ValueError(
                f"the job snapshot is missing the {role} source payload; "
                "source-less derived material is refused"
            )
        try:
            payloads[role] = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as error:
            raise ValueError(f"invalid {role} source payload: {error}") from error
    provenance_data = snapshot.get("hydro_provenance")
    if not isinstance(provenance_data, dict):
        raise ValueError(
            "a derived-operator job snapshot must carry its structured hydro "
            "provenance record; a stripped provenance record is refused"
        )
    try:
        provenance = HydroBridgeProvenance.from_dict(provenance_data)
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError(
            "the snapshot's hydro provenance record is not a valid frozen "
            f"HydroBridgeProvenance: {error}"
        ) from error
    bundle = provenance.derived_bundle
    if bundle is None:
        raise ValueError(
            "a derived-operator job snapshot must carry its frozen derived "
            "bundle; a stripped derived intent is refused"
        )
    # Declared raw byte identities are bookkeeping, not proof: the actual
    # payload bytes must hash to the record's declared raw sha256 values
    # before any source is decoded or recompiled.
    declared_raw = {
        "dataset": provenance.dataset_raw_sha256,
        "operators": bundle["operators_raw_sha256"],
        "kernel": provenance.kernel_raw_sha256,
    }
    for role, payload in payloads.items():
        actual = hashlib.sha256(payload).hexdigest()
        if actual != str(declared_raw[role]):
            raise ValueError(
                f"the snapshot's {role} source payload hashes to {actual} but "
                f"the hydro provenance record declares {declared_raw[role]!r}; "
                "an inconsistent raw byte identity is refused"
            )
    try:
        recompiled = recompile_derived_inputs(
            project,
            provenance.to_dict(),
            payloads["dataset"],
            payloads["operators"],
            payloads["kernel"],
        )
    except HydroImportError as error:
        raise ValueError(
            f"the snapshot's authoritative derived sources do not re-verify: {error}"
        ) from error
    if linear_input_hash(recompiled) != declared_hash:
        raise ValueError(
            "the snapshot inputs do not reproduce the effective inputs "
            "recompiled from the snapshot's authoritative sources; "
            "effective-operator tampering is refused"
        )


def _run_synthetic(snapshot: dict, output_path: Path, cancel_path: Path | None) -> int:
    try:
        if snapshot.get("schema_version") != 1:
            raise ValueError("unsupported snapshot schema_version")
        project_data = snapshot["project"]
        bodies = tuple(Body.from_dict(item) for item in project_data["bodies"])
        project = ProjectModel(
            name=str(project_data["name"]),
            bodies=bodies,
            equilibrium=EquilibriumMetadata.from_dict(project_data.get("equilibrium", {}) or {}),
            settings=AnalysisSettings.from_dict(snapshot.get("settings", {}) or {}),
        )
        declared_hash = str(snapshot["input_hash"])
        # The snapshot must identify the actual effective inputs: verify the
        # declared hash against a hash recomputed from the reconstructed
        # project (including the snapshot's effective settings).
        recomputed = input_hash(project)
        if recomputed != declared_hash:
            raise ValueError(
                f"snapshot input_hash {declared_hash[:12]}... does not match the "
                f"reconstructed inputs {recomputed[:12]}...; refusing the job"
            )
    except (KeyError, ValueError, TypeError) as error:
        print(f"invalid input snapshot: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    settings = project.settings
    times = sample_times(settings)
    total = len(times)
    progress_interval = max(1, total // MAX_PROGRESS_LINES)

    for index, _t in enumerate(times):
        if cancel_path is not None and cancel_path.exists():
            _emit("CANCELLED")
            return EXIT_CANCELLED
        if index % progress_interval == 0 and index:
            _emit(f"PROGRESS {index / total:.4f}")

    try:
        result = generate_synthetic_replay(
            project, settings=settings, input_hash=declared_hash
        )
        atomic_write_json(output_path, result.to_dict())
    except (OSError, ValueError, TypeError) as error:
        print(f"worker failure: {error}", file=sys.stderr)
        return EXIT_FAILURE
    _emit("PROGRESS 1.0000")
    _emit("DONE")
    return EXIT_SUCCESS


def _run_linear(snapshot: dict, output_path: Path, cancel_path: Path | None) -> int:
    try:
        if snapshot.get("schema_version") != 1:
            raise ValueError("unsupported snapshot schema_version")
        inputs = LinearDevelopmentInputs.from_dict(snapshot["inputs"])
        bodies = tuple(Body.from_dict(item) for item in snapshot["project"]["bodies"])
        project = ProjectModel(
            name=str(snapshot["project"]["name"]),
            bodies=bodies,
            equilibrium=EquilibriumMetadata.from_dict(
                snapshot["project"].get("equilibrium", {}) or {}
            ),
        )
        # Stale body identity/order/reference/mesh or equilibrium binding is
        # refused, never rebound.
        verify_linear_binding(project, inputs)
        declared_hash = str(snapshot["input_hash"])
        recomputed = linear_input_hash(inputs)
        if recomputed != declared_hash:
            raise ValueError(
                f"snapshot input_hash {declared_hash[:12]}... does not match the "
                f"reconstructed linear inputs {recomputed[:12]}...; refusing the job"
            )
        # A derived-operator snapshot must carry its own authoritative
        # source evidence and the worker re-verifies it: the effective
        # inputs are recompiled from the snapshot's embedded source bytes
        # through the public ANYhydro source proof and must reproduce the
        # declared hash. Re-hashed operator tampering is refused even
        # after the originals and saved sidecars disappear.
        # Derived intent is claimed on both sides: by the inputs' own
        # provenance details (input-owned route) and by the snapshot's
        # structured hydro provenance bundle. The claim set is closed-world:
        # embedded source payloads or any input-owned derived evidence
        # (derived_* fields, the operators raw identity) also claim the
        # derived route. Either claim requires the complete derived intent
        # and its embedded source proof; stripping the explicit markers
        # while partial derived evidence remains must not bypass the proof
        # by falling back to an unproven linear solve. Legacy raw/explicit
        # snapshots carry none of these fields and are unaffected.
        details = inputs.provenance.details
        declared_route = (
            details.get("route") if isinstance(details, Mapping) else None
        )
        hydro_provenance = snapshot.get("hydro_provenance")
        bundle_present = (
            isinstance(hydro_provenance, dict)
            and hydro_provenance.get("derived_bundle") is not None
        )
        partial_derived_evidence = isinstance(
            snapshot.get("hydro_source_payloads"), dict
        ) or (
            isinstance(details, Mapping)
            and any(
                key == "operators_raw_sha256" or str(key).startswith("derived_")
                for key in details
            )
        )
        if (
            declared_route == HYDRO_ROUTE_DERIVED
            or bundle_present
            or partial_derived_evidence
        ):
            if not bundle_present or declared_route != HYDRO_ROUTE_DERIVED:
                raise ValueError(
                    "the snapshot carries partial derived-operator evidence "
                    "(input-owned route, derived fields, operators raw "
                    "identity, embedded source payloads or structured "
                    "provenance bundle) without the complete derived "
                    "intent; a stripped or inconsistent derived intent is "
                    "refused"
                )
            _verify_derived_snapshot_sources(snapshot, project, declared_hash)
    except LinearBindingError as error:
        print(f"stale body binding: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except (KeyError, ValueError, TypeError) as error:
        print(f"invalid input snapshot: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    # The solver is polled once per accepted step through the cancel callable;
    # the poll counter doubles as the progress signal.
    state = {"polls": 0}
    progress_interval = max(1, inputs.n_steps // MAX_PROGRESS_LINES)

    def cancel_and_progress():
        state["polls"] += 1
        if state["polls"] % progress_interval == 0:
            _emit(f"PROGRESS {min(1.0, state['polls'] / inputs.n_steps):.4f}")
        return cancel_path is not None and cancel_path.exists()

    try:
        result = run_linear(inputs, cancel=cancel_and_progress)
        atomic_write_json(output_path, result.to_dict())
    except LinearCancelledError:
        _emit("CANCELLED")
        return EXIT_CANCELLED
    except LinearInputRejected as error:
        print(f"solver rejected the inputs: {error}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except LinearSolverUnavailableError as error:
        print(f"solver unavailable: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except (LinearSolveFailed, LinearError, OSError, ValueError, TypeError) as error:
        print(f"worker failure: {error}", file=sys.stderr)
        return EXIT_FAILURE
    _emit("PROGRESS 1.0000")
    _emit("DONE")
    return EXIT_SUCCESS




def _run_native(snapshot, output_path, cancel_path):
    """One headless native process; non-success evidence never becomes output."""
    import hashlib
    import time
    import uuid
    from datetime import datetime, timezone
    from .native_jobs import project_from_snapshot, record_from_evidence
    from .mechanics import prepare_native_run
    started = time.monotonic()
    state = {'timed_out': False, 'progress_bucket': 0}
    partial_path = output_path.with_name(output_path.stem + '.partial.json')
    try:
        if output_path.exists():
            raise ValueError('native output path already exists; evidence must not be overwritten')
        project = project_from_snapshot(snapshot)
        model, initial_states, spec = prepare_native_run(project)
        import anysolver.marine_mechanical as solver
    except (ImportError, KeyError, TypeError, ValueError, OSError) as error:
        print(f'native input preparation failed: {error}', file=sys.stderr)
        return EXIT_INVALID_INPUT

    def cancel():
        if time.monotonic() - started >= snapshot['execution_limits']['wall_seconds']:
            state['timed_out'] = True
        return state['timed_out'] or (cancel_path is not None and cancel_path.exists())

    def progress(frame):
        bucket = min(MAX_PROGRESS_LINES - 1, frame.step * (MAX_PROGRESS_LINES - 1) // spec.steps)
        if bucket > state['progress_bucket']:
            state['progress_bucket'] = bucket
            _emit(f'PROGRESS {frame.step / spec.steps:.4f}')

    def incomplete(status, reason, evidence=None):
        atomic_write_json(partial_path, {'schema_version': 1, 'kind': 'mechanical_development',
            'input_hash': snapshot['input_hash'], 'status': status,
            'reason': reason, 'evidence': evidence})

    try:
        from .mechanics import prepare_native_nodes
        result = solver.solve_marine_mechanical(model, initial_states, spec, cancel=cancel, progress=progress,
                                               initial_nodes=prepare_native_nodes(project))
        evidence = solver.result_to_dict(result)
        # Poll again before publication: a late request/deadline never seals success.
        requested = cancel()
        if result.status != 'completed' or requested:
            status = 'failed' if state['timed_out'] else 'cancelled'
            reason = 'execution deadline exceeded' if state['timed_out'] else 'cancellation requested'
            incomplete(status, reason, evidence)
            _emit('CANCELLED' if status == 'cancelled' else 'FAILED')
            return EXIT_CANCELLED if status == 'cancelled' else EXIT_FAILURE
        if project.native_inputs.potential_flow is not None:
            from anysolver.marine_rigid import RigidBodyState
            from anysolver.marine_potential_flow import PotentialFlowDevelopmentContribution
            last = result.accepted[-1]
            accepted_states = {bid:RigidBodyState(position=last.positions[bid],
                anchor=last.rotations[bid],velocity=last.world_velocities[bid],
                acceleration=last.world_accelerations[bid]) for bid in result.body_ids}
            for contribution in model.contributions:
                if type(contribution) is PotentialFlowDevelopmentContribution:
                    contribution.validate_accepted_history(result.histories[contribution.contribution_id],
                                                          accepted_states,spec.steps)
        record = record_from_evidence(evidence, snapshot, result_id=str(uuid.uuid4()),
            note=('Native compliant frictionless mooring contact development, unqualified; equilibrium/friction/W6 incomplete.'
                  if project.native_inputs.mooring is not None and project.native_inputs.mooring.has_contact else
                  'Native wave/current member/line hydrodynamics development, unqualified; contact/equilibrium/W6 incomplete.'
                  if project.native_inputs.mooring is not None and project.native_inputs.mooring.has_hydrodynamics else
                  'Native coupled body/mooring development, unqualified; no line hydrodynamics/contact qualification.'
                  if project.native_inputs.mooring is not None else
                  'Native potential-flow/Morison wave/current/link development, unqualified; no physical source admission.'
                  if project.native_inputs.morison is not None and project.native_inputs.potential_flow is not None else
                  'Native Morison wave/current/link mechanics: development evidence, unqualified.'
                  if project.native_inputs.morison is not None else
                  'Native coupled potential-flow/link development fixture, unqualified; no physical source admission.' 
                  if project.native_inputs.potential_flow is not None else
                  'Native dry/link mechanics: development evidence, unqualified; no hydrodynamic loads.'),
            created_utc=datetime.now(timezone.utc).isoformat())
        if cancel():
            incomplete('failed' if state['timed_out'] else 'cancelled',
                       'late deadline/cancellation before output', evidence)
            return EXIT_FAILURE if state['timed_out'] else EXIT_CANCELLED
        atomic_write_json(output_path, {'schema_version': 1, 'kind': 'mechanical_development',
            'input_hash': snapshot['input_hash'], 'evidence': evidence, 'result': record.to_dict()})
        _emit('OUTPUT_SHA256 ' + hashlib.sha256(output_path.read_bytes()).hexdigest())
    except solver.MarineMechanicalSolveError as error:
        incomplete('failed', str(error), None if error.partial is None else solver.result_to_dict(error.partial))
        print(f'native solve failed ({error.reason}): {error}', file=sys.stderr)
        return EXIT_FAILURE
    except Exception as error:
        incomplete('failed', f'{type(error).__name__}: {error}')
        print(f'native worker failed: {error}', file=sys.stderr)
        return EXIT_FAILURE
    _emit('PROGRESS 1.0000')
    _emit('DONE')
    return EXIT_SUCCESS

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 3):
        print("usage: python -m anymarine.worker <input.json> <output.json> [cancel-file]", file=sys.stderr)
        return EXIT_INVALID_INPUT
    input_path = Path(argv[0])
    output_path = Path(argv[1])
    cancel_path = Path(argv[2]) if len(argv) == 3 else None
    return run(input_path, output_path, cancel_path)


if __name__ == "__main__":
    raise SystemExit(main())
