"""Explicit numeric resampling; never invoked by reading or by default execution."""
import numpy as np
from .core import TransformResult


def resample(values, timestamps, target_timestamps, *, method, max_gap=None):
    """Resample numeric rows in one explicitly shared clock, without extrapolation.

    linear is component-wise, not rotation/quaternion interpolation. max_gap is
    nearest sample distance for nearest, and bracketing interval for linear.
    """
    data = np.asarray(values)
    source = np.asarray(timestamps, dtype=float)
    target = np.asarray(target_timestamps, dtype=float)
    for axis in (source, target):
        if axis.ndim != 1 or not len(axis) or not np.isfinite(axis).all() or np.any(np.diff(axis) <= 0):
            raise ValueError('Resampling requires finite, nonempty, strictly increasing timelines')
    if data.ndim < 1 or len(data) != len(source) or data.dtype.kind not in 'fiu':
        raise ValueError('Numeric rows must match source timestamps')
    if method not in {'nearest', 'linear'}:
        raise ValueError('Resampling method must be nearest or linear')
    if max_gap is not None and (not np.isfinite(max_gap) or max_gap <= 0):
        raise ValueError('max_gap must be positive and finite')
    if target[0] < source[0] or target[-1] > source[-1]:
        raise ValueError('Resampling extrapolation is forbidden')
    right = np.searchsorted(source, target, side='left')
    left = np.maximum(right - 1, 0)
    exact = source[right] == target
    left[exact] = right[exact]
    if method == 'nearest':
        selected = np.where(target - source[left] <= source[right] - target, left, right)
        if max_gap is not None and np.any(np.abs(source[selected] - target) > max_gap):
            raise ValueError('Nearest sample distance exceeds max_gap')
        output = data[selected].copy()
        rows = {'indices': selected.tolist()}
    else:
        span = source[right] - source[left]
        if max_gap is not None and np.any(span > max_gap):
            raise ValueError('Interpolation interval exceeds max_gap')
        weight = np.divide(target-source[left], span, out=np.zeros_like(target), where=span != 0)
        w = weight.reshape((-1,) + (1,) * (data.ndim-1))
        output = (1-w)*data[left] + w*data[right]
        output[exact] = data[right[exact]]  # Do not contaminate exact rows with adjacent NaN.
        rows = {'left_indices': left.tolist(), 'right_indices': right.tolist(), 'right_weights': weight.tolist()}
    return TransformResult(output, {'operation': 'resample', 'method': method, 'max_gap': max_gap,
        'target_timestamps': target.tolist(), 'extrapolation': False},
        {'source_timestamps': source.tolist(), 'rows': rows})


def align_signal(episode, channel, field, reference, *, method, max_gap=None, time_source=None):
    from .core import RobotTransforms
    from .episode import provenance
    episode.open()
    from edc.algorithms.channel_names import resolve_channel
    channel, reference = resolve_channel(episode, channel), resolve_channel(episode, reference)
    source, target = episode.channels[channel], episode.channels[reference]
    source_times, target_times = source.timestamps, target.timestamps
    source_clock, target_clock = source.clock, target.clock
    source_origin, target_origin = source.time_source, target.time_source
    axes = None
    if time_source is not None:
        from edc.data.ir.time_axis import time_axis
        a = time_axis(episode, channel, time_source=time_source)
        b = time_axis(episode, reference, time_source=time_source)
        source_times, target_times = a.output, b.output
        source_clock, target_clock = a.params['clock'], b.params['clock']
        source_origin = target_origin = time_source
        axes = {'source': a.describe(), 'reference': b.describe()}
    if source_times is None or target_times is None:
        raise ValueError('Alignment requires both channel timelines')
    if source_origin != target_origin:
        raise ValueError('Alignment requires the same time source for both channels')
    if len(source_times) != source.count or len(target_times) != target.count:
        raise ValueError('Alignment timeline counts must match channels')
    clock = RobotTransforms(episode.profile).clock(source_times, source_clock, target_clock,
                                                  time_source=source_origin)
    result = resample(episode.read_signal(channel, field), clock.output, target_times,
                      method=method, max_gap=max_gap)
    result.params.update(clock_transform=clock.params, clock=target_clock,
                         source_time_source=source_origin, reference_time_source=target_origin,
                         input_semantics=source.field_spec(field), proves_acquisition_sync=False)
    if axes is not None:
        result.params['time_axes'] = axes
    result.source_mapping.update(provenance(episode, channel, field),
                                 reference=provenance(episode, reference),
                                 original_source_timestamps=list(source_times),
                                 rows=result.source_mapping['rows'])
    return result
