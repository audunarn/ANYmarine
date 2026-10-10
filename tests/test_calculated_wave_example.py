"""Regression over ONE stored actual calculation; never run/import Capytaine."""
import copy,json,hashlib,shutil,sys
import numpy as np
import pytest
from anymarine.services import ApplicationService,ServiceError
from anymarine.wave_examples import calculated_wave_project
from anymarine.hashing import input_hash

def test_pure_stored_project_load_is_detached_and_no_solve(monkeypatch):
    monkeypatch.setitem(sys.modules,'capytaine',None)
    project=calculated_wave_project();assert not project.results and len(project.bodies)==2
    assert project.native_inputs.run_spec['steps']==3
    service=ApplicationService();p,field=service.load_calculated_wave_example()
    assert p.to_dict()==project.to_dict()
    assert input_hash(p)==field['request']['project_input_hash']
    assert field['kind']=='fixed_body_diffraction_preview' and field['panels']==28
    assert len(field['vertices'])==801
    first=copy.deepcopy(field);field['diffraction_real'][0]+=1.
    assert service.load_calculated_wave_example()[1]==first

@pytest.mark.parametrize('failure',['seal','project','missing','provenance','kind'])
def test_corrupted_example_refused_atomically(tmp_path,monkeypatch,failure):
    import anymarine.wave_examples as examples
    copyroot=tmp_path/'bundle';shutil.copytree(examples._BUNDLE,copyroot)
    monkeypatch.setattr(examples,'_BUNDLE',copyroot)
    service=ApplicationService(tmp_path/'previous');root,store=service.root,service.store
    if failure=='missing':(copyroot/'output.json').unlink()
    elif failure=='project':
        p=copyroot/'project-snapshot.json';data=json.loads(p.read_text());data['project']['bodies'][0]['mass']+=1.
        p.write_text(json.dumps(data))
    elif failure=='kind':
        p=copyroot/'output.json';data=json.loads(p.read_text());data['field']['kind']='incident_only'
        p.write_text(json.dumps(data));meta=copyroot/'provenance.json';d=json.loads(meta.read_text())
        d['output_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();meta.write_text(json.dumps(d))
    else:
        p=copyroot/'provenance.json';data=json.loads(p.read_text())
        if failure=='seal':data['output_sha256']='0'*64
        else:data['status']='qualified'
        p.write_text(json.dumps(data))
    with pytest.raises(ServiceError):service.load_calculated_wave_example()
    assert service.root==root and service.store is store

def test_qt_loader_uses_actual_arrays_resets_prior_manual_values_and_never_queues(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    from anyhydro.wave_preview import positions_at_time
    monkeypatch.setitem(sys.modules,'capytaine',None)
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    errors=[];w._notify_warning=lambda *args:errors.append(args)
    try:
        w._waves.amplitude.setValue(.6);w._waves.omega.setValue(2.);w._waves.depth.setValue(10.)
        w.load_calculated_wave_example();app.processEvents();assert not errors,errors
        p=w._waves;assert p.mode.currentIndex()==2 and p.active and p.handle is not None
        assert p.field['panels']==28 and not w._job_manager.jobs()
        p.timer.stop();p.time=.37;p.advance(step=False)
        np.testing.assert_allclose(p.handle.mesh.positions,positions_at_time(p.field,.37,disturbed=True),atol=1e-12)
        np.testing.assert_array_equal(p.handle.mesh.node_scalars,p.handle.mesh.positions[:,2])
        assert len(p.handle.mesh.lines)>0 and 'true height' in p.elevation_legend.text()
        bound=p.elevation_bound()
        p.mode.setCurrentIndex(1);p.timer.stop();p.advance(step=False)
        np.testing.assert_allclose(p.handle.mesh.positions,positions_at_time(p.field,.37),atol=1e-12)
        assert p.elevation_bound()==bound
        np.testing.assert_array_equal(p.handle.mesh.node_scalars,p.handle.mesh.positions[:,2])
        p.mode.setCurrentIndex(2);p.timer.stop();w._change_renderer('software')
        assert not p.timer.isActive() and not w._job_manager.jobs()
        w.load_native_sea_state_example();assert not p.active and p.field is None
    finally:w._dirty=False;w.close();app.processEvents()
