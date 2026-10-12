"""RAO dock: register cases, compute FD tables and TD campaigns, view verified results.

All numerical work runs in a child process through the reviewed headless CLI
(``rao-prepare``, ``rao-table``, ``rao-run``); the GUI thread only starts the
process, streams its progress and displays artifacts whose table/report hashes it
re-verifies. Development evidence only; never a qualification.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QProcess, QProcessEnvironment, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)


class RaoResultError(ValueError):
    """A results directory that is incomplete or fails its hash verification."""


def load_results(directory):
    """Read ``rao_table.json`` (+ optional ``rao_report.json``) and verify their hashes."""
    from ..rao_report import _hash
    root = Path(directory)
    try:
        table = json.loads((root / 'rao_table.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RaoResultError(f'no readable rao_table.json in {root}: {error}') from error
    if table.get('artifact') != 'anymarine_rao_table' or _hash(table) != table.get('table_hash'):
        raise RaoResultError('rao_table.json fails its hash verification')
    report = None
    if (root / 'rao_report.json').exists():
        report = json.loads((root / 'rao_report.json').read_text(encoding='utf-8'))
        if (report.get('artifact') != 'anymarine_rao_report' or _hash(report) != report.get('report_hash')
                or report.get('table_hash') != table['table_hash'] or report.get('case_hash') != table['case_hash']):
            raise RaoResultError('rao_report.json fails its hash/table/case verification')
    return table, report


class RaoPlot(QWidget):
    """Amplitude (top) and phase (bottom) against omega; FD lines, TD markers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self._lines, self._markers, self._title = [], [], ''

    def set_data(self, title, lines, markers):
        """``lines``: [(label, omegas, complex values)], ``markers``: [(label, omega, complex)]."""
        self._title, self._lines, self._markers = title, list(lines), list(markers)
        self.update()

    def series(self):
        return list(self._lines), list(self._markers)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#ffffff'))
        painter.setPen(QColor('#202020'))
        painter.setFont(QFont(painter.font().family(), 8))
        painter.drawText(6, 12, self._title or 'No RAO data')
        omegas = [w for _, ws, _ in self._lines for w in ws] + [w for _, w, _ in self._markers]
        if not omegas:
            return
        lo, hi = min(omegas), max(omegas)
        pad = 0.1 * (hi - lo) if hi > lo else 0.5          # a single frequency sits mid-panel
        lo, hi = lo - pad, hi + pad
        width, height = self.width(), self.height()
        panels = ((18, height / 2 - 6, 'amplitude'), (height / 2 + 6, height - 8, 'phase'))
        amps = [abs(v) for _, _, vs in self._lines for v in vs] + [abs(v) for *_, v in self._markers]
        top = max(amps) if amps and max(amps) > 0 else 1.0
        colors = ['#1f6fb5', '#d1801f', '#3a9a3a', '#9a3a9a']
        for y0, y1, kind in panels:
            painter.setPen(QPen(QColor('#c8c8c8')))
            painter.drawRect(40, int(y0), int(width - 48), int(y1 - y0))
            painter.setPen(QColor('#505050'))
            painter.drawText(2, int(y0) + 10, 'amp' if kind == 'amplitude' else 'deg')
            x = lambda w: 40 + (w - lo) / (hi - lo) * (width - 48)
            y = (lambda v: y1 - abs(v) / top * (y1 - y0)) if kind == 'amplitude' else \
                (lambda v: y0 + (180.0 - math.degrees(math.atan2(v.imag, v.real))) / 360.0 * (y1 - y0))
            for i, (_, ws, vs) in enumerate(self._lines):
                painter.setPen(QPen(QColor(colors[i % len(colors)]), 1.6))
                points = [QPointF(x(w), y(v)) for w, v in zip(ws, vs)]
                for a, b in zip(points, points[1:]):
                    painter.drawLine(a, b)
                for pnt in points:
                    painter.drawEllipse(pnt, 2.0, 2.0)
            painter.setPen(QPen(QColor('#c0392b'), 1.4))
            for _, w, v in self._markers:
                pnt = QPointF(x(w), y(v))
                painter.drawLine(pnt + QPointF(-4, -4), pnt + QPointF(4, 4))
                painter.drawLine(pnt + QPointF(-4, 4), pnt + QPointF(4, -4))


class RaoPanel(QWidget):
    """Dock content; ``project_root`` and ``project_input_hash`` come from the window."""

    results_loaded = Signal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.process = None
        self.table = self.report = None
        self._pending_open = None
        layout = QVBoxLayout(self)
        self.register_button = QPushButton('Register case…')
        self.table_button = QPushButton('FD table')
        self.run_button = QPushButton('Run TD campaign')
        self.open_button = QPushButton('Open results…')
        self.cancel_button = QPushButton('Cancel')
        for row in ((self.register_button, self.table_button, self.run_button),
                    (self.open_button, self.cancel_button)):
            buttons = QHBoxLayout()
            for b in row:
                buttons.addWidget(b)
            layout.addLayout(buttons)
        self.case_label = QLabel('No RAO case. Register one from a JSON specification (see docs/RAO_REPORTING.md).')
        self.case_label.setWordWrap(True)
        layout.addWidget(self.case_label)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.dof = QComboBox()
        self.omega = QComboBox()
        for label, combo in (('DOF', self.dof), ('omega [rad/s]', self.omega)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(combo, 1)
            layout.addLayout(row)
        self.plot = RaoPlot()
        layout.addWidget(self.plot, 2)
        self.forces = QTableWidget(0, 4)
        self.forces.setHorizontalHeaderLabels(['contribution', '|F| per m', 'phase deg', 'mean power W'])
        layout.addWidget(self.forces, 1)
        self.checks = QTableWidget(0, 4)
        self.checks.setHorizontalHeaderLabels(['check', 'value', 'limit', 'passed'])
        layout.addWidget(self.checks, 1)
        self.note = QLabel('RAO reporting is development evidence only (docs/RAO_REPORTING.md); unqualified.')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.case_path = None
        self.register_button.clicked.connect(self.register_case)
        self.table_button.clicked.connect(lambda: self.start('rao-table'))
        self.run_button.clicked.connect(lambda: self.start('rao-run'))
        self.open_button.clicked.connect(self.open_results_dialog)
        self.cancel_button.clicked.connect(self.cancel)
        self.dof.currentIndexChanged.connect(self.refresh)
        self.omega.currentIndexChanged.connect(self.refresh)
        self._enable()

    # ------------------------------------------------------------- processes
    def _root(self):
        root = getattr(self.window, '_project_root', None)
        if root is None:
            raise RuntimeError('Save the project first: RAO artifacts live in <project>/rao/')
        return Path(root)

    def _enable(self):
        busy = self.process is not None
        for b in (self.register_button, self.open_button):
            b.setEnabled(not busy)
        for b in (self.table_button, self.run_button):
            b.setEnabled(not busy and self.case_path is not None)
        self.cancel_button.setEnabled(busy)

    def register_case(self, spec_path=None):
        try:
            root = self._root()
            if not spec_path:
                spec_path, _ = QFileDialog.getOpenFileName(self, 'RAO case specification (JSON)', str(root), 'JSON (*.json)')
            if not spec_path:
                return
            case_id = json.loads(Path(spec_path).read_text(encoding='utf-8'))['case_id']
            target = root / 'rao' / 'cases' / f"{case_id}-{_stamp()}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            self._pending_case = target
            self._launch(['rao-prepare', '--project', str(root), '--spec', str(spec_path), '--out', str(target)])
        except (RuntimeError, OSError, ValueError, KeyError) as error:
            self.status.setText(f'Register refused: {error}')

    def start(self, command):
        try:
            root = self._root()
            if self.case_path is None:
                raise RuntimeError('register or select an RAO case first')
            out = root / 'rao' / f"{'table' if command == 'rao-table' else 'run'}-{_stamp()}"
            self._pending_open = out
            self._launch([command, '--project', str(root), '--case', str(self.case_path), '--out-dir', str(out)])
        except (RuntimeError, OSError) as error:
            self.status.setText(f'{command} refused: {error}')

    def _launch(self, arguments):
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments(['-X', 'utf8', '-m', 'anymarine.cli', *arguments])
        process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._output)
        process.finished.connect(self._finished)
        self.process = process
        self.status.setText(f'Running {arguments[0]} in a child process…')
        self._enable()
        process.start()

    def _output(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', 'replace').strip()
        if text:
            self.status.setText(text.splitlines()[-1])

    def _finished(self, code, status):
        command = self.process.arguments()[4]   # after -X utf8 -m anymarine.cli
        self._output()
        self.process = None
        self._enable()
        if code not in (0, 2):
            self.status.setText(f'{command} failed (exit {code}); evidence kept under the output directory. '
                                + self.status.text())
            self._pending_open = None
            return
        if command == 'rao-prepare':
            self.case_path = self._pending_case
            self.case_label.setText(f'Case: {self.case_path.name}')
            self._enable()
        elif self._pending_open is not None:
            self.open_results(self._pending_open)
            self._pending_open = None

    def cancel(self):
        if self.process is not None:
            self.process.kill()
            self.status.setText('Cancelled; partial evidence stays in the output directory.')

    # --------------------------------------------------------------- results
    def open_results_dialog(self):
        start = str(getattr(self.window, '_project_root', '') or '')
        directory = QFileDialog.getExistingDirectory(self, 'Open RAO results directory', start)
        if directory:
            self.open_results(directory)

    def open_results(self, directory):
        try:
            self.table, self.report = load_results(directory)
        except RaoResultError as error:
            self.status.setText(str(error))
            return
        current = getattr(self.window, '_project', None)
        detached = ''
        if current is not None:
            from ..hashing import input_hash
            if input_hash(current) != self.table['project_input_hash']:
                detached = ' DETACHED: produced from different project inputs.'
        verdict = 'FD table only' if self.report is None else (
            f"category {self.report['category']}, verdict {self.report['verdict'].upper()}"
            + (f", demoted: {'; '.join(self.report['demoted'])}" if self.report.get('demoted') else ''))
        self.case_label.setText(f"Results {Path(directory).name}: {verdict}.{detached}")
        self.dof.blockSignals(True)
        self.omega.blockSignals(True)
        self.dof.clear()
        self.dof.addItems(self.table['dof_keys'])
        self.omega.clear()
        self.omega.addItems([f'{w:g}' for w in sorted({e['omega'] for e in self.table['entries']})])
        self.dof.blockSignals(False)
        self.omega.blockSignals(False)
        self.refresh()
        self._fill_checks()
        self.results_loaded.emit(str(directory))

    def refresh(self):
        if self.table is None or self.dof.currentIndex() < 0:
            return
        k = self.dof.currentIndex()
        lines = {}
        for e in self.table['entries']:
            label = e['source'] + ('' if e.get('amplitude') is None else f" a={e['amplitude']:g}")
            lines.setdefault(label, []).append((e['omega'], complex(*e['rao'][k])))
        series = [(label, [w for w, _ in pts], [v for _, v in pts]) for label, pts in lines.items()]
        markers = []
        if self.report is not None:
            for run in self.report['runs']:
                markers.append((f"TD a={run['amplitude']:g}", run['omega'], complex(*run['rao'][k])))
        self.plot.set_data(f"{self.table['dof_keys'][k]}: RAO amplitude and phase (FD lines, TD x)", series, markers)
        self._fill_forces()

    def _fill_forces(self):
        omega = float(self.omega.currentText()) if self.omega.currentText() else None
        k = self.dof.currentIndex()
        entry = next((e for e in self.table['entries'] if e['omega'] == omega), None)
        rows = [] if entry is None else sorted(entry['forces'].items())
        self.forces.setRowCount(len(rows))
        for i, (cid, values) in enumerate(rows):
            z = complex(*values[k])
            for j, text in enumerate((cid, f'{abs(z):.4g}', f'{math.degrees(math.atan2(z.imag, z.real)):.1f}',
                                      f"{entry['power_fd'].get(cid, 0.0):.4g}")):
                self.forces.setItem(i, j, QTableWidgetItem(text))

    def _fill_checks(self):
        checks = [] if self.report is None else self.report['checks']
        self.checks.setRowCount(len(checks))
        for i, c in enumerate(checks):
            for j, text in enumerate((c['name'], f"{c['value']:.4g}", f"{c['limit']:.4g}", 'yes' if c['passed'] else 'NO')):
                item = QTableWidgetItem(text)
                if not c['passed']:
                    item.setForeground(QColor('#c0392b'))
                self.checks.setItem(i, j, item)


def _stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
