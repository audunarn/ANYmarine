"""Small coefficient-run panel; all numerical work stays in an owned child."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QPlainTextEdit

from ..hashing import input_hash


class CylinderStudyDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.process = None
        self.cancelled = False
        self.folder = None
        self.setWindowTitle('Cylinder + dipole plate — frequency-domain development run')
        self.resize(740, 520)
        layout = QVBoxLayout(self)
        label = QLabel('Two periods: 0.78 and 1.0 s · infinite-depth potential flow · fresh water. '
            'Hull and zero-thickness plate share six rigid-body modes. Experimental geometry: '
            '60 mm cylinder, 150 mm draft, 80 mm disk 65 mm below the keel. '
            'No viscous damping or experimental-response validation is implied.')
        label.setWordWrap(True);layout.addWidget(label)
        self.status = QLabel('Ready. Loading this panel does not start a calculation.')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.output = QPlainTextEdit();self.output.setReadOnly(True)
        self.output.document().setMaximumBlockCount(200);layout.addWidget(self.output, 1)
        self.run_button = QPushButton('Run coupled dipole coefficients')
        self.run_button.clicked.connect(self.start);layout.addWidget(self.run_button)
        self.cancel_button = QPushButton('Cancel');self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel);layout.addWidget(self.cancel_button)
        self.timer = QTimer(self);self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.deadline)

    def start(self):
        if self.process is not None:return
        project = self.window._project
        if project is None or len(project.bodies) != 1 or project.bodies[0].body_id != 'cylinder-heave-plate':
            self.status.setText('Load the cylinder study before running.');return
        self.folder = Path(tempfile.mkdtemp(prefix='anymarine-cylinder-'))
        self.expected_hash = input_hash(project)
        snapshot = dict(project=project.to_dict(), input_hash=self.expected_hash, periods_s=[.78, 1.0])
        (self.folder/'input.json').write_text(json.dumps(snapshot, allow_nan=False), encoding='utf-8')
        self.cancelled = False
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        package_parent = str(Path(__import__('anymarine').__file__).resolve().parent.parent)
        existing = os.environ.get('PYTHONPATH', '')
        environment.insert('PYTHONPATH', os.pathsep.join(p for p in (package_parent, existing) if p))
        environment.insert('OPENBLAS_NUM_THREADS', '1');environment.insert('OMP_NUM_THREADS', '1')
        self.process.setProcessEnvironment(environment)
        self.process.setProgram(sys.executable)
        self.process.setArguments(['-m', 'anymarine.cylinder_worker', '--input', str(self.folder/'input.json'),
                                   '--output', str(self.folder/'coefficients.nc')])
        self.process.readyReadStandardOutput.connect(lambda: self.read_output(False))
        self.process.readyReadStandardError.connect(lambda: self.read_output(True))
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.output.clear();self.run_button.setEnabled(False);self.cancel_button.setEnabled(True)
        self.status.setText('Running in a separate process; deadline 120 s. Evidence: '+str(self.folder))
        self.process.start();self.timer.start(120000)

    def read_output(self, stderr=False):
        if self.process is None:return
        raw = bytes(self.process.readAllStandardError() if stderr else self.process.readAllStandardOutput())
        with (self.folder/('stderr.txt' if stderr else 'stdout.txt')).open('ab') as stream:stream.write(raw)
        self.output.insertPlainText(raw.decode('utf-8', errors='replace'))

    def process_error(self, error):
        if self.process is not None and error == QProcess.ProcessError.FailedToStart:
            self.status.setText('Worker failed to start. Evidence: '+str(self.folder))
            self.release_process()

    def _process_finished(self, code, exit_status):
        if self.process is None:return
        self.read_output();self.read_output(True)
        if self.cancelled:
            self.status.setText('Cancelled or deadline exceeded. Partial evidence retained: '+str(self.folder))
        elif code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self.status.setText('Coefficient run failed. Diagnostics retained: '+str(self.folder))
        else:
            try:
                receipt = json.loads((self.folder/'coefficients.receipt.json').read_text(encoding='utf-8'))
                from anyhydro import load_hydro_dataset, dataset_content_hash
                dataset = load_hydro_dataset(self.folder/'coefficients.nc')
                if (receipt['status'] != 'completed' or receipt['input_hash'] != self.expected_hash
                        or dataset.attrs.get('anymarine_input_hash') != self.expected_hash
                        or dataset_content_hash(dataset) != receipt['dataset_hash']):
                    raise ValueError('Completed artifact identity mismatch')
                if self.window._project is None or input_hash(self.window._project) != self.expected_hash:
                    self.status.setText('Result retained for earlier inputs; current project changed. '+str(self.folder))
                else:
                    self.status.setText('Development coefficients saved and validated: '+str(self.folder/'coefficients.nc'))
                    self.output.appendPlainText(json.dumps(receipt['rows'], indent=2))
            except (OSError, ValueError, KeyError, ImportError) as error:
                self.status.setText('Output refused: '+str(error)+'; evidence '+str(self.folder))
        self.release_process()

    def release_process(self):
        self.timer.stop()
        if self.process is not None:self.process.deleteLater()
        self.process = None
        self.run_button.setEnabled(True);self.cancel_button.setEnabled(False)

    def cancel(self):
        if self.process is not None:
            self.cancelled = True
            self.process.kill()  # Backend uses native threads, never child processes.

    def deadline(self):
        self.cancel()

    def shutdown(self):
        if self.process is not None:
            self.cancel()
            self.process.waitForFinished(3000)
        return self.process is None or self.process.state() == QProcess.ProcessState.NotRunning

    def closeEvent(self, event):
        if self.shutdown():super().closeEvent(event)
        else:event.ignore()


def open_cylinder_study(window):
    dialog = getattr(window, '_cylinder_study_dialog', None)
    if dialog is None:
        dialog = CylinderStudyDialog(window)
        window._cylinder_study_dialog = dialog
    dialog.show();dialog.raise_();dialog.activateWindow()
    return dialog
