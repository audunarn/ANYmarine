"""ANYmarine Qt Widgets shell: one QMainWindow hosting the ANY3dView viewer.

Public viewer contract only: ``create_viewer(parent, backend,
host=QtViewerHostAdapter())``; meshes are added once and replayed through
``handle.set_transform(matrix)``. All Qt callbacks run on the GUI thread.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from any3dview import MeshArrays, create_viewer
from any3dview.qt import QtViewerHostAdapter

from ..assessment_jobs import (
    ASSESSMENT_KIND_ADJUDICATE_PAIR,
    ASSESSMENT_KIND_ADJUDICATE_SINGLE,
    ASSESSMENT_KIND_PAIR,
    ASSESSMENT_KIND_PREPARE,
    AssessmentJobError,
    adjudication_job,
    pair_assessment_job,
    prepare_assessment_job,
    project_from_frozen,
    single_assessment_job,
)
from ..development_status import (
    DevelopmentStatusError,
    load_development_status,
    startup_summary,
)
from ..domain import (
    LINEAR_SOURCE_ANALYTIC_DEMO,
    LINEAR_SOURCE_EXPLICIT,
    LINEAR_SOURCE_HYDRO,
    ProjectModel,
    ResultRecord,
)
from ..linear import LinearError, linear_input_hash, linear_result_source
from ..linear_assessment import AssessmentError, read_case_artifact
from ..persistence import ProjectError, ProjectStore
from ..services import ApplicationService, ServiceError
from ..transforms import frame_transform, matrix_frame_transform
from .assessment import (
    AssessSingleDialog,
    AssessmentPanel,
    PairDialog,
    RegisterCaseDialog,
    ReopenSnapshotDialog,
)
from .details import DetailsPanel
from .jobs import ACTIVE_STATES, JobManager
from .plot import TimeSeriesPlot
from .projectmodel import ProjectTreeModel
from .theme import apply_theme

CLOSE_CANCEL_WAIT_MS = 8000
REPLAY_TICK_MS = 100


class MainWindow(QMainWindow):
    """Application shell: viewer, project tree, details, jobs, replay."""

    def __init__(self, parent: Optional[QWidget] = None, *, viewer_backend: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("ANYmarine")
        self._service = ApplicationService()
        self._project: Optional[ProjectModel] = None
        self._project_root: Optional[Path] = None
        self._dirty = False
        self._handles: dict[str, object] = {}
        self._current_result: Optional[ResultRecord] = None
        self._current_frame = 0
        self._result_status = "no result"
        # Detached snapshot replay context (never the current project).
        self._replay_project: Optional[ProjectModel] = None
        self._replay_members: Optional[dict] = None
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_pending = False
        self._fit_timer.timeout.connect(self._apply_pending_fit)

        apply_theme(self)
        backend = (viewer_backend or os.environ.get("ANYMARINE_VIEWER_BACKEND") or "auto").casefold()
        if backend not in ('software','auto','gpu'):
            raise ValueError('viewer backend must be software, auto or gpu')
        self._requested_viewer_backend = backend
        # ANY3dView probes a real OpenGL context in its constructor. Create it
        # only once the top-level host has an exposed native window; probing a
        # hidden shell can produce a black composed framebuffer on Windows.
        self._startup_renderer_pending=backend!='software'
        self._startup_window=None
        self._startup_renderer_timer=QTimer(self)
        self._startup_renderer_timer.setSingleShot(True)
        self._startup_renderer_timer.timeout.connect(self._start_visible_renderer)
        initial_backend='software' if self._startup_renderer_pending else backend
        self.viewer = create_viewer(self, initial_backend, host=QtViewerHostAdapter())
        self.viewer.setMinimumSize(480, 300)
        self.viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCentralWidget(self.viewer)
        from .member_scene import MemberScene
        self._member_scene = MemberScene(self.viewer)
        # Project loads reuse the viewer; only an explicit renderer change replaces it.
        self.viewer.installEventFilter(self)
        self.viewer.render_error.connect(self._on_render_error)
        self.viewer.backend_changed.connect(self._on_backend_changed)

        self._tree_model = ProjectTreeModel(self._service, self)
        self._build_docks()
        # Stable names let Qt preserve dock geometry during a renderer exchange.
        for dock in self.findChildren(QDockWidget):
            if not dock.objectName():
                dock.setObjectName(f'anymarine-dock-{dock.windowTitle()}')
        self._job_manager = JobManager(self._service, self)
        from .waves import WavesPanel
        self._waves = WavesPanel(self)
        wave_dock = QDockWidget('Waves', self)
        wave_dock.setObjectName('anymarine-dock-Waves')
        wave_dock.setMinimumWidth(240)
        wave_dock.setWidget(self._scroll_dock_content(self._waves))
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, wave_dock)
        assessment_dock = next(d for d in self.findChildren(QDockWidget) if d.windowTitle() == 'Assessment')
        self.tabifyDockWidget(assessment_dock, wave_dock)
        wave_dock.raise_()
        self._wave_dock = wave_dock
        from .rao_panel import RaoPanel
        self._rao = RaoPanel(self)
        rao_dock = QDockWidget('RAO', self)
        rao_dock.setObjectName('anymarine-dock-RAO')
        rao_dock.setMinimumWidth(240)
        rao_dock.setWidget(self._scroll_dock_content(self._rao))
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, rao_dock)
        self.tabifyDockWidget(assessment_dock, rao_dock)
        wave_dock.raise_()
        self._rao_dock = rao_dock
        self._build_menus()
        self._viewer_backend_label = QLabel()
        self._viewer_backend_label.setToolTip('Change renderer under View > Renderer')
        self.statusBar().addPermanentWidget(self._viewer_backend_label)
        self._development_status_label = QLabel()
        self._development_status_label.setToolTip('Details under Help > Development status')
        self.statusBar().addPermanentWidget(self._development_status_label)
        self._refresh_startup_development_status()
        self._log_viewer_backend()

        self._job_manager.state_changed.connect(self._on_job_state)
        self._job_manager.progress_changed.connect(self._on_job_progress)
        self._job_manager.result_ready.connect(self._on_job_result)
        self._job_manager.adoption_ready.connect(self._on_adoption_ready)
        self._job_manager.hams_ready.connect(self._on_hams_ready)
        self._job_manager.job_failed.connect(self._on_job_failed)
        self._job_manager.assessment_ready.connect(self._on_assessment_ready)

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(REPLAY_TICK_MS)
        self._play_timer.timeout.connect(self._advance_frame)

        self.resize(1280, 720)
        self.statusBar().showMessage("Development preview — results are unqualified")

    def showEvent(self,event):  # noqa: N802
        super().showEvent(event)
        if self._startup_renderer_pending:
            self._watch_startup_window();self._startup_renderer_timer.start(0)

    def hideEvent(self, event):  # noqa: N802
        self._startup_renderer_timer.stop()
        super().hideEvent(event)

    def _watch_startup_window(self):
        native=self.windowHandle()
        if native is not None and native is not self._startup_window:
            self._startup_window=native;native.installEventFilter(self)
        return native

    def _start_visible_renderer(self):
        if not self._startup_renderer_pending or not self.isVisible():return
        native=self._watch_startup_window()
        if native is None or not native.isExposed():return
        backend=self._requested_viewer_backend
        self._startup_renderer_pending=False
        self._change_renderer(backend)
        if self._handles:self._schedule_fit()

    # ------------------------------------------------------------- dialogs
    # Thin wrappers so tests (offscreen) can replace modal dialogs.

    def _notify_warning(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _notify_critical(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)

    def _notify_info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def _ask_open_file(self, title: str, name_filter: str) -> str:
        """Thin wrapper so offscreen tests can replace the modal dialog."""
        path, _ = QFileDialog.getOpenFileName(self, title, "", name_filter)
        return str(path)

    # ------------------------------------------------------------------ docks

    @staticmethod
    def _scroll_dock_content(widget: QWidget) -> QScrollArea:
        """Scrollable dock content so panels stay usable in small windows."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumSize(160, 100)
        scroll.setWidget(widget)
        return scroll

    def _build_docks(self) -> None:
        # project tree
        tree_dock = QDockWidget("Project", self)
        tree_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        tree = QTreeView()
        tree.setModel(self._tree_model)
        tree.clicked.connect(self._on_tree_clicked)
        tree_dock.setWidget(tree)
        tree_dock.setMinimumWidth(180)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, tree_dock)
        self._tree = tree

        # details
        details_dock = QDockWidget("Details", self)
        details_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self._details = DetailsPanel(self._service)
        self._details.body_edited.connect(self._apply_body_edit)
        self._details.equilibrium_edited.connect(self._apply_equilibrium_edit)
        self._details.settings_edited.connect(self._apply_settings_edit)
        self._details.surface_edited.connect(self._apply_surface_edit)
        self._details.surface_remove_requested.connect(self._apply_surface_remove)
        self._details.surface_create_requested.connect(self._apply_surface_create)
        self._details.surface_create_failed.connect(
            lambda message: self._notify_warning("Surface creation refused", message))
        details_dock.setWidget(self._details)
        details_dock.setMinimumWidth(240)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, details_dock)

        # jobs + diagnostics
        jobs_dock = QDockWidget("Jobs / Diagnostics", self)
        jobs_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        jobs_host = QWidget()
        jobs_layout = QVBoxLayout(jobs_host)
        self._jobs_table = QTableWidget(0, 3)
        self._jobs_table.setHorizontalHeaderLabels(["Job", "State", "Progress"])
        self._jobs_table.horizontalHeader().setStretchLastSection(True)
        self._diagnostics = QPlainTextEdit()
        self._diagnostics.setReadOnly(True)
        self._diagnostics.setMaximumHeight(140)
        jobs_layout.addWidget(self._jobs_table)
        jobs_layout.addWidget(QLabel("Diagnostics"))
        jobs_layout.addWidget(self._diagnostics)
        jobs_dock.setWidget(self._scroll_dock_content(jobs_host))
        jobs_dock.setMinimumHeight(110)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, jobs_dock)

        # replay
        replay_dock = QDockWidget("Replay", self)
        replay_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        replay_host = QWidget()
        replay_layout = QVBoxLayout(replay_host)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        self._play_button = QPushButton("Play")
        self._play_button.clicked.connect(self._toggle_play)
        self._frame_slider = QSlider(Qt.Orientation.Horizontal)
        self._frame_slider.setRange(0, 0)
        self._frame_slider.valueChanged.connect(self._on_slider_moved)
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumContentsLength(16)
        self._channel_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._channel_combo.currentTextChanged.connect(self._on_channel_changed)
        self._frame_label = QLabel("no result")
        self._frame_label.setWordWrap(True)
        self._frame_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        controls_layout.addWidget(self._play_button)
        controls_layout.addWidget(self._frame_slider)
        controls_layout.addWidget(self._channel_combo)
        controls_layout.addWidget(self._frame_label)
        self._plot = TimeSeriesPlot()
        replay_layout.addWidget(controls)
        replay_layout.addWidget(self._plot)
        replay_dock.setWidget(self._scroll_dock_content(replay_host))
        replay_dock.setMinimumHeight(110)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, replay_dock)

        # assessment evidence (child-validated payloads; display only)
        assessment_dock = QDockWidget("Assessment", self)
        assessment_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self._assessment_panel = AssessmentPanel()
        self._assessment_panel.register_case_requested.connect(self._register_case_dialog)
        self._assessment_panel.assess_requested.connect(self._assess_dialog)
        self._assessment_panel.pair_requested.connect(self._pair_dialog)
        self._assessment_panel.reopen_requested.connect(self._reopen_dialog)
        self._assessment_panel.replay_member_changed.connect(self._select_replay_member)
        assessment_dock.setWidget(self._scroll_dock_content(self._assessment_panel))
        assessment_dock.setMinimumWidth(240)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, assessment_dock)
        # Responsive workbench: bottom docks share one strip; side docks keep
        # readable minimums and the model canvas takes the remaining space.
        self.resizeDocks([jobs_dock, replay_dock], [200, 200], Qt.Orientation.Vertical)
        self.resizeDocks([tree_dock, details_dock], [200, 300], Qt.Orientation.Horizontal)
        self.resizeDocks([assessment_dock], [280], Qt.Orientation.Horizontal)

    def _build_menus(self) -> None:
        self._menu_bar = self.menuBar()
        file_menu = self._menu_bar.addMenu("&File")
        new_action = QAction("&New project", self)
        new_action.triggered.connect(self._new_project)
        open_action = QAction("&Open project...", self)
        open_action.triggered.connect(self._open_project)
        save_action = QAction("&Save project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(new_action)
        file_menu.addAction(open_action)
        example_action = QAction("Load &example (two-body replay)", self)
        example_action.triggered.connect(self.load_example)
        file_menu.addAction(example_action)
        regular_wave_action = QAction('Load moored bodies in a regular wave (solved replay)', self)
        regular_wave_action.triggered.connect(self.load_regular_wave_example)
        file_menu.addAction(regular_wave_action)
        native_example_action = QAction('Load native dry two-body example', self)
        native_example_action.triggered.connect(self.load_native_example)
        file_menu.addAction(native_example_action)
        plates_action = QAction('Load attached-plate geometry example', self)
        plates_action.triggered.connect(self.load_plate_example)
        file_menu.addAction(plates_action)
        mooring_example_action=QAction('Load native mooring example (three lines)',self)
        mooring_example_action.triggered.connect(self.load_native_mooring_example)
        file_menu.addAction(mooring_example_action)
        fluid_example_action = QAction('Load native coupled fluid example',self)
        fluid_example_action.triggered.connect(self.load_native_fluid_example)
        file_menu.addAction(fluid_example_action)
        sea_example_action=QAction('Load coupled seeded-wave example',self)
        sea_example_action.triggered.connect(self.load_native_sea_state_example)
        file_menu.addAction(sea_example_action)
        calculated_wave_action=QAction('Load calculated wave preview example',self)
        calculated_wave_action.triggered.connect(self.load_calculated_wave_example)
        file_menu.addAction(calculated_wave_action)
        hams_wave_action=QAction('Load stored HAMS wave samples',self)
        hams_wave_action.triggered.connect(lambda:self.load_hams_wave_bundle())
        file_menu.addAction(hams_wave_action)
        open_hams_wave_action=QAction('Open stored HAMS wave bundle…',self)
        open_hams_wave_action.triggered.connect(self.open_hams_wave_bundle)
        file_menu.addAction(open_hams_wave_action)
        morison_example_action = QAction('Load native wave/current Morison example', self)
        morison_example_action.triggered.connect(self.load_native_morison_example)
        file_menu.addAction(morison_example_action)
        line_hydro_action=QAction('Load native wave/current mooring example',self)
        line_hydro_action.triggered.connect(self.load_native_line_hydro_example)
        file_menu.addAction(line_hydro_action)
        contact_action=QAction('Load native seabed contact example',self)
        contact_action.triggered.connect(self.load_native_contact_example)
        file_menu.addAction(contact_action)
        static_example_action=QAction('Load coupled static equilibrium example',self)
        static_example_action.triggered.connect(self.load_equilibrium_example)
        file_menu.addAction(static_example_action)
        file_menu.addAction(save_action)

        jobs_menu = self._menu_bar.addMenu("&Jobs")
        run_action = QAction("&Run synthetic job", self)
        run_action.triggered.connect(self._run_job)
        run_linear_action = QAction(
            "Run &linear development job", self
        )
        run_linear_action.triggered.connect(self._run_linear_job)
        cancel_action = QAction("Cancel &all jobs", self)
        cancel_action.triggered.connect(self._job_manager.cancel_all)
        import_hydro_action = QAction(
            "Import &hydrodynamic inputs...", self
        )
        import_hydro_action.triggered.connect(self._import_hydro_inputs)
        import_derived_action = QAction(
            "Import derived hydrodynamic &inputs...", self
        )
        import_derived_action.triggered.connect(self._import_derived_hydro_inputs)
        import_native_action = QAction('Import native mechanical inputs...', self)
        import_native_action.triggered.connect(self._import_native_inputs)
        edit_native_action = QAction('Edit native mechanical inputs...', self)
        edit_native_action.triggered.connect(self._edit_native_inputs)
        run_native_action = QAction('Run native mechanical job...', self)
        run_native_action.triggered.connect(self._run_native_job)
        jobs_menu.addAction(import_native_action)
        jobs_menu.addAction(edit_native_action)
        edit_sea_action=QAction('Edit shared sea state...',self)
        edit_sea_action.triggered.connect(self._edit_shared_sea_state)
        jobs_menu.addAction(edit_sea_action)
        jobs_menu.addAction(run_native_action)
        run_hams_action = QAction('Run configured HAMS fields…', self)
        run_hams_action.triggered.connect(self._run_hams_job)
        jobs_menu.addAction(run_hams_action)
        static_action=QAction('Run coupled static equilibrium job',self)
        static_action.triggered.connect(self._run_equilibrium_job)
        jobs_menu.addAction(static_action)
        adoption_action=QAction('Adopt selected static equilibrium…',self)
        adoption_action.triggered.connect(self._adopt_equilibrium_dialog)
        jobs_menu.addAction(adoption_action)
        jobs_menu.addSeparator()
        jobs_menu.addAction(run_action)
        jobs_menu.addAction(run_linear_action)
        jobs_menu.addAction(import_hydro_action)
        jobs_menu.addAction(import_derived_action)
        jobs_menu.addAction(cancel_action)

        assessment_menu = self._menu_bar.addMenu("&Assessment")
        register_case_action = QAction("&Register case...", self)
        register_case_action.triggered.connect(self._register_case_dialog)
        assess_action = QAction("&Assess selected result...", self)
        assess_action.triggered.connect(self._assess_dialog)
        pair_action = QAction("Assess coarse/&fine pair...", self)
        pair_action.triggered.connect(self._pair_dialog)
        reopen_action = QAction("&Reopen assessment snapshot...", self)
        reopen_action.triggered.connect(self._reopen_dialog)
        assessment_menu.addAction(register_case_action)
        assessment_menu.addAction(assess_action)
        assessment_menu.addAction(pair_action)
        assessment_menu.addAction(reopen_action)

        view_menu = self._menu_bar.addMenu("&View")
        fit_action = QAction("&Fit model", self)
        fit_action.setShortcut("Ctrl+F")
        fit_action.triggered.connect(self._fit_model)
        view_menu.addAction(fit_action)
        self._fit_action = fit_action
        renderer_menu = view_menu.addMenu('Renderer')
        self._renderer_group = QActionGroup(self)
        self._renderer_group.setExclusive(True)
        self._renderer_actions = {}
        for mode, label in (('software', 'Software (compatible)'),
                            ('auto', 'Automatic (OpenGL with fallback)'),
                            ('gpu', 'OpenGL')):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(mode == self._requested_viewer_backend)
            self._renderer_group.addAction(action)
            renderer_menu.addAction(action)
            action.triggered.connect(lambda checked=False, mode=mode: self._change_renderer(mode))
            self._renderer_actions[mode] = action
        self._renderer_menu = renderer_menu

        help_menu = self._menu_bar.addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)
        loading = QAction('Loading models...', self)
        loading.triggered.connect(self._show_loading_help)
        help_menu.addAction(loading)
        development_status_action = QAction('Development status...', self)
        development_status_action.triggered.connect(self._show_development_status)
        help_menu.addAction(development_status_action)
        # Keep Python wrappers alive alongside their Qt menu-bar ownership.
        self._menus = (file_menu, jobs_menu, assessment_menu, view_menu, help_menu)
        # QAction.menu() ties wrapper lifetime to its action in this runtime.
        self._menu_actions = tuple(menu.menuAction() for menu in self._menus)

    # ------------------------------------------------------------ project I/O

    def load_project(self, root: str) -> None:
        try:
            project = self._service.load(root)
        except (ProjectError, ServiceError) as error:
            self._notify_critical("Load failed", str(error))
            return
        self._project_root = self._service.root
        self._set_project(project)
        self.setWindowTitle(f"ANYmarine — {project.name}")

    def load_calculated_wave_example(self) -> None:
        if not self._confirm_discard():return
        try:project,field=self._service.load_calculated_wave_example()
        except ServiceError as error:
            self._notify_warning('Calculated wave example',str(error));return
        self._project_root=None;self._set_project(project)
        try:
            from anyhydro.wave_preview import request_hash
            r=field['request']
            for control,key in ((self._waves.amplitude,'amplitude'),(self._waves.omega,'omega'),
                                (self._waves.heading,'heading_degrees'),(self._waves.depth,'water_depth')):
                control.setValue(r[key])
            self._waves.time=0.
            if request_hash(self._waves.request())!=field['request_hash']:
                raise ValueError('stored wave field captured geometry differs from the display')
            self._waves.field=field;self._waves.mode.model().item(2).setEnabled(True)
            self._waves.mode.setCurrentIndex(2)
        except (ImportError,ValueError) as error:
            self._waves.reset();self._notify_warning('Calculated wave example',str(error));return
        self.setWindowTitle('ANYmarine — calculated wave preview (unsaved, unqualified)')
        self.statusBar().showMessage('Stored incident + diffraction waves; use Waves to switch disturbance off/on')
        self._diagnostics.appendPlainText('Loaded sealed Capytaine 3.0.0 fixed-body diffraction: 28 panels, 801 surface nodes. No solve while loading; visualization development evidence only.')

    def open_hams_wave_bundle(self):
        directory=QFileDialog.getExistingDirectory(self,'Open stored HAMS wave bundle')
        if directory:self.load_hams_wave_bundle(directory)

    def load_hams_wave_bundle(self,directory=None):
        if not self._confirm_discard():return
        try:project,record=self._service.load_hams_wave_bundle(directory)
        except ServiceError as error:
            self._notify_warning('Stored HAMS waves',str(error));return
        self._project_root=None;self._set_project(project)
        try:self._waves.load_stored_hams(record)
        except (ImportError,ValueError) as error:
            self._waves.reset();self._notify_warning('Stored HAMS waves',str(error));return
        self.setWindowTitle('ANYmarine — stored HAMS wave samples (unsaved, unqualified)')
        self._wave_dock.raise_()
        self.statusBar().showMessage('Stored HAMS samples — Waves selects incident, total, scattered or prescribed-motion radiation')
        self._diagnostics.appendPlainText('Loaded saved source-bound HAMS point fields. No calculation. Surface samples have true elevation; submerged dynamic pressure is retained separately. Development, unqualified.')

    def load_native_sea_state_example(self) -> None:
        if not self._confirm_discard():return
        self._project_root=None
        self._set_project(self._service.load_native_sea_state_example())
        self.setWindowTitle('ANYmarine — coupled seeded waves (unsaved, unqualified)')
        self.statusBar().showMessage('Jobs → Edit shared sea state / Run native mechanical job; Waves → Mechanical incident waves')
        self._diagnostics.appendPlainText('Saved seeded waves drive synthetic excitation and Morison members. No solve while loading; development fixture, unqualified.')

    def _edit_shared_sea_state(self) -> None:
        if self._project is None or self._project.native_inputs is None or self._project.native_inputs.wave_realization is None:
            self._notify_info('Shared sea state','Load the coupled seeded-wave development example first.')
            return
        from .sea_state_dialog import SeaStateDialog
        try:dialog=SeaStateDialog(self._project,self)
        except ValueError as error:
            self._notify_warning('Sea state refused',str(error));return
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:project=self._service.replace_native_sea_state(self._project,**dialog.parameters())
        except (ValueError,ServiceError) as error:
            self._notify_warning('Sea state refused',str(error));return
        self._mark_edited(project)
        self._diagnostics.appendPlainText('New shared realization saved; excitation resynthesized and incident Morison components replaced. Existing runs retain their producing inputs.')

    def load_native_morison_example(self) -> None:
        if not self._confirm_discard():
            return
        self._project_root = None
        self._set_project(self._service.load_native_morison_example())
        self.setWindowTitle('ANYmarine — wave/current Morison example (unsaved, unqualified)')
        self.statusBar().showMessage('Member centrelines loaded; Jobs → Edit native mechanical inputs / Run native mechanical job')
        self._diagnostics.appendPlainText('Declared Airy waves/current and straight Morison members. No solve while loading. Waves tab is a separate preview, not the mechanical environment.')

    def load_equilibrium_example(self):
        if not self._confirm_discard():return
        project=self._service.load_equilibrium_example()
        self._project_root=None;self._set_project(project)
        self.statusBar().showMessage('Static example loaded; Jobs → Run coupled static equilibrium job')

    def load_native_contact_example(self) -> None:
        if not self._confirm_discard():return
        self._project_root=None
        self._set_project(self._service.load_native_contact_example())
        self.setWindowTitle('ANYmarine — compliant seabed contact example (unsaved, unqualified)')
        self.statusBar().showMessage('Three lines and seabed loaded; Jobs → Run native mechanical job')
        self._diagnostics.appendPlainText('Compliant frictionless contact, explicit initial penetration31.25 mm and zero gravity. Three development steps; equilibrium/friction/W6 incomplete. Red crosses replay stored active endpoints after a job.')

    def load_native_line_hydro_example(self) -> None:
        if not self._confirm_discard():return
        self._project_root=None
        self._set_project(self._service.load_native_line_hydro_example())
        self.setWindowTitle('ANYmarine — wave/current mooring example (unsaved, unqualified)')
        self.statusBar().showMessage('Two members and three lines loaded; Jobs → Run native mechanical job')
        self._diagnostics.appendPlainText('Members and lines share saved wave/current inputs. Three development steps; contact/equilibrium/W6 incomplete. No solve while loading.')
        self._waves.mode.setCurrentIndex(3)

    def load_native_mooring_example(self) -> None:
        if not self._confirm_discard():return
        self._project_root=None
        self._set_project(self._service.load_native_mooring_example())
        self.setWindowTitle('ANYmarine — mooring example (unsaved, unqualified)')
        self.statusBar().showMessage('Three anchored lines loaded; Jobs → Run native mechanical job')
        self._diagnostics.appendPlainText('Two bodies and three dynamic mooring nodes. Three development steps; no line hydrodynamics/contact qualification. No solve while loading.')

    def load_native_fluid_example(self) -> None:
        if not self._confirm_discard():
            return
        self._project_root = None
        self._set_project(self._service.load_native_fluid_example())
        self.setWindowTitle('ANYmarine — coupled fluid example (unsaved, unqualified)')
        self.statusBar().showMessage('Development fluid inputs loaded; Jobs → Run native mechanical job starts integration')
        self._diagnostics.appendPlainText('Synthetic coupled operators and axial link; no numerical run while loading. Development fixture, unqualified.')

    def load_plate_example(self) -> None:
        if not self._confirm_discard():
            return
        try:
            project = self._service.load_plate_example()
        except (ValueError, ImportError) as error:
            self._notify_warning('Attached plate example', str(error))
            return
        self._set_project(project)
        self._project_root = None
        self._dirty = True
        self.setWindowTitle('ANYmarine — attached plates (geometry preview, unsaved)')
        self.statusBar().showMessage('Two rigid bodies with submerged plates; geometry preview. Native sheet hydrodynamics are unsupported.')
        self._diagnostics.appendPlainText('Attached plates share owner motion. No numerical coefficients, added mass, fictitious displacement or qualification implied.')

    def load_native_example(self) -> None:
        if not self._confirm_discard():
            return
        self._project_root = None
        self._set_project(self._service.load_native_example())
        self.setWindowTitle('ANYmarine — native dry example (unsaved, unqualified)')
        self.statusBar().showMessage('Native dry/link inputs loaded; Jobs → Run native mechanical job starts integration')
        self._diagnostics.appendPlainText('Declared uniform cubes and axial spring; no solver run while loading. Development example, unqualified.')

    def load_example(self) -> None:
        """Open the bundled stored replay without changing package files."""
        if not self._confirm_discard():
            return
        try:
            project = self._service.load_example()
        except (ProjectError, ServiceError, OSError) as error:
            self._notify_critical("Example load failed", str(error))
            return
        self._project_root = None
        self._set_project(project)
        self._set_result(project.results[0])
        self.setWindowTitle("ANYmarine — Two-body example (unsaved copy)")
        self.statusBar().showMessage("Synthetic example — press Play to replay; Save creates your own copy")
        self._diagnostics.appendPlainText(
            "Loaded bundled two-body example with stored synthetic replay. "
            "Not BEM output or dynamics integration; unqualified demonstration."
        )

    def load_regular_wave_example(self) -> None:
        """Open stored bodies solved in a regular wave; waves follow the replay."""
        if not self._confirm_discard():
            return
        try:
            project = self._service.load_regular_wave_example()
        except (ProjectError, ServiceError, OSError) as error:
            self._notify_critical("Example load failed", str(error))
            return
        self._project_root = None
        self._set_project(project)
        self._set_result(project.results[0])
        self._wave_dock.raise_()
        self.setWindowTitle("ANYmarine — moored bodies in a regular wave (unsaved copy)")
        self.statusBar().showMessage("Stored solved replay — press Play: bodies and their forcing wave move together")
        self._diagnostics.appendPlainText(
            "Loaded two moored cubes solved natively in one regular Airy wave (0.3 m, 2 rad/s), "
            "Morison members and dynamic lines, 10 s. The Waves panel shows that saved wave at the "
            "replay time. No solve while loading; development, unqualified."
        )

    def _set_project(self, project: ProjectModel) -> None:
        self._waves.reset()
        self._project = project
        if getattr(self, '_rao', None) is not None:
            self._rao.project_changed()
        self._dirty = False
        self._current_result = None
        self._handles = {}
        self._replay_project = None
        self._replay_members = None
        self.viewer.clear(keep_canvas=True)
        self._sync_viewer_bodies()
        self._tree_model.set_project(project)
        self._tree.expandAll()
        self._details.set_project(project)
        self._set_result(None)
        self._schedule_fit()
        self._refresh_assessment_detachment()

    def _sync_viewer_bodies(self, project: Optional[ProjectModel] = None, *, line_nodes=None) -> None:
        """Add each body mesh once; replay reuses handles via set_transform."""

        source = self._project if project is None else project
        if source is None:
            return
        for body in source.bodies:
            if body.body_id in self._handles:
                continue
            self._handles[body.body_id] = self._add_viewer_body(self.viewer, body, source)
        self._member_scene.sync(source, self._handles,line_nodes=line_nodes)
        if self._handles or self._member_scene.line_handles:
            self._schedule_fit()

    @staticmethod
    def _add_viewer_body(viewer, body, source):
        from ..surface_assignments import display_body_mesh
        display_mesh = display_body_mesh(body)
        mesh = MeshArrays(
            positions=np.asarray(display_mesh.positions, dtype=np.float64),
            triangles=np.asarray(display_mesh.triangles, dtype=np.uint32),
        )
        attached = any(s.role == 'attached' for s in body.hydro_surfaces)
        handle = viewer.add_mesh_arrays(mesh, tags=body.body_id, color=body.color,
                                        cull_backface=not attached, two_sided_shell=attached)
        transform = np.eye(4)
        transform[:3, :3] = body.reference_orientation
        transform[:3, 3] = body.reference_position
        if source.native_inputs is not None:
            initial = source.native_inputs.initial_states[body.body_id]
            transform = matrix_frame_transform(initial['position'], initial['rotation'])
        handle.set_transform(transform)
        return handle

    def _change_renderer(self, backend: str) -> None:
        """Replace only the public ANY3dView host, preserving scene and replay.

        Prepare a complete candidate before exchanging the central widget.
        A failed backend selection leaves the current viewer and model intact.
        """
        self._startup_renderer_pending=False
        self._startup_renderer_timer.stop()
        old = self.viewer
        candidate = None
        try:
            layout_state = self.saveState()
            horizontal_docks = [dock for dock in self.findChildren(QDockWidget)
                if self.dockWidgetArea(dock) in (Qt.DockWidgetArea.LeftDockWidgetArea,
                                                Qt.DockWidgetArea.RightDockWidgetArea)]
            dock_widths = [dock.size().width() for dock in horizontal_docks]
            state = old.export_view_state()
            highlights = old.highlighted_tags()
            candidate = create_viewer(self, backend, host=QtViewerHostAdapter())
            candidate.resize(old.size())
            candidate.setMinimumSize(480, 300)
            candidate.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            handles = {}
            source = self._replay_project or self._project
            if source is not None:
                for body in source.bodies:
                    handle = self._add_viewer_body(candidate, body, source)
                    previous = self._handles.get(body.body_id)
                    if previous is not None:
                        handle.set_transform(np.array(previous.transform, copy=True))
                    handles[body.body_id] = handle
            from .member_scene import MemberScene
            member_scene = MemberScene(candidate)
            member_scene.sync(source, handles,line_nodes=(self._current_result.line_nodes[self._current_frame] if self._current_result is not None and self._current_result.kind=='line_equilibrium_development' else None))
            if self._current_result is not None and self._current_result.line_nodes is not None:
                index=min(max(self._current_frame,0),self._current_result.frame_count()-1)
                member_scene.update(handles,self._current_result.line_nodes[index],result=self._current_result,index=index)
            candidate.apply_view_state(state)
            candidate.set_highlight(highlights)
        except Exception as error:
            if candidate is not None:
                candidate.destroy()
            self._renderer_actions[self._requested_viewer_backend].setChecked(True)
            self._diagnostics.appendPlainText(f'Renderer selection failed ({backend}): {error}')
            self._notify_warning('Renderer unavailable',
                f'ANY3dView could not start {backend}: {error}. The current view is retained. '
                'Choose View > Renderer > Software (compatible).')
            return
        self._fit_timer.stop()
        self._fit_pending = False
        old.removeEventFilter(self)
        old.render_error.disconnect(self._on_render_error)
        old.backend_changed.disconnect(self._on_backend_changed)
        self.takeCentralWidget()
        self.viewer = candidate
        self._handles = handles
        self._member_scene = member_scene
        self._requested_viewer_backend = backend
        candidate.installEventFilter(self)
        candidate.render_error.connect(self._on_render_error)
        candidate.backend_changed.connect(self._on_backend_changed)
        self.setCentralWidget(candidate)
        self.restoreState(layout_state)
        # Qt redistributes spare central space after replacing the widget.
        # Reapply the existing side-panel widths after that layout pass.
        def restore_dock_widths():
            if self.viewer is candidate:
                self.resizeDocks(horizontal_docks, dock_widths, Qt.Orientation.Horizontal)
        restore_dock_widths()
        QTimer.singleShot(0, restore_dock_widths)
        self._renderer_actions[backend].setChecked(True)
        old.destroy()
        self._waves.renderer_changed()
        candidate.redraw()
        self._log_viewer_backend()

    # -------------------------------------------------------- viewport fit

    def _schedule_fit(self) -> None:
        """Fit once the viewport is visible with its final laid-out size."""

        self._fit_pending = True
        self._fit_timer.start(0)

    def _apply_pending_fit(self) -> None:
        if not self._fit_pending:
            return
        viewer = self.viewer
        size = viewer.size()
        if not viewer.isVisible() or size.width() <= 1 or size.height() <= 1:
            return  # not laid out yet; the Show/Resize filter re-arms the timer
        self._fit_pending = False
        viewer.fit_to_scene()

    def _fit_model(self) -> None:
        """User-triggered immediate fit (View > Fit model)."""

        self._fit_pending = False
        self.viewer.fit_to_scene()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (self._startup_renderer_pending and self.isVisible()
                and watched is self._startup_window
                and event.type()==QEvent.Type.Expose):
            self._startup_renderer_timer.start(0)
        if watched is self.viewer and self._fit_pending:
            if event.type() in (
                QEvent.Type.Show,
                QEvent.Type.ShowToParent,
                QEvent.Type.Resize,
            ):
                self._fit_timer.start(0)
        return super().eventFilter(watched, event)

    # -------------------------------------------------- renderer diagnostics

    def _log_viewer_backend(self) -> None:
        name = self.viewer.backend_name
        notes = tuple(self.viewer.backend_diagnostics)
        self._viewer_backend_label.setText(f"ANY3dView: {name}")
        message = f"viewer backend: {name}"
        if notes:
            message += f" ({'; '.join(notes)})"
        self._diagnostics.appendPlainText(message)

    def _on_backend_changed(self, name: str) -> None:
        self._viewer_backend_label.setText(f"ANY3dView: {name}")
        self._diagnostics.appendPlainText(f"viewer backend changed to {name}")
        self.statusBar().showMessage(f"Viewer backend: {name}")
        # A fallback replaces the rendering surface. Fit after its layout,
        # using the retained handles rather than rebuilding the project.
        if self._handles:
            self._schedule_fit()

    def _on_render_error(self, message: str) -> None:
        self._diagnostics.appendPlainText(f"RENDER ERROR: {message}")
        # The wheel falls back to software after emitting render_error when
        # the backend is auto; check the settled backend before alarming.
        QTimer.singleShot(0, self._report_render_error)

    def _report_render_error(self) -> None:
        if not self.isVisible() or self.viewer.backend_name == "software":
            return  # hidden shell or software fallback rendering (logged above)
        notes = tuple(self.viewer.backend_diagnostics)
        detail = notes[-1] if notes else "unknown renderer error"
        self._notify_critical(
            "Renderer failure",
            f"The 3D viewer failed to render and cannot show the model "
            f"(backend {self.viewer.backend_name}): {detail}. "
            "Choose View > Renderer > Software (compatible) to recover the view.",
        )

    # ------------------------------------------------------------------ edits

    def _apply_body_edit(self, body_id: str, changes: dict) -> None:
        if self._project is None:
            return
        body = self._project.body(body_id)
        if body is None:
            return
        try:
            new_body = body.with_edits(
                name=changes.get("name", body.name),
                reference_position=changes.get("reference_position", body.reference_position),
                mass=changes.get("mass", body.mass),
                color=changes.get("color", body.color),
                mass_properties=changes.get("mass_properties", body.mass_properties),
            )
            project = self._project.with_edits(
                bodies=tuple(
                    new_body if item.body_id == body_id else item for item in self._project.bodies
                )
            )
        except ValueError as error:
            self._notify_warning("Invalid value", str(error))
            self._details.show_body(body_id)
            return
        self._mark_edited(project)

    def _apply_equilibrium_edit(self, changes: dict) -> None:
        if self._project is None:
            return
        equilibrium = self._project.equilibrium
        try:
            new_equilibrium = equilibrium.with_edits(
                waterline_z=changes.get("waterline_z", equilibrium.waterline_z),
                description=changes.get("description", equilibrium.description),
            )
            project = self._project.with_edits(equilibrium=new_equilibrium)
        except ValueError as error:
            self._notify_warning("Invalid value", str(error))
            self._details.show_equilibrium()
            return
        self._mark_edited(project)

    def _apply_settings_edit(self, changes: dict) -> None:
        if self._project is None:
            return
        settings = self._project.settings
        try:
            new_settings = settings.with_edits(
                duration=changes.get("duration", settings.duration),
                dt=changes.get("dt", settings.dt),
                wave_amplitude=changes.get("wave_amplitude", settings.wave_amplitude),
                wave_period=changes.get("wave_period", settings.wave_period),
            )
            project = self._project.with_edits(settings=new_settings)
        except ValueError as error:
            self._notify_warning("Invalid value", str(error))
            self._details.show_settings()
            return
        self._mark_edited(project)

    def _apply_surface_edit(self, body_id: str, surface_id: str, changes: dict) -> None:
        """Atomic surface edit through public frozen records.

        Identity (surface id, owner), role and mesh are locked; the public
        constructors revalidate every field combination and a refusal keeps
        the current project untouched (same input hash).
        """
        if self._project is None:
            return
        body = self._project.body(body_id)
        if body is None:
            return
        assignment = next((s for s in body.hydro_surfaces
                           if s.semantics.surface_id == surface_id), None)
        if assignment is None:
            return
        try:
            from .surface_details import edited_assignment
            new_assignment = edited_assignment(assignment, changes)
            new_body = body.with_edits(hydro_surfaces=tuple(
                new_assignment if s.semantics.surface_id == surface_id else s
                for s in body.hydro_surfaces))
            project = self._project.with_edits(bodies=tuple(
                new_body if item.body_id == body_id else item
                for item in self._project.bodies))
        except ValueError as error:
            self._notify_warning("Surface edit refused", str(error))
            self._details.show_surface(body_id, surface_id)
            return
        self._mark_edited(project)
        self._details.show_surface(body_id, surface_id)

    def _apply_surface_remove(self, body_id: str, surface_id: str) -> None:
        """Explicit removal of one owned assignment; no reclassification."""
        if self._project is None:
            return
        body = self._project.body(body_id)
        if body is None:
            return
        if not any(s.semantics.surface_id == surface_id for s in body.hydro_surfaces):
            return
        try:
            new_body = body.with_edits(hydro_surfaces=tuple(
                s for s in body.hydro_surfaces
                if s.semantics.surface_id != surface_id))
            project = self._project.with_edits(bodies=tuple(
                new_body if item.body_id == body_id else item
                for item in self._project.bodies))
        except ValueError as error:
            self._notify_warning("Surface removal refused", str(error))
            return
        self._mark_edited(project)
        self._details.show_body(body_id)

    def _apply_surface_create(self, body_id: str, spec: dict) -> None:
        """Create one owned assignment with an explicit role/kind/geometry.

        The physical kind follows the explicit role; public validation
        admits or refuses the combination. Surface ids are unique across
        the whole project.
        """
        if self._project is None:
            return
        body = self._project.body(body_id)
        if body is None:
            return
        surface_id = str(spec.get("surface_id", ""))
        if any(s.semantics.surface_id == surface_id
               for item in self._project.bodies for s in item.hydro_surfaces):
            self._notify_warning(
                "Surface creation refused",
                f"duplicate surface id {surface_id!r}; surface ids are unique across the project",
            )
            return
        try:
            from .surface_details import new_assignment
            assignment = new_assignment(body, spec)
            new_body = body.with_edits(hydro_surfaces=body.hydro_surfaces + (assignment,))
            project = self._project.with_edits(bodies=tuple(
                new_body if item.body_id == body_id else item
                for item in self._project.bodies))
        except ValueError as error:
            self._notify_warning("Surface creation refused", str(error))
            return
        self._mark_edited(project)
        self._details.show_surface(body_id, surface_id)

    def _mark_edited(self, project: ProjectModel) -> None:
        """Adopt the edited project, flag dirt and refresh stale markers."""

        self._waves.reset()
        self._project = project
        self._dirty = True
        if getattr(self, '_rao', None) is not None:
            self._rao.project_changed()
        self._tree_model.set_project(project)
        self._details.set_project(project)
        self._refresh_assessment_detachment()
        if self._current_result is not None:
            stale = self._service.result_stale(project, self._current_result)
            self._result_status = self._result_status_text(self._current_result, stale)
            self._apply_frame(self._current_frame)
        else:
            self._handles = {}
            self.viewer.clear(keep_canvas=True)
            self._sync_viewer_bodies(project)

    def _result_status_text(self, result: ResultRecord, stale: bool) -> str:
        if result.kind=='line_equilibrium_development':
            return f"stored line equilibrium (development, unqualified) — {'stale' if stale else 'current'}"
        if result.kind=='equilibrium_development':
            flag=any(g['coefficient_rebind_required'] for g in result.equilibrium_evidence['audit']['potential_geometry'])
            return f"static equilibrium (development, unqualified){' — coefficients require regeneration' if flag else ''} — {'stale' if stale else 'current'}"
        if result.kind == "mechanical_development":
            producing = result.native_snapshot['project'].get('native_inputs',{})
            kind_label = ('native potential-flow/Morison/link mechanics (development, unqualified)'
                          if producing.get('morison') is not None and producing.get('potential_flow') is not None else
                          'native Morison wave/current/link mechanics (development, unqualified)'
                          if producing.get('morison') is not None else
                          'native coupled fluid/link mechanics (development, unqualified)' 
                          if producing.get('potential_flow') is not None else
                          'native dry/link mechanics (development, unqualified)')
            if producing.get('mooring') is not None:
                kind_label=kind_label.replace('/link mechanics','/link/mooring mechanics')
        elif result.kind == "linear_development":
            # Provenance-aware label: explicitly supplied inputs must never
            # be presented as the analytic demo.
            source = linear_result_source(result)
            if source == LINEAR_SOURCE_HYDRO:
                if "derived-operator import" in result.note:
                    kind_label = (
                        "linear development dynamics (ANYhydro derived-operator "
                        "import — development source, unqualified)"
                    )
                else:
                    kind_label = (
                        "linear development dynamics (ANYhydro dataset import — "
                        "development source, unqualified)"
                    )
            elif source == LINEAR_SOURCE_EXPLICIT:
                kind_label = "linear development dynamics (explicit supplied inputs)"
            elif source == LINEAR_SOURCE_ANALYTIC_DEMO:
                kind_label = "linear development dynamics (analytic demo)"
            else:
                kind_label = "linear development dynamics"
        else:
            kind_label = "synthetic demonstration"
        return (
            f"{kind_label} — result {result.result_id[:8]}… "
            f"({'STALE — inputs edited' if stale else 'current'})"
        )

    # ------------------------------------------------------------- tree/views

    def _on_tree_clicked(self, index) -> None:
        node = self._tree_model.node_for_index(index)
        if node is None:
            return
        if node.kind == "body":
            if self._current_result is not None and self._current_result.kind == 'mechanical_development':
                self._set_result(None)
            self._details.show_body(node.payload)
            self.viewer.set_highlight([node.payload])
        elif node.kind == 'surface':
            self._details.show_surface(node.payload[0], node.payload[1])
            self.viewer.set_highlight([node.payload[0]])
            self.statusBar().showMessage(node.label + '; attached surfaces share their owning body motion')
        elif node.kind == 'morison-member':
            self._set_result(None)
            self._details.show_empty()
            self.viewer.set_highlight(['morison-member:' + node.payload])
            self.statusBar().showMessage('Selected member centreline; edit endpoints, section and environment under Jobs → Edit native mechanical inputs')
        elif node.kind == "equilibrium":
            self._details.show_equilibrium()
            self.viewer.set_highlight([])
        elif node.kind == "settings":
            self._details.show_settings()
            self.viewer.set_highlight([])
        elif node.kind == "result":
            self._details.show_result(node.payload)
            result = (
                next((r for r in self._project.results if r.result_id == node.payload), None)
                if self._project is not None
                else None
            )
            if result is not None:
                self._set_result(result)
            self.viewer.set_highlight([])
        else:
            self._details.show_empty()
            self.viewer.set_highlight([])

    # ------------------------------------------------------------------ jobs

    def _import_native_inputs(self) -> None:
        if self._project is None:
            self._notify_info('Native inputs', 'Open or create a project first.')
            return
        path = self._ask_open_file('Select declared native mechanical inputs', 'Mechanical inputs (JSON *.json)')
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            project = self._service.load_native_inputs(self._project, data)
        except (OSError, ValueError, ServiceError) as error:
            self._notify_warning('Native inputs refused', str(error))
            return
        self._mark_edited(project)
        self._diagnostics.appendPlainText('Native mechanical inputs admitted through public solver validation; no job executed.')

    def _edit_native_inputs(self) -> None:
        if self._project is None or self._project.native_inputs is None:
            self._notify_info('Native inputs', 'Import declared native inputs first.')
            return
        from .native_inputs import NativeInputsDialog
        dialog = NativeInputsDialog(self._project, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            project = self._service.load_native_inputs(self._project, dialog.data())
        except (ValueError, ServiceError) as error:
            self._notify_warning('Native input changes refused', str(error))
            return
        self._mark_edited(project)

    def _adopt_equilibrium_dialog(self):
        result=self._current_result
        if self._project is None or result is None or result.kind!='equilibrium_development':
            self._notify_info('Equilibrium adoption','Select a complete current static equilibrium result in the project tree first.');return
        if self._service.result_stale(self._project,result):
            self._notify_warning('Equilibrium adoption','The selected static result is stale. Run equilibrium for the current project first.');return
        from .adoption_dialog import AdoptionDialog
        dialog=AdoptionDialog(self._project,result,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:self._queue_adoption(result,**dialog.parameters())
        except (ValueError,OSError,ServiceError) as error:
            self._notify_warning('Equilibrium adoption refused',str(error))

    def _queue_adoption(self,result,**options):
        job_id=self._job_manager.submit_adoption(self._project,result,**options)
        self._diagnostics.appendPlainText(f'Equilibrium adoption {job_id[:8]} queued; original project retained until audited output returns.')
        return job_id

    def _on_adoption_ready(self,job_id,adopted):
        from ..adoption_jobs import current_submission
        record=self._job_manager.job(job_id)
        if record is None or not current_submission(self._project,record.snapshot):
            artifact=str(record.output_path) if record is not None else job_id
            self._diagnostics.appendPlainText('Adoption output detached: project changed during execution. Preserved at '+artifact)
            self.statusBar().showMessage('Adoption output preserved; current edits were kept.')
            return
        self._set_project(adopted)
        self._dirty=True
        self.setWindowTitle('ANYmarine — '+adopted.name+' (adopted, unsaved, unqualified)')
        self._diagnostics.appendPlainText('Equilibrium adopted after worker balance audit. Save the project; run native dynamics when ready. Stored earlier results remain stale.')
        self.statusBar().showMessage('Equilibrium adopted (development, unqualified). Save to keep the new initial state.')

    def _run_equilibrium_job(self):
        if self._project is None:return
        try:job_id=self._job_manager.submit_equilibrium(self._project)
        except (ValueError,ServiceError) as error:
            self._notify_warning('Static job refused',str(error));return
        self._diagnostics.appendPlainText(f'Coupled static equilibrium job {job_id[:8]} queued; development/unqualified.')

    def _run_hams_job(self):
        if self._project is None:
            self._notify_info('HAMS fields', 'Load the producing geometry first.'); return
        path, _ = QFileDialog.getOpenFileName(self, 'Explicit trusted HAMS field configuration', '', 'JSON (*.json)')
        if not path:return
        try:
            import json
            config = json.loads(Path(path).read_text(encoding='utf-8'))
            job_id = self._job_manager.submit_hams(self._project, config)
        except (OSError, ValueError, ServiceError) as error:
            self._notify_warning('HAMS fields refused', str(error)); return
        self._diagnostics.appendPlainText(f'HAMS field job {job_id[:8]} queued with captured geometry/source inputs; unqualified. Evidence: {self._job_manager.job(job_id).input_path.parent}')

    def _on_hams_ready(self, job_id, record):
        job = self._job_manager.job(job_id)
        if self._project is None or not record.matches(self._project) or self._current_result is not None:
            self._diagnostics.appendPlainText(f'HAMS fields {job_id[:8]} retained without display; producing project changed or body replay selected. Bundle: {job.output_path.parent / "hams-wave-bundle"}')
            return
        try:self._waves.load_stored_hams(record)
        except ValueError as error:
            self._notify_warning('HAMS replay refused', str(error)); return
        self._wave_dock.raise_()
        self._diagnostics.appendPlainText(f'HAMS field job {job_id[:8]} displayed from authoritative saved arrays; development, unqualified.')

    def _run_native_job(self) -> None:
        if self._project is None or self._project.native_inputs is None:
            self._notify_info('Native mechanics', 'Import and validate declared native inputs first.')
            return
        from .native_inputs import NativeRunLimitsDialog
        dialog = NativeRunLimitsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            job_id = self._job_manager.submit_native(self._project, **dialog.limits())
        except (ValueError, ServiceError) as error:
            self._notify_warning('Native job refused', str(error))
            return
        self._diagnostics.appendPlainText(f'Native mechanical development job {job_id[:8]} queued; unqualified.')

    def _run_job(self) -> None:
        if self._project is None:
            self._notify_info("No project", "Open or create a project first.")
            return
        job_id = self._job_manager.submit(self._project)
        self._diagnostics.appendPlainText(f"submitted job {job_id}")
        self.statusBar().showMessage(
            "Synthetic development replay — not a marine analysis"
        )

    def _import_hydro_inputs(self) -> None:
        """Import ANYhydro dataset/kernel inputs through the shared service.

        The same ApplicationService admission method as the CLI
        ``import-hydro`` command validates the geometry proof, identity
        mapping, reconstruction criteria and forcing options before the
        project changes; the authoritative raw files are copied into the
        project so the saved project reproduces without the original paths.
        """

        if self._project is None:
            self._notify_info("No project", "Open or create a project first.")
            return
        if self._project_root is None:
            self._notify_warning(
                "Save first",
                "Save the project before importing hydrodynamic inputs: the "
                "authoritative dataset/kernel files are copied into the project.",
            )
            return
        dataset = self._ask_open_file(
            "Select ANYhydro dataset", "ANYhydro dataset (NetCDF *.nc)"
        )
        if not dataset:
            return
        kernel = self._ask_open_file(
            "Select ANYhydro radiation kernel", "ANYhydro radiation kernel (NetCDF *.nc)"
        )
        if not kernel:
            return
        options_path = self._ask_open_file(
            "Select import options", "Import options (JSON *.json)"
        )
        if not options_path:
            return
        try:
            options = json.loads(Path(options_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._notify_critical("Hydro import failed", f"cannot read {options_path}: {error}")
            return
        try:
            project = self._service.import_hydro_inputs(
                self._project, dataset, kernel, options
            )
        except (ServiceError, ProjectError) as error:
            self._notify_critical("Hydro import failed", str(error))
            return
        self._mark_edited(project)
        provenance = project.hydro_provenance
        self._diagnostics.appendPlainText(
            f"imported ANYhydro inputs (dataset {provenance.dataset_content_hash[:12]}... "
            f"kernel {provenance.kernel_content_hash[:12]}...; development source, "
            "unqualified)"
        )
        self.statusBar().showMessage(
            "Hydrodynamic inputs imported (ANYhydro dataset; development source, "
            "unqualified — not a qualified marine analysis)"
        )

    def _import_derived_hydro_inputs(self) -> None:
        """Import ANYhydro raw/derived/kernel inputs through the shared service.

        Explicit derived-operator route: the same ApplicationService
        admission method as the CLI ``import-derived-hydro`` command,
        requiring the actual raw dataset, the derived-operator artifact
        and the derived radiation kernel plus explicit options. The
        authoritative raw files are copied into the project so the saved
        project reproduces without the original paths. Never a fallback
        of the raw import route.
        """

        if self._project is None:
            self._notify_info("No project", "Open or create a project first.")
            return
        if self._project_root is None:
            self._notify_warning(
                "Save first",
                "Save the project before importing derived hydrodynamic "
                "inputs: the authoritative dataset/operators/kernel files "
                "are copied into the project.",
            )
            return
        dataset = self._ask_open_file(
            "Select ANYhydro raw dataset", "ANYhydro dataset (NetCDF *.nc)"
        )
        if not dataset:
            return
        operators = self._ask_open_file(
            "Select ANYhydro derived-operator artifact",
            "ANYhydro derived operators (NumPy archive *.npz)",
        )
        if not operators:
            return
        kernel = self._ask_open_file(
            "Select ANYhydro derived radiation kernel",
            "ANYhydro radiation kernel (NetCDF *.nc)"
        )
        if not kernel:
            return
        options_path = self._ask_open_file(
            "Select import options", "Import options (JSON *.json)"
        )
        if not options_path:
            return
        try:
            options = json.loads(Path(options_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._notify_critical("Derived hydro import failed", f"cannot read {options_path}: {error}")
            return
        try:
            project = self._service.import_derived_hydro_inputs(
                self._project, dataset, operators, kernel, options
            )
        except (ServiceError, ProjectError) as error:
            self._notify_critical("Derived hydro import failed", str(error))
            return
        self._mark_edited(project)
        provenance = project.hydro_provenance
        bundle = provenance.derived_bundle
        self._diagnostics.appendPlainText(
            f"imported ANYhydro derived-operator inputs (derived "
            f"{bundle['derived_content_hash'][:12]}...; development source, "
            "unqualified)"
        )
        self.statusBar().showMessage(
            "Derived hydrodynamic inputs imported (ANYhydro derived operators; "
            "development source, unqualified - not a qualified marine analysis)"
        )

    def _run_linear_job(self) -> None:
        """Create/load the labelled linear demo inputs and run a QProcess job.

        The stored linear inputs are reused when present; otherwise the
        analytic demo is created from the project's two bodies and stored
        with the project. The job runs through the same isolated worker;
        a missing optional solver fails closed in the worker and is shown
        here as a failed job.
        """

        if self._project is None:
            self._notify_info("No project", "Open or create a project first.")
            return
        inputs = self._project.linear_inputs
        if inputs is None:
            try:
                inputs = self._service.linear_demo_inputs(self._project)
                project = self._service.set_linear_inputs(self._project, inputs)
            except (ServiceError, LinearError) as error:
                self._notify_warning("Linear demo", str(error))
                return
            self._mark_edited(project)
        try:
            job_id = self._job_manager.submit(self._project, linear_inputs=inputs)
        except (ServiceError, LinearError) as error:
            self._notify_warning("Linear job", str(error))
            return
        if inputs.provenance.source == LINEAR_SOURCE_HYDRO:
            origin = "ANYhydro dataset import"
        elif inputs.provenance.source == LINEAR_SOURCE_EXPLICIT:
            origin = "explicit supplied inputs"
        else:
            origin = "analytic demo inputs"
        self._diagnostics.appendPlainText(
            f"submitted linear development job {job_id} ({origin}; "
            "development dynamics, not a qualified marine analysis)"
        )
        self.statusBar().showMessage(
            f"Linear development dynamics ({origin}) — development route, "
            "not a qualified marine analysis"
        )

    def _on_job_state(self, job_id: str, state: str) -> None:
        self._refresh_jobs_table()
        record = self._job_manager.job(job_id)
        if record is not None:
            self._diagnostics.appendPlainText(
                f"job {job_id[:8]}… {state}"
            )

    def _on_job_progress(self, job_id: str, fraction: float) -> None:
        self._refresh_jobs_table()

    def _on_job_result(self, job_id: str, result: ResultRecord) -> None:
        if self._project is None:
            return
        try:
            project = self._service.add_result(self._project, result)
            if self._project_root is not None:
                self._service.save_current(project, self._project_root)
        except (ServiceError, ProjectError) as error:
            self._notify_critical("Job result rejected", str(error))
            return
        self._project = project
        self._dirty = self._project_root is None
        self._tree_model.set_project(project)
        self._details.set_project(project)
        self._refresh_assessment_detachment()
        self._set_result(result)
        self._diagnostics.appendPlainText(
            f"job {job_id[:8]}… completed; result {result.result_id[:8]}… stored "
            f"(kind {result.kind})"
        )

    def _on_job_failed(self, job_id: str, message: str) -> None:
        self._diagnostics.appendPlainText(f"job {job_id[:8]}… FAILED: {message}")

    def _refresh_jobs_table(self) -> None:
        jobs = self._job_manager.jobs()
        self._jobs_table.setRowCount(len(jobs))
        for row, (job_id, record) in enumerate(jobs.items()):
            self._jobs_table.setItem(row, 0, QTableWidgetItem(job_id[:8] + "…"))
            self._jobs_table.setItem(row, 1, QTableWidgetItem(record.state))
            self._jobs_table.setItem(row, 2, QTableWidgetItem(f"{record.progress * 100:.0f}%"))

    # ------------------------------------------------------------- assessment

    def _assessment_project_ready(self) -> bool:
        if self._project is None or self._project.linear_inputs is None:
            self._notify_info(
                "Assessment",
                "Open a project with stored linear inputs first.",
            )
            return False
        return True

    def _register_case_dialog(self) -> None:
        if not self._assessment_project_ready():
            return
        dialog = RegisterCaseDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._do_register_case(dialog.spec_path(), dialog.target_path())

    def _do_register_case(self, spec_path: str, case_target: str) -> None:
        """Submit a case-registration job (validation runs in the child)."""

        if not self._assessment_project_ready():
            return
        try:
            spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self._notify_warning(
                "Register case", f"cannot read the case specification: {error}"
            )
            return
        try:
            snapshot = prepare_assessment_job(
                self._project, spec, case_target, service_root=self._service.root
            )
            job_id = self._job_manager.submit_assessment(snapshot)
        except AssessmentJobError as error:
            self._notify_warning("Register case", str(error))
            return
        self._assessment_panel.set_status(
            f"case registration job {job_id[:8]}… submitted (proof in child)"
        )
        self._diagnostics.appendPlainText(
            f"submitted assessment case-registration job {job_id}"
        )

    def _assess_dialog(self) -> None:
        if not self._assessment_project_ready():
            return
        result_ids = [record.result_id for record in self._project.results]
        if not result_ids:
            self._notify_info(
                "Assessment", "The project holds no stored results to assess."
            )
            return
        current = self._current_result.result_id if self._current_result else ""
        dialog = AssessSingleDialog(result_ids, current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._do_assess_result(
            dialog.case_path(), dialog.result_id(), dialog.snapshot_target()
        )

    def _do_assess_result(
        self, case_path: str, result_id: str, snapshot_target: str = ""
    ) -> None:
        """Submit a single-result assessment job (all proofs in the child)."""

        if not self._assessment_project_ready():
            return
        try:
            case = read_case_artifact(case_path)
        except (OSError, ValueError, AssessmentError) as error:
            self._notify_warning("Assess result", f"cannot load the case: {error}")
            return
        matches = [
            record for record in self._project.results
            if record.result_id == result_id
        ]
        if len(matches) != 1:
            self._notify_warning(
                "Assess result",
                f"the project must hold exactly one result with id {result_id!r}",
            )
            return
        try:
            snapshot = single_assessment_job(
                self._project,
                matches[0],
                case,
                service_root=self._service.root,
                snapshot_target=snapshot_target or None,
            )
            job_id = self._job_manager.submit_assessment(snapshot)
        except AssessmentJobError as error:
            self._notify_warning("Assess result", str(error))
            return
        self._assessment_panel.set_status(
            f"assessment job {job_id[:8]}… submitted (proof in child)"
        )
        self._diagnostics.appendPlainText(
            f"submitted single assessment job {job_id} (result {result_id[:8]}…)"
        )

    def _load_project_results(self, root: str) -> list:
        """Dialog loader: light public record load (no source proof).

        ``ProjectStore.load`` reads the public project records only; the
        hydro source/eligibility proof runs in the child on the frozen
        payloads, never in this GUI callback.
        """

        project = ProjectStore(root).load()
        return [record.result_id for record in project.results]

    def _pair_dialog(self) -> None:
        dialog = PairDialog(self._load_project_results, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._do_pair(
            dialog.coarse().project_root(),
            dialog.coarse().result_id(),
            dialog.coarse().case_path(),
            dialog.fine().project_root(),
            dialog.fine().result_id(),
            dialog.fine().case_path(),
            dialog.out_root(),
        )

    def _do_pair(
        self,
        coarse_root: str,
        coarse_result_id: str,
        coarse_case_path: str,
        fine_root: str,
        fine_result_id: str,
        fine_case_path: str,
        out_root: str,
    ) -> None:
        """Submit an explicit coarse/fine pair job with exclusive snapshot."""

        try:
            # Light public record loads only: the hydro source/eligibility
            # proof for the frozen members runs in the child, never in this
            # GUI callback.
            coarse_project = ProjectStore(coarse_root).load()
            fine_project = ProjectStore(fine_root).load()
            coarse_result = self._select_result(coarse_project, coarse_result_id)
            fine_result = self._select_result(fine_project, fine_result_id)
            coarse_case = read_case_artifact(coarse_case_path)
            fine_case = read_case_artifact(fine_case_path)
            snapshot = pair_assessment_job(
                coarse_project,
                coarse_result,
                coarse_case,
                coarse_root,
                fine_project,
                fine_result,
                fine_case,
                fine_root,
                out_root,
            )
            job_id = self._job_manager.submit_assessment(snapshot)
        except (OSError, ValueError, AssessmentError, ProjectError, ServiceError,
                AssessmentJobError) as error:
            self._notify_warning("Assess pair", str(error))
            return
        self._assessment_panel.set_status(
            f"paired assessment job {job_id[:8]}… submitted (proof in child)"
        )
        self._diagnostics.appendPlainText(
            f"submitted paired assessment job {job_id} (exclusive snapshot target)"
        )

    @staticmethod
    def _select_result(project: ProjectModel, result_id: str) -> ResultRecord:
        matches = [
            record for record in project.results if record.result_id == result_id
        ]
        if len(matches) != 1:
            raise ServiceError(
                f"the project must hold exactly one result with id {result_id!r}; "
                f"found {len(matches)}"
            )
        return matches[0]

    def _reopen_dialog(self) -> None:
        dialog = ReopenSnapshotDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._do_reopen(dialog.snapshot_root())

    def _do_reopen(self, snapshot_root: str) -> None:
        """Submit a snapshot-reopen job (reproduction runs in the child)."""

        try:
            snapshot = adjudication_job(snapshot_root)
            job_id = self._job_manager.submit_assessment(snapshot)
        except (OSError, ValueError, AssessmentJobError) as error:
            self._notify_warning("Reopen snapshot", str(error))
            return
        self._assessment_panel.set_status(
            f"snapshot reproduction job {job_id[:8]}… submitted (proof in child)"
        )
        self._diagnostics.appendPlainText(
            f"submitted snapshot adjudication job {job_id} ({Path(snapshot_root).name})"
        )

    def _assessment_detached(self, payload: dict) -> bool:
        """True when the current project no longer matches the submission.

        Evidence from a job whose bound inputs differ from the currently
        open project is detached/stale, never presented as current. The
        check uses the light public result-staleness / linear binding
        identity checks (never hydro numerical source proof): a physical
        edit (body mass, reference position, mesh, equilibrium) invalidates
        the binding even though the stored linear-inputs hash is
        unchanged, so displayed evidence is re-labelled after any edit.
        """

        context = payload.get("context")
        if self._project is None or self._project.linear_inputs is None:
            return True
        current = linear_input_hash(self._project.linear_inputs)
        contexts = []
        if isinstance(context, dict):
            contexts.append(context)
            for value in context.values():
                if isinstance(value, dict) and (
                    value.get("input_hash") or value.get("result_id")
                ):
                    contexts.append(value)
        for bound in contexts:
            if bound.get("input_hash") and str(bound["input_hash"]) != current:
                return True
            result_id = bound.get("result_id")
            if not result_id:
                continue
            matches = [
                record
                for record in self._project.results
                if record.result_id == str(result_id)
            ]
            if len(matches) != 1:
                return True
            if self._service.result_stale(self._project, matches[0]):
                return True
            body_ids = bound.get("body_ids")
            if isinstance(body_ids, list) and body_ids:
                if [str(b) for b in body_ids] != [
                    str(b) for b in matches[0].body_ids
                ]:
                    return True
        return False

    def _refresh_assessment_detachment(self) -> None:
        """Re-label displayed evidence after any current-project change.

        Evidence already on screen is re-evaluated against the (possibly
        edited or replaced) current project on every project change, so
        stale/detached state is always shown honestly — including edits
        that happen after the evidence was displayed.
        """

        panel = getattr(self, "_assessment_panel", None)
        if panel is None:
            return
        payload = panel.payload()
        if payload is None or str(payload.get("kind")) == ASSESSMENT_KIND_PREPARE:
            return
        panel.display_payload(payload, self._assessment_detached(payload))

    def _on_assessment_ready(self, job_id: str, payload: dict) -> None:
        """Display child-validated evidence; admit replay only from the
        child's reproduced captured records."""

        kind = str(payload.get("kind"))
        if kind == ASSESSMENT_KIND_PREPARE:
            case_id = str((payload.get("case") or {}).get("case_id") or "?")
            self._assessment_panel.set_status(
                f"case {case_id} registered (artifact "
                f"{Path(str(payload.get('case_target'))).name}); the case is "
                "not assessment evidence"
            )
            self._diagnostics.appendPlainText(
                f"assessment job {job_id[:8]}… registered case {case_id} "
                f"(hash {str(payload.get('case_hash'))[:12]}…)"
            )
            return
        detached = self._assessment_detached(payload)
        note = ""
        if kind in (
            ASSESSMENT_KIND_ADJUDICATE_SINGLE,
            ASSESSMENT_KIND_ADJUDICATE_PAIR,
            ASSESSMENT_KIND_PAIR,
        ):
            note = self._admit_snapshot_replay(payload, detached)
        self._assessment_panel.display_payload(payload, detached, note)
        verdict = payload.get("verdict")
        self._diagnostics.appendPlainText(
            f"assessment job {job_id[:8]}… evidence admitted "
            f"(kind {kind}, verdict {verdict!r}"
            + (", snapshot saved" if payload.get("snapshot") else "")
            + ")"
        )
        self.statusBar().showMessage(
            f"Assessment verdict: {verdict} — developmental, unqualified"
        )

    def _rebuild_replay_records(self, entry: object, label: str):
        """Rebuild child-returned replay records (transport validation only).

        The child already reproduced the snapshot from its private
        reconstruction and returned the captured authoritative project/
        result payloads (public record serialization). The GUI never
        re-reads the original snapshot root and never re-runs hydro
        source or assessment numerics here; deleting the originals after
        submission cannot break replay.
        """

        if not isinstance(entry, dict):
            raise AssessmentJobError(
                f"the child returned no reproduced {label} records"
            )
        try:
            project = project_from_frozen(entry["project"])
            result = ResultRecord.from_dict(entry["result"])
        except (AssessmentJobError, KeyError, ValueError, TypeError) as error:
            raise AssessmentJobError(
                f"the reproduced {label} records are invalid: {error}"
            ) from error
        matches = [
            record for record in project.results if record.result_id == result.result_id
        ]
        if len(matches) != 1 or matches[0].to_dict() != result.to_dict():
            raise AssessmentJobError(
                f"the reproduced {label} result is not the snapshot's own "
                "stored result"
            )
        return project, result

    def _admit_snapshot_replay(self, payload: dict, detached: bool) -> str:
        """Admit replay from the child's reproduced captured records only."""

        kind = str(payload.get("kind"))
        replay = payload.get("replay")
        try:
            if kind == ASSESSMENT_KIND_ADJUDICATE_SINGLE:
                project, result = self._rebuild_replay_records(
                    replay, "assessment snapshot"
                )
                self._replay_members = None
                self._set_snapshot_replay(
                    project,
                    result,
                    "reproduced assessment snapshot — detached replay, "
                    "development only, unqualified",
                )
                return (
                    "snapshot reproduced in child; replaying its own "
                    "stored arrays"
                )
            if not isinstance(replay, dict):
                raise AssessmentJobError(
                    "the child returned no reproduced paired records"
                )
            members = {}
            for role in ("coarse", "fine"):
                members[role] = self._rebuild_replay_records(
                    replay.get(role), f"{role} member"
                )
        except AssessmentJobError as error:
            self._replay_members = None
            return f"replay refused: {error}"
        self._replay_members = members
        self._select_replay_member(self._assessment_panel.current_member())
        return "paired snapshot reproduced in child; select a member to replay"

    def _select_replay_member(self, role: str) -> None:
        """Replay ONE member's own captured project/result; never mix."""

        if self._replay_members is None:
            return
        member = self._replay_members.get(str(role))
        if member is None:
            return
        project, result = member
        self._set_snapshot_replay(
            project,
            result,
            f"reproduced paired snapshot — {role} member (detached replay, "
            "development only, unqualified)",
        )

    def _set_snapshot_replay(
        self, project: ProjectModel, result: ResultRecord, status: str
    ) -> None:
        """Detached replay from the snapshot's own authoritative arrays."""

        self._replay_project = project
        self._handles = {}
        self.viewer.clear(keep_canvas=True)
        self._sync_viewer_bodies(project)
        self._set_result(result, status=status)

    # ----------------------------------------------------------------- replay

    def _set_result(self, result: Optional[ResultRecord], status: Optional[str] = None) -> None:
        if result is not None and result.kind == 'linear_development' and result.surface_snapshot is None:
            from ..surface_assignments import has_surface_intent
            source = self._project if status is None else (self._replay_project or self._project)
            if source is not None and has_surface_intent(source):
                self._notify_warning('Replay geometry unavailable', 'This linear result has no producing surface geometry. Replay is refused for a project with current or retained surface intent; open its plate-free producing project or a sealed surface snapshot.')
                return
        mechanical_waves=self._waves.mode.currentIndex()==3
        self._waves.reset()
        if status is None and self._replay_project is not None:
            # Leaving detached snapshot replay: restore the current
            # project's own viewer bodies before showing its results.
            self._replay_project = None
            self._handles = {}
            self.viewer.clear(keep_canvas=True)
            self._sync_viewer_bodies()
        if result is not None and result.surface_snapshot is not None:
            from ..surface_assignments import project_from_surface_snapshot
            self._replay_project = project_from_surface_snapshot(result.surface_snapshot)
            self._handles = {}
            self.viewer.clear(keep_canvas=True)
            self._sync_viewer_bodies(self._replay_project)
        if result is not None and result.kind in ('mechanical_development','equilibrium_development','line_equilibrium_development'):
            if result.kind=='line_equilibrium_development':
                from ..line_equilibrium import project_from_snapshot
            elif result.kind=='equilibrium_development':
                from ..equilibrium_jobs import project_from_snapshot
            else:
                from ..native_jobs import project_from_snapshot
            self._replay_project = project_from_snapshot(result.native_snapshot)
            self._handles = {}
            self.viewer.clear(keep_canvas=True)
            self._sync_viewer_bodies(self._replay_project,line_nodes=result.line_nodes[0] if result.kind=='line_equilibrium_development' else None)
        self._current_result = result
        self._play_timer.stop()
        self._play_button.setText("Play")
        if result is None:
            self._frame_slider.setRange(0, 0)
            self._channel_combo.clear()
            self._plot.set_series("", [], [])
            self._result_status = "no result"
            self._frame_label.setText("no result")
            return
        frames = result.frame_count()
        self._frame_slider.setRange(0, frames - 1)
        self._frame_slider.setValue(0)
        self._channel_combo.blockSignals(True)
        self._channel_combo.clear()
        self._channel_combo.addItems(sorted(result.channels))
        self._channel_combo.blockSignals(False)
        self._on_channel_changed(self._channel_combo.currentText())
        self._current_frame = 0
        stale = self._service.result_stale(self._project, result) if self._project else False
        self._result_status = (
            status if status is not None else self._result_status_text(result, stale)
        )
        self._apply_frame(0)
        if not self._waves.match_result(result) and mechanical_waves:self._waves.mode.setCurrentIndex(3)

    def _on_slider_moved(self, value: int) -> None:
        self._apply_frame(value)

    def _on_channel_changed(self, name: str) -> None:
        if self._current_result is None or not name:
            return
        values = self._current_result.channels.get(name, ())
        self._plot.set_abscissa('stage' if self._current_result.kind in ('equilibrium_development','line_equilibrium_development') else 'time')
        self._plot.set_series(name, self._current_result.times, values)
        self._plot.set_cursor(self._current_frame)

    def _toggle_play(self) -> None:
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._play_button.setText("Play")
        else:
            if self._current_result is None:
                return
            self._play_timer.start()
            self._play_button.setText("Pause")

    def _advance_frame(self) -> None:
        if self._current_result is None:
            self._play_timer.stop()
            return
        frames = self._current_result.frame_count()
        self._current_frame = (self._current_frame + 1) % max(frames, 1)
        self._frame_slider.setValue(self._current_frame)

    def _apply_frame(self, frame: int) -> None:
        """Push one stored frame to the viewer via handle.set_transform only."""

        if self._waves.active and not self._waves.follows_replay:
            self._waves.reset()
        self._current_frame = frame
        if self._current_result is None:
            return
        index = min(max(frame, 0), self._current_result.frame_count() - 1)
        for body_id in self._current_result.body_ids:
            handle = self._handles.get(body_id)
            if handle is None:
                continue
            position = self._current_result.positions[body_id][index]
            if self._current_result.kind in ('mechanical_development','equilibrium_development'):
                rotation = self._current_result.rotation_matrices[body_id][index]
                transform = matrix_frame_transform(position, rotation)
            else:
                orientation = self._current_result.orientations[body_id][index]
                transform = frame_transform(position, orientation)
            handle.set_transform(transform)
        self._member_scene.update(self._handles,None if self._current_result.line_nodes is None else self._current_result.line_nodes[index],result=self._current_result,index=index)
        if self._current_result.kind not in ('equilibrium_development','line_equilibrium_development'):self._waves.set_replay_time(self._current_result.times[index])
        channel = self._channel_combo.currentText()
        if channel:
            self._plot.set_cursor(index)
        if self._current_result.times:
            self._frame_label.setText(
                f"{self._result_status} — frame {index + 1}/{self._current_result.frame_count()} "
                + (f"stage {index}: {self._current_result.equilibrium_evidence['frames'][index]['kind']}" if self._current_result.kind=='equilibrium_development' else f"t={self._current_result.times[index]:.2f}s")
            )
        else:
            self._frame_label.setText(self._result_status)
        if self._current_result.kind=='line_equilibrium_development':
            from ..line_equilibrium import stages,final_audit
            stage=stages(self._current_result)[index]
            self._frame_label.setText(f'{self._result_status} — stage {index+1}/{self._current_result.frame_count()}: {stage["name"]} fraction={stage["fraction"]:g}; residual={stage["residual_norm"]:g} N')
            if index==self._current_result.frame_count()-1:
                audit=final_audit(self._current_result)
                self._frame_label.setText(self._frame_label.text()+f'; final audit: minimum tension={audit["min_tension"]:g} N; maximum penetration={audit["max_penetration"]:g} m; anchor reactions={audit["anchor_reactions"]}')
        if self._member_scene.contact_inputs is not None and self._current_result.kind in ('mechanical_development','equilibrium_development'):
            from ..contact_inputs import contact_lumps
            count=sum(self._current_result.channels[f'mooring/contact/lump{i}/active'][index]
                      for i in range(len(contact_lumps(self._member_scene.contact_inputs))))
            penetration=self._current_result.channels['mooring/contact/max_penetration'][index]
            self._frame_label.setText(self._frame_label.text()+f' — active contact samples{int(count)}, max penetration{penetration:.5g} m')

    # ------------------------------------------------------------- file menu

    def _new_project(self) -> None:
        if not self._confirm_discard():
            return
        project = self._service.new_project("Two-body demonstrator")
        self._project_root = None
        self._set_project(project)
        self.setWindowTitle("ANYmarine — unsaved project")

    def _open_project(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project manifest", "", "ANYmarine manifest (project.json)"
        )
        if path:
            self.load_project(str(Path(path).parent))

    def _save_project(self) -> None:
        if self._project is None:
            return
        target_root = self._project_root
        if target_root is None:
            path = QFileDialog.getSaveFileName(
                self, "Save project", "", "ANYmarine manifest (project.json)"
            )[0]
            if not path:
                return
            target_root = Path(path).parent if str(path).endswith("project.json") else Path(path)
        try:
            saved_root = self._service.save_current(self._project, target_root)
        except (ProjectError, ServiceError) as error:
            self._notify_critical("Save failed", str(error))
            return
        self._project_root = saved_root
        self._dirty = False
        self._diagnostics.appendPlainText(f"saved project to {self._project_root}")

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            "The project has unsaved edits. Discard them?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _show_loading_help(self) -> None:
        self._notify_info('Loading models',
            'File > Open project...: select project.json in a saved ANYmarine project.\n\n'
            'File > Load example (two-body replay): ready-to-play stored demonstration.\n'
            'File > Load native coupled fluid example: load two bodies; '
            'Jobs > Run native mechanical job... creates results.\n\n'
            'View > Fit model (Ctrl+F): bring the loaded bodies into view.\n'
            'For a black canvas, choose View > Renderer > Software (compatible).\n\n'
            'CAD/mesh-file import is not available yet. Built-in numerical examples '
            'are development inputs and remain unqualified.')

    def _refresh_startup_development_status(self) -> None:
        try:
            status = load_development_status()
        except DevelopmentStatusError as exc:
            self._development_status = None
            self._development_status_label.setText(
                'Development status data unavailable (corrupt or missing)')
            self._development_status_label.setToolTip(str(exc))
            return
        self._development_status = status
        self._development_status_label.setText(startup_summary(status))

    def _show_development_status(self) -> None:
        try:
            status = load_development_status()
        except DevelopmentStatusError as exc:
            self._notify_critical(
                'Development status',
                'Development status data is unavailable or corrupt:\n' f'{exc}')
            return
        from .development_status import DevelopmentStatusDialog
        dialog = DevelopmentStatusDialog(status, self)
        dialog.exec()

    def _show_about(self) -> None:
        self._notify_info(
            "ANYmarine",
            "ANYmarine application foundation.\n\n"
            "Synthetic replay results are deterministic demonstrations labelled "
            "'synthetic_unqualified'. They are not boundary-element-method output "
            "and not dynamics integration, and are not qualified for production use.",
        )

    # ----------------------------------------------------------------- close

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._rao.busy:
            answer = QMessageBox.question(
                self, "RAO calculation running",
                "Cancel the running RAO calculation and close? Its evidence stays in the output directory.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._rao.cancel()
        active = self._job_manager.active_jobs()
        if not active:
            if not self._confirm_discard():
                event.ignore()
                return
            self._fit_timer.stop()
            self._play_timer.stop()
            self._waves.timer.stop()
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Jobs still active",
            f"Cancel {len(active)} active job(s) and close? Choose No to keep the window open.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._job_manager.cancel_all()
        deadline = QTimer(self)
        deadline.setSingleShot(True)
        deadline.start(CLOSE_CANCEL_WAIT_MS)
        while self._job_manager.active_jobs() and deadline.isActive():
            QApplication.processEvents()
        if self._job_manager.active_jobs():
            event.ignore()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        self._fit_timer.stop()
        self._play_timer.stop()
        self._waves.timer.stop()
        event.accept()


def run_app(project_root: str | None = None, *, viewer_backend: str | None = None) -> int:
    """GUI entry point (GUI thread only)."""

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(viewer_backend=viewer_backend)
    if project_root:
        window.load_project(project_root)
    else:
        window.load_example()
    window.show()
    return app.exec()


