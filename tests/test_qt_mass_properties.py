"""Actual editor wiring, atomic partial edits and authoritative persistence."""
import pytest
from PySide6.QtWidgets import QApplication
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.hashing import input_hash
from anymarine.services import ApplicationService

def test_qt_mass_table_edits_validation_refusal_clear_and_reopen(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    win=MainWindow()
    for name in ('_notify_warning','_notify_critical','_notify_info'):
        monkeypatch.setattr(win,name,lambda *a: None)
    try:
        win.load_example(); body_id=win._project.bodies[0].body_id
        panel=win._details; panel.show_body(body_id)
        old=input_hash(win._project)
        panel._mass_enabled.setChecked(True)
        assert input_hash(win._project)==old  # no implicit zero/matrix defaults
        panel._updating=True
        for edit,value in zip(panel._cg_fields,(.3,-.2,.7)): edit.setText(str(value))
        I=((4e6,.2e6,.1e6),(.2e6,5e6,-.1e6),(.1e6,-.1e6,6e6))
        for row in range(3):
            for col in range(3): panel._inertia_table.item(row,col).setText(str(I[row][col]))
        panel._updating=False; panel._emit_mass_properties(); app.processEvents()
        assert win._project.body(body_id).mass_properties.inertia_cg_body==I
        assert input_hash(win._project)!=old
        panel.show_body(body_id); panel._mass_validate.click()
        assert 'inputs valid' in panel._mass_status.text()
        root=tmp_path/'saved'; win._service.save(win._project,root)
        reopened=ApplicationService().load(root)
        assert reopened.body(body_id).mass_properties==win._project.body(body_id).mass_properties
        assert input_hash(reopened)==input_hash(win._project)
        # Full-tensor declaration can be stored; solver must reject nonphysical symmetry.
        panel._inertia_table.item(0,1).setText('123')
        panel.show_body(body_id); panel._mass_validate.click()
        assert 'symmetric' in panel._mass_status.text()
        before=input_hash(win._project)
        panel._cg_fields[0].setText('nan'); panel._emit_mass_properties()
        assert input_hash(win._project)==before
        panel.show_body(body_id); panel._mass_enabled.setChecked(False)
        assert win._project.body(body_id).mass_properties is None
    finally:
        win._dirty=False; win.close(); app.processEvents()
