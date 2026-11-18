"""Storage-independent input, original frame identity and explicit timing evidence."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from edc.execution.frames import inspect_frames, cache_frames, PreparedFrames
from edc.algorithms.value import runtime
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.algorithm import plugin, progress_views
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.timeline import FrameTimeline, normalize_subtasks

ROLES=('cam_high','cam_left_wrist','cam_right_wrist')


def episode(tmp_path,times=None,storage='frames',playback=None):
    times=np.asarray([100,100.09,100.21,100.32,100.4] if times is None else times)
    channels={k:NS(modality='rgb',image_available=True,count=len(times),clock='source',
        time_source='acquisition',timestamps=times.copy(),source_mapping={},source=str(tmp_path/'source'),
        time_evidence={'video_frame_mapping':'frame i = trajectory row i'}) for k in ('high','left','right')}
    def frames(name):
        for i in range(channels[name].count):
            yield {'index':i,'source_index':i,'playback_time':playback[i] if playback is not None else i/10,
                   'source_time':i/10,'image':np.full((16,16,3),i,np.uint8)}
    return NS(id='synthetic',path=tmp_path/'source',selector={'episode_index':4},format='episode',
              channels=channels,open=lambda:None,image_storage=lambda _:storage,iter_frames=frames)


def params(tmp_path):
    model=tmp_path/'model';model.mkdir(exist_ok=True)
    return {'views':dict(zip(ROLES,('high','left','right'))),'model':str(model),'task':'pick',
            'physical_signals':{},'subtasks':[{'step':0,'query':'pick','clip_start_frame':0,'clip_end_frame':5}],
            'filter_policy':'none'}


def test_native_images_need_no_fps_or_video(tmp_path,monkeypatch):
    ep=episode(tmp_path);cfg=params(tmp_path)
    monkeypatch.setattr(runtime,'check_runtime',lambda *a, **kw:None)
    m=progress_views(ep,cfg);t=FrameTimeline.from_manifest(m)
    assert m['coverage_complete'] and t.fps is None
    assert t.source.tolist()==list(ep.channels['high'].timestamps)
    assert plugin.preflight(ep,cfg)==('runnable','')
    cache_frames(ep,m,tmp_path/'run'/'input',[0,2,4])
    assert not list((tmp_path/'run').rglob('*.mp4'))
    assert len(list((tmp_path/'run').rglob('*.png')))==9
    assert PreparedFrames(m).image_path('cam_high',2).endswith('frame_000002.png')
    assert m['selector']=={'episode_index':4}


@pytest.mark.parametrize('storage',['video','frames'])
def test_variable_playback_and_source_gaps_do_not_impose_quality_gate(tmp_path,storage):
    ep=episode(tmp_path,[0,.1,.2,1,1.1],storage=storage,playback=[0,.1,.24,.3,.45])
    cfg=params(tmp_path);cfg['max_frame_gap_sec']=.01
    m=progress_views(ep,cfg)
    assert m['coverage_complete']
    assert m['views']['cam_high']['source_timestamps'][3]==1
    assert FrameTimeline.from_manifest(m).fps is None


def test_different_rates_nearest_ties_and_uncovered_tail(tmp_path):
    ep=episode(tmp_path,[0,.125,.25,.375,.5]);ep.channels['left'].timestamps=np.array([0,.25])
    ep.channels['left'].count=2
    m=progress_views(ep,params(tmp_path));left=m['views']['cam_left_wrist']
    assert left['reference_indices']==[0,0,1,None,None]
    assert left['pairing_offsets_sec'][:3]==[0,-.125,0]
    assert left['missing_reference_indices']==[3,4]
    assert not m['coverage_complete']


def test_explicit_row_mapping_supports_images_without_seconds(tmp_path):
    ep=episode(tmp_path)
    for c in ep.channels.values():
        c.timestamps=None;c.time_source='unknown';c.clock=None
        c.time_evidence={'timeline_scope':'shared_dataset_rows','source_row_mapping':'identity; explicit metadata'}
    m=progress_views(ep,params(tmp_path));t=FrameTimeline.from_manifest(m)
    assert m['coverage_complete'] and t.times is None
    assert normalize_subtasks(params(tmp_path)['subtasks'],t)[0]['_end_frame']==4
    with pytest.raises(ValueError,match='timeline'):
        normalize_subtasks([{'step':0,'query':'pick','clip_start_sec':0,'clip_end_sec':1}],t)


def test_matching_frame_counts_alone_do_not_establish_pairing(tmp_path):
    ep=episode(tmp_path)
    for c in ep.channels.values(): c.timestamps=None;c.clock=None
    m=progress_views(ep,params(tmp_path))
    assert not m['coverage_complete']
    assert m['views']['cam_left_wrist']['reference_indices']==[None]*5


def test_declared_clock_mapping_retained(tmp_path):
    ep=episode(tmp_path);ep.channels['left'].clock='other';ep.channels['left'].timestamps+=10
    ep.transform_timestamps=lambda *a:NS(output=ep.channels['high'].timestamps,source_mapping={'transform_id':'offset'})
    m=progress_views(ep,params(tmp_path));row=m['views']['cam_left_wrist']
    assert row['clock_mapping']=={'transform_id':'offset'}
    assert row['source_timestamps'][0]==110 and row['mapped_timestamps'][0]==100


def test_decoder_pts_do_not_replace_recorded_time(tmp_path):
    ep=episode(tmp_path,storage='video')
    for c in ep.channels.values():c.time_evidence={}
    m=progress_views(ep,params(tmp_path))
    assert m['views']['cam_high']['source_timestamps'] is None
    assert m['views']['cam_high']['playback_timestamps']==pytest.approx(np.arange(5)/10)


def test_seconds_compatibility_and_frame_half_open_bounds():
    t=FrameTimeline(np.arange(40)/10,10)
    old=normalize_subtasks([{'step':0,'query':'pick','clip_start_sec':0,'clip_end_sec':2}],t)[0]
    new=normalize_subtasks([{'step':0,'query':'pick','clip_start_frame':0,'clip_end_frame':20}],t)[0]
    assert old['_end_frame']==20 and new['_end_frame']==19
    with pytest.raises(ValueError):normalize_subtasks([{**old,'clip_start_frame':0,'clip_end_frame':20}],t)


def test_preflight_allows_nonuniform_optional_branches(tmp_path,monkeypatch):
    ep=episode(tmp_path);cfg=params(tmp_path)
    monkeypatch.setattr(runtime,'check_runtime',lambda *a, **kw:None)
    cfg['filter_policy']='training_filter_v8'
    assert plugin.preflight(ep,cfg)==('runnable','')
    cfg.pop('subtasks');cfg['segmentation']={'allow_remote_upload':True}
    assert plugin.preflight(ep,cfg)==('runnable','')


def test_upstream_reference_retained_without_inventing_quality_pass(tmp_path):
    ep=episode(tmp_path);cfg=params(tmp_path)
    refs={'quality':{'run_id':'q1','dataset':{'fingerprint':'verified-upstream'}}}
    m=inspect_frames(ep,cfg['views'],upstream=refs)
    assert m['upstream']==refs and not m['proves_acquisition_sync']
    assert inspect_frames(ep,cfg['views'])['quality_evidence']=='not_supplied'


def test_quality_reference_requires_matching_episode_and_release(tmp_path):
    ep=episode(tmp_path);cfg=params(tmp_path)
    ref={'fingerprint':'checked-by-workflow','episodes':[{'id':ep.id,'source':str(ep.path),'selector':ep.selector}]}
    upstream={'q':{'run_id':'quality-run','dataset':ref,'quality':{ep.id:{'decision':'keep','evidence':['record-1']}}}}
    m=inspect_frames(ep,cfg['views'],upstream=upstream)
    assert m['quality_references'][0]['dataset_fingerprint']=='checked-by-workflow'
    assert m['quality_evidence']=='upstream_reference'
    ref['episodes'][0]['selector']={'episode_index':5}
    assert inspect_frames(ep,cfg['views'],upstream=upstream)['quality_evidence']=='not_supplied'


def test_single_auxiliary_image_covers_only_observed_time(tmp_path):
    ep=episode(tmp_path,[0,.1,.2]);ep.channels['left'].timestamps=np.array([.1]);ep.channels['left'].count=1
    m=progress_views(ep,params(tmp_path))
    assert m['views']['cam_left_wrist']['reference_indices']==[None,0,None]


def test_source_and_playback_clip_choices_are_independent():
    timeline=FrameTimeline([100,101,103,104],playback_timestamps=[0,.1,.25,.4])
    task={'step':0,'query':'move','clip_start_sec':0,'clip_end_sec':.25}
    assert normalize_subtasks([task],timeline)[0]['_end_frame']==2
    task['clip_end_sec']=1
    assert normalize_subtasks([task],timeline,'source')[0]['_end_frame']==1


def test_v8_uses_source_seconds_for_nearby_closes_and_next_action():
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import training_filter_v8 as v8
    times=np.arange(300)/100
    t=FrameTimeline(times,count=300)
    event={'event_id':'p','source_type':'failed_grasp_blocked','arm':'right','decision':'keep','label':'old',
        'source_evidence':{'spike_start':100,'dip_frame':150},
        'evidence':{'nearby_close_events':[[120,'close']],'already_closed_30_frames':False,
                    'incomplete_close':True,'progress_drop':.2,'recovered':True,'recovery_frame':180}}
    assert v8.classify(event)[0]=='review'
    assert v8.classify(event,timeline=t)[0]=='exclude' # 20 frames, only .2 seconds
    interval,error=v8.bounds(event,{'subtask_results':[{'start_frame':0,'end_frame':299}]},
        {'right':[(120,'close'),(140,'open')]},300,timeline=t)
    assert not error and interval['end_frame']==139


def test_inclusive_event_window_cannot_include_first_sample_past_boundary():
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.offline_v3 import _bound
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.decision_export_v4 import _regression
    t=FrameTimeline([0,.1,1,3,3.2,3.5],count=6)
    a=NS(timeline=t)
    assert _bound(a,1,90,inclusive_end=True)==3 # upper target3.1; frame4 is too late
    evaluation={'subtask_results':[{'start_frame':0,'end_frame':5,'regressions':[{'frame_index':0},{'frame_index':3}]}]}
    assert _regression(evaluation,2,t)['frame_index']==0 # source distance1 vs2; frame distance2 vs1


def test_segmentation_derivatives_use_recorded_time():
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import segmenter
    times=np.array([0,.1,.3,.4,.8,1.,1.2])
    rows=[{'follow_left_position':[2*t,0,0],'follow_right_position':[0,t,0]} for t in times]
    speed=segmenter.compute_end_effector_speed(rows,30,times=times)
    jerk=segmenter.compute_jerk(rows,30,times=times)
    assert speed['left'][1:]==pytest.approx([2]*6)
    assert jerk['left']==pytest.approx(np.zeros(7),abs=1e-10)


def test_declared_rows_bind_video_time_without_guessing_rate(tmp_path):
    ep=episode(tmp_path,storage='video',playback=[0,.1,.22,.31,.4])
    for c in ep.channels.values():c.time_evidence={'timeline_scope':'shared_dataset_rows','source_row_mapping':'identity'}
    m=progress_views(ep,params(tmp_path))
    assert m['views']['cam_high']['source_timestamps']==pytest.approx(ep.channels['high'].timestamps)
    assert m['views']['cam_left_wrist']['pairing_offsets_sec']==[0]*5


def test_deprecated_gap_ignored_and_recorded_in_actual_adapter(tmp_path,monkeypatch):
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import algorithm
    from edc.execution.runner import Context
    ep=episode(tmp_path);cfg=params(tmp_path);cfg['max_frame_gap_sec']=.001
    cfg.update(frame_interval=2,mode='forward',filter_policy='none')
    work=tmp_path/'run';work.mkdir()
    monkeypatch.setattr(algorithm,'save_provenance',lambda *a,**kw:{'test':'synthetic'})
    monkeypatch.setattr(algorithm,'physical_inputs',lambda *a:NS(episode_id='synthetic',timestamps=ep.channels['high'].timestamps,arms={},warnings=[]))
    captured=[]
    monkeypatch.setattr(algorithm,'run_worker',lambda *a:(captured.append(a[-1]) or {'assessment_complete':False}))
    with pytest.warns(FutureWarning,match='deprecated and ignored'):
        algorithm.plugin.run(Context(None,ep,work),cfg)
    assert captured[0]['frame_manifest']['warnings']
    assert not list((work/'input').rglob('*.png'))


def test_declared_rows_allow_partial_view_and_preserve_gaps(tmp_path):
    ep=episode(tmp_path)
    for c in ep.channels.values():
        c.timestamps=None;c.clock=None
        c.time_evidence={'timeline_scope':'shared_dataset_rows','source_row_mapping':'identity'}
    ep.channels['left'].count=3
    m=progress_views(ep,params(tmp_path))
    assert m['views']['cam_left_wrist']['reference_indices']==[0,1,2,None,None]
    assert m['views']['cam_left_wrist']['pairing_basis']=='declared_shared_rows'
