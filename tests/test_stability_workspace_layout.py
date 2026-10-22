"""Synthetic engineering workspace checks; no private data or geometry owner."""
import json
from dataclasses import dataclass,replace
import pytest
from PySide6.QtWidgets import QApplication,QFileDialog,QMessageBox
from anymarine.services import ServiceError
from anymarine.qtapp import reference_barge as ui

@dataclass(frozen=True)
class Study:
 selected_case:str='coupled'
 changed:bool=False
 identity:str='synthetic-study'
 def to_dict(self):return {'result':None}

class Service:
 def __init__(self):self.calls=[]
 def reference_barge_example(self):self.calls.append('load');return Study()
 def reference_barge_controls(self,study,raw):
  self.calls.append('controls')
  try:json.loads(raw)
  except ValueError as error:raise ServiceError('invalid controls') from error
  return replace(study,changed=True)
 def select_reference_barge_case(self,study,name):self.calls.append('select');return replace(study,selected_case=name)
 def reference_barge_view(self,study):
  self.calls.append('view')
  return dict(controls={'max_wall_seconds':5},evidence='Saved synthetic evidence',loading_label='ILLUSTRATIVE synthetic loads',case=study.selected_case,status='converged',
   mass_summary=None if study.changed else {'mass_kg':120000.25,'cg_body_m':[0.,0.,1.2]},pose={'heel_rad':.02,'trim_rad':.01,'origin_z_m':-.7},
   capacities={'TEST'+str(i):{'volume_m3':10.,'plan_capacity_m3':None} for i in range(8)},tank_solutions={},targets_m3={},densities_kg_m3={},
   calibration_rows=[],condition={'items':[{'item_id':'demonstration','mass_kg':120000.25,'cg_body_m':[0,0,1.2],'source':'synthetic only','covers':['synthetic']}]},
   diagnostics={'residual_Fz_Qheel_Qtrim':[.1,.2,.3],'net_moment_world_Nm':[.2,.3,0]},mass_basis='synthetic',base_condition={},definition_identity='synthetic',dataset_identity='synthetic',limitations=['synthetic only'])

@pytest.fixture
def app():return QApplication.instance() or QApplication([])
@pytest.fixture
def pane(app,monkeypatch):
 monkeypatch.setattr(ui,'reference_preview',lambda _:b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="300"><rect x="5" y="5" width="790" height="290" fill="#ddeefa"/></svg>')
 monkeypatch.setattr(QMessageBox,'warning',lambda *args:None)
 service=Service();records=[];widget=ui.ReferenceBargePane(service,recorder=lambda *args:records.append(args));widget.resize(900,650);widget.show();app.processEvents()
 yield widget,service,records
 widget.shutdown();widget.close();app.processEvents()

def test_primary_actions_and_cards_stay_visible_across_pages_and_resize(pane,app):
 widget,service,_=pane
 assert widget.case_selector.itemText(0)=='Floating equilibrium'
 assert widget.case_selector.currentText()=='coupled'
 assert '120.00 t' in widget.result_cards['mass'].text() and '120000.25' in widget.result_cards['mass'].toolTip()
 assert widget.result_table.rowCount()==2
 for width,height in [(900,650),(750,600)]:
  widget.resize(width,height);app.processEvents()
  assert widget.width()==width and widget.minimumSizeHint().width()<=width, [(widget.layout().itemAt(i).minimumSize().width()) for i in range(widget.layout().count())]
  assert widget.evidence_badge.isVisibleTo(widget) and widget.evidence_badge.width()>=150
  for index in range(widget.tabs.count()):
   widget.tabs.setCurrentIndex(index);app.processEvents()
   assert widget.run_button.isVisibleTo(widget) and widget.cancel_button.isVisibleTo(widget) and widget.allow.isVisibleTo(widget)
   assert widget.run_button.geometry().width()>60
  widget.tabs.setCurrentIndex(0);app.processEvents()
  assert widget.preview.width()<width and widget.preview.minimumWidth()==0 and widget.preview.renderer().isValid()
  assert widget.preview.canvas.width()>=widget.preview.scroll.viewport().width()-3
 assert set(service.calls)<= {'load','view'} # Layout/navigation never solves, parses or records another load.

def test_named_selection_dirty_refusal_and_result_invalidation(pane,app):
 widget,service,records=pane
 widget.case_selector.setCurrentText('combined');assert widget.study.selected_case=='combined' and not widget.run_button.isEnabled()
 before=widget.study
 widget.controls.setPlainText('{broken')
 assert all(card.text()=='Not paired' for card in widget.result_cards.values()) and widget.result_table.rowCount()==0
 widget.case_selector.setCurrentText('upright');assert widget.study==before and widget.case_selector.currentText()=='combined'
 assert records[-1][2]=='refused'
 widget.controls.setPlainText('{"max_wall_seconds":4}');widget.case_selector.setCurrentText('coupled')
 assert widget.study.changed and widget.study.selected_case=='coupled'
 assert 'reference_barge_controls' in records[-1][1] and 'select_reference_barge_case' in records[-1][1]

def test_cancelled_destination_preserves_active_study(pane,monkeypatch):
 widget,_,records=pane;before=widget.study;count=len(records)
 monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:('',''))
 widget.controls.setPlainText('{"max_wall_seconds":4}');widget.run()
 assert widget.study==before and widget.job is None and len(records)==count

def test_busy_run_cancel_and_dirty_completion_quarantine(pane,monkeypatch):
 widget,service,records=pane;reasons=[]
 assert not widget.cancel_button.isEnabled()
 class Job:
  path='synthetic-run';detail='synthetic';result=Study();state='running'
  def cancel(self,reason):reasons.append(reason)
  def poll(self):return self.state
  def close(self):return True
 job=Job()
 monkeypatch.setattr(service,'run_reference_barge',lambda *a,**k:job,raising=False)
 monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:('synthetic-run',''))
 widget.allow.setChecked(True);widget.run()
 assert widget.job is job and not widget.run_button.isEnabled() and widget.cancel_button.isEnabled()
 widget.cancel_button.click();assert reasons[-1]=='user cancellation'
 widget.controls.setPlainText('{"max_wall_seconds":3}')
 assert reasons[-1]=='controls edited' and widget._dirty
 before=widget.study;job.state='completed';widget.poll()
 assert widget.study is before and widget.job is None
 assert not widget.cancel_button.isEnabled()
 assert all(card.text()=='Not paired' for card in widget.result_cards.values())
 assert not any('barge_run.finish()' in record[1] for record in records)

def test_controls_edited_before_run_can_accept_matching_completion(pane,monkeypatch):
 widget,service,records=pane
 class Job:
  path='synthetic-run';detail='';result=Study();state='running'
  def cancel(self,reason):raise AssertionError('no post-start edit or cancel')
  def poll(self):return self.state
  def close(self):return True
 job=Job();monkeypatch.setattr(service,'run_reference_barge',lambda *a,**k:job,raising=False)
 monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:('synthetic-run',''))
 widget.controls.setPlainText('{"max_wall_seconds":4}');assert widget._dirty
 widget.allow.setChecked(True);widget.run()
 assert widget.job is job and not widget._dirty
 job.state='completed';widget.poll()
 assert widget.study is job.result and widget.job is None
 assert records[-1][1]=='barge_study = barge_run.finish()\n'
 assert not widget.cancel_button.isEnabled()

def test_missing_study_has_open_retry_and_explicit_example(app,monkeypatch):
 class Missing(Service):
  def reference_barge_example(self):raise ServiceError('LOCAL_DATA_UNAVAILABLE: synthetic missing setup')
 host=ui.Recipe3StabilityHost(Missing());host.resize(900,650);host.show();app.processEvents()
 assert host.reference is None and not host.tabs.tabBar().isVisibleTo(host)
 assert host.open_button.isVisibleTo(host) and host.retry_button.isVisibleTo(host)
 assert 'LOCAL_DATA_UNAVAILABLE' in host.diagnostic.text()
 host.shutdown();host.close()

@pytest.mark.parametrize('case',['combined','opposite','raked'])
def test_attitude_weight_wrench_is_visible_without_recalculation(pane,monkeypatch,case):
 widget,service,_=pane;original=service.reference_barge_view
 def view(study):
  result=original(study);result['diagnostics']={'force_world_N':[0,0,-500.25],'moment_world_Nm':[3.5,-4.25,0],'generalized_heel_trim_Nm':[3.4,4.25],'reference':'current_body_origin_world_axes'};return result
 monkeypatch.setattr(service,'reference_barge_view',view)
 widget.case_selector.setCurrentText(case)
 rows={widget.result_table.item(row,0).text():widget.result_table.item(row,1).text() for row in range(widget.result_table.rowCount())}
 assert rows['Weight force · world [N]']=='(0, 0, -500.25)'
 assert rows['Weight physical moment · world [Nm]']=='(3.5, -4.25, 0)'
 assert rows['Weight generalized heel / trim [Nm]']=='(3.4, 4.25)'
 assert rows['Weight moment reference']=='current_body_origin_world_axes'
 assert set(service.calls)<={'load','view','select'}

def test_focused_main_modes_restore_user_chrome_and_fit_window(app,monkeypatch):
 from anymarine.qtapp.mainwindow import MainWindow
 monkeypatch.setattr(ui.Recipe3StabilityHost,'retry',lambda self:None)
 window=MainWindow(viewer_backend='software');window.resize(1024,720);window.show();app.processEvents()
 window._toolbar.hide();window._jobs_dock.show();window._navigator_dock.show()
 window._set_mode('stability');app.processEvents()
 assert window.width()==1024 and window.minimumSizeHint().width()<=1024
 assert window._toolbar.isHidden() and window._jobs_dock.isHidden() and window._navigator_dock.isHidden()
 window._set_mode('model');app.processEvents()
 assert window._toolbar.isHidden() and not window._jobs_dock.isHidden() and not window._navigator_dock.isHidden()
 window._dirty=False;window.close();app.processEvents()
