# Parent reconciliation — 2026-10-08

The corrected core equations are adopted as a prerequisite, not W3/W4 acceptance.
Contract section13 and ADR-004 govern. The historical worker checkpoint below is
retained as evidence; its stale remaining-work/adoption/history statements are
superseded by this reconciliation and TASK.md.

Actual process history: original session59418 was intentionally interrupted for
algebra-only scope deviation. Correction session63705 completed eight algebra
checks and candidate docs; its corrected checkpoint arrived late after algebra.
Session47280 ended at24turns after math normalization; session68995 ended at32turns
after main contract semantics; session33366 ended at24turns after potential
notation and ADR-011 reconciliation. The latter three handoffs were incomplete.
The parent then performed the reserved governing adoption and final ADR-004
source-admission/domain decision; this was no production implementation takeover.
All owned sessions are terminal, and no additional checks were run for doc repairs.

Parent-01/02:12 instantaneous cases each, PASS,1.5180686999956378s total/30s.
Worker01/02:10 metered attempts,3.16732190000766s total/45s; failures retained.
Prior unmetered Python JSON reads mean these are not all-process usage claims.
Both pools are now closed; unused authority is not pooled into other milestones.
Original trajectory demonstrations are excluded. No BEM or physical source study
executed, pending approval unchanged. Toy history cases are not production tests.

Remaining work: actual integrator/rechart transport, CG/inertia schema/public APIs,
source force-bases/baselines/derivatives and FULL import policy, validated motion
envelopes, W3/W4 production tests and every remaining roadmap qualification gate.

---
# MECHANICS_LOAD_CHECKPOINT — corrected checkpoint (2026-10-08)

Status: **correction milestone executed.** Governing corrections 1-9 of
`mechanics-contract-correction-brief.md` are adopted into the candidate
contract documents; the corrected algebra-only candidate evidence passes all
checks. Nothing here is adopted by the parent; RESERVED decisions are listed
at the end. This checkpoint replaces the prior worker-01 checkpoint content,
which is preserved below under "Correction history" (earlier defects are
recorded, not concealed).

## Correction history (preserved record)

- The original implementation brief (mechanics-contract-implementation-
  brief.md) produced `mechanics-contract-worker-01/candidate_algebra.py`
  (9 checks, runs check-01..05: crashes/failures retained, check-05 passed)
  and the first draft of the contract documents.
- The parent interrupted that worker for **algebra-only scope deviation**
  (trajectory/integration content), not service failure. Governing
  corrections 1-9 were issued; the correction worker log
  (mechanics-contract-correction-worker.log) shows it was interrupted after
  read-only inspection, before any correction work.
- **Excluded from adoption:** original trajectory checks 2/4/6
  (`rate_conflation` time integration, `newton_euler` free-body integration,
  `morison_assembly_once` Newmark marching). Script and results retained
  intact in `mechanics-contract-worker-01/`; no trajectory claim survives.
- **New permitted scope:** `mechanics-contract-worker-02/
  candidate_algebra_permitted.py` — instantaneous pose/matrix/derivative
  identities and counterexamples ONLY. No integration, ODE/Newmark/time
  marching, solver/backend import or source-study execution. Pure numpy,
  <=12 DOFs, <=64 cases/check, single process/thread.

## Corrected core equations (adopted into the contract; verified below)

1. **Chart with fixed anchor:** R = Exp(theta) Rc. Additive-chart Exp
   identities are FIRST ORDER + O(||d||^2), not finite-exact (residual
   6.5e-12 at ||d||=1e-5, quadratic decay ratio 0.24998). Spatial left
   retraction differs from additive increments at first order unless mapped
   by Jl (Log(Exp(d)R) = theta + Jl^-1 d + O(d^2); first-order difference
   2.9e-6 nonzero). Principal Log branch ambiguity at ||theta|| = pi
   (norm 3.2 maps to principal 3.083 = 2pi - 3.2); Jl non-singular at pi
   (det = 4/pi^2 = 0.4053), singular at nonzero 2pi multiples (min sv 0.0).
   Rechart = new anchor PLUS transported chart rates/accelerations
   (theta_dot_new = omega to 9.7e-12, theta_ddot_new = alpha to 3.1e-9);
   Log(R) alone is not a rechart. No accepted pi/2 bound is invented;
   integration admission requires explicit update, branch guard and
   accepted-step rechart transport (deferred implementation, not physics).
2. **World-frame dry mass:** c = R c_body, Ic = R Ic_body R^T,
   Io = Ic + m((c.c)I - cc^T) (composition 2.4e-16),
   M = [[mI, -mS(c)], [mS(c), Io]], g = [m om x (om x c); om x (Io om)].
   Inertial INTERNAL covector = B^T(M nu_dot + g), B = diag(I, Jl),
   nu_dot = B qddot + Bdot qdot (1.2e-10); NO negative sign. Chart
   acceleration Jacobian B^T M B, symmetric (1.1e-15). T = .5 nu^T M nu,
   NOT .5 qdot^T M qdot without B (all forms agree to 3.1e-16).
   Power identity nu^T(M nu_dot + g) = dT/dt (8.1e-10). World aC includes
   alpha x c and centripetal (1.5e-10); omega = Jl theta_dot,
   omega_body = Rc^T Jr theta_dot (3.5e-16); alpha = Jl theta_ddot +
   Jldot theta_dot (1.4e-10).
3. **Residual/tangent semantics:** r = Qext - Qint; tangents =
   -dr/d(q,qdot,qddot) include external follower (follower derivative 2.62
   nonzero; K is NOT simply dQint/dq), inertial geometry and transport
   derivatives. Morison external contains -Ma qddot; equivalently INTERNAL
   +Ma qddot (routes identical to 1.9e-16); the original '-Ma inside Fint'
   is wrong (residual error = 2 J^T Ma J qddot + J^T Ma h, matched to
   7.1e-16). Geometric acceleration bias assembled ONCE (omission error
   matched to 6.4e-15). Declared dry/BEM/projected-Morison mass forms
   symmetric (8.9e-16); generic mass-derivative symmetry NOT universally
   assumed.
4. **Newton trial (contract text; no marching run):** additive-chart
   Newmark trial q = qpred + beta dt^2 a, qdot = vpred + gamma dt a;
   Newton Jacobian J = -[Ma + gamma dt Cv + beta dt^2 Kq]; solve positive
   effective operator delta_a = r; full residual reevaluated. Radiation
   current term in Cv OR explicit split, NEVER both. Existing linear
   closed-form RHS is a special case. HHT pending derivation and
   independent admission.
5. **Exact pose->equilibrium hydro map (adopted, replaces constant-offset
   finite pose):** h = [E0^T(r + R rhoH - r0 - R0 rhoH);
   Log(E0^T R R0^T E0)], H = [[E0^T, -E0^T S(R rhoH)],
   [0, Jl(hrot)^-1 E0^T]]; hdot = H nu (3.5e-11), hddot = H nu_dot + Hdot nu
   (2.3e-9), Qworld = H^T Qh, Qchart = B^T H^T Qh (work conjugacy 4.9e-11;
   infinitesimal virtual work second-order, ratio 0.0033 of declared bound).
   Acceleration/mass/bias composition and full crossbody H blocks verified
   at 12 DOF (chart mass FD 7.3e-16, bias 1.5e-16, symmetry 3.6e-15).
   General axes coefficient transformations ANYhydro-owned; consumer
   nonlinear mechanical pullback ANYsolver-owned; current ANYhydro
   translation-only remains an explicit prerequisite. Exact map is
   kinematics ONLY, never large-excursion BEM qualification; envelope
   unknown => unadmitted; principal branch guarded.
6. **General baseline-wrench/chart conversion (vertical-center restriction
   REJECTED):** with gravity tau0 = -W c x ez at theta0,
   Kphysical = -W(cz I - c ez^T) (wrench derivative, 9.5e-11), rollYaw =
   +W cx; Kchart = Kphysical - .5 S(tau0) equals -d(Jl^T m)/dtheta (1.5e-10)
   and the symmetric potential Hessian (1.4e-7 second-difference FD);
   symmetrization coincides for this gravity-only example (0.0) but blind
   symmetrization of general imported C is unjustified. Source
   force-basis/role/baseline wrench stored and converted with the
   connection. Current ANYhydro is BUOYANCY_ONLY already; no existing
   double-gravity bug. FULL imports need exact source mass/CG/bases
   decomposition or reference-only gravity ownership.
7. **Drag signs:** relative dissipation fD.(v-u) <= 0 always (16/16 random
   cases); structure power fD.v CAN BE POSITIVE under wave/current —
   explicit counterexample u=(2,0,0), v=(1,0,0): fD.v = +1 while
   fD.(v-u) = -1 (3/16 random cases positive). Diagnostic wave/current
   decomposition includes interaction (1.89) and sums exactly (0.0).
   Fluid convention: first linear-wave/uniform-current = Eulerian
   partial_t u, current local acceleration 0; fluid material Df/Dt =
   partial_t + (u.grad)u differs from member-path derivative
   partial_t + (vP.grad)u (max difference 1.88); no silent current-advection
   extension.
8. **Rotating-axis added mass:** force-based Morison is not a generic
   conservative KE model. Counterexample (parent01): axis (1,0,0),
   ndot (0,1,0), v (1,2,0), aP = 0, ma = 1: prescribed force power 0 while
   variational KE derivative = -2.0 W (exact). No generic variable-axis
   energy/free-decay acceptance claimed; no silent variational additions.
9. **History/cancellation:** cancellation polled during trial operations
   while rollback-safe (8/8 logic cases): mid-trial cancel preserves
   accepted history and incomplete logs; only successful accepted steps
   commit; late/double/non-owner commits refused; trial inputs immutable.

## Corrected candidate evidence (actual runs, metered)

Script: `mechanics-contract-worker-02/candidate_algebra_permitted.py`.
Harness: exact runtime python for harness AND child, run_check.py, timeout
5 s, OMP/OPENBLAS/MKL = 1, PYTHONPATH = C:/Github/ANYmarine/src only,
TEMP/TMP = evidence/tmp, single process/thread.

| run | wall (s) | status | note |
|---|---|---|---|
| worker-02-check-01 | 0.19005 | exit 1 (crash) | matmul precedence bug; retained |
| worker-02-check-02 | 0.21820 | exit 1 (crash) | ndarray in JSON output; retained |
| worker-02-check-03 | 0.23065 | exit 1 (3 checks FAIL) | det(Jl(pi)) expectation wrong (4 vs 4/pi^2); T2 used Io not Ic; wrong-route diff missing bias; rechart FD roundoff; retained |
| worker-02-check-04 | 0.21938 | exit 1 (1 check FAIL) | T2 edit not applied; retained |
| worker-02-check-05 | 0.22695 | exit 0, all 8 checks PASS | current corrected candidate |

Cumulative metered worker-02 spend: **1.08522 s**. Remaining from the
42.9179 s correction budget: **41.83268 s** (no renewal needed). Accounting
uncertainty: earlier Python JSON reads by prior workers were unmetered per
the correction brief; this worker's JSON inspection used PowerShell
(unmetered reads, not check runs).

## Remaining work (this milestone)

- Revise `docs/MECHANICS_LOAD_CONTRACT.md` in place with correction history.
- Write `docs/adr/ADR-003-dry-mass-authority.md`, `ADR-004-hydrodynamic-
  ownership.md`, `ADR-011-residual-and-hydrodynamic-coordinates.md`.
- Write `evidence/mechanics-contract-final-handoff.md` (changed files,
  actual commands/walls, failures, protected-source comparison, unresolved
  decisions).

## Reserved choices (parent decisions, not made here)

- Adoption of any equation, sign convention, API shape, tolerance,
  admission rule, rechart threshold/event semantics, or HHT admission.
- Public API names/signatures in ANYsolver/ANYhydro/ANYloads/ANYmarine.
- FULL-vs-BUOYANCY_ONLY import composition policy and the treatment of
  imported asymmetric C (connection-corrected conversion is specified;
  source convention treatment is RESERVED).
- Any change to existing solver/linear/hydro semantics or qualification
  limits; W3/W4 acceptance is NOT granted by this evidence.
