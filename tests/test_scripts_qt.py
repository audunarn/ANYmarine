"""Scripts integration tests: public service doubles, no geometry or solves."""
import hashlib,json
from types import SimpleNamespace
import pytest
from conftest import requires_qt
pytestmark=requires_qt
from PySide6.QtWidgets import QApplication,QMessageBox,QWidget
from anymarine.qtapp.scripts import StabilityRecording,ScriptsHost
from anymarine.qtapp.stability import StabilityDialog
from anymarine.qtapp.mainwindow import MainWindow

@pytest.fixture
def app():return QApplication.instance() or QApplication([])

class FakeResult:
 def __init__(self,payload=None):
  if payload is not None:self.payload_json=payload
 payload_json='{"fixed":true}'
 markdown='REFERENCE ONLY'
 def to_dict(self):return {'output':{'draft_m':1.,'query_count':1}}

class Service:
 def __init__(self):self.calls=[];self.reject=False
 def stability_example_inputs(self):self.calls.append('example');return {'vessel':'declared'}
 def parse_stability_inputs(self,text):self.calls.append('parse');return json.loads(text)
 def calculate_stability(self,inputs,*,allow_reference=False):
  self.calls.append(('calculate',inputs,allow_reference))
  if self.reject:raise ValueError('refused\nno invented receipt')
  return FakeResult()
 def open_stability_study(self,path):self.calls.append(('open',path));return {'loaded':True},FakeResult()
 def save_stability_study(self,inputs,path,*,result=None):self.calls.append(('save',inputs,path,result));return {'result_saved':result is not None}

def test_recording_before_pane_open_snapshots_failure_and_no_stale_save(app,tmp_path):
 recording=StabilityRecording();service=Service();dialog=StabilityDialog(service,recorder=recording.record)
 assert service.calls==['example'];assert len(recording.entries)==1
 dialog.opt_in.setChecked(True);dialog.calculate();calculation=recording.entries[-1]
 assert 'allow_reference=True' in calculation
 assert hashlib.sha256(FakeResult.payload_json.encode()).hexdigest() in calculation
 dialog.inputs.setPlainText('{"edited":true}');assert dialog.result is None
 dialog.save_study(tmp_path/'save');saved=recording.entries[-1]
 assert 'stability_result = None' in saved and 'StabilityResult(' not in saved
 assert repr(str((tmp_path/'save').absolute())) in saved
 dialog.open_study(tmp_path/'open');opened=recording.entries[-1]
 assert 'open_stability_study' in opened and 'calculate_stability' not in opened
 dialog.save_study(tmp_path/'copy');assert 'StabilityResult(' in recording.entries[-1]
 service.reject=True
 with pytest.raises(ValueError):dialog.calculate()
 assert 'UI outcome: refused' in recording.entries[-1]
 assert '# no invented receipt' in recording.entries[-1] or '# UI diagnostic' in recording.entries[-1]
 assert 'allow_reference=False' in recording.entries[-1] # Open resets opt-in.
 compile(recording.source(),'<GUI recording>','exec')
 dialog.close()

def test_recorded_public_replay_uses_snapshots_and_no_save_recalculation(app,tmp_path,monkeypatch):
 from anymarine import services,stability_service
 recording=StabilityRecording();service=Service();dialog=StabilityDialog(service,recorder=recording.record)
 dialog.opt_in.setChecked(True);dialog.calculate();dialog.save_study(tmp_path/'saved')
 dialog.inputs.setPlainText('{"changed":1}');dialog.save_study(tmp_path/'inputs-only')
 dialog.open_study(tmp_path/'opened');dialog.save_study(tmp_path/'copy')
 replay=Service();monkeypatch.setattr(services,'ApplicationService',lambda:replay);monkeypatch.setattr(stability_service,'StabilityResult',FakeResult)
 exec(compile(recording.source(),'<recorded GUI actions>','exec'),{})
 assert len([call for call in replay.calls if isinstance(call,tuple) and call[0]=='calculate'])==1
 saves=[call for call in replay.calls if isinstance(call,tuple) and call[0]=='save']
 assert saves[0][-1] is not None and saves[1][-1] is None and saves[2][-1] is not None
 assert saves[1][1]=={'changed':1};dialog.close()


def test_embedded_actual_qwidget_lazy_and_record_insert_never_runs(app,monkeypatch):
 from anystability import console_gui
 monkeypatch.setattr(console_gui,'ConsoleSession',lambda:pytest.fail('kernel must start only on Run'))
 recording=StabilityRecording();recording.record('Load','stability_inputs = service.stability_example_inputs()')
 host=ScriptsHost(recording);assert host.console is not None
 assert isinstance(host.console,QWidget);assert not host.console.isWindow();assert host.console.session is None
 host.actions_to_editor();assert 'ApplicationService' in host.console.editor.toPlainText();assert host.console.session is None
 recording.record('Explicit failure','raise ValueError("record only")','refused','diagnostic')
 assert 'Explicit failure' in host.actions.toPlainText();assert host.console.session is None
 host.shutdown();host.close()

def test_optional_missing_pane_visible_and_bounded_recording(app,monkeypatch):
 import anymarine.qtapp.scripts as scripts
 original=scripts.importlib.import_module
 monkeypatch.setattr(scripts.importlib,'import_module',lambda name:SimpleNamespace() if name=='anystability.console_gui' else original(name))
 recording=StabilityRecording();host=ScriptsHost(recording);assert host.console is None
 assert 'create_pane_class' in host.findChild(QWidget,'scriptsUnavailable').text()
 recording.MAX_BYTES=10;assert not recording.record('Action','pass');assert 'NOT recorded' in host.warning.text()
 host.actions_to_editor();assert 'unavailable' in host.warning.text();host.close()

def test_main_scripts_mode_viewport_and_accepted_rejected_close(app,monkeypatch):
 from anystability import console_gui
 monkeypatch.setattr(console_gui,'ConsoleSession',lambda:pytest.fail('mode change must not start kernel'))
 window=MainWindow(viewer_backend='software');window.resize(1280,850)
 monkeypatch.setattr(window,'_notify_warning',lambda *a:None)
 assert window._scripts_host is None
 original_viewport=window._viewport;window._mode_actions['scripts'].trigger();host=window._scripts_host
 assert window._central_stack.currentWidget() is host;assert host.console.session is None
 window._set_mode('model');assert window._central_stack.currentWidget() is original_viewport
 window._set_mode('scripts');assert window._scripts_host is host
 closed=[];monkeypatch.setattr(host,'shutdown',lambda:closed.append(True))
 monkeypatch.setattr(window,'_confirm_discard',lambda:False);window.close();assert not closed
 monkeypatch.setattr(window,'_confirm_discard',lambda:True);window.close();assert closed==[True]
 # Close the real lazy pane timer after the deliberately mocked lifecycle check.
 host.console.shutdown()


def test_present_incompatible_factory_never_publishes_partial_widget(app,monkeypatch):
 import anymarine.qtapp.scripts as scripts
 from PySide6.QtCore import QTimer,QCoreApplication,QEvent
 created=[]
 def old_factory():
  class OldPane(QWidget):
   def __init__(self,**kwargs):
    super().__init__(kwargs.get('parent'));self.timer=QTimer(self);self.timer.start(50);created.append(self)
  return OldPane
 original=scripts.importlib.import_module
 monkeypatch.setattr(scripts.importlib,'import_module',lambda name:SimpleNamespace(create_pane_class=old_factory) if name=='anystability.console_gui' else original(name))
 host=ScriptsHost(StabilityRecording());assert host.console is None
 assert not created[0].timer.isActive()
 assert 'set_source/shutdown' in host.findChild(QWidget,'scriptsUnavailable').text()
 host.actions_to_editor();host.shutdown();host.close()
 QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
 app.processEvents()
