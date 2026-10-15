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

from PySide6.QtCore import QPointF, QProcess, QRectF, QProcessEnvironment, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

_TABLE_KEYS = ('artifact', 'case_hash', 'project_input_hash', 'dof_keys', 'entries', 'table_hash')
_REPORT_KEYS = ('artifact', 'case_hash', 'table_hash', 'category', 'checks', 'runs', 'verdict', 'report_hash')
_SUCCESS = {'rao-prepare': (0,), 'rao-table': (0,), 'rao-run': (0, 3)}   # 3 = nogo (2 = usage error)
_CASE_ID_CHARACTERS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-')
_CHECK_KEYS = ('name', 'value', 'limit', 'passed')


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
    try:
        return _load_results(directory, read_case_artifact, RaoCaseError, _hash)
    except RaoResultError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError, OverflowError) as error:
        raise RaoResultError(f'RAO artifacts are malformed: {error!r}') from error


def _finite_number(x):
    if type(x) not in (int, float):          # bool is refused (type(True) is bool)
        return False
    try:
        return math.isfinite(float(x))
    except OverflowError:
        return False


def _positive(x):
    return _finite_number(x) and x > 0


def _is_vector(value, length):
    return isinstance(value, list) and len(value) == length and all(
        isinstance(p, list) and len(p) == 2 and all(_finite_number(x) for x in p) for p in value)


def _load_results(directory, read_case_artifact, RaoCaseError, _hash):
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
    n = len(table['dof_keys']) if isinstance(table['dof_keys'], list) else -1
    if n < 0 or not all(isinstance(k, str) and '::' in k for k in table['dof_keys']):
        raise RaoResultError('rao_table.json dof_keys are malformed')
    n_body = sum(1 for k in table['dof_keys'] if k.rsplit('::', 1)[1] in ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw'))
    grid = {(w, h) for w in case.omegas for h in case.headings_degrees}
    if not isinstance(table['entries'], list) or not table['entries'] or not all(
            isinstance(e, dict) and _is_vector(e.get('rao'), n) and isinstance(e.get('forces'), dict)
            and all(isinstance(c, str) and _is_vector(f, n) for c, f in e['forces'].items())
            and _positive(e.get('omega')) and _finite_number(e.get('heading_degrees'))
            and (e.get('omega'), e.get('heading_degrees')) in grid and isinstance(e.get('source'), str)
            and (e.get('amplitude') is None or _positive(e.get('amplitude'))) for e in table['entries']):
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
        runs, checks = report['runs'], report['checks']
        expected = len(case.omegas) * len(case.headings_degrees) * len(case.amplitudes)
        grid = sorted((w, h, a) for w in case.omegas for h in case.headings_degrees for a in case.amplitudes)
        if not isinstance(runs, list) or len(runs) != expected or not all(
                isinstance(r, dict) and _is_vector(r.get('rao'), n) and _positive(r.get('omega'))
                and _finite_number(r.get('heading_degrees')) and _positive(r.get('amplitude'))
                and isinstance(r.get('forces', {}), dict)
                and all(isinstance(c, str) and isinstance(f, dict) and _is_vector(f.get('phasor'), n_body)
                        for c, f in r.get('forces', {}).items()) for r in runs):
            raise RaoResultError(f'rao_report.json runs do not cover the case grid ({expected} runs expected)')
        if sorted((r.get('omega'), r.get('heading_degrees'), r.get('amplitude')) for r in runs) != grid:
            raise RaoResultError('rao_report.json runs do not match the case grid point for point')
        if not isinstance(checks, list) or not checks or not all(
                isinstance(c, dict) and all(k in c for k in _CHECK_KEYS) for c in checks):
            raise RaoResultError('rao_report.json checks are missing or malformed')
        if not isinstance(report['verdict'], str) or not isinstance(report['category'], str):
            raise RaoResultError('rao_report.json verdict/category are malformed')
    return case, table, report


def _nice_ticks(lo, hi, count=5):
    """Round tick values covering [lo, hi] (1, 2, 5 x 10^k steps)."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / max(count - 1, 1)
    power = 10.0 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1.0, 2.0, 5.0, 10.0) if m * power >= raw)
    first = math.floor(lo / step) * step
    ticks, value = [], first
    while value <= hi + 0.5 * step and len(ticks) < 50:
        ticks.append(round(value, 12))
        value += step
    return ticks


class RaoAxes(QWidget):
    """One RAO graph: FD lines (dark red), TD markers (blue crosses), labelled axes and grid."""

    LINE_COLORS = ('#8b0000', '#d1801f', '#3a9a3a', '#9a3a9a')
    MARKER_COLORS = ('#1f4fb5', '#0f8f8f', '#5a5a5a', '#b51f8f')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(150, 150)
        self._title, self._xlabel, self._ylabel, self._series = '', '', '', []

    def set_data(self, title, xlabel, ylabel, series):
        """``series``: rao_view.Series items (kind 'line' or 'marker')."""
        self._title, self._xlabel, self._ylabel, self._series = title, xlabel, ylabel, list(series)
        self.update()

    def series(self):
        return list(self._series)

    def title(self):
        return self._title

    def labels(self):
        return self._xlabel, self._ylabel

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#ffffff'))
        font = QFont(painter.font().family(), 8)
        painter.setFont(font)
        width, height = self.width(), self.height()
        metrics = QFontMetrics(font)
        left, right, top, bottom = 30 + metrics.horizontalAdvance('-00000.0'), 10, 10, 46
        x0, x1, y0, y1 = left, width - right, top, height - bottom
        painter.setPen(QColor('#202020'))
        bold = QFont(font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.drawText(6, height - 6, self._title)
        painter.setFont(font)
        xs = [x for item in self._series for x in item.xs]
        ys = [y for item in self._series for y in item.ys]
        if not xs:
            painter.setPen(QPen(QColor('#d5dce5')))
            painter.drawRect(int(x0), int(y0), int(x1 - x0), int(y1 - y0))
            painter.setPen(QColor('#8592a3'))
            painter.drawText(QRectF(x0, y0, x1 - x0, y1 - y0), int(Qt.AlignmentFlag.AlignCenter),
                             'no data for this selection')
            return
        painter.setPen(QPen(QColor('#000000')))
        painter.drawRect(int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        xlo, xhi = min(xs), max(xs)
        if xhi <= xlo:
            xlo, xhi = xlo - 0.5, xhi + 0.5
        ylo, yhi = min(min(ys), 0.0), max(ys)
        if yhi <= ylo:
            yhi = ylo + 1.0
        xt, yt = _nice_ticks(xlo, xhi), _nice_ticks(ylo, yhi)
        xlo, xhi = min(xlo, xt[0]), max(xhi, xt[-1])
        ylo, yhi = min(ylo, yt[0]), max(yhi, yt[-1])
        fx = lambda v: x0 + (v - xlo) / (xhi - xlo) * (x1 - x0)
        fy = lambda v: y1 - (v - ylo) / (yhi - ylo) * (y1 - y0)
        grid = QPen(QColor('#a0a0a0'), 1.0, Qt.PenStyle.DashLine)
        for v in xt:
            painter.setPen(grid)
            painter.drawLine(QPointF(fx(v), y0), QPointF(fx(v), y1))
            painter.setPen(QColor('#202020'))
            painter.drawText(QPointF(fx(v) - 10, y1 + 13), f'{v:g}')
        for v in yt:
            painter.setPen(grid)
            painter.drawLine(QPointF(x0, fy(v)), QPointF(x1, fy(v)))
            painter.setPen(QColor('#202020'))
            text = f'{v:.4g}'
            painter.drawText(QPointF(x0 - 4 - metrics.horizontalAdvance(text), fy(v) + 4), text)
        painter.drawText(QPointF((x0 + x1 - metrics.horizontalAdvance(self._xlabel)) / 2, y1 + 27), self._xlabel)
        painter.save()
        painter.translate(12, (y0 + y1 + metrics.horizontalAdvance(self._ylabel)) / 2)
        painter.rotate(-90)
        painter.drawText(0, 0, self._ylabel)
        painter.restore()
        lines = [item for item in self._series if item.kind == 'line']
        markers = [item for item in self._series if item.kind != 'line']
        for i, item in enumerate(lines):
            painter.setPen(QPen(QColor(self.LINE_COLORS[i % len(self.LINE_COLORS)]), 1.4))
            points = [QPointF(fx(x), fy(y)) for x, y in zip(item.xs, item.ys)]
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)
            if len(points) == 1:
                painter.drawEllipse(points[0], 2.5, 2.5)
        for i, item in enumerate(markers):
            painter.setPen(QPen(QColor(self.MARKER_COLORS[i % len(self.MARKER_COLORS)]), 1.6))
            for x, y in zip(item.xs, item.ys):
                pnt = QPointF(fx(x), fy(y))
                painter.drawLine(pnt + QPointF(-4, -4), pnt + QPointF(4, 4))
                painter.drawLine(pnt + QPointF(-4, 4), pnt + QPointF(4, -4))
        if len(self._series) > 1:
            # Legend in the bottom strip, right of the DOF title (never over the curves).
            legend_x = x1
            for i, item in reversed(list(enumerate(lines + markers))):
                color = (self.LINE_COLORS[i % len(self.LINE_COLORS)] if item.kind == 'line'
                         else self.MARKER_COLORS[(i - len(lines)) % len(self.MARKER_COLORS)])
                legend_x -= metrics.horizontalAdvance(item.label) + 26
                painter.setPen(QPen(QColor(color), 1.6))
                if item.kind == 'line':
                    painter.drawLine(QPointF(legend_x, height - 10), QPointF(legend_x + 14, height - 10))
                else:
                    centre = QPointF(legend_x + 7, height - 10)
                    painter.drawLine(centre + QPointF(-3, -3), centre + QPointF(3, 3))
                    painter.drawLine(centre + QPointF(-3, 3), centre + QPointF(3, -3))
                painter.setPen(QColor('#202020'))
                painter.drawText(QPointF(legend_x + 18, height - 6), item.label)


class RaoGrid(QWidget):
    """3 rows x 2 columns of RAO graphs: translations left, rotations right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.axes = [[RaoAxes(), RaoAxes()] for _ in range(3)]
        for r, row in enumerate(self.axes):
            for c, axes in enumerate(row):
                layout.addWidget(axes, r, c)

    def show_series(self, grid, data, xlabel, ylabel_for):
        """``grid``: rao_view grid of DOF names (None = empty cell); ``data``: {dof: [Series]}."""
        for r, row in enumerate(grid):
            for c, dof in enumerate(row):
                axes = self.axes[r][c]
                if dof is None:
                    axes.set_data('', xlabel, '', [])
                    axes.setVisible(False)
                    continue
                axes.setVisible(True)
                axes.set_data(dof.capitalize(), xlabel, ylabel_for(dof), data.get(dof, []))

    def cell(self, dof):
        for row in self.axes:
            for axes in row:
                if axes.title().lower() == dof.lower():
                    return axes
        return None


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
        self.register_button.setProperty('primary', True)
        # Two columns keep the panel readable in a narrow context panel.
        buttons = QGridLayout()
        for i, b in enumerate((self.register_button, self.open_button, self.table_button, self.run_button,
                               self.cancel_button)):
            buttons.addWidget(b, i // 2, i % 2)
        layout.addLayout(buttons)
        self.case_label = QLabel('No RAO case. Register one from a JSON specification (see docs/RAO_REPORTING.md).')
        self.case_label.setWordWrap(True)
        layout.addWidget(self.case_label)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        # Graphs: OrcaWave-style RAO grid; frequency (FD table), time (TD runs) or both.
        graphs = QWidget()
        graphs_layout = QVBoxLayout(graphs)
        controls = QGridLayout()
        self.owner = QComboBox()
        self.quantity = QComboBox()
        self.quantity.addItem('Displacement RAOs', 'displacement')
        self.quantity.addItem('Contribution forces', 'force')
        self.contribution = QComboBox()
        self.heading = QComboBox()
        self.amplitude = QComboBox()
        self.x_axis = QComboBox()
        self.x_axis.addItem('Period (s)', 'period')
        self.x_axis.addItem('Frequency (rad/s)', 'frequency')
        for i, (label, combo) in enumerate((('Body', self.owner), ('Graph type', self.quantity),
                                            ('Contribution', self.contribution), ('Wave heading (deg)', self.heading),
                                            ('Wave amplitude', self.amplitude), ('X axis', self.x_axis))):
            controls.addWidget(QLabel(label), i // 2 * 2, i % 2)
            controls.addWidget(combo, i // 2 * 2 + 1, i % 2)
            combo.setMinimumContentsLength(8)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        graphs_layout.addLayout(controls)
        options = QHBoxLayout()
        self.source_group, self.display_group = QButtonGroup(self), QButtonGroup(self)
        self.source_buttons = {}
        for key, text in (('frequency', 'Frequency'), ('time', 'Time'), ('compare', 'Compare')):
            button = QRadioButton(text)
            self.source_buttons[key] = button
            self.source_group.addButton(button)
            options.addWidget(button)
        options.addStretch(1)
        graphs_layout.addLayout(options)
        display = QHBoxLayout()
        self.display_buttons = {}
        for key, text in (('amplitude', 'Amplitude'), ('phase', 'Phase')):
            button = QRadioButton(text)
            self.display_buttons[key] = button
            self.display_group.addButton(button)
            display.addWidget(button)
        display.addStretch(1)
        self.display_buttons['amplitude'].setChecked(True)
        graphs_layout.addLayout(display)
        self.grid = RaoGrid()
        graphs_layout.addWidget(self.grid, 1)
        self.tabs.addTab(graphs, 'Graphs')
        # Details: one exact table entry, its contribution forces, and the report checks.
        details = QWidget()
        details_layout = QVBoxLayout(details)
        self.dof = QComboBox()
        self.point = QComboBox()        # one exact table entry: omega, heading, source, amplitude
        for label, combo in (('DOF', self.dof), ('Point', self.point)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(combo, 1)
            details_layout.addLayout(row)
        self.forces = QTableWidget(0, 4)
        self.forces.setHorizontalHeaderLabels(['contribution', '|F| per m', 'phase deg', 'mean power W'])
        details_layout.addWidget(self.forces, 1)
        self.checks = QTableWidget(0, 4)
        self.checks.setHorizontalHeaderLabels(['check', 'value', 'limit', 'passed'])
        details_layout.addWidget(self.checks, 1)
        self.tabs.addTab(details, 'Details')
        self.note = QLabel('RAO reporting is development evidence only (docs/RAO_REPORTING.md); unqualified.')
        self.note.setWordWrap(True)
        self.note.setProperty('role', 'hint')
        layout.addWidget(self.note)
        self.register_button.clicked.connect(lambda: self.register_case())
        self.table_button.clicked.connect(lambda: self.start('rao-table'))
        self.run_button.clicked.connect(lambda: self.start('rao-run'))
        self.open_button.clicked.connect(self.open_results_dialog)
        self.cancel_button.clicked.connect(self.cancel)
        self.dof.currentIndexChanged.connect(self.refresh)
        self.point.currentIndexChanged.connect(self.refresh)
        for combo in (self.owner, self.quantity, self.contribution, self.heading, self.amplitude, self.x_axis):
            combo.currentIndexChanged.connect(self.refresh_graphs)
        self.source_group.buttonToggled.connect(lambda *_: self.refresh_graphs())
        self.display_group.buttonToggled.connect(lambda *_: self.refresh_graphs())
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
            if not case_id or not all(c in _CASE_ID_CHARACTERS for c in case_id) or case_id[0] in '.-':
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
        # -P: the working directory is not prepended to sys.path, so it cannot shadow the package.
        process.setArguments(['-P', '-X', 'utf8', '-m', 'anymarine.cli', *arguments])
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
        """Kill the child without blocking the GUI thread; ``finished`` resets the dock."""
        if self.process is not None:
            self.process.kill()
            self.status.setText('Cancelling; partial evidence stays in the output directory.')

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
            self._fill_graph_controls()
            self.project_changed()
            self.refresh()
            self._fill_checks()
        except (KeyError, TypeError, ValueError, IndexError, ArithmeticError) as error:
            self._clear()
            self.status.setText(f'results could not be displayed: {error}')
            return
        self.results_loaded.emit(str(directory))

    def _clear(self):
        self.case = self.table = self.report = self._directory = None
        for combo in (self.dof, self.point):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        self._fill_graph_controls()
        self.forces.setRowCount(0)
        self.checks.setRowCount(0)
        self.case_label.setText('No RAO results shown.')

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

    def _graph_combos(self):
        return (self.owner, self.quantity, self.contribution, self.heading, self.amplitude, self.x_axis)

    def _fill_graph_controls(self):
        """Selectors from the open artifacts; a source without data is disabled, not hidden."""
        from .. import rao_view
        combos = self._graph_combos()
        for combo in combos:
            combo.blockSignals(True)
        for button in list(self.source_buttons.values()) + list(self.display_buttons.values()):
            button.blockSignals(True)
        info = rao_view.available(self.table, self.report)
        for combo, values, text in ((self.owner, info['owners'], rao_view.owner_label),
                                    (self.contribution, info['contributions'], str),
                                    (self.heading, info['headings'], lambda v: f'{v:g}')):
            combo.clear()
            for v in values:
                combo.addItem(text(v), v)
        self.amplitude.clear()
        self.amplitude.addItem('All', None)
        for a in info['amplitudes']:
            self.amplitude.addItem(rao_view.amplitude_label(a), a)
        self.source_buttons['frequency'].setEnabled(info['has_frequency'])
        self.source_buttons['time'].setEnabled(info['has_time'])
        self.source_buttons['compare'].setEnabled(info['has_frequency'] and info['has_time'])
        preferred = 'compare' if info['has_frequency'] and info['has_time'] else (
            'time' if info['has_time'] else 'frequency')
        self.source_buttons[preferred].setChecked(True)
        self.contribution.setEnabled(self.quantity.currentData() == 'force')
        for combo in combos:
            combo.blockSignals(False)
        for button in list(self.source_buttons.values()) + list(self.display_buttons.values()):
            button.blockSignals(False)
        self.refresh_graphs()

    def graph_selection(self):
        source = next((k for k, b in self.source_buttons.items() if b.isChecked()), 'frequency')
        display = next((k for k, b in self.display_buttons.items() if b.isChecked()), 'amplitude')
        return dict(owner=self.owner.currentData(), heading=self.heading.currentData(),
                    quantity=self.quantity.currentData(), contribution=self.contribution.currentData(),
                    source=source, display=display, x_axis=self.x_axis.currentData(),
                    amplitude=self.amplitude.currentData())

    def refresh_graphs(self):
        from .. import rao_view
        self.contribution.setEnabled(self.quantity.currentData() == 'force')
        selection = self.graph_selection()
        if self.table is None or selection['owner'] is None or selection['heading'] is None or (
                selection['quantity'] == 'force' and selection['contribution'] is None):
            self.grid.show_series(rao_view.GRID, {}, '', lambda dof: '')
            return
        try:
            data = rao_view.series(self.table, self.report, **selection)
        except (KeyError, TypeError, ValueError, IndexError, ArithmeticError) as error:
            self.status.setText(f'graphs could not be drawn: {error}')
            self.grid.show_series(rao_view.GRID, {}, '', lambda dof: '')
            return
        xlabel = 'Period (s)' if selection['x_axis'] == 'period' else 'Frequency (rad/s)'
        kind = 'Amplitude' if selection['display'] == 'amplitude' else 'Phase'
        self.grid.show_series(rao_view.grid_for(selection['owner'], self.table['dof_keys']), data, xlabel,
                              lambda dof: f"{kind} ({rao_view.unit(dof, selection['quantity'], selection['display'])})")

    def refresh(self):
        entry = self._entry()
        if entry is None or self.dof.currentIndex() < 0:
            return
        self._fill_forces(entry, self.dof.currentIndex())

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
