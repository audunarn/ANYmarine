# Native dynamic mooring workflow â€” development, unqualified

Choose **File â†’ Load native mooring example (three lines)**. ANY3dView shows two
bodies and three brown anchored lines. Loading displays the declared initial
geometry without importing or executing the numerical solver.

**Jobs â†’ Edit native mechanical inputsâ€¦** exposes named anchors, material body
fairleads, world free-node positions/velocities and per-segment SI coefficients.
The shared application compiler validates edits. **Jobs â†’ Run native mechanical
jobâ€¦** runs the example's three fixed steps in an isolated child process using
the public ANYsolver coupled body/node driver. The example has explicit zero
gravity and is a transport/mechanics demonstration, not a catenary benchmark.

Each accepted frame stores body transforms, world node positions, velocities,
accelerations, line tensions, endpoint/anchor forces and energies. Replay follows
these authoritative arrays; it does not solve line motion in Qt. Renderer changes
retain the selected frame. Results carry their immutable producing project, so
editing inputs labels previous results stale and replay still uses their original
geometry. Save/reopen preserves this information.

Mooring transport uses `native_mooring_inputs_v1` and Solver's
`marine_mechanical_mooring_v1`. Body-only records retain their existing contracts.
Node inventories, finite values, initial states, force residuals and complete
timelines are checked on worker admission. Stored node positions must agree with
their velocities/accelerations under the fixed Newmark Î²=Â¼, Î³=Â½ recurrence both
on admission and reopening. The 1e-12 relative allowance with SI unit floors
covers transport roundoff; it is not a scientific force/response criterion.
Producer seals and source qualification are separate from array consistency.

The current line law includes tension-only elasticity, axial damping, reference
mass and fully submerged constant-volume weight/buoyancy. Line hydrodynamic drag, fluid inertia and added mass are implemented for fully
submerged centerlines. Contact qualification/friction, coupled static equilibrium and registered W6
catenary/refinement/time-step acceptance remain incomplete. Full six-DOF physical
BEM source admission and W8â€“W10 acceptance also remain incomplete. No qualified
marine analysis or release is claimed.


## Wave/current line example

Choose **File → Load native wave/current mooring example**. It loads two bodies,
two Morison members and three anchored lines, with one explicit shared Airy-wave
and current environment. **Jobs → Edit native mechanical inputs…** exposes each
segment's diameter, normal/tangential drag, added-mass and fluid-inertia
coefficients. Members and lines must use exactly the same environment; duplicate
physical ownership is refused. Missing compatible public Loads/Solver APIs fail
before execution. The example uses three fixed steps and explicit zero gravity;
it demonstrates the connected development workflow, not a qualified equilibrium.

The hydrodynamic line intent uses `marine_mooring_hydrodynamics_v1` inside the
existing application transport. Legacy mechanical line intent stays supported.
Accepted and reopened results require normal/tangential drag, fluid inertia and
added-mass force channels at nodes and body fairleads, relative drag power and
added-mass kinetic energy. The producing snapshot remains authoritative for
stale results. Seeded sea-state regeneration updates line and member waves
together with the potential-flow excitation realization.

**Waves dock → Mechanical incident waves (saved realization)** displays the actual
declared producing environment through ANY3dView. With a stored result, wave time
follows its selected replay frame. Turning the layer off preserves the displayed
body/line frame. Fixed-body calculated disturbance previews remain a separate
development display; moving-body disturbance is not inferred from incident waves.


## Frictionless seabed contact example

Choose **File → Load native seabed contact example**. ANY3dView shows two bodies,
three lines and a gray patch at the declared horizontal seabed. Loading does not
estimate active contact or run a solver. **Jobs → Edit native mechanical inputs…**
exposes seabed elevation, pressure stiffness/damping and segment contact diameter.
**Jobs → Run native mechanical job…** performs three development steps; red
crosses then identify endpoints with stored active contact. Scrubbing and renderer
changes retain the producing floor, body/node coordinates and stored active flags.
The replay label reports stored maximum penetration and active sample count;
named channels plot penetration, energy, dissipation, reaction and force components.

The example has explicit zero gravity and31.25 mm initial compliant surface
penetration. It is not a catenary equilibrium or nonpenetration qualification.
The finite displayed floor patch illustrates an infinite horizontal floor law.
Contact stiffness [Pa/m] and damping [Pa s/m] act on half reference segment
length times diameter. The public solver owns its unilateral constitutive law,
energy accounting and tangents. App loading/replay never solves contact.

`marine_mooring_contact_v1` retains immutable floor/diameter intent, including
optional shared hydrodynamic environment. Contact/hydro diameters and floor/depth
must agree when combined; fluid points below that floor remain refused. Missing
installed contact APIs fail before numerical execution. Old line schemas remain
supported. Results require every contact lump's gap/active sample, energy/power/
penetration/reaction, node forces and six body wrench components. Geometry/index,
finite/sign/binary checks validate transport; they do not infer constitutive truth
for all positive finite values. Producer output seals and saved sidecar checksums
refuse later finite-value tampering. Parsing a manually supplied unqualified
record is not scientific qualification. Stale results replay their immutable
producing snapshot, not edited current floor inputs. Synthetic replay contains
no inferred contact markers. Staged equilibrium, friction and W6 remain pending.
