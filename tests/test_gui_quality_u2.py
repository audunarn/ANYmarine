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
def test_workflow_dialog_buttons_stay_enabled_as_before_with_empty_fields(tmp_path):
    """Only the labels changed (U2); enablement is the original one, so the buttons are never disabled."""
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.assessment import (AssessSingleDialog, PairDialog, RegisterCaseDialog,
                                            ReopenSnapshotDialog)
    QApplication.instance() or QApplication([])
    register = RegisterCaseDialog()
    assert register._ok.text() == 'Register case' and register._ok.isEnabled()
    assess = AssessSingleDialog(['r1'], 'r1')
    assert assess._ok.text() == 'Assess result' and assess._ok.isEnabled()
    assert AssessSingleDialog([], '')._ok.isEnabled()
    pair = PairDialog(lambda root: ['r1'])
    assert pair._ok.text() == 'Assess pair' and pair._ok.isEnabled()
    reopen = ReopenSnapshotDialog(project_root=tmp_path)
    assert reopen._ok.text() == 'Reopen snapshot' and reopen._ok.isEnabled()


@requires_qt
def test_native_inputs_and_waves_buttons_are_enabled_as_before():
    from PySide6.QtWidgets import QApplication, QWidget
    QApplication.instance() or QApplication([])
    from anymarine.native_examples import morison_two_body_project
    from anymarine.qtapp.native_inputs import NativeInputsDialog
    from anymarine.qtapp.waves import WavesPanel
    from PySide6.QtCore import QObject, Signal

    class Jobs(QObject):
        wave_ready = Signal(object, object)
        state_changed = Signal(object, object)
    window = QWidget()
    window._job_manager = Jobs()
    panel = WavesPanel(window)
    assert panel.handle is None and panel.job_id is None
    assert panel.play.isEnabled() and panel.cancel_button.isEnabled()      # as on origin/main
    dialog = NativeInputsDialog(morison_two_body_project())
    assert dialog._edit_member_button.isEnabled() and dialog._remove_member_button.isEnabled()
    dialog.close()


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


def _plot_image(label='kinetic_energy', abscissa=None, size=(400, 200)):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.plot import TimeSeriesPlot
    QApplication.instance() or QApplication([])
    plot = TimeSeriesPlot()
    plot.resize(*size)
    if abscissa:
        plot._abscissa = abscissa
    plot.set_series(label, [0., 1., 2., 3.], [0., 2., 1., 4.])
    plot.set_cursor(2)
    assert plot.series_values() == (0., 2., 1., 4.) and plot.cursor_index() == 2
    return plot, plot.grab().toImage()


def _ink(image, x0, y0, x1, y1):
    """Number of non-background pixels in a rectangle (the plot paints on white)."""
    count = 0
    for y in range(max(y0, 0), min(y1, image.height())):
        for x in range(max(x0, 0), min(x1, image.width())):
            if (image.pixel(x, y) & 0xFFFFFF) != 0xFFFFFF:
                count += 1
    return count


@requires_qt
def test_time_series_plot_draws_title_axis_labels_and_caption():
    plot, image = _plot_image()
    w, h = image.width(), image.height()
    assert (w, h) == (400, 200) or image.devicePixelRatio() > 1
    w, h = int(w / image.devicePixelRatio()), int(h / image.devicePixelRatio())
    ratio = image.devicePixelRatio()
    # each strip is outside the curve/grid area, so ink there can only come from the drawn text
    title = _ink(image, 0, 0, int(w * ratio), int(8 * ratio) + 12)          # title strip above the frame
    y_labels = _ink(image, 0, int(40 * ratio), int(26 * ratio), int(h * ratio) - int(50 * ratio))   # y tick labels
    caption = _ink(image, int(w * ratio * 0.6), int((h - 14) * ratio), int(w * ratio), int(h * ratio))   # axis caption
    assert title > 20 and y_labels > 20 and caption > 20, (title, y_labels, caption)
    # the text content matters: another channel/abscissa changes the strips
    _, other = _plot_image(label='', abscissa='stage')
    assert _ink(other, 0, 0, int(w * ratio * 0.5), int(8 * ratio) + 12) < title
    assert image != other
    empty = _plot_image()[0]
    empty.set_series('', [], [])
    blank = empty.grab().toImage()
    assert _ink(blank, int(w * ratio * 0.6), int((h - 14) * ratio), int(w * ratio), int(h * ratio)) == 0   # no caption without a series
    assert not blank.isNull()


def test_nice_ticks_ordinary_ranges_are_unchanged():
    from anymarine.qtapp.rao_panel import _nice_ticks
    assert _nice_ticks(0, 1, 5) == [0.0, 0.5, 1.0]
    assert _nice_ticks(-1, 1, 4) == [-1.0, 0.0, 1.0]
    assert _nice_ticks(0.1, 0.7, 5) == [0.0, 0.2, 0.4, 0.6]
    assert _nice_ticks(0, 360, 5) == [0.0, 100.0, 200.0, 300.0, 400.0]
    assert _nice_ticks(1e9, 5e9, 4) == [0.0, 2e9, 4e9, 6e9]


def test_nice_ticks_survive_tiny_and_huge_magnitudes():
    from anymarine.qtapp.rao_panel import _nice_ticks
    for lo, hi in ((1e-13, 4e-13), (2e-20, 9e-20), (1e-8, 3e-8)):
        ticks = _nice_ticks(lo, hi, 4)
        assert len(set(ticks)) == len(ticks) >= 3, (lo, hi, ticks)
        labels = [f"{v:.4g}" for v in ticks]
        assert len(set(labels)) == len(labels) and "0" not in labels[1:], labels
        assert ticks == sorted(ticks) and ticks[0] <= lo and ticks[-1] >= hi - (hi - lo)
    ticks = _nice_ticks(1e-13, 4e-13, 4)
    assert any(t > 0 for t in ticks) and all(abs(t) < 1e-12 for t in ticks)
    big = _nice_ticks(1e9, 5e9, 4)
    assert len(set(f"{v:.4g}" for v in big)) == len(big)
