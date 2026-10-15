"""Light marine workbench styling for the ANYmarine shell.

Originally adapted from the ANYfem workbench theme
(C:/Github/ANYfem/src/anyfem/ui/qt/theme.py, MPL-2.0, ANYfem authors); revised 2026-10-15 for the
mode-based workspace (main toolbar, mode bar, context panel, card sections). Dimensions are Qt logical
pixels; the viewer canvas paints its own background and is unaffected by the sheet.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget

# Palette (one place; the stylesheet below uses these values)
APP_BG = "#eef2f6"
PANEL_BG = "#ffffff"
SUBTLE_BG = "#f6f8fb"
BORDER = "#dbe2ea"
TEXT = "#1f2b3d"
MUTED = "#6b7889"
ACCENT = "#1d63b8"
ACCENT_HOVER = "#164f96"
ACCENT_SOFT = "#e6f0fb"
WARN_BG = "#fff4dc"
WARN_TEXT = "#8a5a00"

UI_FONT_FAMILIES = ("Segoe UI", "Inter", "Helvetica Neue", "Arial")

# Arrow images for spin boxes, combo boxes and tree branches (Qt style sheets need file URLs).
ASSETS = (Path(__file__).resolve().parent / "assets").as_posix()

WORKBENCH_STYLESHEET = f"""
        QMainWindow, QDialog {{ background: {APP_BG}; }}
        QDockWidget, QWidget#TaskWorkspace, QWidget#ContextPanel, QWidget#Navigator {{ background: {APP_BG}; }}
        QWidget {{ color: {TEXT}; }}
        QMenuBar {{ background: {PANEL_BG}; border: none; padding: 2px 4px; }}
        QMenuBar::item {{ padding: 6px 10px; border-radius: 4px; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_HOVER}; }}
        QMenu {{ background: {PANEL_BG}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}
        QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 4px; }}
        QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
        QToolBar {{ background: {PANEL_BG}; border: none; spacing: 2px; padding: 4px 8px; }}
        QToolBar#MainToolbar {{ border-bottom: 1px solid {BORDER}; }}
        QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 6px 8px; }}
        QToolBar#ModeBar {{ background: {SUBTLE_BG}; border-right: 1px solid {BORDER}; padding: 8px 4px; spacing: 4px; }}
        QToolBar#ModeBar QToolButton {{ border: none; border-radius: 8px; padding: 7px 2px; min-width: 64px;
            color: {MUTED}; font-size: 8.5pt; }}
        QToolBar#ModeBar QToolButton:hover {{ background: #e9eef5; color: {TEXT}; }}
        QToolBar#ModeBar QToolButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}
        QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 5px 7px; }}
        QToolButton:hover {{ background: {ACCENT_SOFT}; }}
        QToolButton:pressed, QToolButton:checked {{ background: #d7e6f8; }}
        QDockWidget {{ titlebar-close-icon: none; }}
        QDockWidget::title {{ background: {APP_BG}; padding: 8px 12px 4px 12px; color: {MUTED};
            font-weight: 600; text-transform: uppercase; }}
        QLabel#WorkspaceHeading, QLabel[role="section"] {{ color: {MUTED}; font-size: 8.5pt; font-weight: 700;
            letter-spacing: 0.4px; padding: 6px 0 2px 0; }}
        QLabel#PanelTitle {{ font-size: 12pt; font-weight: 600; color: {TEXT}; padding: 2px 0 6px 0; }}
        QLabel[role="hint"] {{ color: {MUTED}; }}
        QLabel#EmptyState {{ color: {MUTED}; padding: 18px; }}
        QLabel#DevBadge {{ background: {WARN_BG}; color: {WARN_TEXT}; border-radius: 9px; padding: 2px 10px;
            font-size: 8.5pt; font-weight: 600; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollArea > QWidget > QWidget {{ background: transparent; }}
        QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {PANEL_BG}; top: -1px; }}
        QTabBar::tab {{ background: transparent; padding: 7px 14px; border: none; color: {MUTED}; }}
        QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
        QTabBar::tab:hover {{ color: {TEXT}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
            background: {PANEL_BG}; border: 1px solid #cdd6e1; border-radius: 6px; padding: 4px 7px;
            selection-background-color: {ACCENT}; selection-color: white;
        }}
        QComboBox {{ min-height: 20px; padding-right: 22px; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 22px;
            border: none; }}
        QComboBox::down-arrow {{ image: url({ASSETS}/chevron-down.svg); width: 11px; height: 11px; }}
        QComboBox::down-arrow:disabled {{ image: url({ASSETS}/chevron-down-disabled.svg); }}
        QAbstractSpinBox {{ padding-right: 22px; min-height: 20px; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 20px;
            border: none; border-left: 1px solid {BORDER}; background: {SUBTLE_BG}; }}
        QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; }}
        QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 6px; }}
        QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {ACCENT_SOFT}; }}
        QAbstractSpinBox::up-arrow {{ image: url({ASSETS}/chevron-up.svg); width: 9px; height: 9px; }}
        QAbstractSpinBox::down-arrow {{ image: url({ASSETS}/chevron-down.svg); width: 9px; height: 9px; }}
        QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{
            image: url({ASSETS}/chevron-up-disabled.svg); }}
        QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{
            image: url({ASSETS}/chevron-down-disabled.svg); }}
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {ACCENT}; }}
        QAbstractItemView {{ background: {PANEL_BG}; alternate-background-color: {SUBTLE_BG};
            border: 1px solid {BORDER}; border-radius: 6px;
            selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_HOVER}; }}
        QAbstractItemView::item {{ padding: 4px; }}
        QTreeView {{ border: none; background: {PANEL_BG}; border-radius: 6px; padding: 4px 2px; }}
        QTreeView::branch {{ background: transparent; }}
        QTreeView::branch:has-children:closed {{ image: url({ASSETS}/branch-closed.svg); }}
        QTreeView::branch:has-children:open {{ image: url({ASSETS}/branch-open.svg); }}
        QHeaderView::section {{ background: {SUBTLE_BG}; color: {MUTED}; padding: 6px 8px; border: none;
            border-bottom: 1px solid {BORDER}; font-weight: 600; }}
        QPushButton {{ background: {PANEL_BG}; border: 1px solid #cdd6e1; border-radius: 6px;
            padding: 6px 14px; min-height: 18px; }}
        QPushButton:hover {{ background: {ACCENT_SOFT}; border-color: #9bb9df; }}
        QPushButton:pressed {{ background: #d7e6f8; }}
        QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
        QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
        QPushButton:focus, QToolButton:focus {{ outline: none; border: 1px solid {ACCENT}; }}
        QWidget:disabled {{ color: #9aa5b4; }}
        QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled {{ background: #f1f4f8; border-color: {BORDER}; }}
        QPushButton[primary="true"]:disabled {{ background: #e8edf3; color: #9aa5b4; border-color: {BORDER}; }}
        QCheckBox, QRadioButton {{ spacing: 7px; padding: 2px; }}
        QGroupBox {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px;
            margin-top: 14px; padding: 12px 10px 10px 10px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; font-weight: 700; }}
        QFrame#Transport {{ background: {PANEL_BG}; border-top: 1px solid {BORDER}; }}
        QFrame#Transport QPushButton {{ padding: 4px 12px; }}
        QToolButton#JobsIndicator {{ color: {MUTED}; padding: 1px 8px; }}
        QToolButton#JobsIndicator:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
        QLabel#ReferenceBanner {{ background: {WARN_BG}; color: {WARN_TEXT}; border-radius: 6px; padding: 6px 8px; }}
        QFrame#Card {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 8px; }}
        QStatusBar {{ background: {PANEL_BG}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
        QStatusBar::item {{ border: none; }}
        QToolTip {{ background: {TEXT}; color: white; border: none; padding: 6px 8px; border-radius: 4px; }}
        QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {PANEL_BG}; border: 2px solid {ACCENT}; width: 12px; height: 12px;
            margin: -6px 0; border-radius: 7px; }}
        QSplitter::handle {{ background: {APP_BG}; }}
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: #c6cfdb; border-radius: 4px; min-height: 24px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle:horizontal {{ background: #c6cfdb; border-radius: 4px; min-width: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
"""


def ui_font(base: QFont) -> QFont:
    """The workbench UI font: the first available family of UI_FONT_FAMILIES at 9.5 pt."""
    font = QFont(base)
    font.setFamilies(list(UI_FONT_FAMILIES))
    font.setPointSizeF(9.5)
    return font


def light_palette(base: QPalette) -> QPalette:
    """A light palette so a dark system theme cannot leak into widgets the sheet leaves unstyled."""
    palette = QPalette(base)
    for role, colour in ((QPalette.ColorRole.Window, APP_BG), (QPalette.ColorRole.WindowText, TEXT),
                         (QPalette.ColorRole.Base, PANEL_BG), (QPalette.ColorRole.AlternateBase, SUBTLE_BG),
                         (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, PANEL_BG),
                         (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.ToolTipBase, TEXT),
                         (QPalette.ColorRole.ToolTipText, "#ffffff"), (QPalette.ColorRole.PlaceholderText, MUTED),
                         (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, "#ffffff"),
                         (QPalette.ColorRole.Link, ACCENT)):
        palette.setColor(role, QColor(colour))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#9aa5b4"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#9aa5b4"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#9aa5b4"))
    return palette


def apply_theme(window: QWidget) -> None:
    """Apply the light workbench palette, font and stylesheet to a top-level window."""

    window.setPalette(light_palette(window.palette()))
    window.setFont(ui_font(window.font()))
    window.setStyleSheet(WORKBENCH_STYLESHEET)
