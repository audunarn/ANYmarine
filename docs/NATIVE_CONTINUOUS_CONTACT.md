# Explicit continuous seabed contact intent

New development examples declare nested `marine_seabed_contact_v2` with positive
`activation_depth_m` in metres. The umbrella mooring schema and output arrays remain
unchanged. ANYmarine transports the input; ANYsolver owns the constitutive law.
The new example uses 0.001 m as uncalibrated development data, not a universal seabed
property. Existing v1 projects remain v1 on load, save and compilation.

For penetration p, v2 ramps damping by min(p/activation_depth_m, 1), with unilateral
normal reaction max(K*p - C*f*v_normal, 0). Elastic storage remains K*p*p/2 and
dissipation includes outgoing clipping. This is an explicit change in constitutive
input near the boundary. It preserves the fixed-step integrator and accepted-history
boundaries; it does not establish arbitrary coupled convergence or physical calibration.

V2 input is rejected before job launch when the installed solver lacks its named
continuous API. Missing/extra fields, nonpositive or nonfinite depth and unknown
versions are rejected. There is no silent v1 migration or step-dependent depth.
Completed results retain their immutable producing input hash and stored arrays;
changing contact intent makes prior results stale.

W6 v4 uses separate prospective reference fixtures at 0.0001 m with an outgoing
clipping case. Historical v1 no-root failures remain evidence, not renamed passes.
Independent scientific assessment and authoritative application replay are required
before W6 acceptance. These changes alone do not qualify marine analysis.
