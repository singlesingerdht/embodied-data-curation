"""Exercise the actual GRM/attribution worker with a controlled inference boundary."""
import json
from pathlib import Path
import numpy as np
import pytest
cv2=pytest.importorskip('cv2')
from edc.reporting.results import write_json
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import worker,evaluator


def request(tmp_path):
    inputs=tmp_path/'input';inputs.mkdir()
    for name in ('cam_high','cam_left_wrist','cam_right_wrist'):
        video=cv2.VideoWriter(str(inputs/f'{name}.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),10,(32,32))
        for i in range(40):video.write(np.full((32,32,3),i,dtype=np.uint8))
        video.release()
    arms={}
    for arm in ('left','right'):
        entries={}
        for role,unit,values in [('hand_position','m',np.full((40,1),.5)),
            ('hand_load','A',np.full((40,1),.2)),('arm_load','A',np.full((40,2),.2)),
            ('ee_position','m',np.zeros((40,3)))]:
            config={'unit':unit,'calibration_status':'confirmed','delta_threshold':.3}
            entries[role]={'values':values,'config':config,'description':{'signal_state':'constant'}}
            if role=='hand_position':entries[role]['openness']=np.full(40,.5)
        arms[arm]=entries
    write_json(tmp_path/'physical-signals.json',{'episode_id':'a','timestamps':np.arange(40)/10,'arms':arms,'warnings':[]})
    return {'workdir':str(tmp_path),'params':{'model':'unused-double','task':'pick then place','mode':'forward',
        'frame_interval':5,'gpu_memory_utilization':.8,'filter_policy':'none',
        'subtasks':[{'step':0,'query':'pick','clip_start_sec':0,'clip_end_sec':2},
                    {'step':1,'query':'place','clip_start_sec':2,'clip_end_sec':4}]},
        'views':{'cam_high':{'fps':10,'mapped_timestamps':(np.arange(40)/10).tolist()}}}


class Model:
    def inference_batch(self,samples):return [{'pred':f'<score>{10+i*12}</score>'} for i,_ in enumerate(samples)]
    def shutdown(self):pass


def install_double(monkeypatch,model):
    monkeypatch.setattr(evaluator.SubtaskTrajectoryEvaluator,'load_model',lambda self:setattr(self,'model',model))
    monkeypatch.setattr(evaluator,'plot_video_reward',lambda *a,**k:None)


def test_native_progress_worker_preserves_combined_curve_and_evidence(tmp_path,monkeypatch):
    req=request(tmp_path);install_double(monkeypatch,Model())
    out=worker.execute(req)
    assert out['filter_policy']=='none' and 'filter_suggestions' not in out
    assert out['assessment_complete'] is True
    assert list((tmp_path/'native').glob('subtask_*/raw_forward.json'))
    assert list((tmp_path/'native').glob('.frame_cache/cam_high/*.png'))
    curve=json.loads((tmp_path/'native/pred_vllm.json').read_text())
    assert curve[-1]['progress']>.5 # normalized across tasks, not a reset local curve


def test_invalid_model_response_is_failed_processing_with_raw_evidence(tmp_path,monkeypatch):
    req=request(tmp_path)
    class Bad(Model):
        def inference_batch(self,samples):return [{'pred':'unparseable'} for _ in samples]
    install_double(monkeypatch,Bad())
    with pytest.raises(ValueError,match='score tags'):worker.execute(req)
    assert list((tmp_path/'native').glob('subtask_*/raw_forward.json'))
    assert not (tmp_path/'native-result.json').exists()


def jittered_request(tmp_path):
    req=request(tmp_path)
    times=100+np.r_[0,np.cumsum(np.tile([.09,.11,.105],13))]
    req['views']['cam_high']['mapped_timestamps']=times.tolist()
    data=json.loads((tmp_path/'physical-signals.json').read_text())
    data['timestamps']=times.tolist()
    write_json(tmp_path/'physical-signals.json',data)
    return req,times


def test_jittered_worker_keeps_original_frames_and_records_source_time(tmp_path,monkeypatch):
    req,times=jittered_request(tmp_path);install_double(monkeypatch,Model())
    out=worker.execute(req)
    assert out['assessment_complete'] and not out['frame_timeline']['matches_playback']
    assert out['frame_timeline']['source_timestamps']==times.tolist()
    assert out['evaluation']['duration_time_basis']=='source'
    curve=json.loads((tmp_path/'native/pred_vllm.json').read_text())
    assert curve[1]['source_time_sec']==pytest.approx(times[curve[1]['frame_index']]-times[0])
    assert curve[1]['playback_time_sec']==curve[1]['frame_index']/10
    assert len(list((tmp_path/'input').glob('*.mp4')))==3


def test_model_initialization_failure_keeps_frame_timeline(tmp_path,monkeypatch):
    req,times=jittered_request(tmp_path)
    def fail(_): raise RuntimeError('controlled initialization failure')
    monkeypatch.setattr(evaluator.SubtaskTrajectoryEvaluator,'load_model',fail)
    with pytest.raises(RuntimeError,match='initialization failure'):
        worker.execute(req)
    evidence=json.loads((tmp_path/'native/frame-timeline.json').read_text())
    assert evidence['source_timestamps']==times.tolist()
    assert not (tmp_path/'native-result.json').exists()


def test_jittered_v8_worker_preserves_visual_results_with_missing_calibration(tmp_path,monkeypatch):
    req,_=jittered_request(tmp_path);req['params']['filter_policy']='training_filter_v8'
    install_double(monkeypatch,Model())
    out=worker.execute(req)
    assert not out['assessment_complete']
    assert out['components']['visual']['status']=='completed'
    assert out['components']['filter']['status']=='incomplete'
    assert (tmp_path/'native'/'pred_vllm.json').exists()


def test_v8_is_explicit_and_missing_native_calibration_cannot_exclude(tmp_path,monkeypatch):
    req=request(tmp_path);req['params']['filter_policy']='training_filter_v8'
    install_double(monkeypatch,Model());out=worker.execute(req)
    assert not out['assessment_complete'] and 'filter_suggestions' not in out
    assert out['components']['filter']['status']=='incomplete'


def test_verified_native_units_preserve_proactive_rule_events(tmp_path):
    from types import SimpleNamespace as NS
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import attributor
    n=100;grip=np.full(n,20.);load=np.ones(n);load[20:25]=12.
    curve={i:(.5 if i<40 else .3) for i in range(n)}
    def scan(calibrated):
        obj=object.__new__(attributor.EpisodeAttributor);obj.fps=30
        obj.grip_events={'right':[],'left':[]}
        obj.gfeat=NS(n=n,limitations={'left':[],'right':[]},gripper={'left':grip,'right':grip},
            grip_cur_rms={'left':np.ones(n),'right':load})
        if calibrated:
            obj.gfeat.signals=True
            obj.gfeat.proactive_inputs={'left':(grip,np.ones(n)),'right':(grip,load)}
            obj.gfeat.gripper={'left':grip/100,'right':grip/100}
            obj.gfeat.grip_cur_rms={'left':np.ones(n)/100,'right':load/100}
        return obj.proactive_grasp_scan(curve)
    native=scan(False);mapped=scan(True)
    assert mapped==native and len(mapped)==1
    assert mapped[0]['type']=='failed_grasp_blocked'
    assert mapped[0]['evidence']['he_max']==12.


def prepared_request(tmp_path, *, timed=True, calibrated=False):
    """Same physical fixture; GRM has no video dependency on this path."""
    from edc.execution.frames import cache_frames
    from types import SimpleNamespace as NS
    req,times=jittered_request(tmp_path)
    for path in (tmp_path/'input').glob('*.mp4'):path.unlink()
    views={}
    for role in ('cam_high','cam_left_wrist','cam_right_wrist'):
        views[role]={'channel':role,'count':40,'source_timestamps':times.tolist() if timed else None,
            'playback_timestamps':None,'reference_indices':list(range(40)),
            'missing_reference_indices':[],'pairing_basis':'declared_shared_rows'}
    manifest={'schema':'edc_frame_input_v1','frame_count':40,'reference':'cam_high',
              'views':views,'coverage_complete':True}
    def frames(_):
        for i in range(40):yield {'index':i,'image':np.full((32,32,3),i,np.uint8)}
    ep=NS(path=tmp_path/'source',iter_frames=frames)
    cache_frames(ep,manifest,tmp_path/'input',range(40))
    req.pop('views');req['frame_manifest']=manifest
    req['params']['subtasks']=[{'step':0,'query':'pick','clip_start_frame':0,'clip_end_frame':20},
                              {'step':1,'query':'place','clip_start_frame':20,'clip_end_frame':40}]
    if calibrated:
        data=json.loads((tmp_path/'physical-signals.json').read_text())
        for arm in data['arms'].values():
            for role in ('hand_position','hand_load'):
                arm[role]['config']['v8_native_scale']={'scale':1,'offset':0,'verified':True,'reference':'synthetic-test'}
        write_json(tmp_path/'physical-signals.json',data)
    return req


def test_prepared_images_complete_with_nonuniform_source_time_and_v8(tmp_path,monkeypatch):
    req=prepared_request(tmp_path,calibrated=True)
    req['params']['filter_policy']='training_filter_v8'
    install_double(monkeypatch,Model())
    monkeypatch.setattr(cv2,'VideoCapture',lambda *a:pytest.fail('prepared GRM cannot open video'))
    out=worker.execute(req)
    assert out['assessment_complete']
    assert all(x['status']=='completed' for x in out['components'].values())
    assert out['evaluation']['video_fps'] is None
    assert out['filter_suggestions']['policy_version']=='training_filter_v8'
    assert (tmp_path/'native/progress_chart.png').is_file()
    assert not list(tmp_path.rglob('*.mp4'))


def test_order_only_images_keep_visual_result_without_seconds_attribution(tmp_path,monkeypatch):
    req=prepared_request(tmp_path,timed=False);install_double(monkeypatch,Model())
    out=worker.execute(req)
    assert not out['assessment_complete']
    assert out['components']['visual']['status']=='completed'
    assert out['components']['physical']['status']=='incomplete'
    assert out['evaluation']['stagnation_evaluated'] is False
    curve=json.loads((tmp_path/'native/pred_vllm.json').read_text())
    assert all(x['source_time_sec'] is None and x['playback_time_sec'] is None for x in curve)
    assert (tmp_path/'native/progress_chart.png').is_file()


def test_missing_physical_input_retains_visual_curve(tmp_path,monkeypatch):
    req=prepared_request(tmp_path);install_double(monkeypatch,Model())
    data=json.loads((tmp_path/'physical-signals.json').read_text());data['input_error']='Required calibration unavailable'
    write_json(tmp_path/'physical-signals.json',data)
    out=worker.execute(req)
    assert not out['assessment_complete'] and out['evaluation']
    assert out['attribution']['assessment_complete'] is False
    assert 'calibration' in out['components']['physical']['limitations']['input'][0]
    assert (tmp_path/'native/attribution_report.txt').exists()


def test_missing_selected_view_does_not_invoke_model(tmp_path,monkeypatch):
    req=prepared_request(tmp_path)
    req['frame_manifest']['views']['cam_left_wrist']['reference_indices'][5]=None
    req['frame_manifest']['coverage_complete']=False
    monkeypatch.setattr(evaluator.SubtaskTrajectoryEvaluator,'load_model',lambda *a:pytest.fail('missing image'))
    out=worker.execute(req)
    assert not out['assessment_complete']
    assert out['components']['visual']['missing_reference_indices']['cam_left_wrist']==[5]


def test_prepared_model_failure_preserves_cached_images_and_mapping(tmp_path,monkeypatch):
    req=prepared_request(tmp_path)
    def fail(*a):raise RuntimeError('controlled model failure')
    monkeypatch.setattr(evaluator.SubtaskTrajectoryEvaluator,'load_model',fail)
    with pytest.raises(RuntimeError,match='controlled'):worker.execute(req)
    assert (tmp_path/'input-mapping.json').is_file()
    assert len(list((tmp_path/'input').rglob('*.png')))==120
    assert (tmp_path/'native/frame-timeline.json').is_file()


def test_same_rgb_images_and_variable_rate_video_reach_model_unchanged(tmp_path,monkeypatch):
    import av
    import hashlib
    from fractions import Fraction
    from PIL import Image
    from types import SimpleNamespace as NS
    from edc.data.readers.video import video_frames
    from edc.execution.frames import inspect_frames,cache_frames
    rng=np.random.default_rng(21)
    pixels=[rng.integers(0,256,(32,32,3),dtype=np.uint8) for _ in range(40)]
    pts=np.r_[0,np.cumsum(np.tile([90,110,105],13))]
    path=tmp_path/'original.mkv'
    with av.open(str(path),'w') as out:
        stream=out.add_stream('ffv1',rate=10);stream.width=32;stream.height=32;stream.pix_fmt='bgr0'
        for i,pixel in enumerate(pixels):
            frame=av.VideoFrame.from_ndarray(pixel,format='rgb24');frame.pts=int(pts[i]);frame.time_base=Fraction(1,1000)
            for packet in stream.encode(frame):out.mux(packet)
        for packet in stream.encode():out.mux(packet)
    decoded=list(video_frames(path))
    assert len(decoded)==40 and all(np.array_equal(a,b['image']) for a,b in zip(pixels,decoded))
    assert len(set(round(decoded[i+1]['playback_time']-decoded[i]['playback_time'],3) for i in range(39)))>1
    samples=[];curves=[];mappings=[]
    class Capture(Model):
        def inference_batch(self,items):
            batch=[]
            for item in items:
                hashes=[]
                for name in item['image']:
                    with Image.open(name) as image:hashes.append(hashlib.sha256(np.asarray(image).tobytes()).hexdigest())
                batch.append((item['id'],item['task'],hashes))
            samples.append(batch)
            return super().inference_batch(items)
    install_double(monkeypatch,Capture())
    for storage in ('frames','video'):
        work=tmp_path/storage;work.mkdir();req=prepared_request(work)
        times=req['frame_manifest']['views']['cam_high']['source_timestamps']
        channels={r:NS(modality='rgb',image_available=True,count=40,clock='recorded',time_source='acquisition',
            timestamps=times,source=str(path),source_mapping={},time_evidence={'video_frame_mapping':'explicit identity'}) for r in req['frame_manifest']['views']}
        def frames(_):
            if storage=='video':yield from video_frames(path)
            else:
                for i,pixel in enumerate(pixels):yield {'index':i,'source_index':i,'image':pixel}
        ep=NS(id='same-episode',path=tmp_path/'source',format='episode',selector={'episode_index':3},channels=channels,
            open=lambda:None,image_storage=lambda _:storage,iter_frames=frames)
        m=inspect_frames(ep,{r:r for r in channels});cache_frames(ep,m,work/'original-cache',range(40))
        req['frame_manifest']=m
        result=worker.execute(req)
        assert result['assessment_complete']
        curves.append([(x['frame_index'],x['progress'],x['source_time_sec']) for x in json.loads((work/'native/pred_vllm.json').read_text())])
        mappings.append({r:(v['reference_indices'],[f['source_index'] for f in v['frames']]) for r,v in m['views'].items()})
    assert curves[0]==curves[1] and mappings[0]==mappings[1]
    assert samples[:2]==samples[2:]


def test_automatic_segmentation_maps_display_video_back_to_source_frames(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import segmenter
    req=prepared_request(tmp_path);install_double(monkeypatch,Model())
    req['params'].pop('subtasks')
    req['params']['segmentation']={'method':'gemini','model':'double','api_key_env':'EDC_TEST_SEGMENT_KEY',
        'allow_remote_upload':True,'description':'synthetic test','subtasks':[{'step':0,'query':'pick'},{'step':1,'query':'place'}]}
    monkeypatch.setenv('EDC_TEST_SEGMENT_KEY','synthetic')
    data=json.loads((tmp_path/'physical-signals.json').read_text())
    for arm in data['arms'].values():
        arm['hand_position']['config']['segmentation_scale_reference']='synthetic'
        arm['joint_position']={'values':np.zeros((40,6)),'config':{'unit':'rad','calibration_status':'confirmed'},'description':{}}
    write_json(tmp_path/'physical-signals.json',data)
    deleted=[];seen={}
    client=NS(files=NS(delete=lambda **kw:deleted.append(kw['name'])))
    google=NS(genai=NS(Client=lambda **kw:client));monkeypatch.setitem(sys.modules,'google',google)
    monkeypatch.setattr(segmenter,'_upload_and_wait_ready',lambda client,path:NS(name=path.name))
    def plots(features,fps,work):
        seen['features']=features;return []
    monkeypatch.setattr(segmenter,'generate_feature_plots',plots)
    monkeypatch.setattr(segmenter,'feature_images_description',lambda plots:('synthetic features',[]))
    monkeypatch.setattr(segmenter,'load_feature_images',lambda paths:[])
    def align(**kw):
        seen['request']=kw
        duration=kw['video_duration']
        return {'subtasks':[{'step':0,'query':'pick','clip_start_sec':0,'clip_end_sec':duration/2},
                            {'step':1,'query':'place','clip_start_sec':duration/2,'clip_end_sec':duration}]}
    monkeypatch.setattr(segmenter,'align_subtasks_with_gemini',align)
    out=worker.execute(req)
    assert out['assessment_complete'] and len(deleted)==3
    mapping=json.loads((tmp_path/'segmentation-video/video-mapping.json').read_text())
    times=np.array(data['timestamps'])-data['timestamps'][0]
    assert seen['features']['timestamps']==pytest.approx(times)
    assert seen['request']['video_duration']==pytest.approx(times[-1])
    assert mapping['fps']==30 and abs(mapping['encoded_duration_sec']-times[-1])<=1/30
    for item in out['components']['segmentation']['intervals']:
        assert item['_start_frame']==mapping['reference_indices'][item['presentation_start_frame']]
        assert item['_end_frame']==mapping['reference_indices'][item['presentation_end_frame']]
        assert item['source_end_sec']==pytest.approx(times[item['_end_frame']])


def test_auto_segmentation_without_physical_evidence_is_incomplete(tmp_path,monkeypatch):
    req=prepared_request(tmp_path);req['params'].pop('subtasks')
    req['params']['segmentation']={'allow_remote_upload':True}
    monkeypatch.setattr(evaluator.SubtaskTrajectoryEvaluator,'load_model',lambda *a:pytest.fail('no task boundaries'))
    out=worker.execute(req)
    assert not out['assessment_complete'] and out['components']['segmentation']['status']=='incomplete'
    assert out['components']['visual']['status']=='not_run'
    assert not (tmp_path/'segmentation-video').exists()


def test_edc_cli_reads_native_images_and_preserves_incomplete_attribution(tmp_path,monkeypatch,capsys):
    import yaml
    import sys
    import hashlib
    import h5py
    from edc.execution.cli import main
    from edc.algorithms.value import runtime
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import algorithm
    source=tmp_path/'images.h5'
    with h5py.File(source,'w') as handle:
        handle['rgb']=np.stack([np.full((16,16,3),i,np.uint8) for i in range(40)])
        handle['timestamp']=np.arange(40)*.1
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    model=tmp_path/'model';model.mkdir();(model/'weights').write_bytes(b'synthetic')
    params={'model':str(model),'python':sys.executable,'task':'synthetic move',
        'views':{r:'rgb' for r in ('cam_high','cam_left_wrist','cam_right_wrist')},'physical_signals':{},
        'frame_interval':10,'subtasks':[{'step':0,'query':'move','clip_start_frame':0,'clip_end_frame':40}]}
    profile={'robot':{'name':'synthetic','confirmed_by':'test fixture'},'data':{'time':{'source':'timestamp','unit':'s'}},
        'cameras':{'head':{'source':'rgb','mount':'head'}},'checks':{'Progress_Stagnation_Attribution':params}}
    robot=tmp_path/'robot.yaml';robot.write_text(yaml.safe_dump(profile))
    monkeypatch.setattr(runtime,'check_runtime',lambda *a, **kw:None)
    install_double(monkeypatch,Model())
    def native(context,effective,package,payload):
        return worker.execute({'workdir':str(context.workdir),'params':effective,**payload})
    monkeypatch.setattr(algorithm,'run_worker',native)
    output=tmp_path/'results'
    assert main(['run',str(source),'--robot',str(robot),'--algorithm','Progress_Stagnation_Attribution',
        '--output',str(output),'--no-html'])==0
    record=json.loads(next(output.glob('_runs/*/*/records.jsonl')).read_text())
    assert record['schema_version']=='edc_record_v1' and record['status']=='completed'
    result=record['result']['reports'][0]
    assert result['evaluation'] and not result['assessment_complete']
    assert result['provenance']['shared_source_files']['edc.execution.frames']
    artifacts={Path(x).name for x in record['artifacts']}
    assert {'provenance.json','input-mapping.json','pred_vllm.json','progress_chart.png'}<=artifacts
    assert (output/'catalog.json').is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def test_invalid_segmentation_intervals_preserve_raw_response_without_inventing_bounds(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace as NS
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import segmentation,segmenter
    signals=NS(timestamps=np.arange(40)*.1,arms={})
    cfg={'task':'test','segmentation':{'method':'gemini','model':'double','api_key_env':'EDC_TEST_KEY',
        'allow_remote_upload':True,'description':'test','subtasks':[{'step':0,'query':'move'}]}}
    monkeypatch.setenv('EDC_TEST_KEY','synthetic')
    monkeypatch.setitem(sys.modules,'google',NS(genai=NS(Client=lambda **kw:NS(files=NS(delete=lambda **kw:None)))))
    monkeypatch.setattr(segmentation,'feature_payload',lambda *a:{'timestamps':[0,1]})
    monkeypatch.setattr(segmenter,'generate_feature_plots',lambda *a:[])
    monkeypatch.setattr(segmenter,'feature_images_description',lambda *a:('',[]))
    monkeypatch.setattr(segmenter,'load_feature_images',lambda *a:[])
    monkeypatch.setattr(segmenter,'extract_gripper_events',lambda *a:'')
    monkeypatch.setattr(segmenter,'extract_movement_events',lambda *a:'')
    monkeypatch.setattr(segmenter,'_upload_and_wait_ready',lambda *a:NS(name='synthetic'))
    monkeypatch.setattr(segmenter,'align_subtasks_with_gemini',lambda **kw:{'subtasks':[{'step':0,'query':'move'}]})
    views={r:{'fps':10,'mapped_timestamps':list(range(40)),'path':'synthetic'} for r in ('cam_high','cam_left_wrist','cam_right_wrist')}
    with pytest.raises(ValueError,match='valid evidence-backed'):
        segmentation.resolve_subtasks(cfg,signals,views,tmp_path)
    assert (tmp_path/'segmentation/raw-segmentation.json').exists()
    assert not (tmp_path/'segmentation/mapped-subtasks.json').exists()
