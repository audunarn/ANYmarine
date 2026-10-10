# W4 mechanical slice — governing implementation decisions

Compatible prospective extension of the adopted mechanics/load core. New
solver-owned experimental APIs; no existing structural/linear behavior changes.
Physics below is ADOPTED for implementation. Full scientific W4/W8 acceptance
is separate. No BEM, wave/current/Morison, line/contact or HHT claim in this slice.

## Objective and public outcome

Implement the connected solver outcome: full dry K/C/Ma derivatives;
MechanicalLink6D with finite attachment geometry, frame, preload, elastic and
viscous force/moment output; a generic dynamic-contribution trial/history owner;
and fixed-step nonlinear Newmark integration of all body DOFs monolithically.
Rigid bodies are the existing public RigidBody6D/RigidBodyState. Preserve the
root API, marine_linear and unrelated structural code. New public modules may
be marine_link, marine_contributions and marine_mechanical. Expose versioned
APIs and finite immutable specification/result records; parent integrates app.

## Link law (explicit physical decision)

For attachment points rho_a/rho_b and proper body-fixed attachment axes Qa/Qb:
xa=ra+Ra rho_a, xb=rb+Rb rho_b, A=Ra Qa, B=Rb Qb, t=xb-xa.
Declared rest translation d0 is in attachment-A axes, declared rest relative
rotation D0 is A0.T B0. Strain:

    z = [A.T t - d0 ; Log((A.T B) D0.T)]

This is a conservative finite-geometry spring whose axes follow attachment A.
Full local 6x6 symmetric PSD K and C, SI mixed translation/rotation units.
No diagonal-only limitation, clipping, symmetrization or invented stiffness.
Allow a declared numerical PSD tolerance (relative1e-12), retain original matrix.
Preload p0 is a declared LOCAL constitutive covector; positive axial preload
pulls the endpoints together. U=.5 z.T K z+p0.T z,
s=Kz+p0+C zdot. Relative rotation uses the principal branch; refuse near pi,
never silently unwrap or change rest frames. This is not an unbounded torsion law.

Let pa=Ra rho_a, pb=Rb rho_b, V=Jl(phi)^-1 A.T, phi=z_rotation.
World-twist derivative blocks:

    La = [[-A.T, A.T skew(pa+t)], [0, -V]]
    Lb = [[ A.T,-A.T skew(pb) ], [0,  V]]
    G  = [La Ba, Lb Bb], Bbody=diag(I,Jl(theta_body))
    zdot=G qdot_pair; Q_external_pair=-G.T s

Total link derivatives (NOT merely material stiffness):

    C_link=G.T C G
    K_link[:,j]=dG_j.T s + G.T K G[:,j] + G.T C (dG_j qdot)
    Ma_link=0

Analytic derivatives can use dA.T=-A.T skew(delta_omega_a),
dpa=delta_omega_a x pa, dpb=delta_omega_b x pb,
dt=delta_rb-delta_ra+delta_omega_b x pb-delta_omega_a x pa,
dV=-Jl_inv DJl(phi)[dphi] Jl_inv A.T+Jl_inv dA.T,
dphi=G_rotation[:,j], and existing Jl directional derivatives for dBa/dBb.
Actual physical world wrench is recovered from chart covectors by B^-T.
Verify total force/moment about a COMMON world origin sums to zero, including
the frame-dependent translation spring torque on A; do not omit that torque.
Power: qdot.T Qext=-Udot-zdot.T C zdot. Rest reference factory may derive
d0,D0 ONLY through an explicitly named caller-selected reference-state factory.
No rest state is guessed from the current trial. IDs/frames immutable and exact.

## Dry derivatives and residual

Qdry=B.T(M nu_dot+g) from the adopted core. Supply full derivatives wrt q and
qdot, not just acceleration tangent. Stable analytic second Jl derivatives
or an equivalent exact analytic method; finite differences are verification,
not the production tangent. A distinct stable second-derivative series threshold
is permitted with a numerical accuracy rationale. Keep all transport/gyro terms.
Dry K=dQdry/dq, C=dQdry/dqdot, Ma=dQdry/dqdd.

Each generic contribution returns an EXTERNAL chart covector and total
K=-dQext/dq, C=-dQext/dqdot, Ma=-dQext/dqdd, component diagnostics and trial
history. Residual r=sum Qext-Qdry; assembled K/C/Ma sum dry and external terms.
Acceleration-dependent contribution is included ONCE. Reject duplicate IDs/
physical ownership keys including reserved dry-mass ownership. Do not assume
generic Ma or effective E symmetric/positive definite. No physical interpretation
or source admission is conferred on an arbitrary developer contribution.
Named world-wrench and dry-gravity contributors may be supplied as trusted
native types with exact follower/chart derivative terms. Gravity is explicitly
selected; do not add it to unknown BEM/hydrostatic material.

## Trial and committed history

prepare_step reads immutable accepted state/history. Each evaluate_trial reads
the SAME committed history for the step and can produce fresh trial-local history.
Single history owner commits ALL contributions atomically after convergence,
finite checks and cancellation polling. Rejected Newton/line-search trials,
failed/cancelled steps, stale trial tokens, duplicate/non-owner commit cannot
change committed history. Poll cancellation during trial operations as well as
between steps. Logs retain incomplete trials and accepted-step inventory.
History representation must be immutable/copied, not an alias to provider data.
Physical/world or fixed-reference histories survive body rechart unchanged;
chart-bound history requires a real explicit transport callback. Unknown basis
or missing callback refuses admission. Do not reset unknown histories to zero.
No radiation initial-history contract is changed or implemented here.

## Newmark and rechart decision (adopted for this native route)

Fixed dt, beta=.25 gamma=.5, using existing public marine_linear constants.
For every step, keep accepted chart anchors fixed throughout Newton:
q=qpred+beta dt² a; qdot=vpred+gamma dt a; solve
E delta_a=r, E=Ma+gamma dt C+beta dt² K, and update a+=delta_a.
Reevaluate the actual full residual. Optional residual line search may reduce
Newton increment, NEVER dt or physical parameters. Typed failure at singular,
nonfinite, branch violation or iteration limit; no accepted invalid state.
Compute consistent initial acceleration from the actual residual with q/qdot
fixed, including acceleration-dependent contributions once.

After EACH accepted step (and initial normalization), set each new body anchor
to its exact current R, theta'=0, keep world position, and transport angular
chart rate/acceleration to omega/alpha. Translation rates unchanged. Compute
next-step predictors anew from that accepted chart state. Rechart/transport
trial histories before atomic commit using declared basis/callback; preserve
physical pose/rates, all source/rest frames and committed evidence. Output
authoritative rotation MATRICES, positions and world rates/accelerations, not
an ambiguous accumulated rotation-vector trajectory. No silent orthonormalization.
This new route is not HHT and does not claim a structural FE registration API.

## Results and errors

Public solve_marine_mechanical(model, initial_states, run_spec, cancel, progress)
or equivalent versioned API. Explicit ordered stable body IDs; all DOFs together.
Completed only after every requested step converges and commits. Cancel returns
cancelled partial evidence containing only accepted finite states. Runtime
failure raises a typed solve error carrying failed partial evidence, logs and
histories. Invalid inputs fail before execution. Include per-component force
decomposition, kinetic/link potential energy and link damping power; histories
sum to actual residual forces. No completed result for cancelled/failed jobs.
Native model/spec serializers may encode trusted named native types only;
never load arbitrary Python/provider code from a project JSON.

## Development verification (prospective, not scientific campaigns)

Algebra/work/action-reaction relative1e-10; FD derivatives1e-8, normalize separately
force [N], moment [Nm], and tangent blocks in their SI units with unit floor1.
Exact/near-zero cases also use absolute1e-10 algebra /1e-8 FD in the named units.
Random samples at most12 per analytic-check process. Named integration cases:
1 spherical free translation/rotation and gravity quadratic response (100 steps);
2 offset-CG, nonspherical free body: physical linear/angular momentum and energy
  drift<=1e-6 relative over .1s with dt=.001, initial scales unit floors;
3 two-body axial link linear reduction: max displacement error<=1e-3 of declared
  initial relative amplitude, dt=.001, 100 steps; do NOT apply RAO criteria to a
  generic nonlinear model;
4 nonlinear offset framed link: work/action-reaction and finite convergence,
  100 steps maximum, exact parameters fixed BEFORE the attempt.
Dedicated short (<10 steps) protocol cases: acceleration term once, follower
derivative, same committed history across rejected trials, mutation refusal,
mid-trial cancellation, failed provider, nonfinite/singular systems, final-step
late cancellation and stale/double commit refusal. Do not invent a pass by
omitting the failed case or tuning physics/tolerances. A failed fixed prospective
case is retained; repair compatible algorithms or escalate the concrete issue.
No BEM/Capytaine/backend/source-study execution/probe EVEN MOCKED, no external
campaign, contact/Morison/line qualification, structural baseline campaign or
publication. All prior physical and ordinary pools remain closed/frozen.
