"""Explicit smoothness timeline preparation; no inferred clocks or generated FPS."""
import copy

import numpy as np


def validate_options(params):
    timeline = params.get('timeline') or {}
    mode = timeline.get('mode', 'native')
    reference = timeline.get('reference_channel')
    if mode == 'reference_channel':
        if not reference:
            raise ValueError('reference_channel mode requires timeline.reference_channel')
        if timeline.get('time_source', 'acquisition') != 'acquisition':
            raise ValueError('reference_channel mode requires acquisition time, not container/playback time')
    elif reference:
        raise ValueError('native mode cannot declare a reference_channel')
    if params.get('window_length', 11) % 2 != 1:
        raise ValueError('window_length must be odd')
    if params.get('rate_tolerance_ratio', .2) >= 1:
        raise ValueError('rate_tolerance_ratio must be less than 1')


def _axis(channel, required=None):
    if required and channel.time_source != required:
        raise ValueError(f'{channel.name}: requires {required} timestamps, got {channel.time_source}')
    if channel.time_source not in {'acquisition', 'container'}:
        raise ValueError(f'{channel.name}: acquisition/container timestamps required')
    values = np.asarray(channel.timestamps, dtype=float)
    if (values.ndim != 1 or len(values) < 4 or len(values) != channel.count
            or not np.isfinite(values).all() or np.any(np.diff(values) <= 0)):
        raise ValueError(f'{channel.name}: timeline must be finite, strictly increasing, match rows and contain >=4 samples')
    return values


def timeline_plan(episode, channel, params):
    """Metadata-only gate shared by recommendation and actual execution."""
    validate_options(params)
    options = params.get('timeline') or {}
    mode = options.get('mode', 'native')
    reference = getattr(episode, '_smoothness_reference_channel', None)
    plan = {'mode': mode, 'source_channel': channel.name,
            'reference_channel': reference.name if reference else None,
            'source_frames': channel.count, 'expected_rate_hz': params.get('expected_rate_hz', 30),
            'rate_tolerance_ratio': params.get('rate_tolerance_ratio', .2),
            'source_time_source': channel.time_source, 'source_clock': channel.clock,
            'clock_policy': 'require_common_or_explicit_mapping',
            'clock_status': 'not_required_single_channel', 'issues': [],
            'resample': 'linear' if mode == 'reference_channel' else None,
            'proves_acquisition_sync': False, 'extrapolation': False}
    if reference:
        plan['reference'] = {'file': reference.source,
                             'source_mapping': copy.deepcopy(reference.source_mapping),
                             'time_evidence': copy.deepcopy(reference.time_evidence)}
    try:
        source = _axis(channel, 'acquisition' if mode == 'reference_channel' else options.get('time_source'))
        target = source
        plan['source_timestamps_s'] = source.tolist()
        plan['source_time_range_s'] = [float(source[0]), float(source[-1])]
        if mode == 'reference_channel':
            if reference is None:
                raise ValueError('Reference timeline channel is unavailable; no fallback allowed')
            target = _axis(reference, 'acquisition')
            plan.update(reference_clock=reference.clock, reference_time_source=reference.time_source,
                        reference_frames=reference.count,
                        reference_time_range_s=[float(target[0]), float(target[-1])],
                        reference_timestamps_s=target.tolist(), clock_status='unconfirmed')
            from edc.data.transforms import RobotTransforms
            try:
                mapped = RobotTransforms(episode.profile).clock(
                    source, channel.clock, reference.clock, time_source=channel.time_source)
            except ValueError as exc:
                plan['issues'].append(f'共同 clock 或显式映射未确认：{exc}')
                return plan
            source = mapped.output
            plan.update(clock_status='common_clock' if channel.clock == reference.clock else 'explicit_mapping',
                        clock_transform=mapped.params, mapped_source_timestamps_s=source.tolist())
            # Only true reference rows inside source support are eligible; never extrapolate.
            rows = np.flatnonzero((target >= source[0]) & (target <= source[-1]))
            plan['reference_rows'] = rows.tolist()
            plan['excluded_reference_rows'] = np.flatnonzero(
                (target < source[0]) | (target > source[-1])).tolist()
            target = target[rows]
            if len(target) < 4:
                raise ValueError('Insufficient overlapping reference timestamps; extrapolation forbidden')
        if len(target) < params.get('window_length', 11):
            raise ValueError('Evaluation rows are fewer than window_length; no silent filter-window change')
        dt = np.diff(target)
        rate = float(1 / np.median(dt))
        expected = params.get('expected_rate_hz', 30)
        tolerance = params.get('rate_tolerance_ratio', .2)
        compatible = bool(abs(rate / expected - 1) <= tolerance + 1e-12)
        window_span = params.get('window_length', 11) - 1
        plan.update(target_timestamps_s=target.tolist(),
                    evaluated_frames=len(target), time_range_s=[float(target[0]), float(target[-1])],
                    expected_rate_hz=expected, observed_median_rate_hz=rate,
                    rate_tolerance_ratio=tolerance, calibration_rate_compatible=compatible,
                    dt_min_s=float(np.min(dt)), dt_median_s=float(np.median(dt)), dt_max_s=float(np.max(dt)),
                    window_duration_median_s=float(np.median(target[window_span:] - target[:-window_span])))
        if not compatible:
            plan['issues'].append(f'实际中位频率 {rate:.3f} Hz 与标定 {expected:g} Hz 不匹配；'
                                  '诊断 jerk 不用于该阈值的质量结论，请选择参考时间轴或提供已标定参数')
    except ValueError as exc:
        plan['issues'].append(str(exc))
    return plan


def _review(issues):
    return {'score': None, 'passed': None, 'grade': 'N/A', 'verdict': 'NEEDS_REVIEW',
            'assessment_complete': False, 'reasons': list(issues),
            'method_version': 'smoothness_stationary_residual_explicit_dt_v8'}


def assess(episode, channel, params, context):
    from edc.data.transforms import resample
    from edc.algorithms.quality.algorithms.Action_Smoothness.run_check import assess_arrays
    plan = timeline_plan(episode, channel, params)
    result = _review(plan['issues'])
    if 'target_timestamps_s' in plan:
        positions = np.asarray(episode.read_signal(channel.name, 'position'))  # SI view
        if positions.shape != (channel.count, 3) or not np.isfinite(positions).all():
            plan['issues'].append('SI position must be a finite N x 3 matrix; no imputation applied')
            result = _review(plan['issues'])
        else:
            timestamps = np.asarray(plan['target_timestamps_s'])
            if plan['mode'] == 'reference_channel':
                transformed = resample(positions, plan['mapped_source_timestamps_s'], timestamps, method='linear')
                positions = transformed.output
                plan['source_mapping'] = transformed.source_mapping
                plan['resampling_params'] = transformed.params
                source = episode
                while hasattr(source, '_episode'):
                    source = source._episode
                if hasattr(source, '_clock_evidence'):
                    source._clock_evidence.append({'channel': channel.name,
                        'source_clock': channel.clock, 'target_clock': plan['reference_clock'],
                        'params': plan['clock_transform'], 'original_timestamps_s': channel.timestamps,
                        'time_source': channel.time_source, 'rows': 'identity',
                        'resampled': False, 'resampling_evidence': 'timeline.json'})
            else:
                plan['source_mapping'] = {'rows': 'identity'}
            report = assess_arrays(positions, timestamps, weights=params.get('weights'),
                window_length=params.get('window_length', 11),
                vel_threshold_factor=params.get('vel_threshold_factor', .1),
                jerk_threshold_m_s3=params.get('jerk_threshold_m_s3', 15))
            if plan['issues']:
                # Preserve raw diagnostics, but never publish an uncalibrated pass/fail.
                result['diagnostic_result'] = report
                result['raw'] = report['raw']
            else:
                result = report
    plan['source'] = {'file': str(episode.path), 'selector': copy.deepcopy(episode.selector),
                      'source_mapping': copy.deepcopy(channel.source_mapping),
                      'time_evidence': copy.deepcopy(channel.time_evidence),
                      'field': 'position', 'execution_unit': 'm', 'time_unit': 's'}
    context.write_result('timeline.json', plan)
    result['timeline'] = {k: v for k, v in plan.items() if k not in {
        'source_timestamps_s', 'mapped_source_timestamps_s', 'reference_timestamps_s',
        'target_timestamps_s', 'source_mapping', 'resampling_params', 'reference_rows', 'excluded_reference_rows'}}
    result['timeline']['evidence'] = 'timeline.json'
    result.update(input_channel=channel.name, side=channel.side,
                  input=channel.source, num_poses=plan.get('evaluated_frames', 0))
    return result
