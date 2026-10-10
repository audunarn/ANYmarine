"""Persistence: round-trip, atomicity, corrupt/missing sidecars, path escapes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from anymarine.domain import ProjectModel
from anymarine.persistence import (
    ProjectLoadError,
    ProjectStore,
    UnsafePathError,
    atomic_write_bytes,
    check_relative_reference,
    resolve_sidecar,
)


def test_round_trip(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    loaded = service.load(root)
    assert loaded.to_dict() == project.to_dict()
    manifest = json.loads((root / "project.json").read_text())
    assert manifest["schema_version"] == 2
    for entry in manifest["bodies"].values():
        assert len(entry["sha256"]) == 64


def test_corrupt_sidecar_raises_without_model_mutation(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    sidecar = next((root / "bodies").glob("*.json"))
    sidecar.write_text(sidecar.read_text() + "corruption", encoding="utf-8")
    before = project.to_dict()
    with pytest.raises(ProjectLoadError) as info:
        service.load(root)
    assert "corrupt" in str(info.value) or "sha256" in str(info.value)
    assert project.to_dict() == before  # caller's model untouched


def test_missing_sidecar_raises(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    sidecar = next((root / "bodies").glob("*.json"))
    sidecar.unlink()
    with pytest.raises(ProjectLoadError) as info:
        service.load(root)
    assert "missing" in str(info.value)


def test_manifest_written_last_preserves_old_project_on_crash(
    tmp_path, service, project, monkeypatch
):
    root = tmp_path / "proj"
    service.save(project, root)
    old_manifest = (root / "project.json").read_bytes()

    edited = project.with_edits(name="Edited")
    import anymarine.persistence as persistence

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst, *args, **kwargs):
        if Path(dst).name == "project.json":
            raise OSError("simulated crash while writing the manifest")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(Exception):
        service.save(edited, root)
    monkeypatch.setattr(os, "replace", real_replace)

    # The old manifest survived and still loads with matching checksums.
    assert (root / "project.json").read_bytes() == old_manifest
    reloaded = service.load(root)
    assert reloaded.name == project.name


def test_atomic_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "dir" / "file.json"
    atomic_write_bytes(target, b"payload")
    assert target.read_bytes() == b"payload"
    leftovers = [p for p in target.parent.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_atomic_write_failure_preserves_existing(tmp_path, monkeypatch):
    target = tmp_path / "file.json"
    atomic_write_bytes(target, b"original")
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new")
    monkeypatch.setattr(os, "replace", real_replace)
    assert target.read_bytes() == b"original"
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


@pytest.mark.parametrize(
    "reference",
    [
        "C:/evil.json",
        "/etc/passwd",
        "\\\\server\\share\\evil.json",
        "../evil.json",
        "bodies/../../evil.json",
        "..\\evil.json",
        "",
        "   ",
        "./bodies/x.json",
        "bodies//x.json",
    ],
)
def test_unsafe_references_rejected(reference):
    with pytest.raises(UnsafePathError):
        check_relative_reference(reference)


def test_load_rejects_traversal_reference(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    manifest_path = root / "project.json"
    manifest = json.loads(manifest_path.read_text())
    body_id = next(iter(manifest["bodies"]))
    manifest["bodies"][body_id]["sidecar"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(UnsafePathError):
        service.load(root)


def test_load_rejects_absolute_reference(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    manifest_path = root / "project.json"
    manifest = json.loads(manifest_path.read_text())
    body_id = next(iter(manifest["bodies"]))
    manifest["bodies"][body_id]["sidecar"] = str(tmp_path / "outside.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(UnsafePathError):
        service.load(root)


def test_symlink_escape_rejected(tmp_path, service, project):
    if os.name != "nt":
        link = tmp_path / "link"
        os.symlink(tmp_path, link)
        with pytest.raises(UnsafePathError):
            resolve_sidecar(link, "bodies/x.json")
    else:
        try:
            outside = tmp_path / "outside"
            outside.mkdir()
            (outside / "evil.json").write_text("{}", encoding="utf-8")
            link = tmp_path / "linked-body.json"
            os.symlink(outside / "evil.json", link)
        except OSError:
            pytest.skip("symlinks unavailable on this Windows configuration")
        with pytest.raises(UnsafePathError):
            resolve_sidecar(tmp_path, "linked-body.json")


def test_schema_version_mismatch_rejected(tmp_path, service, project):
    root = tmp_path / "proj"
    service.save(project, root)
    manifest_path = root / "project.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProjectLoadError) as info:
        service.load(root)
    assert "schema_version" in str(info.value)


def test_result_sidecars_round_trip(tmp_path, service, project):
    result = service.run_synthetic_local(project)
    project = service.add_result(project, result)
    root = tmp_path / "proj"
    service.save(project, root)
    loaded = service.load(root)
    assert len(loaded.results) == 1
    assert loaded.results[0].to_dict() == result.to_dict()
