"""Supervised, independently cancellable engineering calculation process."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from .engineering_stability import api, request_identity, EngineeringIntegrationError
from .engineering_stability_worker import _atomic
from .engineering_stability_process import OwnedProcessTree

class EngineeringRun:
    def __init__(self, study, path, *, options, seed_result=None, popen=None):
        self.started = time.monotonic()
        if seed_result is not None and seed_result.to_dict().get('work',{}).get('resumable') is not True:
            raise EngineeringIntegrationError('RESUME_UNAVAILABLE','Hard-killed/accepted-only checkpoints have unknown spent work and cannot resume')
        self.study = study
        self.path = Path(path).absolute()
        self.options = json.loads(json.dumps(options, allow_nan=False))
        self.request_identity = request_identity(study, self.options,
            None if seed_result is None else seed_result.identity)
        self._tree = None
        self.result = None
        self.partial = None
        self.progress = {}
        self.detail = ''
        self.status = 'preparing'
        self._closed = False
        self._read_cache={}
        self.result_data=None
        self._timed_out=False
        self._loading=False
        self._stop = threading.Event()
        self._cancel_at = None
        self._lock = threading.RLock()
        self.path.mkdir(exist_ok=False)
        # Public persistence freezes the complete source, not a mutable live-study pointer.
        api().save_stability_study(study, self.path/'input', result=seed_result)
        _atomic(self.path/'request.json', {'study_identity':study.identity,
            'host_request_identity':self.request_identity, 'options':self.options,
            'seed_identity':None if seed_result is None else seed_result.identity,
            'execution_started_monotonic':self.started})
        seconds = self.options.get('controls', {}).get('max_wall_seconds')
        if seconds is None:
            seconds = api().workflow_defaults(self.options.get('profile', 'equilibrium')).get('max_wall_seconds', 90)
        spent=0 if seed_result is None else seed_result.to_dict().get('work',{}).get('elapsed_wall_seconds',0)
        self.deadline = self.started + max(0,float(seconds)-spent)
        python = Path(sys.executable)
        if python.name.lower() == 'pythonw.exe':
            python = python.with_name('python.exe')
        self.command = [str(python), '-I', '-u', '-X', 'utf8', '-m',
            'anymarine.engineering_stability_worker', str(self.path), str(self.deadline)]
        self._stdout = (self.path/'stdout.txt').open('wb')
        self._stderr = (self.path/'stderr.txt').open('wb')
        try:
            self.process = (popen or subprocess.Popen)(self.command,
                stdin=subprocess.DEVNULL, stdout=self._stdout, stderr=self._stderr,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                start_new_session=os.name != 'nt')
        except OSError:
            self._stdout.close()
            self._stderr.close()
            raise
        try:
            # Test popen injection runs no OS child. Production must bind its tree before GO.
            if popen is None:self._tree=OwnedProcessTree(self.process)
            (self.path/'go').write_bytes(b'go\n')
        except Exception:
            self.process.kill();self.process.wait(timeout=1)
            self._stdout.close();self._stderr.close()
            raise
        self.status = 'running'
        self._watch = threading.Thread(target=self._supervise, daemon=True)
        self._watch.start()
        self._reader=threading.Thread(target=self._read_loop,daemon=True);self._reader.start()

    def _kill(self):
        if self.process.poll() is None:
            try:
                if os.name == 'nt':
                    if self._tree is not None:self._tree.terminate()
                    else:self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
            except OSError as error:
                self.detail += '; termination failed: '+str(error)
                self.status = 'termination_unconfirmed'

    def _supervise(self):
        while not self._stop.wait(.05):
            if self.process.poll() is not None:return
            now = time.monotonic()
            if now >= self.deadline:
                self._timeout()
            if self._cancel_at is not None and now-self._cancel_at >= .75:
                self._kill()

    def _read_loop(self):
        while not self._stop.wait(.05):
            self._loading=True
            try:self._refresh()
            except Exception as error:
                self.detail='background result processing failed: '+str(error)
                self.result=None;self.result_data=None
                if self.process.poll() is None:
                    self.cancel(self.detail);self._kill()
                self.status='refused'
                self._stop.set()
            finally:self._loading=False
            if self.process.poll() is not None:return

    def _timeout(self):
        with self._lock:
            if self.status!='running':return
            self._timed_out=True;self.status='timing_out';self.detail='parent hard deadline reached'
            self._cancel_at=time.monotonic()
            try:(self.path/'timeout').write_text(self.detail,encoding='utf-8')
            except OSError:pass

    def cancel(self, reason='user cancellation'):
        with self._lock:
            if self.status != 'running':
                return
            self.status = 'cancelling'
            self.detail = str(reason)
            self._cancel_at = time.monotonic()
            try:(self.path/'cancel').write_text(self.detail, encoding='utf-8')
            except OSError as error:self.detail+='; cancel marker failed: '+str(error)

    def _read_bound(self,name):
        path=self.path/name
        previous=self._read_cache.get(name)
        try:
            stat=path.stat();signature=(stat.st_mtime_ns,stat.st_size)
            if previous and previous[0]==signature:return previous[1]
            if stat.st_size>64*1024*1024:raise ValueError('worker record exceeds64MiB transport bound')
            record=json.loads(path.read_text('utf-8'))
        except (PermissionError,FileNotFoundError,json.JSONDecodeError):
            return previous[1] if previous else None
        if record.get('study_identity')!=self.study.identity or record.get('host_request_identity')!=self.request_identity:
            raise ValueError('worker record input/request identity mismatch')
        self._read_cache[name]=(signature,record)
        return record

    @staticmethod
    def compact_result(result):
        data=result.to_dict()
        work=data.get('work',{})
        data['work']={key:value for key,value in work.items() if key not in ('queries','evaluations')}
        return data

    def poll(self):
        # All filesystem reads/JSON parsing are owned by the supervisor thread.
        return 'running' if self._loading and self.status not in ('running','cancelling','timing_out','termination_unconfirmed') else self.status

    def _refresh(self):
        if self.status not in ('running', 'cancelling', 'timing_out', 'termination_unconfirmed'):
            return self.status
        try:
            self.progress = self._read_bound('progress.json') or self.progress
            checkpoint = self._read_bound('checkpoint.json')
            if checkpoint:
                self.partial = checkpoint['result']
            code = self.process.poll()
            if code is None:
                return self.status
            self._stop.set()
            receipt = self._read_bound('status.json')
            if receipt and receipt['status'] == 'returned' and code == 0:
                study, result = api().load_stability_study(self.path/'output')
                if study.identity != self.study.identity or result is None or result.identity != receipt['result_identity'] or result.to_dict()['study_identity'] != self.study.identity:
                    raise ValueError('worker output identity mismatch')
                self.result = result
                self.status = result.to_dict()['status']
            elif self._cancel_at is not None:
                self.status = 'timed_out' if self._timed_out else 'cancelled'
                accepted=self.path/'accepted.json'
                if accepted.is_file():
                    saved_study,saved_result=api().load_stability_study(accepted)
                    if saved_study.identity!=self.study.identity or saved_result.to_dict()['study_identity']!=self.study.identity:
                        raise ValueError('accepted checkpoint input identity mismatch')
                    self.result=saved_result
            else:
                self.status = 'refused'
                self.detail = (receipt or {}).get('error', f'worker exited {code} without final receipt')
        except Exception as error:
            self.detail = str(error)
            if self.process.poll() is None:
                self.cancel('invalid worker transport: '+str(error))
            else:
                self.status = 'refused'
        if self.result is not None and self.result_data is None:self.result_data=self.compact_result(self.result)
        return self.status

    def finish(self):
        while self.status in ('running', 'cancelling', 'timing_out', 'termination_unconfirmed') and time.monotonic() < self.deadline+2:
            self.poll()
            time.sleep(.025)
        self.poll()
        self.close()
        if self.result is None:
            raise EngineeringIntegrationError('RUN_UNRESOLVED', self.status+': '+self.detail+'; evidence '+str(self.path))
        return self.result

    def close(self):
        if self._closed:
            return True
        self.cancel('application closed')
        self._kill()
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.status = 'termination_unconfirmed'
            return False
        self._stop.set()
        if self._tree is not None:self._tree.close()
        self._stdout.close()
        self._stderr.close()
        self._closed = True
        _atomic(self.path/'host.json', {'status':self.status, 'detail':self.detail,
            'study_identity':self.study.identity, 'host_request_identity':self.request_identity,
            'process_wall_seconds':time.monotonic()-self.started, 'command':self.command})
        return True
