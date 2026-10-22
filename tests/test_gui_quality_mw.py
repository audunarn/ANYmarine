"""GUI quality slice M1: mainwindow-level menus, shortcuts, labels and small wiring."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtWidgets import QApplication  # noqa: E402

from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    from PySide6 import QtCore
    real = QtCore.QSettings
    monkeypatch.setattr(QtCore, "QSettings", lambda *a: real(str(tmp_path / "s.ini"), real.Format.IniFormat))
    monkeypatch.delenv("ANYMARINE_OCEAN", raising=False)
    win = MainWindow(viewer_backend="software")
    monkeypatch.setattr(win, "_notify_warning", lambda *a: None)
    monkeypatch.setattr(win, "_notify_critical", lambda *a: None)
    win._info = []
    monkeypatch.setattr(win, "_notify_info", lambda *a: win._info.append(a))
    win.resize(1280, 720)
    win.show()
    qapp.processEvents()
    yield win
    win._dirty = False
    win.close()


def _menu(window, name):
    return next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == name)


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def test_channel_combo_shows_readable_titles_but_keeps_raw_ids(qapp, window):
    window.load_example()
    qapp.processEvents()
    combo = window._channel_combo
    result = window._current_result
    ids = [combo.itemData(i) for i in range(combo.count())]
    assert sorted(ids) == sorted(result.channels)
    assert combo.currentData() in result.channels
    assert all(combo.itemText(i) != raw and combo.itemText(i)[0].isupper() for i, raw in enumerate(ids))
    raw = ids[-1]
    combo.setCurrentText(raw)                      # callers that name the stored id still select it
    assert combo.currentData() == raw
    assert list(window._plot.series_values()) == list(result.channels[raw])


def test_result_label_is_elided_with_full_text_tooltip(qapp, window):
    label = window._frame_label
    full = "synthetic_unqualified - frame 1/251 t=0.00 s " + "x" * 400
    label.setText(full)
    assert label.text() == full and label.toolTip() == full
    assert label.minimumSizeHint().width() < 300


def test_reopen_dialog_receives_project_root(window, monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, parent=None, **kw):
            seen.update(kw)

        def exec(self):
            return 0

    monkeypatch.setattr("anymarine.qtapp.mainwindow.ReopenSnapshotDialog", Fake)
    window._project_root = "C:/some/project"
    window._reopen_dialog()
    assert seen.get("project_root") == "C:/some/project"


def test_ocean_motion_toggle_in_view_menu(qapp, window, monkeypatch):
    from anymarine.qtapp import ocean
    action = window._ocean_motion_action
    assert action in _menu(window, "View").actions()
    assert action.isCheckable() and action.isChecked() and window._marine.mode == "animated"
    stored = []
    monkeypatch.setattr(ocean, "store_motion", stored.append)
    action.setChecked(False)
    assert stored == [False] and window._marine.mode == "still"
    action.setChecked(True)
    assert stored == [False, True] and window._marine.mode == "animated"
    window._water_actions["water"].setChecked(False)
    assert not action.isEnabled() and not window._water_actions["grid"].isEnabled()
    window._water_actions["water"].setChecked(True)
    assert action.isEnabled() and window._water_actions["grid"].isEnabled()


def test_view_menu_has_modes_and_standard_views(window):
    view = _menu(window, "View")
    subs = {a.text().replace("&", ""): a.menu() for a in view.actions() if a.menu()}
    assert "Mode" in subs and "Standard views" in subs
    assert [a.shortcut().toString() for a in subs["Mode"].actions()] == [f"Ctrl+{i}" for i in range(1, len(window._mode_actions) + 1)]
    assert len(subs["Standard views"].actions()) == 3
    assert "Renderer" in subs and "&Fit model" in _texts(view)


def test_apply_frame_refreshes_waterline(qapp, window, monkeypatch):
    window.load_example()
    qapp.processEvents()
    calls = []
    monkeypatch.setattr(window._marine, "refresh_waterline", lambda: calls.append(1) or False)
    window._apply_frame(1)
    assert calls == [1]


def test_file_menu_exit_and_shortcuts(window):
    file_menu = _menu(window, "File")
    exit_action = next(a for a in file_menu.actions() if a.text().replace("&", "") == "Exit")
    assert exit_action.shortcut().toString() == "Ctrl+Q"
    assert _texts(file_menu).index(exit_action.text()) == len(_texts(file_menu)) - 1
    assert window._actions["open"].shortcut().toString() == "Ctrl+O"
    status = next(a for a in _menu(window, "Help").actions() if a.text() == "Development status...")
    assert status.shortcut().toString() == "F1"


def test_jobs_menu_is_grouped_and_keeps_every_entry(window):
    jobs = _menu(window, "Jobs")
    texts = _texts(jobs)
    for kept in ("&Run synthetic job", "Run &linear development job", "Cancel &all jobs",
                 "Import &hydrodynamic inputs...", "Import derived hydrodynamic &inputs...",
                 "Import native mechanical inputs...", "Edit native mechanical inputs...",
                 "Run native mechanical job...", "Edit shared sea state...",
                 "Run configured HAMS fields…", "Run coupled static equilibrium job",
                 "Adopt selected static equilibrium…"):
        assert kept in texts
    assert sum(a.isSeparator() for a in jobs.actions()) >= 2
    assert texts[0] == "Run &linear development job" and texts[-1] == "Cancel &all jobs"
    assert texts.index("Run &linear development job") < texts.index("&Run synthetic job")
    synthetic = next(a for a in jobs.actions() if a.text() == "&Run synthetic job")
    assert "synthetic" in synthetic.toolTip().lower() and synthetic.toolTip() != synthetic.text()


def test_about_names_version_licence_components_and_docs(window):
    window._show_about()
    title, text = window._info[-1][:2]
    assert title == "ANYmarine"
    assert "0.1.0" in text and "MPL-2.0" in text
    assert "Components" in text and "any3dview" in text.lower() and "numpy" in text.lower()
    assert "docs" in text.lower()
    assert "synthetic_unqualified" in text and "not qualified for production use" in text


def test_sea_state_dialog_focuses_first_field(qapp):
    from anymarine.qtapp.sea_state_dialog import SeaStateDialog
    data = dict(jonswap=dict(significant_height=0.5, peak_period=8.0), seed=7)
    project = SimpleNamespace(native_inputs=SimpleNamespace(wave_realization=SimpleNamespace(data=data)))
    dialog = SeaStateDialog(project)
    dialog.show()
    for _ in range(5):
        qapp.processEvents()
    dialog.activateWindow()
    for _ in range(5):
        qapp.processEvents()
    assert dialog.focusWidget() is dialog.height
    dialog.close()
