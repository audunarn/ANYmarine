# PAUSED restart point - 2026-10-10

Development is PAUSED at the user's request. Saved 2026-10-10T17:08:35.844126+00:00.
This section supersedes historical checkpoint states below. The full goal remains
incomplete: implement the ANYmarine multibody potential-flow/Morison roadmap.
Canonical repositories: C:/Github/ANYmarine and C:/Github/ANYhydro.
Root coordinates Mistral directly; do not launch Astra. No publication is authorised.

## Exact stopping state

- Energy checker implemented and independently reviewed: 50 focused mock tests
  passed (energy-tests-08, process wall 62.80005879999953 s); adopted independent
  review findings were closed. Product checker SHA256:
  a808c0b5b8b41cc052131f967cdb74a79754bb1d2f2b3b0b4d206dc561394447.
- Native energy-point-02 is TERMINAL, exit 1, process wall
  1657.9393863000005 s. Child completed_unqualified; five Green-function
  evaluations, one system solve, no Capytaine BEM solve, one effective thread.
  All seven registered self finite-difference checks passed. This is diagnostic
  evidence, not numerical admission.
- Reference comparison FAILED: added mass 10/16 pass, 6/16 fail; damping
  8/16 pass, 8/16 fail; mean power failed. Candidate power
  +0.0008646219627273671 W versus HAMS +0.0015879742100973749 W;
  difference 0.0007233522473700077 W exceeds registered limit
  0.000047649226302921244 W. Candidate sign is positive. W5 remains NO_GO,
  general numerical admission false. No plan-voiding conclusion is established.
- Independent Mistral stored-array audit run-02 is COMPLETE, 100/100 checks
  passed and all three failed reference predicates reproduced. Audit consistency
  does not establish physical acceptance. Script SHA256:
  6139d28d3ea9ea065964ce057d24243b7cd4d6afeac9da592b9b32b7d23b1b66.
- Audit repair worker ended by timeout: 247.60715029999847 s, exit 1;
  combined review pool 420.2959906000033 s. Its final handoff and proposed extra
  hull/lid source diagnostic were not delivered. The completed audit is retained.
  Terminal sessions: native 11666, audit preparation 13606, audit repair 3131.
  Owned audit PID 65868 was absent at pause inspection. No new activity launched.

## Evidence and identities

All paths below are absolute; keep failed attempts and raw arrays unchanged.
- Native summary and arrays:
  C:/Github/ANYhydro/.development-evidence/w5-e1-energy-check-20261010/results/energy-point-02/
- Independent audit and preserved failed run-01:
  C:/Github/ANYhydro/.development-evidence/w5-e1-energy-check-20261010/audit02/
  Current report: results/run-02/audit-report-02.json; receipt:
  receipts/run-02/audit-execution-02.json. Read the report's limitations.
- Exact process receipts, review reports and runtime accounting:
  C:/ANYworkspaceWork/anymarine-evidence/w5-e1-energy-check-20261010/attempts/
- Protected source baseline (101 files): workspace/protected-originals.json,
  where workspace is the preceding C:/ANYworkspaceWork evidence directory.
  Last inspected protected baseline had zero mismatches.
- Packet criteria SHA256 eef713eb88f1d1defddcbbcfdecc076487e07d9b6faa03934170461255bd3387;
  consumed request c1fe27a56d3c2b4c602c15a43b3ea48dec8b4ef174dacb308efc87e8a0b64864.
  Root issuance: repository evidence receipts/root-issued-energy-02.json.
  Request inputs/proposed-admission-energy-02.json.consumed must not be reused.
- Existing working trees contain extensive untracked application/library work.
  ANYmarine LICENSE is pre-existing modified work; preserve it and MPL-2.0 policy.
  Nothing committed, reset, discarded or published for this pause.

## First action after an explicit resume

1. Read current global/repository AGENTS, TASK.md and docs/IMPLEMENTATION_PLAN.md;
   verify canonical roots, relevant dirty ownership, exact checker/audit identities
   and saved receipts. Do not restart terminal energy-point-02.
2. Give Mistral a bounded stored-evidence diagnosis of the failed physical
   comparison: reconcile case/reference conventions and auxiliary-lid treatment,
   mean versus node boundary residuals, and near/far energy accounting. Existing
   diagnostics show far power about 0.00488087583698 W, node residual max about
   0.782 but mean residual about 1.59e-15. These are questions, not adopted causes.
   Recover a concise handoff from existing audit arrays/logs before further work.
3. Choose a discriminating next check from that diagnosis. Root retains adoption
   of physics, compatibility, tolerances and scientific acceptance. Preserve rate,
   coordinates, reference definitions and failed evidence; do not flip signs,
   clamp matrices, weaken criteria or automatically refine/rerun.

Execution pools are frozen for pause. The native one-shot was consumed even
though comparison failed; unused seconds do not authorise a retry. Review pool
is exhausted. Resume requires a prospective bounded decision for any new checks
under the user's execution-limit discretion; pause itself grants none.

Keep remaining roadmap gates: full frequency/PSD and A-infinity checks,
added-mass AND damping reconstruction, kernel tail, linear RAO/free-decay;
progressive six-DOF/environment/lines/contact, matched backend cases and fields,
complete headless demonstrator before full GUI acceptance, installed Windows/
Linux GPU and software fallback/reproduction, and thin-sheet/CAD/wave milestones.
Existing GUI/examples and startup/wave work are preserved; desktop qualification
and the full plan remain open. Historical notes below are evidence only.

---

# CHECKPOINT — linear-development integration slice (2026-10-08, early checkpoint)

Status: investigation complete, design fixed, implementation starting. All
current uncommitted files are preserved as baseline; nothing has been edited
yet in this slice.

## Findings (inspected, not assumed)

- Baseline is coherent and retained: `domain.py` (Body/ResultRecord/
  ProjectModel, frozen + defensively copied), `hashing.py` (canonical JSON
  project hash), `persistence.py` (content-addressed sidecars, atomic
  manifest commit, schema_version 2), `services.py` (Qt-free shared service,
  job snapshot/validate), `worker.py` (JSON in/out, exit 0/2/3/4, cancel
  file), `cli.py`, `qtapp/` (JobManager QProcess, MainWindow replay),
  9 test modules. ResultRecord already carries generic `channels`, so
  q/v/a + force decompositions fit without a schema break.
- Solver public API (`C:/Github/ANYsolver/src/anysolver/marine_linear.py`,
  read-only): `MarineLinearSpec` (labels, dt, times, forces, mass_dry,
  added_mass_infinite, damping, stiffness, radiation_kernel, kernel_times,
  q0/v0), `solve_marine_linear(spec, cancel=...) -> MarineLinearResult` with
  q/v/a, full force decomposition, residual, complete/cancelled flags;
  `exponential_radiation_kernel`. It validates grids/shapes/finiteness/
  symmetry/positive-definiteness itself and fails closed. I call it; no
  numerical integration is duplicated.
- Isolated runtime `anymarine-evidence/runtime` (Python 3.13.9) has PySide6;
  ANYsolver is NOT installed there, so worker/CLI/test paths must locate it
  via explicit PYTHONPATH (`C:/Github/ANYsolver/src` allowed) and fail
  closed with an actionable message when absent.
- `run_check.py` records exact command, wall seconds, exit code, full
  stdout/stderr; unique evidence dirs required per attempt.

## Intended edits (all inside C:/ANYworkspaceWork/ANYmarine only)

1. `src/anymarine/linear.py` (new, Qt-free, solver-optional): immutable
   `LinearBodyBinding` (body_id, name, reference_position, mass — order
   significant) and `LinearDevelopmentInputs` (4-DOF two-body surge/heave:
   dt, t0, n_steps, forces, Mdry/Ainf/C/K, kernel + kernel_times, q0/v0,
   provenance source/description/created_utc). Construction validates
   shapes/finiteness/grid compatibility and fails closed. `to_dict`/
   `from_dict`, `linear_input_hash` over the full effective inputs.
   `analytic_demo_inputs(project)`: clearly labelled analytic demo with a
   linear link (off-diagonal K coupling between the bodies), matrices
   derived from body masses, exponential kernel via the solver's public
   builder. Explicit supplied matrices/kernel admitted through the same
   record. `run_linear(inputs, cancel)` lazily imports
   `anysolver.marine_linear` (ServiceError if missing), calls the public
   API, and converts only complete, non-cancelled, finite results into a
   `ResultRecord` of new kind `linear_development`: q/v/a and all force
   decompositions stored as authoritative channels; surge mapped to x,
   heave to z at the fixed equilibrium reference position; zero rotation.
2. `domain.py`/`__init__.py`: add `RESULT_KIND_LINEAR = "linear_development"`
   to RESULT_KINDS; `ProjectModel` gains optional `linear_inputs` field
   (default None) wired through `with_edits`/`to_dict`. Legacy projects
   without the field keep working.
3. `hashing.py`: linear-aware staleness — a linear result is stale when the
   project has no stored linear inputs, or the stored inputs rebound
   against current bodies (identity/order/reference/mass/name) no longer
   reproduce the result hash. Synthetic hashing/staleness unchanged.
4. `persistence.py`: optional content-addressed `linear_inputs` sidecar
   referenced from the manifest (schema_version stays 2; absent key loads
   as None, so old manifests remain loadable).
5. `services.py`: `linear_demo_inputs`, `set_linear_inputs`, `run_linear_local`,
   `linear_job_snapshot` (full effective inputs + body binding in plain
   JSON), kind-aware `add_result`/`result_stale`/`stale_result_ids`.
6. `worker.py`: accept `linear_development` snapshots — rebuild inputs,
   verify hash, verify body binding against the snapshot project (stale
   identity/order/reference refused, exit 2), import solver (missing →
   exit 4 with actionable message), run with cancel-file polling doubling
   as progress, refuse cancelled/incomplete output (exit 3, no output
   file), atomic write only on complete validated results.
7. `cli.py`: `run-linear --project DIR [--demo | --inputs FILE.json] [--local]`.
8. `qtapp/jobs.py` + `mainwindow.py`: JobManager submits linear jobs
   (propagating `ANYMARINE_SOLVER_PATH` into the child PYTHONPATH); GUI
   action creates/loads the linear demo inputs and runs the QProcess job;
   labels distinguish "linear development (analytic demo)" from synthetic.
9. Tests: input validation/immutability/hash, demo determinism + labelling,
   service local run (solver present/absent), worker subprocess success/
   cancel/invalid/stale-binding/missing-solver, persistence round-trip +
   legacy load + staleness on body edits/reorder, GUI job path including
   cancellation and the demo action.
10. README/HANDOFF updates (TASK.md untouched — parent owns).

## Decisions

- DOF labels are `"<body_id>:surge"`/`"<body_id>:heave"` (stable, unique);
  channel names `q:`/`v:`/`a:`/`force_external:`/`force_dry_inertia:`/
  `force_added_inertia:`/`force_memory:`/`force_damping:`/
  `force_stiffness:`/`force_residual:` + label.
- Body binding includes name and mass as well as identity/order/reference:
  the replay is presented against those bodies, so any edit flags stale.
  Stricter than the minimum (identity/order/reference) and consistent with
  fail-closed intent.
- No hardcoded sibling path in application code: the solver is located by
  normal import; `ANYMARINE_SOLVER_PATH` (tests/checks) extends the child
  process PYTHONPATH. Fail closed when the optional solver is missing.
- Solver equation/signs/direct convolution/zero prehistory untouched; no
  Capytaine conversion, no nonlinear mechanics, no qualification claims,
  no BEM runs; jobs bounded (<=1001 frames, 4 DOFs).

## Remaining work

Implement 1–9 in order, then focused checks via
`C:/ANYworkspaceWork/anymarine-evidence/run_check.py` +
`runtime/Scripts/python.exe` (sequential, single numerical thread, <=60 s
each, cumulative 180 s process-wall with 60 s reserved for final
verification), unique evidence dirs, explicit PYTHONPATH
(this repo/src + C:/Github/ANYsolver/src), full failure logs preserved;
then README/HANDOFF.
