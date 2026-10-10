"""Actual isolated adoption audits, Qt commit guards and cancellation; no solves."""
from pathlib import Path
import time,json
import pytest
import numpy as np
from PySide6.QtWidgets import QApplication,QMessageBox,QDialog
from PySide6.QtCore import QProcess
from PySide6.QtGui import QCloseEvent
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.qtapp.jobs import JobManager,JobRecord
from anymarine.services import ApplicationService
from anymarine.equilibrium_transition import AdoptionDomain,EquilibriumRegeneration
from anymarine.potential_flow_inputs import NativePotentialFlowInputs
from anymarine.adoption_jobs import load_output
from test_adoption_jobs import records

@pytest.fixture(scope='module')
def app():return QApplication.instance() or QApplication([])

def wait(app,predicate,timeout=12.):
    end=time.monotonic()+timeout
    while time.monotonic()<end and not predicate():app.processEvents();time.sleep(.005)
    app.processEvents();assert predicate(),'Qt job did not reach expected terminal state'

def record_job(record):
    evidence=Path(__import__('os').environ['ANYMARINE_ADOPTION_EVIDENCE'])
    evidence.mkdir(parents=True,exist_ok=True)
    path=evidence/(record.job_id+'.json')
    path.write_text(json.dumps(dict(job_id=record.job_id,state=record.state,pid=record.process.processId() if record.process else None,
        snapshot_hash=record.input_hash,input=str(record.input_path),output=str(record.output_path),seal=record.output_seal,
        stdout=str(record.stdout_path),stderr=str(record.stderr_path),diagnostics=record.diagnostics),indent=2))

def test_qt_adoption_commit_save_reopen_and_stored_replay(app,tmp_path,monkeypatch):
    p,r,q,g=records();w=MainWindow(viewer_backend='software')
    try:
        w._set_project(p);w._set_result(r);source=tmp_path/'active'
        w._service.save_current(p,source);w._project_root=source;before=(source/'project.json').read_bytes()
        jid=w._queue_adoption(r,domain=AdoptionDomain(10.),regeneration=g,wall_seconds=8.)
        rec=w._job_manager.job(jid)
        with pytest.raises(TypeError):rec.snapshot['project']['name']='changed'
        wait(app,lambda:rec.state not in ('queued','running','cancelling'));record_job(rec)
        assert rec.state=='completed' and w._project.to_dict()==q.to_dict()
        assert w._dirty and w._current_result is None and w._replay_project is None
        assert (source/'project.json').read_bytes()==before
        w._service.save_current(w._project,source);reopened=w._service.load(source)
        assert reopened.to_dict()==q.to_dict()
        assert len(w._handles)==2 and w._member_scene.line_nodes is not None
        w._set_result(r)
        assert 'stale' in w._result_status
        for i in range(r.frame_count()):
            w._apply_frame(i)
            np.testing.assert_array_equal(w._member_scene.line_nodes,r.line_nodes[i])
        # A late validated output never overwrites new edits or a different project.
        edited=p.with_edits(name='user edit');w._set_project(edited)
        w._on_adoption_ready(jid,q)
        assert w._project.to_dict()==edited.to_dict() and 'detached' in w._diagnostics.toPlainText()
    finally:
        w._job_manager.cancel_all();wait(app,lambda:not w._job_manager.active_jobs())
        w._dirty=False;w.close();app.processEvents()

def test_failed_regeneration_is_not_completed_and_keeps_project(app):
    p,r,q,g=records();w=MainWindow(viewer_backend='software')
    try:
        w._set_project(p);fluid=g.potential_flow.to_dict();fluid['baseline']=[0.]*12
        bad=EquilibriumRegeneration(NativePotentialFlowInputs(fluid))
        jid=w._queue_adoption(r,domain=AdoptionDomain(10.),regeneration=bad,wall_seconds=8.)
        rec=w._job_manager.job(jid);wait(app,lambda:rec.state not in ('queued','running','cancelling'));record_job(rec)
        assert rec.state=='failed' and w._project.to_dict()==p.to_dict()
        assert rec.output_path.with_suffix('.failure.json').exists() and not rec.output_seal
    finally:w._dirty=False;w.close();app.processEvents()

def test_cancelled_job_and_queue_do_not_publish_adoption(app):
    p,r,q,g=records();manager=JobManager(ApplicationService());ready=[];manager.adoption_ready.connect(lambda *a:ready.append(a))
    jid=manager.submit_adoption(p,r,domain=AdoptionDomain(10.),regeneration=g,wall_seconds=8.)
    rec=manager.job(jid);manager.request_cancel(jid)
    wait(app,lambda:rec.state not in ('queued','running','cancelling'));record_job(rec)
    assert rec.state=='cancelled' and not ready
    # Deterministic queued cancellation starts no numerical worker.
    manager._jobs['blocker']=JobRecord(job_id='blocker',state='running')
    queued=manager.submit_adoption(p,r,domain=AdoptionDomain(10.),regeneration=g)
    manager.request_cancel(queued);assert manager.job(queued).state=='cancelled' and manager.job(queued).process is None
    manager._jobs['blocker'].state='completed'

def test_worker_crash_is_failed_and_no_candidate_is_emitted(app):
    p,r,q,g=records();manager=JobManager(ApplicationService());ready=[];manager.adoption_ready.connect(lambda *a:ready.append(a))
    jid=manager.submit_adoption(p,r,domain=AdoptionDomain(10.),regeneration=g,wall_seconds=8.)
    rec=manager.job(jid)
    assert rec.process.state()!=QProcess.ProcessState.NotRunning
    rec.process.kill()
    wait(app,lambda:rec.state not in ('queued','running','cancelling'));record_job(rec)
    assert rec.state=='failed' and not ready and rec.stderr_path.exists() and rec.stdout_path.exists()

def test_close_during_adoption_cancels_and_keeps_saved_project(app,tmp_path,monkeypatch):
    p,r,q,g=records();w=MainWindow(viewer_backend='software');w._set_project(p)
    w._service.save_current(p,tmp_path);w._project_root=tmp_path;before=(tmp_path/'project.json').read_bytes()
    jid=w._queue_adoption(r,domain=AdoptionDomain(10.),regeneration=g,wall_seconds=8.)
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k:QMessageBox.StandardButton.Yes)
    event=QCloseEvent();w.closeEvent(event);app.processEvents();rec=w._job_manager.job(jid);record_job(rec)
    assert event.isAccepted() and rec.state=='cancelled' and (tmp_path/'project.json').read_bytes()==before

def test_adoption_controls_select_result_and_freeze_file_data(app,tmp_path,monkeypatch):
    from anymarine.qtapp.adoption_dialog import AdoptionDialog
    from anymarine.adoption_jobs import regeneration_to_data
    p,r,q,g=records();w=MainWindow(viewer_backend='software');w._set_project(p);w._set_result(r)
    path=tmp_path/'regenerated.json';path.write_text(json.dumps(regeneration_to_data(g)))
    dialog=AdoptionDialog(p,r,w);dialog.dependencies.setText(str(path));params=dialog.parameters()
    assert params['regeneration'].potential_flow.to_dict()==g.potential_flow.to_dict()
    assert any(a.text()=='Adopt selected static equilibrium…' for a in w.findChildren(__import__('PySide6.QtGui',fromlist=['QAction']).QAction))
    calls=[]
    monkeypatch.setattr(AdoptionDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    monkeypatch.setattr(AdoptionDialog,'parameters',lambda self:params)
    monkeypatch.setattr(w,'_queue_adoption',lambda result,**options:calls.append((result,options)))
    w._adopt_equilibrium_dialog();assert calls[0][0] is r
    w._project=p.with_edits(settings=p.settings.with_edits(dt=.2))
    monkeypatch.setattr(w,'_notify_warning',lambda *a:None);w._adopt_equilibrium_dialog();assert len(calls)==1
    dialog.close();w._dirty=False;w.close();app.processEvents()

def test_malformed_dependency_file_is_reported_without_queueing(app,tmp_path,monkeypatch):
    from anymarine.qtapp.adoption_dialog import AdoptionDialog
    p,r,q,g=records();w=MainWindow(viewer_backend='software');w._set_project(p);w._set_result(r)
    path=tmp_path/'bad.json';path.write_text(json.dumps(dict(potential_flow=None,lids={'body':dict(positions=None,triangles=[])})))
    def accept(self):self.dependencies.setText(str(path));return QDialog.DialogCode.Accepted
    monkeypatch.setattr(AdoptionDialog,'exec',accept)
    warnings=[];monkeypatch.setattr(w,'_notify_warning',lambda title,text:warnings.append(text))
    monkeypatch.setattr(w,'_queue_adoption',lambda *a,**k:pytest.fail('queued malformed dependencies'))
    w._adopt_equilibrium_dialog()
    assert warnings and 'must be arrays' in warnings[0] and w._project.to_dict()==p.to_dict()
    w._dirty=False;w.close();app.processEvents()
