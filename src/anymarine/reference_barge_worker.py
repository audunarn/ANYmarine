"""Bounded explicit coupled-reference worker; no calculation on import."""
import json,sys,time
from pathlib import Path
from .reference_barge import load_reference_barge,_owner,ReferenceBargeError

def main(argv=None):
 args=sys.argv[1:] if argv is None else argv
 if len(args)!=2:return 2
 root=Path(args[0]);deadline=float(args[1]);binding=None;request_identity=None
 try:
  study=load_reference_barge(root/'input');binding=study.identity
  from anystability.validation import content_hash
  request_identity=content_hash(study.to_dict()['request'] or json.loads(study._records)['coupled_request'])
  if (root/'cancel').exists() or time.monotonic()>=deadline:raise ReferenceBargeError('CANCELLED','cancel/deadline before owner execution; no query attempted')
  if study.selected_case!='coupled':raise ReferenceBargeError('INPUT_INVALID','only coupled equilibrium Run supported')
  with _owner() as a:
   spec=study.to_dict()['request'] or json.loads(study._records)['coupled_request']
   request=a.coupled.CoupledRequest(a.v.canonical_bytes(spec));request_identity=request.identity
   if (root/'cancel').exists() or time.monotonic()>=deadline:raise ReferenceBargeError('CANCELLED','cancel/deadline before owner execution; no query attempted')
   a.coupled.execute_coupled_request(root/'input/model',request,allow_reference=True,output=root/'result.json',cancel=lambda:(root/'cancel').exists() or time.monotonic()>=deadline)
  status={'status':'completed','study_identity':binding,'request_identity':request_identity,'production_qualified':False}
 except Exception as error:
  cause=error.__cause__ or error
  status={'status':'refused','study_identity':binding,'request_identity':request_identity,'error':{'code':str(getattr(error,'code','WORKER_ERROR')),'type':type(error).__module__+'.'+type(error).__qualname__,'message':str(error)[:4000],'cause_type':type(cause).__module__+'.'+type(cause).__qualname__,'cause_message':str(cause)[:4000]},'production_qualified':False}
 from anystability.persistence import atomic_write
 from anystability.validation import canonical_bytes
 atomic_write(root/'status.json',canonical_bytes(status))
 return 0 if status['status']=='completed' else 2

if __name__=='__main__':raise SystemExit(main())
