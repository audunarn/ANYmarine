"""Preview-first public engineering workflow, kept separate from historical results."""
from __future__ import annotations
import json
import math
import threading
from pathlib import Path
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QComboBox,QDoubleSpinBox,QSpinBox,QLineEdit,QCheckBox,QTabWidget,QTableWidget,
    QTableWidgetItem,QPlainTextEdit,QFileDialog,QMessageBox,QHeaderView,QSplitter)
from ..engineering_stability import api, create_from_reference, script_for_reference, default_owner_wheel, with_engineering_inputs, engineering_pose_bounds
from ..engineering_stability_run import EngineeringRun
from .gz_chart import GZChart

class EngineeringStabilityPane(QWidget):
    retained_state_changed=Signal(object,object,bool)
    def __init__(self, reference_getter=None, parent=None, *, recorder=None, run_factory=None):
        super().__init__(parent)
        self.reference_getter = reference_getter or (lambda:None)
        self.recorder = recorder
        self.run_factory = run_factory or EngineeringRun
        self.study = None
        self.result = None
        self.job = None
        self.history = []
        self._result_views={}
        self._opening_task=None
        self.stale = False
        self._source_identity = None
        self._use_opened_study = False
        self._updating = False
        self._last_options = None
        self._run_source_identity = None
        box = QVBoxLayout(self)
        introduction = QLabel('Engineering estimates · preview first · no production qualification or rule PASS. '
            'Existing saved reference results remain available in Loading & results.')
        introduction.setWordWrap(True);box.addWidget(introduction)
        provider = QHBoxLayout()
        self.geometry_mode = QComboBox();self.geometry_mode.addItems(['engineering_estimate','certified_point'])
        self.geometry_mode.setToolTip('Provider assurance remains distinct from curve/root certification.')
        provider.addWidget(QLabel('Geometry provider'));provider.addWidget(self.geometry_mode)
        self.owner_wheel = QLineEdit(default_owner_wheel());self.owner_wheel.setPlaceholderText('Local matching ANYgeometry wheel path (provider provenance)')
        provider.addWidget(self.owner_wheel,1)
        browse = QPushButton('Browse…');browse.clicked.connect(self._wheel_dialog);provider.addWidget(browse)
        rangebar = QHBoxLayout()
        self.mode = QComboBox();self.mode.addItem('Free trim','free_trim');self.mode.addItem('Fixed trim','fixed_trim')
        rangebar.addWidget(self.mode)
        self.fixed_trim = self._angle(-80,80,0);rangebar.addWidget(QLabel('Fixed trim'));rangebar.addWidget(self.fixed_trim)
        self.heel_min = self._angle(-80,0,-10);self.heel_max = self._angle(0,80,10)
        rangebar.addWidget(QLabel('Heel range'));rangebar.addWidget(self.heel_min);rangebar.addWidget(self.heel_max)
        box.addLayout(rangebar)
        limits = QHBoxLayout()
        self.wall_seconds = QSpinBox();self.wall_seconds.setRange(1,600);self.wall_seconds.setValue(90);self.wall_seconds.setSuffix(' s')
        self.query_limit = QSpinBox();self.query_limit.setRange(1,8192);self.query_limit.setValue(1024)
        self.evaluation_limit = QSpinBox();self.evaluation_limit.setRange(1,512);self.evaluation_limit.setValue(48)
        self.point_limit = QSpinBox();self.point_limit.setRange(1,241);self.point_limit.setValue(5)
        for title,control in [('Wall',self.wall_seconds),('Queries',self.query_limit),('Evaluations',self.evaluation_limit),('Points',self.point_limit)]:
            limits.addWidget(QLabel(title));limits.addWidget(control)
        self.limit_summary=QLabel();self.limit_summary.setWordWrap(True);box.addWidget(self.limit_summary)
        action = QHBoxLayout()
        self.allow = QCheckBox('Allow development estimate');box.addWidget(self.allow)
        self.equilibrium_button = QPushButton('Engineering equilibrium')
        self.preview_button = QPushButton('Preview GZ');self.preview_button.setToolTip('Four prescribed heels plus accepted floating equilibrium: five points maximum.')
        self.full_button = QPushButton('Full GZ…')
        self.resume_button=QPushButton('Resume retained');self.resume_button.setEnabled(False)
        self.cancel_button = QPushButton('Cancel');self.cancel_button.setEnabled(False)
        for button in (self.equilibrium_button,self.preview_button,self.full_button,self.resume_button,self.cancel_button): action.addWidget(button)
        box.addLayout(action)
        self.status = QLabel('No new calculation. Open a saved engineering study or select a local provider and Preview.')
        self.status.setWordWrap(True);box.addWidget(self.status)
        storage = QHBoxLayout()
        for title,callback in [('Open engineering…',self.open_dialog),('Save paired study…',self.save_dialog),('Export report…',self.export_dialog)]:
            button = QPushButton(title);button.clicked.connect(callback);storage.addWidget(button)
        self.history_selector = QComboBox();self.history_selector.setMinimumContentsLength(18)
        storage.addWidget(QLabel('Retained history'));storage.addWidget(self.history_selector,1);box.addLayout(storage)
        self.views = QTabWidget();box.addWidget(self.views,1)
        gz = QWidget();gzbox = QVBoxLayout(gz);self.chart = GZChart();gzbox.addWidget(self.chart,2)
        self.points = QTableWidget();gzbox.addWidget(self.points,1);self.views.addTab(gz,'GZ curve')
        self.equilibrium = QTableWidget();self.views.addTab(self.equilibrium,'Equilibrium')
        self.metrics = QTableWidget();self.views.addTab(self.metrics,'Metrics / report')
        self.details = QPlainTextEdit();self.details.setReadOnly(True);self.views.addTab(self.details,'Evidence')
        opening = QWidget();openingbox = QVBoxLayout(opening)
        openingbox.addLayout(provider);openingbox.addLayout(limits)
        openingbox.addWidget(QLabel('Optional critical openings: JSON array of name, point_body_m, closing_status, limits_intact. '
            'An empty inventory leaves downflooding unavailable; it is not an intact-envelope proof.'))
        openingbox.addWidget(QLabel('Engineering search bounds: [[origin z min,max] m, [heel min,max] rad, [trim min,max] rad]. Empty uses the authored-hull support box, heel ±80°, trim ±20°.'))
        self.pose_bounds=QLineEdit();self.pose_bounds.setPlaceholderText('Automatic authored-hull support box');openingbox.addWidget(self.pose_bounds)
        self.openings = QPlainTextEdit('[]');openingbox.addWidget(self.openings);self.views.addTab(opening,'Advanced / provider')
        for control in (self.geometry_mode,self.mode): control.currentIndexChanged.connect(self.mark_stale)
        for control in (self.fixed_trim,self.heel_min,self.heel_max,self.wall_seconds,self.query_limit,self.evaluation_limit,self.point_limit): control.valueChanged.connect(self.mark_stale)
        for control in (self.wall_seconds,self.query_limit,self.evaluation_limit,self.point_limit):control.valueChanged.connect(self._show_limits)
        self._show_limits()
        self.pose_bounds.textChanged.connect(self.mark_stale);self.owner_wheel.textChanged.connect(self.mark_stale);self.openings.textChanged.connect(self.mark_stale)
        self.equilibrium_button.clicked.connect(lambda:self.run('equilibrium'))
        self.preview_button.clicked.connect(lambda:self.run('preview'))
        self.full_button.clicked.connect(self.full_run)
        self.resume_button.clicked.connect(self.resume)
        self.cancel_button.clicked.connect(lambda:self.cancel())
        self.history_selector.currentIndexChanged.connect(self._select_history)
        for label in self.findChildren(QLabel):label.setWordWrap(True)
        self.timer = QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(100)

    def _show_limits(self,*args):
        self.limit_summary.setText(f'Global caps: {self.wall_seconds.value()} s · {self.query_limit.value()} owner queries · {self.evaluation_limit.value()} evaluations · {self.point_limit.value()} points. Edit in Advanced / provider.')

    @staticmethod
    def _angle(low, high, value):
        control = QDoubleSpinBox();control.setRange(low,high);control.setDecimals(2);control.setValue(value);control.setSuffix('°');return control

    def _wheel_dialog(self):
        path = QFileDialog.getOpenFileName(self,'Select local geometry provider wheel','','Wheel (*.whl)')[0]
        if path:self.owner_wheel.setText(path)

    def _record(self,title,code,error=None):
        if self.recorder:self.recorder(title,code,'refused' if error else 'success',str(error or ''))

    def _guard(self,callback):
        try:return callback()
        except Exception as error:
            self.status.setText(str(error))
            QMessageBox.warning(self,'Engineering stability',str(error))
            return None

    def mark_stale(self,*args):
        if self._updating:return
        self.resume_button.setEnabled(False)
        self.stale = self.result is not None or self.job is not None
        if self.result is not None and self.history:
            index=self.history_selector.currentIndex()
            if 0<=index<len(self.history):
                study,result,_,origin=self.history[index]
                self.history[index]=(study,result,True,origin)
                self.history_selector.setItemText(index,f'{self._result_data(result)["status"]} · {result.identity[:10]} · historical')
        if self.stale:self.status.setText('Inputs changed. Retained result belongs to its frozen request; it is historical until a new Run.')
        if self.result is not None:self.retained_state_changed.emit(self.study,self.result,self.stale)

    def source_changed(self, identity):
        if identity != self._source_identity:
            self._source_identity = identity
            self._use_opened_study = False
            self.mark_stale()

    def options(self, profile):
        controls = {'max_wall_seconds':self.wall_seconds.value(),
            'max_owner_queries':self.query_limit.value(), 'max_evaluations':self.evaluation_limit.value(),
            'max_points':self.point_limit.value()}
        if profile == 'equilibrium':
            controls.pop('max_points')
            return {'operation':'equilibrium','controls':controls}
        if self.heel_min.value() >= self.heel_max.value():raise ValueError('Heel minimum must be below maximum')
        # Five preview stations; Full begins every five degrees and lets owner refine.
        low, high = self.heel_min.value(), self.heel_max.value()
        degrees = [low,low/2,high/2,high] if profile=='preview' else [low+5*i for i in range(int((high-low)//5)+1)]+[high]
        degrees = sorted(set(degrees if profile=='preview' else degrees+[0.0]))
        return {'operation':'gz','profile':profile,'mode':self.mode.currentData(),
            'fixed_trim_rad':math.radians(self.fixed_trim.value()) if self.mode.currentData()=='fixed_trim' else None,
            'heels_rad':[math.radians(v) for v in degrees], 'controls':controls}

    def full_run(self):
        def action():
            # Explicit full selection exposes full caps/range before the actual Run.
            self._updating=True
            self.wall_seconds.setValue(600);self.query_limit.setValue(8192)
            self.evaluation_limit.setValue(512);self.point_limit.setValue(241)
            self.heel_min.setValue(-60);self.heel_max.setValue(60)
            self._updating=False;self.mark_stale()
            reply=QMessageBox.question(self,'Full engineering GZ',
                'Full uses the displayed limits: up to 600 s, 8192 queries, 512 evaluations and 241 points. Start explicitly?',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
            if reply==QMessageBox.StandardButton.Yes:self.run('full')
        self._guard(action)

    def run(self, profile='preview', *, path=None):
        def action():
            if self.job is not None:raise ValueError('Wait for the owned calculation to stop before starting another')
            if not self.allow.isChecked():raise ValueError('Explicit development estimate opt-in is required')
            reference = None if self._use_opened_study else self.reference_getter()
            if reference is None and self.study is None:raise ValueError('Open a local reference or engineering study first')
            options=self.options(profile)
            bounds=json.loads(self.pose_bounds.text()) if self.pose_bounds.text().strip() else (engineering_pose_bounds(reference) if reference is not None else self.study.to_dict()['pose_bounds'])
            destination=path or QFileDialog.getSaveFileName(self,'Choose a NEW engineering Run evidence directory')[0]
            if not destination:return
            if reference is not None:
                study=create_from_reference(reference, owner_wheel=self.owner_wheel.text().strip(),
                    geometry_mode=self.geometry_mode.currentText(), openings=json.loads(self.openings.toPlainText()),pose_bounds=bounds)
                code=script_for_reference(reference, options, owner_wheel=self.owner_wheel.text().strip(),
                    geometry_mode=self.geometry_mode.currentText(), run_path=destination, openings=json.loads(self.openings.toPlainText()),pose_bounds=bounds)
            else:
                study=with_engineering_inputs(self.study,owner_wheel=self.owner_wheel.text().strip(),
                    geometry_mode=self.geometry_mode.currentText(),openings=json.loads(self.openings.toPlainText()),pose_bounds=bounds)
                code=('from anymarine.engineering_stability_run import EngineeringRun\n'
                      'from anymarine.engineering_stability import with_engineering_inputs\n'
                      f'engineering_study = with_engineering_inputs(engineering_study, owner_wheel={self.owner_wheel.text().strip()!r}, geometry_mode={self.geometry_mode.currentText()!r}, openings={json.loads(self.openings.toPlainText())!r}, pose_bounds={bounds!r})\n'
                      f'engineering_run = EngineeringRun(engineering_study, {str(Path(destination).absolute())!r}, options={options!r})\n')
            self.job=self.run_factory(study,destination,options=options)
            self._last_options=options;self._run_source_identity=None if reference is None else reference.identity
            self._run_study=study;self._run_dirty=False;self.stale=False
            self.cancel_button.setEnabled(True);self._buttons(False)
            self._record('Run engineering '+profile,code)
            self.status.setText('Running frozen '+study.identity[:12]+' · evidence '+str(self.job.path))
        return self._guard(action)

    def _buttons(self, enabled):
        for button in (self.equilibrium_button,self.preview_button,self.full_button):button.setEnabled(enabled)
        if not enabled:self.resume_button.setEnabled(False)

    def resume(self, *, path=None):
        def action():
            reason=self._resume_reason()
            if reason:raise ValueError(reason)
            if not self.allow.isChecked():raise ValueError('Explicit development estimate opt-in is required')
            destination=path or QFileDialog.getSaveFileName(self,'Choose a NEW Resume evidence directory')[0]
            if not destination:return
            request=self._result_data(self.result)['request']
            options={key:request[key] for key in ('mode','fixed_trim_rad','profile','heels_rad','controls')}
            options['operation']='gz'
            seed=self.result
            self.job=self.run_factory(self.study,destination,options=options,seed_result=seed)
            self._run_study=self.study;self._last_options=options;self.stale=False
            self._buttons(False);self.cancel_button.setEnabled(True)
            self._record('Resume unchanged engineering request',
                f'from anymarine.engineering_stability_run import EngineeringRun\nengineering_run = EngineeringRun(engineering_study, {str(Path(destination).absolute())!r}, options={options!r}, seed_result=engineering_result)\n')
        return self._guard(action)

    def _resume_reason(self):
        if self.job:return 'An owned calculation is already active'
        if self.stale:return 'Inputs changed; Resume requires the unchanged frozen request'
        if self.result is None:return 'No retained result to resume'
        data=self._result_data(self.result);request=data.get('request',{})
        if data.get('kind')!='gz_curve' or data.get('status')=='complete':return 'Only an incomplete GZ request can resume'
        work=data.get('work',{});caps=request.get('controls',{})
        if work.get('resumable') is not True:return 'Accepted-only/hard-killed checkpoint has unknown spent work; Resume is unavailable'
        pairs=(('max_wall_seconds','elapsed_wall_seconds'),('max_owner_queries','attempted_owner_queries'),
            ('max_evaluations','evaluation_count'),('max_points','accepted_point_count'))
        for limit,spent in pairs:
            if caps.get(limit,0)<=work.get(spent,0):return f'Retained request has exhausted {limit}; a new explicit request is required'
        return None

    def cancel(self, reason='user cancellation'):
        if self.job:
            self.job.cancel(reason);self._record('Cancel engineering calculation',f'engineering_run.cancel({reason!r})\n')
            self.status.setText('Cancellation requested; accepted checkpoints and diagnostics remain in the evidence directory.')

    def poll(self):
        if self._opening_task is not None and self._opening_task.get('done'):
            task=self._opening_task;self._opening_task=None
            if 'error' in task:self.status.setText(str(task['error']))
            else:self._opened_study(task['path'],task['study'],task['result'],task['data'])
        if self.job is None:return
        state=self.job.poll()
        progress=self.job.progress
        if state in ('running','cancelling','timing_out','termination_unconfirmed'):
            self.status.setText(f"{state} · {progress.get('phase','starting')} · queries {progress.get('query_count','—')} · evaluations {progress.get('evaluation_count','—')} · frozen request {self.job.request_identity[:12]}"+(' · inputs edited / result historical' if self.stale else ''))
            if self.job.partial:self.chart.set_result(self.job.partial)
            return
        job=self.job
        if not job.close():return
        self.job=None;self._buttons(True);self.cancel_button.setEnabled(False)
        if job.result is not None:
            stale=self.stale
            origin=str(job.path/('output' if (job.path/'output').is_file() else 'accepted.json'))
            self._retain(self._run_study,job.result,stale=stale,origin=origin,data=getattr(job,'result_data',None))
            self._record('Retain engineering result','from anystability import services as stability\nengineering_study = engineering_run.study\nengineering_result = engineering_run.finish()\nengineering_pairs = globals().get("engineering_pairs", {})\n'+f'engineering_pairs[{origin!r}] = (engineering_study, engineering_result)\n')
        else:
            if job.partial:self.chart.set_result(job.partial)
            self.status.setText(state+': '+job.detail+' · partial checkpoints retained at '+str(job.path))

    def _result_data(self,result):
        if result.identity not in self._result_views:self._result_views[result.identity]=EngineeringRun.compact_result(result)
        return self._result_views[result.identity]

    def _retain(self,study,result,*,stale=False,origin='opened',data=None):
        if data is not None:self._result_views[result.identity]=data
        self.history.append((study,result,stale,origin))
        self.history_selector.blockSignals(True)
        self.history_selector.addItem(f'{self._result_data(result)["status"]} · {result.identity[:10]}'+(' · historical' if stale else ''))
        self.history_selector.setCurrentIndex(len(self.history)-1);self.history_selector.blockSignals(False)
        self._select_history(len(self.history)-1,record=False)

    def _select_history(self,index,*,record=True):
        if index<0 or index>=len(self.history):return
        self.study,self.result,self.stale,origin=self.history[index]
        if record:self._record('Select retained engineering result',self._pair_code())
        data=self._result_data(self.result);self.chart.set_result(data)
        self.resume_button.setEnabled(self._resume_reason() is None)
        self._table(self.points,['Heel [deg]','GZ [m]','Trim [deg]','Origin z [m]','Righting moment [Nm]','Branch'],
            [[self._degree(p.get('heel_rad')),p.get('gz_m'),self._degree(p.get('trim_rad')),p.get('origin_z_m'),p.get('righting_moment_Nm'),p.get('branch')] for p in data.get('points',[])])
        equilibrium=data.get('equilibrium') or {}
        self._table(self.equilibrium,['Equilibrium quantity','Retained value'],[[k,v] for k,v in equilibrium.items()])
        self._table(self.metrics,['Metric','Retained value / reason'],self._metric_rows(data.get('metrics') or {}))
        evidence=dict(data)
        evidence['points']={'retained_count':len(data.get('points',[])),
            'first_identities':[p.get('identity') for p in data.get('points',[])[:12]],
            'full_evidence':'Saved paired study and Run evidence files'}
        self.details.setPlainText(json.dumps(evidence,indent=2,ensure_ascii=False))
        assurance=data.get('assurance') or {}
        assurance_text=assurance.get('geometry','unavailable') if isinstance(assurance,dict) else str(assurance)
        self.status.setText(('Historical · inputs changed · ' if self.stale else '')+data['status']+' · '+assurance_text.replace('_',' ')+' · no production qualification · '+origin)
        self.retained_state_changed.emit(self.study,self.result,self.stale)

    @staticmethod
    def _metric_rows(values,prefix=''):
        rows=[]
        for key,value in values.items():
            label=(prefix+' / '+key if prefix else key).replace('_',' ')
            if isinstance(value,dict):rows.extend(EngineeringStabilityPane._metric_rows(value,label))
            else:rows.append([label,value])
        return rows

    @staticmethod
    def _degree(value):return None if value is None else math.degrees(value)

    @staticmethod
    def _table(table,headers,rows):
        table.setColumnCount(len(headers));table.setHorizontalHeaderLabels(headers);table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                text='Unavailable' if value is None else json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else f'{value:.7g}' if isinstance(value,float) else str(value)
                item=QTableWidgetItem(text);item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable);item.setToolTip(text);table.setItem(i,j,item)
        table.verticalHeader().hide();table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.setMinimumSize(0,0)

    def open_study(self,path):
        if self.job or self._opening_task is not None:raise ValueError('Wait for the owned operation before replacing the displayed study')
        if Path(path).is_file() and Path(path).stat().st_size>1024*1024:
            task={'path':path,'done':False};self._opening_task=task
            def load():
                try:
                    task['study'],task['result']=api().load_stability_study(path)
                    task['data']=None if task['result'] is None else EngineeringRun.compact_result(task['result'])
                except Exception as error:task['error']=error
                finally:task['done']=True
            threading.Thread(target=load,daemon=True).start()
            self.status.setText('Reading retained study off the GUI thread; no calculation executed.');return
        study,result=api().load_stability_study(path)
        return self._opened_study(path,study,result)

    def _opened_study(self,path,study,result,data_view=None):
        if result is not None and data_view is not None:self._result_views[result.identity]=data_view
        self.study=study;self._use_opened_study=True
        self._updating=True
        data=study.to_dict()
        if data.get('owner_wheel'):self.owner_wheel.setText(str(data['owner_wheel']))
        if data.get('geometry_mode') in ('engineering_estimate','certified_point'):self.geometry_mode.setCurrentText(data['geometry_mode'])
        self.openings.setPlainText(json.dumps(data.get('openings',[]),indent=2))
        if data.get('pose_bounds'):self.pose_bounds.setText(json.dumps(data['pose_bounds']))
        if result is not None:
            request=self._result_data(result).get('request',{})
            controls=request.get('controls',{})
            for name,control in [('max_wall_seconds',self.wall_seconds),('max_owner_queries',self.query_limit),('max_evaluations',self.evaluation_limit),('max_points',self.point_limit)]:
                if name in controls:control.setValue(int(controls[name]))
            if request.get('mode') in ('free_trim','fixed_trim'):self.mode.setCurrentIndex(0 if request['mode']=='free_trim' else 1)
            if request.get('fixed_trim_rad') is not None:self.fixed_trim.setValue(math.degrees(request['fixed_trim_rad']))
            heels=request.get('heels_rad',[])
            if heels:self.heel_min.setValue(math.degrees(min(heels)));self.heel_max.setValue(math.degrees(max(heels)))
        self._updating=False
        if result is not None:self._retain(study,result,origin=str(path))
        else:
            self.result=None;self.chart.set_result({});self.status.setText('Study opened without a result; no calculation executed.')
            self.retained_state_changed.emit(self.study,None,False)
        self._record('Open engineering study',f'from anystability import services as stability\nengineering_study, engineering_result = stability.load_stability_study({str(Path(path).absolute())!r})\nassert engineering_study.identity == {study.identity!r}\n'+(f'engineering_pairs = globals().get("engineering_pairs", {{}})\nengineering_pairs[{str(Path(path).absolute())!r}] = (engineering_study, engineering_result)\n' if result is not None else ''))

    def open_dialog(self):
        path=QFileDialog.getOpenFileName(self,'Open engineering study','','Stability study (*.json);;All files (*)')[0]
        if path:self._guard(lambda:self.open_study(path))

    def _pair_code(self):
        index=self.history_selector.currentIndex()
        if self.result is None or not 0<=index<len(self.history):return ''
        origin=self.history[index][3]
        return ('from anystability import services as stability\n'
            'engineering_pairs = globals().get("engineering_pairs", {})\n'
            f'engineering_study, engineering_result = engineering_pairs.get({origin!r}) or stability.load_stability_study({origin!r})\n')

    def save_study(self,path):
        if self.study is None:raise ValueError('No frozen engineering study to save')
        api().save_stability_study(self.study,path,result=self.result)
        self._record('Save paired engineering study',self._pair_code()+f'stability.save_stability_study(engineering_study, {str(Path(path).absolute())!r}, result=engineering_result)\n')
        self.status.setText('Saved exact frozen input/result pair. Current edited controls are not substituted.')

    def save_dialog(self):
        path=QFileDialog.getSaveFileName(self,'Choose a paired study JSON file')[0]
        if path:self._guard(lambda:self.save_study(path))

    def export_report(self,path):
        if self.study is None or self.result is None:raise ValueError('Open or calculate a retained result before reporting')
        receipt=api().export_stability_report(self.study,self.result,path)
        self._record('Export engineering report',self._pair_code()+f'stability.export_stability_report(engineering_study, engineering_result, {str(Path(path).absolute())!r})\n')
        self.status.setText('Report exported from retained data only: '+str(path));return receipt

    def export_dialog(self):
        path=QFileDialog.getSaveFileName(self,'Choose a NEW static report directory')[0]
        if path:self._guard(lambda:self.export_report(path))

    def shutdown(self):
        if self.job and not self.job.close():return False
        self.job=None;self.timer.stop();return True
