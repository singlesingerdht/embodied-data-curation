"""Odometry fields and robot-defined addressing; all fixtures are synthetic."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from edc import channel_catalog, open_dataset, run
from edc.configuration import execution_profile, load_config, workflow_config
from edc.data.ir.dataset import Channel
from edc.robots.profiles import load_profile
from edc.robots.scope import instances

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ['position', 'orientation', 'lin_vel', 'ang_vel', 'pose_covariance', 'twist_covariance']


def write_odometry(tmp_path, storage='standalone_mcap', topic='/odom', nonfinite=False):
    pytest.importorskip('rosbags')
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS1_NOETIC if storage == 'ros1' else Stores.ROS2_HUMBLE)
    if storage == 'ros1':
        from rosbags.rosbag1 import Writer
        source = tmp_path / 'synthetic.bag'
        writer = Writer(source)
    else:
        from rosbags.rosbag2 import StoragePlugin, Writer
        source = tmp_path / 'synthetic'
        writer = Writer(source, version=9, storage_plugin=(
            StoragePlugin.MCAP if storage == 'standalone_mcap' else StoragePlugin.SQLITE3))
    n = 60
    values = {
        'position': np.arange(n * 3, dtype=float).reshape(n, 3) / 10,
        'orientation': np.tile([0., 0., 0., 1.], (n, 1)),
        'lin_vel': np.arange(n * 3, dtype=float).reshape(n, 3) / 100,
        'ang_vel': np.arange(n * 3, dtype=float).reshape(n, 3) / 1000,
        'pose_covariance': np.tile(np.arange(36, dtype=float), (n, 1)),
        'twist_covariance': np.tile(np.arange(36, dtype=float) + 100, (n, 1)),
    }
    if nonfinite:
        values['lin_vel'][2, 1] = np.nan
        values['twist_covariance'][3, 35] = np.inf
    typ = 'nav_msgs/msg/Odometry'
    with writer:
        conn = writer.add_connection(topic, typ, typestore=store)
        for i in range(n):
            stamp = store.types['builtin_interfaces/msg/Time'](10, i * 7_000_000)
            frame = 'odom' if i < 30 else 'map'
            header = (store.types['std_msgs/msg/Header'](i, stamp, frame) if storage == 'ros1'
                      else store.types['std_msgs/msg/Header'](stamp, frame))
            pose = store.types['geometry_msgs/msg/Pose'](
                store.types['geometry_msgs/msg/Point'](*values['position'][i]),
                store.types['geometry_msgs/msg/Quaternion'](*values['orientation'][i]))
            twist = store.types['geometry_msgs/msg/Twist'](
                store.types['geometry_msgs/msg/Vector3'](*values['lin_vel'][i]),
                store.types['geometry_msgs/msg/Vector3'](*values['ang_vel'][i]))
            msg = store.types[typ](header, 'base_link',
                store.types['geometry_msgs/msg/PoseWithCovariance'](pose, values['pose_covariance'][i]),
                store.types['geometry_msgs/msg/TwistWithCovariance'](twist, values['twist_covariance'][i]))
            raw = store.serialize_ros1(msg, typ) if storage == 'ros1' else store.serialize_cdr(msg, typ)
            writer.write(conn, 20_000_000_000 + i * 11_000_000, raw)
    if storage == 'standalone_mcap':
        source = next(source.glob('*.mcap'))
    return source, values


def hashes(source):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (source.rglob('*') if source.is_dir() else [source]) if p.is_file()}


@pytest.mark.parametrize('storage', ['standalone_mcap', 'ros1', 'ros2_sqlite'])
@pytest.mark.parametrize('topic', ['/odom', '/imu/misleading_name'])
def test_odometry_six_fields_frames_and_original_timeline(tmp_path, storage, topic):
    source, expected = write_odometry(tmp_path, storage, topic, nonfinite=True)
    before = hashes(source)
    with open_dataset(source) as dataset:
        with next(dataset.iter_episodes()).opened() as ep:
            c = ep.channels[topic]
            assert c.modality == 'odometry' and c.value_available
            assert c.fields == FIELDS and 'lin_acc' not in c.fields
            for field in reversed(FIELDS):
                np.testing.assert_array_equal(ep.read_signal(topic, field), expected[field])
                assert c.field_spec(field)['unit'] == ('1' if field == 'orientation' else None)
                assert ep.recorded_channels[topic].field_spec(field)['unit'] is None
            np.testing.assert_allclose(c.timestamps, 20 + np.arange(60) * .011)
            assert c.time_source == 'container' and c.clock is None
            evidence = c.frame_evidence
            assert evidence['segments'] == [
                {'start_row': 0, 'stop_row': 30, 'frames': {'header.frame_id': 'odom', 'child_frame_id': 'base_link'}},
                {'start_row': 30, 'stop_row': 60, 'frames': {'header.frame_id': 'map', 'child_frame_id': 'base_link'}}]
            assert evidence['field_references']['lin_vel'] == 'child_frame_id'
            assert evidence['field_references']['position'] == 'header.frame_id'
            assert not evidence['coordinate_transform_applied'] and not evidence['calibration_confirmed']
            assert c.coordinate_frame is None
            assert channel_catalog(ep)['channels'][0]['frame_evidence'] == evidence
    profile = {'schema_version': 1, 'id': 'synthetic_odom', 'version': '1',
               'channels': {topic: {'time_source': 'acquisition'}}}
    with open_dataset(source, profile=profile) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            c = ep.channels[topic]
            np.testing.assert_allclose(c.timestamps, 10 + np.arange(60) * .007)
            assert c.time_source == 'acquisition' and c.clock is None
            assert not c.time_evidence['establishes_acquisition_sync']
    assert hashes(source) == before


def test_pending_odometry_binding_and_explicit_armless_scope():
    cfg = load_config(ROOT / 'configs/odometry.yaml')
    with pytest.raises(ValueError, match='Unbound source channel odometry/base'):
        execution_profile(cfg)
    params = workflow_config(cfg)['stages'][0]['params']
    assert all('arms' not in p for p in params.values())
    cfg['algorithms']['methods']['Numeric_Check']['arms'] = 'all'
    supplied = workflow_config(cfg)['stages'][0]['params']['Numeric_Check']
    assert supplied['arms'] == 'all'
    ep = SimpleNamespace(profile=cfg['robot'], channels={})
    with pytest.raises(ValueError, match='Declare parts'):
        instances(ep, supplied, 'numeric')


def test_odometry_workflow_produces_reports_without_imu_physical_checks(tmp_path):
    from edc.workflows.engine import run_workflow
    source, _ = write_odometry(tmp_path)
    before = hashes(source)
    cfg = load_config(ROOT / 'configs/odometry.yaml')
    cfg['dataset']['path'] = str(source)
    cfg['dataset']['source_bindings']['odometry/base'] = '/odom'
    cfg['robot']['channels']['odometry/base']['time_source'] = 'acquisition'
    result = run_workflow(cfg, tmp_path / 'result')
    records = [json.loads(line) for p in (tmp_path / 'result').rglob('records.jsonl')
               for line in p.read_text().splitlines() if line]
    assert len(records) == 2 and all(r['status'] == 'completed' for r in records)
    numeric = next(r for r in records if r['algorithm']['id'] == 'Numeric_Check')
    assert numeric['channel_bindings'][0]['requested'] == 'odometry/base'
    assert numeric['channel_bindings'][0]['channel'] == '/odom'
    reports = numeric['result']['reports']
    assert {r['input_field'] for r in reports} == set(FIELDS)
    for report in reports:
        assert report['physical_checks_applied'] is False
        assert report['coverage_complete'] is False
        assert not report.get('imu_pair_check')
        assert all(c['id'] not in {'gyro_freeze', 'accel_freeze'} for c in report['supplemental_checks'])
    assert result['delivery_status'] == 'suspended'
    assert all(r['decision'] == 'unassessed' for r in result['stages'][0]['gate'].values())
    assert list((tmp_path / 'result').rglob('catalog.json'))
    assert list((tmp_path / 'result').rglob('report.html'))
    assert hashes(source) == before


def test_odometry_schema_cannot_be_relabelled_as_imu(tmp_path):
    source, _ = write_odometry(tmp_path)
    p = {'schema_version': 1, 'id': 'bad_imu', 'version': '1', 'channels': {
        '/odom': {'modality': 'imu', 'logical_name': 'imu/base', 'fields': ['ang_vel']}}}
    with open_dataset(source, profile=p) as ds:
        with pytest.raises(ValueError, match='Odometry source requires modality odometry'):
            next(ds.iter_episodes()).open()


@pytest.mark.parametrize('name,expected', [
    ('single_arm_sensors', {'main': 'arm', 'main_wrist': 'sensor_mount'}),
    ('dual_arm_mobile_sensors', {'base': 'base', 'left': 'arm', 'right': 'arm'}),
    ('quadruped_sensors', {'base': 'base', 'front_left': 'leg', 'rear_right': 'leg'}),
])
def test_configurations_describe_different_robot_topologies(name, expected):
    p = load_profile(ROOT / f'configs/robots/{name}.yaml')
    assert all(p['parts'][k]['type'] == v for k, v in expected.items())
    for c in p['channels'].values():
        assert c['robot_part'] in p['parts']
        assert c['clock'] is None and c['calibration_status'] == 'unknown'
        assert all(f['unit'] is None for f in c['field_metadata'].values())
    if name == 'dual_arm_mobile_sensors':
        assert {'imu/wrist/left', 'imu/wrist/right'} <= set(p['channels'])


@pytest.mark.parametrize('name,logical_names', [
    ('single_arm_sensors', ['imu/wrist/main']),
    ('dual_arm_mobile_sensors', ['imu/wrist/left', 'imu/wrist/right']),
    ('quadruped_sensors', ['lab__imu/leg/front_left']),
])
def test_topology_and_custom_names_reach_algorithm_results(tmp_path, name, logical_names):
    h5py = pytest.importorskip('h5py')
    source = tmp_path / 'synthetic.h5'
    profile = load_profile(ROOT / f'configs/robots/{name}.yaml')
    with h5py.File(source, 'w') as f:
        f['time'] = np.arange(60) / 100
        for i, logical in enumerate(logical_names):
            f[f'raw_{i}'] = np.arange(180).reshape(60, 3) + i
            spec = profile['channels'].pop(logical)
            spec['fields'] = ['ang_vel']
            spec['field_metadata'] = {'ang_vel': spec['field_metadata']['ang_vel']}
            profile['channels'][f'raw_{i}'] = spec
    profile.update(timestamp_column='time', timestamp_unit='s')
    before = hashes(source)
    with open_dataset(source, profile=profile) as ds:
        ep = next(ds.iter_episodes())
        catalog = channel_catalog(ep)
        bound = [r for r in catalog['channels'] if r['logical_name'] in logical_names]
        assert len(bound) == len(logical_names)
        assert all(r['part_definition'] == profile['parts'][r['robot_part']] for r in bound)
        result = run(ds, algorithm_ids=['Numeric_Check', 'Dedup_Check'], output=tmp_path / 'result',
            params={a: {'channels': logical_names} for a in ['Numeric_Check', 'Dedup_Check']})
    assert result['status_counts'] == {'completed': 2}
    rows = [json.loads(line) for p in (tmp_path / 'result').rglob('records.jsonl')
            for line in p.read_text().splitlines() if line]
    assert all(r.get('scope_complete', True) for r in rows)
    for r in rows:
        assert {b['requested'] for b in r['channel_bindings']} == set(logical_names)
        assert {b['channel'] for b in r['channel_bindings']} == {f'raw_{i}' for i in range(len(logical_names))}
    assert hashes(source) == before


@pytest.mark.parametrize('mode', ['numeric', 'channels'])
def test_base_selection_does_not_require_unrelated_arms(mode):
    profile = load_profile(ROOT / 'configs/robots/dual_arm_mobile_sensors.yaml')
    ep = SimpleNamespace(profile=profile, channels={'odometry/base': Channel(
        'odometry/base', 'odometry', FIELDS, 3, 'fixture', value_available=True,
        logical_name='odometry/base', robot_part='base')})
    selected = instances(ep, {'channels': ['odometry/base']}, mode)[0]
    assert selected['complete'] and selected['scope']['missing_arms'] == []
    missing = instances(ep, {'channels': ['odometry/base', 'imu/wrist/right']}, mode)[0]
    assert not missing['complete']
    assert missing['scope']['missing_arms'] == ['right']
    assert missing['scope']['missing_channels'] == ['imu/wrist/right']
    with pytest.raises(ValueError, match='unselected arm right'):
        instances(ep, {'channels': ['imu/wrist/right'], 'arms': ['left']}, mode)


@pytest.mark.parametrize('changes', [
    {'parts': {'mount': {'arm': 'arm_a'}, 'arm_a': 'invalid'}},
    {'parts': {'mount': {'arm': 'absent'}}},
    {'parts': {'mount': {'type': 'sensor_mount', 'arm': 'mount'}}},
    {'parts': {'mount': {'side': 'left', 'arm': 'arm_a'}, 'arm_a': {'type': 'arm', 'side': 'right'}}},
    {'parts': {'base': {'type': ''}}},
    {'parts': {'left': {'type': 'arm', 'side': 'left'}}, 'channels': {
        'raw': {'modality': 'imu', 'robot_part': 'left', 'side': 'right'}}},
    {'parts': {'base': {'type': 'base'}}, 'channels': {'raw': {'robot_part': ['base']}}},
])
def test_invalid_part_references_and_side_conflicts_fail_cleanly(changes):
    with pytest.raises(ValueError):
        load_profile({'schema_version': 1, 'id': 'invalid', 'version': '1', **changes})
