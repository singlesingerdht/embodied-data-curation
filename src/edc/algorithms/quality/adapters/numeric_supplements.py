"""Repository-owned numeric supplements; no duplicate scoring path.

Rows are zero-based within one Episode; intervals are [start, stop). Constant
content is observable, but cannot by itself prove an acquisition failure.
"""
import copy
import csv
import hashlib
import json
from collections import deque
import numpy as np

FREEZE_GROUPS = {'joint_positions': 'joint_freeze', 'cmd_positions': 'cmd_freeze',
                 'gyro': 'gyro_freeze', 'accel': 'accel_freeze'}


def source_mapping(episode, channel, field):
    selection = copy.deepcopy(channel.selection or {})
    return {'source': channel.source, 'channel': selection.get('channel', channel.name),
            'field': selection.get('field', field), 'selection': selection,
            'episode_id': getattr(episode, 'id', None),
            'episode_selector': copy.deepcopy(getattr(episode, 'selector', {})),
            'channel_source_mapping': copy.deepcopy(channel.source_mapping),
            'row_basis': 'zero-based episode-local source rows; no sorting or resampling'}


def _component(channel, field, index):
    indices = (channel.selection or {}).get('indices')
    names = channel.field_spec(field).get('joint_names') or []
    return {'component_index': index, 'source_component_index': indices[index] if indices else index,
            'component_name': names[index] if index < len(names) else None}


def _state(values, channel, physical, minimum=1):
    if (values.ndim != 2 or not values.size or len(values) != channel.count
            or len(values) < minimum):
        return 'insufficient_input', '需要与通道样本数一致的 N×D 数据及足够样本。'
    if not np.isfinite(values).all():
        return 'blocked_nonfinite', '输入含非有限值，未执行此物理子项。'
    if not physical:
        return 'pending_confirmation', '单位、语义、校准或当前阈值确认未满足。'
    return 'evaluated', ''


def _timeline(channel, n):
    evidence = {'time_source': channel.time_source, 'clock': channel.clock,
                'time_evidence': copy.deepcopy(channel.time_evidence),
                'establishes_acquisition_sync': False, 'unit': 's',
                'status': 'unavailable', 'reason': '缺少本通道有效记录时间；只报告源行区间。'}
    if channel.time_source not in {'acquisition', 'container'}:
        evidence['reason'] = '时间来源为 unknown/playback/synthetic，不计算物理持续时间。'
        return evidence, None
    try:
        times = np.asarray(channel.timestamps, dtype=float)
    except (TypeError, ValueError):
        return evidence, None
    if (times.shape != (n,) or not np.isfinite(times).all()
            or (n > 1 and not np.all(np.diff(times) > 0))):
        evidence['reason'] = '本通道时间缺失、长度不符、非有限、重复或回退；只报告源行区间。'
        return evidence, None
    evidence.update(status='available', reason='使用本通道所选记录时间；不跨通道配对，不替换为 FPS 或索引时间。')
    return evidence, times


def _constant_runs(column, epsilon, minimum):
    # Maximal bounded-range windows in O(N), retaining near-constant suffixes.
    # Windows may overlap; they are evidence, not independent sample counts.
    start = 0
    lows, highs = deque(), deque()
    for index, value in enumerate(column):
        previous_start = start
        while lows and column[lows[-1]] >= value:
            lows.pop()
        while highs and column[highs[-1]] <= value:
            highs.pop()
        lows.append(index)
        highs.append(index)
        while column[highs[0]] - column[lows[0]] > epsilon:
            if lows[0] == start:
                lows.popleft()
            if highs[0] == start:
                highs.popleft()
            start += 1
        if start > previous_start and index - previous_start >= minimum:
            yield previous_start, index
    if len(column) - start >= minimum:
        yield start, len(column)


def command_limits(values, episode, channel, field, settings, physical):
    limits = settings['limits']
    state, reason = _state(values, channel, physical)
    if limits is None:
        state, reason = 'unconfigured', '未填写独立的 command_limits.limits；不借用反馈关节限位。'
    elif values.ndim == 2 and len(limits) != values.shape[1]:
        state, reason = 'invalid_configuration', '指令限位必须逐项对应当前所选字段的分量顺序与数量。'
    check = {'id': 'command_limits', 'name': '动作指令位置限位', 'status': state,
             'threshold': copy.deepcopy(limits), 'unit': 'rad', 'reason': reason,
             'outcome': None, 'events': [], 'source_mapping': source_mapping(episode, channel, field),
             'changes_score': False, 'violations_count': 0, 'verdict': None}
    if state != 'evaluated':
        return check
    bounds = np.asarray(limits)
    bad = np.argwhere((values < bounds[:, 0]) | (values > bounds[:, 1]))
    check.update(outcome='finding' if len(bad) else 'no_finding',
                 violations_count=len(bad), verdict='DO_NOT_USE' if len(bad) else 'ACCEPTABLE',
                 reason='闭区间逐分量检查；阈值按当前字段顺序配置，单位 rad；原评分保持不变。')
    for row, component in bad:
        j = int(component)
        check['events'].append({'type': 'command_position_out_of_limits', 'source_row': int(row),
            **_component(channel, field, j), 'value': float(values[row, j]),
            'lower': float(bounds[j, 0]), 'upper': float(bounds[j, 1])})
    return check


def joint_velocity_limit(values, episode, channel, field, settings, physical):
    """|recorded joint velocity| > max_abs (rad/s) per component; counts and contiguous segments.
    max_abs is one number for every joint, or one per joint (e.g. the URDF <limit velocity>)."""
    state, reason = _state(values, channel, physical)
    limit = (settings or {}).get('max_abs')
    if limit is None:
        state, reason = 'unconfigured', '未填写 numeric_check.thresholds.joint_velocity.max_abs；不假设默认阈值。'
    limits = None
    if state == 'evaluated':
        limits = np.broadcast_to(np.asarray(limit, dtype=float), values.shape[1:]) if np.ndim(limit) == 0 else np.asarray(limit, float)
        if limits.shape != values.shape[1:]:
            state, reason = 'unconfigured', f'joint_velocity.max_abs 有 {limits.size} 个值，但 {channel.name} 有 {values.shape[1]} 个关节'
    text = '/'.join(f'{x:g}' for x in limit) if isinstance(limit, (list, tuple)) else f'{limit:g}' if limit is not None else ''
    timeline, times = _timeline(channel, len(values) if values.ndim else 0)
    check = {'id': 'joint_velocity_limit', 'name': '关节速度上限', 'status': state,
             'threshold': limit, 'threshold_text': text, 'unit': 'rad/s', 'reason': reason, 'outcome': None,
             'events': [], 'source_mapping': source_mapping(episode, channel, field), 'timeline': timeline,
             'changes_score': False, 'verdict': None, 'frames': int(len(values)) if values.ndim else 0,
             'frames_over': 0, 'samples_over': 0, 'segments': 0, 'per_component': []}
    if state != 'evaluated':
        return check
    over = np.abs(values) > limits
    for j in range(values.shape[1]):
        limit = float(limits[j])
        column = over[:, j]
        edges = np.diff(np.r_[0, column.astype(int), 0])
        starts, stops = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        for start, stop in zip(starts, stops):
            peak = start + int(np.abs(values[start:stop, j]).argmax())
            event = {'type': 'joint_velocity_over_limit', 'start': int(start), 'stop': int(stop),
                     'sample_count': int(stop - start), **_component(channel, field, j),
                     'peak_value': float(values[peak, j]), 'peak_row': int(peak), 'limit': float(limit),
                     'needs_review': True, 'duration_s': None, 'start_time_s': None, 'last_time_s': None,
                     'interpretation': f'峰值 {values[peak, j]:.2f} rad/s，超过 {limit:g} rad/s；可能是真实快速动作，需复核。'}
            if times is not None:
                event.update(start_time_s=float(times[start]), last_time_s=float(times[stop - 1]),
                             duration_s=float(times[stop - 1] - times[start]))
            check['events'].append(event)
        if column.any():
            check['per_component'].append({**_component(channel, field, j), 'samples_over': int(column.sum()),
                'segments': int(len(starts)), 'max_abs': float(np.abs(values[:, j]).max())})
    found = bool(check['events'])
    check.update(outcome='finding' if found else 'no_finding', verdict='NEEDS_REVIEW' if found else 'ACCEPTABLE',
                 frames_over=int(over.any(axis=1).sum()), samples_over=int(over.sum()), segments=len(check['events']),
                 max_abs=float(np.abs(values).max()),
                 reason=f'逐分量检查 |速度| > {text} rad/s；连续超限的行合并为一段。超限可能是真实快速动作，需复核。')
    return check


def numeric_freeze(values, episode, channel, field, target, settings, physical, min_duration_s=None):
    """min_duration_s (per robot, adapter option): a hold shorter than this after motion is a normal
    pause, recorded as an observation only. None keeps the sample-count rule (min_run) alone."""
    state, reason = _state(values, channel, physical, settings['min_run'])
    if target in {'gyro', 'accel'} and values.ndim == 2 and values.shape[1] != 3:
        state, reason = 'insufficient_input', 'IMU 冻结检查要求 N×3 字段。'
    timeline, times = _timeline(channel, len(values) if values.ndim else 0)
    group = FREEZE_GROUPS[target]
    check = {'id': group, 'name': {'joint_freeze': '关节反馈数值冻结候选',
        'cmd_freeze': '动作指令数值冻结候选', 'gyro_freeze': 'IMU 角速度数值冻结候选',
        'accel_freeze': 'IMU 加速度数值冻结候选'}[group], 'status': state,
        'threshold': copy.deepcopy(settings),
        'unit': {'joint_positions': 'rad', 'cmd_positions': 'rad', 'gyro': 'rad/s', 'accel': 'm/s^2'}[target],
        'reason': reason, 'outcome': None, 'events': [], 'observations': [],
        'source_mapping': source_mapping(episode, channel, field), 'timeline': timeline,
        'changes_score': False, 'proves_acquisition_failure': False}
    if state != 'evaluated':
        return check
    for j in range(values.shape[1]):
        column = values[:, j]
        for start, stop in _constant_runs(column, settings['freeze_eps'], settings['min_run']):
            prior = max(0, start - settings['lookback'])
            span = float(np.ptp(column[prior:start + 1]))
            moved = span > settings['motion_threshold']
            held = float(times[stop - 1] - times[start]) if times is not None else None
            short_hold = moved and min_duration_s is not None and held is not None and held < min_duration_s
            candidate = moved and not short_hold
            event = {'type': 'constant_after_motion_candidate' if candidate else
                             'short_hold_after_motion' if short_hold else 'constant_observation',
                     'start': start, 'stop': stop, 'sample_count': stop - start,
                     **_component(channel, field, j), 'value_min': float(column[start:stop].min()),
                     'value_max': float(column[start:stop].max()), 'preceding_start': prior,
                     'preceding_range': span, 'needs_review': candidate,
                     'duration_s': None, 'start_time_s': None, 'last_time_s': None,
                     'interpretation': '运动后恒值，可能为正常保持或冻结，需复核。' if candidate
                         else f'运动后恒值不到 {min_duration_s:g} 秒，按本机型设置视为正常停住。' if short_hold
                         else '恒值观察，缺少此前运动证据；不能据此证明正常静止或采集故障。'}
            if times is not None:
                event.update(start_time_s=float(times[start]), last_time_s=float(times[stop - 1]),
                             duration_s=float(times[stop - 1] - times[start]))
            check['events' if candidate else 'observations'].append(event)
    check.update(outcome='finding' if check['events'] else 'observation' if check['observations'] else 'no_finding',
        reason='逐分量检查区间全幅差；运动后恒值仅为复核候选，普通恒值仅观察；不作采集故障结论。')
    return check


def _paired_pose(episode, channel):
    """The same arm's recorded tip position on the same row timeline, or None."""
    for other in episode.channels.values():
        if (other.modality == 'pose' and other.value_available and 'position' in other.fields
                and other.robot_part and other.robot_part == channel.robot_part
                and other.source_kind == channel.source_kind and other.count == channel.count
                and other.timestamps is not None and channel.timestamps is not None
                and np.array_equal(np.asarray(other.timestamps, float), np.asarray(channel.timestamps, float))):
            return other
    return None


def joint_stuck(values, episode, channel, field, settings, physical, min_duration_s):
    """Per-robot adapter option `joint_stuck` ({min_move_m, verdict}), used instead of the per-joint freeze
    candidates: some robots (X1 Pro) report bit-identical joint values whenever a joint holds, so a joint or a
    whole arm holding still is a normal pause. A finding needs all joints of the arm unchanged for at least
    min_duration_s while the same arm's recorded tip position moves at least min_move_m: the joint readings
    stopped updating although the arm moved."""
    min_move = float(settings.get('min_move_m', 0.02))
    min_moving = float(settings.get('min_moving_s', 1.0))
    min_speed = float(settings.get('moving_speed_m_s', 0.02))
    min_duration = float(min_duration_s or 0)
    state, reason = _state(values, channel, physical, 2)
    timeline, times = _timeline(channel, len(values) if values.ndim else 0)
    pose = _paired_pose(episode, channel)
    check = {'id': 'joint_stuck', 'name': '关节读数卡住（末端在动）', 'status': state,
             'threshold': {'min_move_m': min_move, 'min_moving_s': min_moving, 'moving_speed_m_s': min_speed,
                           'min_duration_s': min_duration},
             'unit': 'rad; m', 'reason': reason, 'outcome': None, 'events': [], 'observations': [],
             'verdict': None, 'pose_channel': pose.name if pose else None,
             'source_mapping': source_mapping(episode, channel, field), 'timeline': timeline,
             'changes_score': False}
    if state == 'evaluated' and (times is None or pose is None):
        check.update(status='insufficient_input',
                     reason='需要本通道有效时间和同一手臂、同一时间轴的末端位置通道。')
    if check['status'] != 'evaluated':
        return check
    tip = np.asarray(episode.read_signal(pose.name, 'position'), dtype=float)
    same = np.r_[False, (np.diff(values, axis=0) == 0).all(axis=1)]  # row i identical to row i-1 on every joint
    step = np.r_[0.0, np.diff(times)]
    speed = np.r_[0.0, np.linalg.norm(np.diff(tip, axis=0), axis=1) / np.maximum(step[1:], 1e-3)]
    i = 1
    while i < len(same):
        if not same[i]:
            i += 1
            continue
        start = i - 1
        while i < len(same) and same[i]:
            i += 1
        stop = i  # rows [start, stop) hold one value
        held = float(times[stop - 1] - times[start])
        if held < min_duration:
            continue
        moved = float(np.linalg.norm(tip[start:stop] - tip[start], axis=1).max())
        # Seconds in which the tip really moves while every joint reading stays the same.
        moving = float(step[start + 1:stop][speed[start + 1:stop] > min_speed].sum())
        stuck = moved >= min_move and moving >= min_moving
        event = {'type': 'joint_stuck_while_pose_moves' if stuck else 'arm_hold',
                 'component_index': None, 'source_component_index': '全部关节', 'start': start, 'stop': stop, 'sample_count': stop - start, 'needs_review': stuck,
                 'start_time_s': float(times[start]), 'last_time_s': float(times[stop - 1]), 'duration_s': held,
                 'pose_move_m': moved, 'pose_moving_s': moving, 'reaches_end': stop == len(values),
                 'pose_channel': pose.name,
                 'interpretation': (f'整条手臂 {held:.1f} 秒内关节读数一个都没变，其中 {moving:.1f} 秒末端确实在动'
                                    f'（最远 {moved * 100:.0f} cm）：关节数据没在更新。'
                                    if stuck else f'整条手臂停住 {held:.1f} 秒，末端也基本没动（{moved * 100:.1f} cm）：正常停住。')}
        check['events' if stuck else 'observations'].append(event)
    found = bool(check['events'])
    check.update(outcome='finding' if found else 'observation' if check['observations'] else 'no_finding',
                 verdict=settings.get('verdict', 'DO_NOT_USE') if found else 'ACCEPTABLE',
                 reason=f'整条手臂所有关节同时不变至少 {min_duration:g} 秒、其间末端移动至少 {min_move * 100:g} cm 且真正在动'
                        f'（速度超过 {min_speed * 100:g} cm/s）的时间至少 {min_moving:g} 秒才算卡住；'
                        '单个关节或整条手臂停住而末端不动是正常停住，只记观察。')
    return check


def declared_range(values, episode, channel, field, declared):
    """Values outside the range the robot yaml declares (`range`, e.g. a raw 0-255 gripper reading)."""
    low, high = declared['range']
    check = {'id': 'declared_range', 'name': '超出 yaml 写的范围', 'status': 'evaluated',
             'threshold': [low, high], 'unit': declared.get('unit'), 'reason': '', 'outcome': None, 'events': [],
             'source_mapping': source_mapping(episode, channel, field), 'changes_score': False, 'verdict': None}
    if not values.size or not np.isfinite(values).all():
        check.update(status='blocked_nonfinite' if values.size else 'insufficient_input',
                     reason='没有数据或含 NaN/无穷大，未查范围。')
        return check
    table = values.reshape(len(values), -1)
    bad = (table < low) | (table > high)
    rows = np.flatnonzero(bad.any(axis=1))
    for j in range(table.shape[1]):
        hit = np.flatnonzero(bad[:, j])
        if len(hit):
            worst = hit[np.argmax(np.maximum(low - table[hit, j], table[hit, j] - high))]
            check['events'].append({'type': 'value_out_of_declared_range', **_component(channel, field, j),
                                    'rows': int(len(hit)), 'first_row': int(hit[0]), 'worst_row': int(worst),
                                    'value': float(table[worst, j]), 'lower': low, 'upper': high})
    check.update(outcome='finding' if len(rows) else 'no_finding', rows_over=int(len(rows)), rows=int(len(table)),
                 verdict='DO_NOT_USE' if len(rows) else 'ACCEPTABLE',
                 reason=f'{len(rows)}/{len(table)} 行超出 yaml 写的范围 [{low:g}, {high:g}]' if len(rows) else
                        f'全部在 yaml 写的范围 [{low:g}, {high:g}] 内')
    return check


def supplements(values, episode, channel, field, target, settings, physical, workdir=None, freeze_min_duration_s=None,
                joint_stuck_settings=None, value_range=None):
    checks = []
    if value_range is not None:
        checks.append(declared_range(values, episode, channel, field, value_range))
    if target == 'cmd_positions':
        checks.append(command_limits(values, episode, channel, field, settings['command_limits'], physical))
    if target == 'joint_velocities':
        checks.append(joint_velocity_limit(values, episode, channel, field, settings.get('joint_velocity'), physical))
    if target == 'joint_positions' and joint_stuck_settings is not None:
        checks.append(joint_stuck(values, episode, channel, field, joint_stuck_settings, physical,
                                  freeze_min_duration_s))
    elif target in FREEZE_GROUPS:
        checks.append(numeric_freeze(values, episode, channel, field, target,
                                     settings[FREEZE_GROUPS[target]], physical, freeze_min_duration_s))
    if workdir is not None:
        for check in checks:
            events = check['events'] + check.get('observations', [])
            if not events:
                continue
            name = hashlib.sha256(f'{channel.name}\0{field}'.encode()).hexdigest()[:16]
            path = workdir / f"{check['id']}-{name}.csv"
            fields = list(events[0]) + ['source_mapping', 'timeline']
            with path.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for event in events:
                    writer.writerow({**event, 'source_mapping': json.dumps(check['source_mapping'], ensure_ascii=False),
                        'timeline': json.dumps(check.get('timeline'), ensure_ascii=False)})
            check['evidence_csv'] = path.name
    return checks
