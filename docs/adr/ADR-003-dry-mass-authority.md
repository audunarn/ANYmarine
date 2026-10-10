# ADR-003: Dry mass authority and world-frame inertial terms

Status: governing core adopted by the parent on 2026-10-08; implementation and qualification pending. Governing corrections r1
adopted; API/integrator qualification pending. Evidence:
`C:/ANYworkspaceWork/anymarine-evidence/mechanics-contract-worker-02/`
(check-05, instantaneous identities only).

## Context

W3/W4 need an exact rigid-body mechanics core before any marine dynamics
contribution is admitted. `anymarine.domain.Body` currently stores only
scalar `mass` and `reference_position`; Capytaine can auto-estimate inertia
but must never be the authority (adopted plan 6.3). The first contract
draft expressed the dry terms in a body-frame Newton-Euler form verified by
free-body integration; that trajectory evidence is excluded from adoption
(correction brief), and the corrected formulation is world-frame and
instantaneous.

## Decision

1. **Authority:** ANYsolver owns dry mass \(m\), CG \(c_{body}\) and the
   full inertia \(I_{c,body}\) about the CG. Source-estimated inertia is
   never used. The application schema extension (CG, inertia fields) is
   RESERVED to parent.
2. **World-frame composition (adopted core):**
   \(c = Rc_{body}\), \(I_c = RI_{c,body}R^{T}\),
   \(I_O = I_c + m((c\cdot c)I - cc^{T})\),
   \(M = [[mI, -mS(c)],[mS(c), I_O]]\),
   \(g = [m\,\omega\times(\omega\times c);\ \omega\times(I_O\omega)]\).
3. **Inertial internal covector (no negative sign):**
   \(Q_{int,inertial} = B^{T}(M\dot\nu + g)\) with
   \(B = \mathrm{diag}(I, J_l)\), \(\dot\nu = B\ddot q + \dot B\dot q\).
   The residual is external minus internal, so the dry force sits on the
   internal side with a positive sign (matching `marine_linear`'s
   `f_dry`). The chart acceleration Jacobian is \(B^{T}MB\) (symmetric).
4. **Kinetic energy:** \(T = \tfrac12\nu^{T}M\nu\), NOT
   \(\tfrac12\dot q^{T}M\dot q\) without \(B\). The gyroscopic vector
   \(g\) is mandatory: the instantaneous power identity
   \(\nu^{T}(M\dot\nu+g) = \mathrm{d}T/\mathrm{d}t\) fails without it.
5. **Point map:** \(a_C = a_O + \alpha\times c +
   \omega\times(\omega\times c)\); body-frame translational derivatives
   need transport. Attachment Jacobian \(J = [I, -S(R\rho)J_l]\) with
   bias \(h = \dot J\dot q\) mandatory in accelerations.

## Evidence (worker-02 check-05, all PASS)

- \(I_O\) composition 2.4e-16; KE forms agree 3.1e-16; power identity
  8.1e-10; \(a_C\) map 1.5e-10; \(B^{T}MB\) symmetry 1.1e-15;
  covector power 2.3e-15; \(\alpha = J_l\ddot\theta +
  \dot J_l\dot\theta\) 1.4e-10; \(\omega^B = R^{T}\omega\) 3.5e-16.
- Parent01 independently verified the full shifted mass/gyro/power and
  point map (report: maxima <= 1.2e-10, PASS, DEVELOPMENT_ALGEBRA_ONLY).

## Consequences

- W4 implements the world-frame form; the body-frame Newton-Euler route is
  not the contract form (it remains mathematically equivalent but its
  integration-based evidence is excluded).
- No trajectory/free-decay claim is made by this ADR; W4 gates are
  separate (contract 10.3).
- Reference-point shifts follow the existing ANYhydro `shift_matrix`
  convention (worker-01, retained).


Parent adoption follows MECHANICS_LOAD_CONTRACT.md section13 and ADR-004.
Independent parent-01/02 algebra supports the core; actual integrator/public APIs,
source admission and W3/W4 gates remain unimplemented or unqualified.
