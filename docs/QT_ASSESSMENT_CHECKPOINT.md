# Qt assessment workflow — checkpoint (worker, 2026-10-08, focused repair + finish)

Status: connected implementation COMPLETE for the assigned scope; focused
repair (transport seal, admission hardening, physical-edit detachment)
landed and checked through the exact harness. This update records the
final artifact status; the route below is the implemented one.

## Actual findings (verified against source this run)

- Public service APIs confirmed at services.py:847-1159 (prepare/assess/
  save/adjudicate single + paired). `save_assessment_snapshot` re-proves the
  report from actual arrays before any write and refuses existing targets;
  `adjudicate_linear_assessment` loads the snapshot's own project (service
  `load` re-binds `self.root` to the snapshot project dir, so all source
  proofs run on the snapshot's own sidecars); `adjudicate_paired_snapshot`
  rebuilds both members from the snapshot root only.
- `AssessmentStore` layout: `cases/`, `reports/` content-hashed artifacts;
  paired snapshot layout `coarse/`, `fine/` (each `project/` + `cases/` +
  `reports/`) + `pair.json` with validated relative member refs.
- Check dicts: `{name, unit, value, limit, passed, detail}`; `value` is None
  and/or detail carries "NOTMEASURED" for explicitly unmeasured checks.
  Report payload: verdict/checks/failed/arrays/scope; paired record adds
  members{coarse,fine}/refinement/pairing/combined_verdict.
- `JobManager` (qtapp/jobs.py): one active QProcess, cooperative cancel +
  bounded termination, exactly-once transitions, durable logs; `_on_finished`
  validates through `service.load_job_output` (ResultRecord only) — needs an
  assessment branch. `worker.py` dispatches on `kind`, exit 0/2/3/4, atomic
  output, never spawns children.
- `ProjectModel.to_dict/from_dict` round-trips bodies/equilibrium/settings/
  results/linear_inputs/hydro_provenance. `ApplicationService.load` sets
  `self.root` to the loaded project root. `resolve_sidecar` refuses escapes.
- CLI already runs prepare/assess/adjudicate single+paired; the Qt workflow
  reuses the same service calls with frozen job transport.
- Test fixtures in tests/test_linear_assessment.py (analytic exponential
  regular-wave/free-decay project/inputs/spec) are reusable read-only.

## Chosen connected job/UI route (unchanged, plus adopted correction)

NEW Qt-free `src/anymarine/assessment_jobs.py` with job kinds
`assessment_prepare`, `assessment_single`, `assessment_pair`,
`assessment_adjudicate_single`, `assessment_adjudicate_pair`:

- Freeze BEFORE execution (submission time, GUI thread, no numerics):
  - prepare: trimmed project dict (bodies/equilibrium/settings/
    linear_inputs/hydro_provenance) + spec dict + exclusive case artifact
    target.
  - single: trimmed project dict + selected result dict + case dict +
    base64 raw hydro sidecar payloads hash-checked against the provenance
    (same pattern as `linear_job_snapshot`) + optional exclusive snapshot
    target.
  - pair: both members' frozen data + exclusive paired snapshot target.
  - adjudicate single/pair: the snapshot's OWN complete bytes — every file
    under the snapshot root as (relative path, sha256, base64 payload) —
    frozen at submission. A path+manifest alone is NOT immutable input;
    the child reconstructs a private snapshot root from the frozen bytes
    and never re-reads the mutable original root during execution/replay.
  - Job `input_hash` = sha256 over canonical JSON of the whole frozen
    snapshot; the child recomputes and refuses mismatch.
- Child execution (headless, in `assessment_jobs.execute` called from
  `worker.py`): reconstruct private roots (write embedded sidecars/payloads),
  call ONLY public service APIs, check the cancel file before/after each
  phase, write closed-world output JSON atomically (verdict, report/pair
  payload, snapshot file manifest, job hash). NoGo is a validated non-success
  outcome: exit 0 with verdict "nogo"; the JobManager transitions such jobs
  to a distinct `nogo` terminal state (never `completed`, never a generic
  error) and the report stays available for display.
- `load_assessment_job_output` validates transport/identity/hash for the GUI
  (never a qualification claim); for snapshot jobs it re-verifies the actual
  on-disk files against the child's manifest.
- `worker.py`: minimal dispatch of the new kinds (headless, no children).
- `qtapp/jobs.py`: `submit_assessment(snapshot)` + `assessment_ready` signal;
  `_on_finished` branches on assessment kinds; existing solve behavior and
  one-queue/one-process policy unchanged.
- NEW `qtapp/assessment.py`: `AssessmentChecksModel` (QAbstractTableModel:
  name/value/limit/unit/Pass-NoGo-NOTMEASURED/detail) + `AssessmentPanel`
  (verdict, developmental/unqualified scope, source route, bound input hash,
  body/result identity, detached/stale labeling) + dialogs: register case
  from spec file, assess selected result (case artifact + explicit result
  choice + optional exclusive snapshot target), coarse/fine pair dialog
  (distinct role fields, explicit result ids, exclusive output target),
  reopen single/paired snapshot. `mainwindow.py`: Assessment menu + dock,
  signal wiring; snapshot replay loads the snapshot's OWN project with
  reproduced/detached labeling; pair member selection replays that member's
  own project/result only (no channel/body mixing).
- NEW tests `tests/test_assessment_jobs.py` (headless child/service: single
  pass + NoGo, pair pass, original-disappearance, current-model mutation,
  snapshot-only reopen, tampered/missing outputs, cancellation, crash) and
  `tests/test_qt_assessment.py` (offscreen MainWindow actions through real
  QProcess, model/view values, stale/detached labels, member/body-specific
  replay, closure). NEW `examples/qt_assessment_workflow.py`, this checkpoint
  and an assessment user guide.

## Implementation progression

- [x] Inspection; assessment_jobs.py (builders + child executor + output
      validator); worker.py dispatch; jobs.py JobManager extension
      (nogo state, assessment_ready); qtapp/assessment.py panel +
      dialogs; mainwindow.py wiring; headless + offscreen Qt tests;
      example + docs.
- [x] Focused repair (this continuation), adopted parent corrections:
      - Transport seal: `output_seal()` canonical SHA256 over the whole
        child output (excluding the seal field); `execute` embeds it and
        emits `OUTPUT_SHA256 <digest>` on stdout; `JobRecord.output_seal`
        captures the trusted producer digest; JobManager admission
        requires it (`expected_output_seal`), so an altered file with a
        re-hashed envelope still cannot replace producer evidence.
        Reader refuses unsealed/partial output for all five kinds.
      - Admission hardening: single-output validation now also checks
        the frozen result's `body_ids` and the frozen case's actual
        `case_hash` (via the public light `load_case`), on top of
        kind/verdict/result/case/input-hash identity checks.
      - `_assessment_detached` now uses the light public
        `result_stale`/linear binding identity checks (never hydro
        source proof) plus role-specific contexts: a physical body
        mass/reference-position/equilibrium edit detaches displayed
        evidence even though the stored linear-inputs hash is
        unchanged, before completion and after display.
      - Adversarial regressions copied/adapted into
        tests/test_assessment_jobs.py (unsealed partial output for all
        kinds, unknown verdicts, naive and re-sealed tamper, forged
        producer digest) and tests/test_qt_assessment.py (physical-edit
        detachment before completion and after display, restore, later
        edits).
- [x] examples/qt_assessment_workflow.py (registered case before the
      solve, real stored linear project for GUI assessment; reuses the
      sibling analytic rehearsal fixtures, no new physics).
- [x] docs/QT_ASSESSMENT.md user guide.
- [x] Focused checks through exact harness (qt-assessment2-worker-19..27).
- [x] Final handoff qt-assessment2-final-handoff.md (evidence root).
      Repair note (2026-10-08 continuation): the prior session marked this
      item `[x]` but the file was never written (session 10543 hit the
      normal 60-turn terminal limit first). That was a false completion
      mark. The continuation session wrote the actual file at
      C:/ANYworkspaceWork/anymarine-evidence/qt-assessment2-final-handoff.md
      with no code inspection, edits, or execution; the `[x]` above is now
      true only as of that write. Pending, not claimed by the worker:
      the parent's trusted-producer-rehash adversarial case (added after
      worker-23; never executed) and the parent's actual desktop/installed
      verification.

## Concrete blockers / reserved choices

- None blocking. Reserved: GUI case registration reads the spec JSON at
  submission (closed-world spec artifact route, matching the CLI); no
  in-app spec editor. Adjudication freezes complete snapshot bytes at
  submission; snapshot roots with very large hydro sidecars make large
  job inputs — accepted, bounded by actual snapshot size.
- No changes to protected files; only the permitted minimal connected
  edits (worker.py dispatch, jobs.py extension, mainwindow.py wiring)
  were made.
