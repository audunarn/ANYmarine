"""Qt production scene integration; no solver job or BEM run."""
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from anymarine.qtapp.mainwindow import MainWindow
from anymarine.surface_assignments import display_body_mesh
from anymarine.plate_example import attached_plate_project
from test_application_surfaces import admitted,fixture_result

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

@pytest.fixture
def window(app,monkeypatch):
    w=MainWindow();monkeypatch.setattr(w,'_confirm_discard',lambda:True)
    yield w
    w._dirty=False;w.close();app.processEvents()

def test_file_menu_loads_owned_plates_and_selects_owner(window,app):
    window.load_plate_example();p=window._project
    assert len(window._handles)==2
    owner=window._tree_model.index(0,0,window._tree_model.index(0,0))
    sheet=window._tree_model.index(1,0,owner)
    window._on_tree_clicked(sheet)
    assert window.viewer.highlighted_tags()==frozenset([p.bodies[0].body_id])
    assert 'attached surfaces' in window.statusBar().currentMessage()

def test_surface_reference_edit_refreshes_preview(window):
    p=attached_plate_project();window._set_project(p)
    old=window._handles[p.bodies[0].body_id]
    b=p.bodies[0].with_edits(reference_position=(-20.,0.,0.))
    window._mark_edited(p.with_edits(bodies=(b,p.bodies[1])))
    assert old.removed
    assert np.array_equal(window._handles[b.body_id].transform[:3,3],b.reference_position)

def test_stale_sheet_replay_and_renderer_replacement_use_producer(window,admitted,monkeypatch):
    p,_,_=admitted;r=fixture_result(p,monkeypatch)
    edited=p.with_edits(bodies=tuple(b.with_edits(hydro_surfaces=()) for b in p.bodies),linear_inputs=None,hydro_provenance=None,results=(r,))
    window._set_project(edited);window._set_result(r);window._apply_frame(1)
    assert window._replay_project is not None
    for b in p.bodies:
        assert len(display_body_mesh(window._replay_project.body(b.body_id)).triangles)==len(b.mesh.triangles)+2
        assert np.array_equal(window._handles[b.body_id].transform[:3,3],r.positions[b.body_id][1])
    window._change_renderer('software')
    assert all(len(b.hydro_surfaces)==2 for b in window._replay_project.bodies)
    window._set_result(None)
    assert window._replay_project is None
    assert all(not b.hydro_surfaces for b in window._project.bodies)

def test_plate_scene_uses_public_two_sided_options(window,monkeypatch):
    calls=[];original=window.viewer.add_mesh_arrays
    def capture(mesh,**kwargs):
        calls.append((len(mesh.triangles),kwargs))
        return original(mesh,**kwargs)
    monkeypatch.setattr(window.viewer,'add_mesh_arrays',capture)
    window.load_plate_example()
    assert len(calls)==2
    assert all(count==14 and kw['two_sided_shell'] and not kw['cull_backface'] for count,kw in calls)

def test_legacy_linear_replay_after_plate_addition_is_refused(window,admitted,monkeypatch):
    from anymarine.domain import ResultRecord
    p,_,_=admitted;r=fixture_result(p,monkeypatch)
    payload=r.to_dict();payload.pop('surface_snapshot');payload['input_hash']='a'*64
    legacy=ResultRecord.from_dict(payload)
    project=p.with_edits(results=(legacy,))
    warnings=[];monkeypatch.setattr(window,'_notify_warning',lambda *args:warnings.append(args))
    window._set_project(project);before=window._handles.copy()
    window._set_result(legacy)
    assert warnings and window._current_result is None and window._handles==before

def test_snapshotless_guard_uses_source_after_detached_reset(window,admitted,monkeypatch):
    from anymarine.domain import ResultRecord
    p,_,_=admitted;r=fixture_result(p,monkeypatch)
    payload=r.to_dict();payload.pop('surface_snapshot');payload['input_hash']='b'*64
    legacy=ResultRecord.from_dict(payload)
    window._set_project(p.with_edits(results=(legacy,)))
    window._replay_project=p.with_edits(bodies=tuple(b.with_edits(hydro_surfaces=()) for b in p.bodies),
                                       linear_inputs=None,hydro_provenance=None)
    warnings=[];monkeypatch.setattr(window,'_notify_warning',lambda *args:warnings.append(args))
    window._set_result(legacy)
    assert warnings and window._current_result is None
