"""Action_Smoothness input: one arm's end-effector position (m) on that channel's own time axis (s)."""
import numpy as np


def nominal_frame_time(channel):
    """Time the storage format itself defines as frame index / fps (e.g. LeRobot `timestamp`): evenly spaced
    rows, enough for a single-channel derivative, never evidence of acquisition timing."""
    return (channel.time_source == 'synthetic'
            and (channel.time_evidence or {}).get('timestamp_unit_source') == 'format_standard')


def time_axis(channel):
    if channel.time_source not in {'acquisition', 'container'} and not nominal_frame_time(channel):
        raise ValueError(f'{channel.name}: 需要采集时间、容器时间或格式规定的帧时间')
    values = np.asarray(channel.timestamps, dtype=float)
    if (values.ndim != 1 or len(values) != channel.count or len(values) < 4
            or not np.isfinite(values).all() or np.any(np.diff(values) <= 0)):
        raise ValueError(f'{channel.name}: 时间轴需要有限、严格递增、与行数一致且至少 4 行')
    return values


def assess(episode, channel, params, context):
    from edc.algorithms.quality.algorithms.Action_Smoothness.shake import DEFAULTS, assess_arrays
    where = {'side': channel.side, 'input_channel': channel.name, 'input': channel.source, 'num_poses': channel.count}
    try:
        t = time_axis(channel)
    except ValueError as exc:
        return {'verdict': 'NEEDS_REVIEW', 'score': None, 'assessment_complete': False, 'reasons': [str(exc)], **where}
    positions = np.asarray(episode.read_signal(channel.name, 'position'), dtype=float)  # SI view, metres
    finite = np.isfinite(positions).all(axis=1)  # NaN rows are reported by Numeric_Check; here they are gaps.
    if finite.sum() < 4:
        return {'verdict': 'NEEDS_REVIEW', 'score': None, 'assessment_complete': False,
                'reasons': ['末端位置几乎全是 NaN/Inf，没法评价平滑度'], **where}
    report = assess_arrays(positions[finite], t[finite], {k: v for k, v in params.items() if k in DEFAULTS})
    return {**report, **where}
