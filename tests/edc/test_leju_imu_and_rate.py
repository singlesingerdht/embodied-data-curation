"""Leju additions: IMU block of kuavo sensorsData via select.fields, and the joint velocity limit."""
from types import SimpleNamespace

import numpy as np
import pytest

from edc.data.ir.values import ros_values_to_dict
from edc.robots.mapping import select_field_rows, select_values, validate_mapping_config


def _vec(x, y, z):
    return SimpleNamespace(x=x, y=y, z=z)


def _sensors(t):
    imu = SimpleNamespace(gyro=_vec(.01 * t, 0., 0.), acc=_vec(0., 0., 9.8), free_acc=_vec(0., 0., 0.),
                          quat=SimpleNamespace(x=0., y=0., z=0., w=1.))
    return SimpleNamespace(joint_data=SimpleNamespace(joint_q=[t], joint_v=[0.], joint_torque=[0.]), imu_data=imu)


IMU = {'modality': 'imu', 'fields': ['ang_vel', 'lin_acc', 'orientation'], 'source_kind': 'feedback',
       'select': {'channel': '/sensors_data_raw',
                  'fields': {'ang_vel': 'imu_gyro', 'lin_acc': 'imu_acc', 'orientation': 'imu_quat'}}}


def _profile(imu=IMU):
    return {'channels': {'/sensors_data_raw': {'modality': 'other', 'fields': ['joint_q', 'joint_v', 'tau']},
                         'imu/main': imu}}


def test_kuavo_sensors_data_exposes_imu_block_with_source_names():
    values = ros_values_to_dict(_sensors(2.), 'kuavo_msgs/msg/sensorsData')
    assert values['joint_q'] == [2.]
    assert values['imu_gyro'] == [.02, 0., 0.] and values['imu_acc'] == [0., 0., 9.8]
    assert values['imu_free_acc'] == [0., 0., 0.] and values['imu_quat'] == [0., 0., 0., 1.]


def test_field_selection_renames_fields_without_conversion():
    profile = _profile()
    validate_mapping_config(profile)
    episode = SimpleNamespace(profile=profile, read_values=lambda name: [
        ros_values_to_dict(_sensors(t), 'kuavo_msgs/msg/sensorsData') for t in range(3)])
    rows = select_field_rows(episode, 'imu/main')
    assert rows[1] == {'ang_vel': [.01, 0., 0.], 'lin_acc': [0., 0., 9.8], 'orientation': [0., 0., 0., 1.]}
    assert select_values(episode, 'imu/main', 'ang_vel').shape == (3, 3)
    assert select_values(episode, 'imu/main', 'orientation').shape == (3, 4)


@pytest.mark.parametrize('change', [
    {'fields': ['ang_vel', 'lin_acc']},                                       # declared fields != select.fields
    {'select': {'channel': '/sensors_data_raw', 'fields': {'ang_vel': 1}}},  # source field must be a name
    {'select': {'channel': '/sensors_data_raw', 'fields': {}}},
])
def test_field_selection_rejects_mismatched_declarations(change):
    with pytest.raises(ValueError):
        validate_mapping_config(_profile({**IMU, **change}))


def test_field_selection_missing_source_field_is_an_error():
    episode = SimpleNamespace(profile=_profile(), read_values=lambda name: [{'joint_q': [0.]}])
    with pytest.raises(ValueError, match='imu_gyro'):
        select_field_rows(episode, 'imu/main')


def test_field_selection_with_indices_picks_same_components_from_every_field():
    profile = {'parts': {'left': {'type': 'arm', 'side': 'left', 'dof': 2}},
               'channels': {'/state': {'modality': 'other', 'fields': ['q', 'v']},
                            'joint/arm/left/feedback': {'modality': 'joint_space', 'robot_part': 'left',
                                'fields': ['position', 'velocity'],
                                'select': {'channel': '/state', 'fields': {'position': 'q', 'velocity': 'v'},
                                           'indices': [1, 3]}}}}
    validate_mapping_config(profile)
    episode = SimpleNamespace(profile=profile, read_values=lambda name: [{'q': [0, 1, 2, 3], 'v': [0, 10, 20, 30]}])
    assert select_field_rows(episode, 'joint/arm/left/feedback') == [{'position': [1, 3], 'velocity': [10, 30]}]
    profile['channels']['joint/arm/left/feedback']['select']['indices'] = [1, 2, 3]  # 3 != dof 2
    with pytest.raises(ValueError, match='dof'):
        validate_mapping_config(profile)


def test_joint_velocity_threshold_is_adapter_only_and_absent_unless_configured():
    from edc.robots.numeric_review import method_settings, numeric_settings
    assert 'joint_velocity' not in numeric_settings({})['thresholds']  # existing digests unchanged
    settings = numeric_settings({'numeric_check': {'thresholds': {'joint_velocity': {'max_abs': 3.0}}}})
    assert settings['thresholds']['joint_velocity'] == {'max_abs': 3.0}
    assert 'joint_velocity' not in method_settings(settings)['thresholds']  # never reaches the method
    assert numeric_settings({}, {'thresholds': {'joint_velocity': {'max_abs': 2}}})['thresholds']['joint_velocity'] == {'max_abs': 2}
    for bad in ({'max_abs': 0}, {'max_abs': float('inf')}, {'limit': 3}, {}):
        with pytest.raises(ValueError):
            numeric_settings({'numeric_check': {'thresholds': {'joint_velocity': bad}}})


def _velocity_channel(n):
    return SimpleNamespace(name='joint/arm/left/feedback', count=n, time_source='acquisition', clock='c',
                           time_evidence={}, timestamps=list(np.arange(n) * .002), source='bag',
                           selection={'channel': '/s', 'fields': {'velocity': 'v'}, 'indices': [12, 13]},
                           source_mapping={}, field_spec=lambda field: {})


def test_joint_velocity_limit_counts_frames_and_merges_contiguous_rows():
    from edc.algorithms.quality.adapters.numeric_supplements import joint_velocity_limit
    values = np.zeros((10, 2))
    values[2:5, 0] = [3.5, -4.0, 3.2]   # one segment, 3 rows
    values[7, 0] = 3.1                  # second segment on the same joint
    values[3, 1] = -3.01                # overlaps row 3: frames_over counts rows once
    values[8, 1] = 3.0                  # exactly at the limit: not over
    check = joint_velocity_limit(values, None, _velocity_channel(10), 'velocity', {'max_abs': 3.0}, True)
    assert check['status'] == 'evaluated' and check['verdict'] == 'NEEDS_REVIEW'
    assert (check['frames'], check['frames_over'], check['samples_over'], check['segments']) == (10, 4, 5, 3)
    first = check['events'][0]
    assert (first['start'], first['stop'], first['peak_value'], first['source_component_index']) == (2, 5, -4.0, 12)
    assert first['duration_s'] == pytest.approx(.004)
    assert [c['segments'] for c in check['per_component']] == [2, 1]
    calm = joint_velocity_limit(np.ones((5, 2)), None, _velocity_channel(5), 'velocity', {'max_abs': 3.0}, True)
    assert calm['verdict'] == 'ACCEPTABLE' and calm['frames_over'] == 0
    assert joint_velocity_limit(values, None, _velocity_channel(10), 'velocity', None, True)['status'] == 'unconfigured'
    assert joint_velocity_limit(values, None, _velocity_channel(10), 'velocity', {'max_abs': 3.0}, False)['status'] == 'pending_confirmation'


