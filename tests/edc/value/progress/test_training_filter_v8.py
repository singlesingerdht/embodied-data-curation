from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.training_filter_v8 import classify, bounds, convert, training_segments

def event(kind='failed_grasp_close', recovered=False):
    return dict(event_id='proactive_0',source_type=kind,arm='right',decision='keep',label='old',
        source_evidence=dict(close_frame=100,dip_frame=150),
        evidence=dict(progress_drop=.06,recovered=recovered,recovery_frame=180 if recovered else None))

def test_small_drop_and_recovery_are_not_proof_of_normality():
    assert classify(event())[0]=='review'
    assert classify(event(recovered=True))[0]=='review'

def test_current_and_retry_alone_not_confirmation():
    e=event();e['evidence'].update(progress_drop=.2,empty_current_signal=True,retry_events=[[155,'open']])
    assert classify(e)[0]=='review'


def test_empty_close_with_small_delta_and_retry_confirms_failure():
    e=event();e['evidence'].update(progress_drop=.05,empty_current_signal=True,
        close_current_delta=0.053,retry_events=[[155,'open'],[176,'close']])
    assert classify(e)[:2]==('exclude','failed_grasp_close')


def test_empty_close_with_delta_at_or_above_threshold_stays_review():
    e=event();e['evidence'].update(empty_current_signal=True,close_current_delta=0.116,
        retry_events=[[155,'open']])
    assert classify(e)[0]=='review'
    e['evidence']['close_current_delta']=None
    assert classify(e)[0]=='review'


def test_drop_window_capped_at_next_gripper_action():
    drop=dict(decision_id='regr_100_right',decision='review',label='unconfirmed_drop_by_release',
              severity=2,start_frame=100,end_frame=500,reason='r',evidence_ref='x')
    attribution={'rule_version':'progress_attribution_v3','training_events_v3':[],
                 'regression_results':{'regr_100_right':dict(arm='right',frame=100)}}
    common=dict(episode='e',timeline={'total_frames':600,'fps':30},method={})
    d,o,_,errors,_=convert(attribution,{'subtask_results':[{'start_frame':0,'end_frame':599}]},
        dict(**common,decisions=[drop]),dict(**common,observations=[]),
        {'right':[(90,'close'),(100,'open'),(160,'close'),(200,'open')]})
    assert not errors
    capped=d['decisions'][0]
    assert capped['end_frame']==159
    assert capped['window_cap']['boundary_basis']=='open_to_next_gripper_action_or_5s'
    assert capped['window_cap']['next_action_frame']==160


def test_drop_window_falls_back_to_five_second_horizon():
    # No gripper action for a long stretch: the 5 s horizon caps the window
    # even though the exporter stretched it to the subtask end (MIS-0124).
    drop=dict(decision_id='regr_100_right',decision='review',label='unconfirmed_drop_by_release',
              severity=2,start_frame=100,end_frame=500,reason='r',evidence_ref='x')
    attribution={'rule_version':'progress_attribution_v3','training_events_v3':[],
                 'regression_results':{'regr_100_right':dict(arm='right',frame=100)}}
    common=dict(episode='e',timeline={'total_frames':600,'fps':30},method={})
    d,o,_,errors,_=convert(attribution,{'subtask_results':[{'start_frame':0,'end_frame':599}]},
        dict(**common,decisions=[drop]),dict(**common,observations=[]),
        {'right':[(90,'close'),(100,'open')]})
    assert not errors
    capped=d['decisions'][0]
    assert capped['end_frame']==100+150  # 5 s at 30 fps
    assert capped['window_cap']['next_action_frame'] is None

def test_verified_blocked_attempt_survives_later_recovery():
    e=event('failed_grasp_blocked',True)
    e['source_evidence'].update(spike_start=100)
    e['evidence'].update(progress_drop=.2,nearby_close_events=[[102,'close']],
                         already_closed_30_frames=False,incomplete_close=True)
    assert classify(e)[0]=='exclude'
    interval,error=bounds(e,{'subtask_results':[{'start_frame':0,'end_frame':299}]},
                          {'right':[(102,'close'),(140,'open'),(160,'close')]},300)
    assert not error
    assert interval['end_frame']==139

def test_distinct_attempt_recovery_and_following_training_interval():
    e=event('failed_grasp_blocked',True)
    e['source_evidence'].update(spike_start=100)
    e['evidence'].update(progress_drop=.2,nearby_close_events=[[100,'close']],already_closed_30_frames=False,incomplete_close=True)
    common=dict(episode='e',timeline={'total_frames':300,'fps':30},method={})
    d,o,attempts,errors,_=convert({'rule_version':'progress_attribution_v3','training_events_v3':[e]},
        {'subtask_results':[{'start_frame':0,'end_frame':299}]},dict(**common,decisions=[]),
        dict(**common,observations=[]),{'right':[(100,'close'),(140,'open'),(160,'close')]})
    assert not errors
    assert d['decisions'][0]['end_frame']==139
    recovery=o['observations'][0]
    assert recovery['decision_id']!='proactive_0'
    assert recovery['related_attempt_id']=='proactive_0'
    assert recovery['start_frame']==180 and recovery['success_confirmed'] is False
    segments=training_segments(d)['segments']
    assert next(s for s in segments if s['start_frame']<=160<=s['end_frame'])['training_decision']=='keep'

def test_invalid_anchor_does_not_become_single_frame_exclude():
    e=event();e['source_evidence']['dip_frame']=90
    interval,error=bounds(e,{'subtask_results':[{'start_frame':0,'end_frame':299}]},{},300)
    assert interval is None and error=='invalid_temporal_anchor'
