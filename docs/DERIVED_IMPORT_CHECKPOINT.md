# DERIVED_IMPORT_CHECKPOINT (worker-owned, final state)

Continuation completed: parent counterexamples fixed, regressions green,
budget closed. See docs/DERIVED_IMPORT.md for the full contract.

## Fixes landed this continuation (candidate source, not yet re-installed
end-to-end by the parent)

1. worker.py `_run_linear`: derived intent is enforced from BOTH claims -
   the inputs' own provenance details route (`derived_operators`) and the
   snapshot's structured hydro provenance derived bundle. Either claim
   requires the complete intent; stripping `hydro_provenance`, stripping
   just `derived_bundle`, or an inconsistent one-sided claim is refused
   (exit 2, no output). No automatic fallback; non-hydro legacy snapshots
   are untouched by the gate.
2. worker.py `_verify_derived_snapshot_sources`: the structured record is
   validated as a frozen `HydroBridgeProvenance` (from_dict) and the
   actual embedded dataset/operators/kernel payload bytes are hashed and
   compared against the record's declared `*_raw_sha256` identities BEFORE
   any decode/recompile; a declared identity of 0*64 over unchanged bytes
   is refused. Recompilation now consumes the validated frozen record.
3. hydro.py `_load_derived_triple`: removed the `ignore_cleanup_errors`
   workaround and its misleading scratch-leak comment; strict
   TemporaryDirectory cleanup restored (parent installs the fixed ANYhydro
   NPZ lifecycle wheel before foreign/forged refusal verification).
4. tests/test_derived_import.py: three new worker regressions on the
   actual `anymarine.worker.run` production path -
   `test_worker_refuses_stripped_derived_intent` (3 params),
   `test_worker_refuses_inconsistent_raw_byte_identity` (3 params), and
   `test_worker_admits_unmodified_derived_snapshot` (positive control,
   full job completes). Placed BEFORE the existing rehashed-forgery test,
   which deletes the module-scoped fixture's original artifact files.

## Checks (exact harness, evidence under C:/ANYworkspaceWork/anymarine-evidence)

- derived-import-worker-26: first focused batch attempt, 7 failed / 2
  passed in 5.155s - failures were test-ordering only (the pre-existing
  rehashed-forgery test deletes the shared fixture originals; my tests
  ran after it). No source failure; evidence preserved.
- derived-import-worker-27: focused batch
  `-k "stripped or inconsistent_raw or rehashed or admits_unmodified"`:
  9 passed / 23 deselected in 6.088s (wall 6.088s).
- derived-import-worker-28: legacy control
  `tests/test_worker_linear.py::test_worker_success_validates_output`
  (non-hydro snapshot through the new gate): passed in 3.815s.

## Wall ledger (160s cumulative process allowance)

133.0754707 (prior worker-01..25) + 5.1554052 + 6.0881795 + 3.8154218 =
148.1344772s used; 11.8655228s remaining, held as verification/handoff
reserve. Per-process timeouts 15/10/10s, all within remaining at start.

## Gaps / open items for the parent

- Installed closure check PENDING: the fixed ANYhydro wheel must be
  installed before foreign/forged refusal verification (the strict temp
  cleanup now surfaces any loader handle leak as a cleanup error); parent
  owns that install and the end-to-end/new Qt desktop verification.
- Pre-existing latent hazard (not introduced here, not fixed to keep the
  diff minimal): in a full-file in-order run of tests/test_derived_import.py,
  `test_detached_worker_refuses_rehashed_operator_forgery` deletes the
  module-scoped `derived_artifacts` originals, so later tests that read
  those originals (e.g. the roles/persistence tests) would fail. All prior
  evidence runs used -k selections; my new tests are placed before it.
- Parent test file (evidence copy) not re-run here: it lives outside the
  repo; the worker-side equivalents of its new cases are green via
  worker-27. Parent should re-run its own 14-case file after installing
  the fixed wheel.
- Snapshot provenance linear_input_hash binding (parent's consideration)
  is enforced transitively: recompiled inputs must reproduce the declared
  hash, which must match the snapshot's reconstructed inputs.


## Parent installed integration closure, 2026-10-08

Overrides the earlier pending/latent-hazard status above: final candidate repairs
paired-marker partial intent and isolates destructive fixture checks with private
copies. Last worker wrong-runtime checks are unmetered and excluded from
acceptance; exact author final timing remains unknown/frozen. Parent independently
rebuilt/installed BOTH canonical wheels, sourceSHA matched all package files,
17installed cases passed (including archive closure and all refusal variants),
real Windows Qt/OpenGL3.3 action/child/save/reopen/replay/staleness passed.
Full evidence, resource accounting, preserved failures and open scientific gates:
C:/ANYworkspaceWork/anymarine-evidence/derived-import-final-manifest.json.
TASK.md remains authoritative. This is verified development consumer behavior;
actual Capytaine qualification and full roadmap remain incomplete.
