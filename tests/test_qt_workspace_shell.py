"""Mode-based workspace shell (GUI redesign 2026-10-15): modes, drawer, menus and the marine scene."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402
from anymarine.qtapp.marine_scene import model_extent, water_mesh  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, monkeypatch):
    win = MainWindow(viewer_backend="software")
    monkeypatch.setattr(win, "_notify_warning", lambda *a: None)
    monkeypatch.setattr(win, "_notify_critical", lambda *a: None)
    monkeypatch.setattr(win, "_notify_info", lambda *a: None)
    win.resize(1280, 720)
    win.show()
    qapp.processEvents()
    yield win
    win._dirty = False
    win.close()


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def test_modes_switch_one_context_panel(qapp, window):
    assert window._mode == "model" and window._context_dock.isHidden()
    for mode, panel in (("waves", window._waves), ("rao", window._rao),
                        ("assessment", window._assessment_panel)):
        window._mode_actions[mode].trigger()
        pump(qapp)
        assert window._mode == mode and not window._context_dock.isHidden()
        page = window._context_stack.currentWidget()
        assert isinstance(page, QScrollArea) and page.widget() is panel
        assert window._context_title.text()
    window._mode_actions["results"].trigger()
    assert window._plot.isVisibleTo(window._context_stack.currentWidget())
    window._mode_actions["model"].trigger()
    assert window._context_dock.isHidden()


def test_jobs_drawer_collapsed_and_toggled_from_status_bar(qapp, window):
    assert window._jobs_dock.isHidden() and not window._jobs_button.isChecked()
    window._jobs_button.click()
    assert not window._jobs_dock.isHidden() and window._jobs_button.isChecked()
    window._jobs_button.click()
    assert window._jobs_dock.isHidden() and not window._jobs_button.isChecked()
    window._on_job_failed("0123456789", "boom")
    assert not window._jobs_dock.isHidden() and window._jobs_button.isChecked()
    assert "FAILED: boom" in window._diagnostics.toPlainText()


def test_transport_bar_follows_result(qapp, window):
    window.load_regular_wave_example()
    pump(qapp)
    assert window._current_result is not None and not window._transport.isHidden()
    window._set_result(None)
    assert window._transport.isHidden()


def test_examples_are_a_file_submenu(window):
    file_menu = window._menus[0]
    assert window._examples_menu in [a.menu() for a in file_menu.actions() if a.menu()]
    titles = [a.text() for a in window._examples_menu.actions() if not a.isSeparator()]
    from anymarine.study_examples import PRIMARY_EXAMPLES
    assert titles[:5] == [label for _,label in PRIMARY_EXAMPLES]
    assert titles[5] == 'Advanced examples'
    titles = [a.text() for a in window._advanced_examples_menu.actions() if not a.isSeparator()]
    assert len(titles) >= 13
    # This separately tested optional study action predates the Scripts mode.
    stability_label = "ANYstability analytical box…"
    assert titles.count(stability_label) == 1
    assert all(t.startswith("Load ") for t in titles if t != stability_label)


def test_water_plane_sized_from_model_and_not_pickable(qapp, window):
    window.load_example()
    pump(qapp)
    handle = window._marine.handle
    assert handle is not None and not handle.removed and handle.visible
    low, high = model_extent(window._handles.values())
    positions = np.asarray(handle.mesh.positions)
    assert np.allclose(positions[:, 2], 0.0)
    assert positions[:, 0].min() < low[0] and positions[:, 0].max() > high[0]
    assert positions[:, 1].min() < low[1] and positions[:, 1].max() > high[1]


def test_fit_frames_the_model_not_the_water(qapp, window):
    window.load_example()
    pump(qapp, 20)
    window._fit_model()
    with_water = window.viewer.export_view_state()
    window._water_actions["water"].setChecked(False)
    assert window._marine.handle is None
    window._fit_model()
    without_water = window.viewer.export_view_state()
    assert repr(with_water) == repr(without_water)
    assert window._marine.handle is None or window._marine.handle.visible
    window._water_actions["water"].setChecked(True)
    assert window._marine.handle is not None and window._marine.handle.visible


def test_grid_toggle_rebuilds_plane_without_lines(qapp, window):
    window.load_example()
    pump(qapp)
    assert window._marine.handle.mesh.lines is not None
    window._water_actions["grid"].setChecked(False)
    assert window._marine.handle.mesh.lines is None or len(window._marine.handle.mesh.lines) == 0


def test_wave_surface_suppresses_water_plane(qapp, window):
    window.load_regular_wave_example()
    pump(qapp, 20)
    if window._waves.handle is None:
        pytest.skip("example shows no wave surface in this configuration")
    assert window._marine.suppressed
    assert window._marine.handle is None or not window._marine.handle.visible
    window._waves.handle = None
    assert not window._marine.suppressed


def test_water_mesh_grid_steps():
    mesh = water_mesh(np.array([-5.0, -2.0]), np.array([5.0, 2.0]), grid=True)
    positions = np.asarray(mesh.positions)
    assert positions[:, 0].min() < -5.0 and positions[:, 0].max() > 5.0
    assert len(mesh.triangles) > 2 and len(mesh.lines) > 4
    assert water_mesh(np.zeros(2), np.ones(2), grid=False).lines is None


def test_renderer_change_restores_water(qapp, window):
    window.load_example()
    pump(qapp)
    window._renderer_actions["software"].trigger()
    pump(qapp)
    handle = window._marine.handle
    assert window._marine.viewer is window.viewer
    assert handle is not None and not handle.removed


def test_unsaved_changes_mark_the_title(window):
    window.load_example()
    assert window.windowTitle().endswith("[*]")
    window._dirty = True
    assert window.isWindowModified()
    window._dirty = False
    assert not window.isWindowModified()


def test_transport_bar_stays_one_line_with_full_status_in_tooltip(qapp, window):
    window.load_regular_wave_example()
    window._set_mode("waves")
    pump(qapp, 20)
    window._apply_frame(5)
    pump(qapp, 10)
    assert window._transport.height() <= 60
    assert window.width() == 1280 and window._frame_slider.width() >= 120
    # what is painted keeps the whole qualification, however narrow the label gets
    shown = window._transport_label.displayed_text()
    assert shown.endswith(" · unqualified")
    for width in (0, 40, 90, 150):
        assert window._transport_label.displayed_text(width).endswith(" · unqualified")
    assert window._transport_label.minimumSizeHint().width() >= window._transport_label.fontMetrics().horizontalAdvance(" · unqualified")
    label = window._transport_label
    assert label.text().startswith("frame 6/") and "unqualified" in label.text()
    assert label.toolTip() == window._frame_label.text() and "unqualified" in label.toolTip()
