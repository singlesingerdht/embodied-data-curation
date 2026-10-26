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


def align_signal(episode, channel, field, reference, *, method, max_gap=None):
    from .core import RobotTransforms
    from .episode import provenance
    episode.open()
    source, target = episode.channels[channel], episode.channels[reference]
    if source.timestamps is None or target.timestamps is None:
        raise ValueError('Alignment requires both channel timelines')
    if len(source.timestamps) != source.count or len(target.timestamps) != target.count:
        raise ValueError('Alignment timeline counts must match channels')
    clock = RobotTransforms(episode.profile).clock(source.timestamps, source.clock, target.clock,
                                                  time_source=source.time_source)
    result = resample(episode.read_signal(channel, field), clock.output, target.timestamps,
                      method=method, max_gap=max_gap)
    result.params.update(clock_transform=clock.params, clock=target.clock,
                         source_time_source=source.time_source, reference_time_source=target.time_source,
                         input_semantics=source.field_spec(field), proves_acquisition_sync=False)
    result.source_mapping.update(provenance(episode, channel, field),
                                 reference=provenance(episode, reference),
                                 original_source_timestamps=list(source.timestamps),
                                 rows=result.source_mapping['rows'])
    return result
