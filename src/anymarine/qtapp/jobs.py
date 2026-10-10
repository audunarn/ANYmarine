"""Isolated job management for the Qt shell.

Jobs run in child processes (``python -m anymarine.worker``) fed a plain JSON
input snapshot. States: queued, running, cancelling, cancelled, failed,
completed. Cancellation is cooperative (cancel file) with bounded owned-process
termination. There is no partial success: a result is registered only when the
child exits 0 and its output artifact validates against the job's input hash.

Failure handling: a worker that fails to start never emits ``finished``; the
error handler transitions that job to failed exactly once and advances the
queue. Full stdout/stderr of every job are preserved on disk under the job
directory while in-memory tails stay bounded; progress records split across
byte chunks are reassembled before parsing.

Process policy: synthetic workers spawn no children. HAMS workers use ANYhydro
Windows atomic JobList/kill-on-close containment. Killing the worker closes its
noninherited Job Object and kills producer descendants. Linux HAMS Qt admission
remains refused until supervisor-death containment is verified.

All signals are delivered on the GUI thread (QProcess + QTimer).

Assessment jobs (``anymarine.assessment_jobs`` kinds) share the same
queue/process policy. Their validated evidence is emitted through
``assessment_ready``; a validated NoGo verdict is a distinct terminal
``nogo`` state (non-success, report preserved), never ``completed`` and
never a generic failure. Assessment evidence is never registered as a
ResultRecord.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ..domain import AnalysisSettings, LinearDevelopmentInputs, ProjectModel, ResultRecord
from ..services import ApplicationService, ServiceError
from ..assessment_jobs import (
    ASSESSMENT_JOB_KINDS,
    ASSESSMENT_KIND_PREPARE,
    VERDICT_NOGO,
    VERDICT_PASS,
    AssessmentJobError,
    load_assessment_job_output,
)

JOB_STATE_QUEUED = "queued"
JOB_STATE_RUNNING = "running"
JOB_STATE_CANCELLING = "cancelling"
JOB_STATE_CANCELLED = "cancelled"
JOB_STATE_FAILED = "failed"
JOB_STATE_COMPLETED = "completed"
JOB_STATE_NOGO = "nogo"
ACTIVE_STATES = (JOB_STATE_QUEUED, JOB_STATE_RUNNING, JOB_STATE_CANCELLING)

CANCEL_GRACE_MS = 5000
MAX_KEPT_OUTPUT_LINES = 200


def terminate_process_tree(process: QProcess) -> None:
    """Kill the worker; admitted Windows HAMS Job Object closure kills descendants."""
    process.kill()


@dataclass
class JobRecord:
    job_id: str
    state: str = JOB_STATE_QUEUED
    kind: str = ""
    progress: float = 0.0
    input_path: Path = field(default_factory=Path)
    output_path: Path = field(default_factory=Path)
    cancel_path: Path = field(default_factory=Path)
    stdout_path: Path = field(default_factory=Path)
    stderr_path: Path = field(default_factory=Path)
    input_hash: str = ""
    snapshot: dict = field(default_factory=dict)
    output_seal: str = ""
    process: Optional[QProcess] = None
    cancel_timer: Optional[QTimer] = None
    deadline_timer: Optional[QTimer] = None
    deadline_exceeded: bool = False
    output_lines: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    stdout_buffer: str = ""

    def note(self, line: str) -> None:
        self.diagnostics.append(line)
        del self.diagnostics[:-MAX_KEPT_OUTPUT_LINES]

    def log_stdout(self, text: str) -> None:
        """Preserve the full stdout on disk (unbounded, per job directory)."""

        if self.stdout_path != Path():
            with self.stdout_path.open("a", encoding="utf-8") as stream:
                stream.write(text)

    def log_stderr(self, text: str) -> None:
        """Preserve the full stderr on disk (unbounded, per job directory)."""

        if self.stderr_path != Path():
            with self.stderr_path.open("a", encoding="utf-8") as stream:
                stream.write(text)


class JobManager(QObject):
    """Owns child job processes; one job runs at a time, extras stay queued."""

    state_changed = Signal(str, str)          # job_id, state
    progress_changed = Signal(str, float)     # job_id, fraction
    result_ready = Signal(str, ResultRecord)  # job_id, validated result
    assessment_ready = Signal(str, dict)      # job_id, validated assessment payload
    hams_ready = Signal(str, object)
    wave_ready = Signal(str, dict)
    adoption_ready = Signal(str, object)
    job_failed = Signal(str, str)            # job_id, message

    def __init__(self, service: ApplicationService, parent: QObject | None = None):
        super().__init__(parent)
        self._service = service
        self._jobs: dict[str, JobRecord] = {}
        self._queue: list[str] = []
        self._workroot = Path(tempfile.mkdtemp(prefix="anymarine-jobs-"))

    # ---------------------------------------------------------------- public

    def jobs(self) -> dict[str, JobRecord]:
        return dict(self._jobs)

    def job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def active_jobs(self) -> list[str]:
        return [job_id for job_id, job in self._jobs.items() if job.state in ACTIVE_STATES]

    def submit(
        self,
        project: ProjectModel,
        settings: AnalysisSettings | None = None,
        linear_inputs: LinearDevelopmentInputs | None = None,
    ) -> str:
        """Queue a synthetic replay or linear development job; returns the id.

        ``linear_inputs`` submits a ``linear_development`` job through the
        same isolated worker protocol; the snapshot then carries the full
        effective numerical inputs and the body binding.
        """

        if linear_inputs is not None:
            snapshot = self._service.linear_job_snapshot(project, linear_inputs)
        else:
            snapshot = self._service.job_input_snapshot(project, settings)
        return self._enqueue(snapshot)

    def submit_native(self, project: ProjectModel, *, max_frames=1001, wall_seconds=60.) -> str:
        """Queue saved native inputs; widgets never execute the solver."""
        return self._enqueue(self._service.native_input_snapshot(
            project, max_frames=max_frames, wall_seconds=wall_seconds))

    def submit_adoption(self,project,result,**options):
        return self._enqueue(self._service.adoption_input_snapshot(project,result,**options))

    def submit_equilibrium(self,project,**options):
        return self._enqueue(self._service.equilibrium_input_snapshot(project,**options))

    def submit_hams(self, project, config):
        return self._enqueue(self._service.hams_input_snapshot(project, config))

    def submit_wave(self, request, *, wall_seconds=20.):
        from ..wave_jobs import wave_input_snapshot
        return self._enqueue(wave_input_snapshot(request, wall_seconds=wall_seconds))

    def submit_assessment(self, snapshot: dict) -> str:
        """Queue a prebuilt frozen assessment job; returns the id.

        The snapshot must come from the Qt-free
        :mod:`anymarine.assessment_jobs` builders (complete inputs frozen
        before execution). Assessment jobs never produce a ResultRecord:
        their validated evidence arrives through ``assessment_ready`` and
        a NoGo verdict transitions to the distinct non-success ``nogo``
        state, never ``completed``.
        """

        return self._enqueue(snapshot)

    def _enqueue(self, snapshot: dict) -> str:
        job_id = str(uuid.uuid4())
        job_dir = self._workroot / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(job_id=job_id)
        record.input_path = job_dir / "input.json"
        record.output_path = job_dir / "output.json"
        record.cancel_path = job_dir / "cancel"
        record.stdout_path = job_dir / "stdout.log"
        record.stderr_path = job_dir / "stderr.log"
        # Preserve empty logs even when the worker fails before any output.
        record.stdout_path.touch(exist_ok=False)
        record.stderr_path.touch(exist_ok=False)
        record.kind = str(snapshot["kind"])
        record.input_hash = snapshot["input_hash"]
        import json
        plain_snapshot = json.loads(json.dumps(snapshot, allow_nan=False))
        self._service.write_job_input(plain_snapshot, record.input_path)
        if record.kind in ('mechanical_development', 'wave_preview','equilibrium_development','equilibrium_adoption_development','hams_field_development'):
            from ..domain import _freeze_json_value
            record.snapshot = _freeze_json_value(plain_snapshot, 'native job snapshot')
        else:
            record.snapshot = plain_snapshot
        record.note("queued; input snapshot written")
        self._jobs[job_id] = record
        self._queue.append(job_id)
        self.state_changed.emit(job_id, record.state)
        self._pump()
        return job_id

    def request_cancel(self, job_id: str) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        if record.state == JOB_STATE_QUEUED:
            self._queue.remove(job_id)
            self._finish(record, JOB_STATE_CANCELLED, "cancelled before start")
            return
        if record.state != JOB_STATE_RUNNING:
            return
        record.state = JOB_STATE_CANCELLING
        record.note("cancellation requested (cooperative)")
        self.state_changed.emit(job_id, record.state)
        try:
            record.cancel_path.touch()
        except OSError as error:
            record.note(f"could not write cancel file: {error}")
        timer = QTimer(self)
        timer.setSingleShot(True)

        def escalate():
            process = record.process
            if record.state == JOB_STATE_CANCELLING and process is not None:
                record.note(
                    "cooperative cancellation exceeded bound; terminating process tree"
                )
                terminate_process_tree(process)

        timer.timeout.connect(escalate)
        timer.start(CANCEL_GRACE_MS)
        record.cancel_timer = timer

    def cancel_all(self) -> None:
        for job_id in list(self._jobs):
            if self._jobs[job_id].state in ACTIVE_STATES:
                self.request_cancel(job_id)

    # --------------------------------------------------------------- private

    def _pump(self) -> None:
        running = any(job.state in (JOB_STATE_RUNNING, JOB_STATE_CANCELLING) for job in self._jobs.values())
        if running:
            return
        while self._queue:
            job_id = self._queue[0]
            record = self._jobs.get(job_id)
            if record is None or record.state != JOB_STATE_QUEUED:
                self._queue.pop(0)
                continue
            self._start(record)
            return

    def _start(self, record: JobRecord) -> None:
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(
            [
                "-m",
                "anymarine.worker",
                str(record.input_path),
                str(record.output_path),
                str(record.cancel_path),
            ]
        )
        environment = QProcessEnvironment.systemEnvironment()
        package_parent = str(Path(__import__("anymarine").__file__).resolve().parent.parent)
        parts = [package_parent]
        # Optional solver location for linear development jobs; nothing is
        # invented here and an absent solver fails closed in the worker.
        solver_path = os.environ.get("ANYMARINE_SOLVER_PATH", "")
        if solver_path:
            parts.append(solver_path)
        existing = os.environ.get("PYTHONPATH", "")
        if existing:
            parts.append(existing)
        environment.insert("PYTHONPATH", os.pathsep.join(parts))
        if record.kind in ('mechanical_development', 'wave_preview','equilibrium_development','equilibrium_adoption_development','hams_field_development'):
            for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
                environment.insert(name, '1')
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        record.process = process
        record.state = JOB_STATE_RUNNING
        record.note("started worker process")
        self.state_changed.emit(record.job_id, record.state)

        process.started.connect(lambda: None)
        process.readyReadStandardOutput.connect(
            lambda: self._on_stdout(record)
        )
        process.readyReadStandardError.connect(
            lambda: self._on_stderr(record)
        )
        process.finished.connect(
            lambda code, status: self._on_finished(record, code, status)
        )
        process.errorOccurred.connect(
            lambda error: self._on_process_error(record, error)
        )
        if record.kind in ('mechanical_development', 'wave_preview','equilibrium_development','equilibrium_adoption_development','hams_field_development'):
            record.deadline_timer = QTimer(self)
            record.deadline_timer.setSingleShot(True)
            record.deadline_timer.timeout.connect(lambda: self._native_deadline(record))
            record.deadline_timer.start(int(record.snapshot['execution_limits']['wall_seconds'] * 1000))
        process.start()

    def _native_deadline(self, record: JobRecord) -> None:
        if record.state != JOB_STATE_RUNNING:
            return
        record.deadline_exceeded = True
        record.note('native execution deadline exceeded; requesting cancellation')
        self.request_cancel(record.job_id)

    def _on_stdout(self, record: JobRecord) -> None:
        if record.process is None:
            return
        data = bytes(record.process.readAllStandardOutput().data()).decode("utf-8", "replace")
        # Full log on disk; bounded tail in memory.
        record.log_stdout(data)
        # Progress records can be split across byte chunks: buffer and only
        # handle complete lines.
        record.stdout_buffer += data
        *complete, record.stdout_buffer = record.stdout_buffer.split("\n")
        for line in complete:
            self._handle_stdout_line(record, line)

    def _handle_stdout_line(self, record: JobRecord, line: str) -> None:
        record.output_lines.append(line)
        del record.output_lines[:-MAX_KEPT_OUTPUT_LINES]
        text = line.strip()
        if text.startswith("OUTPUT_SHA256 "):
            # Trusted producer digest of the child's whole output payload.
            # Assessment admission requires it, so a replaced or re-sealed
            # output file cannot masquerade as the child's evidence.
            digest = text[len("OUTPUT_SHA256 "):].strip()
            if len(digest) == 64 and record.output_seal == "":
                record.output_seal = digest
                record.note("stdout: producer output seal captured")
            return
        if text.startswith("PROGRESS "):
            try:
                fraction = min(1.0, max(0.0, float(text.split()[1])))
            except (IndexError, ValueError):
                return
            record.progress = fraction
            self.progress_changed.emit(record.job_id, fraction)
        elif text:
            record.note(f"stdout: {text}")

    def _flush_stdout_buffer(self, record: JobRecord) -> None:
        """Handle a trailing line that ended without a newline."""

        if record.stdout_buffer:
            remainder = record.stdout_buffer
            record.stdout_buffer = ""
            self._handle_stdout_line(record, remainder)

    def _on_stderr(self, record: JobRecord) -> None:
        if record.process is None:
            return
        data = bytes(record.process.readAllStandardError().data()).decode("utf-8", "replace")
        record.log_stderr(data)
        for line in data.splitlines():
            if line.strip():
                record.note(f"stderr: {line.strip()}")

    def _on_process_error(self, record: JobRecord, error) -> None:
        record.note(f"process error: {error}")
        if error != QProcess.ProcessError.FailedToStart:
            # Crash/timeout/write/read errors are always followed by a
            # finished signal, which performs the transition.
            return
        if record.state not in ACTIVE_STATES:
            return  # already transitioned; never fail the same job twice
        # FailedToStart never emits finished: transition exactly once here,
        # then advance the queue so later jobs are not blocked forever.
        self._finish(record, JOB_STATE_FAILED, f"worker failed to start ({error})")
        self._pump()

    def _on_finished(self, record: JobRecord, exit_code: int, status) -> None:
        if record.cancel_timer is not None:
            record.cancel_timer.stop()
            record.cancel_timer = None
        if record.state not in (JOB_STATE_RUNNING, JOB_STATE_CANCELLING):
            # Already transitioned (e.g. failed to start); late signals are
            # recorded but never transition the same job twice.
            record.note(f"ignored late finished signal in state {record.state}")
            return
        self._flush_stdout_buffer(record)
        if record.state == JOB_STATE_CANCELLING:
            state = JOB_STATE_FAILED if record.deadline_exceeded else JOB_STATE_CANCELLED
            self._finish(record, state, "deadline exceeded" if record.deadline_exceeded else "cancelled")
            self._pump()
            return
        if status != QProcess.ExitStatus.NormalExit:
            self._finish(record, JOB_STATE_FAILED, f"worker crashed (status {status})")
            self._pump()
            return
        if exit_code != 0:
            state = JOB_STATE_CANCELLED if record.kind in ('mechanical_development','wave_preview','equilibrium_adoption_development','hams_field_development') and exit_code == 3 else JOB_STATE_FAILED
            self._finish(record, state, f"worker exit code {exit_code}")
            self._pump()
            return
        if record.kind == 'equilibrium_adoption_development':
            try:
                from ..adoption_jobs import load_output
                adopted=load_output(record.output_path,record.snapshot,record.output_seal)
            except Exception as error:
                self._finish(record,JOB_STATE_FAILED,str(error));self._pump();return
            record.progress=1.0
            self.progress_changed.emit(record.job_id,1.0)
            self._finish(record,JOB_STATE_COMPLETED,'equilibrium adoption audited; development, unqualified')
            self.adoption_ready.emit(record.job_id,adopted)
            self._pump();return
        if record.kind == 'hams_field_development':
            try:
                field = self._service.load_hams_job_output(record.output_path, record.snapshot, record.output_seal)
            except ServiceError as error:
                self._finish(record, JOB_STATE_FAILED, str(error)); self._pump(); return
            record.progress = 1.0
            self.progress_changed.emit(record.job_id, 1.0)
            self._finish(record, JOB_STATE_COMPLETED, 'source-bound HAMS fields reopened; development, unqualified')
            self.hams_ready.emit(record.job_id, field)
            self._pump(); return
        if record.kind == 'wave_preview':
            try:
                from ..wave_jobs import load_wave_output
                field = load_wave_output(record.output_path, record.snapshot, record.output_seal)
            except Exception as error:
                self._finish(record, JOB_STATE_FAILED, str(error))
                self._pump()
                return
            record.progress = 1.0
            self.progress_changed.emit(record.job_id, 1.0)
            self._finish(record, JOB_STATE_COMPLETED, 'fixed-body diffraction field validated; unqualified')
            self.wave_ready.emit(record.job_id, field)
            self._pump()
            return
        if record.kind in ASSESSMENT_JOB_KINDS:
            # Assessment evidence is never a ResultRecord: the payload is
            # transport/identity/hash validated here (against the exact
            # submitted job kind and frozen input) and the scientific
            # reproduction already ran in the child. A case registration
            # completes on its validated case artifact without any
            # assessment verdict. A validated PASS completes; a validated
            # NoGo transitions to the distinct non-success nogo state with
            # its report available — never a generic error, never completed.
            try:
                if not record.output_seal:
                    raise AssessmentJobError(
                        "the worker produced no canonical output seal; "
                        "unsealed evidence is refused"
                    )
                payload = load_assessment_job_output(
                    record.output_path,
                    record.input_hash,
                    expected_kind=record.kind,
                    expected_snapshot=record.snapshot,
                    expected_output_seal=record.output_seal,
                )
            except AssessmentJobError as error:
                self._finish(record, JOB_STATE_FAILED, str(error))
                self._pump()
                return
            record.progress = 1.0
            self.progress_changed.emit(record.job_id, 1.0)
            if record.kind == ASSESSMENT_KIND_PREPARE:
                self._finish(record, JOB_STATE_COMPLETED, "case artifact registered")
            else:
                verdict = str(payload.get("verdict"))
                if verdict == VERDICT_PASS:
                    self._finish(record, JOB_STATE_COMPLETED, "completed")
                elif verdict == VERDICT_NOGO:
                    self._finish(
                        record,
                        JOB_STATE_NOGO,
                        "assessment verdict 'nogo' (non-success; report preserved)",
                    )
                else:
                    self._finish(
                        record,
                        JOB_STATE_FAILED,
                        f"assessment verdict {verdict!r} is not admissible",
                    )
            self.assessment_ready.emit(record.job_id, payload)
            self._pump()
            return
        try:
            result = self._service.load_job_output(
                record.output_path, record.input_hash, snapshot=record.snapshot,
                expected_output_seal=record.output_seal
            )
        except ServiceError as error:
            self._finish(record, JOB_STATE_FAILED, str(error))
            self._pump()
            return
        record.progress = 1.0
        self.progress_changed.emit(record.job_id, 1.0)
        self._finish(record, JOB_STATE_COMPLETED, "completed")
        self.result_ready.emit(record.job_id, result)
        self._pump()

    def _finish(self, record: JobRecord, state: str, message: str) -> None:
        if record.deadline_timer is not None:
            record.deadline_timer.stop()
            record.deadline_timer = None
        record.state = state
        record.note(message)
        self.state_changed.emit(record.job_id, state)
        if state == JOB_STATE_FAILED:
            self.job_failed.emit(record.job_id, message)
