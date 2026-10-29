"""Read only the selected edc episode; never reopen or resegment sources."""
import math
import numpy as np
from .plugins import family


def field_mapping(channel, params):
    kind = family(channel)
    expected = {
        'joint': {'position': 'rad'},
        'cmd': {'joint_q': 'rad'},
        'pose': {'position': 'm'},
        'imu': {'ang_vel': 'rad/s', 'lin_acc': 'm/s^2'},
    }.get(kind, {})
    explicit = params.get('field_map', {}).get(channel.name, {})
    if set(explicit) - set(expected):
        raise ValueError('Unsupported target field; current/torque and state/action are not interchangeable')
    mapping = {key: (explicit.get(key, key), unit) for key, unit in expected.items()}
    for key, (source, unit) in mapping.items():
        if source not in channel.fields:
            raise ValueError(f'Declare field_map for {channel.name}.{key}; available={channel.fields}')
        spec = channel.field_spec(source)
        if kind in {'joint', 'cmd'} and spec['source_kind'] != ('command' if kind == 'cmd' else 'feedback'):
            raise ValueError('Command/feedback field role conflicts with indicator')
    return mapping


class EpisodeBridge:
    def __init__(self, episode, channels, params):
        from edc.algorithms.quality.indicator_core.data.ir import DataInfo, TopicInfo
        self.episode, self.channels, self.params = episode, channels, params
        self.path = str(episode.path)
        self._values = {}
        valid = [float(t) for c in channels for t in (c.timestamps or []) if math.isfinite(t)]
        self.base = min(valid, default=0.0)
        modalities = {'camera': 'image', 'joint': 'joint_state', 'cmd': 'cmd', 'pose': 'pose', 'imu': 'imu'}
        topics = {}
        for c in channels:
            topics[c.name] = TopicInfo(c.name, '', c.count,
                modality=modalities.get(family(c), 'aux'),
                value_available=c.value_available, image_available=c.image_available,
                image_compressed=False, timestamps=list(c.timestamps) if c.timestamps is not None else None,
                source=c.source)
        self._info = DataInfo(self.path, episode.format, topics,
            duration=max(valid, default=self.base)-self.base, start_time=self.base,
            end_time=max(valid, default=self.base), extra={})

    def info(self):
        return self._info

    def close(self):
        pass

    def channel_values(self, name, indices=None):
        if name not in self._values:
            c = self.episode.channels[name]
            signals = {target: np.asarray(self.episode.read_signal(name, source))
                       for target, (source, _) in field_mapping(c, self.params).items()}
            if any(len(v) != c.count for v in signals.values()):
                raise ValueError('Payload row count does not match episode channel')
            self._values[name] = [{key: np.atleast_1d(value[i]).tolist() for key, value in signals.items()}
                                  for i in range(c.count)]
        values = self._values[name]
        return values if indices is None else [values[i] for i in indices]

    def iter_values(self, name):
        return iter(self.channel_values(name))

    def image_bytes(self, name):
        c = self.episode.channels[name]
        count = 0
        for frame in self.episode.iter_frames(name):
            if frame['index'] != count:
                raise ValueError('RGB frame indices must preserve episode row order')
            arr = frame['image']
            if not isinstance(arr, np.ndarray) or arr.ndim != 3 or arr.shape[2] != 3 or arr.dtype != np.uint8:
                raise ValueError('Full-frame indicator requires uint8 RGB')
            yield count, c.timestamps[count], arr
            count += 1
        if count != c.count:
            raise ValueError('Decoded image count differs from selected episode')

    def source_mapping(self):
        return {'episode_id': self.episode.id, 'source': self.path,
            'selector': self.episode.selector, 'time_origin_s': self.base,
            'channels': [{'name': c.name, 'source': c.source, 'count': c.count,
                'time_source': c.time_source, 'clock': c.clock, 'selection': c.selection,
                'unit': c.unit, 'source_kind': c.source_kind, 'dimensions': c.dimensions,
                'calibration_status': c.calibration_status, 'coordinate_frame': c.coordinate_frame,
                'source_mapping': c.source_mapping, 'field_metadata': c.field_metadata,
                'rows': 'identity within selected episode'} for c in self.channels]}
