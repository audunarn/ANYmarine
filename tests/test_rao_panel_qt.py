"""RAO slice 9: Qt dock drives the headless CLI in child processes and shows verified results."""
import json
import time

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


@requires_qt
@requires_solver
def test_register_table_and_verified_display(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    from anymarine.qtapp import rao_panel
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'project'
    project = oscillator_with_cylinder()
    ApplicationService(root).save(project)
    (tmp_path / 'spec.json').write_text(json.dumps(spec()), encoding='utf-8')
    w = MainWindow(viewer_backend='software')
    try:
        w.load_project(str(root))
        panel = w._rao
        assert not panel.table_button.isEnabled()
        panel.register_case(str(tmp_path / 'spec.json'))
        wait(app, panel)
        assert panel.case_path is not None and panel.case_path.exists(), panel.status.text()
        panel.start('rao-table')
        wait(app, panel)
        assert panel.table is not None and panel.report is None, panel.status.text()
        assert 'FD table only' in panel.case_label.text() and 'DETACHED' not in panel.case_label.text()
        lines, markers = panel.plot.series()
        assert lines and not markers and panel.forces.rowCount() >= 3
        out = next((root / 'rao').glob('table-*'))
        data = json.loads((out / 'rao_table.json').read_text(encoding='utf-8'))
        data['entries'][0]['rao'][0][0] += 1.0                     # tampered artifact
        bad = tmp_path / 'bad'
        bad.mkdir()
        (bad / 'rao_table.json').write_text(json.dumps(data), encoding='utf-8')
        with pytest.raises(rao_panel.RaoResultError, match='hash'):
            rao_panel.load_results(bad)
        from anymarine.native_inputs import NativeMechanicalInputs
        data = project.native_inputs.to_dict()
        data['links'][0]['stiffness'][0][0] = 11.0                    # different inputs -> detached label
        w._project = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
        panel.open_results(out)
        assert 'DETACHED' in panel.case_label.text()
    finally:
        w._dirty = False
        w.close()
        app.processEvents()
