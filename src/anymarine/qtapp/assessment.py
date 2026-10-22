"""Assessment panel, engineering checks model and workflow dialogs.

Display-only Qt components for the assessment workflow. The panel shows
REAL check values/limits/units/Pass-NoGo-NOTMEASURED status and diagnostics
from child-validated payloads through a QAbstractTableModel/QTableView,
plus the bound identities (input hash, result/case ids, body ids, source
route) and the developmental/unqualified scope. It never computes or
re-validates scientific evidence: reproduction happens in the isolated
child and this panel only renders the validated payload, with detached/
stale labeling when the current project no longer matches the submission.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

CHECK_COLUMNS = ("Check", "Value", "Limit", "Unit", "Status", "Detail")

_PATH_MIN_WIDTH = 320
EMPTY_ASSESSMENT_TEXT = (
    "No assessment yet. Three steps:\n"
    "1. Register case - load the engineering case specification.\n"
    "2. Assess - check a stored result against the case.\n"
    "3. Reopen snapshot - reproduce a saved assessment later."
)


def find_snapshots(root, max_depth: int = 2) -> list[str]:
    """Assessment snapshot directories under ``root`` (a pair has pair.json, a single has cases/ and reports/).

    Looks at most ``max_depth`` levels below ``root`` and does not descend into a snapshot.
    """
    found: list[str] = []
    base = Path(root)

    def scan(directory: Path, depth: int) -> None:
        try:
            entries = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for entry in entries:
            if (entry / "pair.json").is_file() or ((entry / "cases").is_dir() and (entry / "reports").is_dir()):
                found.append(str(entry))
            elif depth < max_depth:
                scan(entry, depth + 1)

    if base.is_dir():
        scan(base, 1)
    return found


def _wide(edit: QLineEdit, placeholder: str) -> QLineEdit:
    edit.setMinimumWidth(_PATH_MIN_WIDTH)
    edit.setPlaceholderText(placeholder)
    return edit


def _ok_button(buttons: QDialogButtonBox, text: str):
    ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    ok.setText(text)
    return ok


SCOPE_NOTE = (
    "Developmental assessment only — unqualified; never a qualified marine "
    "analysis. Evidence reproduced in an isolated child process."
)


def check_status(check: dict) -> str:
    """Pass / NoGo / NOTMEASURED from the actual check payload."""

    if check.get("passed"):
        return "Pass"
    value = check.get("value")
    detail = str(check.get("detail") or "")
    if value is None or "NOTMEASURED" in detail or "NOT measured" in detail:
        return "NOTMEASURED"
    return "NoGo"


class AssessmentChecksModel(QAbstractTableModel):
    """Real engineering checks: name, value, limit, unit, status, detail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checks: list[dict] = []

    def set_checks(self, checks) -> None:
        self.beginResetModel()
        self._checks = [dict(check) for check in checks or []]
        self.endResetModel()

    def checks(self) -> list[dict]:
        return list(self._checks)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._checks)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(CHECK_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return CHECK_COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        check = self._checks[index.row()]
        column = index.column()
        if column == 0:
            return str(check.get("name", ""))
        if column in (1, 2):
            value = check.get("value") if column == 1 else check.get("limit")
            if value is None:
                return "—"
            try:
                return f"{float(value):.6g}"
            except (TypeError, ValueError):
                return str(value)
        if column == 3:
            return str(check.get("unit") or "")
        if column == 4:
            return check_status(check)
        return str(check.get("detail") or "")


def _identity_text(context: Optional[dict], detached: bool) -> str:
    if not isinstance(context, dict):
        context = {}
    parts = []
    if context.get("input_hash"):
        parts.append(f"input {str(context['input_hash'])[:12]}…")
    if context.get("result_id"):
        parts.append(f"result {str(context['result_id'])[:8]}…")
    if context.get("case_id"):
        parts.append(f"case {context['case_id']}")
    if context.get("body_ids"):
        parts.append("bodies " + ", ".join(str(b) for b in context["body_ids"]))
    if context.get("source_route"):
        parts.append(f"source {context['source_route']}")
    text = " | ".join(parts) if parts else "no bound identity"
    if detached:
        text += (
            "  [DETACHED — the current project changed since submission; "
            "this evidence is not the current project's state]"
        )
    return text


class AssessmentPanel(QWidget):
    """Displays child-validated assessment evidence and launches the workflow."""

    register_case_requested = Signal()
    assess_requested = Signal()
    pair_requested = Signal()
    reopen_requested = Signal()
    replay_member_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._payload: Optional[dict] = None
        self._member_role = ""

        layout = QVBoxLayout(self)
        self._verdict_label = QLabel("No assessment yet")
        self._empty_card = QLabel(EMPTY_ASSESSMENT_TEXT)
        self._empty_card.setObjectName("EmptyState")
        self._empty_card.setWordWrap(True)
        self._empty_card.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._scope_label = QLabel(SCOPE_NOTE)
        self._scope_label.setWordWrap(True)
        self._identity_label = QLabel("")
        self._identity_label.setWordWrap(True)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._model = AssessmentChecksModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumHeight(160)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setVisible(False)

        buttons = QGridLayout()
        self._register_button = QPushButton("Register case…")
        self._assess_button = QPushButton("Assess selected result…")
        self._set_primary(self._register_button)
        self._pair_button = QPushButton("Assess coarse/fine pair…")
        self._reopen_button = QPushButton("Reopen snapshot…")
        self._register_button.clicked.connect(self.register_case_requested.emit)
        self._assess_button.clicked.connect(self.assess_requested.emit)
        self._pair_button.clicked.connect(self.pair_requested.emit)
        self._reopen_button.clicked.connect(self.reopen_requested.emit)
        for index, button in enumerate((
            self._register_button,
            self._assess_button,
            self._pair_button,
            self._reopen_button,
        )):
            buttons.addWidget(button, index // 2, index % 2)

        self._member_combo = QComboBox()
        self._member_combo.addItem("coarse")
        self._member_combo.addItem("fine")
        self._member_combo.currentTextChanged.connect(self._member_changed)
        self._member_combo.hide()

        layout.addWidget(self._verdict_label)
        layout.addWidget(self._empty_card, 1)
        layout.addWidget(self._scope_label)
        layout.addWidget(self._identity_label)
        layout.addWidget(self._status_label)
        layout.addWidget(self._table)
        layout.addLayout(buttons)
        layout.addWidget(self._member_combo)

    def _set_primary(self, primary: QPushButton) -> None:
        """One primary button: Register case while empty (step one), Assess once evidence is shown."""
        for button in (self._register_button, self._assess_button):
            button.setProperty("primary", button is primary)
            button.style().unpolish(button)
            button.style().polish(button)

    # ------------------------------------------------------------ payload

    def payload(self) -> Optional[dict]:
        return self._payload

    def set_status(self, text: str) -> None:
        self._status_label.setText(str(text))

    def _member_changed(self, role: str) -> None:
        if self._member_role != role:
            self._member_role = role
            self.replay_member_changed.emit(str(role))

    def display_payload(self, payload: dict, detached: bool, note: Optional[str] = None) -> None:
        """Render one child-validated assessment payload (never a proof).

        ``note`` None keeps the previous status note, so detachment
        refreshes re-render the evidence without clearing the message.
        """

        self._payload = dict(payload)
        self._empty_card.setVisible(False)
        self._table.setVisible(True)
        self._set_primary(self._assess_button)
        kind = str(payload.get("kind"))
        verdict = payload.get("verdict")
        if verdict == "pass":
            verdict_text = "PASS — development admission only (unqualified)"
        elif verdict == "nogo":
            verdict_text = "NoGo — non-success; report preserved"
        else:
            verdict_text = str(verdict)
        if kind in ("assessment_pair", "assessment_adjudicate_pair"):
            verdict_text = f"combined verdict: {verdict_text}"
        self._verdict_label.setText(verdict_text)

        context = payload.get("context")
        if isinstance(context, dict) and kind in (
            "assessment_pair",
            "assessment_adjudicate_pair",
        ):
            lines = []
            for role in ("coarse", "fine"):
                member = context.get(role)
                if isinstance(member, dict):
                    lines.append(f"{role}: {_identity_text(member, detached)}")
            self._identity_label.setText("\n".join(lines))
        else:
            self._identity_label.setText(_identity_text(context, detached))
        if note is None:
            note = self._status_label.text()
        self.set_status(note)

        if kind in ("assessment_pair", "assessment_adjudicate_pair"):
            self._member_combo.show()
            self._show_member_checks(self._member_combo.currentText() or "coarse")
        else:
            self._member_combo.hide()
            report = payload.get("report")
            checks = report.get("checks") if isinstance(report, dict) else None
            self._model.set_checks(checks)

    def _show_member_checks(self, role: str) -> None:
        pair = self._payload.get("pair") if self._payload else None
        checks = None
        if isinstance(pair, dict):
            member = (pair.get("members") or {}).get(role)
            if isinstance(member, dict):
                report = member.get("report")
                if isinstance(report, dict):
                    checks = report.get("checks")
        self._model.set_checks(checks)

    def current_member(self) -> str:
        return str(self._member_combo.currentText() or "coarse")


# ---------------------------------------------------------------- dialogs


class RegisterCaseDialog(QDialog):
    """Explicit case registration: spec file + exclusive artifact target."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Register assessment case")
        form = QFormLayout(self)
        self._spec_edit = _wide(QLineEdit(), "path to the case specification .json")
        spec_browse = QPushButton("Browse…")
        spec_browse.clicked.connect(self._browse_spec)
        spec_row = QHBoxLayout()
        spec_row.addWidget(self._spec_edit)
        spec_row.addWidget(spec_browse)
        spec_host = QWidget()
        spec_host.setLayout(spec_row)
        form.addRow("Case specification (JSON)", spec_host)
        self._target_edit = _wide(QLineEdit(), "new file to create, e.g. case-001.json")
        form.addRow("Case artifact target (new file)", self._target_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = _ok_button(buttons, "Register case")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_spec(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Case specification", "", "Case specification (*.json)"
        )
        if path:
            self._spec_edit.setText(str(path))

    def spec_path(self) -> str:
        return self._spec_edit.text().strip()

    def target_path(self) -> str:
        return self._target_edit.text().strip()


class AssessSingleDialog(QDialog):
    """Explicit single assessment: case artifact + result + optional snapshot."""

    def __init__(self, result_ids, current_result_id="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assess selected result")
        form = QFormLayout(self)
        self._case_edit = _wide(QLineEdit(), "path to the registered case .json")
        case_browse = QPushButton("Browse…")
        case_browse.clicked.connect(self._browse_case)
        case_row = QHBoxLayout()
        case_row.addWidget(self._case_edit)
        case_row.addWidget(case_browse)
        case_host = QWidget()
        case_host.setLayout(case_row)
        form.addRow("Registered case artifact", case_host)
        self._result_combo = QComboBox()
        self._result_combo.setPlaceholderText("no stored results")
        self._result_combo.addItems([str(r) for r in result_ids])
        if self._result_combo.count():
            self._result_combo.setCurrentIndex(0)  # a placeholder would otherwise leave nothing selected
        if current_result_id:
            self._result_combo.setCurrentText(str(current_result_id))
        form.addRow("Stored result", self._result_combo)
        self._snapshot_edit = _wide(QLineEdit(), "optional: new folder to save the assessment in")
        form.addRow("Assessment snapshot target", self._snapshot_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = _ok_button(buttons, "Assess result")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_case(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Registered case artifact", "", "Case artifact (*.json)"
        )
        if path:
            self._case_edit.setText(str(path))

    def case_path(self) -> str:
        return self._case_edit.text().strip()

    def result_id(self) -> str:
        return self._result_combo.currentText()

    def snapshot_target(self) -> str:
        return self._snapshot_edit.text().strip()


class PairMemberWidget(QWidget):
    """One explicit pair member: project root, stored result, case artifact."""

    changed = Signal()

    def __init__(self, role: str, result_loader, parent=None):
        super().__init__(parent)
        self._role = role
        self._loader = result_loader
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self._root_edit = _wide(QLineEdit(), "project folder of this member")
        root_browse = QPushButton("Browse…")
        root_browse.clicked.connect(self._browse_root)
        root_row = QHBoxLayout()
        root_row.addWidget(self._root_edit)
        root_row.addWidget(root_browse)
        root_host = QWidget()
        root_host.setLayout(root_row)
        form.addRow("Project root", root_host)
        self._result_combo = QComboBox()
        self._result_combo.setPlaceholderText("choose a project root first")
        form.addRow("Stored result", self._result_combo)
        self._case_edit = _wide(QLineEdit(), "path to the registered case .json")
        case_browse = QPushButton("Browse…")
        case_browse.clicked.connect(self._browse_case)
        case_row = QHBoxLayout()
        case_row.addWidget(self._case_edit)
        case_row.addWidget(case_browse)
        case_host = QWidget()
        case_host.setLayout(case_row)
        form.addRow("Registered case artifact", case_host)
        self._message = QLabel("")
        self._message.setWordWrap(True)
        form.addRow(self._message)
        self._root_edit.textChanged.connect(lambda *_: self.changed.emit())
        self._case_edit.textChanged.connect(lambda *_: self.changed.emit())
        self._result_combo.currentTextChanged.connect(lambda *_: self.changed.emit())

    def is_complete(self) -> bool:
        return bool(self.project_root() and self.result_id() and self.case_path())

    def _browse_root(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, f"{self._role} project root"
        )
        if path:
            self._root_edit.setText(str(path))
            self.reload_results()

    def _browse_case(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"{self._role} case artifact", "", "Case artifact (*.json)"
        )
        if path:
            self._case_edit.setText(str(path))

    def reload_results(self) -> None:
        """Populate the result combo from the chosen project (public load)."""

        self._result_combo.clear()
        self._message.setText("")
        root = self._root_edit.text().strip()
        if not root:
            return
        try:
            result_ids = self._loader(root)
        except Exception as error:  # noqa: BLE001 - dialog-level message only
            self._message.setText(f"cannot load project: {error}")
            return
        self._result_combo.addItems([str(r) for r in result_ids])
        if self._result_combo.count():
            self._result_combo.setCurrentIndex(0)

    def project_root(self) -> str:
        return self._root_edit.text().strip()

    def result_id(self) -> str:
        return self._result_combo.currentText()

    def case_path(self) -> str:
        return self._case_edit.text().strip()


class PairDialog(QDialog):
    """Explicit coarse/fine pair assessment with an exclusive snapshot target."""

    def __init__(self, result_loader, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assess coarse/fine pair")
        layout = QVBoxLayout(self)
        self._coarse = PairMemberWidget("coarse", result_loader)
        self._fine = PairMemberWidget("fine", result_loader)
        layout.addWidget(QLabel("Coarse member"))
        layout.addWidget(self._coarse)
        layout.addWidget(QLabel("Fine member"))
        layout.addWidget(self._fine)
        form = QFormLayout()
        self._out_edit = _wide(QLineEdit(), "new folder to save the paired assessment in")
        form.addRow("Paired snapshot target", self._out_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = _ok_button(buttons, "Assess pair")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def coarse(self) -> PairMemberWidget:
        return self._coarse

    def fine(self) -> PairMemberWidget:
        return self._fine

    def out_root(self) -> str:
        return self._out_edit.text().strip()


class ReopenSnapshotDialog(QDialog):
    """Reopen a single or paired assessment snapshot for child reproduction."""

    def __init__(self, parent=None, project_root=None):
        super().__init__(parent)
        self.setWindowTitle("Reopen assessment snapshot")
        form = QFormLayout(self)
        self._root_edit = _wide(QLineEdit(), "snapshot folder")
        if project_root is None:
            project_root = getattr(parent, "_project_root", None)
        self._snapshots = QListWidget()
        self._snapshots.setMinimumHeight(90)
        if project_root is not None:
            for path in find_snapshots(project_root):
                self._snapshots.addItem(path)
        if self._snapshots.count():
            form.addRow("Snapshots in this project", self._snapshots)
            self._root_edit.setPlaceholderText("snapshot folder (choose one above or browse)")
            self._snapshots.currentTextChanged.connect(lambda text: text and self._root_edit.setText(text))
        else:
            self._snapshots.hide()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self._root_edit)
        row.addWidget(browse)
        host = QWidget()
        host.setLayout(row)
        form.addRow("Snapshot root", host)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = _ok_button(buttons, "Reopen snapshot")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Assessment snapshot root")
        if path:
            self._root_edit.setText(str(path))

    def snapshot_root(self) -> str:
        return self._root_edit.text().strip()
