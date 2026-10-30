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
    """|recorded joint velocity| > max_abs (rad/s) per component; counts and contiguous segments."""
    state, reason = _state(values, channel, physical)
    if settings is None:
        state, reason = 'unconfigured', '未填写 numeric_check.thresholds.joint_velocity.max_abs；不假设默认阈值。'
    limit = (settings or {}).get('max_abs')
    timeline, times = _timeline(channel, len(values) if values.ndim else 0)
    check = {'id': 'joint_velocity_limit', 'name': '关节速度上限', 'status': state,
             'threshold': limit, 'unit': 'rad/s', 'reason': reason, 'outcome': None,
             'events': [], 'source_mapping': source_mapping(episode, channel, field), 'timeline': timeline,
             'changes_score': False, 'verdict': None, 'frames': int(len(values)) if values.ndim else 0,
             'frames_over': 0, 'samples_over': 0, 'segments': 0, 'per_component': []}
    if state != 'evaluated':
        return check
    over = np.abs(values) > limit
    for j in range(values.shape[1]):
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
                 reason=f'逐分量检查 |速度| > {limit:g} rad/s；连续超限的行合并为一段。超限可能是真实快速动作，需复核。')
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


def supplements(values, episode, channel, field, target, settings, physical, workdir=None, freeze_min_duration_s=None):
    checks = []
    if target == 'cmd_positions':
        checks.append(command_limits(values, episode, channel, field, settings['command_limits'], physical))
    if target == 'joint_velocities':
        checks.append(joint_velocity_limit(values, episode, channel, field, settings.get('joint_velocity'), physical))
    if target in FREEZE_GROUPS:
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
