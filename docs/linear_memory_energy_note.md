# Radiation memory work of the linear route is non-negative (ADR-015 section 8 (v), slice L6-A)

Status: ACCEPTED by the owner 2026-10-15 as the proof of ADR-015 section 8 (v) in its qualified form (Codex review
L6-A rounds 1-3, session 01a14068-2b0d-7440-ad99-75b0661dedc4). The numerical verification is in
`tests/test_linear_fd.py` (`test_memory_work_*`).

## Setting

`anysolver.marine_linear` integrates

    (M_dry + A_inf) a_n + C v_n + K q_n + m_n = F_n

with trapezoidal Newmark (beta = 1/4, gamma = 1/2). Its radiation memory force is the trapezoidal causal convolution
of the stored kernel samples K_l = K_r(l dt), where each K_l is symmetric (enforced by the solver):

    m_0 = 0,    m_n = dt ( 1/2 K_n v_0 + sum_{j=1}^{n-1} K_{n-j} v_j + 1/2 K_0 v_n )    (n >= 1)

This is the solver's endpoint weighting: half weight at the start of the history and at the current step. Prehistory
is zero.

**Discrete energy identity.** Let E_n = 1/2 v_n^T M_eff v_n + 1/2 q_n^T K q_n with M_eff = M_dry + A_inf, and use the
step averages xbar_n = (x_n + x_{n+1}) / 2. With symmetric M_eff, C and K, the trapezoidal rule gives

    E_{n+1} - E_n = dt vbar_n^T (Fbar_n - C vbar_n - mbar_n),

and hence E_N = E_0 + W_ext - W_C - W_mem. Here W_C = sum dt vbar^T C vbar >= 0 for C positive semi-definite, and

    W_mem(N) = sum_{n=0}^{N-1} dt vbar_n^T mbar_n.

## Claim

If v_0 = 0 (every judged run starts from rest or from a displacement) and the stored kernel is passive in the sense
of ADR-015 section 8 (iv) with exactly positive semi-definite samples, then W_mem(N) >= 0 for every N.
Consequently E_N <= E_0 + W_ext. The admitted round-off tolerance is treated in the section after the proof.

## Proof

1. **Rewrite the memory as one convolution.** With v_0 = 0 the starting-endpoint term vanishes, so for every n:

       m_n = dt sum_{j=0}^{n} h_{n-j} v_j,    h_0 = 1/2 K_0,  h_l = K_l (l >= 1).

   The case n = 0 is consistent: m_0 = 1/2 dt K_0 v_0 = 0.

2. **Express the step sums by s_n = v_n + v_{n+1}.** Shift the second sum by one index; the extra term
   h_{n+1} v_0 is zero because v_0 = 0. This gives

       m_n + m_{n+1} = dt sum_{j=0}^{n} h_{n-j} s_j,

   and therefore

       W_mem(N) = (dt / 4) sum_{n=0}^{N-1} s_n^T (m_n + m_{n+1}) = (dt^2 / 4) sum_{n=0}^{N-1} sum_{j=0}^{n} s_n^T h_{n-j} s_j.

3. **Symmetrise to a two-sided Toeplitz form.** Define g_l = K_|l| for every integer l. Then g_0 = K_0 = 2 h_0, so
   the solver's half weight at the current step exactly cancels the doubling of the diagonal.

   - Each diagonal term is s_n^T h_0 s_n = 1/2 s_n^T g_0 s_n.
   - Each off-diagonal term (n > j) equals 1/2 (s_n^T g_{n-j} s_j + s_j^T g_{j-n} s_n), because K_l is symmetric.

   Hence

       W_mem(N) = (dt^2 / 8) sum_{n, j = 0}^{N-1} s_n^T g_{n-j} s_j.

   This is a finite section of the block Toeplitz quadratic form with symbol

       G(theta) = sum_l g_l e^{i l theta} = K_0 + 2 sum_{l >= 1} K_l cos(l theta).

4. **The symbol is positive semi-definite for a passive kernel.**
   - The continuous kernel is the cosine transform of the damping, K_r(t) = (2/pi) int_0^inf B(w) cos(w t) dw.
     Its two-sided Fourier transform is therefore int K_r(|t|) e^{i w t} dt = 2 B(|w|).
   - (iv)(a)-(b): every sample B(w_i) is positive semi-definite, and the componentwise-linear interpolant of positive
     semi-definite samples is positive semi-definite at every w. With linear-to-zero or explicit-zero low tails and a
     truncated high tail, B(w) is positive semi-definite for all w.
   - (iv)(d): the Bartlett taper (1 - |t|/H)_+ has the non-negative Fejer transform. Multiplying by it in time
     convolves the spectrum with a non-negative kernel, which preserves positive semi-definiteness.
   - (iv)(c): uniform sampling at dt (Poisson summation; the tapered kernel has compact support, so the sum is
     absolutely convergent) gives

         dt G(theta) = sum_m 2 Btilde((theta + 2 pi m) / dt),

     a sum of positive semi-definite matrices, with Btilde the tapered spectrum. So G(theta) is positive
     semi-definite for every theta.

5. **Conclude.** For any finite sequence s_0 .. s_{N-1}, write S(theta) = sum_n s_n e^{-i n theta}. Then

       sum_{n,j} s_n^T g_{n-j} s_j = (1 / 2 pi) int_{-pi}^{pi} S(theta)^H G(theta) S(theta) d theta >= 0.

   Therefore W_mem(N) >= 0.  QED.

## Admission tolerance: a round-off-level perturbation bound

The theorem above assumes exactly positive semi-definite damping samples. Structural admission (ADR-015 section 8
(iii)-(iv)) checks the normalised operators X_hat = S^T X S, where q = S q_hat and S = diag(1 for translations,
1/L for rotations). It admits normalised eigenvalues down to -64 eps ||B_hat|| (and -64 eps ||C_hat||). Such an
operator is not exactly positive semi-definite, and a small negative W_mem is then possible (Codex review L6-A
round 1). The claim for the admitted class is therefore stated, entirely in normalised coordinates, with the
following bound.

**Coordinates.** With v = S v_hat and s = S s_hat:

    s_n^T g_l s_j = s_hat_n^T g_hat_l s_hat_j,    g_hat_l = S^T g_l S.

So the proof above applies verbatim to the normalised kernel, and W_mem is coordinate-free.

**Notation.**
- Let delta_hat = max over all samples i of max(0, -lambda_min(B_hat_i)), so delta_hat <= 64 eps max_i ||B_hat_i||.
- Let omega_max be the top of the stored band.

**Steps.**
1. The piecewise-linear interpolant of the normalised samples, with a linear-to-zero or zero low tail and a
   truncated high tail, satisfies B_hat(w) >= -delta_hat I on [0, omega_max] and B_hat(w) = 0 above it. This holds
   because the negative part of a convex combination is bounded by the same delta_hat.
2. The Bartlett-tapered spectrum is the convolution of B_hat with the normalised non-negative Fejer kernel. That
   kernel integrates to w(0) = 1, so Btilde_hat(w) >= -delta_hat (F * 1_band)(w) I, where 1_band is the indicator of
   the band.
3. Sum the aliases. The sum of F * 1_band over the shifted copies (w + 2 pi m / dt) is at most
   c = ceil(omega_max dt / pi) + 1, the number of copies that overlap. Hence dt G_hat(theta) >= -2 c delta_hat I.
   If the band lies below Nyquist (omega_max dt <= pi), then c <= 2.
4. By Parseval, (1 / 2 pi) int |S_hat|^2 d theta = sum_n |s_hat_n|^2, so

       W_mem(N) >= -(dt / 4) c delta_hat sum_n |s_hat_n|^2,    with delta_hat <= 64 eps max_i ||B_hat_i||.

**External damping.** In the same coordinates, W_C >= -64 eps ||C_hat|| sum_n dt |vbar_hat_n|^2.

**Interpretation.**
- Relative to the normalised radiation-dissipation scale (dt / 4) max ||B_hat|| sum |s_hat_n|^2, the deficit is at
  most 64 c eps, that is about 3e-14 for c <= 2.
- The energy balance of an admitted run is therefore E_N <= E_0 + W_ext + D_N, where

      D_N = (dt / 4) c delta_hat sum_{n<N} |s_hat_n|^2 + 64 eps ||C_hat|| sum_{n<N} dt |vbar_hat_n|^2.

  D_N is a finite-history bound. It grows with the accumulated squared velocity.
- **This is not a uniform-in-time stability theorem for inputs at the edge of the tolerance.** For example,
  admitted damping C = diag(1, -1e-14) with zero memory has an energy-growing mode (poles about 5e-15 +/- 1i).
- For the finite judged runs of ADR-015 (at most 20,001 steps) D_N is a round-off-level quantity.
- ADR-015 section 8 (v) is accepted in this form: exact for exactly positive semi-definite inputs, and up to D_N for
  inputs admitted with the -64 eps tolerance. Stability claims cite it as such. This is an owner acceptance item.

## Scope and limits

- **v_0 = 0 is used.** With a nonzero initial velocity, the starting-endpoint term 1/2 dt K_n v_0 does not cancel in
  step 2 and the claim is not proven. ADR-015 requires judged runs to start from rest or from a displacement.
- **The claim bounds the total energy, not the mechanical energy.** E_n may rise temporarily while the memory
  returns energy it has stored.
  - For exactly positive semi-definite inputs, E_N <= E_0 + W_ext.
  - For inputs admitted within the -64 eps tolerance, E_N <= E_0 + W_ext + D_N (previous section).
- **A rectangular cut is not covered.** The time-window zeroing of the stored kernel is not admitted for judged
  cases: its Dirichlet transform has negative lobes, and G(theta) can lose positive semi-definiteness.
  `test_weights_oracle_nonzero_last_retained_sample` uses such a cut deliberately, with damping that dominates it.
- **The numerical check is a sanity check, not part of the proof.** `linear_fd.memory_symbol_min_eigenvalue`
  evaluates the smallest eigenvalue of dt G(theta) on a dense grid. Equivalently it is 2 Re G_d, the Hermitian part
  of ADR-015's G_d(w) on the discrete frequency circle.

## Numerical verification (tests/test_linear_fd.py)

- **Passive kernels.** Three runs use passive Bartlett-tapered exponential-cosine kernels with positive
  semi-definite amplitude, random forcing, an initial displacement and v_0 = 0. The cumulative W_mem stays at or
  above -1e-12 times the accumulated absolute step work, and the symbol's smallest eigenvalue is non-negative to
  round-off.
- **Discrimination.** The negated (active) kernel has a negative symbol, and a pulse-forced run shows W_mem < 0.
