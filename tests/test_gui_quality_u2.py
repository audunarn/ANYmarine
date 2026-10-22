"""GUI quality slice U2: logic behind the easy-use fixes (error wording, contrast, enabling, snapshot discovery)."""
from types import SimpleNamespace

from conftest import requires_qt


def _luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_secondary_text_colours_reach_4_5_to_1():
    from anymarine.qtapp import theme
    for background in (theme.APP_BG, theme.SUBTLE_BG, theme.PANEL_BG, "#e9eef5"):
        assert _contrast(theme.MUTED, background) >= 4.5, background
    assert _contrast(theme.WARN_TEXT, theme.WARN_BG) >= 4.5
    assert _contrast(theme.ACCENT, theme.ACCENT_SOFT) >= 4.5


@requires_qt
def test_missing_package_gets_a_sentence_and_other_errors_stay_verbatim():
    from anymarine.qtapp.waves import friendly_error
    try:
        import anymarine_no_such_module_xyz  # noqa: F401
    except ImportError as error:
        text, detail = friendly_error(error)
        assert 'anymarine_no_such_module_xyz' in text and 'not installed' in text
        assert 'No module named' not in text and 'No module named' in detail
    error = ModuleNotFoundError("No module named 'anyloads'", name='anyloads')
    text, detail = friendly_error(error)
    assert text == 'Mechanical waves need the anyloads package, which is not installed.'
    assert detail == "No module named 'anyloads'"
    assert friendly_error(ValueError('Load a project first')) == ('Load a project first', '')


def test_find_snapshots_recognises_single_and_pair_folders(tmp_path):
    from anymarine.qtapp.assessment import find_snapshots
    single = tmp_path / 'assess' / 'one'
    (single / 'cases').mkdir(parents=True)
    (single / 'reports').mkdir()
    pair = tmp_path / 'pair-1'
    pair.mkdir()
    (pair / 'pair.json').write_text('{}')
    (tmp_path / 'other' / 'cases').mkdir(parents=True)          # cases without reports: not a snapshot
    (tmp_path / 'a' / 'b' / 'c' / 'deep' / 'cases').mkdir(parents=True)
    (tmp_path / 'a' / 'b' / 'c' / 'deep' / 'reports').mkdir()   # deeper than two levels: not listed
    assert find_snapshots(tmp_path) == [str(single), str(pair)]
    assert find_snapshots(tmp_path / 'missing') == []


@requires_qt
def test_workflow_dialogs_enable_their_action_only_when_valid(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.assessment import (AssessSingleDialog, PairDialog, RegisterCaseDialog,
                                            ReopenSnapshotDialog)
    QApplication.instance() or QApplication([])
    register = RegisterCaseDialog()
    assert register._ok.text() == 'Register case' and not register._ok.isEnabled()
    register._spec_edit.setText('spec.json')
    assert not register._ok.isEnabled()
    register._target_edit.setText('case.json')
    assert register._ok.isEnabled()
    assess = AssessSingleDialog(['r1'], 'r1')
    assert assess._ok.text() == 'Assess result' and not assess._ok.isEnabled()
    assess._case_edit.setText('case.json')
    assert assess._ok.isEnabled()
    assert not AssessSingleDialog([], '')._ok.isEnabled()
    pair = PairDialog(lambda root: ['r1'])
    assert pair._ok.text() == 'Assess pair' and not pair._ok.isEnabled()
    for member in (pair.coarse(), pair.fine()):
        member._root_edit.setText(str(tmp_path))
        member.reload_results()
        member._case_edit.setText('case.json')
    assert not pair._ok.isEnabled()
    pair._out_edit.setText(str(tmp_path / 'out'))
    assert pair._ok.isEnabled()
    snap = tmp_path / 'snap'
    (snap / 'cases').mkdir(parents=True)
    (snap / 'reports').mkdir()
    reopen = ReopenSnapshotDialog(project_root=tmp_path)
    assert not reopen._ok.isEnabled() and reopen._snapshots.count() == 1
    reopen._snapshots.setCurrentRow(0)
    assert reopen.snapshot_root() == str(snap) and reopen._ok.isEnabled()


@requires_qt
def test_rao_graphs_tab_shows_an_empty_card_until_a_table_exists():
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.rao_panel import RaoPanel
    from test_rao_view import TABLE
    QApplication.instance() or QApplication([])
    panel = RaoPanel(SimpleNamespace(_project_root=None, _project=None, _dirty=False))
    panel.refresh_graphs()
    assert panel.graphs_stack.currentWidget() is panel.graphs_empty
    assert 'Register case' in panel.graphs_empty.text()
    panel.table = TABLE
    panel._fill_graph_controls()
    assert panel.graphs_stack.currentWidget() is not panel.graphs_empty
    panel._clear()
    assert panel.graphs_stack.currentWidget() is panel.graphs_empty


@requires_qt
def test_sea_state_dialog_shows_ranges_and_resets():
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from anymarine.qtapp.sea_state_dialog import SeaStateDialog
    QApplication.instance() or QApplication([])
    data = dict(jonswap=dict(significant_height=0.5, peak_period=8.0), seed=7)
    project = SimpleNamespace(native_inputs=SimpleNamespace(wave_realization=SimpleNamespace(data=data)))
    dialog = SeaStateDialog(project)
    labels = [dialog.layout().itemAt(i, dialog.layout().ItemRole.LabelRole).widget().text() for i in range(2)]
    assert '(0 to 2)' in labels[0] and '(1 to 30)' in labels[1]
    dialog.height.setValue(1.5)
    dialog.period.setValue(12.)
    dialog.seed.setValue(99)
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
    assert dialog.parameters() == dict(significant_height=0.5, peak_period=8.0, seed=7)


def test_channel_titles_are_readable_and_units_are_never_guessed():
    from anymarine.qtapp.plot import channel_title
    assert channel_title('kinetic_energy') == ('Kinetic energy', 'J')
    assert channel_title('equilibrium/force_residual N') == ('Equilibrium / force residual', 'N')
    assert channel_title('mooring/node0/position x') == ('Mooring / node0 / position x', '')
    assert channel_title('link_damping_power') == ('Link damping power', 'W')
    assert channel_title('') == ('', '')


@requires_qt
def test_time_series_plot_renders_with_axes_and_keeps_stored_arrays():
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.plot import TimeSeriesPlot
    QApplication.instance() or QApplication([])
    plot = TimeSeriesPlot()
    plot.resize(400, 200)
    plot.set_series('kinetic_energy', [0., 1., 2., 3.], [0., 2., 1., 4.])
    plot.set_cursor(2)
    assert plot.series_values() == (0., 2., 1., 4.) and plot.cursor_index() == 2
    assert not plot.grab().isNull()
    plot.set_series('', [], [])
    assert not plot.grab().isNull()
