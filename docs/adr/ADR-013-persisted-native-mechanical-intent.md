# ADR 013: persisted native mechanical intent

Status: adopted input contract; implementation verification pending.

The next native Qt integration requires immutable project-owned execution
intent. `NativeMechanicalInputs` stores a fixed-step spec, exact body identity
mapping of initial positions, BODY-to-WORLD rotation matrices and WORLD linear
and angular velocities, explicit links, optional gravity and named world
wrenches. Acceleration is not an input: ANYsolver computes consistent initial
acceleration. No chart state, provider code or serialized executable is admitted.

The application validates representation, shape, finiteness, identities and
proper rotations. ANYsolver remains the sole owner of physical mass/inertia,
link law, stiffness/damping admissibility and assembly. Link PSD tolerance is
fixed at 1e-12 by the public compiler, not exposed as a weakening control.
Rest geometry must be declared; it is never inferred from a trial state.

Optional native intent is appended to ProjectModel. Absence is omitted from
serialization and hashes, preserving legacy example identities. Schema 5 uses
a content-addressed, SHA-256 checked native-input sidecar. Older schemas refuse
native intent; schema 5 requires it. Native intent cannot coexist with configured
linear/hydrodynamic inputs or body hydro sidecars while only dry mechanics is
admitted. Unsupported semantics must be refused, not silently dropped.

Shared preparation compiles current project-owned mass/CG/inertia through the
public solver API, then constructs named native links/wrenches and initial
states. There is no duplicated mass model in the saved native configuration.
Immutable run snapshots bind all effective project inputs and native controls.
Compilation does not execute numerical jobs or establish scientific acceptance.

This slice does not admit native job output or enable GUI execution yet. The
next connected slice must add the isolated worker, producer seal, successful
result admission, matrix replay and Qt controls before claiming a native GUI
workflow. Prior scientific gates and execution restrictions remain in force.

Verification obligations: legacy input hash unchanged; save/reopen identity;
missing/corrupt sidecar and schema downgrade refusal; strict unknown-field and
code-provider refusal; nested immutability; physical invalidity through the
public compiler; snapshot isolation from subsequent project edits. No new
numerical campaign is authorized by this ADR.
