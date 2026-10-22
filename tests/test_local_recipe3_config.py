"""Qt/geometry-free local configuration and private-package boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest
from anymarine import reference_barge


@pytest.fixture
def checkout():
    root = Path(__file__).resolve().parents[1]
    if not (root / 'local_gui_config.py').is_file():
        pytest.skip('checkout-only launcher/config files not installed')
    return root


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_local_config_precedence_and_strict_failures(checkout,tmp_path):
    local = module(checkout/'local_gui_config.py','local_config_test')
    assets=tmp_path/'private';assets.mkdir()
    interpreter=tmp_path/'python.exe';interpreter.write_bytes(b'not executed')
    second=tmp_path/'override.exe';second.write_bytes(b'not executed')
    settings={'schema_version':1,'python':str(interpreter),'reference_barge_path':str(assets)}
    config=tmp_path/'.anymarine-local.json'
    assert local.local_settings(tmp_path,{})==(None,None)
    config.write_text(json.dumps(settings))
    assert local.local_settings(tmp_path,{})==(str(interpreter),str(assets))
    assert local.local_settings(tmp_path,{'ANYMARINE_PYTHON':str(second)})==(str(second),str(assets))
    alternate=tmp_path/'alternate';alternate.mkdir()
    assert local.local_settings(tmp_path,{'ANYMARINE_REFERENCE_BARGE_DIR':str(alternate)})==(str(interpreter),str(alternate))
    config.write_text(json.dumps({**settings,'python':str(tmp_path/'stale.exe'),'reference_barge_path':str(tmp_path/'stale-assets')}))
    assert local.local_settings(tmp_path,{'ANYMARINE_PYTHON':str(second),'ANYMARINE_REFERENCE_BARGE_DIR':str(alternate)})==(str(second),str(alternate))
    for malformed in ('{broken', '[]', json.dumps({**settings,'schema_version':True}),
                      json.dumps({**settings,'unknown':0}), '{"schema_version":1,"schema_version":1}',
                      ' '*16385, json.dumps({**settings,'python':'relative.exe'})):
        config.write_text(malformed)
        with pytest.raises(ValueError):local.local_settings(tmp_path,{'ANYMARINE_PYTHON':str(second)})
    config.unlink()
    for env in ({'ANYMARINE_PYTHON':''},{'ANYMARINE_PYTHON':str(tmp_path/'missing')},
                {'ANYMARINE_REFERENCE_BARGE_DIR':'relative'}):
        with pytest.raises(ValueError):local.local_settings(tmp_path,env)


def test_launcher_exports_assets_before_runtime_relaunch(checkout,tmp_path,monkeypatch):
    local=module(checkout/'local_gui_config.py','local_gui_config')
    monkeypatch.setitem(sys.modules,'local_gui_config',local)
    launcher=module(checkout/'run_gui.py','checkout_launcher_test')
    interpreter=tmp_path/'python.exe';interpreter.write_bytes(b'not executed')
    assets=tmp_path/'private';assets.mkdir();calls=[]
    monkeypatch.setattr(launcher,'local_settings',lambda *args:(str(interpreter),str(assets)))
    monkeypatch.setattr(launcher.subprocess,'call',lambda command:calls.append((command,os.environ.get('ANYMARINE_REFERENCE_BARGE_DIR'))) or 7)
    monkeypatch.setattr(sys,'argv',['run_gui.py','--viewer-backend','software'])
    # Register restoration even when initially absent; launcher intentionally exports it.
    monkeypatch.setenv('ANYMARINE_REFERENCE_BARGE_DIR','')
    assert launcher.main()==7
    assert calls==[([str(interpreter),str(checkout/'run_gui.py'),'--viewer-backend','software'],str(assets))]
    def refused(*args):raise ValueError('invalid explicit config')
    monkeypatch.setattr(launcher,'local_settings',refused)
    assert launcher.main()==2 and len(calls)==1


def test_no_private_default_or_hidden_fallback(monkeypatch,tmp_path):
    monkeypatch.delenv('ANYMARINE_REFERENCE_BARGE_DIR',raising=False)
    monkeypatch.setattr(reference_barge,'_owner',lambda:pytest.fail('absence must refuse before optional imports'))
    with pytest.raises(reference_barge.ReferenceBargeError,match='LOCAL_DATA_UNAVAILABLE'):
        reference_barge.load_reference_barge()
    monkeypatch.setenv('ANYMARINE_REFERENCE_BARGE_DIR',str(tmp_path/'missing'))
    with pytest.raises(reference_barge.ReferenceBargeError,match='LOCAL_DATA_UNAVAILABLE'):
        reference_barge.load_reference_barge()


def test_checkout_excludes_private_assets_and_local_configuration(checkout):
    import tomllib
    config=tomllib.loads((checkout/'pyproject.toml').read_text())
    included=config['tool']['setuptools']['package-data']['anymarine']
    excluded=config['tool']['setuptools']['exclude-package-data']['anymarine']
    assert not any('reference-barge-recipe3' in item for item in included)
    assert 'examples/reference-barge-recipe3/*' in excluded
    assert not (checkout/'src/anymarine/examples/reference-barge-recipe3').exists()
    assert 'prune src/anymarine/examples/reference-barge-recipe3' in (checkout/'MANIFEST.in').read_text()
    assert 'exclude .anymarine-local.json' in (checkout/'MANIFEST.in').read_text()
    assert '.anymarine-local.json' in (checkout/'.gitignore').read_text().splitlines()
