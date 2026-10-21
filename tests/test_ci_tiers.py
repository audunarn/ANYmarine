"""Tests for the tier and parallel-job tooling (tools/ci_tiers.py, tools/dev_checks.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import ci_tiers  # noqa: E402
import dev_checks  # noqa: E402

IDS = [f"tests/test_a.py::test_{i}" for i in range(40)] + [f"tests/test_b.py::TestC::test_{i}[p{i}]" for i in range(25)]
TABLE = {"slow_files": ["tests/test_a.py"], "tests": {IDS[41]: 99.0, IDS[42]: 0.4, IDS[50]: 70.0}}


def _every_id_once(groups):
    flat = [i for group in groups for i in group]
    assert sorted(flat) == sorted(IDS) and len(flat) == len(set(flat))


@pytest.mark.parametrize("count", [1, 2, 3, 7, 100])
def test_lpt_places_every_id_exactly_once(count):
    _every_id_once(dev_checks.lpt(dev_checks.estimate(TABLE, IDS), count))


@pytest.mark.parametrize("spec", ["1/3", "2/3", "3/3"])
def test_shards_partition_the_selection(spec):
    _every_id_once([dev_checks.shard_of(IDS, TABLE, f"{k}/3") for k in (1, 2, 3)])
    assert set(dev_checks.shard_of(IDS, TABLE, spec)) <= set(IDS)


@pytest.mark.parametrize("pack", [True, False])
def test_jobs_cover_the_selection_exactly(monkeypatch, pack):
    monkeypatch.setattr(dev_checks, "PACK_JOBS", pack)
    jobs = dev_checks.make_jobs(IDS, TABLE, 4)
    _every_id_once([job["ids"] for job in jobs])
    if pack:
        assert len(jobs) <= 4


def test_big_file_is_split_by_test_id(monkeypatch):
    monkeypatch.setattr(dev_checks, "PACK_JOBS", False)
    jobs = dev_checks.make_jobs(IDS, TABLE, 4)
    assert len([job for job in jobs if job["files"] == ["tests/test_b.py"]]) > 1


def test_slow_rule_uses_time_and_slow_files():
    assert ci_tiers.is_slow(TABLE, IDS[41], threshold=1.0)
    assert not ci_tiers.is_slow(TABLE, IDS[42], threshold=1.0)
    assert ci_tiers.is_slow(TABLE, "tests/test_a.py::test_not_in_table", threshold=1.0)
    assert not ci_tiers.is_slow(TABLE, "tests/test_b.py::TestC::test_unknown", threshold=1.0)


def test_junit_names_map_to_node_ids():
    this = Path(__file__).stem
    assert ci_tiers.junit_node_id(f"tests.{this}", "test_x") == f"tests/{this}.py::test_x"
    assert ci_tiers.junit_node_id(f"tests.{this}.TestK", "test_x[a-1]") == f"tests/{this}.py::TestK::test_x[a-1]"
    assert ci_tiers.junit_node_id("tests.no_such_module", "test_x") is None


def test_missing_and_failed_tests_are_visible_in_the_junit_read(tmp_path):
    this = Path(__file__).stem
    (tmp_path / "job000.xml").write_text(
        f'<testsuites><testsuite>'
        f'<testcase classname="tests.{this}" name="test_one"/>'
        f'<testcase classname="tests.{this}" name="test_two"><failure message="x"/></testcase>'
        f'<testcase classname="tests.{this}" name="test_three"><skipped/></testcase>'
        f'<testcase classname="tests.{this}" name="test_four"><error message="x"/></testcase>'
        f'<testcase classname="other" name="t"/></testsuite></testsuites>', encoding="utf-8")
    outcomes, unmapped = dev_checks.read_junit(tmp_path)
    base = f"tests/{this}.py::"
    assert outcomes == {base + "test_one": "passed", base + "test_two": "failed",
                        base + "test_three": "skipped", base + "test_four": "error"}
    assert unmapped == ["other::t"]
    assert base + "test_five" not in outcomes   # an absent id stays absent, so the run reports it as missing


# --- review round 1 regressions -------------------------------------------------------------------------------
@pytest.mark.parametrize("code,failing,consistent", [
    (0, 0, True), (1, 2, True),          # clean pass; failing tests reported
    (0, 1, False),                        # exit 0 but a failure in the JUnit file
    (1, 0, False),                        # session-level failure: no failing test explains the exit status
    (5, 0, False), (2, 0, False), ("timeout", 0, False), (-11, 3, False),
])
def test_job_exit_status_must_match_its_junit(code, failing, consistent):
    assert dev_checks.job_consistent(code, failing) is consistent


def test_empty_collection_is_refused(monkeypatch):
    class Done:
        returncode, stdout, stderr = 5, "no tests collected\n", ""
    monkeypatch.setattr(dev_checks.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(SystemExit):
        dev_checks.collect(None)


def test_update_keeps_unreported_tests_and_drops_vanished_ones(tmp_path):
    table = tmp_path / "durations.json"
    table.write_text('{"slow_files": ["tests/x.py"], "tests": {"tests/a.py::old": 5.0, "tests/a.py::gone": 7.0}}',
                     encoding="utf-8")
    ci_tiers.write_table({"tests/a.py::new": 2.0}, table, keep_ids={"tests/a.py::old", "tests/a.py::new"})
    data = ci_tiers.load_table(table)
    assert data["tests"] == {"tests/a.py::new": 2.0, "tests/a.py::old": 5.0}
    assert data["slow_files"] == ["tests/x.py"]


def test_timeout_kills_the_whole_process_tree(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = (f"import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']);"
            f"open(r'{pid_file}','w').write(str(p.pid)); time.sleep(120)")
    status, _ = dev_checks.run_with_timeout([sys.executable, "-c", code], cwd=tmp_path, env=dict(__import__("os").environ), timeout=4)
    assert status == "timeout"
    pid = int(pid_file.read_text())
    import time
    for _ in range(20):
        if not dev_checks.pid_alive(pid):
            break
        time.sleep(0.25)
    assert not dev_checks.pid_alive(pid)


# --- review round 2 regressions -------------------------------------------------------------------------------
def test_empty_shard_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_checks, "collect", lambda marker: ["tests/test_a.py::test_only"])
    with pytest.raises(SystemExit):
        dev_checks.main(["--tier", "full", "--shard", "2/2", "--workdir", str(tmp_path / "w")])


def test_failures_file_cannot_overwrite_the_baseline(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_checks, "collect", lambda marker: ["tests/test_a.py::test_only"])
    baseline = tmp_path / "known.txt"
    baseline.write_text("tests/test_a.py::test_only\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        dev_checks.main(["--tier", "full", "--workdir", str(tmp_path / "w"),
                         "--expect-failures", str(baseline), "--write-failures", str(baseline)])
    assert baseline.read_text(encoding="utf-8") == "tests/test_a.py::test_only\n"


def test_a_child_holding_the_output_pipe_does_not_hold_up_a_finished_job(tmp_path):
    code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
            "print('done')")
    import time
    started = time.time()
    status, out = dev_checks.run_with_timeout([sys.executable, "-c", code], cwd=tmp_path,
                                              env=dict(__import__("os").environ), timeout=20)
    assert status == 0 and "done" in out and time.time() - started < 15   # Windows: the leftover child is not killed (known limit)


# --- fix round: workdir safety, process-group kill, guard input, empty slow tier ----------------------------------
import os  # noqa: E402


def _run_main_with_workdir(monkeypatch, workdir):
    monkeypatch.setattr(dev_checks, "collect", lambda marker, **kw: ["tests/test_a.py::test_only"])
    calls = []
    monkeypatch.setattr(dev_checks.shutil, "rmtree", lambda *a, **k: calls.append(a[0]))
    with pytest.raises(SystemExit):
        dev_checks.main(["--tier", "full", "--workdir", str(workdir)])
    return calls


def test_workdir_refuses_repo_root_ancestors_home_and_fs_root(monkeypatch, tmp_path):
    for bad in (dev_checks.ROOT, dev_checks.ROOT.parent, Path(dev_checks.ROOT.anchor), Path.home()):
        assert _run_main_with_workdir(monkeypatch, bad) == [], bad


def test_workdir_refuses_a_foreign_nonempty_dir_and_keeps_its_files(tmp_path):
    foreign = tmp_path / "mine"
    foreign.mkdir()
    (foreign / "precious.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit):
        dev_checks.prepare_workdir(foreign)
    assert (foreign / "precious.txt").exists()


def test_workdir_own_dir_is_recreated_and_fresh_dir_is_used(tmp_path):
    work = tmp_path / "w"
    dev_checks.prepare_workdir(work)                      # fresh: created with a marker
    (work / "job000.xml").write_text("old", encoding="utf-8")
    dev_checks.prepare_workdir(work)                      # own (marker present): emptied and recreated
    assert (work / "tmp").is_dir() and not (work / "job000.xml").exists()
    empty = tmp_path / "e"
    empty.mkdir()
    dev_checks.prepare_workdir(empty)                     # existing empty dir is fine


def test_workdir_refuses_a_link_and_does_not_touch_its_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    if os.name == "nt":
        import subprocess
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)
        made = True
    if not made:
        pytest.skip("cannot create a link here")
    with pytest.raises(SystemExit):
        dev_checks.prepare_workdir(link)
    assert (target / "keep.txt").exists()


def test_kill_tree_signals_the_group_captured_at_start_even_after_the_parent_is_reaped(monkeypatch):
    import signal
    class Gone:
        pid = 4321
        def kill(self):
            raise AssertionError("must signal the group, not only the parent")
    seen = []
    def getpgid(pid):
        raise ProcessLookupError
    monkeypatch.setattr(dev_checks.os, "name", "posix")
    monkeypatch.setattr(dev_checks.os, "getpgid", getpgid, raising=False)
    monkeypatch.setattr(dev_checks.os, "killpg", lambda pg, sig: seen.append(pg), raising=False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    dev_checks._kill_tree(Gone())
    assert seen == [4321]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_a_descendant_of_a_finished_job_is_killed(tmp_path):
    pid_file = tmp_path / "child.pid"
    code = (f"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
            f"open(r'{pid_file}','w').write(str(p.pid))")
    status, _ = dev_checks.run_with_timeout([sys.executable, "-c", code], cwd=tmp_path, env=dict(os.environ), timeout=20)
    assert status == 0
    import time
    pid = int(pid_file.read_text())
    for _ in range(20):
        if not dev_checks.pid_alive(pid):
            break
        time.sleep(0.25)
    assert not dev_checks.pid_alive(pid)


_CASE = '<testsuite><testcase classname="{c}" name="{n}" time="{t}"/></testsuite>'


def test_guard_rejects_unmapped_cases(tmp_path, capsys):
    xml = tmp_path / "a.xml"
    xml.write_text(_CASE.format(c="unmapped.module", n="test_slow", t=99), encoding="utf-8")
    assert ci_tiers.main(["guard", str(xml)]) != 0
    assert "could not be mapped" in capsys.readouterr().err


def test_guard_and_update_reject_empty_input(tmp_path, capsys):
    assert ci_tiers.main(["guard", str(tmp_path)]) != 0
    assert "no JUnit" in capsys.readouterr().err
    assert ci_tiers.main(["update", str(tmp_path), "--out", str(tmp_path / "t.json")]) != 0
    assert not (tmp_path / "t.json").exists()


def _fake_collect(monkeypatch, tiers):
    def collect(marker, allow_empty=False):
        ids = tiers[marker]
        if not ids and not allow_empty:
            sys.exit("collection failed")
        return ids
    monkeypatch.setattr(dev_checks, "collect", collect)
    monkeypatch.setattr(dev_checks.ci_tiers, "load_table", lambda *a: {"slow_files": [], "tests": {}})


def test_verify_accepts_an_empty_slow_tier(monkeypatch):
    ids = ["tests/a.py::t1", "tests/a.py::t2"]
    _fake_collect(monkeypatch, {None: ids, "slow": [], "not slow": ids})
    assert dev_checks.verify() == 0


def test_verify_rejects_an_empty_full_tier(monkeypatch):
    _fake_collect(monkeypatch, {None: [], "slow": [], "not slow": []})
    with pytest.raises(SystemExit):
        dev_checks.verify()


def test_collect_allow_empty_accepts_only_pytest_no_tests_status(monkeypatch):
    class NoTests:
        returncode, stdout, stderr = 5, "no tests collected\n", ""
    class Broken:
        returncode, stdout, stderr = 2, "", "boom"
    monkeypatch.setattr(dev_checks.subprocess, "run", lambda *a, **k: NoTests())
    assert dev_checks.collect("slow", allow_empty=True) == []
    monkeypatch.setattr(dev_checks.subprocess, "run", lambda *a, **k: Broken())
    with pytest.raises(SystemExit):
        dev_checks.collect("slow", allow_empty=True)


# --- fix round 2: marker ownership, empty fast tier, cleanup errors ------------------------------------------------
_HEADER = "anymarine tools/dev_checks.py workdir token\n"


def _foreign_with_marker(tmp_path, content):
    d = tmp_path / "foreign"
    d.mkdir()
    (d / "precious.txt").write_text("x", encoding="utf-8")
    if content is not None:
        (d / dev_checks.WORKDIR_MARKER).write_text(content, encoding="utf-8")
    return d


@pytest.mark.parametrize("content", ["", "created by tools/dev_checks.py", _HEADER + "nothex\n"])
def test_workdir_refuses_a_foreign_dir_with_a_marker_named_file_of_wrong_content(tmp_path, content):
    d = _foreign_with_marker(tmp_path, content)
    with pytest.raises(SystemExit):
        dev_checks.prepare_workdir(d)
    assert (d / "precious.txt").exists()


def test_workdir_refuses_a_symlink_marker(tmp_path):
    d = _foreign_with_marker(tmp_path, None)
    good = tmp_path / "good"
    good.write_text(_HEADER + "0" * 32 + "\n", encoding="utf-8")
    try:
        (d / dev_checks.WORKDIR_MARKER).symlink_to(good)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create a symlink here")
    with pytest.raises(SystemExit):
        dev_checks.prepare_workdir(d)
    assert (d / "precious.txt").exists()


def test_workdir_marker_is_a_random_token_per_directory(tmp_path):
    dev_checks.prepare_workdir(tmp_path / "a")
    dev_checks.prepare_workdir(tmp_path / "b")
    ta = (tmp_path / "a" / dev_checks.WORKDIR_MARKER).read_text(encoding="utf-8")
    tb = (tmp_path / "b" / dev_checks.WORKDIR_MARKER).read_text(encoding="utf-8")
    assert ta != tb and ta.startswith(_HEADER)


def test_workdir_cleanup_error_is_reported_and_dir_not_reused(monkeypatch, tmp_path):
    work = tmp_path / "w"
    dev_checks.prepare_workdir(work)
    (work / "stale.xml").write_text("old", encoding="utf-8")
    def boom(path, *a, **k):
        if k.get("ignore_errors"):
            return                      # old behaviour: error swallowed, directory left in place
        raise PermissionError("locked")
    monkeypatch.setattr(dev_checks.shutil, "rmtree", boom)
    with pytest.raises(SystemExit) as exc:
        dev_checks.prepare_workdir(work)
    assert "cannot clean" in str(exc.value)
    assert (work / "stale.xml").exists()


def test_verify_rejects_an_empty_fast_tier(monkeypatch):
    ids = ["tests/a.py::t1"]
    _fake_collect(monkeypatch, {None: ids, "slow": ids, "not slow": []})
    with pytest.raises(SystemExit):
        dev_checks.verify()
