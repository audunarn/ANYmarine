import copy,hashlib,json,time
from pathlib import Path
import numpy as np
import pytest
from anymarine.native_examples import fluid_two_body_project
from anymarine.wave_jobs import preview_request,wave_input_snapshot,validate_snapshot,load_wave_output,run_wave_job
from anyhydro.wave_preview import incident_field,positions_at_time,validate_field,field_record

def request():
    p=fluid_two_body_project()
    poses={}
    for body in p.bodies:
        m=np.eye(4);m[:3,3]=body.reference_position;poses[body.body_id]=m
    return preview_request(p,poses)

def fixture_field(r):
    f=incident_field(r)
    incident=np.asarray(f['incident_real'])+1j*np.asarray(f['incident_imag'])
    # Test-only declared arrays; never execute/import a BEM backend here.
    diffraction=.2j*incident
    return field_record(r,f['vertices'],f['triangles'],incident,diffraction,
                        'fixed_body_diffraction_preview','3.0.0',24)

def test_incident_phasor_grid_and_composition():
    r=request();f=incident_field(r)
    eta=np.asarray(f['incident_real'])+1j*np.asarray(f['incident_imag'])
    np.testing.assert_allclose(np.abs(eta),r['amplitude'],rtol=1e-12,atol=1e-12)
    total=fixture_field(r)
    for t in (0.,.4,1.):
        points=positions_at_time(total,t,disturbed=True)
        np.testing.assert_allclose(points[:,2],np.real(eta*(1+.2j)*np.exp(-1j*r['omega']*t)),atol=1e-12)
    with pytest.raises(ValueError,match='actual diffraction'):
        positions_at_time(f,0.,disturbed=True)

def test_field_identity_and_missing_or_corrupted_arrays_refused():
    f=fixture_field(request())
    for change in ('hash','grid','nonfinite','missing','source','incident'):
        bad=copy.deepcopy(f)
        if change=='hash':bad['request_hash']='0'*64
        if change=='grid':bad['vertices'][0][0]+=.1
        if change=='nonfinite':bad['incident_real'][0]=float('nan')
        if change=='missing':del bad['diffraction_real']
        if change=='source':bad['capytaine_version']='unknown'
        if change=='incident':bad['incident_real'][0]+=.01
        with pytest.raises(ValueError):validate_field(bad)

def test_wave_output_seal_snapshot_and_request_binding(tmp_path):
    from anymarine.domain import _freeze_json_value
    r=request();snapshot=wave_input_snapshot(r);field=fixture_field(r)
    frozen=_freeze_json_value(snapshot,'wave snapshot')
    assert validate_snapshot(frozen)==snapshot
    path=tmp_path/'output.json'
    path.write_text(json.dumps(dict(kind='wave_preview',input_hash=snapshot['input_hash'],field=field)),encoding='utf-8')
    seal=hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_wave_output(path,frozen,seal)==field
    with pytest.raises(ValueError,match='seal'):load_wave_output(path,snapshot,'')
    stale=copy.deepcopy(snapshot);stale['request']['amplitude']+=.1
    with pytest.raises(ValueError):load_wave_output(path,stale,seal)
    path.write_text(json.dumps(dict(kind='wave_preview',input_hash=snapshot['input_hash'],field=incident_field(r))),encoding='utf-8')
    with pytest.raises(ValueError,match='actual body diffraction'):
        load_wave_output(path,snapshot,hashlib.sha256(path.read_bytes()).hexdigest())

@pytest.mark.parametrize('failure',('cancel','backend'))
def test_worker_cancel_or_missing_backend_never_seals_success(tmp_path,monkeypatch,capsys,failure):
    import anyhydro.wave_preview as hydro
    def stop(*args,**kwargs):
        if failure=='cancel':raise InterruptedError('injected cancellation before solve')
        raise ImportError('injected missing optional backend')
    monkeypatch.setattr(hydro,'compute_diffraction_preview',stop)
    output=tmp_path/'output.json'
    assert run_wave_job(wave_input_snapshot(request()),output,None)==(3 if failure=='cancel' else 4)
    assert not output.exists() and output.with_suffix('.incomplete.json').exists()
    assert 'OUTPUT_SHA256' not in capsys.readouterr().out

def test_wave_layer_toggle_restoration_renderer_and_queued_cancel(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([])
    w=MainWindow(viewer_backend='software')
    try:
        w.load_native_fluid_example();w.show();app.processEvents()
        panel=w._waves
        original={bid:np.array(h.transform,copy=True) for bid,h in w._handles.items()}
        panel.mode.setCurrentIndex(1);app.processEvents()
        assert panel.active and panel.handle is not None
        incident=np.array(panel.handle.mesh.positions,copy=True)
        panel.advance()
        assert not np.allclose(incident,panel.handle.mesh.positions)
        panel.job_id='fixture'
        field=fixture_field(panel.request())
        prior_handle=panel.handle
        panel.field_ready('fixture',field)
        assert prior_handle.removed and panel.handle is not prior_handle
        assert panel.elevation_bound()>panel.amplitude.value()
        assert f'{panel.elevation_bound():.3g}' in panel.elevation_legend.text()
        assert panel.mode.currentIndex()==2 and panel.handle is not None
        panel.timer.stop()
        w._change_renderer('software');app.processEvents()
        assert panel.handle is not None and panel.active
        np.testing.assert_allclose(panel.handle.mesh.positions,positions_at_time(field,panel.time,disturbed=True),atol=1e-12)
        panel.mode.setCurrentIndex(0)
        assert not panel.active and panel.handle is None
        for bid,h in w._handles.items():np.testing.assert_array_equal(h.transform,original[bid])
        monkeypatch.setattr(w._job_manager,'_pump',lambda:None)
        panel.calculate()
        job=w._job_manager.job(panel.job_id)
        assert job.state=='queued'
        panel.cancel()
        assert job.state=='cancelled' and job.input_path.exists()
        assert not w._job_manager.active_jobs()
        prior_field=panel.field
        panel.field_ready('late-old-job',field)
        assert panel.field is prior_field
        panel.mode.setCurrentIndex(1)
        assert panel.timer.isActive()
        monkeypatch.setattr(w,'_confirm_discard',lambda:False)
        from PySide6.QtGui import QCloseEvent
        event=QCloseEvent();w.closeEvent(event)
        assert not event.isAccepted() and panel.timer.isActive()
    finally:
        w._waves.reset();w._dirty=False;w._confirm_discard=lambda:True;w.close();app.processEvents()

def test_backend_public_contract_with_test_double(monkeypatch):
    """Exercise adapter arguments and phasors, with no actual BEM calculation."""
    import sys,types,importlib.metadata
    import anyhydro.wave_preview as hydro
    calls=[]
    class Mesh:
        def __init__(self,vertices,faces):self.nb_faces=len(faces)
        def immersed_part(self,*,water_depth):calls.append(('clip',water_depth));return self
    class Body:
        def __init__(self,mesh,name):self.mesh=mesh;self.name=name
    class Multi:
        def __init__(self,bodies,*,name):self.mesh=types.SimpleNamespace(nb_faces=sum(b.mesh.nb_faces for b in bodies))
    class Solver:
        def __init__(self,*,green_function):calls.append(('solver',green_function))
        def solve(self,problem,*,keep_details,n_threads):
            calls.append(('solve',problem,keep_details,n_threads));return object()
        def compute_free_surface_elevation(self,points,result):
            assert points.shape[1]==2
            return np.full(len(points),.2+.1j)
    def green(**kwargs):calls.append(('green',kwargs));return 'direct-integrals'
    monkeypatch.setitem(sys.modules,'capytaine',types.SimpleNamespace(Mesh=Mesh,FloatingBody=Body,
        Multibody=Multi,BEMSolver=Solver,Delhommeau=green,DiffractionProblem=lambda **kw:kw))
    monkeypatch.setattr(importlib.metadata,'version',lambda name:'3.0.0')
    r=request();r.update(phase=.7,heading_degrees=33.)
    f=hydro.compute_diffraction_preview(r)
    eta=np.asarray(f['diffraction_real'])+1j*np.asarray(f['diffraction_imag'])
    np.testing.assert_allclose(eta,r['amplitude']*(.2+.1j)*np.exp(.7j),atol=1e-12)
    solves=[call for call in calls if call[0]=='solve']
    assert len(solves)==1 and solves[0][2:]==(True,1)
    assert solves[0][1]['omega']==r['omega']
    np.testing.assert_allclose(solves[0][1]['wave_direction'],np.deg2rad(33.))
    settings=[call[1] for call in calls if call[0]=='green'][0]
    assert settings['tabulation_nr']==settings['tabulation_nz']==0
    assert settings['tabulation_cache_dir'] is None
    assert f['solver_settings']==hydro.SOLVER_SETTINGS
    with pytest.raises(InterruptedError):hydro.compute_diffraction_preview(r,cancel=lambda:True)

def test_nonmanifold_envelope_refused():
    from anyhydro.wave_preview import validate_request
    r=request();r['bodies'][0]['triangles']*=2
    with pytest.raises(ValueError,match='closed consistently oriented'):validate_request(r)

@pytest.mark.parametrize('mode',('completed','unsealed','crash','deadline','close'))
def test_wave_process_transport_and_terminal_paths(tmp_path,monkeypatch,mode):
    """Real Qt child processes carrying labelled test data; no numerical backend."""
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication,QMessageBox
    import anymarine.qtapp.jobs as jobs
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([])
    w=MainWindow(viewer_backend='software');manager=w._job_manager
    manager._workroot=tmp_path
    r=request();snapshot=wave_input_snapshot(r)
    fixture=tmp_path/'declared-test-fixture.json'
    fixture.write_text(json.dumps(dict(kind='wave_preview',input_hash=snapshot['input_hash'],field=fixture_field(r))),encoding='utf-8')
    runner=tmp_path/'transport-test-only.py'
    runner.write_text('import sys,time,hashlib\nfrom pathlib import Path\n'
        'mode,output,fixture=sys.argv[1:]\n'
        'if mode in ("deadline","close","crash"):time.sleep(60)\n'
        'else:\n p=Path(output);p.write_bytes(Path(fixture).read_bytes())\n'
        ' if mode=="completed":print("OUTPUT_SHA256 "+hashlib.sha256(p.read_bytes()).hexdigest(),flush=True)\n',encoding='utf-8')
    original=QProcess.setArguments
    def fixture_arguments(process,args):
        original(process,[str(runner),mode,args[3],str(fixture)])
    monkeypatch.setattr(QProcess,'setArguments',fixture_arguments)
    monkeypatch.setattr(jobs,'CANCEL_GRACE_MS',50)
    received=[];manager.wave_ready.connect(lambda jid,f:received.append((jid,f)))
    if mode=='crash':
        start=QProcess.start
        def kill_on_start(process,*args):process.started.connect(process.kill);start(process,*args)
        monkeypatch.setattr(QProcess,'start',kill_on_start)
    jid=manager.submit_wave(r,wall_seconds=.1 if mode=='deadline' else 20.)
    try:
        if mode=='close':
            monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
            assert w.close()
        deadline=time.monotonic()+2.
        while manager.active_jobs() and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        assert not manager.active_jobs()
        job=manager.job(jid)
        assert job.state==('completed' if mode=='completed' else 'cancelled' if mode=='close' else 'failed')
        assert job.stdout_path.exists() and job.stderr_path.exists() and job.input_path.exists()
        assert bool(received)==(mode=='completed')
        if mode=='deadline':assert job.deadline_exceeded
        assert not w._project or not w._project.results
    finally:
        manager.cancel_all();w._dirty=False;w.close();app.processEvents()
