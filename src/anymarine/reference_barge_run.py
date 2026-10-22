"""One independently supervised child run; snapshots and failures are retained."""
import json,os,signal,subprocess,sys,time,threading
from pathlib import Path
from .reference_barge import save_reference_barge,reference_controls,ReferenceBargeError,attach_coupled_result,_hash

class ReferenceBargeRun:
 def __init__(self,study,path,*,allow_reference=False,popen=None):
  if allow_reference is not True:raise ReferenceBargeError('CAPABILITY_UNSUPPORTED','explicit reference Run opt-in required')
  if study.selected_case!='coupled':raise ReferenceBargeError('INPUT_INVALID','only coupled case supports Run')
  self.study=study;self.path=Path(path).absolute();self.path.mkdir(exist_ok=False)
  save_reference_barge(study,self.path/'input')
  from anystability.validation import content_hash
  self.request_identity=content_hash(study.to_dict()['request'] or json.loads(study._records)['coupled_request'])
  self.started=time.monotonic();self.deadline=self.started+reference_controls(study)['max_wall_seconds'];self.status='running';self.detail='';self._abort_at=None
  self._lock=threading.RLock();self._stop=threading.Event();self._output_bytes=0
  python=Path(sys.executable)
  if python.name.lower()=='pythonw.exe':python=python.with_name('python.exe')
  self.command=[str(python),'-I','-u','-X','utf8','-m','anymarine.reference_barge_worker',str(self.path),str(self.deadline)]
  self._stdout=(self.path/'stdout.txt').open('wb');self._stderr=(self.path/'stderr.txt').open('wb')
  try:self.process=(popen or subprocess.Popen)(self.command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
  except OSError as error:
   self._stdout.close();self._stderr.close();self.status='refused';raise ReferenceBargeError('WORKER_START_FAILED',str(error)) from error
  self._reader_done=[]
  for pipe,output in ((self.process.stdout,self._stdout),(self.process.stderr,self._stderr)):
   done=threading.Event();self._reader_done.append(done);threading.Thread(target=self._capture,args=(pipe,output,done),daemon=True).start()
  self._watch=threading.Thread(target=self._supervise,daemon=True);self._watch.start()
 def _capture(self,pipe,output,done):
  try:
   while True:
    data=pipe.read(8192)
    if not data:break
    with self._lock:
     room=max(0,1_000_000-self._output_bytes);output.write(data[:room]);output.flush();self._output_bytes+=min(len(data),room)
    if len(data)>room:self.cancel('worker output limit exceeded; retained prefix only')
  except (OSError,ValueError) as error:self.cancel('worker capture failed: '+str(error))
  finally:
   try:pipe.close()
   except (OSError,AttributeError):pass
   done.set()
 def cancel(self,reason='user cancellation'):
  with self._lock:
   if self.status!='running':return
   self.detail=reason;self._abort_at=time.monotonic();self.status='aborting'
   try:(self.path/'cancel').write_bytes(b'cancel\n')
   except OSError as error:self.detail+='; cancel marker failed: '+str(error)
   self._journal()
 def _journal(self):
  try:
   from anystability.persistence import atomic_write
   from anystability.validation import canonical_bytes
   atomic_write(self.path/'parent-status.json',canonical_bytes({'study_identity':self.study.identity,'request_identity':self.request_identity,'status':self.status,'detail':self.detail,'elapsed_seconds':max(0,time.monotonic()-self.started),'production_qualified':False}),overwrite=True)
  except Exception as error:self.detail+='; parent receipt unavailable: '+str(error)[:300]
 def _kill(self):
  if self.process.poll() is not None:return
  try:
   if os.name=='nt':
    try:subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired):pass
   else:
    try:os.killpg(self.process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   if self.process.poll() is None:self.process.kill()
  except OSError as error:self.status='termination_unconfirmed';self.detail+='; termination failed: '+str(error)
 def _supervise(self):
  while not self._stop.wait(.025):
   if self.process.poll() is not None:return
   now=time.monotonic()
   if now>=self.deadline:self.cancel('parent hard deadline reached')
   if self.status in ('aborting','termination_unconfirmed') and now-self._abort_at>=.5:self._kill();self._abort_at=now
 def poll(self):
  code=self.process.poll()
  if code is None:return self.status
  for event in self._reader_done:event.wait(.1)
  if self.status in ('aborting','termination_unconfirmed'):self.status='aborted';return self.status
  if self.status!='running':return self.status
  try:
   if time.monotonic()>=self.deadline:raise ReferenceBargeError('WORK_BUDGET_EXCEEDED','late final reply refused')
   from anystability.persistence import read_bytes
   from anystability.validation import decode_json,exact
   reply=decode_json(read_bytes(self.path/'status.json'))
   expected={'status','study_identity','request_identity','production_qualified'}|({'error'} if reply.get('status')=='refused' else set())
   exact(reply,expected,'worker reply')
   if reply['production_qualified'] is not False or reply['status'] not in ('completed','refused') or reply['study_identity']!=self.study.identity or reply['request_identity']!=self.request_identity:
    raise ReferenceBargeError('SOURCE_STALE','worker receipt schema/study/request mismatch')
   if reply['status']=='refused':
    exact(reply['error'],{'code','type','message','cause_type','cause_message'},'worker refusal')
    if any(not isinstance(value,str) or len(value)>4000 for value in reply['error'].values()):raise ReferenceBargeError('INTEGRITY_ERROR','invalid worker refusal details')
   if code or reply['status']!='completed':self.status='refused';self.detail=str(reply.get('error',reply));return self.status
   result=attach_coupled_result(self.study,self.path/'result.json')
   with self._lock:
    if self.status!='running':return self.status
    if time.monotonic()>=self.deadline:raise ReferenceBargeError('WORK_BUDGET_EXCEEDED','late result validation refused')
    self.result=result;self.status='completed'
  except Exception as error:
   if self.status=='running':self.status='refused';self.detail=str(error)
  return self.status
 def finish(self):
  """Bounded explicit wait; return matching completed study or typed refusal."""
  while self.status in ('running','aborting','termination_unconfirmed') and time.monotonic()<self.deadline+3:
   self.poll()
   if self.status in ('running','aborting','termination_unconfirmed'):time.sleep(.025)
  if self.status=='completed':
   result=self.result;self.close();return result
  self.close()
  raise ReferenceBargeError('RUN_UNRESOLVED',self.status+': '+self.detail+'; evidence retained at '+str(self.path))
 def close(self):
  try:self.cancel('application closed')
  finally:
   self._kill()
   try:self.process.wait(timeout=1)
   except subprocess.TimeoutExpired:pass
   if self.process.poll() is None:
    self.status='termination_unconfirmed';self.detail+='; cleanup deadline reached; process remains supervised/owned';self._journal();return False
   self._stop.set()
   for event in self._reader_done:event.wait(.1)
   self._stdout.close();self._stderr.close()
   if self.status in ('running','aborting','termination_unconfirmed'):self.status='aborted'
   self._journal();return True
