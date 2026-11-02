"""Canonical orientation at public reads; source values remain inspectable."""
import copy
from contextlib import contextmanager

from edc.data.transforms.orientation import orientation_spec, sample_shape

QUATERNION = {'kind': 'quaternion', 'order': 'xyzw', 'unit': '1'}


class OrientationInputError(ValueError):
    """An orientation lacks an explicit, compatible source representation."""


class CanonicalEpisode:
    """All normal orientation reads are N×4 XYZW in the original frames."""

    def __init__(self, source):
        self._source = source._source if isinstance(source, CanonicalEpisode) else source
        self.channels = {}
        self._orientation_rules = {}
        self._orientation_reads = {}

    def __getattr__(self, name):
        if name in {'id', 'path', 'format', 'profile', 'selector', 'metadata', 'backend',
                    'iter_frames', 'image_storage', 'video_source', 'materialize_video', 'video_evidence'}:
            return getattr(self._source, name)
        raise AttributeError(name)

    @property
    def recorded_channels(self):
        self._source.open()
        return copy.deepcopy(self._source.channels)

    def open(self):
        self._source.open()
        if self.channels:
            return self
        self.channels = copy.deepcopy(self._source.channels)
        from edc.data.transforms.episode import provenance
        for name, channel in self.channels.items():
            if 'orientation' not in channel.fields:
                continue
            original = self._source.channels[name]
            spec = original.field_spec('orientation')
            representation = copy.deepcopy(spec.get('representation') or original.representation)
            if 'unit' not in representation and spec['unit'] not in (None, 'unknown'):
                representation['unit'] = spec['unit']
            rule = {'channel': name, 'field': 'orientation',
                    'source_representation': representation, 'source_dimensions': spec['dimensions'],
                    'source_unit': spec['unit'], 'target_representation': copy.deepcopy(QUATERNION),
                    'source_mapping': {**provenance(self._source, name, 'orientation'),
                        'original_source_mapping': copy.deepcopy(original.source_mapping),
                        'time_source': original.time_source, 'clock': original.clock,
                        'coordinate_frame': spec['coordinate_frame'],
                        'frame_evidence': copy.deepcopy(original.frame_evidence)},
                    'status': 'conversion_required'}
            try:
                normalized = orientation_spec(representation)
                if spec['unit'] not in (None, 'unknown', normalized['unit']):
                    raise ValueError('Orientation representation unit conflicts with field unit')
                if spec['dimensions'] is not None and tuple(spec['dimensions']) != sample_shape(normalized):
                    raise ValueError('Orientation representation conflicts with source dimensions')
                rule['source_representation'] = normalized
                if normalized == QUATERNION:
                    rule['status'] = 'validation_required'
            except ValueError as exc:
                rule.update(status='blocked', reason=str(exc))
            self._orientation_rules[name] = rule
            channel.field_metadata.setdefault('orientation', {}).update(
                representation=copy.deepcopy(QUATERNION), dimensions=[4], unit='1',
                source_representation=copy.deepcopy(representation), source_unit=spec['unit'],
                source_dimensions=copy.deepcopy(spec['dimensions']),
                normalization_status=rule['status'])
            if channel.representation.get('kind') in {'quaternion', 'rpy', 'rotation_matrix', 'unknown'}:
                channel.representation = {k: v for k, v in channel.representation.items()
                                          if k not in {'kind', 'order', 'unit', 'axes', 'convention', 'layout', 'vectors', 'handedness'}}
                channel.representation.update(QUATERNION)
            if channel.fields == ['orientation']:
                channel.unit, channel.dimensions = '1', [4]
        return self

    def close(self):
        self._source.close()
        self.channels = {}
        self._orientation_rules = {}

    @contextmanager
    def opened(self):
        try:
            yield self.open()
        finally:
            self.close()

    def _resolve(self, channel):
        self.open()
        from edc.algorithms.channel_names import resolve_channel
        return resolve_channel(self, channel)

    def read_signal(self, channel, field):
        name = self._resolve(channel)
        if field != 'orientation':
            return self._source.read_signal(name, field)
        self.channels[name].field_spec(field)
        rule = self._orientation_rules[name]
        if rule['status'] == 'blocked':
            raise OrientationInputError(f'{name}.{field}: {rule["reason"]}')
        ledger = self._orientation_reads.setdefault(name, {'read_count': 0, 'failures': []})
        try:
            result = self._source.transform_orientation(channel, target=QUATERNION, field=field)
        except ValueError as exc:
            ledger['failures'].append(str(exc))
            raise
        ledger['read_count'] += 1
        ledger['params'] = copy.deepcopy(result.params)
        ledger['source_mapping'] = copy.deepcopy(result.source_mapping)
        return result.output

    def read_values(self, channel, indices=None):
        name = self._resolve(channel)
        c = self.channels[name]
        if 'orientation' not in c.fields:
            return self._source.read_values(name, indices)
        signals = {field: self.read_signal(name, field) for field in c.fields}
        rows = range(c.count) if indices is None else indices
        return [{field: values[index] for field, values in signals.items()} for index in rows]

    def inspect_recorded_signal(self, channel, field):
        """Explicit source-value diagnostics; no normalization or silent fallback."""
        return self._source.read_signal(self._resolve(channel), field)

    def orientation_evidence(self):
        self.open()
        fields = []
        for name, rule in self._orientation_rules.items():
            ledger = self._orientation_reads.get(name, {})
            fields.append({**copy.deepcopy(rule), **copy.deepcopy(ledger),
                           'read_count': ledger.get('read_count', 0),
                           'normalization_applied': ledger.get('read_count', 0) > 0})
        return {'schema_version': 'edc_orientation_normalization_v1',
                'policy': 'auto', 'target_representation': copy.deepcopy(QUATERNION),
                'source_unchanged': True, 'rows': 'identity', 'frame_transform': False,
                'resampling': False, 'fields': fields}

    def reset_orientation_evidence(self):
        self._orientation_reads.clear()

    def describe(self):
        self.open()
        return {**self._source.describe(), 'channels': [c.describe() for c in self.channels.values()],
                'orientation_normalization': self.orientation_evidence()}

    def describe_legacy(self):
        self.open()
        info = copy.deepcopy(self._source.describe_legacy())
        for name, c in self.channels.items():
            for key in ('field_metadata', 'representation', 'unit', 'dimensions'):
                setattr(info.topics[name], key, copy.deepcopy(getattr(c, key)))
        info.extra['orientation_normalization'] = self.orientation_evidence()
        return info

    def algorithm_view(self):
        from edc.data.ir.bridge import EpisodeAccess
        return self.describe_legacy(), EpisodeAccess(self)

    def transform_orientation(self, channel, *, target, field='orientation', tolerance=1e-6):
        return self._source.transform_orientation(channel, target=target, field=field, tolerance=tolerance)

    def transform_signal(self, channel, field, **kwargs):
        from edc.data.transforms.episode import signal
        return signal(self, channel, field, **kwargs)

    def transform_timestamps(self, channel, target_clock):
        return self._source.transform_timestamps(channel, target_clock)

    def forward_kinematics(self, chain):
        from edc.data.transforms.episode import fk
        return fk(self, chain)

    def align_signal(self, channel, field, reference, **kwargs):
        from edc.data.transforms.resampling import align_signal
        return align_signal(self, channel, field, reference, **kwargs)

    def time_axis(self, channel, *, time_source):
        from edc.data.ir.time_axis import time_axis
        return time_axis(self, channel, time_source=time_source)


class CanonicalDataset:
    def __init__(self, source):
        self._source = source._source if isinstance(source, CanonicalDataset) else source
        self._episodes = [CanonicalEpisode(ep) for ep in self._source.iter_episodes()]

    def __getattr__(self, name):
        if name in {'id', 'path', 'version', 'profile', 'tasks', 'task_catalog', 'metadata'}:
            return getattr(self._source, name)
        raise AttributeError(name)

    def iter_episodes(self):
        yield from self._episodes

    def describe(self):
        episodes = []
        for ep in self.iter_episodes():
            try:
                with ep.opened():
                    episodes.append(ep.describe())
            except Exception as exc:
                episodes.append({'id': ep.id, 'source': str(ep.path), 'error': str(exc)})
        return {'id': self.id, 'version': self.version, 'tasks': self.tasks,
                'metadata': self.metadata, 'episodes': episodes}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for ep in self._episodes:
            ep.close()


def open_dataset(path, profile=None, robot=None, *, backend='auto'):
    from edc.data.ir.dataset import open_dataset as open_source_dataset
    return CanonicalDataset(open_source_dataset(path, profile, robot, backend=backend))


def open_source_dataset(path, profile=None, robot=None, *, backend='auto'):
    """Compatibility/source audit entry; ordinary consumers use open_dataset."""
    from edc.data.ir.dataset import open_dataset as read_source
    return read_source(path, profile, robot, backend=backend)


def open_dataset_reference(reference):
    from edc.workflows.references import open_dataset_reference as read_source
    return CanonicalDataset(read_source(reference))
