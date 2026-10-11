"""Focused Qt surface-assignment editor tests.

Stored/fake analytic responses only: no solver job, BEM run or numerical
qualification evidence. Production widgets and shell handlers are exercised
directly; refusals must leave the current project untouched (same hash).
"""
import ast
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from anymarine.domain import BodyMesh, default_two_body_project
from anymarine.hashing import input_hash
from anymarine.plate_example import attached_plate_project
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.qtapp.surface_details import SurfaceAssignmentDialog
from anymarine.services import ApplicationService
from anymarine.surface_assignments import migrate_legacy_surface
from test_application_surfaces import admitted, fixture_result

PAGE_SURFACE = 5


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    w = MainWindow()
    monkeypatch.setattr(w, '_confirm_discard', lambda: True)
    yield w
    w._dirty = False
    w.close()
    app.processEvents()


def _sheet(body):
    return body.hydro_surfaces[-1]


def _hull(body):
    return body.hydro_surfaces[0]


def _surface_node(window, body_index=0, surface_index=1):
    owner = window._tree_model.index(body_index, 0, window._tree_model.index(0, 0))
    return window._tree_model.index(surface_index, 0, owner)


def _warnings(window, monkeypatch):
    collected = []
    monkeypatch.setattr(window, '_notify_warning', lambda *args: collected.append(args))
    return collected


# --------------------------------------------------------------- selection

def test_surface_selection_shows_surface_details(window, app):
    window.load_plate_example()
    body = window._project.bodies[0]
    sheet = _sheet(body)
    window._on_tree_clicked(_surface_node(window))
    assert window._details._stack.currentIndex() == PAGE_SURFACE
    form = window._details._surface_form
    assert form._identity.text().startswith(sheet.semantics.surface_id)
    assert 'thin_sheet' in form._identity.text()
    assert form._normal.text() == 'minus_to_plus'
    assert form._minus_side.text() == 'lower-water'
    assert form._plus_side.text() == 'upper-water'
    assert 'No bound ANYhydro dataset' in form._dataset.text()
    assert window.viewer.highlighted_tags() == frozenset([body.body_id])


def test_dataset_status_shows_actual_bound_labels(window, admitted):
    p, _, _ = admitted
    window._set_project(p)
    body = p.bodies[0]
    window._details.show_surface(body.body_id, _sheet(body).semantics.surface_id)
    text = window._details._surface_form._dataset.text()
    assert 'development_fixture' in text
    assert 'not scientific sheet qualification' in text
    assert 'pressure-jump' in text.casefold()


# --------------------------------------------------------------- edit/apply

def test_surface_edit_applies_and_invalidates_results(window, admitted, monkeypatch):
    p, _, _ = admitted
    r = fixture_result(p, monkeypatch)
    window._set_project(p.with_edits(results=(r,)))
    window._set_result(r)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    before = input_hash(window._project)
    window._details.show_surface(body.body_id, sid)
    form = window._details._surface_form
    form._correction.setCurrentText('plate_drag')
    form._plate_area.setValue(12.5)
    form._plate_cd.setValue(2.3)
    form._minus_side.setText('sea-side')
    form._apply.click()
    assert window._dirty
    edited = _sheet(window._project.body(body.body_id)).semantics
    assert edited.minus_side == 'sea-side'
    assert edited.viscous_correction == 'plate_drag'
    assert edited.plate_drag.reference_area_m2 == 12.5
    assert edited.plate_drag.coefficient == 2.3
    assert edited.plate_drag.coefficient_convention == 'F=0.5*rho*Cd*area*abs(vn)*vn*n'
    assert input_hash(window._project) != before
    assert window._service.result_stale(window._project, r)
    assert 'STALE' in window._result_status
    # the form re-shows the applied record, not the pre-edit values
    assert window._details._surface_form._minus_side.text() == 'sea-side'


@pytest.mark.parametrize('target,changes,pattern', [
    ('sheet', {'primary_hydrodynamics': 'morison'}, 'Morison'),
    ('sheet', {'minus_side': 'upper-water'}, 'distinct fluid sides'),
    ('sheet', {'viscous_correction': 'member_drag'}, 'circular member drag'),
    ('hull', {'minus_side': 'a', 'plus_side': 'b'}, 'two-sided pressure labels'),
    ('hull', {'viscous_correction': 'plate_drag', 'plate_drag': (1.0, 1.0)},
     'plate_drag requires a thin_sheet'),
    ('lid', {'primary_hydrodynamics': 'potential_flow'}, 'numerical lid is not a physical plate'),
])
def test_invalid_surface_edits_refused_same_hash(window, target, changes, pattern, monkeypatch):
    p = attached_plate_project()
    body = p.bodies[0]
    if target == 'lid':
        lid_mesh = BodyMesh(((-1., -1., 0.), (1., -1., 0.), (1., 1., 0.)), ((0, 1, 2),))
        body = migrate_legacy_surface(body.with_edits(hydro_lid=lid_mesh), role='lid',
                                      legacy_role='numerical_lid',
                                      surface_id=body.body_id + '/lid',
                                      source_geometry_id=body.body_id + '/lid-geometry')
    window._set_project(p.with_edits(bodies=(body, p.bodies[1])))
    current = window._project
    assignment = {'sheet': _sheet, 'hull': _hull,
                  'lid': lambda b: b.hydro_surfaces[-1]}[target](body)
    sid = assignment.semantics.surface_id
    before = input_hash(window._project)
    warned = _warnings(window, monkeypatch)
    window._apply_surface_edit(body.body_id, sid, changes)
    assert warned and pattern in warned[0][1]
    assert window._project is current
    assert input_hash(window._project) == before


@pytest.mark.parametrize('plate', [(0.0, 1.0), (1.0, 0.0), (-2.0, 1.0)])
def test_invalid_plate_numbers_warn_without_mutation(window, monkeypatch, plate):
    p = attached_plate_project()
    window._set_project(p)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    before = input_hash(window._project)
    warned = _warnings(window, monkeypatch)
    window._apply_surface_edit(body.body_id, sid,
        {'viscous_correction': 'plate_drag', 'plate_drag': plate})
    assert warned and 'positive' in warned[0][1]
    assert input_hash(window._project) == before


def test_unparseable_plate_text_is_refused_by_constructors(window, monkeypatch):
    p = attached_plate_project()
    window._set_project(p)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    before = input_hash(window._project)
    warned = _warnings(window, monkeypatch)
    window._details.show_surface(body.body_id, sid)
    form = window._details._surface_form
    form._correction.setCurrentText('plate_drag')
    form._plate_area.setText('not-a-number')
    form._apply.click()
    assert warned and 'positive' in warned[-1][1]
    assert input_hash(window._project) == before


def _project_with_tiny_plate(p):
    from dataclasses import replace
    from anymarine.surface_assignments import neutral_api
    body = p.bodies[0]
    assignment = body.hydro_surfaces[-1]
    plate = neutral_api().PlateDragCorrection(reference_area_m2=0.000005,
                                              coefficient=0.0000002)
    changed = replace(assignment, semantics=replace(assignment.semantics,
                      viscous_correction='plate_drag', plate_drag=plate))
    body = body.with_edits(hydro_surfaces=body.hydro_surfaces[:-1] + (changed,))
    return p.with_edits(bodies=(body,) + p.bodies[1:])


def test_unmodified_small_plate_values_survive_show_and_apply(window):
    p = _project_with_tiny_plate(attached_plate_project())
    window._set_project(p)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    before = input_hash(window._project)
    window._details.show_surface(body.body_id, sid)
    form = window._details._surface_form
    assert form._plate_area.text() == '5e-06'
    assert form._plate_cd.text() == '2e-07'
    form._apply.click()
    assert input_hash(window._project) == before
    plate = _sheet(window._project.body(body.body_id)).semantics.plate_drag
    assert plate.reference_area_m2 == 0.000005
    assert plate.coefficient == 0.0000002


def test_unrelated_edit_preserves_small_plate_values(window):
    p = _project_with_tiny_plate(attached_plate_project())
    window._set_project(p)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    window._details.show_surface(body.body_id, sid)
    form = window._details._surface_form
    form._source_geometry_id.setText('unrelated-edit-source')
    form._apply.click()
    semantics = _sheet(window._project.body(body.body_id)).semantics
    assert semantics.source_geometry_id == 'unrelated-edit-source'
    assert semantics.plate_drag.reference_area_m2 == 0.000005
    assert semantics.plate_drag.coefficient == 0.0000002


def test_valid_small_plate_values_are_editable(window):
    p = attached_plate_project()
    window._set_project(p)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    window._details.show_surface(body.body_id, sid)
    form = window._details._surface_form
    form._correction.setCurrentText('plate_drag')
    form._plate_area.setText('0.000005')
    form._plate_cd.setText('0.0000002')
    form._apply.click()
    plate = _sheet(window._project.body(body.body_id)).semantics.plate_drag
    assert plate.reference_area_m2 == 0.000005
    assert plate.coefficient == 0.0000002


def test_surface_remove_is_explicit(window):
    p = attached_plate_project()
    window._set_project(p)
    body = p.bodies[0]
    sid = _sheet(body).semantics.surface_id
    before = input_hash(window._project)
    window._details.show_surface(body.body_id, sid)
    window._details._surface_form._remove.click()
    remaining = window._project.body(body.body_id).hydro_surfaces
    assert len(remaining) == 1 and remaining[0].role == 'hull'
    assert input_hash(window._project) != before
    assert window._dirty


# --------------------------------------------------------------- creation

def _sheet_spec(body_id, mesh_path=None, surface_id=None):
    return dict(role='attached',
                surface_id=surface_id or f'{body_id}/keel-sheet',
                source_geometry_id=f'{body_id}/keel-sheet-geometry',
                primary_hydrodynamics='potential_flow',
                viscous_correction='none',
                minus_side='lower-water', plus_side='upper-water',
                plate_drag=None, mesh=None)


def test_create_attached_sheet_from_explicit_mesh_json(window, tmp_path):
    p = default_two_body_project('surface editor creation')
    window._set_project(p)
    body = window._project.bodies[0]
    mesh = BodyMesh(((0., 0., -5.), (4., 0., -5.), (4., 4., -5.), (0., 4., -5.)),
                    ((0, 1, 2), (0, 2, 3)))
    path = tmp_path / 'sheet.json'
    path.write_text(json.dumps(mesh.to_dict()), encoding='utf-8')
    dialog = SurfaceAssignmentDialog(body)
    dialog._role.setCurrentIndex(2)
    dialog._minus_side.setText('lower-water')
    dialog._plus_side.setText('upper-water')
    dialog._mesh_path.setText(str(path))
    spec = dialog.spec()
    assert spec['role'] == 'attached' and spec['mesh'] is not None
    before = input_hash(window._project)
    window._apply_surface_create(body.body_id, spec)
    created = window._project.body(body.body_id).hydro_surfaces[-1]
    assert created.role == 'attached'
    assert created.semantics.surface_kind == 'thin_sheet'
    assert created.semantics.normal_convention == 'minus_to_plus'
    assert len(created.mesh.triangles) == 2
    assert input_hash(window._project) != before
    assert window._dirty
    assert window._details._stack.currentIndex() == PAGE_SURFACE
    assert 'thin_sheet' in window._details._surface_form._identity.text()


def test_create_hull_and_lid_keep_physical_distinction(window, monkeypatch):
    p = default_two_body_project('surface editor creation')
    window._set_project(p)
    body = window._project.bodies[0]
    warned = _warnings(window, monkeypatch)
    # hull: refers to the existing body mesh; a duplicate mesh is refused
    window._apply_surface_create(body.body_id, dict(
        role='hull', surface_id=body.body_id + '/hull',
        source_geometry_id=body.body_id + '/hull-geometry',
        primary_hydrodynamics='potential_flow', viscous_correction='none',
        minus_side=None, plus_side=None, plate_drag=None, mesh=None))
    hull = window._project.body(body.body_id).hydro_surfaces[0]
    assert hull.role == 'hull'
    assert hull.semantics.surface_kind == 'body_boundary'
    assert hull.semantics.normal_convention == 'outward'
    duplicate_mesh = BodyMesh(((0., 0., -5.), (4., 0., -5.), (4., 4., -5.)),
                              ((0, 1, 2),))
    before = input_hash(window._project)
    window._apply_surface_create(body.body_id, dict(
        role='hull', surface_id=body.body_id + '/hull2',
        source_geometry_id=body.body_id + '/hull-geometry',
        primary_hydrodynamics='potential_flow', viscous_correction='none',
        minus_side=None, plus_side=None, plate_drag=None, mesh=duplicate_mesh))
    assert warned and 'duplicate mesh' in warned[-1][1]
    assert input_hash(window._project) == before
    # lid without existing lid geometry is refused by public validation
    window._apply_surface_create(body.body_id, dict(
        role='lid', surface_id=body.body_id + '/lid',
        source_geometry_id=body.body_id + '/lid-geometry',
        primary_hydrodynamics='none', viscous_correction='none',
        minus_side=None, plus_side=None, plate_drag=None, mesh=None))
    assert 'hydro_lid' in warned[-1][1]
    assert input_hash(window._project) == before
    # explicit lid geometry admits a numerical lid, never a physical plate
    lid_mesh = BodyMesh(((-1., -1., 0.), (1., -1., 0.), (1., 1., 0.)), ((0, 1, 2),))
    with_lid = window._project.body(body.body_id).with_edits(hydro_lid=lid_mesh)
    window._mark_edited(window._project.with_edits(
        bodies=tuple(with_lid if b.body_id == body.body_id else b
                     for b in window._project.bodies)))
    window._apply_surface_create(body.body_id, dict(
        role='lid', surface_id=body.body_id + '/lid',
        source_geometry_id=body.body_id + '/lid-geometry',
        primary_hydrodynamics='none', viscous_correction='none',
        minus_side=None, plus_side=None, plate_drag=None, mesh=None))
    lid = window._project.body(body.body_id).hydro_surfaces[-1]
    assert lid.role == 'lid'
    assert lid.semantics.surface_kind == 'numerical_lid'
    assert lid.semantics.primary_hydrodynamics == 'none'
    assert lid.semantics.normal_convention == 'downward'
    # a lid declared as a load owner is refused
    before = input_hash(window._project)
    window._apply_surface_create(window._project.bodies[1].body_id, dict(
        role='lid', surface_id=window._project.bodies[1].body_id + '/lid',
        source_geometry_id='g', primary_hydrodynamics='potential_flow',
        viscous_correction='none', minus_side=None, plus_side=None,
        plate_drag=None, mesh=None))
    assert 'numerical lid' in warned[-1][1]
    assert input_hash(window._project) == before


def test_create_refuses_duplicate_ids_and_missing_sheet_mesh(window, monkeypatch):
    p = attached_plate_project()
    window._set_project(p)
    body = window._project.bodies[0]
    other = window._project.bodies[1]
    warned = _warnings(window, monkeypatch)
    before = input_hash(window._project)
    # duplicate id across bodies
    window._apply_surface_create(other.body_id, dict(
        role='hull', surface_id=_hull(body).semantics.surface_id,
        source_geometry_id='g', primary_hydrodynamics='potential_flow',
        viscous_correction='none', minus_side=None, plus_side=None,
        plate_drag=None, mesh=None))
    assert any('duplicate surface id' in text for _, text in warned)
    assert input_hash(window._project) == before
    # attached sheet without explicit body-local mesh
    window._apply_surface_create(other.body_id, dict(
        role='attached', surface_id=other.body_id + '/sheet',
        source_geometry_id='g', primary_hydrodynamics='potential_flow',
        viscous_correction='none', minus_side='a', plus_side='b',
        plate_drag=None, mesh=None))
    assert 'body-local BodyMesh' in warned[-1][1]
    assert input_hash(window._project) == before


def test_creation_plate_numbers_validate_clearly(window, monkeypatch, tmp_path):
    p = default_two_body_project('surface editor creation')
    window._set_project(p)
    body = window._project.bodies[0]
    mesh = BodyMesh(((0., 0., -5.), (4., 0., -5.), (4., 4., -5.), (0., 4., -5.)),
                    ((0, 1, 2), (0, 2, 3)))
    path = tmp_path / 'sheet.json'
    path.write_text(json.dumps(mesh.to_dict()), encoding='utf-8')
    dialog = SurfaceAssignmentDialog(body)
    dialog._role.setCurrentIndex(2)
    dialog._correction.setCurrentText('plate_drag')
    dialog._minus_side.setText('lower-water')
    dialog._plus_side.setText('upper-water')
    dialog._mesh_path.setText(str(path))
    warned = _warnings(window, monkeypatch)
    before = input_hash(window._project)
    dialog._plate_area.setText('0')
    window._apply_surface_create(body.body_id, dialog.spec())
    assert warned and 'positive' in warned[-1][1]
    assert input_hash(window._project) == before
    dialog._plate_area.setValue(0.000005)
    dialog._plate_cd.setValue(0.0000002)
    window._apply_surface_create(body.body_id, dialog.spec())
    created = window._project.body(body.body_id).hydro_surfaces[-1]
    assert created.semantics.plate_drag.reference_area_m2 == 0.000005
    assert created.semantics.plate_drag.coefficient == 0.0000002


# ------------------------------------------------------ save/reopen + replay

def test_gui_surface_edit_save_reopen_schema7(window, tmp_path):
    from anymarine.persistence import ProjectStore
    p = attached_plate_project()
    window._set_project(p)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    window._apply_surface_edit(body.body_id, sid, {'source_geometry_id': 'gui-edited-source'})
    store = ProjectStore(tmp_path / 'project')
    store.save(window._project)
    manifest = json.loads((store.root / 'project.json').read_text(encoding='utf-8'))
    assert manifest['schema_version'] == 7
    loaded = store.load()
    assert loaded.to_dict() == window._project.to_dict()
    assert input_hash(loaded) == input_hash(window._project)
    assert (_sheet(loaded.body(body.body_id)).semantics.source_geometry_id
            == 'gui-edited-source')


def test_edited_current_surfaces_never_leak_into_producer_replay(window, admitted, monkeypatch):
    p, _, _ = admitted
    r = fixture_result(p, monkeypatch)
    window._set_project(p.with_edits(results=(r,)))
    window._set_result(r)
    body = window._project.bodies[0]
    sid = _sheet(body).semantics.surface_id
    original = _sheet(body).semantics.source_geometry_id
    window._apply_surface_edit(body.body_id, sid, {'source_geometry_id': 'edited-source'})
    assert window._service.result_stale(window._project, r)
    current = _sheet(window._project.body(body.body_id)).semantics
    assert current.source_geometry_id == 'edited-source'
    assert window._replay_project is not None
    producer = _sheet(window._replay_project.body(body.body_id)).semantics
    assert producer.source_geometry_id == original
    assert producer.minus_side == _sheet(body).semantics.minus_side


# ------------------------------------------ producing-dataset metadata

def _metadata_root(dataset):
    return dataset.parent / 'project'


def test_service_accessor_reads_only_stored_dataset_metadata(admitted):
    p, dataset, _ = admitted
    service = ApplicationService(_metadata_root(dataset))
    before = input_hash(p)
    metadata = service.producing_dataset_metadata(p)
    assert metadata.available and metadata.diagnostic == ''
    assert metadata.pressure_jump_convention == 'p_minus-p_plus;normal_minus_to_plus'
    assert metadata.source_capability_status == 'verified'
    assert metadata.sheet_source_backend == 'transport-fixture'
    assert metadata.pressure_jump_contract_sign is None
    assert input_hash(p) == before


def test_service_accessor_without_bound_dataset_is_unavailable():
    service = ApplicationService()
    missing = service.producing_dataset_metadata(None)
    assert not missing.available and 'no bound ANYhydro dataset' in missing.diagnostic
    missing = service.producing_dataset_metadata(attached_plate_project())
    assert not missing.available and 'no bound ANYhydro dataset' in missing.diagnostic


def test_selection_shows_stored_dataset_metadata(window, admitted):
    p, dataset, _ = admitted
    window._service.root = _metadata_root(dataset)
    window._set_project(p)
    before = input_hash(window._project)
    window._on_tree_clicked(_surface_node(window))
    assert window._details._stack.currentIndex() == PAGE_SURFACE
    text = window._details._surface_form._dataset.text()
    assert 'p_minus-p_plus;normal_minus_to_plus' in text
    assert 'Sheet source backend: transport-fixture' in text
    assert 'source capability status: verified' in text
    assert 'not current geometry qualification' in text
    assert 'source capability label' in text
    assert 'not scientific qualification' in text
    assert 'unavailable (not stored in the producing dataset)' in text
    assert input_hash(window._project) == before


def test_no_project_root_metadata_is_unavailable_with_reason(window, admitted):
    p, _, _ = admitted
    assert window._service.root is None
    window._set_project(p)
    window._details.show_surface(p.bodies[0].body_id,
                                  _sheet(p.bodies[0]).semantics.surface_id)
    text = window._details._surface_form._dataset.text()
    assert 'unavailable' in text
    assert 'load or save the project' in text
    assert 'p_minus-p_plus' not in text


def test_missing_sidecar_unavailable_without_external_fallback(window, admitted, tmp_path):
    p, dataset, _ = admitted
    service = ApplicationService(_metadata_root(dataset))
    root = service.save_current(p, tmp_path / 'missing-sidecar')
    sidecar = root / p.hydro_provenance.dataset_sidecar
    sidecar.unlink()
    # the unverified external source path still exists and holds the real
    # metadata; it must never be used as a display fallback
    assert Path(p.hydro_provenance.dataset_source_path).exists()
    window._service.root = root
    window._set_project(p)
    before = input_hash(window._project)
    window._details.show_surface(p.bodies[0].body_id,
                                  _sheet(p.bodies[0]).semantics.surface_id)
    text = window._details._surface_form._dataset.text()
    assert 'unavailable' in text
    assert 'dataset sidecar' in text
    assert 'p_minus-p_plus' not in text
    assert input_hash(window._project) == before


def test_tampered_sidecar_bytes_are_unavailable(window, admitted, tmp_path):
    p, dataset, _ = admitted
    service = ApplicationService(_metadata_root(dataset))
    root = service.save_current(p, tmp_path / 'tampered-sidecar')
    sidecar = root / p.hydro_provenance.dataset_sidecar
    payload = bytearray(sidecar.read_bytes())
    payload[0] ^= 0xFF
    sidecar.write_bytes(bytes(payload))
    window._service.root = root
    window._set_project(p)
    window._details.show_surface(p.bodies[0].body_id,
                                  _sheet(p.bodies[0]).semantics.surface_id)
    text = window._details._surface_form._dataset.text()
    assert 'unavailable' in text and 'sha256' in text
    assert 'p_minus-p_plus' not in text


def test_metadata_loader_reads_the_hash_verified_bytes(admitted, monkeypatch):
    from types import SimpleNamespace

    from anymarine.persistence import resolve_sidecar
    import anymarine.surface_assignments as sa

    p, dataset, _ = admitted
    service = ApplicationService(dataset.parent / 'project')
    source = resolve_sidecar(service.root, p.hydro_provenance.dataset_sidecar)
    approved = source.read_bytes()
    observed = []

    def loader(path):
        # mutate the authoritative sidecar between the hash check and the
        # load; the public loader must still receive the approved bytes
        source.write_bytes(b'changed-after-hash')
        observed.append(Path(path).read_bytes())
        return SimpleNamespace(attrs={})

    monkeypatch.setattr(sa, 'neutral_api',
                        lambda: SimpleNamespace(load_hydro_dataset=loader))
    try:
        result = service.producing_dataset_metadata(p)
        assert not result.available or observed == [approved]
    finally:
        source.write_bytes(approved)


def test_absent_neutral_package_metadata_is_actionable(window, admitted, monkeypatch):
    p, dataset, _ = admitted
    window._service.root = _metadata_root(dataset)
    window._set_project(p)
    import anymarine.surface_assignments as assignments

    def unavailable():
        raise ValueError('Explicit hydrodynamic surfaces require the tested '
                          'ANYhydro package; install it before loading this project')

    monkeypatch.setattr(assignments, 'neutral_api', unavailable)
    before = input_hash(window._project)
    window._details.show_surface(p.bodies[0].body_id,
                                  _sheet(p.bodies[0]).semantics.surface_id)
    text = window._details._surface_form._dataset.text()
    assert 'unavailable' in text and 'install' in text
    assert 'p_minus-p_plus' not in text
    assert input_hash(window._project) == before


# ------------------------------------------------- optional neutral package

def test_missing_neutral_package_is_actionable(window, monkeypatch):
    p = attached_plate_project()
    window._set_project(p)
    import anymarine.surface_assignments as assignments

    def unavailable():
        raise ValueError('Explicit hydrodynamic surfaces require the tested '
                          'ANYhydro package; install it before loading this project')

    monkeypatch.setattr(assignments, 'neutral_api', unavailable)
    body = window._project.bodies[0]
    before = input_hash(window._project)
    warned = _warnings(window, monkeypatch)
    window._apply_surface_create(body.body_id, dict(
        role='hull', surface_id=body.body_id + '/new-hull',
        source_geometry_id='g', primary_hydrodynamics='potential_flow',
        viscous_correction='none', minus_side=None, plus_side=None,
        plate_drag=None, mesh=None))
    assert warned and 'install' in warned[-1][1]
    window._apply_surface_edit(body.body_id, _sheet(body).semantics.surface_id,
                               {'source_geometry_id': 'edited'})
    assert 'install' in warned[-1][1]
    assert input_hash(window._project) == before


def test_qtapp_modules_have_no_module_level_anyhydro_import():
    import anymarine
    qtapp = Path(anymarine.__file__).parent / 'qtapp'
    sources = sorted(qtapp.glob('*.py'))
    assert sources
    for source in sources:
        tree = ast.parse(source.read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.Import):
                assert all(not alias.name.split('.')[0] == 'anyhydro'
                           for alias in node.names), source.name
            if isinstance(node, ast.ImportFrom):
                module = (node.module or '').split('.')[0]
                assert module != 'anyhydro', source.name
