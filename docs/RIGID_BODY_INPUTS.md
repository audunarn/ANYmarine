# Explicit rigid-body mass inputs

The Body properties panel now includes **Specify CG and inertia**, CG in body
axes [m], a full 3×3 CG inertia table [kg m²], and **Validate dry mechanics**.
All entries are explicit: absent values are unknown, never zero or an estimated
box inertia. The full matrix uses tensor values, including off-diagonals.
The reference geometry uses body/mesh axes parallel to GLOBAL at its reference
position, as in the existing project format. Arbitrary initial body orientation
in the project geometry is not introduced by this extension.

Checking the box with incomplete fields leaves the model untouched. Complete
finite declarations are stored atomically. Invalid/nonphysical inertia can be
retained as draft input but the public solver preparation refuses it; it cannot
become a completed analysis result. Validation requires the compatible optional
ANYsolver API and reports an actionable diagnostic when it is unavailable.
Removing the checkbox explicitly removes the declaration. CG/inertia edits
change the project input hash and make old results stale. In-flight immutable
snapshots keep their captured declarations and hashes.

`Body.mass_properties` holds a Qt-free immutable `BodyMassProperties` record.
Its optional JSON extension is:

```json
{
  "version": "body_mass_properties_v1",
  "cg_body": [0.3, -0.2, 0.7],
  "inertia_cg_body": [[4000000, 200000, 100000],
                      [200000, 5000000, -100000],
                      [100000, -100000, 6000000]]
}
```

These are illustrative declared inputs, not a qualified physical body.
The extension is omitted when absent, preserving existing project hashes and
serialized identities. Recognized malformed data and unknown extension versions
are refused; finite numbers and immutable copies are required. The application
record transports the inputs; ANYsolver owns physical inertia validation and
mechanical mass/force/energy assembly.

Both headless and Qt callers use
`ApplicationService.prepare_rigid_body(project, body_id)` (or
`anymarine.mechanics.prepare_rigid_body`). The service calls public
`anysolver.marine_rigid.RigidBody6D` lazily and never imports a numerical backend
at application base-import time. Missing CG/inertia, foreign identities,
nonphysical inertia or an incompatible optional solver give typed non-success
without mutating the project. The returned object supports instantaneous exact
dry mechanics and point geometry. It does **not** run nonlinear time integration
or admit source hydrodynamics; W4/W8 qualification remains pending.

The public example `examples/rigid_body_workflow.py --out NEW_DIRECTORY` prepares
a loadable project with illustrative explicit CG/inertia for both bodies, validates
them through the shared service and saves them. No trajectory is generated. Open
it with `run_gui.py --project NEW_DIRECTORY` or File > Open. Existing directories
are refused rather than overwritten; startup replay example remains separate.
