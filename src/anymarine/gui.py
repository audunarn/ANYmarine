"""Qt-free launcher for the ANYmarine desktop application.

This module is the supported installed-GUI entry point
(``anymarine-gui`` console script, ``python -m anymarine.gui``). It stays
importable without Qt or ANY3dView: ``--help`` and argument parsing never
import the GUI stack, and the Qt import happens only when actually launching.
Core and CLI modules remain Qt-free.
"""

from __future__ import annotations

import argparse
import sys

VIEWER_BACKENDS = ("software", "auto", "gpu")

MISSING_GUI_DEPENDENCIES = (
    "ANYmarine GUI dependencies are unavailable: {error}. "
    "Install ANYmarine[gui] and the tested ANY3dView Qt wheel into this "
    "Python environment."
)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the installed GUI launcher (no Qt import)."""
    parser = argparse.ArgumentParser(
        prog="anymarine-gui", description="ANYmarine development desktop"
    )
    parser.add_argument(
        "--project", help="Saved project directory to open"
    )
    parser.add_argument(
        "--viewer-backend",
        choices=VIEWER_BACKENDS,
        default=None,
        help="ANY3dView renderer; defaults to GPU-preferred auto or "
        "ANYMARINE_VIEWER_BACKEND",
    )
    return parser


def main(argv=None) -> int:
    """Parse arguments, then launch the Qt application.

    Returns 2 with a clear diagnostic (no traceback) when the GUI
    dependencies are missing. Exceptions raised by ``run_app`` itself are
    not masked: they propagate as the application's real failure.
    """
    args = build_parser().parse_args(argv)
    try:
        from anymarine.qtapp import run_app
    except ImportError as error:
        print(MISSING_GUI_DEPENDENCIES.format(error=error), file=sys.stderr)
        return 2
    return run_app(args.project, viewer_backend=args.viewer_backend)


if __name__ == "__main__":
    raise SystemExit(main())
