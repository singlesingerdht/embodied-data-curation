"""Official LeRobot I/O wrapped in the existing detection contract.

get_raw_item still runs the upstream torch transform. Use an unformatted HF view
and explicitly reconstruct the feature dtype instead, preserving float64 values.
"""
import fnmatch
import json
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import numpy as np
from framework.readers.base import DataReader
from framework.ir.metadata import DataInfo, TopicInfo

OFFICIAL_VERSION = '0.6.1'


def official_class():
    try:
        installed = version('lerobot')
    except PackageNotFoundError as exc:
        raise ImportError('Official backend requires Python >=3.12 and embodied-dqs[lerobot]') from exc
    if installed != OFFICIAL_VERSION:
        raise ImportError(f'Official backend validated with lerobot=={OFFICIAL_VERSION}; found {installed}')
    try:
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
    except ImportError as exc:
        raise ImportError(f'Official dataset dependencies unavailable: {exc}; install embodied-dqs[lerobot]') from exc
    return LeRobotDataset


def local_official(root, episodes=None):
    """Local-only loader; missing inputs must never download or repair."""
    root = Path(root).resolve()
    info = json.loads((root / 'meta/info.json').read_text())
    if info.get('codebase_version') != 'v3.0':
        raise ValueError('Official backend requires v3.0; convert a copy or request backend=compat')
    import pyarrow.parquet as pq
    paths = sorted((root / 'meta/episodes').rglob('*.parquet'))
    if not paths:
        raise FileNotFoundError('Missing LeRobot episode metadata')
    rows = [r for p in paths for r in pq.read_table(p).to_pylist()]
    if len(rows) != info['total_episodes'] or len({r['episode_index'] for r in rows}) != len(rows):
        raise ValueError('LeRobot episode metadata count/identity mismatch')
    if info.get('total_tasks', 0) and not (root / 'meta/tasks.parquet').is_file():
        raise FileNotFoundError('Missing LeRobot task metadata')
    for row in rows:
        for key in [None] + [k for k, f in info['features'].items() if f['dtype'] == 'video']:
            prefix = 'data/' if key is None else f'videos/{key}/'
            template = info['data_path'] if key is None else info['video_path']
            path = (root / template.format(chunk_index=int(row[prefix+'chunk_index']),
                      file_index=int(row[prefix+'file_index']), video_key=key)).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise FileNotFoundError(f'Missing/outside dataset source: {path}')
    cls = official_class()

    class LocalDataset(cls):
        def _download(self, *args, **kwargs):
            raise FileNotFoundError('Incomplete local dataset; automatic download is disabled')

    return LocalDataset(repo_id='local/dqs', root=root, episodes=episodes,
                        revision='local', video_backend='pyav', return_uint8=True,
                        image_transforms=None, delta_timestamps=None, token=False)


def bindings(profile, features):
    """Mappings contain locations only; physical declarations live in channels."""
    declared = profile.get('lerobot', {}).get('feature_mapping')
    reserved = {'timestamp', 'frame_index', 'episode_index', 'index', 'task_index'}
    if declared is None:
        return {k: {'feature': k, 'source_sample_shape': f['shape']}
                for k, f in features.items() if k not in reserved}
    if not isinstance(declared, dict):
        raise ValueError('lerobot.feature_mapping must be a mapping')
    for name, item in declared.items():
        if not isinstance(item, dict) or item.get('feature') not in features:
            raise ValueError(f'{name}: missing LeRobot feature')
        if set(item) - {'feature', 'field', 'source_sample_shape'}:
            raise ValueError(f'{name}: put semantics in channels; mappings accept location/shape only')
        feature_shape = tuple(features[item['feature']].get('shape', ()))
        shape = item.get('source_sample_shape', list(feature_shape))
        if not isinstance(shape, list) or any(type(n) is not int or n <= 0 for n in shape):
            raise ValueError(f'{name}: invalid source_sample_shape')
        if tuple(shape) != feature_shape and not (shape == [] and feature_shape == (1,)):
            raise ValueError(f'{name}: source shape conflicts with LeRobot feature; select components explicitly')
    covered = {i['feature'] for i in declared.values()}
    required = set(features) - reserved
    if covered != required:
        raise ValueError(f'Unmapped LeRobot source features: {sorted(required-covered)}')
    return declared


class OfficialReader(DataReader):
    def open(self, path):
        return self.info

    def close(self):
        self.dataset = self.raw = None

    def numeric(self, name):
        item = self.mapping[name]
        feature = self.features[item['feature']]
        column = self.raw.select_columns([item['feature']])[item['feature']][:]
        shape = tuple(item.get('source_sample_shape', feature['shape']))
        return np.asarray(column, dtype=feature['dtype']).reshape((len(self.raw),) + shape)

    def channel_values(self, name, indices=None):
        values = self.numeric(name)
        fields = self.info.topics[name].fields
        if len(fields) != 1:
            raise ValueError(f'{name}: one mapped field required; select components explicitly')
        return [{fields[0]: values[i]} for i in (range(len(values)) if indices is None else indices)]

    def read_field(self, info, name, field):
        if field not in info.topics[name].fields:
            raise ValueError(f'{name}: undeclared field {field}')
        return self.numeric(name)

    def image_storage(self, name):
        dtype = self.features[self.mapping[name]['feature']]['dtype']
        return 'video' if dtype == 'video' else 'frames' if dtype == 'image' else 'unknown'

    def iter_frames(self, name):
        item = self.mapping[name]
        feature = self.features[item['feature']]
        timestamps = self.info.topics[name].timestamps
        column = None if feature['dtype'] == 'video' else self.raw.select_columns([item['feature']])
        for i in range(len(self.raw)):
            if feature['dtype'] == 'video':
                pixels = self.dataset[i][item['feature']].numpy().transpose(1, 2, 0)
            elif feature['dtype'] == 'image':
                pixels = np.asarray(column[i][item['feature']])
            else:
                pixels = np.asarray(column[i][item['feature']], dtype=feature['dtype'])
            yield {'index': i, 'source_index': i, 'playback_time': i / self.fps,
                   'source_time': timestamps[i] if timestamps is not None else None, 'image': pixels}

    def materialize_image_channel(self, info, name, output_path=None):
        if name in self.video_sources:
            path, start, end = self.video_sources[name]
            if start is None and end is None:
                return str(path)
            if start == 0:
                # An episode occupying the entire original file needs no re-encoding.
                from framework.readers.video import video_frames
                frames = list((f['index'], f['source_time']) for f in video_frames(path))
                if (len(frames) == len(self.raw) and frames and frames[0][1] >= start - 1e-7
                        and frames[-1][1] < end - 1e-7):
                    return str(path)
        from framework.readers.video import cache_path, encode_frames
        if info.topics[name].modality == 'depth':
            raise ValueError('Depth cannot pass through quantizing RGB video encoding')
        output = output_path or cache_path(self.episode.path, name, self.episode.selector)
        result = encode_frames(self.iter_frames(name), output, fps=self.fps, expected_count=len(self.raw))
        from framework.results import write_json
        write_json(Path(str(result)+'.evidence.json'), {'inspection_object': 'derived_preview',
                    'source_channel': name, 'source_dataset': str(self.episode.path),
                    'source_encoding_integrity': 'not_assessed_by_materialization'})
        return result


def open_official(episode):
    reader = OfficialReader()
    reader.episode = episode
    reader.dataset = local_official(episode.path, [episode.selector['episode_index']])
    reader.raw = reader.dataset.hf_dataset.with_format(None)
    if len(reader.raw) != episode.selector['length']:
        raise ValueError('Official episode length differs from explicit metadata')
    if list(reader.raw.select_columns(['frame_index'])['frame_index'][:]) != list(range(len(reader.raw))):
        raise ValueError('LeRobot frame_index must be unique, ordered and contiguous')
    reader.features = episode.selector['info']['features']
    reader.mapping = bindings(episode.profile, reader.features)
    reader.fps = float(episode.selector['info']['fps'])
    if not np.isfinite(reader.fps) or reader.fps <= 0:
        raise ValueError('Invalid LeRobot nominal FPS')
    reader.video_sources = {}
    topics = {}
    timeline = episode.profile.get('lerobot', {}).get('original_time', {})
    timestamps = None
    recorded_timestamps = None
    unit = 'unknown'
    if timeline.get('feature'):
        key = timeline['feature']
        if key not in reader.features:
            raise ValueError('Missing original_time feature')
        from framework.readers.timeline import declared_timestamp_unit
        unit = declared_timestamp_unit(timeline.get('unit'))
        recorded_timestamps = np.asarray(reader.raw.select_columns([key])[key][:], dtype=reader.features[key]['dtype']).reshape(-1).tolist()
        if unit != 'unknown':
            from framework.transforms.core import convert_units
            timestamps = convert_units(np.asarray(reader.raw.select_columns([key])[key][:], dtype=reader.features[key]['dtype']).reshape(-1), unit, 's').output.tolist()
    for name, item in reader.mapping.items():
        feature = reader.features[item['feature']]
        spec = next((v for pat, v in episode.profile.get('channels', {}).items() if fnmatch.fnmatchcase(name, pat)), {})
        if timestamps is not None:
            if spec.get('clock', timeline.get('clock')) != timeline.get('clock'):
                raise ValueError(f'{name}: conflicting original-time clock; explicit clock mapping required')
            if spec.get('time_source', timeline.get('time_source', 'unknown')) != timeline.get('time_source', 'unknown'):
                raise ValueError(f'{name}: conflicting original-time source')
        modality = spec.get('modality', 'rgb' if feature['dtype'] in {'image', 'video'} else 'other')
        fields = spec.get('fields', [item.get('field', 'recorded_values')] if feature['dtype'] not in {'image', 'video'} else [])
        if item.get('field') and fields != [item['field']]:
            raise ValueError(f'{name}: conflicting field mapping')
        numeric = feature['dtype'] not in {'image', 'video', 'string'}
        topics[name] = TopicInfo(name=name, modality=modality, count=len(reader.raw), fields=fields,
            value_available=numeric, image_available=feature['dtype'] in {'image', 'video'} or modality == 'depth',
            image_compressed=feature['dtype'] == 'video', source=str(episode.path), timestamps=timestamps,
            time_source=timeline.get('time_source', 'unknown') if timestamps is not None else 'unknown')
        topics[name].clock = timeline.get('clock')
        if recorded_timestamps is not None:
            topics[name].recorded_timestamp_values = recorded_timestamps
            topics[name].recorded_timestamp_unit = unit
            topics[name].time_evidence = {'timeline_scope': 'shared_dataset_rows',
                'timestamp_field': timeline['feature'], 'timestamp_unit': unit,
                'algorithm_timestamp_unit': 's' if timestamps is not None else None,
                'unavailable_reason': ('Original timestamp unit is unconfirmed; no seconds or clock inferred'
                                       if unit == 'unknown' else None),
                'time_generated': timeline.get('time_source') == 'synthetic',
                'independent_acquisition_timeline': False, 'source_row_mapping': 'identity',
                'establishes_acquisition_sync': False}
        if feature['dtype'] == 'video':
            row = episode.selector['metadata']
            prefix = f'videos/{item["feature"]}/'
            start, end = float(row[prefix+'from_timestamp']), float(row[prefix+'to_timestamp'])
            if not np.isfinite([start, end]).all() or end <= start:
                raise ValueError(f'{name}: invalid video interval')
            relative = episode.selector['info']['video_path'].format(video_key=item['feature'], chunk_index=int(row[prefix+'chunk_index']), file_index=int(row[prefix+'file_index']))
            path = (episode.path / relative).resolve()
            if not path.is_relative_to(episode.path) or not path.is_file():
                raise FileNotFoundError(path)
            reader.video_sources[name] = (path, start, end)
            topics[name].source = str(path)
            topics[name].msg_type = 'video/mp4'
    reader.info = DataInfo(path=str(episode.path), format='lerobot', topics=topics,
        extra={'reader_backend': 'official', 'lerobot_version': OFFICIAL_VERSION,
               'index_time_source': 'synthetic', 'original_time': timeline,
               'source_mapping': episode.selector.get('source_mapping', {})})
    reader._info = reader.info
    return reader, reader.info
