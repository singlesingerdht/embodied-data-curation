"""`arms` of the robot yaml: joints, end effector, gripper / hand, URDF and Home pose of each arm."""
from .common import NO_LIMIT, OTHER_UNITS, CardError, scaled, unknown_keys
from .urdf import urdf_chain

ARM_KEYS = {'joints', 'joint_velocity', 'joint_command', 'eef_position', 'eef_orientation', 'eef_command',
            'gripper', 'gripper_command', 'hand', 'hand_command', 'urdf', 'home', 'max_joint_velocity', 'side', 'label'}
SIDE_LABEL = {'left': '左臂', 'right': '右臂'}


def side_of(arm, spec):
    return (spec or {}).get('side', arm if arm in ('left', 'right') else 'unknown')


def add_arms(build, arms):
    if not isinstance(arms, dict):
        raise CardError('arms 写成 {手臂名: {...}}，单臂可以只写一个（如 main）')
    for arm, spec in arms.items():
        where = f'arms.{arm}'
        if not isinstance(spec, dict):
            raise CardError(f'{where}: 写成映射')
        unknown_keys(spec, ARM_KEYS, where)
        side = side_of(arm, spec)
        if side not in ('left', 'right', 'unknown'):
            raise CardError(f'{where}.side 只能是 left、right 或 unknown')
        tag = side if side != 'unknown' else 'main'
        if tag == 'main' and sum(1 for a, s in arms.items() if side_of(a, s) == 'unknown') > 1:
            raise CardError('多条手臂时请用 left/right 命名，或给每条写 side')
        label = spec.get('label') or SIDE_LABEL.get(side) or ('' if len(arms) == 1 else arm)
        build.profile['parts'][arm] = add_arm(build, arm, spec, where, side, tag, label)
    add_homes(build)


def add_arm(build, arm, spec, where, side, tag, label):
    """Channels of one arm; returns its part entry."""
    part = {'type': 'arm', 'side': side}
    # Derived channels take their time from the recorded channel they are cut from.
    common = {'robot_part': arm, 'body_part': 'arm', 'side': side}
    confirmed = {'semantic_status': 'confirmed', 'confirmed_by': build.confirmed_by, 'calibration_status': 'confirmed'}
    feedback = f'joint/arm/{tag}/feedback'
    joint = spec.get('joints')
    dof = None
    if joint is not None:
        source, field, picks = build.source_of(joint, f'{where}.joints')
        if picks is None:
            raise CardError(f'{where}.joints: 写 index（关节在数组里的下标），如 0-5')
        dof = part['dof'] = len(picks)
        unit = joint.get('unit')
        if unit not in ('rad', 'deg'):
            raise CardError(f'{where}.joints.unit 必填：rad 或 deg')
        fields = {'position': field}
        metadata = {'position': {'unit': unit, 'quantity': 'joint_angle', **confirmed, **scaled(joint, f'{where}.joints')}}
        velocity = spec.get('joint_velocity')
        if velocity is not None:
            v_source, v_field, v_picks = build.source_of(velocity, f'{where}.joint_velocity')
            if len(v_picks or []) != dof:
                raise CardError(f'{where}.joint_velocity: index 个数要和 joints 一样（{dof} 个）')
            if velocity.get('unit') not in ('rad/s', 'deg/s'):
                raise CardError(f'{where}.joint_velocity.unit 必填：rad/s 或 deg/s')
            fields['velocity'] = (v_field if v_source == source and v_picks == picks else
                                  {'channel': v_source, 'field': v_field, 'indices': v_picks})
            metadata['velocity'] = {'unit': velocity['unit'], 'quantity': 'angular_velocity', **confirmed,
                                    **scaled(velocity, f'{where}.joint_velocity')}
        build.add(feedback, {
            'modality': 'joint_space', 'fields': list(fields), **common, 'source_kind': 'feedback',
            'dimensions': [dof], 'unit': unit,
            'select': {'channel': source, 'fields': fields, 'indices': picks}, 'field_metadata': metadata,
            'logical_name': feedback}, f'{label}关节')
    command = spec.get('joint_command')
    if command is not None:
        source, field, picks = build.source_of(command, f'{where}.joint_command')
        if picks is None or (dof and len(picks) != dof):
            raise CardError(f'{where}.joint_command: index 个数要和 joints 一样')
        part.setdefault('dof', len(picks))
        if command.get('unit') not in ('rad', 'deg'):
            raise CardError(f'{where}.joint_command.unit 必填：rad 或 deg')
        channel = f'joint/arm/{tag}/command'
        build.add(channel, {'modality': 'joint_space', 'fields': ['position'], **common, 'source_kind': 'command',
                            'dimensions': [len(picks)], 'unit': command['unit'],
                            'select': {'channel': source, 'fields': {'position': field}, 'indices': picks},
                            'field_metadata': {'position': {'unit': command['unit'], 'quantity': 'joint_angle', **confirmed,
                                                            **scaled(command, f'{where}.joint_command')}},
                            'logical_name': channel}, f'{label}关节指令')
        build.command_limits[channel] = [NO_LIMIT] * len(picks)
    has_velocity = joint is not None and 'velocity' in build.channels[feedback]['fields']
    if spec.get('urdf') is not None:
        if joint is None:
            raise CardError(f'{where}.urdf: 要先写 joints（URDF 用关节角算末端）')
        chain = urdf_chain(spec['urdf'], build.base, f'{where}.urdf', dof, feedback)
        build.chains[arm] = chain['config']
        build.limits[feedback] = chain['limits']
        if chain['velocity'] and has_velocity:
            build.velocity_limits[feedback] = chain['velocity']
    if spec.get('max_joint_velocity') is not None:
        value = spec['max_joint_velocity']
        values = value if isinstance(value, list) else [value] * (dof or 0)
        if not values or len(values) != dof or any(not isinstance(v, (int, float)) or v <= 0 for v in values):
            raise CardError(f'{where}.max_joint_velocity: 一个正数，或每个关节一个（rad/s）')
        build.velocity_limits[feedback] = [float(v) for v in values]
    if has_velocity and feedback not in build.velocity_limits:
        raise CardError(f'{where}: 写了 joint_velocity 就要给上限：写 max_joint_velocity，或提供带 velocity 的 URDF')
    eef = spec.get('eef_position')
    if eef == 'fk':
        if arm not in build.chains:
            raise CardError(f'{where}.eef_position: fk 需要先写 urdf')
        build.add(f'pose/arm/{tag}/feedback', {
            'modality': 'pose', 'fields': ['position'], **common, 'source_kind': 'feedback',
            'dimensions': [3], 'unit': 'm', 'select': {'fk': arm},
            'field_metadata': {'position': {'unit': 'm', 'quantity': 'position', **confirmed}},
            'logical_name': f'pose/arm/{tag}/feedback'}, f'{label}末端位置（由 URDF 算出）')
    elif eef is not None:
        pose_channel(build, eef, f'{where}.eef_position', f'pose/arm/{tag}/feedback', 'feedback', common, confirmed,
                     f'{label}末端位置')
    if spec.get('eef_command') is not None:
        pose_channel(build, spec['eef_command'], f'{where}.eef_command', f'pose/arm/{tag}/command', 'command', common,
                     confirmed, f'{label}末端位置指令')
    for key, word, kind in (('eef_orientation', '末端姿态', 'orientation'), ('gripper', '夹爪', 'gripper'),
                            ('gripper_command', '夹爪指令', 'gripper'), ('hand', '灵巧手', 'hand'),
                            ('hand_command', '灵巧手指令', 'hand')):
        if spec.get(key) is not None:
            build.other(f'{key}/{tag}', spec[key], f'{where}.{key}', f'{label}{word}', OTHER_UNITS[kind])
    if spec.get('home') is not None:
        build.homes[arm] = (spec['home'], tag, dof)
    return part


def pose_channel(build, entry, where, channel, kind, common, confirmed, label):
    source, field, picks = build.source_of(entry, where)
    if picks is None or len(picks) != 3:
        raise CardError(f'{where}: index 写 x、y、z 三个下标，如 0-2')
    if entry.get('unit') not in ('m', 'mm', 'cm'):
        raise CardError(f'{where}.unit 必填：m、cm 或 mm')
    build.add(channel, {'modality': 'pose', 'fields': ['position'], **common, 'source_kind': kind, 'dimensions': [3],
                        'unit': entry['unit'],
                        # Per-field indices: a 3-element position is not checked against the arm's joint count.
                        'select': {'channel': source, 'fields': {'position': {'channel': source, 'field': field,
                                                                               'indices': picks}}},
                        'field_metadata': {'position': {'unit': entry['unit'], 'quantity': 'position', **confirmed,
                                                        **scaled(entry, where)}},
                        'logical_name': channel}, label)


def add_homes(build):
    """Home pose of the arms -> Reset_Detection calibration (one unit and tolerance for all arms)."""
    homes = build.homes
    if not homes:
        return
    units = {('m' if 'eef_position' in h else 'rad') for h, _, _ in homes.values()}
    tolerances = {float(h.get('tolerance', -1)) for h, _, _ in homes.values()}
    if len(units) != 1 or len(tolerances) != 1 or min(tolerances) <= 0:
        raise CardError('home: 每条手臂都写 joints（rad）或都写 eef_position（m），且 tolerance 相同、大于 0')
    unit = units.pop()
    reference = {}
    for arm, (home, tag, dof) in homes.items():
        key = 'eef_position' if unit == 'm' else 'joints'
        value = home.get(key)
        channel = f'pose/arm/{tag}/feedback' if unit == 'm' else f'joint/arm/{tag}/feedback'
        if channel not in build.channels or not isinstance(value, list) or len(value) != (3 if unit == 'm' else dof):
            raise CardError(f'arms.{arm}.home.{key}: 个数不对，或这条手臂没有对应的 {key} 数据')
        reference[channel] = [float(v) for v in value]
    build.profile['calibration'] = {'Reset_Detection': {'unit': unit, 'tolerance': tolerances.pop(), 'reference': reference}}
