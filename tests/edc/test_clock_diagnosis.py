"""Plain-language clock diagnosis attached to Sync_Check / Root_Cause; never changes times or scores."""
import json
from types import SimpleNamespace
import numpy as np
from edc import open_dataset, run
from edc.algorithms.quality.adapters.cpu import (clock_diagnosis, clock_shift, shift_note, ShiftedReader,
                                                 derived_copies, timestamp_scoring, too_short)

T0 = 1773741605.0  # 2026-03-17


def episode(streams):
    """streams: name -> (header times s, record times s or None, selection)."""
    channels = {n: SimpleNamespace(timestamps=list(h), clock='one', selection=sel) for n, (h, _, sel) in streams.items()}
    topics = {n: SimpleNamespace(container_timestamps_ns=None if r is None else [int(x * 1e9) for x in r])
              for n, (_, r, _) in streams.items()}
    return SimpleNamespace(channels=channels, describe_legacy=lambda: SimpleNamespace(topics=topics))


def stream(rate, delay_s, start=T0, seconds=2.0, header=None):
    record = start + np.arange(int(rate * seconds)) / rate
    return (record - delay_s if header is None else header(record)), record, {}


def test_same_clock_gives_one_group_and_no_notes():
    ep = episode({'/robot': stream(500, 0.002), '/cam': stream(30, 0.015)})
    d = clock_diagnosis(ep, ['/robot', '/cam'])
    assert len(d['groups']) == 1 and d['notes'] == []
    assert d['channels']['/cam']['record_minus_header_ms'] == 15.0


def test_processing_latency_below_a_second_is_not_called_a_clock_mismatch():
    ep = episode({'/robot': stream(500, 0.002), '/depth': stream(30, 0.4)})
    d = clock_diagnosis(ep, ['/robot', '/depth'])
    assert len(d['groups']) == 1 and d['notes'] == []
    assert d['channels']['/depth']['record_minus_header_ms'] == 400.0


def test_unsynchronised_device_clock_is_named_with_offset():
    ep = episode({'/robot': stream(500, 32.114), '/arm': stream(100, 32.113), '/cam': stream(30, 0.015)})
    d = clock_diagnosis(ep, ['/robot', '/arm', '/cam'])
    assert [sorted(g['channels']) for g in d['groups']] == [['/cam'], ['/arm', '/robot']]
    assert '32.1 秒' in d['notes'][0] and '没有对准' in d['notes'][0]
    assert d['salvage']['status'] == 'fixed_offset' and '可救' in d['notes'][-1]


def test_offset_that_moves_within_the_episode_is_not_salvageable():
    drift = lambda r: r - 32.0 - (r - T0) * 0.2  # Robot clock loses 200 ms per second of recording.
    ep = episode({'/robot': stream(500, 0, header=drift), '/cam': stream(30, 0.015)})
    d = clock_diagnosis(ep, ['/robot', '/cam'])
    assert d['salvage']['status'] == 'unstable' and list(d['salvage']['moving_channels']) == ['/robot']
    assert '不能简单平移' in d['notes'][-1]


def test_clock_shift_moves_smaller_groups_onto_the_largest_and_derived_channels_follow():
    cam = stream(30, 0.015)
    ep = episode({'/robot': stream(500, 32.114), '/arm': stream(100, 32.113), '/cam': cam,
                  '/cam_crop': (cam[0], None, {'channel': '/cam'}), '/cam_fk': (cam[0], None, None)})
    shift = clock_shift(ep, clock_diagnosis(ep, list(ep.channels)))
    assert set(shift) == {'/cam', '/cam_crop', '/cam_fk'} and abs(shift['/cam'] + 32.098) < 1e-9
    assert shift['/cam_crop'] == shift['/cam_fk'] == shift['/cam']  # Same timeline, same clock.
    reader = SimpleNamespace(require_timestamps=lambda info, min_channels=2: {'/cam': [1.0], '/robot': [1.0]})
    assert ShiftedReader(reader, shift).require_timestamps(None) == {'/cam': [1.0 + shift['/cam']], '/robot': [1.0]}
    assert shift_note({'/a': 32.04, '/b': 32.0, '/w': 2.5e9}) == '已把 /a、/b 的时间平移 +32.0 秒；已把 /w 的设备计数时间换算到统一时钟'
    same = episode({'/robot': stream(500, 0.002), '/cam': stream(30, 0.015)})
    assert clock_shift(same, clock_diagnosis(same, ['/robot', '/cam'])) == {}


def test_no_clock_problem_has_no_salvage_verdict():
    ep = episode({'/robot': stream(500, 0.002), '/cam': stream(30, 0.015)})
    assert clock_diagnosis(ep, ['/robot', '/cam'])['salvage'] is None


def test_group_with_spread_latencies_shows_a_range():
    ep = episode({'/robot': stream(500, 32.114), '/cam': stream(30, 0.015), '/depth': stream(30, 0.408)})
    d = clock_diagnosis(ep, ['/robot', '/cam', '/depth'])
    assert '15 毫秒–408 毫秒' in d['notes'][0]


def test_device_counter_header_is_flagged_and_left_out_of_groups():
    ep = episode({'/robot': stream(500, 0.002), '/cam_l': stream(30, 0, header=lambda r: r - 2.521e9),
                  '/cam_h': stream(30, 0.015)})
    d = clock_diagnosis(ep, ['/robot', '/cam_l', '/cam_h'])
    assert d['channels']['/cam_l']['calendar_time'] is False
    assert any('/cam_l' in n and '设备内部计数' in n and '1946' in n for n in d['notes'])
    assert all('/cam_l' not in g['channels'] for g in d['groups'])


def test_out_of_range_counter_and_derived_channels_do_not_break():
    ep = episode({'/robot': stream(500, 0.002), '/huge': stream(30, 0, header=lambda r: r * 1e9),
                  'joint/derived': (*stream(500, 0.002)[:2], {'channel': '/robot'})})
    d = clock_diagnosis(ep, ['/robot', '/huge', 'joint/derived'])
    assert 'joint/derived' not in d['channels']
    assert any('/huge' in n and '超出日期范围' in n for n in d['notes'])


def test_missing_record_time_is_reported_not_guessed():
    ep = episode({'/a': (T0 + np.arange(10) / 10, None, {}), '/b': (T0 + np.arange(10) / 10, None, {})})
    assert clock_diagnosis(ep, ['/a', '/b'])['notes'] == ['缺少录包时间，无法估计各话题的时钟偏差']


def write_bag(path):
    """Two topics recorded together; /robot headers come from a clock 32 s behind the recorder."""
    import pytest
    pytest.importorskip('rosbags')
    from rosbags.rosbag1 import Writer
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS1_NOETIC)
    typ = 'sensor_msgs/msg/JointState'
    with Writer(path) as writer:
        for topic, rate, delay in (('/robot', 100, 32.0), ('/cam', 30, 0.015)):
            con = writer.add_connection(topic, typ, typestore=store)
            for i in range(int(rate * 3)):
                record = int((T0 + i / rate) * 1e9)
                ns = record - int(delay * 1e9)
                header = store.types['std_msgs/msg/Header'](i, store.types['builtin_interfaces/msg/Time'](ns // 10**9, ns % 10**9), 'base')
                msg = store.types[typ](header, ['q'], np.array([0.0]), np.zeros(1), np.zeros(1))
                writer.write(con, record, store.serialize_ros1(msg, typ))


def test_sync_and_root_cause_records_explain_the_offset(tmp_path):
    source = tmp_path / 'clocks.bag'
    write_bag(source)
    profile = {'schema_version': 1, 'id': 'clock', 'version': '1',
               'channels': {'*': {'time_source': 'acquisition', 'clock': 'one'}}}
    with open_dataset(source, profile=profile) as ds:
        run(ds, output=tmp_path / 'out', algorithm_ids=['Sync_Check', 'Root_Cause'])
    for ident in ('Sync_Check', 'Root_Cause'):
        rec = json.loads(next((tmp_path / 'out/_runs').glob(f'*/{ident}/records.jsonl')).read_text())
        assert rec['status'] == 'completed', (ident, rec.get('reason'))
        reports = rec['result'].get('reports') or [rec['result']]
        notes = [n for r in reports for n in r['clock_diagnosis']['notes']]
        assert any('32.0 秒' in n and '没有对准' in n for n in notes), (ident, notes)
        assert any('32.0 秒' in n for r in reports for n in r['reasons'])
    root = json.loads(next((tmp_path / 'out/_runs').glob('*/Root_Cause/records.jsonl')).read_text())['result']
    root = (root.get('reports') or [root])[0]
    assert root['clock_shift_s'] and root['verdict'] != 'OBSERVATION'


def test_derived_copies_are_not_scored_twice_and_scoring_options_are_restored():
    robot, cam = stream(500, 0.002), stream(30, 0.015)
    ep = episode({'/robot': robot, '/cam': cam, '/arm': (robot[0], None, {'channel': '/robot'}),
                  '/fk': (robot[0], None, None), '/lonely': (stream(50, 0.0)[0], None, None)})
    assert derived_copies(ep, ep.describe_legacy()) == ['/arm', '/fk']
    reader = SimpleNamespace(require_timestamps=lambda info, min_channels=2: {'/robot': [1.0], '/arm': [1.0]})
    assert ShiftedReader(reader, {}, ['/arm']).require_timestamps(None) == {'/robot': [1.0]}
    module = SimpleNamespace(DEFAULT_IOU_THRESHOLD=0.95)
    with timestamp_scoring(module, {'iou_threshold': 0.8}):
        assert module.DEFAULT_IOU_THRESHOLD == 0.8
    assert module.DEFAULT_IOU_THRESHOLD == 0.95
    import pytest
    with pytest.raises(ValueError):
        timestamp_scoring(module, {'typo': 1})


def test_too_short_uses_each_channels_own_span():
    ep = SimpleNamespace(profile={'min_duration_s': 3.0}, channels={
        'a': SimpleNamespace(timestamps=[T0, T0 + 1.7]), 'counter': SimpleNamespace(timestamps=[5.0, 6.0])})
    assert too_short(ep) == {'seconds': 1.7, 'min_duration_s': 3.0}
    ep.channels['a'].timestamps = [T0, T0 + 3.5]
    assert too_short(ep) is None
    assert too_short(SimpleNamespace(profile={}, channels={})) is None
    ep.channels['a'].timestamps = [T0, T0 + 2.0]
    ep.profile = {}
    assert too_short(ep) is None  # Library default: no rule.
    from edc.algorithms.quality.adapters import cpu
    from edc.execution.cli import CLI_MIN_DURATION_S
    cpu.DEFAULT_MIN_DURATION_S = CLI_MIN_DURATION_S  # What `edc run` does for every robot and format.
    try:
        assert too_short(ep) == {'seconds': 2.0, 'min_duration_s': 3.0}
    finally:
        cpu.DEFAULT_MIN_DURATION_S = None
    ep.profile = {'min_duration_s': None}  # A profile can switch it off.
    assert too_short(ep) is None
