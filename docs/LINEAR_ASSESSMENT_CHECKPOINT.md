# Linear assessment slice — early worker checkpoint (2026-10-08)

Owner: Mistral (implementation). Parent owns contract adoption/acceptance.

## Final continuation status — unblock-and-deliver repair (verified against assigned criteria)

Status label: **implemented and verified against the assigned source-check
criteria** (worker verification only; the parent independently
builds/installs/adjudicates the final candidate — this is not installed
acceptance and not qualification).

Mandatory repair findings from the independent installed parent04 review,
all implemented in source:

1. Reproduction compares the FULL semantic payload. `adjudicate_report`
   re-assesses and compares verdict, every check with its actual metrics and
   every recorded array; only the fresh report identity
   (`report_id`/`created_utc`; the hash is derived) is excluded. Numeric
   scalars use one tight documented roundoff tolerance (relative 1e-12 +
   absolute 1e-12 per scalar; a persistence comparison, never a physics
   tolerance). A re-hashed report with a forged FD `qhat` metric and
   unchanged pass flags is refused. `save_assessment_snapshot` reproduces
   the report (re-assessment + semantic comparison) BEFORE creating/writing
   anything, after the existing-target refusal; NoGo artifacts are
   preserved.
2. Actual project context for the hydro core. `assess_result` /
   `adjudicate_report` gained an optional `project=` keyword; a
   `hydro_spectrum` reference REQUIRES the actual `ProjectModel` + root. The
   core runs the public `verify_linear_binding(project, inputs)`, requires
   the assessed inputs to BE the project's stored inputs (input hash), any
   supplied provenance to be the project's own record, and the public
   `ApplicationService(root).verify_hydro_provenance(project)` (metadata /
   input-owned evidence consistency plus the authoritative derived-route
   recompilation matching the actual input hash). A generic hydro case
   without the actual project refuses; re-hashed bookkeeping over
   substituted effective matrices (e.g. mechanical C = 10000 I) refuses.
   `load_hydro_spectrum` remains a spectral loading helper, never a
   standalone eligibility certificate. The service delegates pass the
   actual current/reloaded project; the core never loads a stale disk
   project. `prepare_linear_assessment` additionally re-verifies any
   existing hydro provenance before admitting a source-bound registration.
   The analytic core remains Qt-free with no project requirement.
3. `examples/linear_assessment.py` imports `mesh_hash` from
   `anymarine.hashing` (the actual public symbol; the old
   `anymarine.domain` import was the worker09 ImportError). The example
   demonstrates its intended outcome with the real public services and
   snapshot/adjudication: regular wave coarse/fine PASS, refinement PASS,
   free decay PASS, snapshot adjudication agrees. The first run exposed a
   genuine fixture defect (the startup memory transient had not decayed
   below the UNCHANGED steadiness/fit criteria by the first window); the
   development fixture's mechanical damping was raised (a fixture choice,
   not a criteria change or threshold retuning).
4. Documentation completed: `docs/LINEAR_ASSESSMENT.md` (new; scope, service
   and CLI usage, actual source-reference proof, units/phase conventions,
   NoGo/refusal/artifact handling, semantic reproduction, pending gaps),
   this checkpoint, and a README section that distinguishes source checks
   (33 focused tests), installed acceptance (parent-owned) and
   qualification (out of scope). Hydro-source free-decay assessment is
   documented as PENDING (the free-decay reference schema supports
   `analytic_exponential` only); no arbitrary hydro kernel is labelled
   analytic and no W5 completion is claimed.

Changed files (this continuation): `src/anymarine/linear_assessment.py`,
`src/anymarine/services.py`, `tests/test_linear_assessment.py`,
`examples/linear_assessment.py`, `docs/LINEAR_ASSESSMENT.md` (new),
`docs/LINEAR_ASSESSMENT_CHECKPOINT.md`, `README.md`, and
`C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-final-handoff.md`.
Protected original linear/domain/hydro/persistence/worker/qtapp/run_gui
sources, licenses and sibling files are untouched.

Checks actually run (exact harness `C:/ANYworkspaceWork/anymarine-evidence/run_check.py`,
runtime `C:/ANYworkspaceWork/anymarine-evidence/runtime/Scripts/python.exe`,
`PYTHONPATH=C:/Github/ANYmarine/src` only, `ANYMARINE_SOLVER_PATH` empty,
OMP/OPENBLAS/MKL=1, TEMP/TMP=evidence tmp, one process, per-process <= 15 s):

- worker-10: `pytest tests/test_linear_assessment.py -q` (basetemp
  linear-assessment-temp-10) — **33 passed, 0 failed**, wall 8.132276900010766 s.
- worker-11: example run — exit 0 but regular-wave verdicts nogo (fixture
  transient; preserved as failed evidence), wall 1.490632300003199 s.
- worker-12: example run after the fixture repair — all verdicts pass,
  snapshot adjudication agrees=True, wall 1.4255593000125373 s.
- worker-13: `python -m anymarine new` (headless CLI subprocess) — exit 0,
  wall 0.268476400000508 s.
- worker-14: `python -m anymarine validate` on the worker-13 project —
  exit 0, wall 0.20189100000425242 s.
- worker-15: the parent's read-only independent tests, UNMODIFIED, run
  from source (`pytest .../test_linear_assessment_parent.py -q`, basetemp
  linear-assessment-temp-15) — **15 passed, 0 failed** (both mandatory
  findings included), wall 4.706377900001826 s.

Budget: worker allowance 180 s cumulative process wall. Consumed
01-09: 49.14641439997649 s; this continuation 10-15:
16.225213800033088 s; total consumed 65.37162820000958 s;
remaining 114.62837179999042 s (allowance NOT renewed). Parent 60 s
reserve: 13.261314999967 s consumed, 46.738685000033 s remaining
(parent checks paused during this work).

Remaining gaps: hydro-source free-decay assessment pending (schema refuses
`hydro_spectrum` for free decay); GUI integration of the assessment
workflow deferred; installed acceptance and qualification are parent-owned
and not claimed here. The parent's read-only independent tests were
executed unmodified from source in worker-15 (15 passed); the parent's
own installed adjudication remains pending. Mirrored worker regressions:
`test_rehashed_metric_forgery_refused_by_adjudication_and_snapshot`,
`test_hydro_core_refuses_rehashed_substituted_matrices`.

Authoritative evidence paths:
`C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-worker-10..15/`
(result.json/stdout.log/stderr.log each),
`C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-worker-11-out/`
(preserved nogo example output),
`C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-worker-12-out/`
(passing example output incl. snapshot),
`C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-final-handoff.md`.

## Corrected checkpoint — review-delta repair (worker-04, before checks)

Parent review consumed the worker-03 run (18 failed / 5 errors, all rooted in
the missing `schema_version` default plus the review findings below) and
adopted a repair delta. All seven findings are implemented in source before
any new check runs; existing files/logs/failures are preserved.

1. `prepare_case` now defaults an omitted `schema_version` to the current
   schema (a declared value must match exactly). This was the root cause of
   the 18 failures / 5 errors: the test specs never declared it.
2. Free-decay work accounting: the trapezoid work is the endpoint-averaged
   force DOT the per-step displacement increment (one scalar per step; the
   old `delta_q[:, :, None]` broadcast was wrong). `force_memory` is the
   POSITIVE resisting force, so zero-history passivity is
   `min cumulative W_memory >= -tolerance` (positive net work and negative
   increments are both admitted; an active kernel is a NoGo). Closure
   `E - E0 - W_ext + W_damp + W_mem` is checked over the WHOLE trajectory,
   not only at the final time.
3. `_memory_oracle` endpoint contraction fixed to `velocities @ K[0]^T`
   (the old einsum had an output subscript absent from the inputs).
4. `_fit_window` fits ONE stacked 4-DOF least-squares problem: independent
   cos/sin/intercept columns per DOF plus exactly ONE shared drift
   coefficient multiplying `u = (1,0,1,0)/sqrt2`; no per-DOF or heave
   drift. `K u = 0` is verified at case preparation; the shared drift
   coefficient is reported per window.
5. `ApplicationService.prepare_linear_assessment` and
   `assess_linear_result` now call the existing public
   `_verify_linear_binding` (body mass/mesh/equilibrium) before preparing
   or assessing; the `run_linear_local` gate alone no longer establishes
   later assessment eligibility.
6. The generic hydro core (`load_hydro_spectrum`) no longer relies on the
   caller having run `verify_hydro_provenance`: the actual
   dataset/operators/KERNEL sidecar bytes are re-read from the project
   root, sha256-checked against the public provenance, re-loaded through
   the public ANYhydro loaders, content-hash re-checked, and the re-proved
   kernel must reproduce the stored effective input matrices exactly.
7. `hydro_reciprocal_sensitivity` response change is the DIMENSIONLESS
   `S^-1 qhat` (declared dimensionless floor/abs 1e-8); the retained SI
   solutions are recorded separately; the work-conjugate `S^T Z S`
   impedance stays in J. No processing or floor values changed.
8. `save_assessment_snapshot` refuses an existing target BEFORE any write
   (old NoGo/failed outputs preserved) and validates that the
   case/result/report correspond before creating anything.

Budget: worker allowance 180 s cumulative process wall; consumed by
workers 01-03: 13.115490699987276 s (collection repair 4.50291819999984,
collection 2.5184107999957632, run 6.094161699991673). Remaining before
worker-04 checks: 166.88450930001272 s; parent 60 s reserve untouched.
Planned checks: focused pytest suite via the exact harness (worker-04
evidence path), repair reruns (worker-05+), example run. Final 35 s /
30 turns reserved for checks/repair/handoff.

## DELTA adoption update (same-day continuation, supersedes reserved items 4/5)

The parent interrupted the early checkpoint before implementation and adopted a
DELTA. The adopted design changes are recorded here and govern the
implementation below; the original proposal is retained underneath for history.

1. HYDRO RAO references never Fourier-transform the stored TD kernel. The FD
   reference for hydro-sourced inputs uses the ACTUAL stored neutral dataset
   `A(omega)`, `B(omega)`, `F(omega, heading)` (raw route, work-conjugate
   selector projection, exact stored sample) or the public derived artifact's
   `derived_added_mass` / `derived_damping` / `excitation_force` (derived
   route), loaded through the PUBLIC `anyhydro.load_hydro_dataset` /
   `anyhydro.load_derived_operators(source_dataset=...)` after the shared
   `ApplicationService.verify_hydro_provenance` gate has run. Sidecar bytes
   are re-read from the project root and re-checked against the provenance
   raw sha256 values; dataset/operator content hashes must equal the public
   `project.hydro_provenance` identities. No private source helpers, no
   caller boolean/hash substitutes for the actual arrays. A hydro_spectrum
   case without real project context is a typed refusal.
2. New reference kind `hydro_spectrum` with explicit wave amplitude/phase/
   heading/omega selection and immutable source identity (dataset content
   hash; derived content hash + operators raw sha256 for the derived route).
   The case force phasors must match `amplitude * exp(+i phase) * F_source`
   at the exact selected sample (identity tolerance, not a physics gate),
   and the case wave selection must equal the provenance's recorded wave
   selection. Raw route uses the exact selected raw A/B (the raw route's own
   strict admission already admitted them); derived route MUST use the
   artifact's processed arrays plus the raw-source proof — raw arrays are
   never silently substituted when the TD consumed processed arrays.
3. `kernel_quadrature` is REMOVED as an admissible RAO reference kind (it
   was never published). It survives only as an explicit self-consistency
   diagnostic: the stored TD kernel's cosine/sine quadrature is TESTED
   against the independent spectrum A/B and recorded informationally in
   hydro reports; it can never yield admission or a PASS.
   `analytic_exponential` remains the independent closed-form rehearsal
   reference (stored kernel validated against `R exp(-alpha t)` first).
4. Reciprocal-average sensitivity is a WORKING check, not recorded fields:
   from the loaded public artifact's `raw_added_mass`/`raw_damping` vs
   `derived_added_mass`/`derived_damping` at the case omega and the SAME
   physical M/K/C/F, compute `Z_raw` vs `Z_processed` (compared as
   `S^T Z S` max-entry with the declared J floor/abs) and the full coupled
   complex `q_raw` vs `q_processed` (explicit qhat units), enforcing the
   prospective 0.01 / 0.02 limits with floors/abs 1e-9 J and 1e-8. A
   singular/meaningless raw FD is a typed non-success with diagnostics,
   never skip=>pass. Exercised with analytic/mock neutral datasets and
   derived operators (no BEM) including a failed-sensitivity counterexample.
   The report records the TD/FD reference SOURCE identity (route, hashes,
   omega/direction indices) so an actual gate cannot masquerade as kernel
   self-reference.
5. Snapshots are SELF-CONTAINED: the selected project/result is saved through
   the existing PUBLIC `ApplicationService.save` save-as route (which copies
   and checks the real hydro dataset/operators/kernel sidecars) under the
   assessment output directory, next to the case/report artifacts.
   Reproduction loads that saved project (provenance verification and
   source proof re-run), re-assesses from the captured actual arrays and
   compares with the stored report; report booleans/hashes alone are
   necessary, not sufficient. Only the selected result is copied.

## Actual findings from source inspection

- `anysolver.marine_linear` (installed 0.4.7) confirms the exact conventions the
  assessment must verify: equation `(M_dry+A_inf)a + C v + K q + memory = F`,
  Newmark beta=1/4 gamma=1/2 (`q[n]=q[n-1]+dt v[n-1]+dt^2/4(a[n-1]+a[n])`,
  `v[n]=v[n-1]+dt/2(a[n-1]+a[n])`), memory
  `dt*(0.5*K[n]v0 + sum_{j=1..n-1}K[n-j]v_j + 0.5*K[0]v_n)`, `memory[0]=0`,
  `a0` from the initial load, channels store each resisting term and
  `residual = F - sum(resisting)`.
- `LinearDevelopmentInputs` (domain.py) is immutable, hash-covered by
  `linear_input_hash`, carries full coupled 4x4 matrices, sampled kernel,
  absolute-time grid `t0 + k*dt`, q0/v0, provenance; `verify_linear_result`
  already enforces kind/body-order/exact-grid/all-channel/replay-pose
  completeness. The assessment reuses these public verifiers; it does not
  re-implement them.
- `ApplicationService` exposes `run_linear_local`, `save`/`load` (ProjectStore,
  content-addressed sidecars, schema v3/v4), `verify_hydro_provenance`,
  `linear_job_snapshot`. Persistence primitives (`atomic_write_bytes`,
  `sha256_bytes`, `check_relative_reference`, `resolve_sidecar`) are public and
  reusable for assessment artifacts without touching ProjectStore itself.
- CLI pattern: thin subcommands over ApplicationService; errors via
  ServiceError -> exit 1.
- No Capytaine/Qt/ANYsolver import at base package import today; the
  assessment module must preserve that (solver only via
  `ApplicationService.run_linear_local`).

## Proposed public API (module `anymarine.linear_assessment`)

Records (immutable, closed-world, hash-bound; unknown fields refuse):

- `AssessmentCase` — registered BEFORE any rehearsal solve. Fields:
  `schema_version=1`, `case_id`, `mode` (`regular_wave`|`free_decay`),
  `input_hash` (must equal `linear_input_hash` of the stored inputs),
  `result_id` (optional selector), `reference` (`analytic_exponential` with
  closed-form R/alpha, or `kernel_quadrature` numerical FD from the stored
  kernel+Ainf), `omega` + per-DOF complex force phasor `F_hat` (SI N,
  includes wave amplitude and exp(+i phase); regular_wave only),
  `windows` (two adjacent equal-duration absolute-time windows),
  `fit_controls` (intercepts allowed, drift `none`|`common_surge_linear`
  along (1,0,1,0)/sqrt2, `max_condition<=1e12`), `criteria` (all tolerances
  below), `refinement` (optional: partner case hash + integer dt ratio >= 2),
  `scope` (`development_only` always). `case_hash` = sha256 over the
  canonical case JSON (created_utc excluded).
- `AssessmentReport` — verdict `pass`|`nogo` (NoGo preserved with failed
  checks), the case hash, and raw authoritative arrays: fitted phasors per
  window per DOF, FD reference phasors, fit residuals, force-balance norms,
  memory-oracle deviation, energy/work arrays (free decay), refinement
  residuals. Always labelled development/unqualified. `report_hash` bound.

Functions:

- `prepare_case(spec: dict) -> AssessmentCase` — validates closed-world,
  window durations (each >= 2 cycles, equal, inside grid), Nyquist
  (omega < pi/dt), criteria never looser than the governing contract minima,
  drift rule shape. Refuses unknown fields.
- `assess_result(case, result, inputs) -> AssessmentReport` — eligibility
  gates (kind, `verify_linear_result`, hash agreement, binding via service),
  then mode dispatch:
  - regular_wave: input-force match `Re(F_hat e^{-i omega t})` at absolute
    times (rel 1e-10 + abs 1e-9 N); FD reference
    `Z=K-omega^2(M+A(omega))-i omega(B(omega)+C)`, full complex 4x4 solve,
    off-diagonals kept; two-window cos/sin least squares, negative phasor
    `a+i b`; condition/rank gates; steadiness < 2% (floor/abs 1e-8 m);
    fit RMS <= 1e-8 m + 0.02*max(FD amp, floor); RAO < 3% / < 3 deg;
    near-zero (<= 1e-8 m) phase explicitly NOT measured, amplitude abs
    < 1e-8 m; optional common-surge drift only with `K u = 0` verified
    against the actual K under the declared scale.
  - both modes: authoritative physics balance recomputed from stored
    q/v/a + effective matrices (dry/added/C/K channels, external vs input
    forces, Newmark q/v updates, declared q0/v0), independent FFT trapezoid
    memory oracle, governing normalized balance 1e-10 floor 1e-9 N.
  - free_decay: zero external forcing, E = .5 v'(M+Ainf)v + .5 q'Kq,
    delta_q = dt(v_n+v_{n-1})/2, trapezoid work accumulation with resisting
    signs, net radiation work >= -tol (non-monotone W allowed — returning
    fluid energy is not failure), mechanical peak-growth bound, discrete
    closure abs 1e-10 J + rel 1e-8*max(E0, declared scale, floor 1e-12 J).
- `assess_refinement(case_coarse, case_fine, result_coarse, result_fine,
  inputs_coarse, inputs_fine) -> AssessmentReport` — same-physical-model
  guard (same M/Ainf/C/K/q0/v0/forcing/omega/duration/windows; integer dt
  ratio >= 2; kernel from the same analytic descriptor or same hydro source
  identity, sampling hash may differ), then regular-wave phasor error < 2%
  of the finest per-DOF amplitude (floor/abs 1e-8 m) or free-decay q/v/a
  history comparison at shared times with per-quantity per-DOF unit scales
  (rel < 0.02, floor/abs 1e-8 in m, m/s, m/s^2; no mixed-unit maximum).
- Persistence: `save_case`/`load_case`, `save_report`/`load_report` —
  content-addressed JSON under `<project>/assessments/`, reusing the public
  persistence primitives; load verifies the self-hash and refuses
  mutation/unknown fields/traversal.
- `adjudicate(case_path, project_root, report_path)` — reloads the actual
  project (ProjectStore + `verify_hydro_provenance`), selects the stored
  result, re-assesses from the actual arrays and public proofs, and compares
  against the stored report; never trusts report booleans alone.

Service wiring (minimal, additive methods on `ApplicationService`):
`prepare_linear_assessment(project, spec)`, `assess_linear_result(project,
result, case)`, `adjudicate_linear_assessment(project, case, report)` — thin
delegates that add the binding/staleness/hydro-provenance gates.

CLI (additive subcommands): `prepare-assessment --project --spec --out`,
`assess-linear --project --case [--result-id] --out`,
`adjudicate-assessment --project --case --report`.

Example `examples/linear_assessment.py` (role `analytic_rehearsal_only`):
stored project -> demo-quality explicit exponential-radiation coupled inputs
-> cases prepared BEFORE the solve -> `run_linear_local` (public real solver
route) -> assess regular wave + free decay -> coarse/fine refinement ->
save/reopen/adjudicate -> concise printed summary. No Capytaine, no W5 claim.

## Reserved choices (concrete, prospective; parent may adopt/adjust)

1. Criteria live entirely in the case record; the constructor admits stricter
   values but refuses looser ones than the governing contract minima
   (balance 1e-10/1e-9 N, steady 2%, RAO 3%/3 deg, refinement 2%, energy
   closure 1e-10 J + 1e-8 rel, near-zero floors as specified).
2. Newmark/initial-state verification uses normalized rel 1e-10 with explicit
   absolute floors 1e-12 m and 1e-12 m/s (declared in the case; the contract
   does not fix these two floors, so they are prospective case controls).
3. Free-decay peak-growth bound reuses the declared energy tolerance
   (closure tolerance) as the prospective growth control.
4. (REVISED to the adopted hydro_spectrum rule) The FD reference for
   hydro-sourced inputs is NEVER a transform of the stored TD kernel. It is
   the ACTUAL stored neutral dataset `A(omega)`/`B(omega)`/`F(omega,heading)`
   sample (raw route, exact work-conjugate selector projection) or the
   public derived artifact's processed arrays plus the full raw-source proof
   (derived route), loaded through the public ANYhydro loaders; the kernel
   sidecar is re-proved and must reproduce the stored effective input
   matrices. `kernel_quadrature` is removed as an admissible reference kind
   and survives only as an explicit self-consistency diagnostic that can
   never yield admission or a PASS. `analytic_exponential` remains the
   independent closed-form rehearsal reference (the stored kernel is
   validated against `R exp(-alpha t)` first).
5. (REVISED to the adopted working-check rule) Reciprocal-average
   sensitivity is a WORKING check, not recorded fields: from the loaded
   public artifact's raw vs processed arrays at the case omega under the
   SAME physical M/K/C/F, `Z_raw` vs `Z_processed` are compared as
   work-conjugate `S^T Z S` max entries (J floor/abs 1e-9, relative 0.01)
   and the full coupled complex response is compared as the DIMENSIONLESS
   `S^-1 qhat` (floor/abs 1e-8, relative 0.02; retained SI solutions
   recorded separately). A singular/meaningless raw FD is a typed
   non-success with diagnostics, never skip=>pass. The report records the
   TD/FD reference SOURCE identity so a gate cannot masquerade as kernel
   self-reference.
6. Assessment artifacts (case/report JSON) are exclusive outputs under
   `<project>/assessments/`; the project itself (with the selected result)
   is persisted only through the existing `ApplicationService.save` /
   ProjectStore route. No new project persistence is invented.
7. Small-excursion envelope: the case declares an explicit caller length
   scale; the report records the actual max |q| excursion per DOF against
   it (no demeaning anywhere).

## Intended edits

- NEW `src/anymarine/linear_assessment.py` (single assessment module,
  numpy-only, no Qt/Capytaine/ANYsolver at import).
- Minimal wiring: `src/anymarine/services.py` (3 delegate methods),
  `src/anymarine/cli.py` (3 subcommands), `src/anymarine/__init__.py`
  (export), `README.md` (short section). Protected physics/domain/persistence
  code untouched.
- NEW `tests/test_linear_assessment.py` (+ `-persistence`/`-cli` companion if
  useful), NEW `examples/linear_assessment.py`, NEW
  `docs/LINEAR_ASSESSMENT.md`.

## Remaining checks planned (worker 180 s allowance, harness-only)

Import/base-import guard, focused assessment suite (analytic/mocked,
<= 1003 points, 4 DOFs), CLI end-to-end on a saved project, example run.
Exact harness commands with unique `linear-assessment-worker-01+` evidence
paths; full logs on disk, concise counts in the final handoff.

## Source/reference admission guard — final reserved-contract delta (worker-16, compact checkpoint saved before checks)

- Loophole (static inspection): `prepare_case`/`assess_result` selected the
  source proof only by `reference.kind == hydro_spectrum`, so a
  `LinearDevelopmentInputs` with `provenance.source == LINEAR_SOURCE_HYDRO`
  could declare `analytic_exponential` and bypass the mandated actual
  source-spectrum/context route. The governing contract has always been: for
  HYDRO inputs the RAO reference MUST be the actual neutral/derived
  spectrum; declaring a different reference kind cannot revoke hydro
  ownership.
- Implemented SMALL shared guard `_admit_source_reference(inputs, mode,
  reference_kind, error)` in `src/anymarine/linear_assessment.py`, called at
  BOTH `prepare_case` (raises `AssessmentCaseError`) and `assess_result`
  (raises `AssessmentRefusalError`, runs before any eligibility proof so a
  loaded/direct `AssessmentCase` cannot bypass preparation):
  - hydro source + `regular_wave` + non-`hydro_spectrum` reference -> refused;
  - hydro source + `free_decay` -> explicitly unsupported/refused until its
    source-only assessment contract is added; never relabelled analytic;
  - explicit/analytic rehearsal inputs stay admitted unchanged.
- Reference-family admission only: no physics, tolerances, criteria,
  optional dependency or protected file changes; same owned
  assessment/services/tests/example/docs boundary.
- Focused source-intent refusal tests added in
  `tests/test_linear_assessment.py` (registration x2, loaded/direct case x2).
- Honest pending gap: hydro-source free-decay assessment remains PENDING.
  A refinement diagnostic does NOT establish individual member physical
  admission; any future qualification must also pass each complete member
  assessment/source proof.
- Checks: pending at checkpoint time (harness, each <= 15 s); accounting to
  be appended after checks. Parent numerical tests remain paused; parent
  reserve untouched.

## Parent installed verification

The final development wheel passed16 independent installed cases and37 focused
regressions copied outside the source checkout. Source/installed Python and
bundled-example resource bytes matched; the startup example still loads2bodies
and81frames. The final source-reference guard's repaired regression passed in
this installed run. Full commands, failures, exact wall accounting and hashes:
`C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-final-manifest.json`.
All worker activity is terminal. Windows development checks do not establish
scientific qualification, Linux support or release acceptance. Hydro-source
free decay remains refused/pending. Refinement is a diagnostic comparison;
self-contained paired-result reproduction remains a future connected slice.
