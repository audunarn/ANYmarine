"""Recipe3 pane/recording checks with stored artifacts; no geometry queries."""
import json,os
from pathlib import Path
import pytest
from conftest import requires_qt
pytestmark=requires_qt
from PySide6.QtWidgets import QApplication,QMessageBox,QFileDialog
from anymarine.services import ApplicationService
from anymarine.qtapp.reference_barge import ReferenceBargePane,Recipe3StabilityHost
from anymarine import reference_barge as r

@pytest.fixture
def app():return QApplication.instance() or QApplication([])
@pytest.fixture
def pane(app,monkeypatch):
 if not os.environ.get('ANYMARINE_REFERENCE_BARGE_DIR'):pytest.skip('private local recipe3 data requires explicit ANYMARINE_REFERENCE_BARGE_DIR')
 messages=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:messages.append(args[-1]))
 records=[];p=ReferenceBargePane(ApplicationService(),recorder=lambda *args:records.append(args));p._test_records=records;p._test_messages=messages
 yield p
 p.shutdown();p.close();app.processEvents()

def test_default_model_tanks_conditions_and_legacy_is_explicit(app,pane):
 assert pane.study.selected_case=='coupled' and pane.tank_table.rowCount()==8 and pane.calibration_table.rowCount()==40
 assert pane.ledger.rowCount()==2 and '236304.32' in pane.summary.text()
 assert pane.preview.renderer().isValid()
 for name in r.CASES:
  pane.case_selector.setCurrentText(name);assert pane.study.selected_case==name and pane.tank_table.rowCount()==8
  assert 'ILLUSTRATIVE' in pane.summary.text()
 host=Recipe3StabilityHost(ApplicationService());assert host.reference is not None and host._legacy is None
 host.open_legacy_box();assert host.study is not None and host.study.result is None
 host.shutdown();host.close()

def test_invalid_controls_rollback_and_dirty_switch_recording(pane):
 before=pane.study.identity;pane.controls.setPlainText('{broken');pane.case_selector.setCurrentText('upright')
 assert pane.study.identity==before and pane.case_selector.currentText()=='coupled'
 assert pane._test_messages and pane._test_records[-1][2]=='refused'
 controls=r.reference_controls(pane.study);controls['max_wall_seconds']=60
 pane.controls.setPlainText(json.dumps(controls));pane.case_selector.setCurrentText('combined')
 assert pane.study.selected_case=='combined'
 assert r.reference_controls(pane.study)['max_wall_seconds']==60
 code=pane._test_records[-1][1];assert 'reference_barge_controls' in code and 'select_reference_barge_case' in code
 assert code.index('reference_barge_controls')<code.index('select_reference_barge_case')

def test_cancelled_run_dialog_does_not_commit_controls(pane,monkeypatch):
 before=pane.study.identity;controls=r.reference_controls(pane.study);controls['max_wall_seconds']=60
 pane.controls.setPlainText(json.dumps(controls));count=len(pane._test_records)
 monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs:('',''))
 pane.run();assert pane.study.identity==before and pane._dirty and len(pane._test_records)==count

def test_cancel_button_records_explicit_user_reason(pane):
 reasons=[]
 class Running:
  def cancel(self,reason):reasons.append(reason)
  def close(self):return True
 pane.job=Running();pane.cancel_button.setEnabled(True);pane.cancel_button.click()
 assert reasons==['user cancellation']
 assert pane._test_records[-1][1]=="barge_run.cancel('user cancellation')\n"
 pane.job=None

def test_accepted_run_recording_then_save_replays_actual_study(pane,tmp_path):
 attached=r.attach_coupled_result(pane.study,pane.study._root/'coupled-result.json')
 class Finished:
  status='completed';detail='';path=tmp_path/'retained-run'
  result=attached
  def poll(self):return 'completed'
  def close(self):return True
  def finish(self):return self.result
 job=Finished();pane.job=job;pane._job_identity=pane.study.identity;pane.poll()
 assert pane.study.identity==attached.identity and pane._test_records[-1][1]=='barge_study = barge_run.finish()\n'
 target=tmp_path/'saved';pane.save_study(target)
 source=''.join(record[1] for record in pane._test_records if record[2]=='success')
 assert 'barge_saved.identity == barge_study.identity' in source
 replay_path=tmp_path/'replayed';source=source.replace(repr(str(target.absolute())),repr(str(replay_path.absolute())))
 scope={'service':pane.service,'barge_run':job};exec(compile(source,'<recorded recipe3>','exec'),scope)
 assert scope['barge_saved'].identity==pane.study.identity
 assert r.load_reference_barge(replay_path).identity==attached.identity

@pytest.mark.skipif(not os.environ.get('ANYMARINE_REFERENCE_BARGE_DIR'),reason='private local recipe3 data not configured')
def test_scripts_default_is_valid_owner_free_source():
 from anymarine.qtapp.scripts import MARINE_EXAMPLE
 scope={};exec(compile(MARINE_EXAMPLE,'<initial Scripts example>','exec'),scope)
 assert scope['barge_study'].selected_case=='coupled'

def test_fresh_save_open_replay_identity_map_multisave_external_and_clear(pane,tmp_path):
 from anystability.reference.coupled import load_coupled_result,CoupledResult,save_coupled_result
 from anystability.validation import canonical_bytes
 from anymarine.qtapp.scripts import PREAMBLE
 baseline=pane.study
 original=r.attach_coupled_result(baseline,baseline._root/'coupled-result.json')
 data=load_coupled_result(baseline._root/'coupled-result.json').to_dict()
 data['work']['elapsed_seconds']+=.125 # Legitimately different runtime receipt, same physical result.
 alternate_path=tmp_path/'alternate-result.json';save_coupled_result(CoupledResult(canonical_bytes(data)),alternate_path)
 alternate=r.attach_coupled_result(baseline,alternate_path)
 assert alternate.identity!=original.identity
 class Finished:
  status='completed';detail='';path=tmp_path/'original-run';result=original
  def poll(self):return 'completed'
  def close(self):return True
  def finish(self):return self.result
 pane.job=Finished();pane._job_identity=baseline.identity;pane.poll()
 first=tmp_path/'first';second=tmp_path/'second';external=tmp_path/'external'
 pane.save_study(first);pane.save_study(second);pane.load_default();pane.open_study(first);pane.open_study(second)
 assert "globals().get('barge_saved_identities'" in pane._test_records[-1][1]
 pane.service.save_reference_barge(baseline,external);pane.open_study(external)
 assert 'globals().get' not in pane._test_records[-1][1] # External artifact retains literal pin.
 source=PREAMBLE+''.join(record[1] for record in pane._test_records if record[2]=='success')
 replay_first=tmp_path/'replay-first';replay_second=tmp_path/'replay-second'
 source=source.replace(repr(str(first.absolute())),repr(str(replay_first.absolute()))).replace(repr(str(second.absolute())),repr(str(replay_second.absolute())))
 replay_job=Finished();replay_job.result=alternate
 scope={'barge_run':replay_job};exec(compile(source,'<changed elapsed replay>','exec'),scope)
 assert r.load_reference_barge(replay_first).identity==alternate.identity
 assert r.load_reference_barge(replay_second).identity==alternate.identity
 assert scope['barge_saved_identities'][str(replay_first.absolute())]==alternate.identity
 # A cleared recording has a new preamble/map and safely falls back to the current literal pin.
 pane._test_records.clear();pane.open_study(first)
 cleared=PREAMBLE+pane._test_records[-1][1]
 scope={'barge_saved_identities':{str(first.absolute()):'stale namespace value'}}
 exec(compile(cleared,'<after clear>','exec'),scope)
 assert scope['barge_study'].identity==original.identity and scope['barge_saved_identities']=={}

def test_unconfirmed_stability_close_preserves_app_timers_and_scripts(app,monkeypatch):
 from PySide6.QtGui import QCloseEvent
 from anymarine.qtapp.mainwindow import MainWindow
 window=MainWindow(viewer_backend='software');closed=[]
 class Stability:
  def shutdown(self):return False
 class Scripts:
  def shutdown(self):closed.append(True)
 window._stability_host=Stability();window._scripts_host=Scripts()
 monkeypatch.setattr(window,'_confirm_discard',lambda:True)
 window._fit_timer.start(10000);window._play_timer.start(10000);window._waves.timer.start(10000)
 event=QCloseEvent();window.closeEvent(event)
 assert not event.isAccepted() and not closed
 assert window._fit_timer.isActive() and window._play_timer.isActive() and window._waves.timer.isActive()
 window._stability_host=None;window._scripts_host=None;window._dirty=False;window.close()
