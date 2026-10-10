"""Development transaction contracts; actual producer fixture is sealed separately."""
from pathlib import Path
import hashlib,json
import numpy as np
import pytest
from anymarine.domain import Body,ProjectModel
from anymarine.hashing import input_hash
from anymarine.native_examples import dry_two_body_project
from anymarine.native_jobs import project_from_snapshot
from anymarine.persistence import ProjectStore,ProjectLoadError
from anymarine.services import ApplicationService
from anymarine.equilibrium_transition import AdoptionDomain,EquilibriumAdoptionError

R=((0.,-1.,0.),(1.,0.,0.),(0.,0.,1.))
def save_project(p,path):ProjectStore(path).save(p)
def load_project(path):return ProjectStore(path).load()

def test_orientation_default_preserves_legacy_serialized_identity():
    p=dry_two_body_project();d=p.bodies[0].to_dict()
    assert 'reference_orientation' not in d
    assert Body.from_dict(d).to_dict()==d
    b=p.bodies[0].with_edits(reference_orientation=R)
    q=p.with_edits(bodies=(b,p.bodies[1]))
    assert input_hash(q)!=input_hash(p)
    assert Body.from_dict(b.to_dict()).reference_orientation==R
    assert b.mesh==p.bodies[0].mesh and b.mass_properties==p.bodies[0].mass_properties

@pytest.mark.parametrize('matrix',[[[1,0,0],[0,1,0],[0,0,-1]],[[1,0,0],[0,2,0],[0,0,1]],[[True,0,0],[0,1,0],[0,0,1]]])
def test_invalid_reference_rotation_refused(matrix):
    with pytest.raises(ValueError):dry_two_body_project().bodies[0].with_edits(reference_orientation=matrix)

def test_rotated_mechanical_reference_and_native_snapshot():
    p=dry_two_body_project();p=p.with_edits(bodies=(p.bodies[0].with_edits(reference_orientation=R),p.bodies[1]))
    p=project_from_snapshot(ApplicationService().native_input_snapshot(p))
    model=ApplicationService().prepare_rigid_body(p,p.bodies[0].body_id)
    assert model.reference_orientation==R
    assert model.inertia_cg_body==p.bodies[0].mass_properties.inertia_cg_body

def test_orientation_schema_and_retained_snapshot_downgrade_protection(tmp_path):
    p=dry_two_body_project();p=p.with_edits(bodies=(p.bodies[0].with_edits(reference_orientation=R),p.bodies[1]))
    save_project(p,tmp_path);loaded=load_project(tmp_path)
    assert loaded.to_dict()==p.to_dict()
    manifest=tmp_path/'project.json';d=json.loads(manifest.read_text(encoding='utf-8'));assert d['schema_version']==9
    d['schema_version']=5;manifest.write_text(json.dumps(d),encoding='utf-8')
    with pytest.raises(ProjectLoadError,match='schema_version 9'):load_project(tmp_path)

def test_rotated_legacy_hydro_geometry_refused_before_backend(monkeypatch):
    from anymarine import hydro
    body=dry_two_body_project().bodies[0].with_edits(reference_orientation=R)
    monkeypatch.setattr(hydro,'_import_anyhydro',lambda:pytest.fail('backend import before refusal'))
    with pytest.raises(hydro.HydroImportError,match='orientation'):hydro.expected_world_mesh(body)

@pytest.mark.parametrize('value',[0.,-1.,True,float('nan')])
def test_bad_domain_depth_refused(value):
    with pytest.raises(ValueError):AdoptionDomain(value)

def test_missing_receipt_cannot_claim_adoption():
    p=dry_two_body_project()
    with pytest.raises(ValueError,match='receipt'):p.with_edits(equilibrium=p.equilibrium.with_edits(source='adopted_static_development_v1'))

@pytest.fixture
def actual():
    from anymarine.equilibrium_jobs import project_from_snapshot as static_project,decode_output
    root=Path(__file__).parent/'fixtures/equilibrium-transition'
    seal=json.loads((root/'provenance.json').read_text(encoding='utf-8'))
    for name,h in seal['sha256'].items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==h
    snap=json.loads((root/'input.json').read_text(encoding='utf-8'));raw=(root/'output.json').read_bytes()
    return static_project(snap),decode_output(raw,snap,snap['input_hash'],seal['sha256']['output.json'])

def regenerator(p,r):
    def build(candidate,request):
        from anymarine.mechanics import prepare_native_run,prepare_native_nodes
        from anymarine.potential_flow_inputs import NativePotentialFlowInputs
        from anymarine.equilibrium_transition import EquilibriumRegeneration
        from anysolver.marine_rigid import RigidBodyState
        from anysolver.marine_coupled_equilibrium import evaluate_marine_static
        from anysolver.marine_hydro_coordinates import EquilibriumHydroFrame,evaluate_coupled
        from anysolver.marine_potential_flow import equilibrium_geometry_hash
        model,_,_=prepare_native_run(p)
        states={bid:RigidBodyState(position=r.positions[bid][-1],anchor=r.rotation_matrices[bid][-1]) for bid in r.body_ids}
        force=evaluate_marine_static(model,states,prepare_native_nodes(candidate)).components['potential-flow-development'][:12]
        fluid=p.native_inputs.potential_flow.to_dict()
        for frame,b in zip(fluid['frames'],candidate.bodies):
            frame['equilibrium_position']=list(b.reference_position);frame['equilibrium_rotation']=[list(row) for row in b.reference_orientation]
        frames=tuple(EquilibriumHydroFrame(**f) for f in fluid['frames']);fluid['equilibrium_hash']=equilibrium_geometry_hash(frames)
        fluid['baseline']=np.linalg.solve(evaluate_coupled(frames,states).jacobian.T,force).tolist()
        fluid['source_identity']='NEW synthetic regenerated adoption test '+fluid['equilibrium_hash']
        return EquilibriumRegeneration(NativePotentialFlowInputs(fluid))
    return build

def test_actual_adoption_force_continuity_and_atomic_roundtrip(actual,tmp_path):
    from anymarine.native_jobs import project_from_snapshot
    p,r=actual;before=p.to_dict();svc=ApplicationService(tmp_path/'active');svc.save(p)
    manifest=(tmp_path/'active/project.json').read_bytes()
    q=svc.adopt_equilibrium(p,r,domain=AdoptionDomain(10.),regenerate=regenerator(p,r))
    assert p.to_dict()==before and (tmp_path/'active/project.json').read_bytes()==manifest
    assert q.equilibrium_adoption['audit']['component_continuity_ratio']<=1.
    assert q.equilibrium_adoption['status']=='development_unqualified'
    assert q.native_inputs.potential_flow.data['baseline']!=p.native_inputs.potential_flow.data['baseline']
    assert svc.result_stale(q,r) and q.results[-1].to_dict()==r.to_dict()
    assert q.native_inputs.mooring.data==p.native_inputs.mooring.data
    for body in q.bodies:
        assert body.reference_position==tuple(r.positions[body.body_id][-1])
        assert body.reference_orientation==tuple(tuple(row) for row in r.rotation_matrices[body.body_id][-1])
        assert body.mesh==p.body(body.body_id).mesh and body.mass_properties==p.body(body.body_id).mass_properties
        assert q.native_inputs.initial_states[body.body_id]['velocity_world']==(0.,)*6
    assert q.native_inputs.mooring.initial_nodes['positions']==r.line_nodes[-1]
    save_project(q,tmp_path/'adopted');loaded=load_project(tmp_path/'adopted')
    assert loaded.to_dict()==q.to_dict()
    snap=svc.native_input_snapshot(loaded);assert project_from_snapshot(snap).native_inputs==q.native_inputs
    with pytest.raises(TypeError):q.equilibrium_adoption['audit']['force_residual']=1.

@pytest.mark.parametrize('when',['before','during','commit'])
def test_cancelled_adoption_rolls_back(actual,when):
    from anymarine.equilibrium_transition import EquilibriumAdoptionCancelled
    p,r=actual;before=p.to_dict();calls=[0]
    def cancel():
        calls[0]+=1
        return calls[0]>= {'before':1,'during':3,'commit':4}[when]
    with pytest.raises(EquilibriumAdoptionCancelled):ApplicationService().adopt_equilibrium(p,r,domain=AdoptionDomain(10.),regenerate=regenerator(p,r),should_cancel=cancel)
    assert p.to_dict()==before

def test_stale_result_and_missing_regeneration_refused(actual):
    p,r=actual;svc=ApplicationService()
    with pytest.raises(EquilibriumAdoptionError,match='regeneration required'):svc.adopt_equilibrium(p,r,domain=AdoptionDomain(10.))
    changed=p.with_edits(settings=p.settings.with_edits(dt=.2))
    with pytest.raises(EquilibriumAdoptionError,match='current immutable'):svc.adopt_equilibrium(changed,r,domain=AdoptionDomain(10.),regenerate=regenerator(p,r))

@pytest.mark.parametrize('failure',['exception','zero_baseline','project_edits','old_frames','production'])
def test_failed_or_invalid_regeneration_keeps_original(actual,failure):
    from anymarine.equilibrium_transition import EquilibriumRegeneration
    from anymarine.potential_flow_inputs import NativePotentialFlowInputs
    p,r=actual;before=p.to_dict()
    def regenerate(candidate,request):
        assert candidate is not p and candidate.native_inputs.potential_flow is None
        with pytest.raises(TypeError):request['domain']['water_depth']=1.
        if failure=='exception':raise RuntimeError('injected regeneration crash')
        if failure=='project_edits':return p
        if failure=='old_frames':return EquilibriumRegeneration(p.native_inputs.potential_flow)
        fluid=regenerator(p,r)(candidate,request).potential_flow.to_dict()
        if failure=='zero_baseline':fluid['baseline']=[0.]*12
        if failure=='production':fluid['source_kind']='qualified_bem'
        return EquilibriumRegeneration(NativePotentialFlowInputs(fluid))
    with pytest.raises((ValueError,RuntimeError)):ApplicationService().adopt_equilibrium(p,r,domain=AdoptionDomain(10.),regenerate=regenerate)
    assert p.to_dict()==before

def test_adopted_dependencies_are_stale_after_body_edit(actual):
    from anymarine.mechanics import prepare_native_run
    p,r=actual;q=ApplicationService().adopt_equilibrium(p,r,domain=AdoptionDomain(10.),regenerate=regenerator(p,r))
    b=q.bodies[0];changed=q.with_edits(bodies=(b.with_edits(reference_position=(b.reference_position[0]+.01,*b.reference_position[1:])),q.bodies[1]))
    with pytest.raises(EquilibriumAdoptionError,match='stale'):prepare_native_run(changed)

def test_retained_adoption_snapshot_requires_new_manifest(actual,tmp_path):
    from anymarine.domain import ResultRecord
    p,r=actual
    root=Path(__file__).parent/'fixtures/equilibrium-transition'
    dr=ResultRecord.from_dict(json.loads((root/'dynamic-output.json').read_text(encoding='utf-8'))['result'])
    detached=p.with_edits(native_inputs=None,results=(dr,))
    save_project(detached,tmp_path);manifest=tmp_path/'project.json';data=json.loads(manifest.read_text(encoding='utf-8'))
    assert data['schema_version']==9
    data['schema_version']=5;manifest.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ProjectLoadError,match='schema_version 9'):load_project(tmp_path)

def test_base_viewer_transform_honors_reference_rotation():
    from types import SimpleNamespace
    from anymarine.qtapp.mainwindow import MainWindow
    p=dry_two_body_project().with_edits(native_inputs=None);b=p.bodies[0].with_edits(reference_orientation=R)
    captured={};handle=SimpleNamespace(set_transform=lambda matrix:captured.update(matrix=matrix.copy()))
    viewer=SimpleNamespace(add_mesh_arrays=lambda *args,**kwargs:handle)
    MainWindow._add_viewer_body(viewer,b,p)
    np.testing.assert_array_equal(captured['matrix'][:3,:3],R)
    np.testing.assert_array_equal(captured['matrix'][:3,3],b.reference_position)

def test_adopted_waterline_edit_remains_stale_after_save(actual,tmp_path):
    from anymarine.mechanics import prepare_native_run
    p,r=actual
    q=ApplicationService().adopt_equilibrium(p,r,domain=AdoptionDomain(10.),regenerate=regenerator(p,r))
    changed=q.with_edits(equilibrium=q.equilibrium.with_edits(waterline_z=.25))
    save_project(changed,tmp_path)
    loaded=load_project(tmp_path)
    with pytest.raises(EquilibriumAdoptionError,match='waterline'):
        prepare_native_run(loaded)
    # Choosing a different run duration does not invalidate stationary dependencies.
    data=q.native_inputs.to_dict();data['run_spec']['steps']=2
    from anymarine.native_inputs import NativeMechanicalInputs
    prepare_native_run(q.with_edits(native_inputs=NativeMechanicalInputs.from_dict(data)))

def test_schema9_lid_policy_and_native_transport_coexist(tmp_path):
    from anymarine.domain import BodyMesh
    p=dry_two_body_project()
    lid=BodyMesh(positions=((-1.,-1.,-.1),(1.,-1.,-.1),(1.,1.,-.1)),triangles=((0,1,2),))
    b=p.bodies[0].with_edits(reference_orientation=R,hydro_lid=lid,hydro_lid_policy='submerged_0p1m_v1')
    p=p.with_edits(bodies=(b,p.bodies[1]))
    save_project(p,tmp_path)
    loaded=load_project(tmp_path)
    assert loaded.to_dict()==p.to_dict()
    assert json.loads((tmp_path/'project.json').read_text())['schema_version']==9
    assert project_from_snapshot(ApplicationService().native_input_snapshot(loaded)).to_dict()==p.with_edits(results=()).to_dict()

def test_public_synthetic_route_refuses_rotated_references_before_sampling(monkeypatch):
    from anymarine import synthetic
    p=dry_two_body_project()
    p=p.with_edits(bodies=(p.bodies[0].with_edits(reference_orientation=R),p.bodies[1]))
    before=p.to_dict()
    monkeypatch.setattr(synthetic,'sample_times',lambda *args:pytest.fail('sampling before refusal'))
    with pytest.raises(ValueError,match='rotated body references'):
        ApplicationService().run_synthetic_local(p)
    assert p.to_dict()==before

def test_shared_wave_adoption_explicitly_refused_without_dropping_intent(actual):
    p=ApplicationService().load_native_sea_state_example();_,r=actual
    before=p.to_dict()
    with pytest.raises(EquilibriumAdoptionError,match='shared-wave equilibrium adoption is unsupported'):
        ApplicationService().adopt_equilibrium(p,r,domain=AdoptionDomain(10.),
            regenerate=lambda *args:pytest.fail('regeneration before refusal'))
    assert p.to_dict()==before
    assert p.native_inputs.potential_flow.data['excitation_series']['wave_realization_hash']==p.native_inputs.wave_realization.content_hash
