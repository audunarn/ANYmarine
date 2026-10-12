# Saved shared sea state — development workflow

Start `C:/Github/ANYmarine/run_gui.py` and choose **File → Load coupled seeded-wave
example**. Loading reads the bundled records and displays two bodies and member
centrelines in ANY3dView without running a solve. The default renderer prefers GPU;
**View → Renderer** permits explicit software fallback.

Choose **Jobs → Edit shared sea state…** to set significant wave height, peak period
and phase seed. Confirming creates a new saved realization and updates excitation
and Morison wave components together. Reopening preserves materialized phases;
it never draws new random phases. Save the project through the File menu.

In the Waves dock choose **Saved incident waves (matched to result)**. This
renders that project's exact wave components using public ANYloads surface
elevation and public ANY3dView mesh interfaces. Manual single-wave preview fields
are hidden in this mode. The initial example has Hs=0.02 m, so visible elevation
is small relative to the bodies; display geometry is not exaggerated.

**Jobs → Run native mechanical job…** submits an immutable input snapshot to an
isolated child. Editing while it runs creates a different project hash; its result
remains associated with its producing project. Select the saved result in the
project tree to replay stored body transforms and authoritative diagnostic arrays.
Showing the result selects the shared waves automatically at the replay time;
**Play** moves bodies and waves together and frame changes keep the layer. The
manual single-wave preview modes remain fixed-body previews that a frame change
resets. Renderer changes preserve playback state and body transforms.

This fixture has synthetic coupled coefficients, six saved wave components,
Morison members and a linear link; it is a three-step development example, not a
qualified floating-system analysis. Compilation reconstructs excitation from the
exact public fixture source and saved waves and rejects altered forcing. Physical
source admission, equilibrium qualification and full-plan acceptance remain pending.

The existing **Incident waves (without disturbance)** controls provide an
independent single-frequency preview. **Calculate body disturbance** uses the
optional Capytaine backend and is unavailable for the saved multi-frequency mode.
The separate calculated single-frequency example now contains a sealed actual diffraction field; see CALCULATED_WAVES.md. Its accuracy is unqualified.
No body disturbance, motion radiation or current-modified wave dispersion is
fabricated in the saved incident mode. Current is superposed for Morison drag.

Tested application environment: Python 3.13.9, PySide6 6.10.1, ANY3dView 0.5.5;
Windows OpenGL 3.3 on NVIDIA RTX 3090 Ti. Optional Capytaine remains pinned to
3.0.0, but no BEM solve ran during this integration. Linux desktop acceptance,
physical-source and complete scientific demonstrator gates remain open.
