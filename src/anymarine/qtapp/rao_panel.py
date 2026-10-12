"""RAO dock: register cases, compute FD tables and TD campaigns, view verified results.

All numerical work runs in a child process through the reviewed headless CLI
(``rao-prepare``, ``rao-table``, ``rao-run``) with the parent's import path, so the
child runs the same code; the GUI thread only starts the process, streams its
progress and displays artifacts after verifying the case artifact, the table and
report hashes and their bindings. Development evidence only; never a qualification.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QPointF, QProcess, QProcessEnvironment, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

_TABLE_KEYS = ('artifact', 'case_hash', 'project_input_hash', 'dof_keys', 'entries', 'table_hash')
_REPORT_KEYS = ('artifact', 'case_hash', 'table_hash', 'category', 'checks', 'runs', 'verdict', 'report_hash')
_SUCCESS = {'rao-prepare': (0,), 'rao-table': (0,), 'rao-run': (0, 2)}   # 2 = nogo, a valid outcome


class RaoResultError(ValueError):
    """A results directory that is incomplete or fails its verification."""


def _read_json(path, what):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RaoResultError(f'{what} is not readable JSON: {error}') from error
    if not isinstance(data, dict):
        raise RaoResultError(f'{what} is not a JSON object')
    return data


def load_results(directory):
    """Verified ``(case, table, report or None)`` of an RAO output directory.

    The directory must hold the case artifact the CLI copied there (``case.json``,
    re-validated against its own hash), a ``rao_table.json`` whose hash verifies and
    that is bound to that case and its project inputs, and optionally a
    ``rao_report.json`` whose hash (which covers its table and case hashes) verifies.
    """
    from ..rao_case import RaoCaseError, read_case_artifact
    from ..rao_report import _hash
    root = Path(directory)
    try:
        case = read_case_artifact(root / 'case.json')
    except RaoCaseError as error:
        raise RaoResultError(f'the case artifact does not verify: {error}') from error
    table = _read_json(root / 'rao_table.json', 'rao_table.json')
    if any(k not in table for k in _TABLE_KEYS) or table['artifact'] != 'anymarine_rao_table':
        raise RaoResultError('rao_table.json lacks required fields')
    if _hash(table) != table['table_hash']:
        raise RaoResultError('rao_table.json fails its hash verification')
    if table['case_hash'] != case.case_hash or table['project_input_hash'] != case.project_input_hash:
        raise RaoResultError('rao_table.json is not bound to this case')
    if not isinstance(table['entries'], list) or not all(
            isinstance(e, dict) and len(e.get('rao', ())) == len(table['dof_keys']) for e in table['entries']):
        raise RaoResultError('rao_table.json entries are malformed')
    report = None
    if (root / 'rao_report.json').exists():
        report = _read_json(root / 'rao_report.json', 'rao_report.json')
        if any(k not in report for k in _REPORT_KEYS) or report['artifact'] != 'anymarine_rao_report':
            raise RaoResultError('rao_report.json lacks required fields')
        if _hash(report) != report['report_hash']:
            raise RaoResultError('rao_report.json fails its hash verification')
        if report['table_hash'] != table['table_hash'] or report['case_hash'] != case.case_hash:
            raise RaoResultError('rao_report.json is not bound to this table and case')
        if not all(isinstance(r, dict) and len(r.get('rao', ())) == len(table['dof_keys']) for r in report['runs']):
            raise RaoResultError('rao_report.json runs are malformed')
    return case, table, report


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
    """Dock content; the window supplies ``_project_root``, ``_project`` and ``_dirty``."""

    results_loaded = Signal(str)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.process = None
        self.case = self.table = self.report = None
        self.case_path = None
        self._pending_case = self._pending_open = None
        self._command = None
        self._directory = None
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
        self.point = QComboBox()        # one exact table entry: omega, heading, source, amplitude
        for label, combo in (('DOF', self.dof), ('Point', self.point)):
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
        self.register_button.clicked.connect(lambda: self.register_case())
        self.table_button.clicked.connect(lambda: self.start('rao-table'))
        self.run_button.clicked.connect(lambda: self.start('rao-run'))
        self.open_button.clicked.connect(self.open_results_dialog)
        self.cancel_button.clicked.connect(self.cancel)
        self.dof.currentIndexChanged.connect(self.refresh)
        self.point.currentIndexChanged.connect(self.refresh)
        self._enable()

    @property
    def busy(self):
        return self.process is not None

    # ------------------------------------------------------------- processes
    def _root(self):
        root = getattr(self.window, '_project_root', None)
        if root is None:
            raise RuntimeError('Save the project first: RAO artifacts live in <project>/rao/')
        if getattr(self.window, '_dirty', False):
            raise RuntimeError('Save the project first: the CLI works on the saved project inputs')
        return Path(root)

    def _enable(self):
        for b in (self.register_button, self.open_button):
            b.setEnabled(not self.busy)
        for b in (self.table_button, self.run_button):
            b.setEnabled(not self.busy and self.case_path is not None)
        self.cancel_button.setEnabled(self.busy)

    def register_case(self, spec_path=None):
        try:
            root = self._root()
            if not spec_path:
                spec_path, _ = QFileDialog.getOpenFileName(self, 'RAO case specification (JSON)', str(root),
                                                           'JSON (*.json)')
            if not spec_path:
                return
            case_id = str(json.loads(Path(spec_path).read_text(encoding='utf-8'))['case_id'])
            if not case_id or not all(c.isalnum() or c in '._-' for c in case_id) or case_id[0] in '.-':
                raise ValueError('case_id must use letters, digits, ".", "_" or "-" only')
            cases = (root / 'rao' / 'cases').resolve()
            target = (cases / f'{case_id}-{_stamp()}.json').resolve()
            if target.parent != cases:
                raise ValueError('case artifacts stay inside <project>/rao/cases')
            cases.mkdir(parents=True, exist_ok=True)
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
        environment = QProcessEnvironment.systemEnvironment()
        # The child must import exactly what this process imported (a source checkout
        # started through run_gui.py extends sys.path without PYTHONPATH).
        paths = [p for p in sys.path if p and os.path.isdir(p)]
        existing = environment.value('PYTHONPATH', '')
        environment.insert('PYTHONPATH', os.pathsep.join(paths + ([existing] if existing else [])))
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._output)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self.process, self._command = process, arguments[0]
        self.status.setText(f'Running {arguments[0]} in a child process…')
        self._enable()
        process.start()

    def _output(self):
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8', 'replace').strip()
        if text:
            self.status.setText(text.splitlines()[-1])

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.process is not None:
            self.process = None
            self._pending_case = self._pending_open = None
            self.status.setText(f'{self._command} could not start: {error.name}')
            self._enable()

    def _finished(self, code, status):
        if self.process is None:
            return
        command = self._command
        self._output()
        self.process = None
        ok = status == QProcess.ExitStatus.NormalExit and code in _SUCCESS.get(command, (0,))
        if ok and command == 'rao-prepare' and not (self._pending_case and self._pending_case.exists()):
            ok = False
        if not ok:
            self.status.setText(f'{command} failed (exit {code}, {status.name}); any evidence stays in its '
                                f'output directory. {self.status.text()}')
            self._pending_case = self._pending_open = None
            self._enable()
            return
        if command == 'rao-prepare':
            self.case_path, self._pending_case = self._pending_case, None
            self.case_label.setText(f'Case: {self.case_path.name}')
        elif self._pending_open is not None:
            self.open_results(self._pending_open)
            self._pending_open = None
        self._enable()

    def cancel(self):
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(3000)
            self.status.setText('Cancelled; partial evidence stays in the output directory.')

    # --------------------------------------------------------------- results
    def open_results_dialog(self):
        start = str(getattr(self.window, '_project_root', '') or '')
        directory = QFileDialog.getExistingDirectory(self, 'Open RAO results directory', start)
        if directory:
            self.open_results(directory)

    def open_results(self, directory):
        try:
            case, table, report = load_results(directory)
        except RaoResultError as error:
            self.status.setText(str(error))
            return
        self.case, self.table, self.report, self._directory = case, table, report, Path(directory)
        try:
            self.dof.blockSignals(True)
            self.point.blockSignals(True)
            self.dof.clear()
            self.dof.addItems(table['dof_keys'])
            self.point.clear()
            for e in table['entries']:
                amplitude = '' if e.get('amplitude') is None else f", a={e['amplitude']:g} m"
                self.point.addItem(f"omega={e['omega']:g}, heading={e['heading_degrees']:g}, {e['source']}{amplitude}")
            self.dof.blockSignals(False)
            self.point.blockSignals(False)
            self.project_changed()
            self.refresh()
            self._fill_checks()
        except (KeyError, TypeError, ValueError, IndexError) as error:
            self.case = self.table = self.report = None
            self.status.setText(f'results could not be displayed: {error}')
            return
        self.results_loaded.emit(str(directory))

    def project_changed(self):
        """Recompute the detached label for the window's current project inputs."""
        if self.table is None:
            return
        current = getattr(self.window, '_project', None)
        detached = ''
        if current is None:
            detached = ' DETACHED: no project open.'
        else:
            from ..hashing import input_hash
            if input_hash(current) != self.table['project_input_hash']:
                detached = ' DETACHED: produced from different project inputs.'
        verdict = 'FD table only' if self.report is None else (
            f"category {self.report['category']}, verdict {self.report['verdict'].upper()}"
            + (f", demoted: {'; '.join(self.report['demoted'])}" if self.report.get('demoted') else ''))
        self.case_label.setText(f"Results {self._directory.name}: {verdict}.{detached}")

    def _entry(self):
        i = self.point.currentIndex()
        return None if self.table is None or i < 0 else self.table['entries'][i]

    def refresh(self):
        entry = self._entry()
        if entry is None or self.dof.currentIndex() < 0:
            return
        k = self.dof.currentIndex()
        heading = entry['heading_degrees']
        lines = {}
        for e in self.table['entries']:
            if e['heading_degrees'] != heading:
                continue
            label = e['source'] + ('' if e.get('amplitude') is None else f" a={e['amplitude']:g}")
            lines.setdefault(label, []).append((e['omega'], complex(*e['rao'][k])))
        series = []
        for label, pts in lines.items():
            pts = sorted(pts, key=lambda p: p[0])
            series.append((label, [w for w, _ in pts], [v for _, v in pts]))
        markers = []
        if self.report is not None:
            for run in self.report['runs']:
                if run.get('heading_degrees') == heading:
                    markers.append((f"TD a={run['amplitude']:g}", run['omega'], complex(*run['rao'][k])))
        self.plot.set_data(f"{self.table['dof_keys'][k]}, heading {heading:g}: RAO amplitude and phase "
                           f"(FD lines, TD x)", series, markers)
        self._fill_forces(entry, k)

    def _fill_forces(self, entry, k):
        rows = sorted(entry['forces'].items())
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
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
