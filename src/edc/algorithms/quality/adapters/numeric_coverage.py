"""Describe executed subchecks without changing or rescoring the detector."""
import numpy as np


def coverage(values, eligibility, settings, result, physical, unit_reason=None):
    rows = []
    target = eligibility['target'][0] if eligibility['target'] else None
    n = len(values) if values.ndim else 0
    finite = bool(np.isfinite(values).all())
    blockers = '; '.join(filter(None, [unit_reason, *eligibility['reasons']]))

    def add(ident, name, *, applicable=True, configured=True, enough=True,
            events=(), group=None, threshold=None, unit=None, reason=''):
        state = ('not_applicable' if not applicable else 'unconfigured' if not configured
                 else 'insufficient_input' if not enough or not values.size
                 else 'blocked_nonfinite' if not finite
                 else 'pending_confirmation' if not physical else 'evaluated')
        detected = (result.get('numeric') or {}).get(group) or {}
        matches = [event for event in detected.get('events', []) if event.get('type') in events]
        if ident == 'pose_freeze' and detected.get('segments'):
            matches = detected['segments']
        rows.append({'id': ident, 'name': name, 'status': state,
            'outcome': ('finding' if matches else 'no_finding') if state == 'evaluated' else None,
            'threshold': threshold, 'unit': unit, 'events': matches,
            'reason': (reason if state != 'pending_confirmation' else blockers)})

    rows.append({'id': 'finite', 'name': '有限值（NaN / +Inf / −Inf）',
        'status': 'evaluated' if values.size else 'insufficient_input',
        'outcome': ('no_finding' if finite else 'finding') if values.size else None,
        'reason': '不依赖物理单位；逐元素检查，位置见异常索引及 CSV。'})
    joint = settings['joint']
    add('joint_limits', '关节位置限位', applicable=target == 'joint_positions',
        configured=joint['limits'] is not None, threshold=joint['limits'], unit='rad',
        events=('joint_range',), group='joint', reason='未填写 joint.limits，不判断是否越过关节机械限位。' if joint['limits'] is None else '')
    if target == 'joint_positions':
        for ident, name, key, events, enough in (
                ('joint_step', '相邻关节位置差', 'vel_threshold', ('joint_jump',), n >= 2),
                ('joint_step_peak', '最大相邻关节位置差', 'velocity_peak', ('joint_step_peak',), n >= 2),
                ('frequent_joint_steps', '位置差超阈值的区间比例', 'reset_ratio', ('frequent_joint_steps',), n >= 3)):
            add(ident, name, enough=enough, threshold=joint[key],
                unit='ratio' if key == 'reset_ratio' else 'rad per adjacent sample',
                events=events, group='joint', reason='按相邻样本计算，未除以时间，不代表真实速度或复位。')
    add('command_difference', '指令位置二阶样本差', applicable=target == 'cmd_positions', enough=n >= 3,
        threshold=settings['cmd']['jerk_threshold'], unit='rad per sample-index squared',
        events=('cmd_second_sample_difference',), group='cmd', reason='需要至少 3 个样本；未使用时间，不代表物理 jerk。')
    for kind, label, key, event, required in (
            ('gyro', 'IMU 角速度幅值', 'gyro_max', 'imu_gyro_over', 'rad/s'),
            ('accel', 'IMU 加速度幅值', 'acc_max', 'imu_acc_over', 'm/s^2')):
        add(f'{kind}_range', label, applicable=target == kind, threshold=settings['imu'][key],
            unit=required, events=(event,), group='imu')
    # The current public adapter calls the detector once per field. It does not
    # combine accelerometer data with gyro-selected intervals.
    if target == 'gyro':
        minimum = settings['imu']['min_static_frames']
        segments = []
        if physical and finite and values.ndim == 2 and values.size >= minimum:
            from edc.algorithms.quality.algorithms.Numeric_Check.numeric_check import _find_runs
            static = np.abs(np.diff(np.linalg.norm(values, axis=1))) < 0.02
            segments = _find_runs(~static, minimum)
        add('gyro_bias', 'IMU 角速度偏置（既有区段选择）',
            enough=bool(segments) if physical and finite else n >= minimum,
            threshold=settings['imu']['gyro_bias_max'], unit='rad/s',
            events=('imu_gyro_bias',), group='imu',
            reason=f'既有区段选择器返回 {len(segments)} 段；无区段不代表偏置通过。')
    if target == 'accel':
        add('accel_bias', 'IMU 加速度偏置', enough=False,
            threshold=settings['imu']['acc_bias_max'], unit='m/s^2',
            reason='当前逐字段调用未同时传入 gyro 及其区段；本子项未执行。')
    add('pose_freeze', '末端位置冻结', applicable=target == 'pose_freeze_positions',
        enough=n >= settings['pose_freeze']['min_run'], threshold=settings['pose_freeze'], unit='m; samples',
        group='pose_freeze', reason='相邻位置变化及前段运动条件；不代表速度检测。')
    if target is None:
        rows.append({'id': 'other_physical', 'name': '其他物理子项',
            'status': 'pending_confirmation' if blockers else 'unsupported', 'outcome': None,
            'reason': blockers or '现有 Numeric_Check 未实现此字段的物理检查，仅查有限值。'})
    return {'schema_version': 'dqs_numeric_coverage_v1', 'checks': rows,
        'coverage_complete': all(row['status'] in {'evaluated', 'not_applicable'} for row in rows),
        'legacy_completeness_scope': 'assessment_complete retains the existing configured-detector eligibility rule; use checks for actual subcheck coverage',
        'changes_score_or_thresholds': False}
