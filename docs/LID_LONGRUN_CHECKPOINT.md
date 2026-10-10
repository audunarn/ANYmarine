# Lid geometry and longer development jobs — worker checkpoint (final)

Worker: Mistral (Edit), repository C:/Github/ANYmarine, 2026-10-08.
Continuation session (workers 05–12). Prior sessions spent 17.631172300 s of
the original 90 s metered budget (runs 01–04 preserved); this session spent
48.0916877000127 s; grand total 65.7228600000127 s, remaining
24.2771399999873 s of 90 (not renewed). One sequential process/thread,
timeout <= 20 s per check and bounded by remaining budget, 15 s reserve held
for final verification (worker-12 used 2.64 s of it). All runtime checks
through the exact harness (C:/ANYworkspaceWork/anymarine-evidence/runtime/
Scripts/python.exe + run_check.py, unique increasing --out dirs
lid-longrun-worker-05..12, all preserved). No BEM/Capytaine run, no
qualification, no Git/network/install/build, no sibling edits, no nested
workers, no commit.

## Implemented state (source complete; no product change this session)

- domain.py: `Body.hydro_lid` (optional `BodyMesh`, omitted when None in
  to_dict/from_dict/with_edits), `LinearBodyBinding.lid_mesh_hash`
  (application-local, omitted when None), frozen public
  `LinearExecutionLimits(max_frames=1001)` with hard ceiling 20001 rejecting
  bool/fractional/nonfinite/unknown/<=1, and
  `LinearDevelopmentInputs.execution_limits` (None preserves legacy 1001
  default, serialization and hash; explicit limits enter the input hash and
  are enforced before allocation).
- hashing.py: `hydro_lid` enters the project input hash only when present.
- linear.py: binding verification refuses lid add/remove/replace; demo
  bindings carry the local lid hash.
- hydro.py: public `expected_world_lid` (exact vertex translation, DOWN
  normals, nondegenerate, waterplane z=0 only, typed refusals, no
  flip/round/adopt), exact `lid_mesh_hash` comparison against the dataset,
  both-absent supported, optional `execution_limits` import option enforced
  before inputs are built; provenance (structured record + input-owned
  details) carries world lid hashes additively, omitted when no lid.
- services.py: input-owned evidence check requires `lid_mesh_hashes` in the
  details when the provenance carries them, so a snapshot/save that strips
  the lid identity is refused.
- README.md: lid contract, longer-jobs reproduction example labelled
  development/unqualified, and an explicit known-supply-gap statement.

## Repairs this session (test expectations only; no product change)

1. worker-04 failure `test_import_admits_proven_lid`:
   `LinearProvenance.__post_init__` freezes details deep-immutable (lists
   become tuples), so `details["lid_mesh_hashes"]` is a tuple. The test
   expected `list(...)`; fixed to expect the frozen tuple
   (`== provenance.lid_mesh_hashes`). The product was correct (the service
   compares through `_jsonable_value`, which normalizes tuples to lists).
2. worker-06 failures (2): the longer child/Qt tests attempted a 1002-frame
   hydro import, which the public 1001-sample kernel cannot supply
   (correctly refused on kernel duration). Rewritten to the genuine GUI
   production path: `analytic_demo_inputs(n_steps=1002,
   execution_limits=LinearExecutionLimits(1201))` ->
   `service.set_linear_inputs` -> save -> snapshot/submit. This proves the
   application limit, snapshot and worker behaviour above the default bound
   only, NOT an actual long hydro import (stated in the test docstrings and
   README).
3. worker-07 failure: `linear_result_stale` correctly reports a result as
   stale when the project's stored inputs differ from the inputs that
   produced it; the adoption via `set_linear_inputs` (repair 2) makes the
   demo inputs the project's stored inputs, so the staleness assertions now
   hold on the shared production path.

## Metered evidence (exact harness, all dirs preserved)

| run | command (pytest) | wall s | result |
|-----|------------------|--------|--------|
| 05 | tests/test_lid_longrun.py -k "import or lid_edit or save_reopen or snapshot" | 4.713467000008677 | 6 passed, 7 deselected |
| 06 | tests/test_lid_longrun.py -q | 4.700783600012073 | 2 failed, 11 passed (preserved; kernel supply gap) |
| 07 | tests/test_lid_longrun.py -q | 7.462991299995338 | 1 failed, 12 passed (preserved; staleness semantics) |
| 08 | tests/test_lid_longrun.py -q | 7.312432999999146 | 13 passed |
| 09 | test_hydro_bridge.py (minus Qt menu test) + test_hash_stale.py + test_linear_inputs.py + test_linear_persistence.py | 8.934477499991772 | 66 passed, 1 deselected |
| 10 | test_linear_service.py + test_worker_linear.py + test_linear_resume_contracts.py + test_linear_review.py | 7.32255620000069 | 30 passed |
| 11 | test_hydro_bridge.py::test_qt_menu_import_and_replay_label | 5.0072899000078905 | 1 passed |
| 12 | test_domain.py + test_persistence.py (final verification) | 2.637689199997112 | 31 passed, 1 skipped |

Session totals: 170 passed, 3 failed (all in preserved runs 06/07, all
corrected test expectations, no product change), 1 environmental skip
(pre-existing Windows symlink skip in test_persistence.py:164, unrelated to
this work). Environment for every run: PYTHONPATH=C:/Github/ANYmarine/src,
ANYMARINE_SOLVER_PATH= (empty), OMP/OPENBLAS/MKL_NUM_THREADS=1,
TEMP/TMP=C:/ANYworkspaceWork/anymarine-evidence/tmp, QT_QPA_PLATFORM=offscreen.

## Coverage of the required outcome

- Lid-equipped equilibrium geometry across import: worker-05/08
  (test_import_admits_proven_lid, typed refusals for missing/mismatch lids).
- Immutable snapshots, save/reopen/save-as, lid edit/removal/replacement
  staleness, snapshot cannot strip lid or limits: worker-05/08.
- Longer development jobs above the 1001 default through the shared
  CLI/Qt/services/child path with explicit execution limits: worker-08
  (1002-frame child job via `anymarine.worker` subprocess; Qt JobManager
  job; snapshot carrying execution_limits and lid_mesh_hash).
- Existing regression: hydro bridge (66 + the Qt menu import/replay test),
  linear suites, hash staleness, domain/persistence: workers 09–12.

## Known gaps (stated, not worked around)

- The installed public ANYhydro radiation builder caps kernels at 1001 time
  samples, so hydro-imported jobs cannot yet be supplied longer than 1001
  frames; a 1002-frame hydro import is refused on kernel duration even with
  explicit limits (fail-closed, no truncation, no forged longer kernel).
  The explicit-input child/Qt runs above 1001 prove application limit/
  snapshot/worker behaviour only, NOT an actual long hydro import. A
  separately scoped ANYhydro long-kernel control is required before
  acceptance of an actual long hydro workflow.
- Not run this session (outside the brief's regression subset, unverified
  here): tests/test_gui_integration.py, test_gui_linear.py,
  test_qt_shell.py, test_headless.py, test_synthetic.py,
  test_jobs_qprocess.py, test_review_contracts.py.
- Synthetic fixtures in the focused tests are analytic development
  fixtures, not qualified marine results; no BEM/Capytaine import or
  qualification was performed.
- Working tree: LICENSE has a pre-existing unrelated modification and most
  files are untracked (single "Initial commit" repository); preserved
  untouched, no commit made (integration-owner authority required).

## Blockers

None. Author is not final acceptance.

## Parent verification closes the dependency gap — 2026-10-08

Historical author gaps above are preserved, but the1001 kernel-supply gap is now
closed by separately scoped ANYhydro explicit RadiationExecutionLimits. Installed
public1003-sample kernel feeds an actual nonzero1002-frame application hydro
import/save/reopen/child/replay/save-as workflow.87 unique installed cases passed
across two attempts; five disposable-harness errors/failure preserved and fixed.
Final source-matched wheels are evidence/wheels/lid-longrun-v2; reader repair and
README correction included. Full identity/commands/failures/process-wall ledger:
C:/ANYworkspaceWork/anymarine-evidence/lid-longrun-final-manifest.json.
Worker65.722860000/90s; shared parent29.503520500/30s. Development slice verified,
not scientific qualification or full GUI/roadmap acceptance. Actual coarse BEM
pilot still refuses after lid proof. No numerical authority renewed.
