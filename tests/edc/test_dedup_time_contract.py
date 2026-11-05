"""Exact timestamp repetition is observable without inventing acquisition clocks."""
import hashlib
from types import SimpleNamespace
import numpy as np
import pytest
from edc import open_dataset
from edc.algorithms.quality.adapters.cpu import builtins,preflight
from edc.algorithms.quality.algorithms.Dedup_Check.dedup_check import check_timestamp_dedup
from edc.algorithms.quality.algorithms.Dedup_Check.method import assess


def plugin(name): return next(p for p in builtins() if p.spec.id==name)


def test_three_extra_occurrences_are_not_five_involved_rows():
    r=check_timestamp_dedup([100,110,110,120,120,120])
    assert r['involved_record_count']==5
    assert r['extra_duplicate_count']==3
    assert r['adjacent_equal_count']==3 and r['max_consecutive_count']==3
    assert r['duplicate_count']==0 and r['severity']=='pass'
    report=assess([100,110,110,120,120,120])
    assert report['verdict']=='OBSERVATION' and report['passed'] is None
    assert not report['assessment_complete']


def test_nonadjacent_reuse_and_long_run_are_separate():
    r=check_timestamp_dedup([1,2,1,3,3,3,3,3])
    assert r['extra_duplicate_count']==5 and r['involved_record_count']==7
    assert r['adjacent_equal_count']==4 and r['nonadjacent_reuse_count']==1
    assert r['duplicate_count']==5 and r['legacy_segments']==[{'start':3,'end':7,'count':5}]
    assert assess([1,2,1,3,3,3,3,3])['verdict']=='DO_NOT_USE'


@pytest.mark.parametrize('unit',['ms','unknown'])
def test_hdf5_one_stored_axis_not_independent_sensor_clocks(tmp_path,unit):
    h5py=pytest.importorskip('h5py');path=tmp_path/'time.h5'
    raw=np.array([1776694770000000000+i for i in (0,1,1,2)],np.int64)
    with h5py.File(path,'w') as f:
        f['timestamp']=raw;f['joint_pos']=np.arange(8).reshape(4,2);f['pose']=np.zeros((4,3))
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    profile={'schema_version':1,'id':'shared','version':'1','timestamp_column':'timestamp','timestamp_unit':unit,
             'channels':{'*':{'time_source':'acquisition','clock':'declared'},
                         'joint_pos':{'modality':'joint_space','fields':['position'],'unit':'rad', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}},
                         'pose':{'modality':'pose','fields':['position'],'unit':'m', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    with next(open_dataset(path,profile=profile).iter_episodes()).opened() as ep:
        assert preflight(plugin('Sync_Check'),ep)[0]=='missing_input'
        assert preflight(plugin('Root_Cause'),ep)[0]=='missing_input'
        assert preflight(plugin('Dedup_Check'),ep)[0]=='runnable'
        results=plugin('Dedup_Check').run(SimpleNamespace(episode=ep),{})
        assert len(results)==1
        td=results[0]['dedup']['timestamp_dedup']
        assert td['extra_duplicate_count']==1 and td['involved_record_count']==2
        assert results[0]['timestamp_unit']==unit
        assert results[0]['input_label']=='公共时间字段 timestamp'
        assert results[0]['inspection_scope']=='shared_timestamp_equality_only'
        assert not results[0]['establishes_acquisition_sync']
        if unit=='unknown':assert preflight(plugin('Check_Timestamp'),ep)[0]=='missing_input'
        else:
            assert preflight(plugin('Check_Timestamp'),ep)[0]=='runnable'
            assert plugin('Check_Timestamp').availability(ep,{})['availability']=='partial'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_exact_ros_nanoseconds_prevent_false_float_duplicates():
    from edc.data.ir.dataset import Channel
    ns=[1776694770000000000,1776694770000000001,1776694770000000002]
    c=Channel('a','other',[],3,'',time_source='acquisition',timestamps=[x*1e-9 for x in ns])
    ep=SimpleNamespace(channels={'a':c},profile={},describe_legacy=lambda:SimpleNamespace(topics={
        'a':SimpleNamespace(acquisition_timestamps_ns=ns,seqs=None)}))
    assert len(set(c.timestamps))==1
    r=plugin('Dedup_Check').run(SimpleNamespace(episode=ep),{})[0]
    assert r['dedup']['timestamp_dedup']['extra_duplicate_count']==0 and r['timestamp_unit']=='ns'


def test_generated_or_absent_time_never_enables_time_algorithms(tmp_path):
    import json
    for generated in (False,True):
        path=tmp_path/str(generated);path.mkdir()
        payload={'data':[{'pose':[i*.01,0,0]} for i in range(8)]}
        if generated:payload['fps']=20
        (path/'trajectory.json').write_text(json.dumps(payload))
        profile={'schema_version':1,'id':'x','version':'1','channels':{
            'pose':{'modality':'pose','fields':['position'],'unit':'m','time_source':'acquisition','side':'right', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
        with next(open_dataset(path,profile=profile).iter_episodes()).opened() as ep:
            for name in ('Check_Timestamp','Sync_Check','Root_Cause','Dedup_Check','Action_Smoothness'):
                assert preflight(plugin(name),ep)[0]=='missing_input',name


def test_invalid_right_header_remains_unavailable_not_repeated_zeroes():
    from edc.data.ir.dataset import Channel
    from edc.algorithms.quality.algorithms.Dedup_Check.algorithm import unavailable_dedup_inputs
    left=Channel('left','pose',['position'],4,'source',time_source='acquisition',timestamps=[1,2,3,4])
    right=Channel('right','pose',['position'],4,'source',time_source='unknown',
                  time_evidence={'unavailable_reason':'All four original header values are zero'})
    ep=SimpleNamespace(channels={'left':left,'right':right},profile={})
    missing=unavailable_dedup_inputs(ep)
    assert len(missing)==1 and missing[0]['channel']=='right'
    assert 'zero' in missing[0]['reason']
    assert plugin('Dedup_Check').availability(ep,{})['availability']=='partial'
    assert plugin('Dedup_Check').availability(ep,{'channel':'left'})['availability']=='available'
    ep.describe_legacy=lambda:SimpleNamespace(topics={
        'left':SimpleNamespace(seqs=None),'right':SimpleNamespace(seqs=None)})
    result=plugin('Dedup_Check').run(SimpleNamespace(episode=ep),{})
    assert len(result)==1 and result[0]['verdict']=='OBSERVATION'
    assert not result[0]['assessment_complete']


def test_selecting_one_dedup_channel_does_not_mark_other_valid_channel_missing():
    from edc.data.ir.dataset import Channel
    channels={name:Channel(name,'other',[],3,'source',time_source='acquisition',timestamps=[1,2,3])
              for name in ('left','right')}
    info=SimpleNamespace(topics={name:SimpleNamespace(seqs=None) for name in channels})
    ep=SimpleNamespace(channels=channels,profile={},describe_legacy=lambda:info)
    result=plugin('Dedup_Check').run(SimpleNamespace(episode=ep),{'channel':'left'})
    assert len(result)==1 and result[0]['input_channel']=='left'
    assert result[0]['unavailable_inputs']==[]
