"""Editable details panel for the current project-tree selection."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QCheckBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QScrollArea,
    QHeaderView,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSizePolicy,
    QLineEdit,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..domain import ProjectModel, ResultRecord
from ..mass_properties import BodyMassProperties
from ..mechanics import MechanicsError
from ..services import ApplicationService

_PAGE_EMPTY = 0
_PAGE_BODY = 1
_PAGE_EQUILIBRIUM = 2
_PAGE_SETTINGS = 3
_PAGE_RESULT = 4


def _spin(value: float, decimals: int = 3, minimum: float = -1.0e9, maximum: float = 1.0e9) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setValue(float(value))
    return box


class DetailsPanel(QWidget):
    """Shows editable fields for bodies/equilibrium/settings and result info.

    Edits are emitted as plain dictionaries; the main window applies them to
    the ProjectModel, marks the project dirty and refreshes stale flags.
    """

    body_edited = Signal(str, dict)
    equilibrium_edited = Signal(dict)
    settings_edited = Signal(dict)

    def __init__(self, service: ApplicationService, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._service = service
        self._project: Optional[ProjectModel] = None
        self._current_body_id: Optional[str] = None
        self._updating = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._stack = QStackedLayout()
        layout.addLayout(self._stack)

        # empty page
        self._stack.addWidget(QLabel("Select an item in the project tree."))

        # body page
        body_box = QGroupBox("Body")
        body_form = QFormLayout(body_box)
        self._body_name = QLineEdit()
        self._body_ref_x = _spin(0.0)
        self._body_ref_y = _spin(0.0)
        self._body_ref_z = _spin(0.0)
        self._body_mass = _spin(1.0e6, decimals=0, minimum=1.0)
        self._body_color = QLineEdit("#3b82c4")
        body_form.addRow("Name", self._body_name)
        body_form.addRow("Reference X [m]", self._body_ref_x)
        body_form.addRow("Reference Y [m]", self._body_ref_y)
        body_form.addRow("Reference Z [m]", self._body_ref_z)
        body_form.addRow("Mass [kg]", self._body_mass)
        body_form.addRow("Color", self._body_color)
        self._mass_enabled = QCheckBox("Specify CG and inertia")
        body_form.addRow(self._mass_enabled)
        self._cg_fields = [QLineEdit() for _ in range(3)]
        for axis, edit in zip("XYZ", self._cg_fields):
            edit.setPlaceholderText("Required when enabled")
            body_form.addRow(f"CG {axis}, body axes [m]", edit)
            edit.editingFinished.connect(self._emit_mass_properties)
        self._inertia_table = QTableWidget(3, 3)
        self._inertia_table.setHorizontalHeaderLabels(list("XYZ"))
        self._inertia_table.setVerticalHeaderLabels(list("XYZ"))
        self._inertia_table.setFixedHeight(145)
        self._inertia_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._inertia_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row in range(3):
            for col in range(3):
                self._inertia_table.setItem(row, col, QTableWidgetItem(""))
                self._inertia_table.setColumnWidth(col, 68)
        self._inertia_table.setToolTip("Full symmetric inertia tensor about CG in body axes; tensor off-diagonals, kg m²")
        body_form.addRow(QLabel("CG inertia [kg m²], body axes"))
        body_form.addRow(self._inertia_table)
        self._mass_status = QLabel("CG/inertia not supplied")
        self._mass_status.setWordWrap(True)
        self._mass_status.setMinimumHeight(60)
        self._mass_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        body_form.addRow(self._mass_status)
        self._mass_validate = QPushButton("Validate dry mechanics")
        body_form.addRow(self._mass_validate)
        self._mass_validate.clicked.connect(self._validate_mass_properties)
        self._mass_enabled.toggled.connect(self._toggle_mass_properties)
        self._inertia_table.itemChanged.connect(self._emit_mass_properties)
        self._toggle_mass_properties(False)
        body_page = QWidget()
        body_page.setObjectName("massInputs")
        body_page.setLayout(QVBoxLayout())
        body_page.layout().addWidget(body_box)
        body_page.layout().addStretch(1)
        self._mass_scroll = QScrollArea()
        self._mass_scroll.setWidgetResizable(True)
        self._mass_scroll.setStyleSheet("QScrollArea, QWidget#qt_scrollarea_viewport, QWidget#massInputs { background: #f3f5f8; }")
        self._mass_scroll.setWidget(body_page)
        self._stack.addWidget(self._mass_scroll)

        # equilibrium page
        eq_box = QGroupBox("Equilibrium")
        eq_form = QFormLayout(eq_box)
        self._eq_waterline = _spin(0.0)
        self._eq_description = QLineEdit()
        self._eq_source = QLabel("-")
        eq_form.addRow("Waterline Z [m]", self._eq_waterline)
        eq_form.addRow("Description", self._eq_description)
        eq_form.addRow("Source", self._eq_source)
        eq_page = QWidget()
        eq_page.setLayout(QVBoxLayout())
        eq_page.layout().addWidget(eq_box)
        eq_page.layout().addStretch(1)
        self._stack.addWidget(eq_page)

        # settings page
        set_box = QGroupBox("Analysis settings")
        set_form = QFormLayout(set_box)
        self._set_duration = _spin(20.0, decimals=2, minimum=0.01)
        self._set_dt = _spin(0.25, decimals=3, minimum=0.001)
        self._set_amplitude = _spin(0.8, decimals=3, minimum=0.0)
        self._set_period = _spin(6.0, decimals=2, minimum=0.01)
        set_form.addRow("Duration [s]", self._set_duration)
        set_form.addRow("Time step [s]", self._set_dt)
        set_form.addRow("Wave amplitude [m]", self._set_amplitude)
        set_form.addRow("Wave period [s]", self._set_period)
        set_page = QWidget()
        set_page.setLayout(QVBoxLayout())
        set_page.layout().addWidget(set_box)
        set_page.layout().addStretch(1)
        self._stack.addWidget(set_page)

        # result page (read-only)
        res_box = QGroupBox("Result")
        res_form = QFormLayout(res_box)
        self._res_kind = QLabel("-")
        self._res_hash = QLabel("-")
        self._res_hash.setWordWrap(True)
        self._res_hash.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._res_stale = QLabel("-")
        self._res_frames = QLabel("-")
        self._res_note = QLabel("-")
        self._res_note.setWordWrap(True)
        res_form.addRow("Kind", self._res_kind)
        res_form.addRow("Input hash", self._res_hash)
        res_form.addRow("Stale", self._res_stale)
        res_form.addRow("Frames", self._res_frames)
        res_form.addRow("Note", self._res_note)
        res_page = QWidget()
        res_page.setLayout(QVBoxLayout())
        res_page.layout().addWidget(res_box)
        res_page.layout().addStretch(1)
        self._stack.addWidget(res_page)

        # wire edits
        self._body_name.editingFinished.connect(self._emit_body)
        for box in (self._body_ref_x, self._body_ref_y, self._body_ref_z, self._body_mass):
            box.editingFinished.connect(self._emit_body)
        self._body_color.editingFinished.connect(self._emit_body)
        self._eq_waterline.editingFinished.connect(self._emit_equilibrium)
        self._eq_description.editingFinished.connect(self._emit_equilibrium)
        for box in (self._set_duration, self._set_dt, self._set_amplitude, self._set_period):
            box.editingFinished.connect(self._emit_settings)

    # ---------------------------------------------------------------- state

    def set_project(self, project: Optional[ProjectModel]) -> None:
        self._project = project
        self._current_body_id = None
        self._stack.setCurrentIndex(_PAGE_EMPTY)

    def show_empty(self) -> None:
        self._current_body_id = None
        self._stack.setCurrentIndex(_PAGE_EMPTY)

    def show_body(self, body_id: str) -> None:
        if self._project is None:
            return
        body = self._project.body(body_id)
        if body is None:
            return
        self._updating = True
        self._current_body_id = body_id
        self._body_name.setText(body.name)
        self._body_ref_x.setValue(body.reference_position[0])
        self._body_ref_y.setValue(body.reference_position[1])
        self._body_ref_z.setValue(body.reference_position[2])
        self._body_mass.setValue(float(body.mass))
        self._body_color.setText(body.color)
        properties = body.mass_properties
        self._mass_enabled.setChecked(properties is not None)
        for index, edit in enumerate(self._cg_fields):
            edit.setText("" if properties is None else repr(properties.cg_body[index]))
        for row in range(3):
            for col in range(3):
                self._inertia_table.item(row, col).setText(
                    "" if properties is None else repr(properties.inertia_cg_body[row][col]))
        self._toggle_mass_properties(properties is not None)
        self._mass_status.setText("CG/inertia not supplied" if properties is None
                                  else "CG/inertia stored; validate before mechanics use")
        self._stack.setCurrentIndex(_PAGE_BODY)
        self._updating = False

    def _toggle_mass_properties(self, enabled):
        for edit in self._cg_fields:
            edit.setEnabled(enabled)
        self._inertia_table.setEnabled(enabled)
        self._mass_validate.setEnabled(enabled)
        if self._updating or not self._current_body_id:
            return
        if not enabled:
            self._mass_status.setText("CG/inertia not supplied")
            self.body_edited.emit(self._current_body_id, {"mass_properties": None})
        else:
            self._emit_mass_properties()

    def _mass_declaration(self):
        return BodyMassProperties(
            [float(edit.text()) for edit in self._cg_fields],
            [[float(self._inertia_table.item(row, col).text()) for col in range(3)] for row in range(3)])

    def _emit_mass_properties(self, *args):
        if self._updating or not self._current_body_id or not self._mass_enabled.isChecked():
            return
        try:
            properties = self._mass_declaration()
        except ValueError as error:
            self._mass_status.setText(f"Enter all CG and inertia values: {error}")
            return
        self._mass_status.setText("CG/inertia stored; validate before mechanics use")
        self.body_edited.emit(self._current_body_id, {"mass_properties": properties})

    def _validate_mass_properties(self):
        if self._project is None or not self._current_body_id:
            return
        try:
            properties = self._mass_declaration()
            body = self._project.body(self._current_body_id).with_edits(mass_properties=properties)
            candidate = self._project.with_edits(bodies=tuple(
                body if b.body_id == body.body_id else b for b in self._project.bodies))
            self._service.prepare_rigid_body(candidate, body.body_id)
        except (ValueError, MechanicsError) as error:
            self._mass_status.setText(f"Dry mechanics unavailable: {error}")
            return
        self._mass_status.setText("Dry mechanics inputs valid; nonlinear time integration is pending")

    def show_equilibrium(self) -> None:
        if self._project is None:
            return
        self._current_body_id = None
        self._updating = True
        self._eq_waterline.setValue(float(self._project.equilibrium.waterline_z))
        self._eq_description.setText(self._project.equilibrium.description)
        self._eq_source.setText(self._project.equilibrium.source)
        self._stack.setCurrentIndex(_PAGE_EQUILIBRIUM)
        self._updating = False

    def show_settings(self) -> None:
        if self._project is None:
            return
        self._current_body_id = None
        self._updating = True
        settings = self._project.settings
        self._set_duration.setValue(float(settings.duration))
        self._set_dt.setValue(float(settings.dt))
        self._set_amplitude.setValue(float(settings.wave_amplitude))
        self._set_period.setValue(float(settings.wave_period))
        self._stack.setCurrentIndex(_PAGE_SETTINGS)
        self._updating = False

    def show_result(self, result_id: str) -> None:
        if self._project is None:
            return
        result = next((r for r in self._project.results if r.result_id == result_id), None)
        if result is None:
            return
        self._current_body_id = None
        self._res_kind.setText(result.kind)
        self._res_hash.setText(result.input_hash)
        stale = self._service.result_stale(self._project, result)
        self._res_stale.setText("yes" if stale else "no")
        self._res_frames.setText(str(result.frame_count()))
        self._res_note.setText(result.note)
        self._stack.setCurrentIndex(_PAGE_RESULT)

    # ---------------------------------------------------------------- edits

    def _emit_body(self) -> None:
        if self._updating or self._project is None or self._current_body_id is None:
            return
        self.body_edited.emit(
            self._current_body_id,
            {
                "name": self._body_name.text().strip(),
                "reference_position": (
                    self._body_ref_x.value(),
                    self._body_ref_y.value(),
                    self._body_ref_z.value(),
                ),
                "mass": self._body_mass.value(),
                "color": self._body_color.text().strip(),
            },
        )

    def _emit_equilibrium(self) -> None:
        if self._updating or self._project is None:
            return
        self.equilibrium_edited.emit(
            {
                "waterline_z": self._eq_waterline.value(),
                "description": self._eq_description.text(),
            }
        )

    def _emit_settings(self) -> None:
        if self._updating or self._project is None:
            return
        self.settings_edited.emit(
            {
                "duration": self._set_duration.value(),
                "dt": self._set_dt.value(),
                "wave_amplitude": self._set_amplitude.value(),
                "wave_period": self._set_period.value(),
            }
        )



