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


# ---------------------------------------------------------------- M2: guidance and layout
import re  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QLabel, QPushButton  # noqa: E402

NON_WAVE_EXAMPLES = ("load_example", "load_native_example", "load_plate_example", "load_native_mooring_example",
                     "load_native_fluid_example", "load_native_sea_state_example", "load_native_morison_example",
                     "load_native_line_hydro_example", "load_native_contact_example", "load_equilibrium_example")


def _pump(qapp, n=6):
    for _ in range(n):
        qapp.processEvents()


def test_f18_welcome_overlay_and_tree_placeholder(qapp, window, monkeypatch):
    welcome = window._welcome
    _pump(qapp)
    assert window._project is None and welcome.isVisibleTo(window._viewport)
    assert welcome.parent() is window._viewport and welcome.parent() is not window.viewer   # not part of the 3D view
    assert window._viewport_layout.indexOf(welcome) < 0                                     # not in the layout: fit unaffected
    labels = sorted(b.text() for b in welcome.findChildren(QPushButton))
    assert labels == ["Help", "Open project", "Try an example"]
    assert welcome.geometry().intersects(window.viewer.geometry()) and window._viewport.rect().contains(welcome.geometry())
    assert window._tree_placeholder.text().startswith("No project open") and window._tree_placeholder.isVisibleTo(window._tree)
    calls = []
    monkeypatch.setattr(window, "_open_project", lambda: calls.append("open"))
    monkeypatch.setattr(window, "_show_loading_help", lambda: calls.append("help"))
    for button in welcome.findChildren(QPushButton):
        if button.text() != "Try an example":
            button.click()
    assert sorted(calls) == ["help", "open"]
    next(b for b in welcome.findChildren(QPushButton) if b.text() == "Try an example").click()
    _pump(qapp)
    assert window._project is not None and not welcome.isVisibleTo(window._viewport)
    assert not window._tree_placeholder.isVisibleTo(window._tree)
    assert window._handles and window.viewer.isVisible()


def test_f18_welcome_stays_hidden_once_a_project_exists(qapp, window):
    window.load_example()
    _pump(qapp)
    assert not window._welcome.isVisibleTo(window._viewport)
    window._new_project()
    assert not window._welcome.isVisibleTo(window._viewport)      # a new (empty) project is still a project


def test_f17_loading_help_is_short_plain_steps(window):
    window._show_loading_help()
    title, text = window._info[-1][:2]
    assert title == "Loading models"
    assert text.count("\n") <= 6 and "Load native coupled fluid example" not in text
    for needed in ("project.json", "CAD/mesh-file import is not available yet", "View > Renderer > Software", "Examples"):
        assert needed in text
    assert [b for b in window._welcome.findChildren(QPushButton) if b.text() == "Help"]      # reachable from the welcome card too


@pytest.mark.parametrize("loader", NON_WAVE_EXAMPLES)
def test_f10_non_wave_examples_land_in_model_mode_with_a_next_hint(qapp, window, loader):
    window._set_mode("waves")
    getattr(window, loader)()
    _pump(qapp)
    assert window._mode == "model" and window._context_dock.isHidden()
    hint = window._next_hint
    assert hint.isVisibleTo(window._viewport) and hint.text().startswith("Next:") and "\n" not in hint.text()


@pytest.mark.parametrize("loader", ("load_regular_wave_example", "load_calculated_wave_example", "load_hams_wave_bundle"))
def test_f10_wave_examples_land_in_waves_mode(qapp, window, loader):
    window._set_mode("model")
    getattr(window, loader)()
    _pump(qapp)
    if window._project is None:                                   # optional data/package missing in this environment
        pytest.skip(f"{loader} unavailable here")
    assert window._mode == "waves" and window._next_hint.text().startswith("Next:")


def test_f10_next_hint_clears_when_the_project_changes(qapp, window):
    window.load_example()
    assert window._next_hint.isVisibleTo(window._viewport)
    window._new_project()
    assert not window._next_hint.isVisibleTo(window._viewport)


def test_f05_dead_end_actions_are_disabled_with_a_precondition_tooltip(qapp, window):
    jobs = _menu(window, "Jobs")
    by_text = {a.text(): a for a in jobs.actions() if not a.isSeparator()}
    project_bound = ("Run &linear development job", "&Run synthetic job", "Run configured HAMS fields…",
                     "Run coupled static equilibrium job", "Import &hydrodynamic inputs...",
                     "Import derived hydrodynamic &inputs...", "Import native mechanical inputs...")
    for text in project_bound:
        assert not by_text[text].isEnabled(), text
        assert "File > Examples" in by_text[text].toolTip(), text
    for text, key in (("Run native mechanical job...", "native"), ("Edit native mechanical inputs...", "native"),
                      ("Edit shared sea state...", "seeded-wave"), ("Adopt selected static equilibrium…", "equilibrium")):
        assert not by_text[text].isEnabled() and key in by_text[text].toolTip(), text
    assert by_text["Cancel &all jobs"].isEnabled()
    for a in _menu(window, "Assessment").actions():
        if a.text() in ("&Register case...", "&Assess selected result..."):
            assert not a.isEnabled() and "Run linear development job" in a.toolTip()
        else:
            assert a.isEnabled()                                  # pair and reopen name their own inputs
    assert not window._actions["run"].isEnabled() and not window._actions["save"].isEnabled()
    calc = next(a for a in window._toolbar.actions() if a.text() == "Calculate waves")
    assert not calc.isEnabled() and "File > Examples" in calc.toolTip()

    window.load_example()                                         # two-body replay: a project without native inputs
    _pump(qapp)
    for text in project_bound:
        assert by_text[text].isEnabled(), text
    assert window._actions["run"].isEnabled() and window._actions["save"].isEnabled() and calc.isEnabled()
    assert not by_text["Run native mechanical job..."].isEnabled()
    assert not by_text["Edit shared sea state..."].isEnabled()
    assert not by_text["Adopt selected static equilibrium…"].isEnabled()
    assert by_text["Run &linear development job"].toolTip() and "File > Examples" not in by_text["Run &linear development job"].toolTip()

    window.load_native_sea_state_example()
    _pump(qapp)
    assert by_text["Run native mechanical job..."].isEnabled() and by_text["Edit native mechanical inputs..."].isEnabled()
    assert by_text["Edit shared sea state..."].isEnabled()
    window._current_result = SimpleNamespace(kind="equilibrium_development")
    window._refresh_action_states()
    assert by_text["Adopt selected static equilibrium…"].isEnabled()


def test_f05_assessment_actions_follow_stored_linear_inputs(qapp, window):
    window.load_example()
    register = next(a for a in _menu(window, "Assessment").actions() if a.text() == "&Register case...")
    assert not register.isEnabled()
    inputs = window._service.linear_demo_inputs(window._project)
    window._mark_edited(window._service.set_linear_inputs(window._project, inputs))
    assert register.isEnabled()


def test_f04_message_names_the_route_that_provides_linear_inputs(window):
    assert window._assessment_project_ready() is False
    first = window._info[-1][1]
    assert "File > Examples" in first and "linear" in first.lower()
    window.load_example()
    assert window._assessment_project_ready() is False
    text = window._info[-1][1]
    assert "Jobs > Run linear development job" in text and "stored linear inputs" in text
    assert text != "Open a project with stored linear inputs first."


def test_f11_tree_has_type_icons_and_friendly_result_names(qapp, window):
    window.load_example()
    model = window._tree_model

    def walk(parent):
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            yield index
            yield from walk(index)
    nodes = list(walk(model.index(-1, -1)))
    assert nodes
    for index in nodes:
        icon = model.data(index, Qt.ItemDataRole.DecorationRole)
        assert icon is not None and not icon.isNull(), model.data(index)
    texts = [model.data(i) for i in nodes]
    assert "synthetic_unqualified" not in texts
    result_index = next(i for i in nodes if model.node_for_index(i).kind == "result")
    assert model.data(result_index) == "Synthetic replay (demo)"
    assert "synthetic_unqualified" in model.data(result_index, Qt.ItemDataRole.ToolTipRole)
    from anymarine.qtapp import icons
    assert {"body", "surface", "member", "equilibrium", "settings"} <= set(icons.names())


def test_f11_channel_names_never_show_body_ids(qapp, window):
    window.load_example()
    combo = window._channel_combo
    ids = [b.body_id for b in window._project.bodies]
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert texts and not any(bid in t for t in texts for bid in ids)
    assert any("Body A" in t for t in texts) and any("Body B" in t for t in texts)
    assert all(combo.itemData(i) in window._current_result.channels for i in range(combo.count()))
    assert any(bid in combo.itemData(i) for i in range(combo.count()) for bid in ids)   # raw ids stay data


def test_f11_properties_empty_state_is_short_and_contextual(qapp, window):
    assert "No project open" in window._details.empty_text()
    window.load_example()
    _pump(qapp, 12)
    after = window._details.empty_text()
    assert after.startswith("Select a body") and len(after) < 110
    assert window._details.is_empty()
    sizes = window._navigator.sizes()
    assert sizes[0] > sizes[1]                                    # the tree gets the room while nothing is selected


def test_f14_drawer_heading_and_no_squeeze(qapp, window):
    window.load_example()
    _pump(qapp, 20)
    header = window._jobs_dock.titleBarWidget()
    assert header is not None
    heading = next(label for label in header.findChildren(QLabel) if label.text() == "JOBS AND LOG")
    assert heading.property("role") == "section"
    tree_before, viewer_before = window._tree.height(), window.viewer.size().height()
    window._set_jobs_drawer(True)
    _pump(qapp, 20)
    assert not window._jobs_dock.isHidden()
    assert abs(window._tree.height() - tree_before) <= 2            # the project tree keeps its height
    assert window.viewer.size().height() >= 0.7 * viewer_before             # the 3D view loses well under 30 %
    window._set_jobs_drawer(False)
    _pump(qapp, 20)
    assert window._jobs_dock.isHidden() and abs(window.viewer.size().height() - viewer_before) <= 2


def test_f09_primary_toolbar_actions_have_text_and_distinct_tooltips(window):
    bar = window._toolbar
    styles = {a.text().replace("&", ""): bar.widgetForAction(a).toolButtonStyle()
              for a in bar.actions() if bar.widgetForAction(a) is not None and a.text()}
    beside = Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    assert styles["Cancel all jobs"] == beside and styles["Fit model"] == beside
    tips = [a.toolTip() for a in bar.actions() if not a.isSeparator()]
    assert all(tips) and len(set(tips)) == len(tips)


def test_f23_default_panel_width_is_consistent_and_per_mode_width_is_kept(qapp, window):
    widths = {}
    for mode in ("waves", "rao", "assessment", "results"):
        window._mode_actions[mode].trigger()
        _pump(qapp, 12)
        widths[mode] = window._context_dock.width()
    assert max(widths.values()) - min(widths.values()) <= 4, widths
    window._mode_actions["waves"].trigger()
    _pump(qapp, 6)
    window.resizeDocks([window._context_dock], [widths["waves"] - 30], Qt.Orientation.Horizontal)
    _pump(qapp, 6)
    window._mode_actions["rao"].trigger()
    _pump(qapp, 12)
    window._mode_actions["waves"].trigger()
    _pump(qapp, 12)
    assert abs(window._context_dock.width() - (widths["waves"] - 30)) <= 4
    from anymarine.qtapp.theme import WORKBENCH_STYLESHEET
    assert re.search(r"QLabel#PanelTitle\s*\{[^}]*border-bottom", WORKBENCH_STYLESHEET)


def test_f24_transport_shows_total_duration_and_renderer_label_is_quiet(qapp, window):
    window.load_example()
    _pump(qapp)
    text = window._transport_label.text()
    assert re.search(r"frame 1/\d+ · t = 0\.00 / \d+\.\d\d s", text), text
    label = window._viewer_backend_label
    assert label.text() == "ANY3dView: software"                    # asserted elsewhere: text unchanged
    assert label.objectName() == "RendererStatus" and "View > Renderer" in label.toolTip()
    assert "software" in label.toolTip()


def test_f25_mode_bar_icons_scale_with_the_display(qapp, window, monkeypatch):
    assert MainWindow._mode_icon_size(1.0) == 22
    assert MainWindow._mode_icon_size(1.5) > 22 and MainWindow._mode_icon_size(2.0) >= MainWindow._mode_icon_size(1.5)
    monkeypatch.setattr(window, "devicePixelRatioF", lambda: 1.5)
    window._apply_icon_scale()
    assert window._mode_bar.iconSize().width() == MainWindow._mode_icon_size(1.5)
    monkeypatch.setattr(window, "devicePixelRatioF", lambda: 1.0)
    window._apply_icon_scale()
    assert window._mode_bar.iconSize().width() == 22
    from anymarine.qtapp.theme import WORKBENCH_STYLESHEET
    assert re.search(r"QLabel#DevBadge\s*\{[^}]*margin-left", WORKBENCH_STYLESHEET)


def test_f11_plot_title_follows_the_readable_channel_name(qapp, window):
    window.load_example()
    _pump(qapp)
    ids = [b.body_id for b in window._project.bodies]
    shown = window._plot.toolTip()
    assert "Body" in shown and not any(bid in shown for bid in ids)
    assert not any(bid in window._channel_combo.currentText() for bid in ids)
    raw = window._channel_combo.currentData()
    assert raw in window._current_result.channels and window._plot._label == raw      # the stored id stays the data


# ---- review fixes on M1+M2 (Codex findings) --------------------------------------------------------------------

_UUID_A = "f3879aaf-4300-4b6b-b18f-2c15584e2dd8"
_UUID_B = "cad3c9b9-4c64-43db-a13e-6e4a1d742b86"


def test_title_parts_removes_ids_only_as_whole_tokens():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    n = {_UUID_A: "Body M"}
    assert C.title_parts("equilibrium/moment_residual Nm", n) == ("Equilibrium / moment residual", "Nm")
    assert C.title_parts(f"{_UUID_A}/moment_residual Nm", n) == ("Moment residual (Body M)", "Nm")
    assert C.title_parts(f"heave:{_UUID_A}", n) == ("Heave (Body M)", "")
    assert C.title_parts("force Nm", {"N": "X", "m": "Y"}) == ("Force", "Nm")     # the unit suffix is never edited


def test_title_parts_two_bodies_attribute_correctly():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    names = {_UUID_A: "Body A", _UUID_B: "Body AB"}
    assert C.title_parts(f"heave:{_UUID_B}", names) == ("Heave (Body AB)", "")
    assert C.title_parts(f"heave:{_UUID_A}", names) == ("Heave (Body A)", "")
    assert C.title_parts(f"{_UUID_B}/surge_abc", names) == ("Surge abc (Body AB)", "")
    assert C.title_parts(f"{_UUID_A}/{_UUID_B}/tension N", names) == ("Tension (Body A, Body AB)", "N")


def test_title_parts_bundled_example_titles_unchanged():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    n = {_UUID_A: "Body A", _UUID_B: "Body B"}
    expected = {
        f"heave:{_UUID_B}": ("Heave (Body B)", ""), f"surge:{_UUID_A}": ("Surge (Body A)", ""),
        f"Heave:{_UUID_A}": ("Heave (Body A)", ""), f"{_UUID_A}/surge": ("Surge (Body A)", ""),
        f"{_UUID_B}/force_x N": ("Force x (Body B)", "N"),
        f"equilibrium/{_UUID_A}/moment_residual Nm": ("Equilibrium / moment residual (Body A)", "Nm"),
    }
    for raw, want in expected.items():
        assert C.title_parts(raw, n) == want, raw


def _detached(window, qapp):
    """A window with no current project but a detached snapshot replay, as when a snapshot is reopened."""
    window.load_example()
    project, result = window._project, window._current_result
    window._set_project(project)
    window._project = None
    window._replay_project = None
    window._current_result = None
    window._refresh_welcome()
    window._refresh_action_states()
    _pump(qapp)
    return project, result


def _calc(window):
    return next(a for a in window._toolbar.actions() if a.text() == "Calculate waves")


def test_calculate_waves_follows_the_effective_replay_project(qapp, window):
    project, result = _detached(window, qapp)
    assert window._project is None and not _calc(window).isEnabled()
    window._set_snapshot_replay(project, result, "replayed")
    _pump(qapp)
    assert window._project is None and window._replay_project is not None
    assert _calc(window).isEnabled()                       # WavesPanel.request() accepts the replay project
    window._set_result(None)                               # leaving replay with no project
    assert window._replay_project is None and not _calc(window).isEnabled()


def test_welcome_treats_a_detached_replay_as_content(qapp, window):
    project, result = _detached(window, qapp)
    assert window._welcome.isVisibleTo(window._viewport) and window._tree_placeholder.isVisibleTo(window._tree)
    window._set_snapshot_replay(project, result, "replayed")
    _pump(qapp)
    assert not window._welcome.isVisibleTo(window._viewport)
    assert not window._tree_placeholder.isVisibleTo(window._tree)
    window._set_result(None)                               # clears the replay; no project -> empty state again
    _pump(qapp)
    assert window._welcome.isVisibleTo(window._viewport) and window._tree_placeholder.isVisibleTo(window._tree)


# ---- contract (b): only UUID-shaped ids at known identity positions are replaced ---------------------------------
# Channel formats produced by the native jobs / solvers (all channel names the GUI can list):
#   '<bid>/WORLD vx' ... '<bid>/WORLD alphaz', '<bid>/total external Fx'    (native_jobs.py)
#   'wrench["<contribution>","<bid>"]/Fx'                                    (native_jobs.py:263, rao_extraction)
#   'kinetic_energy', 'link_potential_energy', 'link_damping_power', 'residual_norm', 'newton_iterations'
#   'equilibrium/force_residual N', 'equilibrium/moment_residual Nm'         (equilibrium_jobs.py)
#   'mooring/...', 'mooring[<line>,<segment>]/tensions', 'mooring/contact/...'  (no body id)
#   'q:<label>', 'v:<label>', 'a:<label>', 'force_<kind>:<label>'            (linear.py; label = '<bid>:<dof>' style)
#   'Heave:<bid>' / 'heave:<bid>' name:id suffix, '<bid>/force_x N', 'equilibrium/<bid>/moment_residual Nm' (examples)


def test_non_uuid_body_ids_never_alter_global_channels():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    assert C.title_parts("kinetic_energy", {"energy": "E"}) == ("Kinetic energy", "J")
    assert C.title_parts("equilibrium/force_residual N", {"equilibrium": "Q"}) == ("Equilibrium / force residual", "N")
    assert C.title_parts("equilibrium/moment_residual Nm", {"m": "Body M"}) == ("Equilibrium / moment residual", "Nm")
    assert C.title_parts("heave:m", {"m": "Body M"}) == ("Heave:m", "")           # not a UUID: raw


def test_uuid_ids_at_known_positions_map_as_before():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    n = {_UUID_A: "Body A", "energy": "E", "equilibrium": "Q"}
    assert C.title_parts(f"Heave:{_UUID_A}", n) == ("Heave (Body A)", "")
    assert C.title_parts(f"{_UUID_A}/force x N", n) == ("Force x (Body A)", "N")
    assert C.title_parts(f"{_UUID_A}/WORLD vx", n) == ("WORLD vx (Body A)", "")
    assert C.title_parts(f"{_UUID_A}/total external Fx", n) == ("Total external Fx (Body A)", "")
    assert C.title_parts(f"equilibrium/{_UUID_A}/moment_residual Nm", n) == (
        "Equilibrium / moment residual (Body A)", "Nm")
    assert C.title_parts(f"Heave:{_UUID_A.upper()}", {_UUID_A.upper(): "Up"}) == ("Heave (Up)", "")


def test_wrench_channel_shows_body_name_and_keeps_raw_id():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    raw = f'wrench["dry","{_UUID_A}"]/Fx'
    title, unit = C.title_parts(raw, {_UUID_A: "Body A"})
    assert "Body A" in title and _UUID_A not in title and "dry" in title and title.startswith("Wrench")
    assert C.title_parts(raw, {"x": "X"})[0].count(_UUID_A) == 1               # unknown body: raw
    combo = C()
    combo.set_channels([raw], {_UUID_A: "Body A"})
    assert combo.itemData(0) == raw and combo.itemData(0, Qt.ItemDataRole.ToolTipRole) == raw
    assert _UUID_A not in combo.itemText(0)


def test_uuid_inside_longer_token_is_untouched():
    from anymarine.qtapp.mainwindow import _ChannelCombo as C
    n = {_UUID_A: "Body A"}
    for raw in (f"x{_UUID_A}y/a", f"heave:{_UUID_A}x", f"a_{_UUID_A}/b", f"{_UUID_A}_x/b"):
        title, _ = C.title_parts(raw, n)
        assert "Body A" not in title and _UUID_A.lower() in title.lower(), raw
