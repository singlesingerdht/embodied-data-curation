"""Robot yaml (the one file a user writes) -> the framework's internal robot profile and algorithm parameters.

The user describes, for each arm, where joints / end effector / gripper / hand are stored and in which unit,
plus cameras, optional IMU, URDF and Home pose. Nothing here is specific to a robot or a storage format:
`source` is whatever name the reader shows for the data (LeRobot feature, HDF5 dataset, ROS topic, JSON column),
`field` a field inside it (ROS message field) and `index` the elements to take. Every numeric entry states its
unit; values stored as raw numbers either state their range (`unit: raw, range`) or how they map to a physical
unit (`scale`). The URDF part names, for each recorded joint column, the URDF joint it is.

One file per yaml part: common.py (errors, value parsers, the Builder that collects channels), arms.py,
urdf.py (arms.<arm>.urdf), sensors.py (cameras, imu, other). This file: robot / data, then the checks' settings.
"""
import copy
import re

from .arms import add_arms
from .common import CardError, Builder, edc_command, indices, number
from .sensors import add_cameras, add_imu, add_other
from .urdf import urdf_chain, urdf_route

__all__ = ['CardError', 'compile_card', 'edc_command', 'indices', 'is_card', 'number', 'urdf_chain', 'urdf_route']
TOP_KEYS = {'robot', 'data', 'arms', 'cameras', 'imu', 'other', 'checks'}
DATA_KEYS = {'time', 'time_note', 'min_duration_s', 'video_frame_time', 'episode_group'}


def is_card(value):
    return isinstance(value, dict) and 'robot' in value and 'schema_version' not in value


def compile_card(card, base=None):
    """(profile, params, names) from a robot yaml mapping; base is the folder the yaml lives in."""
    card = copy.deepcopy(card)
    unknown = set(card) - TOP_KEYS
    if unknown:
        raise CardError(f'不认识的顶层项：{", ".join(sorted(unknown))}（只能有 {", ".join(sorted(TOP_KEYS))}）')
    robot = card.get('robot') or {}
    name = robot.get('name')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', name):
        raise CardError('robot.name 必填：英文字母开头，只含字母、数字、下划线、连字符')
    confirmed_by = robot.get('confirmed_by')
    if not isinstance(confirmed_by, str) or not confirmed_by.strip():
        raise CardError('robot.confirmed_by 必填：确认本文件里单位和含义的人')
    data = card.get('data') or {}
    if set(data) - DATA_KEYS:
        raise CardError(f'data 里不认识的项：{", ".join(sorted(set(data) - DATA_KEYS))}（可用：{", ".join(sorted(DATA_KEYS))}）')
    profile = {'schema_version': 1, 'id': name, 'version': str(robot.get('version', '1')),
               'model': robot.get('model') or name, 'instance_id': None,
               'min_duration_s': float(data.get('min_duration_s', 3.0)),
               'parts': {}, 'camera_requirements': {}, 'channels': {}}
    add_data(profile, data)
    params = copy.deepcopy(card.get('checks') or {})
    if not isinstance(params, dict) or any(not isinstance(v, dict) for v in params.values()):
        raise CardError('checks 写成 {算法名: {参数: 值}}')

    build = Builder(profile, confirmed_by, base or '.')
    clock = f'{name}_clock'
    arms = card.get('arms') or {}
    add_arms(build, arms)
    add_cameras(build, card.get('cameras') or {}, arms, clock)
    add_imu(build, card.get('imu') or {})
    add_other(build, card.get('other') or [])
    channels = build.channels
    # Original channels the yaml names: declared as recorded arrays, checked for NaN/Inf only.
    for source, spec in build.sources.items():
        if source in channels:
            raise CardError(f'{source} 既是相机又是数值数据')
        channels[source] = {'modality': 'other', 'fields': spec['fields'], 'time_source': 'acquisition', 'clock': clock}
        build.finite_only += [f'{source}.{f}' for f in spec['fields']]
        build.names.setdefault(source, source)
    # Data the yaml does not mention is not checked.
    channels['*'] = {'modality': 'other', 'fields': [], 'time_source': 'acquisition', 'clock': clock}
    if build.chains:
        profile['transforms'] = {'chains': build.chains}
    add_numeric(build, params.setdefault('Numeric_Check', {}))
    profile['labels'] = build.names  # Readable names, used by the algorithms' reasons and the report.
    from edc.robots.numeric_review import numeric_settings, review_digest
    settings = numeric_settings(profile, params['Numeric_Check'])
    profile['numeric_review'] = {'status': 'confirmed', 'confirmed_by': confirmed_by,
                                 'configuration_sha256': review_digest(profile, settings)}
    return profile, params, build.names


def add_data(profile, data):
    time = data.get('time')
    if time is not None:
        if not isinstance(time, dict) or not isinstance(time.get('source'), str):
            raise CardError('data.time 写成 {source: 时间列名, unit: s/ms/us/ns}')
        profile['timestamp_column'] = time['source']
        profile['timestamp_unit'] = time.get('unit', 's')
    if data.get('time_note') is not None:
        if not isinstance(data['time_note'], str) or not data['time_note'].strip():
            raise CardError('data.time_note 写一句话：为什么没有原始采集时间（显示在“不适用”的原因里）')
        profile['notes'] = {'time': data['time_note'].strip()}
    if data.get('episode_group') is not None:
        if not isinstance(data['episode_group'], str) or not data['episode_group'].strip('/'):
            raise CardError('data.episode_group 写 HDF5 里装着各条数据的组名，如 data（robomimic：data/demo_0、data/demo_1……各算一条）')
        profile['episode_group'] = data['episode_group']
    if data.get('video_frame_time') is not None:
        if data['video_frame_time'] != 'rows':
            raise CardError('data.video_frame_time 只能写 rows（第 i 帧视频对应第 i 行数据）')
        profile['video_frame_time'] = 'rows'


def add_numeric(build, numeric):
    """Limits (per internal channel) for the numeric checks, and the Numeric_Check parameters the yaml implies."""
    thresholds = {}
    if build.limits:
        thresholds['joint'] = {'limits': build.limits}
    if build.velocity_limits:
        thresholds['joint_velocity'] = {'max_abs': build.velocity_limits}
    if build.command_limits:
        thresholds['command_limits'] = {'limits': build.command_limits}
    if thresholds:
        build.profile['numeric_check'] = {'thresholds': thresholds}
    numeric.setdefault('finiteness_only', sorted(set(build.finite_only)))
    if build.ranges:
        numeric.setdefault('ranges', build.ranges)
    numeric.setdefault('freeze_min_duration_s', 5.0)  # Holding still for less than 5 s is a normal pause.
    if any(c.get('modality') == 'joint_space' and c.get('source_kind') == 'feedback' for c in build.channels.values()) \
            and 'joint' not in thresholds:
        numeric.setdefault('joint_limits_pending', '没有提供 URDF，关节限位不检查')
