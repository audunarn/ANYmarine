# Qt desktop verification (W9 source smoke)

`tools/verify_qt_desktop.py` is a bounded, reusable verification child that
exercises the current ANYmarine source or installed GUI on the real
Windows or Linux/WSLg Qt/OpenGL desktop. It produces consumer smoke
evidence supporting W9 only; it never claims whole W9/W10, scientific or
platform acceptance, and it starts no numerical job (no
BEM/HAMS/GF/native dynamics).

## Command

```
python tools/verify_qt_desktop.py --source-root C:/Github/ANYmarine \
    --backend default --evidence-dir <unique-attempt-folder>
```

- `--backend default` instantiates `MainWindow(viewer_backend=None)`, so the
  effective renderer is inherited from the actual configuration
  (`ANYMARINE_VIEWER_BACKEND` or the automatic GPU-preferred startup). The
  tool never calls `_change_renderer` itself; the automatic
  software-pending → exposed swap runs on its own.
- `--backend gpu` and `--backend software` are explicit separate modes.
- `--source-root` selects source mode (the tool verifies `anymarine.__file__`
  really is under that root and records sha256 of key source modules).
  Omitting it selects installed mode, which is labelled as such and claims no
  installed-marine qualification.
- `--evidence-dir` is a unique attempt folder for PNGs and reports.
- `--deadline` (default 25 s) is the tool's own wall-clock budget. On
  exception or deadline a partial report is always written and the tool's own
  window is closed with live jobs recorded.
- `--window-width` / `--window-height` (defaults 1280 / 850) request
  the logical QA window size after startup. The report records the
  requested and actual size and gates `window_within_requested`
  (actual never larger than requested), `responsive_1280x720_limit`
  (the shell minimum-size hint fits 1280x720, so a 720p request is
  never exceeded), `project_tree_usable` (tree at least 150x120 at
  the requested size) and `details_metadata_scrollable` (the
  thin-sheet surface form stays fully reachable through the
  Details page scroll bars).

Exit codes: `0` pass, `1` verification failure or partial run, `2` environment
or usage refusal (for example `QT_QPA_PLATFORM=offscreen` behind a desktop
claim).

## What is recorded

- Inherited environment (`QT_QPA_PLATFORM`, `QT_OPENGL`,
  `ANYMARINE_VIEWER_BACKEND`, `ANY3DVIEW_DISABLE_GPU`, `ANYMARINE_PYTHON`)
  and the real `QApplication.platformName()`; offscreen/minimal platforms are
  refused, never overridden.
- Exact identities: Python, PySide6, the installed ANY3dView wheel
  (version, module path, distribution path), neutral ANYhydro, and source
  sha256 hashes in source mode.
- Startup order reproduces `run_app`: `MainWindow`, `load_example()` BEFORE
  `show()`. The first composed frame is captured as exposed, then the settled
  automatic-fit startup view — both BEFORE any GL query or manual renderer
  action. The composed capture is a full-window `QWidget.grab()` plus a
  viewer crop that accounts for `QPixmap.devicePixelRatio`, so reported
  dimensions match the real pixel images; the viewer's own framebuffer
  capture is saved as supporting evidence only.
- GL provenance comes only from the actual same-viewport Qt OpenGL context
  (`viewer.make_current()`, public `canvas.context()` format, and the
  public `QOpenGLContext.functions().glGetString`
  vendor/renderer/version - no platform-specific system GL library is
  loaded). Hardware-GPU claims reject software driver strings (llvmpipe,
  SwiftShader, GDI Generic, software); provenance is never inferred from
  GPU inventory. A WSLg `D3D12 (...)` renderer names the host GPU and is
  accepted as hardware only on its actual string; llvmpipe is reported as
  the software fallback it is.
- Per-body pixel oracle: a stable same-camera composed baseline, then for
  each body public `MeshHandle.set_visible(False)` → redraw → composed
  capture → `set_visible(True)` → redraw. Each body must change at least 200
  pixels; visibility is restored exactly and per-body PNGs and counts are
  preserved.
- Consumer cases: example reload, attached-plate surface selection through
  the project tree, the native authoring form (`NativeInputsDialog` shown
  non-modally, captured, closed) and the synthetic stored replay pose checked
  against the authoritative arrays with handle retention.
- Live jobs must be zero, the tool's own window must close cleanly, and any
  modal warning is recorded as fail evidence (never swallowed).

## Evidence layout

Each run writes `report.json` (paths/counts only, no embedded images),
`report.md`, `startup-firstframe-window.png`, `startup-window.png`
(full window for root review), `startup-viewer.png`,
`startup-framebuffer.png`, per-body `oracle-hidden-*.png`, and per-case
window/viewer PNGs into the evidence directory.

## Scope limits

This is desktop smoke evidence for the selected source or installed consumer
with the installed ANY3dView 0.5.5 wheel. Installed mode verifies the consumer
origins and actual desktop path; it does not establish whole W9/W10, a clean
release install, platform acceptance or scientific qualification. It does not
replace the mandatory fuller headless replay gates. A clean pass says only
that the verified path rendered and behaved correctly on this desktop.

## Source verification on 2026-10-11

The final default, GPU and software Windows desktop checks pass against the
current source. An inactive wave reset previously cancelled the initial model fit;
that startup lifecycle defect is repaired. Software fallback now frames both
bodies automatically. The plate check selects the neutral `thin_sheet` record
and verifies its identity, normal convention and distinct side labels in Details.
The measured evidence and retained failures are in
`.development-evidence/w9-desktop-current-20261011/root-acceptance.json`;
45 focused QA and viewport regressions pass. These are source desktop consumer
checks with installed ANY3dView0.5.5, within the scope limits above.

The final responsive QA also exercises actual horizontal/vertical scrollbar
movement to bring the thin-sheet side field and Apply control into view, checks
their visible centers, and saves `plates-scroll-*.png`. This verifies control
access beyond minimum-size hints. Root evidence is in
`.development-evidence/w9-responsive-layout-20261011/root-acceptance.json`.

## Installed consumer on 2026-10-11

Fresh local marine/hydro wheels passed GPU and software Windows desktop checks
at 1280x720. Root inspected startup and scrolled thin-sheet screenshots.
The installed Python module launcher separately opened the same two-body GPU
example, with no jobs. See [installation](INSTALLATION.md) for the outstanding
console-executable gate and dependency-reuse limits.

## Linux installed consumer on 2026-10-11

On Ubuntu-24.04/WSLg (Wayland, Python 3.12.3, fresh marine/hydro wheels and
current ecosystem wheels, no checkout imports) the explicit software backend
passes every gate at 1280x720, including per-body 5301/7124 changed pixels,
stored replay, plate metadata scrolling, the native authoring form, zero
jobs and clean closure. The default and explicit GPU modes render correctly,
but the actual same-viewport GL string is `Mesa llvmpipe (LLVM 20.1.2)`, so
their hardware-GPU gates fail honestly: this WSLg host currently provides no
hardware GL to the viewport, and the tool reports that instead of
mislabelling software as GPU. Evidence:
`C:/ANYworkspaceWork/anymarine-evidence/w10-linux-gui-20261011/reports/`.
