# ANYstability in ANYmarine

## Local-only setup

Private recipe3 assets are NOT shipped in wheels or source distributions. A normal
installed package without explicit local configuration shows an unavailable page;
the explicit legacy box remains usable. No ambient work-folder discovery occurs.
The reconstruction implementation is also private: install the separate local-only
`ANYstability-local-reference` add-on alongside the reviewed matching ANYstability
core. Its sole module preserves the original `anystability.reference.barge` import
path and exact implementation bytes without replacing core package files. Do not
publish this add-on. The public core's reference CLI routes refuse truthfully when
it is absent; analytical box/console and existing scientific gates are unchanged.

For a checkout, create a git-ignored `.anymarine-local.json` beside `run_gui.py`:

```json
{"schema_version": 1, "python": "C:/local/runtime/Scripts/python.exe", "reference_barge_path": "C:/private/recipe3"}
```

Use absolute existing paths to a reviewed matching installed runtime and the
private exact dataset. Do not commit this configuration or private assets. Run
`python run_gui.py` normally. The launcher selects this runtime before legacy
runtime discovery and exports `ANYMARINE_REFERENCE_BARGE_DIR` to the application,
Scripts child and coupled worker. It performs no pip install or download.
`ANYMARINE_PYTHON` and `ANYMARINE_REFERENCE_BARGE_DIR` explicitly override their
respective config values. An overridden stale path need not exist; all config
field types/path syntax still validate, and effective paths must exist without
symlink/junction traversal where supported. Invalid present config or explicit
paths refuse instead of falling back. Config is strict bounded JSON, never code.

For installed/headless use, set `ANYMARINE_REFERENCE_BARGE_DIR` explicitly; no
checkout config is read by the installed service. The local folder is validated
against the existing dataset/source/request identities. Explicit Open of a saved
study remains independent of this default and requires its `study.json` commit.
A portable saved study contains private records: keep its directory private too.

## Retained Stability workspace

Select **Stability / Ctrl+7** for the local reference study. This workspace uses
its own header and actions; the unrelated project tree and wave toolbar are hidden
and their previous visibility is restored when you leave Stability or Scripts.
Opening a view, changing modes and selecting a stored condition never calculates.

Choose **Floating equilibrium**, **Upright tank loading**, **Inclined loading**,
**Opposite-heel loading** or **Raked-end loading**. Only Floating equilibrium can
start a new coupled run. The other choices are saved loading states, not floating
equilibria. A visible result-origin label distinguishes saved evidence from an
attached run. **ILLUSTRATIVE** loads remain distinct from historical/as-built data.

- **Overview** shows mass, body-frame centre of gravity, heel/trim in degrees,
  body-origin height and tank loading. The original drawing supports Fit width,
  Fit page and bounded zoom with scrolling. Body-origin height is not draught.
- **Loading & results** shows the stored owner ledger, forces, physical moments
  and generalized equilibrium residuals with their frames and units.
- **Advanced** retains strict raw run controls, original diagnostics/identities,
  model-versus-drawing capacities and the upright calibration. Exact stored values
  remain available even where summary cards use rounded display units.

Run equilibrium, Cancel, Open and Save remain visible while changing views.
Cancel is available while a run is active. Editing controls clears paired summary
results; failed validation preserves the previous study/selection. The analytical
box remains an explicit secondary example, with a return action to this study.
Without local data, the start page offers Open study, Retry and the analytical
example; no private assets are discovered or bundled automatically.

Only WB1..WB8 store liquid; pump/voids are excluded. Model capacity estimates and
drawing capacities remain distinct. Upright fills use achieved-volume mass;
attitude/coupled states conserve target-volume mass. Aggregate mass includes liquid
once. Actual liquid CG captures redistribution; no additional FSM is added.
Missing stored quantities stay absent. Assumed reconstruction, unresolved capacity
basis and opening/closure limits remain recorded in the underlying study.

## Portable saved studies

Load local case, Open and selection use public typed ANYstability loaders and
request makers. They require ANYstability but do not import ANYgeometry or solve.
The private dataset identity is
`b42da7fa9a75689bf2a4d3b633dc22e96cfdd018bd03c82bb1e362353f263c89`.
Source/model snapshots, controls, targets, densities and request/result pairings
are checked together; independent valid envelopes are insufficient. Repeated
validation may reuse an exact-byte dataset with the same public validator
identities. Hashes bind integrity, not signatures or certification.

Save requires a NEW directory under an existing parent. Fixed portable files are
written atomically and `study.json` is the last commit marker. Failure can leave
a partial directory; explicit Open refuses it without a valid marker. Existing
destinations are not overwritten. Saved studies require no original work-folder
paths. Corrupt, stale, cross-recipe and unpaired results are refused.

## Explicit coupled Run

Only coupled supports a new Run. Review controls, enable reference-development
opt-in and select a NEW job directory. Controls may tighten tolerances or lower
caps; they cannot change fixed loading/model/domain or raise the original limits:
512 owner queries, 24 physical evaluations, six iterations and 90 seconds.
Editing invalidates paired results. Invalid edits preserve the old selection.

Run snapshots the study and invokes public `execute_coupled_request` in one child.
An independent parent watchdog enforces the captured deadline and bounded cleanup,
without relying on Qt polling. Cancel first requests cooperative cancellation,
then terminates the owned process where supported. Logs, partial journals, typed
causes and parent status remain. Unconfirmed termination stays supervised and
prevents close from dropping the worker. Late, cancelled, stale or refused replies
do not become successful GUI results. Cancellation cannot undo external effects.

The 20 October local integration separately verified fresh reference runs,
recorded Scripts replay and cooperative cancellation; its retained failures and
limits remain in the evidence record. The 21 October UI redesign uses cached
studies, synthetic jobs and installed UI/persistence checks, with no geometry
queries. Certified ANYgeometry delivery is forthcoming. The current interface
preserves existing provider admission and reference opt-in; a cached or reference
result does not itself establish certification or a continuous GZ claim.

## Headless public API

```python
from anymarine.services import ApplicationService
service = ApplicationService()
study = service.reference_barge_example()  # explicit local dataset; saved evidence only
print(service.reference_barge_view(study))
saved = service.save_reference_barge(study, "C:/work/new-reference-study")
opened = service.open_reference_barge("C:/work/new-reference-study")
assert opened.identity == saved.identity
# Explicit optional execution only:
# run = service.run_reference_barge(study, "C:/work/new-reference-run",
#                                   allow_reference=True)
# study = run.finish()  # bounded wait; typed refusal on non-success
```

See `examples/reference_barge_workflow.py`. `reference_barge_controls` creates a
checked immutable study; `select_reference_barge_case` selects saved evidence.
`attach_reference_barge_result` accepts only a matching converged public result
with accepted loading. Partial/refused worker evidence stays separate. Public
owner `coupled_loading_condition(..., allow_reference=True)` supplies the ledger;
ANYmarine does not recreate liquid MassItems or equilibrium mathematics.

## Main-window Scripts section

**Scripts / Ctrl+6** embeds the optional console. Selecting it loads the widget;
its isolated persistent interpreter starts only on explicit Run. The initial
script opens saved recipe3 evidence through ApplicationService. Loading or copying
text and inserting recorded GUI actions never executes it.

The separate Stability GUI action recording captures Load/Open, controls,
Select/Save, Run/completion/cancel and refused attempts. Exact snapshots and paths
are recorded, not live widget objects. Dirty controls precede selection. Successful
completion records `barge_study = barge_run.finish()` before Save; replay compares
the new study identity dynamically rather than requiring identical elapsed times. Save records a per-path identity binding; later Open of that unchanged GUI-saved path uses its replay-generated binding. Externally supplied or changed artifacts keep literal pins. Each recording preamble resets the binding map, including in a persistent Scripts interpreter.
Change NEW output paths before replay. Child execution does not update the live
GUI study implicitly. Recording covers stability actions, not every marine action.

Submission history preserves failed text and submissions from before namespace
reset. Its combined editable .py source may need repair and is not guaranteed
reproducible replay. Run is trusted arbitrary Python, not a sandbox or undo.
Accepted app close stops owned sessions; rejected close preserves app timers.
Owner console documentation describes output and descendant-cleanup limitations.

## Explicit legacy analytical box

The Legacy analytical box tab and File / Examples / ANYstability analytical box
menu retain the independent earlier study. That menu focuses the legacy tab,
leaving recipe3 unchanged. It uses a 20 x 10 x 4 m box with illustrative dry loads
and four full fixed-CG tanks. Old saved studies, public APIs and standalone
StabilityDialog remain available. Only this route uses the S1 analytical-box solve.

```powershell
anymarine stability-info
anymarine stability-example --path C:\work\box-stability
anymarine run-stability --project C:\work\box-stability --allow-reference
python examples/stability_workflow.py --out C:\work\another-box --allow-reference
```

These commands concern the box, not recipe3. Full fixed-CG box tanks do not imply
partial-tank/free-surface or general hull admission.

## Optional installation

Install reviewed compatible ANYstability and ANYmarine wheels into a chosen
environment (`ANYmarine[stability,gui]` for Qt). The public reference readers,
request makers and accepted-loading helper are required; a version string alone
is not artifact proof. Missing/incompatible providers show a diagnostic/Retry
page, while the marine shell and explicit legacy tab remain independently usable.
No live environment is upgraded automatically. Headless imports do not require Qt
or ANYgeometry. No physical or certified admission is added by this interface.
