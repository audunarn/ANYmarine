"""Focused factory contracts; no backend calculation or licensed fixtures."""
import json
import math
import pytest
from anymarine import reference_comparison as reference
from anymarine.study_examples import l01_study, run_modes, l01_mass_properties, PRIMARY_EXAMPLES
from anymarine.domain import ProjectModel
from anymarine.persistence import ProjectStore
from anymarine.mass_properties import BodyMassProperties

@pytest.fixture
def comparison(tmp_path):
    data=reference.build(title='Synthetic test reference',notes=['Synthetic fixture only'],
        body=dict(name='L01 default vessel',mass_kg=1000.,centre_of_mass_m=[1.,0.,-1.],
            display_mesh=dict(positions=[[0.,-2.,-2.],[4.,-2.,-2.],[0.,2.,0.]],triangles=[[0,1,2]])),
        periods_s=[5.],headings_deg=[0.],sources=[dict(label='fixture',program='fixture',version='1',
            method='synthetic test',details={},raos=[[[[0.,0.] for _ in range(6)]]])])
    path=tmp_path/'reference.json';reference.write(data,path);return path


def test_five_primary_entries():
    assert len(PRIMARY_EXAMPLES)==5
    assert len(set(k for k,_ in PRIMARY_EXAMPLES))==5


def test_pair_and_roundtrip(comparison, tmp_path):
    example=l01_study(comparison,count=2)
    p=example.project
    assert p.bodies[0].body_id != p.bodies[1].body_id
    assert p.bodies[0].reference_position[1] < p.bodies[1].reference_position[1]
    assert p.linear_inputs is None and p.results == ()
    store=ProjectStore(tmp_path/'saved');store.save(p)
    assert store.load().to_dict()==p.to_dict()
    assert not next(m for m in run_modes(p) if m.key=='reference').available
    assert not next(m for m in run_modes(p) if m.key=='native').available


def test_spread_topology_and_independent_serialization(comparison, tmp_path):
    p=l01_study(comparison,moored=True).project
    mooring=p.native_inputs.mooring.to_dict()
    lines=mooring['data']['lines']
    assert len(lines)==4
    for line in lines:
        assert sum(line['rest_lengths'])==1000.
        assert line['endpoints'][0]['position'][2]==-200.
        assert line['endpoints'][-1]['body_id']==p.bodies[0].body_id
        assert line['endpoints'][-1]['point_body'][2]==-5.
        assert line['axial_rigidity']==[1e8,1e8]
        a=line['endpoints'][0]['position']; f=line['endpoints'][-1]['point_body']
        assert math.dist(a,f)<1000.
    lines[0]['rest_lengths'][0]=1.
    assert lines[1]['rest_lengths']==[500.,500.]
    assert p.native_inputs.mooring.to_dict()['data']['lines'][0]['rest_lengths']==[500.,500.]
    store=ProjectStore(tmp_path/'saved');store.save(p)
    assert store.load().to_dict()==p.to_dict()


def test_readiness_mass_and_no_fake_fluid(comparison):
    props=BodyMassProperties((1.,0.,-1.),((100.,0.,0.),(0.,200.,0.),(0.,0.,250.)))
    p=l01_study(comparison,mass_properties=props).project
    modes={m.key:m for m in run_modes(p,reference_available=True)}
    assert modes['reference'].available and modes['native'].available
    assert not modes['static'].available and not modes['linear'].available
    assert p.native_inputs.potential_flow is None


def test_spatial_mass_to_cg_and_refuse_wrong_source(comparison,tmp_path):
    data=reference.load(comparison)['body']
    m=1000.; cg=[1.,0.,-1.]
    # Explicit full tensor, nonzero CG coupling and off-diagonal CG inertia.
    I=[[1100.,2.,1003.],[2.,2200.,4.],[1003.,4.,1300.]]
    cross=[[0.,1.,0.],[-1.,0.,-1.],[0.,1.,0.]]
    M=[[0.]*6 for _ in range(6)]
    for i in range(3):
        M[i][i]=m
        for j in range(3):
            M[i][j+3]=-m*cross[i][j];M[j+3][i]=M[i][j+3];M[i+3][j+3]=I[i][j]
    path=tmp_path/'mass.json'
    path.write_text(json.dumps(dict(body=dict(mass_kg=m,centre_of_mass_m=cg,inertia_matrix_si=M))))
    props=l01_mass_properties(path,data)
    assert props.inertia_cg_body[0]==pytest.approx([100.,2.,3.])
    data['mass_kg']=999.
    with pytest.raises(ValueError,match='does not match'):l01_mass_properties(path,data)


def test_invalid_count_and_tampering_refused(comparison):
    with pytest.raises(ValueError):l01_study(comparison,count=2,moored=True)
    with pytest.raises(ValueError):l01_study(comparison,count=True)
    with pytest.raises(ValueError):l01_study(comparison,moored='false')
    data=json.loads(comparison.read_text());data['body']['mass_kg']*=2
    comparison.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='hash'):l01_study(comparison)


def test_wrong_vessel_and_mass_frame_refused(comparison):
    props=BodyMassProperties((0.,0.,0.),((100.,0.,0.),(0.,200.,0.),(0.,0.,250.)))
    with pytest.raises(ValueError,match='CG does not match'):
        l01_study(comparison,mass_properties=props)
    data=reference.load(comparison);data['body']['name']='Another vessel'
    data['artifact_hash']=reference.artifact_hash(data);reference.write(data,comparison)
    with pytest.raises(ValueError,match='not another vessel'):l01_study(comparison)



def test_qt_catalog_loading_never_submits(comparison, monkeypatch):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from anymarine.qtapp.mainwindow import MainWindow
    app=QApplication.instance() or QApplication([])
    win=MainWindow(viewer_backend='software')
    monkeypatch.setattr(win,'_confirm_discard',lambda:True)
    monkeypatch.setattr(win,'_l01_reference_candidates',lambda:[comparison])
    monkeypatch.setattr(win,'_remember_l01_reference',lambda path:None)
    # Test fixture intentionally has a different mass than installed local L01.
    monkeypatch.setattr('anymarine.study_examples.l01_mass_properties',lambda *args:None)
    def forbidden(*args,**kwargs):raise AssertionError('loading submitted calculation')
    for name in ('submit_native','submit_equilibrium','submit_hams','submit'):
        monkeypatch.setattr(win._job_manager,name,forbidden)
    actions=[a for a in win._examples_menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions[:5]]==[label for _,label in PRIMARY_EXAMPLES]
    assert actions[5].menu().title()=='Advanced examples'
    win.load_l01_study_example(count=2)
    assert len(win._project.bodies)==2
    notices=[]
    monkeypatch.setattr(win,'_notify_info',lambda *args:notices.append(args))
    win._run_linear_job()
    assert notices and 'Physical hydrodynamic inputs required' in notices[-1][0]
    assert win._project.linear_inputs is None
    win.load_l01_study_example(moored=True)
    assert len(win._project.native_inputs.mooring.to_dict()['data']['lines'])==4
    win._dirty=False;win.close();app.processEvents()
