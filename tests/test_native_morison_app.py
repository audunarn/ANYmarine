"""Native member application contracts; each real trajectory has three steps."""
import copy
import json
from pathlib import Path
import time

import numpy as np
import pytest

from anymarine.hashing import input_hash
from anymarine.native_inputs import NativeMechanicalInputs
from anymarine.native_jobs import project_from_snapshot, record_from_evidence, decode_output
from anymarine.native_examples import morison_two_body_project, fluid_two_body_project
from anymarine.services import ApplicationService, ServiceError
from anymarine.morison_inputs import MORISON_ID


def test_immutable_records_legacy_identity_and_saved_reopen(tmp_path):
    service = ApplicationService()
    baseline = json.loads(Path('C:/ANYworkspaceWork/anymarine-evidence/native-fluid-app-legacy-baseline.json').read_text())
    dry = service.load_native_example()
    assert dry.to_dict() == baseline['project'] and input_hash(dry) == baseline['input_hash']
    assert 'morison' not in dry.native_inputs.to_dict()
    fluid = fluid_two_body_project()
    assert 'morison' not in fluid.native_inputs.to_dict()
    p = service.load_native_morison_example()
    assert not p.results and p.native_inputs.run_spec['steps'] == 3
    raw = p.native_inputs.to_dict()
    restored = NativeMechanicalInputs.from_dict(raw)
    raw['morison']['environment']['components'][0]['phase'] = 9.
    assert restored.to_dict() == p.native_inputs.to_dict()
    with pytest.raises(TypeError):
        restored.morison.data['members'][0]['end_a']['point_body'][0] = 1.
    changed = p.with_edits(native_inputs=NativeMechanicalInputs.from_dict(raw))
    assert input_hash(changed) != input_hash(p)
    service.save_current(p, tmp_path/'project')
    reopened = service.load(tmp_path/'project')
    assert reopened.to_dict() == p.to_dict()


@pytest.mark.parametrize('bad', ['none', 'unknown', 'version', 'unknown_environment',
    'boolean_point', 'nonfinite', 'duplicate_member', 'unknown_body', 'reserved_wrench'])
def test_input_envelope_refuses_malformed_data(bad):
    data = morison_two_body_project().native_inputs.to_dict()
    m = data['morison']
    if bad == 'none': data['morison'] = None
    elif bad == 'unknown': m['provider'] = 'executable.module'
    elif bad == 'version': m['api_version'] = 'unknown'
    elif bad == 'unknown_environment': m['environment']['provider'] = 'unknown'
    elif bad == 'boolean_point': m['members'][0]['end_a']['point_body'][0] = True
    elif bad == 'nonfinite': m['environment']['components'][0]['phase'] = float('nan')
    elif bad == 'duplicate_member': m['members'][1]['member_id'] = m['members'][0]['member_id']
    elif bad == 'unknown_body': m['members'][0]['end_a']['body_id'] = 'missing'
    else:
        data['world_wrenches'].append(dict(contribution_id=MORISON_ID,
            body_id=next(iter(data['initial_states'])), point_body=[0.,0.,0.],
            force_world=[0.,0.,0.], moment_world=[0.,0.,0.]))
    with pytest.raises(ValueError): NativeMechanicalInputs.from_dict(data)


def test_public_compile_coupled_order_and_atomic_physics_refusals():
    service = ApplicationService()
    p = morison_two_body_project()
    model, states, spec = service.prepare_native_run(p)
    assert model.contributions[-1].contribution_id == MORISON_ID
    assert spec.steps == 3 and set(states) == set(p.native_inputs.initial_states)
    data = fluid_two_body_project().native_inputs.to_dict()
    data['morison'] = p.native_inputs.morison.to_dict()
    coupled = service.load_native_inputs(fluid_two_body_project(), data)
    model, _, _ = service.prepare_native_run(coupled)
    assert [c.contribution_id for c in model.contributions] == ['potential-flow-development', MORISON_ID]
    stale = coupled.with_edits(bodies=(coupled.bodies[0].with_edits(reference_position=(-1.,0.,0.)), coupled.bodies[1]))
    with pytest.raises(ValueError, match='equilibrium'): service.prepare_native_run(stale)
    for failure in ('diameter', 'duplicate_inertia', 'current', 'order', 'collapsed'):
        bad = p.native_inputs.to_dict()
        m = bad['morison']['members'][0]
        if failure == 'diameter': m['section']['diameter'] = -1.
        elif failure == 'duplicate_inertia': m['ownership']['bem_included'] = True
        elif failure == 'current': bad['morison']['environment']['current']['depths'][1] = -1.
        elif failure == 'order': m['quadrature_order'] = 17
        else: m['end_b'] = copy.deepcopy(m['end_a'])
        with pytest.raises(ServiceError): service.load_native_inputs(p, bad)
        assert p.native_inputs.to_dict() == morison_two_body_project().native_inputs.to_dict()


@pytest.mark.parametrize('missing', ['missing', 'incompatible'])
def test_optional_backend_refusal(monkeypatch, missing):
    import anymarine.mechanics as mechanics
    from types import SimpleNamespace
    original = mechanics.importlib.import_module
    def imported(name):
        if name == 'anysolver.marine_morison':
            if missing == 'missing': raise ImportError('test unavailable optional API')
            return SimpleNamespace(API_VERSION='unknown')
        return original(name)
    monkeypatch.setattr(mechanics.importlib, 'import_module', imported)
    with pytest.raises(mechanics.MechanicsUnavailableError):
        mechanics.prepare_native_run(morison_two_body_project())


def assert_member_positions(window):
    scene = window._member_scene
    for member in scene.members:
        points = []
        for end in ('end_a', 'end_b'):
            e = member[end]
            pose = window._handles[e['body_id']].transform
            points.append(pose[:3,:3] @ e['point_body'] + pose[:3,3])
        np.testing.assert_allclose(scene.handles[member['member_id']].mesh.positions, points, rtol=0., atol=1e-12)


def test_qt_editor_member_selection_transforms_wave_freeze_renderer(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    from anymarine.qtapp.native_inputs import NativeInputsDialog
    app = QApplication.instance() or QApplication([])
    w = MainWindow(viewer_backend='software')
    try:
        w.load_native_morison_example()
        assert len(w._member_scene.handles) == 2 and not w._current_result
        assert_member_positions(w)
        dialog = NativeInputsDialog(w._project)
        assert dialog.data() == w._project.native_inputs.to_dict()
        path = ('morison','environment','components',0,'amplitude')
        cell = next(cell for cell, keys, _ in dialog._leaves if keys == path)
        cell.setText('.12')
        edited = w._service.load_native_inputs(w._project, dialog.data())
        dialog.close()
        w._mark_edited(edited)
        assert w._project.native_inputs.morison.data['environment']['components'][0]['amplitude'] == .12
        data = edited.native_inputs.to_dict()
        bid = edited.bodies[0].body_id
        data['initial_states'][bid]['position'][0] += .7
        data['initial_states'][bid]['rotation'] = [[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]]
        w._mark_edited(w._service.load_native_inputs(edited, data))
        assert_member_positions(w)
        old = w._member_scene.handles.copy()
        w._change_renderer('software')
        assert all(w._member_scene.handles[mid] is not h for mid,h in old.items())
        assert_member_positions(w)
        w._waves.mode.setCurrentIndex(1)
        assert w._waves.active and w._waves.handle is not None
        saved = {bid: np.array(h.transform,copy=True) for bid,h in w._handles.items()}
        w._waves.advance()
        assert_member_positions(w)
        w._waves.reset()
        for bid,h in w._handles.items(): np.testing.assert_array_equal(h.transform,saved[bid])
        root = w._tree_model
        section = next(root.index(i,0) for i in range(root.rowCount()) if root.index(i,0).data() == 'Morison members')
        w._on_tree_clicked(root.index(0,0,section))
        assert set(w.viewer.highlighted_tags()) == {'morison-member:vertical-member-a'}
        assert w._current_result is None
        # Removing the optional capability removes its retained geometry.
        data = w._project.native_inputs.to_dict()
        del data['morison']
        w._mark_edited(w._service.load_native_inputs(w._project,data))
        assert not w._member_scene.handles
    finally:
        w._dirty = False
        w.close()


def test_actual_qprocess_immutable_result_inventory_replay_save_and_mutation(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow(viewer_backend='software')
    try:
        w.load_native_morison_example()
        p = w._project
        manager = w._job_manager
        manager._workroot = tmp_path/'jobs'
        job_id = manager.submit_native(p,max_frames=4,wall_seconds=6.)
        edited = p.native_inputs.to_dict()
        edited['morison']['members'][0]['end_b']['point_body'][2] = -2.5
        w._mark_edited(w._service.load_native_inputs(p,edited))
        deadline = time.monotonic()+9.
        while manager.active_jobs() and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.01)
        job = manager.job(job_id)
        assert job.state == 'completed', job.diagnostics
        result = w._current_result
        assert result.frame_count() == 4 and 'Morison' in result.note
        assert 'STALE' in w._result_status and 'Morison' in w._result_status
        assert w._replay_project.native_inputs.to_dict() == p.native_inputs.to_dict()
        w._apply_frame(3)
        assert_member_positions(w)
        # Producing endpoint geometry is authoritative despite current edits.
        assert w._member_scene.members[0]['end_b']['point_body'][2] == -3.
        raw = job.output_path.read_bytes()
        payload = json.loads(raw)
        assert payload['evidence']['histories'][MORISON_ID] is None
        assert MORISON_ID in payload['evidence']['accepted'][3]['force_decomposition']
        producing = project_from_snapshot(result.native_snapshot)
        service = ApplicationService()
        stored = service.add_result(producing,result)
        service.save_current(stored,tmp_path/'saved')
        reopened = service.load(tmp_path/'saved')
        assert reopened.results[0].to_dict() == result.to_dict()
        assert not service.result_stale(reopened,result)
        for failure in ('history','inventory','component'):
            evidence = copy.deepcopy(payload['evidence'])
            if failure == 'history': evidence['histories'][MORISON_ID] = {'silently_reset': True}
            elif failure == 'inventory': del evidence['histories'][MORISON_ID]
            else: del evidence['accepted'][0]['force_decomposition'][MORISON_ID]
            with pytest.raises(ValueError):
                record_from_evidence(evidence,job.snapshot,result_id='bad',note='',created_utc='')
        with pytest.raises(ValueError,match='seal'): decode_output(raw,job.snapshot,job.input_hash,'')
        altered = copy.deepcopy(payload)
        altered['result']['positions'][result.body_ids[0]][-1][0] += 1.
        mutated = json.dumps(altered).encode()
        import hashlib
        with pytest.raises(ValueError,match='authoritative evidence'):
            decode_output(mutated,job.snapshot,job.input_hash,hashlib.sha256(mutated).hexdigest())
        channel = 'wrench'+json.dumps([MORISON_ID,result.body_ids[0]],separators=(',',':'))+'/Fx'
        w._channel_combo.setCurrentText(channel)
        assert w._plot.series_values() == result.channels[channel]
        w._change_renderer('software')
        assert_member_positions(w)
        w._set_result(None)
        assert w._member_scene.members[0]['end_b']['point_body'][2] == -2.5
    finally:
        if w._job_manager.active_jobs():
            w._job_manager.cancel_all()
            until = time.monotonic()+3.
            while w._job_manager.active_jobs() and time.monotonic()<until:
                app.processEvents(); time.sleep(.01)
        w._dirty = False
        w.close()


def test_morison_worker_pre_cancel_preserves_partial(tmp_path, capsys):
    from anymarine.worker import run
    service = ApplicationService()
    snapshot = service.native_input_snapshot(morison_two_body_project(),max_frames=4,wall_seconds=6.)
    inp,out,cancel = tmp_path/'input.json',tmp_path/'output.json',tmp_path/'cancel'
    service.write_job_input(snapshot,inp)
    cancel.touch()
    assert run(inp,out,cancel) == 3 and not out.exists()
    assert 'OUTPUT_SHA256' not in capsys.readouterr().out
    assert json.loads(out.with_suffix('.partial.json').read_text())['status'] == 'cancelled'
