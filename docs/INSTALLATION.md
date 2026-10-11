# Installation and launch

From the checkout, run `python run_gui.py`. From an environment containing the
installed packages, run:

```powershell
python -m anymarine.gui
python -m anymarine.gui --project C:/path/to/saved-project
```

Startup loads the bundled two-body example and stored synthetic replay.
Use **File > Open project...** to open an ANYmarine `project.json`, or the
example entries in **File** to explore the available development models.
CAD and mesh import remain pending. See [user workflow](USER_WORKFLOW.md).

The installed module and checkout launchers share `anymarine.gui.main`.
`--help` works without Qt. Launching requires the `gui` extra; GPU is the
default. `--viewer-backend software` explicitly requests the documented
fallback. `--viewer-backend gpu` requires GPU rendering. The tested Windows
combination is Python 3.13.9, PySide6 6.10.1 and ANY3dView 0.5.5 with its
`qt,gpu` extras. Those extras include the GPU and image dependencies used by
the public viewer. Core imports and the headless CLI remain Qt-independent.

The wheel also declares `anymarine-gui`. Its Windows console executable has
an **unmet execution gate** on this workstation: the OS returned WinError 5.
The installed Python module was separately launched and rendered successfully;
this does not establish that the executable works. No OS restriction was
bypassed. On Ubuntu-24.04/WSLg the installed `bin/anymarine-gui` console
script executes and its `--help` gate passes; the Windows gate stays unmet.

## Local installed-consumer verification

`tools/verify_installed_application.py` provides separate `snapshot`, `build`,
`install`, `donor-build`, `install-extra`, `verify` and `installed-tests`
commands. Use `--help` for their arguments. Build from disposable snapshots,
not live source directories. Each output directory must be new. Installation
uses local wheels with `--no-index --no-deps`; it does not resolve dependencies
or download packages.

The install helper creates a clean venv without system-site-packages and
requires explicit `--borrow-site-packages` directories; the borrow `.pth`
file is placed in the target interpreter's effective site directory
(queried through `sysconfig`, not a hardcoded `Lib`/`lib` layout). Choose an
installed runtime containing the compatible dependencies. A directory entry
does not execute that directory's `.pth` files. Do not supply a broad global
directory containing editable checkout imports. Where a dependency is only
present there, copy its specific distribution files and retain licenses and
a hash manifest in a disposable dependency directory, or provide a compatible
local wheel. On hosts without `ensurepip` (for example Ubuntu without the
`python3.12-venv` package), pass `--venv-extra-args "--without-pip --copies"`
and `--pip-python <interpreter-with-pip>`; pip then installs into the target
through `pip --python` without needing pip inside the target.

`donor-build` can snapshot named read-only ecosystem repositories into scratch
and produce their wheels, retaining declared license and notice files.
`install-extra` installs exact wheel bytes, including same-version replacements.
`verify` accepts `--extra-wheel` and `--dependency-manifest`, checks their bytes,
source and licenses, and checks active dependency constraints including extras.
Old packages that import successfully but violate requirements are refused.
No donor checkout is edited by this workflow.

For desktop evidence, run `tools/verify_qt_desktop.py` using the target Python,
from outside the checkouts, without `--source-root`. Record separate GPU and
software attempts with `--window-width 1280 --window-height 720`. The tool
records actual module origins, OpenGL renderer, per-body visibility, stored
replay, surface controls, zero jobs and closure.

The 2026-10-11 Linux target (Ubuntu-24.04/WSLg, Python 3.12.3, fresh
pip-less venv) reproduced fresh ANYmarine/ANYhydro wheels plus current
ANYsolver 0.4.7, ANYloads 0.1.0, ANYmaterial 0.2.0, ANYfileio 0.3.2,
ANYgeometry 0.4.5 and a Linux-native ANYmesher 0.5.1 wheel built from the
donor snapshot, with PySide6 6.10.1/ANY3dView 0.5.5/numpy/scipy borrowed
from an explicit donor venv. The actual console script and module `--help`,
Qt-free core, blocked-backend stored replay, package bytes, origins and
editable denial all pass. The declared shapely constraint is now met with exact pinned-wheel
license evidence: Shapely 2.1.2
(shapely-2.1.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl,
sha256 1e7d4d7ad262a48bb44277ca12c7c78cb1b0f56b32c10734ec9a1d30c0b0c54b,
BSD 3-Clause; dist-info licenses LICENSE.txt and LICENSE_GEOS retained)
is installed in the target venv, and the full installed verification
verify-02 passes, including dependency_constraints. No runtime
consumer imports shapely directly. Real-desktop evidence:
explicit software backend passes fully at 1280x720 on Wayland/WSLg
(per-body 5301/7124 changed pixels); the default and explicit GPU modes
render correctly but the actual viewport GL string is Mesa llvmpipe, so
the hardware-GPU gate fails honestly - a WSLg host limitation, not a
software-mislabelled pass.

The 2026-10-11 Windows target reproduced fresh ANYmarine/ANYhydro wheels,
compatible current geometry/mesh/material/fileio wheels, and explicitly reused
installed dependencies. Package bytes, 48 active dependency constraints,
Qt-free core, optional-backend absence, stored replay/save/reopen and corrupt
sidecar refusal passed. Real GPU and software desktop checks passed, as did
native input preparation without integration. This is installed-consumer
development evidence, not a clean full release install or scientific acceptance.
The full verifier remains non-success because the console executable is unmet.
Exact artifacts and failures are retained in TASK.md's linked evidence.

### Public native API correction

A version number alone does not identify a compatible development wheel. The
initial Linux ANYsolver 0.4.7 wheel lacked `marine_mechanical`; the actual
public compiler refused it even though metadata checks passed. Root selected
a wheel matching the five tested marine module hashes, reinstalled its exact
bytes, and reproduced the native dialog/edit/compiler/save/reopen workflow
without integration. The installed verifier now requires this Qt-free,
Capytaine-blocked public compilation control. Final root-verify-03 passes.
Old failures and the earlier narrower metadata pass remain preserved.
