"""External reference comparison artifact and builder (synthetic data only; no third-party results)."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from anymarine.reference_comparison import (ARTIFACT, DOFS, ReferenceComparisonError, artifact_hash, build,
                                            drift_series, has_mean_drift, load, series, write)

ROOT = Path(__file__).resolve().parents[1]


def synthetic(n_sources=2, periods=(4.0, 8.0, 12.0), headings=(0.0, 90.0)):
    # closed, outward-oriented box straddling the waterline (the wave preview requires a closed envelope)
    corners = [[x, y, z] for x in (-2.0, 2.0) for y in (-1.0, 1.0) for z in (-1.0, 0.5)]
    faces = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
             [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    body = dict(name='Synthetic box', mass_kg=1000.0, centre_of_mass_m=[0.0, 0.0, -0.5],
                display_mesh=dict(positions=corners, triangles=faces))
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
    assert len(data['body']['display_mesh']['triangles']) >= 4         # closed into a box by the display builder
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
    _, source, _, _, _ = tool.orcawave_parts(tmp_path / 'extract')
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


# ---- L6 motion helpers ----------------------------------------------------------------------------------------

def test_pose_is_the_real_part_of_the_rao_phasor():
    from anymarine.reference_comparison import motion_rao, pose
    data = synthetic()
    rao = motion_rao(data, source=1, heading=90.0, period=8.0)
    omega, t, a = 2 * math.pi / 8.0, 1.7, 1.3
    translation, rotation = pose(rao, amplitude=a, omega=omega, time=t)
    expected = [(x * a * complex(math.cos(omega * t), -math.sin(omega * t))).real for x in rao]
    assert translation + rotation == pytest.approx(expected, abs=1e-12)


def test_rigid_motion_is_a_proper_rotation_about_the_origin():
    from anymarine.reference_comparison import rigid_motion
    m = rigid_motion([1.0, 2.0, 3.0], [0.0, 0.0, math.pi / 2])
    assert np.allclose(m[:3, :3] @ [1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    assert np.allclose(m[:3, 3], [1.0, 2.0, 3.0]) and np.isclose(np.linalg.det(m[:3, :3]), 1.0)
    small = rigid_motion([0, 0, 0], [0.01, -0.02, 0.005])
    assert np.allclose(small[:3, :3], np.eye(3) + np.array([[0, -0.005, -0.02], [0.005, 0, -0.01], [0.02, 0.01, 0]]),
                       atol=5e-4)
    assert np.array_equal(rigid_motion([0, 0, 0], [0, 0, 0]), np.eye(4))


def test_environment_depth_is_optional_and_validated():
    from anymarine.reference_comparison import validate, water_depth
    data = synthetic()
    assert water_depth(data) is None
    data['environment'] = {'water_depth_m': 400.0}
    data['artifact_hash'] = artifact_hash(data)
    validate(data)
    assert water_depth(data) == 400.0
    data['environment'] = {'water_depth_m': -1.0}
    data['artifact_hash'] = artifact_hash(data)
    with pytest.raises(ReferenceComparisonError, match='depth'):
        validate(data)


def test_display_mesh_closes_a_half_hull_with_topsides_and_deck(tmp_path):
    from collections import Counter
    tool = _load_tool('reference_comparison')
    # half box (y >= 0), wet panels to z = 0 and a separate topside panel to z = 2 without shared nodes (as in Rhino)
    panels = [[[0, 0, -1], [4, 0, -1], [4, 1, -1], [0, 1, -1]],          # bottom
              [[0, 1, -1], [4, 1, -1], [4, 1, 0], [0, 1, 0]],            # side
              [[0, 0, -1], [0, 1, -1], [0, 1, 0], [0, 0, 0]],            # stern
              [[4, 0, -1], [4, 0, 0], [4, 1, 0], [4, 1, -1]],            # bow
              [[0, 1, 0.0001], [4, 1, 0.0001], [4, 1, 2], [0, 1, 2]]]    # topside (not shared)
    lines = ['x', '1 9.80665', '0 1', f'{len(panels)}'] + [' '.join(map(str, v)) for p in panels for v in p]
    (tmp_path / 'h.gdf').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    positions, triangles = tool.display_mesh(tmp_path / 'h.gdf')
    v, t = np.asarray(positions), np.asarray(triangles)
    undirected = Counter(tuple(sorted(e)) for a, b, c in t for e in ((a, b), (b, c), (c, a)))
    directed = Counter(e for a, b, c in t for e in ((a, b), (b, c), (c, a)))
    assert all(n == 2 for n in undirected.values()) and all(n == 1 for n in directed.values())
    volume = sum(np.dot(v[a], np.cross(v[b], v[c])) for a, b, c in t) / 6.0
    assert volume == pytest.approx(4 * 2 * (1 + 2))               # wet 8 m3 + topsides to the 2 m freeboard 16 m3


def test_non_simple_deck_outlines_are_refused():
    tool = _load_tool('reference_comparison')
    points = np.array([[0.0, 0.0], [2.0, 2.0], [0.0, 2.0], [2.0, 0.0]])      # bow-tie
    with pytest.raises(ValueError, match='simple'):
        tool._ear_clip([0, 1, 2, 3], points)
    square = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])
    assert len(tool._ear_clip([0, 1, 2, 3], square)) == 2
    collinear = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    with pytest.raises(ValueError):
        tool._ear_clip([0, 1, 2], collinear)


def test_extractor_mean_drift_block_is_si_and_omits_missing_methods():
    extractor = _load_tool('orcawave_extract')

    class Fake:
        meanDriftHeadingPairs = np.array([[0.0, 0.0], [0.0, 90.0]])
        meanDriftLoadControlSurface = np.ones((2, 3, 6), complex) * (2.0 + 0.5j)
        meanDriftLoadMomentumConservation = np.full((2, 3, 6), 1.5 + 0j)
        meanDriftLoadPressureIntegration = None          # not calculated by OrcaWave

    block = extractor._mean_drift(Fake())
    assert set(block['methods']) == {'control_surface', 'momentum_conservation'}
    assert block['methods']['control_surface'][1][2][5] == [2000.0, 500.0]
    assert block['methods']['momentum_conservation'][0][0][0] == [1500.0, 0.0]
    assert block['heading_pairs_deg'] == [[0.0, 0.0], [0.0, 90.0]]

    class Empty:
        meanDriftHeadingPairs = np.zeros((0, 2))
    assert extractor._mean_drift(Empty()) is None


# ---- MD4: mean drift in the reference comparison ----------------------------------------------------------

def _with_drift(data, values=None, **entry):
    out = {k: v for k, v in data.items() if k != 'artifact_hash'}
    out['sources'] = [dict(s) for s in out['sources']]
    n_h, n_p = len(out['headings_deg']), len(out['periods_s'])
    values = values or [[[1000.0 * (h + 1), -500.0, 2000.0 * p] for p in range(n_p)] for h in range(n_h)]
    out['sources'][0]['mean_drift'] = [dict(dict(label='far field', method='synthetic', values=values), **entry)]
    return build(**{k: out[k] for k in ('title', 'notes', 'body', 'periods_s', 'headings_deg', 'sources')})


def test_artifacts_without_drift_stay_valid_and_report_none():
    data = synthetic()
    assert not has_mean_drift(data)
    assert drift_series(data, heading=0.0) == {'surge': [], 'sway': [], 'yaw': []}


def test_drift_round_trips_and_gives_kilonewton_series(tmp_path):
    data = _with_drift(synthetic())
    write(data, tmp_path / 'd.json')
    back = load(tmp_path / 'd.json')
    assert has_mean_drift(back)
    out = drift_series(back, heading=90.0, x_axis='period')
    assert [s.label for s in out['surge']] == ['Program 0 - far field']
    assert out['surge'][0].xs == (4.0, 8.0, 12.0) and out['surge'][0].ys == (2.0, 2.0, 2.0)   # kN/m^2
    assert out['yaw'][0].ys == (0.0, 2.0, 4.0)


@pytest.mark.parametrize('values, message', [
    ([[[1.0, 2.0]] * 3] * 2, 'three finite numbers'),
    ([[[1.0, 2.0, 3.0]] * 2] * 2, 'one row per period'),
    ([[[1.0, 2.0, float('nan')]] * 3] * 2, None),
    ([[[1.0, 2.0, True]] * 3] * 2, 'three finite numbers'),
])
def test_malformed_drift_is_refused(values, message):
    with pytest.raises((ReferenceComparisonError, ValueError), match=message):
        _with_drift(synthetic(), values=values)


def test_drift_entries_need_label_and_method():
    with pytest.raises(ReferenceComparisonError, match='label, method and values'):
        _with_drift(synthetic(), extra=1)
    with pytest.raises(ReferenceComparisonError, match='method must be a string'):
        _with_drift(synthetic(), method=' ')


def test_builder_adds_orcawave_and_npz_drift(tmp_path):
    periods, headings = [5.0, 10.0], [0.0, 90.0]
    _write_extract(tmp_path / 'extract', periods, headings)
    ref_path = tmp_path / 'extract' / 'orcawave_reference.json'
    ref = json.loads(ref_path.read_text(encoding='utf-8'))
    pairs = [[0.0, 0.0], [0.0, 90.0], [90.0, 90.0]]
    # distinct value per pair, period and DOF; nonzero imaginary parts must be dropped (real parts only)
    mc = [[[[100.0 * (i + 1) + 10.0 * p + k, 0.5 * (k + 1)] for k in range(6)] for p in range(2)] for i in range(3)]
    ref['mean_drift'] = dict(heading_pairs_deg=pairs, methods=dict(momentum_conservation=mc),
                             units='N/m^2', layout='[pair][period][dof]')
    ref_path.write_text(json.dumps(ref), encoding='utf-8')
    drift = np.arange(12, dtype=float).reshape(2, 2, 3)
    np.savez(tmp_path / 'ours.npz', periods_s=np.array(periods), headings_deg=np.array(headings),
             raos=np.zeros((2, 2, 6), complex), meta=json.dumps(dict(label='Ours', program='P', version='1', method='m')),
             mean_drift=drift, mean_drift_meta=json.dumps(dict(label='far field', method='Maruo/Newman')))
    out = tmp_path / 'ref.json'
    subprocess.run([sys.executable, str(ROOT / 'tools' / 'reference_comparison.py'), '--orcawave',
                    str(tmp_path / 'extract'), '--source', str(tmp_path / 'ours.npz'), '--title', 'T',
                    '--out', str(out)], check=True, cwd=ROOT)
    data = load(out)
    orcawave, ours = data['sources']
    (entry,) = orcawave['mean_drift']
    assert entry['label'] == 'momentum conservation'
    # equal pairs only: heading 0 -> pair 0, heading 90 -> pair 2; DOFs surge, sway, yaw
    assert entry['values'][0][1] == [110.0, 111.0, 115.0] and entry['values'][1][0] == [300.0, 301.0, 305.0]
    assert ours['mean_drift'][0]['values'] == drift.tolist()


@pytest.mark.parametrize('bad', [np.ones((2, 2, 3), dtype=bool), np.full((2, 2, 3), 1000 + 9000j)])
def test_builder_refuses_boolean_or_complex_npz_drift(tmp_path, bad):
    periods, headings = [5.0, 10.0], [0.0, 90.0]
    _write_extract(tmp_path / 'extract', periods, headings)
    np.savez(tmp_path / 'ours.npz', periods_s=np.array(periods), headings_deg=np.array(headings),
             raos=np.zeros((2, 2, 6), complex), meta=json.dumps(dict(label='Ours', program='P', version='1', method='m')),
             mean_drift=bad, mean_drift_meta=json.dumps(dict(label='far field', method='m')))
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'reference_comparison.py'), '--orcawave',
                             str(tmp_path / 'extract'), '--source', str(tmp_path / 'ours.npz'), '--title', 'T',
                             '--out', str(tmp_path / 'ref.json')], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode != 0 and 'real numbers' in (result.stderr + result.stdout)
    assert not (tmp_path / 'ref.json').exists()


def test_builder_refuses_drift_without_unidirectional_pairs(tmp_path):
    _write_extract(tmp_path / 'extract', [5.0], [0.0, 90.0])
    ref_path = tmp_path / 'extract' / 'orcawave_reference.json'
    ref = json.loads(ref_path.read_text(encoding='utf-8'))
    ref['mean_drift'] = dict(heading_pairs_deg=[[0.0, 0.0]], methods=dict(momentum_conservation=[[[[1.0, 0.0]] * 6]]))
    ref_path.write_text(json.dumps(ref), encoding='utf-8')
    result = subprocess.run([sys.executable, str(ROOT / 'tools' / 'reference_comparison.py'), '--orcawave',
                             str(tmp_path / 'extract'), '--title', 'T', '--out', str(tmp_path / 'ref.json')],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode != 0 and 'unidirectional' in (result.stderr + result.stdout)
