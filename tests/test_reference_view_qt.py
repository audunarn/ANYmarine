"""Reference comparison view in RAO mode and the L01 example loader (synthetic artifact only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import requires_qt

pytestmark = requires_qt

from PySide6.QtWidgets import QApplication  # noqa: E402

from anymarine.qtapp.mainwindow import MainWindow  # noqa: E402
from anymarine.reference_comparison import write  # noqa: E402
from test_reference_comparison import synthetic  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    win = MainWindow(viewer_backend="software")
    notices = {'warning': [], 'info': []}
    monkeypatch.setattr(win, "_notify_warning", lambda *a: notices['warning'].append(a))
    monkeypatch.setattr(win, "_notify_critical", lambda *a: None)
    monkeypatch.setattr(win, "_notify_info", lambda *a: notices['info'].append(a))
    # never read or write the user's real settings or local licensed data in tests
    remembered = []
    monkeypatch.setattr(win, "_remember_l01_reference", lambda path: remembered.append(path))
    win.notices, win.remembered = notices, remembered
    yield win
    win._dirty = False
    win.close()


def test_open_reference_shows_labelled_tab_with_one_line_per_source(qapp, window, tmp_path):
    path = write(synthetic(), tmp_path / 'ref.json')
    assert window._rao.open_reference(path)
    view = window._rao.reference_view
    assert window._rao.tabs.currentWidget() is view
    assert 'not an ANYmarine RAO case' in view.banner.text() and 'unqualified' in view.banner.text()
    assert [view.heading.itemData(i) for i in range(view.heading.count())] == [0.0, 90.0]
    roll = window._rao.reference_view.grid.cell('roll')
    assert [s.label for s in roll.series()] == ['Program 0', 'Program 1']
    view.display_buttons['phase'].setChecked(True)
    assert all(abs(y) <= 180 for s in roll.series() for y in s.ys)


def test_refused_reference_keeps_panel_usable(qapp, window, tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({'artifact': 'nope'}), encoding='utf-8')
    assert not window._rao.open_reference(bad)
    assert 'refused' in window._rao.status.text() and window._rao.reference_view is None


def test_l01_example_loads_vessel_and_opens_rao_mode(qapp, window, tmp_path, monkeypatch):
    path = write(synthetic(), tmp_path / 'prepared' / 'reference_comparison.json')
    monkeypatch.setattr(window, '_l01_reference_candidates', lambda: [tmp_path / 'missing.json', path])
    window.load_l01_reference_example()
    assert window._mode == 'rao' and window._rao.reference_view is not None
    assert [b.name for b in window._project.bodies] == ['Synthetic box']
    assert window._project.bodies[0].mass == 1000.0 and window._project_root is None
    assert 'unqualified' in window.windowTitle() and window.remembered == [path]
    assert window._marine.handle is not None          # the vessel sits in the marine scene


def test_l01_example_without_local_data_explains_and_can_be_cancelled(qapp, window, tmp_path, monkeypatch):
    monkeypatch.setattr(window, '_l01_reference_candidates', lambda: [tmp_path / 'missing.json'])
    monkeypatch.setattr(window, '_ask_open_file', lambda *a: '')
    before = window._project
    window.load_l01_reference_example()
    assert window.notices['info'] and 'not shipped' in window.notices['info'][0][1]
    assert window._project is before and window.remembered == []


def test_l01_example_refuses_a_tampered_file(qapp, window, tmp_path, monkeypatch):
    data = synthetic()
    data['title'] = 'changed after hashing'
    bad = tmp_path / 'tampered.json'
    bad.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(window, '_l01_reference_candidates', lambda: [bad])
    window.load_l01_reference_example()
    assert window.notices['warning'] and 'hash' in window.notices['warning'][0][1]
    assert window.remembered == []


def test_l01_example_is_in_the_examples_submenu(window):
    titles = [a.text() for a in window._advanced_examples_menu.actions()]
    assert any(t.startswith('Load L01 default vessel') for t in titles)


def test_default_l01_location_is_next_to_a_sibling_anyhydro_checkout(qapp, monkeypatch):
    win = MainWindow(viewer_backend="software")
    try:
        from PySide6.QtCore import QSettings
        monkeypatch.setattr(QSettings, 'value', lambda self, key, default=None: '')
        first = win._l01_reference_candidates()[0]
        assert first.parts[-4:] == ('examples', 'L01 Default vessel', 'anymarine-prepared', 'reference_comparison.json')
        assert first.parents[3].name == 'ANYhydro'
        assert Path(__file__).resolve().parents[1].parent == first.parents[4]
    finally:
        win._dirty = False
        win.close()


@pytest.mark.parametrize('fake', ['C:/x/anymarine/qtapp/mainwindow.py',
                                  'C:/venv/Lib/site-packages/anymarine/qtapp/mainwindow.py'])
def test_installed_layouts_skip_the_checkout_default_without_failing(qapp, monkeypatch, fake):
    import anymarine.qtapp.mainwindow as module
    from PySide6.QtCore import QSettings
    win = MainWindow(viewer_backend="software")
    try:
        monkeypatch.setattr(QSettings, 'value', lambda self, key, default=None: 'D:/picked/ref.json')
        monkeypatch.setattr(module, '__file__', fake)
        assert win._l01_reference_candidates() == [Path('D:/picked/ref.json')]
    finally:
        win._dirty = False
        win.close()


def test_rao_motion_follows_the_wave_clock_and_stops_cleanly(qapp, window, tmp_path, monkeypatch):
    import math
    import numpy as np
    from anymarine.reference_comparison import motion_rao, pose, rigid_motion
    data = synthetic()
    path = write(data, tmp_path / 'ref.json')
    monkeypatch.setattr(window, '_l01_reference_candidates', lambda: [path])
    window.load_l01_reference_example()
    view = window._rao.reference_view
    assert view.motion_period.currentData() == 4.0          # synthetic periods 4/8/12: nearest to 6 s
    view.heading.setCurrentIndex(view.heading.findData(90.0))
    view.motion_period.setCurrentIndex(view.motion_period.findData(8.0))
    view.motion_source.setCurrentIndex(1)
    view.motion_amplitude.setValue(1.5)
    handle = window._handles[window._project.bodies[0].body_id]
    rest = np.array(handle.transform, copy=True)
    view.animate_button.click()
    assert window._rao_motion is not None and window._waves.handle is not None
    assert window._waves.amplitude.value() == 1.5 and window._waves.heading.value() == 90.0
    assert window._waves.omega.value() == pytest.approx(2 * math.pi / 8.0, abs=1e-3)
    window._waves.advance()                                  # one wave tick, then the vessel follows it
    window._rao_motion_tick()
    t = window._waves.time
    assert t > 0
    rao = motion_rao(data, source=1, heading=90.0, period=8.0)
    expected = rigid_motion(*pose(rao, amplitude=1.5, omega=2 * math.pi / 8.0, time=t)) @ rest
    assert np.allclose(handle.transform, expected, atol=1e-12)
    assert 'unqualified' in window.statusBar().currentMessage()
    view.stop_button.click()
    assert window._rao_motion is None and window._waves.handle is None
    assert np.allclose(handle.transform, rest)


def _animate(qapp, window, tmp_path, monkeypatch):
    path = write(synthetic(), tmp_path / 'ref.json')
    monkeypatch.setattr(window, '_l01_reference_candidates', lambda: [path])
    window.load_l01_reference_example()
    view = window._rao.reference_view
    view.heading.setCurrentIndex(view.heading.findData(90.0))
    view.motion_period.setCurrentIndex(view.motion_period.findData(8.0))
    view.animate_button.click()
    return view


def test_vessel_and_wave_share_the_same_frequency(qapp, window, tmp_path, monkeypatch):
    _animate(qapp, window, tmp_path, monkeypatch)
    assert window._rao_motion['omega'] == window._waves.omega.value()
    assert window._waves.field['request']['omega'] == window._rao_motion['omega']


def test_wave_reset_or_mode_change_ends_the_animation(qapp, window, tmp_path, monkeypatch):
    _animate(qapp, window, tmp_path, monkeypatch)
    window._waves.mode.setCurrentIndex(0)                    # Off
    assert window._rao_motion is None
    window._waves.mode.setCurrentIndex(1)                    # incident waves again: no stale motion resumes
    window._waves.advance()
    window._rao_motion_tick()
    assert window._rao_motion is None
    _animate(qapp, window, tmp_path, monkeypatch)
    window._waves.amplitude.setValue(0.5)                    # changed wave inputs reset the panel
    assert window._rao_motion is None


def test_reference_rao_never_moves_another_project(qapp, window, tmp_path, monkeypatch):
    view = _animate(qapp, window, tmp_path, monkeypatch)
    assert window._rao_motion is not None
    window.load_example()                                    # another project; the Reference tab stays
    assert window._rao_motion is None
    view.animate_button.click()
    assert window._rao_motion is None and 'belong to that vessel' in window._rao.status.text()


def test_long_legend_labels_are_elided_inside_the_plot(qapp):
    from PySide6.QtGui import QFont, QFontMetrics
    from anymarine.qtapp.rao_panel import RaoAxes
    from anymarine.rao_view import Series
    metrics = QFontMetrics(QFont('Arial', 8))
    items = [Series('A very long program label that cannot fit anywhere in a narrow plot', 'line', (1,), (1,)),
             Series('Short', 'line', (1,), (1,))]
    rows = RaoAxes._legend_rows(items, metrics, 30, 200)
    for row in rows:
        width = sum(metrics.horizontalAdvance(text) + 26 for _, _, text in row)
        assert width <= 200 - 6


def test_replacing_the_reference_stops_motion_and_unbinds_the_vessel(qapp, window, tmp_path, monkeypatch):
    view = _animate(qapp, window, tmp_path, monkeypatch)
    assert window._rao_motion is not None
    other = synthetic()
    other['title'] = 'Another comparison'
    from anymarine.reference_comparison import artifact_hash
    other['artifact_hash'] = artifact_hash(other)
    assert window._rao.open_reference(write(other, tmp_path / 'other.json'))
    assert window._rao_motion is None and window._reference_binding is None
    view.animate_button.click()
    assert window._rao_motion is None and 'belong to that vessel' in window._rao.status.text()


def test_mean_drift_display_appears_only_with_drift(qapp, window, tmp_path):
    from test_reference_comparison import _with_drift
    path = write(synthetic(), tmp_path / 'plain.json')
    assert window._rao.open_reference(path)
    view = window._rao.reference_view
    assert view.display_buttons['drift'].isHidden()
    path = write(_with_drift(synthetic()), tmp_path / 'drift.json')
    assert window._rao.open_reference(path)
    view = window._rao.reference_view
    assert not view.display_buttons['drift'].isHidden() and view.drift_note.isHidden()
    view.display_buttons['drift'].setChecked(True)
    assert not view.drift_note.isHidden()
    surge, yaw = view.grid.cell('surge'), view.grid.cell('yaw')
    assert [s.label for s in surge.series()] == ['Program 0 - far field']
    assert surge.series()[0].ys == (1.0, 1.0, 1.0)              # heading 0: 1000 N/m^2 -> 1 kN/m^2
    assert yaw.series()[0].ys == (0.0, 2.0, 4.0)
    assert view.grid.cell('heave') is None or view.grid.cell('heave').isHidden()
    assert 'mean drift far field: synthetic' in view.sources.text()
    view.display_buttons['amplitude'].setChecked(True)
    assert view.drift_note.isHidden() and len(view.grid.cell('roll').series()) == 2
