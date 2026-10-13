"""Control/orchestration tests. Numerical owners are mocked; no physical solves."""
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from anyhydro.source_study_case import make_prospective_source_study_case
from anyhydro.radiation import RadiationSettings
from anymarine import w5_workflow as w5
from anymarine.linear_assessment import validate_assessment_controls
from anymarine.services import ApplicationService, ServiceError
from test_linear_assessment import CRITERIA


@pytest.fixture
def spec():
    source = make_prospective_source_study_case()
    return dict(schema_version=1, case_id='w5-control-test', source_case=source.to_dict(),
        kernel_settings={r: RadiationSettings(dt=dt, n_time=nt, symmetry_tol=1e-10,
            passivity_tol=1e-10, low_tail_policy='linear_to_zero_extension',
            extension_name='test declared linear zero continuation').to_dict()
            for r, dt, nt in [('coarse', .32, 201), ('fine', .16, 401)]},
        reconstruction_criteria=dict(omega_min=2., omega_max=6., added_mass_abs_tol=1.,
            added_mass_rel_tol=.01, damping_abs_tol=1., damping_rel_tol=.01),
        frames=dict(coarse=101, fine=201), linear_execution_limits=dict(coarse=None, fine=None),
        dt_ratio=2, t0=0., surge_link_stiffness=50.,
        free_decay_initial_states=[dict(q0=[.01, .02, -.01, .01], v0=[0., 0., 0., 0.])],
        regular_waves=[dict(omega=2., heading=0., amplitude=.02, phase=.3,
            windows=dict(start=16., mid=24., end=32.), q0=[0.]*4, v0=[0.]*4)],
        assessment_criteria=dict(CRITERIA),
        fit_controls=dict(intercepts=True, drift='none', max_condition=1e12),
        declarations={key: 'TEST control declaration; not evidence of adequacy'
                      for key in w5._DECLARATIONS})


def test_case_identity_defensive_copy(spec):
    case = w5.W5Case.from_dict(spec)
    frozen = case.to_dict()
    old = case.case_hash
    spec['regular_waves'][0]['amplitude'] *= 2
    assert case.to_dict() == frozen and case.case_hash == old
    changed = case.to_dict(); changed['declarations']['frequency_band'] += ' revised'
    assert w5.W5Case.from_dict(changed).case_hash != old


@pytest.mark.parametrize('mutate', [
    lambda s: s.update(unknown=True),
    lambda s: s.update(schema_version=True),
    lambda s: s['assessment_criteria'].update(rao_amplitude_relative=.031),
    lambda s: s['source_case'].update(include_infinite_frequency=False),
    lambda s: s['frames'].update(fine=200),
    lambda s: s['kernel_settings']['fine'].update(dt=.15),
    lambda s: s['regular_waves'][0].update(omega=3.),
    lambda s: s['regular_waves'][0]['windows'].update(start=0.),
    lambda s: s['regular_waves'][0]['windows'].update(start=29., mid=30.5, end=32.),
    lambda s: s['declarations'].update(high_tail=''),
    lambda s: s['free_decay_initial_states'][0].update(q0=[0.]*4),
])
def test_refusal_before_any_output(spec, tmp_path, mutate):
    mutate(spec)
    out = tmp_path/'refused'
    with pytest.raises(ServiceError):
        ApplicationService().prepare_w5_case(spec, out)
    assert not out.exists()


def test_existing_owner_control_limits_remain_authoritative(spec):
    controls = validate_assessment_controls(spec['assessment_criteria'], spec['fit_controls'])
    controls['criteria']['rao_amplitude_relative'] = 10
    assert spec['assessment_criteria']['rao_amplitude_relative'] == .03
    with pytest.raises(Exception, match='rao_phase_deg'):
        validate_assessment_controls({**spec['assessment_criteria'], 'rao_phase_deg': 3.01}, spec['fit_controls'])


def test_pair_execution_limits_match_before_source_work(spec,tmp_path):
    spec['kernel_settings']['fine']['execution_limits']={'max_time_samples':501}
    with pytest.raises(w5.W5Error,match='execution-limit identity'):
        w5.prepare_w5_case(spec,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()
    spec['kernel_settings']['coarse']['execution_limits']={'max_time_samples':501}
    assert w5.W5Case.from_dict(spec).to_dict()['kernel_settings']['fine']['execution_limits']=={'max_time_samples':501}


def test_source_preparation_error_has_typed_public_failure(spec,tmp_path,monkeypatch):
    import anyhydro.source_study as source
    def fail(case,path): raise RuntimeError('owned source preparation crash')
    monkeypatch.setattr(source,'prepare_source_study',fail)
    out=tmp_path/'failed'
    with pytest.raises(ServiceError,match='source preparation failed'):
        ApplicationService().prepare_w5_case(spec,out)
    assert (out/'case.json').exists()
    assert json.loads((out/'status.json').read_text())['state']=='failed'


def test_real_public_preparation_no_backend(spec, tmp_path, monkeypatch):
    import anyhydro.source_study as source
    def forbidden(*args, **kwargs):
        pytest.fail('preparation attempted numerical execution')
    monkeypatch.setattr(source, 'execute_source_study', forbidden)
    out = tmp_path/'registered'
    status = ApplicationService().prepare_w5_case(spec, out)
    assert status['state'] == 'prepared'
    assert not status['scientific_qualification']
    assert json.loads((out/'source/status.json').read_text())['status'] == 'not_executed'
    case, loaded = w5.load_w5_case(out)
    assert case.case_hash == status['case_hash'] == loaded['case_hash']
    before = (out/'case.json').read_bytes()
    with pytest.raises(ServiceError, match='already exists'):
        ApplicationService().prepare_w5_case(spec, out)
    assert (out/'case.json').read_bytes() == before


@pytest.mark.parametrize('tamper', ['case', 'promotion', 'missing_source'])
def test_saved_case_and_scope_tamper_refused(spec, tmp_path, tamper):
    out=tmp_path/'case'; w5.prepare_w5_case(spec,out)
    if tamper == 'case':
        record=json.loads((out/'case.json').read_text());record['case']['surge_link_stiffness']*=2
        (out/'case.json').write_text(json.dumps(record))
    elif tamper == 'promotion':
        status=json.loads((out/'status.json').read_text());status['scientific_qualification']=True
        (out/'status.json').write_text(json.dumps(status))
    else:
        (out/'source/case.json').unlink()
    with pytest.raises(w5.W5Error):
        w5.load_w5_case(out)


def test_cancellation_before_source_preserves_registration(spec, tmp_path, monkeypatch):
    out=tmp_path/'cancelled'; w5.prepare_w5_case(spec,out)
    import anyhydro.source_study as source
    monkeypatch.setattr(source,'execute_source_study',lambda *a,**k: pytest.fail('unexpected source execution'))
    with pytest.raises(w5.W5Cancelled):
        w5.execute_w5_case(out,backend=object(),execution_limits=object(),should_cancel=lambda:True)
    assert (out/'case.json').exists() and (out/'source/case.json').exists()
    assert json.loads((out/'status.json').read_text())['state']=='cancelled'
    with pytest.raises(w5.W5Error,match='fresh prepared'):
        w5.execute_w5_case(out,backend=object(),execution_limits=object())


def test_cancel_callback_failure_records_failed_public_status(spec,tmp_path):
    service=ApplicationService();out=tmp_path/'case';service.prepare_w5_case(spec,out)
    calls=[]
    def broken():
        calls.append(1)
        raise RuntimeError('cancellation callback crashed')
    with pytest.raises(ServiceError,match='cancellation callback crashed'):
        service.execute_w5_case(out,backend=object(),execution_limits=object(),should_cancel=broken)
    assert calls==[1]
    assert json.loads((out/'status.json').read_text())['state']=='failed'
    assert (out/'case.json').exists()


def _fake_owners(monkeypatch, spec, out, *, source_outcome='source_admission_passed_development_only',
                 response_verdict='pass', throw_response=False):
    """Mock physical ownership only, execute the actual W5 caller and case builder."""
    import anyhydro.source_study as source
    import anyhydro.source_study_case as case_owner
    import anyhydro.radiation as radiation
    import anymarine.services as services
    import anymarine.linear as linear
    import anymarine.linear_assessment as assessment
    import anymarine.paired_refinement as paired
    events=[]
    source_case=case_owner.SourceStudyCase.from_dict(spec['source_case'])
    artifacts={str(level):NS(omegas=np.array(source_case.omegas),
        wave_directions=np.array(source_case.wave_directions),
        derived_damping=np.stack([np.diag([1.,2.,3.,4.])]*len(source_case.omegas)),
        excitation_force=np.tile(np.array([1+2j,2-1j,-1j,3.]),(len(source_case.omegas),1,1)))
        for level in source_case.levels}
    loaded=dict(case_hash=source_case.content_hash(),levels={
        str(level):dict(artifact=artifacts[str(level)],dataset=NS(),
            level_report=dict(raw_file='fixture.nc',derived_artifact='fixture.npz'))
        for level in source_case.levels})
    def execute(sc, backend, limits, target, *, should_cancel):
        assert sc.content_hash()==source_case.content_hash();events.append('source')
        (Path(target)/'mock-source-evidence.txt').write_text('MOCK: no numerical solve')
    monkeypatch.setattr(source,'execute_source_study',execute)
    monkeypatch.setattr(source,'load_source_study',lambda path:loaded)
    monkeypatch.setattr(source,'adjudicate_source_study',lambda path:dict(outcome=source_outcome))
    monkeypatch.setattr(radiation,'radiation_kernel_from_derived_operators',lambda *a,**k:NS())
    monkeypatch.setattr(radiation,'save_radiation_kernel',lambda obj,path:Path(path).write_text('MOCK kernel'))
    def write_case(case,path):
        Path(path).write_text(json.dumps(case.spec))
        events.append('case:'+str(Path(path).parent))
    monkeypatch.setattr(assessment,'write_case_artifact',write_case)
    monkeypatch.setattr(assessment,'write_report_artifact',lambda report,path:Path(path).write_text(report.verdict))
    monkeypatch.setattr(linear,'linear_input_hash',lambda inputs:inputs.hash)
    class FakeService:
        def __init__(self): self.root=None
        def save(self,project,root=None):
            target=Path(root) if root is not None else self.root
            assert target is not None
            target.mkdir(parents=True,exist_ok=True)
            (target/'model.json').write_text('MOCK project')
            return target
        def save_current(self,project,root):
            target=self.save(project,root)
            self.root=target
            return target
        def import_derived_hydro_inputs(self,project,dataset,operators,kernel,options):
            assert self.root is not None
            role=self.root.name
            return NS(linear_inputs=NS(hash=hashlib.sha256(str(self.root).encode()).hexdigest()),
                hydro_provenance=NS(dataset_content_hash='d'*64,
                    derived_bundle=dict(derived_content_hash='o'*64,operators_raw_sha256='s'*64)))
        def prepare_linear_assessment(self,project,specification):
            assert specification['input_hash']==project.linear_inputs.hash
            digest=hashlib.sha256(json.dumps(specification,sort_keys=True).encode()).hexdigest()
            return NS(spec=specification,case_hash=digest)
        def run_linear_local(self,project,*,cancel):
            parent=self.root.parent
            assert (parent/'fine/registered-case.json').exists()
            assert (parent/'coarse/registered-case.json').exists()
            for run in ('decay-0','wave-0'):
                for role in ('coarse','fine'):
                    assert (out/run/role/'registered-case.json').exists()
            events.append('solve:'+str(parent))
            if throw_response: raise RuntimeError('owned response crash')
            return NS()
        def add_result(self,project,result):return project
        def assess_linear_result(self,*args):return NS(verdict=response_verdict)
    monkeypatch.setattr(services,'ApplicationService',FakeService)
    monkeypatch.setattr(paired,'PairedMember',lambda project,result,case,*,project_root:NS(root=project_root))
    monkeypatch.setattr(paired,'assess_paired_refinement',lambda coarse,fine:NS(combined_verdict='pass'))
    def save_pair(coarse,fine,report,path):
        assert report.combined_verdict=='pass';Path(path).mkdir()
        (Path(path)/'mock-pair.txt').write_text('MOCK snapshot')
    monkeypatch.setattr(paired,'save_paired_snapshot',save_pair)
    return events


def test_connected_production_caller_registers_both_before_solves(spec,tmp_path,monkeypatch):
    out=tmp_path/'run';w5.prepare_w5_case(spec,out)
    events=_fake_owners(monkeypatch,spec,out)
    result=w5.execute_w5_case(out,backend=object(),execution_limits=object())
    assert result['state']=='checks_completed' and not result['scientific_qualification']
    assert result['open_qualification_gates']==list(w5._OPEN_GATES)
    assert 'frequency_band_and_tail_adequacy' in result['open_qualification_gates']
    assert 'off_diagonal_radiation_force_observability' in result['open_qualification_gates']
    assert 'independent_scientific_assessment' in result['open_qualification_gates']
    assert len(result['pairs'])==2
    assert len([e for e in events if e.startswith('solve:')])==4
    wave=json.loads((out/'wave-0/fine/registered-case.json').read_text())
    # Nonzero complex physical source phasor: all four coupled components kept.
    expected=.02*(1+2j)*complex(math.cos(.3),math.sin(.3))
    assert wave['force_phasors'][0]==pytest.approx([expected.real,expected.imag])
    assert len(wave['force_phasors'])==4 and wave['reference']['kind']=='hydro_spectrum'
    assert json.loads((out/'decay-0/fine/registered-case.json').read_text())['reference']['kind']=='hydro_source'
    assert (out/'directional-damping.json').exists()


def test_cancellation_after_first_response_stops_and_retains_cases(spec,tmp_path,monkeypatch):
    out=tmp_path/'run';w5.prepare_w5_case(spec,out)
    events=_fake_owners(monkeypatch,spec,out)
    def cancel():return any(e.startswith('solve:') for e in events)
    with pytest.raises(w5.W5Cancelled):
        w5.execute_w5_case(out,backend=object(),execution_limits=object(),should_cancel=cancel)
    assert len([e for e in events if e.startswith('solve:')])==1
    assert (out/'decay-0/fine/assessment.json').exists()
    assert (out/'decay-0/coarse/registered-case.json').exists()
    assert json.loads((out/'status.json').read_text())['state']=='cancelled'


def test_public_execute_wrapper_refuses_retry(spec,tmp_path):
    service=ApplicationService();out=tmp_path/'case'
    service.prepare_w5_case(spec,out)
    with pytest.raises(ServiceError,match='cancelled'):
        service.execute_w5_case(out,backend=object(),execution_limits=object(),should_cancel=lambda:True)
    with pytest.raises(ServiceError,match='fresh prepared'):
        service.execute_w5_case(out,backend=object(),execution_limits=object())


@pytest.mark.parametrize('outcome,state',[('no_go','no_go'),('cancelled','cancelled'),('failed','failed')])
def test_source_terminal_classification_no_response(spec,tmp_path,monkeypatch,outcome,state):
    out=tmp_path/'run';w5.prepare_w5_case(spec,out)
    events=_fake_owners(monkeypatch,spec,out,source_outcome=outcome)
    result=w5.execute_w5_case(out,backend=object(),execution_limits=object())
    assert result['state']==state and events==['source']
    assert (out/'source-adjudication.json').exists()


@pytest.mark.parametrize('crash',[False,True])
def test_response_failure_preserves_cases_and_source(spec,tmp_path,monkeypatch,crash):
    out=tmp_path/'run';w5.prepare_w5_case(spec,out)
    _fake_owners(monkeypatch,spec,out,response_verdict='no_go',throw_response=crash)
    if crash:
        with pytest.raises(w5.W5Error,match='owned response crash'):
            w5.execute_w5_case(out,backend=object(),execution_limits=object())
    else:
        assert w5.execute_w5_case(out,backend=object(),execution_limits=object())['state']=='no_go'
    assert (out/'source/mock-source-evidence.txt').exists()
    assert (out/'decay-0/coarse/registered-case.json').exists()
    assert json.loads((out/'status.json').read_text())['state']==('failed' if crash else 'no_go')


def test_actual_first_slice_project_geometry(spec):
    from anyhydro.source_study_case import SourceStudyCase,build_study_bodies
    from anymarine.hydro import expected_world_mesh,expected_world_lid
    source=SourceStudyCase.from_dict(spec['source_case'])
    project=w5._project(source,max(source.levels))
    for app,hydro in zip(project.bodies,build_study_bodies(source,max(source.levels))):
        assert np.array_equal(expected_world_mesh(app).vertices,hydro.mesh.vertices)
        assert np.array_equal(expected_world_lid(app).vertices,hydro.lid_mesh.vertices)
        assert app.mass==512.5


@pytest.mark.parametrize('quadrature_method', ['frequency_trapezoid', 'piecewise_linear_exact', 'band_shared', 'fd_start', 'fd_cancel',
                                               'fd_prehistory'])
def test_real_public_source_kernel_import_registration_join_without_solver(spec,tmp_path,monkeypatch,quadrature_method):
    """Analytic source fixture; mock ONLY source-study admission, never claim BEM.

    All public downstream geometry, derivation, persistence, reconstruction,
    input compilation and assessment registration run. Stop before any solve.
    """
    import anyhydro
    import anyhydro.source_study as source
    from anyhydro.source_study_case import SourceStudyCase,build_study_bodies,build_study_request,study_operator_settings
    from anymarine.linear_assessment import read_case_artifact
    from anymarine.linear import linear_input_hash
    band=quadrature_method=='band_shared'
    fd_cancel=quadrature_method=='fd_cancel'   # cancellation pending before the FD bootstrap
    fd_prehistory=quadrature_method=='fd_prehistory'   # + declared periodic prehistory
    fd_start=quadrature_method in ('fd_start','fd_cancel','fd_prehistory')
    if fd_start:
        quadrature_method='frequency_trapezoid'
        spec['t0']=.7   # nonzero start time: the start-state phase is exercised
        for wave in spec['regular_waves']:
            del wave['q0'], wave['v0']
            wave['initial_state']='fd_steady_state'
            if fd_prehistory: wave['prehistory']='periodic'
    for controls in spec['kernel_settings'].values():
        if quadrature_method != 'frequency_trapezoid': controls['quadrature_method']='piecewise_linear_exact'
        if band:
            # Band-consistent A_inf calibrated once on the fine grid (dt .16).
            controls.update(a_inf_policy='band_consistent',a_inf_calibration_min=.2,
                            a_inf_calibration_max=1.,a_inf_calibration_dt=.16)
    if band:
        spec['reconstruction_criteria']=dict(form='full_block',omega_min=2.,omega_max=6.,block_rel_tol=.02,
            modal_rel_tol=.02,energetic_fraction=1e-3,cluster_fraction=.01)
    spec['source_case']['omegas']=[round(.1*k,1) for k in range(1,81)]
    omega=np.array(spec['source_case']['omegas'])
    R=np.diag(np.linspace(.1,1.2,12))+.01*np.ones((12,12))
    a_inf=10*np.eye(12)+R
    lam=1.5
    selection=[0,2,6,8]
    for settings in spec['kernel_settings'].values():
        settings.update(low_tail_policy='explicit_zero_sample',extension_name=None,
                        zero_frequency_damping=(R[np.ix_(selection,selection)]/lam).tolist())
    sc=SourceStudyCase.from_dict(spec['source_case'])
    loaded=dict(case_hash=sc.content_hash(),levels={})
    def execute(case,backend,limits,target,*,should_cancel):
        for level in sc.levels:
            bodies=build_study_bodies(sc,level)
            centers=np.array([b.rotation_center for b in bodies])
            com=np.array([b.center_of_mass for b in bodies])
            H=np.zeros((12,12));H[2,2]=H[8,8]=1025*9.81
            dataset=anyhydro.assemble_hydro_dataset(bodies,build_study_request(sc),
                added_mass=np.array([a_inf-R/(lam**2+w**2) for w in omega]),
                radiation_damping=np.array([R*lam/(lam**2+w**2) for w in omega]),
                excitation_force=np.ones((len(omega),1,12),dtype=complex)*(1+2j),
                hydrostatic_stiffness=H,rotation_center=centers,center_of_mass=com,
                center_of_buoyancy=com,added_mass_infinite=a_inf,
                added_mass_infinite_source='analytic-exponential TEST fixture',
                backend='analytic-test-fixture',backend_version='1.0.0',
                qualification_status='development_fixture')
            artifact=anyhydro.derived_operators_from_dataset(dataset,study_operator_settings(sc))
            level_root=Path(target)/f'level-{level:02d}';level_root.mkdir()
            anyhydro.save_hydro_dataset(dataset,level_root/'analytic-fixture.nc')
            anyhydro.save_derived_operators(artifact,level_root/'analytic-fixture.npz')
            loaded['levels'][str(level)]=dict(artifact=artifact,dataset=dataset,
                level_report=dict(raw_file='analytic-fixture.nc',derived_artifact='analytic-fixture.npz'))
    original_load=source.load_source_study
    monkeypatch.setattr(source,'execute_source_study',execute)
    monkeypatch.setattr(source,'load_source_study',lambda path:loaded if loaded['levels'] else original_load(path))
    monkeypatch.setattr(source,'adjudicate_source_study',lambda path:dict(
        outcome='source_admission_passed_development_only',
        test_note='MOCK source-study admission; downstream analytic fixture is NOT BEM'))
    def forbidden(*args,**kwargs):pytest.fail('public registration join attempted a dynamics solve')
    monkeypatch.setattr(ApplicationService,'run_linear_local',forbidden)
    out=tmp_path/'join';w5.prepare_w5_case(spec,out)
    def cancel_after_cases():
        return (out/'wave-0/fine/registered-case.json').exists() and (out/'wave-0/coarse/registered-case.json').exists()
    if fd_cancel:
        import anymarine.linear_assessment as la
        monkeypatch.setattr(la,'fd_reference_response',lambda *a,**k:pytest.fail('FD bootstrap ran after cancellation'))
        cancel_after_cases=lambda:(out/'decay-0/coarse/registered-case.json').exists()
    with pytest.raises(w5.W5Cancelled):
        w5.execute_w5_case(out,backend=object(),execution_limits=object(),should_cancel=cancel_after_cases)
    if fd_cancel:
        assert not (out/'wave-0').exists()
        assert json.loads((out/'status.json').read_text())['state']=='cancelled'
        return
    for run in ('decay-0','wave-0'):
        a_inf={}
        for role in ('coarse','fine'):
            service=ApplicationService()
            project=service.load(out/run/role)
            a_inf[role]=project.linear_inputs.added_mass_infinite
            registered=read_case_artifact(out/run/role/'registered-case.json')
            assert registered.input_hash==linear_input_hash(project.linear_inputs)
            assert project.hydro_provenance.derived_bundle is not None
            service.verify_hydro_provenance(project)
            assert registered.mode==('free_decay' if run=='decay-0' else 'regular_wave')
        if band:  # one physical model at both time steps
            assert a_inf['coarse']==a_inf['fine']
        if fd_start and run=='wave-0':
            start=json.loads((out/'wave-0'/'fd-start'/'fd-start.json').read_text())
            for role in ('coarse','fine'):
                inputs=ApplicationService().load(out/run/role).linear_inputs
                assert list(inputs.q0)==start['q0'] and list(inputs.v0)==start['v0']
            assert any(start['q0']) and any(start['v0'])
            # Independent FD steady state from the analytic fixture (retained DOFs 0,2,6,8, SI):
            # Z = K - w^2 (M + A(w)) - i w (B(w) + C), F from the registered case.
            fine=ApplicationService().load(out/run/'fine').linear_inputs
            registered=read_case_artifact(out/run/'fine'/'registered-case.json')
            w=registered.omega
            sel=np.ix_(selection,selection)
            A=(10*np.eye(12)+R-R/(lam**2+w**2))[sel];B=(R*lam/(lam**2+w**2))[sel]
            Z=(np.asarray(fine.stiffness)-w*w*(np.asarray(fine.mass_dry)+A)-1j*w*(B+np.asarray(fine.damping)))
            F=np.array([complex(*p) for p in registered.force_phasors])
            q=np.linalg.solve(Z,F)*np.exp(-1j*w*.7)
            assert np.allclose(start['q0'],q.real,rtol=1e-9,atol=1e-15)
            assert np.allclose(start['v0'],(-1j*w*q).real,rtol=1e-9,atol=1e-15)
            if fd_prehistory:
                # Both members: the same declaration in provenance and case; forces = wave - H
                # with H from an independent brute-force sum over each member's stored kernel.
                from test_prehistory import brute_force
                qd=np.array([complex(*p) for p in start['qhat']])
                declaration=dict(kind='periodic',qhat=start['qhat'])
                for role in ('coarse','fine'):
                    project=ApplicationService().load(out/run/role)
                    inputs=project.linear_inputs
                    member_case=read_case_artifact(out/run/role/'registered-case.json')
                    assert member_case.reference['prehistory']==declaration
                    assert project.hydro_provenance.to_dict()['wave']['prehistory']==declaration
                    times=np.asarray(inputs.times)
                    wave_force=(F[None,:]*np.exp(-1j*w*times)[:,None]).real
                    H=brute_force(np.asarray(inputs.radiation_kernel),inputs.dt,inputs.t0,inputs.n_steps,w,qd,-1j*w*qd)
                    assert np.max(np.abs(H))>0
                    assert np.max(np.abs(np.asarray(inputs.forces)-(wave_force-H)))<=1e-9*np.max(np.abs(wave_force))
    assert json.loads((out/'status.json').read_text())['state']=='cancelled'


def test_directional_report_uses_fixed_fine_basis_and_no_threshold(spec):
    from anyhydro.source_study_case import SourceStudyCase
    source=SourceStudyCase.from_dict(spec['source_case'])
    coarse,fine=source.levels
    B=np.diag([.01,.01,2.,3.])
    delta=np.array([[0.,.005,0,0],[.005,0,0,0],[0,0,.1,0],[0,0,0,0]])
    loaded=dict(levels={str(level):dict(artifact=NS(omegas=np.array([2.]),
        derived_damping=np.array([matrix]))) for level,matrix in [(coarse,B+delta),(fine,B)]})
    report=w5.retained_direction_diagnostics(loaded,source)
    item=report['frequencies'][0]
    projected=np.array(item['levels'][str(coarse)]['projected_delta'])
    assert item['levels'][str(coarse)]['spectral_delta']==pytest.approx(.1)
    basis=np.array(item['finest_basis'])
    assert np.allclose(basis@projected@basis.T,delta)
    assert 'threshold' in report['scope'] and 'pass' not in report


def test_band_consistent_pair_must_calibrate_on_the_fine_grid(spec):
    for role in ('coarse','fine'):
        spec['kernel_settings'][role].update(quadrature_method='piecewise_linear_exact',
            a_inf_policy='band_consistent',a_inf_calibration_min=.2,a_inf_calibration_max=1.)
    spec['reconstruction_criteria']=dict(form='full_block',omega_min=2.,omega_max=6.,block_rel_tol=.02,
        modal_rel_tol=.02,energetic_fraction=1e-3,cluster_fraction=.01)
    with pytest.raises(w5.W5Error,match='fine kernel grid'):
        w5.W5Case.from_dict(spec)
    for role in ('coarse','fine'):spec['kernel_settings'][role]['a_inf_calibration_dt']=.16
    w5.W5Case.from_dict(spec)


def test_band_consistent_pair_calibration_grid_must_equal_the_fine_grid(spec):
    """abs-tol horizon edge: equal-looking horizons with unnested sample counts are refused."""
    for role in ('coarse','fine'):
        spec['kernel_settings'][role].update(quadrature_method='piecewise_linear_exact',
            a_inf_policy='band_consistent',a_inf_calibration_min=.2,a_inf_calibration_max=1.,
            a_inf_calibration_dt=.16)
    spec['reconstruction_criteria']=dict(form='full_block',omega_min=2.,omega_max=6.,block_rel_tol=.02,
        modal_rel_tol=.02,energetic_fraction=1e-3,cluster_fraction=.01)
    w5.W5Case.from_dict(spec)
    from anyhydro.radiation import RadiationSettings
    coarse=RadiationSettings.from_dict(spec['kernel_settings']['coarse'])
    fine=RadiationSettings.from_dict(spec['kernel_settings']['fine'])
    assert coarse.calibration_settings().n_time==fine.n_time
    # Codex edge: horizons 5e-10 and 4.995e-10 agree within the 1e-12 absolute
    # tolerance, but the coarse calibration rebuild would have 1001 samples.
    spec['kernel_settings']['coarse'].update(dt=1e-12,n_time=501,a_inf_calibration_dt=5e-13)
    spec['kernel_settings']['fine'].update(dt=5e-13,n_time=1000,a_inf_calibration_dt=5e-13)
    with pytest.raises(w5.W5Error,match='nested sample counts'):
        w5.W5Case.from_dict(spec)


def test_periodic_prehistory_requires_the_fd_steady_state_start(spec):
    wave = spec['regular_waves'][0]
    wave['prehistory'] = 'periodic'
    with pytest.raises(w5.W5Error):                      # explicit q0/v0 start: refused
        w5.W5Case.from_dict(spec)
    del wave['q0'], wave['v0']
    wave['initial_state'] = 'fd_steady_state'
    assert w5.W5Case.from_dict(spec).to_dict()['regular_waves'][0]['prehistory'] == 'periodic'
    wave['prehistory'] = 'ramp'
    with pytest.raises(w5.W5Error, match="prehistory must be 'periodic'"):
        w5.W5Case.from_dict(spec)


def test_regular_wave_fd_steady_state_start_is_declared(spec):
    wave = spec['regular_waves'][0]
    del wave['q0'], wave['v0']
    wave['initial_state'] = 'fd_steady_state'
    case = w5.W5Case.from_dict(spec)
    assert case.to_dict()['regular_waves'][0]['initial_state'] == 'fd_steady_state'
    wave['initial_state'] = 'rest'
    with pytest.raises(w5.W5Error, match='fd_steady_state'):
        w5.W5Case.from_dict(spec)
    wave['initial_state'] = 'fd_steady_state'
    wave['q0'] = [0.0] * 4
    with pytest.raises(w5.W5Error):
        w5.W5Case.from_dict(spec)
