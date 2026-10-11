"""Production Morison member authoring tests; real public compiler, no solve.

Exercises the production dialog event handlers and the shared
ApplicationService.load_native_inputs admission (public ANYsolver/ANYloads
compiler, no integration, no job). Fabricated transport fixtures are clearly
labelled and never scientific evidence.
"""
import pytest
from conftest import requires_qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from anymarine.hashing import input_hash, is_stale
from anymarine.native_examples import morison_two_body_project
from anymarine.native_jobs import record_from_evidence
from anymarine.services import ApplicationService, ServiceError
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.qtapp.morison_authoring import (MorisonMemberDialog,
    development_environment_template)
from anymarine.qtapp.native_inputs import NativeInputsDialog

pytestmark = requires_qt


@pytest.fixture(scope='module')
def qapp():
    yield QApplication.instance() or QApplication([])


def member(member_id, body_a, point_a, body_b, point_b, *, diameter=.06,
           drag=1., added=1., quadrature=4, primary_role='morison_full',
           bem=False, correction=False, buoyancy='none'):
    return dict(member_id=member_id,
        end_a=dict(body_id=body_a, point_body=list(point_a)),
        end_b=dict(body_id=body_b, point_body=list(point_b)),
        section=dict(diameter=diameter, drag_coefficient=drag,
                     added_mass_coefficient=added),
        ownership=dict(primary_role=primary_role, bem_included=bem,
                       drag_correction=correction, buoyancy_owner=buoyancy),
        quadrature_order=quadrature)


def set_member_fields(dialog, value):
    dialog.identity.setText(value['member_id'])
    for key in ('end_a', 'end_b'):
        editor = getattr(dialog, key)
        editor.body.setCurrentIndex(max(0, editor.body.findData(value[key]['body_id'])))
        for axis, coordinate in enumerate(value[key]['point_body']):
            editor.coordinates[axis].setValue(float(coordinate))
    dialog.diameter.setValue(value['section']['diameter'])
    dialog.drag_coefficient.setValue(value['section']['drag_coefficient'])
    dialog.added_mass_coefficient.setValue(value['section']['added_mass_coefficient'])
    dialog.quadrature_order.setValue(value['quadrature_order'])
    dialog.primary_role.setCurrentText(value['ownership']['primary_role'])
    dialog.bem_included.setChecked(value['ownership']['bem_included'])
    dialog.drag_correction.setChecked(value['ownership']['drag_correction'])
    dialog.buoyancy_owner.setCurrentText(value['ownership']['buoyancy_owner'])


def accept_member_as(monkeypatch, value):
    """Script the production member dialog: set fields, run production accept."""
    def scripted(dialog):
        set_member_fields(dialog, value)
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(MorisonMemberDialog, 'exec', scripted)


def accept_outer_with(monkeypatch, script, accepted=True):
    def scripted(dialog):
        script(dialog)
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    monkeypatch.setattr(NativeInputsDialog, 'exec', scripted)


def fabricated_morison_result(service, project):
    """Fabricated transport fixture; never solved scientific evidence."""
    snapshot = service.native_input_snapshot(project)
    inputs = project.native_inputs
    spec = inputs.run_spec
    ids = [body.body_id for body in project.bodies]
    components = ['dry', 'mechanical-links', inputs.morison.data['contribution_id']]
    frames = []
    for step in range(spec['steps'] + 1):
        frames.append(dict(step=step, time=step * spec['dt'],
            positions={bid: list(inputs.initial_states[bid]['position']) for bid in ids},
            rotations={bid: [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]] for bid in ids},
            world_velocities={bid: [0.] * 6 for bid in ids},
            world_accelerations={bid: [0.] * 6 for bid in ids},
            kinetic_energy=0., link_potential_energy=0., link_damping_power=0.,
            residual_norm=0., newton_iterations=0,
            force_coordinate_system='world_wrench_at_body_origin',
            force_decomposition={cid: {bid: [0.] * 6 for bid in ids} for cid in components}))
    evidence = dict(api_version='marine_mechanical_v1', status='completed', body_ids=ids,
        dt=spec['dt'], steps_requested=spec['steps'], accepted=frames,
        histories={'mechanical-links': None, inputs.morison.data['contribution_id']: None},
        force_signs={'dry': 'internal', 'contributions': 'external'}, logs=[])
    return record_from_evidence(evidence, snapshot, result_id='morison-authoring-fixture',
        note='Fabricated authoring transport fixture, not solved scientific evidence.',
        created_utc='')


def test_member_dialog_offers_declared_bodies_and_public_choices(qapp):
    project = morison_two_body_project()
    bodies = tuple((body.body_id, body.name) for body in project.bodies)
    dialog = MorisonMemberDialog(bodies)
    try:
        assert [dialog.end_a.body.itemData(i) for i in range(dialog.end_a.body.count())] \
            == [body_id for body_id, _ in bodies]
        assert [dialog.end_b.body.itemData(i) for i in range(dialog.end_b.body.count())] \
            == [body_id for body_id, _ in bodies]
        assert [dialog.primary_role.itemText(i) for i in range(dialog.primary_role.count())] \
            == ['morison_full', 'potential_flow', 'none']
        assert [dialog.buoyancy_owner.itemText(i) for i in range(dialog.buoyancy_owner.count())] \
            == ['none', 'potential_flow', 'morison']
        assert dialog.quadrature_order.minimum() == 2 and dialog.quadrature_order.maximum() == 16
        # Exact decimal fields replace the ranged spin boxes: the shared
        # compiler owns positivity/sign criteria, so explicitly declared
        # small or large valid values are preserved, never silently clamped.
        dialog.diameter.setValue(1e-9)
        assert dialog.diameter.value() == 1e-9
        dialog.end_a.coordinates[0].setValue(2.5e6)
        assert dialog.end_a.coordinates[0].value() == 2.5e6
    finally:
        dialog.close()


def test_member_dialog_refuses_duplicate_identity_and_identical_endpoints(qapp):
    project = morison_two_body_project()
    bodies = tuple((body.body_id, body.name) for body in project.bodies)
    a, b = (body.body_id for body in project.bodies)
    dialog = MorisonMemberDialog(bodies, existing_ids=['vertical-member-a'])
    try:
        set_member_fields(dialog, member('vertical-member-a', a, [0., 0., .5],
                                         a, [0., 0., -3.]))
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert 'unique' in dialog.message.text()
        set_member_fields(dialog, member('fresh-member', a, [1., 2., 3.],
                                         a, [1., 2., 3.]))
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert 'zero current length' in dialog.message.text()
        set_member_fields(dialog, member('fresh-member', a, [1., 2., 3.],
                                         b, [1., 2., 3.]))
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.member()['member_id'] == 'fresh-member'
    finally:
        dialog.close()


def test_production_add_member_admits_through_public_compiler(qapp, monkeypatch):
    service = ApplicationService()
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    before = project.native_inputs.to_dict()
    dialog = NativeInputsDialog(project)
    try:
        assert dialog.data() == before
        authored = member('authored-member', a, [.5, 0., 0.], b, [0., 0., -1.],
                          diameter=.1, drag=.8, added=.6, quadrature=6)
        accept_member_as(monkeypatch, authored)
        dialog._add_member_button.click()
        data = dialog.data()
        assert [m['member_id'] for m in data['morison']['members']] \
            == ['vertical-member-a', 'spanning-member-ab', 'authored-member']
        assert data['morison']['environment'] == before['morison']['environment']
        assert data['links'] == before['links'] and data['initial_states'] == before['initial_states']
    finally:
        dialog.close()
    admitted = service.load_native_inputs(project, data)
    stored = admitted.native_inputs.morison.data['members'][-1]
    assert stored['member_id'] == 'authored-member'
    assert stored['section'] == dict(diameter=.1, drag_coefficient=.8, added_mass_coefficient=.6)
    assert stored['quadrature_order'] == 6
    assert admitted.native_inputs.morison.data['environment'] == project.native_inputs.morison.data['environment']
    assert project.native_inputs.to_dict() == before


def test_production_handler_adds_member_updates_project_and_preview(qapp, monkeypatch):
    service = ApplicationService()
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    authored = member('authored-member', a, [.5, 0., 0.], b, [0., 0., -1.])
    accept_member_as(monkeypatch, authored)
    accept_outer_with(monkeypatch, lambda dialog: dialog._add_member_button.click())
    window = MainWindow(viewer_backend='software')
    try:
        window._set_project(project)
        assert set(window._member_scene.handles) == {'vertical-member-a', 'spanning-member-ab'}
        window._edit_native_inputs()
        assert window._dirty is True
        ids = [m['member_id'] for m in window._project.native_inputs.morison.data['members']]
        assert ids == ['vertical-member-a', 'spanning-member-ab', 'authored-member']
        assert 'authored-member' in window._member_scene.handles
        assert window._project.native_inputs.morison.data['environment'] \
            == project.native_inputs.morison.data['environment']
    finally:
        window._dirty = False
        window.close()


def test_production_handler_remove_member_updates_preview(qapp, monkeypatch):
    project = morison_two_body_project()
    accept_outer_with(monkeypatch, lambda dialog: (
        dialog.tree.setCurrentIndex(dialog._member_items[1].index()),
        dialog._remove_member_button.click()))
    window = MainWindow(viewer_backend='software')
    try:
        window._set_project(project)
        window._edit_native_inputs()
        ids = [m['member_id'] for m in window._project.native_inputs.morison.data['members']]
        assert ids == ['vertical-member-a']
        assert set(window._member_scene.handles) == {'vertical-member-a'}
    finally:
        window._dirty = False
        window.close()


def test_production_edit_member_attachments_and_ownership_admit(qapp, monkeypatch):
    service = ApplicationService()
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    edited = member('vertical-member-a', a, [0., 0., .5], b, [0., 0., -2.],
                    diameter=.08, buoyancy='morison')
    accept_member_as(monkeypatch, edited)
    accept_outer_with(monkeypatch, lambda dialog: (
        dialog.tree.setCurrentIndex(dialog._member_items[0].index()),
        dialog._edit_member_button.click()))
    window = MainWindow(viewer_backend='software')
    try:
        window._set_project(project)
        window._edit_native_inputs()
        stored = window._project.native_inputs.morison.data['members'][0]
        assert stored['end_b'] == dict(body_id=b, point_body=(0., 0., -2.))
        assert stored['ownership']['buoyancy_owner'] == 'morison'
        assert stored['section']['diameter'] == .08
        assert window._project.native_inputs.morison.data['members'][1] \
            == project.native_inputs.morison.data['members'][1]
    finally:
        window._dirty = False
        window.close()


def test_create_morison_route_when_project_has_none(qapp, monkeypatch):
    service = ApplicationService()
    project = service.load_native_example()
    assert 'morison' not in project.native_inputs.to_dict()
    a, b = (body.body_id for body in project.bodies)
    dialog = NativeInputsDialog(project)
    try:
        assert dialog._create_morison_button.isVisibleTo(dialog)
        assert not dialog._add_member_button.isVisibleTo(dialog)
        accept_member_as(monkeypatch, member('first-member', a, [0., 0., 1.],
                                             b, [0., 0., -1.], diameter=.12))
        dialog._create_morison_button.click()
        data = dialog.data()
        assert dialog._add_member_button.isVisibleTo(dialog)
        assert not dialog._create_morison_button.isVisibleTo(dialog)
    finally:
        dialog.close()
    assert data['morison']['environment'] == development_environment_template()
    assert data['links'] == project.native_inputs.to_dict()['links']
    assert data['initial_states'] == project.native_inputs.to_dict()['initial_states']
    assert 'potential_flow' not in data and 'wave_realization' not in data
    admitted = service.load_native_inputs(project, data)
    assert admitted.native_inputs.morison.data['members'][0]['member_id'] == 'first-member'
    assert 'morison' not in project.native_inputs.to_dict()


def test_create_morison_reuses_shared_mooring_environment(qapp, monkeypatch):
    """Hydrodynamic mooring lines share one declared environment record."""
    from anymarine.native_examples import mooring_hydrodynamic_project
    from anymarine.native_inputs import NativeMechanicalInputs
    project = mooring_hydrodynamic_project()
    data = project.native_inputs.to_dict()
    del data['morison']
    project = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
    dialog = NativeInputsDialog(project)
    try:
        a, b = (body.body_id for body in project.bodies)
        accept_member_as(monkeypatch, member('shared-env-member', a, [0., 0., 1.],
                                             b, [0., 0., -1.]))
        dialog._create_morison_button.click()
        authored = dialog.data()
    finally:
        dialog.close()
    assert 'morison' in authored
    assert authored['morison']['environment'] == authored['mooring']['data']['environment']


def test_cancel_keeps_current_project_unchanged(qapp, monkeypatch):
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    accept_member_as(monkeypatch, member('cancelled-member', a, [0., 0., 1.],
                                         b, [0., 0., -1.]))
    accept_outer_with(monkeypatch, lambda dialog: dialog._add_member_button.click(),
                      accepted=False)
    window = MainWindow(viewer_backend='software')
    try:
        window._set_project(project)
        window._edit_native_inputs()
        assert window._project is project and window._dirty is False
        assert window._project.native_inputs.to_dict() == project.native_inputs.to_dict()
    finally:
        window._dirty = False
        window.close()


def test_refused_compiler_ownership_keeps_project_unchanged(qapp, monkeypatch):
    """The shared compiler refuses duplicate BEM inertia; the UI does not pre-validate."""
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    accept_member_as(monkeypatch, member('bem-member', a, [0., 0., 1.],
                                         b, [0., 0., -1.], primary_role='morison_full',
                                         bem=True))
    accept_outer_with(monkeypatch, lambda dialog: dialog._add_member_button.click())
    window = MainWindow(viewer_backend='software')
    try:
        window._set_project(project)
        warnings = []
        monkeypatch.setattr(window, '_notify_warning',
                            lambda title, text: warnings.append(text))
        window._edit_native_inputs()
        assert window._project is project and window._dirty is False
        assert warnings and 'refused' in warnings[0]
        assert window._project.native_inputs.to_dict() == project.native_inputs.to_dict()
    finally:
        window._dirty = False
        window.close()


def test_service_refuses_invalid_member_data_transactionally():
    service = ApplicationService()
    project = morison_two_body_project()
    before = project.native_inputs.to_dict()
    a, b = (body.body_id for body in project.bodies)

    def candidate():
        data = morison_two_body_project().native_inputs.to_dict()
        return data

    cases = {
        'duplicate identity': lambda data: data['morison']['members'].append(
            dict(data['morison']['members'][0])),
        'zero diameter': lambda data: data['morison']['members'][0]['section'].update(diameter=0.),
        'negative drag': lambda data: data['morison']['members'][0]['section'].update(
            drag_coefficient=-1.),
        'identical endpoints': lambda data: data['morison']['members'][0].update(
            end_b=dict(data['morison']['members'][0]['end_a'])),
        'missing endpoint body': lambda data: data['morison']['members'][0]['end_b'].update(
            body_id='undeclared-body'),
        'quadrature below public bound': lambda data: data['morison']['members'][0].update(
            quadrature_order=1),
        'empty member envelope': lambda data: data['morison'].update(members=[]),
    }
    for name, mutate in cases.items():
        data = candidate()
        mutate(data)
        with pytest.raises(ServiceError):
            service.load_native_inputs(project, data)
        assert project.native_inputs.to_dict() == before


def test_last_member_removal_is_blocked_with_actionable_reason(qapp, monkeypatch):
    service = ApplicationService()
    project = morison_two_body_project()
    data = project.native_inputs.to_dict()
    del data['morison']['members'][1]
    single = service.load_native_inputs(project, data)
    dialog = NativeInputsDialog(single)
    try:
        messages = []
        monkeypatch.setattr(QMessageBox, 'information',
                            staticmethod(lambda *args, **kwargs: messages.append(args)))
        dialog.tree.setCurrentIndex(dialog._member_items[0].index())
        dialog._remove_member_button.click()
        assert messages and 'at least one' in messages[-1][2]
        assert len(dialog.data()['morison']['members']) == 1
    finally:
        dialog.close()


def test_missing_owner_package_refusal_is_actionable(monkeypatch):
    import anymarine.mechanics as mechanics
    service = ApplicationService()
    project = morison_two_body_project()
    original = mechanics.importlib.import_module

    def imported(name):
        if name == 'anysolver.marine_morison':
            raise ImportError('test unavailable optional API')
        return original(name)
    monkeypatch.setattr(mechanics.importlib, 'import_module', imported)
    data = project.native_inputs.to_dict()
    data['morison']['members'].append(member('unadmitted-member',
        project.bodies[0].body_id, [0., 0., 1.], project.bodies[1].body_id, [0., 0., -1.]))
    with pytest.raises(ServiceError) as error:
        service.load_native_inputs(project, data)
    assert 'install the tested ANYsolver wheel with native Morison support' in str(error.value)
    assert 'morison' in project.native_inputs.to_dict()
    assert len(project.native_inputs.morison.data['members']) == 2


def test_save_reopen_preserves_exact_authored_data(qapp, monkeypatch, tmp_path):
    service = ApplicationService()
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    dialog = NativeInputsDialog(project)
    try:
        accept_member_as(monkeypatch, member('authored-member', a, [.5, 0., 0.],
                                             b, [0., 0., -1.], diameter=.1))
        dialog._add_member_button.click()
        data = dialog.data()
    finally:
        dialog.close()
    admitted = service.load_native_inputs(project, data)
    root = service.save_current(admitted, tmp_path / 'authored-project')
    reopened = ApplicationService().load(root)
    assert reopened.native_inputs.to_dict() == admitted.native_inputs.to_dict()
    assert reopened.native_inputs.morison.data['environment'] \
        == project.native_inputs.morison.data['environment']
    assert [m['member_id'] for m in reopened.native_inputs.morison.data['members']] \
        == ['vertical-member-a', 'spanning-member-ab', 'authored-member']


def test_member_edits_invalidate_hash_and_stale_replay_uses_captured_geometry(
        qapp, monkeypatch):
    service = ApplicationService()
    project = morison_two_body_project()
    record = fabricated_morison_result(service, project)
    a, b = (body.body_id for body in project.bodies)
    dialog = NativeInputsDialog(project)
    try:
        accept_member_as(monkeypatch, member('authored-member', a, [.5, 0., 0.],
                                             b, [0., 0., -1.]))
        dialog._add_member_button.click()
        data = dialog.data()
    finally:
        dialog.close()
    admitted = service.load_native_inputs(project, data)
    assert input_hash(admitted) != input_hash(project)
    assert is_stale(admitted, record.input_hash)
    assert service.result_stale(admitted, record)
    window = MainWindow(viewer_backend='software')
    try:
        window._set_project(project)
        window._set_result(record)
        assert 'STALE' not in window._result_status
        window._mark_edited(admitted)
        assert 'STALE' in window._result_status
        captured = window._replay_project.native_inputs.morison.data['members']
        assert [m['member_id'] for m in captured] \
            == ['vertical-member-a', 'spanning-member-ab']
        assert len(window._project.native_inputs.morison.data['members']) == 3
    finally:
        window._dirty = False
        window.close()


def test_member_addition_preserves_shared_wave_realization(qapp, monkeypatch):
    service = ApplicationService()
    project = service.load_native_sea_state_example()
    waves = project.native_inputs.wave_realization.to_dict()
    series = project.native_inputs.potential_flow.to_dict()['excitation_series']
    environment = project.native_inputs.morison.data['environment']
    a, b = (body.body_id for body in project.bodies)
    dialog = NativeInputsDialog(project)
    try:
        path = ('morison', 'environment', 'components', 0, 'amplitude')
        cell = next(cell for cell, keys, _ in dialog._leaves if keys == path)
        assert not cell.isEditable()
        accept_member_as(monkeypatch, member('authored-member', a, [.5, 0., 0.],
                                             b, [0., 0., -1.]))
        dialog._add_member_button.click()
        data = dialog.data()
    finally:
        dialog.close()
    admitted = service.load_native_inputs(project, data)
    assert admitted.native_inputs.wave_realization.to_dict() == waves
    assert admitted.native_inputs.potential_flow.to_dict()['excitation_series'] == series
    assert admitted.native_inputs.morison.data['environment'] == environment
    assert project.native_inputs.wave_realization.to_dict() == waves


def _density_cell(dialog):
    return next(cell for cell, path, kind in dialog._leaves
                if path == ('morison', 'environment', 'density'))


def test_member_dialog_noop_preserves_exact_declared_numbers(qapp):
    """Measured regression: fixed-decimals spin boxes rounded unedited values.

    An unedited show/apply cycle must round-trip the stored record
    byte-exactly, including values beyond six decimals.
    """
    project = morison_two_body_project()
    bodies = tuple((body.body_id, body.name) for body in project.bodies)
    a, b = (body.body_id for body in project.bodies)
    record = member('exact-member', a, [0.123456789012, 0., .5], b, [0., 0., -3.],
                    diameter=0.06123456789012, drag=0.98765432109,
                    added=0.76543219876)
    dialog = MorisonMemberDialog(bodies, member=record)
    try:
        assert dialog.member() == record
        # Zero Cd/Ca are valid public nonnegative coefficients, not refusals.
        dialog.drag_coefficient.setValue(0.)
        dialog.added_mass_coefficient.setValue(0.)
        authored = dialog.member()
        assert authored['section']['drag_coefficient'] == 0.
        assert authored['section']['added_mass_coefficient'] == 0.
    finally:
        dialog.close()
    data = project.native_inputs.to_dict()
    data['morison']['members'][0] = record
    admitted = ApplicationService().load_native_inputs(project, data)
    assert admitted.native_inputs.morison.data['members'][0]['section'] \
        == record['section']


def test_member_dialog_refuses_unparseable_number_without_accepting(qapp):
    project = morison_two_body_project()
    bodies = tuple((body.body_id, body.name) for body in project.bodies)
    a, b = (body.body_id for body in project.bodies)
    dialog = MorisonMemberDialog(bodies)
    try:
        set_member_fields(dialog, member('bad-number-member', a, [0., 0., .5],
                                         b, [0., 0., -3.]))
        dialog.diameter.setText('not-a-number')
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert 'not a valid number' in dialog.message.text()
        dialog.diameter.setValue(.06)
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.member()['section']['diameter'] == .06
    finally:
        dialog.close()


def test_structural_member_operations_keep_pending_tree_edits(qapp, monkeypatch):
    """Measured regression: staged density edit 1500.0 reverted to 1025.0.

    Add, edit, remove and create must fold pending tree edits into the
    working copy before rebuilding the tree; unrelated declared records stay
    exact.
    """
    project = morison_two_body_project()
    a, b = (body.body_id for body in project.bodies)
    dialog = NativeInputsDialog(project)
    try:
        _density_cell(dialog).setText('1500.0')
        accept_member_as(monkeypatch, member('authored-member', a, [.5, 0., 0.],
                                             b, [0., 0., -1.]))
        dialog._add_member_button.click()
        data = dialog.data()
        assert data['morison']['environment']['density'] == 1500.0
        assert len(data['morison']['members']) == 3

        dialog.tree.setCurrentIndex(dialog._member_items[0].index())
        accept_member_as(monkeypatch, member('vertical-member-a', a, [0., 0., .5],
                                             b, [0., 0., -2.5]))
        dialog._edit_member_button.click()
        data = dialog.data()
        assert data['morison']['environment']['density'] == 1500.0
        assert data['morison']['members'][0]['end_b']['point_body'] == [0., 0., -2.5]

        dialog.tree.setCurrentIndex(dialog._member_items[2].index())
        dialog._remove_member_button.click()
        data = dialog.data()
        assert data['morison']['environment']['density'] == 1500.0
        assert [m['member_id'] for m in data['morison']['members']] \
            == ['vertical-member-a', 'spanning-member-ab']
        assert data['links'] == project.native_inputs.to_dict()['links']
        assert data['initial_states'] == project.native_inputs.to_dict()['initial_states']
    finally:
        dialog.close()


def test_create_morison_keeps_pending_unrelated_tree_edits(qapp, monkeypatch):
    service = ApplicationService()
    project = service.load_native_example()
    a, b = (body.body_id for body in project.bodies)
    dialog = NativeInputsDialog(project)
    try:
        cell = next(cell for cell, path, kind in dialog._leaves
                    if path == ('run_spec', 'dt'))
        cell.setText('0.02')
        accept_member_as(monkeypatch, member('first-member', a, [0., 0., 1.],
                                             b, [0., 0., -1.]))
        dialog._create_morison_button.click()
        data = dialog.data()
        assert data['run_spec']['dt'] == 0.02
        assert data['morison']['members'][0]['member_id'] == 'first-member'
    finally:
        dialog.close()


def test_invalid_pending_edit_blocks_structural_change_without_mutation(
        qapp, monkeypatch):
    project = morison_two_body_project()
    dialog = NativeInputsDialog(project)
    try:
        warnings = []
        monkeypatch.setattr(QMessageBox, 'warning',
                            staticmethod(lambda *args, **kwargs: warnings.append(args)))
        opened = []
        monkeypatch.setattr(MorisonMemberDialog, 'exec',
                            lambda self: opened.append(self) or QDialog.DialogCode.Accepted)
        _density_cell(dialog).setText('not-a-number')
        dialog._add_member_button.click()
        assert warnings and 'not a valid number' in warnings[-1][-1]
        assert not opened
        assert _density_cell(dialog).text() == 'not-a-number'
        assert len(dialog._data['morison']['members']) == 2
        assert dialog._project.native_inputs.to_dict() == project.native_inputs.to_dict()
    finally:
        dialog.close()


def test_member_dialog_cancel_keeps_parent_pending_edits(qapp, monkeypatch):
    project = morison_two_body_project()
    dialog = NativeInputsDialog(project)
    try:
        _density_cell(dialog).setText('1500.0')
        monkeypatch.setattr(MorisonMemberDialog, 'exec',
                            lambda self: QDialog.DialogCode.Rejected)
        dialog._add_member_button.click()
        assert _density_cell(dialog).text() == '1500.0'
        data = dialog.data()
        assert data['morison']['environment']['density'] == 1500.0
        assert len(data['morison']['members']) == 2
        dialog.reject()
        assert dialog._project.native_inputs.to_dict() == project.native_inputs.to_dict()
    finally:
        dialog.close()
