# Independent Mistral review — w5-e1-operator-route-20261010 source/evidence reconciliation (2026-10-10)

Independent document-only review by Mistral, separate from the implementation
author. Coordination is direct Mistral; no Astra agent activity and no active
Astra reviewer/administrator claim. This completes the follow-up that timed
out at 108.4488449000055 s without a final report (preserved INCOMPLETE; its
frozen 300.4090557000018 s pool is not reused). Evidence basis: the supplied
packet
C:/ANYworkspaceWork/anymarine-evidence/w5-e1-operator-route-20261010/final-review-packet.md
(SHA256 745b475b8466403c4236c24b96fae38d33ff906f5d2519f14620198baf4e3fa9,
verified), containing the current artifact profile and checker, the final 180
lines of each new regression test file, the actual guard-tests-1 receipt and
stdout, the current Linux installed identity (stage-03), the stale handoff
and the original independent report. No source/test/numerical work was
performed; no test or checker was re-executed.

## Verdict

The install/source identity claim is supported: stage-03 wheel
88f1324a7ae978a8b037d4fc92535530fba0b5b7a65be85b32ce3b356dca857d installed
into /var/tmp/anyhydro-e1-operator-route-20261010/venv, 37/37 anyhydro
modules byte-identical to the current tested repository source, RECORD
checks 44 of 82 and 72 of 135 entries byte-verified with zero mismatches,
both native extensions runtime-hashed matching the sidecar pins (f64
3375a0a8818c00032894abcb3d20e2338a28ac29721da07a23f1d0c8cd96c532, f32
d2f4425c4441df8ef7bb38ccf587e59d287ef1b7ea786e22834029c15b3a26cd), all
thread pools at one thread, imports only. This is install/source identity
only: no native constructor, kernel, tabulation, evaluate or system call,
and no scientific or numerical qualification of any kind. W5 remains NO_GO.

## Findings — actual dispositions against the current corrected source

1. OPEN — first actual native guard run. No native probe admission exists;
   the checker has never run end-to-end. The 106 passing tests (guard-tests-1,
   exit 0, 106 passed in 38.43 s, owned-source import confirmed, exact
   source/checker hashes in
   C:/ANYworkspaceWork/anymarine-evidence/w5-e1-operator-route-20261010/attempts/guard-tests-1/result.json)
   are mock-based; they validate the guard contract at source level only and
   execute no native code. The first admitted run must still be treated as
   validation of the guard itself.
2. ADDRESSED — f32 execution-time hash. Current
   src/anyhydro/artifact_bound_panel_profile.py (SHA256
   9b5f925580b38031ea4355570f25b988911f36d5eceb98e97f18d2b290c5a011) imports
   and runtime-hashes BOTH native extensions in green_function before the
   constructor and re-hashes them after; the checker (SHA256
   737e8c222808b1676f1be3cfc83215d3c202d824b0b9929384a16b3641c254b1) imports
   and hashes the f32 extension at execution entry, records
   f32_extension_sha256 in the summary and refuses mismatch
   (test_f32_extension_mismatch_refused). The stale handoff line citing
   lines 171-176 ("verifies only f64") described pre-correction source and is
   superseded.
3. ADDRESSED — execution module identity. green_function re-verifies the
   three implementation-module hashes at entry ("changed after loading"),
   closing the TOCTOU window; bind_case re-checks as well. The stale handoff
   item answered installed-vs-repo identity (true, 37/37) but not this
   finding; both are now stated.
4. ADDRESSED — cache-None effective property. green_function reads
   tabulation_cache_dir from exportable_settings and, because the exact E1
   settings map excludes that key, from the pinned instance attribute; any
   non-None effective cache is refused and no setting is invented. Covered by
   test_green_function_refuses_effective_tabulation_cache,
   test_green_function_refuses_positive_cache_instance_attribute and
   test_green_function_accepts_backend_without_exposed_cache_property.
5. ADDRESSED — two-body assertion. The checker asserts len(bodies) == 2
   (EXPECTED_BODIES) with refusal "not the exact two-body coupled case", and
   the summary records body_count; test_body_count_not_two_refused covers it.
6. ADDRESSED — RECORD wording. Handoff and receipt state "44 of 82" and "72
   of 135" entries byte-verified, zero mismatches.

## Limits of this review

Document-only: I did not re-execute tests or the checker (prohibited; none
exists for the probe), did not re-read historical TASK sections or the full
plan, and audited only the final 180 lines of each new regression test file
as supplied in the packet. The stage-03 receipt records only the
green-function Python subset (delhommeau.py ce24385d...,
abstract_green_function.py 37222fbf..., hams.py 97905431..., __init__.py
e3b0c442...); the evaluator's three-file backend source map
(green_functions/delhommeau.py, tools/prony_decomposition.py,
meshes/geometry.py) is binding/criteria-pinned and enforced at execution
entry by the current source, but is not runtime-recorded in that receipt —
no missing receipt keys were invented. Mock-based tests prove source-level
contracts, not native behavior; the supported claim is install/source
identity, not native qualification.

## Plan/goal state and next slice

Goal remains full implementation at engineering quality, status ACTIVE; W5
NO_GO; W6-W10 and thin-sheet open; the original 11 gates preserved; MPL-2.0
own code and third-party artifacts unchanged; Windows/Linux ANY3dView
GPU-optional backend/UI qualification open. Efficient next slice: the first
admitted four-receiver probe against the original 864-panel two-body coupled
case under prospective exact identity/limits registration; no automatic full
matrix, energy run or savings claim. Plan current active section inserted;
prior E1 section labelled historical; TASK.md received an ASCII checkpoint
prefix with the original bytes preserved byte-identical (proof:
C:/Github/ANYmarine/.development-evidence/w5-e1-operator-route-review-20261010/proof.json).
The stale ANYhydro handoff dispositions are corrected in place with the
pre-correction handoff preserved byte-identical as
worker-handoff-stage03-before-review.md.
