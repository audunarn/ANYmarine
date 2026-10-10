# Viewport blank-load and ANYfem-style — early checkpoint (worker, 2026-10-08)

Status: inspection complete, implementation starting. Required early checkpoint
for this user-steering slice; not a permission gate. The broader assessment GUI
slice remains parked at docs/QT_ASSESSMENT_CHECKPOINT.md and is not touched here.

## Actual findings (measured against installed wheel any3dview 0.5.5)

- `MainWindow._set_project` (src/anymarine/qtapp/mainwindow.py) destroys the
  central viewer and creates a new one on every project load:
  `self.viewer.destroy()` + `create_viewer(...)`. The installed
  `QtViewer.destroy()` hides the widget and schedules `deleteLater()`; for the
  GPU backend each load therefore builds and tears down a fresh
  `QOpenGLWidget`/GL context. ANYfem keeps one central viewport alive across
  rebuilds.
- Fit runs before the viewport has a visible size: `_sync_viewer_bodies` calls
  `viewer.fit_to_scene()` immediately and `_set_project` also starts a 0 ms
  `_fit_timer`. In `run_app()` the example loads before `window.show()`, so the
  fit camera is computed from the pre-layout surface size
  (`RetainedViewer.viewport_size` falls back to the 800x600 init state only
  when the surface is <=1 px). No re-fit happens when the real layout size
  arrives.
- No renderer diagnostics are surfaced: the public `QtViewer` signals
  `render_error` and `backend_changed`, plus `backend_name` and
  `backend_diagnostics()`, are never connected. With backend `auto` the wheel
  falls back to software silently; with explicit `gpu` a render failure leaves
  a blank canvas whose only trace is a widget tooltip.
- There is no user-facing Fit control.
- Style: ANYfem `src/anyfem/ui/qt/theme.py` sets a 10 pt font and a light
  `#f3f5f8`/`#253247` workbench (white menus/inputs/tables, blue
  selections/buttons, padded dock titles). ANYmarine currently applies no
  stylesheet.
- Prior `startup-example-desktop.png` evidence asserted handles/visibility
  only; it does not establish rendered-pixel truth for reload/replay paths.

## Intended edit (owned files only)

1. New `src/anymarine/qtapp/theme.py`: light workbench stylesheet adapted from
   ANYfem's theme (MPL-2.0, attribution in module docstring), applied once in
   `MainWindow.__init__`. Qt-only module; numerical imports stay Qt-free.
2. `src/anymarine/qtapp/mainwindow.py`:
   - Keep one viewer for the window lifetime. On project change use the public
     `viewer.clear(keep_canvas=True)` and `MeshHandle.remove()` for stale
     handles instead of destroy/recreate.
   - Defer fit until the viewer is visible with its final size: pending-fit
     flag + event filter on the viewer widget (Show/Resize), plus the existing
     timer as a fallback; fit also re-runs after result transforms are applied.
   - Connect `render_error`/`backend_changed`; log effective backend and
     diagnostics to the Jobs/Diagnostics panel and status bar. A renderer
     error must never leave an unexplained blank canvas.
   - Add a View > Fit model action.
3. New `tests/test_qt_viewport.py`: focused offscreen software regressions
   (single viewer across loads, handle repopulation, scheduled fit, render
   error surfacing, theme applied, replay transforms preserved).
4. QA evidence script run through the exact harness: offscreen software path
   and a real Windows desktop `auto` path, grabbing actual pixels after load,
   resize, reload and replay steps; screenshots saved under
   `C:/ANYworkspaceWork/anymarine-evidence/qt-assessment-worker-01*`.

## Remaining work

Implement the above, run bounded checks (worker budget 160 s cumulative,
each <=12 s), repair failures, then final handoff at
`C:/ANYworkspaceWork/anymarine-evidence/viewport-style-final-handoff.md`.

## Blockers / reserved

None. Root cause is not yet confirmed by rendered-pixel evidence; the QA run
will record the effective backend and pixel counts before and after the fix.
GPU availability on this desktop is unknown until the real-desktop check runs.

## Outcome (same worker, same slice)

Implemented as intended above; one addition: `_report_render_error` also
guards against a hidden shell so a closing window never raises a modal.
Verified with rendered-pixel evidence on both backends (details in
`C:/ANYworkspaceWork/anymarine-evidence/viewport-style-final-handoff.md`):
offscreen software and real Windows desktop `auto` (effective backend `gpu`)
both render both meshes across startup example, resize, reload, replay steps
and saved-project open/reopen; fit clears only after the viewport is visible.
The user's blank-load report is addressed by removing per-load viewer/context
recreation, deferring fit to the visible layout, and surfacing renderer
diagnostics; no synthetic substitutes were used.
