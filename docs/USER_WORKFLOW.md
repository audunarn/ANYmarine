# ANYmarine user workflow

This page describes the everyday Qt GUI workflow for hydrodynamic surface
assignments. It is a companion to the repository [README](../README.md)
(overview, launch and test instructions) and to
[docs/SURFACE_PROJECTS.md](SURFACE_PROJECTS.md) (the surface record and
persistence contract). Everything here is development tooling: surface
editing declares intent through public records; it is not a qualified
marine analysis.

## Starting and loading models

Run `python run_gui.py` in the checkout, or `python -m anymarine.gui` in the
installed environment. Startup displays the bundled two-body model. Open saved
projects through **File > Open project...**, choosing `project.json`; other
**File** example entries load development models. Press **Play** to replay the
selected stored result. See [installation](INSTALLATION.md) for renderer options.

## Working with surface assignments

Each body in the project tree owns its surface assignments. Selecting a
surface node shows its details in the Details dock; selecting the owning
body shows the body form with an "Add hydrodynamic surface..." button.

### Selecting and inspecting a surface

1. Expand **Bodies** in the Project tree and click a surface node.
2. The Details dock shows the surface identity (id, role, physical kind,
   owning body), the **derived normal convention** (read-only: outward for
   `body_boundary`, minus-to-plus for `thin_sheet`, downward for
   `numerical_lid`) and the producing dataset status.
3. The dataset status is honest by absence and by content: with a bound
   ANYhydro import the actual source backend, schema version and development
   qualification label are shown (a development label is never scientific
   sheet qualification), together with the pressure-jump convention, the
   sheet source backend, the sheet source capability status and the optional
   pressure-jump contract sign stored in the exact content-addressed
   producing dataset sidecar. These are producing-dataset development
   evidence, not a qualification of the currently edited geometry, and a
   source capability label is not scientific qualification. Attributes the
   producing dataset does not store are shown as unavailable. When the
   project root, the sidecar or the tested neutral package is missing, or
   the sidecar bytes no longer match the stored raw sha256, the panel
   states an actionable unavailable reason and never falls back to the
   original external dataset path.

### Editing a surface

Editable fields: primary hydrodynamics (`potential_flow`, `morison`,
`none`), viscous correction (`none`, `member_drag`, `plate_drag`), the
source geometry id, and — for thin sheets only — the two distinct
minus/plus side labels. When the correction is `plate_drag`, an explicit
positive reference area and drag coefficient are required; the record
stores the canonical public convention (`F=0.5*rho*Cd*area*abs(vn)*vn*n`,
relative velocity wave+current minus surface, normal projection). The GUI
declares this intent; it implements no force law.

- Press **Apply surface edit**. The edit is applied atomically through the
  public frozen records; the project is marked dirty and stored results
  become stale.
- Refusals (for example a Morison primary on a sheet, identical side
  labels, side labels on a hull, plate drag on a non-sheet, or a load
  owner on a numerical lid) show a warning and leave the project
  completely unchanged.
- The surface id, owning body, role and mesh are locked during editing.

### Adding a surface assignment

1. Select the owning body and press **Add hydrodynamic surface...**.
2. Choose the explicit role; the physical kind follows from it and is
   never inferred from mesh shape:
   - `hull` — `body_boundary`, referring to the existing body mesh;
   - `lid` — `numerical_lid`, referring to existing lid geometry (a
     numerical lid is never a physical plate or load owner);
   - `attached` — `thin_sheet`, requiring an explicit body-local mesh JSON
     file (positions/triangles); shell or open-mesh inference is never
     used.
3. Provide a unique surface id (unique across the whole project), a
   source geometry id, the primary treatment and correction, and for
   sheets the two distinct side labels.
4. **Add assignment** applies the creation through the same public
   validation; refusals (duplicate id, lid without lid geometry, mesh on
   a hull/lid assignment, unsupported kind/role combinations) warn and
   change nothing.

### Removing a surface assignment

Select the surface and press **Remove assignment**. Removal is explicit;
nothing is reclassified and no mesh is silently dropped.

### After editing: results, replay and saving

- Editing any surface invalidates stored results (they are shown as
  stale); replay of a stored result always uses its own producing
  surface snapshot, never the edited current surfaces.
- Save with **File > Save project**; surface intent persists in schema 7
  project records and reopens identically.
- Coefficient-only results never invent local pressures; if a linear
  result lacks producing surface geometry, replay is refused for projects
  with surface intent.

## Working with native Morison members

Native mechanical projects declare their Morison members in the same public
records the CLI and worker consume. Open **Jobs → Edit native mechanical
inputs…** to author them without external JSON:

- **Add Morison member…** appends a member with an explicit unique identity,
  two endpoint attachments (body chosen from the declared project bodies,
  explicit body-local coordinates), a circular section (positive diameter,
  nonnegative drag/added-mass coefficients), a Gauss quadrature order
  (2–16) and the public ownership choices (primary role
  `morison_full`/`potential_flow`/`none`, BEM inclusion, optional drag
  correction, buoyancy owner `none`/`potential_flow`/`morison`).
- **Edit Morison member…** changes the selected member's identity,
  attachments, section, ownership or quadrature.
- **Remove Morison member** deletes the selected member. The public Morison
  envelope requires at least one member, so the last member cannot be
  removed; edit it instead.
- **Create Morison members…** appears when the project has no Morison
  contribution. It starts from a clearly labelled synthetic development
  environment template that remains editable in the numeric tree — or from
  the exact declared environment when hydrodynamic mooring lines already
  share one — plus the first member dialog.

All authoring is admitted atomically through the shared
`ApplicationService.load_native_inputs` and the public ANYsolver/ANYloads
compiler (no solve). Refusals — duplicate or empty identities, identical
endpoints, invalid coefficients, incompatible ownership combinations,
undeclared endpoint bodies or missing owner packages — show an actionable
warning and leave the current project completely unchanged. Member edits
never alter the saved environment, shared wave realization or excitation
series; they do invalidate stored results, and stale replay keeps using each
result's captured producing geometry.

## Verifying the desktop rendering

`tools/verify_qt_desktop.py` checks that the GUI you launch actually renders
on your real Windows desktop, without touching your windows or running any
numerical job:

```
python tools/verify_qt_desktop.py --source-root C:/Github/ANYmarine \
    --backend default --evidence-dir <empty-folder-for-pictures>
```

Use `--backend default` for the normal automatic startup, or `--backend gpu`
/ `--backend software` for an explicit renderer. The tool opens its own
window, loads the two-body example exactly like a normal launch, captures
what you would see (full window and viewer), proves each body is a real
rendered mesh by hiding it through the public viewer API, exercises the
plate surface selection, the native authoring form and the stored replay,
then closes itself. Pictures and a pass/fail report (`report.json`,
`report.md`) land in the folder you give it. Exit code 0 means the verified
path passed on this desktop; anything else means the report says why. This
is smoke evidence for the current source only — it is not a scientific or
installed-package qualification. Details: `docs/QT_DESKTOP_VERIFICATION.md`.

## Responsive workbench window

The workbench fits a 1280x720 window: docks keep readable
minimums, the model canvas takes the remaining space, and long
Details metadata stays reachable through the page scroll bars
instead of forcing the window larger. The desktop verification
tool requests 1280x850 by default; pass `--window-width 1280
--window-height 720` to verify the compact size. The report
records the requested and actual window size and gates that the
actual window never exceeds the request.
