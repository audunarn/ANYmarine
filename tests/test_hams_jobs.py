# SPDX-License-Identifier: MPL-2.0
"""Ordinary lifecycle fixtures only: no real numerical executable is launched."""
from pathlib import Path
from dataclasses import replace
import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import time

import numpy as np
import pytest

from anymarine.hams_jobs import (hams_input_snapshot, validate_snapshot, validate_config,
                                run_hams_job, load_hams_output, CONFIG_FORMAT)
from anymarine.hams_wave_replay import load_hams_wave_example


@pytest.fixture
def case(tmp_path, monkeypatch):
    from anyhydro.hams_producer import HamsProducerBuild, GRAVITY_SOURCE_IDENTITY
    from anyhydro.hams_backend import HamsExecutionLimits
    source = Path(os.environ['HAMS_QT_STORED_OUTPUT'])
    ledger = Path(os.environ['HAMS_QT_FIXTURE_LEDGER'])
    project = load_hams_wave_example().project
    eye = np.eye(6).tolist(); zero = np.zeros((6, 6)).tolist()
    exe = Path(sys.executable).resolve()
    build = HamsProducerBuild(exe, hashlib.sha256(exe.read_bytes()).hexdigest(), GRAVITY_SOURCE_IDENTITY,
                              'windows' if os.name == 'nt' else 'linux', 'test-private Python copying saved numerical evidence; not a HAMS build')
    config = dict(format=CONFIG_FORMAT, producer=dict(identity=build.identity(), executable_path=str(exe)),
        request=dict(omegas=[.7], wave_directions=[0.], water_depth=150., rho=1000., gravity=9.80665),
        mechanics={b.body_id:dict(center_of_gravity=list(b.reference_position), mass=eye,
            linear_damping=zero, quadratic_damping=zero, hydrostatic_restoring=zero, external_restoring=zero) for b in project.bodies},
        center_of_buoyancy={b.body_id:[b.reference_position[0], 0., -.25] for b in project.bodies},
        field_points=[[-6.,-4.,0.],[6.,-4.,0.],[6.,4.,0.],[-6.,4.,0.],[-6.,-4.,-2.],[6.,-4.,-2.],[6.,4.,-2.],[-6.,4.,-2.]],
        execution_limits=HamsExecutionLimits(wall_seconds=12.).identity(), irregular_frequency_removal=True, allow_mechanical_rounding=False)
    script = tmp_path/'producer.py'
    script.write_text('''import pathlib,sys,os,json,shutil,time,subprocess
mode,source,ledger=sys.argv[1:]; path=pathlib.Path.cwd()
with pathlib.Path(ledger).open('a') as out:out.write(json.dumps({'kind':'producer','pid':os.getpid(),'mode':mode})+'\\n')
(path/'started').write_text(str(os.getpid()))
print(json.dumps({key:os.environ.get(key) for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')}),flush=True)
if mode=='tree':
 child='import pathlib,time,os,json; p=pathlib.Path("heartbeat"); n=0\\nwith pathlib.Path('+repr(ledger)+').open("a") as out: out.write(json.dumps({"kind":"descendant","pid":os.getpid()})+"\\\\n")\\nwhile True:\\n p.write_text(str(n));n+=1;time.sleep(.01)'
 subprocess.Popen([sys.executable,'-c',child]);time.sleep(50)
if mode=='delay':time.sleep(.4)
if mode=='crash':
 (path/'Output/partial.txt').write_text('partial');print('preserved crash',file=sys.stderr,flush=True);sys.exit(7)
for item in pathlib.Path(source).rglob('*'):
 if item.is_file():
  target=path/'Output'/item.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(item,target)
if mode=='missing':(path/'Output/Wamit_format/PressureElevation.6p').unlink()
print('fixture finished',flush=True)
''', encoding='utf-8')
    mode=['success']
    import anyhydro.hams_backend as hb
    monkeypatch.setattr(hb, '_command', lambda b:(str(b.executable_path),str(script),mode[0],str(source),str(ledger)))
    return dict(project=project,config=config,mode=mode,tmp=tmp_path,script=script,source=source,ledger=ledger)


def execute(c):
    snap=hams_input_snapshot(c['project'],c['config']);out=c['tmp']/'output.json'
    with contextlib.redirect_stdout(io.StringIO()) as stdout:
        code=run_hams_job(snap,out,None)
    return snap,out,code,stdout.getvalue()


def test_shared_worker_seal_reopen_and_authoritative_arrays(case):
    snap,out,code,text=execute(case);assert code==0, text
    seal=[s.split(' ',1)[1] for s in text.splitlines() if s.startswith('OUTPUT_SHA256 ')][0]
    r=load_hams_output(out,snap,seal)
    assert r.matches(case['project']) and len(r.surface_indices)==4
    np.testing.assert_array_equal(r.positions('total',.6),load_hams_wave_example().positions('total',.6))
    run=out.parent/json.loads(out.read_text())['run_directory']; assert (run/'completed.json').exists()
    env=json.loads((run/'stdout.log').read_text().splitlines()[0]);assert set(env.values())=={'1'}
    (run/'fields.json').unlink()
    with pytest.raises(Exception):load_hams_output(out,snap,seal)


@pytest.mark.parametrize('mode',['crash','missing'])
def test_failed_or_missing_native_fields_never_seal(case,mode):
    case['mode'][0]=mode;snap,out,code,text=execute(case)
    assert code==4 and 'OUTPUT_SHA256' not in text and not out.exists()
    assert json.loads(out.with_suffix('.incomplete.json').read_text())['status']=='failed'
    run=next((out.parent/'hams-runs').iterdir());assert (run/'stdout.log').exists() and not (run/'completed.json').exists()


def test_cancel_before_native_launch_preserves_incomplete(case):
    cancel=case['tmp']/'cancel';cancel.touch();snap=hams_input_snapshot(case['project'],case['config']);out=case['tmp']/'output.json'
    assert run_hams_job(snap,out,cancel)==3
    assert not (out.parent/'hams-runs').exists() and not out.exists()


@pytest.mark.parametrize('field',['input_hash','project_input_hash','execution_limits','project','config'])
def test_snapshot_mutation_refuses(case,field):
    snap=hams_input_snapshot(case['project'],case['config'])
    if field in ('input_hash','project_input_hash'):snap[field]='0'*64
    elif field=='execution_limits':snap[field]['wall_seconds']=3.
    elif field=='project':snap[field]['name']='edited'
    else:snap[field]['field_points'][0][0]-=1.
    with pytest.raises(Exception):validate_snapshot(snap)


@pytest.mark.parametrize('field',['rho_bool','mechanics_bool','cb_text','source','limits','no_surface','extra'])
def test_config_does_not_coerce_or_broaden_contract(case,field):
    c=copy.deepcopy(case['config']);bid=case['project'].bodies[0].body_id
    if field=='rho_bool':c['request']['rho']=True
    elif field=='mechanics_bool':c['mechanics'][bid]['mass'][0][0]=True
    elif field=='cb_text':c['center_of_buoyancy'][bid][0]='1'
    elif field=='source':c['producer']['identity']['source_tree_sha256']='0'*64
    elif field=='limits':c['execution_limits']['wall_seconds']=61.
    elif field=='no_surface':c['field_points']=[[-6.,-4.,-2.]]
    else:c['launch_prefix']=['wsl']
    with pytest.raises(Exception):hams_input_snapshot(case['project'],c)


def test_non_native_build_refuses_before_queue_and_no_implicit_launcher(case):
    c=copy.deepcopy(case['config']);c['producer']['identity']['platform']='linux' if os.name=='nt' else 'windows'
    with pytest.raises(Exception,match='format|platform|Windows'):hams_input_snapshot(case['project'],c)


def _wait(app,predicate,seconds=18.):
    end=time.perf_counter()+seconds
    while not predicate() and time.perf_counter()<end:
        app.processEvents();time.sleep(.005)
    assert predicate(),'bounded Qt job wait expired'


def private_qprocess(monkeypatch,c):
    """Only tests inject the native fixture into the otherwise public worker."""
    from PySide6.QtCore import QProcess
    helper=c['tmp']/'worker-fixture.py'
    helper.write_text('''import sys
from pathlib import Path
import anyhydro.hams_backend as hb
from anymarine.worker import run
script,mode,source,ledger,inp,out,cancel=sys.argv[1:]
hb._command=lambda b:(str(b.executable_path),script,mode,source,ledger)
sys.exit(run(Path(inp),Path(out),Path(cancel)))
''',encoding='utf-8')
    class FixtureProcess(QProcess):
        def setArguments(self,args):
            super().setArguments([str(helper),str(c['script']),c['mode'][0],str(c['source']),str(c['ledger']),*args[-3:]])
    import anymarine.qtapp.jobs as jobs
    monkeypatch.setattr(jobs,'QProcess',FixtureProcess)


@pytest.mark.parametrize('mode,expected',[('success','completed'),('crash','failed'),('missing','failed')])
def test_real_qprocess_worker_completion_contract(case,monkeypatch,mode,expected):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.jobs import JobManager
    from anymarine.services import ApplicationService
    app=QApplication.instance() or QApplication([]);case['mode'][0]=mode;private_qprocess(monkeypatch,case)
    manager=JobManager(ApplicationService());manager._workroot=case['tmp']/'qt';received=[]
    manager.hams_ready.connect(lambda jid,r:received.append(r))
    jid=manager.submit_hams(case['project'],case['config'])
    _wait(app,lambda:not manager.active_jobs());assert manager.job(jid).state==expected
    assert len(received)==int(expected=='completed')
    assert manager.job(jid).stdout_path.exists() and manager.job(jid).stderr_path.exists()


def test_qprocess_cancel_queue_and_forced_supervisor_death_kill_descendants(case,monkeypatch):
    if os.name!='nt' and not sys.platform.startswith('linux'):pytest.skip('Supported Windows/Linux ownership contract')
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.jobs import JobManager
    from anymarine.services import ApplicationService
    app=QApplication.instance() or QApplication([]);case['mode'][0]='tree';private_qprocess(monkeypatch,case)
    import anymarine.qtapp.jobs as jobs
    monkeypatch.setattr(jobs,'CANCEL_GRACE_MS',100)
    manager=JobManager(ApplicationService());manager._workroot=case['tmp']/'qt';received=[]
    manager.hams_ready.connect(lambda *a:received.append(a))
    jid=manager.submit_hams(case['project'],case['config']);record=manager.job(jid)
    queued=manager.submit_hams(case['project'],case['config']);manager.request_cancel(queued)
    assert manager.job(queued).state=='cancelled'
    _wait(app,lambda:bool(list(record.input_path.parent.glob('hams-runs/*/heartbeat'))) or not manager.active_jobs())
    assert list(record.input_path.parent.glob('hams-runs/*/heartbeat')),record.diagnostics
    heartbeat=next(record.input_path.parent.glob('hams-runs/*/heartbeat'))
    # Kill the supervisor directly: cancellation callbacks cannot do the cleanup.
    record.process.kill();_wait(app,lambda:not manager.active_jobs());assert record.state=='failed'
    time.sleep(.12);before=heartbeat.read_bytes();time.sleep(.12);assert heartbeat.read_bytes()==before
    assert not received


def test_qt_display_admission_and_stale_project_retains_artifact(case,monkeypatch):
    from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([]);private_qprocess(monkeypatch,case)
    config=case['tmp']/'config.json';config.write_text(json.dumps(case['config']))
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(config),''))
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    w=MainWindow(viewer_backend='software');w._job_manager._workroot=case['tmp']/'qt'
    monkeypatch.setattr(w,'_notify_warning',lambda *a:pytest.fail(str(a)))
    try:
        w.show();w._set_project(case['project']);w._run_hams_job()
        _wait(app,lambda:not w._job_manager.active_jobs());assert w._waves.field['kind']=='stored_hams_points'
        w._waves.timer.stop();case['mode'][0]='delay';w._run_hams_job()
        body=w._project.bodies[0]
        changed=body.with_edits(reference_position=(body.reference_position[0]+.1,*body.reference_position[1:]))
        w._set_project(w._project.with_edits(bodies=(changed,*w._project.bodies[1:])))
        _wait(app,lambda:not w._job_manager.active_jobs());assert w._waves.field is None
        assert 'retained without display' in w._diagnostics.toPlainText()
        # Selection can change without changing the project input hash.
        from anymarine.synthetic import generate_synthetic_replay
        w._set_project(case['project']);case['mode'][0]='delay';w._run_hams_job()
        from anymarine.hashing import input_hash
        w._current_result=generate_synthetic_replay(w._project,input_hash=input_hash(w._project))
        _wait(app,lambda:not w._job_manager.active_jobs());assert w._waves.field is None
        assert w._current_result is not None
    finally:
        w._dirty=False;w._confirm_discard=lambda:True;w.close();app.processEvents()


def test_native_receipt_cannot_be_relabelled_as_other_snapshot(case):
    snap,out,code,text=execute(case);assert code==0,text
    payload=json.loads(out.read_text());bid=case['project'].bodies[0].body_id
    for kind in ('centers','bounds','wall','rounding'):
        config=copy.deepcopy(case['config'])
        if kind=='centers':config['center_of_buoyancy'][bid][2]-=.1
        elif kind=='bounds':config['execution_limits']['max_field_points']=2048
        elif kind=='wall':config['execution_limits']['wall_seconds']=1.
        else:config['allow_mechanical_rounding']=True
        changed=hams_input_snapshot(case['project'],config);forged=dict(payload,input_hash=changed['input_hash'])
        out.write_text(json.dumps(forged));seal=hashlib.sha256(out.read_bytes()).hexdigest()
        with pytest.raises(Exception,match='CB|controls|limits'):
            load_hams_output(out,changed,seal)


def test_intermediate_owned_directory_reparse_refuses(case,monkeypatch):
    from types import SimpleNamespace
    snap,out,code,text=execute(case);assert code==0,text
    original=Path.lstat
    def lstat(path,*a,**kw):
        info=original(path,*a,**kw)
        if path==out.parent/'hams-runs':return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(Exception,match='reparse'):
        load_hams_output(out,snap,hashlib.sha256(out.read_bytes()).hexdigest())


@pytest.mark.parametrize('mutation',['inward','degenerate','open'])
def test_winding_never_inferred_from_unverified_display_mesh(case,mutation):
    from anymarine.domain import BodyMesh
    body=case['project'].bodies[0];triangles=list(body.mesh.triangles)
    if mutation=='inward':triangles=[tuple(reversed(t)) for t in triangles]
    elif mutation=='degenerate':triangles[0]=(triangles[0][0],)*3
    else:triangles.pop()
    altered=replace(body,mesh=BodyMesh(body.mesh.positions,tuple(triangles)))
    project=replace(case['project'],bodies=(altered,*case['project'].bodies[1:]))
    with pytest.raises(Exception,match='Hull|hull|Degenerate|Repeated|lid'):
        hams_input_snapshot(project,case['config'])


def test_active_job_application_closure_cancels_owned_native_child(case,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([]);case['mode'][0]='tree';private_qprocess(monkeypatch,case)
    w=MainWindow(viewer_backend='software');w._job_manager._workroot=case['tmp']/'qt';w._set_project(case['project'])
    w._dirty=False;w._confirm_discard=lambda:True
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    w.show();jid=w._job_manager.submit_hams(w._project,case['config']);record=w._job_manager.job(jid)
    _wait(app,lambda:bool(list(record.input_path.parent.glob('hams-runs/*/heartbeat'))) or not w._job_manager.active_jobs())
    assert list(record.input_path.parent.glob('hams-runs/*/heartbeat')),record.diagnostics
    heartbeat=next(record.input_path.parent.glob('hams-runs/*/heartbeat'))
    w.close();app.processEvents();assert not w._job_manager.active_jobs() and record.state=='cancelled'
    time.sleep(.1);before=heartbeat.read_bytes();time.sleep(.1);assert heartbeat.read_bytes()==before
    assert not w.isVisible() and w._waves.field is None


def test_native_deadline_failure_has_no_completed_delivery(case):
    case['mode'][0]='tree';case['config']['execution_limits']['wall_seconds']=.6
    snap,out,code,text=execute(case)
    assert code==4 and 'OUTPUT_SHA256' not in text and not out.exists()
    assert 'deadline' in json.loads(out.with_suffix('.incomplete.json').read_text())['reason'].lower()


def test_cli_native_config_inspection_and_missing_platform(case,capsys):
    from anymarine.cli import main
    config=case['tmp']/'config.json';config.write_text(json.dumps(case['config']))
    assert main(['inspect-hams-config','--config',str(config)])==0
    assert json.loads(capsys.readouterr().out)['producer_available'] is True
    other=copy.deepcopy(case['config']);other['producer']['identity']['platform']='linux' if os.name=='nt' else 'windows'
    config.write_text(json.dumps(other));assert main(['inspect-hams-config','--config',str(config)])==0
    report=json.loads(capsys.readouterr().out);assert not report['producer_available'] and not report['numerical_calculation']


def test_cli_isolated_worker_runs_and_retains_portable_bundle(case,monkeypatch,capsys):
    from anymarine import cli
    private_qprocess(monkeypatch,case)
    config=case['tmp']/'config.json';config.write_text(json.dumps(case['config']))
    original=cli.subprocess.Popen
    def fixture(command,**kwargs):
        args=[command[0],str(case['tmp']/'worker-fixture.py'),str(case['script']),case['mode'][0],str(case['source']),str(case['ledger']),*command[-3:]]
        return original(args,**kwargs)
    monkeypatch.setattr(cli.subprocess,'Popen',fixture)
    directory=case['tmp']/'cli-run'
    assert cli.main(['run-hams-fields','--config',str(config),'--workdir',str(directory)])==0
    report=json.loads(capsys.readouterr().out);assert report['status']=='completed_development_unqualified'
    assert (directory/'hams-wave-bundle/bundle.json').exists()


@pytest.mark.parametrize('first_interrupt',[True,False])
def test_cli_second_interrupt_and_postkill_timeout_always_cleanup(case,monkeypatch,first_interrupt):
    from anymarine import cli
    config=case['tmp']/'config.json';config.write_text(json.dumps(case['config']))
    class Fake:
        def __init__(self):self.waits=0;self.kills=0;self.code=None
        def wait(self,timeout=None):
            self.waits+=1
            if self.waits<=2:
                if first_interrupt:raise KeyboardInterrupt()
                raise cli.subprocess.TimeoutExpired('test-private no process',timeout)
            if self.waits==3:raise cli.subprocess.TimeoutExpired('test-private postkill timeout',timeout)
            self.code=-9;return self.code
        def kill(self):self.kills+=1
        def poll(self):return self.code
    child=Fake();monkeypatch.setattr(cli.subprocess,'Popen',lambda *a,**k:child)
    root=case['tmp']/'cli-abort'
    assert cli.main(['run-hams-fields','--config',str(config),'--workdir',str(root)])==(130 if first_interrupt else 1)
    assert child.kills==2 and child.waits==4 and child.poll() is not None
    status=json.loads((root/'job-status.json').read_text())
    assert not status['cleanup_uncertain'] and status['cleanup_errors'] and status['state'] in ('cancelled','failed')
