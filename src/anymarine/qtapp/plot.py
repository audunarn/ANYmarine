"""Small first-order Qt painter time-series plot.

Values are plotted strictly from stored result arrays; no plotting dependency.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class TimeSeriesPlot(QWidget):
    """Paints one stored channel against time with a frame cursor."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._times: list[float] = []
        self._values: list[float] = []
        self._cursor_index: int = -1
        self._label: str = ""
        self._abscissa="time"
        self.setMinimumHeight(120)

    def set_series(self, label: str, times, values) -> None:
        self._label = str(label)
        self._times = [float(t) for t in times]
        self._values = [float(v) for v in values]
        self._cursor_index = min(max(self._cursor_index, 0), max(len(self._times) - 1, 0))
        self.update()

    def set_abscissa(self,kind):
        self._abscissa=kind
        self.update()

    def set_cursor(self, index: int) -> None:
        if not self._times:
            return
        self._cursor_index = max(0, min(index, len(self._times) - 1))
        self.update()

    # read-only accessors (tests and callers must rely on stored arrays)

    def series_times(self) -> tuple[float, ...]:
        return tuple(self._times)

    def series_values(self) -> tuple[float, ...]:
        return tuple(self._values)

    def cursor_index(self) -> int:
        return self._cursor_index

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor("#ffffff"))
        margin = 8.0
        plot = QRectF(margin, margin, max(width - 2 * margin, 1.0), max(height - 2 * margin, 1.0))
        if len(self._times) < 2:
            painter.setPen(QColor("#54647a"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "no series loaded")
            painter.end()
            return

        t_min, t_max = self._times[0], self._times[-1]
        v_min, v_max = min(self._values), max(self._values)
        if v_max == v_min:
            v_min, v_max = v_min - 1.0, v_max + 1.0
        span_t = t_max - t_min if t_max != t_min else 1.0
        span_v = v_max - v_min

        def x_of(t: float) -> float:
            return plot.left() + (t - t_min) / span_t * plot.width()

        def y_of(v: float) -> float:
            return plot.bottom() - (v - v_min) / span_v * plot.height()

        # zero axis when in range
        painter.setPen(QPen(QColor("#dce2eb"), 1))
        if v_min <= 0.0 <= v_max:
            painter.drawLine(QPointF(plot.left(), y_of(0.0)), QPointF(plot.right(), y_of(0.0)))

        # series polyline from stored values only
        painter.setPen(QPen(QColor("#245fab"), 2))
        points = [QPointF(x_of(t), y_of(v)) for t, v in zip(self._times, self._values)]
        for a, b in zip(points, points[1:]):
            painter.drawLine(a, b)

        # frame cursor
        if 0 <= self._cursor_index < len(points):
            cursor = points[self._cursor_index]
            painter.setPen(QPen(QColor("#b56b17"), 1))
            painter.drawLine(QPointF(cursor.x(), plot.top()), QPointF(cursor.x(), plot.bottom()))
            painter.setBrush(QColor("#b56b17"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(cursor, 3.5, 3.5)

        painter.setPen(QColor("#54647a"))
        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF() * 0.8, 6.0))
        painter.setFont(font)
        painter.drawText(
            QRectF(plot.left(), plot.top(), plot.width(), 14.0),
            Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.drawText(
            QRectF(plot.left(), plot.bottom() - 14.0, plot.width(), 14.0),
            Qt.AlignmentFlag.AlignRight,
            f"stage {int(self._times[max(self._cursor_index,0)])}" if self._abscissa=="stage" else f"t={self._times[max(self._cursor_index, 0)]:.2f}s",
        )
        painter.end()

