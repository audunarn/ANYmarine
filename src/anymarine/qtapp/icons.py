"""Small line-icon set for the ANYmarine shell (inline SVG, no image files).

Each icon is a 24 x 24 stroke drawing rendered in the normal ink colour and, for the checked
state of mode/toggle buttons, in the accent colour.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

INK = "#3a4a60"
ACCENT = "#1d63b8"
DISABLED = "#a7b1bf"

_PATHS = {
    "new": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7h6l2 2h10v10H3z"/><path d="M3 7V5h6l2 2"/>',
    "save": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4"/><path d="M8 20v-6h8v6"/>',
    "waves": '<path d="M2 9c2.5-3 5-3 7.5 0s5 3 7.5 0 3.5-2 5 0"/><path d="M2 15c2.5-3 5-3 7.5 0s5 3 7.5 0 3.5-2 5 0"/>',
    "run": '<path d="M7 5l12 7-12 7z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "fit": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><rect x="9" y="9" width="6" height="6"/>',
    "iso": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    "top": '<rect x="5" y="5" width="14" height="14" rx="1.5"/><path d="M12 9v6M9 12h6"/>',
    "side": '<path d="M3 15h18"/><path d="M6 15l2-5h8l2 5"/><path d="M3 19c3-2 6-2 9 0s6 2 9 0"/>',
    "model": '<path d="M3 13h18l-3 6H6z"/><path d="M8 13V8h8v5"/><path d="M11 8V5h2v3"/>',
    "rao": '<path d="M4 20V4M4 20h16"/><path d="M6 17c3-1 4-9 6-9s3 6 6 7"/>',
    "assessment": '<rect x="5" y="4" width="14" height="17" rx="1.5"/><path d="M9 4V3h6v1"/><path d="M8.5 12.5l2.2 2.2 4.8-5"/>',
    "results": '<path d="M4 18l4-6 4 3 4-8 4 5"/><path d="M4 21h16"/>',
    "log": '<rect x="4" y="4" width="16" height="16" rx="1.5"/><path d="M8 9h8M8 12h8M8 15h5"/>',
    "play": '<path d="M8 5l11 7-11 7z"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
}


def _svg(name: str, colour: str) -> bytes:
    body = _PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{colour}" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode("utf-8")


def _pixmap(name: str, colour: str, size: int) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, colour)))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QPixmap.fromImage(image)


@lru_cache(maxsize=None)
def icon(name: str) -> QIcon:
    """The named icon with normal, checked (accent) and disabled variants at 16-48 px."""
    if name not in _PATHS:
        raise KeyError(f"unknown icon {name!r}")
    result = QIcon()
    for size in (16, 20, 24, 32, 48):
        result.addPixmap(_pixmap(name, INK, size), QIcon.Mode.Normal, QIcon.State.Off)
        result.addPixmap(_pixmap(name, ACCENT, size), QIcon.Mode.Normal, QIcon.State.On)
        result.addPixmap(_pixmap(name, ACCENT, size), QIcon.Mode.Active, QIcon.State.On)
        result.addPixmap(_pixmap(name, DISABLED, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return result


def names() -> tuple[str, ...]:
    return tuple(_PATHS)
