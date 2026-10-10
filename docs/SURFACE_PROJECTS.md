# Owned hydrodynamic surfaces

Use **File → Load attached-plate geometry example** to view two floaters with
one submerged plate attached to each. Save creates a normal project copy.
The plates move with their owning body and add no rigid-body coordinates.
This example is a geometry preview; it has no computed plate coefficients.

`Body.hydro_surfaces` extends the existing body record. Each assignment has a
stable surface ID, owning body, source geometry ID, explicit surface kind,
primary hydrodynamic owner and optional viscous convention from the public
ANYhydro contract. Hull and numerical-lid assignments refer to existing body
geometry; attached sheets retain body-local triangles. Numerical lids remain
invisible and have no physical load ownership. Sheet winding is minus-to-plus;
two-sided display never changes that winding. Explicit surfaces require the
tested ANYhydro package; ordinary application imports remain optional-backend free.

The public `migrate_legacy_surface` operation requires an explicit role, source
identity and, for sheets, fluid sides. Old projects load unchanged. Shell
openness and numerical lids never imply physical plates. Unknown/ambiguous
assignments, coincident opposing panels and full Morison sheet inertia refuse.
Surface records carry no plate mass or displacement; those remain existing
mechanical/hydrostatic authorities. Declaration of plate drag does not implement
its force adapter, so current numerical import/native paths refuse that correction.

Projects with current **or retained** surface intent use manifest schema 7.
Older applications refuse it. Removing plates does not erase this protection
from retained coefficient bindings or producing result snapshots. Surface
geometry, sides, ownership and source edits change input identity and make
dependent coefficients/results stale. Save/reopen retains those stale records.

Raw and derived coefficient importers prove the complete fixed-equilibrium
world-space inventory before decoding dependent operators/kernels. Coefficient-
only datasets are supported; local pressure is optional. This preserves the
existing four-DOF import scope and all reconstruction/admission criteria; it does
not qualify 12-DOF plate dynamics or authenticate declared external source evidence.

Surface-bound effective linear inputs retain immutable producing geometry.
Their result records retain those effective inputs and validate their hash and
geometry binding. Stored replay, renderer replacement and stale replay restore
that source geometry. Removing a matching result's entire snapshot is refused;
an older linear result without producing surface geometry cannot replay in a
project with current or retained surface intent. Open its plate-free producing
project instead. Legitimate plate-free legacy records keep their serialization.

Native Capytaine sheet solves, the native application surface force adapter and
native wave-preview disturbance for explicit surfaces remain unsupported and
fail clearly. No panel omission, numerical thickness or zero-filled plate
response is substituted. A dedicated incident-only plate wave display remains
pending; current plate preview has no computed wave disturbance.

Interface tests use analytic transport fixtures and a fake stationary backend
for result-builder checks. They are not plate physics or D10–D14 qualification.
