# SPDX-License-Identifier: MPL-2.0
from dataclasses import replace
from pathlib import Path
import hashlib,json,math,shutil,sys
import numpy as np
import pytest
from anymarine.hams_wave_replay import load_hams_wave_example,load_hams_wave_bundle,save_hams_wave_replay,bind_hams_wave_replay
from anymarine.services import ApplicationService,ServiceError

@pytest.fixture
def replay():return load_hams_wave_example()

def test_surface_incident_total_scattered_and_radiation_use_authoritative_arrays(replay):
    r=replay;f=r.fields;indices=list(r.surface_indices);amp=.4;phase=.6;t=.8
    assert r.source_kind=='hams_canonical_point_fields_v1' and len(indices)==4 and not r.project.results
    for mode in ('incident','total','scattered'):
        expected=amp*getattr(f,mode)[0,0,indices]*np.exp(1j*(phase-.7*t))
        np.testing.assert_allclose(r.positions(mode,t,amplitude=amp,phase=phase)[:,2],expected.real,rtol=0,atol=1e-15)
    np.testing.assert_allclose(r.response('total',amplitude=amp),r.response('incident',amplitude=amp)+r.response('scattered',amplitude=amp),atol=1e-15)
    for dof in (0,5,7,11):
        expected=f.radiation[0,indices,dof]*.03*np.exp(1j*(phase-.7*t))
        np.testing.assert_allclose(r.positions('radiation',t,dof=dof,displacement=.03,phase=phase)[:,2],expected.real,atol=1e-15)
    assert np.array_equal(r.vertices[:,2],np.zeros(4))
    with pytest.raises(ValueError):f.total.setflags(write=True)

@pytest.mark.parametrize('args',[{'component':'unknown'},{'component':'incident','frequency':True},{'component':'incident','frequency':9},{'component':'incident','heading':1},{'component':'radiation','dof':12},{'component':'incident','amplitude':-1},{'component':'incident','phase':float('nan')}])
def test_invalid_coordinate_units_and_nonfinite_refused(replay,args):
    with pytest.raises(ValueError):replay.response(**args)

def test_portable_save_reopen_and_detached_service(replay,tmp_path,monkeypatch):
    monkeypatch.setitem(sys.modules,'capytaine',None);monkeypatch.setitem(sys.modules,'anysolver',None)
    service=ApplicationService(tmp_path/'previous');project,record=service.load_hams_wave_bundle()
    assert service.root is None and service.store is None and project.to_dict()==replay.project.to_dict()
    saved=service.save_hams_wave_bundle(tmp_path/'saved',record);assert saved.fields.content_hash()==record.fields.content_hash()
    fresh=load_hams_wave_bundle(tmp_path/'saved');assert fresh.project_hash==record.project_hash
    with pytest.raises(ServiceError):service.save_hams_wave_bundle(tmp_path/'saved',record)

def test_cli_shared_service_wave_sampling_and_refusal(replay,tmp_path,capsys):
    from anymarine.cli import main
    assert main(['inspect-wave-bundle','--component','radiation','--time','.7','--dof-index','5','--displacement','.02','--save-copy',str(tmp_path/'copy')])==0
    data=json.loads(capsys.readouterr().out);expected=replay.positions('radiation',.7,dof=5,displacement=.02)
    assert data['source_kind']==replay.source_kind and data['surface_samples']==4 and data['numerical_calculation'] is False
    assert data['elevation_max_m']==pytest.approx(expected[:,2].max())
    assert main(['inspect-wave-bundle','--path',str(tmp_path/'copy'),'--frequency-index','9'])==1
    assert 'coordinate index' in capsys.readouterr().err

@pytest.mark.parametrize('failure',['missing','bytes','kind','parent','project','frames','pressure-height','extra'])
def test_bundle_refusals_preserve_current_service_atomically(replay,tmp_path,failure):
    folder=tmp_path/'saved';save_hams_wave_replay(folder,replay)
    m=json.loads((folder/'bundle.json').read_text())
    if failure=='missing':(folder/'fields.json').unlink()
    elif failure=='bytes':(folder/'fields.json').write_bytes((folder/'fields.json').read_bytes()+b' ')
    elif failure=='kind':m['source_kind']='capytaine_v1'
    elif failure=='parent':m['parent_hash']='0'*64
    elif failure=='extra':(folder/'unknown.json').write_text('{}')
    elif failure=='project':
        p=json.loads((folder/'project.json').read_text());p['bodies'][0]['mesh']['positions'][0][0]+=.1
        (folder/'project.json').write_text(json.dumps(p));m['files']['project.json']=hashlib.sha256((folder/'project.json').read_bytes()).hexdigest()
    else:
        from anyhydro.hams_fields import load_hams_fields,save_hams_fields
        f=load_hams_fields(folder/'fields.json')
        if failure=='frames':
            meta=json.loads((folder/'fields.json').read_text())['payload']['metadata'];meta['target_frames'][meta['target_body_order'][0]]['reference_center'][0]+=.1;f=replace(f,metadata=meta)
        else:
            # Removing elevation points must never reinterpret pressure as height.
            f=replace(f,points=f.points[4:],incident=f.incident[:,:,4:],total=f.total[:,:,4:],radiation=f.radiation[:,4:,:])
        (folder/'fields.json').unlink();save_hams_fields(f,folder/'fields.json');m['files']['fields.json']=hashlib.sha256((folder/'fields.json').read_bytes()).hexdigest();m['field_hash']=f.content_hash()
    (folder/'bundle.json').write_text(json.dumps(m))
    service=ApplicationService(tmp_path/'previous');root,store=service.root,service.store
    with pytest.raises(ServiceError):service.load_hams_wave_bundle(folder)
    assert service.root==root and service.store is store

def test_source_geometry_identity_and_mutated_parent_refuse(replay,tmp_path):
    r=replay;p=r.project
    changed=p.with_edits(bodies=(replace(p.bodies[0],reference_position=(99.,0.,0.)),*p.bodies[1:]))
    assert not r.matches(changed)
    with pytest.raises(ValueError):bind_hams_wave_replay(changed,r._dataset,r.fields)
    r._dataset.added_mass.values[0,0,0]+=1.
    with pytest.raises(ValueError):save_hams_wave_replay(tmp_path/'bad',r)
    assert not (tmp_path/'bad').exists()

@pytest.mark.parametrize('key',['source_profile','executable_sha256','input_sha256','request_hash','raw_field_sha256','reference_length_m','source_frames'])
def test_inconsistent_source_declarations_refuse_even_rebound_parent(replay,key):
    r=replay;meta=json.loads(json.dumps(r.fields.payload(),default=lambda v:dict(v)))['metadata']
    if key=='source_profile':
        ds=r._dataset.copy(deep=True);p=ds.attrs['wamit_import'];p=json.loads(p) if isinstance(p,str) else dict(p);p['profile']='another-source';ds.attrs['wamit_import']=p
        from anyhydro.hashing import dataset_content_hash
        f=replace(r.fields,parent_dataset_hash=dataset_content_hash(ds))
    else:
        ds=r._dataset
        if key=='raw_field_sha256':meta[key]['combined']='0'*64
        elif key=='reference_length_m':meta[key]=2.
        elif key=='source_frames':meta[key][meta['source_body_order'][0]]['reference_center'][0]+=.1
        else:meta[key]='0'*64
        f=replace(r.fields,metadata=meta)
    with pytest.raises(ValueError,match='source declarations'):bind_hams_wave_replay(r.project,ds,f)

def test_constant_rotated_shifted_frames_preserve_world_samples(replay,tmp_path):
    from anyhydro.frames import reframe_dataset
    from anyhydro.hams_fields import reframe_hams_fields
    r=replay;ids=[b.body_id for b in r.project.bodies];Q=[[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]]
    axes={key:Q for key in ids};centers={b.body_id:tuple(np.asarray(b.reference_position)+[.1,-.2,.3]) for b in r.project.bodies}
    ds=reframe_dataset(r._dataset,new_axes=axes,new_reference_centers=centers)
    f=reframe_hams_fields(r.fields,r._dataset,ds,new_axes=axes,new_reference_centers=centers)
    new=bind_hams_wave_replay(r.project,ds,f);save_hams_wave_replay(tmp_path/'reframed',new)
    np.testing.assert_array_equal(new.positions('total',.3),r.positions('total',.3))
    np.testing.assert_allclose(new.response('radiation',dof=5,displacement=.02),f.radiation[0,list(new.surface_indices),5]*.02,atol=1e-15)

@pytest.mark.parametrize('entry',['root','fields.json'])
def test_python311_reparse_attributes_refuse(replay,tmp_path,monkeypatch,entry):
    from types import SimpleNamespace
    import stat
    folder=tmp_path/'saved';save_hams_wave_replay(folder,replay);original=Path.lstat
    target=folder if entry=='root' else folder/entry
    def lstat(path,*a,**k):
        info=original(path,*a,**k)
        return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400)) if path==target else info
    monkeypatch.setattr(Path,'lstat',lstat)
    with pytest.raises(ValueError,match='reparse'):load_hams_wave_bundle(folder)

def test_qt_stored_modes_scalars_renderer_save_open_and_project_edit(replay,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QFileDialog
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([]);w=MainWindow(viewer_backend='software')
    monkeypatch.setattr(w,'_notify_warning',lambda *args:pytest.fail(str(args)))
    try:
        w.show();w.load_hams_wave_bundle();app.processEvents();p=w._waves
        assert p.mode.currentIndex()==5 and p.active and p.handle is not None and p.field['source_kind']==replay.source_kind
        assert p.handle.mesh.triangles.shape==(0,3) and np.array_equal(p.handle.mesh.point_indices,np.arange(4))
        poses={bid:h.transform.copy() for bid,h in w._handles.items()}
        p.timer.stop();p.time=.8;p.amplitude.setValue(.4);p.stored_phase.setValue(30.)
        for index,mode in ((4,'incident'),(5,'total'),(6,'scattered'),(7,'radiation')):
            p.mode.setCurrentIndex(index);p.timer.stop();p.advance(step=False)
            expected=replay.positions(mode,p.time,**p._stored_selection())
            np.testing.assert_allclose(p.handle.mesh.positions,expected,atol=1e-14)
            np.testing.assert_allclose(p.handle.mesh.node_scalars,expected[:,2],atol=1e-14)
            assert p.elevation_bound()==pytest.approx(np.max(np.abs(replay.response(mode,**p._stored_selection()))))
            assert not p.compute.isEnabled()
        p.stored_dof.setCurrentIndex(5);assert '[rad]' in p.displacement_label.text()
        w._change_renderer('software');app.processEvents();p.timer.stop()
        np.testing.assert_allclose(p.handle.mesh.positions,replay.positions('radiation',p.time,**p._stored_selection()),atol=1e-14)
        for bid,h in w._handles.items():np.testing.assert_array_equal(h.transform,poses[bid])
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**k:(str(tmp_path/'saved'),'') )
        p.save_stored.click();assert (tmp_path/'saved/bundle.json').exists()
        w.load_hams_wave_bundle(tmp_path/'saved');assert w._waves.mode.currentIndex()==5
        p.mode.setCurrentIndex(0);assert p.handle is None and not p.active
        p.mode.setCurrentIndex(4);assert p.handle is not None
        p.mode.setCurrentIndex(1);assert p.compute.isEnabled() and 'Fixed captured' in p.note.text()
        # This source geometry is an open wetted hull. The legacy calculation
        # requires a closed model; do not silently add a lid to its physical mesh.
        if p.field is None:assert p.mode.currentIndex()==0 and 'closed' in p.status.text()
        else:assert p.field['kind']=='incident_only'
        w.load_hams_wave_bundle();assert p.mode.currentIndex()==5
        w._set_project(w._project.with_edits(name='edited'));assert p.field is None and not p.mode.model().item(5).isEnabled()
    finally:w._dirty=False;w._confirm_discard=lambda:True;w.close();app.processEvents()
