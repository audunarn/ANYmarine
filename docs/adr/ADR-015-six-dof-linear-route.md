# ADR-015: Six-DOF rigid-body linear route (contract)

Status: ADOPTED by the owner 2026-10-15 (rev 7; Codex review round 7: fit for adoption) (programme L6, plan
`C:/ANYworkspaceWork/anymarine-evidence/linear-6dof-20261015/PLAN.md`). Not implemented. Builds on ADR-003 (dry
mass authority), ADR-004 (hydrodynamic ownership, hydrostatic composition), ADR-011 (residual, coordinates) and
ADR-014 (matrix replay). Rev 2-6 answer Codex review rounds 1-5. Owner decisions 2026-10-15: poles are diagnostic only (rev 5);
stability is established by structural admission, time-domain runs only set settling and fitting windows (rev 6);
asymmetric source operators are handled by a declared symmetrisation (rev 7) (session 01a13f40-e75c-7ab0-a9eb-3adbca477db0).
Nothing here qualifies results; every output stays "linear development, unqualified".

## Context

The linear route (`LinearDevelopmentInputs`, `run_linear`, `hydro.import_hydro_inputs`, derived import,
`linear_assessment`) is a fixed four-DOF layout: two bodies, surge and heave, scalar dry mass, zero rotation
(`domain.LINEAR_DOF_COUNT = 4`). `anysolver.marine_linear` is dimension-independent (N labels, dense N x N mass,
A_inf, damping and stiffness, (n_kernel, N, N) kernel covering the whole run at the run dt). A ship (OrcaWave L01)
needs all six rigid DOFs. The change/preserve/refuse disposition per function is in `L6-0-disposition.md` (plan
folder); `L6-0-inventory.md` is the raw search behind it.

## Decision

### 1. Layouts and identity
- `LinearDevelopmentInputs.layout`: `"two_body_surge_heave_v1"` (legacy; the field is omitted when serialising it,
  so legacy records hash and serialise exactly as today) or `"rigid_subset_v1"`. Unknown layouts are refused.
- `rigid_subset_v1`: per body an ordered subset of (surge, sway, heave, roll, pitch, yaw), bodies in project order,
  N = 1..6 x n_bodies. Solver labels keep the single-colon form `body:dof`; ANYhydro keys keep `body::dof`; the map
  between them is explicit and hashed.
- Frozen regression fixtures (legacy raw and derived projects, inputs, results, assessment cases/reports): numerics
  and hashes reproduce bit-identically; IDs/timestamps compared separately; omitted new fields load as legacy.

### 2. Dry mass (ADR-003 authority)
- Each body needs scalar mass plus `BodyMassProperties` (CG and full CG inertia, body axes); absence refuses the
  layout. Dry 6 x 6 matrix:
  `RigidBody6D(body_id=..., mass=..., cg_body=..., inertia_cg_body=..., reference_origin=body.reference_position,
  reference_orientation=body.reference_orientation)` then `rb.mass_matrix(rb.state())` at zero increments: about the
  reference point, world axes, including the -m S(c) coupling. Retained DOFs are a row/column selection.
- `dry_tangents` excludes gravity; gravity is assembled separately (section 3).
- Mass record and reference pose are hashed; edits make inputs and results stale. Tests: offset CG,
  non-diagonal inertia, edited inertia, rotated reference orientation.

### 3. Restoring (ADR-004 admission contract)
- K_total = K_buoyancy_chart + K_gravity_chart + K_external, all in the solver chart: reference-point
  translations (world, m) and small rotation vector theta (world, rad), K = -dQ/dq.
- K_buoyancy_chart comes from the source only through a bound hydrostatic contract
  (`anyhydro.hydrostatic_contract`): declared force basis, source coordinate/rate map D, derivative semantics and a
  measured baseline wrench, all content-bound to the dataset. The conversion is ADR-004's pullback
  K_chart = D^T K_source D - columns[(dD^T/dq_j) F_baseline]. Unresolved semantics refuse. The current Capytaine
  backend computes buoyancy-only stiffness but does not yet attach that complete contract: providing it is an
  ANYhydro deliverable of L6-4 (owner: ANYhydro), consumed by L6-3.
- K_gravity_chart: mechanics-owned, ADR-004 origin form at theta = 0 with one declared g (the run's gravity),
  W = m g, c the world CG relative to the reference point: K = -W (c_z I - c e_z^T) - 0.5 skew(tau_baseline) on
  the rotational block, tau_baseline = -W c x e_z.
- Equilibrium check per body (never summed across bodies): full wrench (buoyancy + gravity + declared preload or
  declared constraint reactions) about the reference point; force residual <= 1e-3 W, moment residual <= 1e-3 W L
  with L the body's declared positive length scale (waterline length for ships). Failure refuses with the residual.

### 4. Radiation admission
- A_inf and the memory kernel from `anyhydro.radiation` (damping-spectrum cosine transform; Prony v2 fits the
  finite-depth Green function and is not used here).
- Gate: a bound raw-source full-block route (L6-3/L6-4), because raw reconstruction comparison accepts only
  coefficientwise criteria today and derived recompilation is not generalised. Normalisation q = S q_hat,
  X_hat = S^T X S applied to both reference and reconstruction before norms and eigenanalysis; S = diag(1 for
  translations, 1/L for rotations) per body; scales and prospective criteria hashed. `_full_block_report`'s current
  SI comparison is corrected by this normalisation (a no-op for translation-only W5).
- The kernel must cover the whole run at the run dt (solver requirement); the memory cutoff for judged cases is a
  Bartlett taper of length H (section 8 (iv)); full-run arrays are kept (zero after H).

### 5. Forcing, damping and external matrices
- Regular wave (amplitude, omega, heading, phase) from dataset excitation at an omega in the dataset grid
  (existing `wave` option), all retained DOFs; ANYmarine phasor x = Re{X exp(-i w t)}.
- Irregular sea (slice N3, 2026-10-16): `wave = {kind: "irregular", realization: <canonical ANYloads
  WaveRealization dict>, slow_drift: null | "geometric" | "arithmetic"}`. Every component frequency inside the
  admitted reconstruction window; first order by ANYhydro's excitation series (complex coefficients interpolated
  linearly inside the stored range, never extrapolated; headings exact modulo 2 pi), same phasor convention as the
  regular wave. Optional Newman slow drift (ANYhydro N2, docs/MEAN_DRIFT_CONTRACT.md section 8): single body,
  unidirectional, from the dataset's far-field mean drift; added to the retained surge/sway/yaw DOFs (the others
  are dropped and recorded; none retained is refused). Recorded in the import details (`irregular_wave`: realization,
  excitation and drift series hashes, applied/dropped DOFs, mean drift); the provenance `wave` stays null (no
  regular selection) and the frequency-domain references refuse irregular imports. The options (with the full
  realization) are in the rigid bundle, so verification re-runs the synthesis. Regular and free-decay imports are
  unchanged (bit-identical inputs and provenance).
- Constant linear damping C and external stiffness: explicit records with ordered DOF keys, SI units, world axes,
  reference point, kind, additive semantics, source identity, constant scope; hashed and carried through
  recompilation; never folded into hydrodynamic coefficients.

### 6. Results, persistence, replay
- Result format discriminator `linear_development` + `result_layout: "rigid_subset_v1"` stored in the record itself
  (results stay identifiable after inputs change); older schema versions refuse such records (no downgrade).
- Mandatory producing snapshot (ADR-014 style: bodies, reference poses, mass records, layout, matrices hash).
- Stored: q, v, a (N channels) and the force decomposition; per body reference-point translation u and rotation
  vector theta; `rotation_matrices` (BODY-to-WORLD, R = Exp(theta) R0) admitted for this layout; Euler
  `orientations` empty. Replay: x_world = r0 + u + R (x_body), producing-snapshot geometry.
- Decoder checks: proper rotations, matrices equal Exp(theta) R0 from stored q, positions equal r0 + u, frame
  inventory complete; tamper/truncation/cancellation refused.
- Small-angle envelope: declared per run (default |theta| <= 0.2 rad, |u| <= 0.2 L); replay labels frames outside
  it; validation runs outside it fail L6-5 (section 8).
- Changes allocated: domain (ResultRecord), persistence, worker/CLI decoding, GUI replay dispatch (by result
  layout, not only native kind).

### 7. Shared frequency-domain tools (slice L6-A) and assessment scope
- L6-A delivers an independent numerical module (`anymarine.linear_fd`, no AssessmentCase dependency) with explicit
  inputs: the N-DOF matrices (M_dry, A_inf, C, K_total), the stored causal kernel with its cutoff and weights, the
  source-proved N-DOF spectrum A(w), B(w), F(w) taken from the L6-3 import provenance (bound to the dataset hash),
  and explicit fit controls (window length, count, residual rule). It provides:
  (a) Discrete consistency reference - the exact Newmark/kernel steady state at the actual forcing frequency w:
      r_N = (2/dt) tan(w dt/2) (trapezoidal effective rate, applied to velocity and acceleration),
      G_d(w) = dt sum_{l=0..L_H} w_l K_l exp(i w l dt) (lag phases at the actual w; the stored kernel - Bartlett
      tapered for judged cases, so K(H) = 0 there - is zero after H = L_H dt; in the finite-memory steady regime
      the solver's weights are w_0 = 1/2 and w_l = 1 for l = 1..L_H; the same weights are used in Ktilde(s); an
      oracle case with a nonzero last retained sample tests the weights),
      Z_d = K - r_N^2 (M_dry + A_inf) - i r_N [C + G_d(w)], X_d = Z_d^-1 F.
  (b) Dataset RAO reference - continuous Z(w) = K - w^2 (M_dry + A(w)) - i w (B(w) + C), X = Z^-1 F, with identical
      total restoring, external damping and excitation conventions.
  (c) N-channel harmonic window fitting (generalised `_fit_window`), the memory oracle (generalised
      `_memory_oracle`), time-domain stability/settling evaluation (section 8) and diagnostic poles (section 8).
- L6-5 evidence is produced by a declared evidence script on top of `linear_fd`, recorded with input hashes and
  adjudicated against section 8 by that script and Codex review; it does not use the assessment API.
- Refusal guards (explicit "not available for six-DOF layouts") ship in the slice that first makes the layout
  reachable: L6-1 for domain/persistence, assessment, W5, derived-import and CLI entry points (new-layout inputs
  and results can exist from L6-1); hydro-import-specific guards in L6-3. Never deferred to L6-7. L6-7 later generalises the assessment API (cases, reports, source proof) on top of `linear_fd`.

### 8. Validation criteria (declared before any physical run; L6-5)
- Analytic, frozen case table (per DOF type translation/rotation, dt = T/200):
  (i) undamped free oscillation from an initial displacement: amplitude conserved by the trapezoidal rule to
      1e-10 relative over 40 periods; period equals Newmark's analytic period elongation to 1e-9 relative;
  (ii) damped forced response, zeta = 0.05, forcing ratios r = 0.5, 0.9, 1.5, started from the exact discrete
      particular solution: TD vs X_d amplitude/phase <= 1e-9 relative / 1e-7 rad; X_d vs continuous transfer
      function: the exact predicted difference is tabulated per case before running and TD vs continuous must equal
      it within 1e-6 relative;
  (iii) coupled 2-DOF damped forced case: independent complex phasor solution, scaled max-norm, rules as (ii).
- Declared symmetrisation (owner decision 2026-10-15; ADR-004 forbids blind symmetrisation, this one is declared,
  bounded and quantified): after the ADR-004 basis conversions, every source operator X entering the solver
  (A_inf, K_buoyancy_chart, each radiation-kernel sample K_l, external C and K) is split X = X_s + X_k into its
  symmetric and skew parts in normalised coordinates. The solver receives X_s only (meeting its unchanged per-input
  1e-10 symmetry requirement for mass_dry, A_inf, C, K and the kernel). The skew bound ||X_k|| / ||X|| <= 1e-2 per
  operator is required (otherwise refuse) and recorded and hashed. K_gravity_chart is symmetric by construction
  (-W c_z I + (W/2)(c e_z^T + e_z c^T)). The effect of the dropped skew parts is quantified in the frequency domain:
  dataset RAOs with and without the skew parts are both computed and their per-channel difference is reported as a
  stated modelling approximation; validation (reference b) is judged against the symmetrised model.
- Stability: structural admission (owner decision 2026-10-15) on the symmetrised operators, in normalised
  coordinates, before any judged run; any failure FAILS every stable-response case of that system:
  (i) M_eff = M_dry + A_inf_s positive definite (Cholesky succeeds; the solver already requires this);
  (ii) K_total_s positive definite (Cholesky succeeds) - this excludes neutral modes; a system needing weak
       restoring declares it fully (natural period >= 5 x the longest judged period, identical in TD and FD);
  (iii) C_s positive semi-definite: every eigenvalue >= 0 up to round-off (>= -64 eps ||C_s||); any external damping
       record must be declared positive semi-definite;
  (iv) passivity of the stored memory by construction: (a) every dataset damping sample B_s(w_i) positive
       semi-definite (eigenvalues >= -64 eps ||B||), including any supplied zero-frequency matrix B(0) (ANYhydro's
       explicit_zero_sample accepts a supplied matrix, not necessarily zero) and every generated tail endpoint;
       amendment 2026-10-20 (owner, slice F2b, per-sample basis after review round 1): a sample whose smallest
       eigenvalue lies below that round-off floor is admitted down to -eta_B x ||B_s(w_i)|| of the SAME sample
       (normalised, eta_B = 1e-8): BEM noise of near-zero damping (measured on the B3y barge U0, smallest eigenvalue /
       own norm: -7.4e-12 retained surge/heave/pitch, -1.2e-9 full 6x6); every sample so admitted is recorded in the
       import details (damping_noise_admitted); below the floor the system is refused. Assurance is qualified for
       admitted samples: they are positive semi-definite only up to a negative part of at most eta_B of their own
       norm, and the interpolation argument in (b) holds up to that bound; (b) the kernel is produced by ANYhydro with
       quadrature_method = piecewise_linear_exact (a componentwise-linear interpolant of positive semi-definite
       samples is positive semi-definite at every w), low tail linear_to_zero_extension or explicit zero, high tail
       truncate (a power-law tail is refused unless it is a positive scalar multiple of a positive semi-definite
       matrix), so the continuous kernel's spectrum is positive semi-definite; (c) uniform sampling at dt keeps the
       discrete-time transfer of the two-sided sequence positive semi-definite (Poisson summation of positive
       semi-definite aliases); (d) the memory cutoff is a Bartlett taper (1 - t/H on [0, H], zero after; its
       transform is the non-negative Fejer kernel), which preserves positive semi-definiteness - the rectangular
       time_window zeroing is not admitted for judged cases (ANYhydro deliverable: a Bartlett time window option,
       L6-4). The Hermitian part of G_d(w) on a dense grid is then evaluated as a diagnostic sanity check only.
  (v) Discrete energy: with trapezoidal Newmark (energy-conserving for M_eff, K_total_s, C_s) the step identity
      E_{n+1} - E_n = dt * vbar^T (Fbar - C_s vbar - mbar), vbar = (v_n + v_{n+1})/2, mbar the step-averaged memory
      force, gives E_n = E_0 + W_ext - W_C - W_mem with W_C >= 0. W_mem >= 0 is the cumulative memory work, a finite
      quadratic form in the velocity history built from the passive kernel; its non-negativity for zero prehistory
      and v_0 = 0 (all judged runs start from rest or from a displacement) is to be PROVEN in L6-A (written proof
      note including the solver's endpoint weights) and verified numerically on oracle kernels; until that proof is
      accepted, (v) is a stated hypothesis and stability claims cite it as such. The claim is bounded total energy
      (mechanical energy may rise temporarily when memory returns stored energy), not monotone mechanical energy.
- Settling (time domain, sets fitting windows only): one free-decay run per retained DOF from a declared small
  initial displacement (translations 0.05 m, rotations 0.005 rad; recorded with units), no forcing, same dt, kernel
  and matrices as the forced runs, length S_free (declared, <= frame ceiling). Normalised measure
  e(t) = max_k |q_k(t)| / a_k with a_k the perturbation amplitude of DOF k; complete windows of 2 T_max indexed from
  t = 0 (a trailing partial window is ignored). Settling time t_s: the start of the first window after which every
  later complete window has e <= max(1e-3 x max_t e, 1e-9) and this holds for a terminal hold of at least
  3 complete windows; otherwise the system is unresolved (not judged).
- Forced fitting for each dt / cutoff variant starts at t_fit = max(t_s, H_variant) (finite-memory steady regime);
  both fitting windows must lie complete after t_fit within the run; otherwise the case is reported, not judged.
- Poles (diagnostic only, never an acceptance input): candidates from a matrix-pencil exponential fit of the stored
  kernel and the augmented state matrix, refined by Newton on the exact stored-kernel determinant
  det[s^2 (M_dry + A_inf) + s (C + Ktilde(s)) + K] = 0; reported with damped periods and decay rates beside the
  free-decay identification. Oracles: (i) the one-DOF infinite exponential kernel M = K = 1, C = 0.2,
  K_r = exp(-0.01 t) for the augmented realisation (cubic s^3 + 0.21 s^2 + 2.002 s + 0.01: oscillatory pair decay
  ~ 9.76 s and real pole ~ 200.1 s); (ii) the same kernel sampled and cut off (dt = 0.05 s, H = 300/450 s) for the
  stored-kernel refinement, checked against the analytic finite geometric sum (real pole decay ~ 171.4 / 187.9 s);
  the H dependence is reported (no convergence claim).
- Dataset RAO (reference b) per channel k: s_k = max |RAO_k| over the band; floors f_k = 1e-4 m/m translations,
  1e-5 rad/m rotations; tolerance tol_k = max(0.02 s_k, f_k); amplitude judged when |RAO_k| >= max(0.1 s_k, f_k),
  phase only then (<= 2 deg); channels with s_k < f_k must stay below 10 f_k in TD (spurious motion). Discrete
  consistency (reference a) uses tol_k = max(1e-6 s_k, f_k * 1e-3).
- Fitting: consecutive windows of 5 whole wave periods; accept when the last two windows agree within
  max(0.002 s_k, 0.1 f_k) and the fit residual is <= max(0.01 |fitted amplitude|, 0.1 f_k). Run length
  >= max(40 T, max(t_s, H_variant) + 2 x 5 T) with t_s the free-decay settling time.
- Convergence: dt vs dt/2 and memory cutoff H vs 1.5 H (Bartlett taper lengths, common run beyond 1.5 H, identical
  coefficients and A_inf) differ <= max(0.005 s_k, 0.1 f_k).
- Free decay (diagnostic): damped periods identified from the free-decay runs (zero crossings of the dominant modal
  coordinate) are reported beside the diagnostic poles; no acceptance threshold.
- Motion envelope: every validation run inside the declared small-angle envelope, otherwise the case fails.

### 9. Feasibility gates
- L6-F1 (before L6-1, solver only): synthetic N = 6 matrices and kernel at the target sizes through
  `anysolver.marine_linear` in-process; measures time, peak memory and cancellation latency.
- L6-F2 (after L6-3 and the ANYhydro L6-4 code, before any physical L6-4 source campaign or L6-5 run): the same
  synthetic case through import -> worker -> serialization -> reload. L6-F2 gates physical execution.
- Target tuple: N = 6 (one body); T range 4-22 s; dt = 0.1 s coarse, 0.05 s fine; memory cutoff H = 300 s and
  1.5 H = 450 s; run 880 s (>= 1.5 H, = 40 T_max) -> S = 8,801 / 17,601 frames; valid when
  max(t_s, 1.5 H) <= 880 s - 10 T. If t_s is longer, the case uses the declared weak stiffness or extends to at most 1,000 s
  (fine-dt frame ceiling 20,001) or is reported, not judged.
- Budgets: peak memory <= 4 GB, wall time <= 600 s per run, cancellation latency <= 1 s. N = 12 is a separate gate
  (about 4 x the convolution work); N > 12 refused until assessed.
- Controls: `LinearExecutionLimits` gains explicit radiation-sample and wall-time limits (L6-1); worker and CLI
  enforce wall time and cancellation (L6-2, with a latency test); the Qt deadline dispatch includes linear jobs
  (L6-6).

### 10. Vessel source study (L6-4, ANYhydro)
- `source_study_case` fixes W5 retained DOFs, box geometry recipes, subdivision meaning and fluid settings; a
  versioned vessel-study recipe is needed: bound hull and lid geometry (e.g. the L01 OrcaWave panels + strip lid),
  refinement construction, reference pose, fluid settings (rho, g, depth), retained DOFs, the hydrostatic contract
  (section 3) and source-admission evidence. Owner: ANYhydro.

## Consequences
- ANYsolver unchanged unless L6-F shows a need. ANYhydro deliverables: hydrostatic contract attachment, vessel
  study recipe, six-DOF kernel generation and the normalised full-block report.
- Slice order: L6-0 -> L6-F1 -> L6-1 -> L6-2 -> L6-A -> L6-3 (+ ANYhydro L6-4 code) -> L6-F2 -> physical
  L6-4 source campaign -> L6-5 -> L6-6 -> L6-7.
- Every slice: tests, full suite vs baseline, Codex review, owner go before merge.
