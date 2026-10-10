"""Application policy transport and fail-closed persistence; NO BEM."""
from dataclasses import replace
import json
import numpy as np
import pytest
from anymarine.domain import Body, ProjectModel
from anymarine.hydro import expected_world_lid, _prove_source_geometry, HydroLidGeometryError
from anymarine.persistence import ProjectStore, ProjectLoadError
from anymarine.w5_workflow import _project
from anyhydro.source_study_case import (SourceStudyCase, make_prospective_source_study_case,
    build_study_bodies, build_study_request,FIRST_SLICE_SUBMERGED_LID_GEOMETRY_RECIPE)
from anyhydro.schema import assemble_hydro_dataset
def case():
    d=make_prospective_source_study_case().to_dict()
    d.update(geometry_recipe=FIRST_SLICE_SUBMERGED_LID_GEOMETRY_RECIPE,levels=[2,3],omegas=[1.5,4.,8.])
    return SourceStudyCase.from_dict(d)
def dataset(c):
    bodies=build_study_bodies(c,2);r=build_study_request(c);n=12;w=len(r.omegas)
    return assemble_hydro_dataset(bodies,r,added_mass=np.zeros((w,n,n)),radiation_damping=np.zeros((w,n,n)),
        excitation_force=np.zeros((w,1,n),complex),hydrostatic_stiffness=np.zeros((n,n)),
        rotation_center=[b.rotation_center for b in bodies],center_of_mass=[b.center_of_mass for b in bodies],
        center_of_buoyancy=[b.center_of_mass for b in bodies],backend="stub",backend_version="0",
        qualification_status="development_fixture")
def test_w5_project_geometry_policy_and_save_reopen(tmp_path):
    c=case();p=_project(c,2);ds=dataset(c)
    _prove_source_geometry(__import__('anyhydro'),p,ds)
    for b,h in zip(p.bodies,build_study_bodies(c,2)):
        assert b.hydro_lid_policy=="submerged_0p1m_v1"
        assert expected_world_lid(b).mesh_hash==h.lid_mesh.mesh_hash
        assert Body.from_dict(b.to_dict())==b
    store=ProjectStore(tmp_path/'project');store.save(p);saved=store.load()
    assert saved.to_dict()==p.to_dict()
    manifest=json.loads((tmp_path/'project/project.json').read_text());assert manifest['schema_version']==8
    manifest['schema_version']=4
    (tmp_path/'project/project.json').write_text(json.dumps(manifest))
    with pytest.raises(ProjectLoadError):store.load()
def test_old_project_serialization_and_schema_unchanged(tmp_path):
    d=case().to_dict();d['geometry_recipe']="first-slice-two-box-wetted-hull-lid-v1"
    p=_project(SourceStudyCase.from_dict(d),2)
    assert all('hydro_lid_policy' not in b.to_dict() for b in p.bodies)
    store=ProjectStore(tmp_path/'old');store.save(p)
    assert json.loads((tmp_path/'old/project.json').read_text())['schema_version']==2
    assert store.load().to_dict()==p.to_dict()
@pytest.mark.parametrize('policy',[None,'unknown',3])
def test_unknown_body_policy_is_not_dropped(policy):
    b=_project(case(),2).bodies[0];d=b.to_dict();d['hydro_lid_policy']=policy
    with pytest.raises((ValueError,TypeError)):Body.from_dict(d)
def test_missing_or_mismatched_policy_refuses_dataset():
    p=_project(case(),2);ds=dataset(case())
    ds.attrs.pop('auxiliary_lid_policy')
    with pytest.raises(HydroLidGeometryError):_prove_source_geometry(__import__('anyhydro'),p,ds)
    old=replace(p,bodies=tuple(b.with_edits(hydro_lid_policy='free_surface_v1') for b in p.bodies))
    assert old.to_dict()!=p.to_dict()
    with pytest.raises(HydroLidGeometryError):expected_world_lid(old.bodies[0])
def test_wrong_plane_and_no_lid_refuse():
    p=_project(case(),2);b=p.bodies[0]
    moved=replace(b.hydro_lid,positions=tuple((x,y,z+.001) for x,y,z in b.hydro_lid.positions))
    with pytest.raises(HydroLidGeometryError):expected_world_lid(b.with_edits(hydro_lid=moved))
    with pytest.raises(ValueError):b.with_edits(hydro_lid=None)

def test_policy_only_edit_stales_project_and_linear_binding(tmp_path):
    from anymarine.hashing import input_hash, is_stale
    from anymarine.linear import analytic_demo_inputs, verify_linear_binding, LinearBindingError, linear_input_hash
    p=_project(case(),2);inputs=analytic_demo_inputs(p)
    producing=input_hash(p)
    old=replace(p,bodies=tuple(b.with_edits(hydro_lid_policy='free_surface_v1') for b in p.bodies))
    assert input_hash(old)!=producing and is_stale(old,producing)
    assert inputs.bindings[0].to_dict()['hydro_lid_policy']=='submerged_0p1m_v1'
    with pytest.raises(LinearBindingError,match='policy'):verify_linear_binding(old,inputs)
    retained=replace(old,linear_inputs=inputs)
    store=ProjectStore(tmp_path/'retained');store.save(retained)
    assert json.loads((tmp_path/'retained/project.json').read_text())['schema_version']==8
    assert store.load().to_dict()==retained.to_dict()
def test_lid_outside_declared_footprint_refuses():
    b=_project(case(),2).bodies[0]
    moved=replace(b.hydro_lid,positions=tuple((x+10,y,z) for x,y,z in b.hydro_lid.positions))
    with pytest.raises(HydroLidGeometryError,match='footprint'):expected_world_lid(b.with_edits(hydro_lid=moved))

def test_public_hydro_import_save_reopen_and_result_only_retention(tmp_path):
    import anyhydro as h
    from anymarine.services import ApplicationService
    from anymarine.linear import run_linear, verify_linear_binding, LinearBindingError
    from anymarine.surface_assignments import project_from_surface_snapshot
    c=case();p=_project(c,2);ds=dataset(c)
    # A constant positive mass with exactly zero damping has an exact zero
    # memory kernel. This is an analytic interface fixture, not BEM evidence.
    ds['added_mass'].values[:]=100.*np.eye(12)
    ds['added_mass_infinite']=(('influenced_dof','radiating_dof'),100.*np.eye(12))
    ds['added_mass_infinite'].attrs['source']='analytic-constant-fixture'
    kernel=h.radiation_kernel_from_dataset(ds,h.RadiationSettings(dt=.05,n_time=65,symmetry_tol=1e-10,passivity_tol=1e-10,
        low_tail_policy='explicit_zero_sample',zero_frequency_damping=np.zeros((12,12))))
    dataset_path=tmp_path/'dataset.nc';kernel_path=tmp_path/'kernel.npz'
    h.save_hydro_dataset(ds,dataset_path);h.save_radiation_kernel(kernel,kernel_path)
    service=ApplicationService(tmp_path/'public');service.save(p)
    imported=service.import_hydro_inputs(p,dataset_path,kernel_path,dict(n_steps=3,
        criteria=dict(omega_min=1.5,omega_max=8.,added_mass_abs_tol=1e-10,added_mass_rel_tol=1e-10,
                      damping_abs_tol=1e-10,damping_rel_tol=1e-10)))
    assert imported.hydro_provenance.auxiliary_lid_policy=='submerged_0p1m_v1'
    assert 'z=-0.1' in imported.linear_inputs.provenance.description
    service.save(imported);reopened=service.load(tmp_path/"public")
    assert reopened.to_dict()==imported.to_dict()
    service.verify_hydro_provenance(reopened)
    result=run_linear(reopened.linear_inputs)
    assert result.surface_snapshot is not None
    producing=project_from_surface_snapshot(result.surface_snapshot)
    assert all(b.hydro_lid_policy=='submerged_0p1m_v1' for b in producing.bodies)
    retained=ProjectModel('Retained result only',bodies=(),results=(result,))
    store=ProjectStore(tmp_path/'retained-result');store.save(retained)
    assert json.loads((tmp_path/'retained-result/project.json').read_text())['schema_version']==8
    assert store.load().to_dict()==retained.to_dict()
    old=replace(reopened,bodies=tuple(b.with_edits(hydro_lid_policy='free_surface_v1') for b in reopened.bodies))
    with pytest.raises(LinearBindingError,match='policy'):verify_linear_binding(old,reopened.linear_inputs)
