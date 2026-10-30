"""Exact topic contracts, camera semantics and source-frame/video separation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from edc import open_dataset, recommend, run
from edc.execution.runner import Context
from edc.data.ir.dataset import Channel, Episode
from edc.algorithms.quality.adapters.cpu import builtins, preflight
from edc.algorithms.quality.adapters.selection import ChannelSelection


def plugin(name):
    return next(p for p in builtins() if p.spec.id == name)


def numeric_fixture(tmp_path):
    import h5py
    p=tmp_path/'data.h5'
    with h5py.File(p,'w') as f:
        f['time']=np.arange(100,dtype=np.float64)*.01
        f['right']=np.zeros((100,3));f['left']=np.ones((100,3))
        f['bad']=np.full((100,3),np.nan)
    config={'schema_version':1,'id':'explicit','version':'1','timestamp_column':'time','timestamp_unit':'s',
        'channels':{name:{'modality':'pose','fields':['position'],'unit':'m','side':name,
            'source_kind':'feedback','time_source':'acquisition','clock':'one', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}} for name in ('right','left')}}
    return p,config


@pytest.mark.parametrize('ident',[p.spec.id for p in builtins()])
@pytest.mark.parametrize('names',[['absent'],['left','left'],[]])
def test_every_cpu_algorithm_rejects_missing_duplicate_empty_selection(tmp_path,ident,names):
    p,config=numeric_fixture(tmp_path)
    with open_dataset(p,profile=config) as ds:
        row=next(r for r in recommend(ds,params={ident:{'channels':names}})['episodes'][0]['algorithms'] if r['algorithm']['id']==ident)
    assert row['status']=='missing_input'


def test_numeric_reads_only_selected_fields_and_preserves_source(tmp_path):
    p,config=numeric_fixture(tmp_path);before=p.read_bytes()
    with open_dataset(p,profile=config) as ds:
        run(ds,output=tmp_path/'out',algorithm_ids=['Numeric_Check'],params={'Numeric_Check':{'channels':['left']}})
    rec=json.loads(next((tmp_path/'out/_runs').glob('*/Numeric_Check/records.jsonl')).read_text())
    assert rec['status']=='completed'
    assert {r['input_channel'] for r in rec['result']['reports']}=={'left'}
    assert p.read_bytes()==before


def test_explicit_action_selects_exact_same_side_channel(tmp_path):
    p,config=numeric_fixture(tmp_path)
    config['channels']['left']['side']='right'  # two feedback topics on same side
    with open_dataset(p,profile=config) as ds:
        run(ds,output=tmp_path/'out',algorithm_ids=['Action_Smoothness'],
            params={'Action_Smoothness':{'channel':'left','side':'right'}})
    rec=json.loads(next((tmp_path/'out/_runs').glob('*/Action_Smoothness/records.jsonl')).read_text())
    assert rec['status']=='completed'
    assert rec['result']['reports'][0]['input_channel']=='left'


@pytest.mark.parametrize('change',[{'side':'right'},{'channel':'right','channels':['right']},
    {'channels':['left','right']}])
def test_action_rejects_conflicts_and_ambiguity(tmp_path,change):
    p,config=numeric_fixture(tmp_path)
    params={'channel':'left',**change}
    if 'channels' in change and 'channel' not in change:params.pop('channel')
    with open_dataset(p,profile=config) as ds:
        ep=next(ds.iter_episodes());ep.open()
        assert preflight(plugin('Action_Smoothness'),ep,params)[0]=='missing_input'


def test_explicit_action_rejects_command_instead_of_feedback(tmp_path):
    p,config=numeric_fixture(tmp_path);config['channels']['right']['source_kind']='command'
    with open_dataset(p,profile=config) as ds:
        ep=next(ds.iter_episodes());ep.open()
        assert preflight(plugin('Action_Smoothness'),ep,{'channel':'right'})[0]=='missing_input'


def test_source_frame_mode_never_encodes_or_applies_container_metrics(tmp_path,monkeypatch):
    from edc.algorithms.quality.adapters.video import assess
    c=Channel('/chest/rgb','rgb',[],4,'source.mcap',image_available=True,
        representation={'camera_role':'chest','camera_role_status':'confirmed'})
    def forbidden(*args,**kwargs):raise AssertionError('Source-frame path must not materialize video')
    ep=SimpleNamespace(path=tmp_path/'source.mcap',channels={c.name:c},
        iter_frames=lambda n:({'index':i,'image':np.zeros((32,40,3),dtype=np.uint8)} for i in range(4)),
        materialize_video=forbidden,video_evidence=forbidden)
    out=assess(ep,{'input_mode':'frames','sample_step':1,'downscale':1},Context(None,ep,tmp_path))
    r=out[0]
    assert r['camera_role']=='chest' and r['threshold_set']=='camera_agnostic'
    assert r['inspection_object']=='source_decoded_frames'
    assert r['score'] is None and r['assessment_complete']  # Image-only: no container, frame check is complete.
    assert r['frame_quality']['real_frames']==4
    assert r['container_checks']['status']=='not_applicable'


def test_video_path_uses_robot_camera_role_and_single_evidence_input(tmp_path,monkeypatch):
    from edc.algorithms.quality.adapters.video import assess
    from edc.algorithms.quality.algorithms.Video_Integrity import run_check
    c=Channel('/misleading/wrist/head','rgb',[],4,'source.mcap',image_available=True,
        representation={'camera_role':'chest','camera_role_status':'confirmed'})
    seen=[]
    def check(path,**params):seen.append(params['cam_type']);return {'score':100}
    monkeypatch.setattr(run_check,'assess',check)
    ep=SimpleNamespace(path=tmp_path/'source.mcap',channels={c.name:c},
        materialize_video=lambda n,p:str(p),
        video_evidence=lambda n,inspection_input:{'inspection_object':'derived_preview','checked_path':inspection_input})
    r=assess(ep,{'input_mode':'video'},Context(None,ep,tmp_path))[0]
    assert seen==['chest'] and r['camera_role']=='chest'
    assert r['checked_path']==str(tmp_path/'rgb-0.mp4')


def test_materialized_video_reused_for_evidence_and_other_algorithm(tmp_path):
    source=tmp_path/'source.mcap';source.write_bytes(b'fixture')
    c=Channel('rgb','rgb',[],1,str(source),image_available=True)
    ep=Episode('fixture',source,'mcap',{})
    calls=[]
    def materialize(info,name):
        calls.append(name);p=tmp_path/'cached.mp4';p.write_bytes(b'fixture derived video');return str(p)
    ep._reader=SimpleNamespace(materialize_image_channel=materialize,close=lambda:None)
    ep._info=SimpleNamespace();ep.channels={'rgb':c}
    first=ep.materialize_video('rgb',tmp_path/'first.mp4')
    evidence=ep.video_evidence('rgb',inspection_input=first)
    second=ep.materialize_video('rgb',tmp_path/'second.mp4')
    assert calls==['rgb'] and Path(first).read_bytes()==Path(second).read_bytes()
    assert evidence['inspection_object']=='derived_preview'
    assert Path(second+'.timeline.json').is_file()
    ep.close();assert not ep._video_cache


@pytest.mark.parametrize('step',[1,3])
def test_same_pixels_frame_entry_matches_existing_video_detector(tmp_path,step):
    import cv2
    from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
    path=tmp_path/'sample.avi';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),30,(64,48))
    rng=np.random.default_rng(3)
    for i in range(40):writer.write(rng.integers(0,256,(48,64,3),dtype=np.uint8))
    writer.release()
    cap=cv2.VideoCapture(str(path));frames=[]
    while True:
        ok,pixels=cap.read()
        if not ok:break
        frames.append({'index':len(frames),'image':cv2.cvtColor(pixels,cv2.COLOR_BGR2RGB)})
    cap.release();assert len(frames)==40
    det=FrameQualityDetector(sample_step=step,downscale=2)
    assert det.detect(path)==det.detect_frames(iter(frames),40)


@pytest.mark.parametrize('frames,count',[
    ([],2),([{'index':1,'image':np.zeros((4,4,3),dtype=np.uint8)}],1),
    ([{'index':0,'image':np.zeros((4,4),dtype=np.uint16)}],1)])
def test_bad_source_frames_fail_instead_of_quality_pass(frames,count):
    from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
    with pytest.raises(ValueError):FrameQualityDetector().detect_frames(frames,count)


def test_depth_is_not_an_rgb_input(tmp_path):
    ep=SimpleNamespace(channels={'depth':Channel('depth','depth',[],2,'source.h5',image_available=True)},
        profile={},selector={},format='hdf5')
    status,reason=preflight(plugin('Video_Integrity'),ep,{'channels':['depth'],'input_mode':'frames'})
    assert status=='missing_input' and 'RGB' in reason


def test_frame_mode_rejects_video_composite_weights(tmp_path):
    ep=SimpleNamespace(channels={'rgb':Channel('rgb','rgb',[],2,'source.h5',image_available=True)},
        profile={},selector={},format='hdf5')
    status,reason=preflight(plugin('Video_Integrity'),ep,{'input_mode':'frames','weights':{'frame_quality':1}})
    assert status=='missing_input' and 'weights' in reason


@pytest.mark.parametrize('ident',['Sync_Check','Root_Cause'])
def test_topic_selection_does_not_invent_a_common_clock(tmp_path,ident):
    p,config=numeric_fixture(tmp_path)
    for c in config['channels'].values():c['clock']=None
    with open_dataset(p,profile=config) as ds:
        ep=next(ds.iter_episodes());ep.open()
        status,reason=preflight(plugin(ident),ep,{'channels':['right','left']})
    assert status=='missing_input' and 'clock' in reason


def test_materialized_original_video_keeps_evidence_and_frame_boundaries(tmp_path):
    source=tmp_path/'source.mp4';source.write_bytes(b'synthetic encoded fixture')
    Path(str(source)+'.frames.json').write_text('{"start_frame":3,"stop_frame":9}')
    ep=Episode('fixture',source,'mp4',{})
    ep.channels={'rgb':Channel('rgb','rgb',[],6,str(source),image_available=True)}
    ep._info=SimpleNamespace()
    ep._reader=SimpleNamespace(materialize_image_channel=lambda info,name:str(source),
        close=lambda:None,video_sources={'rgb':(str(source),{})})
    output=ep.materialize_video('rgb',tmp_path/'copy.mp4')
    assert ep.video_evidence('rgb',inspection_input=output)['inspection_object']=='original_encoded_video'
    assert Path(output+'.frames.json').read_bytes()==Path(str(source)+'.frames.json').read_bytes()


def test_frame_decode_failure_is_neutral_and_isolated_per_channel(tmp_path):
    from edc.algorithms.quality.adapters.video import assess
    channels={n:Channel(n,'rgb',[],2,'source.mcap',image_available=True) for n in ('broken','good')}
    def frames(name):
        if name=='broken':raise ValueError('corrupt source image')
        yield from ({'index':i,'image':np.zeros((32,40,3),dtype=np.uint8)} for i in range(2))
    ep=SimpleNamespace(path=tmp_path/'source.mcap',channels=channels,iter_frames=frames)
    reports=assess(ep,{'input_mode':'frames'},Context(None,ep,tmp_path))
    assert len(reports)==2
    assert reports[0]['processing_status']=='processing_error'
    assert reports[0]['score'] is None and reports[0]['passed'] is None
    assert reports[0]['verdict']=='NOT_APPLICABLE' and 'error' in reports[0]['ir_unavailable']
    assert reports[1]['frame_quality']['real_frames']==2
