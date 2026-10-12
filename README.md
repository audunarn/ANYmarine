# ANYmarine

Marine multibody application foundation (W0-W1 slice): domain records, robust
project/results persistence, a headless CLI, and a PySide6 Qt Widgets shell
hosting the public ANY3dView Qt viewer.

## Status and scope

This repository delivers the application foundation and a bounded linear development route:

- Two-body project model with stable body UUIDs, names, reference positions,
  mass properties and equilibrium metadata.
- Checksummed, atomic project persistence with relative sidecars and strict
  path-safety refusals (no absolute paths, traversal or symlink escapes).
- A deterministic **synthetic** two-body replay generator. Results are
  explicitly labelled `kind: "synthetic_unqualified"`. They are **not**
  boundary-element-method output and **not** dynamics integration, and must
  not be used as production numerical evidence.
- Two-body surge/heave dynamics through the public ANYsolver linear API:
  full coupled 4x4 matrices, explicit infinite-frequency added mass, fixed-step
  Newmark integration, direct radiation convolution and a linear link. The
  supplied analytic demo exercises cross-body coupling; its results carry
  `kind: "linear_development"`. This is development evidence, not qualification.
- Isolated job workflow: child processes (`python -m anymarine.worker`) fed a
  plain JSON input snapshot, with cooperative cancellation, bounded
  termination, and no partial success.
- Qt Widgets shell: one QMainWindow, central ANY3dView viewer, docked project
  tree, editable details, jobs/diagnostics and replay slider with a
  first-order painter time-series plot. Project loads reuse the viewer (clear and repopulate it), fits the model
  once the viewport is visible with its final size, offers View > Fit model,
  applies a light workbench theme (adapted from ANYfem, MPL-2.0), and logs the
  effective viewer backend plus renderer errors in the Jobs / Diagnostics
  panel; an unfallbackable renderer failure raises an explicit dialog instead
  of a silent blank canvas.

ANYhydro dataset/kernel import (development, unqualified): a saved project can
import a public ANYhydro neutral dataset plus its radiation kernel through the
same shared service from the CLI or the Qt shell. The import proves the
dataset geometry against the project bodies, admits only the exact ordered
four-DOF selection `[body0::surge, body0::heave, body1::surge,
body1::heave]`, re-checks reconstruction against caller-declared criteria, and
copies the authoritative raw files into the project as content-addressed
sidecars, so a saved project reproduces without the original external files.
The full effective source identity (content hashes, request hash, criteria,
report, kernel settings, wave selection) is frozen into immutable provenance
records covered by the input hash.

## Layout

```
src/anymarine/
  domain.py        Qt-free domain records and validation
  hashing.py       canonical input hashing (stale-result detection)
  synthetic.py     deterministic synthetic replay generator (labelled)
  persistence.py   atomic store, manifests, checksummed sidecars, refusals
  services.py      shared Qt-free application services (CLI and GUI)
  worker.py        isolated job child module (plain JSON in/out)
  cli.py           headless command line interface
  qtapp/           PySide6 shell (optional dependency)
tests/             focused pytest suite
```

## Running

Start `python run_gui.py` from this checkout. For an installed environment,
use `python -m anymarine.gui`; see [installation and launch](docs/INSTALLATION.md). With no `--project` argument,
the desktop opens a bundled two-body example with a stored 20-second synthetic
replay. Press **Play**, scrub the frame slider, or select a channel to inspect
its plot. **File â†’ Load example (two-body replay)** reopens the example.
**Save project** asks for a destination and saves your own editable copy;
the bundled files remain unchanged. `--project <directory>` opens your saved
project instead. This example is synthetic demonstration data, not BEM output
or a dynamics integration and not scientific qualification evidence.
The same saved example can be opened directly from
`src/anymarine/examples/two-body-replay/project.json`.

Load a saved model using **File â†’ Open project...**, selecting its
`project.json`. **File â†’ Load native coupled fluid example** loads two cubes;
**Jobs â†’ Run native mechanical job...** computes its development results.
CAD/mesh-file import is still pending; this menu opens ANYmarine projects.
**File → Load native seabed contact example** displays the floor and three lines.
Run its native mechanical job to replay stored active contact endpoints and plot
penetration/energy diagnostics. The example uses explicit compliant initial
penetration; static equilibrium and W6 qualification remain pending.
**File → Load native wave/current mooring example** loads three lines and two
Morison members sharing an explicit wave/current environment. Run its native
mechanical job for stored node/body replay with incident waves synchronized to
the selected frame. See [native mooring workflow](docs/NATIVE_MOORING_DEVELOPMENT.md).

The default canvas is the public **ANY3dView Qt GPU renderer**, with automatic
software fallback if OpenGL is unavailable. For a black
canvas, use **View â†’ Renderer â†’ Software (compatible)**, then **View â†’ Fit model**
(`Ctrl+F`). Renderer changes preserve the model, replay frame and camera.
OpenGL remains available under **View â†’ Renderer**. To choose it at launch:

```powershell
python run_gui.py --viewer-backend auto
python run_gui.py --viewer-backend software
```

An explicit launch option takes precedence over `ANYMARINE_VIEWER_BACKEND`.
**Help â†’ Loading models...** shows these steps inside the application.

CLI (no Qt required):

```
python -m anymarine.cli new --path <dir> --name "Demo"
python -m anymarine.cli run --project <dir> --duration 20 --dt 0.25
python -m anymarine.cli run-linear --project <dir> --demo
python -m anymarine.cli list-results --project <dir>
python -m anymarine.cli validate --project <dir>
```

Registered linear assessment (development, unqualified): an immutable,
closed-world, hash-bound case is registered BEFORE any rehearsal solve and a
stored linear result is later assessed under it - matched frequency-domain
RAO (full coupled 4x4 impedance), regular-wave steadiness, authoritative
force/Newmark/memory-oracle balance, free-decay energy/work accounting with
passivity bounds, and caller-explicit dt refinement. Hydro-sourced
references use the ACTUAL stored neutral dataset / derived-operator spectrum
sample (never a transform of the TD kernel) and require the actual project
context: the core re-runs the public binding verifier and the public hydro
provenance source proof (including the authoritative recompilation), so
re-hashed bookkeeping over substituted matrices is refused. Free-decay
assessment supports the analytic_exponential rehearsal reference and the
hydro_source reference: the proper hydro free-decay reference, bound to
the actual raw/derived source identities with the same actual-source
proof (derived recompilation; raw re-import of the stored sidecar bytes
through the public pure import route under the original declared
controls), zero stored external forcing, and an explicit NOTMEASURED/NoGo
gate on near-zero initial energy. Reports are
reproduced from the actual arrays - the full semantic payload, not just pass
flags - at adjudication and before any snapshot write; NoGo reports and
refusals preserve all evidence. See docs/LINEAR_ASSESSMENT.md.

```
python -m anymarine.cli prepare-assessment --project <dir> --spec spec.json --out case.json
python -m anymarine.cli assess-linear --project <dir> --case case.json --out report.json
python -m anymarine.cli adjudicate-assessment --snapshot <dir> --case case.json --report report.json
python -m anymarine.cli assess-refinement --coarse-project <dir> --coarse-case coarse-case.json --coarse-result-id <id> --fine-project <dir> --fine-case fine-case.json --fine-result-id <id> --snapshot <snapshot-dir>
python -m anymarine.cli adjudicate-refinement --snapshot <snapshot-dir>
```

The same complete workflow runs inside the Qt application
(`python -m anymarine.qtapp`, Assessment menu) through isolated headless
child processes with frozen inputs, exclusive snapshot targets, a
canonical output seal plus trusted producer digest on admission, honest
Pass/NoGo/NOTMEASURED check tables, member-specific replay and
detached/stale re-labelling after any project edit. See
docs/QT_ASSESSMENT.md; `examples/qt_assessment_workflow.py` prepares a
real stored project with a registered case for GUI assessment
(analytic rehearsal inputs, development scope only).

Initial focused source checks covered 37 linear-assessment and 17
hydro-decay/paired-refinement cases; two additional persistence regressions
were then added. The corrected Windows wheel passed 17 independent installed
checks and actual CLI snapshot reproduction. This is development evidence,
distinct from scientific qualification or supported release. Paired coarse/fine refinement of the same physical
model/source (integer nested dt ratio, kernel identity at shared lags)
is assessed with a combined verdict, an exclusive self-contained paired
snapshot and snapshot-only reproduction; see
docs/HYDRO_DECAY_REFINEMENT_CHECKPOINT.md.

Hydrodynamic import (development, unqualified; requires the optional ANYhydro
package and a saved two-body project whose bodies provably match the dataset
geometry):

```
# hydro-options.json - every value is explicit; there are no hidden defaults
{
  "n_steps": 201,
  "criteria": {
    "omega_min": 0.5, "omega_max": 4.0,
    "added_mass_abs_tol": 50.0, "added_mass_rel_tol": 0.05,
    "damping_abs_tol": 50.0, "damping_rel_tol": 0.05
  },
  "t0": 0.0, "q0": [0.0, 0.0, 0.0, 0.0], "v0": [0.0, 0.0, 0.0, 0.0],
  "surge_link_stiffness": 0.0,
  "wave": {"omega": 1.0, "heading": 0.0, "amplitude": 2.5, "phase": 0.3}
}
python -m anymarine.cli import-hydro --project <dir> \n  --dataset <dataset.nc> --kernel <kernel.nc> --options hydro-options.json
python -m anymarine.cli run-linear --project <dir>
```

Omit `"wave"` (or set it to `null`) for zero-forcing free decay. The wave
frequency and heading must be exact stored dataset samples inside the declared
reconstruction window; nothing is interpolated. The same import runs from the
GUI menu and uses the identical shared service.

Numerical lid (development, unqualified): a project body may carry an optional
`hydro_lid`, a body-local triangle mesh that is numerical geometry only (no
display, gravity or buoyancy). A dataset lid is admitted only through an exact
proof: the dataset's per-body `lid_mesh_hash` must equal the hash of the
expected world-space project lid (exact vertex translation with the body
reference position, vertices on the equilibrium waterplane z=0, nondegenerate
faces, DOWN normals). A missing, mismatched, upward, degenerate or
tilted/off-plane lid is refused; both sides absent is supported. The lid
identity is bound into the linear inputs (local lid mesh hash) and the import
provenance (world lid hashes), so a lid edit, removal or replacement makes
stored inputs and results stale exactly like a hull edit.

Longer development jobs: the default job bound remains 1001 frames. An
explicit `execution_limits` object in the import options (or in explicit
linear inputs) admits longer development jobs up to a hard ceiling of 20001
frames:

```
# hydro-options.json (longer development job; execution bound, NOT qualification)
{
  "n_steps": 1201,
  "execution_limits": {"max_frames": 1201},
  "criteria": {
    "omega_min": 0.5, "omega_max": 4.0,
    "added_mass_abs_tol": 50.0, "added_mass_rel_tol": 0.05,
    "damping_abs_tol": 50.0, "damping_rel_tol": 0.05
  }
}
python -m anymarine.cli import-hydro --project <dir> \
  --dataset <dataset.nc> --kernel <kernel.nc> --options hydro-options.json
python -m anymarine.cli run-linear --project <dir>
```

Booleans, fractional, non-finite, non-positive, over-ceiling and unknown
controls are rejected before anything is imported or allocated. The explicit
limits travel with the inputs into snapshots, persistence and the input hash;
without them the legacy 1001 default and the exact legacy serialization and
hashes are preserved. This reproduction example is development evidence only:
an execution bound never confers qualification.

The public ANYhydro builder now supports matching explicit kernel bounds:

```python
from anyhydro import RadiationExecutionLimits, RadiationSettings

settings = RadiationSettings(
    dt=0.05, n_time=1201,
    execution_limits=RadiationExecutionLimits(max_time_samples=1201),
    symmetry_tol=declared_symmetry_tolerance,
    passivity_tol=declared_passivity_tolerance,
    low_tail_policy="explicit_zero_sample",
    zero_frequency_damping=declared_zero_frequency_damping,
)
```

Both packages retain their default 1001-sample/frame guards. Declare kernel
and job bounds explicitly, and supply a kernel covering the run; insufficient
history is refused without padding or truncation. Installed development checks
exercise a nonzero lid-equipped 1002-frame hydrodynamic import, save/reopen,
child job and save-as/replay. These checks confer no scientific qualification.

GUI (requires PySide6 and any3dview):

```
python -m anymarine.qtapp <project-dir>
python run_gui.py --project <project-dir>
```

Choose **Run linear development job** in the GUI to create the analytic demo
or execute stored linear inputs. The local `run_gui.py` launcher prefers the
tested isolated runtime when available. The linear route requires an ANYsolver
candidate containing `anysolver.marine_linear`; a missing backend produces a
failed job with diagnostics. No backend is imported by base project services.

Explicit inputs may be supplied with `run-linear --inputs FILE`; the JSON form
of `LinearDevelopmentInputs` includes body order, mesh/equilibrium hashes,
matrices, kernel, fixed grid, forcing, initial conditions and provenance.
The default limit is 1001 frames and four DOFs; an explicit `execution_limits`
record admits longer development jobs up to 20001 frames (execution bound
only, never qualification). Editing geometry, equilibrium or a numerical lid
makes coefficients and results stale; old coefficients are never silently rebound.
Analytic/explicit linear projects use manifest schema 3; hydro imports use
schema 4 with checked dataset/kernel sidecars. Older readers refuse unsupported
intent. Legacy synthetic schema-2 projects remain readable. Results preserve authoritative
q/v/a and force channels; replay is validated against those arrays before
job completion. Capytaine RAO/free-decay qualification remains outstanding.

## Tests

```
pytest
```

Qt tests run offscreen (`QT_QPA_PLATFORM=offscreen`). Integration consumes an
installed ANY3dView candidate wheel with PySide6 6.10.1, not sibling source
imports. A separate real Windows OpenGL desktop check also exercises replay.

## Local tested launch

The development installation on this workstation is isolated in
`C:/ANYworkspaceWork/anymarine-evidence/runtime`. From this checkout:

```powershell
./Start-ANYmarine.ps1 -Python C:/ANYworkspaceWork/anymarine-evidence/runtime/Scripts/python.exe -Project C:/ANYworkspaceWork/anymarine-evidence/demo-project
```

For another environment, install the candidate ANY3dView Qt wheel and
`ANYmarine[gui]`, then use `python -m anymarine.qtapp <project-directory>`.
The dependency's candidate wheel has not been published by this task.

The saved format is currently a project directory with `project.json`, a
versioned manifest and content-addressed JSON sidecars. It preserves body order
and commits by atomically replacing the manifest; interrupted saves leave the
previous project loadable. Results and input snapshots are distinct. Schemas
2 (synthetic), 3 (linear), 4 (hydro import) and 5 (native inputs/results) preserve their required intent;
unsupported versions are refused.

## Status and limits

This is the W0â€“W1 application foundation plus the development hydrodynamic
import bridge, not the full marine demonstrator. The GUI admits synthetic
replay jobs and linear development jobs (analytic demo, explicit inputs, or
ANYhydro dataset/kernel imports); no result is a qualified marine analysis.
Capytaine preprocessing exists in the separate ANYhydro package and linear
radiation dynamics in the separate ANYsolver development module; the bridge
admits development imports only, never BEM qualification evidence.
Native dry/link, declared potential-flow, body Morison and dynamic mooring mechanics
are available as development routes. Physical six-DOF source admission, line
hydrodynamics/contact, HAMS-MREL, coupled equilibrium and scientific qualification
remain outstanding. See [dynamic mooring workflow](docs/NATIVE_MOORING_DEVELOPMENT.md).
The GUI's synthetic worker has no children; admitting backend subprocess trees
requires a tested lifecycle mechanism first.

The headless CI definitions target Windows/Linux and Python 3.11/3.13. Only
Windows Python 3.13.9 was exercised locally; remote CI was not run. See
`TASK.md` for the living record and `docs/IMPLEMENTATION_PLAN.md` for the
adopted roadmap snapshot. No release or qualification is claimed.

The root launcher runs the current checkout. It discovers `.venv`, the local tested runtime, or `ANYMARINE_PYTHON`. The `tools/run_gui.py` launcher exercises installed packages.


Explicit body CG and full CG inertia can be edited in the Qt Body panel and
saved with the project. Validate dry mechanics uses the optional public
ANYsolver rigid-body API; native dry/link jobs use its finite-rotation time integrator.
See [rigid-body inputs](docs/RIGID_BODY_INPUTS.md).

Native mechanics development route: File â†’ Load native dry two-body example
loads explicitly declared dry inputs without solving. Jobs offers native input
import, model/view editing and an isolated native run. Native replay uses stored
BODY-to-WORLD rotation matrices and the producing snapshot's geometry. CLI uses
`import-native --project <dir> --inputs <json>` and
`run-native --project <dir> --max-frames 1001 --wall-seconds 60`.
Focused source checks, installed CLI/job reproduction and Windows software/OpenGL
desktop replay have passed. These are development checks, not scientific qualification.
Synthetic startup replay remains the default.
See TASK.md and docs/adr/ADR-014-native-jobs-and-matrix-replay.md for admission and evidence.


Native coupled fluid example: run `python run_gui.py`, then choose **File â†’ Load
native coupled fluid example**. Two cube bodies appear at their declared initial
poses. Loading does not run dynamics. **Jobs â†’ Run native mechanical job** runs
the stored three-step development case in an isolated process; select a stored
wrench channel or replay the accepted frames. Save/reopen keeps the authoritative
arrays and producing inputs. Edits mark old results stale and do not change them.

The example uses explicitly synthetic full coupled added-mass/restoring/radiation
operators, one link and harmonic excitation. It is unqualified development data,
not BEM results. Existing startup synthetic replay and dry example remain available.
Model/view editing and the CLI `import-native`/`run-native` routes use the same
strict shared data/solver contracts. Unknown physics/bases, stale equilibrium
binding, duplicated owners and out-of-bounds initial motion are refused.

Use the tested matched application/solver wheels; old solver builds with the same
development version may lack this named API. The launcher selects a local GUI
runtime or the evidence runtime when available; `ANYMARINE_PYTHON` selects one
explicitly. Missing/incompatible optional solver support produces an actionable
refusal. Full physical-source admission, scientific W4 and W8-W10 remain pending.
See `docs/NATIVE_FLUID_DEVELOPMENT.md` and TASK.md for exact scope/evidence.


## Development status in the app

After startup the status bar shows a compact development snapshot line, and
**Help > Development status...** opens a read-only capability table plus the
latest hydrodynamic development state (currently unqualified; W5 NO_GO). The
data is the bundled, validated `development_status.json` loaded through a
Qt-free public loader, so installed wheels show the same snapshot; corrupt or
missing data fails clearly. See
[development status route](docs/DEVELOPMENT_STATUS.md). This view is a dated
development snapshot, separate from project results and assessment, and never
a qualification claim.

## Wave visualization

The **Waves** tab beside Assessment displays a retained ANY3dView water surface.
Choose **Incident waves (without disturbance)** to view/animate the selected
linear Airy wave immediately. Set amplitude, angular frequency, propagation
heading and water depth; **Pause waves** controls animation.

**Matched waves.** When the shown result was solved in waves, the Waves tab
switches to **Saved incident waves (matched to result)** automatically and shows
that exact wave at the replay time, so **Play** moves bodies and waves together.
Native mechanical results use the member/line environment of their producing
snapshot, also after later project edits. Hydro linear results use the forcing
wave (amplitude, frequency, heading, phase) recorded by their producing inputs
(the result's surface snapshot, or the project's linear inputs when they
reproduce the result hash), with the depth and gravity of the identity-verified
excitation-source declaration of the project's bound dataset, which must be the
producing dataset. Anything else is refused with a status message, and choosing
this mode for a result not solved in waves is refused. Only the undisturbed
incident field is drawn. **File → Load moored bodies in a regular
wave (solved replay)** opens a stored 10 s native solve of two moored cubes in
one 0.3 m, 2 rad/s wave (development, unqualified; regenerate with
`tools/build_regular_wave_example.py`).

Click **Calculate body disturbance** to queue a separate child-process
calculation. On successful validation, **Incident + body disturbance** shows
the incident wave plus the actual Capytaine diffraction elevation. Toggle the
two modes to compare the same incident realization. **Cancel wave calculation**
uses the existing job queue; diagnostics and incomplete evidence are retained.

This is a coarse, unqualified fixed-body preview with captured WORLD geometry
(at most two bodies,48 immersed panels,1024 surface sample points,one frequency).
Bodies stay at the captured poses while waves animate; body-motion radiation,
wave stretching, current-modified dispersion and nonlinear free surfaces are
not included. Selecting a replay frame, editing/loading a project or changing
wave parameters invalidates the preview. These fields never supply mechanical
forces or qualify an analysis. Requires the tested ANYhydro/ANYloads packages
and optional Capytaine3.0.0 for the disturbed mode; incident mode does not import
Capytaine. Saved wave fields/persistence integration is still pending.


Installed Windows validation confirms GPU-preferred startup on an NVIDIA RTX
3090 Ti, incident and actual fixed-body diffraction animation, exact body pose
restoration and software/GPU switching. The first historical diffraction attempt
timed out constructing the backend's default lookup table. A later bounded actual
QProcess run completed with Capytaine3.0.0 public direct integration: two bodies,
28 immersed panels,801 surface nodes, one frequency and one thread. Its sealed
field is bundled for **File â†’ Load calculated wave preview example**; loading
never executes or imports Capytaine. See [calculated waves](docs/CALCULATED_WAVES.md)
for controls, provenance and limitations. This proves calculation/visualization
development integration; numerical accuracy and full scientific qualification
remain open. Historical failed/consumed evidence is preserved.



## Native wave/current Morison example

Choose **File â†’ Load native wave/current Morison example** to display two bodies
and teal member centrelines in ANY3dView. **Jobs â†’ Edit native mechanical inputsâ€¦**
edits the saved member endpoints, section and prescribed wave/current environment;
**Jobs â†’ Run native mechanical jobâ€¦** runs the three-step development example in
an isolated child. Save/reopen, input hashes and producing-snapshot replay preserve
these declarations. GPU rendering remains preferred by default.

The Waves tab is an independent fixed-body visualization preview; it does not
set the mechanical environment. This example and provider remain unqualified.
See [native Morison development](docs/NATIVE_MORISON_DEVELOPMENT.md) for loading,
ownership, approximations and remaining gates. No BEM attempt is made by loading.


## Coupled saved-wave example

Choose **File â†’ Load coupled seeded-wave example**, **Jobs â†’ Edit shared sea stateâ€¦**
and **Waves â†’ Saved incident waves (matched to result)**. The same saved
components drive development excitation, Morison kinematics and incident display.
See [saved shared-sea-state workflow](docs/NATIVE_SHARED_SEA_STATE.md) for loading,
replay, immutable runs and limitations. Standard local wheel build/install and
Windows GPU development checks pass; physical and full-plan acceptance remain open.

## Native mooring example

Choose **File â†’ Load native mooring example (three lines)** for two bodies and
three brown anchored lines. **Jobs â†’ Run native mechanical jobâ€¦** runs three
fixed development steps. Replay and save/reopen preserve the actual stored node
positions and producing geometry. See [native mooring development](docs/NATIVE_MOORING_DEVELOPMENT.md)
for editing, transport validation and remaining scientific gates.

## Coupled static equilibrium example

Choose **File > Load coupled static equilibrium example**, then **Jobs > Run
coupled static equilibrium job**. Stored replay shows continuation stages and
body/line geometry, with explicit coefficient-regeneration and stale-result
status. `python -m anymarine.cli run-equilibrium --project PATH` runs the same
services headlessly. See [static development workflow](docs/NATIVE_STATIC_EQUILIBRIUM.md)
for evidence, cancellation, controls and remaining scientific gates.

## Attached-plate geometry example

Start `run_gui.py` and choose **File → Load attached-plate geometry example**.
The viewport shows two floaters and their submerged, two-sided plates. Save and
reopen preserve the assignments; each plate shares its body's motion. This is
a geometry preview, with no computed plate response. Native plate hydrodynamics
and body-disturbed wave calculation remain unsupported. See
[owned-surface project records and admission](docs/SURFACE_PROJECTS.md).

Stored HAMS wave samples are available from **File > Load stored HAMS wave samples**.
See [stored HAMS wave replay](docs/STORED_HAMS_WAVES.md) for incident/disturbed
point displays, portable save/reopen and the shared headless CLI.

Explicit native HAMS field jobs share the Qt/CLI queue and validation contracts;
see [native HAMS fields](docs/NATIVE_HAMS_FIELDS.md) for producer availability,
configuration, lifecycle guarantees and outstanding scientific admission.
