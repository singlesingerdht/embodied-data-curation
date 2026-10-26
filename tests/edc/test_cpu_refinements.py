"""Scope, evidence and partial-assessment regressions; fixtures are synthetic."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from edc.data.ir.metadata import DataInfo, TopicInfo
from edc.algorithms.quality.shared.timing_scope import partition_timing_topics
from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import assess_from_ir
from edc.algorithms.quality.algorithms.Root_Cause.root_cause import analyze_root_cause, _detect_real_stall
from edc.algorithms.quality.algorithms.Root_Cause.run_check import assess as root_assess


def timing_info(extra=None):
    axes = {'/head/rgb': (np.arange(100) / 30).tolist(),
            '/arm/joint_states': (np.arange(100) / 30 + .001).tolist()}
    topics = {name: TopicInfo(name, count=len(ts), modality='rgb' if 'rgb' in name else 'joint_space',
                              timestamps=ts, time_source='container') for name, ts in axes.items()}
    topics.update(extra or {})
    info = DataInfo('/synthetic', format='mcap', topics=topics)
    reader = SimpleNamespace(require_timestamps=lambda info, **kw: {
        name: topic.timestamps for name, topic in info.topics.items() if topic.timestamps is not None})
    return info, reader


def test_metadata_does_not_reduce_timestamp_score_and_remains_auditable():
    baseline = assess_from_ir(*timing_info())
    metadata = TopicInfo('/instruction_data', msg_type='std_msgs/msg/String', count=1,
                         timestamps=[0.], time_source='container')
    result = assess_from_ir(*timing_info({metadata.name: metadata}))
    assert result['score'] == baseline['score']
    assert result['sub_indicators'] == baseline['sub_indicators']
    assert result['excluded_topics'] == [{'topic': '/instruction_data', 'reason': 'non_sampled_text',
                                         'sample_count': 1, 'included_in_score': False}]
    assert not result['timestamp_scope']['metadata_in_score']


@pytest.mark.parametrize('modality,msg_type,name', [
    ('task_text', '', '/instruction'), ('annotation', '', '/events'),
    ('other', 'sensor_msgs/msg/CameraInfo', '/calibration'), ('other', '', '/tf_static')])
def test_explicit_metadata_scope(modality, msg_type, name):
    topic = TopicInfo(name, modality=modality, msg_type=msg_type, count=10)
    selected, excluded = partition_timing_topics(DataInfo('/synthetic', topics={name: topic}), {name: [1.] * 10})
    assert not selected and excluded[0]['topic'] == name


def test_short_sensor_is_not_silently_filtered_as_metadata():
    single = TopicInfo('/wrist/rgb', modality='rgb', count=1, timestamps=[0.])
    info, reader = timing_info({single.name: single})
    result = assess_from_ir(info, reader)
    assert single.name in result['timestamp_scope']['included_topics']
    assert single.name in {sub['name'] for sub in result['sub_indicators']}
    assert not result['excluded_topics']


def test_metadata_cannot_supply_missing_second_sampled_stream():
    info, reader = timing_info({'/metadata': TopicInfo('/metadata', count=1, timestamps=[0.])})
    del info.topics['/arm/joint_states']
    result = assess_from_ir(info, reader)
    assert result['score'] is None and result['verdict'] == 'NOT_APPLICABLE'
    assert result['excluded_topics'][0]['topic'] == '/metadata'


def test_metadata_clock_does_not_block_but_sensor_clock_still_matters():
    from edc.data.ir.dataset import Channel
    from edc.algorithms.quality.adapters.cpu import builtins, preflight
    channels = {name: Channel(name, modality, [], 20, '', time_source='container',
                              clock=clock, timestamps=(np.arange(20) * .1).tolist())
                for name, modality, clock in [('/head/rgb', 'rgb', 'recorder'),
                                               ('/arm/joints', 'joint_space', 'recorder'),
                                               ('/metadata', 'other', None)]}
    ep = SimpleNamespace(channels=channels, profile={})
    for ident in ('Check_Timestamp', 'Root_Cause'):
        plugin = next(p for p in builtins() if p.spec.id == ident)
        assert preflight(plugin, ep)[0] == 'runnable'
        channels['/arm/joints'].clock = 'different'
        # Cadence observations remain available; only cross-clock algorithms block.
        assert preflight(plugin, ep)[0] == ('runnable' if ident == 'Check_Timestamp' else 'missing_input')
        channels['/arm/joints'].clock = 'recorder'


def gap_axis(gap=.676, index=50):
    ts = np.arange(150) * .01
    ts[index + 1:] += gap - .01
    return ts.tolist()


def test_root_cause_inspects_later_channels_and_is_order_independent():
    healthy = (np.arange(150) * .01).tolist()
    data = {'/healthy/joints': healthy, '/faulty/joints': gap_axis()}
    result = analyze_root_cause(data)
    assert result == analyze_root_cause(dict(reversed(list(data.items()))))
    assert result['max_gap_s'] == .676 and result['is_real_hole']
    event = result['channel_gap_evidence']['/faulty/joints']['events'][0]
    assert (event['before_index'], event['after_index']) == (50, 51)
    assert event['gap_s'] == pytest.approx(.676)
    assert _detect_real_stall({}) == (False, 0., 0.)


def test_root_does_not_count_same_source_topics_as_three_sources():
    axes = {f'/arm/{name}': gap_axis() for name in ('position', 'velocity', 'end_pose')}
    assert not analyze_root_cause(axes)['correlated_gaps']
    explicit = analyze_root_cause(axes, families={name: str(i) for i, name in enumerate(axes)})
    assert len(explicit['correlated_gaps']) == 1
    assert explicit['correlated_is_real_hole']
    assert explicit['gap_excess_s'] == .666  # not 3 * .666
    assert explicit['correlated_gaps'][0]['max_gap_ms'] == 676
    with pytest.raises(ValueError, match='families'):
        analyze_root_cause(axes, families={'/absent': 'sensor'})


def test_unrelated_long_gap_does_not_promote_short_correlated_event():
    axes = {f'/{name}/samples': gap_axis(.03) for name in ('a', 'b', 'c')}
    axes['/local/samples'] = gap_axis(.676, index=110)
    result = root_assess(axes)
    assert result['root_cause']['is_real_hole']
    assert not result['root_cause']['correlated_is_real_hole']
    assert result['sub_indicators'][0]['score'] == 70
    assert result['warnings']
    assert result['verdict'] == 'OBSERVATION'


@pytest.mark.parametrize('stability', [None, {}, {'stability': {}},
    {'primary': '/a/values', 'stability': {'/b/values': {'has_jump': False}}}])
def test_missing_stability_is_unknown_not_no_drift(stability):
    axes = {f'/{name}/values': (np.arange(50) * .01).tolist() for name in ('a', 'b')}
    result = root_assess(axes, stability)
    assert result['root_cause']['has_drift'] is None
    assert not result['assessment_complete']
    assert result['verdict'] == 'OBSERVATION'
    assert result['passed'] is None
    assert result['sub_indicators'][2]['score'] is None


def test_root_stability_detects_negative_drift_and_preserves_measured_failure():
    axes = {f'/{name}/values': (np.arange(50) * .01).tolist() for name in ('a', 'b')}
    result = root_assess(axes, {'primary': '/a/values', 'stability': {
        '/b/values': {'drift_rate_ms_per_min': -6., 'has_jump': False}}})
    assert result['assessment_complete']
    assert result['root_cause']['has_drift'] is True
    assert result['verdict'] == 'DO_NOT_USE'


@pytest.mark.parametrize('axes', [{}, {'/a': [1.]}, {'/a': [1., 2.], '/b': [1.]}])
def test_root_insufficient_intervals_never_pass(axes):
    result = root_assess(axes)
    assert result['score'] is None and not result['assessment_complete']


def test_root_partial_zero_available_weights_does_not_divide_by_zero():
    axes = {f'/{name}': (np.arange(50) * .01).tolist() for name in ('a', 'b')}
    result = root_assess(axes, weights={'跨通道Stall': 0, '周期性丢帧': 0, '时钟漂移': 1, '时钟跳变': 0})
    assert result['score'] is None and result['verdict'] == 'OBSERVATION'


def smoothness(positions):
    pytest.importorskip('scipy')
    from edc.algorithms.quality.algorithms.Action_Smoothness.action_evaluator import ActionEvaluator
    from edc.algorithms.quality.algorithms.Action_Smoothness.scoring import build_action_smoothness_module
    from edc.algorithms.quality.algorithms.Action_Smoothness.run_check import _report
    evaluator = ActionEvaluator()
    ts = np.arange(len(positions)) * .01
    jerk = evaluator.evaluate_arrays(positions, ts)
    slam = evaluator.detect_slam_freeze_arrays(positions, ts) if len(ts) >= 5 else {}
    slam['assessment_complete'] = len(ts) >= 5
    return _report(build_action_smoothness_module(jerk, slam)), jerk, slam


def test_no_stationary_segment_is_warning_not_perfect_smoothness():
    positions = np.zeros((400, 3)); positions[:, 0] = np.arange(400) * .001
    report, jerk, _ = smoothness(positions)
    assert jerk['details']['stationary_frames'] == 0
    assert report['warnings'] and report['sub_indicators'][0]['score'] is None
    assert not report['assessment_complete']
    assert report['score'] is None and report['verdict'] == 'OBSERVATION'


def test_static_pose_has_evaluable_segment_and_existing_score():
    report, jerk, slam = smoothness(np.zeros((400, 3)))
    assert jerk['details']['stationary_frames'] > 0
    assert jerk['score'] == 0 and not slam['has_freeze']
    assert report['assessment_complete'] and report['score'] == 100


def test_missing_stationary_evidence_does_not_hide_observed_freeze_failure():
    pytest.importorskip('scipy')
    from edc.algorithms.quality.algorithms.Action_Smoothness.scoring import build_action_smoothness_module
    from edc.algorithms.quality.algorithms.Action_Smoothness.run_check import _report
    report = _report(build_action_smoothness_module(
        {'score': 0., 'details': {'stationary_frames': 0}, 'assessment_complete': False},
        {'has_freeze': True, 'freeze_count': 1, 'segments': [], 'assessment_complete': True}))
    assert not report['assessment_complete'] and report['verdict'] == 'DO_NOT_USE'


def test_short_smoothness_samples_are_not_evaluated():
    report, _, _ = smoothness(np.zeros((3, 3)))
    assert all(sub['score'] is None for sub in report['sub_indicators'])
    assert report['verdict'] == 'OBSERVATION'


def test_execution_does_not_keep_trajectory_without_stationary_segment(tmp_path):
    pytest.importorskip('scipy')
    from edc.data.ir.dataset import open_dataset
    from edc.execution.runner import run
    source = tmp_path / 'episode'; source.mkdir()
    (source / 'trajectory.json').write_text(json.dumps({'data': [
        {'timestamp': i*.01, 'pose': [i*.001, 0., 0.]} for i in range(400)]}))
    profile = {'schema_version': 1, 'id': 'synthetic', 'version': '1', 'timestamp_unit': 's',
               'channels': {'pose': {'modality': 'pose', 'fields': ['position'], 'unit': 'm',
                                     'side': 'right', 'time_source': 'acquisition', 'clock': 'robot', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    with open_dataset(source, profile=profile) as dataset:
        summary = run(dataset, output=tmp_path/'out', algorithm_ids=['Action_Smoothness'])
    assert summary['status_counts'] == {'completed': 1}
    selection = json.loads(next((tmp_path/'out/_runs').glob('*/selection.json')).read_text())
    assert selection[0]['decision'] == 'unassessed'
    assert not selection[0]['assessment_complete']


def test_increasing_timestamps_and_repeated_images_are_independent(tmp_path):
    cv2 = pytest.importorskip('cv2'); pytest.importorskip('xxhash')
    from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
    from edc.algorithms.quality.algorithms.Dedup_Check.run_check import assess
    path = tmp_path/'lossless.avi'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'FFV1'), 30, (64, 64))
    assert writer.isOpened()
    image = np.random.default_rng(20261020).integers(40, 200, (64, 64, 3), dtype=np.uint8)
    for _ in range(31):
        writer.write(image)
    writer.release()
    pixels = FrameQualityDetector(sample_step=1, downscale=1).detect(path)
    timestamp_result = assess((np.arange(31)/30).tolist())
    assert timestamp_result['dedup']['timestamp_dedup']['duplicate_count'] == 0
    assert len(pixels['freeze_segments']) == 1
    assert pixels['freeze_segments'][0]['count'] == 30
    assert pixels['total_frames'] == 31
