"""First-class Stability mode: retained shared state, no calculation on selection."""
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QDialog
from conftest import requires_qt
from anymarine.qtapp.stability import StabilityPane,StabilityDialog,StabilityHost
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.services import ServiceError
pytestmark=requires_qt

class Service:
 def __init__(self):self.available=True;self.examples=0;self.calculations=0
 def stability_example_inputs(self):
  self.examples+=1
  if not self.available:raise ServiceError('PROVIDER_UNAVAILABLE: optional study owner missing')
  return {'example':'reference only'}
 def calculate_stability(self,*args,**kwargs):self.calculations+=1;raise AssertionError('no automatic solve')

@pytest.fixture
def app():return QApplication.instance() or QApplication([])

def test_shared_standalone_and_pane_public_state_compatibility(app):
 service=Service();standalone=StabilityDialog(service);pane=StabilityPane(service)
 assert isinstance(standalone,QDialog) and isinstance(pane,QWidget)
 marker=object();standalone.result=marker;assert standalone.result is marker
 standalone.study_root='custom';assert standalone.study_root=='custom'
 replacement=Service();standalone.service=replacement;standalone.load_example()
 assert replacement.examples==1 and standalone.result is None
 pane.result=marker;pane.opt_in.setChecked(True);pane.inputs.setPlainText('{"edited":1}')
 assert pane.result is None and pane.opt_in.isChecked()
 assert service.calculations==0;standalone.close();pane.close()

def test_missing_owner_retry_one_study_and_refusal_recording(app):
 service=Service();service.available=False;records=[]
 host=StabilityHost(service,recorder=lambda *args:records.append(args))
 assert host.study is None and host.retry_button.isVisibleTo(host._unavailable)
 assert 'PROVIDER_UNAVAILABLE' in host.diagnostic.text()
 assert records[-1][2]=='refused'
 host.retry();assert host.study is None and len(records)==2
 service.available=True;host.retry_button.click();study=host.study
 assert study is not None and host._unavailable is None
 assert records[-1][2]=='success' and len(records)==3
 study.inputs.setPlainText('{"kept":1}');study.opt_in.setChecked(True)
 host.retry();assert host.study is study and len(records)==3
 assert study.inputs.toPlainText()=='{"kept":1}' and study.opt_in.isChecked()
 assert service.calculations==0;host.close()

def test_main_mode_and_examples_share_study_and_preserve_scripts_shortcut(app,monkeypatch):
 window=MainWindow(viewer_backend='software');service=Service();window._service=service
 try:
  assert window._stability_host is None and window._scripts_host is None
  assert window._mode_actions['scripts'].shortcut().toString()=='Ctrl+6'
  assert window._mode_actions['stability'].shortcut().toString()=='Ctrl+7'
  window.open_stability_example();study=window._stability_host.study
  study.inputs.setPlainText('{"user":1}');study.opt_in.setChecked(True);marker=object();study.result=marker
  root=object();study.study_root=root;study.report.setPlainText('kept report')
  records=len(window._stability_recording.entries)
  window._set_mode('model');window.open_stability_example()
  assert window._stability_host.study is study
  assert study.result is marker and study.study_root is root and study.report.toPlainText()=='kept report'
  assert study.inputs.toPlainText()=='{"user":1}' and study.opt_in.isChecked()
  assert len(window._stability_recording.entries)==records and service.examples==1 and service.calculations==0
  assert window._context_dock.isHidden()
 finally:window._dirty=False;window.close();app.processEvents()


# --- NAPA-style loading screen: interaction, fail-closed behaviour --------------
class BoxService:
 """Real ANYstability box through the public service; counts calculations."""
 def __init__(self):
  from anymarine import stability_service as s
  self.s=s;self.calculations=0
 def stability_example_inputs(self):return self.s.example_inputs()
 def parse_stability_inputs(self,text):return self.s.parse_inputs(text)
 def calculate_stability(self,inputs,*,allow_reference=False):
  self.calculations+=1;return self.s.calculate(inputs,allow_reference=allow_reference)

@pytest.fixture
def box(app):
 pytest.importorskip('anystability')
 service=BoxService();pane=StabilityPane(service);app.processEvents();yield pane,service
 pane.close()

def test_screen_lists_tanks_and_edits_inputs_without_calculating(box):
 pane,service=box
 assert pane.tank_table.rowCount()==4 and service.calculations==0 and pane.result is None
 assert 'Not calculated' in pane.banner.text()
 mass=pane.weight_block.values['mass'].text();assert mass=='438.50 t'
 pane.tank_table.cellWidget(0,3).setValue(50.0)           # ballast-tank-port to 50 %
 assert pane.weight_block.values['mass'].text()=='433.38 t' and pane.weight_block.values['partial'].text()=='1'
 assert service.calculations==0 and pane.result is None and pane.floating_block.values['draught'].text()=='—'

def test_live_fill_change_recalculates_and_gm_banner_follows(box,app):
 pane,service=box
 pane.live.setChecked(True);pane._timer.stop();pane._live_calculate()
 assert pane.opt_in.isChecked() and service.calculations==1
 gm_full=float(pane.stability_block.values['gmf'].text().split()[0])
 assert 'Calculated' in pane.banner.text() and pane.floating_block.values['draught'].text()=='2.139 m'
 pane.required_gm.setValue(gm_full+1.0);assert 'BELOW MINIMUM' in pane.banner.text()
 pane.required_gm.setValue(0.5);assert 'GM OK' in pane.banner.text()
 pane.tank_table.cellWidget(0,2).setValue(500)            # slider: 50 %
 assert pane.result is None and pane._timer.isActive()    # stale until the debounce fires
 pane._timer.stop();pane._live_calculate()
 assert service.calculations==2 and pane.result is not None
 assert float(pane.stability_block.values['fsc'].text().split()[0])>0
 assert float(pane.stability_block.values['gmf'].text().split()[0])<float(pane.stability_block.values['gms'].text().split()[0])
 assert 'Free-surface correction' in pane.banner.text() and 'rotational equilibrium' in pane.banner.text()

def test_live_off_never_calculates_on_fill_change(box):
 pane,service=box
 pane.opt_in.setChecked(True);pane.tank_table.cellWidget(1,3).setValue(25.0)
 assert not pane._timer.isActive() and service.calculations==0

def test_empty_tank_stays_on_screen_and_refills(box):
 pane,_=box
 pane.tank_table.cellWidget(2,3).setValue(0.0)
 assert pane.tank_table.rowCount()==4 and pane.tank_table.cellWidget(2,3).value()==0.0
 pane.tank_table.cellWidget(2,3).setValue(100.0);assert pane.weight_block.values['mass'].text()=='438.50 t'

def test_selection_syncs_drawing_and_table(box):
 pane,_=box
 pane.hull.tankClicked.emit('freshwater-tank-aft')
 assert pane.hull.selected=='freshwater-tank-aft' and pane.tank_table.currentRow()==2
 assert 'freshwater-tank-aft' in pane.tank_detail.text()
 pane.tank_table.selectRow(0);pane.tank_table.tankSelected.emit('ballast-tank-port');assert pane.hull.selected=='ballast-tank-port'

def test_non_box_inputs_show_empty_screen_not_a_crash(app):
 service=Service();pane=StabilityPane(service)
 assert pane.hull.model is None and pane.tank_table.rowCount()==0 and 'not an analytical box' in pane.banner.text()
 pane.close()

def test_unparsable_inputs_clear_the_tank_table(box):
 pane,_=box
 assert pane.tank_table.rowCount()==4
 pane.inputs.setPlainText('{')
 assert pane.tank_table.rowCount()==0 and pane.hull.model is None

def test_zero_length_hull_does_not_crash_the_drawing(box,app):
 import json
 pane,_=box;data=json.loads(pane.inputs.toPlainText());data['vessel']['geometry']['definition']['length_m']=0.0
 pane.inputs.setPlainText(json.dumps(data));pane.show();app.processEvents();pane.hull.grab()
 assert pane.hull.model is None

def test_pending_live_calculation_is_cancelled_by_a_raw_json_edit(box):
 import json
 pane,service=box
 pane.opt_in.setChecked(True);pane.live.setChecked(True);pane._timer.stop()
 pane.tank_table.cellWidget(0,3).setValue(50.0);assert pane._timer.isActive()
 data=json.loads(pane.inputs.toPlainText());data['vessel']['geometry']['definition']['length_m']=30.0
 pane.inputs.setPlainText(json.dumps(data))
 assert not pane._timer.isActive() and service.calculations==0
