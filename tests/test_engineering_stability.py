"""Synthetic host-contract coverage. No geometry/provider execution is permitted."""
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

class Record:
    def __init__(self, identity, data):self.identity=identity;self.data=data
    def to_dict(self):return json.loads(json.dumps(self.data))

@pytest.fixture
def records():
    study=Record('study-A',{'identity':'study-A'})
    result=Record('result-A',{'status':'partial','study_identity':'study-A','request_identity':'request-A',
        'assurance':'engineering_estimate','equilibrium':{'mass_kg':1000.,'cg_body_m':[0,0,1]},
        'points':[{'heel_rad':-.1,'gz_m':-.02,'branch':'negative','trim_rad':0},
                  {'heel_rad':.1,'gz_m':.03,'branch':'positive','trim_rad':0}],
        'metrics':{'downflooding':{'value':None,'reason':'no opening inventory'}},'pending':['unresolved interval']})
    return study,result

class FakeAPI:
    def __init__(self,study,result):self.study=study;self.result=result;self.saved={};self.called=[]
    def save_stability_study(self,study,path,*,result=None):
        self.called.append(('save',study.identity,result.identity if result else None))
        Path(path).mkdir(exist_ok=False);self.saved[str(path)]=(study,result)
    def load_stability_study(self,path):
        self.called.append(('load',str(path)))
        return self.saved.get(str(path),(self.study,self.result))
    def workflow_defaults(self,profile):return {'max_wall_seconds':90}
    def calculate_gz_curve(self,study,**options):
        self.called.append(('calculate',options))
        options['progress']({'phase':'accepted','heel_rad':.1,'query_count':7,'evaluation_count':2,'result':self.result.to_dict()})
        return self.result
    def export_stability_report(self,study,result,path):
        self.called.append(('report',study.identity,result.identity));return {'destination':str(path)}

def settled(run):
    limit=time.monotonic()+2
    while run.poll() in ('running','cancelling','timing_out','termination_unconfirmed') and time.monotonic()<limit:time.sleep(.01)
    return run.poll()

class Process:
    pid=12345
    def __init__(self,*args,**kwargs):self.code=None;self.killed=False
    def poll(self):return self.code
    def kill(self):self.killed=True;self.code=-9
    def wait(self,timeout=None):self.code=-9 if self.code is None else self.code;return self.code

@pytest.fixture
def owner(monkeypatch,records):
    import anymarine.engineering_stability_run as run
    fake=FakeAPI(*records);monkeypatch.setattr(run,'api',lambda:fake)
    # Fake process on Windows ownership path; no platform process/group is touched.
    monkeypatch.setattr(run.EngineeringRun,'_kill',lambda self:self.process.kill() if self.process.poll() is None else None)
    return fake

def test_snapshot_and_cancel_only_owned_process(tmp_path,records,owner):
    from anymarine.engineering_stability_run import EngineeringRun
    run=EngineeringRun(records[0],tmp_path/'job',options={'operation':'gz','controls':{'max_wall_seconds':3}},popen=Process)
    try:
        request=json.loads((run.path/'request.json').read_text())
        assert request['study_identity']=='study-A' and request['host_request_identity']==run.request_identity
        assert owner.called==[('save','study-A',None)]
        run.cancel();assert (run.path/'cancel').exists()
        assert run.close() and run.process.killed
        assert (run.path/'input').is_dir() and (run.path/'host.json').exists()
    finally:run.close()

def test_worker_public_api_checkpoint_and_paired_output(tmp_path,records,owner):
    from anymarine.engineering_stability_run import EngineeringRun
    from anymarine.engineering_stability_worker import execute
    run=EngineeringRun(records[0],tmp_path/'job',options={'operation':'gz','controls':{'max_wall_seconds':3}},popen=Process)
    try:
        assert execute(run.path,time.monotonic()+2,owner=owner)==0
        run.process.code=0
        assert settled(run)=='partial' and run.result is records[1]
        assert run.partial['study_identity']=='study-A'
        assert run.progress['query_count']==7
        assert run.finish() is records[1]
    finally:run.close()

def test_worker_cancellation_before_owner_operation(tmp_path,records,owner):
    from anymarine.engineering_stability_run import EngineeringRun
    from anymarine.engineering_stability_worker import execute
    run=EngineeringRun(records[0],tmp_path/'job',options={'controls':{'max_wall_seconds':3}},popen=Process)
    try:
        run.cancel();assert execute(run.path,time.monotonic()+2,owner=owner)==2
        assert not any(row[0]=='calculate' for row in owner.called)
    finally:run.close()

def test_mismatched_child_receipt_is_refused(tmp_path,records,owner):
    from anymarine.engineering_stability_run import EngineeringRun
    run=EngineeringRun(records[0],tmp_path/'job',options={'controls':{'max_wall_seconds':3}},popen=Process)
    try:
        (run.path/'progress.json').write_text(json.dumps({'study_identity':'other','host_request_identity':run.request_identity}))
        run.process.code=0
        assert settled(run)=='refused' and 'mismatch' in run.detail and run.result is None
    finally:run.close()

def test_request_identity_binds_limits_and_seed(records):
    from anymarine.engineering_stability import request_identity
    s=records[0]
    assert request_identity(s,{'max_points':5})!=request_identity(s,{'max_points':6})
    assert request_identity(s,{},'previous')!=request_identity(s,{})

@pytest.fixture(scope='module')
def app():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

@pytest.fixture
def pane(app,monkeypatch,records):
    import anymarine.qtapp.engineering_stability as ui
    fake=FakeAPI(*records);monkeypatch.setattr(ui,'api',lambda:fake)
    widget=ui.EngineeringStabilityPane();widget._test_api=fake
    yield widget
    widget.shutdown();widget.close()

def test_open_report_save_never_calculates(pane,tmp_path,records):
    pane.open_study(tmp_path/'input')
    pane.save_study(tmp_path/'copy');pane.export_report(tmp_path/'report')
    assert pane.result is records[1]
    assert all(row[0]!='calculate' for row in pane._test_api.called)
    assert ('save','study-A','result-A') in pane._test_api.called

def test_controls_mark_retained_result_stale_without_mutation(pane,records):
    pane._retain(*records);before=pane.result.to_dict()
    pane.fixed_trim.setValue(2)
    assert pane.stale and pane.result.to_dict()==before
    assert 'historical' in pane.status.text()

def test_preview_options_units_and_limits(pane):
    options=pane.options('preview')
    assert options['heels_rad']==pytest.approx([-.1745329252,-.0872664626,.0872664626,.1745329252])
    assert options['controls']=={'max_wall_seconds':90,'max_owner_queries':1024,'max_evaluations':48,'max_points':5}
    pane.mode.setCurrentIndex(1);pane.fixed_trim.setValue(3)
    assert pane.options('preview')['fixed_trim_rad']==pytest.approx(.05235987756)

def test_layout_and_chart_retained_branches(pane,app,records,tmp_path):
    pane._retain(*records);pane.resize(1024,720);pane.show();app.processEvents()
    image=pane.grab();assert not image.isNull();image.save(str(tmp_path/'engineering-pane.png'))
    chart=pane.chart;chart.repaint();app.processEvents()
    assert len(chart._hits)==2 and {p['branch'] for _,p in chart._hits}=={'negative','positive'}
    assert any('no opening inventory' in pane.metrics.item(i,1).text() for i in range(pane.metrics.rowCount()))

def test_fake_run_finish_retains_original_pair_after_edits(pane,records,tmp_path):
    study,result=records
    class Finished:
        progress={};partial=None;request_identity='run';detail='';path=tmp_path
        def poll(self):return 'partial'
        def close(self):return True
    job=Finished();job.result=result;pane.job=job;pane._run_study=study;pane.stale=True
    pane.poll()
    assert pane.job is None and pane.result is result and pane.stale and pane.history[-1][0] is study


def test_resume_rejects_complete_spent_or_changed_request(pane,records):
    study,result=records
    result.data.update(kind='gz_curve',request={'controls':{'max_wall_seconds':90,'max_owner_queries':10,'max_evaluations':8,'max_points':5}},
        work={'resumable':True,'elapsed_wall_seconds':20,'attempted_owner_queries':3,'evaluation_count':2,'accepted_point_count':2})
    pane._retain(study,result)
    assert pane._resume_reason() is None and pane.resume_button.isEnabled()
    result.data['work']['attempted_owner_queries']=10;pane._result_views.clear()
    assert 'exhausted' in pane._resume_reason()
    result.data['work']['attempted_owner_queries']=3;pane._result_views.clear()
    pane.mark_stale();assert 'Inputs changed' in pane._resume_reason()


def test_capture_engineering_layouts(pane,app,records):
    from PySide6.QtGui import QFontDatabase,QFont
    font_id=QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    families=QFontDatabase.applicationFontFamilies(font_id)
    assert families, 'Windows Segoe UI must load for meaningful visual QA'
    app.setFont(QFont(families[0],10))
    pane._retain(*records)
    for width in (1024,1280):
        pane.resize(width,760);pane.show();app.processEvents()
        assert pane.width()==width
        assert pane.chart.width()>300 and pane.chart.height()>=220
        pane.grab().save('C:/ANYworkspaceWork/anystability-usable-gz-20261024/marine/engineering-'+str(width)+'x760.png')

def test_public_binding_owner_free_snapshot_report(tmp_path,monkeypatch):
    """Use actual public core persistence/report, with geometry imports forbidden."""
    import sys
    import importlib.abc
    class DenyGeometry(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname.lower().startswith(('anygeometry','capytaine')):
                raise AssertionError('owner import during persistence/report: '+fullname)
    blocker=DenyGeometry();sys.meta_path.insert(0,blocker)
    try:
        from anystability import services as public
        from anystability.domain import LoadingCondition,MassItem
        from anystability.conventions import Pose
        from anystability.validation import content_hash
        vertices=[[x,y,z] for x in (-5.,5.) for y in (-2.,2.) for z in (0.,4.)]
        source={'manifest':{'capacity_comparisons':{'HULL':{'volume_m3':160.,'centroid_body_m':[0.,0.,2.]}}},
            'files':{'definition.json':{'hull':{'faces_body_m':[vertices]},'spaces':{}},'HULL.geometry.json':{}}}
        base=LoadingCondition('test','Synthetic dry condition',(MassItem('dry',40000.,(0.,0.,.5),'synthetic',('dry',)),),1000.,10.)
        study=public.create_stability_study(source,base_condition=base,targets_m3={},densities_kg_m3={},
            initial_pose=Pose(0.,0.,-1.),pose_bounds=[[-8.,8.],[-1.3,1.3],[-.3,.3]],owner_wheel='fake-local-wheel',allow_development=True)
        request={'study_identity':study.identity,'calculation':'gz_curve','controls':public.workflow_defaults()}
        data={'schema':'anystability-engineering-workflow-v1','kind':'gz_curve','status':'refused',
            'study_identity':study.identity,'request':request,'request_identity':content_hash(request),
            'assurance':{'production_qualified':False,'geometry':'unavailable'},'equilibrium':None,'points':[],'branches':{},
            'metrics':{},'work':{},'failures':[],'pending':None}
        data['work']={'elapsed_wall_seconds':0.,'attempted_owner_queries':0,'completed_owner_queries':0,
            'evaluation_count':0,'accepted_point_count':0,'queries':[],'evaluations':[],'limits':request['controls']}
        data['identity']=content_hash(data)
        result=public.EngineeringResult.from_dict(data)
        public.save_stability_study(study,tmp_path/'pair.json',result=result)
        reopened,retained=public.load_stability_study(tmp_path/'pair.json')
        assert (reopened.identity,retained.identity)==(study.identity,result.identity)
        public.export_stability_report(reopened,retained,tmp_path/'report')
        assert (tmp_path/'report'/'manifest.json').is_file()
    finally:sys.meta_path.remove(blocker)

@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows job contract')
def test_windows_owned_job_kills_fake_child_before_gate(tmp_path):
    import subprocess,sys
    from anymarine.engineering_stability_process import OwnedProcessTree
    child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(30)'],
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    tree=None
    try:
        tree=OwnedProcessTree(child)
        tree.terminate();child.wait(timeout=2)
        assert child.poll() is not None
    finally:
        if tree:tree.close()
        if child.poll() is None:child.kill();child.wait(timeout=2)

def test_recorded_history_and_cleared_report_keep_exact_pair(pane,records,tmp_path,monkeypatch):
    from anystability import services as public
    records_out=[];pane.recorder=lambda *row:records_out.append(row)
    study_a,result_a=records
    study_b=Record('study-B',{'identity':'study-B'})
    data=result_a.to_dict();data['study_identity']='study-B'
    result_b=Record('result-B',data)
    first=tmp_path/'first.json';second=tmp_path/'second.json'
    pane._test_api.saved[str(first)]=(study_a,result_a)
    pane._test_api.saved[str(second)]=(study_b,result_b)
    pane.open_study(first);pane.open_study(second)
    pane.history_selector.setCurrentIndex(0)
    pane.export_report(tmp_path/'report')
    monkeypatch.setattr(public,'load_stability_study',pane._test_api.load_stability_study)
    monkeypatch.setattr(public,'export_stability_report',pane._test_api.export_stability_report)
    source='engineering_pairs = {}\n'+''.join(row[1] for row in records_out)
    namespace={};exec(compile(source,'<engineering history recording>','exec'),namespace)
    assert namespace['engineering_study'].identity=='study-A'
    assert namespace['engineering_result'].identity=='result-A'
    # Clearing recording resets the replay map; persisted frozen pair still resolves.
    records_out.clear();pane.export_report(tmp_path/'report-cleared')
    namespace={'engineering_pairs':{}}
    exec(compile(records_out[-1][1],'<after recording clear>','exec'),namespace)
    assert namespace['engineering_result'].identity=='result-A'


def test_atomic_replace_retries_transient_windows_lock(tmp_path,monkeypatch):
    from anymarine.engineering_stability_worker import _atomic
    original=Path.replace;attempts=[]
    def replace(path,target):
        attempts.append(target)
        if len(attempts)<4:raise PermissionError('synthetic reader lock')
        return original(path,target)
    monkeypatch.setattr(Path,'replace',replace)
    _atomic(tmp_path/'progress.json',{'phase':'accepted'})
    assert len(attempts)==4 and json.loads((tmp_path/'progress.json').read_text())['phase']=='accepted'


def test_progress_io_failure_does_not_abort_final_result(tmp_path,records,owner,monkeypatch):
    import anymarine.engineering_stability_worker as worker
    from anymarine.engineering_stability_run import EngineeringRun
    run=EngineeringRun(records[0],tmp_path/'job',options={'operation':'gz','controls':{'max_wall_seconds':3}},popen=Process)
    original=worker._atomic
    def locked(path,value):
        if path.name in ('progress.json','checkpoint.json'):raise PermissionError('synthetic telemetry lock')
        return original(path,value)
    monkeypatch.setattr(worker,'_atomic',locked)
    try:
        assert worker.execute(run.path,time.monotonic()+2,owner=owner)==0
        assert (run.path/'status.json').exists() and (run.path/'progress-errors.txt').exists()
    finally:run.close()


def test_hard_timeout_is_not_user_cancel_and_poll_is_nonblocking(tmp_path,records,owner,monkeypatch):
    from anymarine.engineering_stability_run import EngineeringRun
    run=EngineeringRun(records[0],tmp_path/'job',options={'controls':{'max_wall_seconds':3}},popen=Process)
    try:
        run._timeout()
        assert (run.path/'timeout').exists() and not (run.path/'cancel').exists()
        assert run.poll()=='timing_out'
        monkeypatch.setattr(run,'_read_bound',lambda name:(_ for _ in ()).throw(AssertionError('GUI poll performed I/O')))
        assert run.poll()=='timing_out'
    finally:run.close()


def test_accepted_only_checkpoint_cannot_resume_even_through_api(tmp_path,records,owner):
    from anymarine.engineering_stability_run import EngineeringRun
    records[1].data['work']={'resumable':False}
    with pytest.raises(Exception,match='unknown spent work'):
        EngineeringRun(records[0],tmp_path/'forbidden',options={},seed_result=records[1],popen=Process)
    assert not (tmp_path/'forbidden').exists()


def test_checkpoint_cache_reuses_unchanged_record_and_tolerates_reader_lock(tmp_path,records,owner,monkeypatch):
    from anymarine.engineering_stability_run import EngineeringRun
    run=EngineeringRun(records[0],tmp_path/'job',options={'controls':{'max_wall_seconds':3}},popen=Process)
    try:
        target=run.path/'bounded.json'
        target.write_text(json.dumps({'study_identity':records[0].identity,'host_request_identity':run.request_identity,'result':{}}))
        first=run._read_bound('bounded.json')
        original=Path.read_text
        def locked(path,*args,**kwargs):
            if path==target:raise PermissionError('synthetic lock')
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'read_text',locked)
        assert run._read_bound('bounded.json') is first
        target.write_text('')
        assert run._read_bound('bounded.json') is first
    finally:run.close()


def test_missing_resumable_fails_closed_before_spawn(tmp_path,records,owner):
    from anymarine.engineering_stability_run import EngineeringRun
    records[1].data['work']={}
    with pytest.raises(Exception,match='unknown spent work'):
        EngineeringRun(records[0],tmp_path/'forbidden-legacy',options={},seed_result=records[1],popen=Process)


def test_background_compaction_failure_becomes_retained_refusal(tmp_path,records,owner,monkeypatch):
    from anymarine.engineering_stability_run import EngineeringRun
    from anymarine.engineering_stability_worker import execute
    run=EngineeringRun(records[0],tmp_path/'job',options={'operation':'gz','controls':{'max_wall_seconds':3}},popen=Process)
    try:
        execute(run.path,time.monotonic()+2,owner=owner)
        monkeypatch.setattr(run,'compact_result',lambda result:(_ for _ in ()).throw(ValueError('synthetic compact failure')))
        run.process.code=0
        assert settled(run)=='refused'
        assert 'synthetic compact failure' in run.detail and (run.path/'output').exists()
    finally:run.close()


def test_real_fake_provider_child_returns_typed_deadline_within_three_seconds(tmp_path):
    """Real harmless child; actual public workflow and worker.main, no geometry owner."""
    import subprocess,sys
    from anystability import services as public
    from anystability.domain import LoadingCondition,MassItem
    from anystability.conventions import Pose
    from anymarine.engineering_stability_run import EngineeringRun
    vertices=[[x,y,z] for x in (-5.,5.) for y in (-2.,2.) for z in (0.,4.)]
    source={'manifest':{'capacity_comparisons':{'HULL':{'volume_m3':160.,'centroid_body_m':[0.,0.,2.]}}},
        'files':{'definition.json':{'hull':{'faces_body_m':[vertices]},'spaces':{}},'HULL.geometry.json':{}}}
    study=public.create_stability_study(source,
        base_condition=LoadingCondition('clock','Fake provider clock',(MassItem('dry',40000.,(0.,0.,.5),'fake',('dry',)),),1000.,10.),
        targets_m3={},densities_kg_m3={},initial_pose=Pose(0.,0.,-1.),
        pose_bounds=[[-8,8],[-1,1],[-.3,.3]],owner_wheel='fake-only',allow_development=True)
    childcode="""
import sys,time,importlib.abc
sys.path[:0]=['C:/Github/ANYmarine/src','C:/Github/ANYstability/src']
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.lower().startswith(('anygeometry','capytaine')):raise AssertionError('real owner forbidden')
sys.meta_path.insert(0,Block())
from anystability import workflow
class FakeOwnerProvider:
 def __init__(self,spec,poll,deadline):self.poll=poll
 def query(self,*args):
  while True:self.poll();time.sleep(.01)
 def finish(self):pass
workflow._OwnerProvider=FakeOwnerProvider
from anymarine.engineering_stability_worker import main
raise SystemExit(main(sys.argv[1:]))
"""
    kills=[]
    def child(command,**kwargs):
        process=subprocess.Popen(command[:5]+['-c',childcode]+command[7:],**kwargs)
        original=process.kill
        def kill():kills.append(True);return original()
        process.kill=kill
        return process
    run=EngineeringRun(study,tmp_path/'three-second-real-child',
        options={'operation':'equilibrium','controls':{'max_wall_seconds':3}},popen=child)
    try:
        result=run.finish().to_dict()
        receipt=json.loads((run.path/'status.json').read_text())
        assert receipt['status']=='returned' and not kills
        assert any(f['error']['code']=='DEADLINE_EXCEEDED' for f in result['failures'])
        assert run.process.returncode==0
        assert result['work']['elapsed_wall_seconds']<=3
        assert not (run.path/'cancel').exists()
    finally:run.close()
