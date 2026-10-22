"""Saved recipe3 model, tank calibration and illustrative loading interface."""
import json,math
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QByteArray
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,QTableWidget,QTableWidgetItem,QTabWidget,QPlainTextEdit,QTextBrowser,QCheckBox,QFileDialog,QMessageBox,QScrollArea,QFrame,QSizePolicy,QHeaderView)
from .stability_widgets import LoadingSelector,ModelPreview
from ..services import ServiceError
from ..reference_barge import CASES,reference_preview
def _display(value):
 if value is None:return '\u2014'
 if isinstance(value,float):return format(value,'.6g')
 if isinstance(value,(tuple,list)):return '('+', '.join(_display(v) for v in value)+')'
 return str(value)
class ReferenceBargePane(QWidget):
 def __init__(self,service,parent=None,*,recorder=None,study=None):
  opened=study is not None
  if study is None:study=service.reference_barge_example()
  super().__init__(parent);self.service=service;self.recorder=recorder;self.study=study;self.job=None;self._updating=False;self._dirty=False;self._recorded_saves={}
  self.setObjectName('StabilityWorkspace')
  self.setStyleSheet("QWidget#StabilityWorkspace {background:#f4f7fb;} QFrame#ResultCard,QFrame#ModelCard {background:white;border:1px solid #dce4ee;border-radius:8px;} QLabel#WorkspaceTitle {font-size:23px;font-weight:600;color:#19344c;} QLabel#CardValue {font-size:17px;font-weight:600;color:#19344c;} QLabel#Muted {color:#5b6e80;} QPushButton#PrimaryRun {background:#17698b;color:white;border:0;border-radius:5px;padding:9px 16px;font-weight:600;} QTableWidget {background:white;border:0;gridline-color:#edf1f5;} QHeaderView::section {background:#eaf0f6;border:0;padding:7px;color:#334c61;}")
  layout=QVBoxLayout(self);layout.setContentsMargins(18,12,18,12);layout.setSpacing(10)
  title=QLabel('Stability');title.setObjectName('WorkspaceTitle')
  self.evidence_badge=QLabel('Reference study');self.evidence_badge.setObjectName('Muted');self.evidence_badge.setMinimumWidth(150);self.evidence_badge.setMaximumWidth(210);self.evidence_badge.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Preferred)
  bar=QHBoxLayout();bar.addWidget(title);self.case_selector=LoadingSelector();self.case_selector.setToolTip('Saved loading condition');bar.addWidget(self.case_selector,1)
  for label,method in [('Open…',self.open_dialog),('Save…',self.save_dialog)]:
   button=QPushButton(label);button.clicked.connect(method);bar.addWidget(button)
  bar.addWidget(self.evidence_badge);layout.addLayout(bar)
  actionbar=QHBoxLayout();self.allow=QCheckBox('Allow reference calculation');self.allow.setToolTip('Explicit development opt-in. Current source/admission gates remain in force.');actionbar.addWidget(self.allow);actionbar.addStretch()
  self.run_button=QPushButton('Run equilibrium');self.run_button.setObjectName('PrimaryRun');self.cancel_button=QPushButton('Cancel');actionbar.addWidget(self.run_button);actionbar.addWidget(self.cancel_button);layout.addLayout(actionbar)
  self.summary=QLabel();self.summary.setWordWrap(True);self.summary.setObjectName('Muted');layout.addWidget(self.summary)
  cards=QHBoxLayout();self.result_cards={}
  for key,title in [('mass','Total mass'),('cg','Centre of gravity · body'),('angles','Heel / trim'),('height','Body-origin height')]:
   frame=QFrame();frame.setObjectName('ResultCard');box=QVBoxLayout(frame);label=QLabel(title);label.setObjectName('Muted');box.addWidget(label)
   value=QLabel('—');value.setObjectName('CardValue');value.setWordWrap(True);value.setMinimumWidth(0);value.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);box.addWidget(value);cards.addWidget(frame,1);self.result_cards[key]=value
  layout.addLayout(cards)
  self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
  overview=QWidget();overview_layout=QVBoxLayout(overview);overview_layout.setContentsMargins(0,10,0,0)
  self.model_card=QFrame();self.model_card.setObjectName('ModelCard');ml=QVBoxLayout(self.model_card);ml.setContentsMargins(8,8,8,8)
  try:
   self.preview=ModelPreview(reference_preview(study));ml.addWidget(self.preview)
  except ImportError:ml.addWidget(QLabel('Drawing preview unavailable. The source SVG remains in the saved study.'))
  overview_layout.addWidget(self.model_card,3)
  self.tank_table=QTableWidget();overview_layout.addWidget(self.tank_table,2);self.tabs.addTab(overview,'Overview')
  loading=QWidget();ll=QVBoxLayout(loading);ll.addWidget(QLabel('Loading ledger · liquids included once'))
  self.ledger=QTableWidget();ll.addWidget(self.ledger,1);ll.addWidget(QLabel('Stored result diagnostics'))
  self.result_table=QTableWidget();ll.addWidget(self.result_table,1);self.tabs.addTab(loading,'Loading & results')
  advanced=QWidget();al=QVBoxLayout(advanced);al.addWidget(QLabel('Development reference · illustrative dry loads. No certified admission or stability proof.'))
  reload_button=QPushButton('Reload local study');reload_button.clicked.connect(self.load_default);al.addWidget(reload_button)
  al.addWidget(QLabel('Bounded Run controls · lower caps / tighten tolerances only'))
  self.controls=QPlainTextEdit();self.controls.setMaximumHeight(190);al.addWidget(self.controls)
  al.addWidget(QLabel('Model / drawing capacities · WB storage only, pump and voids excluded'))
  self.capacity_table=QTableWidget();self.capacity_table.setMinimumHeight(170);al.addWidget(self.capacity_table)
  al.addWidget(QLabel('Upright calibration · height fraction differs from volume fill fraction'))
  self.calibration_table=QTableWidget();self.calibration_table.setMinimumHeight(190);al.addWidget(self.calibration_table)
  al.addWidget(QLabel('Exact saved records, source identities and limitations'))
  self.details=QTextBrowser();self.details.setMinimumHeight(180);al.addWidget(self.details)
  scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(advanced);self.tabs.addTab(scroll,'Advanced')
  self.status=QLabel('Saved study ready. No new calculation.');self.status.setWordWrap(True);self.status.setObjectName('Muted');layout.addWidget(self.status)
  self.case_selector.currentIndexChanged.connect(lambda _index:self.select_case(self.case_selector.currentData()));self.controls.textChanged.connect(self._edited);self.run_button.clicked.connect(self.run);self.cancel_button.clicked.connect(lambda _checked=False:self.cancel())
  for label in self.findChildren(QLabel):label.setWordWrap(True)
  self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(80)
  self.refresh()
  if not opened:self._record('Load recipe3 reference','barge_study = service.reference_barge_example()\n')
 def _record(self,action,code,error=None):
  if self.recorder:self.recorder(action,code,'success' if error is None else 'refused',str(error or ''))
 def _guard(self,callback):
  try:return callback()
  except (ServiceError,OSError,ValueError) as error:self.status.setText(str(error));QMessageBox.warning(self,'Reference study refused',str(error))
 def _edited(self):
  if not self._updating:
   self._dirty=True;self.cancel('controls edited');self.evidence_badge.setText('Controls changed · result not paired');self._clear_result_cards();self.summary.setText('CONTROLS EDITED: prior equilibrium is not paired with these controls. Run or Save validates them; cached mass/pose is not a new result.');self.status.setText('Controls edited; result pairing invalidated.')
 def _clear_result_cards(self):
  for card in self.result_cards.values():card.setText('Not paired')
  self.result_table.setRowCount(0)
 def _table(self,table,headers,rows):
  table.setColumnCount(len(headers));table.setHorizontalHeaderLabels(headers);table.setRowCount(len(rows))
  for i,row in enumerate(rows):
   for j,value in enumerate(row):
    item=QTableWidgetItem(_display(value));item.setToolTip(str(value));item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable);table.setItem(i,j,item)
  table.setMinimumSize(0,0);table.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
  table.verticalHeader().hide();table.setAlternatingRowColors(True);table.setWordWrap(False)
  table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);table.horizontalHeader().setStretchLastSection(True)
  for column in range(len(headers)):table.setColumnWidth(column,135 if column else 110)
 def refresh(self):
  view=self.service.reference_barge_view(self.study);self._updating=True
  self.case_selector.setCurrentText(self.study.selected_case);self.controls.setPlainText(json.dumps(view['controls'],indent=2));self._updating=False;self._dirty=False
  self.run_button.setEnabled(self.study.selected_case=='coupled' and not self.job)
  mass=view['mass_summary'];pose=view['pose']
  mass_text='Mass/CG not paired' if mass is None else 'Mass '+format(mass['mass_kg'],'.2f')+' kg | CG body m '+_display(mass['cg_body_m'])
  pose_text='Pose not retained' if pose is None else 'heel '+_display(pose['heel_rad'])+' rad | trim '+_display(pose['trim_rad'])+' rad | body-origin z '+_display(pose['origin_z_m'])+' m (not draught)'
  self.summary.setText('ILLUSTRATIVE dry loads | '+('Not paired' if mass is None else format(mass['mass_kg'],'.2f')+' kg')+' | '+('Achieved-volume liquid mass' if self.study.selected_case=='upright' else 'Fixed liquid quantities')+' | height is not draught')
  self.summary.setToolTip(view['loading_label']+'\n'+mass_text+'\n'+pose_text)
  self.evidence_badge.setText(('Run result' if self.study.to_dict().get('result') is not None else 'Saved reference')+' · '+view['status'].replace('_',' '));self.evidence_badge.setToolTip(view['evidence'])
  self.result_cards['mass'].setText('Not paired' if mass is None else format(mass['mass_kg']/1000,'.2f')+' t')
  self.result_cards['mass'].setToolTip(mass_text)
  self.result_cards['cg'].setText('Not paired' if mass is None else '('+', '.join(format(v,'.3f') for v in mass['cg_body_m'])+') m')
  self.result_cards['angles'].setText('Not retained' if pose is None else ' / '.join(('0.00' if format(math.degrees(pose[k]),'.2f')=='-0.00' else format(math.degrees(pose[k]),'.2f')) for k in ('heel_rad','trim_rad'))+' deg')
  self.result_cards['height'].setText('Not retained' if pose is None else _display(pose['origin_z_m'])+' m')
  self.cancel_button.setEnabled(self.job is not None)
  diagnostic=view['diagnostics'] or {};diagnostic_rows=[]
  for key,label in [('residual_Fz_Qheel_Qtrim','Residual Fz [N], Qheel / Qtrim [Nm]'),('net_moment_world_Nm','Net physical moment [Nm]'),('buoyancy_force_world_N','Buoyancy force · world [N]'),('weight_force_world_N','Weight force · world [N]'),('force_world_N','Weight force · world [N]'),('moment_world_Nm','Weight physical moment · world [Nm]'),('generalized_heel_trim_Nm','Weight generalized heel / trim [Nm]'),('moment_reference','Moment reference'),('reference','Weight moment reference'),('free_surface_treatment','Liquid treatment')]:
   if key in diagnostic:diagnostic_rows.append([label,diagnostic[key]])
  self._table(self.result_table,['Quantity','Stored value'],diagnostic_rows)
  rows=[]
  for name,capacity in view['capacities'].items():
   solution=view['tank_solutions'].get(name,{});sample=solution.get('sample') or {};cg=sample.get('cg_body_m',sample.get('liquid_cg_body_m'));fs=sample.get('free_surface')
   rows.append([name,capacity['volume_m3'],capacity['plan_capacity_m3'],view['targets_m3'].get(name),solution.get('achieved_volume_m3'),view['densities_kg_m3'].get(name),cg,None if fs is None else fs['area_m2']])
  self._table(self.tank_table,['Tank','Target [m³]','Achieved [m³]','Liquid CG · body [m]','Free surface [m²]'],[[r[0],r[3],r[4],r[6],r[7]] for r in rows])
  self._table(self.capacity_table,['Tank','Model [m³]','Drawing [m³]','Density [kg/m³]'],[[r[0],r[1],r[2],r[5]] for r in rows])
  self._table(self.calibration_table,['Tank','Height fraction','Height m','Volume m3','Volume fill fraction','Liquid CG body m','FS area m2'],[[r['tank_id'],r['height_fraction'],r['height_m'],r['volume_m3'],r['volume_fill_fraction'],r['liquid_cg_body_m'],None if r['free_surface'] is None else r['free_surface']['area_m2']] for r in view['calibration_rows']])
  condition=view['condition'];self._table(self.ledger,['Item','Mass kg','CG body m','Source','Physical covers'],[] if condition is None else [[r['item_id'],r['mass_kg'],r['cg_body_m'],r['source'],r['covers']] for r in condition['items']])
  self.details.setPlainText(json.dumps({k:view[k] for k in ('mass_basis','mass_summary','pose','condition','base_condition','diagnostics','tank_solutions','definition_identity','dataset_identity','limitations')},indent=2))
 def _control_candidate(self):
  raw=self.controls.toPlainText()
  candidate=self.service.reference_barge_controls(self.study,raw)
  return candidate,json.loads(raw) # Already strictly parsed and translated by the service.
 def _snapshot_controls(self):
  self.study,controls=self._control_candidate();return controls
 def select_case(self,name):
  if self._updating:return
  previous=self.study;code=''
  try:
   self.cancel('case switched');candidate=self.study
   if self._dirty:
    candidate,controls=self._control_candidate();code+='barge_study = service.reference_barge_controls(barge_study, '+repr(controls)+')\n'
   code+='barge_study = service.select_reference_barge_case(barge_study, '+repr(name)+')\n'
   selected=self.service.select_reference_barge_case(candidate,name)
   self.study=selected;self.refresh();self._record('Select recipe3 loading',code)
  except (ServiceError,OSError,ValueError) as error:
   self.study=previous;self._updating=True;self.case_selector.setCurrentText(previous.selected_case);self._updating=False
   self._record('Select recipe3 loading',code or 'barge_study = service.reference_barge_controls(barge_study, '+repr(self.controls.toPlainText())+')\n',error)
   self.status.setText(str(error));QMessageBox.warning(self,'Reference study refused',str(error))
 def load_default(self):
  code='barge_study = service.reference_barge_example()\n'
  def action():
   try:self.cancel('new example');candidate=self.service.reference_barge_example()
   except (ServiceError,OSError,ValueError) as error:self._record('Load recipe3 reference',code,error);raise
   self.study=candidate;self.refresh();self._record('Load recipe3 reference',code)
  self._guard(action)
 def open_study(self,path):
  absolute=str(Path(path).absolute())
  code='barge_study = service.open_reference_barge('+repr(absolute)+')\n'
  try:
   self.cancel('study opened');candidate=self.service.open_reference_barge(path)
   binding=repr(candidate.identity)
   if self._recorded_saves.get(absolute)==candidate.identity:
    binding="globals().get('barge_saved_identities', {}).get("+repr(absolute)+', '+binding+')'
   code+='assert barge_study.identity == '+binding+'\n'
  except (ServiceError,OSError,ValueError) as error:self._record('Open recipe3 study',code,error);raise
  self.study=candidate;self.refresh();self._record('Open recipe3 study',code)
 def open_dialog(self):
  path=QFileDialog.getExistingDirectory(self,'Open portable recipe3 study')
  if path:self._guard(lambda:self.open_study(path))
 def save_study(self,path):
  code=''
  absolute=str(Path(path).absolute())
  try:
   candidate=self.study
   if self._dirty:
    candidate,controls=self._control_candidate();code+='barge_study = service.reference_barge_controls(barge_study, '+repr(controls)+')\n'
   code+='barge_saved = service.save_reference_barge(barge_study, '+repr(absolute)+')\n'
   saved=self.service.save_reference_barge(candidate,path)
   code+='assert barge_saved.identity == barge_study.identity\n'
   code+="barge_saved_identities = globals().get('barge_saved_identities', {})\nbarge_saved_identities["+repr(absolute)+'] = barge_saved.identity\n'
  except (ServiceError,OSError,ValueError) as error:self._record('Save recipe3 study',code or 'barge_study = service.reference_barge_controls(barge_study, '+repr(self.controls.toPlainText())+')\n',error);raise
  self.study=candidate;self._recorded_saves[absolute]=saved.identity;self._record('Save recipe3 study',code);self.status.setText('Saved portable selected study; no calculation executed.');self.refresh()
 def save_dialog(self):
  path=QFileDialog.getSaveFileName(self,'Choose a NEW recipe3 study directory name')[0]
  if path:self._guard(lambda:self.save_study(path))
 def run(self):
  def action():
   code=''
   try:
    if self.job is not None:raise ValueError('one active reference Run only; wait for previous cancellation cleanup')
    candidate,controls=self._control_candidate()
    path=QFileDialog.getSaveFileName(self,'Choose a NEW Run evidence directory')[0]
    if not path:return # No state changes when the destination dialog is cancelled.
    code='barge_study = service.reference_barge_controls(barge_study, '+repr(controls)+')\n'
    code+='barge_run = service.run_reference_barge(barge_study, '+repr(str(Path(path).absolute()))+', allow_reference='+repr(self.allow.isChecked())+')\n'
    job=self.service.run_reference_barge(candidate,path,allow_reference=self.allow.isChecked())
   except (ServiceError,OSError,ValueError) as error:self._record('Explicit coupled reference Run',code or 'barge_study = service.reference_barge_controls(barge_study, '+repr(self.controls.toPlainText())+')\n',error);raise
   self.study=candidate;self.job=job;self._dirty=False;self.run_button.setEnabled(False);self.cancel_button.setEnabled(True);self._job_identity=candidate.identity
   self._record('Explicit coupled reference Run',code)
   self.status.setText('Running explicit bounded reference; cached evidence unchanged until matching validated completion.')
  self._guard(action)
 def cancel(self,reason='user cancellation'):
  if self.job:
   self.job.cancel(str(reason));self._record('Cancel coupled Run','barge_run.cancel('+repr(str(reason))+')\n');self.status.setText('Abort requested; late replies quarantined; evidence directory retained.')
 def poll(self):
  if not self.job:return
  state=self.job.poll()
  if state in ('running','aborting','termination_unconfirmed'):return
  job=self.job;job.close();self.job=None
  if state=='completed' and self.study.identity==self._job_identity and not self._dirty:
   self.study=job.result;self.refresh();self.status.setText('Explicit Run completed; estimated residual convergence, not stability/certification.')
   self._record('Accept completed coupled Run','barge_study = barge_run.finish()\n')
  else:self.status.setText('Run '+state+': '+job.detail+' | all evidence retained at '+str(job.path))
  self.run_button.setEnabled(self.study.selected_case=='coupled');self.cancel_button.setEnabled(False)
 def shutdown(self):
  self.timer.stop()
  if self.job:
   if self.job.close() is False:self.timer.start(80);self.status.setText('Run termination unconfirmed; application stays open and retains supervision.');return False
   self.job=None
  self.cancel_button.setEnabled(False)
  return True
class Recipe3StabilityHost(QWidget):
 def __init__(self,service,parent=None,*,recorder=None):
  super().__init__(parent);self.service=service;self.recorder=recorder;self.reference=None;self._legacy=None
  layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
  navigation=QHBoxLayout();self.back_button=QPushButton('← Reference barge (saved)');self.back_button.clicked.connect(lambda:self.tabs.setCurrentIndex(0));self.back_button.hide();navigation.addWidget(self.back_button);navigation.addStretch()
  self.legacy_button=QPushButton('Live analytical box →');self.legacy_button.setToolTip('Interactive tank loading on the analytical reference box: change fills, see draught and GM');self.legacy_button.setObjectName('studySwitch');self.legacy_button.clicked.connect(self.open_legacy_box);navigation.addWidget(self.legacy_button);layout.addLayout(navigation)
  self.tabs=QTabWidget();self.tabs.tabBar().hide();layout.addWidget(self.tabs)
  self.reference_page=QWidget();self.reference_layout=QVBoxLayout(self.reference_page);self.reference_layout.setContentsMargins(0,0,0,0);self.tabs.addTab(self.reference_page,'Stability')
  self.legacy_page=QWidget();self.legacy_layout=QVBoxLayout(self.legacy_page);self.tabs.addTab(self.legacy_page,'Analytical example')
  self.tabs.currentChanged.connect(self._tab);self.retry()
 @property
 def study(self):return None if self._legacy is None else self._legacy.study
 def retry(self):
  if self.reference is not None:return
  try:candidate=ReferenceBargePane(self.service,self,recorder=self.recorder)
  except (ServiceError,AttributeError) as error:
   if not hasattr(self,'unavailable'):
    self.unavailable=QWidget();missing=QVBoxLayout(self.unavailable);missing.addStretch()
    title=QLabel('Open a stability study');title.setAlignment(Qt.AlignmentFlag.AlignCenter);title.setStyleSheet('font-size:24px;font-weight:600;color:#19344c');missing.addWidget(title)
    explanation=QLabel('No local reference study is available. Open a saved study, retry your local setup, or explore the analytical example.');explanation.setWordWrap(True);explanation.setAlignment(Qt.AlignmentFlag.AlignCenter);missing.addWidget(explanation)
    buttons=QHBoxLayout();buttons.addStretch();self.open_button=QPushButton('Open study…');self.open_button.clicked.connect(self._open_saved);buttons.addWidget(self.open_button)
    self.retry_button=QPushButton('Retry local study');self.retry_button.clicked.connect(self.retry);buttons.addWidget(self.retry_button)
    example=QPushButton('Analytical example');example.clicked.connect(self.open_legacy_box);buttons.addWidget(example);buttons.addStretch();missing.addLayout(buttons)
    self.diagnostic=QLabel();self.diagnostic.setWordWrap(True);self.diagnostic.setAlignment(Qt.AlignmentFlag.AlignCenter);self.diagnostic.setStyleSheet('color:#6b7785;font-size:11px');missing.addWidget(self.diagnostic);missing.addStretch();self.reference_layout.addWidget(self.unavailable)
   self.diagnostic.setText(str(error));return
  self._show_reference(candidate)
 def _show_reference(self,candidate):
  if hasattr(self,'unavailable'):self.unavailable.hide()
  self.reference_layout.addWidget(candidate);self.reference=candidate
 def _open_saved(self):
  path=QFileDialog.getExistingDirectory(self,'Open saved stability study')
  if not path:return
  code='barge_study = service.open_reference_barge('+repr(str(Path(path).absolute()))+')\n'
  try:
   study=self.service.open_reference_barge(path)
   candidate=ReferenceBargePane(self.service,self,recorder=self.recorder,study=study)
   code+='assert barge_study.identity == '+repr(study.identity)+'\n'
  except (ServiceError,OSError,ValueError,AttributeError) as error:
   if self.recorder:self.recorder('Open recipe3 study',code,'refused',str(error))
   self.diagnostic.setText(str(error));return
  self._show_reference(candidate)
  if self.recorder:self.recorder('Open recipe3 study',code,'success','')
 def _tab(self,index):
  self.back_button.setVisible(index==1);self.legacy_button.setVisible(index==0)
  if index==1 and self._legacy is None:
   from .stability import StabilityHost
   self._legacy=StabilityHost(self.service,self,recorder=self.recorder);self.legacy_layout.addWidget(self._legacy)
 def open_legacy_box(self):self.tabs.setCurrentIndex(1);self._tab(1)
 def shutdown(self):
  return True if self.reference is None else self.reference.shutdown()
