"""Owner-free recipe3 artifact integration/adversarial/persistence checks."""
import builtins,json,time,dataclasses,io,os
from pathlib import Path
import pytest
from anymarine import reference_barge as r

@pytest.fixture(scope='module')
def study():
 if not os.environ.get('ANYMARINE_REFERENCE_BARGE_DIR'):pytest.skip('private local recipe3 data requires explicit ANYMARINE_REFERENCE_BARGE_DIR')
 return r.load_reference_barge()

def test_exact_public_records_all_cases_and_no_geometry_import(study,monkeypatch):
 original=builtins.__import__
 def guarded(name,*args,**kwargs):
  if name.split('.')[0] in ('anygeometry','PySide6'):raise AssertionError('owner/GUI imported on saved-data path')
  return original(name,*args,**kwargs)
 monkeypatch.setattr(builtins,'__import__',guarded)
 for case in r.CASES:
  view=r.reference_barge_view(r.select_reference_case(study,case))
  assert len(view['capacities'])==8 and len(view['calibration_rows'])==40
  assert view['condition']['items'] and view['mass_summary']['mass_kg']>0
  assert view['definition_identity']==r.DEFINITION
 assert r.reference_barge_view(study)['mass_summary']['mass_kg']==236304.32
 assert b'<svg' in r.reference_preview(study)

def test_controls_no_change_keeps_attached_result_and_lowerwall(study):
 root=study._root
 attached=r.attach_coupled_result(study,root/'coupled-result.json')
 kept=r.with_reference_controls(attached,r.reference_controls(attached))
 assert kept.to_dict()['result']==attached.to_dict()['result']
 controls=r.reference_controls(study);controls['max_wall_seconds']=1e-9
 edited=r.with_reference_controls(attached,controls)
 assert edited.to_dict()['result'] is None and r.reference_barge_view(edited)['status']=='unpaired_controls_edited'
 assert r.reference_barge_view(edited)['targets_m3']=={'WB3':31.0}
 assert r.reference_controls(edited)['max_wall_seconds']==1e-9
 controls=r.reference_controls(study);controls['max_owner_queries']+=1
 with pytest.raises(r.ReferenceBargeError):r.with_reference_controls(study,controls)
 controls=r.reference_controls(study);controls['volume_tolerance_m3']*=2
 with pytest.raises(r.ReferenceBargeError):r.with_reference_controls(study,controls)

def test_immutable_snapshot_forgery_preflight_no_effect(study,tmp_path):
 with pytest.raises(r.ReferenceBargeError):dataclasses.replace(study,_files=(('../outside.json',b'evil'),))
 with pytest.raises(r.ReferenceBargeError):dataclasses.replace(study,_records=b'{}')
 with pytest.raises(r.ReferenceBargeError):dataclasses.replace(study,_selection=bytearray(study._selection))
 assert not (tmp_path/'outside.json').exists()

def test_portable_roundtrip_and_manifestlast_failure(study,tmp_path,monkeypatch):
 selected=r.select_reference_case(study,'combined');saved=r.save_reference_barge(selected,tmp_path/'valid')
 assert saved.identity==selected.identity and saved.selected_case=='combined'
 assert r.reference_barge_view(saved)['condition']==r.reference_barge_view(selected)['condition']
 with pytest.raises(r.ReferenceBargeError):r.save_reference_barge(selected,tmp_path/'valid')
 from anystability import persistence
 original=persistence.atomic_write
 def fail(path,data,**kwargs):
  if Path(path).name=='study.json':raise PermissionError('commit locked')
  return original(path,data,**kwargs)
 monkeypatch.setattr(persistence,'atomic_write',fail)
 with pytest.raises(r.ReferenceBargeError):r.save_reference_barge(selected,tmp_path/'partial')
 assert (tmp_path/'partial/dataset.json').exists() and not (tmp_path/'partial/study.json').exists()
 with pytest.raises(r.ReferenceBargeError):r.load_reference_barge(tmp_path/'partial')
 monkeypatch.setattr(persistence,'atomic_write',original)
 payload=json.loads((tmp_path/'valid/study.json').read_bytes());payload['selection']['dataset_identity']='0'*64
 from anystability.validation import content_hash,canonical_bytes
 payload['integrity']=content_hash(payload['selection']);(tmp_path/'valid/study.json').write_bytes(canonical_bytes(payload))
 with pytest.raises(r.ReferenceBargeError):r.load_reference_barge(tmp_path/'valid')

class FakeProcess:
 def __init__(self,*args,**kwargs):self.stdout=io.BytesIO();self.stderr=io.BytesIO();self.code=None;self.pid=999999999;self.killed=False
 def poll(self):return self.code
 def kill(self):self.killed=True;self.code=-9
 def wait(self,timeout=None):
  if self.code is None:raise __import__('subprocess').TimeoutExpired('fake',timeout)
  return self.code

def test_worker_deadline_independent_poll_and_cancel_marker_failure(study,tmp_path,monkeypatch):
 from anymarine.reference_barge_run import ReferenceBargeRun
 controls=r.reference_controls(study);controls['max_wall_seconds']=.02
 snapshot=r.with_reference_controls(study,controls)
 job=ReferenceBargeRun(snapshot,tmp_path/'job',allow_reference=True,popen=FakeProcess)
 monkeypatch.setattr(job,'_kill',lambda:job.process.kill())
 deadline=time.monotonic()+2
 while not job.process.killed and time.monotonic()<deadline:time.sleep(.01)
 assert job.process.killed and job.status=='aborting' # watchdog acts with no poll.
 job.close();assert job.status=='aborted'
 job=ReferenceBargeRun(study,tmp_path/'marker',allow_reference=True,popen=FakeProcess)
 monkeypatch.setattr(job,'_kill',lambda:job.process.kill())
 original=Path.write_bytes
 def fail(path,data):
  if path.name=='cancel':raise PermissionError('marker blocked')
  return original(path,data)
 monkeypatch.setattr(Path,'write_bytes',fail)
 job.close();assert job.process.killed and job.status=='aborted' and 'marker failed' in job.detail

def test_worker_receipt_substitution_refuses(study,tmp_path):
 from anymarine.reference_barge_run import ReferenceBargeRun
 job=ReferenceBargeRun(study,tmp_path/'reply',allow_reference=True,popen=FakeProcess)
 try:
  (job.path/'status.json').write_text(json.dumps({'status':'completed','study_identity':study.identity,'request_identity':'0'*64,'production_qualified':False}))
  job.process.code=0;assert job.poll()=='refused' and 'mismatch' in job.detail
 finally:job.close()


def test_finish_fixture_result_and_transient_termination_recovery(study,tmp_path,monkeypatch):
 from anymarine.reference_barge_run import ReferenceBargeRun
 job=ReferenceBargeRun(study,tmp_path/'finish',allow_reference=True,popen=FakeProcess)
 (job.path/'result.json').write_bytes(dict(study._files)['coupled-result.json'])
 (job.path/'status.json').write_text(json.dumps({'status':'completed','study_identity':study.identity,'request_identity':job.request_identity,'production_qualified':False}))
 job.process.code=0;finished=job.finish();assert finished.to_dict()['result'] is not None and job.status=='completed'
 job=ReferenceBargeRun(study,tmp_path/'unconfirmed',allow_reference=True,popen=FakeProcess);calls=[]
 def fail_then_kill():
  calls.append(True)
  if len(calls)==1:job.status='termination_unconfirmed'
  else:job.process.kill()
 monkeypatch.setattr(job,'_kill',fail_then_kill)
 try:
  assert job.close() is False and job.status=='termination_unconfirmed'
  deadline=time.monotonic()+2
  while job.process.poll() is None and time.monotonic()<deadline:time.sleep(.01)
  assert len(calls)>=2 and job.process.killed
  assert job.poll()=='aborted'
 finally:job.process.code=-9;job.close()
