# SPDX-License-Identifier: MPL-2.0
import hashlib,os,sys
import pytest
from test_hams_jobs import case

@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux backend contract handshake')
@pytest.mark.parametrize('status',[None,{},dict(platform='linux',contract='old-process-group',available=True),dict(platform='linux',contract='linux-owned-session-guard-v1',available=False,reason='runtime unavailable'),dict(platform='linux',contract='linux-owned-session-guard-v1',available=1)])
def test_linux_missing_old_or_unavailable_runtime_refuses_before_snapshot(case,monkeypatch,status):
    from anyhydro.hams_backend import HamsBackend
    from anymarine.hams_jobs import hams_input_snapshot,HamsJobError
    monkeypatch.setattr(HamsBackend,'native_supervision_status',staticmethod(lambda:status))
    before=case['ledger'].read_bytes() if case['ledger'].exists() else b''
    with pytest.raises(HamsJobError,match='supervision unavailable'):hams_input_snapshot(case['project'],case['config'])
    assert (case['ledger'].read_bytes() if case['ledger'].exists() else b'')==before

@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux public no-launch runtime probe')
def test_public_supervision_probe_is_available_and_starts_no_process(monkeypatch):
    import subprocess
    from anyhydro.hams_backend import HamsBackend
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('probe launched a child'))
    result=HamsBackend.native_supervision_status()
    assert result['available'] is True and result['contract']=='linux-owned-session-guard-v1'
    assert len(result['guard_sha256'])==64 and result['reason'] is None

@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux runtime capability refusals')
@pytest.mark.parametrize('condition',['pidfd','sigchld','waitid','guard'])
def test_public_supervision_probe_reports_runtime_failure(monkeypatch,condition):
    import signal
    from anyhydro.hams_backend import HamsBackend
    if condition=='pidfd':
        def refused(*a,**kw):raise OSError('test-private pidfd unavailable')
        monkeypatch.setattr(os,'pidfd_open',refused)
    elif condition=='sigchld':monkeypatch.setattr(signal,'getsignal',lambda *a:signal.SIG_IGN)
    elif condition=='waitid':
        import errno
        def unsupported(*a,**kw):raise OSError(errno.EINVAL,'test-private unsupported waitid')
        monkeypatch.setattr(os,'waitid',unsupported)
    else:
        from pathlib import Path
        def unreadable(*a,**kw):raise OSError('test-private guard unavailable')
        monkeypatch.setattr(Path,'read_bytes',unreadable)
    result=HamsBackend.native_supervision_status()
    assert result['available'] is False and result['reason']

@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Older same-version Linux backend')
def test_absent_backend_handshake_refuses_before_snapshot(case,monkeypatch):
    from anyhydro.hams_backend import HamsBackend
    from anymarine.hams_jobs import hams_input_snapshot,HamsJobError
    monkeypatch.delattr(HamsBackend,'native_supervision_status')
    before=case['ledger'].read_bytes() if case['ledger'].exists() else b''
    with pytest.raises(HamsJobError,match='missing verified backend ownership contract'):
        hams_input_snapshot(case['project'],case['config'])
    assert (case['ledger'].read_bytes() if case['ledger'].exists() else b'')==before
