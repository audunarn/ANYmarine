# ADR-004: Hydrodynamic ownership and hydrostatic composition

Status: governing core adopted by the parent, 2026-10-08. Implementation,
source-specific composition and scientific qualification remain pending.

## Decision

Mechanics owns dry gravity; the hydrodynamic source owns buoyancy. Each wetted
entity has one buoyancy owner. The current ANYhydro Capytaine adapter explicitly
uses mass=0 and buoyancy-only stiffness: this work identifies no existing
adapter double-counting bug. Potential flow plus drag-only correction is allowed;
potential flow plus duplicate Morison inertia/buoyancy is refused.

All composition uses declared source force bases, baseline wrenches and
stiffness derivative semantics. Baseline buoyancy, gravity and preload are
separate from incremental restoring. Their assembled equilibrium residual must
meet a prospectively declared solver criterion before dynamics admission.

For a generalized force pullback Q(q)=D(q).T F(q), each derivative column is

    dQ/dq_j = (dD.T/dq_j) F + D.T (dF/dq_j).
    K_chart = -dQ/dq.

If F is work-conjugate to source coordinates h, dF/dh=-K_source and D=dh/dq,

    K_chart = D.T K_source D - columns[(dD.T/dq_j) F_baseline].

A physical-wrench source requires its declared physical rate map instead of
an assumed h-gradient. Geometry, frame and nonzero-baseline terms are included.
No blind symmetrization, mirroring or arbitrary stiffness swap is permitted.

The gravity closed form is only at the additive chart origin theta=0. With
R(theta)=Exp(theta) R_anchor and c=R_anchor c_body in world coordinates,

    tau_baseline = -W c cross e_z
    K_physical = -W (c_z I - c e_z.T)
    K_chart = K_physical - 0.5 skew(tau_baseline).

Here K_physical is minus the moment derivative with respect to spatial
angular increments at that origin. K_chart is the Hessian of the rotational
potential W e_z.T Exp(theta)c. At a nonzero chart coordinate use the general
pullback derivative above, not this origin-only formula. Gravity-only
symmetrization happens to give this same Hessian; it is not a general
conversion rule. The proposed vertical-center-through-CG/CB restriction is
rejected: general origins are retained.

BUOYANCY_ONLY inputs compose with separately owned gravity after the declared
force-basis conversion. FULL inputs require exact source mass/CG/bases and a
verified decomposition into buoyancy and gravity, or an explicitly admitted
reference-only gravity ownership policy. Neither alternative is silently
selected. Unknown source baseline/bases/derivative semantics leave every affected
future six-DOF composition unadmitted until resolved. An imported-as-is label
never bypasses this condition. Existing admitted linear four-DOF behavior is
unchanged; no new six-DOF source capability is claimed.

## Evidence and limits

The corrected Mistral algebra candidate worker-02/check-05 passes eight checks.
Its gravity covector derivative error is 1.54e-10; its second-difference
potential Hessian error is 1.45e-7 under a separately declared development
second-derivative criterion. Independent parent-02 has 12 instantaneous cases,
with gravity derivative error 6.383992139502503e-11 under the prospective 1e-8
criterion. Both are algebra evidence, not W3/W4 acceptance or physical source
qualification. Parent reports are in the evidence workspace at
mechanics-contract-parent-01-artifacts/report.json and parent-02-artifacts/report.json.

Original failed attempts and excluded trajectory demonstrations are retained.
Source schema extensions, source-specific asymmetry interpretation, FULL import
policy, case validity envelopes and actual six-DOF integration remain open.
The current adapter and legacy linear route are not modified by this ADR.