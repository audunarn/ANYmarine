# Stored HAMS wave samples

Open **File > Load stored HAMS wave samples** to load the packaged two-body
geometry and saved HAMS fields. This performs no numerical calculation. The
Waves dock selects incident, total (incident plus disturbance), scattered
(total minus incident), or prescribed-motion radiation. Use Play/Pause waves,
amplitude, saved frequency/heading and harmonic phase to inspect the fields.
Radiation selects a target-frame DOF and displacement in metres or radians.
The bodies remain at the geometry which produced the fields; radiation is not
a solved body trajectory. Pressure samples are retained separately and never
rendered as surface elevation.

The default public ANY3dView renderer requests GPU rendering, with documented
software fallback. View > Renderer changes the renderer while retaining the
field source. Editing or replacing the producing model clears the stored view.

This packaged acquisition contains four surface points and four submerged
pressure points. The canvas shows true surface elevation at those four points.
It does not interpolate them into a continuous disturbed-wave surface. The
existing analytical incident-wave and calculated Capytaine modes remain
separate. A dense, validated disturbed surface is still a development objective.
The HAMS hulls are wetted meshes with separate numerical lids, so a legacy
closed-body Capytaine preview can refuse this geometry.

**Save stored HAMS wave bundle...** writes a new directory. Reopen it with
**File > Open stored HAMS wave bundle...**. A bundle has exactly bundle.json,
project.json, coefficients.nc and fields.json. Shared headless services check
file hashes, parent dataset identity, producing hull/lid geometry, body/DOF
ordering, source declarations and frame transformations before displaying data.
Unsafe paths, missing files and changed bindings are refused. This portable
bundle is a visualization artifact, not a producer attestation or completed
mechanics result. Project Save does not incorporate these field bundles; use
the explicit wave-bundle command to preserve them.

The same public service is available without Qt through the CLI:

```powershell
anymarine inspect-wave-bundle --component total --time 1.3
anymarine inspect-wave-bundle --path C:\work\saved-wave-bundle --component radiation --dof-index 3 --displacement 0.01
anymarine inspect-wave-bundle --save-copy C:\work\new-wave-bundle
```

Canonical harmonic convention is exp(-i omega t), with phase exp(+i phase).
Incident/total/scattered elevation scales with wave amplitude in metres.
Radiation elevation scales with prescribed target-frame displacement (m/rad),
without another omega factor. The current source profile is gravity-consistent
finite-frequency HAMS only; its infinite-frequency added mass is not admitted,
and added mass from older source profiles must not be substituted.

All fields, examples and replay are development evidence, unqualified. Physical
W5/W7 comparisons, denser sampling, a corrected Windows producer and full saved
project/qualified GUI acceptance remain open. Stored visualization does not
change those scientific gates.
