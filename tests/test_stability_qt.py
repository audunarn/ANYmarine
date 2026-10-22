import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("anystability")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from anymarine.services import ApplicationService
from anymarine.qtapp.stability import StabilityDialog


def test_dialog_load_calculate_edit_and_reopen(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = ApplicationService()
    real_calculate = service.calculate_stability
    monkeypatch.setattr(service, "calculate_stability", lambda *a, **k: pytest.fail("loading must not solve"))
    dialog = StabilityDialog(service)
    assert dialog.result is None
    assert not dialog.calculate_button.isEnabled()
    assert "four full tanks" in dialog.status.text()
    assert "Expected draught 2.13902439 m" in dialog.status.text()
    monkeypatch.setattr(service, "calculate_stability", real_calculate)
    dialog.opt_in.setChecked(True)
    dialog.calculate_button.click()
    assert dialog.result is not None
    assert "REFERENCE ONLY" in dialog.report.toPlainText()
    for name in ("ballast-tank-port", "ballast-tank-starboard", "freshwater-tank-aft", "freshwater-tank-forward"):
        assert name in dialog.report.toPlainText()
    assert dialog.status.text().startswith("Reference draught: ")
    assert dialog.result.to_dict()["output"]["draft_m"] == pytest.approx(438500 / (1025 * 200), abs=1e-6)
    dialog.save_study(tmp_path)
    monkeypatch.setattr(service, "calculate_stability", lambda *a, **k: pytest.fail("opening must not solve"))
    dialog.open_study(tmp_path)
    assert dialog.result is not None
    assert not dialog.opt_in.isChecked()
    dialog.inputs.appendPlainText(" ")
    assert dialog.result is None
    assert dialog.report.toPlainText() == ""
    dialog.close()
    app.processEvents()


def test_deep_gui_inputs_refused_without_partial_result(monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = StabilityDialog(ApplicationService())
    dialog.inputs.setPlainText("[" * 5000 + "0" + "]" * 5000)
    dialog.opt_in.setChecked(True)
    from PySide6.QtWidgets import QMessageBox
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    dialog.calculate_button.click()
    assert dialog.result is None
    assert len(messages) == 1
    assert "INPUT_INVALID" in messages[0]
    dialog.close()
    app.processEvents()


def test_mainwindow_menu_lifecycle_and_missing_owner(monkeypatch,tmp_path):
    pytest.importorskip("any3dview")
    from anymarine.qtapp.mainwindow import MainWindow
    from anymarine import stability_service as bridge
    app = QApplication.instance() or QApplication([])
    window = MainWindow(viewer_backend="software")
    monkeypatch.setattr(window._service, "calculate_stability", lambda *a, **k: pytest.fail("mode/menu must not solve"))
    original_project = window._project
    action = next(a for a in window._examples_menu.actions() if a.text() == "ANYstability analytical box…")
    other=None
    try:
        action.trigger(); app.processEvents()
        host=window._stability_host;study=host.study
        assert window._mode=='stability' and study.result is None
        assert window._central_stack.currentWidget() is host
        study.inputs.setPlainText(study.inputs.toPlainText()+'\n ');study.opt_in.setChecked(True)
        edited=study.inputs.toPlainText();recorded=len(window._stability_recording.entries)
        window._set_mode('model');action.trigger()
        assert window._stability_host.study is study
        assert study.inputs.toPlainText()==edited and study.opt_in.isChecked()
        assert len(window._stability_recording.entries)==recorded
        assert window._project is original_project
        original_import = bridge.importlib.import_module
        def missing(name, *args, **kwargs):
            if name.startswith("anystability"):
                raise ModuleNotFoundError("optional owner absent")
            return original_import(name, *args, **kwargs)
        monkeypatch.setattr(bridge.importlib, "import_module", missing)
        from anymarine import reference_barge
        monkeypatch.setattr(reference_barge, "import_module", missing)
        # Exercise missing owner independently of whether private local data is configured.
        monkeypatch.setenv('ANYMARINE_REFERENCE_BARGE_DIR',str(tmp_path))
        other=MainWindow(viewer_backend="software");other._set_mode('stability')
        assert other._stability_host.study is None
        assert 'PROVIDER_UNAVAILABLE' in other._stability_host.diagnostic.text()
        assert other._mode=='stability'
        other._set_mode('model');assert other._central_stack.currentWidget() is other._viewport
    finally:
        window._dirty = False;window.close()
        if other is not None:other._dirty=False;other.close()
        app.processEvents()
