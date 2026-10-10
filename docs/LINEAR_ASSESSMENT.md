# Linear assessment (development, unqualified)

This document describes the registered linear assessment slice
(`src/anymarine/linear_assessment.py`) and its public service/CLI surface.
Everything here is **development output, unqualified**: a registered
diagnostic of a stored `linear_development` result. It is never a qualified
marine analysis, never a production promotion, and never a W5 claim.
Boundary-element preprocessing and solving live in the separate ANYhydro /
ANYsolver packages; this slice consumes only their public records, loaders
and verifiers.

Three evidence levels are distinguished throughout:

| Level | Meaning |
|---|---|
| Source checks | Focused tests executed from this repository source tree (`tests/test_linear_assessment.py`). |
| Installed acceptance | Independent parent build/install/adjudication of a candidate package. Not established by this repository's tests. |
| Qualification | Formal W5 qualification campaigns. Explicitly out of scope; no authority exists in this slice. |

## Workflow (the acceptance boundary of the slice)

```
stored project -> registered case (BEFORE the solve) -> actual result ->
assessment report -> self-contained snapshot -> reopen -> adjudication
```

An immutable, closed-world, hash-bound `AssessmentCase` is prepared and
registered before any rehearsal solve. It binds the exact effective
`linear_input_hash`, the mode (`regular_wave` / `free_decay`), the labelled
reference, the absolute time windows, the fit/drift rules and every
prospective criterion (stricter than the governing contract minima, never
looser). A result is later assessed under the same case; physics failures
produce a **NoGo** report with every raw metric preserved, while eligibility
failures are typed refusals that produce no report.

## Public service and CLI usage

Shared `ApplicationService` methods (Qt-free, same route for CLI and GUI):

- `prepare_linear_assessment(project, spec)` — validate and freeze a case
  against the stored inputs. The public binding verifier re-checks that the
  stored inputs still describe the actual project bodies/mesh/equilibrium,
  and any existing hydro provenance is re-verified against the authoritative
  sidecars before a source-bound registration is admitted.
- `assess_linear_result(project, result, case)` — assess one stored result.
  The shared hydro provenance gate runs before any sidecar load.
- `save_assessment_snapshot(project, result, case, report, out_root)` —
  write a self-contained snapshot (public save-as route with the real hydro
  sidecars copied and checked, plus the hashed case/report artifacts). An
  existing target is refused before anything is written; the report must
  REPRODUCE from the actual arrays before the snapshot is created.
- `adjudicate_linear_assessment(snapshot_root, case_path, report_path)` —
  reload the snapshot project and reproduce the stored assessment.

CLI (headless, no Qt):

```
python -m anymarine.cli prepare-assessment --project <dir> --spec spec.json --out case.json
python -m anymarine.cli assess-linear --project <dir> --case case.json --out report.json
python -m anymarine.cli adjudicate-assessment --snapshot <dir> --case case.json --report report.json
```

Module-level API: `prepare_case`, `assess_result`, `assess_refinement`,
`adjudicate_report`, `load_hydro_spectrum`, `hydro_reciprocal_sensitivity`,
`AssessmentStore`, and the artifact readers/writers. `assess_result` /
`adjudicate_report` accept an optional `project=` keyword: a
`hydro_spectrum` reference REQUIRES the actual `ProjectModel` (see below);
an analytic reference stays Qt-free and needs no project.

## References and the actual source proof

- `analytic_exponential` — an independent closed-form rehearsal reference
  `R exp(-alpha t)` with `B(omega) = alpha R / (alpha^2 + omega^2)` and
  `A(omega) = Ainf - R / (alpha^2 + omega^2)`. The stored TD kernel is
  validated against the analytic form first. Honest rehearsal only.
- `hydro_spectrum` — the FD reference is the ACTUAL stored neutral dataset
  sample `A(omega)`, `B(omega)`, `F(omega, heading)` (raw route, exact
  work-conjugate selector projection) or the public derived-operator
  artifact's processed arrays plus the full raw-source proof (derived
  route). The stored TD kernel is never Fourier-transformed into the
  reference; `kernel_spectrum_self_consistency` is an explicitly labelled
  self-consistency diagnostic only and can never yield admission or a PASS.
- `hydro_source` — the proper reference for a hydro-sourced FREE DECAY
  (mode `free_decay` only). A closed-world descriptor binds the route
  (`raw_dataset`/`derived_operators`), the dataset content hash and, for the
  derived route, the derived content/operator hashes; there is no
  omega/heading/wave phasor because a free decay has none. The source proof
  is the same actual-source proof as `hydro_spectrum`: the derived route
  reuses the public recompilation proof, and the raw route re-imports the
  stored sidecar bytes (private byte-identical copies) through the public
  PURE `anymarine.hydro.import_hydro_inputs` under the original declared
  controls and compares the full rebuilt `linear_input_hash`. The stored
  external forcing must be zero and `provenance.wave` must be `None`. A
  near-zero initial energy (`E0 <= nearzero_energy_floor_j`) is an explicit
  `initial_energy_measured` NOTMEASURED/NoGo check, never a trivial
  zero-state decay PASS.

A hydro-spectrum assessment requires the actual project context; no caller
flag, hash string or documentation substitute is accepted:

1. `assess_result`/`adjudicate_report` must receive the actual
   `ProjectModel` and the project root holding the authoritative sidecars.
   A generic hydro case without the actual project is refused. The project
   is supplied by the caller (the live or reloaded project); the core never
   loads a stale disk project to substitute for the actual current unsaved
   project.
2. The public `verify_linear_binding(project, inputs)` re-checks that the
   assessed inputs describe the actual project bodies/mesh/equilibrium, and
   the assessed inputs must BE the project's stored linear inputs (input
   hash equality). Substituted effective matrices with consistently
   re-hashed case/provenance bookkeeping are refused.
3. Any supplied provenance record must be the project's own
   `HydroBridgeProvenance` record.
4. The public `ApplicationService(root).verify_hydro_provenance(project)`
   re-runs the full source proof: metadata/input-owned evidence consistency
   and, for the derived route, the authoritative recompilation from the
   stored dataset/operators/kernel sidecars, which must reproduce the actual
   input hash exactly.
5. `load_hydro_spectrum` then re-reads the actual sidecar bytes from the
   project root, re-checks the public raw sha256 identities, re-loads
   through the public ANYhydro loaders (which re-run the source proof on
   the actual bytes) and matches the re-proved kernel against the stored
   effective input matrices. It is a spectral loading helper, never a
   standalone eligibility certificate.

## Units and phase conventions

- Matched SI frequency domain:
  `Z(omega) = K - omega^2 (M_dry + A(omega)) - i omega (B(omega) + C)`,
  solved as one full complex 4x4 system (off-diagonal coupling kept).
- The force phasor includes the supplied wave amplitude (reference 1 m)
  and `exp(+i phase)`; `q(t) = Re(qhat exp(-i omega t))` evaluated at the
  ABSOLUTE input times (never silently `t - t0`).
- The TD solve uses `Ainf`; the FD reference uses `A(omega)`. No invented
  drag, inertia or buoyancy.
- RAO fits use least-squares cos/sin with the NEGATIVE phasor convention
  `qhat = a + i b` over two predeclared adjacent equal-duration windows
  (each >= 2 cycles, below Nyquist, explicit condition bound <= 1e12).
  Fitting intercepts are permitted explicitly; the only admitted drift is
  the optional `common_surge_linear` along `(1, 0, 1, 0)/sqrt2` and only
  when `K u = 0` for the actual stiffness.
- Near-zero reference amplitude (<= 1e-8 m): phase is explicitly NOT
  measured (no fabricated phase pass); the absolute amplitude error is
  checked instead.
- Reciprocal-average sensitivity compares `Z_raw` vs `Z_processed` as
  work-conjugate `S^T Z S` max entries (J, floor/abs 1e-9, relative 0.01)
  and the full coupled complex response as the DIMENSIONLESS `S^-1 qhat`
  (floor/abs 1e-8, relative 0.02), with the retained SI solutions recorded
  separately.

## NoGo, refusals and artifact handling

- Physics failures (steadiness, RAO, balance, energy, refinement,
  sensitivity limits) are **NoGo** verdicts: the report preserves every
  raw authoritative array, metric and residual. NoGo reports are snapshotted
  and preserved like any other result.
- Eligibility failures (wrong kind, stale/foreign result, tampered case or
  report, missing sidecars, missing/forged project context, unknown fields,
  traversal) are typed refusals (`AssessmentRefusalError` /
  `AssessmentCaseError`, `ServiceError` at the service layer) and produce
  no report.
- Case/report artifacts are closed-world JSON with a stored content hash;
  a mutated artifact is refused on reload.
- Reproduction (`adjudicate_report` / `adjudicate_linear_assessment`)
  re-assesses from the actual stored arrays and compares the FULL semantic
  payload — verdict, every check with its actual metrics, and every
  recorded array — not just the pass flags. Only the fresh report identity
  (`report_id`/`created_utc`; the hash is derived) is excluded; numeric
  scalars use one tight documented roundoff tolerance (relative 1e-12 +
  absolute 1e-12 per scalar) so backend roundoff cannot fail a persistence
  comparison. This is NOT a physics tolerance. A re-hashed report with
  forged numerical metrics and unchanged flags is refused.
- `save_assessment_snapshot` refuses an existing target before writing
  (existing outputs, including NoGo/failed reports, are preserved) and
  requires the report to reproduce before creating anything.

## Example

`examples/linear_assessment.py` (role `analytic_rehearsal_only`) runs the
whole boundary with the public services: regular wave coarse/fine with a dt
refinement comparison, free decay, snapshot, reopen and adjudication. The
radiation kernel is an explicit exponential analytic construction — not BEM
output and no qualification claim.

`examples/hydro_decay_refinement.py` (same honest analytic development
label) demonstrates the hydro-source free-decay and paired slice: two
coarse/fine cases prepared BEFORE solving through the public services, the
actual source proofs, the paired assessment and exclusive snapshot, and
snapshot-only reproduction after the original projects are removed.

## Paired coarse/fine refinement (development, unqualified)

`src/anymarine/paired_refinement.py` adds a self-contained paired
coarse/fine assessment on top of the single-member slice. Both members must
describe the SAME physical model and source: identical ordered body
bindings/DOFs, equilibrium, mass/mesh/lid/reference geometry, `Mdry`/
`Ainf`/`C`/`K`/`q0`/`v0`, source dataset/operator identities, forcing,
duration/windows and (for hydro) tail/window/source settings apart from the
sampling `dt`/`n_time`; a registered integer nested dt ratio >= 2 with
explicit partner hashes; and the radiation kernel must be the SAME physical
kernel — lag values at shared times identical within rtol/atol 1e-12 in the
kernel's own units (kg/s^2), not a processing allowance. Comparing
different kernels/damping is refused, never called dt refinement.

Each member independently passes the current binding/source/completeness
eligibility and is fully assessed. The combined verdict is PASS only if
BOTH member full reports AND the refinement report pass; a valid NoGo
member yields a preserved composite NoGo, never a "pair pass because
close". The raw `assess_refinement` remains a diagnostic and is never the
sole physical admission.

The paired record is a separate immutable closed-world artifact format
`anymarine-linear-refinement-v1` binding both case/input/result/report
identities, the actual member reports, the refinement metrics, the combined
verdict and the units/criteria. `save_paired_snapshot` writes an EXCLUSIVE
snapshot using the existing public save-as/`AssessmentStore` routes (only
the selected member results; the real raw/operators/kernel sidecars are
copied for each hydro project) with relative member refs validated against
escape/missing/unknown/tampered records; an existing target is refused
BEFORE any write. Reproduction (`adjudicate_paired_snapshot` /
`adjudicate-refinement --snapshot`) uses ONLY the snapshot's own
projects/results/cases/sidecars — the original projects and external
source files may be absent — re-runs BOTH members' complete physical and
source proofs plus the refinement, and compares the actual semantic
metrics/arrays/identities against the stored payload (fresh pair/report
identity excluded; the same 1e-12 persistence roundoff, never a physics
tolerance).

Shared `ApplicationService` wrappers: `assess_paired_refinement`,
`save_paired_refinement_snapshot`, `adjudicate_paired_refinement_snapshot`
(explicit per-member project roots; hydro roots REQUIRED). CLI:

```
python -m anymarine.cli assess-refinement --coarse-project <dir> --coarse-case case.json --coarse-result-id <id> --fine-project <dir> --fine-case case.json --fine-result-id <id> --snapshot <snapshot-dir>
python -m anymarine.cli adjudicate-refinement --snapshot <dir>
```

Both commands take explicit choices only (no automatic campaigns, grids or
current-result selection); the exit code is nonzero for NoGo/refusal and
success only for a reproduced passing pair.

## Pending (honest gaps)

- GUI integration of the assessment workflow is deferred to a later shared
  service slice; no new GUI widgets exist here.
- Installed acceptance and qualification are parent-owned and not claimed
  by this repository's source checks.

## Installed development evidence

The parent verified the final wheel with16 independent installed cases and37
focused regressions, including actual-source refusal and metric reproduction.
The source-reference guard prevents hydro inputs from bypassing source ownership
by selecting an analytic descriptor, both at registration and assessment.
Manifest: `C:/ANYworkspaceWork/anymarine-evidence/linear-assessment-final-manifest.json`.
These are Windows development checks; scientific qualification remains pending.
A refinement report alone never admits its individual members: each member
must also pass its complete assessment and source proof. The paired
coarse/fine slice (`hydro_source` free decay, `anymarine-linear-refinement-v1`
snapshot/reproduction, `assess-refinement`/`adjudicate-refinement`) is
implemented and source-verified in this repository (17 new focused tests plus
the 37 existing, all passing; see
`docs/HYDRO_DECAY_REFINEMENT_CHECKPOINT.md`); its installed acceptance is
parent-owned and not yet claimed.
