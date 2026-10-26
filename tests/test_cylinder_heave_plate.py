import pytest
import numpy as np
import json

from anymarine.cylinder_heave_plate import cylinder_heave_plate_project, REFERENCE


def test_two_sided_disk_owned_by_single_six_dof_body():
    project = cylinder_heave_plate_project()
    assert len(project.bodies) == 1
    body = project.bodies[0]
    assert body.mass == .418
    assert len(body.hydro_surfaces) == 2
    surface = next(s for s in body.hydro_surfaces if s.role == 'attached')
    assert surface.semantics.owning_body_id == body.body_id
    assert surface.semantics.surface_kind == 'thin_sheet'
    assert surface.semantics.minus_side != surface.semantics.plus_side
    p = np.asarray(surface.mesh.positions)
    assert np.allclose(p[:, 2], -.215)
    assert np.max(np.linalg.norm(p[:, :2], axis=1)) == pytest.approx(.04)
    f = np.asarray(surface.mesh.triangles)
    assert np.all(np.cross(p[f[:, 1]]-p[f[:, 0]], p[f[:, 2]]-p[f[:, 0]])[:, 2] > 0)
    assert project.results == ()


def test_plate_free_control_retains_identical_mass_and_hull():
    a = cylinder_heave_plate_project().bodies[0]
    b = cylinder_heave_plate_project(with_plate=False).bodies[0]
    assert a.mesh == b.mesh
    assert a.mass_properties == b.mass_properties
    assert len(b.hydro_surfaces) == 1
    assert REFERENCE['experimental_plate_thickness_m'] == .002


@pytest.mark.parametrize('kwargs', [{'sectors': True}, {'plate_rings': 1}, {'vertical_panels': 0}])
def test_refuses_invalid_mesh_parameters(kwargs):
    with pytest.raises(ValueError):
        cylinder_heave_plate_project(**kwargs)


def test_worker_refuses_tampered_snapshot_before_solve(tmp_path, monkeypatch):
    from anymarine.cylinder_worker import run
    from anymarine.hashing import input_hash
    project = cylinder_heave_plate_project()
    snapshot = dict(project=project.to_dict(), input_hash=input_hash(project), periods_s=[.78])
    snapshot['project']['bodies'][0]['mass'] += 1.
    source = tmp_path/'input.json';source.write_text(json.dumps(snapshot), encoding='utf-8')
    def forbidden(*a, **kw):raise AssertionError('tampered snapshot reached backend')
    monkeypatch.setattr('anymarine.cylinder_heave_plate.solve_project_coefficients', forbidden)
    with pytest.raises(ValueError, match='input hash mismatch'):
        run(source, tmp_path/'output.nc')
    assert not (tmp_path/'output.nc').exists()


def test_qt_panel_load_and_cancel_owned_process(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import QProcess
    from anymarine.qtapp.cylinder_study import CylinderStudyDialog
    import sys
    app = QApplication.instance() or QApplication([])
    window = QWidget();window._project = cylinder_heave_plate_project()
    dialog = CylinderStudyDialog(window)
    assert dialog.process is None
    # Exercise owned-process cancellation without launching BEM.
    dialog.folder = tmp_path
    process = QProcess(dialog);dialog.process = process
    process.setProgram(sys.executable);process.setArguments(['-c', 'import time; time.sleep(20)'])
    process.finished.connect(dialog._process_finished)
    process.start()
    assert process.waitForStarted(3000)
    dialog.cancel()
    process.waitForFinished(3000);app.processEvents()
    assert dialog.process is None
    assert 'Cancelled' in dialog.status.text()
    dialog.close();window.close()


def test_qt_refuses_running_cylinder_on_another_study():
    from PySide6.QtWidgets import QApplication, QWidget
    from anymarine.qtapp.cylinder_study import CylinderStudyDialog
    from anymarine.domain import default_two_body_project
    app = QApplication.instance() or QApplication([])
    window = QWidget();window._project = default_two_body_project()
    dialog = CylinderStudyDialog(window);dialog.start()
    assert dialog.process is None
    assert 'Load the cylinder' in dialog.status.text()
    dialog.close();window.close();app.processEvents()
