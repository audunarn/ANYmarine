"""Offscreen Qt tests for the development status route in the GUI.

Covers the Help > Development status dialog, the compact startup status and
preservation of existing menu paths. No native numerical or BEM execution.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ANYMARINE_VIEWER_BACKEND", "software")

import pytest

pyside = pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from anymarine.development_status import (
    DevelopmentStatusError,
    load_development_status,
    startup_summary,
)
from anymarine.qtapp.development_status import (
    DevelopmentStatusDialog,
    DevelopmentStatusTableModel,
)
from anymarine.qtapp.mainwindow import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _make_window(app):
    window = MainWindow()
    yield window
    window._dirty = False
    window.close()
    app.processEvents()


@pytest.fixture
def window(app):
    yield from _make_window(app)


def test_startup_status_label_shows_compact_snapshot(window):
    label = window._development_status_label
    status = load_development_status()
    # compact badge in the status bar; the snapshot summary is its tooltip
    assert label.text() == "Development preview · unqualified"
    assert label.toolTip().startswith(startup_summary(status))
    assert "2026-10-10" in label.toolTip()
    assert "unqualified" in label.toolTip()
    assert "NO_GO" in label.toolTip()


def test_help_menu_has_development_status_action(window):
    help_menu = window._menus[-1]
    titles = [action.text() for action in help_menu.actions()]
    assert "Development status..." in titles
    assert "&About" in titles
    assert "Loading models..." in titles


def test_existing_menu_paths_preserved(window):
    file_titles = [
        action.text() for action in window._menus[0].actions()
    ]
    # examples live in File > Examples, not directly under File (owner, 2026-10-15)
    assert "&Examples" in file_titles
    assert not any(title.startswith("Load ") for title in file_titles)
    assert "Open stored HAMS wave bundle…" in file_titles
    example_titles = [
        action.text() for action in window._examples_menu.actions()
    ]
    for expected in (
        "Load &example (two-body replay)",
        "Load native dry two-body example",
        "Load attached-plate geometry example",
        "Load native mooring example (three lines)",
        "Load native coupled fluid example",
        "Load coupled seeded-wave example",
        "Load calculated wave preview example",
        "Load stored HAMS wave samples",
        "Load native wave/current Morison example",
        "Load native wave/current mooring example",
        "Load native seabed contact example",
        "Load coupled static equilibrium example",
    ):
        assert expected in example_titles
    renderer_titles = [
        action.text() for action in window._renderer_menu.actions()
    ]
    assert "Software (compatible)" in renderer_titles
    assert "OpenGL" in renderer_titles


def test_show_development_status_opens_dialog(window, monkeypatch):
    opened = []
    real_dialog = DevelopmentStatusDialog

    class FakeDialog:
        def __init__(self, status, parent=None):
            opened.append(real_dialog(status, parent))

        def exec(self):
            return 0

    monkeypatch.setattr(
        "anymarine.qtapp.development_status.DevelopmentStatusDialog", FakeDialog
    )
    window._show_development_status()
    assert len(opened) == 1
    dialog = opened[0]
    assert "2026-10-10" in dialog.windowTitle()
    assert dialog._model.rowCount() == len(load_development_status()["capabilities"])


def test_dialog_model_view_contents(app):
    status = load_development_status()
    dialog = DevelopmentStatusDialog(status)
    model = dialog._model
    assert model.columnCount() == 5
    assert model.rowCount() == len(status["capabilities"])
    assert model.headerData(2, Qt.Orientation.Horizontal) == "Status"
    index = model.index(0, 2)
    assert index.data() == status["capabilities"][0]["status"]
    text = dialog._hydro_text.toPlainText()
    assert "energy_point_comparison_failed_unqualified" in text
    assert "NO_GO" in text
    assert "100/100" in text
    assert "does not imply admission" in text
    assert "timed out" in text
    assert "Next development question" in text
    dialog.close()


def test_dialog_model_read_only(app):
    status = load_development_status()
    model = DevelopmentStatusTableModel(status["capabilities"])
    flags = model.flags(model.index(0, 0))
    assert not flags & Qt.ItemFlag.ItemIsEditable


def test_corrupt_data_startup_label_fails_clearly(window, monkeypatch):
    def broken(path=None):
        raise DevelopmentStatusError("development status data file is missing: fake")

    monkeypatch.setattr(
        "anymarine.qtapp.mainwindow.load_development_status", broken
    )
    window._refresh_startup_development_status()
    assert window._development_status is None
    assert "unavailable" in window._development_status_label.text()
    assert "missing" in window._development_status_label.toolTip()


def test_corrupt_data_dialog_route_notifies_critical(window, monkeypatch):
    def broken(path=None):
        raise DevelopmentStatusError("corrupt (invalid JSON)")

    monkeypatch.setattr(
        "anymarine.qtapp.mainwindow.load_development_status", broken
    )
    calls = []
    monkeypatch.setattr(
        window, "_notify_critical", lambda title, text: calls.append((title, text))
    )
    window._show_development_status()
    assert len(calls) == 1
    assert calls[0][0] == "Development status"
    assert "corrupt" in calls[0][1]
