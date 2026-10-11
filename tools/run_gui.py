"""Launch the installed ANYmarine application (no source-path injection).

Thin compatibility shim: the launcher contract (arguments, missing-Qt
diagnostic, lazy GUI import) lives in :mod:`anymarine.gui`, which also backs
the public ``anymarine-gui`` console script and ``python -m anymarine.gui``.
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    try:
        from anymarine.gui import main as launch
    except ImportError as error:
        print(
            f"ANYmarine is not importable in this environment: {error}. "
            "Install the anymarine package (wheel) or launch through the "
            "repository root run_gui.py for a checkout run.",
            file=sys.stderr,
        )
        return 2
    return launch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
