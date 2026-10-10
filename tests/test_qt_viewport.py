"""Viewport lifecycle regressions: persistent viewer, deferred fit, diagnostics.

Offscreen software-backend regressions for the blank-canvas-on-load fix.
Rendered-pixel evidence (offscreen and real desktop) lives in the QA
evidence runs under C:/ANYworkspaceWork/anymarine-evidence/.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtWidgets import QApplication  # noqa: E402

from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wait_until(predicate, timeout_s=10.0, app=None):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.02)
    return predicate()


@pytest.fixture()
def window(qapp, monkeypatch):
    win = MainWindow()
    monkeypatch.setattr(win, "_notify_warning", lambda *a: None)
    monkeypatch.setattr(win, "_notify_critical", lambda *a: None)
    monkeypatch.setattr(win, "_notify_info", lambda *a: None)
    yield win
    win._dirty = False
    win.close()


def test_viewer_survives_project_reloads(qapp, window, project_dir):
    viewer_before = window.viewer
    window.load_project(str(project_dir))
    assert window.viewer is viewer_before
    window.load_example()
    assert window.viewer is viewer_before
    window.load_project(str(project_dir))
    assert window.viewer is viewer_before
    # handles are repopulated for the current project only
    project = window._project
    assert set(window._handles) == {body.body_id for body in project.bodies}
    for handle in window._handles.values():
        assert not handle.removed


def test_fit_deferred_until_viewport_is_visible(qapp, window, project_dir):
    window.load_project(str(project_dir))
    # not shown yet: fit must stay pending, never fitted blind
    assert window._fit_pending is True
    window.show()
    assert wait_until(lambda: window._fit_pending is False, app=qapp)
    size = window.viewer.size()
    assert size.width() > 1 and size.height() > 1


def test_fit_action_fits_immediately(qapp, window, project_dir):
    window.load_project(str(project_dir))
    window.show()
    assert wait_until(lambda: window._fit_pending is False, app=qapp)
    window._fit_action.trigger()
    qapp.processEvents()
    assert window._fit_pending is False


def test_render_error_surfaces_in_diagnostics(qapp, window, project_dir, monkeypatch):
    window.load_project(str(project_dir))
    window.viewer.backend_changed.emit("software")
    window.viewer.render_error.emit("context lost")
    qapp.processEvents()
    text = window._diagnostics.toPlainText()
    assert "RENDER ERROR: context lost" in text
    assert "viewer backend changed to software" in text
    # software backend means the fallback (or explicit choice) is rendering;
    # no modal failure dialog is raised for it
    critical = []
    monkeypatch.setattr(window, "_notify_critical", lambda *a: critical.append(a))
    window.viewer.render_error.emit("another failure")
    # process events so the deferred report runs while the patch is active
    # (the report is a no-op here: the software backend is rendering)
    wait_until(
        lambda: "another failure" in window._diagnostics.toPlainText(), app=qapp
    )
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.02)
    assert "another failure" in window._diagnostics.toPlainText()
    assert not critical


def test_unfallbackable_render_failure_is_visible(qapp, window, project_dir, monkeypatch):
    window.load_project(str(project_dir))
    window.show()
    critical = []
    monkeypatch.setattr(window, "_notify_critical", lambda *a: critical.append(a))
    # explicit gpu backend that fails has no software fallback: the blank
    # canvas must be explained to the user, not left silent
    monkeypatch.setattr(
        type(window.viewer), "backend_name", property(lambda self: "gpu")
    )
    try:
        window.viewer.render_error.emit("shader compile failed")
        assert wait_until(lambda: critical, app=qapp)
    finally:
        monkeypatch.undo()
    assert critical and "Renderer failure" in critical[0][0]


def test_light_workbench_theme_applied(qapp, window):
    sheet = window.styleSheet()
    assert "#f3f5f8" in sheet
    assert "#253247" in sheet
    assert window.font().pointSizeF() == pytest.approx(10.0)


def test_reload_preserves_reference_and_replay_transforms(qapp, window):
    window.load_example()
    handle = window._handles[window._project.bodies[0].body_id]
    frame0 = np.array(handle.transform, copy=True)
    frames = window._current_result.frame_count()
    window._apply_frame(min(1, frames - 1))
    moved = np.array(handle.transform, copy=True)
    assert frames < 2 or not np.allclose(frame0, moved)
    # reload: fresh handles at the stored reference/frame-0 transforms
    window.load_example()
    handle2 = window._handles[window._project.bodies[0].body_id]
    assert handle2 is not handle
    assert np.allclose(np.array(handle2.transform), frame0)


def test_backend_switch_refits_retained_scene(qapp, window):
    window.load_example()
    window.show()
    assert wait_until(lambda: not window._fit_pending, app=qapp)
    handles = dict(window._handles)
    window.viewer.backend_changed.emit("software")
    assert window._fit_pending
    assert wait_until(lambda: not window._fit_pending, app=qapp)
    assert window._handles == handles


def test_example_has_rendered_bodies_after_load_and_reload(qapp, window, tmp_path):
    window.show()
    saved = tmp_path / "saved-example"
    window.load_example()
    window._service.save(window._project, saved)
    for load in (window.load_example, lambda: window.load_project(str(saved))):
        load()
        assert wait_until(lambda: not window._fit_pending, app=qapp)
        qapp.processEvents()
        pixels = np.asarray(window.viewer.capture_image().convert("RGB"), dtype=int)
        blue = (pixels[:, :, 2] - pixels[:, :, 0] > 35) & (pixels[:, :, 0] < 150)
        orange = (pixels[:, :, 0] - pixels[:, :, 2] > 50) & (pixels[:, :, 0] > 130)
        assert blue.sum() > 100
        assert orange.sum() > 100


def test_anyfem_controls_and_plot_use_light_workbench(qapp, window):
    sheet = window.styleSheet()
    for selector in ("QToolButton:hover", "QGroupBox::title", "QCheckBox", "QTabBar::tab:selected"):
        assert selector in sheet
    window._plot.resize(400, 150)
    image = window._plot.grab().toImage()
    assert image.pixelColor(0, 0).name() == "#ffffff"

