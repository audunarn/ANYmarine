"""RAO slice 6b: CLI registration and FD table (campaign covered in test_rao_report)."""
import csv
import json

import pytest

from conftest import requires_solver
from anymarine.cli import main
from anymarine.services import ApplicationService
from test_rao_report import oscillator_with_cylinder, spec


@requires_solver
def test_cli_prepare_and_table(tmp_path, capsys):
    root = tmp_path / 'project'
    ApplicationService(root).save(oscillator_with_cylinder())
    (tmp_path / 'spec.json').write_text(json.dumps(spec()), encoding='utf-8')
    case = tmp_path / 'case.json'
    assert main(['rao-prepare', '--project', str(root), '--spec', str(tmp_path / 'spec.json'),
                 '--out', str(case)]) == 0
    assert 'category=kernel_consistency_only' in capsys.readouterr().out
    assert main(['rao-prepare', '--project', str(root), '--spec', str(tmp_path / 'spec.json'),
                 '--out', str(case)]) == 1                       # never overwritten
    out = tmp_path / 'table'
    assert main(['rao-table', '--project', str(root), '--case', str(case), '--out-dir', str(out)]) == 0
    table = json.loads((out / 'rao_table.json').read_text(encoding='utf-8'))
    assert table['case_hash'] == json.loads(case.read_text())['case_hash']
    rows = list(csv.reader((out / 'rao_table.csv').open(encoding='utf-8')))
    assert len(rows) == 13
    assert main(['rao-table', '--project', str(root), '--case', str(case), '--out-dir', str(out)]) == 1
    tampered = json.loads(case.read_text())
    tampered['case']['criteria']['rao_phase_deg'] = 2.0
    (tmp_path / 'tampered.json').write_text(json.dumps(tampered), encoding='utf-8')
    assert main(['rao-table', '--project', str(root), '--case', str(tmp_path / 'tampered.json'),
                 '--out-dir', str(tmp_path / 't2')]) == 1
