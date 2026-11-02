"""IMU declarations and real reader integration using explicitly synthetic MCAP."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from edc.algorithms.channel_names import STANDARD_CHANNELS, resolve_channel
from edc.configuration import execution_profile, load_config, open_config_dataset, workflow_config
from edc.execution.runner import run
from edc.robots.numeric_review import confirm_numeric_profile
from edc.robots.profiles import load_profile

ROOT = Path(__file__).resolve().parents[2]
TOPIC = '/recorded/imu_sensor'


def configured(source):
    cfg = load_config(ROOT / 'configs/imu.yaml')
    cfg['dataset']['path'] = str(source)
    cfg['dataset']['source_bindings']['imu/main'] = TOPIC
    cfg['dataset']['capture']['scope'] = 'synthetic IMU fixture only'
    return cfg


def write_imu(path):
    pytest.importorskip('mcap')
    pytest.importorskip('rosbags')
    from mcap.writer import Writer
    from rosbags.typesys import Stores, get_typestore
    store = get_typestore(Stores.ROS2_HUMBLE)
    typ = 'sensor_msgs/msg/Imu'
    stamps = [10_000_000_000 + i * 7_000_000 for i in range(60)]
    with path.open('wb') as stream:
        writer = Writer(stream)
        writer.start(profile='ros2')
        definition, _ = store.generate_msgdef(typ, ros_version=2)
        schema = writer.register_schema(typ, 'ros2msg', definition.encode())
        channel = writer.register_channel(TOPIC, 'cdr', schema)
        for i, ns in enumerate(stamps):
            stamp = store.types['builtin_interfaces/msg/Time'](ns // 10**9, ns % 10**9)
            header = store.types['std_msgs/msg/Header'](stamp, 'synthetic_imu_frame')
            msg = store.types[typ](header,
                store.types['geometry_msgs/msg/Quaternion'](0., 0., 0., 1.), np.zeros(9),
                store.types['geometry_msgs/msg/Vector3'](0., 0., 0.), np.zeros(9),
                store.types['geometry_msgs/msg/Vector3'](0., 0., 9.81), np.zeros(9))
            log = 20_000_000_000 + i * 11_000_000
            writer.add_message(channel, log, bytes(store.serialize_cdr(msg, typ)), log)
        writer.finish()
    return stamps


@pytest.mark.parametrize('name', ['imu/main', 'imu/base', 'imu/head', 'imu/chest',
    'imu/wrist/left', 'imu/wrist/right', 'imu/wrist/main', 'imu/main@backup'])
def test_standard_imu_names_keep_physical_metadata_unknown(name):
    channel = {'modality': 'imu', 'logical_name': name, 'fields': ['ang_vel', 'lin_acc']}
    profile = load_profile({'schema_version': 1, 'id': 'imu_test', 'version': '1',
        'channels': {TOPIC: channel}})
    assert profile['channels'][TOPIC] == channel
    assert STANDARD_CHANNELS[name.split('@')[0]]['modality'] == 'imu'


@pytest.mark.parametrize('changes', [{'modality': 'odometry'}, {'modality': 'pose'}, {'side': 'right'}])
def test_imu_logical_alias_rejects_conflicting_modality_or_side(changes):
    channel = {'logical_name': 'imu/wrist/left', 'modality': 'imu', **changes}
    with pytest.raises(ValueError, match='conflicts'):
        load_profile({'schema_version': 1, 'id': 'imu_test', 'version': '1',
            'channels': {TOPIC: channel}})


def test_pending_data_binding_is_explicit_and_does_not_invent_source():
    cfg = load_config(ROOT / 'configs/imu.yaml')
    original = copy.deepcopy(cfg)
    assert cfg['dataset']['source_bindings'] == {'imu/main': None}
    for operation in (execution_profile, open_config_dataset):
        with pytest.raises(ValueError, match='Unbound source channel imu/main'):
            operation(cfg)
    assert cfg == original
    params = workflow_config(cfg)['stages'][0]['params']['Numeric_Check']
    assert params == {'channels': ['imu/main']}  # An IMU does not require a fictitious arm.


def test_explicit_invalid_arm_scope_is_not_discarded():
    cfg = load_config(ROOT / 'configs/imu.yaml')
    cfg['algorithms']['defaults']['arms'] = ['missing_arm']
    assert workflow_config(cfg)['stages'][0]['params']['Numeric_Check']['arms'] == ['missing_arm']


def test_dataset_capture_preserves_per_field_units_and_robot_template(tmp_path):
    cfg = configured(tmp_path / 'synthetic.mcap')
    original = copy.deepcopy(cfg['robot'])
    capture = cfg['dataset']['capture']['channels']['imu/main']
    capture.update(time_source='acquisition', clock=None)
    capture['field_metadata']['ang_vel']['unit'] = 'deg/s'
    capture['field_metadata']['lin_acc']['unit'] = 'mm/s^2'
    profile = execution_profile(cfg)
    channel = profile['channels'][TOPIC]
    assert set(profile['channels']) == {TOPIC}
    assert channel['field_metadata']['ang_vel']['unit'] == 'deg/s'
    assert channel['field_metadata']['lin_acc']['unit'] == 'mm/s^2'
    assert channel['clock'] is None and channel['calibration_status'] == 'unknown'
    assert cfg['robot'] == original
    with pytest.raises(ValueError): confirm_numeric_profile(profile, 'not authorized by metadata')


@pytest.mark.parametrize('confirmed', [False, True])
def test_mcap_imu_binding_reading_timeline_and_numeric_output(tmp_path, confirmed):
    source = tmp_path / 'synthetic.mcap'
    stamps = write_imu(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    cfg = configured(source)
    capture = cfg['dataset']['capture']['channels']['imu/main']
    capture['time_source'] = 'acquisition'
    if confirmed:
        capture['calibration_status'] = 'confirmed'
        for field, unit in [('ang_vel', 'rad/s'), ('lin_acc', 'm/s^2'), ('orientation', '1')]:
            capture['field_metadata'][field].update(unit=unit, semantic_status='confirmed')
        # Attest only a generated fixture, after applying its actual dataset bindings.
        cfg['robot'] = confirm_numeric_profile(execution_profile(cfg), 'synthetic fixture owner')
    params = workflow_config(cfg)['stages'][0]['params']['Numeric_Check']
    output = tmp_path / 'result'
    with open_config_dataset(cfg) as dataset:
        with next(dataset.iter_episodes()).opened() as episode:
            assert resolve_channel(episode, 'imu/main') == TOPIC
            channel = episode.channels[TOPIC]
            assert channel.modality == 'imu' and channel.clock is None
            assert channel.timestamps == [v * 1e-9 for v in stamps]
            assert channel.time_source == 'acquisition'
            assert not channel.time_evidence['establishes_acquisition_sync']
            np.testing.assert_array_equal(episode.read_signal(TOPIC, 'ang_vel'), np.zeros((60, 3)))
            np.testing.assert_array_equal(episode.read_signal(TOPIC, 'lin_acc'), np.tile([0., 0., 9.81], (60, 1)))
            assert episode.read_signal(TOPIC, 'orientation').shape == (60, 4)
        run(dataset, algorithm_ids=['Numeric_Check'], params={'Numeric_Check': params}, output=output)
    record = json.loads(next(output.glob('_runs/*/Numeric_Check/records.jsonl')).read_text())
    assert record['status'] == 'completed'
    assert record['channel_bindings'][0]['requested'] == 'imu/main'
    assert record['channel_bindings'][0]['channel'] == TOPIC
    fields = {r['input_field']: r for r in record['result']['reports']}
    for field, check in [('ang_vel', 'gyro_freeze'), ('lin_acc', 'accel_freeze')]:
        report = fields[field]
        assert report['physical_checks_applied'] is confirmed
        assert report['source_mapping']['channel'] == TOPIC
        supplemental = next(c for c in report['supplemental_checks'] if c['id'] == check)
        assert supplemental['status'] == ('evaluated' if confirmed else 'pending_confirmation')
        assert supplemental['timeline']['time_source'] == 'acquisition'
    if confirmed:
        assert fields['lin_acc']['imu_pair_check']['status'] == 'evaluated'
        assert list(output.rglob('*.csv'))
    else:
        selection = json.loads(next(output.glob('_runs/*/selection.json')).read_text())
        assert selection[0]['decision'] == 'unassessed'
    page = (output / 'report.html').read_text()
    assert '数值检查' in page and '逐条详情' in page
    run_ref = next(output.glob('_runs/*/run.json')).parent.relative_to(output).as_posix()
    assert f'href="{run_ref}/"' in page
    assert all((output / ref).is_file() for ref in record['artifacts'])
    assert (output / 'catalog.json').is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_bound_but_absent_imu_never_falls_back_to_another_topic(tmp_path):
    source = tmp_path / 'synthetic.mcap'
    write_imu(source)
    cfg = configured(source)
    cfg['dataset']['source_bindings']['imu/main'] = '/not_recorded'
    output = tmp_path / 'missing'
    with open_config_dataset(cfg) as dataset:
        run(dataset, algorithm_ids=['Numeric_Check'],
            params={'Numeric_Check': {'channels': ['imu/main']}}, output=output)
    record = json.loads(next(output.glob('_runs/*/Numeric_Check/records.jsonl')).read_text())
    assert record['status'] == 'missing_input'
    selection = json.loads(next(output.glob('_runs/*/selection.json')).read_text())
    assert selection[0]['decision'] == 'unassessed'


def test_four_component_imu_workflow_runs_without_inventing_arm_topology(tmp_path):
    from edc.workflows.engine import run_workflow
    source = tmp_path / 'synthetic.mcap'
    write_imu(source)
    cfg = configured(source)
    output = tmp_path / 'workflow'
    result = run_workflow(cfg, output)
    records = [json.loads(line) for p in output.rglob('Numeric_Check/records.jsonl')
               for line in p.read_text().splitlines() if line]
    assert len(records) == 1 and records[0]['status'] == 'completed'
    assert records[0]['effective_params']['channels'] == [TOPIC]
    assert result['delivery_status'] == 'suspended'
    assert all(row['decision'] == 'unassessed' for row in result['stages'][0]['gate'].values())
