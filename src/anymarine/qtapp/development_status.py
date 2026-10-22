"""Development status dialog: model-view capability table plus status text.

GUI-thread Qt component for the bundled development status snapshot. It only
renders data loaded through the Qt-free public loader
(:mod:`anymarine.development_status`); it never imports numerical backends
and never runs calculations. The view is a development snapshot, fully
separate from project results and assessment.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..development_status import startup_summary

_COLUMNS = ("Area", "Feature", "Status", "Menu path", "Note")


class DevelopmentStatusTableModel(QAbstractTableModel):
    """Read-only table model over the snapshot's capability entries."""

    def __init__(self, capabilities: list[dict], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._capabilities = list(capabilities)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._capabilities)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(_COLUMNS):
            return _COLUMNS[section]
        return section

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        entry = self._capabilities[index.row()]
        column = index.column()
        if column == 0:
            return entry["area"]
        if column == 1:
            return entry["feature"]
        if column == 2:
            return entry["status"]
        if column == 3:
            return entry.get("menu_path", "")
        return entry["note"]


def _hydrodynamic_status_text(status: dict) -> str:
    hydro = status["hydrodynamic_status"]
    energy = hydro["energy_point"]
    predicates = ", ".join(
        f"{name}: {verdict}" for name, verdict in energy["registered_predicates"].items()
    )
    added = energy["added_mass_counts"]
    damping = energy["radiation_damping_counts"]
    audit = hydro["independent_audit"]
    checker = hydro["checker_mock_tests"]
    lines = [
        "Hydrodynamic development status (dated snapshot, not a qualification claim)",
        "",
        f"Roadmap: {hydro['roadmap_state']}; broader numerical development "
        f"{hydro['broader_numerical_development']}.",
        f"W5 decision: {hydro['w5_decision']}. General numerical admission: "
        f"{str(hydro['general_numerical_admission']).lower()}.",
        "",
        f"Latest energy diagnostic ({energy['label']}): {energy['run_state']}; "
        f"summary status '{energy['summary_status']}' "
        f"(computation {energy['computation_state']}).",
        f"Registered comparison: {energy['registered_comparison']}. "
        f"Predicates - {predicates}.",
        f"Added mass: {added['passed']} passed / {added['failed']} failed. "
        f"Radiation damping: {damping['passed']} passed / {damping['failed']} failed. "
        f"{energy['retained_dofs']}.",
        f"Mean power: candidate {energy['candidate_mean_power_w']:+.16f} W, "
        f"HAMS reference {energy['hams_reference_mean_power_w']:+.16f} W, "
        f"comparison {energy['mean_power_comparison']}.",
        energy["sign_note"],
        "",
        f"Checker mock tests: {checker['recorded_result']} "
        f"({checker['receipt']}; {checker['scope']}).",
        f"Independent stored-array audit: {audit['state']}, "
        f"{audit['checks_passed']}/{audit['checks_total']} consistency checks passed. "
        f"{audit['interpretation']}",
        audit["handoff_note"],
        "",
        f"Next development question: {status['summary']['next_development_question']}",
    ]
    return "\n".join(lines)


class DevelopmentStatusDialog(QDialog):
    """Help > Development status dialog for the bundled snapshot."""

    def __init__(self, status: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Development status - snapshot {status['snapshot_date']}")
        self._status = status

        layout = QVBoxLayout(self)

        summary_label = QLabel(startup_summary(status))
        summary_label.setWordWrap(True)
        layout.addWidget(summary_label)

        scope_label = QLabel(status["summary"]["scope_note"])
        scope_label.setWordWrap(True)
        layout.addWidget(scope_label)

        self._model = DevelopmentStatusTableModel(status["capabilities"], self)
        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        # Area and Status are short: fit them. Feature and Menu path get a fixed readable width; Note takes the rest.
        for column, width in ((1, 230), (3, 210)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(column, width)
        for column in (0, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(len(_COLUMNS) - 1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(True)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        layout.addWidget(self._table, 1)

        # Technical detail stays available but collapsed: the summary above is the plain-language lead.
        self._details_toggle = QToolButton(self)
        self._details_toggle.setText("Technical details (hydrodynamic evidence)")
        self._details_toggle.setCheckable(True)
        self._details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self._details_toggle)
        self._hydro_text = QPlainTextEdit(self)
        self._hydro_text.setReadOnly(True)
        self._hydro_text.setPlainText(_hydrodynamic_status_text(status))
        self._hydro_text.setVisible(False)
        self._details_toggle.toggled.connect(self._toggle_details)
        layout.addWidget(self._hydro_text, 1)

        provenance = status["provenance"]
        provenance_label = QLabel(
            f"{provenance['data_kind']} Public record: {provenance['public_document']}. "
            "Source identifiers: "
            + "; ".join(source["id"] for source in provenance["sources"])
        )
        provenance_label.setWordWrap(True)
        layout.addWidget(provenance_label)

        self.resize(860, 560)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._table.resizeRowsToContents()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._table.resizeRowsToContents()

    def _toggle_details(self, shown: bool) -> None:
        self._hydro_text.setVisible(shown)
        self._details_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
