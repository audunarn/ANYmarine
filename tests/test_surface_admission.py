"""Existing app must not silently consume newly explicit neutral sheet intent."""
import json
from dataclasses import replace
import numpy as np
import pytest
import anyhydro as h
from anymarine.domain import Body,default_two_body_project
from anymarine.hashing import input_hash
from anymarine.hydro import import_hydro_inputs,import_derived_hydro_inputs,HydroGeometryError

def sheet_dataset():
    bodies=h.fixtures.make_fixture_bodies(); owner=bodies[0].body_id
    semantics=h.SurfaceSemantics('plate',owner,'thin_sheet','potential_flow','source',minus_side='lower',plus_side='upper')
    mesh=h.HydroPanelMesh(np.array([[0.,0.,-2.],[1.,0.,-2.],[0.,1.,-2.]]),np.array([[0,1,2]]),np.array([3]),owner,False,('source',),'',surface=semantics)
    bodies=(replace(bodies[0],attached_surfaces=(mesh,)),bodies[1])
    ds=h.fixtures.make_two_body_fixture();ds.attrs['schema_version']='1.2.0'
    ds.attrs['surface_identity']=h.surface_identity(bodies)
    ds.attrs['sheet_source_provenance']=dict(backend='schema-fixture',version='test-v1',source_commit='test',capability_evidence_sha256='1'*64,coupling='full_hull_sheet_and_cross_body',pressure_jump_convention='p_minus-p_plus;normal_minus_to_plus',source_capability_status='verified')
    return h.validate_hydro_dataset(ds)

@pytest.mark.parametrize('route',['raw','derived'])
def test_current_public_importer_refuses_sheet_before_sidecar_decode(route,tmp_path):
    ds=tmp_path/'sheet.nc';h.save_hydro_dataset(sheet_dataset(),ds)
    kernel=tmp_path/'kernel.nc';kernel.write_bytes(b'must not decode')
    operators=tmp_path/'operators.npz';operators.write_bytes(b'must not decode')
    project=default_two_body_project();before=input_hash(project)
    options={'n_steps':2,'criteria':dict(omega_min=.5,omega_max=1.,added_mass_abs_tol=1.,added_mass_rel_tol=.1,damping_abs_tol=1.,damping_rel_tol=.1)}
    with pytest.raises(HydroGeometryError,match='surface inventory'):
        if route=='raw':import_hydro_inputs(project,ds,kernel,options)
        else:import_derived_hydro_inputs(project,ds,operators,kernel,options)
    assert input_hash(project)==before

@pytest.mark.parametrize('data',[[],[{'surface_kind':'thin_sheet'}]])
def test_future_body_surface_intent_is_not_discarded(data):
    body=default_two_body_project().bodies[0]; payload=body.to_dict();payload['hydro_surfaces']=data
    with pytest.raises(ValueError,match='instead of dropping'):Body.from_dict(payload)
    assert Body.from_dict(body.to_dict())==body
