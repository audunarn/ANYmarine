"""RAO-mode view of an external reference comparison (``anymarine_reference_comparison_v1``).

Display only: one line per source in the 3 x 2 RAO graph grid for the chosen heading. The banner states
that this is evidence about external programs, not an ANYmarine RAO case, and that it is unqualified.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFormLayout, QHBoxLayout, QLabel, QRadioButton,
                               QVBoxLayout, QWidget)

from .. import rao_view
from ..reference_comparison import series
from .rao_panel import RaoGrid

BANNER = ('External reference comparison - not an ANYmarine RAO case and not bound to the project. '
          'Development evidence, unqualified.')


class ReferenceComparisonView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setProperty('role', 'section')
        layout.addWidget(self.title)
        self.banner = QLabel(BANNER)
        self.banner.setWordWrap(True)
        self.banner.setObjectName('ReferenceBanner')
        layout.addWidget(self.banner)
        form = QFormLayout()
        self.heading = QComboBox()
        self.x_axis = QComboBox()
        self.x_axis.addItem('Period (s)', 'period')
        self.x_axis.addItem('Frequency (rad/s)', 'frequency')
        form.addRow('Wave heading (deg)', self.heading)
        form.addRow('X axis', self.x_axis)
        layout.addLayout(form)
        display = QHBoxLayout()
        self.display_group = QButtonGroup(self)
        self.display_buttons = {}
        for key, text in (('amplitude', 'Amplitude'), ('phase', 'Phase')):
            button = QRadioButton(text)
            self.display_buttons[key] = button
            self.display_group.addButton(button)
            display.addWidget(button)
        display.addStretch(1)
        self.display_buttons['amplitude'].setChecked(True)
        layout.addLayout(display)
        self.grid = RaoGrid()
        layout.addWidget(self.grid, 1)
        self.sources = QLabel()
        self.sources.setWordWrap(True)
        self.sources.setProperty('role', 'hint')
        layout.addWidget(self.sources)
        self.heading.currentIndexChanged.connect(self.refresh)
        self.x_axis.currentIndexChanged.connect(self.refresh)
        self.display_group.buttonToggled.connect(lambda *_: self.refresh())

    def set_artifact(self, data: dict) -> None:
        self.data = data
        self.title.setText(data['title'].upper())
        self.heading.blockSignals(True)
        self.heading.clear()
        for heading in data['headings_deg']:
            self.heading.addItem(f'{heading:g}', heading)
        self.heading.blockSignals(False)
        lines = []
        for source in data['sources']:
            details = ', '.join(f'{k}: {v}' for k, v in source['details'].items())
            lines.append(f"{source['label']}: {source['program']} {source['version']}, {source['method']}"
                         + (f' ({details})' if details else ''))
        self.sources.setText('\n'.join(lines + list(data['notes'])))
        self.refresh()

    def selection(self) -> dict:
        display = next(k for k, b in self.display_buttons.items() if b.isChecked())
        return dict(heading=self.heading.currentData(), display=display, x_axis=self.x_axis.currentData())

    def refresh(self) -> None:
        if self.data is None or self.heading.currentIndex() < 0:
            self.grid.show_series(rao_view.GRID, {}, '', lambda dof: '')
            return
        chosen = self.selection()
        data = series(self.data, **chosen)
        xlabel = 'Period (s)' if chosen['x_axis'] == 'period' else 'Frequency (rad/s)'
        kind = 'Amplitude' if chosen['display'] == 'amplitude' else 'Phase'
        self.grid.show_series(rao_view.GRID, data, xlabel,
                              lambda dof: f"{kind} ({rao_view.unit(dof, 'displacement', chosen['display'])})")
