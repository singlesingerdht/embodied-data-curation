"""Preserve typed hand source arrays without inventing physical joint semantics."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from edc import open_dataset, recommend, run
from edc.data.ir.metadata import classify_channel
from edc.data.ir.units import SIDataset, UnitInputError
from edc.data.ir.values import infer_fields, ros_values_to_dict


TYPE = 'kuavo_msgs/msg/robotHandPosition'
FIELDS = ['left_hand_position', 'right_hand_position']
DEFINITION = 'std_msgs/Header header\nuint8[] left_hand_position\nuint8[] right_hand_position\n'


@pytest.mark.parametrize('msgtype', [TYPE, 'kuavo_msgs/robotHandPosition'])
def test_source_type_overrides_misleading_topic_position(msgtype):
    for name in ('/control_robot_hand_position', '/telemetry', '/joint_states/position'):
        assert infer_fields(name, msgtype) == FIELDS
        assert classify_channel(name, msgtype) == 'other'
    left = np.array([0, 100, 1, 2, 3, 4], dtype=np.uint8)
    right = np.array([99, 50, 6, 7, 8, 9], dtype=np.uint8)
    result = ros_values_to_dict(SimpleNamespace(left_hand_position=left,
                                               right_hand_position=right), msgtype)
    assert set(result) == set(FIELDS) and 'position' not in result
    for name, expected in zip(FIELDS, (left, right)):
        actual = np.asarray(result[name])
        assert actual.dtype == expected.dtype
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize('missing', FIELDS)
def test_missing_hand_is_an_error_not_a_copy_or_empty_fill(missing):
    values = {name: np.array([1, 2], dtype=np.uint8) for name in FIELDS if name != missing}
    with pytest.raises(ValueError, match=f'missing source fields: {missing}'):
        ros_values_to_dict(SimpleNamespace(**values), TYPE)


def test_recorded_empty_hand_stays_empty_and_unrelated_type_is_not_claimed():
    msg = SimpleNamespace(left_hand_position=np.array([], dtype=np.uint8),
                          right_hand_position=np.array([100], dtype=np.uint8))
    result = ros_values_to_dict(msg, TYPE)
    assert result['left_hand_position'] == []
    np.testing.assert_array_equal(result['right_hand_position'], [100])
    assert ros_values_to_dict(msg, 'another_vendor/msg/robotHandPosition') == {}


def write_hand(path, backend):
    pytest.importorskip('rosbags')
    from rosbags.typesys import get_typestore, get_types_from_msg, Stores
    ros1 = backend == 'ros1'
    store = get_typestore(Stores.ROS1_NOETIC if ros1 else Stores.ROS2_HUMBLE)
    store.register(get_types_from_msg(DEFINITION, TYPE))
    expected = {name: [] for name in FIELDS}
    messages = []
    for i in range(4):
        stamp = store.types['builtin_interfaces/msg/Time'](10, i * 10_000_000)
        header_type = store.types['std_msgs/msg/Header']
        header = header_type(i, stamp, 'hand') if ros1 else header_type(stamp, 'hand')
        left = np.array([i, 100, 10, 20, 30, 40], dtype=np.uint8)
        right = np.array([99 - i, 50, 60, 70, 80, 90], dtype=np.uint8)
        msg = store.types[TYPE](header, left, right)
        messages.append((20_000_000_000 + i * 10_000_000, msg))
        for name, values in zip(FIELDS, (left, right)):
            expected[name].append(values)

    if backend == 'standalone_mcap':
        pytest.importorskip('mcap')
        from mcap.writer import Writer
        with path.open('wb') as stream:
            writer = Writer(stream)
            writer.start(profile='ros2')
            definition, _ = store.generate_msgdef(TYPE, ros_version=2)
            schema = writer.register_schema(TYPE, 'ros2msg', definition.encode())
            channel = writer.register_channel('/telemetry', 'cdr', schema)
            for timestamp, msg in messages:
                writer.add_message(channel, timestamp, bytes(store.serialize_cdr(msg, TYPE)), timestamp)
            writer.finish()
    else:
        if ros1:
            from rosbags.rosbag1 import Writer
            writer = Writer(path)
        else:
            from rosbags.rosbag2 import Writer, StoragePlugin
            storage = StoragePlugin.MCAP if backend == 'ros2_mcap' else StoragePlugin.SQLITE3
            writer = Writer(path, version=9, storage_plugin=storage)
        with writer:
            connection = writer.add_connection('/telemetry', TYPE, typestore=store)
            for timestamp, msg in messages:
                raw = store.serialize_ros1(msg, TYPE) if ros1 else store.serialize_cdr(msg, TYPE)
                writer.write(connection, timestamp, raw)
    return {name: np.asarray(values) for name, values in expected.items()}


def fingerprints(source):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
            ([source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file()))}


@pytest.mark.parametrize('backend', ['ros1', 'db3', 'ros2_mcap', 'standalone_mcap'])
def test_native_readers_and_numeric_check_keep_two_raw_hands(tmp_path, backend):
    source = tmp_path / ('hands.bag' if backend == 'ros1' else
                         'hands.mcap' if backend == 'standalone_mcap' else 'bag')
    expected = write_hand(source, backend)
    before = fingerprints(source)
    output = tmp_path / 'results'
    with open_dataset(source) as dataset:
        with next(dataset.iter_episodes()).opened() as episode:
            channel = episode.channels['/telemetry']
            assert channel.fields == FIELDS and channel.value_available
            assert channel.modality == 'other' and channel.source_kind == 'unknown'
            assert channel.unit is None and channel.clock is None
            assert channel.time_source == 'container' and channel.count == 4
            np.testing.assert_allclose(channel.timestamps, np.arange(4) * .01 + 20)
            for name in FIELDS:
                actual = episode.read_signal('/telemetry', name)
                assert actual.dtype == np.dtype('uint8')
                np.testing.assert_array_equal(actual, expected[name])
            with pytest.raises(ValueError):
                episode.read_signal('/telemetry', 'position')
        with next(SIDataset(dataset).iter_episodes()).opened() as episode:
            with pytest.raises(UnitInputError):
                episode.read_signal('/telemetry', 'left_hand_position')
        pool = recommend(dataset)
        row = next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id'] == 'Numeric_Check')
        assert row['status'] == 'runnable' and row['availability'] == 'partial'
        summary = run(dataset, algorithm_ids=['Numeric_Check'], output=output)
    assert summary['status_counts'] == {'completed': 1}
    record = json.loads(next(output.glob('_runs/*/Numeric_Check/records.jsonl')).read_text())
    reports = record['result']['reports']
    assert {r['input_field'] for r in reports} == set(FIELDS)
    assert all(r['shape'] == [4, 6] and r['finite'] for r in reports)
    assert all(not r['physical_checks_applied'] and not r['assessment_complete'] for r in reports)
    assert fingerprints(source) == before


def test_explicit_per_side_views_select_actual_source_fields(tmp_path):
    source = tmp_path / 'hands.bag'
    expected = write_hand(source, 'ros1')
    profile = {'schema_version': 1, 'id': 'hand_source_mapping', 'version': '1', 'channels': {
        'left_hand_view': {'modality': 'other', 'fields': ['recorded_values'],
            'side': 'left', 'select': {'channel': '/telemetry', 'field': 'left_hand_position',
                                     'indices': [5, 1, 3]}},
        'right_hand_view': {'modality': 'other', 'fields': ['recorded_values'],
            'side': 'right', 'select': {'channel': '/telemetry', 'field': 'right_hand_position',
                                      'indices': [0, 2, 4]}}}}
    with open_dataset(source, profile=profile) as dataset:
        with next(dataset.iter_episodes()).opened() as episode:
            for side in ('left', 'right'):
                channel = f'{side}_hand_view'
                indices = profile['channels'][channel]['select']['indices']
                np.testing.assert_array_equal(episode.read_signal(channel, 'recorded_values'),
                                              expected[f'{side}_hand_position'][:, indices])
                assert episode.channels[channel].selection['field'] == f'{side}_hand_position'
