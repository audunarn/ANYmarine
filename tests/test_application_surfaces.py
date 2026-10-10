"""Surface transport/admission tests. Coefficients are analytic test fixtures,
not computed plate hydrodynamics and never extension qualification evidence.
"""
from dataclasses import replace
import json
from types import SimpleNamespace
import numpy as np
import pytest
import anyhydro as h
from anymarine.domain import Body, BodyMesh, ResultRecord
from anymarine.hashing import input_hash
from anymarine.hydro import expected_surface_inventory, import_hydro_inputs, import_derived_hydro_inputs, HydroGeometryError
from anymarine.linear import analytic_demo_inputs, verify_linear_binding, linear_result_stale, LinearBindingError
from anymarine.persistence import ProjectStore, ProjectLoadError
from anymarine.services import ApplicationService
from anymarine.plate_example import attached_plate_project
from anymarine.surface_assignments import HydroSurfaceAssignment, body_surface_hash, display_body_mesh, migrate_legacy_surface, surface_result_snapshot, project_from_surface_snapshot
from test_hydro_bridge import _fixture_dataset, _fixture_kernel, _base_options

def test_geometry_example_ownership_and_legacy_readability():
    p=attached_plate_project()
    assert len(p.bodies)==2 and p.linear_inputs is None and p.native_inputs is None
    assert all(len(b.hydro_surfaces)==2 for b in p.bodies)
    for b in p.bodies:
        assert Body.from_dict(b.to_dict())==b
        assert len(display_body_mesh(b).triangles)==len(b.mesh.triangles)+2
        legacy=b.with_edits(hydro_surfaces=())
        assert 'hydro_surfaces' not in legacy.to_dict()
        assert legacy.mass==b.mass
        assert all(s.semantics.owning_body_id==b.body_id for s in b.hydro_surfaces)

@pytest.mark.parametrize('field,value',[('mass',1.),('displaced_volume',1.),('morison_inertia',1.)])
def test_surface_cannot_acquire_mechanical_authority(field,value):
    record=attached_plate_project().bodies[0].hydro_surfaces[-1].to_dict()
    record['semantics'][field]=value
    with pytest.raises(ValueError):HydroSurfaceAssignment.from_dict(record)

def test_explicit_migration_and_ambiguous_refusal():
    b=attached_plate_project().bodies[0].with_edits(hydro_surfaces=())
    out=migrate_legacy_surface(b,role='hull',legacy_role='bem',surface_id='h',source_geometry_id='g')
    assert out.body_id==b.body_id and out.mesh==b.mesh and not b.hydro_surfaces
    with pytest.raises(ValueError,match='Ambiguous'):
        migrate_legacy_surface(b,role='hull',legacy_role='drag_only',surface_id='h',source_geometry_id='g')

def test_wrong_owner_and_closed_or_coincident_sheet_refused():
    b=attached_plate_project().bodies[0];s=b.hydro_surfaces[-1]
    with pytest.raises(ValueError,match='owning_body_id'):
        b.with_edits(hydro_surfaces=(replace(s,semantics=replace(s.semantics,owning_body_id='other')),))
    mesh=BodyMesh(s.mesh.positions,((0,1,2),(2,1,0)))
    with pytest.raises(ValueError,match='coincident'):
        replace(s,mesh=mesh)

def test_duplicate_ids_and_waterline_refused():
    p=attached_plate_project();b=p.bodies[0]
    with pytest.raises(ValueError,match='duplicate'):
        b.with_edits(hydro_surfaces=b.hydro_surfaces+(b.hydro_surfaces[-1],))
    with pytest.raises(ValueError,match='fully submerged'):
        p.with_edits(bodies=(b.with_edits(reference_position=(0.,0.,100.)),p.bodies[1]))

def test_save_reopen_and_manifest_downgrade(tmp_path):
    p=attached_plate_project();store=ProjectStore(tmp_path/'project');store.save(p)
    path=store.root/'project.json';manifest=json.loads(path.read_text())
    assert manifest['schema_version']==7
    loaded=store.load();assert loaded.to_dict()==p.to_dict() and input_hash(loaded)==input_hash(p)
    manifest['schema_version']=2;path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectLoadError,match='downgrade'):store.load()

@pytest.fixture(scope='module')
def admitted(tmp_path_factory):
    p=attached_plate_project();ds=_fixture_dataset(p)
    ds.attrs.update(schema_version='1.2.0',surface_identity=expected_surface_inventory(p),
        sheet_source_provenance=dict(backend='transport-fixture',version='test',source_commit='test',
        capability_evidence_sha256='1'*64,coupling='full_hull_sheet_and_cross_body',
        pressure_jump_convention='p_minus-p_plus;normal_minus_to_plus',source_capability_status='verified'))
    # The declaration tests serialization only; qualification_status stays development_fixture.
    ds=h.validate_hydro_dataset(ds);assert ds.attrs['qualification_status']=='development_fixture'
    kernel=_fixture_kernel(ds);root=tmp_path_factory.mktemp('surface-coefficients')
    dataset=root/'dataset.nc';memory=root/'kernel.nc'
    h.save_hydro_dataset(ds,dataset);h.save_radiation_kernel(kernel,memory)
    service=ApplicationService(root/'project');service.save(p)
    imported=service.import_hydro_inputs(p,dataset,memory,_base_options(n_steps=2))
    return imported,dataset,memory

def test_public_coefficient_only_import_binds_surface_geometry(admitted,tmp_path):
    p,dataset,_=admitted
    assert all(b.surface_hash is not None for b in p.linear_inputs.bindings)
    verify_linear_binding(p,p.linear_inputs)
    target=ApplicationService(dataset.parent/'project').save_current(p,tmp_path/'project')
    store=ProjectStore(target)
    assert store.load().to_dict()==p.to_dict()
    assert p.linear_inputs.surface_geometry is not None

@pytest.mark.parametrize('edit',['remove','source','sides','mesh'])
def test_surface_edits_are_stale_and_keep_schema_barrier(admitted,tmp_path,edit):
    p,dataset,_=admitted;b=p.bodies[0];s=b.hydro_surfaces[-1]
    if edit=='remove': bodies=tuple(b.with_edits(hydro_surfaces=()) for b in p.bodies)
    else:
        if edit=='source':s=replace(s,semantics=replace(s.semantics,source_geometry_id='edited-source'))
        if edit=='sides':s=replace(s,semantics=replace(s.semantics,minus_side='changed-side'))
        if edit=='mesh':s=replace(s,mesh=BodyMesh(tuple((x+1.,y,z) for x,y,z in s.mesh.positions),s.mesh.triangles))
        bodies=(b.with_edits(hydro_surfaces=(b.hydro_surfaces[0],s)),p.bodies[1])
    edited=p.with_edits(bodies=bodies)
    assert input_hash(edited)!=input_hash(p)
    with pytest.raises(LinearBindingError,match='surface'):verify_linear_binding(edited,p.linear_inputs)
    target=ApplicationService(dataset.parent/'project').save_current(edited,tmp_path/edit)
    store=ProjectStore(target)
    assert json.loads((store.root/'project.json').read_text())['schema_version']==7
    assert store.load().to_dict()==edited.to_dict()

@pytest.mark.parametrize('route',['raw','derived'])
def test_exact_surface_refusal_before_dependency_decode(admitted,tmp_path,route):
    p,dataset,_=admitted;before=input_hash(p)
    wrong=p.with_edits(bodies=tuple(b.with_edits(hydro_surfaces=()) for b in p.bodies),linear_inputs=None,hydro_provenance=None)
    kernel=tmp_path/'bad-kernel.nc';kernel.write_bytes(b'must not decode')
    ops=tmp_path/'bad-operators.npz';ops.write_bytes(b'must not decode')
    with pytest.raises(HydroGeometryError,match='surface inventory'):
        if route=='raw':import_hydro_inputs(wrong,dataset,kernel,_base_options(n_steps=2))
        else:import_derived_hydro_inputs(wrong,dataset,ops,kernel,_base_options(n_steps=2))
    assert input_hash(p)==before

def test_unimplemented_analytic_and_native_wave_paths_refuse():
    p=attached_plate_project()
    with pytest.raises(LinearBindingError,match='hull-only'):analytic_demo_inputs(p)
    from anymarine.wave_jobs import preview_request
    with pytest.raises(ValueError,match='plates cannot be omitted'):preview_request(p,{})

def fixture_result(p,monkeypatch):
    """Exercise the production result builder with a stationary test response;
    the fake backend performs no numerical solve and proves transport only."""
    import anymarine.linear as linear
    inputs=p.linear_inputs;zero=np.zeros((inputs.n_steps,4))
    response=SimpleNamespace(complete=True,cancelled=False,times=inputs.times,labels=inputs.labels,
                            displacements=zero,velocities=zero,accelerations=zero)
    for field in ('external_forces','dry_inertia_forces','added_inertia_forces','memory_forces',
                  'damping_forces','stiffness_forces','residual_forces'):setattr(response,field,zero)
    fake=SimpleNamespace(MarineLinearSpec=lambda **kw:SimpleNamespace(**kw),solve_marine_linear=lambda *a,**kw:response)
    monkeypatch.setattr(linear,'_import_solver',lambda:fake)
    return linear.run_linear(inputs)

def test_production_result_retains_and_validates_producing_geometry(admitted,monkeypatch,tmp_path):
    p,_,_=admitted;r=fixture_result(p,monkeypatch)
    source=project_from_surface_snapshot(r.surface_snapshot)
    assert tuple(b.to_dict() for b in source.bodies)==tuple(b.to_dict() for b in p.bodies)
    assert ResultRecord.from_dict(r.to_dict()).to_dict()==r.to_dict()
    damaged=r.to_dict();damaged['surface_snapshot']['linear_inputs']['surface_geometry']['bodies'][0]['hydro_surfaces'][-1]['semantics']['source_geometry_id']='tampered'
    with pytest.raises(ValueError):ResultRecord.from_dict(damaged)
    edited=p.with_edits(bodies=tuple(b.with_edits(hydro_surfaces=()) for b in p.bodies),linear_inputs=None,hydro_provenance=None,results=(r,))
    assert linear_result_stale(edited,r.input_hash)
    store=ProjectStore(tmp_path/'stale');store.save(edited)
    assert json.loads((store.root/'project.json').read_text())['schema_version']==7
    assert store.load().results[0].to_dict()==r.to_dict()

def test_whole_surface_snapshot_stripping_is_refused_contextually(admitted,monkeypatch):
    p,_,_=admitted;r=fixture_result(p,monkeypatch)
    stripped=r.to_dict();stripped.pop('surface_snapshot')
    legacy_shape=ResultRecord.from_dict(stripped)
    with pytest.raises(ValueError,match='stripped intent'):
        p.with_edits(results=(legacy_shape,))

def test_public_worker_output_decode_refuses_missing_surface_snapshot(admitted,monkeypatch,tmp_path):
    from anymarine.services import ServiceError
    p,dataset,_=admitted;r=fixture_result(p,monkeypatch)
    service=ApplicationService(dataset.parent/'project')
    snapshot=service.linear_job_snapshot(p)
    payload=r.to_dict();payload.pop('surface_snapshot')
    path=tmp_path/'stripped-output.json';path.write_text(json.dumps(payload))
    with pytest.raises(ServiceError,match='producing surface geometry'):
        service.load_job_output(path,r.input_hash,snapshot=snapshot)
