"""Oracle tests for tools/verify_qt_desktop.py helpers (offscreen, no solver).

These check the verification tool's own oracles: DPR-aware composed crop math,
changed-pixel counting, software-driver rejection, environment refusal,
partial-report survival and identity helpers. They run offscreen and start no
numerical job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import verify_qt_desktop as tool  # noqa: E402


# ------------------------------------------------------------ crop accounting


@pytest.mark.parametrize(
    "dpr,logical,expected",
    [
        (1.0, (10, 20, 100, 50), (10, 20, 100, 50)),
        (1.5, (10, 20, 100, 50), (15, 30, 150, 75)),
        (2.0, (0, 0, 480, 300), (0, 0, 960, 600)),
        (1.25, (3, 5, 7, 9), (4, 6, 9, 11)),
    ],
)
def test_physical_crop_rect_scales_logical_rect(dpr, logical, expected):
    assert tool.physical_crop_rect(dpr, *logical) == expected


@pytest.mark.parametrize("dpr", (0.0, -1.0))
def test_physical_crop_rect_rejects_nonpositive_dpr(dpr):
    with pytest.raises(ValueError):
        tool.physical_crop_rect(dpr, 0, 0, 10, 10)


def test_composed_crop_extracts_child_widget_region():
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.resize(200, 150)
    child = QWidget(window)
    child.setGeometry(40, 30, 60, 40)
    child.setStyleSheet("background-color: #ff0000;")
    window.setStyleSheet("background-color: #0000ff;")
    window.show()
    app.processEvents()
    pixmap = window.grab()
    dpr = pixmap.devicePixelRatio()
    x, y, w, h = tool.physical_crop_rect(dpr, child.x(), child.y(), child.width(), child.height())
    crop = pixmap.copy(x, y, w, h)
    image = tool.DesktopVerifier._qimage_to_pil(crop.toImage()).convert("RGB")
    assert image.size == (w, h)
    center = image.getpixel((w // 2, h // 2))
    assert center == (255, 0, 0), f"crop missed the child widget: {center}"
    window.close()


# --------------------------------------------------------- changed-pixel count


def _image(size, block=None):
    from PIL import Image

    image = Image.new("RGBA", size, (10, 10, 10, 255))
    if block is not None:
        x0, y0 = block[0], block[1]
        for x in range(x0, x0 + 10):
            for y in range(y0, y0 + 10):
                image.putpixel((x, y), (200, 200, 200, 255))
    return image


def test_count_changed_pixels_identical_images():
    assert tool.count_changed_pixels(_image((50, 50)), _image((50, 50))) == 0


def test_count_changed_pixels_counts_only_real_blocks():
    changed = tool.count_changed_pixels(_image((50, 50)), _image((50, 50), block=(5, 5)))
    assert changed == 100


def test_count_changed_pixels_respects_threshold():
    from PIL import Image

    a = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    b = Image.new("RGBA", (10, 10), (0, 0, 4, 255))  # below default threshold 8
    assert tool.count_changed_pixels(a, b) == 0
    assert tool.count_changed_pixels(a, b, threshold=2) == 100


def test_count_changed_pixels_rejects_size_mismatch():
    with pytest.raises(ValueError):
        tool.count_changed_pixels(_image((10, 10)), _image((20, 20)))


def test_scene_content_summary_uniform_image():
    assert tool.scene_content_summary(_image((40, 40))) == {
        "non_background_pixels": 0,
        "bbox": None,
    }


def test_scene_content_summary_block_bbox():
    summary = tool.scene_content_summary(_image((50, 50), block=(10, 10)))
    assert summary["non_background_pixels"] == 100
    assert summary["bbox"] == [10, 19, 10, 19]
    assert summary["size"] == [50, 50]


# ------------------------------------------------------- GL provenance oracles


@pytest.mark.parametrize(
    "vendor,renderer,expected",
    [
        ("Mesa", "llvmpipe (LLVM 15)", True),
        ("Google", "SwiftShader", True),
        ("Microsoft Corporation", "GDI Generic", True),
        ("Microsoft", "Basic Render Driver", True),
        ("NVIDIA Corporation", "NVIDIA GeForce RTX", False),
        ("AMD", "AMD Radeon", False),
        ("Intel", "Intel(R) UHD Graphics", False),
    ],
)
def test_is_software_gl_renderer(vendor, renderer, expected):
    assert tool.is_software_gl_renderer(vendor, renderer) is expected


def test_decode_gl_string_bytes_and_terminator():
    assert tool.decode_gl_string(b"NVIDIA Corporation\x00") == "NVIDIA Corporation"
    assert tool.decode_gl_string(b"") == ""
    assert tool.decode_gl_string(None) == ""
    assert tool.decode_gl_string("plain") == "plain"


# ------------------------------------------------------- environment refusal


def test_environment_problems_refuses_offscreen():
    problems = tool.environment_problems({"QT_QPA_PLATFORM": "offscreen"})
    assert problems and "offscreen" in problems[0]


def test_environment_problems_accepts_real_desktop():
    assert tool.environment_problems({"QT_QPA_PLATFORM": "windows"}) == []
    assert tool.environment_problems({}) == []


# ------------------------------------------------------------- report survival


def _report(tmp_path, gates, error=None):
    report = tool.Report(tmp_path, {"mode": "default", "deadline": 25.0})
    for name, passed in gates:
        report.gate(name, passed)
    report.data["error"] = error
    return report, report.save()


def test_report_pass_writes_json_and_markdown(tmp_path):
    report, path = _report(tmp_path, [("renderer_ready", True), ("pixels", True)])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["result"] == "pass"
    assert all(g["passed"] for g in data["gates"])
    assert (tmp_path / "report.md").exists()
    assert "Result: **pass**" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_report_fail_and_error_survives(tmp_path):
    report, path = _report(tmp_path, [("renderer_ready", False)], error="DeadlineReached: x")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["result"] == "fail"
    assert data["error"] == "DeadlineReached: x"
    assert data["gates"][0]["passed"] is False


def test_report_records_partial_phase_details(tmp_path):
    report = tool.Report(tmp_path, {"mode": "gpu", "deadline": 25.0})
    report.phase("startup", "timeout", effective_backend="software")
    report.save()
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["phases"]["startup"]["status"] == "timeout"
    assert data["phases"]["startup"]["effective_backend"] == "software"


# --------------------------------------------------------------- identity


def test_distribution_origin_exact_installed_any3dview():
    origin = tool.distribution_origin("any3dview")
    assert "error" not in origin
    assert origin["version"] == "0.5.5"
    assert origin["path"]


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"anymarine desktop verification")
    import hashlib

    assert tool.sha256_file(path) == hashlib.sha256(b"anymarine desktop verification").hexdigest()


def test_refusal_exit_code_constant():
    assert (tool.EXIT_PASS, tool.EXIT_FAIL, tool.EXIT_REFUSAL) == (0, 1, 2)


def test_thin_sheet_selector_skips_first_hull():
    from anymarine.services import ApplicationService
    from anymarine.qtapp.projectmodel import ProjectTreeModel
    service = ApplicationService()
    project = service.load_plate_example()
    model = ProjectTreeModel(service)
    model.set_project(project)
    index, assignment = tool.find_thin_sheet_surface(model, project)
    assert index is not None
    assert assignment.semantics.surface_kind == "thin_sheet"
    assert model.node_for_index(index).payload == (
        assignment.semantics.owning_body_id, assignment.semantics.surface_id)


def test_thin_sheet_selector_refuses_hull_only_project():
    from anymarine.services import ApplicationService
    from anymarine.qtapp.projectmodel import ProjectTreeModel
    service = ApplicationService()
    project = service.load_example()
    model = ProjectTreeModel(service)
    model.set_project(project)
    assert tool.find_thin_sheet_surface(model, project) == (None, None)


def test_installed_origin_guard_refuses_checkout(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tool.sys, "meta_path", [])
    with pytest.raises(RuntimeError, match="checkout"):
        tool.installed_origin_guard(SimpleNamespace(
            __file__="C:/Github/ANYmarine/src/anymarine/__init__.py"))


def test_installed_origin_guard_refuses_editable_finder(tmp_path, monkeypatch):
    from types import SimpleNamespace
    class EditableFinder:
        pass
    monkeypatch.setattr(tool.sys, "meta_path", [EditableFinder()])
    with pytest.raises(RuntimeError, match="editable import finders"):
        tool.installed_origin_guard(SimpleNamespace(
            __file__=str(tmp_path / "Lib/site-packages/anymarine/__init__.py")))


def test_installed_origin_guard_accepts_noneditable_distribution(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tool.sys, "meta_path", [])
    origin=Path("C:/ANYworkspaceWork/installed-origin-fixture/Lib/site-packages/anymarine/__init__.py")
    assert tool.installed_origin_guard(SimpleNamespace(__file__=str(origin))) == {
        "module": str(origin.resolve()), "problems": []}


# --------------------------------------------- Linux/WSL platform support


def test_installed_origin_guard_refuses_linux_checkout(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(tool.sys, "meta_path", [])
    # On Windows the mount text resolves to C:\mnt\c\... and the
    # source-layout refusal fires first; both are valid refusals of a
    # checkout-shaped origin, so the regression asserts the refusal.
    with pytest.raises(RuntimeError, match="anymarine imported from"):
        tool.installed_origin_guard(SimpleNamespace(
            __file__="/mnt/c/Github/ANYmarine/src/anymarine/__init__.py"))


@pytest.mark.parametrize(
    "vendor,renderer,expected",
    [
        ("Microsoft Corporation", "D3D12 (NVIDIA GeForce RTX 3090 Ti)", False),
        ("Mesa", "llvmpipe (LLVM 15.0.7, 256 bits)", True),
    ],
)
def test_is_software_gl_renderer_wslg_cases(vendor, renderer, expected):
    assert tool.is_software_gl_renderer(vendor, renderer) is expected


def test_gl_string_bytes_accepts_qbytearray_and_bytes():
    from PySide6.QtCore import QByteArray

    assert tool.gl_string_bytes(b"raw") == b"raw"
    assert tool.gl_string_bytes(QByteArray(b"qt")) == b"qt"
    assert tool.gl_string_bytes("pyside-str") == b"pyside-str"
    assert tool.gl_string_bytes(None) is None
    assert (tool.decode_gl_string(tool.gl_string_bytes(QByteArray(b"NVIDIA\x00")))
            == "NVIDIA")


def test_gl_query_uses_public_qt_api_not_platform_cdll():
    import inspect

    source = inspect.getsource(tool)
    assert "opengl32.dll" not in source
    assert "ctypes" not in source
    assert "context.functions()" in source
    assert "glGetString" in source
