# ANYmarine governing mechanics/load contract (core adoption r3, 2026-10-08)

**Status: governing core adopted by the parent on 2026-10-08; implementation and qualification pending.** This is
the corrected revision of the contract draft, revised in place under
`mechanics-contract-correction-brief.md` after the parent's governing
corrections 1-9. Correction history: the first draft (worker-01) relied on
trajectory/integration evidence (checks 2/4/6: rate-conflation integration,
free-body integration, Newmark marching) which is **excluded from adoption**;
those scripts/results are retained intact in
`C:/ANYworkspaceWork/anymarine-evidence/mechanics-contract-worker-01/`.
The corrected evidence is
`C:/ANYworkspaceWork/anymarine-evidence/mechanics-contract-worker-02/candidate_algebra_permitted.py`
(runs `mechanics-contract-worker-02-check-01..05`; check-05 passes all 8
instantaneous-identity checks) — development evidence only, not W3/W4
acceptance. Parent (Astra) adopts governing physics and interfaces after
independent review; RESERVED changes (physics semantics, public APIs,
limits, qualification) remain parent decisions.

Source symbols referenced (all read-only, inspected 2026-10-08):

- `anysolver.marine_linear.solve_marine_linear` — residual sign, Newmark
  effective operator, memory-at-rest convention
  (`C:/Github/ANYsolver/src/anysolver/marine_linear.py`).
- `anyhydro.transform` — `shift_matrix`, `map_matrix`, `map_force`,
  `shift_dataset_reference_point`, `rotate_axes` (translation-only)
  (`C:/Github/ANYhydro/src/anyhydro/transform.py`).
- `anyhydro.capytaine_backend.HYDROSTATIC_NOTE`, `mass=0.0` body
  construction (`C:/Github/ANYhydro/src/anyhydro/capytaine_backend.py`).
- `capytaine/bodies/hydrostatics.py` (installed Capytaine 3.0.0, READ only,
  never imported by this contract work) — rigid-branch
  `each_hydrostatic_stiffness`, `hs_stiff = rho * g * norm_hs_stiff`.
- `anymarine.domain.Body` — `reference_position`, scalar `mass`
  (`C:/Github/ANYmarine/src/anymarine/domain.py`).

---

## 1. Frames, coordinates and charts

### 1.1 Frames

- Global inertial frame \(G\): right-handed, \(+z\) upward, free surface at
  \(z=0\) (adopted plan 6.1). SI units, radians.
- Body frame \(B\): fixed in the rigid body; origin at the body reference
  point \(O\); axes parallel to \(B\)'s material axes.
- Equilibrium hydrodynamic frame \(H_b\) per body: origin at the stored
  hydrodynamic reference point (section 6), general equilibrium axes
  \(E_0\) (section 7).

### 1.2 Pose, anchor and the rotation-vector chart

Body pose is \((r, R)\): position of \(O\) in \(G\) and orientation
\(R \in SO(3)\). The internal rotation representation is the
rotation-vector chart **with a fixed chart anchor** \(R_c\):

$$
R(\theta) = \exp([\theta]^\wedge)\,R_c,
$$

so \(\theta = 0\) means "at the anchor", not "identity". The
differential additive-chart identities

$$
\exp([\theta+\delta\theta]^\wedge) = \exp([\theta]^\wedge)\,
\exp([J_r(\theta)\,\delta\theta]^\wedge) + O(\|\delta\theta\|^2),
\qquad
J_r(\theta) = I - \tfrac{1-\cos\theta}{\theta^2}[\theta] +
\tfrac{\theta-\sin\theta}{\theta^3}[\theta]^2,
$$

are **FIRST-ORDER relations + \(O(\|\delta\theta\|^2)\), not
finite-exact equations** (verified: residual 6.5e-12 at
\(\|\delta\theta\| = 10^{-5}\), quadratic decay ratio 0.24998). The
spatial left retraction and additive chart increments **differ already in
first order** unless mapped by \(J_l\):

$$
\log\big(\exp([\delta]^\wedge)R(\theta)\big) =
\theta + J_l(\theta)^{-1}\delta + O(\|\delta\|^2)
\ne \theta + \delta \quad \text{(first order)},
\qquad J_l(\theta) = R(\theta)\,J_r(\theta),
$$

(verified: left-retraction expansion error 0.05 \(\|\delta\|^2\);
first-order difference \(\|J_l^{-1}\delta - \delta\|\) nonzero,
2.9e-6 at \(\|\delta\| = 10^{-5}\)).

**Branch behavior.** The principal \(\log\) has branch ambiguity at
\(\|\theta\| = \pi\) (verified: \(\|\theta\| = 3.2\) maps to
principal norm \(2\pi - 3.2 = 3.083\), a different chart point).
\(J_l\) itself does **not** become singular there —
\(\det J_l(\pi e_z) = 4/\pi^2 \approx 0.405\) — its singularities are
the nonzero \(2\pi\) multiples (verified: min singular value 0.0 at
\(2\pi e_z\)). **Re-charting is not a coordinate refresh:**
\(\theta \leftarrow \log(R)\) again is NOT a rechart. A rechart
requires a NEW anchor \(R_c' = R\) PLUS transported chart rates,
accelerations, Newmark predictors and history (verified transport:
\(\dot\theta' = \omega\) to 9.7e-12 and
\(\ddot\theta' = \alpha\) to 3.1e-9 at the rechart instant). No
accepted \(\pi/2\) bound is invented here. Future integration admission
requires an explicit update rule, a branch guard on the principal log, and
accepted-step rechart transport; implementation is deferred, not the
physics.

**Numerical stability requirement (verified):** the trigonometric
coefficients \((1-\cos\theta)/\theta^2\) and
\((\theta-\sin\theta)/\theta^3\) are cancellation-limited for small
\(\theta\); implementations MUST use the series forms below a declared
threshold (\(\theta < 0.02\) in the evidence script):
$$
\tfrac{1-\cos\theta}{\theta^2} = \tfrac12 - \tfrac{\theta^2}{24} +
\tfrac{\theta^4}{720} + O(\theta^6), \qquad
\tfrac{\theta-\sin\theta}{\theta^3} = \tfrac16 - \tfrac{\theta^2}{120} +
\tfrac{\theta^4}{5040} + O(\theta^6).
$$

### 1.3 Rates: chart rates are not angular velocity

With \(B = \mathrm{diag}(I, J_l(\theta))\) and chart state
\(q = (r, \theta)\), \(\dot q = (\dot r, \dot\theta)\):

$$
\omega = J_l(\theta)\,\dot\theta \ \text{(spatial)}, \qquad
\omega^{B} = R_c^{T} J_r(\theta)\,\dot\theta = R^{T}\omega
\ \text{(body)}, \qquad
\alpha = \dot\omega = J_l\,\ddot\theta + \dot J_l\,\dot\theta,
$$
$$
\nu = (v_O, \omega) = B\,\dot q, \qquad
\dot\nu = B\,\ddot q + \dot B\,\dot q.
$$

Verified instantaneously: \(\omega^B = R^T\omega\) to 3.5e-16;
\(\alpha\) with the transport term vs path derivative 1.4e-10;
\(\dot\nu\) composition 1.2e-10. **Contract rule:** no integrator may
impose ordinary Newmark/HHT update formulas on \(\dot\theta\) as if it
were \(\omega\); \(\omega, \alpha\) are derived quantities including
transport terms (section 4.4 gives the corrected Newton trial).

---

## 2. Dry mass authority (ADR-003)

### 2.1 Authority

ANYsolver owns dry mass \(m\), center of gravity \(c\) (body axes) and
the full inertia tensor \(I_G\) about the CG. Capytaine's automatically
estimated inertia is never used (adopted plan 6.3). `anymarine.domain.Body`
stores scalar `mass` and `reference_position` plus the opt-in explicit CG/inertia
record implemented under parent schema authority (docs/RIGID_BODY_INPUTS.md).
Nonlinear integration and W4 qualification remain pending.

### 2.2 Exact world-frame 6x6 dry mass matrix

With the CG vector and inertia carried to world axes by the pose
(\(R = \exp([\theta]^\wedge)R_c\)):

$$
c = R\,c_{body}, \qquad
I_c = R\,I_{c,body}\,R^{T}, \qquad
I_O = I_c + m\big((c \cdot c)I - c\,c^{T}\big),
$$
$$
M = \begin{bmatrix} mI & -mS(c) \\ mS(c) & I_O \end{bmatrix},
\qquad
g = \begin{bmatrix} m\,\omega \times (\omega \times c) \\
\omega \times (I_O\,\omega) \end{bmatrix},
\qquad \nu = (v_O, \omega).
$$

Verified: \(I_O\) composition 2.4e-16; kinetic energy
\(T = \tfrac12 \nu^{T} M \nu\) — **not** \(\tfrac12 \dot q^{T} M
\dot q\) without \(B\) — agrees with
\(\tfrac12 m\|v_G\|^2 + \tfrac12 \omega^{T} I_c\,\omega\) and with
\(\tfrac12 \dot q^{T} B^{T} M B \dot q\) to 3.1e-16. The gyroscopic
vector \(g\) is exact: the instantaneous power identity
\(\nu^{T}(M\dot\nu + g) = \mathrm{d}T/\mathrm{d}t\) holds to 8.1e-10
(the \(g\) terms absorb the \(\dot M\) power; omitting them breaks the
identity). Reference-point shift with \(d = O' - O\) follows the ANYhydro
convention `shift_matrix` (verified 1.8e-16 in worker-01, retained):
\(M_{O'} = T_d^{T} M_O T_d\), \(F_{O'} = T_d^{T} F_O\),
\(T_d = [[I, S(d)],[0, I]]\).

### 2.3 Inertial internal covector (no negative sign)

The inertial **internal** covector in chart coordinates is

$$
Q_{int,inertial} = B^{T}\big(M\,\dot\nu + g\big), \qquad
\dot\nu = B\,\ddot q + \dot B\,\dot q,
$$

with **no negative sign here**: the residual is external minus internal
(section 4.1), so the dry force appears on the internal side with a
positive sign, exactly like `marine_linear`'s `f_dry`. The chart
acceleration Jacobian (effective dry mass) is \(B^{T} M B\), symmetric
(1.1e-15). Verified: covector power
\(\dot q^{T} Q_{int,inertial} = \nu^{T}(M\dot\nu+g)\) to 2.3e-15.
World CG acceleration includes both \(\alpha \times c\) and the
centripetal term:
$$
a_C = a_O + \alpha \times c + \omega \times (\omega \times c)
\quad \text{(verified 1.5e-10 vs path derivative)};
$$
body-frame translational derivatives need the same transport (they are not
bare differences of body-frame velocities).

---

## 3. Point kinematics, Jacobian and virtual work

For an attachment point with body-fixed vector \(\rho\) (from \(O\)),
\(x = r + R\rho\). With \(J_l = J_l(\theta)\), \(S_x = [R\rho]^\wedge\):

$$
J = \begin{bmatrix} I & -S_x J_l \end{bmatrix}, \qquad
\delta x = J\,\delta q + O(\|\delta q\|^2), \qquad
v = J\,\dot q = \dot r + \omega \times R\rho,
$$
$$
a = J\,\ddot q + h(q, \dot q), \qquad
h = \dot J\,\dot q \ \text{(geometric acceleration bias, mandatory)}.
$$

Verified (worker-01 check-05, retained): \(J\) vs finite differences
1.5e-10 (attachment FD tolerance \(10^{-8}\) relative, absolute floor
\(10^{-12}\)); \(v = J\dot q\) to 1.4e-16; the bias \(h\) matches
the path derivative of \(J\dot q\) to 6.4e-15 in the corrected evidence
(bias-omission residual equals \(J^{T}M_a h\) exactly).

Virtual work / covector force map (point force \(f\) at \(x\), moment
about \(O\): \(m_O = R\rho \times f\)):

$$
Q = J^{T} f = \begin{bmatrix} f \\ J_l^{T}\,m_O \end{bmatrix},
\qquad \delta q^{T} Q = f^{T} \delta x + O(\|\delta q\|^2).
$$

**The chart-covector map is \(Q_\theta = J_l^{T} m_O\), not \(m_O\)
itself** — exact, and critical for hydrostatics (section 6). Distributed
loads (Morison, line contact): \(Q = \int J(s)^{T} f(s)\,ds\) (adopted
plan 8.3/8.5); member-between-bodies shape functions map through both
endpoint Jacobians.

---

## 4. Residual contract (ADR-011)

### 4.1 Sign convention (matches existing public solver)

$$
\mathcal{r}(t, q, \dot q, \ddot q) =
Q_{ext}(t, q, \dot q, \ddot q) - Q_{int}(q, \dot q, \ddot q, \text{history}),
$$

identical in sign and meaning to `marine_linear`'s
`residual[n] = forces[n] - (f_dry + f_added + f_damping + f_stiffness +
f_memory)`. Equilibrium: \(\mathcal{r} = 0\). Legacy structural and
linear semantics are unchanged; no existing solver file is edited.

### 4.2 Derivative meanings (corrected)

The Newton tangents are **total derivatives of the residual**:

$$
K = -\frac{\partial \mathcal{r}}{\partial q}, \qquad
C = -\frac{\partial \mathcal{r}}{\partial \dot q}, \qquad
M_a = -\frac{\partial \mathcal{r}}{\partial \ddot q}.
$$

These include **external follower derivatives, inertial geometry and
transport derivatives**: \(K\) is **NOT simply \(\partial Q_{int}/\partial q\)
generally** (verified counterexample: a body-aligned thrust follower has
\(\|\partial Q_{ext}/\partial\theta\| = 2.62\), and
\(-\partial\mathcal{r}/\partial\theta =
\partial Q_{int}/\partial\theta - \partial Q_{ext}/\partial\theta\)
to 2.1e-10). All derivatives are chart derivatives (connection terms
included); they are not bare wrench Jacobians (section 6.3). Mass-route
symmetry is **declared per contribution**: dry \(B^{T}MB\), BEM
\(A\), and projected-Morison \(J^{T}M_aJ\) forms are symmetric
(8.9e-16); generic mass-derivative symmetry cannot be universally assumed
— a contribution must either declare a symmetric mass form or supply the
full tangent.

### 4.3 Acceleration-dependent forces: assembled ONCE (corrected signs)

The Morison structural force contains \(-M_a^{point}\,a_s\) with
\(a_s = J\ddot q + h\). Two equivalent routes:

- **external route:** \(Q_{ext}\) contains the full
  \(J^{T}M_a(a_f - a_s)\), i.e. includes \(-J^{T}M_aJ\,\ddot q\);
- **internal route:** \(Q_{ext}\) keeps only the fluid part
  \(J^{T}M_a a_f\) and \(Q_{int}\) gains \(+J^{T}M_a a_s\)
  (**internal +\(M_a\ddot q\)**).

Verified: the two routes give identical residuals to 1.9e-16. The original
draft's "**\(-M_a\) inside \(F_{int}\)**" is **wrong**: with internal
\(-J^{T}M_aJ\ddot q\) the residual error is
\(2\,J^{T}M_aJ\ddot q + J^{T}M_a h\) (matched to 7.1e-16). The
geometric acceleration bias \(h\) enters **ONCE** (omission error
\(J^{T}M_a h\), matched to 6.4e-15). No contribution may appear in both
routes (double-assembly refusal, section 11).

### 4.4 Newton trial on the additive chart (fixed step; HHT pending)

For fixed-step Newmark (\(\beta = 1/4, \gamma = 1/2\), matching
`marine_linear`) on the additive chart, the trial state and Newton system
are

$$
q_{trial} = q_{pred} + \beta\,\Delta t^{2}\,a, \qquad
\dot q_{trial} = v_{pred} + \gamma\,\Delta t\,a,
$$
$$
\mathcal{J} = -\Big[M_a + \gamma\,\Delta t\,C_v +
\beta\,\Delta t^{2}\,K_q\Big], \qquad
\mathcal{J}\,\delta a = -\mathcal{r}(q_{trial}, \dot q_{trial}, a),
$$

with the update \(a \leftarrow a + \delta a\). Equivalently, defining
the **sign-reversed effective operator**
\(E = M_a + \gamma\,\Delta t\,C_v + \beta\,\Delta t^{2}\,K_q = -\mathcal{J}\),
the trial solves
$$
E\,\delta a = \mathcal{r}(q_{trial}, \dot q_{trial}, a), \qquad
a \leftarrow a + \delta a,
$$
with the **full residual reevaluated** at each Newton iterate (including
all transport/bias terms). \(E\) is called sign-reversed only because
\(\mathcal{J} = -E\); **no positive-definiteness claim is made for
\(E\)** — definiteness is a property of the assembled tangents, not of
this sign convention. The radiation current-step term is included in
\(C_v\) **OR** kept as an explicit split — **NEVER both** (the existing
`marine_linear` closed-form RHS with `kernel[0]` and the
\(\Delta t/2\) split is a **special case** of this nonlinear formula,
not the formula itself). **HHT-alpha is pending**: its shifted evaluation
points change where transport terms and geometric stiffness are evaluated;
admission requires an explicit derivation and independent review. **No
claim is made that the current integrator already admits new
contributors.** No trajectory demonstration is part of this contract's
evidence (excluded by scope).

---

## 5. Hydrodynamic coordinate map (ADR-011) — exact pose map

### 5.1 Map (adopted: exact pose -> equilibrium, not constant-offset)

Mechanical pose \((r, R)\) maps to equilibrium-frame hydrodynamic
coordinates \(h\) (per body; \(r_0, R_0\) the equilibrium pose,
\(\rho_H\) the hydro reference point in body axes, \(E_0\) the
equilibrium axes):

$$
h = \begin{bmatrix} E_0^{T}(r + R\rho_H - r_0 - R_0\rho_H) \\
\log(E_0^{T} R R_0^{T} E_0) \end{bmatrix},
\qquad
H = \begin{bmatrix} E_0^{T} & -E_0^{T} S(R\rho_H) \\
0 & J_l(h_{rot})^{-1} E_0^{T} \end{bmatrix},
$$
$$
\dot h = H\,\nu, \qquad
\ddot h = H\,\dot\nu + \dot H\,\nu, \qquad
Q_{world} = H^{T} Q_h, \qquad
Q_{chart} = B^{T} H^{T} Q_h.
$$

Verified: \(\dot h = H\nu\) 3.5e-11; \(\ddot h = H\dot\nu +
\dot H\nu\) 2.3e-9; work conjugacy
\(\dot q^{T}Q_{chart} = \dot h^{T}Q_h\) 4.9e-11; infinitesimal virtual
work \(Q_h^{T}\delta h - \delta q^{T}Q_{chart} = O(\|\delta q\|^2)\)
(ratio 0.0033 of the declared second-order bound). Parent01 independently
verified \(H\) by finite differences and infinitesimal work.

### 5.2 Acceleration/mass/bias composition and crossbody blocks

With \(\dot\nu = B\ddot q + \dot B\dot q\):

$$
\ddot h = H B\,\ddot q + (H\dot B + \dot H B)\,\dot q,
\qquad
M_{chart} = B^{T}H^{T}A_h H B, \qquad
\text{bias} = B^{T}H^{T}A_h (H\dot B + \dot H B)\dot q.
$$

Verified at 12 DOF (two bodies, full crossbody \(A_h\) blocks):
\(Q_{chart}(\ddot q) - Q_{chart}(0) = M_{chart}\ddot q\) to 7.3e-16,
bias composition 1.5e-16, \(M_{chart}\) symmetric 3.6e-15. Crossbody
coupling is preserved: \(H\) is block-diagonal per body, and the full
\(12\times12\) \(A_h\) (including cross-body blocks) maps through it
unchanged.

### 5.3 Ownership, prerequisites and limits (explicit)

- **General-axes coefficient transformations are ANYhydro-owned** (the
  frame maps of section 5.1 are the contract they must satisfy); the
  **consumer nonlinear mechanical pullback is ANYsolver-owned**. The
  public `anyhydro.rotate_axes`/`reframe_dataset` now provide fixed equilibrium-linear coefficient maps; source-axis reconciliation
  remains an **explicit prerequisite** for any model whose source axes
  differ from the mechanical axes.
- The exact map is **kinematics ONLY**; it never qualifies large-excursion
  BEM. The admission envelope (rotation magnitude, excursion per body,
  per case) must be **case/source validated**; unknown envelope means
  **unadmitted** — refusal with an actionable diagnostic, never default
  success. No universal qualified motion bound is invented.
- The principal branch of \(\log(E_0^{T}RR_0^{T}E_0)\) is guarded
  (section 1.2); near-seam poses are refused, not wrapped.
- Approximations (linearized coefficients about equilibrium) are labelled
  explicitly in results and provenance.

---

## 6. Hydrostatic ownership and composition (ADR-004)

### 6.1 Ownership (current adapter preserved)

- The current `capytaine_backend` uses `mass=0.0`, per-sub-body
  hydrostatics and declares `HYDROSTATIC_NOTE` (buoyancy-only). This is
  correct and preserved; **the current adapter does not double-count
  gravity** and no statement may claim it does.
- Mechanics (ANYsolver) owns dry gravity: both the equilibrium wrench and
  the incremental restoring. The BEM source owns the equilibrium buoyancy
  wrench and the incremental buoyancy stiffness (buoyancy-only \(C\)).
  One buoyancy owner per wetted entity (section 11).

### 6.2 FULL vs BUOYANCY_ONLY composition (exact, source-read)

Capytaine 3.0.0's rigid-branch hydrostatics (installed source, READ only)
includes mass/CG terms in standard roll/pitch stiffness. With
\(W = mg\), \(c = CG - rc\), the FULL-minus-BUOYANCY_ONLY difference on
the pairs the source implements is exactly the gravity wrench stiffness
(worker-01 check-05, retained: 1.8e-14 over 16 geometries). Composition
rules:

1. Import BUOYANCY_ONLY \(C\) (current adapter). Mechanics adds gravity
   once as a chart-covector contribution (section 8). No double gravity.
2. If a FULL \(C\) is ever imported, the schema must record the variant
   and the source-declared mass/CG/bases; recover buoyancy-only by
   subtracting the gravity wrench terms using that exact decomposition —
   **or** assign gravity ownership to the reference only. No invented
   buoyancy baseline/CB, no arbitrary C swap, no silent asymmetry
   correction.

### 6.3 General baseline-wrench / chart conversion (restriction REJECTED)

The first draft's recommended restriction — hydrodynamic rotation center
on the equilibrium vertical through CG and CB — is **REJECTED**: it is not
generally simultaneously satisfiable and it reduces the planned mechanics.
A **general baseline-wrench/chart conversion** is required instead.
**Domain of the closed forms (corrected):** they hold at the CHART
ORIGIN \(\theta_0 = 0\) of the additive chart
\(R(\theta) = \mathrm{Exp}(\theta)\,R_c\), with \(c = R_c\,c_{body}\)
the CG offset expressed in WORLD coordinates. In this local chart the
gravity moment is
\(m(\theta) = -W\,\big[\mathrm{Exp}(\theta)\,c\big]\times e_z\) — the
ROTATED vector \(\mathrm{Exp}(\theta)\,c\) crossed with \(e_z\), NOT
\(-W\,(Rc)\times e_z\) with \(c\) already world. The closed forms are
NOT general at an arbitrary baseline \(\theta_0\):

$$
\tau_0 = -W\,c \times e_z, \qquad
K_{physical} = -W\,(c_z I - c\,e_z^{T}) \quad
(\text{rollYaw entry} = +W\,c_x),
$$
$$
K_{chart} = K_{physical} - \tfrac12 S(\tau_0),
$$

where \(K_{physical}\) is the wrench derivative
(\(-\partial m/\partial\theta\) at \(\theta = 0\)) and
\(K_{chart} = -\partial(J_l^{T}m)/\partial\theta\) is the symmetric
potential Hessian \(\nabla^2_{\theta}\big(W\,e_z^{T}
\mathrm{Exp}(\theta)\,c\big)\) evaluated at \(\theta = 0\) — with
\(c\) already WORLD, the rotation part of the potential is
\(W\,e_z^{T}\mathrm{Exp}(\theta)\,c\); writing
\(W\,e_z^{T}R(\theta)c\) with \(R(\theta) = \mathrm{Exp}(\theta)R_c\)
would double-rotate.
Verified: \(K_{physical}\) formula vs wrench derivative 9.5e-11;
\(K_{chart} = K_{physical} - \tfrac12 S(\tau_0)\) vs covector
derivative 1.5e-10 and vs the FD potential Hessian 1.4e-7
(second-difference, declared); \(K_{chart}\) symmetric to 0.0.
**Symmetrization note (corrected):** for this gravity-only example,
blind symmetrization \(\tfrac12(K_{physical}+K_{physical}^{T})\) gives
the same Hessian (verified 0.0) — the earlier draft's claim that
symmetrization fails is withdrawn — but blind symmetrization of a
**general imported \(C\)** remains unjustified: correctness comes from
the explicit connection conversion, not from averaging.

**General composition rules (explicit; all baseline/frame terms
included):**

- For a generic generalized force \(Q(q) = D(q)^{T}F(q)\) (covector
  pullback through \(D(q)\)), each column is
  \(\partial Q/\partial q_j = (\partial D^{T}/\partial q_j)\,F +
  D^{T}\,\partial F/\partial q_j\), and the chart stiffness is
  \(K_{chart} = -\partial Q/\partial q\) — the baseline-wrench,
  frame-rotation and geometry terms are ALL included; none may be
  dropped.
- If the source force \(F\) is work-conjugate to source coordinates
  \(h\) with \(\partial F/\partial h = -K_s\), and
  \(D = \partial h/\partial q\), then
  \(K_{chart} = D^{T}K_sD -
  \mathrm{columns}\big[(\partial D^{T}/\partial q_j)\,F_0\big]\)
  with \(F_0\) the baseline source force.
- A **physical-wrench source** (forces/moments in a physical frame)
  must use its **declared physical rate map** for the conversion; an
  assumed \(h\)-gradient must NOT be substituted for it.

**Contract rules:**

- The equation-of-motion stiffness is the **covector derivative**
  (\(K_{chart}\)); a naive C swap treating wrench-convention entries as
  chart stiffness is wrong whenever baseline gravity/buoyancy/preload
  moments about the reference are nonzero.
- The source **force-basis, role and baseline wrench** are stored with the
  imported coefficients, and explicit derivatives are converted with the
  connection term (\(-\tfrac12 S(\tau_0)\) at the baseline for the
  rotational block; the general conversion is the covector derivative).
- Equilibrium buoyancy/gravity/preload wrenches are stored separately
  from incremental stiffness (adopted plan 6.3); the baseline wrench
  enters the residual at \(q = q_{eq}\) and must balance to solver
  tolerance before dynamics; incremental stiffness enters the tangent.
- **Admission limit (explicit):** where a source's baseline wrench,
  force bases or derivative semantics are UNKNOWN, every affected
  future 6-DOF composition is **UNADMITTED** until the source-owned
  metadata/derivatives resolve them. There is NO blanket
  "imported as-is" bypass for such terms, and no silent mirroring,
  symmetrization or rotation-center restriction may substitute for the
  explicit conversion. The existing admitted linear 4-DOF route is
  unchanged by this rule.

### 6.4 Observed source asymmetry (flagged, not adjudicated)

Capytaine 3.0.0's rigid branch sets (Yaw,Roll) and (Yaw,Pitch) to 0 while
(Roll,Yaw)/(Pitch,Yaw) carry the full gravity and waterplane coupling
(order-dependent `elif` chain; no symmetrization). Treatment (mirror,
correct, or restrict) is RESERVED to parent; the contract conversion of
6.3 is the candidate recommendation for gravity; waterplane roll-yaw terms
remain source-owned and are imported as-is under the buoyancy-only rule
**for the existing admitted linear 4-DOF route only** — future 6-DOF
composition involving these terms is UNADMITTED while their
baseline/bases/derivative semantics are unknown (6.3 admission limit;
no blanket as-is bypass).

---

## 7. Morison loading

### 7.1 Model (first release)

For a circular member normal to its axis \(t\) (projection
\(P = I - t t^{T}\)), per unit length, combined wave+current fluid
kinematics \(u, \dot u\), structure kinematics \(v, a\):

$$
f_M = \rho A\,P\dot u
+ \rho C_A A\,P(\dot u - a)
+ \tfrac12 \rho C_D D\,\|P(u - v)\|\,P(u - v).
$$

Components stored separately: Froude-Krylov/inertia, added mass, drag,
buoyancy (owner-governed), and a **declared wave/current drag attribution
that sums exactly to the assembled drag including the nonlinear
interaction** (verified: interaction term 1.89 in the candidate case;
diagnostic split sums to the assembled force to 0.0). Current is never a
second drag force.

### 7.2 Fluid acceleration convention (first linear-wave/uniform-current)

\(\dot u(x,t) = \partial u/\partial t\) at the **fixed spatial point**
\(x\) — the Airy-wave local acceleration; the steady uniform current has
**local acceleration 0**. This explicitly **neglects the fluid material
convective acceleration** \(D_f/Dt = \partial_t u + (u \cdot \nabla)u\),
which is a different quantity from both the Eulerian \(\partial_t u\)
and the **member-path derivative** \(\partial_t u + (v_P \cdot \nabla)u\)
(verified: all three differ; material vs member-path max difference 1.88
in the candidate field). These are not to be conflated, and no silent
current-advection model extension is permitted (any such extension is a
parent decision).

### 7.3 Drag power signs (corrected)

The **relative dissipation is always non-positive**:
\(f_D \cdot (v - u) \le 0\) (16/16 random cases). The **structure
power \(f_D \cdot v\) CAN BE POSITIVE** under wave/current — the first
draft's claim that drag power on the structure is always non-positive is
**withdrawn**. Explicit counterexample (verified):
\(u = (2,0,0)\), \(v = (1,0,0)\): \(f_D \cdot v = +1\) while
\(f_D \cdot (v-u) = -1\) (3/16 random cases had positive structure
power). Energy diagnostics must use the relative dissipation, never the
structure power alone.

### 7.4 Assembly ONCE and ownership

The added-mass term \(-\rho C_A A\,P\,a\) is acceleration-dependent
and follows section 4.3 with the corrected signs: external
\(-M_a\ddot q\) or internal \(+M_a\ddot q\), never both, never at a
predictor, bias \(h\) once. Duplicate inertia (BEM + full Morison on the
same component), duplicate drag (full Morison + drag correction), and
duplicate buoyancy are refused (section 11 table, verified 12/12 in
worker-01, retained).

### 7.5 Prescribed force model vs variational kinetic energy (corrected)

The adopted Morison model is a **prescribed force law** evaluated at
current kinematics. For a member whose axis rotates, it is **not a generic
conservative kinetic-energy model**. Clean counterexample (parent01,
verified exactly): axis \(t = (1,0,0)\), \(\dot t = (0,1,0)\),
\(v = (1,2,0)\), \(a_P = 0\), \(m_a = 1\): the prescribed force
\(m_a P(a_f - a_P) = 0\) does work at rate **0**, while the variational
kinetic energy \(T_f = \tfrac12 m_a\|P(u - v)\|^2\) changes at
**\(-2\) W** — the two models differ by
\(m_a (Pv) \cdot (\dot P v)\), which is nonzero for rotating axes.
**Contract rules:** the prescribed force model is the admitted model; no
generic variable-axis energy/free-decay acceptance is claimed; an energy
test that fails must NOT be "fixed" by silently adding variational
force/moment terms — that is different physics and requires a parent
decision. The physical model and future acceptance limits stay explicit.

---

## 8. Hydrostatic/gravity covector conversion (summary; details 6.3)

The chart-covector map \(Q_\theta = J_l^{T} m\) (section 3) and the
baseline conversion \(K_{chart} = K_{physical} - \tfrac12 S(\tau_0)\)
(section 6.3) are the two faces of one rule: **imported wrench-space
quantities are converted to chart covectors with the explicit connection
term; nothing is swapped or symmetrized blindly.** The conversion applies
to gravity (mechanics-owned), buoyancy (source-owned incremental
stiffness, imported as-is under 6.2 rule 1) and any declared preload.
The closed form \(K_{chart} = K_{physical} - \tfrac12 S(\tau_0)\) is the
chart-origin (\(\theta_0 = 0\), world \(c = R_c\,c_{body}\)) special
case of the general rules of 6.3
(\(K_{chart} = -\partial Q/\partial q\) with all baseline/frame terms
included); at any other baseline the general rules apply. Buoyancy
"as-is" import covers the existing admitted linear 4-DOF route only;
future 6-DOF composition with unknown source baseline/bases/derivative
semantics is UNADMITTED (6.3 admission limit).

---

## 9. Histories: prepare / trial / commit / rollback / cancel / failure

The generic contribution protocol (adopted plan 7.4) is governed as
follows (corrected: cancellation is NOT restricted to accepted-step
boundaries):

- **prepare_step(state, step):** read-only view of the last committed
  state; no history mutation.
- **evaluate_trial(t, q, qd, qdd):** pure with respect to committed
  history; may cache trial-local data keyed by the trial token. Returns
  force, tangents, mass contribution, diagnostics, component breakdown.
- **Cancellation may be polled during trial operations while
  rollback-safe** — mid-trial cancellation is a supported, safe event
  (verified 8/8 logic cases); accepted-step-only polling is NOT imposed as
  a universal contract.
- **commit_step(converged_state):** the ONLY operation that appends to
  committed history, by the single history owner. Only a successful
  accepted step commits; rejected/late/diverged/cancelled states are
  never committed (late commit after cancel refused; double commit
  refused; non-owner commit refused).
- **rollback/cancel/failure:** committed (accepted) history is preserved
  and returned; incomplete trial logs are retained as evidence, not
  discarded; trial inputs are immutable; failure evidence (non-finite
  states, singular effective systems) raises typed errors and no non-finite
  state is accepted (matching `marine_linear`).
- **Radiation memory:** initial memory is zero at \(t_0\) (memory at
  rest, matching `marine_linear`) unless an explicit initial-memory
  contract is later adopted (RESERVED); the convolution history buffer is
  commit/rollback-managed like all other history; the current-step kernel
  split (\(\tfrac{\Delta t}{2}K_{r,0}\)) is part of the radiation
  contribution's declared tangent (section 4.4: in \(C_v\) or explicit
  split, never both).

---

## 10. Capability admission/refusal and needed public APIs

### 10.1 Admission rules (verified table, worker-01 retained)

Primary hydrodynamic role per wetted entity: `potential_flow`,
`morison_full`, `none`; optional separate `drag_correction`. Refusals:
`duplicate_inertia_bem_plus_morison_full`,
`duplicate_drag_full_morison_plus_correction`,
`morison_full_on_bem_component`, `duplicate_buoyancy_owner`. Members
omitted from the BEM mesh may receive full Morison; potential-flow +
documented drag correction is permitted; mooring lines are never in the
BEM body.

### 10.2 Needed public APIs (proposed shapes RESERVED to parent)

- **ANYsolver (W4):** `RigidBody6D` (reference origin, CG, full 6x6 dry
  mass or \(m, c, I_G\), orientation state with anchor/rechart events,
  attachment frames); point kinematics/Jacobians with bias; the
  `DynamicContribution` protocol of plan 7.4 with the section 4 tangent
  semantics; marine commit/rollback history with mid-trial cancellation;
  fixed-step enforcement. No Capytaine imports.
- **ANYhydro:** general-axes frame maps (section 5) as the explicit
  prerequisite; hydrostatic variant recording (`BUOYANCY_ONLY` vs `FULL`
  plus source mass/CG/bases and baseline wrench) in the dataset schema and
  provenance.
- **ANYloads (W3):** wave/current/Morison records per plan 4.3; the fluid
  acceleration convention of section 7.2; ownership validation helpers.
- **ANYmarine:** body model extension (CG, inertia) feeding solver-owned
  mass; role assignment UI/persistence honoring section 10.1 refusals.

### 10.3 Required independent future gates (not run here)

- W3: analytical Morison amplitude \(10^{-10}\); relative dissipation
  non-positive; seeded-kinematics identity after serialization.
- W4: work conjugacy \(10^{-10}\); link action-reaction \(10^{-10}\);
  attachment Jacobian FD \(10^{-8}\) relative with declared floors;
  legacy transient baselines unchanged.
- Independent review of this contract before adoption (parent); the
  candidate algebra here is development evidence only.

---

## 11. Counterexample index (corrected evidence; worker-02 check-05)

| # | Wrong route | Observed (corrected evidence) | Contract rule |
|---|---|---|---|
| 1 | Additive Exp identity treated as finite-exact | residual 6.5e-12, quadratic decay 0.25 | 1.2 first-order + O(d^2) only |
| 2 | Left retraction confused with additive increment | first-order difference 2.9e-6 | 1.2 mapped by Jl |
| 3 | Log(R) treated as a rechart | branch ambiguity at pi (3.2 -> 3.083) | 1.2 new anchor + transported rates/accelerations |
| 4 | Jl assumed singular at pi | det Jl(pi ez) = 4/pi^2; singular only at 2pi | 1.2 singularity structure |
| 5 | theta_dot as omega / theta_ddot as alpha | transport identities 1.4e-10 | 1.3/2.3 B, Jl, Jldot mandatory |
| 6 | T = .5 qdot^T M qdot without B | forms differ; with B agree to 3.1e-16 | 2.2 T = .5 nu^T M nu |
| 7 | Omitting g (gyro/centripetal) | power identity breaks (g absorbs dM/dt) | 2.2/2.3 g mandatory |
| 8 | a = J qddot (no bias) | omission error = J^T Ma h (6.4e-15) | 3/4.3 bias once |
| 9 | '-Ma inside Fint' | residual error 2 J^T Ma J qddot + J^T Ma h | 4.3 internal +Ma qddot |
| 10 | K = dQint/dq (follower ignored) | follower derivative 2.62 nonzero | 4.2 total residual derivative |
| 11 | Wrench stiffness as chart stiffness | Kchart = Kphysical - .5 S(tau0) (1.5e-10) | 6.3 connection conversion |
| 12 | Vertical-center restriction | rejected: not generally possible | 6.3 general conversion |
| 13 | Drag structure power claimed <= 0 | u=(2,0,0), v=(1,0,0): fD.v = +1 | 7.3 relative dissipation only |
| 14 | Df/Dt conflated with member-path derivative | max difference 1.88 | 7.2 Eulerian partial_t convention |
| 15 | Variational KE "fix" for rotating-axis energy test | force work 0 vs dT/dt = -2 W | 7.5 prescribed force model |
| 16 | Accepted-step-only cancellation polling | mid-trial cancel rollback-safe 8/8 | 9 poll during trial ops |

## 12. Unresolved questions for parent

1. Rechart event semantics and branch-guard threshold (implementation
   RESERVED; no pi/2 bound invented — 1.2).
2. HHT-alpha admission after transport linearization at shifted evaluation
   points (4.4).
3. Treatment of Capytaine's asymmetric roll/yaw hydrostatic convention
   (6.4); the connection conversion of 6.3 is the candidate
   recommendation for gravity.
4. FULL-import policy: exact source mass/CG/bases decomposition vs
   reference-only gravity ownership (6.2).
5. Initial radiation memory beyond the at-rest convention (9).
6. Public API names/signatures (10.2) and the `anymarine.domain.Body`
   CG/inertia schema extension.
7. Ownership placement is resolved in section13: ANYhydro owns coefficient frame maps; ANYsolver owns consumer nonlinear pullbacks. Public signatures and integration remain pending.

## 13. Parent adoption and remaining admission obligations

The parent reviewed the corrected equations and independently verified the
geometry, work, dry inertia/power, hydrodynamic acceleration, gravity derivative,
Morison acceleration tangent and nonlinear Newton chain rule. Core equations,
force-based Morison model, ownership, history isolation and explicit refusal rules
are adopted. This is a scientific contract prerequisite; W3/W4 implementation,
production cancellation and qualification gates are not passed by prototypes.

ADR-004 is authoritative for baseline/source composition. Its gravity closed
form applies only at theta=0 with world c=R_anchor*c_body; the general derivative
is required elsewhere. Source-specific bases/baselines/derivatives must be resolved
before affected six-DOF admission. General coefficient frame maps belong to
ANYhydro; mechanical nonlinear pullbacks and inertial assembly belong to ANYsolver.

Public signatures, CG/inertia persistence, integrator update and accepted-step
rechart/history transport, HHT, source-specific FULL composition and case/source
motion envelopes remain prospective implementation/admission obligations.
Existing four-DOF solver and all production files are unchanged. Unknown inputs
or envelopes do not become admitted because the kinematic map is exact.

Evidence: parent-01 and parent-02 artifacts under the ANYmarine evidence workspace;
12 instantaneous cases in each, DEVELOPMENT_ALGEBRA_ONLY. Parent-02 maximum
hydro acceleration error5.706443688270121e-10, gravity derivative6.383992139502503e-11,
Morison acceleration tangent4.0520273616474395e-12, Newton chain rule2.0448307050593237e-12.
No trajectories, BEM solves or physical campaigns were executed for these checks.
Worker-02's history cases are toy protocol evidence, not production-path tests.
Production prerequisite delta 2026-10-09: ANYsolver marine_rigid_v1 implements
instantaneous dry mass/gyro/point/chart/gravity mechanics (sections1-3), independently
reviewed against this adopted core. Optional BodyMassProperties persistence and
lazy Marine shared-service/Qt bridge implemented. This does not implement dynamic
contribution K/C tangents, accepted-step rechart/history or nonlinear integration.
ANYhydro fixed equilibrium-linear general coefficient-frame maps are implemented;
source finite-pose hydrostatic semantics, FULL composition and motion envelopes
remain separate admission gates. Scientific W3/W4/W8-W10 qualification unchanged.


