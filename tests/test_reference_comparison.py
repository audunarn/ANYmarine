"""External reference comparison artifact and builder (synthetic data only; no third-party results)."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from anymarine.reference_comparison import (ARTIFACT, DOFS, ReferenceComparisonError, artifact_hash, build, load,
                                            series, write)

ROOT = Path(__file__).resolve().parents[1]


def synthetic(n_sources=2, periods=(4.0, 8.0, 12.0), headings=(0.0, 90.0)):
    body = dict(name='Synthetic box', mass_kg=1000.0, centre_of_mass_m=[0.0, 0.0, -0.5],
                display_mesh=dict(positions=[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                                  triangles=[[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]]))
    sources = []
    for s in range(n_sources):
        raos = [[[[0.1 * (k + 1) * (1 + s * 0.01), 0.01 * p] for k in range(6)] for p in range(len(periods))]
                for _ in headings]
        sources.append(dict(label=f'Program {s}', program=f'Program {s}', version='1', method='synthetic',
                            details={'case': 'test'}, raos=raos))
    return build(title='Synthetic comparison', notes=['test only'], body=body, periods_s=list(periods),
                 headings_deg=list(headings), sources=sources)


def test_round_trip_and_hash(tmp_path):
    data = synthetic()
    assert data['artifact'] == ARTIFACT and data['artifact_hash'] == artifact_hash(data)
    path = write(data, tmp_path / 'ref.json')
    assert load(path) == data


@pytest.mark.parametrize('mutate, message', [
    (lambda d: d['sources'][0]['raos'][0][0][0].__setitem__(0, 9.0), 'hash'),
    (lambda d: d.pop('body'), 'required fields'),
    (lambda d: d.__setitem__('artifact', 'other'), 'not an'),
])
def test_tampered_or_malformed_artifacts_are_refused(tmp_path, mutate, message):
    data = synthetic()
    mutate(data)
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ReferenceComparisonError, match=message):
        load(path)


@pytest.mark.parametrize('field, value, message', [
    ('periods_s', [4.0, -8.0, 12.0], 'periods'),
    ('headings_deg', [0.0, 0.0], 'distinct'),
    ('dofs', ['surge'], 'dofs'),
])
def test_invalid_grids_are_refused_even_with_a_matching_hash(field, value, message):
    data = synthetic()
    data[field] = value
    data['artifact_hash'] = artifact_hash(data)
    with pytest.raises(ReferenceComparisonError, match=message):
        from anymarine.reference_comparison import validate
        validate(data)


def test_non_finite_and_shape_errors_are_refused(tmp_path):
    data = synthetic()
    data['sources'][1]['raos'][1].pop()            # one period missing
    data['artifact_hash'] = artifact_hash(data)
    with pytest.raises(ReferenceComparisonError, match='one row per period'):
        from anymarine.reference_comparison import validate
        validate(data)
    bad = synthetic()
    bad['sources'][0]['raos'][0][0][0][0] = float('nan')
    (tmp_path / 'nan.json').write_text(json.dumps(bad), encoding='utf-8')     # Python writes NaN
    assert 'NaN' in (tmp_path / 'nan.json').read_text(encoding='utf-8')
    with pytest.raises(ReferenceComparisonError, match='non-finite'):
        load(tmp_path / 'nan.json')


def test_series_one_line_per_source_in_degrees_for_rotations():
    data = synthetic()
    out = series(data, heading=90.0, display='amplitude', x_axis='period')
    assert set(out) == set(DOFS)
    assert [s.label for s in out['roll']] == ['Program 0', 'Program 1']
    roll = out['roll'][0]
    assert roll.xs == (4.0, 8.0, 12.0)
    assert roll.ys[0] == pytest.approx(math.degrees(abs(complex(0.4, 0.0))))
    freq = series(data, heading=0.0, display='phase', x_axis='frequency')
    assert list(freq['surge'][0].xs) == sorted(freq['surge'][0].xs)


def _write_extract(directory: Path, periods, headings):
    """A synthetic tools/orcawave_extract.py output with a half-hull GDF (ISY = 1)."""
    directory.mkdir(parents=True)
    gdf = ['synthetic', '1 9.80665 ULEN GRAV', '0 1 ISX ISY', '1 NPAN',
           '0 0 0', '1 0 0', '1 1 0', '0 1 0']
    (directory / 'body_mesh_half_xz.gdf').write_text('\n'.join(gdf) + '\n', encoding='utf-8')
    raos = [[[[1.0, 0.5]] * 6 for _ in periods] for _ in headings]
    ref = dict(body=dict(name='Body1', mass_kg=500.0, centre_of_mass_m=[0, 0, 0], mesh_exported='body_mesh_half_xz.gdf',
                         interior_surface_panels='Yes'),
               source=dict(sha256='ab' * 32, orcawave_dll='11.6c'),
               settings=dict(preferred_load_rao_method='Haskind'),
               periods_s=list(periods), headings_deg=list(headings), displacement_raos=raos)
    (directory / 'orcawave_reference.json').write_text(json.dumps(ref), encoding='utf-8')


def test_builder_tool_mirrors_mesh_and_conjugates_orcawave(tmp_path):
    periods, headings = [5.0, 10.0], [0.0, 180.0]
    _write_extract(tmp_path / 'extract', periods, headings)
    other = (np.ones((2, 2, 6)) * (0.9 - 0.4j)).astype(complex)
    np.savez(tmp_path / 'other.npz', periods_s=np.array(periods), headings_deg=np.array(headings), raos=other,
             meta=json.dumps(dict(label='Other', program='Other', version='2', method='m')))
    out = tmp_path / 'ref.json'
    subprocess.run([sys.executable, str(ROOT / 'tools' / 'reference_comparison.py'), '--orcawave',
                    str(tmp_path / 'extract'), '--source', str(tmp_path / 'other.npz'), '--title', 'T',
                    '--body-name', 'Vessel', '--out', str(out)], check=True, cwd=ROOT)
    data = load(out)
    assert data['body']['name'] == 'Vessel'
    positions = np.array(data['body']['display_mesh']['positions'])
    assert positions[:, 1].min() == -1.0 and positions[:, 1].max() == 1.0      # mirrored across y = 0
    assert len(data['body']['display_mesh']['triangles']) == 4
    orcawave, other_source = data['sources']
    assert orcawave['label'] == 'OrcaWave' and orcawave['raos'][0][0][0] == [1.0, -0.5]   # conjugated
    assert other_source['raos'][1][1][5] == [0.9, -0.4]


def test_builder_refuses_mismatched_grids(tmp_path):
    _write_extract(tmp_path / 'extract', [5.0, 10.0], [0.0])
    np.savez(tmp_path / 'other.npz', periods_s=np.array([5.0, 11.0]), headings_deg=np.array([0.0]),
             raos=np.zeros((1, 2, 6), complex), meta=json.dumps(dict(label='O', program='O', version='1', method='m')))
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'reference_comparison.py'), '--orcawave',
                             str(tmp_path / 'extract'), '--source', str(tmp_path / 'other.npz'), '--title', 'T',
                             '--out', str(tmp_path / 'ref.json')], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode != 0 and 'differ' in (result.stderr + result.stdout)
    assert not (tmp_path / 'ref.json').exists()


# ---- L4 review round 1 regressions -------------------------------------------------------------------------

def _load_tool(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f'tool_{name}', ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overflowing_numbers_are_refused_not_crashed(tmp_path):
    raw = json.dumps(synthetic())
    for bad in (raw.replace('1000.0', '1e999', 1), raw.replace('1000.0', '1' + '0' * 400, 1)):
        (tmp_path / 'x.json').write_text(bad, encoding='utf-8')
        with pytest.raises(ReferenceComparisonError):
            load(tmp_path / 'x.json')


@pytest.mark.parametrize('cog', [None, ['bad', False, 0.0], [0.0, 0.0], [0.0, True, 1.0]])
def test_centre_of_mass_must_be_three_finite_numbers(cog):
    from anymarine.reference_comparison import validate
    data = synthetic()
    if cog is None:
        data['body'].pop('centre_of_mass_m')
    else:
        data['body']['centre_of_mass_m'] = cog
    data['artifact_hash'] = artifact_hash(data)
    with pytest.raises(ReferenceComparisonError, match='centre of mass'):
        validate(data)


def test_gdf_vertices_are_not_scaled_by_ulen(tmp_path):
    tool = _load_tool('reference_comparison')
    gdf = ['x', '10 9.80665 ULEN GRAV', '0 0 ISX ISY', '1 NPAN', '0 0 0', '2 0 0', '2 3 0', '0 3 0']
    (tmp_path / 'm.gdf').write_text('\n'.join(gdf) + '\n', encoding='utf-8')
    positions, triangles = tool.read_gdf(tmp_path / 'm.gdf')
    assert max(p[0] for p in positions) == 2.0 and max(p[1] for p in positions) == 3.0 and len(triangles) == 2


def test_source_grids_must_match_exactly(tmp_path):
    periods, headings = [5.0, 10.0], [0.0, 1e-9]
    _write_extract(tmp_path / 'extract', periods, headings)
    meta = json.dumps(dict(label='O', program='O', version='1', method='m'))
    np.savez(tmp_path / 'near.npz', periods_s=np.array(periods), headings_deg=np.array([0.0, 0.0]),
             raos=np.zeros((2, 2, 6), complex), meta=meta)
    np.savez(tmp_path / 'short.npz', periods_s=np.array(periods), headings_deg=np.array([0.0]),
             raos=np.zeros((2, 2, 6), complex), meta=meta)
    for name in ('near', 'short'):
        result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'reference_comparison.py'), '--orcawave',
                                 str(tmp_path / 'extract'), '--source', str(tmp_path / f'{name}.npz'), '--title', 'T',
                                 '--out', str(tmp_path / f'{name}.json')], capture_output=True, text=True, cwd=ROOT)
        assert result.returncode != 0 and 'exact match' in result.stderr + result.stdout


def test_single_load_method_provenance(tmp_path):
    tool = _load_tool('reference_comparison')
    _write_extract(tmp_path / 'extract', [5.0], [0.0])
    ref = json.loads((tmp_path / 'extract' / 'orcawave_reference.json').read_text())
    ref['settings'] = dict(load_rao_method='Diffraction', preferred_load_rao_method=None)
    (tmp_path / 'extract' / 'orcawave_reference.json').write_text(json.dumps(ref))
    _, source, _, _ = tool.orcawave_parts(tmp_path / 'extract')
    assert source['method'].endswith('from Diffraction loads') and 'None' not in source['method']


def test_extractor_writes_a_full_gdf_for_unsymmetric_bodies(tmp_path):
    extractor = _load_tool('orcawave_extract')
    assert extractor.MESH_SYMMETRY['None'][0] is None
    dtype = [('vertices', '<f8', (4, 3))]
    nan = float('nan')
    panels = np.array([([[0, 0, -1], [1, 0, -1], [1, 1, -1], [0, 1, -1]],),
                       ([[0, 0, -1], [1, 0, -1], [0, 0, 0], [nan, nan, nan]],)], dtype=dtype)
    extractor.write_gdf(tmp_path / 'full.gdf', panels, 9.80665)
    lines = (tmp_path / 'full.gdf').read_text().splitlines()
    assert lines[2].split()[:2] == ['0', '0'] and lines[3].split()[0] == '2'
    positions, triangles = _load_tool('reference_comparison').read_gdf(tmp_path / 'full.gdf')
    assert len(positions) == 5 and len(triangles) == 3        # quad -> 2, padded triangle -> 1
