# ADR-011: Residual semantics and hydrodynamic coordinate mapping

Status: governing core adopted by the parent on 2026-10-08; implementation and qualification pending. Governing corrections r1/r3/
r4/r5 adopted; HHT and integrator admission pending. Evidence:
`C:/ANYworkspaceWork/anymarine-evidence/mechanics-contract-worker-02/`
(check-05, instantaneous identities only).

## Context

The marine contribution protocol needs exact residual/tangent semantics
and an exact mechanical-to-equilibrium-hydrodynamic coordinate map before
W3/W4. The first draft used a constant-offset finite-pose map
\(q_h = E_0(q_m - q_{eq})\); the parent adopted the exact pose->equilibrium
map instead, and corrected the Morison internal sign and the tangent
definitions.

## Decision

1. **Residual (adopted):** \(\mathcal{r} = Q_{ext} - Q_{int}\), matching
   `marine_linear`. Tangents are **total residual derivatives**:
   \(K = -\partial\mathcal{r}/\partial q\),
   \(C = -\partial\mathcal{r}/\partial\dot q\),
   \(M_a = -\partial\mathcal{r}/\partial\ddot q\), including external
   follower, inertial geometry and transport derivatives. \(K\) is NOT
   simply \(\partial Q_{int}/\partial q\) generally (follower
   counterexample verified). Mass-route symmetry is declared per
   contribution (dry \(B^{T}MB\), BEM \(A\), projected Morison
   \(J^{T}M_aJ\) symmetric); generic symmetry is not universally assumed.
2. **Acceleration-dependent forces (corrected signs):** Morison external
   contains \(-M_a\ddot q\); equivalently internal \(+M_a\ddot q\)
   (routes identical, verified 1.9e-16). The original '-Ma inside Fint' is
   wrong (error \(2J^{T}M_aJ\ddot q + J^{T}M_a h\), verified). The
   geometric acceleration bias \(h\) is assembled ONCE.
3. **Newton trial (adopted form; no marching run):** additive-chart
   Newmark trial \(q = q_{pred} + \beta\Delta t^2 a\),
   \(\dot q = v_{pred} + \gamma\Delta t\,a\); Newton system
   \(\mathcal{J}\,\delta a = -\mathcal{r}\) with
   \(\mathcal{J} = -[M_a + \gamma\Delta t\,C_v +
   \beta\Delta t^2 K_q]\), equivalently the **sign-reversed effective
   operator** \(E = M_a + \gamma\Delta t\,C_v +
   \beta\Delta t^2 K_q = -\mathcal{J}\) solves
   \(E\,\delta a = \mathcal{r}\); update
   \(a_{next} = a + \delta a\), with the full residual reevaluated at
   each Newton iterate. \(E\) is called sign-reversed only because
   \(\mathcal{J} = -E\); **no positive-definiteness assumption is made
   for \(E\)**. The
   radiation current term is in \(C_v\) OR an explicit split, never
   both. The existing linear closed-form RHS is a special case, not the
   nonlinear formula. HHT is pending derivation and independent
   admission.
4. **Exact pose->equilibrium hydro map (adopted; replaces the
   constant-offset finite-pose map):**
   \(h = [E_0^{T}(r + R\rho_H - r_0 - R_0\rho_H);\
   \log(E_0^{T}RR_0^{T}E_0)]\),
   \(H = [[E_0^{T}, -E_0^{T}S(R\rho_H)],[0,
   J_l(h_{rot})^{-1}E_0^{T}]]\),
   \(\dot h = H\nu\), \(\ddot h = H\dot\nu + \dot H\nu\),
   \(Q_{world} = H^{T}Q_h\), \(Q_{chart} = B^{T}H^{T}Q_h\).
   Acceleration/mass/bias composition:
   \(M_{chart} = B^{T}H^{T}A_hHB\),
   bias \(B^{T}H^{T}A_h(H\dot B + \dot H B)\dot q\); full crossbody
   blocks preserved (12-DOF verification).
5. **Ownership and limits:** general-axes coefficient transformations are
   ANYhydro-owned (current translation-only `rotate_axes` is an explicit
   prerequisite); the consumer nonlinear mechanical pullback is
   ANYsolver-owned. The exact map is kinematics ONLY and never qualifies
   large-excursion BEM; the admission envelope is case/source validated,
   unknown => unadmitted. The principal log branch is guarded.

## Evidence (worker-02 check-05, all PASS)

- Route equivalence 1.9e-16; wrong-sign error matched 7.1e-16; bias
  omission matched 6.4e-15; tangent identity 2.1e-10; follower derivative
  2.62 (nonzero); declared mass symmetry 8.9e-16.
- \(\dot h = H\nu\) 3.5e-11; \(\ddot h\) composition 2.3e-9; work
  conjugacy 4.9e-11; infinitesimal virtual work second-order (ratio 0.0033
  of declared bound); crossbody chart mass FD 7.3e-16; bias 1.5e-16;
  symmetry 3.6e-15.
- Parent01 independently verified \(H\) by finite differences and
  infinitesimal work (report: hydro_jac <= 1.2e-10, work 5.3e-16, PASS).

## Consequences

- W4's contribution protocol uses the total-derivative tangents and the
  exact pose map; the constant-offset map is not the contract form.
- The existing `marine_linear` closed form remains valid as the linear
  special case; no existing solver semantics change.
- HHT-alpha, rechart event semantics and the admission envelope remain
  RESERVED to parent; no current-6DOF claim is made.


Parent adoption follows MECHANICS_LOAD_CONTRACT.md section13 and ADR-004.
Independent parent-01/02 algebra supports the core; actual integrator/public APIs,
source admission and W3/W4 gates remain unimplemented or unqualified.

## Implementation update, 2026-10-09

The native dry mechanics, framed links, total-derivative contribution protocol,
fixed-step Newmark and accepted-step rechart now have public solver APIs and
scoped source/installed development evidence. ANYmarine's native persisted-input,
QProcess job, result admission and authoritative matrix replay paths have source,
installed and Windows desktop development checks. Narrow independent dry and
integrator/history source assessments are recorded; full scientific W4 acceptance
and required legacy regression remain pending. Failure/rollback diagnostic repairs
and independent delta assessment are recorded in the living task note.

ANYhydro's proper orthonormal coefficient frames and constant equilibrium-linear
axes/reference transformations are implemented with scoped development evidence;
the historical translation-only prerequisite above is satisfied. The exact
nonlinear consumer pose/rate/acceleration pullback in decision4 is still pending,
as are source-specific motion envelopes and six-DOF fluid admission. Exact
mechanical geometry/rechart does not qualify large-excursion potential flow.
The next coherent fluid prerequisite is this public ANYsolver pullback and its
work/derivative tests, followed by source-specific equilibrium/admission.
HHT remains outside the admitted fixed-step Newmark route.

## Implementation update: exact hydrodynamic pullback, 2026-10-09

Decision4 now has public `marine_hydro_coordinates_v1` source/wheel/installed
DEVELOPMENT evidence. Material reference offsets, distinct current/equilibrium
anchors, exact log/rate/acceleration and first/second derivatives, full coupled
force/baseline stiffness and constant added-inertia K/C/Ma are implemented.
Independent mathematical and coverage assessments complete; reviewed hardening
and coverage additions verified. This satisfies the kinematic prerequisite at
development scope, not physical BEM validity or full W4 scientific acceptance.

Native fluid contribution/history integration and explicit real-source force
basis/baseline conversion, geometry binding and motion envelope are next. Unknown
source semantics remain unadmitted. Current ANYmarine native jobs still refuse
configured fluid sources; the existing admitted linear4DOF route is unchanged.
Evidence: hydro-coordinate-verified-manifest.json/verified-handoff.md and TASK.md.
HHT, large-excursion potential-flow qualification and W8-W10 remain unadmitted.

## Implementation update: explicit clock, 2026-10-09
Native StepInformation carries immutable phase/time/dt and previous accepted states; compatible timed hooks and consistent initialization abort without history commit are development verified source/installed. Real fluid and source admission remain pending; native-fluid-plan.md defines the next development-only contribution slice. Evidence marine-clock-verified-manifest.json, TASK.md.

## Implementation update: native development fluid, 2026-10-09
Public marine_potential_flow_development_v1 now integrates the exact pullback and explicit clock with full coupled inertia/restoring/excitation/direct convolution in native mechanics. Immutable fixed-equilibrium accepted velocity history, initialization/finite-window/rechart/cancellation/refusal and total derivative/force oracles have scoped source/installed development evidence. Real physical source admission and equilibrium balance/envelope proof remain pending; only labelled development fixtures in the declared h-gradient basis are admitted. GUI and native JSON integration remain next. Evidence native-fluid-verified-manifest.json and TASK.md. No full W4/W8-W10 qualification or large-excursion potential-flow claim.
