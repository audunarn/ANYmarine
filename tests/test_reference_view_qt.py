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
    titles = [a.text() for a in window._examples_menu.actions()]
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
