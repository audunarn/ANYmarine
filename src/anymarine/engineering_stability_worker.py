"""Explicit engineering worker. The parent owns the process and deadline."""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from .engineering_stability import api

def _atomic(path, value):
    data = json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_bytes(data)
    for delay in (0,.005,.01,.02,.04,.08,.16):
        if delay:time.sleep(delay)
        try:
            temporary.replace(path);return
        except PermissionError:
            if delay==.16:raise

def execute(root, deadline, *, owner=None):
    root = Path(root)
    owner = owner or api()
    request = json.loads((root/'request.json').read_text('utf-8'))
    study, seed = owner.load_stability_study(root/'input')
    binding = request['host_request_identity']
    if study.identity != request['study_identity']:
        raise ValueError('frozen input identity differs from request')
    def cancelled():
        return (root/'cancel').exists()
    def progress(event):
        # Compact progress is independent of potentially64MiB accepted evidence.
        event=dict(event)
        checkpoint=event.pop('result',event.pop('checkpoint',None))
        try:
            if checkpoint is not None:
                if hasattr(owner,'EngineeringResult'):
                    typed=owner.EngineeringResult.from_dict(checkpoint)
                    owner.save_stability_study(study,root/'accepted.json',result=typed)
                _atomic(root/'checkpoint.json',{'study_identity':study.identity,
                    'host_request_identity':binding,'result':checkpoint})
            compact={key:event.get(key) for key in ('phase','heel_rad','query_count','evaluation_count')}
            _atomic(root/'progress.json',{'study_identity':study.identity,
                'host_request_identity':binding,**compact})
        except OSError as error:
            # Telemetry I/O cannot abort/discard a numerical result. Final persistence
            # remains mandatory and reports its own failure with evidence retained.
            try:
                with (root/'progress-errors.txt').open('a',encoding='utf-8') as stream:stream.write(str(error)[:2000]+'\n')
            except OSError:pass
    options = dict(request['options'])
    operation = options.pop('operation', 'gz')
    if cancelled():
        _atomic(root/'status.json', {'status':'cancelled', 'study_identity':study.identity,
            'host_request_identity':binding, 'error':'cancelled before owner execution'})
        return 2
    try:
        if operation == 'equilibrium':
            result = owner.solve_floating_equilibrium(study, **options,
                cancellation_check=cancelled, progress=progress,
                execution_started_monotonic=request.get('execution_started_monotonic'))
        else:
            result = owner.calculate_gz_curve(study, **options, seed_result=seed,
                cancellation_check=cancelled, progress=progress,
                execution_started_monotonic=request.get('execution_started_monotonic'))
    except Exception as error:
        evidence=getattr(error,'evidence',None)
        if evidence is not None:owner.save_stability_study(study,root/'quarantined.json',result=evidence)
        raise
    if result.to_dict()['study_identity'] != study.identity:
        raise ValueError('owner returned a result for a different study')
    owner.save_stability_study(study, root/'output', result=result)
    _atomic(root/'status.json', {'status':'returned', 'study_identity':study.identity,
        'host_request_identity':binding, 'result_identity':result.identity})
    return 0

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        return 2
    root = Path(argv[0])
    try:
        deadline=float(argv[1])
        while not (root/'go').exists():
            if (root/'cancel').exists() or time.monotonic()>=deadline:return 2
            time.sleep(.02)
        return execute(root, deadline)
    except Exception as error:
        request = json.loads((root/'request.json').read_text('utf-8'))
        _atomic(root/'status.json', {'status':'refused',
            'study_identity':request['study_identity'],
            'host_request_identity':request['host_request_identity'],
            'error':str(error)[:4000], 'code':str(getattr(error, 'code', 'WORKER_ERROR'))})
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
