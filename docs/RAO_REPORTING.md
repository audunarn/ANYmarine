# RAO reporting with Morison and mooring contributions — design

Status: working design (2026-10-12), development only. Owner decisions: RAOs are produced
both ways and compared (linearized frequency-domain table, checked by time-domain
extraction at selected frequencies); headless (Qt-free module, JSON/CSV, service + CLI)
plus a GUI view. The additional numeric criteria (marked *proposed* below) were adopted by the owner on
2026-10-12 (IMPLEMENTATION_PLAN, RAO reporting criteria); nothing is tuned after evaluation. ANYsolver is used through its public API
only (no edits). Line references are to the code as read on 2026-10-12.

## Conventions

- Phasor `x(t) = Re{X exp(-i omega t)}`, `X = a + i b` for `x ~ a cos(wt) + b sin(wt)`
  (as `linear_assessment.py`, ANYhydro `radiation.py`, `excitation_series.py`).
- Body DOFs: additive chart coordinates about the equilibrium anchor, translation of the
  body origin and rotation vector theta with `R = Exp(theta) R0`
  (`anysolver/marine_rigid.py:111-150`). At theta = 0 chart covectors equal the WORLD
  wrenches at the body origin stored in `AcceptedStepRecord.force_decomposition`.
  Mooring node DOFs (3 per free node) follow the body block.
- RAO = response phasor per unit incident amplitude, per body DOF (amplitude, phase in deg).

## 1. Frequency-domain model (same physics as the native time-domain model)

Linearization point: a stationary state at which the wave-free model has zero residual within
the `CoupledStaticSpec` limits, audited with `evaluate_marine_static`
(`anysolver/marine_coupled_equilibrium.py:68-99`). Its source is declared in the case:
`declared_initial_state`, `adopted_equilibrium` (verified adoption receipt) or
`static_solve_in_job` (`solve_coupled_marine_equilibrium` on the wave-free model).
Failing the audit is a typed refusal. The wave-free model is a `model_to_dict` copy with
every environment component amplitude set to 0 (static routes refuse active waves).

Tangents at that point, from one public call set: `ContributionSet(...).evaluate_trial`
in the `initialization` phase (links, dry gravity, potential flow, Morison),
`RigidBody6D.dry_tangents` and `MooringLineAssembler.evaluate` (full body+node K, C, M).
Per-contribution blocks come from each contribution's `evaluate_trial_with_context`.
Tests check the tangents against central differences of the public residual (1e-8 rel).

Per frequency: `Z(w) = K - w^2 (M + A_rad(w)) - i w (C + B_rad(w))`, `Z q = X`.

- Rigid body: `dry_tangents` mass (CG offset included); gyroscopic terms vanish at rest.
- Potential flow (h-gradient development basis): restoring and A_inf via the pullback `D`
  (`marine_hydro_coordinates.py:290-335`). Radiation from two labelled sources:
  `kernel_discrete`, the exact steady-state transfer of the solver's trapezoidal
  convolution (`marine_potential_flow.py:310-316`), which checks integration only and can
  never be a reference; and `dataset`, `A(w)`, `B(w)` of the bound ANYhydro dataset at
  exact samples, the RAO reference when it exists. Excitation: the single-omega fixture
  phasor (`excitation_real + i excitation_imag`, omega must match), or the one-component
  `build_excitation_series` of the bound source.
- Links: exact 12x12 stiffness/damping from `MechanicalLink6D.evaluate`.
- Morison members: inertia terms are linear and exact. Added mass equals the solver's
  `Ma` tangent; the excitation `X_M = int J^T (m_f + m_a) P a_f dl` is obtained exactly from
  three public evaluations of the contribution at the fixed stationary geometry
  (t = 0, T/4, T/2), because the force is linear in the Airy kinematics when drag is zero;
  this keeps the solver's own quadrature (implemented; verified against the analytic
  finite-depth vertical cylinder to 1e-10). Tangents come from a wave-free copy of the
  project (calm-water linearization; wave-gradient terms are second order). Drag uses a
  declared `drag_linearization`:
  - `none`: all drag coefficients are 0 (exact linear model);
  - `tangent_about_current`: the solver's tangent damping at the current, with mean drag
    in the equilibrium; the wave/current velocity ratio is reported;
  - `equivalent_harmonic`: zero current only, `(8/3pi)|w|` linearization iterated on the
    response to a declared convergence; non-convergence is refused.
  Mixed wave+current with nonzero drag and no admitted linearization is reported only.
- Mooring (dynamic lumped lines): node DOFs are kept in the complex system; the condensed
  body impedance `Z_bb - Z_bn Z_nn^-1 Z_nb` is reported as the mooring block. It is
  algebraically identical for the body response; no quasi-static model is offered. Line
  fluid inertia is linear and exact; line drag follows the member policy. Validity needs
  every segment taut with a declared margin (predicted dynamic tension < static tension)
  and no contact branch change. Otherwise the case is reported as nonlinear
  (`tension_only_branch` or `contact_branch_change`).
- Per-contribution force phasors `F_c = X_c - (K_c - w^2 M_c - i w C_c) q`, with the balance
  `sum_c F_c = -w^2 M_dry q` checked to roundoff, and FD power `1/2 Re(F_c conj(-i w q))`.

### Implemented status (2026-10-12)

- Mooring (slice 7): node DOFs are kept (3 per free node after the bodies) with the
  assembler's exact sparse K/C/M; the static audit includes node residuals. A case is taut
  when every segment tension is positive at the linearization point and, at the largest
  declared amplitude, (a) the exact minimum of each segment's linearized tension
  `T_s - |G q + G_v (-i w q)|` (position and rate gradients of the public assembly) and (b)
  the sampled minimum of the full nonlinear assembly along the predicted orbit (1024
  samples, every discrete local minimum refined) both stay above 5 % of that segment's
  static tension; otherwise `nonlinear_reported`. Observed slack (stored tensions) or
  missing tension evidence demotes a run.
  Line fluid inertia excitation uses three public force evaluations. Seabed contact and
  line drag are refused for now. Verified: exact taut-line equilibrium, mooring tangent vs
  central differences, axial EA/L0 tension gradient, and a native campaign with node DOFs
  passing the comparison.
- Member drag (slice 8): describing-function (first-harmonic) balance. For a trial response
  the stateless member contribution is evaluated over one period at the moving states and the
  fundamental of its reported drag component is balanced by Newton iteration on (Re q, Im q)
  with a finite-difference drag Jacobian and backtracking (tolerance 1e-8). The inertia
  excitation excludes the drag component. With drag the FD table has one entry per declared
  amplitude. Member drag with current is refused (no admitted linearization). Verified: the
  fixed vertical cylinder matches (8/3pi) b int U^2 dz to 1e-6; a native drag campaign passes
  the linearized-nonlinear gates (steadiness, power closure, omega dt) with the RAO
  difference reported. Linearized-nonlinear cases gate on convergence/balance only.

## 2. Time-domain extraction

- One native run per (omega, heading, amplitude), built as a derived project through the
  existing envelopes: the component set for Morison and mooring environments, the potential
  flow `omega`, or a one-component `wave_realization` + `excitation_series` (as
  `sea_state_service.py`, re-proved by `prepare_native_run`). Initial state = the
  linearization point. `dt` = the potential-flow `dt`. Steps cover a declared transient
  (at least the kernel memory) plus two equal steady windows of at least 2 cycles each.
  `omega dt >= pi` is refused; `omega dt` and the Newmark period-elongation estimate are
  reported against a declared `max_omega_dt`. At least two amplitudes (default a and a/2).
- Each run executes like a native job (snapshot, solve, `record_from_evidence`) and is an
  ordinary replayable `ResultRecord`.
- DOF series from `EquilibriumHydroFrame(...).evaluate`; node displacements from the
  stored nodes.
- Harmonic fits (new `harmonic_fit.py`): stacked least squares with intercept, harmonics
  1..K and optional drift, condition/rank refusals, two adjacent windows for steadiness,
  THD, drift slope and residual RMS.
- Per-contribution force harmonics from the stored `wrench[cid, bid]` and node force
  channels. Sub-terms (excitation/radiation/restoring; Froude-Krylov/added mass/drag) are
  not persisted by the solver, so they are re-evaluated from stored kinematics and labelled.
- Checks: frame residuals; mean power per contribution over whole cycles (sum ~ 0; stored
  dissipation channels cross-check); energy periodicity between windows.
- Nonlinearity signals (reported): steadiness, mean offset, drift slope, THD, the RAO
  difference between the two amplitudes, minimum segment tension, contact flag changes,
  maximum rotation against the small-angle bound, and potential-flow motion-bound
  proximity.

## 3. Categories and criteria

The category is derived from the inputs at case registration and cannot be chosen:

- `matched_linearized`: dataset radiation source, no drag, small-angle links, taut lines
  without contact switching, no current, audited linearization point. The RAO comparison
  gates on the plan's 3 % amplitude and 3 degrees phase (`IMPLEMENTATION_PLAN.md:2568`),
  near-zero DOFs on an absolute bound; plus steadiness, fit, amplitude independence,
  THD and power closure (*proposed* limits: steadiness 0.02, linearity 0.01,
  `max_omega_dt` 0.05). Verdict pass or nogo.
- `kernel_consistency_only`: the same model without a dataset (synthetic fixtures); same
  checks against `kernel_discrete`, labelled as time-integration consistency, never
  source admission.
- `linearized_nonlinear`: declared drag linearization or a current tangent. RAO
  differences are advisory (*proposed* bands 10 %/10 deg); only convergence/balance
  checks gate.
- `nonlinear_reported`: slack lines, contact switching, large motion, or mixed drag
  without an admitted linearization. Everything is reported, with no RAO verdict.
- A run that violates its category's preconditions is demoted with the reason and its
  matched verdict set to nogo; never silently re-categorized.

## 4. Artifacts and entry points

- Modules (Qt-free), as implemented: `rao_case.py` (closed-world hash-bound case),
  `rao_linearize.py` (calm-water tangents, potential-flow/Morison/mooring terms),
  `harmonic_fit.py`, `rao_extraction.py` and `rao_report.py` (features/category, FD table,
  campaign, comparison, CSV). Child-process job transport for the GUI (`rao_jobs.py`) is
  part of slice 9.
- Comparison safeguards (review round 1): the table must belong to the case and the
  campaign must cover the case grid exactly once; unresolved DOFs (FD reference below the
  declared near-zero amplitude) get absolute checks on their higher harmonics, residual and
  window change; linearity compares every run with the largest-amplitude run (order
  independent); predicted motion uses vector magnitudes; observed peak excursion/rotation
  outside the small-motion bounds demotes the case (verdict nogo); mean contribution powers
  (dry inertia included) must close. Each run's record, partial or failed evidence is stored
  as soon as it exists; the per-run wall limit is enforced through the solver's cancel hook.
- Service (with slice 9): table/campaign snapshots and output re-admission for the GUI job.
- CLI: `rao-prepare`, `rao-table`, `rao-run` (the FD table, campaign, comparison, report, CSV and
  run records). Exit codes: 0 pass (or table written), 3 nogo, 1 refusal/failure (a
  `failure.json` marker is written once an output directory exists), 2 usage error.
- Artifacts: `rao_table.json`, `rao_extraction.json` per run and `rao_report.json`
  (hashes exclude `created_utc`), plus CSV files `rao_table.csv`, `rao_td.csv`,
  `contribution_forces.csv` and `comparison.csv`. Stored under `<project>/rao/`. Campaign
  runs are project results, labelled as campaign runs bound to their case.
- GUI: an "RAO" dock beside Assessment/Waves. It shows amplitude/phase plots against
  omega (FD curve, TD markers per amplitude), a per-contribution force table, a checks
  table and scope/verdict labels, with detached labelling after edits. A RAO menu has
  Register case, Compute FD table (job), Run TD campaign (job), Export CSV and Reopen
  report. The panel never computes.

## 5. Slices

1. `harmonic_fit.py` + `rao_case.py` (schema, hashing, refusals, category derivation).
2. Linearization for dry body, links and gravity; analytic single-DOF RAO; tangent
   finite-difference checks.
3. Potential flow: `kernel_discrete` (exponential-kernel oracle) and dataset pullback.
4. Morison inertia: analytic vertical cylinder; `A_M` equals the solver tangent.
5. Time-domain extraction; an exact linear oscillator through the potential-flow fixture
   (A_inf = 0, kernel = 0, diagonal restoring).
6. Jobs, worker, service and CLI with a real child-process campaign.
7. Mooring and contact blocks; taut-segment analytic stiffness; slack example classified
   as nonlinear.
8. Drag linearizations.
9. GUI.
10. Docs and status.

Public-API gaps (worked around, not patched):
- no "tangents at state" entry, and no per-component mooring tangent blocks;
- sub-term forces are not persisted;
- the Morison wet quadrature is private;
- static routes refuse waves;
- series projects bind to the bundled fixture source.

Risks:
- Newmark phase error at larger `omega dt`;
- truncated fixture kernels;
- finite-rotation limits on matched amplitudes;
- campaign cost.
