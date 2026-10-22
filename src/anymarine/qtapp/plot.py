"""Small first-order Qt painter time-series plot.

Values are plotted strictly from stored result arrays; no plotting dependency.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

_UNITS = ("N", "Nm", "m", "rad", "s", "W", "J", "kg", "Pa", "m/s", "rad/s", "m/s2", "rad/s2")
# Channel names whose last word fixes the unit (SI); anything else shows no unit rather than a guess.
_WORD_UNITS = {"energy": "J", "dissipation": "J", "power": "W", "penetration": "m"}


def channel_title(name: str) -> tuple[str, str]:
    """(readable title, unit or "") for a stored channel id such as ``kinetic_energy`` or ``... residual N``."""
    text = str(name).strip()
    unit = ""
    head, _, tail = text.rpartition(" ")
    if head and tail in _UNITS:
        text, unit = head, tail
    if not unit:
        last = text.replace("/", "_").rsplit("_", 1)[-1].split(" ")[-1].lower()
        unit = _WORD_UNITS.get(last, "")
    title = text.replace("_", " ").replace("/", " / ").strip()
    return (title[:1].upper() + title[1:] if title else title), unit


class TimeSeriesPlot(QWidget):
    """Paints one stored channel against time with a frame cursor."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._times: list[float] = []
        self._values: list[float] = []
        self._cursor_index: int = -1
        self._label: str = ""
        self._abscissa="time"
        self.setMinimumHeight(160)

    def set_series(self, label: str, times, values) -> None:
        self._label = str(label)
        self.setToolTip(self._label)
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
        from .rao_panel import _nice_ticks
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor("#ffffff"))
        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF() * 0.85, 6.0))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        title, unit = channel_title(self._label)
        if len(self._times) < 2:
            painter.setPen(QColor("#54647a"))
            painter.drawText(QRectF(8, 8, max(width - 16, 1), max(height - 16, 1)), Qt.AlignmentFlag.AlignCenter, "no series loaded")
            painter.end()
            return

        t_min, t_max = self._times[0], self._times[-1]
        v_min, v_max = min(self._values), max(self._values)
        if v_max == v_min:
            v_min, v_max = v_min - 1.0, v_max + 1.0
        yt = _nice_ticks(v_min, v_max, 4)
        xt = _nice_ticks(t_min, t_max, 5) if t_max > t_min else [t_min]
        y_labels = [f"{v:.4g}" for v in yt]
        left = 12.0 + max(metrics.horizontalAdvance(s) for s in y_labels)
        top = 8.0 + metrics.height() + 4.0          # the title strip sits above the plot, never over the curve
        bottom = 8.0 + 2 * metrics.height()          # tick labels and the axis caption
        plot = QRectF(left, top, max(width - left - 10.0, 1.0), max(height - top - bottom, 1.0))
        span_t = t_max - t_min if t_max != t_min else 1.0
        span_v = v_max - v_min

        def x_of(t: float) -> float:
            return plot.left() + (t - t_min) / span_t * plot.width()

        def y_of(v: float) -> float:
            return plot.bottom() - (v - v_min) / span_v * plot.height()

        # grid and tick labels
        painter.setPen(QPen(QColor("#e3e8ef"), 1))
        for v in yt:
            if v_min <= v <= v_max:
                painter.drawLine(QPointF(plot.left(), y_of(v)), QPointF(plot.right(), y_of(v)))
        for t in xt:
            if t_min <= t <= t_max:
                painter.drawLine(QPointF(x_of(t), plot.top()), QPointF(x_of(t), plot.bottom()))
        if v_min <= 0.0 <= v_max:
            painter.setPen(QPen(QColor("#b9c3d1"), 1))
            painter.drawLine(QPointF(plot.left(), y_of(0.0)), QPointF(plot.right(), y_of(0.0)))
        painter.setPen(QPen(QColor("#8592a3"), 1))
        painter.drawRect(plot)
        painter.setPen(QColor("#54647a"))
        for v, text in zip(yt, y_labels):
            if v_min <= v <= v_max:
                painter.drawText(QPointF(plot.left() - 4 - metrics.horizontalAdvance(text), y_of(v) + metrics.ascent() / 2), text)
        for t in xt:
            if t_min <= t <= t_max:
                text = f"{t:g}"
                painter.drawText(QPointF(x_of(t) - metrics.horizontalAdvance(text) / 2, plot.bottom() + metrics.ascent() + 2), text)
        caption = "stage" if self._abscissa == "stage" else "time [s]"
        painter.drawText(QPointF(plot.right() - metrics.horizontalAdvance(caption), height - 6), caption)

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

        # title strip: channel and unit on the left, the cursor position on the right
        now = self._times[max(self._cursor_index, 0)]
        readout = f"stage {int(now)}" if self._abscissa == "stage" else f"t = {now:.2f} s"
        painter.setPen(QColor("#1f2b3d"))
        label = f"{title} [{unit}]" if unit else title
        room = max(int(plot.width() - metrics.horizontalAdvance(readout) - 12), 20)
        painter.drawText(QPointF(plot.left(), 8.0 + metrics.ascent()),
                         metrics.elidedText(label, Qt.TextElideMode.ElideMiddle, room))
        painter.setPen(QColor("#54647a"))
        painter.drawText(QPointF(plot.right() - metrics.horizontalAdvance(readout), 8.0 + metrics.ascent()), readout)
        painter.end()
