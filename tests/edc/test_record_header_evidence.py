"""Raw paired-clock evidence, deliberately independent of scores or causal interpretation."""
from types import SimpleNamespace
import pytest
from edc.data.ir.dataset import Channel
from edc.algorithms.quality.shared.record_header import paired_time_evidence, evidence_sentences


def episode(deltas):
    base = 1773741605000000000
    h = [base + i*33333333 for i in range(len(deltas))]
    r = [t + int(d*1e6) for t,d in zip(h,deltas)]
    c = Channel('/arm', 'joint_space', ['position'], len(h), 'source.mcap')
    topic = SimpleNamespace(container_timestamps_ns=r, acquisition_timestamps_ns=h)
    return SimpleNamespace(channels={'/arm': c}, describe_legacy=lambda: SimpleNamespace(topics={'/arm': topic}))


@pytest.mark.parametrize('delta', [20, 2000, -20])
def test_constant_difference_has_no_deviation(delta):
    evidence = paired_time_evidence(episode([delta]*300), 5)
    row = evidence['channels'][0]
    assert row['median_difference_ms'] == delta
    assert row['max_absolute_deviation_ms'] == row['exceedance_count'] == 0
    assert row['max_deviation_message']['record_ns']-row['max_deviation_message']['header_ns'] == delta*1000000


def test_single_spike_exact_sentence_and_opt_in_threshold():
    deltas = [20]*300; deltas[150] = 500
    ep = episode(deltas)
    row = paired_time_evidence(ep)['channels'][0]
    assert row['max_deviation_message']['source_row'] == 150
    assert row['max_absolute_deviation_ms'] == 480
    assert '第151条：通常差值20 ms，本条差值500 ms，偏离通常水平+480 ms。' in row['summary']
    assert 'exceedance_count' not in row
    evidence = paired_time_evidence(ep, 100)
    assert evidence['channels'][0]['exceedance_count'] == 1
    assert evidence['channels'][0]['exceedance_periods'][0]['start_source_row'] == 150
    assert '+480 ms' in ' '.join(evidence_sentences(evidence))
    assert paired_time_evidence(ep, 480)['channels'][0]['exceedance_count'] == 0  # Strictly exceeds.


def test_sustained_change_bounded_details_and_even_median():
    row = paired_time_evidence(episode([20]*150+[500]*150), 100)['channels'][0]
    assert row['median_difference_ms'] == 260
    assert row['exceedance_count'] == 300
    assert len(row['exceedance_messages']) == 20 and row['details_truncated']
    assert row['exceedance_periods'][0]['count'] == 300


def test_missing_header_and_zero_rows_are_not_container_fallback():
    ep = episode([20]*3)
    ep.describe_legacy().topics['/arm'].acquisition_timestamps_ns = [None, 0, None]
    row = paired_time_evidence(ep)['channels'][0]
    assert row['status'] == 'unavailable'
    assert '无法计算' in evidence_sentences({'channels': [row]})[0]


@pytest.mark.parametrize('value', [-1, float('nan'), True, '1'])
def test_invalid_threshold(value):
    with pytest.raises(ValueError):
        paired_time_evidence(episode([20]), value)


def test_three_public_time_adapters_share_evidence_without_scoring_it(tmp_path):
    import hashlib
    import json
    import numpy as np
    pytest.importorskip('mcap')
    pytest.importorskip('rosbags')
    from mcap.writer import Writer
    from rosbags.typesys import Stores, get_typestore
    from edc import open_dataset, run
    from edc.reporting.batch import summarize, page
    # Controlled ROS2 MCAP: equal cadence, one record/header difference at row 150.
    store = get_typestore(Stores.ROS2_HUMBLE)
    scores = []
    ids = ['Check_Timestamp', 'Sync_Check', 'Root_Cause']
    for spike in (False, True):
        source = tmp_path/f'{spike}.mcap'
        with source.open('wb') as stream:
            writer = Writer(stream); writer.start(profile='ros2')
            for name, typ in [('/head','sensor_msgs/msg/Image'),('/arm','sensor_msgs/msg/JointState')]:
                definition,_ = store.generate_msgdef(typ, ros_version=2)
                sid = writer.register_schema(typ, 'ros2msg', definition.encode())
                cid = writer.register_channel(name, 'cdr', sid)
                for i in range(300):
                    ns = 1773741605000000000 + i*33333333 + (2000000 if name == '/arm' else 0)
                    stamp = store.types['builtin_interfaces/msg/Time'](ns//10**9, ns%10**9)
                    header = store.types['std_msgs/msg/Header'](stamp, 'base')
                    msg = (store.types[typ](header,2,2,'rgb8',0,6,np.zeros(12,np.uint8)) if name=='/head' else
                           store.types[typ](header,['q'],np.zeros(1),np.zeros(1),np.zeros(1)))
                    recorded = ns + (500000000 if spike and name=='/arm' and i==150 else 20000000)
                    writer.add_message(cid,recorded,bytes(store.serialize_cdr(msg,typ)),recorded)
            writer.finish()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        profile = {'schema_version':1,'id':'paired','version':'1','channels':{
            '*':{'time_source':'acquisition','clock':'common'},
            '/head':{'modality':'rgb','time_source':'acquisition','clock':'common'}}}
        out = tmp_path/f'out-{spike}'
        with open_dataset(source, profile=profile) as ds:
            run(ds, output=out, algorithm_ids=ids,
                params={ident:{'record_header_deviation_threshold_ms':100} for ident in ids})
        result_scores = []
        for ident in ids:
            record = json.loads(next(out.glob(f'_runs/*/{ident}/records.jsonl')).read_text())
            assert record['status'] == 'completed', record['reason']
            report = record['result']['reports'][0]
            row = next(r for r in report['record_header_differences']['channels'] if r['channel']=='/arm')
            assert row['max_absolute_deviation_ms'] == (480 if spike else 0)
            assert row['exceedance_count'] == (1 if spike else 0)
            if spike:
                assert '第151条' not in report['edc_note'] and '+480 ms' not in report['edc_note']
                detail = report['edc_item']['evidence'][0]
                assert detail['type'] == 'record_header_differences' and detail['data'] == report['record_header_differences']
            result_scores.append((report['score'],report['verdict'],report['edc_item']['verdict']))
        scores.append(result_scores)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert scores[0] == scores[1]
    summary = summarize(out)
    saved_summary = json.dumps(summary, sort_keys=True)
    readable = page([{**summary, 'name': 'synthetic spike', 'seconds': 0, 'source': str(source)}],
        {'robot': 'synthetic', 'inputs': str(source), 'finished': 'test', 'seconds': 0, 'jobs': 1, 'profile': 'test'})
    assert '+480 ms' not in readable and '记录与采集时间差' not in readable
    assert 'id="bag0-time' not in readable
    assert '第1条：通常差值20 ms' not in readable
    assert '没跑完' not in readable
    assert json.dumps(summary, sort_keys=True) == saved_summary
    for algorithm in summary['algos'].values():
        detail = next(e for item in algorithm['items'] for e in item['evidence']
                      if e['type'] == 'record_header_differences')
        row = next(r for r in detail['data']['channels'] if r['channel'] == '/arm')
        assert row['max_deviation_message']['message_number'] == 151
        assert row['max_deviation_message']['deviation_ms'] == 480
