"""Safety uses the framework's original images and calibration vocabulary."""
from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from edc.execution.frames import inspect_frames,cache_frames,render_videos
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.algorithm import inspect_safety_frames,physical_arrays


def episode(tmp_path,storage='frames'):
    times=np.array([100.,100.1,100.31,100.5])
    channels={k:NS(modality='rgb',image_available=True,count=4,clock='source',time_source='acquisition',
        timestamps=times.copy(),source_mapping={},source=str(tmp_path/'source'),
        time_evidence={'video_frame_mapping':'explicit row identity'}) for k in ('high','left')}
    def frames(name):
        for i in range(channels[name].count):
            yield {'index':i,'source_index':i,'playback_time':[0,.04,.12,.3][i],
                   'image':np.full((16,16,3),i*40,np.uint8)}
    return NS(id='synthetic',path=tmp_path/'source',selector={},format='episode',channels=channels,
              open=lambda:None,image_storage=lambda _:storage,iter_frames=frames)


@pytest.mark.parametrize('storage',['frames','video'])
def test_source_jitter_and_variable_playback_are_accepted(tmp_path,storage):
    ep=episode(tmp_path,storage)
    ep.channels['left'].timestamps=np.array([100.,100.5]);ep.channels['left'].count=2
    m=inspect_safety_frames(ep,{'views':{'faceImg':'high','leftImg':'left'}})
    assert m['views']['leftImg']['reference_indices']==[0,0,1,1]
    assert m['views']['faceImg']['source_timestamps']==[100.,100.1,100.31,100.5]
    assert m['coverage_complete'] and not m['proves_acquisition_sync']


def test_pairing_gaps_or_missing_source_time_cannot_be_complete(tmp_path):
    ep=episode(tmp_path);cfg={'views':{'faceImg':'high','leftImg':'left'}}
    ep.channels['left'].timestamps+=.01
    with pytest.raises(ValueError,match='coverage'):inspect_safety_frames(ep,cfg)
    ep.channels['high'].time_source='playback'
    with pytest.raises(ValueError,match='recorded source time'):inspect_safety_frames(ep,cfg)


def test_derived_video_keeps_source_duration_and_original_auxiliary_images(tmp_path):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.physical_worker import MappedEpisode
    ep=episode(tmp_path);m=inspect_safety_frames(ep,{'views':{'faceImg':'high','leftImg':'left'}})
    work=tmp_path/'run';cache_frames(ep,m,work/'frames',range(4))
    mapping=render_videos(m,work/'input',m['views']['faceImg']['source_timestamps'])
    assert mapping['duration_sec']==.5 and mapping['reference_indices'][-1]==3
    assert len(mapping['reference_indices'])==16
    obj=object.__new__(MappedEpisode);obj.frame_manifest=m
    from edc.execution.frames import PreparedFrames
    obj.prepared=PreparedFrames(m)
    # Original auxiliary frame index 2 is not encoded video frame index 2.
    assert np.all(obj.aux_frame('leftImg',2)==80)


def physical_episode(status='confirmed',unit='cm',source_kind='feedback'):
    channels={'rgb':NS(count=3)};bindings={};values={};calls=[]
    for role,u,size in [('position','m',3),('quaternion','1',4),('gripper',unit,1),('terminal_effort','N*m',1)]:
        meta={'source_kind':source_kind,'unit':u,'calibration_status':status,'calibration_reference':'synthetic measured fixture'}
        channels[role]=NS(time_source='acquisition',clock='recorded',field_spec=lambda _,meta=meta:meta)
        bindings[role]={'channel':role,'field':'value'}
        if role=='quaternion':bindings[role]['order']='wxyz'
        values[role]=np.ones((3,size))
        if role=='quaternion':values[role][:]=[1,0,0,0]
    bindings['T_arm_to_camera']=np.eye(4).tolist()
    def align(name,field,reference,**kw):
        calls.append((name,kw));return NS(output=values[name],source_mapping={},params={})
    return NS(channels=channels,align_signal=align),{'arms':{'left':bindings}},calls


def test_framework_confirmed_cm_is_used_without_algorithm_calibration_switch():
    ep,params,calls=physical_episode()
    out=physical_arrays(ep,params,'rgb')
    assert out['left_gripper'][:,0]==pytest.approx([.01]*3)
    assert dict(calls)['quaternion']['method']=='nearest'
    assert dict(calls)['position']['max_gap'] is None


@pytest.mark.parametrize('status,kind,unit',[('verified','feedback','cm'),('unknown','feedback','cm'),
    ('pending','feedback','cm'),('confirmed','command','cm'),('confirmed','feedback','unknown')])
def test_no_unknown_or_command_data_is_promoted(status,kind,unit):
    ep,params,_=physical_episode(status,unit,kind)
    with pytest.raises(ValueError):physical_arrays(ep,params,'rgb')


def test_duplicate_v62_status_switch_is_rejected():
    ep,params,_=physical_episode();params['arms']['left']['v62_gripper_calibration']={'native_thresholds_verified':True}
    with pytest.raises(ValueError,match='remove v62'):physical_arrays(ep,params,'rgb')


@pytest.mark.parametrize('field,value', [('calibration_status','confirmed'),('unit','m'),('verified',True)])
def test_bindings_cannot_override_robot_metadata(field,value):
    ep,params,calls=physical_episode(status='pending')
    params['arms']['left']['position'][field]=value
    with pytest.raises(ValueError,match='physical metadata belongs in the robot profile'):
        physical_arrays(ep,params,'rgb')
    assert not calls


def test_physical_windows_select_original_samples_by_time(tmp_path):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.run_four_event_pipeline import _enrich_lid_gripper_state
    # Deliberately nonuniform: frame count cannot stand in for elapsed time.
    times=[0,.1,.2,2,2.1,2.2,3]
    data=[{'follow_right_gripper':.1 if t<1 else .01,'follow_right_joint_effort':[0.]} for t in times]
    (tmp_path/'edc-physical-state.json').write_text(json.dumps({'timestamps':times,'data':data,'time_source':'acquisition'}))
    c=NS(type='lid_fall',start=1.,end=2.,evidence={})
    _enrich_lid_gripper_state([c],tmp_path)
    assert c.evidence['robot_state']['empty_closure_during_lid_transition']
    assert c.evidence['robot_state']['time_source']=='acquisition'


def test_contact_speed_uses_recorded_seconds_and_merge_uses_time():
    pytest.importorskip('torch')
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.pipeline import _box_contact_transition,merge_collision_events
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.collision_engine import CollisionEvent
    times=np.arange(31)/30
    assert _box_contact_transition([(0,20.)],30,5.,times)['closing_speed_px_s']==15.
    irregular=np.linspace(0,2,31)
    assert _box_contact_transition([(0,20.)],30,5.,irregular)['closing_speed_px_s']==7.5
    def ev(frame,time):return CollisionEvent(frame,time,'intersection','a','b',{},'test')
    # Many frames in two seconds merge; adjacent frames six seconds apart do not.
    assert len(merge_collision_events([ev(0,0),ev(300,2)]))==1
    assert len(merge_collision_events([ev(0,0),ev(1,6)]))==2


@pytest.mark.parametrize('order,values,unit', [
    ('wxyz',[2**-.5,0,0,2**-.5],'1'),
    ('xyzw',[0,0,2**-.5,2**-.5],'1'),
    ('rotvec',[0,0,np.pi/2],'rad'),
])
def test_orientation_is_delivered_in_native_xyzw(order,values,unit):
    ep,params,calls=physical_episode()
    ep.channels['quaternion'].field_spec=lambda field: {'source_kind':'feedback',
        'calibration_status':'confirmed','unit':unit}
    align=ep.align_signal
    def aligned(name,*args,**kwargs):
        result=align(name,*args,**kwargs)
        if name=='quaternion':result.output=np.tile(values,(3,1))
        return result
    ep.align_signal=aligned
    params['arms']['left']['quaternion']['order']=order
    evidence={}
    output=physical_arrays(ep,params,'rgb',evidence)
    from scipy.spatial.transform import Rotation
    rotated=Rotation.from_quat(output['left_quaternion'][0]).apply([1,0,0])
    assert rotated==pytest.approx([0,1,0],abs=1e-10)
    assert evidence['left_quaternion']['output_order']=='xyzw'


def test_selected_range_keeps_original_frame_identities(tmp_path):
    ep=episode(tmp_path)
    m=inspect_safety_frames(ep,{'views':{'faceImg':'high'},'frame_range':[1,3]})
    assert m['selected_reference_indices']==[1,2]
    assert m['views']['faceImg']['reference_indices']==[1,2]
    assert m['source_frame_count']==4 and m['frame_count']==2
    cache_frames(ep,m,tmp_path/'run'/'frames',range(2))
    from edc.execution.frames import PreparedFrames
    from PIL import Image
    with Image.open(PreparedFrames(m).image_path('faceImg',0)) as im:
        assert np.all(np.asarray(im)==40)


def test_gripper_geometry_and_native_rule_scales_are_separate():
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.physical_worker import MappedEpisode
    ep=object.__new__(MappedEpisode)
    ep.arms={'left':{'ee':np.array([[0,0,0,0,0,0,1]]),'gripper':np.array([.0216])}}
    state=ep.arm_state_at(0,'left')
    assert state['gripper']==pytest.approx(2.16)
    assert state['gripper_width_m']==pytest.approx(.0216)


@pytest.mark.parametrize('bad_depth', ['negative', 'empty', 'nan', 'shape', 'range'])
def test_depth_cannot_silently_fill_or_relabel_missing_input(tmp_path, bad_depth):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.algorithm import depth_array
    values=np.ones((2,8,8),dtype=np.float32)
    if bad_depth=='negative':values[0,0,0]=-1
    elif bad_depth=='empty':values[1]=0
    elif bad_depth=='nan':values[1,2,3]=np.nan
    elif bad_depth=='shape':values=values[:1]
    np.save(tmp_path/'depth.npy',values)
    ep=episode(tmp_path)
    params={'views':{'faceImg':'high'},'frame_range':[1,3],
        'depth':{'source':'monocular_estimate','unit':'m','registered_to_rgb':True,
        'registration_id':'synthetic fixture','episodes':{'synthetic':{
            'path':str(tmp_path/'depth.npy'),'rgb_channel':'high',
            'correspondence_evidence':'synthetic fixture',
            'frame_range':[0,2] if bad_depth=='range' else [1,3]}}}}
    with pytest.raises(ValueError):depth_array(ep,params,(2,8,8))


def test_depth_mm_conversion_preserves_source_file(tmp_path):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.algorithm import depth_array
    values=np.full((4,8,8),1200,dtype=np.uint16)
    path=tmp_path/'sensor.npy';np.save(path,values);before=path.read_bytes()
    ep=episode(tmp_path)
    params={'views':{'faceImg':'high'},'depth':{'source':'sensor_depth','unit':'mm',
        'registered_to_rgb':True,'registration_id':'synthetic fixture',
        'episodes':{'synthetic':{'path':str(path),'rgb_channel':'high',
            'correspondence_evidence':'synthetic fixture'}}}}
    result=depth_array(ep,params,values.shape,tmp_path/'converted.npy')
    assert np.allclose(result,1.2)
    assert path.read_bytes()==before
