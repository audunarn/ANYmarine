"""Repeatable local wheel reproduction and installed-application verification.

Builds fresh ``anymarine`` and ``anyhydro`` wheels from a disposable snapshot
of the current checkouts (no live-tree build side effects), installs exactly
those two wheels with ``pip --no-index --no-deps`` into an isolated target
venv in the workspace, and verifies the installed application consumer:
module and distribution origins, package bytes against the wheels and the
snapshot, entry points, Qt-free core, public launcher controls and focused
copied tests under a guard conftest.

Scope: this is dependency-reuse verification, not a clean base install and
not a full dependency-graph release claim. Dependencies (PySide6, ANY3dView,
numpy, ANYloads, ANYsolver, ...) are borrowed from the preinstalled evidence
runtime through explicit inert directory entries in a clean venv; their
exact versions and origins are recorded. Broad system site-packages and
checkout editable finders are not inherited. No network, no global installs, no numerical jobs.

Subcommands are separately launchable so each fits a bounded pool:

    python tools/verify_installed_application.py snapshot \
        --marine-root C:/Github/ANYmarine --hydro-root C:/Github/ANYhydro \
        --output-dir <workspace>/snapshot
    python tools/verify_installed_application.py build \
        --snapshot-dir <workspace>/snapshot --output-dir <workspace>/wheels
    python tools/verify_installed_application.py install \
        --wheel-marine <wheels>/anymarine-*.whl \
        --wheel-hydro <wheels>/anyhydro-*.whl \
        --target-dir <workspace>/target
    python tools/verify_installed_application.py verify \
        --target-dir <workspace>/target --snapshot-dir <workspace>/snapshot \
        --wheel-marine <wheels>/anymarine-*.whl \
        --wheel-hydro <wheels>/anyhydro-*.whl \
        --report-dir <workspace>/reports/<attempt>
    python tools/verify_installed_application.py installed-tests \
        --target-dir <workspace>/target --report-dir <workspace>/reports/<attempt> \
        --test-sources <repo>/tests/test_gui_launcher.py

Exit codes: 0 pass, 1 verification failure, 2 usage or environment refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_REFUSAL = 2

SNAPSHOT_FILES = ("pyproject.toml", "README.md", "LICENSE")
EXPECTED_ENTRY_POINTS = {
    "anymarine": "anymarine.cli:main",
    "anymarine-gui": "anymarine.gui:main",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def refuse_if_exists(path: Path, what: str) -> None:
    if path.exists():
        raise SystemExit(f"{EXIT_REFUSAL}|refusing to overwrite existing {what}: {path}")


# ------------------------------------------------------------- snapshot step


def copy_repo_snapshot(repo_root: Path, dest: Path) -> dict:
    """Copy src/, pyproject.toml, README.md, LICENSE into a snapshot folder."""
    copied: dict[str, str] = {}
    for name in SNAPSHOT_FILES:
        source = repo_root / name
        if not source.is_file():
            raise SystemExit(f"{EXIT_REFUSAL}|missing {name} in {repo_root}; refusing snapshot")
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied[name] = sha256_file(target)
    src = repo_root / "src"
    if not src.is_dir():
        raise SystemExit(f"{EXIT_REFUSAL}|missing src/ in {repo_root}")
    for source in sorted(src.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(src)
        target = dest / "src" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied[f"src/{relative.as_posix()}"] = sha256_file(target)
    return copied


def command_snapshot(args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    refuse_if_exists(output, "snapshot dir")
    started = time.time()
    manifest = {
        "marine": copy_repo_snapshot(Path(args.marine_root), output / "marine"),
        "hydro": copy_repo_snapshot(Path(args.hydro_root), output / "hydro"),
    }
    manifest_path = output / "snapshot-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    summary = {
        "snapshot_dir": str(output),
        "marine_files": len(manifest["marine"]),
        "hydro_files": len(manifest["hydro"]),
        "manifest": str(manifest_path),
        "wall_seconds": round(time.time() - started, 2),
    }
    print(json.dumps(summary))
    return EXIT_PASS


# --------------------------------------------------------------- build step


def wheel_dist_name(path: Path) -> str:
    return path.name.split("-")[0]


def command_build(args: argparse.Namespace) -> int:
    snapshot = Path(args.snapshot_dir)
    output = Path(args.output_dir)
    refuse_if_exists(output, "wheel output dir")
    output.mkdir(parents=True)
    started = time.time()
    report: dict = {"wheels": [], "builds": []}
    for repo in ("marine", "hydro"):
        repo_dir = snapshot / repo
        if not (repo_dir / "pyproject.toml").is_file():
            raise SystemExit(f"{EXIT_REFUSAL}|snapshot missing pyproject.toml: {repo_dir}")
        completed = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation",
             "--outdir", str(output)],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        report["builds"].append(
            {
                "repo": repo,
                "command": "python -m build --wheel --no-isolation",
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-2000:],
                "stderr_tail": completed.stderr[-2000:],
            }
        )
        if completed.returncode != 0:
            report["result"] = "fail"
            print(json.dumps(report))
            return EXIT_FAIL
    wheels = sorted(p for p in output.glob("*.whl"))
    names = {wheel_dist_name(w) for w in wheels}
    if names != {"anymarine", "anyhydro"}:
        raise SystemExit(
            f"{EXIT_REFUSAL}|built wheels are not exactly anymarine+anyhydro: {sorted(names)}"
        )
    for wheel in wheels:
        report["wheels"].append(
            {
                "path": str(wheel),
                "name": wheel.name,
                "sha256": sha256_file(wheel),
                "bytes": wheel.stat().st_size,
            }
        )
    report["result"] = "pass"
    report["wall_seconds"] = round(time.time() - started, 2)
    (output / "build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return EXIT_PASS


# ------------------------------------------------------------- install step


def target_python(target_dir: Path) -> Path:
    for candidate in (target_dir / "Scripts" / "python.exe", target_dir / "bin" / "python"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"{EXIT_REFUSAL}|no python found in target venv {target_dir}")


def target_site_packages(target_dir: Path) -> Path:
    """Effective site-packages dir of the target venv interpreter.

    Asked from the target interpreter itself (``sysconfig`` purelib),
    so the answer is correct for the actual venv layout on every
    platform; the hardcoded ``Lib/site-packages`` vs
    ``lib/site-packages`` guess places the borrow ``.pth`` file in a
    nonexistent ``lib/site-packages`` on Linux venvs.
    """
    completed = subprocess.run(
        [str(target_python(target_dir)), "-I", "-c",
         "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise SystemExit(
            f"{EXIT_REFUSAL}|cannot determine target site dir: "
            + (completed.stderr.strip() or "no output")[-300:]
        )
    site = Path(completed.stdout.strip().splitlines()[-1])
    if not site.is_dir():
        raise SystemExit(f"{EXIT_REFUSAL}|target site dir not found: {site}")
    return site


def command_install(args: argparse.Namespace) -> int:
    target = Path(args.target_dir)
    refuse_if_exists(target, "target venv dir")
    started = time.time()
    borrow_dirs = [Path(entry) for entry in args.borrow_site_packages]
    for borrow in borrow_dirs:
        if not borrow.is_dir():
            raise SystemExit(f"{EXIT_REFUSAL}|borrow site-packages not found: {borrow}")
    extra_venv_args = shlex.split(getattr(args, "venv_extra_args", "") or "")
    if "--system-site-packages" in extra_venv_args:
        raise SystemExit(
            f"{EXIT_REFUSAL}|venv-extra-args must not include "
            "--system-site-packages (clean venv contract)"
        )
    venv_command = [sys.executable, "-m", "venv"]
    venv_command += extra_venv_args
    venv_command.append(str(target))
    created = subprocess.run(
        venv_command,
        capture_output=True, text=True, timeout=args.timeout,
    )
    report = {
        "venv_command": " ".join(venv_command)
                       + " (clean, no system-site-packages)",
        "venv_returncode": created.returncode,
        "venv_stderr_tail": created.stderr[-1000:],
        "base_python": sys.executable,
        "borrowed_dependencies": (
            "PySide6/ANY3dView/numpy/ANYloads/ANYsolver/pytest/Pillow are "
            "borrowed only from explicitly supplied directories by a generated "
            ".pth entry added to "
            "the target venv site-packages; the borrowed .pth files of those "
            "directories stay inert, so no editable finder activates; this is "
            "dependency reuse, not a clean base install or a full "
            "dependency-graph release"
        ),
        "borrow_site_packages": [str(entry) for entry in borrow_dirs],
    }
    if created.returncode != 0:
        report["result"] = "fail"
        print(json.dumps(report))
        return EXIT_FAIL
    wheels = [Path(args.wheel_marine).resolve(), Path(args.wheel_hydro).resolve()]
    for wheel in wheels:
        if not wheel.is_file():
            raise SystemExit(f"{EXIT_REFUSAL}|wheel not found: {wheel}")
    if args.pip_python:
        pip_command = [str(Path(args.pip_python)), "-m", "pip", "--python",
                       str(target_python(target)), "install"]
    else:
        pip_command = [str(target_python(target)), "-m", "pip", "install"]
    pip_command += ["--no-index", "--no-deps", *[str(w) for w in wheels]]
    installed = subprocess.run(
        pip_command,
        capture_output=True, text=True, timeout=args.timeout,
    )
    report.update(
        {
            "pip_command": " ".join(pip_command),
            "pip_python": str(args.pip_python) if args.pip_python else "",
            "pip_returncode": installed.returncode,
            "pip_stdout_tail": installed.stdout[-2000:],
            "pip_stderr_tail": installed.stderr[-2000:],
            "wheel_sha256": {w.name: sha256_file(w) for w in wheels},
        }
    )
    if installed.returncode == 0:
        pth = (target_site_packages(target)
               / "_borrow_evidence_runtime_sitepackages.pth")
        pth.write_text("".join(str(entry) + "\n" for entry in borrow_dirs),
                       encoding="utf-8")
        report["borrow_pth"] = str(pth)
    report["result"] = "pass" if installed.returncode == 0 else "fail"
    report["wall_seconds"] = round(time.time() - started, 2)
    print(json.dumps(report))
    return EXIT_PASS if installed.returncode == 0 else EXIT_FAIL


# --------------------------------------------------------------- verify step


INSPECTION_SOURCE = '''\
import argparse
import hashlib
import importlib
import importlib.metadata
import json
import subprocess
import sys
import sysconfig
import time
import zipfile
from pathlib import Path

DENY_PREFIXES = ("c:/github", "/mnt/c/github")
QT_BASE_PREFIXES = ("PySide6", "PyQt", "shiboken")
BLOCKED_GUI_TOP = ("PySide6", "any3dview", "shiboken6", "PyQt6", "PyQt5")
EXPECTED_ENTRY_POINTS = {
    "anymarine": "anymarine.cli:main",
    "anymarine-gui": "anymarine.gui:main",
}


def deny_path(value):
    text = str(value).replace(chr(92), "/").casefold()
    return any(text.startswith(prefix) for prefix in DENY_PREFIXES)


def deny_text(value):
    text = str(value).replace(chr(92), "/").casefold()
    return any(prefix in text for prefix in DENY_PREFIXES)


def license_member(name):
    base = name.rsplit("/", 1)[-1].casefold()
    return (base.startswith(("license", "notice", "copyright",
                             "dependency-licenses"))
            or base in ("sources.md", "license_data.md"))


def registered_site_dirs():
    """Directories whose .pth files actually execute at interpreter start."""
    import site
    dirs = []
    for path in site.getsitepackages():
        candidate = Path(path)
        if candidate.is_dir():
            dirs.append(candidate.resolve())
    return dirs


def site_dirs():
    """All site-packages directories visible on sys.path (for file lookup)."""
    dirs = registered_site_dirs()
    for entry in sys.path:
        if entry:
            candidate = Path(entry)
            if candidate.is_dir() and candidate.name == "site-packages":
                resolved = candidate.resolve()
                if resolved not in dirs:
                    dirs.append(resolved)
    return dirs


def native_preparation_code():
    """Exercise the installed public native compiler, with no integration."""
    return chr(10).join([
        "import sys",
        "class NativeBlocker:",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if fullname.split('.')[0] in ('capytaine', 'PySide6', 'any3dview', 'shiboken6'):",
        "            raise ImportError('blocked in native preparation control: ' + fullname)",
        "        return None",
        "sys.meta_path.insert(0, NativeBlocker())",
        "from anymarine.services import ApplicationService",
        "service = ApplicationService()",
        "project = service.load_native_morison_example()",
        "candidate = service.load_native_inputs(project, project.native_inputs.to_dict())",
        "model, states, spec = service.prepare_native_run(candidate)",
        "assert set(states) == {b.body_id for b in project.bodies}",
        "assert not any(m.split('.')[0] in ('capytaine', 'PySide6', 'any3dview', 'shiboken6') for m in sys.modules)",
        "print('NATIVE_PREPARATION_OK')",
        "",
    ])


def run_checks(args, started):
    report = {"result": "pass", "failures": [], "checks": {}, "records": {}}
    checks = report["checks"]

    def fail(name, message):
        checks[name] = "fail"
        report["failures"].append(name + ": " + message)

    def ok(name):
        checks.setdefault(name, "pass")

    # 1. sys.path guard: no checkout directory may feed imports.
    bad_paths = [e for e in sys.path if e and deny_path(Path(e).resolve())]
    if bad_paths:
        fail("path_guard", "checkout paths on sys.path: " + repr(bad_paths))
    else:
        ok("path_guard")
    report["records"]["sys_path"] = list(sys.path)
    report["records"]["python"] = sys.executable
    report["records"]["python_version"] = sys.version.split()[0]
    report["records"]["sys_prefix"] = sys.prefix
    report["records"]["base_prefix"] = sys.base_prefix

    controls = {}

    def run_control(name, argv, expect_code, stdout_has=None, stderr_has=None,
                    stderr_forbids=("Traceback",), required=True):
        budget = max(2.0, args.deadline - (time.perf_counter() - started) - 2.0)
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=budget)
        except subprocess.TimeoutExpired:
            controls[name] = {"exit": "timeout"}
            if required:
                fail("launcher_controls", name + " timed out")
            return "timeout"
        except OSError as error:
            controls[name] = {"exit": "oserror", "error": str(error)}
            if required:
                fail("launcher_controls", name + " OS error: " + str(error))
            return "oserror"
        record = {"exit": done.returncode,
                  "stdout_tail": done.stdout[-300:],
                  "stderr_tail": done.stderr[-300:]}
        controls[name] = record
        problems = []
        if done.returncode != expect_code:
            problems.append("exit " + str(done.returncode) + " != " + str(expect_code))
        if stdout_has and stdout_has not in done.stdout:
            problems.append("stdout missing " + repr(stdout_has))
        if stderr_has and stderr_has not in done.stderr:
            problems.append("stderr missing " + repr(stderr_has))
        if any(forbidden in done.stderr for forbidden in stderr_forbids):
            problems.append("stderr contains traceback")
        if problems:
            if required:
                fail("launcher_controls", name + ": " + repr(problems))
            return "mismatch"
        ok("launcher_controls_" + name)
        return "pass"

    # 2/3. module and distribution origins in the isolated target.
    import anymarine
    import anyhydro
    prefix = Path(sys.prefix).resolve()
    origins = {}
    for name, module in (("anymarine", anymarine), ("anyhydro", anyhydro)):
        module_path = Path(module.__file__).resolve()
        entry = {"module": str(module_path), "in_target": prefix in module_path.parents}
        try:
            dist = importlib.metadata.distribution(name)
            dist_path = Path(str(getattr(dist, "_path", ""))).resolve()
            entry["version"] = dist.version
            entry["distribution"] = str(dist_path)
            entry["dist_in_target"] = prefix in dist_path.parents
        except Exception as error:
            entry["distribution_error"] = type(error).__name__ + ": " + str(error)
            entry["dist_in_target"] = False
        origins[name] = entry
        if not entry["in_target"] or not entry["dist_in_target"]:
            fail("module_origins", name + " not isolated in target venv: " + repr(entry))
    report["records"]["origins"] = origins
    if not any(f.startswith("module_origins") for f in report["failures"]):
        ok("module_origins")

    # 4. editable finder / checkout import denial.
    editable_finders = [
        finder.__class__.__name__ for finder in sys.meta_path
        if "editable" in finder.__class__.__name__.casefold()
    ]
    dirty_pth = []
    # Only .pth files in registered site dirs execute; .pth files inside
    # borrowed directories added through our own .pth stay inert, so they
    # cannot activate editable finders and are not denial violations.
    for directory in registered_site_dirs():
        for pth in directory.glob("*.pth"):
            try:
                text = pth.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "__editable__" in pth.name or deny_text(text):
                dirty_pth.append(str(pth))
    if editable_finders or dirty_pth:
        fail("editable_denial", "finders=" + repr(editable_finders) + " pth=" + repr(dirty_pth))
    else:
        ok("editable_denial")

    # 5. borrowed dependencies: present, from installed runtime only.
    borrowed = {}
    for dist_name, import_name in (
        ("ANYloads", "anyloads"),
        ("ANYsolver", "anysolver"),
        ("PySide6", "PySide6"),
        ("ANY3dView", "any3dview"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("xarray", "xarray"),
        ("capytaine", "capytaine"),
        ("Pillow", "PIL"),
    ):
        try:
            module = importlib.import_module(import_name)
            origin = str(Path(module.__file__).resolve())
            try:
                version = importlib.metadata.version(dist_name)
            except Exception:
                version = "unknown"
            borrowed[dist_name] = {"version": version, "origin": origin}
            if deny_path(origin):
                fail("borrowed_deps", dist_name + " imported from checkout: " + origin)
        except Exception as error:
            borrowed[dist_name] = {"error": type(error).__name__ + ": " + str(error)}
    for required in ("ANYloads", "ANYsolver"):
        if "error" in borrowed.get(required, {}):
            fail(
                "borrowed_deps",
                required + " is not importable from the installed runtime alone ("
                + borrowed[required]["error"] + "); the runtime reaches it only "
                "through the system site-packages, which this verifier refuses "
                "to borrow because it carries editable finders pointing at the "
                "C:/Github source checkouts; installing anything needs a root "
                "decision, this verifier refuses to do it",
            )
    report["records"]["borrowed"] = borrowed
    if not any(f.startswith("borrowed_deps") for f in report["failures"]):
        ok("borrowed_deps")

    # 5b. explicit extra dependency wheels (donor-built): each must be
    # installed in the target venv itself with a version matching the wheel,
    # carry owned/upstream license or notice files, declare its requirements,
    # and import from a module origin inside the target, never a checkout.
    extra = {}
    manifest_donors = {}
    if args.dependency_manifest:
        manifest_path = Path(args.dependency_manifest)
        if not manifest_path.is_file():
            fail("dependency_manifest", "manifest not found: " + str(manifest_path))
        else:
            try:
                manifest_data = json.loads(
                    manifest_path.read_text(encoding="utf-8"))
                manifest_donors = {d["name"]: d
                                  for d in manifest_data.get("donors", [])}
                report["records"]["dependency_manifest_donors"] = sorted(manifest_donors)
            except Exception as error:
                fail("dependency_manifest",
                     "unreadable: " + type(error).__name__ + ": " + str(error))
    for wheel_arg in args.extra_wheel:
        wheel_path = Path(wheel_arg)
        if not wheel_path.is_file():
            fail("extra_dependency_wheels", "wheel not found: " + str(wheel_path))
            continue
        dist_name = wheel_path.name.split("-")[0]
        version = wheel_path.name.split("-")[1]
        with zipfile.ZipFile(wheel_path) as wheel:
            names = wheel.namelist()
            metadata_name = next((n for n in names
                                  if n.endswith(".dist-info/METADATA")), None)
            if metadata_name is None:
                fail("extra_dependency_wheels",
                     "no METADATA in " + wheel_path.name)
                continue
            metadata_text = wheel.read(metadata_name).decode("utf-8", "replace")
            requires = [line.split(":", 1)[1].strip()
                        for line in metadata_text.splitlines()
                        if line.startswith("Requires-Dist:")]
            license_members = sorted(n for n in names if license_member(n))
        entry = {"wheel": str(wheel_path), "distribution": dist_name,
                 "version": version, "requires_dist": requires,
                 "license_files_in_wheel": license_members}
        if not license_members:
            fail("extra_dependency_wheels",
                 "no owned/upstream license or notice files in "
                 + wheel_path.name)
        try:
            installed_version = importlib.metadata.version(dist_name)
        except Exception as error:
            installed_version = None
            entry["version_error"] = type(error).__name__ + ": " + str(error)
        entry["installed_version"] = installed_version
        if installed_version != version:
            fail("extra_dependency_wheels",
                 dist_name + " installed version " + repr(installed_version)
                 + " != wheel version " + repr(version))
        donor = manifest_donors.get(dist_name)
        if donor is not None and donor.get("module"):
            module_name = donor["module"]
            try:
                module = importlib.import_module(module_name)
                origin = str(Path(module.__file__).resolve())
                entry["module"] = origin
                if Path(sys.prefix).resolve() not in Path(origin).parents:
                    fail("extra_dependency_wheels",
                         module_name + " not installed in the target venv: "
                         + origin)
                if deny_path(origin):
                    fail("extra_dependency_wheels",
                         module_name + " imported from checkout: " + origin)
            except Exception as error:
                fail("extra_dependency_wheels",
                     module_name + " import failed: "
                     + type(error).__name__ + ": " + str(error))
        if donor is not None:
            if hashlib.sha256(wheel_path.read_bytes()).hexdigest() != donor.get("sha256"):
                fail("extra_dependency_wheels", "donor wheel hash changed: " + dist_name)
            mismatches = []
            target_site = Path(sysconfig.get_paths()["purelib"])
            with zipfile.ZipFile(wheel_path) as wheel:
                for member in wheel.infolist():
                    if member.is_dir() or member.filename.endswith("/RECORD"):
                        continue
                    installed = target_site / member.filename
                    if not installed.is_file() or installed.read_bytes() != wheel.read(member.filename):
                        mismatches.append(member.filename)
                manifest = donor.get("source_manifest", {})
                for source, digest in manifest.items():
                    if source.startswith("src/") and source.endswith(".py"):
                        member = source[4:]
                        if member not in names or hashlib.sha256(wheel.read(member)).hexdigest() != digest:
                            mismatches.append("source:" + source)
                for source in donor.get("declared_license_files", []):
                    matches = [n for n in names if n.endswith(".dist-info/licenses/" + source)
                               or ".dist-info/licenses/" in n and n.split(".dist-info/licenses/", 1)[1] == source]
                    if not matches or any(hashlib.sha256(wheel.read(n)).hexdigest() != manifest.get(source) for n in matches):
                        mismatches.append("declared-license:" + source)
            entry["byte_mismatches"] = mismatches
            if mismatches:
                fail("extra_dependency_wheels", dist_name + " byte/license mismatch " + repr(mismatches[:5]))
        extra[dist_name] = entry
    report["records"]["extra_dependency_wheels"] = extra
    if args.extra_wheel and not any(
            f.startswith("extra_dependency_wheels") for f in report["failures"]):
        ok("extra_dependency_wheels")

    # Verify active dependency requirements, including propagated extras.
    # A successful import is insufficient when an installed version is too old.
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    queue = [("anymarine", {"gui", "native-morison", "native-waves"}),
             ("anyhydro", set()), ("ANY3dView", {"qt", "gpu"})]
    seen = set()
    dependency_constraints = []
    while queue:
        name, extras = queue.pop()
        key = (canonicalize_name(name), tuple(sorted(extras)))
        if key in seen:
            continue
        seen.add(key)
        try:
            requirements = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            fail("dependency_constraints", "missing distribution " + name)
            continue
        for text in requirements:
            requirement = Requirement(text)
            if requirement.marker and not any(
                requirement.marker.evaluate({"extra": extra})
                for extra in extras | {""}
            ):
                continue
            try:
                actual = importlib.metadata.version(requirement.name)
                valid = not requirement.url and actual in requirement.specifier
            except importlib.metadata.PackageNotFoundError:
                actual, valid = None, False
            dependency_constraints.append({"owner": name, "requirement": text,
                                           "installed_version": actual, "passed": valid})
            if not valid:
                fail("dependency_constraints", name + " requires " + text
                     + "; installed=" + repr(actual))
            else:
                queue.append((requirement.name, set(requirement.extras)))
    report["records"]["dependency_constraints"] = dependency_constraints
    if not any(f.startswith("dependency_constraints:") for f in report["failures"]):
        ok("dependency_constraints")

    # Direct proof: nothing imported so far may resolve under a checkout.
    checkout_modules = sorted(
        name for name, module in list(sys.modules.items())
        if getattr(module, "__file__", None)
        and deny_path(getattr(module, "__file__"))
    )
    report["records"]["checkout_modules_loaded"] = checkout_modules
    if checkout_modules:
        fail("checkout_import_denial",
             "modules imported from checkout: " + repr(checkout_modules[:10]))
    else:
        ok("checkout_import_denial")

    # 6. Qt-free core in a clean interpreter: importing core, CLI and the
    # launcher module pulls no Qt base (this process already imported PySide6
    # for the borrowed-dependency record, so the oracle needs a subprocess).
    nl = chr(10)
    qt_free_code = nl.join([
        "import sys",
        "import anymarine, anymarine.cli, anymarine.gui",
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('PySide6', 'PyQt', 'shiboken')]",
        "print('QT_LOADED:' + ','.join(bad) if bad else 'QT_FREE')",
        "sys.exit(1 if bad else 0)",
        "",
    ])
    run_control("qt_free_core", [sys.executable, "-c", qt_free_code], 0,
                stdout_has="QT_FREE")

    # Version metadata alone cannot establish public marine compiler capability.
    run_control("native_preparation", [sys.executable, "-c", native_preparation_code()], 0,
                stdout_has="NATIVE_PREPARATION_OK")

    # 7. optional BEM backend absence proven with an import blocker in a
    #    clean interpreter (this process already imported capytaine for the
    #    borrowed-dependency record, so the blocker would never engage).
    bem_code = chr(10).join([
        "import sys",
        "class Blocker:",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if fullname.split('.')[0] == 'capytaine':",
        "            raise ImportError('blocked for verification: ' + fullname)",
        "        return None",
        "sys.meta_path.insert(0, Blocker())",
        "import anymarine.wave_examples",
        "loaded = [m for m in sys.modules if m.split('.')[0] == 'capytaine']",
        "print('BEM_LOADED:' + ','.join(loaded) if loaded else 'BEM_ABSENT')",
        "sys.exit(1 if loaded else 0)",
        "",
    ])
    run_control("optional_backend_absence", [sys.executable, "-c", bem_code], 0,
                stdout_has="BEM_ABSENT")

    # 7b. saved-consumer reopen workflow with the optional backend blocked
    #     throughout, in a fresh child: native input/sidecar save + reopen,
    #     surface editing with staleness, and stored-array replay must not
    #     need the blocked backend. The blocker is proven engaged by a
    #     direct probe before the workflow runs, and proven still unimported
    #     after it.
    saved_code = chr(10).join([
        "import json",
        "import sys",
        "import tempfile",
        "from dataclasses import replace",
        "from pathlib import Path",
        "class Blocker:",
        "    def find_spec(self, fullname, path=None, target=None):",
        "        if fullname.split('.')[0] == 'capytaine':",
        "            raise ImportError('blocked for verification: ' + fullname)",
        "        return None",
        "sys.meta_path.insert(0, Blocker())",
        "try:",
        "    import capytaine",
        "    print('BLOCKER_NOT_ENGAGED')",
        "    sys.exit(1)",
        "except ImportError as error:",
        "    if 'blocked for verification' not in str(error):",
        "        raise",
        "from anymarine.services import ApplicationService",
        "from anymarine.plate_example import attached_plate_project",
        "from anymarine.surface_assignments import HydroSurfaceAssignment",
        "service = ApplicationService()",
        "native = service.load_native_example()",
        "root = Path(tempfile.mkdtemp()) / 'saved-native'",
        "service.save_current(native, root)",
        "manifest = json.loads((root / 'project.json').read_text(encoding='utf-8'))",
        "assert manifest['native_inputs']['sidecar'].startswith('native_inputs/')",
        "reopened = ApplicationService().load(root)",
        "assert reopened.native_inputs.to_dict() == native.native_inputs.to_dict()",
        "plate = attached_plate_project()",
        "plate_root = Path(tempfile.mkdtemp()) / 'saved-plate'",
        "service.save_current(plate, plate_root)",
        "reopened_plate = ApplicationService().load(plate_root)",
        "assert reopened_plate.to_dict() == plate.to_dict()",
        "from anymarine.hashing import input_hash",
        "old_plate_hash = input_hash(plate)",
        "body = plate.bodies[0]",
        "sheet = body.hydro_surfaces[-1]",
        "semantics = replace(sheet.semantics,",
        "                    minus_side='edited-' + str(sheet.semantics.minus_side))",
        "edited_sheet = HydroSurfaceAssignment(sheet.role, semantics, sheet.mesh)",
        "edited_body = body.with_edits(hydro_surfaces=tuple(",
        "    edited_sheet if s.semantics.surface_id == sheet.semantics.surface_id else s",
        "    for s in body.hydro_surfaces))",
        "edited = plate.with_edits(bodies=tuple(",
        "    edited_body if b.body_id == body.body_id else b for b in plate.bodies))",
        "assert input_hash(edited) != old_plate_hash",
        "example = ApplicationService().load_example()",
        "stored = example.results[0]",
        "assert stored.channels and stored.times",
        "assert all(len(stored.positions[b]) == len(stored.times)",
        "           for b in stored.body_ids)",
        "assert tuple(stored.body_ids) == tuple(b.body_id for b in example.bodies)",
        "saved = Path(tempfile.mkdtemp()) / 'stored-replay'",
        "service.save_current(example, saved)",
        "roundtrip = ApplicationService().load(saved)",
        "assert roundtrip.results[0].to_dict() == stored.to_dict()",
        "assert service.stale_result_ids(roundtrip) == ()",
        "body = roundtrip.bodies[0]",
        "changed = roundtrip.with_edits(bodies=tuple(",
        "    b.with_edits(mass=b.mass * 1.01) if b.body_id == body.body_id else b",
        "    for b in roundtrip.bodies))",
        "assert service.stale_result_ids(changed) == (stored.result_id,)",
        "from anymarine.transforms import frame_transform",
        "import numpy as np",
        "for body_id in stored.body_ids:",
        "    for i in (0, stored.frame_count() // 2, stored.frame_count() - 1):",
        "        transform = frame_transform(stored.positions[body_id][i], stored.orientations[body_id][i])",
        "        assert np.array_equal(transform[:3, 3], stored.positions[body_id][i])",
        "        assert np.allclose(transform[:3,:3].T @ transform[:3,:3], np.eye(3), atol=1e-12)",
        "from anymarine.persistence import ProjectError",
        "from anymarine.services import ServiceError",
        "sidecar = root / manifest['native_inputs']['sidecar']",
        "assert sidecar.resolve().is_relative_to(root.resolve())",
        "original = sidecar.read_bytes()",
        "for damaged in (None, b'{}'):",
        "    if damaged is None:",
        "        sidecar.unlink()",
        "    else:",
        "        sidecar.write_bytes(damaged)",
        "    try:",
        "        ApplicationService().load(root)",
        "        raise AssertionError('damaged sidecar accepted')",
        "    except (ProjectError, ServiceError):",
        "        pass",
        "    finally:",
        "        sidecar.write_bytes(original)",
        "assert not [m for m in sys.modules if m.split('.')[0] == 'capytaine']",
        "print('SAVED_CONSUMER_REPLAY_OK')",
        "",
    ])
    run_control("saved_consumer_blocked_backend",
                [sys.executable, "-c", saved_code], 0,
                stdout_has="SAVED_CONSUMER_REPLAY_OK")

    # 8/9. installed package bytes against wheel and snapshot.
    snapshot = json.loads(Path(args.snapshot_manifest).read_text(encoding="utf-8"))
    wheel_map = {"anymarine": Path(args.marine_wheel), "anyhydro": Path(args.hydro_wheel)}
    package_counts = {}
    directories = site_dirs()
    for package, wheel_path in wheel_map.items():
        with zipfile.ZipFile(wheel_path) as wheel:
            members = {
                info.filename: hashlib.sha256(wheel.read(info.filename)).hexdigest()
                for info in wheel.infolist()
                if not info.is_dir()
            }
        package_dir = Path(importlib.import_module(package).__file__).resolve().parent
        installed_files = 0
        installed_bytes = 0
        mismatches = []
        for member, member_sha in members.items():
            parts = member.split("/")
            if parts[-1] == "RECORD":
                continue
            if parts[0].endswith(".dist-info"):
                installed = None
                for directory in directories:
                    candidate = directory / member
                    if candidate.is_file():
                        installed = candidate
                        break
                if installed is None:
                    mismatches.append("missing dist-info file " + member)
                    continue
            else:
                installed = package_dir.parent / member
                if not installed.is_file():
                    mismatches.append("missing package file " + member)
                    continue
            data = installed.read_bytes()
            installed_files += 1
            installed_bytes += len(data)
            if hashlib.sha256(data).hexdigest() != member_sha:
                mismatches.append("byte mismatch " + member)
            if member.startswith(package + "/"):
                rel = "src/" + member
                repo = "marine" if package == "anymarine" else "hydro"
                expected = snapshot.get(repo, {}).get(rel)
                if expected is None:
                    mismatches.append("no snapshot entry " + repo + ":" + rel)
                elif expected != member_sha:
                    mismatches.append("snapshot mismatch " + repo + ":" + rel)
        extra_installed = []
        for installed in package_dir.rglob("*"):
            if installed.is_file() and "__pycache__" not in installed.parts:
                rel = installed.relative_to(package_dir.parent).as_posix()
                if rel not in members:
                    extra_installed.append(rel)
        package_counts[package] = {
            "wheel_members": len(members),
            "installed_files_checked": installed_files,
            "installed_bytes": installed_bytes,
            "mismatches": mismatches,
            "extra_installed_files": extra_installed,
        }
        if mismatches or extra_installed:
            fail("package_bytes_" + package,
                 repr(mismatches[:5]) + " extra=" + repr(extra_installed[:5]))
        else:
            ok("package_bytes_" + package)
    report["records"]["package_counts"] = package_counts

    # 10. entry points and console script.
    entry_points = {}
    for package in ("anymarine", "anyhydro"):
        dist = importlib.metadata.distribution(package)
        path = Path(str(getattr(dist, "_path", "")))
        ep_file = path / "entry_points.txt"
        entry_points[package] = ep_file.read_text(encoding="utf-8") if ep_file.is_file() else ""
    report["records"]["entry_points"] = entry_points
    text = entry_points["anymarine"]
    missing = [k + " = " + v for k, v in EXPECTED_ENTRY_POINTS.items()
               if k + " = " + v not in text]
    script = Path(sys.prefix) / "Scripts" / "anymarine-gui.exe"
    if not script.is_file():
        script = Path(sys.prefix) / "bin" / "anymarine-gui"
    if missing or not script.is_file():
        fail("entry_points", "missing " + repr(missing) + " script=" + str(script))
    else:
        ok("entry_points")
    report["records"]["gui_script"] = str(script)

    # 11. launcher controls in subprocesses.
    # The console gate must prove the ACTUAL installed exe script. Loading
    # the entry function through the interpreter is not the exe: when the OS
    # blocks the launcher exe the gate is UNMET, the failure is retained and
    # the aggregation stays nonzero. The function load below is recorded as
    # supplementary evidence only, never as a pass of the actual command.
    controls["console_script_help_command"] = [str(script), "--help"]
    exe_outcome = run_control("console_script_help", [str(script), "--help"], 0,
                              stdout_has="ANYmarine development desktop",
                              required=False)
    if exe_outcome == "oserror":
        base_exe = Path(sys.base_prefix) / (
            "Scripts/anymarine.exe" if sys.platform == "win32" else "bin/anymarine")
        base_status = "absent"
        if base_exe.is_file():
            try:
                done = subprocess.run([str(base_exe), "--help"],
                                      capture_output=True, text=True, timeout=10)
                base_status = "exit " + str(done.returncode)
            except OSError as error:
                base_status = "oserror: " + str(error)
        controls["console_script_exe_environment"] = {
            "target_exe": str(script),
            "exact_command": [str(script), "--help"],
            "base_runtime_exe": str(base_exe),
            "base_runtime_exe_status": base_status,
            "note": "the OS denies executing pip console-script launcher "
                    "exes on this machine (the pre-existing runtime console "
                    "exe is denied identically); the gate is UNMET and the "
                    "entry-function load below is supplementary evidence "
                    "only, not a pass of the actual command",
        }
        fail("console_script_exe",
             "unmet: OS denied executing the installed console-script exe "
             + str(script) + "; the actual command was not proven and is "
             "not claimed (the entry-function load is supplementary "
             "evidence only)")
        run_control(
            "console_entry_function_help",
            [sys.executable, "-c",
             "import sys; from anymarine.gui import main; "
             "sys.exit(main(['--help']))"],
            0, stdout_has="ANYmarine development desktop", required=False)
    elif exe_outcome != "pass":
        fail("launcher_controls", "console_script_help " + exe_outcome)
    run_control("module_help", [sys.executable, "-m", "anymarine.gui", "--help"], 0,
                stdout_has="ANYmarine development desktop")
    blocker_code = (
        "import sys\\n"
        "class Blocker:\\n"
        "    def find_spec(self, fullname, path=None, target=None):\\n"
        "        if fullname.split('.')[0] in "
        "('PySide6', 'any3dview', 'shiboken6', 'PyQt6', 'PyQt5'):\\n"
        "            raise ImportError('blocked for verification: ' + fullname)\\n"
        "        return None\\n"
        "sys.meta_path.insert(0, Blocker())\\n"
        "from anymarine.gui import main\\n"
        "sys.exit(main([]))\\n"
    )
    run_control("missing_qt_diagnostic", [sys.executable, "-c", blocker_code], 2,
                stderr_has="ANYmarine GUI dependencies are unavailable")
    report["records"]["launcher_controls"] = controls

    # 12. bundled examples present in the installed package.
    examples = Path(anymarine.__file__).resolve().parent / "examples"
    example_files = [p for p in examples.rglob("*") if p.is_file()]
    report["records"]["examples"] = {
        "dir": str(examples),
        "files": len(example_files),
        "bytes": sum(p.stat().st_size for p in example_files),
    }
    if not example_files:
        fail("examples_installed", "no bundled example files in installed package")
    else:
        ok("examples_installed")

    if report["failures"]:
        report["result"] = "fail"
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--marine-wheel", required=True)
    parser.add_argument("--hydro-wheel", required=True)
    parser.add_argument("--snapshot-manifest", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--extra-wheel", action="append", default=[])
    parser.add_argument("--dependency-manifest", default="")
    parser.add_argument("--deadline", type=float, default=20.0)
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        report = run_checks(args, started)
    except Exception as error:  # partial report survives any exception
        import traceback

        report = {
            "result": "fail",
            "failures": ["crashed: " + type(error).__name__ + ": " + str(error)],
            "checks": {},
            "records": {},
            "error": type(error).__name__ + ": " + str(error),
            "traceback": traceback.format_exc(),
        }
    Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"result": report["result"], "failures": report["failures"],
                      "checks": report["checks"]}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def command_verify(args: argparse.Namespace) -> int:
    target = Path(args.target_dir)
    report_dir = Path(args.report_dir)
    refuse_if_exists(report_dir, "report dir")
    report_dir.mkdir(parents=True)
    python = target_python(target)
    snapshot_manifest = Path(args.snapshot_dir) / "snapshot-manifest.json"
    for required in (args.wheel_marine, args.wheel_hydro, snapshot_manifest):
        if not Path(required).is_file():
            raise SystemExit(f"{EXIT_REFUSAL}|missing input: {required}")
    inspection = report_dir / "inspection.py"
    inspection.write_text(INSPECTION_SOURCE, encoding="utf-8")
    result_path = report_dir / "inspection-result.json"
    started = time.time()
    command = [str(python), str(inspection),
               "--marine-wheel", str(Path(args.wheel_marine).resolve()),
               "--hydro-wheel", str(Path(args.wheel_hydro).resolve()),
               "--snapshot-manifest", str(snapshot_manifest.resolve()),
               "--json-out", str(result_path.resolve()),
               "--deadline", str(args.timeout)]
    for wheel in (args.extra_wheel or []):
        command += ["--extra-wheel", str(Path(wheel).resolve())]
    if args.dependency_manifest:
        command += ["--dependency-manifest",
                    str(Path(args.dependency_manifest).resolve())]
    completed = subprocess.run(
        command,
        capture_output=True, text=True, timeout=args.timeout + 15,
    )
    summary = {
        "command": "target python inspection.py",
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
        "wheel_sha256": {
            Path(args.wheel_marine).name: sha256_file(Path(args.wheel_marine)),
            Path(args.wheel_hydro).name: sha256_file(Path(args.wheel_hydro)),
        },
        "inspection_result": str(result_path),
        "wall_seconds": round(time.time() - started, 2),
    }
    (report_dir / "verify-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return EXIT_PASS if completed.returncode == 0 else EXIT_FAIL


# ---------------------------------------------------- installed tests step

GUARD_CONTEST = '''\
# Guard conftest for installed-mode focused tests (generated by
# tools/verify_installed_application.py). It deliberately inserts no source
# paths: anymarine must come from the isolated target venv, and any checkout
# directory on sys.path is refused rather than silently used.
import sys
from pathlib import Path

target_root = Path(sys.prefix).resolve()

import anymarine  # noqa: E402

module_root = Path(anymarine.__file__).resolve()
if target_root not in module_root.parents:
    raise RuntimeError(
        "installed-tests guard: anymarine imported from " + str(module_root)
        + ", expected under the isolated target " + str(target_root)
    )
for entry in sys.path:
    if entry:
        resolved = str(Path(entry).resolve()).replace(chr(92), "/").casefold()
        if resolved.startswith(("c:/github", "/mnt/c/github")):
            raise RuntimeError(
                "installed-tests guard: checkout path on sys.path: " + str(entry)
            )
'''


def command_installed_tests(args: argparse.Namespace) -> int:
    target = Path(args.target_dir)
    report_dir = Path(args.report_dir)
    tests_dir = report_dir / "installed-tests"
    refuse_if_exists(tests_dir, "installed tests dir")
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text(GUARD_CONTEST, encoding="utf-8")
    copied = []
    for source in args.test_sources:
        path = Path(source)
        if not path.is_file():
            raise SystemExit(f"{EXIT_REFUSAL}|test source not found: {path}")
        shutil.copyfile(path, tests_dir / path.name)
        copied.append(path.name)
    started = time.time()
    completed = subprocess.run(
        [str(target_python(target)), "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider", str(tests_dir)],
        capture_output=True, text=True, timeout=args.timeout,
        # cwd inside the copied tests dir: with the checkout as cwd,
        # "python -m pytest" would put the checkout root on sys.path and
        # the guard conftest would (correctly) refuse.
        cwd=str(tests_dir),
    )
    summary = {
        "copied_tests": copied,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-3000:],
        "stderr_tail": completed.stderr[-1000:],
        "wall_seconds": round(time.time() - started, 2),
    }
    (report_dir / "installed-tests-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return EXIT_PASS if completed.returncode == 0 else EXIT_FAIL


# ------------------------------------------------------- donor build step

DONOR_COPY_FILES = (
    "pyproject.toml", "README.md", "LICENSE", "NOTICE",
    "THIRD_PARTY_NOTICES.md", "dependency-licenses.json", "MANIFEST.in",
    "setup.py", "docs/LICENSE.md",
)


def copy_donor_snapshot(donor_root: Path, dest: Path) -> tuple[dict, str]:
    """Copy metadata, declared license files and src/ of a read-only donor.

    Tests, benchmarks, reports and every other donor subtree are
    deliberately not copied: the donor root is read-only evidence and the
    disposable build copy holds only what the wheel build needs.
    """
    if not (donor_root / "pyproject.toml").is_file():
        raise SystemExit(
            f"{EXIT_REFUSAL}|donor missing pyproject.toml: {donor_root}")
    if not (donor_root / "src").is_dir():
        raise SystemExit(f"{EXIT_REFUSAL}|donor missing src/: {donor_root}")
    dest.mkdir(parents=True)
    copied: dict[str, str] = {}
    import tomllib
    config = tomllib.loads((donor_root / "pyproject.toml").read_text(encoding="utf-8"))
    names = set(DONOR_COPY_FILES)
    declarations = list(config.get("project", {}).get("license-files", []))
    declarations += list(config.get("tool", {}).get("setuptools", {}).get("license-files", []))
    for pattern in declarations:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise SystemExit(f"{EXIT_REFUSAL}|license path escapes donor: {pattern}")
        matches = [item for item in donor_root.glob(pattern) if item.is_file()]
        if not matches:
            raise SystemExit(f"{EXIT_REFUSAL}|declared license file missing: {pattern}")
        for item in matches:
            if not item.resolve().is_relative_to(donor_root.resolve()):
                raise SystemExit(f"{EXIT_REFUSAL}|license symlink escapes donor: {item}")
            names.add(item.relative_to(donor_root).as_posix())
    for name in sorted(names):
        source = donor_root / name
        if source.is_file():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied[name] = sha256_file(target)
    packages = sorted(
        entry for entry in (donor_root / "src").iterdir()
        if entry.is_dir() and (entry / "__init__.py").is_file()
    )
    if len(packages) != 1:
        raise SystemExit(
            f"{EXIT_REFUSAL}|donor src must hold exactly one package: "
            f"{donor_root}")
    for source in sorted((donor_root / "src").rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(donor_root)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied[relative.as_posix()] = sha256_file(target)
    return copied, packages[0].name


def wheel_license_members(wheel_path: Path) -> list[str]:
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
    return sorted(
        name for name in names
        if name.rsplit("/", 1)[-1].casefold().startswith(
            ("license", "notice", "copyright", "dependency-licenses"))
        or name.rsplit("/", 1)[-1].casefold() in ("sources.md", "license_data.md")
    )


def wheel_requires(wheel_path: Path) -> list[str]:
    with zipfile.ZipFile(wheel_path) as wheel:
        metadata_name = next(
            (n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")),
            None)
        if metadata_name is None:
            raise SystemExit(f"{EXIT_REFUSAL}|no METADATA in {wheel_path}")
        text = wheel.read(metadata_name).decode("utf-8", "replace")
    return [line.split(":", 1)[1].strip() for line in text.splitlines()
            if line.startswith("Requires-Dist:")]


def command_donor_build(args: argparse.Namespace) -> int:
    import tomllib

    output = Path(args.output_dir)
    build_dir = Path(args.build_dir)
    refuse_if_exists(output, "donor wheel output dir")
    refuse_if_exists(build_dir, "donor build dir")
    output.mkdir(parents=True)
    build_dir.mkdir(parents=True)
    started = time.perf_counter()
    report: dict = {"donors": [], "builds": []}
    for spec in args.donor:
        name, _, path = spec.partition("=")
        if not name or not path:
            raise SystemExit(
                f"{EXIT_REFUSAL}|--donor must be NAME=PATH: {spec!r}")
        root = Path(path)
        if not root.is_dir():
            raise SystemExit(f"{EXIT_REFUSAL}|donor root not found: {root}")
        dest = build_dir / name
        copied, module_name = copy_donor_snapshot(root, dest)
        if getattr(args, "build_frontend", "build") == "pip-wheel":
            build_command = [sys.executable, "-m", "pip", "wheel", "--no-deps",
                             "--no-build-isolation", "--wheel-dir", str(output), "."]
        else:
            build_command = [sys.executable, "-m", "build", "--wheel",
                            "--no-isolation", "--outdir", str(output)]
        completed = subprocess.run(
            build_command,
            cwd=str(dest), capture_output=True, text=True,
            timeout=max(0.01, args.timeout - (time.perf_counter() - started)),
        )
        report["builds"].append({
            "donor": name,
            "root": str(root),
            "command": " ".join(build_command),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-1500:],
            "stderr_tail": completed.stderr[-1500:],
        })
        if completed.returncode != 0:
            report["result"] = "fail"
            print(json.dumps(report))
            return EXIT_FAIL
        project = tomllib.loads(
            (dest / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        dist_name = project["name"]
        version = str(project["version"])
        wheel = next((w for w in sorted(output.glob("*.whl"))
                     if w.name.casefold().startswith(dist_name.casefold() + "-")), None)
        if wheel is None:
            raise SystemExit(
                f"{EXIT_REFUSAL}|no wheel for {dist_name} in {output}")
        license_members = wheel_license_members(wheel)
        entry = {
            "name": dist_name,
            "donor_root": str(root),
            "module": module_name,
            "version": version,
            "declared_dependencies": [
                str(d) for d in project.get("dependencies", [])],
            "declared_license_files": [
                str(f) for f in project.get("license-files", [])],
            "wheel": str(wheel),
            "wheel_name": wheel.name,
            "sha256": sha256_file(wheel),
            "bytes": wheel.stat().st_size,
            "requires_dist": wheel_requires(wheel),
            "license_files_in_wheel": license_members,
            "source_manifest": copied,
        }
        if not license_members:
            report.setdefault("failures", []).append(
                dist_name + ": no owned/upstream license or notice files in "
                + wheel.name)
        report["donors"].append(entry)
    report["result"] = "fail" if report.get("failures") else "pass"
    report["wall_seconds"] = round(time.perf_counter() - started, 2)
    (output / "donor-manifest.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report
                      if k in ("result", "failures", "wall_seconds")}))
    return EXIT_PASS if report["result"] == "pass" else EXIT_FAIL


# ---------------------------------------------------- install-extra step


def command_install_extra(args: argparse.Namespace) -> int:
    target = Path(args.target_dir)
    python = target_python(target)
    wheels = [Path(w) for w in args.wheel]
    if not wheels:
        raise SystemExit(f"{EXIT_REFUSAL}|at least one --wheel is required")
    for wheel in wheels:
        if not wheel.is_file():
            raise SystemExit(f"{EXIT_REFUSAL}|wheel not found: {wheel}")
    started = time.time()
    if args.pip_python:
        pip_command = [str(Path(args.pip_python)), "-m", "pip", "--python",
                       str(python), "install"]
    else:
        pip_command = [str(python), "-m", "pip", "install"]
    pip_command += ["--no-index", "--no-deps", "--force-reinstall",
                    *[str(w) for w in wheels]]
    installed = subprocess.run(
        pip_command,
        capture_output=True, text=True, timeout=args.timeout,
    )
    report = {
        "pip_command": " ".join(pip_command),
        "pip_python": str(args.pip_python) if args.pip_python else "",
        "existing_target": str(target),
        "pip_returncode": installed.returncode,
        "pip_stdout_tail": installed.stdout[-2000:],
        "pip_stderr_tail": installed.stderr[-2000:],
        "wheel_sha256": {w.name: sha256_file(w) for w in wheels},
    }
    if installed.returncode == 0:
        names = [w.name.split("-")[0] for w in wheels]
        probe = subprocess.run(
            [str(python), "-c",
             "import importlib.metadata, json; print(json.dumps({n: "
             "importlib.metadata.version(n) for n in " + repr(names) + "}))"],
            capture_output=True, text=True, timeout=30,
        )
        try:
            report["installed_versions"] = json.loads(probe.stdout or "{}")
        except json.JSONDecodeError:
            report["installed_versions"] = {}
            report["installed_versions_error"] = probe.stderr[-500:]
    report["result"] = "pass" if installed.returncode == 0 else "fail"
    report["wall_seconds"] = round(time.time() - started, 2)
    print(json.dumps(report))
    return EXIT_PASS if installed.returncode == 0 else EXIT_FAIL


# --------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="copy current source to a disposable snapshot")
    snapshot.add_argument("--marine-root", required=True)
    snapshot.add_argument("--hydro-root", required=True)
    snapshot.add_argument("--output-dir", required=True)
    snapshot.set_defaults(handler=command_snapshot)

    build = sub.add_parser("build", help="build fresh wheels from the snapshot")
    build.add_argument("--snapshot-dir", required=True)
    build.add_argument("--output-dir", required=True)
    build.add_argument("--timeout", type=float, default=50.0)
    build.set_defaults(handler=command_build)

    install = sub.add_parser("install", help="create isolated venv and install the two wheels")
    install.add_argument("--wheel-marine", required=True)
    install.add_argument("--wheel-hydro", required=True)
    install.add_argument("--target-dir", required=True)
    install.add_argument("--borrow-site-packages", nargs="+", required=True)
    install.add_argument("--venv-extra-args", default="",
                         help="extra arguments for python -m venv, for "
                              "example --without-pip --copies on hosts "
                              "without ensurepip")
    install.add_argument("--pip-python", default=None,
                         help="interpreter whose pip installs into the "
                              "target through pip --python; default is "
                              "the target interpreter itself")
    install.add_argument("--timeout", type=float, default=50.0)
    install.set_defaults(handler=command_install)

    verify = sub.add_parser("verify", help="verify installed identity and launcher controls")
    verify.add_argument("--target-dir", required=True)
    verify.add_argument("--wheel-marine", required=True)
    verify.add_argument("--wheel-hydro", required=True)
    verify.add_argument("--snapshot-dir", required=True)
    verify.add_argument("--report-dir", required=True)
    verify.add_argument("--extra-wheel", action="append", default=[])
    verify.add_argument("--dependency-manifest", default=None)
    verify.add_argument("--timeout", type=float, default=20.0)
    verify.set_defaults(handler=command_verify)

    donor = sub.add_parser(
        "donor-build",
        help="build local wheels from read-only donor roots into a "
             "disposable workspace copy")
    donor.add_argument("--donor", action="append", required=True,
                       help="NAME=PATH donor root, repeatable")
    donor.add_argument("--output-dir", required=True)
    donor.add_argument("--build-dir", required=True)
    donor.add_argument("--build-frontend", choices=("build", "pip-wheel"),
                       default="build",
                       help="wheel build frontend; pip-wheel needs no "
                            "build package and uses the local "
                            "setuptools backend directly")
    donor.add_argument("--timeout", type=float, default=50.0)
    donor.set_defaults(handler=command_donor_build)

    extra = sub.add_parser(
        "install-extra",
        help="install extra wheels with pip --no-index --no-deps into an "
             "existing target venv")
    extra.add_argument("--target-dir", required=True)
    extra.add_argument("--wheel", action="append", required=True)
    extra.add_argument("--pip-python", default=None,
                         help="interpreter whose pip installs into the "
                              "target through pip --python; default is "
                              "the target interpreter itself")
    extra.add_argument("--timeout", type=float, default=50.0)
    extra.set_defaults(handler=command_install_extra)

    tests = sub.add_parser("installed-tests", help="run copied focused tests in the target")
    tests.add_argument("--target-dir", required=True)
    tests.add_argument("--report-dir", required=True)
    tests.add_argument("--test-sources", nargs="+", required=True)
    tests.add_argument("--timeout", type=float, default=25.0)
    tests.set_defaults(handler=command_installed_tests)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
