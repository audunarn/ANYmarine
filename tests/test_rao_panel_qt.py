"""RAO slice 9: Qt dock drives the headless CLI in child processes and shows verified results."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from conftest import requires_qt, requires_solver
from anymarine.services import ApplicationService
from test_rao_report import oscillator_with_cylinder, spec


def wait(app, panel, seconds=180):
    deadline = time.monotonic() + seconds
    while panel.process is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert panel.process is None, 'child process did not finish'


@pytest.fixture
def window(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'project'
    ApplicationService(root).save(oscillator_with_cylinder())
    (tmp_path / 'spec.json').write_text(json.dumps(spec()), encoding='utf-8')
    w = MainWindow(viewer_backend='software')
    w.load_project(str(root))
    yield app, w, root, tmp_path
    if w._rao.busy:
        w._rao.cancel()
    w._dirty = False
    w.close()
    app.processEvents()


@requires_qt
@requires_solver
def test_register_table_and_verified_display(window):
    from anymarine.native_inputs import NativeMechanicalInputs
    from anymarine.qtapp import rao_panel
    from anymarine.rao_report import _hash
    app, w, root, tmp_path = window
    panel = w._rao
    assert not panel.table_button.isEnabled()
    panel.register_case(str(tmp_path / 'spec.json'))
    wait(app, panel)
    assert panel.case_path is not None and panel.case_path.exists(), panel.status.text()
    assert panel.case_path.parent == (root / 'rao' / 'cases').resolve()
    panel.start('rao-table')
    wait(app, panel)
    assert panel.table is not None and panel.report is None, panel.status.text()
    assert 'FD table only' in panel.case_label.text() and 'DETACHED' not in panel.case_label.text()
    lines, markers = panel.plot.series()
    assert lines and not markers and panel.forces.rowCount() >= 3
    assert panel.point.count() == 1 and 'omega=1' in panel.point.currentText()
    out = next((root / 'rao').glob('table-*'))
    # tampered table, or a table re-hashed but no longer bound to its case: refused
    bad = tmp_path / 'bad'
    shutil.copytree(out, bad)
    data = json.loads((out / 'rao_table.json').read_text(encoding='utf-8'))
    data['entries'][0]['rao'][0][0] += 1.0
    (bad / 'rao_table.json').write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(rao_panel.RaoResultError, match='hash'):
        rao_panel.load_results(bad)
    data['table_hash'] = _hash(data)
    data['case_hash'] = 'f' * 64
    data['table_hash'] = _hash(data)
    (bad / 'rao_table.json').write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(rao_panel.RaoResultError, match='bound'):
        rao_panel.load_results(bad)
    (bad / 'case.json').unlink()
    with pytest.raises(rao_panel.RaoResultError, match='case artifact'):
        rao_panel.load_results(bad)
    # an edit makes the open results DETACHED without reopening them
    data = w._project.native_inputs.to_dict()
    data['links'][0]['stiffness'][0][0] = 11.0
    w._mark_edited(w._project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data)))
    assert 'DETACHED' in panel.case_label.text()
    panel.register_case(str(tmp_path / 'spec.json'))                     # unsaved edits: refused
    assert 'Save the project first' in panel.status.text() and not panel.busy


def test_report_hash_binds_its_table():
    from anymarine.rao_report import _hash
    report = dict(artifact='anymarine_rao_report', case_hash='c' * 64, table_hash='a' * 64, verdict='pass')
    report['report_hash'] = _hash(report)
    moved = dict(report, table_hash='b' * 64)
    assert _hash(moved) != report['report_hash']


@requires_qt
def test_child_environment_start_failure_and_exit_codes(window):
    import anymarine
    from PySide6.QtCore import QProcess
    app, w, root, tmp_path = window
    panel = w._rao
    package_root = str(Path(anymarine.__file__).resolve().parents[1])
    panel._launch(['rao-prepare', '--help'])
    env = panel.process.processEnvironment().value('PYTHONPATH')
    assert any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(package_root) for p in env.split(os.pathsep))
    wait(app, panel)
    # a failed start does not leave the dock busy
    executable = sys.executable
    try:
        sys.executable = str(tmp_path / 'missing-python.exe')
        panel._launch(['rao-prepare', '--help'])
        deadline = time.monotonic() + 10
        while panel.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
    finally:
        sys.executable = executable
    assert not panel.busy and 'could not start' in panel.status.text()
    # exit code 2 is a valid outcome only for rao-run; registration without its file fails
    class Finished:                                       # stands in for a child that already exited
        def readAllStandardOutput(self):
            return b'usage: error'
    panel._command, panel.process, panel._pending_case = 'rao-prepare', Finished(), tmp_path / 'never.json'
    panel._finished(2, QProcess.ExitStatus.NormalExit)
    assert panel.case_path is None and 'failed' in panel.status.text()


def test_incomplete_report_is_refused(tmp_path):
    from anymarine.qtapp import rao_panel
    from anymarine.rao_report import _hash
    from test_rao_review_fixes import case, table, BASE
    c = case()
    t = table(c, BASE)
    t.update(artifact='anymarine_rao_table', project_input_hash=c.project_input_hash)
    t['table_hash'] = _hash(t)
    (tmp_path / 'case.json').write_text(json.dumps(dict(case=c.to_dict(), case_hash=c.case_hash)), encoding='utf-8')
    (tmp_path / 'rao_table.json').write_text(json.dumps(t), encoding='utf-8')
    report = dict(artifact='anymarine_rao_report', case_hash=c.case_hash, table_hash=t['table_hash'],
                  category=c.category, checks=[], runs=[], verdict='pass')
    report['report_hash'] = _hash(report)
    (tmp_path / 'rao_report.json').write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(rao_panel.RaoResultError, match='case grid'):
        rao_panel.load_results(tmp_path)
