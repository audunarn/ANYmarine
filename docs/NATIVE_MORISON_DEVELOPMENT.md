# Native wave/current members — development

Run `python run_gui.py` from the ANYmarine checkout. The default ANY3dView
renderer is Auto (GPU preferred); its status bar reports the effective backend.
`python run_gui.py --viewer-backend gpu` explicitly requires the GPU path.

Choose **File → Load native wave/current Morison example**. Two cubes, a vertical
member on cube A and a member spanning A/B appear. Teal lines show declared
centrelines; they are not cylinders or calculated wet geometry. Select a member
in the project tree to highlight it.

**Jobs → Edit native mechanical inputs…** edits existing endpoint coordinates,
diameter, drag/added-mass coefficients, quadrature order, Airy components and
current profile. Identities and textual ownership declarations are read-only in
this editor. Add/remove definitions by importing a named native-input JSON file.
Validation uses the same public ANYsolver compiler as the CLI and worker.
Rejected edits leave the existing project intact. Saving/reopening preserves
all member/environment records; changes invalidate dependent results.

**Jobs → Run native mechanical job…** starts the isolated child process. The
example selects dt=0.02 s and three steps. No numerical job runs while loading.
Replay uses the captured producing project and authoritative BODY-to-WORLD
matrices, including member endpoints. Edited current inputs do not replace a
stale result's geometry. Plots use stored authoritative force arrays.

The optional `native-morison` extra needs the matched tested ANYsolver/ANYloads
builds. The named contracts are `marine_morison_development_v1` and
`marine_loads_v1`. Missing/incompatible owner packages fail with a typed refusal;
base project imports, example loading and persistence remain Qt/solver-free.

The solver owns prescribed Morison inertia, combined wave/current relative drag
and selected buoyancy. No extra current drag or inertia is assembled in the app.
The example selects full Morison for its declared members, excludes them from
BEM and omits buoyancy/gravity. It is a driven development example, not a floating
equilibrium or a scientifically qualified marine analysis. Straight centreline,
flat bed/still-water clipping and full nominal section are declared approximations.
Entry/slamming, stretching, line/contact laws and structural member stiffness
remain outside this slice. Potential-flow coefficients still require their own
equilibrium binding, ownership and excursion checks.

The **Waves** tab is a separate fixed-body visualization preview. Its controls
do not change the saved mechanical environment. Choose **Incident waves (without
disturbance)** to animate immediately, or **Calculate body disturbance** for the
optional Capytaine diffraction route. The latter remains numerically unverified
after the previously retained timeout; no new BEM calculation ran in this slice.
Wave preview fields are not stored mechanical force results.

Development verification is distinct from scientific qualification, full W4
regression and W8–W10 acceptance. See TASK.md and the external evidence record.
