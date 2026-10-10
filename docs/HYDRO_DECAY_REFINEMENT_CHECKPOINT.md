# Hydro decay refinement — implementation checkpoint

Status: **implemented and source-verified** (focused repository checks through
the exact harness; installed acceptance remains parent-owned). Development
only, unqualified; no scientific promotion. Owner: Mistral implementation
slice under `decay-refinement-implementation-brief.md` (adopted contracts
quoted there, not re-decided here).

The early checkpoint below (turn 12) is preserved verbatim as the record of
the inspection/design phase; the final status section at the end supersedes
its "implementation starting" framing.

## Early checkpoint (inspection and design, preserved)

### Concrete source findings

* `src/anymarine/linear_assessment.py` (2734 lines at inspection) already
  provides the closed-world `AssessmentCase`/`AssessmentReport`,
  `_admit_source_reference`, the raw diagnostic `assess_refinement` with
  `_same_physical_model` (matrices/q0/v0/t0/nested grid/forcing, no
  kernel-identity or source-identity pairing), the `AssessmentStore`, and
  `adjudicate_report` with the 1e-12/1e-12 semantic reproduction comparison
  (`_payload_disagreement`).
* `src/anymarine/services.py`: `prepare_linear_assessment`,
  `assess_linear_result`, `save_assessment_snapshot` (exclusive target,
  save-as copies real hydro sidecars, reproduction checked BEFORE writes),
  `adjudicate_linear_assessment`; `verify_hydro_provenance` runs the full
  derived recompile proof but had NO raw-route recompile proof.
* `src/anymarine/hydro.py`: public PURE
  `import_hydro_inputs(project, dataset_path, kernel_path, options)` returns
  `(inputs, provenance_data)` and never mutates the project; options are
  closed-world (`n_steps`, `criteria`, `t0`, `q0`, `v0`,
  `surge_link_stiffness`, `wave`, `execution_limits`); free decay is the
  absence of `wave` (provenance `wave=None`, zero forces).
* `linear.linear_input_hash` excludes only `provenance.created_utc`, so a
  read-only re-import on byte-identical copies under the original declared
  controls reproduces the exact input hash — the adopted raw proof is
  implementable exactly as specified.
* CLI already had `prepare-assessment`, `assess-linear`,
  `adjudicate-assessment`; tests already carried analytic 12-DOF ANYhydro
  fixtures (raw + derived routes) and analytic exponential fixtures.

### Proposed paired API (new module `src/anymarine/paired_refinement.py`)

```python
PairedMember            # frozen record: project, project_root, result, case
assess_paired_refinement(member_coarse, member_fine) -> PairedRefinementReport
save_paired_snapshot(member_coarse, member_fine, paired_report, out_root) -> Path
adjudicate_paired_snapshot(snapshot_root) -> dict
write_paired_artifact(report, path) / read_paired_artifact(path)
```

* New reference kind `hydro_source` (free decay only) in
  `linear_assessment.py`: closed-world descriptor binding
  `route`/`dataset_content_hash` (+ `derived_content_hash`/
  `operators_raw_sha256` iff derived); no omega/heading/wave fields.
  Hydro regular wave keeps `hydro_spectrum`; analytic keeps
  `analytic_exponential`; old artifacts/hashes unchanged.
* Raw source proof: read the stored sidecar bytes from the project root,
  sha256-check against provenance, write PRIVATE temp byte copies and call
  the public pure `anymarine.hydro.import_hydro_inputs` with the original
  declared controls (n_steps/criteria/t0/surge-link from provenance; q0/v0/
  execution_limits preserved from the stored inputs as caller mechanical
  controls; no wave key), then compare the full rebuilt
  `linear_input_hash` with the case hash. Derived route reuses the existing
  public recompile proof. Both routes re-prove the kernel sidecar against
  the stored effective kernel samples.
* Hydro-source free decay adds the registered near-zero initial energy gate:
  `E0 <= nearzero_energy_floor_j` is an explicit NOTMEASURED/NoGo check
  (check name `initial_energy_measured`), never a zero-state PASS.
  Legacy analytic free-decay reports are unchanged and reproducible.
* Pairing guards (in the paired module, on top of the raw
  `assess_refinement` diagnostic): same ordered bindings/DOFs/equilibrium
  hash, same source identities (dataset/derived/operator hashes, route),
  kernel lag values at shared times identical within rtol/atol 1e-12 in the
  kernel's own units, same kernel tail/window/source settings apart from
  sampling dt/n_time, same forcing at shared times, registered integer
  nested dt ratio >= 2 with explicit partner hashes.
* Combined verdict: PASS only if BOTH member full reports pass AND the
  refinement report passes; any valid NoGo member yields a preserved
  composite NoGo (never a refusal, never "pair pass because close").
* Paired record: new separate immutable artifact format
  `anymarine-linear-refinement-v1` binding both case/input/result/report
  identities, the actual member reports, refinement metrics, combined
  verdict, units/criteria. Snapshot layout:
  `out/coarse/project`, `out/fine/project` (public save-as route, only the
  selected result), `out/{coarse,fine}/cases|reports` (existing
  AssessmentStore), `out/pair.json` with relative refs; existing target
  refused BEFORE any write; private scoped service instances so the active
  `service.root` is not changed.
* Reproduction uses ONLY the snapshot's own projects/sidecars/artifacts;
  both members' full physical/source proofs and the refinement are re-run
  and the semantic payload compared with `_payload_disagreement` (1e-12
  persistence roundoff only).
* Services: thin `ApplicationService` wrappers
  (`assess_paired_refinement`, `save_paired_refinement_snapshot`,
  `adjudicate_paired_refinement_snapshot`) taking explicit per-member
  roots (hydro roots REQUIRED, analytic roots optional).
* CLI: `assess-refinement` (explicit coarse/fine project/case/result
  choices -> exclusive snapshot; exit 0 only on combined pass) and
  `adjudicate-refinement --snapshot` (exit 0 only on a reproduced passing
  pair). Existing commands and single-case APIs unchanged.

### Owned edit plan (as executed)

1. `src/anymarine/linear_assessment.py`: added `hydro_source` reference
   kind, free-decay admission, raw/derived source proof loader
   (`load_hydro_source_proof`), near-zero E0 gate, kernel-sidecar proof
   helper shared with `load_hydro_spectrum`.
2. New `src/anymarine/paired_refinement.py` (module above).
3. `src/anymarine/services.py`, `src/anymarine/cli.py`,
   `src/anymarine/__init__.py`: minimal connected exports.
4. New `tests/test_hydro_decay_refinement.py` (raw+derived hydro free-decay
   flows, paired coarse/fine, snapshot/reopen after originals removed,
   composite NoGo, zero-energy NOTMEASURED, tamper/refusal matrix, CLI).
   `tests/test_linear_assessment.py`: only compatible admission updates
   (the two former "hydro free decay refused" tests became
   hydro_source-admission tests; no assertion/criterion weakened).
5. New `examples/hydro_decay_refinement.py`; docs updates
   (`LINEAR_ASSESSMENT.md`, this file) and README.

## Final status (implemented, source-verified)

### What was delivered

* `hydro_source` free-decay reference kind with the full source proof:
  derived route reuses the public recompile proof; raw route re-imports via
  the public PURE `anymarine.hydro.import_hydro_inputs` on private
  byte-identical sidecar copies under the original declared controls and
  compares the full rebuilt `linear_input_hash`. Both routes re-prove the
  stored kernel sidecar against the stored effective kernel samples.
  `initial_energy_measured` NOTMEASURED/NoGo gate for near-zero E0.
* `src/anymarine/paired_refinement.py`: paired coarse/fine assessment with
  kernel-identity and source-identity pairing guards (kernel values at
  shared lags within rtol/atol 1e-12 in the kernel's own units kg/s^2),
  combined verdict (both member reports AND refinement must pass; valid
  NoGo members yield a preserved composite NoGo), immutable
  `anymarine-linear-refinement-v1` artifact, exclusive paired snapshot via
  the public save-as/AssessmentStore routes (only the selected member
  results; real sidecars copied), and snapshot-only reproduction that
  re-runs both members' complete physical/source proofs and compares the
  semantic payload (fresh pair/report identity excluded; 1e-12 persistence
  roundoff only).
* Services wrappers and CLI `assess-refinement` / `adjudicate-refinement`
  (explicit choices only; nonzero exit on NoGo/refusal).
* Example `examples/hydro_decay_refinement.py`: cases prepared BEFORE
  solving, source proofs, paired snapshot, reproduction after the originals
  are removed.

### Verification (source checks, exact harness)

All checks through `C:/ANYworkspaceWork/anymarine-evidence/run_check.py`
with the pinned runtime
`C:/ANYworkspaceWork/anymarine-evidence/runtime/Scripts/python.exe`,
`PYTHONPATH=C:/Github/ANYmarine/src`, single thread, evidence dirs
`C:/ANYworkspaceWork/anymarine-evidence/decay-refinement-worker-01..27`.

* `tests/test_hydro_decay_refinement.py`: 19 tests, all pass (run in split
  invocations to respect the 15 s per-check limit).
* `tests/test_linear_assessment.py`: 37 tests, all pass (23 non-hydro +
  14 hydro; compatible admission updates only).
* `examples/hydro_decay_refinement.py`: runs end-to-end in ~4 s; both
  members pass, paired verdict pass, kernel identity residual 0.0,
  snapshot-only reproduction agrees.
* CLI smoke: `adjudicate-refinement --snapshot` on the example snapshot
  (see the final handoff for the recorded evidence).

Failures: none outstanding. Two implementation defects were found and
fixed during checks (paired artifact now carries and validates its member
refs; reproduction comparison strips fresh pair/report identity before the
semantic payload comparison).

### Parent-review repair (same owner, compatible persistence fix)

Two parent static findings on the persistence contract were repaired
without touching any scientific criterion:

* `PairedRefinementReport` is now closed-world at construction (unknown
  or missing record fields are refused) and every dict/list property
  (`members`, `refinement`, `pairing`, `failed`, `scope`) returns a
  defensive deep copy, so attempted mutation of an exposed payload can
  never change the record content or its hash.
* `_snapshot_member` no longer parses and discards the captured member
  report: the captured report's case/input/result identities, verdict,
  actual hash and full semantic payload are bound to the corresponding
  stored paired member section BEFORE any new assessment. A re-hashed
  captured report with a changed check metric/flag is refused as a
  paired inventory violation even when the embedded pair payload is
  unchanged; the binding is semantic, never hash-only. Both members and
  the refinement are still fully recomputed from the snapshot's own
  captured data.
* New regressions: `test_pair_record_closed_world_and_stable_under_mutation`
  and `test_snapshot_refuses_forged_captured_member_report` (public
  `write_report_artifact` forgery, including a variant where the stored
  member hash is re-forged in `pair.json` so only the semantic binding
  can refuse). `test_pair_artifact_tamper_refused` received a compatible
  pattern update: its forged embedded payload is now refused even
  earlier, by the same inventory binding.

### Remaining gaps (honest)

* Installed acceptance of this milestone is parent-owned and not claimed
  here; source checks are development evidence only.
* GUI integration of the assessment/paired workflow is deferred; no new
  GUI widgets exist.
* Physical source-study approval (actual BEM/Capytaine source study)
  remains unanswered and was not consumed; all fixtures are honestly
  labelled analytic development constructions, never BEM output.
* Scientific qualification (W5) remains out of scope; everything here is
  DEVELOPMENT_ONLY/unqualified.

## Parent installed verification and final refusal repair

Installed candidatev1 parent03 timed out after9progress dots (not a completed pass); parent04 found unknown inner member-ref accepted (1failed3passed). Same-owner focused continuation85495 ended at20turn limit without implementing the repair and made two unmetered solver-import probes. Their shared proxy effect lasted2.494s, not precise process-wall accounting; logs preserved, no inferred remaining worker authority. Parent completed the narrow adjudication integration of the existing _validate_member_refs and added the unknown inner-field case to the existing snapshot-refusal regression. Physics and numerical criteria unchanged.

Corrected wheel evidence/wheels/decay-refinement-final-v2 installed. Independent installed parent07 passed9 cases, parent08 passed4 derived cases, parent09 passed4 corrupt-inventory cases (17total). Parent10 actual installed CLI adjudication passed and matched25 package Python/resource SHA256 identities, all bundled startup resources unchanged and16hydro source identities unchanged. Initial timeout/failed candidate retained. Precise parent01..10 wall53.48281609998958/60s, remaining6.517183900010423s, separate/frozen. Metered worker01..27 wall122.2141619999893s plus unmetered import-probe uncertainty; no total/unused-worker claim. No active owned processes, no physical BEM run or qualification/release. Manifest evidence/decay-refinement-final-manifest.json; living TASK.md contains historical constraints and failures.
