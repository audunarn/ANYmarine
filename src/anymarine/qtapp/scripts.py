"""Embedded trusted scripts and a separate, non-executing stability GUI recording."""
from __future__ import annotations
import importlib
from PySide6.QtCore import QObject,Signal,Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPlainTextEdit,QPushButton,QSplitter,QFileDialog,QMessageBox,QApplication

MARINE_EXAMPLE='''# Saved recipe3 reference evidence; no solve on load/selection.
from anymarine.services import ApplicationService
service = ApplicationService()
barge_study = service.reference_barge_example()
print(service.reference_barge_view(barge_study)['mass_summary'])
# Explicit geometry Run is optional, bounded and requires a NEW evidence path:
# barge_run = service.run_reference_barge(barge_study, r'C:/new-reference-run', allow_reference=True)
'''

PREAMBLE='''# Recorded ANYmarine stability GUI actions. Review before explicit Run.
# Replays a separate study; does not mutate the live GUI/project.
from anymarine.services import ApplicationService
from anymarine.stability_service import StabilityResult
import hashlib
import json
service = ApplicationService()
stability_inputs = None
stability_result = None
barge_saved_identities = {}

'''

class StabilityRecording(QObject):
    changed=Signal()
    MAX_BYTES=1_500_000
    MAX_ACTIONS=256
    def __init__(self,parent=None):
        super().__init__(parent);self.entries=[];self.warning=''
    def record(self,action,source,outcome='success',error=''):
        comment=lambda value:'\n'.join('# '+line for line in str(value).splitlines())
        block=comment(f'{len(self.entries)+1}. {action}; UI outcome: {outcome}')+'\n'
        if error:block+=comment('UI diagnostic (not a success receipt): '+str(error)[:2000])+'\n'
        block+=source+('' if source.endswith('\n') else '\n')+'\n'
        if len(self.entries)>=self.MAX_ACTIONS or len((self.source()+block).encode('utf8'))>self.MAX_BYTES:
            self.warning='GUI action recording limit reached: this action was NOT recorded. Save/clear recording explicitly.';self.changed.emit();return False
        self.entries.append(block);self.changed.emit();return True
    def source(self):return PREAMBLE+''.join(self.entries)
    def clear(self):self.entries.clear();self.warning='';self.changed.emit()

class ScriptsHost(QWidget):
    """No kernel is started by constructing or selecting this page."""
    def __init__(self,recording,parent=None):
        super().__init__(parent);self.recording=recording;self.console=None
        layout=QVBoxLayout(self);layout.setContentsMargins(4,4,4,4)
        title=QLabel('Scripts - trusted arbitrary Python | GUI recording covers Stability only')
        title.setWordWrap(True);layout.addWidget(title)
        split=QSplitter(Qt.Orientation.Vertical);layout.addWidget(split,1)
        candidate=None
        try:
            module=importlib.import_module('anystability.console_gui')
            factory=getattr(module,'create_pane_class',None)
            if not callable(factory):raise ImportError('installed ANYstability has no reusable console pane API')
            candidate=factory()(lazy=True,parent=self)
            if not isinstance(candidate,QWidget) or any(not callable(getattr(candidate,name,None)) for name in ('set_source','shutdown')):
                raise ImportError('installed console widget lacks the required public set_source/shutdown API')
            candidate.set_source(MARINE_EXAMPLE);split.addWidget(candidate)
            self.console=candidate
        except (ImportError,AttributeError,RuntimeError,TypeError) as error:
            self.console=None
            if isinstance(candidate,QWidget):
                shutdown=getattr(candidate,'shutdown',None)
                if callable(shutdown):
                    try:shutdown()
                    except Exception:pass  # Keep the primary incompatibility diagnostic.
                for timer in candidate.findChildren(QTimer):timer.stop()
                candidate.hide();candidate.deleteLater()
            message=QLabel('Script console unavailable. Install an ANYstability version providing console_gui.create_pane_class with its console extra in this environment.\n'+str(error))
            message.setWordWrap(True);message.setObjectName('scriptsUnavailable');split.addWidget(message)
        recording_pane=QWidget();record_layout=QVBoxLayout(recording_pane)
        record_layout.addWidget(QLabel('Recorded Stability GUI actions - never executed on insertion'))
        self.actions=QPlainTextEdit();self.actions.setReadOnly(True);self.actions.setObjectName('stabilityActionRecording');record_layout.addWidget(self.actions,1)
        self.warning=QLabel();self.warning.setWordWrap(True);record_layout.addWidget(self.warning)
        buttons=QHBoxLayout();record_layout.addLayout(buttons)
        for label,callback in [('Copy GUI actions',self.copy_actions),('GUI actions to editor',self.actions_to_editor),('Save GUI actions .py',self.save_actions),('Clear GUI recording',recording.clear)]:
            button=QPushButton(label);button.clicked.connect(callback);buttons.addWidget(button)
        split.addWidget(recording_pane);split.setSizes([600,180])
        recording.changed.connect(self.refresh);self.refresh()
    def refresh(self):self.actions.setPlainText(self.recording.source());self.warning.setText(self.recording.warning)
    def copy_actions(self):QApplication.clipboard().setText(self.recording.source())
    def actions_to_editor(self):
        if self.console is None:
            self.warning.setText('Console unavailable; Copy GUI actions into another trusted Python editor.');return
        self.console.set_source(self.recording.source(),filename='<recorded-stability-actions>')
    def save_actions(self):
        path,_=QFileDialog.getSaveFileName(self,'Save recorded GUI actions (does not execute)',filter='Python (*.py)')
        if path:
            try:importlib.import_module('anystability.console').save_script(path,self.recording.source())
            except (ImportError,OSError,ValueError,RuntimeError) as error:QMessageBox.warning(self,'Cannot save GUI recording',str(error))
    def shutdown(self):
        if self.console is not None:self.console.shutdown()
