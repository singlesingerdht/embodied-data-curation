"""Real reader/runner contract using synthetic 250 Hz poses and jittered camera headers."""
import copy
import hashlib
import json

import numpy as np
import pytest

from edc import open_dataset, recommend, run


def fixture(tmp_path, *, clock='shared', unit='m', arms=('right',)):
    pytest.importorskip('mcap'); pytest.importorskip('rosbags'); pytest.importorskip('scipy')
    from mcap.writer import Writer
    from rosbags.typesys import get_typestore, Stores
    store = get_typestore(Stores.ROS2_HUMBLE)
    source = tmp_path / 'source.mcap'
    source_times = 10 + np.arange(801) * .004
    reference_times = 10 + np.arange(-1, 100) / 30 + .001 * np.sin(np.arange(-1, 100))
    positions = np.zeros((len(source_times), 3))
    positions[:, 0] = np.minimum((source_times - 10) * .1, .08)
    positions[:, 1] = .00001 * np.sin(2 * np.pi * 12 * source_times)
    with source.open('wb') as stream:
        writer = Writer(stream); writer.start(profile='ros2')
        for typ, topics in [('geometry_msgs/msg/PoseStamped', [f'/{arm}/pose' for arm in arms]),
                            ('sensor_msgs/msg/Image', ['/camera'])]:
            definition, _ = store.generate_msgdef(typ, ros_version=2)
            schema = writer.register_schema(typ, 'ros2msg', definition.encode())
            for topic in topics:
                channel = writer.register_channel(topic, 'cdr', schema)
                times = reference_times if topic == '/camera' else source_times
                for i, t in enumerate(times):
                    ns = int(round(t * 1e9))
                    stamp = store.types['builtin_interfaces/msg/Time'](ns // 10**9, ns % 10**9)
                    header = store.types['std_msgs/msg/Header'](stamp, 'test_frame')
                    if topic == '/camera':
                        msg = store.types[typ](header, 2, 2, 'rgb8', 0, 6, np.zeros(12, dtype=np.uint8))
                    else:
                        xyz = positions[i] * (1000 if unit == 'mm' else 1)
                        point = store.types['geometry_msgs/msg/Point'](*xyz)
                        quat = store.types['geometry_msgs/msg/Quaternion'](0., 0., 0., 1.)
                        msg = store.types[typ](header, store.types['geometry_msgs/msg/Pose'](point, quat))
                    # Intentionally different recorder clock: it must never reach the method.
                    writer.add_message(channel, ns + 100_000_000_000, bytes(store.serialize_cdr(msg, typ)), ns)
        writer.finish()
    profile = {'schema_version': 1, 'id': 'timeline_fixture', 'version': '1',
        'parts': {arm: {'type': 'arm', 'side': arm} for arm in arms},
        'channels': {'/camera': {'logical_name': 'rgb/head', 'modality': 'rgb',
            'time_source': 'acquisition', 'clock': clock}}}
    for arm in arms:
        profile['channels'][f'/{arm}/pose'] = {
            'logical_name': f'pose/arm/{arm}/feedback', 'modality': 'pose',
            'robot_part': arm, 'side': arm, 'fields': ['position', 'orientation'],
            'source_kind': 'feedback', 'unit': unit, 'time_source': 'acquisition', 'clock': clock,
            'field_metadata': {'orientation': {'unit': '1'}},
            'representation': {'quantity': 'position', 'semantic_status': 'confirmed'}}
    params = {'timeline': {'mode': 'reference_channel', 'reference_channel': 'rgb/head',
                          'time_source': 'acquisition', 'resample': 'linear',
                          'clock_policy': 'require_common_or_explicit_mapping'},
              'expected_rate_hz': 30, 'window_length': 11,
              'vel_threshold_factor': .1, 'jerk_threshold_m_s3': 15}
    return source, profile, params, source_times, reference_times, positions


def execute(tmp_path, source, profile, params):
    with open_dataset(source, profile=profile) as ds:
        pool = recommend(ds, params={'Action_Smoothness': params})
        row = next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id'] == 'Action_Smoothness')
        run(ds, algorithm_ids=['Action_Smoothness'], params={'Action_Smoothness': params}, output=tmp_path/'out')
    records = [json.loads(line) for line in next((tmp_path/'out/_runs').glob(
        '*/Action_Smoothness/records.jsonl')).read_text().splitlines()]
    evidence = [json.loads(p.read_text()) for p in sorted((tmp_path/'out/_runs').glob(
        '*/Action_Smoothness/artifacts/*/timeline.json'))]
    return row, records, evidence


@pytest.mark.parametrize('unit', ['m', 'mm'])
def test_reference_uses_actual_camera_headers_and_exact_interpolation_mapping(tmp_path, monkeypatch, unit):
    source, profile, params, ts, camera, positions = fixture(tmp_path, unit=unit)
    from edc.algorithms.quality.algorithms.Action_Smoothness import run_check
    original = run_check.assess_arrays
    seen = []
    def capture(values, timestamps, **kwargs):
        seen.append((values.copy(), timestamps.copy()))
        return original(values, timestamps, **kwargs)
    monkeypatch.setattr(run_check, 'assess_arrays', capture)
    before = hashlib.sha256(source.read_bytes()).hexdigest(); config_before = copy.deepcopy(profile)
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'available'
    assert any(b['parameter'] == 'timeline.reference_channel' and b['channel'] == '/camera'
               for b in records[0]['channel_bindings'])
    assert records[0]['status'] == 'completed'
    plan = evidence[0]
    target = np.asarray(plan['target_timestamps_s'])
    assert not np.allclose(np.diff(target), 1/30, atol=1e-5)
    assert plan['excluded_reference_rows'] and plan['clock_status'] == 'common_clock'
    assert plan['resample'] == 'linear' and not plan['extrapolation']
    assert plan['proves_acquisition_sync'] is False
    np.testing.assert_array_equal(seen[0][1], target)
    mapping = plan['source_mapping']['rows']
    left, right, weights = [np.asarray(mapping[k]) for k in ('left_indices', 'right_indices', 'right_weights')]
    np.testing.assert_allclose(seen[0][0], (1-weights[:, None])*positions[left] + weights[:, None]*positions[right], atol=1e-12)
    direct = original(seen[0][0], target)
    assert records[0]['result']['reports'][0]['raw']['jerk']['score'] == direct['raw']['jerk']['score']
    assert direct['raw']['jerk']['details']['jerk_signal'] == 'filtered_residual'
    assert profile == config_before and hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('clock', [None, 'different'])
def test_unknown_or_different_clock_is_review_without_running_jerk(tmp_path, monkeypatch, clock):
    source, profile, params, *_ = fixture(tmp_path, clock=clock)
    if clock == 'different': profile['channels']['/camera']['clock'] = 'head_clock'
    from edc.algorithms.quality.algorithms.Action_Smoothness import run_check
    monkeypatch.setattr(run_check, 'assess_arrays', lambda *a, **k: pytest.fail('clock gate bypassed'))
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'partial' and 'clock' in row['reason']
    result = records[0]['result']['reports'][0]
    assert records[0]['status'] == 'completed'
    assert result['verdict'] == 'NEEDS_REVIEW' and result['score'] is None and not result['assessment_complete']
    assert 'raw' not in result and evidence[0]['clock_status'] == 'unconfirmed'
    selection = json.loads(next((tmp_path/'out/_runs').glob('*/selection.json')).read_text())
    assert selection[0]['decision'] != 'keep'


def test_explicit_clock_mapping_is_consumed_and_recorded(tmp_path):
    source, profile, params, *_ = fixture(tmp_path)
    profile['channels']['/camera']['clock'] = 'head_clock'
    profile['transforms'] = {'clocks': {'arm_to_head': {'source': 'shared', 'target': 'head_clock',
        'scale': 1.0001, 'offset_seconds': .005, 'valid_range': [10, 14], 'provenance': 'synthetic calibration'}}}
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'available'
    assert evidence[0]['clock_status'] == 'explicit_mapping'
    assert evidence[0]['clock_transform']['mapping'] == 'arm_to_head'
    assert evidence[0]['clock_transform']['definition']['provenance'] == 'synthetic calibration'
    np.testing.assert_allclose(evidence[0]['mapped_source_timestamps_s'],
                               np.asarray(evidence[0]['source_timestamps_s'])*1.0001+.005)
    assert records[0]['result']['reports'][0]['timeline']['clock_status'] == 'explicit_mapping'


@pytest.mark.parametrize('change', ['duplicate', 'backward', 'out_of_range', 'no_overlap'])
def test_invalid_reference_or_mapping_cannot_pass(tmp_path, change):
    # Runtime timestamp validation independent of the reader rejecting bad headers.
    from edc.algorithms.quality.adapters.action import timeline_plan
    from edc.algorithms.quality.adapters.cpu import selected_inputs, selected_pose
    source, profile, params, *_ = fixture(tmp_path)
    from edc.data.ir.units import SIDataset
    with open_dataset(source, profile=profile) as ds:
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            if change in {'duplicate', 'backward'}:
                ep.channels['/camera'].timestamps[10] = ep.channels['/camera'].timestamps[9] - (1 if change == 'backward' else 0)
            if change == 'no_overlap':
                ep.channels['/camera'].timestamps = [t+100 for t in ep.channels['/camera'].timestamps]
            if change == 'out_of_range':
                ep.channels['/camera'].clock = 'head_clock'
                ep.profile['transforms'] = {'clocks': {'bad': {'source': 'shared', 'target': 'head_clock',
                    'scale': 1, 'offset_seconds': 0, 'valid_range': [0, 1], 'provenance': 'synthetic'}}}
            params['channel'] = '/right/pose'
            params['timeline']['reference_channel'] = '/camera'
            view = selected_inputs(ep, params, 'Action_Smoothness')
            plan = timeline_plan(view, selected_pose(view, params), params)
            assert plan['issues'] and 'target_timestamps_s' not in plan


@pytest.mark.parametrize('expected,availability', [(30, 'partial'), (250, 'available')])
def test_native_250hz_does_not_silently_use_30hz_calibration(tmp_path, expected, availability):
    source, profile, params, *_ = fixture(tmp_path)
    params.update(timeline={'mode': 'native', 'time_source': 'acquisition'}, expected_rate_hz=expected)
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == availability
    result = records[0]['result']['reports'][0]
    assert result['raw']['jerk']['details']['n_frames'] == 801
    assert evidence[0]['source_mapping']['rows'] == 'identity'
    assert evidence[0]['observed_median_rate_hz'] == pytest.approx(250)
    if expected == 30:
        assert result['verdict'] == 'NEEDS_REVIEW' and result['score'] is None
        assert not result['assessment_complete'] and 'diagnostic_result' in result
    else:
        assert result['timeline']['calibration_rate_compatible']


@pytest.mark.parametrize('change', [
    {'timeline': {'mode': 'reference_channel'}},
    {'timeline': {'mode': 'reference_rate', 'reference_channel': 'rgb/head', 'time_source': 'container'}},
    {'timeline': {'mode': 'reference_channel', 'reference_channel': '/absent'}},
    {'timeline': {'mode': 'native', 'reference_channel': 'rgb/head'}},
    {'timeline': {'mode': 'reference_channel', 'reference_channel': 'rgb/head', 'resample': 'nearest'}},
    {'window_length': 10}, {'rate_tolerance_ratio': 1}, {'jerk_threshold_m_s3': 5}])
def test_parameter_contract_rejects_missing_or_conflicting_input(tmp_path, change):
    source, profile, params, *_ = fixture(tmp_path)
    params.update(change)
    row, records, _ = execute(tmp_path, source, profile, params)
    assert row['status'] == 'missing_input' and all(r['status'] == 'missing_input' for r in records)


def test_both_arms_use_same_shared_reference_without_losing_pose_scope(tmp_path):
    source, profile, params, *_ = fixture(tmp_path, arms=('left', 'right'))
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'available'
    assert {r['instance_id'] for r in records} == {'left', 'right'}
    assert all(p['reference_channel'] == '/camera' for p in evidence)
    assert {p['source_channel'] for p in evidence} == {'/left/pose', '/right/pose'}


def test_threshold_parameter_reaches_original_inverse_scoring():
    from edc.algorithms.quality.algorithms.Action_Smoothness.scoring import build_action_smoothness_module
    report = build_action_smoothness_module(
        {'score': 20, 'details': {'threshold': 30}}, {'has_freeze': False})
    assert report['sub_indicators'][0]['t_min'] == 30
    assert report['sub_indicators'][0]['score'] > 0


def test_nonfinite_positions_are_not_imputed_or_passed(tmp_path, monkeypatch):
    from edc.algorithms.quality.adapters import action
    from edc.algorithms.quality.adapters.cpu import selected_inputs, selected_pose
    from edc.data.ir.units import SIDataset
    from edc.execution.runner import Context
    source, profile, params, *_ = fixture(tmp_path)
    with open_dataset(source, profile=profile) as ds:
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            params['channel'] = '/right/pose'; params['timeline']['reference_channel'] = '/camera'
            view = selected_inputs(ep, params, 'Action_Smoothness')
            xyz = view.read_signal('/right/pose', 'position').copy(); xyz[20, 0] = np.nan
            monkeypatch.setattr(view, 'read_signal', lambda *a: xyz)
            result = action.assess(view, selected_pose(view, params), params,
                                   Context(dataset=None, episode=ep, workdir=tmp_path))
    assert result['verdict'] == 'NEEDS_REVIEW' and result['score'] is None
    assert 'raw' not in result and any('finite' in r for r in result['reasons'])
    assert np.isnan(xyz[20, 0])


def test_short_overlap_does_not_change_filter_window(tmp_path):
    from edc.algorithms.quality.adapters.action import timeline_plan
    from edc.algorithms.quality.adapters.cpu import selected_inputs, selected_pose
    from edc.data.ir.units import SIDataset
    source, profile, params, *_ = fixture(tmp_path)
    with open_dataset(source, profile=profile) as ds:
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            ep.channels['/camera'].timestamps = ep.channels['/camera'].timestamps[5:10]
            ep.channels['/camera'].count = 5
            params['channel'] = '/right/pose'; params['timeline']['reference_channel'] = '/camera'
            view = selected_inputs(ep, params, 'Action_Smoothness')
            plan = timeline_plan(view, selected_pose(view, params), params)
    assert 'target_timestamps_s' not in plan and any('window_length' in r for r in plan['issues'])


def test_container_reference_keeps_method_pass_and_raw_axes_for_both_arms(tmp_path):
    source, profile, params, ts, camera, _ = fixture(tmp_path, clock=None, arms=('left', 'right'))
    params['timeline'].update(time_source='container', max_gap_s=.1)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'available'
    assert {r['instance_id'] for r in records} == {'left', 'right'}
    for record, plan in zip(records, evidence):
        report = record['result']['reports'][0]
        assert record['status'] == 'completed'
        assert report['score'] is not None and report['verdict'] == 'ACCEPTABLE'
        assert report['assessment_complete'] and 'diagnostic_result' not in report
        assert plan['assessment_scope'] == 'container_recording' and not plan['proves_acquisition_sync']
        assert not plan['issues'] and not plan['extrapolation']
        assert plan['source_clock'] == plan['reference_clock']
        assert plan['source_time_source'] == plan['reference_time_source'] == 'container'
        assert plan['source_time_axis']['source_mapping']['timestamps_ns'][0] == 110_000_000_000
        np.testing.assert_allclose(plan['target_timestamps_s'], (camera+100)[plan['reference_rows']])
        assert plan['resampling_params']['max_gap'] == .1
        assert plan['source_mapping']['rows']['right_weights']
    with open_dataset(source, profile=profile) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            np.testing.assert_allclose(ep.channels['/left/pose'].timestamps, ts)
            assert ep.channels['/left/pose'].clock is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_interpolation_gap_is_rejected_before_jerk(tmp_path, monkeypatch):
    source, profile, params, *_ = fixture(tmp_path)
    params['timeline']['max_gap_s'] = .001  # Actual source interval is 4 ms.
    from edc.algorithms.quality.algorithms.Action_Smoothness import run_check
    monkeypatch.setattr(run_check, 'assess_arrays', lambda *a, **k: pytest.fail('gap gate bypassed'))
    row, records, evidence = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'partial'
    assert 'target_timestamps_s' not in evidence[0]
    report = records[0]['result']['reports'][0]
    assert report['score'] is None and not report['assessment_complete']
    assert any('max_gap' in reason for reason in report['reasons'])
