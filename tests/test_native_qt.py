"""Native Qt production path tests; actual child execution requires check authority."""
import time
import pytest
from conftest import requires_qt
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from anymarine.services import ApplicationService
from anymarine.qtapp.jobs import JobManager, JobRecord, JOB_STATE_RUNNING, JOB_STATE_FAILED
from anymarine.qtapp.native_inputs import NativeInputsDialog
from anymarine.qtapp.mainwindow import MainWindow
from test_native_jobs import short_project, transport_fixture

pytestmark = requires_qt

@pytest.fixture(scope='module')
def qapp():
    yield QApplication.instance() or QApplication([])

def test_native_inputs_editor_uses_model_view_and_roundtrips(qapp):
    project = short_project()
    dialog = NativeInputsDialog(project)
    assert dialog.tree.model() is dialog.model
    assert dialog.data() == project.native_inputs.to_dict()
    dialog.close()

def test_native_replay_uses_frozen_producing_project_and_current_stale_flag(qapp):
    project, snapshot, payload, result = transport_fixture()
    edited = project.with_edits(bodies=(project.bodies[0].with_edits(reference_position=(80.,0.,0.)), project.bodies[1]))
    window = MainWindow()
    try:
        window._set_project(edited)
        window._set_result(result)
        assert window._replay_project.bodies[0].reference_position != edited.bodies[0].reference_position
        assert 'STALE' in window._result_status and 'native' in window._result_status
        window._apply_frame(1)
        assert window._current_frame == 1
    finally:
        window._dirty = False
        window.close()

def test_native_deadline_is_failed_not_cancelled(qapp, tmp_path):
    manager = JobManager(ApplicationService())
    manager._workroot = tmp_path
    record = JobRecord(job_id='deadline-test', state=JOB_STATE_RUNNING,
                       kind='mechanical_development', cancel_path=tmp_path/'cancel')
    manager._jobs[record.job_id] = record
    manager._native_deadline(record)
    manager._on_finished(record, 4, QProcess.ExitStatus.NormalExit)
    assert record.state == JOB_STATE_FAILED

def test_actual_native_qprocess_roundtrip(qapp, tmp_path):
    service = ApplicationService()
    manager = JobManager(service)
    manager._workroot = tmp_path
    received = []
    manager.result_ready.connect(lambda jid, result: received.append(result))
    job_id = manager.submit_native(short_project())
    deadline = time.monotonic() + 8.
    while manager.active_jobs() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)
    if manager.active_jobs():
        manager.cancel_all()
        cleanup = time.monotonic() + 6.
        while manager.active_jobs() and time.monotonic() < cleanup:
            qapp.processEvents()
            time.sleep(.01)
    assert manager.job(job_id).state == 'completed'
    assert len(received) == 1 and received[0].frame_count() == 3


def test_unsaved_native_result_keeps_save_prompt_required(qapp):
    project, snapshot, payload, result = transport_fixture()
    window = MainWindow()
    try:
        window._set_project(project)
        window._project_root = None
        window._on_job_result('native-transport-fixture', result)
        assert window._dirty is True
        assert window._project.results[-1].result_id == result.result_id
    finally:
        window._dirty = False
        window.close()


def test_native_initial_state_edits_refresh_preview_when_no_result(qapp, monkeypatch):
    from anymarine.native_inputs import NativeMechanicalInputs
    import numpy as np
    project = short_project()
    window = MainWindow()
    try:
        window._set_project(project)
        observed = {}
        for bid, handle in window._handles.items():
            original = handle.set_transform
            def capture(matrix, bid=bid, original=original):
                observed[bid] = np.array(matrix, copy=True)
                original(matrix)
            from types import SimpleNamespace
            window._handles[bid] = SimpleNamespace(set_transform=capture)
        data = project.native_inputs.to_dict()
        bid = project.bodies[0].body_id
        data['initial_states'][bid]['position'][0] += 2.
        edited = project.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data))
        window._mark_edited(edited)
        assert observed[bid][0,3] == data['initial_states'][bid]['position'][0]
    finally:
        window._dirty = False
        window.close()



def test_save_then_native_result_uses_newly_adopted_folder(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    project, snapshot, payload, result = transport_fixture()
    root = tmp_path / 'newly-saved'
    window = MainWindow()
    try:
        window._set_project(project)
        window._dirty = True
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(root/'project.json'), ''))
        window._save_project()
        assert window._project_root == root and window._service.root == root
        assert not window._dirty
        window._on_job_result('fabricated-native-transport', result)
        saved = ApplicationService().load(root)
        assert saved.results[-1].result_id == result.result_id
        assert not window._dirty
    finally:
        window._dirty = False
        window.close()


def test_failed_first_save_preserves_unsaved_state(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from anymarine.persistence import ProjectSaveError
    window = MainWindow()
    try:
        window._set_project(short_project())
        window._dirty = True
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(tmp_path/'failed'/'project.json'), ''))
        def fail_save(*args):
            raise ProjectSaveError('simulated save refusal')
        messages = []
        monkeypatch.setattr(window._service, 'save_current', fail_save)
        monkeypatch.setattr(window, '_notify_critical', lambda title, text: messages.append(text))
        window._save_project()
        assert window._project_root is None and window._service.root is None
        assert window._dirty and messages == ['simulated save refusal']
    finally:
        window._dirty = False
        window.close()


def test_rejected_load_keeps_gui_project_and_service_folder_consistent(qapp, tmp_path, monkeypatch):
    from anymarine.services import ServiceError
    project = short_project()
    active_root = tmp_path / 'active'
    candidate_root = tmp_path / 'candidate'
    window = MainWindow()
    try:
        window._service.save_current(project, active_root)
        window._project_root = active_root
        window._set_project(project)
        original_store = window._service.store
        window._service.save(project.with_edits(name='Rejected candidate'), candidate_root)
        def refuse_candidate(candidate):
            assert window._service.root == candidate_root
            raise ServiceError('simulated provenance refusal; no source execution')
        messages = []
        monkeypatch.setattr(window._service, 'verify_hydro_provenance', refuse_candidate)
        monkeypatch.setattr(window, '_notify_critical', lambda title, text: messages.append(text))
        window.load_project(str(candidate_root))
        assert window._project is project
        assert window._project_root == active_root and window._service.root == active_root
        assert window._service.store is original_store
        assert messages == ['simulated provenance refusal; no source execution']
    finally:
        window._dirty = False
        window.close()


def _wait_native_jobs(qapp, manager):
    deadline = time.monotonic() + 8.
    while manager.active_jobs() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)
    if manager.active_jobs():
        manager.cancel_all()
        cleanup = time.monotonic() + 6.
        while manager.active_jobs() and time.monotonic() < cleanup:
            qapp.processEvents()
            time.sleep(.01)
    assert not manager.active_jobs(), 'owned worker did not reach terminal state'


def test_native_failed_start_keeps_logs_and_advances_queue(qapp, tmp_path, monkeypatch):
    manager = JobManager(ApplicationService())
    manager._workroot = tmp_path
    original = QProcess.setProgram
    calls = []
    def first_missing_program(process, program):
        calls.append(program)
        original(process, str(tmp_path/'missing-worker.exe') if len(calls) == 1 else program)
    monkeypatch.setattr(QProcess, 'setProgram', first_missing_program)
    received = []
    manager.result_ready.connect(lambda jid, result: received.append(jid))
    first = manager.submit_native(short_project())
    second = manager.submit_native(short_project())
    _wait_native_jobs(qapp, manager)
    assert manager.job(first).state == 'failed'
    assert manager.job(second).state == 'completed' and received == [second]
    for jid in (first, second):
        record = manager.job(jid)
        assert record.input_path.is_file()
        assert record.stdout_path.is_file() and record.stderr_path.is_file()


def test_native_owned_worker_crash_is_failed_without_result(qapp, tmp_path, monkeypatch):
    # QProcess may emit started inside start(): connect before submission.
    original_start = QProcess.start
    def start_then_kill(process, *args):
        process.started.connect(process.kill)
        original_start(process, *args)
    monkeypatch.setattr(QProcess, 'start', start_then_kill)
    manager = JobManager(ApplicationService())
    manager._workroot = tmp_path
    received = []
    manager.result_ready.connect(lambda jid, result: received.append(result))
    jid = manager.submit_native(short_project())
    record = manager.job(jid)
    _wait_native_jobs(qapp, manager)
    assert record.state == 'failed' and not received
    assert record.stdout_path.is_file() and record.stderr_path.is_file()
    assert any('crashed' in message for message in record.diagnostics)


def test_native_active_window_close_cancels_owned_worker(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window = MainWindow()
    manager = window._job_manager
    manager._workroot = tmp_path
    try:
        window._set_project(short_project())
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
        jid = manager.submit_native(window._project)
        assert jid in manager.active_jobs()
        assert window.close()
        record = manager.job(jid)
        assert not manager.active_jobs() and record.state == 'cancelled'
        assert not window._project.results
        assert record.stdout_path.is_file() and record.stderr_path.is_file()
    finally:
        if manager.active_jobs():
            manager.cancel_all()
            _wait_native_jobs(qapp, manager)
        window._dirty = False
        window.close()


def test_native_jobs_menu_remains_available_after_wrapper_collection(qapp):
    import gc
    window = MainWindow()
    try:
        jobs = next(action.menu() for action in window.menuBar().actions()
                    if action.text().replace('&','') == 'Jobs')
        gc.collect()
        assert any(action.text() == 'Run native mechanical job...' for action in jobs.actions())
        assert len(window._menus) == 5
    finally:
        window._dirty=False
        window.close()
