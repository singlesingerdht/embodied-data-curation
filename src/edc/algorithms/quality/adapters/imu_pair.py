"""Joint IMU evidence from explicitly co-recorded fields, without resampling."""
import numpy as np
from edc.robots.numeric_review import numeric_field_status


def paired_bias(episode, channel, accel, review):
    evidence = {'method_version': 'same_channel_imu_bias_v1',
        'status': 'insufficient_input', 'events': [], 'segments': [],
        'reason': '需要同一 IMU 通道内逐行对应的 ang_vel 和 lin_acc；不跨通道猜测配对。',
        'source_mapping': {'source': channel.source, 'channel': channel.name,
            'fields': ['ang_vel', 'lin_acc'], 'selection': channel.selection,
            'mapping': channel.source_mapping,
            'episode_selector': getattr(episode, 'selector', {}),
            'row_mapping': 'same_channel_identity'},
        'time_source': channel.time_source, 'clock': channel.clock,
        'time_evidence': channel.time_evidence,
        'establishes_acquisition_sync': False, 'changes_legacy_score': False}
    if 'ang_vel' not in channel.fields:
        return evidence
    eligible = numeric_field_status(episode, channel, 'ang_vel', review)
    if not eligible['physical_checks_available']:
        evidence.update(status='pending_confirmation', reason='；'.join(eligible['reasons']))
        return evidence
    from edc.data.ir.units import UnitInputError
    try:
        gyro = np.asarray(episode.read_signal(channel.name, 'ang_vel'))
    except UnitInputError as exc:
        evidence.update(status='pending_confirmation', reason=str(exc))
        return evidence
    if (gyro.ndim != 2 or accel.ndim != 2 or gyro.shape != accel.shape
            or gyro.shape[1] != 3 or len(gyro) != channel.count):
        evidence['reason'] = 'IMU 两字段必须都是同一通道的 N×3 数据，且样本数与通道声明一致。'
        return evidence
    if not np.isfinite(gyro).all() or not np.isfinite(accel).all():
        evidence.update(status='blocked_nonfinite', reason='IMU 联合输入含非有限值；未执行偏置检查。')
        return evidence
    from edc.algorithms.quality.algorithms.Numeric_Check.numeric_check import check_imu_anomaly, _find_runs
    settings = review['settings']['thresholds']['imu']
    minimum = settings['min_static_frames']
    segments = _find_runs(~(np.abs(np.diff(np.linalg.norm(gyro, axis=1))) < 0.02), minimum)
    evidence['segments'] = [{'start': int(a), 'stop': int(b)} for a, b in segments]
    evidence['segment_basis'] = 'legacy gyro-norm adjacent difference < 0.02 rad/s; sample intervals, not independent proof of rest'
    if not segments:
        evidence['reason'] = '既有陀螺仪区段选择器未找到足够长的区段；不代表偏置通过。'
        return evidence
    joint = check_imu_anomaly(gyro, accel, **settings)
    evidence.update(status='evaluated', result=joint,
        events=[event for event in joint['events'] if event['type'] == 'imu_acc_bias'],
        reason='按同通道原始行对应关系执行；沿用既有陀螺仪区段及重力幅值判据，不推断跨通道同步。')
    return evidence
