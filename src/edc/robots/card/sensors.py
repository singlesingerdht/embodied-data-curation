"""`cameras`, `imu` and `other` of the robot yaml."""
from .arms import side_of
from .common import OTHER_UNITS, CardError, unknown_keys

CAMERA_KEYS = {'source', 'mount', 'arm', 'type', 'label'}
MOUNTS = {'head', 'chest', 'wrist', 'main'}


def add_cameras(build, cameras, arms, clock):
    if not isinstance(cameras, dict):
        raise CardError('cameras 写成 {相机名: {source: ..., mount: head/wrist/chest}}')
    for camera, spec in cameras.items():
        where = f'cameras.{camera}'
        if not isinstance(spec, dict) or not isinstance(spec.get('source'), str):
            raise CardError(f'{where}: 写成 {{source: 数据里的名字, mount: head/wrist/chest}}')
        unknown_keys(spec, CAMERA_KEYS, where)
        mount, kind, arm = spec.get('mount', 'main'), spec.get('type', 'rgb'), spec.get('arm')
        if mount not in MOUNTS or kind not in ('rgb', 'depth'):
            raise CardError(f'{where}: mount 是 head/chest/wrist/main，type 是 rgb/depth')
        if arm is not None and arm not in arms:
            raise CardError(f'{where}.arm: 没有叫 {arm} 的手臂')
        side = side_of(arm, arms.get(arm)) if arm else 'unknown'
        logical = f'{kind}/{mount}' + (f'/{side if side != "unknown" else "main"}' if mount == 'wrist' else '')
        entry = {'modality': kind, 'time_source': 'acquisition', 'clock': clock,
                 'body_part': mount if mount != 'main' else 'unknown', 'logical_name': logical,
                 'representation': {'camera_role': mount, 'camera_role_status': 'confirmed',
                                    'camera_role_confirmed_by': build.confirmed_by}}
        if side != 'unknown':
            entry['side'] = side
        if arm:
            entry['robot_part'] = arm
        if kind == 'rgb':
            entry['sensor_id'] = camera
            build.profile['camera_requirements'][camera] = ['rgb']
        else:
            entry['fields'] = []
            entry['representation'].update(depth_scale=None, depth_unit=None)
        if spec['source'] in build.channels:
            raise CardError(f'{where}: {spec["source"]} 重复定义')
        build.channels[spec['source']] = entry
        build.names[spec['source']] = spec.get('label') or f'{camera}相机'
        build.names[logical] = build.names[spec['source']]


def add_imu(build, imu):
    for unit_name, spec in imu.items():
        where = f'imu.{unit_name}'
        if not isinstance(spec, dict) or not isinstance(spec.get('source'), str):
            raise CardError(f'{where}: 写成 {{source, gyro: {{field, unit}}, accel: {{field, unit}}, orientation: {{field, order}}}}')
        fields, metadata = {}, {}
        for key, out, quantity, units in (('gyro', 'ang_vel', 'angular_velocity', ('rad/s', 'deg/s')),
                                          ('accel', 'lin_acc', 'acceleration', ('m/s^2',)),
                                          ('orientation', 'orientation', 'orientation', None)):
            item = spec.get(key)
            if item is None:
                continue
            if not isinstance(item, dict) or not isinstance(item.get('field'), str):
                raise CardError(f'{where}.{key}: 写成 {{field: 字段名, unit: 单位}}')
            build.sources.setdefault(spec['source'], {'fields': []})['fields'].append(item['field'])
            fields[out] = item['field']
            if units:
                if item.get('unit') not in units:
                    raise CardError(f'{where}.{key}.unit 必填：{" 或 ".join(units)}')
                metadata[out] = {'quantity': quantity, 'dimensions': [3], 'unit': item['unit'],
                                 'semantic_status': 'confirmed', 'confirmed_by': build.confirmed_by,
                                 'calibration_status': 'confirmed'}
            else:
                order = item.get('order', 'xyzw')
                build.finite_only.append(f'imu/{unit_name}.orientation')  # No physical check for orientation.
                metadata[out] = {'quantity': 'orientation', 'dimensions': [4], 'unit': '1', 'semantic_status': 'confirmed',
                                 'confirmed_by': build.confirmed_by, 'calibration_status': 'unknown',
                                 'representation': {'kind': 'quaternion', 'order': order, 'unit': '1'}}
        logical = f'imu/{unit_name}'
        build.add(logical, {'modality': 'imu', 'fields': list(fields), 'source_kind': 'feedback',
                            'select': {'channel': spec['source'], 'fields': fields}, 'field_metadata': metadata,
                            'logical_name': logical}, spec.get('label') or 'IMU')


def add_other(build, entries):
    for i, entry in enumerate(entries):
        label = (entry or {}).get('label') or (entry or {}).get('source', f'other {i}')
        build.other(f'other/{i}', entry, f'other[{i}]', label, OTHER_UNITS['other'])
