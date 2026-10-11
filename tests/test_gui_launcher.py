"""Focused launcher tests for the installed-GUI entry contract.

Qt-free: no window is opened and no renderer is started. These tests pin the
argparse contract (``--project``, ``--viewer-backend`` software/auto/gpu,
default None inheriting the GPU-preferred settings), ``--help`` without Qt,
the missing-GUI-dependency diagnostic (nonzero, no traceback), preservation
of real ``run_app`` exceptions, the pyproject console-script mapping and the
thin ``tools/run_gui.py`` shim.

The file is deliberately location-agnostic: tests that need repository files
skip when those are absent, so the same file can be copied into an
installed-mode guard run (tools/verify_installed_application.py
installed-tests) and prove the launcher from the installed package alone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import anymarine.gui as gui

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
QT_BASE_PREFIXES = ("PySide6", "PyQt", "shiboken")

BLOCKER_BOOTSTRAP = (
    "import sys\n"
    "class Blocker:\n"
    "    def find_spec(self, fullname, path=None, target=None):\n"
    "        if fullname.split('.')[0] in "
    "('PySide6', 'any3dview', 'shiboken6', 'PyQt6', 'PyQt5'):\n"
    "            raise ImportError('blocked for verification: ' + fullname)\n"
    "        return None\n"
    "sys.meta_path.insert(0, Blocker())\n"
    "from anymarine.gui import main\n"
    "sys.exit(main([]))\n"
)


def launcher_env() -> dict:
    """Subprocess environment importing the checkout when it is present.

    In an installed-mode copy there is no ``src`` directory next to the
    test file, so the subprocess imports the isolated installed package.
    """
    env = os.environ.copy()
    if SRC.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + existing if existing else "")
    return env


# ------------------------------------------------------------ parser contract


def test_parser_defaults_inherit_gpu_preferred_settings():
    args = gui.build_parser().parse_args([])
    assert args.project is None
    assert args.viewer_backend is None


@pytest.mark.parametrize("backend", ["software", "auto", "gpu"])
def test_parser_accepts_viewer_backends(backend):
    args = gui.build_parser().parse_args(["--viewer-backend", backend])
    assert args.viewer_backend == backend


def test_parser_accepts_project():
    args = gui.build_parser().parse_args(["--project", "C:/some/project"])
    assert args.project == "C:/some/project"


def test_parser_rejects_unknown_backend():
    with pytest.raises(SystemExit) as excinfo:
        gui.build_parser().parse_args(["--viewer-backend", "vulkan"])
    assert excinfo.value.code == 2


# ------------------------------------------------------------------- --help


def test_help_runs_without_importing_qt(capsys):
    before = set(sys.modules)
    with pytest.raises(SystemExit) as excinfo:
        gui.main(["--help"])
    assert excinfo.value.code == 0
    assert "ANYmarine development desktop" in capsys.readouterr().out
    added_qt = [
        m for m in set(sys.modules) - before
        if m.split(".")[0] in QT_BASE_PREFIXES
    ]
    assert added_qt == []


def test_module_help_entry_point():
    completed = subprocess.run(
        [sys.executable, "-m", "anymarine.gui", "--help"],
        capture_output=True, text=True, env=launcher_env(), timeout=30,
    )
    assert completed.returncode == 0
    assert "ANYmarine development desktop" in completed.stdout


# ------------------------------------------------- missing GUI dependencies


def test_missing_qt_diagnostic_is_clean_nonzero():
    completed = subprocess.run(
        [sys.executable, "-c", BLOCKER_BOOTSTRAP],
        capture_output=True, text=True, env=launcher_env(), timeout=30,
    )
    assert completed.returncode == 2
    assert "ANYmarine GUI dependencies are unavailable" in completed.stderr
    assert "Traceback" not in completed.stderr


# ------------------------------------------------------- run_app forwarding


def test_main_forwards_arguments_and_return_code(monkeypatch):
    recorded = {}

    def fake_run_app(project, viewer_backend=None):
        recorded["project"] = project
        recorded["viewer_backend"] = viewer_backend
        return 7

    fake = types.ModuleType("anymarine.qtapp")
    fake.run_app = fake_run_app
    monkeypatch.setitem(sys.modules, "anymarine.qtapp", fake)
    code = gui.main(["--project", "proj", "--viewer-backend", "software"])
    assert code == 7
    assert recorded == {"project": "proj", "viewer_backend": "software"}


def test_main_preserves_run_app_exceptions(monkeypatch):
    def broken_run_app(project, viewer_backend=None):
        raise RuntimeError("real defect must stay visible")

    fake = types.ModuleType("anymarine.qtapp")
    fake.run_app = broken_run_app
    monkeypatch.setitem(sys.modules, "anymarine.qtapp", fake)
    with pytest.raises(RuntimeError, match="real defect must stay visible"):
        gui.main([])


# ------------------------------------------------- packaging and shims (repo)


def test_pyproject_declares_gui_console_script():
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("pyproject.toml not available (installed-mode copy)")
    import tomllib

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["anymarine-gui"] == "anymarine.gui:main"
    assert scripts["anymarine"] == "anymarine.cli:main"


def test_tools_run_gui_shim_delegates_to_launcher():
    shim = REPO_ROOT / "tools" / "run_gui.py"
    if not shim.is_file():
        pytest.skip("tools/run_gui.py not available (installed-mode copy)")
    completed = subprocess.run(
        [sys.executable, str(shim), "--help"],
        capture_output=True, text=True, env=launcher_env(), timeout=60,
    )
    assert completed.returncode == 0
    assert "ANYmarine development desktop" in completed.stdout


def test_root_run_gui_checkout_entry_still_works():
    root_entry = REPO_ROOT / "run_gui.py"
    if not root_entry.is_file():
        pytest.skip("root run_gui.py not available (installed-mode copy)")
    completed = subprocess.run(
        [sys.executable, str(root_entry), "--help"],
        capture_output=True, text=True, env=launcher_env(), timeout=60,
    )
    assert completed.returncode == 0
    assert "ANYmarine development desktop" in completed.stdout
