"""Pure loading/source admission tests; geometry execution is never needed."""
import hashlib
import json
import sys

import pytest

from anymarine.l01_stability import L01StabilityError, load_l01_stability
from anymarine.reference_comparison import build, write


@pytest.fixture
def source(tmp_path):
    artifact = build(title='Synthetic host fixture for L01 source admission', notes=['Synthetic'],
        body=dict(name='L01 default vessel', mass_kg=9017950., centre_of_mass_m=[2.53, 0., -1.974],
            display_mesh=dict(positions=[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], triangles=[[0, 1, 2]])),
        periods_s=[5.], headings_deg=[0.], sources=[dict(label='Synthetic', program='test', version='1',
            method='synthetic', details={}, raos=[[[[0., 0.] for _ in range(6)]]])])
    path = tmp_path / 'reference_comparison.json'
    write(artifact, path)
    return path


def test_recipe_is_pure_immutable_and_mass_balanced(source, monkeypatch):
    # Fail immediately if loading tries to import either owner.
    monkeypatch.setitem(sys.modules, 'anystability', None)
    monkeypatch.setitem(sys.modules, 'anygeometry', None)
    recipe = load_l01_stability(source)
    data = recipe.to_dict()
    assert data['reference']['file_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert data['numerical_status'] == 'not_executed'
    assert len(data['tanks']) == 4
    dry = data['base_condition']['items'][0]
    liquids = [(t['target_m3'] * t['density_kg_m3'], t['upright_cg_body_m']) for t in data['tanks']]
    assert dry['mass_kg'] + sum(m for m, _ in liquids) == data['total_upright_mass_kg']
    for axis in range(3):
        moment = dry['mass_kg'] * dry['cg_body_m'][axis] + sum(m * cg[axis] for m, cg in liquids)
        assert moment / data['total_upright_mass_kg'] == pytest.approx(data['total_upright_cg_body_m'][axis])
    for tank in data['tanks']:
        size = [high-low for low, high in tank['bounds_body_m']]
        assert size[0] * size[1] * size[2] == tank['capacity_m3'] == 160.
        assert tank['target_m3'] == 80.
    data['tanks'][0]['target_m3'] = 0
    assert recipe.to_dict()['tanks'][0]['target_m3'] == 80.
    assert 'display_mesh' not in recipe._bytes.decode()
    assert load_l01_stability(source).identity == recipe.identity


def test_missing_source_is_actionable(tmp_path):
    with pytest.raises(L01StabilityError, match='LOCAL_DATA_UNAVAILABLE.*licensed local L01'):
        load_l01_stability(tmp_path / 'missing.json')


def test_corrupt_source_refused(source):
    data = json.loads(source.read_text());data['body']['mass_kg'] += 1
    source.write_text(json.dumps(data))
    with pytest.raises(L01StabilityError, match='SOURCE_INVALID.*hash'):
        load_l01_stability(source)


def test_other_vessel_refused(source):
    from anymarine.reference_comparison import artifact_hash
    data = json.loads(source.read_text());data['body']['name'] = 'Another vessel'
    data['artifact_hash'] = artifact_hash(data);source.write_text(json.dumps(data))
    with pytest.raises(L01StabilityError, match='SOURCE_IDENTITY_MISMATCH'):
        load_l01_stability(source)


def test_source_and_provider_fail_closed(source, monkeypatch):
    recipe = load_l01_stability(source)
    with pytest.raises(L01StabilityError, match='GEOMETRY_SOURCE_UNAVAILABLE'):
        recipe.engineering_study()
    with pytest.raises(L01StabilityError, match='SOURCE_IDENTITY_MISMATCH'):
        recipe.engineering_study({'manifest': {}})
    with pytest.raises(L01StabilityError, match='INPUT_INVALID'):
        recipe.engineering_study('a-directory')
    import anymarine.engineering_stability as bridge
    monkeypatch.setattr(bridge, 'default_owner_wheel', lambda: '')
    with pytest.raises(L01StabilityError, match='PROVIDER_UNAVAILABLE'):
        recipe.engineering_study({'manifest': {'l01_reference_sha256': recipe.to_dict()['reference']['file_sha256']}})


def test_environment_source_selection(source, monkeypatch):
    monkeypatch.setenv('ANYMARINE_L01_REFERENCE_COMPARISON', str(source))
    assert load_l01_stability().identity == load_l01_stability(source).identity


def test_public_owner_study_creation_and_roundtrip_without_geometry(source, tmp_path, monkeypatch):
    owner = pytest.importorskip('anystability.services')
    monkeypatch.setitem(sys.modules, 'anygeometry', None)
    recipe = load_l01_stability(source)
    data = recipe.to_dict()
    # Structural neutral transport fixture only; never admissible physical evidence.
    entry = {'faces_body_m': [[[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]]}
    names = ['HULL', 'WB1', 'WB2', 'WB3', 'WB4']
    neutral = {'manifest': {'l01_reference_sha256': data['reference']['file_sha256'], 'files': {},
        'capacity_comparisons': {name: {'volume_m3': 160., 'centroid_body_m': [0., 0., 0.]} for name in names}},
        'files': {'definition.json': {'hull': entry, 'spaces': {name: entry for name in names[1:]}},
                  **{name+'.geometry.json': {'synthetic_transport_fixture': True} for name in names}}}
    wheel = tmp_path / 'test-owner.whl';wheel.write_bytes(b'not a real wheel; not executed')
    study = recipe.engineering_study(neutral, owner_wheel=wheel)
    actual = study.to_dict()
    assert actual['targets_m3'] == dict.fromkeys(names[1:], 80.)
    assert actual['base_condition'] == data['base_condition']
    assert actual['allow_development'] is True
    saved = tmp_path / 'saved-study'
    owner.save_stability_study(study, saved)
    reopened, result = owner.load_stability_study(saved)
    assert reopened.identity == study.identity and result is None


def test_qt_preview_never_starts_provider(source, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.l01_stability import L01StabilityDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setitem(sys.modules, 'anygeometry', None)
    dialog = L01StabilityDialog(load_l01_stability(source))
    dialog.show();app.processEvents()
    assert dialog.table.rowCount() == 4
    assert dialog.engineering is None
    assert 'No geometry' in dialog.status.text()
    assert dialog.close()


def test_analytical_box_draft_and_gm_against_closed_form(source, monkeypatch, tmp_path):
    pytest.importorskip('anystability.services')
    monkeypatch.setitem(sys.modules, 'anygeometry', None)
    from anymarine.stability_service import calculate, parse_inputs, save_study, open_study
    from anymarine.stability_model import derive, parse
    inputs = load_l01_stability(source).analytical_box_inputs()
    validated = parse_inputs(json.dumps(inputs))
    assert validated['vessel']['geometry']['provider_id'] == 'reference_box_v1'
    assert 'not actual L01' in validated['vessel']['name']
    model = parse(validated)
    assert len(model.tanks) == 4
    assert all(t.fill == 1. for t in model.tanks)
    with pytest.raises(Exception):
        calculate(validated, allow_reference=False)
    record = calculate(validated, allow_reference=True)
    result = record.to_dict()
    expected_draft = 9017950. / (1025. * 100. * 18.)
    assert result['output']['draft_m'] == pytest.approx(expected_draft, abs=1e-6)
    derived = derive(model, result)
    expected_gm = expected_draft/2. + 18.**2/(12.*expected_draft) - (-1.974+6.66)
    assert derived.gm_solid_m == pytest.approx(expected_gm, abs=1e-6)
    assert derived.fsc_m == 0.
    assert result['output']['rotational_equilibrium'] == 'not_solved'
    assert result['output']['state']['mass']['mass_kg'] == 9017950.
    saved = tmp_path / 'analytical-box'
    save_study(validated, saved, result=record)
    reopened, reopened_result = open_study(saved)
    assert reopened == validated
    assert reopened_result.payload_json == record.payload_json


@pytest.mark.parametrize('use_environment', [True, False])
def test_primary_gui_callback_loads_and_runs_box_only(source, monkeypatch, use_environment):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    if use_environment:
        monkeypatch.setenv('ANYMARINE_L01_REFERENCE_COMPARISON', str(source))
    else:
        monkeypatch.delenv('ANYMARINE_L01_REFERENCE_COMPARISON', raising=False)
    pytest.importorskip('PySide6');pytest.importorskip('anystability.services')
    monkeypatch.setitem(sys.modules, 'anygeometry', None)
    from PySide6.QtWidgets import QApplication, QWidget
    from anymarine.qtapp.l01_stability import open_l01_stability
    from anymarine.services import ApplicationService
    app = QApplication.instance() or QApplication([])
    window = QWidget();window._service = ApplicationService()
    window._l01_reference_candidates = lambda: [source]
    dialog = open_l01_stability(window);app.processEvents()
    assert dialog.result is None and not dialog.opt_in.isChecked()
    assert 'no calculation executed' in dialog.status.text()
    assert 'ANALYTICAL BOX' in dialog.windowTitle()
    assert not dialog.tank_table.isEnabled()
    dialog.opt_in.setChecked(True);dialog.calculate()
    assert dialog.result.to_dict()['output']['status'] == 'engineering_converged'
    dialog.close();window.close()
