# ADR 014: native isolated jobs and matrix replay

Status: adopted integration contract; implementation and verification pending.

`mechanical_development` is a separate, explicitly unqualified dry/link result
kind. It requires authoritative BODY-to-WORLD rotation matrices and an empty
Euler orientation mapping. Legacy kinds omit the new field and retain their
existing representation. Each native record retains its complete producing input
snapshot, including body geometry, explicit mechanical inputs and resource limits.
Schema5 stores native input and/or native result intent; older schemas refuse
both. Removing current native inputs must not erase stored producing snapshots.

The native worker consumes a reconstructed, hash-checked immutable input snapshot
through the shared public solver compiler. Fixed-step controls remain project
owned. A separate explicit execution limit permits 2..20001 frames, default1001,
and 0<wall_seconds<=600, default60. These are development resource controls, not
qualification criteria or a grant to execute checks beyond current authority.
One child process, one numerical thread, bounded progress, no child spawning,
Qt-free execution, and cancellation remain required. Deadline/failure/cancellation
evidence is non-success and retained separately. Only completed, validated output
is sealed by the producer and admitted by the supervisor.

The output envelope carries the native solver's raw authoritative accepted
arrays, status, histories, logs, coordinate/sign conventions and a ResultRecord.
Admission checks the producer SHA-256, exact snapshot hash, kind/API, body order,
dt, requested/completed frame inventory, initial state, proper rotations, all
finite velocity/acceleration/wrench arrays, complete contribution inventory,
and the record's agreement with those arrays. This is transport validation,
not an independent numerical solve or scientific qualification.

Stored arrays alone drive plots and replay, using the producing snapshot's body
geometry rather than newly edited geometry. Native transforms embed the stored
rotation matrix directly. Editing the project cannot change a submitted snapshot;
completed old results retain their producing hash and display as stale.

The application may import declared native inputs through shared services and
queue them through QProcess. It must not run the synchronous solver in a widget
callback. CLI uses the same snapshot, worker and output decoder. Qualification,
large-excursion potential flow, Morison/contact/lines and full headless acceptance
remain separate obligations. No physical study or numerical allowance renewed.

## Native fluid development integration,2026-10-09
The native immutable inputs now optionally carry strict named potential-flow development data; dry exports omit the field to preserve identity. Shared compiler/native worker/transport and Qt/CLI routes support the labelled coupled fixture, complete accepted matrix arrays and producing snapshots. Worker validates final accepted memory before sealing; neutral transport validates clock/shape/component inventory. Failure without a partial record preserves failed evidence. Source/installed shared-service, persistence, actualCLI and Qt offscreenQProcess/replay/plot checks recorded in native-fluid-app-verified-manifest.json and TASK.md. Real source semantics/equilibrium/envelope and fullscientificW4/W8-W10 remain pending; no new physical/desktop qualification claim.
