from pathlib import Path
import copy
import json
import numpy as np
import pytest
from edc.data.transforms import RobotTransforms, convert_units
from edc.robots.profiles import load_profile
from edc.data.ir.dataset import open_dataset

EXAMPLE=Path(__file__).resolve().parents[2]/'tests/edc/fixtures/transforms.yaml'


def profile(): return load_profile(EXAMPLE)


def test_units_and_physical_dimensions():
    np.testing.assert_allclose(convert_units([0,90,180],'deg','rad').output,[0,np.pi/2,np.pi])
    np.testing.assert_allclose(convert_units([1000],'mA','A').output,[1])
    for source,target in [('A','N*m'),('unknown','m'),('m','rad')]:
        with pytest.raises(ValueError): convert_units([1],source,target)


def test_clock_inverse_range_and_origin_preserved():
    t=RobotTransforms(profile());a=np.array([0,10,20.])
    r=t.clock(a,'robot_clock','host_clock',time_source='synthetic')
    np.testing.assert_allclose(r.output,a*1.00001+.25)
    assert r.params['time_source']=='synthetic'
    np.testing.assert_allclose(t.clock(r.output,'host_clock','robot_clock').output,a,atol=1e-12)
    with pytest.raises(ValueError,match='extrapolation'):t.clock([-1],'robot_clock','host_clock')
    with pytest.raises(ValueError):t.clock([1],None,'host_clock')


def test_frame_composition_inverse_points_vectors():
    p=profile();r=np.array([[0,-1,0,1],[1,0,0,0],[0,0,1,0],[0,0,0,1.]])
    p['transforms']['frames']['tool_to_base']={'source':'tool','target':'base','matrix':r.tolist(),'translation_unit':'m'}
    t=RobotTransforms(p)
    out=t.coordinates([[1000,0,0]],'tool','camera_optical',unit='mm')
    np.testing.assert_allclose(out.output,[[1,1,2]])
    np.testing.assert_allclose(t.coordinates(out.output,'camera_optical','tool').output,[[1,0,0]])
    np.testing.assert_allclose(t.coordinates([[1,0,0]],'tool','camera_optical',kind='vectors',unit='N').output,[[0,1,0]])
    poses=t.coordinates(np.eye(4),'tool','camera_optical',kind='poses')
    np.testing.assert_allclose(poses.output[:3,3],[1,0,2])


def test_fk_known_endpoint_batch_and_prismatic():
    p=profile();t=RobotTransforms(p)
    result=t.fk('arm',{'shoulder':np.array([0,90])})
    np.testing.assert_allclose(result.output[:,:3,3],[[1,0,0],[0,1,0]],atol=1e-12)
    joints=p['transforms']['chains']['arm']['joints']
    joints.append({'name':'slide','type':'prismatic','origin':np.eye(4).tolist(),'translation_unit':'m','axis':[1,0,0],'unit':'mm'})
    t=RobotTransforms(p)
    np.testing.assert_allclose(t.fk('arm',{'shoulder':90,'slide':1000}).output[:3,3],[0,2,0],atol=1e-12)
    with pytest.raises(ValueError,match='limits'):t.fk('arm',{'shoulder':181,'slide':0})
    with pytest.raises(ValueError,match='shapes'):t.fk('arm',{'shoulder':[0,1],'slide':[0]})


def test_camera_projection_and_distortion():
    p=profile();t=RobotTransforms(p)
    np.testing.assert_allclose(t.project('head',[[0,0,0],[1,0,0]],source_frame='base').output,[[320,240],[570,240]])
    p['transforms']['cameras']['head']['distortion']=[.1,0,0,0,0]
    np.testing.assert_allclose(RobotTransforms(p).project('head',[[1,0,0]],source_frame='base').output,[[576.25,240]])
    with pytest.raises(ValueError,match='positive depth'):t.project('head',[[0,0,-2]],source_frame='base')


@pytest.mark.parametrize('change', ['rotation','unit','cycle','camera','axis','clock'])
def test_bad_yaml_rejected(change):
    p=profile();c=p['transforms']
    if change=='rotation':c['frames']['base_to_camera']['matrix'][0][0]=2
    if change=='unit':c['frames']['base_to_camera']['translation_unit']='unknown'
    if change=='cycle':c['frames']['duplicate']=copy.deepcopy(c['frames']['base_to_camera'])
    if change=='camera':c['cameras']['head']['K'][0][0]=-1
    if change=='axis':c['chains']['arm']['joints'][0]['axis']=[0,0,2]
    if change=='clock':c['clocks']['robot_to_host']['scale']=0
    with pytest.raises(ValueError):load_profile(p)


def test_episode_binding_keeps_source_and_provenance(tmp_path):
    p=profile();root=tmp_path/'episode';root.mkdir()
    path=root/'trajectory.json';path.write_text(json.dumps({'data':[
        {'timestamp':0,'/joint_states':{'position':[0,0]}},
        {'timestamp':1,'/joint_states':{'position':[90,0]}}]}))
    before=path.read_bytes()
    with next(open_dataset(root,profile=p).iter_episodes()).opened() as ep:
        result=ep.transform_signal('/joint_states','position',target_unit='rad')
        np.testing.assert_allclose(result.output[:,0],[0,np.pi/2])
        fk=ep.forward_kinematics('arm')
        np.testing.assert_allclose(fk.output[:,:3,3],[[1,0,0],[0,1,0]],atol=1e-12)
        assert fk.source_mapping['joint_inputs']['shoulder']['index']==0
        assert fk.source_mapping['source_kind']=='feedback'
        ts=ep.transform_timestamps('/joint_states','host_clock')
        np.testing.assert_allclose(ts.output,[.25,1.25001])
        assert ep.channels['/joint_states'].clock=='robot_clock'
        np.testing.assert_allclose(ep.read_signal('/joint_states','position')[:,0],[0,90])
    assert before==path.read_bytes()


def test_fk_rejects_misaligned_channels(tmp_path):
    from edc.data.ir.dataset import Channel
    from types import SimpleNamespace
    from edc.data.transforms.episode import fk
    p=profile()
    p['transforms']['chains']['arm']['joints'].append({
        'name':'second','type':'revolute','axis':[0,0,1],'unit':'deg',
        'origin':np.eye(4).tolist(),'translation_unit':'m',
        'input':{'channel':'other','field':'position','index':0}})
    a=Channel('/joint_states','joint_state',['position'],2,'source',unit='deg',
        timestamps=[0,1],clock='robot_clock',time_source='acquisition',source_kind='feedback')
    b=copy.deepcopy(a);b.name='other';b.timestamps=[0,1.1]
    ep=SimpleNamespace(profile=p,channels={a.name:a,b.name:b},open=lambda:None,
        read_signal=lambda *args:np.array([[0.],[90.]]),id='test',path=tmp_path,selector={})
    with pytest.raises(ValueError,match='identical timelines'):fk(ep,'arm')
    b.timestamps=[0,1];b.source_kind='command'
    with pytest.raises(ValueError,match='command/feedback'):fk(ep,'arm')
    b.source_kind='feedback';b.unit='A'
    with pytest.raises(ValueError,match='Incompatible units'):fk(ep,'arm')


def test_vector_translation_not_applied_and_raw_nan_preserved():
    values=np.array([1.,np.nan])
    result=convert_units(values,'m','mm')
    assert result.output[0]==1000 and np.isnan(result.output[1])
    assert values[0]==1
    t=RobotTransforms(profile())
    np.testing.assert_allclose(t.coordinates([[0,0,1]],'base','camera_optical',kind='vectors',unit='m/s').output,[[0,0,1]])
    with pytest.raises(ValueError):t.coordinates([[np.nan,0,1]],'base','camera_optical')
