"""Explicit access to recorded clocks, without changing the selected timeline."""
import copy
from pathlib import Path

import numpy as np

from edc.data.transforms.core import TransformResult
from edc.data.transforms.episode import provenance


def time_axis(episode, channel, *, time_source):
    """Return recorded seconds plus original nanoseconds and identity row mapping.

    Container clocks belong to a source file, never to a robot model. Missing
    acquisition stamps cannot fall back to recorder timestamps.
    """
    from edc.algorithms.channel_names import resolve_channel
    episode.open()
    channel = resolve_channel(episode, channel)
    selected = episode.channels[channel]
    if time_source not in {'acquisition', 'container'}:
        raise ValueError('time_source must be acquisition or container')
    source = episode
    while getattr(source, '_source', None) is not None or getattr(source, '_episode', None) is not None:
        source = getattr(source, '_source', None) or source._episode
    original_name = selected.selection.get('channel', channel)
    original = source.channels[original_name]
    topic = source.describe_legacy().topics[original_name]
    raw = getattr(topic, f'{time_source}_timestamps_ns', None)
    evidence = copy.deepcopy(original.time_evidence)
    if raw is not None:
        if (len(raw) != selected.count or any(t is None for t in raw)
                or time_source == 'acquisition' and any(t == 0 for t in raw)):
            raise ValueError(f'{channel}: missing/zero {time_source} timestamps; no fallback')
        values = np.asarray(raw, dtype=np.int64).astype(float) * 1e-9
        field = evidence.get(f'{time_source}_field', 'header.stamp' if time_source == 'acquisition' else 'log_time')
        clock = (f'container:{Path(episode.path).resolve()}:{field}'
                 if time_source == 'container' else original.clock)
    elif original.time_source == time_source and original.timestamps is not None:
        values = np.asarray(original.timestamps, dtype=float).copy()
        field = evidence.get('timestamp_field')
        clock = original.clock
    else:
        raise ValueError(f'{channel}: no recorded {time_source} timeline; no fallback')
    if values.ndim != 1 or len(values) != selected.count or not np.isfinite(values).all():
        raise ValueError(f'{channel}: invalid {time_source} timeline/count')
    return TransformResult(values, {
        'operation': 'time_axis', 'time_source': time_source, 'clock': clock,
        'unit': 's', 'source_field': field, 'time_evidence': evidence,
        'proves_acquisition_sync': False,
    }, {**provenance(episode, channel), 'source_channel': original_name,
        'timestamps_ns': list(raw) if raw is not None else None, 'rows': 'identity'})
