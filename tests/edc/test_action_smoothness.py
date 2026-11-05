"""Action_Smoothness: shake acceleration in a fixed motion-frequency band, through the real reader/runner."""
import copy
import hashlib
import json

import numpy as np
import pytest

from edc import open_dataset, recommend, run
from edc.algorithms.quality.algorithms.Action_Smoothness.method import assess_arrays


def fixture(tmp_path, *, clock='shared', unit='m', arms=('right',)):
    """250 Hz ROS2 poses (move 8 cm, then hold) plus a 30 Hz camera, written to an MCAP file."""
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
                    writer.add_message(channel, ns + 100_000_000_000, bytes(store.serialize_cdr(msg, typ)), ns)
        writer.finish()
    profile = {'schema_version': 1, 'id': 'smoothness_fixture', 'version': '1',
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
    return source, profile, {}, source_times, reference_times, positions


def execute(tmp_path, source, profile, params):
    with open_dataset(source, profile=profile) as ds:
        pool = recommend(ds, params={'Action_Smoothness': params})
        row = next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id'] == 'Action_Smoothness')
        run(ds, algorithm_ids=['Action_Smoothness'], params={'Action_Smoothness': params}, output=tmp_path/'out')
    records = [json.loads(line) for line in next((tmp_path/'out/_runs').glob(
        '*/Action_Smoothness/records.jsonl')).read_text().splitlines()]
    return row, records


def trajectory(rate, shake_hz=0., shake_m=0., seconds=12):
    """Reach 20 cm, hold, move back 10 cm, hold; optional sine shake on the holds only."""
    t = np.arange(0, seconds, 1 / rate)
    s = lambda u: 10 * u**3 - 15 * u**4 + 6 * u**5
    x = np.where(t < 3, .2 * s(np.clip(t / 3, 0, 1)),
                 np.where(t < 6, .2, np.where(t < 8, .2 - .1 * s(np.clip((t - 6) / 2, 0, 1)), .1)))
    hold = ((t > 3.2) & (t < 5.8)) | (t > 8.2)
    x = x + hold * shake_m * np.sin(2 * np.pi * shake_hz * t)
    return np.column_stack([x, 0 * x, 0 * x]), t


@pytest.mark.parametrize('unit', ['m', 'mm'])
def test_runs_through_reader_and_runner_with_common_item(tmp_path, unit):
    source, profile, params, *_ = fixture(tmp_path, unit=unit)
    before, config = hashlib.sha256(source.read_bytes()).hexdigest(), copy.deepcopy(profile)
    row, records = execute(tmp_path, source, profile, params)
    assert row['availability'] == 'available' and records[0]['status'] == 'completed'
    report = records[0]['result']['reports'][0]
    assert report['verdict'] == 'ACCEPTABLE' and report['smoothness']['band_hz'] == [2.0, 10.0]
    assert report['edc_item']['verdict'] == 'PASS' and 'still_shake_m_s2' in report['edc_item']['metrics']
    assert profile == config and hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_both_arms_are_checked_separately(tmp_path):
    source, profile, params, *_ = fixture(tmp_path, arms=('left', 'right'))
    _, records = execute(tmp_path, source, profile, params)
    assert {r['instance_id'] for r in records} == {'left', 'right'}
    assert {r['result']['reports'][0]['input_channel'] for r in records} == {'/left/pose', '/right/pose'}


def test_thresholds_from_the_robot_yaml_reach_the_method(tmp_path):
    source, profile, _, *_ = fixture(tmp_path)
    _, records = execute(tmp_path, source, profile, {'still_review_m_s2': 1e-9})
    assert records[0]['result']['reports'][0]['verdict'] == 'NEEDS_REVIEW'


def test_unknown_parameter_is_rejected(tmp_path):
    source, profile, _, *_ = fixture(tmp_path)
    row, records = execute(tmp_path, source, profile, {'jerk_threshold_m_s3': 15})
    assert row['status'] == 'missing_input' and all(r['status'] == 'missing_input' for r in records)


def test_result_does_not_depend_on_sampling_rate():
    values = [assess_arrays(*trajectory(rate, 6, 1e-3))['smoothness']['still']['rms_m_s2'] for rate in (30, 60, 120, 500)]
    assert max(values) / min(values) < 1.05
    assert values[0] == pytest.approx(1e-3 * (2 * np.pi * 6) ** 2 / np.sqrt(2), rel=.2)  # Sine RMS on the holds.


def test_shake_on_a_hold_is_found_and_located():
    still = assess_arrays(*trajectory(30))
    shaking = assess_arrays(*trajectory(30, 6, 2e-3))
    assert still['verdict'] == 'ACCEPTABLE' and still['smoothness']['still']['rms_m_s2'] < .05
    assert shaking['verdict'] == 'NEEDS_REVIEW' and shaking['smoothness']['still']['rms_m_s2'] > 1
    assert 3.2 < shaking['smoothness']['still']['worst_at_s'] < 5.8 or shaking['smoothness']['still']['worst_at_s'] > 8.2


def test_low_rate_narrows_the_band_and_very_low_rate_is_not_applicable():
    assert assess_arrays(*trajectory(15))['smoothness']['band_hz'] == pytest.approx([2, 6])
    report = assess_arrays(*trajectory(8))
    assert report['verdict'] == 'NOT_APPLICABLE' and report['not_applicable'] == 'low_rate'
    from edc.algorithms.quality.shared.verdicts import to_item
    from edc.algorithms.quality.algorithms.Action_Smoothness import result
    item = to_item('Action_Smoothness', report, result, {})
    assert item.status == 'not_applicable' and '8.0 Hz' in item.reason


def test_motion_alone_is_never_judged_and_no_hold_is_not_applicable():
    t = np.arange(0, 10, 1 / 30)
    x = np.column_stack([.05 * np.sin(2 * np.pi * 3 * t) + .2 * t, 0 * t, 0 * t])  # Shaking along a sweep, never stops.
    report = assess_arrays(x, t)
    assert report['smoothness']['motion']['rms_m_s2'] > 5
    assert report['verdict'] == 'NOT_APPLICABLE' and report['not_applicable'] == 'no_still'


def test_frozen_reading_mid_motion_is_review_but_a_real_stop_is_not():
    x, t = trajectory(30)
    assert not assess_arrays(x, t)['smoothness']['freezes']
    x[45:90] = x[45]  # Stops updating at 1.5 s while the hand moves at about 10 cm/s.
    report = assess_arrays(x, t)
    assert report['verdict'] == 'NEEDS_REVIEW' and report['smoothness']['freezes'][0]['start_s'] == pytest.approx(1.5)


def test_timestamp_jitter_is_not_a_gap_but_a_real_hole_is():
    x, t = trajectory(500)
    jittered = t + np.random.default_rng(0).uniform(-.0008, .0008, len(t))
    assert assess_arrays(x, jittered)['verdict'] == 'ACCEPTABLE'
    keep = (t < 5) | (t > 5.5)  # Half a second missing.
    report = assess_arrays(x[keep], t[keep])
    assert report['verdict'] == 'ACCEPTABLE' and report['smoothness']['still']['seconds'] < 7


def test_nonfinite_rows_are_dropped_not_imputed(tmp_path, monkeypatch):
    from edc.algorithms.quality.algorithms.Action_Smoothness import inputs as action
    from edc.algorithms.quality.adapters.cpu import selected_inputs
    from edc.algorithms.quality.algorithms.Action_Smoothness.algorithm import selected_pose
    from edc.data.ir.units import SIDataset
    from edc.execution.runner import Context
    source, profile, params, *_ = fixture(tmp_path)
    with open_dataset(source, profile=profile) as ds:
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            params['channel'] = '/right/pose'
            view = selected_inputs(ep, params, 'Action_Smoothness')
            xyz = view.read_signal('/right/pose', 'position').copy()
            context = Context(dataset=None, episode=ep, workdir=tmp_path)
            xyz[20, 0] = np.nan
            monkeypatch.setattr(view, 'read_signal', lambda *a: xyz)
            assert action.assess(view, selected_pose(view, params), {}, context)['num_poses'] == 801
            xyz[:] = np.nan
            result = action.assess(view, selected_pose(view, params), {}, context)
    assert result['verdict'] == 'NEEDS_REVIEW' and not result['assessment_complete']
