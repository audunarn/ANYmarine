"""Headless command line interface. Works without Qt or any3dview installed."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .domain import AnalysisSettings
from .persistence import ProjectError
from .services import ApplicationService, ServiceError

WORKER_TIMEOUT_SECONDS = 110


def _child_environment():
    """Environment for child worker jobs.

    The child must import ``anymarine`` from this checkout even when the
    package is not installed, and — for linear development jobs — the
    optional solver through ``ANYMARINE_SOLVER_PATH`` when the caller set it.
    No path is invented here; an absent solver fails closed in the worker.
    """

    environment = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parent.parent)
    parts = [package_parent]
    solver_path = os.environ.get("ANYMARINE_SOLVER_PATH", "")
    if solver_path:
        parts.append(solver_path)
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(parts)
    return environment


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anymarine",
        description="ANYmarine marine project application (headless).",
    )
    parser.add_argument("--version", action="version", version=f"anymarine {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="create a new synthetic two-body project")
    new.add_argument("--path", required=True, help="project directory (created if missing)")
    new.add_argument("--name", default="Two-body demonstrator", help="project name")

    run = sub.add_parser("run", help="run a synthetic replay job and store the result")
    run.add_argument("--project", required=True, help="project directory")
    run.add_argument("--duration", type=float, default=None, help="override replay duration [s]")
    run.add_argument("--dt", type=float, default=None, help="override time step [s]")
    run.add_argument("--local", action="store_true", help="run in-process instead of a child job")

    import_native = sub.add_parser('import-native', help='validate and store declared native development inputs')
    import_native.add_argument('--project', required=True)
    import_native.add_argument('--inputs', required=True, help='native_mechanical_inputs_v1 JSON file')
    run_native = sub.add_parser('run-native', help='run saved native mechanical inputs in an isolated process')
    run_native.add_argument('--project', required=True)
    run_native.add_argument('--max-frames', type=int, default=1001)
    run_native.add_argument('--wall-seconds', type=float, default=60.)
    run_equilibrium = sub.add_parser('run-equilibrium', help='run coupled static continuation in an isolated process')
    run_equilibrium.add_argument('--project', required=True)
    run_equilibrium.add_argument('--max-frames', type=int, default=1001)
    run_equilibrium.add_argument('--wall-seconds', type=float, default=60.)
    run_equilibrium.add_argument('--static-spec', help='JSON static controls; defaults to the tested development controls')


    run_linear = sub.add_parser(
        "run-linear",
        help="run a linear development job (four-DOF two-body surge/heave)",
    )
    run_linear.add_argument("--project", required=True, help="project directory")
    run_linear.add_argument(
        "--demo",
        action="store_true",
        help="build the labelled analytic demo inputs for this project's two bodies",
    )
    run_linear.add_argument(
        "--inputs",
        default=None,
        help="JSON file with explicitly supplied matrices/kernel/forcing inputs",
    )
    run_linear.add_argument("--local", action="store_true", help="run in-process instead of a child job")

    import_hydro = sub.add_parser(
        "import-hydro",
        help="import an ANYhydro dataset/kernel pair as stored linear inputs",
    )
    import_hydro.add_argument("--project", required=True, help="project directory")
    import_hydro.add_argument("--dataset", required=True, help="ANYhydro dataset NetCDF file")
    import_hydro.add_argument("--kernel", required=True, help="ANYhydro radiation kernel NetCDF file")
    import_hydro.add_argument(
        "--options",
        required=True,
        help=(
            "JSON file with explicit import options (n_steps, criteria, "
            "wave/q0/v0, optional execution_limits for longer development jobs)"
        ),
    )

    import_derived = sub.add_parser(
        "import-derived-hydro",
        help="import an ANYhydro raw/derived/kernel triple as stored linear inputs",
    )
    import_derived.add_argument("--project", required=True, help="project directory")
    import_derived.add_argument("--dataset", required=True, help="ANYhydro raw dataset NetCDF file")
    import_derived.add_argument(
        "--operators", required=True, help="ANYhydro derived-operator artifact (.npz)"
    )
    import_derived.add_argument(
        "--kernel", required=True, help="ANYhydro derived radiation kernel NetCDF file"
    )
    import_derived.add_argument(
        "--options",
        required=True,
        help=(
            "JSON file with explicit import options (n_steps, criteria, "
            "wave/q0/v0, optional execution_limits for longer development jobs)"
        ),
    )

    listing = sub.add_parser("list-results", help="list stored results with staleness")
    listing.add_argument("--project", required=True, help="project directory")

    validate = sub.add_parser("validate", help="validate a stored project and its sidecars")
    validate.add_argument("--project", required=True, help="project directory")

    prepare = sub.add_parser(
        "prepare-assessment",
        help="register an immutable linear assessment case for the stored inputs",
    )
    prepare.add_argument("--project", required=True, help="project directory")
    prepare.add_argument(
        "--spec", required=True, help="JSON file with the closed-world case specification"
    )
    prepare.add_argument("--out", required=True, help="output case artifact path")

    assess = sub.add_parser(
        "assess-linear",
        help="assess a stored linear result under a registered case",
    )
    assess.add_argument("--project", required=True, help="project directory")
    assess.add_argument("--case", required=True, help="registered case artifact path")
    assess.add_argument(
        "--result-id", default=None, help="result id (defaults to the case selection)"
    )
    assess.add_argument("--out", required=True, help="output report artifact path")

    adjudicate = sub.add_parser(
        "adjudicate-assessment",
        help="reproduce a snapshot assessment from the captured actual data",
    )
    adjudicate.add_argument("--snapshot", required=True, help="assessment snapshot root")
    adjudicate.add_argument("--case", required=True, help="case artifact path")
    adjudicate.add_argument("--report", required=True, help="stored report artifact path")

    assess_ref = sub.add_parser(
        "assess-refinement",
        help=(
            "assess an explicit coarse/fine pair of the same physical model "
            "and write an exclusive paired snapshot"
        ),
    )
    for role in ("coarse", "fine"):
        assess_ref.add_argument(
            f"--{role}-project", required=True, help=f"{role} project directory"
        )
        assess_ref.add_argument(
            f"--{role}-case", required=True, help=f"{role} registered case artifact path"
        )
        assess_ref.add_argument(
            f"--{role}-result-id",
            required=True,
            help=f"exact {role} result id (no automatic selection)",
        )
    assess_ref.add_argument(
        "--snapshot",
        required=True,
        help="exclusive paired snapshot output root (must not exist)",
    )

    adjudicate_ref = sub.add_parser(
        "adjudicate-refinement",
        help="reproduce a paired snapshot from its own captured actual data",
    )
    adjudicate_ref.add_argument(
        "--snapshot", required=True, help="paired snapshot root"
    )
    rao_prepare = sub.add_parser('rao-prepare', help='register a closed-world RAO case for the project (no solve)')
    rao_prepare.add_argument('--project', required=True, help='project directory')
    rao_prepare.add_argument('--spec', required=True, help='JSON file with the RAO case specification')
    rao_prepare.add_argument('--out', required=True, help='output case artifact path')
    rao_table = sub.add_parser('rao-table', help='frequency-domain RAO table with per-contribution forces')
    rao_table.add_argument('--project', required=True, help='project directory')
    rao_table.add_argument('--case', required=True, help='registered RAO case artifact')
    rao_table.add_argument('--out-dir', required=True, help='new output directory (JSON + CSV)')
    rao_run = sub.add_parser('rao-run', help='FD table, time-domain campaign and comparison; development only')
    rao_run.add_argument('--project', required=True, help='project directory')
    rao_run.add_argument('--case', required=True, help='registered RAO case artifact')
    rao_run.add_argument('--out-dir', required=True, help='new output directory (JSON, CSV, run records)')
    waves=sub.add_parser('inspect-wave-bundle',help='validate and sample stored HAMS waves without a numerical producer')
    waves.add_argument('--path',help='portable wave bundle directory; omitted uses bundled saved example')
    waves.add_argument('--component',choices=('incident','total','scattered','radiation'),default='total')
    waves.add_argument('--time',type=float,default=0.)
    waves.add_argument('--amplitude',type=float,default=.3)
    waves.add_argument('--phase-degrees',type=float,default=0.)
    waves.add_argument('--frequency-index',type=int,default=0)
    waves.add_argument('--heading-index',type=int,default=0)
    waves.add_argument('--dof-index',type=int,default=0)
    waves.add_argument('--displacement',type=float,default=.01,help='prescribed radiation displacement in m or rad for selected DOF')
    waves.add_argument('--save-copy',help='new portable bundle directory')
    hams_inspect=sub.add_parser('inspect-hams-config',help='validate explicit HAMS configuration and diagnose native availability without execution')
    hams_inspect.add_argument('--config',required=True)
    hams_inspect.add_argument('--project',help='saved ANYmarine project directory; omitted uses bundled HAMS producing geometry')
    hams_run=sub.add_parser('run-hams-fields',help='execute an explicitly configured native HAMS field job in an isolated child; unqualified')
    hams_run.add_argument('--config',required=True)
    hams_run.add_argument('--project',help='saved ANYmarine project directory; omitted uses bundled HAMS producing geometry')
    hams_run.add_argument('--workdir',required=True,help='new exclusive evidence directory; must not already exist')
    return parser


def _cmd_new(args) -> int:
    service = ApplicationService()
    project = service.new_project(args.name)
    root = Path(args.path)
    if (root / "project.json").exists():
        print(f"refusing to overwrite existing project at {root}", file=sys.stderr)
        return 2
    service.save(project, root)
    print(f"created project '{project.name}' at {root}")
    for body in project.bodies:
        print(f"  body {body.body_id} '{body.name}'")
    return 0


def _cmd_run(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    overrides = {}
    if args.duration is not None:
        overrides["duration"] = args.duration
    if args.dt is not None:
        overrides["dt"] = args.dt
    settings = project.settings.with_edits(**overrides) if overrides else project.settings

    if args.local:
        result = service.run_synthetic_local(project, settings)
    else:
        result = _run_child_job(service, project, settings)
    project = service.add_result(project, result)
    service.save(project)
    stale = service.stale_result_ids(project)
    print(f"result {result.result_id} kind={result.kind} frames={result.frame_count()}")
    print(f"input_hash={result.input_hash}")
    print(f"note: {result.note}")
    if stale:
        print(f"stale results: {', '.join(stale)}")
    return 0


def _run_child_job(service: ApplicationService, project, settings: AnalysisSettings):
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="anymarine-job-"))
    input_path = workdir / "input.json"
    output_path = workdir / "output.json"
    cancel_path = workdir / "cancel"
    snapshot = service.job_input_snapshot(project, settings)
    service.write_job_input(snapshot, input_path)
    command = [
        sys.executable,
        "-m",
        "anymarine.worker",
        str(input_path),
        str(output_path),
        str(cancel_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        env=_child_environment(),
        timeout=WORKER_TIMEOUT_SECONDS,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return service.load_job_output(
            output_path, snapshot["input_hash"], snapshot=snapshot
        )
    message = (completed.stderr or completed.stdout or "").strip()
    raise ServiceError(
        f"worker failed with exit code {completed.returncode}: {message or 'no output'}"
    )


def _cmd_run_linear(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    if args.demo and args.inputs:
        print("error: pass either --demo or --inputs, not both", file=sys.stderr)
        return 2
    if args.demo:
        inputs = service.linear_demo_inputs(project)
        # New inputs: persist them, replacing any stored inputs and the
        # hydro provenance they produced.
        project = service.set_linear_inputs(project, inputs)
    elif args.inputs:
        try:
            data = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"error: cannot read explicit inputs {args.inputs}: {error}", file=sys.stderr)
            return 2
        inputs = service.load_explicit_linear_inputs(project, data)
        project = service.set_linear_inputs(project, inputs)
    elif project.linear_inputs is not None:
        # Reuse the stored inputs unchanged: keep the project and its hydro
        # provenance intact. set_linear_inputs is only for admitting new
        # inputs; the run route below re-verifies the binding, the effective
        # hydro inputs and the hydro provenance before running.
        inputs = project.linear_inputs
    else:
        print(
            "error: the project has no stored linear inputs; pass --demo to "
            "create the labelled analytic demo or --inputs FILE",
            file=sys.stderr,
        )
        return 2

    if args.local:
        result = service.run_linear_local(project, inputs)
    else:
        result = _run_linear_child_job(service, project, inputs)
    project = service.add_result(project, result)
    service.save(project)
    stale = service.stale_result_ids(project)
    print(f"result {result.result_id} kind={result.kind} frames={result.frame_count()}")
    print(f"input_hash={result.input_hash}")
    print(f"note: {result.note}")
    if stale:
        print(f"stale results: {', '.join(stale)}")
    return 0


def _run_linear_child_job(service: ApplicationService, project, inputs):
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="anymarine-job-"))
    input_path = workdir / "input.json"
    output_path = workdir / "output.json"
    cancel_path = workdir / "cancel"
    snapshot = service.linear_job_snapshot(project, inputs)
    service.write_job_input(snapshot, input_path)
    command = [
        sys.executable,
        "-m",
        "anymarine.worker",
        str(input_path),
        str(output_path),
        str(cancel_path),
    ]
    completed = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        env=_child_environment(),
        timeout=WORKER_TIMEOUT_SECONDS,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return service.load_job_output(
            output_path, snapshot["input_hash"], snapshot=snapshot
        )
    message = (completed.stderr or completed.stdout or "").strip()
    raise ServiceError(
        f"worker failed with exit code {completed.returncode}: {message or 'no output'}"
    )


def _cmd_import_hydro(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    try:
        options = json.loads(Path(args.options).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: cannot read import options {args.options}: {error}", file=sys.stderr)
        return 2
    if not isinstance(options, dict):
        print(f"error: import options {args.options} must be a JSON object", file=sys.stderr)
        return 2
    project = service.import_hydro_inputs(project, args.dataset, args.kernel, options)
    service.save(project)
    provenance = project.hydro_provenance
    inputs = project.linear_inputs
    print(f"imported ANYhydro inputs into project '{project.name}'")
    print(f"input_hash={provenance.linear_input_hash}")
    print(f"dataset content hash: {provenance.dataset_content_hash}")
    print(f"kernel content hash: {provenance.kernel_content_hash}")
    print(
        "reconstruction: "
        f"overall_pass={provenance.reconstruction_report['overall_pass']} "
        f"(max A error {provenance.reconstruction_report['max_added_mass_error']:.6g}, "
        f"max B error {provenance.reconstruction_report['max_damping_error']:.6g})"
    )
    print(
        "source: ANYhydro hydrodynamic dataset (development source, unqualified; "
        "not a qualified marine analysis)"
    )
    print(f"stored linear inputs: dt={inputs.dt} n_steps={inputs.n_steps} frames")
    print("run with: anymarine run-linear --project <dir>")
    return 0


def _cmd_import_derived_hydro(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    try:
        options = json.loads(Path(args.options).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: cannot read import options {args.options}: {error}", file=sys.stderr)
        return 2
    if not isinstance(options, dict):
        print(f"error: import options {args.options} must be a JSON object", file=sys.stderr)
        return 2
    project = service.import_derived_hydro_inputs(
        project, args.dataset, args.operators, args.kernel, options
    )
    service.save(project)
    provenance = project.hydro_provenance
    inputs = project.linear_inputs
    bundle = provenance.derived_bundle
    print(f"imported ANYhydro derived-operator inputs into project '{project.name}'")
    print(f"input_hash={provenance.linear_input_hash}")
    print(f"dataset content hash: {provenance.dataset_content_hash}")
    print(f"derived artifact content hash: {bundle['derived_content_hash']}")
    print(f"kernel content hash: {provenance.kernel_content_hash}")
    print(
        "reconstruction: "
        f"overall_pass={provenance.reconstruction_report['overall_pass']} "
        f"(max A error {provenance.reconstruction_report['max_added_mass_error']:.6g}, "
        f"max B error {provenance.reconstruction_report['max_damping_error']:.6g})"
    )
    print(
        "source: ANYhydro derived operator artifact (explicit derived route, "
        "development source, unqualified; not a qualified marine analysis)"
    )
    print(f"stored linear inputs: dt={inputs.dt} n_steps={inputs.n_steps} frames")
    print("run with: anymarine run-linear --project <dir>")
    return 0


def _cmd_list_results(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    if not project.results:
        print("no stored results")
        return 0
    stale = set(service.stale_result_ids(project))
    for result in project.results:
        flag = " STALE" if result.result_id in stale else ""
        print(
            f"{result.result_id} kind={result.kind} frames={result.frame_count()} "
            f"bodies={len(result.body_ids)}{flag}"
        )
    return 0


def _cmd_validate(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    service.verify_hydro_provenance(project)
    print(f"project '{project.name}' at {args.project} is valid")
    print(f"bodies: {len(project.bodies)}, results: {len(project.results)}")
    print(f"input_hash: {service.input_hash(project)}")
    if project.hydro_provenance is not None:
        provenance = project.hydro_provenance
        print(
            "hydro import: dataset "
            f"{provenance.dataset_content_hash[:12]}... kernel "
            f"{provenance.kernel_content_hash[:12]}... (development source, "
            "unqualified)"
        )
    return 0


def _cmd_prepare_assessment(args) -> int:
    from .linear_assessment import write_case_artifact

    service = ApplicationService()
    project = service.load(args.project)
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ServiceError(f"cannot read the case specification {args.spec}: {error}")
    case = service.prepare_linear_assessment(project, spec)
    path = write_case_artifact(case, args.out)
    print(f"registered case {case.case_id} mode={case.mode}")
    print(f"case_hash={case.case_hash}")
    print(f"input_hash={case.input_hash}")
    print(f"case artifact: {path}")
    print(
        "scope: development only, unqualified; never a qualified marine analysis"
    )
    return 0


def _cmd_assess_linear(args) -> int:
    from .linear_assessment import read_case_artifact, write_report_artifact

    service = ApplicationService()
    project = service.load(args.project)
    case = read_case_artifact(args.case)
    result_id = args.result_id or case.result_id
    if result_id is None:
        print(
            "error: the case selects no result_id; pass --result-id explicitly",
            file=sys.stderr,
        )
        return 2
    matches = [record for record in project.results if record.result_id == result_id]
    if len(matches) != 1:
        raise ServiceError(
            f"the project must hold exactly one result with id {result_id!r}; "
            f"found {len(matches)}"
        )
    report = service.assess_linear_result(project, matches[0], case)
    path = write_report_artifact(report, args.out)
    print(f"verdict: {report.verdict} (case {case.case_id}, result {result_id})")
    if report.failed:
        print(f"failed checks: {', '.join(report.failed)}")
    print(f"report artifact: {path}")
    print(
        "scope: development only, unqualified; never a qualified marine analysis"
    )
    return 0 if report.verdict == "pass" else 1


def _cmd_adjudicate_assessment(args) -> int:
    service = ApplicationService()
    outcome = service.adjudicate_linear_assessment(
        args.snapshot, args.case, args.report
    )
    print(f"adjudication: agrees=True verdict={outcome['verdict']}")
    print(f"stored report hash: {outcome['stored_report_hash']}")
    print(f"recomputed report hash: {outcome['recomputed_report_hash']}")
    print(
        "scope: development only, unqualified; never a qualified marine analysis"
    )
    return 0


def _select_result(project, result_id):
    matches = [record for record in project.results if record.result_id == result_id]
    if len(matches) != 1:
        raise ServiceError(
            f"the project must hold exactly one result with id {result_id!r}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _cmd_assess_refinement(args) -> int:
    from .linear_assessment import read_case_artifact

    service = ApplicationService()
    members = {}
    for role in ("coarse", "fine"):
        project = service.load(getattr(args, f"{role}_project"))
        members[role] = (
            project,
            _select_result(project, getattr(args, f"{role}_result_id")),
            read_case_artifact(getattr(args, f"{role}_case")),
            Path(getattr(args, f"{role}_project")),
        )
    coarse_project, coarse_result, coarse_case, coarse_root = members["coarse"]
    fine_project, fine_result, fine_case, fine_root = members["fine"]
    snapshot = service.save_paired_refinement_snapshot(
        coarse_project,
        coarse_result,
        coarse_case,
        coarse_root,
        fine_project,
        fine_result,
        fine_case,
        fine_root,
        args.snapshot,
    )
    paired = service.adjudicate_paired_refinement_snapshot(snapshot)
    verdict = paired["combined_verdict"]
    print(f"paired refinement verdict: {verdict}")
    print(f"dt pair snapshot: {snapshot}")
    print(f"reproduced from the snapshot: agrees={paired['agrees']}")
    print(
        "scope: development only, unqualified; never a qualified marine analysis"
    )
    return 0 if verdict == "pass" else 1


def _cmd_adjudicate_refinement(args) -> int:
    service = ApplicationService()
    outcome = service.adjudicate_paired_refinement_snapshot(args.snapshot)
    print(f"paired adjudication: agrees=True verdict={outcome['combined_verdict']}")
    print(f"stored pair hash: {outcome['stored_pair_hash']}")
    print(f"recomputed pair hash: {outcome['recomputed_pair_hash']}")
    print(
        "scope: development only, unqualified; never a qualified marine analysis"
    )
    return 0 if outcome["combined_verdict"] == "pass" else 1


def _cmd_import_native(args) -> int:
    service = ApplicationService()
    project = service.load(args.project)
    try:
        data = json.loads(Path(args.inputs).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ServiceError(f'cannot read native inputs: {error}') from error
    candidate = service.load_native_inputs(project, data)
    service.save(candidate)
    print('Stored validated native mechanical inputs; no numerical job executed.')
    return 0


def _cmd_run_native(args) -> int:
    import tempfile
    from .persistence import atomic_write_json
    service = ApplicationService()
    project = service.load(args.project)
    try:
        if getattr(args,'command',None)=='run-equilibrium':
            static_spec=None
            if args.static_spec:
                try:static_spec=json.loads(Path(args.static_spec).read_text(encoding='utf-8'))
                except (OSError,ValueError) as error:raise ServiceError(f'cannot read static controls: {error}') from error
            snapshot=service.equilibrium_input_snapshot(project,static_spec=static_spec,
                max_frames=args.max_frames,wall_seconds=args.wall_seconds)
        else:
            snapshot = service.native_input_snapshot(project, max_frames=args.max_frames,
                                                     wall_seconds=args.wall_seconds)
    except ValueError as error:
        raise ServiceError(str(error)) from error
    workdir = Path(tempfile.mkdtemp(prefix='anymarine-native-'))
    print(f'Native job evidence: {workdir}')
    input_path, output_path, cancel_path = workdir/'input.json', workdir/'output.json', workdir/'cancel'
    service.write_job_input(snapshot, input_path)
    environment = _child_environment()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        environment[name] = '1'
    command = [sys.executable, '-m', 'anymarine.worker', str(input_path), str(output_path), str(cancel_path)]
    interrupted = False
    timed_out = False
    with (workdir/'stdout.log').open('wb') as stdout, (workdir/'stderr.log').open('wb') as stderr:
        child = subprocess.Popen(command, env=environment, stdout=stdout, stderr=stderr)
        try:
            code = child.wait(timeout=args.wall_seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            interrupted = isinstance(error, KeyboardInterrupt)
            timed_out = not interrupted
            try:
                cancel_path.touch()
            except OSError:
                # The owned child must not outlive a failed cancellation signal.
                child.kill()
            try:
                code = child.wait(timeout=5.)
            except subprocess.TimeoutExpired:
                child.kill()
                code = child.wait()
    atomic_write_json(workdir/'job-status.json', {'command': command, 'exit_code': code,
        'interrupted': interrupted, 'deadline_exceeded': timed_out, 'input_hash': snapshot['input_hash']})
    if interrupted:
        print('Native job cancelled; partial evidence and logs retained.')
        return 130
    if timed_out or code != 0:
        raise ServiceError(f'native job failed (exit {code}); evidence retained at {workdir}')
    seals = [line.removeprefix('OUTPUT_SHA256 ').strip()
             for line in (workdir/'stdout.log').read_text(encoding='utf-8', errors='replace').splitlines()
             if line.startswith('OUTPUT_SHA256 ')]
    if len(seals) != 1:
        raise ServiceError(f'native worker produced no unique output seal; evidence at {workdir}')
    result = service.load_job_output(output_path, snapshot['input_hash'], snapshot=snapshot,
                                     expected_output_seal=seals[0])
    # Reopen after execution: never overwrite edits made while this job ran.
    current = service.load(args.project)
    service.save(service.add_result(current, result))
    print(f'Stored native development result {result.result_id}, frames={result.frame_count()}, '
          f"{'stale' if service.result_stale(current, result) else 'current'}, unqualified.")
    return 0


def _hams_config_project(args):
    from .hams_wave_replay import load_hams_wave_example
    service=ApplicationService()
    try:config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    except (OSError,ValueError) as error:raise ServiceError('Cannot read HAMS configuration: '+str(error)) from error
    project=service.load(args.project) if args.project else load_hams_wave_example().project
    return service,project,config


def _cmd_inspect_hams_config(args):
    from .hams_jobs import validate_config
    service,project,config=_hams_config_project(args)
    try:c,bodies,request,build,_,_,points,limits=validate_config(project,config)
    except Exception as error:raise ServiceError('HAMS configuration refused: '+str(error)) from error
    available=True;reason=None
    try:service.hams_input_snapshot(project,c)
    except ServiceError as error:available=False;reason=str(error)
    print(json.dumps(dict(status='development_unqualified',producer_available=available,availability_scope='executable_identity_and_native_platform_only',reason=reason,
        producer=build.identity(),bodies=len(bodies),frequencies=len(request.omegas),headings=len(request.wave_directions),
        field_points=len(points),wall_seconds=limits.wall_seconds,numerical_calculation=False),sort_keys=True))
    return 0


def _cmd_run_hams_fields(args):
    import time
    from .persistence import atomic_write_json
    service,project,config=_hams_config_project(args)
    snapshot=service.hams_input_snapshot(project,config)
    workdir=Path(args.workdir)
    try:workdir.mkdir(parents=True,exist_ok=False)
    except OSError as error:raise ServiceError('New exclusive HAMS job directory required: '+str(error)) from error
    inp,out,cancel=workdir/'input.json',workdir/'output.json',workdir/'cancel'
    service.write_job_input(snapshot,inp)
    command=[sys.executable,'-m','anymarine.worker',str(inp.resolve()),str(out.resolve()),str(cancel.resolve())]
    environment=_child_environment()
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):environment[key]='1'
    start=time.perf_counter();interrupted=False;timed_out=False;code=None
    child=None;primary_error=None;cleanup_errors=[]
    try:
        with (workdir/'stdout.log').open('wb') as stdout,(workdir/'stderr.log').open('wb') as stderr:
            child=subprocess.Popen(command,env=environment,stdout=stdout,stderr=stderr)
            try:code=child.wait(timeout=snapshot['execution_limits']['wall_seconds'])
            except (subprocess.TimeoutExpired,KeyboardInterrupt) as error:
                interrupted=isinstance(error,KeyboardInterrupt);timed_out=not interrupted
                try:cancel.touch()
                except OSError:child.kill()
                try:code=child.wait(timeout=5.)
                except (subprocess.TimeoutExpired,KeyboardInterrupt) as error:
                    interrupted=interrupted or isinstance(error,KeyboardInterrupt)
                    child.kill()
                    try:code=child.wait(timeout=5.)
                    except BaseException as error:cleanup_errors.append(f'{type(error).__name__}: {error}')
    except BaseException as error:
        primary_error=f'{type(error).__name__}: {error}'
        interrupted=interrupted or isinstance(error,KeyboardInterrupt)
    finally:
        if child is not None and child.poll() is None:
            try:child.kill()
            except BaseException as error:cleanup_errors.append(f'{type(error).__name__}: {error}')
            try:code=child.wait(timeout=5.)
            except BaseException as error:cleanup_errors.append(f'{type(error).__name__}: {error}')
        cleanup_uncertain=bool(child is not None and child.poll() is None)
        status=dict(command=command,exit_code=code,interrupted=interrupted,
            deadline_exceeded=timed_out,input_hash=snapshot['input_hash'],wall_seconds=time.perf_counter()-start,
            primary_error=primary_error,cleanup_errors=cleanup_errors,cleanup_uncertain=cleanup_uncertain,
            state='cancelled' if interrupted else 'failed' if timed_out or primary_error or code!=0 or cleanup_uncertain else 'worker_exited',
            environment={k:environment[k] for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')})
        atomic_write_json(workdir/'job-status.json',status)
    if interrupted:return 130
    if timed_out or primary_error or cleanup_uncertain or code!=0:
        raise ServiceError(f'HAMS child failed (exit {code}); retained evidence: {workdir}')
    seals=[line.split(' ',1)[1] for line in (workdir/'stdout.log').read_text(encoding='utf-8').splitlines() if line.startswith('OUTPUT_SHA256 ')]
    if len(seals)!=1:raise ServiceError('HAMS worker produced no unique output seal; evidence: '+str(workdir))
    record=service.load_hams_job_output(out,snapshot,seals[0])
    status['state']='completed_development_unqualified'
    atomic_write_json(workdir/'job-status.json',status)
    stale=bool(args.project and not record.matches(service.load(args.project)))
    print(json.dumps(dict(status='completed_development_unqualified',stale=stale,project_hash=record.project_hash,
        field_hash=record.fields.content_hash(),wave_bundle=str(workdir/'hams-wave-bundle'),evidence=str(workdir)),sort_keys=True))
    return 0


def _utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _rao_out_dir(path):
    out = Path(path)
    if out.exists():
        raise ServiceError(f'{out} exists; RAO evidence is never overwritten')
    out.mkdir(parents=True)
    return out


def _cmd_rao_prepare(args) -> int:
    from . import rao_case, rao_report
    project = ApplicationService().load(args.project)
    try:
        spec = json.loads(Path(args.spec).read_text(encoding='utf-8'))
        case = rao_report.prepare_case(project, spec, created_utc=_utc_now())
        path = rao_case.write_case_artifact(case, args.out)
    except (OSError, ValueError) as error:
        raise ServiceError(f'RAO case refused: {error}') from error
    print(f'registered RAO case {case.case_id} category={case.category}')
    print(f'case_hash={case.case_hash}')
    print(f'case artifact: {path}')
    print('scope: development only, unqualified; criteria adopted: ' + str(case.scope['criteria_adopted']))
    return 0


def _cmd_rao_table(args) -> int:
    from . import rao_case, rao_report
    project = ApplicationService().load(args.project)
    try:
        case = rao_case.read_case_artifact(args.case)
        table = rao_report.fd_table(project, case)
        out = _rao_out_dir(args.out_dir)
        (out / 'rao_table.json').write_text(json.dumps(table, indent=1), encoding='utf-8')
        rao_report.write_csv(out, table)
    except (OSError, ValueError) as error:
        raise ServiceError(f'RAO table refused: {error}') from error
    print(f'RAO table {table["table_hash"]}: {len(table["entries"])} entries -> {out}')
    return 0


def _cmd_rao_run(args) -> int:
    from . import rao_case, rao_report
    project = ApplicationService().load(args.project)
    try:
        case = rao_case.read_case_artifact(args.case)
        table = rao_report.fd_table(project, case)
        out = _rao_out_dir(args.out_dir)
        records, extractions = rao_report.run_campaign(
            project, case, progress=lambda n: print(f'run {n} extracted', flush=True))
        report = rao_report.compare(case, table, extractions)
        runs = out / 'runs'
        runs.mkdir()
        for record in records:
            (runs / f'{record.result_id}.json').write_text(json.dumps(record.to_dict()), encoding='utf-8')
        (out / 'rao_table.json').write_text(json.dumps(table, indent=1), encoding='utf-8')
        (out / 'rao_report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
        rao_report.write_csv(out, table, report)
    except (OSError, ValueError) as error:
        raise ServiceError(f'RAO run refused: {error}') from error
    print(f'RAO report {report["report_hash"]}: category={report["category"]} verdict={report["verdict"]}')
    print(f'failed checks: {sorted(set(report["failed"])) or "none"}')
    return 2 if report['verdict'] == 'nogo' else 0


def _cmd_inspect_wave_bundle(args):
    import math
    service=ApplicationService();project,record=service.load_hams_wave_bundle(args.path)
    selection=dict(frequency=args.frequency_index,heading=args.heading_index,amplitude=args.amplitude,
        phase=math.radians(args.phase_degrees),dof=args.dof_index,displacement=args.displacement)
    try:points=record.positions(args.component,args.time,**selection)
    except ValueError as error:raise ServiceError(str(error)) from error
    if args.save_copy:service.save_hams_wave_bundle(args.save_copy,record)
    print(json.dumps(dict(source_kind=record.source_kind,status='development_unqualified',project_hash=record.project_hash,
        parent_hash=record.parent_hash,field_hash=record.fields.content_hash(),component=args.component,time=args.time,
        omega=record.fields.omegas[args.frequency_index],surface_samples=len(points),pressure_samples=len(record.fields.points)-len(points),
        elevation_min_m=float(points[:,2].min()),elevation_max_m=float(points[:,2].max()),numerical_calculation=False),sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    handlers = {
        'inspect-hams-config': _cmd_inspect_hams_config,
        'run-hams-fields': _cmd_run_hams_fields,
        "inspect-wave-bundle": _cmd_inspect_wave_bundle,
        "new": _cmd_new,
        "run": _cmd_run,
        "run-linear": _cmd_run_linear,
        "import-native": _cmd_import_native,
        "run-native": _cmd_run_native,
        "run-equilibrium": _cmd_run_native,
        "import-hydro": _cmd_import_hydro,
        "import-derived-hydro": _cmd_import_derived_hydro,
        "list-results": _cmd_list_results,
        "validate": _cmd_validate,
        "prepare-assessment": _cmd_prepare_assessment,
        "assess-linear": _cmd_assess_linear,
        "adjudicate-assessment": _cmd_adjudicate_assessment,
        "assess-refinement": _cmd_assess_refinement,
        "adjudicate-refinement": _cmd_adjudicate_refinement,
        "rao-prepare": _cmd_rao_prepare,
        "rao-table": _cmd_rao_table,
        "rao-run": _cmd_rao_run,
    }
    try:
        return handlers[args.command](args)
    except (ProjectError, ServiceError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
