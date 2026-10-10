# Native HAMS fields through Qt and CLI (development)

Load the producing geometry first. **Jobs > Run configured HAMS fields...**
selects an explicit JSON configuration. The app owns one QProcess queue; the
worker supervises the native producer through public ANYhydro interfaces. Only a
validated successful native receipt, canonical fields and reopened portable
bundle produce a completed job. The fields then appear in Waves if the producing
project still matches and no body replay is selected. Otherwise the saved bundle
is retained without display. Queued/running cancellation, crashes, deadlines,
missing sidecars and active-job application closure preserve logs and partial
artifacts. HAMS wave bundles are not mechanics ResultRecords or qualification.

The current corrected producer available on this workstation is a Linux ELF.
No native Windows Fortran compiler was found in PATH or the checked MSYS/Intel/
CMake locations. Actual Windows calculations require an explicitly trusted,
source-bound Windows build. There is no implicit WSL, shell or command-prefix
fallback. Native Linux workers require the public ANYhydro supervision contract
and available runtime prerequisites before queueing and again in the worker.
Older same-version backends without that handshake refuse. Saved field
visualization stays usable. Installed consumer checks use stored-output Python
fixtures; actual native-producer integration remains separately required.

Configuration format is **anymarine-hams-field-job-v1**, with exactly:

- producer: identity from public HamsProducerBuild.identity(), plus canonical
  absolute executable_path. Gravity-consistent finite source profile only.
- request: omegas (rad/s), wave_directions (rad), water_depth (positive metres or
  "infinite"), rho (kg/m3), gravity (m/s2). No zero/Ainf or backend-settings override.
- mechanics: body-ID map of explicit HamsMechanicalInput fields:
  center_of_gravity, mass, linear_damping, quadratic_damping,
  hydrostatic_restoring, external_restoring (complete 6x6 matrices).
- center_of_buoyancy: exact body-ID map of three WORLD coordinates in metres.
- field_points: ordered exterior WORLD [x,y,z], with at least one z=0 surface
  point. Submerged pressure remains separate from elevation.
- execution_limits: complete HamsExecutionLimits.identity(); at most60 seconds,
  one thread, two bodies,128 frequencies,36 headings,20000 panels,4096 points.
- irregular_frequency_removal and allow_mechanical_rounding: explicit booleans.

Use public ANYhydro/ANYfileIO constructors to form these declarations. No source
identity or executable is inferred. Numerical lids must agree with the declared
irregular-frequency policy. Geometry-only projects are admitted; sheets, omitted
viscous corrections and solver-workflow inputs refuse. The hull winding check
joins exact vertices, verifies closed opposite edges and positive signed volume
per component, using the reversed numerical lid only as a verification cap. It
changes no submitted geometry or buoyancy ownership. Self-intersection and
physical hydrodynamic accuracy remain separate obligations.

The CLI uses the same service and isolated worker:

```powershell
anymarine inspect-hams-config --config C:\work\hams-config.json
anymarine run-hams-fields --config C:\work\hams-config.json --workdir C:\work\new-hams-job
```

Optional --project selects a saved ANYmarine project directory. Omission uses the
bundled HAMS producing geometry. Inspection diagnoses executable identity and native-platform availability without
execution; producer_available does not establish complete input-deck readiness. Run requires a new exclusive evidence directory and never overwrites
it. It reports the retained wave-bundle path; open it through File > Open stored
HAMS wave bundle. A changed saved project reports stale and is not overwritten.
The executable's source/build declarations are explicit trust inputs, not an
independent compiler attestation. This route grants no scientific qualification,
corrected Ainf admission, W5/W7 pass or release. Actual-producer integration and
Linux hardware desktop acceptance remain required beyond lifecycle fixtures.


Installed Linux development checks use Ubuntu24.04/WSL2, Python3.12.3 and
PySide6==6.10.1 with exact dependency versions recorded in the task evidence.
QProcess completion/failure, direct worker/CLI execution, forced supervisor death,
queued/running cancellation, application closure, stale results and authoritative
sidecar reopening are covered. These checks do not run a native HAMS calculation.
Only exclusive child reaping and trusted same-group descendants are supported;
see ANYhydro's Linux process ownership documentation for the fault exclusions.

GPU-first application settings are unchanged. On the tested WSLg desktop both
Wayland and X11 selected Mesa llvmpipe: hardware acceptance failed and remains
open. A separate real desktop rendering check confirms a visible model and
animated incident/total/prescribed-motion samples against the saved arrays using
that software OpenGL driver. This is fallback evidence, not a hardware GPU pass.
No host/distro graphics configuration is changed. Four saved surface samples are
points; no dense water surface or pressure-as-elevation interpolation is invented.
