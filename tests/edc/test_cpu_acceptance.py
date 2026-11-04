"""CPU correctness regressions prompted by the real development dataset audit."""
import json
from types import SimpleNamespace
import numpy as np
from edc.data.ir.dataset import open_dataset
from edc.execution.runner import run
from edc.algorithms.quality.adapters.cpu import builtins, preflight


def plugin(name):
    return next(p for p in builtins() if p.spec.id == name)


def test_raw_unknown_time_dedup_without_sync_claim(tmp_path):
    import h5py
    source = tmp_path / 'raw.h5'
    with h5py.File(source, 'w') as f:
        f['ticks'] = [1, 2, 3, 3, 3, 3, 3, 4]
    profile = dict(schema_version=1, id='raw', version='1', timestamp_column='ticks',
                   timestamp_unit='unknown', channels={'ticks': {'modality':'other', 'fields':['recorded_values']}})
    with open_dataset(source, profile=profile) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            assert ep.channels['ticks'].timestamps is None
            assert preflight(plugin('Sync_Check'), ep)[0] == 'missing_input'
            assert preflight(plugin('Dedup_Check'), ep)[0] == 'runnable'
            result = plugin('Dedup_Check').run(SimpleNamespace(episode=ep), {})[0]
            assert result['verdict'] == 'DO_NOT_USE'
            assert result['dedup']['timestamp_dedup']['duplicate_count'] == 5
            assert not result['establishes_acquisition_sync']
            assert result['timestamp_unit'] == 'unknown'
            assert result['sub_indicators'][1]['score'] is None


def test_dedup_checks_later_channel_and_single_sample(tmp_path):
    from edc.data.ir.dataset import Channel
    from edc.algorithms.quality.algorithms.Dedup_Check.run_check import assess
    assert assess([1])['dedup']['timestamp_dedup']['total_count'] == 1
    channels = {name: Channel(name, 'other', [], len(values), '', time_source='container', timestamps=values)
                for name, values in [('good', list(range(10))), ('bad', [1]*10)]}
    ep = SimpleNamespace(channels=channels, profile={}, describe_legacy=lambda: SimpleNamespace(
        topics={name: SimpleNamespace(seqs=None) for name in channels}))
    result = plugin('Dedup_Check').run(SimpleNamespace(episode=ep), {})
    assert [r['input_channel'] for r in result] == ['good', 'bad']
    assert result[1]['verdict'] == 'DO_NOT_USE'


def test_numeric_nonfinite_imu_and_empty_field(tmp_path):
    from edc.data.ir.dataset import Channel
    channels = {'imu': Channel('imu','imu',['lin_acc'],2,'',unit='m/s^2',value_available=True),
                'empty': Channel('empty','other',['values'],0,'',value_available=True)}
    ep = SimpleNamespace(channels=channels, read_signal=lambda name, field:
                         np.array([[0,0,9.81],[0,np.nan,9.81]]) if name=='imu' else np.empty((0,)))
    result = plugin('Numeric_Check').run(SimpleNamespace(episode=ep), {})
    assert result[0]['verdict'] == 'DO_NOT_USE'
    assert result[0]['nonfinite_indices'] == [[1,1]]
    assert result[1]['verdict'] == 'NOT_APPLICABLE'
    assert not result[1]['assessment_complete']


def test_root_cause_missing_stability_never_proves_no_drift():
    from edc.algorithms.quality.algorithms.Root_Cause.run_check import assess
    result = assess({'a': [i*.1 for i in range(50)], 'b': [i*.1+.001 for i in range(50)]})
    assert result['root_cause']['has_drift'] is None
    assert result['root_cause']['has_jump'] is None
    assert not result['assessment_complete']
    assert all(s['score'] is None for s in result['sub_indicators'][2:])


def test_pending_calibration_only_checks_finite_values(tmp_path):
    source=tmp_path/'episode';source.mkdir()
    (source/'trajectory.json').write_text(json.dumps({'data':[{'joint_pos':[0]},{'joint_pos':[100]}]}))
    mapping=dict(schema_version=1,id='pending',version='1',channels={'joint_pos':{
        'modality':'joint_space','fields':['position'],'unit':'rad','calibration_status':'pending', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}})
    with open_dataset(source,profile=mapping) as ds:
        run(ds,algorithm_ids=['Numeric_Check'],output=tmp_path/'results')
    selection=json.loads(next((tmp_path/'results/_runs').glob('*/selection.json')).read_text())
    assert selection[0]['decision']=='unassessed'
    assert not selection[0]['assessment_complete']


def test_pose_freeze_is_reflected_in_numeric_verdict():
    from edc.algorithms.quality.algorithms.Numeric_Check.run_check import assess
    positions = np.zeros((30,3))
    positions[:10,0] = np.arange(10)*.1
    positions[10:,0] = .9
    result = assess(pose_freeze_positions=positions)
    assert result['numeric']['pose_freeze']['has_freeze']
    assert result['verdict'] != 'ACCEPTABLE'


def test_joint_range_warning_cannot_hide_nonfinite_error():
    from edc.algorithms.quality.algorithms.Numeric_Check.numeric_check import check_joint_anomaly
    result = check_joint_anomaly(np.array([[np.nan],[3.]]),limits=[(-1.,1.)])
    assert result['severity']=='error'


def test_sync_without_record_time_only_checks_spans():
    from edc.algorithms.quality.algorithms.Sync_Check.run_check import assess
    result = assess({'a':[i*.1 for i in range(40)],'b':[i*.1+.001 for i in range(40)]})
    assert not result['assessment_complete'] and '缺少录包时间' in ' '.join(result['reasons'])


def test_sync_names_late_channel_and_moving_clock():
    from edc.algorithms.quality.algorithms.Sync_Check.run_check import assess
    ts = {'a': [i*.1 for i in range(1000)], 'b': [6+i*.1 for i in range(940)]}  # b 晚开始 6 秒
    clock = {'channels': {'a': {'record_minus_header_ms': 2.0, 'record_minus_header_change_ms': 1.0},
                          'b': {'record_minus_header_ms': 3.0, 'record_minus_header_change_ms': 250.0}}}
    result = assess(ts, clock=clock)
    text = ' '.join(result['reasons'])
    assert result['verdict'] == 'NEEDS_REVIEW' and result['assessment_complete']
    assert 'b 比最早的一路晚开始 6.0 秒' in text and 'b 的“录包时间 − 采集时间”在数据中变化了 250 毫秒' in text
    assert assess(ts, clock={**clock, 'salvage': {'status': 'unstable'}})['verdict'] == 'DO_NOT_USE'
    ts['b'] = [20+i*.1 for i in range(800)]  # 共同时段只有 80 秒里的 ~60 秒
    assert assess(ts, clock={'channels': {}})['verdict'] == 'DO_NOT_USE'


def test_sync_detects_negative_drift_with_shifted_overlap():
    from edc.algorithms.quality.algorithms.Sync_Check.sync_check import check_stability
    ref=np.arange(1000)*.1
    target=ref[100:]+np.linspace(.04,.001,900)
    result=check_stability({'ref':ref.tolist(),'target':target.tolist()},primary='ref')
    assert result['stability']['target']['drift_rate_ms_per_min'] > 20
    assert result['severity']=='error'
