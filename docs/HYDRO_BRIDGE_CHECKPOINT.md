# Hydrodynamic application bridge — implementation checkpoint (2026-10-08)

Development checkpoint per brief; not a permission gate. Development only,
unqualified. No BEM solve, no qualification claim, no sibling edits.

## Status: parent verified against development criteria; scientific qualification pending

The owner checkpoints below preserve implementation history and failures. The
final parent acceptance is recorded at the end and in TASK.md.

All planned source work (original items 1-8 plus the finish-round provenance
hardening) is implemented in the owned ANYmarine boundary. The focused source
suite plus all 8 independent parent cases pass in the final bounded run
(worker-03-a, exit 0). The parent installed-wheel run then proved the
QProcess success/cancel/crash and Qt menu/replay paths but exposed one real
CLI production wiring defect (`_cmd_run_linear` re-installed stored hydro
inputs through `set_linear_inputs`, which the anti-stripping guard correctly
refuses). That CLI repair now passes the parent installed rerun
(`hydro-bridge-parent-installed-02`, 1 pass). The parent real-desktop run
then succeeded through Qt/OpenGL import, QProcess job, save and reopen but
failed after seek at desktop script line 61
(`assert 'ANYhydro' in window._frame_label.text()`): `_apply_frame`
overwrote the truthful source/unqualified/stale label with frame/time only.
The label repair is now implemented in the owned boundary but is NOT
verified: no worker check has run against it. The parent must rebuild the
changed candidate and rerun the real desktop check. Scientific qualification
remains out of scope here.

## Implemented (changed files, all inside C:/Github/ANYmarine)

- `src/anymarine/hydro.py` (new, Qt-free, lazy `import anyhydro`):
  geometry proof via public `HydroPanelMesh` (world panels from
  `BodyMesh.positions + reference_position`, identical connectivity,
  per-face sizes, body_id projection; mesh/hash/body/rotation-center/
  lid/waterline/transform mismatches refused), 12->4 work-conjugate
  selector with exact ordered double-colon DOF keys, projected
  P.T O P operators, dry diagonal masses, dataset buoyancy-only stiffness
  plus optional explicit surge link stiffness, kernel/dataset hash, Ainf
  and DOF identity checks, `compare_reconstruction` recompute with caller
  criteria (A and B pass, zero symmetry/passivity violations, compatible
  grid, sufficient duration), exact-frequency/heading regular-wave phasor
  forcing or explicit q0/v0 free decay, immutable provenance data,
  `HydroImportError`.
- `src/anymarine/domain.py`: `LINEAR_SOURCE_HYDRO` in `LINEAR_SOURCES`;
  frozen `HydroBridgeProvenance` record; `ProjectModel.hydro_provenance`.
- `src/anymarine/persistence.py`: manifest schema 4 for hydro intent; raw
  dataset/kernel `.nc` preserved as content-addressed project sidecars
  (raw byte hash verified on load); provenance sidecar; loader refuses
  schema-4 manifests and any manifest whose stored linear intent declares
  the hydro source without the structured hydro provenance (hydro intent
  cannot be stripped to a legacy schema); v2/v3 keep loading normally.
- `src/anymarine/services.py`:
  - `import_hydro_inputs(project, dataset, kernel, options)`: shared
    CLI/Qt admission; validates everything before mutating, copies the
    authoritative raw artifacts into the project as content-addressed
    sidecars (refuses source files that change between decode and raw
    hash), then stores inputs + provenance and re-verifies.
  - `verify_hydro_provenance`: fails closed unless the structured hydro
    provenance matches the stored inputs — refuses hydro-sourced stored
    linear inputs with no hydro provenance record (no silent return for
    None anymore), source mismatch, input-hash mismatch, DOF/body order
    mismatch, and missing/inconsistent input-owned import evidence in
    the provenance details.
  - `set_linear_inputs`: refuses `LINEAR_SOURCE_HYDRO` inputs (hydro
    admission only via `import_hydro_inputs`, so the hydro intent cannot
    be stripped); nonhydro demo/explicit paths keep clearing old hydro
    metadata.
  - `load_explicit_linear_inputs`: refuses `LINEAR_SOURCE_HYDRO` data.
  - `run_linear_local` / `linear_job_snapshot`: refuse hydro-sourced
    effective-input overrides unless they reproduce the verified project
    hydro record's full `linear_input_hash`
    (`_verify_effective_hydro_inputs`); nonhydro explicit/demo override
    API preserved.
  - Save-as resolution and `persistence.load` source==hydro/schema4
    refusal preserved as written by the previous round.
- `src/anymarine/cli.py`: `import-hydro` command (dataset + kernel +
  explicit JSON options, no hidden defaults); `run-linear` reuse-path repair
  (see the installed-evidence section below; implemented, unverified).
- `src/anymarine/linear.py`: source-aware result note (hydro dataset
  source no longer labelled "not boundary-element-method output"; still
  development/unqualified).
- `src/anymarine/qtapp/mainwindow.py`: Jobs-menu hydrodynamic import
  through the same service; truthful source/replay labels preserved through
  seek/replay via the `_result_status` cache (see the desktop repair section
  below).
- `tests/test_hydro_bridge.py`: 36 tests (analytic full-12x12 PSD coupled
  fixture with proven geometry and caller criteria, force phase,
  independent projection oracle, refusal/immutability/persistence/missing
  sidecar/staleness, CLI child and Qt service production paths; the Qt
  replay test now also regression-covers source/unqualified/stale label
  persistence across seek and body-mass edit).
- `README.md`: hydro import section with the runnable `import-hydro`
  options example (verified against the actual CLI).

## Measured check evidence (bounded, one-thread, offscreen)

Runtime: `C:/ANYworkspaceWork/anymarine-evidence/runtime/Scripts/python.exe`,
cwd `C:/Github/ANYmarine`, env `QT_QPA_PLATFORM=offscreen`,
`OMP/OPENBLAS/MKL_NUM_THREADS=1`. Evidence dirs under
`C:/ANYworkspaceWork/anymarine-evidence/` (result.json + stdout/stderr).

| Run | Scope | Wall (s) | Result |
|---|---|---|---|
| worker-02-a | tests/test_hydro_bridge.py (2 child/Qt-replay tests deselected) | 6.476 | exit 1: 3 failed, 40 passed (incl. `test_hydro_provenance_immutability` tuple/append) |
| worker-02-b | hydro bridge + parent 8 + persistence/linear service/persistence/inputs legacy | 7.787 | exit 1: 2 failed (`test_hydro_intent_cannot_be_stripped_to_legacy_schema` did-not-raise; `test_save_as_refuses_source_sidecar_path_escape` outside read), 96 passed |
| worker-03-a (final) | 17 source test modules + parent 8 (all tests/ except `test_jobs_qprocess`; same 2 deselects) | 17.671 | exit 0: **169 passed, 1 skipped, 2 deselected** in 15.00s |

All previously measured failures pass in worker-03-a after the finish-round
edits; no new failures. worker-03-a covers `test_hydro_bridge.py` (34 run),
the 8 independent parent cases including the explicit-path hydro-stripping
refusal, and the affected legacy modules (linear service/persistence/inputs,
persistence, domain, hash/stale, headless, synthetic, review contracts,
linear review/resume, worker linear, gui integration/linear, qt shell).

## Budget accounting

Worker measured-check allowance 180 s: 156.355007100 s used before this
round; worker-03-a used 17.67068009999639 s; total 174.02568719999639 s.
A conservative 4 s charge for the previous unmetered py_compile (tool event
interval 3.688 s) brings the total to 178.0256872 s, remaining 1.9743128 s
(no renewal). The remaining allowance cannot safely fit any suite or even an
extra compile command, so no further worker checks or compiles run. The
parent installed-wheel and real-desktop runs (including
hydro-bridge-parent-installed-01/02) are parent budget, not worker budget.

## Installed-wheel parent evidence and CLI reuse-path repair (2026-10-08)

Parent installed-wheel run `hydro-bridge-parent-installed-01` (installed
`test_jobs_qprocess.py` + installed `test_hydro_bridge.py::test_cli_import_and_child_job`
+ `test_qt_menu_import_and_replay_label` + the 8 independent parent cases,
one-thread/offscreen, wall 8.221168599993689 s, exit 1):

**14 passed, 1 failed in 5.57s** (pytest summary). The QProcess
success/cancel/crash coverage and the Qt menu import/replay label all
passed. The single failure is real CLI production wiring, not environment:

- `test_cli_import_and_child_job` — after `import-hydro`, a bare
  `run-linear --project <dir>` exited 1 with
  `error: hydrodynamic linear inputs may only be admitted through the
  verified hydro import (import_hydro_inputs); set_linear_inputs refuses
  them so the hydro intent cannot be stripped`.
  Cause: `_cmd_run_linear` unconditionally called
  `service.set_linear_inputs(project, inputs)` even when reusing the
  project's existing stored hydro inputs; the correct new anti-stripping
  guard refused them.

Repair (implemented in `src/anymarine/cli.py` `_cmd_run_linear`, owned
boundary; NOT yet verified by any check):

- `--demo` and `--inputs` paths still install their new inputs via
  `set_linear_inputs` (which clears stale hydro provenance by design).
- The stored-inputs reuse path no longer calls `set_linear_inputs`; it
  keeps the loaded project and its `hydro_provenance` unchanged and runs
  the existing stored inputs through the usual verified service route
  (`run_linear_local` / `linear_job_snapshot`), which re-verifies the
  body binding, the effective hydro inputs and the hydro provenance.
- The `set_linear_inputs` / `load_explicit_linear_inputs` /
  `verify_hydro_provenance` guards are untouched; demo/explicit paths are
  unchanged in behavior.

No worker test was launched against this delta (per instruction; prior
child-test stall claim and remaining worker allowance). The parent then
rebuilt and reran it: `hydro-bridge-parent-installed-02` passes the
previously failed installed CLI test (1 pass). All prior failures and
evidence above remain preserved.

## Real-desktop parent evidence and replay-label repair (2026-10-08)

The parent real-Windows desktop run (real Qt/OpenGL import, QProcess job,
save, reopen) succeeded through import/job/save/reopen but failed after
seek at desktop script line 61: `assert 'ANYhydro' in
window._frame_label.text()`. Concrete source defect: `_apply_frame`
overwrote the truthful `_result_status_text`-derived label with only
`frame i/n t=…s`, erasing the source/unqualified provenance and any stale
warning on every seek/play frame. The pre-existing offscreen Qt test
checked only the initial label and missed this path.

Repair (implemented in `src/anymarine/qtapp/mainwindow.py`, owned boundary;
NOT yet verified by any check):

- `_set_result` and `_mark_edited` (the existing result-selection and edit
  hooks) now maintain a small `_result_status` cache holding the truthful
  `_result_status_text` label, so the cache is invalidated by every result
  change and every project edit; no per-frame input hashing is done.
- `_apply_frame` composes the cached status with the frame/time text
  instead of replacing it, so source/unqualified/stale status persists
  through seek and play; a result without time samples shows the status
  alone.
- Stored transforms, result kinds and all physics/algorithm behavior are
  untouched; `handle.set_transform` replay is unchanged.

Regression added to `test_qt_menu_import_and_replay_label`
(`tests/test_hydro_bridge.py`): after the job, a seek must keep the
ANYhydro source/unqualified label; a body-mass edit through the existing
`_apply_body_edit` hook must make the stored result stale and a further
seek must keep the stale label; collected GUI notifications must stay
empty. Not run by the worker (parent owns the installed focused Qt run;
remaining worker allowance 1.9743128 s, no extra compile commands). Parent
next step: rebuild the changed candidate and rerun the real desktop check.
The failed desktop project/evidence is preserved.

## Pending gates (parent)

- Real-desktop check rerun against the replay-label repair, plus the
  installed focused Qt test with its new seek/stale regression (parent
  responsibility; the installed CLI reuse path already passed in
  hydro-bridge-parent-installed-02).
- `tests/test_jobs_qprocess.py` and the two deselected child-job/Qt-replay
  tests (`test_cli_import_and_child_job`,
  `test_qt_menu_import_and_replay_label`): not run by the worker (child
  processes stall in the harness sandbox); parent to run in the installed
  environment.
- Scientific qualification: all hydro-imported results remain
  development/unqualified; no BEM solve or marine-analysis claim.

## Reserved decisions (as implemented)

- Run grid `dt` comes from the kernel artifact's fixed grid; options give
  `n_steps` (<= kernel samples, <= 1001).
- Re-import replaces stored linear inputs + provenance atomically at the
  service level; nonhydro input paths clear the hydro provenance; hydro
  inputs are only admitted through the verified import.


## Parent acceptance (development criteria only)

Final CLI/seek repairs passed installed regressions;15 unique installed cases
now pass. Real Windows OpenGL3.3 saved run and actual control/plot/retained
transforms agree with authoritative arrays. The final wheel/code identities
and all measured failures/accounting are in hydro-bridge-canonical-manifest.json.
Parent fixed only test close cleanup, neutral startup footer and stale README
claims after the owner finished. TASK.md supersedes earlier pending status
paragraphs above. No scientific qualification, BEM campaign or publication.
