"""QTreeView model over the ProjectModel (read-only tree; edits go via details)."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..domain import ProjectModel
from ..services import ApplicationService


class _Node:
    def __init__(self, kind: str, label: str, payload=None, parent: "_Node | None" = None):
        self.kind = kind
        self.label = label
        self.payload = payload
        self.parent = parent
        self.children: list["_Node"] = []
        if parent is not None:
            parent.children.append(self)

    def row(self) -> int:
        return 0 if self.parent is None else self.parent.children.index(self)


class ProjectTreeModel(QAbstractItemModel):
    """Sections: Bodies, Equilibrium, Settings, Results (with stale flags)."""

    def __init__(self, service: ApplicationService, parent=None):
        super().__init__(parent)
        self._service = service
        self._project: Optional[ProjectModel] = None
        self._root = _Node("root", "root")

    # ------------------------------------------------------------------ data

    def set_project(self, project: ProjectModel | None) -> None:
        self.beginResetModel()
        self._project = project
        self._root = _Node("root", "root")
        if project is not None:
            bodies = _Node("section", "Bodies", None, self._root)
            for body in project.bodies:
                owner = _Node("body", body.name, body.body_id, bodies)
                for surface in body.hydro_surfaces:
                    s = surface.semantics
                    _Node('surface', f'{s.surface_id}: {s.surface_kind} / {s.primary_hydrodynamics} / drag {s.viscous_correction}',
                          (body.body_id, s.surface_id), owner)
            if project.native_inputs is not None and project.native_inputs.morison is not None:
                members = _Node('section', 'Morison members', None, self._root)
                for member in project.native_inputs.morison.data['members']:
                    _Node('morison-member', member['member_id'], member['member_id'], members)
            equilibrium = _Node("section", "Equilibrium", None, self._root)
            _Node("equilibrium", "Metadata", "equilibrium", equilibrium)
            settings = _Node("section", "Settings", None, self._root)
            _Node("settings", "Analysis", "settings", settings)
            results = _Node("section", "Results", None, self._root)
            stale = set(self._service.stale_result_ids(project)) if project else set()
            for result in project.results:
                flag = " [STALE]" if result.result_id in stale else ""
                _Node("result", f"{result.kind}{flag}", result.result_id, results)
        self.endResetModel()

    def node_for_index(self, index: QModelIndex) -> Optional[_Node]:
        if not index.isValid():
            return None
        return index.internalPointer()

    # ------------------------------------------------------------- Qt model

    def index(self, row: int, column: int, parent=QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        parent_node = parent.internalPointer() if parent.isValid() else self._root
        child = parent_node.children[row]
        return self.createIndex(row, column, child)

    def parent(self, index: QModelIndex) -> QModelIndex:  # noqa: A003
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node is None or node.parent is None or node.parent is self._root:
            return QModelIndex()
        return self.createIndex(node.parent.row(), 0, node.parent)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        if not parent.isValid():
            return len(self._root.children)
        node = parent.internalPointer()
        return len(node.children) if node is not None else 0

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 1

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if section == 0 and orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return "Marine project"
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: A003
        if not index.isValid():
            return None
        node = index.internalPointer()
        if node is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return node.label
        if role == Qt.ItemDataRole.ForegroundRole and node.kind == "result":
            if "[STALE]" in node.label:
                return QColor("#b45309")
        if role == Qt.ItemDataRole.UserRole:
            return node
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
