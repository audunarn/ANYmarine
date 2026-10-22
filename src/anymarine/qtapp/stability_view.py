"""Presentation widgets for the stability screen: hull drawing, tank table, value blocks.

Pure presentation over ``anymarine.stability_model``; nothing here calculates a
draught or touches the study service. Signals carry user intent only.
"""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QSizePolicy, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

LIQUID = {"ballast": QColor("#3b8fd9"), "fresh": QColor("#2fb5a1"), "liquid": QColor("#8e7cc3")}
TANK_WALL = QColor("#5b6e80")
SELECT = QColor("#f59e0b")
WATER = QColor(59, 143, 217, 55)
HULL = QColor("#334c61")
DASH = "—"


def fmt(value, digits=3, unit=""):
    return DASH if value is None else f"{value:.{digits}f}{unit}"


class HullView(QWidget):
    """Profile (top) and plan (bottom) of the box with tanks coloured by fill. Click selects a tank."""
    tankClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = None
        self.derived = None
        self.stale = True
        self.selected = None
        self._hits = []
        self.setMinimumSize(280, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setObjectName("hullView")

    def set_state(self, model, derived, stale, selected):
        self.model, self.derived, self.stale, self.selected = model, derived, stale, selected
        self.update()

    # -- geometry -------------------------------------------------------
    def _frames(self):
        """(profile_rect, plan_rect) in widget pixels with equal x scale."""
        m = self.model
        w, h = self.width() - 24.0, self.height() - 56.0
        scale = max(1e-6, min(w / m.length_m, h / (m.height_m + m.breadth_m)))
        lw = m.length_m * scale
        x0 = (self.width() - lw) / 2.0
        profile = QRectF(x0, 22.0, lw, m.height_m * scale)
        plan = QRectF(x0, profile.bottom() + 28.0, lw, m.breadth_m * scale)
        return profile, plan, scale

    def _tank_rects(self):
        m = self.model
        profile, plan, scale = self._frames()
        out = []
        for t in m.tanks:
            px = profile.left() + (t.cx_m - t.length_m / 2.0 + m.length_m / 2.0) * scale
            side = QRectF(px, profile.bottom() - (t.bottom_z_m + t.height_m) * scale, t.length_m * scale, t.height_m * scale)
            top = QRectF(px, plan.center().y() - (t.cy_m + t.breadth_m / 2.0) * scale, t.length_m * scale, t.breadth_m * scale)
            out.append((t, side, top))
        return out

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self.model is None:
            painter.setPen(QColor("#5b6e80"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No analytical box in these inputs.\nLoad the box example or fix the inputs (Inputs tab).")
            return
        m = self.model
        profile, plan, scale = self._frames()
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.0))
        painter.setFont(small)
        painter.setPen(QColor("#5b6e80"))
        painter.drawText(QPointF(profile.left(), 14), "Profile  (bow →)")
        painter.drawText(QPointF(plan.left(), plan.top() - 8), "Plan  (port ↑)")
        painter.setPen(QPen(HULL, 1.6))
        painter.setBrush(QBrush(QColor("#f7fafc")))
        painter.drawRect(profile)
        painter.drawRect(plan)
        if self.derived is not None and not self.stale:
            draught = max(0.0, min(self.derived.draught_m, m.height_m))
            water = QRectF(profile.left(), profile.bottom() - draught * scale, profile.width(), draught * scale)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(WATER))
            painter.drawRect(water)
            painter.setPen(QPen(QColor("#1d6fb8"), 1.4, Qt.PenStyle.DashLine))
            painter.drawLine(water.topLeft(), water.topRight())
            painter.setPen(QColor("#1d6fb8"))
            painter.drawText(QPointF(water.right() - 92, water.top() - 3), f"T = {self.derived.draught_m:.3f} m")
        self._hits = []
        for tank, side, top in self._tank_rects():
            colour = LIQUID.get(tank.kind, LIQUID["liquid"])
            for rect, vertical in ((side, True), (top, False)):
                painter.setPen(QPen(SELECT if tank.tank_id == self.selected else TANK_WALL,
                                    2.4 if tank.tank_id == self.selected else 1.0))
                painter.setBrush(QBrush(QColor("#ffffff")))
                painter.drawRect(rect)
                fill = QRectF(rect)
                if vertical:
                    fill.setTop(rect.bottom() - rect.height() * tank.fill)
                else:
                    fill.setWidth(rect.width())
                painter.setPen(Qt.PenStyle.NoPen)
                shade = QColor(colour)
                shade.setAlpha(int(70 + 150 * tank.fill))
                painter.setBrush(QBrush(shade))
                if vertical:
                    painter.drawRect(fill)
                else:
                    painter.drawRect(rect)           # plan: full-depth tint scaled by fill
                painter.setPen(QColor("#19344c"))
                if rect.width() > 34 and rect.height() > 14:
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{tank.fill * 100:.0f}%")
                self._hits.append((rect, tank.tank_id))
        cg = m.cg
        if cg is not None:
            gx = profile.left() + (cg[0] + m.length_m / 2.0) * scale
            gy = profile.bottom() - cg[2] * scale
            painter.setPen(QPen(QColor("#c0392b"), 2))
            painter.drawLine(QPointF(gx - 5, gy - 5), QPointF(gx + 5, gy + 5))
            painter.drawLine(QPointF(gx - 5, gy + 5), QPointF(gx + 5, gy - 5))
            painter.drawText(QPointF(gx + 7, gy - 4), "G")
        if self.derived is not None and not self.stale:
            bx = profile.left() + (self.derived.lcb_m + m.length_m / 2.0) * scale
            by = profile.bottom() - self.derived.kb_m * scale
            painter.setPen(QPen(QColor("#1d6fb8"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(bx, by), 4, 4)
            painter.drawText(QPointF(bx + 7, by + 12), "B")

    # -- interaction ------------------------------------------------------
    def _tank_at(self, pos):
        for rect, ident in reversed(self._hits):
            if rect.contains(pos):
                return ident
        return None

    def mousePressEvent(self, event):
        ident = self._tank_at(event.position())
        if ident is not None:
            self.tankClicked.emit(ident)

    def mouseMoveEvent(self, event):
        ident = self._tank_at(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if ident else Qt.CursorShape.ArrowCursor)
        tank = next((t for t in (self.model.tanks if self.model else ()) if t.tank_id == ident), None)
        self.setToolTip("" if tank is None else
                        f"{tank.tank_id}\nfill {tank.fill * 100:.1f} %  ·  {tank.volume_m3:.2f} of {tank.capacity_m3:.2f} m³\n"
                        f"{tank.mass_kg / 1000:.3f} t  ·  click to select")


class ValueBlock(QFrame):
    """A titled group of label/value rows, like the NAPA bottom bar."""

    def __init__(self, title, keys, parent=None):
        super().__init__(parent)
        self.setObjectName("valueBlock")
        self.values = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(1)
        head = QLabel(title.upper())
        head.setObjectName("blockTitle")
        grid.addWidget(head, 0, 0, 1, 2 * ((len(keys) + 2) // 3))
        for i, (key, label) in enumerate(keys):
            col, row = (i // 3) * 2, i % 3 + 1
            name = QLabel(label)
            name.setObjectName("valueName")
            value = QLabel(DASH)
            value.setObjectName("valueText")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(name, row, col)
            grid.addWidget(value, row, col + 1)
            self.values[key] = value

    def set_values(self, mapping, stale_keys=()):
        for key, label in self.values.items():
            label.setText(mapping.get(key, DASH))
            label.setProperty("stale", key in stale_keys)
            label.style().unpolish(label)
            label.style().polish(label)


class TankTable(QTableWidget):
    """Tank list with an editable fill (slider + percent). Emits fillChanged(tank_id, fraction)."""
    fillChanged = Signal(str, float)
    tankSelected = Signal(str)

    HEADERS = ["Tank", "Capacity m³", "Fill", "Fill %", "Mass t", "Density"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.HEADERS), parent)
        self.setObjectName("tankTable")
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.verticalHeader().hide()
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col, width in enumerate((170, 90, 120, 100, 80, 70)):
            self.setColumnWidth(col, width)
        self._rows = {}
        self._signature = None
        self.itemSelectionChanged.connect(self._selection)

    def _selection(self):
        row = self.currentRow()
        for ident, r in self._rows.items():
            if r == row:
                self.tankSelected.emit(ident)

    def select(self, ident):
        row = self._rows.get(ident)
        if row is not None and row != self.currentRow():
            self.blockSignals(True)
            self.selectRow(row)
            self.blockSignals(False)

    def set_tanks(self, tanks):
        signature = tuple(t.tank_id for t in tanks)
        if signature != self._signature:                     # rebuild only when the tank set changes
            self._signature = signature
            self._rows = {t.tank_id: i for i, t in enumerate(tanks)}
            self.setRowCount(len(tanks))
            for i, t in enumerate(tanks):
                for col in (0, 1, 4, 5):
                    item = QTableWidgetItem()
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    if col:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.setItem(i, col, item)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, 1000)
                slider.setObjectName("fillSlider_" + t.tank_id)
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 100.0)
                spin.setDecimals(1)
                spin.setSuffix(" %")
                spin.setObjectName("fillSpin_" + t.tank_id)
                slider.valueChanged.connect(lambda v, ident=t.tank_id, s=spin: self._moved(ident, v / 1000.0, s, True))
                spin.valueChanged.connect(lambda v, ident=t.tank_id, s=slider: self._moved(ident, v / 100.0, s, False))
                self.setCellWidget(i, 2, slider)
                self.setCellWidget(i, 3, spin)
        for i, t in enumerate(tanks):
            self.item(i, 0).setText(t.tank_id)
            self.item(i, 1).setText(f"{t.capacity_m3:.2f}")
            self.item(i, 4).setText(f"{t.mass_kg / 1000:.3f}")
            self.item(i, 5).setText(f"{t.density:.0f}")
            self._sync(self.cellWidget(i, 2), t.fill * 1000.0)
            self._sync(self.cellWidget(i, 3), t.fill * 100.0)

    @staticmethod
    def _sync(widget, value):
        widget.blockSignals(True)
        widget.setValue(round(value) if isinstance(widget, QSlider) else value)
        widget.blockSignals(False)

    def _moved(self, ident, fraction, other, from_slider):
        self._sync(other, fraction * (100.0 if from_slider else 1000.0))
        self.fillChanged.emit(ident, min(1.0, max(0.0, fraction)))
