"""Headless operation: core + CLI must work with PySide6/any3dview blocked."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

BLOCK_TEMPLATE = '''
import sys
class _Blocker:
    def find_spec(self, name, path=None, target=None):
        root = name.split(".")[0]
        if root in ("PySide6", "any3dview", "shiboken6"):
            raise ImportError("blocked for headless test: " + name)
        return None
    def find_module(self, name, path=None):
        return None
sys.meta_path.insert(0, _Blocker())
'''


@pytest.fixture()
def blocked_env(tmp_path):
    """Environment in which importing PySide6/any3dview raises ImportError."""

    block_dir = tmp_path / "block"
    block_dir.mkdir()
    (block_dir / "sitecustomize.py").write_text(BLOCK_TEMPLATE, encoding="utf-8")
    env = os.environ.copy()
    pythonpath = os.pathsep.join([str(SRC), str(block_dir)])
    env["PYTHONPATH"] = pythonpath
    env.pop("QT_QPA_PLATFORM", None)
    return env


def run_blocked(env, *args, cwd=None, timeout=60):
    return subprocess.run(
        [sys.executable, *args],
        env=env,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_core_imports_headless(blocked_env):
    code = (
        "import anymarine, anymarine.cli, anymarine.worker, anymarine.services, "
        "anymarine.synthetic, anymarine.persistence, anymarine.domain, "
        "anymarine.hashing, anymarine.transforms; print('headless-ok')"
    )
    result = run_blocked(blocked_env, "-c", code)
    assert result.returncode == 0, result.stderr
    assert "headless-ok" in result.stdout


def test_blocking_actually_blocks(blocked_env):
    code = "import PySide6"
    result = run_blocked(blocked_env, "-c", code)
    assert result.returncode != 0
    assert "blocked for headless test" in result.stderr


def test_cli_full_workflow_headless(blocked_env, tmp_path):
    root = tmp_path / "proj"
    result = run_blocked(blocked_env, "-m", "anymarine.cli", "new", "--path", str(root), "--name", "Headless")
    assert result.returncode == 0, result.stderr
    assert (root / "project.json").is_file()

    result = run_blocked(blocked_env, "-m", "anymarine.cli", "run", "--project", str(root))
    assert result.returncode == 0, result.stderr
    assert "synthetic_unqualified" in result.stdout

    result = run_blocked(blocked_env, "-m", "anymarine.cli", "list-results", "--project", str(root))
    assert result.returncode == 0, result.stderr
    assert "synthetic_unqualified" in result.stdout

    result = run_blocked(blocked_env, "-m", "anymarine.cli", "validate", "--project", str(root))
    assert result.returncode == 0, result.stderr


def test_cli_run_local_headless(blocked_env, tmp_path):
    root = tmp_path / "proj"
    run_blocked(blocked_env, "-m", "anymarine.cli", "new", "--path", str(root))
    result = run_blocked(blocked_env, "-m", "anymarine.cli", "run", "--project", str(root), "--local")
    assert result.returncode == 0, result.stderr


def test_cli_refuses_overwrite(blocked_env, tmp_path):
    root = tmp_path / "proj"
    first = run_blocked(blocked_env, "-m", "anymarine.cli", "new", "--path", str(root))
    assert first.returncode == 0
    second = run_blocked(blocked_env, "-m", "anymarine.cli", "new", "--path", str(root))
    assert second.returncode == 2
    assert "refusing to overwrite" in second.stderr


def test_cli_reports_corrupt_project_actionably(blocked_env, tmp_path):
    root = tmp_path / "proj"
    run_blocked(blocked_env, "-m", "anymarine.cli", "new", "--path", str(root))
    sidecar = next((root / "bodies").glob("*.json"))
    sidecar.write_text(sidecar.read_text() + "corrupt", encoding="utf-8")
    result = run_blocked(blocked_env, "-m", "anymarine.cli", "validate", "--project", str(root))
    assert result.returncode == 1
    assert "corrupt" in result.stderr or "sha256" in result.stderr


def test_worker_rejects_invalid_input(blocked_env, tmp_path):
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    result = run_blocked(
        blocked_env, "-m", "anymarine.worker", str(input_path), str(output_path)
    )
    assert result.returncode == 2
    assert not output_path.exists()  # no partial success
