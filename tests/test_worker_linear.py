"""Isolated worker linear jobs: success, cancel, refusal and fail-closed paths."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SOLVER_SRC, SRC, requires_solver

pytestmark = requires_solver

from anymarine.domain import LINEAR_MAX_FRAMES  # noqa: E402
from anymarine.linear import analytic_demo_inputs  # noqa: E402


def worker_env(with_solver: bool = True) -> dict:
    env = os.environ.copy()
    parts = [str(SRC)]
    if with_solver and SOLVER_SRC.is_dir():
        parts.append(str(SOLVER_SRC))
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def run_worker(input_path: Path, output_path: Path, cancel_path: Path | None, env: dict):
    command = [sys.executable, "-m", "anymarine.worker", str(input_path), str(output_path)]
    if cancel_path is not None:
        command.append(str(cancel_path))
    return subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=60
    )


@pytest.fixture()
def linear_snapshot(service, project, tmp_path):
    inputs = analytic_demo_inputs(project, n_steps=LINEAR_MAX_FRAMES)
    snapshot = service.linear_job_snapshot(project, inputs)
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    cancel_path = tmp_path / "cancel"
    service.write_job_input(snapshot, input_path)
    return snapshot, input_path, output_path, cancel_path


def test_worker_success_validates_output(service, linear_snapshot):
    snapshot, input_path, output_path, cancel_path = linear_snapshot
    completed = run_worker(input_path, output_path, cancel_path, worker_env())
    assert completed.returncode == 0, completed.stderr
    assert "DONE" in completed.stdout
    result = service.load_job_output(output_path, snapshot["input_hash"])
    assert result.kind == "linear_development"
    expected_ids = tuple(b["body_id"] for b in snapshot["inputs"]["bindings"])
    assert result.body_ids == expected_ids
    assert len(result.times) == snapshot["inputs"]["n_steps"]


def test_worker_cancel_exits_three_without_output(linear_snapshot):
    _snapshot, input_path, output_path, cancel_path = linear_snapshot
    cancel_path.touch()  # cancel before the first accepted step
    completed = run_worker(input_path, output_path, cancel_path, worker_env())
    assert completed.returncode == 3, completed.stderr
    assert not output_path.exists()


def test_worker_cancel_mid_run(linear_snapshot):
    _snapshot, input_path, output_path, cancel_path = linear_snapshot
    process = subprocess.Popen(
        [
            sys.executable, "-m", "anymarine.worker",
            str(input_path), str(output_path), str(cancel_path),
        ],
        env=worker_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Give the worker time to start and accept steps, then cancel.
        import time

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not cancel_path.exists():
            cancel_path.touch()
            if process.poll() is not None:
                break
            time.sleep(0.05)
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 3
    assert not output_path.exists()


def test_worker_refuses_corrupt_snapshot(service, tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text("{ not json", encoding="utf-8")
    completed = run_worker(
        input_path, tmp_path / "out.json", None, worker_env()
    )
    assert completed.returncode == 2


def test_worker_refuses_hash_mismatch(linear_snapshot):
    snapshot, input_path, output_path, cancel_path = linear_snapshot
    data = json.loads(input_path.read_text(encoding="utf-8"))
    data["input_hash"] = "0" * 64
    input_path.write_text(json.dumps(data), encoding="utf-8")
    completed = run_worker(input_path, output_path, cancel_path, worker_env())
    assert completed.returncode == 2
    assert not output_path.exists()


def test_worker_refuses_stale_body_binding(linear_snapshot):
    snapshot, input_path, output_path, cancel_path = linear_snapshot
    data = json.loads(input_path.read_text(encoding="utf-8"))
    data["project"]["bodies"][0]["mass"] *= 2.0
    input_path.write_text(json.dumps(data), encoding="utf-8")
    completed = run_worker(input_path, output_path, cancel_path, worker_env())
    assert completed.returncode == 2
    assert "stale" in completed.stderr
    assert not output_path.exists()


def test_worker_fails_closed_without_solver(linear_snapshot):
    _snapshot, input_path, output_path, cancel_path = linear_snapshot
    env = worker_env(with_solver=False)
    blocker = """
import sys, runpy
class BlockSolver:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'anysolver' or fullname.startswith('anysolver.'):
            raise ModuleNotFoundError('optional solver deliberately unavailable')
sys.meta_path.insert(0, BlockSolver())
sys.argv = ['anymarine.worker', *sys.argv[1:]]
runpy.run_module('anymarine.worker', run_name='__main__')
"""
    completed = subprocess.run(
        [sys.executable, "-c", blocker, str(input_path), str(output_path), str(cancel_path)],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert completed.returncode == 4
    assert "anysolver.marine_linear" in completed.stderr
    assert not output_path.exists()
