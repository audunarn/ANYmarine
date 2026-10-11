"""Responsive workbench layout regressions at a 1280x720 request.

Offscreen layout-only checks: the shell must honour a 1280x720 request
(actual window never larger than requested), keep the project tree usable
and keep long Details metadata reachable through the page scroll
containers. Synthetic example/plate forms only; no numerical job and no
scientific claim.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, monkeypatch):
    win = MainWindow()
    monkeypatch.setattr(win, "_notify_warning", lambda *a: None)
    monkeypatch.setattr(win, "_notify_critical", lambda *a: None)
    monkeypatch.setattr(win, "_notify_info", lambda *a: None)
    monkeypatch.setattr(win, "_confirm_discard", lambda: True)
    yield win
    win._dirty = False
    win.close()


def settle(qapp, rounds=10):
    for _ in range(rounds):
        qapp.processEvents()


def test_window_minimum_fits_1280x720(qapp, window):
    # Root cause regression: wide panel minimum-size hints used to propagate
    # through the docks and force the shell above a 1280x720 request.
    minimum = window.minimumSizeHint()
    assert minimum.width() <= 1280
    assert minimum.height() <= 720


def test_requested_1280x720_is_honoured(qapp, window):
    window.resize(1280, 720)
    settle(qapp)
    assert window.width() <= 1280
    assert window.height() <= 720


def test_project_tree_usable_at_1280x720(qapp, window):
    window.load_example()
    window.resize(1280, 720)
    window.show()
    settle(qapp)
    tree = window._tree
    assert tree.width() >= 150
    assert tree.height() >= 120


def test_details_long_metadata_reachable_by_scrolling(qapp, window):
    window.load_plate_example()
    window.resize(1280, 720)
    window.show()
    settle(qapp)
    project = window._project
    body = next(b for b in project.bodies
                if any(s.semantics.surface_kind == "thin_sheet"
                       for s in b.hydro_surfaces))
    assignment = next(s for s in body.hydro_surfaces
                      if s.semantics.surface_kind == "thin_sheet")
    window._details.show_surface(body.body_id,
                                 assignment.semantics.surface_id)
    settle(qapp)
    page = window._details._stack.currentWidget()
    assert isinstance(page, QScrollArea)
    form = window._details._surface_form
    # identity/normal/sides stay reachable: the scroll container covers the
    # full form minimum both horizontally and vertically
    assert (form.minimumSizeHint().width()
            <= page.viewport().width() + page.horizontalScrollBar().maximum())
    assert (form.minimumSizeHint().height()
            <= page.viewport().height() + page.verticalScrollBar().maximum())

    # Exercise the real scrollbar route, not just minimum-size hints.
    for field in (form._plus_side, form._apply):
        position = field.mapTo(form, field.rect().center())
        page.horizontalScrollBar().setValue(position.x() - page.viewport().width() // 2)
        page.verticalScrollBar().setValue(position.y() - page.viewport().height() // 2)
        settle(qapp)
        center = field.mapTo(page.viewport(), field.rect().center())
        assert page.viewport().rect().contains(center)


def test_qa_tool_window_options_default_1280x850(tmp_path):
    # QA default dimensions stay 1280x850; the 1280x720 request is opt-in.
    path = Path(__file__).resolve().parent.parent / "tools" / "verify_qt_desktop.py"
    spec = importlib.util.spec_from_file_location("verify_qt_desktop_layout_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    captured = {}

    class FakeVerifier:
        def __init__(self, args):
            captured["width"] = args.window_width
            captured["height"] = args.window_height

        def run(self):
            return 0

    original = module.DesktopVerifier
    module.DesktopVerifier = FakeVerifier
    try:
        assert module.main(["--evidence-dir", str(tmp_path / "a")]) == 0
        assert captured == {"width": 1280, "height": 850}
        assert module.main(["--evidence-dir", str(tmp_path / "b"),
                            "--window-width", "1280",
                            "--window-height", "720"]) == 0
        assert captured == {"width": 1280, "height": 720}
    finally:
        module.DesktopVerifier = original
