"""Bundled regular-wave replay: bodies and their forcing wave move together."""
import numpy as np
import pytest

from conftest import requires_qt
from anymarine.hashing import input_hash
from anymarine.native_examples import regular_wave_mooring_project
from anymarine.services import ApplicationService


def test_example_is_the_declared_builder_solved_in_its_saved_wave():
    project = ApplicationService().load_regular_wave_example()
    (result,) = project.results
    assert result.kind == 'mechanical_development' and result.frame_count() == 251
    assert result.input_hash == input_hash(regular_wave_mooring_project()) == input_hash(project)
    assert not ApplicationService().result_stale(project, result)
    native = project.native_inputs
    for env in (native.morison.data['environment'], native.mooring.data['environment']):
        (component,) = env['components']
        assert dict(component) == dict(amplitude=.3, omega=2., direction_degrees=0., phase=0.)
        assert all(not any(v) for v in env['current']['velocities'])
    # The wave visibly moves the bodies (surge and heave of decimetres).
    for body_id in result.body_ids:
        positions = np.asarray(result.positions[body_id])
        assert np.ptp(positions[:, 0]) > .1 and np.ptp(positions[:, 2]) > .1


def _check_frame(w, result, index):
    waves = w._waves
    assert waves.follows_replay and waves.field['kind'] == 'mechanical_incident'
    assert waves.time == result.times[index]
    expected = waves.mechanical_environment.surface_elevation(waves.field['vertices'][:, :2], result.times[index])
    np.testing.assert_allclose(waves.handle.mesh.positions[:, 2], expected, atol=1e-12, rtol=0.)
    for body_id in result.body_ids:
        np.testing.assert_allclose(w._handles[body_id].transform[:3, 3], result.positions[body_id][index],
                                   atol=1e-12, rtol=0.)
        np.testing.assert_allclose(w._handles[body_id].transform[:3, :3],
                                   result.rotation_matrices[body_id][index], atol=1e-12, rtol=0.)


@requires_qt
def test_loading_shows_matched_waves_and_play_moves_both():
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(viewer_backend='software')
    try:
        w.load_regular_wave_example()
        result = w._current_result
        assert w._waves.mode.currentIndex() == 3 and w._waves.active
        assert 'drove the shown result' in w._waves.note.text()
        assert all(c.isHidden() for c in (w._waves.amplitude, w._waves.omega, w._waves.heading, w._waves.depth))
        assert w._waves.status.text().startswith('Saved mechanical incident waves — 1 component;')
        _check_frame(w, result, 0)
        w._toggle_play()
        assert w._play_timer.isActive()
        for _ in range(30):
            w._advance_frame()
        assert w._play_timer.isActive() and w._current_frame == 30
        _check_frame(w, result, 30)
        # Switching the matched waves off and on while playing keeps playing.
        w._waves.mode.setCurrentIndex(0)
        assert not w._waves.amplitude.isHidden() and not w._waves.depth.isHidden()
        w._waves.mode.setCurrentIndex(3)
        assert w._play_timer.isActive()
        w._advance_frame()
        _check_frame(w, result, 31)
        # The wave panel's own clock never runs while it follows the replay.
        w._waves.toggle_play()
        assert not w._waves.timer.isActive() and w._waves.play.text() == 'Wave time follows replay'
    finally:
        w._play_timer.stop()
        w._dirty = False
        w.close()
        app.processEvents()


@requires_qt
def test_result_without_waves_is_not_matched():
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(viewer_backend='software')
    try:
        w.load_example()  # synthetic two-body replay: no forcing wave
        assert w._current_result is not None and w._waves.mode.currentIndex() == 0
        assert not w._waves.active
        assert w._waves.match_result(w._current_result) is False
    finally:
        w._dirty = False
        w.close()
        app.processEvents()
