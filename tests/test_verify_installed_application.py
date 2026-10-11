"""Oracle tests for tools/verify_installed_application.py helpers.

These check the verification tool's own pure helpers: hashing, snapshot
copy manifests, refusal of existing outputs, wheel name parsing, target
interpreter lookup, parser wiring and syntax validity of the embedded
inspection script and the installed-tests guard conftest. They create no
venv, install nothing and touch no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import verify_installed_application as tool  # noqa: E402


# ------------------------------------------------------------------ hashing


def test_sha256_bytes_known_vector():
    assert (
        tool.sha256_bytes(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_sha256_file_matches_bytes(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"abc")
    assert tool.sha256_file(path) == tool.sha256_bytes(b"abc")


# ---------------------------------------------------------------- refusals


def test_refuse_if_exists_rejects_existing_path(tmp_path):
    existing = tmp_path / "out"
    existing.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        tool.refuse_if_exists(existing, "snapshot dir")
    assert str(excinfo.value).startswith("2|")


def test_refuse_if_exists_allows_missing_path(tmp_path):
    tool.refuse_if_exists(tmp_path / "missing", "snapshot dir")


# ----------------------------------------------------------------- snapshot


def test_copy_repo_snapshot_manifest(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "src" / "pkg" / "mod.py").write_bytes(b"print('hi')\n")
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / "LICENSE").write_text("license\n", encoding="utf-8")
    (repo / "src" / "pkg" / "__pycache__").mkdir()
    (repo / "src" / "pkg" / "__pycache__" / "mod.cpython-313.pyc").write_bytes(b"junk")

    manifest = tool.copy_repo_snapshot(repo, tmp_path / "snap")

    assert set(manifest) == {
        "pyproject.toml", "README.md", "LICENSE",
        "src/pkg/__init__.py", "src/pkg/mod.py",
    }
    snapshot_mod = tmp_path / "snap" / "src" / "pkg" / "mod.py"
    assert snapshot_mod.read_bytes() == b"print('hi')\n"
    assert manifest["src/pkg/mod.py"] == tool.sha256_bytes(b"print('hi')\n")
    assert not (tmp_path / "snap" / "src" / "pkg" / "__pycache__").exists()


def test_copy_repo_snapshot_refuses_missing_pyproject(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        tool.copy_repo_snapshot(repo, tmp_path / "snap")
    assert str(excinfo.value).startswith("2|")


# -------------------------------------------------------------- build helpers


def test_wheel_dist_name():
    assert tool.wheel_dist_name(Path("anymarine-0.1.0-py3-none-any.whl")) == "anymarine"
    assert tool.wheel_dist_name(Path("anyhydro-0.1.0-py3-none-any.whl")) == "anyhydro"


def test_target_python_prefers_windows_layout(tmp_path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "python.exe").write_bytes(b"")
    assert tool.target_python(tmp_path) == scripts / "python.exe"


def test_target_python_refuses_missing_interpreter(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        tool.target_python(tmp_path)
    assert str(excinfo.value).startswith("2|")


# ------------------------------------------------- embedded scripts and guard


def test_inspection_source_is_valid_python():
    compile(tool.INSPECTION_SOURCE, "inspection.py", "exec")


def test_guard_conftest_is_valid_python():
    compile(tool.GUARD_CONTEST, "guard_conftest.py", "exec")


def test_inspection_source_denies_checkout_and_editable_origins():
    source = tool.INSPECTION_SOURCE
    assert "c:/github" in source
    assert "editable" in source
    assert "ANYmarine GUI dependencies are unavailable" in source
    assert "blocked for verification" in source


def test_guard_conftest_refuses_checkout_paths():
    assert "c:/github" in tool.GUARD_CONTEST
    assert "installed-tests guard" in tool.GUARD_CONTEST


# ------------------------------------------------------------ parser wiring


def test_parser_wires_all_subcommands():
    parser = tool.build_parser()
    cases = {
        "snapshot": ["snapshot", "--marine-root", "m", "--hydro-root", "h",
                     "--output-dir", "o"],
        "build": ["build", "--snapshot-dir", "s", "--output-dir", "o"],
        "install": ["install", "--wheel-marine", "m.whl", "--wheel-hydro",
                    "h.whl", "--target-dir", "t", "--borrow-site-packages",
                    "C:/runtime/Lib/site-packages"],
        "verify": ["verify", "--target-dir", "t", "--wheel-marine", "m.whl",
                   "--wheel-hydro", "h.whl", "--snapshot-dir", "s",
                   "--report-dir", "r"],
        "installed-tests": ["installed-tests", "--target-dir", "t",
                            "--report-dir", "r", "--test-sources", "x.py"],
    }
    for name, argv in cases.items():
        args = parser.parse_args(argv)
        assert args.command == name
        assert callable(args.handler)


def test_expected_entry_points_constant():
    assert tool.EXPECTED_ENTRY_POINTS == {
        "anymarine": "anymarine.cli:main",
        "anymarine-gui": "anymarine.gui:main",
    }

# ---------------------------------------------------- donor snapshot helpers


def test_donor_snapshot_copies_metadata_src_and_licenses(tmp_path):
    donor = tmp_path / "ANYdonor"
    (donor / "src" / "anydonor").mkdir(parents=True)
    (donor / "src" / "anydonor" / "__init__.py").write_text("X = 1\n", encoding="utf-8", newline="\n")
    (donor / "src" / "anydonor" / "py.typed").write_bytes(b"marker\n")
    (donor / "pyproject.toml").write_text("[project]\n", encoding="utf-8", newline="\n")
    (donor / "README.md").write_text("readme\n", encoding="utf-8", newline="\n")
    (donor / "LICENSE").write_text("license\n", encoding="utf-8", newline="\n")
    (donor / "NOTICE").write_text("notice\n", encoding="utf-8", newline="\n")
    (donor / "docs").mkdir()
    (donor / "docs" / "LICENSE.md").write_text("docs license\n", encoding="utf-8", newline="\n")
    (donor / "tests").mkdir()
    (donor / "tests" / "test_donor.py").write_text("def test_x(): pass\n", encoding="utf-8", newline="\n")
    (donor / "src" / "anydonor" / "__pycache__").mkdir()
    (donor / "src" / "anydonor" / "__pycache__" / "m.pyc").write_bytes(b"junk")

    copied, module = tool.copy_donor_snapshot(donor, tmp_path / "build" / "ANYdonor")

    assert module == "anydonor"
    assert copied["pyproject.toml"] == tool.sha256_file(donor / "pyproject.toml")
    assert copied["LICENSE"] == tool.sha256_bytes(b"license\n")
    assert copied["NOTICE"] == tool.sha256_bytes(b"notice\n")
    assert copied["docs/LICENSE.md"] == tool.sha256_bytes(b"docs license\n")
    assert copied["src/anydonor/__init__.py"] == tool.sha256_bytes(b"X = 1\n")
    assert copied["src/anydonor/py.typed"] == tool.sha256_bytes(b"marker\n")
    build = tmp_path / "build" / "ANYdonor"
    assert (build / "src" / "anydonor" / "__init__.py").read_bytes() == b"X = 1\n"
    assert not (build / "tests").exists()
    assert not (build / "src" / "anydonor" / "__pycache__").exists()


def test_donor_snapshot_refuses_missing_pyproject(tmp_path):
    donor = tmp_path / "ANYdonor"
    (donor / "src" / "anydonor").mkdir(parents=True)
    (donor / "src" / "anydonor" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        tool.copy_donor_snapshot(donor, tmp_path / "build" / "ANYdonor")
    assert str(excinfo.value).startswith("2|")


def test_donor_snapshot_refuses_multiple_packages(tmp_path):
    donor = tmp_path / "ANYdonor"
    donor.mkdir()
    (donor / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    for name in ("anydonor", "other"):
        (donor / "src" / name).mkdir(parents=True)
        (donor / "src" / name / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        tool.copy_donor_snapshot(donor, tmp_path / "build" / "ANYdonor")
    assert str(excinfo.value).startswith("2|")


# --------------------------------------------------- new parser subcommands


def test_parser_wires_donor_build_and_install_extra():
    parser = tool.build_parser()
    donor = parser.parse_args(
        ["donor-build", "--donor", "ANYgeometry=C:/Github/ANYgeometry",
         "--donor", "ANYmesh=C:/Github/ANYmesh",
         "--output-dir", "o", "--build-dir", "b"])
    assert donor.command == "donor-build" and donor.donor == [
        "ANYgeometry=C:/Github/ANYgeometry", "ANYmesh=C:/Github/ANYmesh"]
    extra = parser.parse_args(
        ["install-extra", "--target-dir", "t", "--wheel", "a.whl",
         "--wheel", "b.whl"])
    assert extra.command == "install-extra" and extra.wheel == ["a.whl", "b.whl"]
    verify = parser.parse_args(
        ["verify", "--target-dir", "t", "--wheel-marine", "m.whl",
         "--wheel-hydro", "h.whl", "--snapshot-dir", "s", "--report-dir", "r",
         "--extra-wheel", "e1.whl", "--extra-wheel", "e2.whl",
         "--dependency-manifest", "dm.json"])
    assert verify.extra_wheel == ["e1.whl", "e2.whl"]
    assert verify.dependency_manifest == "dm.json"


def test_inspection_source_wires_extra_wheels_and_exe_gate():
    source = tool.INSPECTION_SOURCE
    assert "--extra-wheel" in source
    assert "--dependency-manifest" in source
    assert "console_script_help_command" in source
    assert "unmet: OS denied executing the installed console-script exe" in source
    assert "SAVED_CONSUMER_REPLAY_OK" in source
    assert "BLOCKER_NOT_ENGAGED" in source


# ------------------------------------- failure aggregation negative control


def test_injected_guard_failure_aggregates_nonzero(tmp_path):
    """Negative control: a deliberately injected guard failure must make the
    embedded inspection aggregate result fail with a nonzero exit code.

    The injection is real (a checkout path placed on the child sys.path), so
    this exercises the actual aggregation path, not the source text.
    """
    import json
    import os
    import subprocess
    import zipfile

    marine_wheel = tmp_path / "anymarine-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(marine_wheel, "w") as archive:
        archive.writestr("anymarine/__init__.py", "X = 1\n")
    hydro_wheel = tmp_path / "anyhydro-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(hydro_wheel, "w") as archive:
        archive.writestr("anyhydro/__init__.py", "X = 1\n")
    manifest = tmp_path / "snapshot-manifest.json"
    manifest.write_text(json.dumps({"marine": {}, "hydro": {}}), encoding="utf-8")
    script = tmp_path / "inspection.py"
    script.write_text(tool.INSPECTION_SOURCE, encoding="utf-8")
    result_json = tmp_path / "inspection-result.json"

    environment = dict(os.environ)
    # Injected guard failure: a checkout directory on the child sys.path.
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, str(script),
         "--marine-wheel", str(marine_wheel),
         "--hydro-wheel", str(hydro_wheel),
         "--snapshot-manifest", str(manifest),
         "--json-out", str(result_json),
         "--deadline", "6"],
        capture_output=True, text=True, timeout=120, env=environment,
    )
    assert completed.returncode != 0, completed.stdout
    report = json.loads(result_json.read_text(encoding="utf-8"))
    assert report["result"] == "fail"
    assert report["failures"], "aggregation lost the injected failure"
    assert report["checks"].get("path_guard") == "fail"
    assert any(f.startswith("path_guard:") for f in report["failures"])


def donor_fixture(root, declaration):
    (root / "src" / "owned").mkdir(parents=True)
    (root / "src" / "owned" / "__init__.py").write_text("# owned\n")
    (root / "pyproject.toml").write_text(
        '[project]\nname="Owned"\nversion="1.0"\nlicense-files='+declaration+'\n')
    (root / "README.md").write_text("example")
    (root / "LICENSE").write_text("MPL-2.0")
    return root


def test_donor_copies_declared_nested_license(tmp_path):
    root=donor_fixture(tmp_path / "donor", '["legal/NOTICE.txt"]')
    (root / "legal").mkdir()
    (root / "legal" / "NOTICE.txt").write_text("upstream notice")
    manifest, module=tool.copy_donor_snapshot(root,tmp_path / "snapshot")
    assert module=="owned"
    assert (tmp_path / "snapshot" / "legal" / "NOTICE.txt").read_text()=="upstream notice"
    assert "legal/NOTICE.txt" in manifest


def test_donor_refuses_license_path_escape(tmp_path):
    root=donor_fixture(tmp_path / "donor", '["../outside.txt"]')
    (tmp_path / "outside.txt").write_text("outside")
    with pytest.raises(SystemExit, match="escapes donor"):
        tool.copy_donor_snapshot(root,tmp_path / "snapshot")


def test_donor_refuses_missing_declared_license(tmp_path):
    root=donor_fixture(tmp_path / "donor", '["missing-notice.txt"]')
    with pytest.raises(SystemExit, match="declared license file missing"):
        tool.copy_donor_snapshot(root,tmp_path / "snapshot")


# --------------------------------------------- Linux/WSL platform support


def test_target_site_packages_asks_the_effective_interpreter(tmp_path, monkeypatch):
    from types import SimpleNamespace

    site = tmp_path / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(tool, "target_python",
                        lambda target: tmp_path / "bin" / "python")
    completed = SimpleNamespace(returncode=0, stdout=str(site) + "\n", stderr="")
    monkeypatch.setattr(tool.subprocess, "run",
                        lambda *args, **kwargs: completed)
    assert tool.target_site_packages(tmp_path) == site


def test_target_site_packages_refuses_failed_interpreter_query(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(tool, "target_python",
                        lambda target: tmp_path / "bin" / "python")
    completed = SimpleNamespace(returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(tool.subprocess, "run",
                        lambda *args, **kwargs: completed)
    with pytest.raises(SystemExit) as excinfo:
        tool.target_site_packages(tmp_path)
    assert str(excinfo.value).startswith("2|")


def test_inspection_deny_path_covers_linux_checkout_mounts():
    namespace = {"__name__": "inspection_extract"}
    exec(tool.INSPECTION_SOURCE, namespace)
    deny_path = namespace["deny_path"]
    assert deny_path("C:\\Github\\ANYmarine\\src")
    assert deny_path("/mnt/c/Github/ANYmarine/src")
    assert not deny_path("/var/tmp/anymarine-linux-gui-20261011/target/lib/python3.12/site-packages")


def test_inspection_source_uses_effective_site_dir_and_mounts():
    source = tool.INSPECTION_SOURCE
    assert 'DENY_PREFIXES = ("c:/github", "/mnt/c/github")' in source
    assert "startswith(DENY_PREFIX)" not in source
    assert 'sysconfig.get_paths()["purelib"]' in source
    assert "lib/python%d.%d/site-packages" not in source


def test_inspection_source_base_exe_is_platform_aware():
    source = tool.INSPECTION_SOURCE
    assert ('"Scripts/anymarine.exe" if sys.platform == "win32" '
            'else "bin/anymarine"') in source


def test_guard_conftest_denies_linux_checkout_mounts():
    assert 'startswith(("c:/github", "/mnt/c/github"))' in tool.GUARD_CONTEST


def test_parser_wires_donor_build_frontend():
    parser = tool.build_parser()
    args = parser.parse_args(
        ["donor-build", "--donor", "ANYmesher=/donor", "--output-dir", "o",
         "--build-dir", "b", "--build-frontend", "pip-wheel"])
    assert args.build_frontend == "pip-wheel"
    default = parser.parse_args(
        ["donor-build", "--donor", "ANYmesher=/donor", "--output-dir", "o",
         "--build-dir", "b"])
    assert default.build_frontend == "build"


def test_parser_wires_pip_less_venv_options():
    parser = tool.build_parser()
    args = parser.parse_args(
        ["install", "--wheel-marine", "m.whl", "--wheel-hydro", "h.whl",
         "--target-dir", "t", "--borrow-site-packages", "/sp",
         "--venv-extra-args", "--without-pip --copies",
         "--pip-python", "/donor/bin/python"])
    assert args.venv_extra_args == "--without-pip --copies"
    assert args.pip_python == "/donor/bin/python"
    default = parser.parse_args(
        ["install", "--wheel-marine", "m.whl", "--wheel-hydro", "h.whl",
         "--target-dir", "t", "--borrow-site-packages", "/sp"])
    assert default.venv_extra_args == "" and default.pip_python is None
    extra = parser.parse_args(
        ["install-extra", "--target-dir", "t", "--wheel", "a.whl",
         "--pip-python", "/donor/bin/python"])
    assert extra.pip_python == "/donor/bin/python"


def test_install_refuses_system_site_packages(tmp_path):
    parser = tool.build_parser()
    args = parser.parse_args(
        ["install", "--wheel-marine", "m.whl", "--wheel-hydro", "h.whl",
         "--target-dir", str(tmp_path / "target"),
         "--borrow-site-packages", str(tmp_path),
         "--venv-extra-args", "--without-pip --system-site-packages"])
    with pytest.raises(SystemExit) as excinfo:
        tool.command_install(args)
    assert str(excinfo.value).startswith("2|")
    assert "--system-site-packages" in str(excinfo.value)


def test_native_preparation_control_refuses_missing_marine_compiler(tmp_path):
    """A version-compatible legacy wheel must fail the public caller control."""
    import subprocess
    import sys
    namespace={"__name__":"inspection_helpers"}
    exec(tool.INSPECTION_SOURCE,namespace)
    code=namespace["native_preparation_code"]()
    missing=chr(10).join([
        "import sys",
        "class MissingMarineCompiler:",
        "    def find_spec(self,fullname,path=None,target=None):",
        "        if fullname == 'anysolver.marine_mechanical':",
        "            raise ModuleNotFoundError('legacy wheel has no marine compiler')",
        "        return None",
        "sys.meta_path.insert(0,MissingMarineCompiler())",
        "",
    ])
    done=subprocess.run([sys.executable,"-c",missing+code],capture_output=True,
                        text=True,timeout=20,cwd=tmp_path)
    assert done.returncode != 0
    assert "NATIVE_PREPARATION_OK" not in done.stdout
    assert "marine_mechanical_v1" in done.stderr
