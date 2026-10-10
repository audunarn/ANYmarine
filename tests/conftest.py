"""Shared fixtures. Qt tests run offscreen; headless tests block Qt entirely."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

# PYTHONPATH is own src only: the ANY3dView candidate is consumed from the
# installed wheel (0.5.5), never from sibling source trees.
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# The linear development route calls the optional anysolver.marine_linear
# public API. The read-only solver checkout is admitted explicitly for
# checks/tests; nothing is installed and absence is a skip, never a failure.
SOLVER_SRC = Path("C:/Github/ANYsolver/src")
if SOLVER_SRC.is_dir():
    import importlib.util

    try:
        if importlib.util.find_spec("anysolver") is None:
            sys.path.insert(0, str(SOLVER_SRC))
    except ModuleNotFoundError:
        sys.path.insert(0, str(SOLVER_SRC))
    os.environ.setdefault("ANYMARINE_SOLVER_PATH", str(SOLVER_SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ANYMARINE_VIEWER_BACKEND", "software")

PYTEST = sys.executable


def solver_available() -> bool:
    try:
        from anysolver import marine_linear  # noqa: F401

        return True
    except Exception:
        return False


requires_solver = pytest.mark.skipif(
    not solver_available(), reason="anysolver.marine_linear unavailable"
)


def qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        import any3dview  # noqa: F401
    except Exception:
        return False
    return True


requires_qt = pytest.mark.skipif(not qt_available(), reason="PySide6/any3dview unavailable")


@pytest.fixture()
def service():
    from anymarine.services import ApplicationService

    return ApplicationService()


@pytest.fixture()
def project(service):
    return service.new_project("Test project")


@pytest.fixture()
def project_dir(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    return root
