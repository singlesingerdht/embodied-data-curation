"""Fidelity-first conversion to standard LeRobot v3 with supplemental robot YAML.

Uses upstream feature schemas, Hugging Face serialization and upstream metadata
writer, rather than a second data format. Statistics are deliberately omitted:
upstream quantile estimation cannot represent arbitrary NaN/Inf and is unrelated
to conversion acceptance. Source values are never cleaned to compute statistics.
"""
import copy
from datetime import datetime, timezone
import fnmatch
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import uuid
import zlib
import numpy as np
import yaml
from framework.results import write_json, append_record
from framework.robots.profiles import load_profile
from framework.readers.lerobot_official import official_class, local_official, OFFICIAL_VERSION


class ConversionError(ValueError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _spec(profile, name):
    return copy.deepcopy(next((v for p, v in profile.get('channels', {}).items()
                               if fnmatch.fnmatchcase(name, p)), {}))


def _plain(value):
    if isinstance(value, bytes):
        return value.decode('utf-8')
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, 'tolist'):
        return _plain(value.tolist())
    return value


def _image_shape(ds):
    value = ds.attrs.get('orig_shape', ds.shape[1:])
    if isinstance(value, (str, bytes)):
        value = [int(n) for n in re.findall(r'[0-9]+', str(value))]
    shape = tuple(int(n) for n in value)
    if not shape or any(n <= 0 for n in shape):
        raise ConversionError(f'{ds.name}: missing/invalid original image shape')
    return shape


def _hdf_sources(episode, profile, evidence):
    import h5py
    from framework.readers.video import decode_image
    handle = h5py.File(episode.path, 'r')
    try:
        group = handle[episode.selector.get('group', '/')]
        sources = {}
        group.visititems(lambda name, obj: sources.update({name: obj}) if isinstance(obj, h5py.Dataset) else None)
        key = profile.get('timestamp_column')
        if not key or key not in sources or sources[key].ndim != 1:
            raise ConversionError('HDF5 conversion requires an explicit one-dimensional timestamp_column')
        count = len(sources[key])
        if not count:
            raise ConversionError('Empty source timeline')
        inventory, columns, mappings = [], {}, {}
        used = set()
        explicit_events = set(profile.get('lerobot', {}).get('non_frame_fields', []))
        for ordinal, (name, ds) in enumerate(sorted(sources.items())):
            row = {'field': name, 'source_file': str(episode.path), 'selector': episode.selector,
                   'dtype': str(ds.dtype), 'shape': list(ds.shape), 'attributes': _plain(dict(ds.attrs))}
            inventory.append(row)
            write_json(evidence / 'source-inventory.json', inventory)
            if ds.ndim == 0 or 0 in ds.shape or name in explicit_events:
                if ds.dtype.kind not in 'biufcSU':
                    raise ConversionError(f'{name}: unsupported non-frame dtype; explicit serialization required')
                target = evidence / f'nonframe-{ordinal}.npy'
                np.save(target, ds[()], allow_pickle=False)
                row.update(disposition='empty' if 0 in ds.shape else 'non_frame', evidence=target.name,
                           evidence_sha256=sha256(target))
                continue
            if len(ds) != count:
                raise ConversionError(f'{name}: {len(ds)} rows != {count}; multirate/missing rows cannot be aligned implicitly')
            spec = _spec(profile, name)
            modality = spec.get('modality', 'other')
            shape = tuple(ds.shape[1:])
            feature = 'source.' + name.replace('/', '.')
            if feature in used:
                raise ConversionError(f'Feature name collision: {feature}')
            used.add(feature)
            if modality == 'rgb':
                shape = _image_shape(ds)
                if len(shape) != 3 or shape[-1] != 3:
                    raise ConversionError(f'{name}: RGB HWC shape required')
                def load(i, ds=ds, shape=shape):
                    pixels = decode_image(ds[i])
                    if pixels.dtype != np.uint8 or pixels.shape != shape:
                        raise ConversionError(f'{ds.name}: decoded RGB shape/dtype mismatch at frame {i}')
                    return pixels
                dtype = 'image'
                encoded = lambda i, ds=ds: bytes(ds[i])
            elif modality == 'depth' and ds.dtype.kind == 'O':
                shape = _image_shape(ds)
                if ds.attrs.get('compressed_with') != 'zlib' or str(ds.attrs.get('orig_dtype')) != 'uint16':
                    raise ConversionError(f'{name}: depth requires explicit zlib + uint16 source declaration')
                expected_bytes = int(np.prod(shape)) * 2
                def load(i, ds=ds, shape=shape, expected_bytes=expected_bytes):
                    decoder = zlib.decompressobj()
                    payload = decoder.decompress(bytes(ds[i]), expected_bytes + 1)
                    if len(payload) != expected_bytes or not decoder.eof or decoder.unused_data:
                        raise ConversionError(f'{ds.name}: corrupt depth payload at frame {i}')
                    return np.frombuffer(payload, dtype=np.uint16).reshape(shape).copy()
                dtype = 'uint16'
                encoded = lambda i, ds=ds: bytes(ds[i])
            elif ds.dtype.kind in 'biuf':
                dtype = ds.dtype.name
                load = lambda i, ds=ds: np.asarray(ds[i])
                encoded = None
            else:
                raise ConversionError(f'{name}: unsupported source field; conversion refuses silent field loss')
            row.update(disposition='frame_feature', feature=feature, decoded_dtype='uint8' if dtype == 'image' else dtype,
                       sample_shape=list(shape))
            columns[feature] = {'dtype': dtype, 'shape': shape or (1,), 'names': None,
                'load': load, 'source_shape': shape, 'encoded': encoded, 'inventory': row}
            mappings[name] = {'feature': feature, 'source_sample_shape': list(shape)}
            if dtype != 'image':
                fields = spec.get('fields', ['recorded_values'])
                if len(fields) != 1:
                    raise ConversionError(f'{name}: one field per source array required; select components explicitly')
                mappings[name]['field'] = fields[0]
        write_json(evidence / 'source-inventory.json', inventory)
        task = _plain(group.attrs.get('task_name', profile.get('lerobot', {}).get('task', '')))
        yield count, columns, mappings, inventory, {'attributes': _plain(dict(group.attrs)), 'tasks': [task]}, key
    finally:
        handle.close()


def _v2_sources(episode, profile, evidence):
    import pyarrow.parquet as pq
    info = episode.selector['info']
    if info.get('codebase_version') != 'v2.1':
        raise ConversionError('Existing LeRobot conversion currently accepts v2.1 only; v3 is already readable')
    index = episode.selector['episode_index']
    relative = info['data_path'].format(episode_chunk=index // info.get('chunks_size', 1000), episode_index=index)
    path = (episode.path / relative).resolve()
    if not path.is_relative_to(episode.path) or not path.is_file():
        raise FileNotFoundError(path)
    table = pq.read_table(path)
    count = episode.selector['length']
    if len(table) != count or table['episode_index'].to_pylist() != [index]*count:
        raise ConversionError('v2.1 episode boundary/length mismatch')
    if table['frame_index'].to_pylist() != list(range(count)):
        raise ConversionError('v2.1 frame_index must be unique and contiguous; no silent sorting')
    inventory, columns, mappings = [], {}, {}
    reserved = {'timestamp', 'episode_index', 'frame_index', 'index', 'task_index'}
    for name in table.column_names:
        ft = info.get('features', {}).get(name)
        if ft is None:
            raise ConversionError(f'{name}: source column missing from feature metadata')
        if ft['dtype'] in {'video', 'image'}:
            raise ConversionError('Unexpected embedded video/image column in v2.1 source')
        import pyarrow as pa
        storage_type = table.schema.field(name).type
        while pa.types.is_list(storage_type) or pa.types.is_large_list(storage_type) or pa.types.is_fixed_size_list(storage_type):
            storage_type = storage_type.value_type
        if str(storage_type) != str(pa.from_numpy_dtype(np.dtype(ft['dtype']))):
            raise ConversionError(f'{name}: source Parquet dtype {storage_type} conflicts with declared {ft["dtype"]}; refuse precision change')
        feature = f'source.{name}' if name in reserved else name
        if feature in columns or (name in reserved and feature in table.column_names):
            raise ConversionError(f'Feature name collision: {feature}')
        shape = tuple(ft['shape'])
        # The physical storage convention for shape(1,) is scalar; preserve its source layout.
        source_shape = () if shape == (1,) else shape
        data = np.asarray(table[name].to_pylist(), dtype=ft['dtype']).reshape((count,) + source_shape)
        row = {'field': name, 'source_file': str(path), 'shape': list(data.shape), 'dtype': str(data.dtype),
               'disposition': 'frame_feature', 'feature': feature, 'sample_shape': list(source_shape)}
        inventory.append(row)
        columns[feature] = {'dtype': ft['dtype'], 'shape': shape, 'names': ft.get('names'),
                           'load': lambda i, data=data: data[i], 'source_shape': source_shape,
                           'encoded': None, 'inventory': row}
        mappings[feature if name in reserved else name] = {'feature': feature, 'field': _spec(profile, name).get('fields', ['recorded_values'])[0],
                                                          'source_sample_shape': list(source_shape)}
    for name, ft in info['features'].items():
        if ft['dtype'] != 'video':
            if name not in table.column_names:
                raise ConversionError(f'{name}: declared source feature is missing')
            continue
        relative = info['video_path'].format(episode_chunk=index // info.get('chunks_size', 1000), episode_index=index, video_key=name)
        source = (episode.path / relative).resolve()
        if not source.is_relative_to(episode.path) or not source.is_file():
            raise FileNotFoundError(source)
        row = {'field': name, 'source_file': str(source), 'dtype': 'video', 'shape': [count]+ft['shape'],
               'disposition': 'encoded_video_copy', 'feature': name, 'sha256': sha256(source)}
        inventory.append(row)
        columns[name] = {'dtype': 'video', 'shape': tuple(ft['shape']), 'names': ft.get('names'),
                         'info': ft.get('info', {}), 'video_source': source, 'inventory': row}
        mappings[name] = {'feature': name, 'source_sample_shape': ft['shape']}
    return count, columns, mappings, inventory, copy.deepcopy(episode.selector['metadata']), 'source.timestamp'


def _write_episode(meta, columns, count, index, root, global_offset, task_indices, fps):
    import datasets
    import pyarrow as pa
    import pyarrow.parquet as pq
    from lerobot.datasets.feature_utils import get_hf_features_from_features
    from PIL import Image
    features = get_hf_features_from_features(meta.features)
    data_relative = meta.data_path.format(chunk_index=0, file_index=index)
    target = root / data_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    try:
        for first in range(0, count, 16):
            stop = min(first + 16, count)
            values = {'timestamp': np.arange(first, stop, dtype=np.float32) / fps,
                      'frame_index': np.arange(first, stop, dtype=np.int64),
                      'episode_index': np.full(stop-first, index, dtype=np.int64),
                      'index': np.arange(global_offset+first, global_offset+stop, dtype=np.int64),
                      'task_index': np.asarray(task_indices[first:stop], dtype=np.int64)}
            for key, column in columns.items():
                if column['dtype'] == 'video':
                    continue
                rows = [column['load'](i) for i in range(first, stop)]
                if column['dtype'] == 'image':
                    images = []
                    for pixels in rows:
                        buffer = io.BytesIO()
                        Image.fromarray(pixels).save(buffer, format='PNG')
                        images.append({'bytes': buffer.getvalue(), 'path': None})
                    values[key] = images
                else:
                    values[key] = np.asarray(rows, dtype=column['dtype'])
                    if tuple(column['shape']) == (1,):
                        values[key] = values[key].reshape(-1)
            table = datasets.Dataset.from_dict(values, features=features).data.table
            if writer is None:
                writer = pq.ParquetWriter(target, table.schema, compression='zstd')
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()
    ep_meta = {'data/chunk_index': 0, 'data/file_index': index, 'data/from_timestamp': 0.,
               'data/to_timestamp': count / fps}
    for key, column in columns.items():
        if column['dtype'] != 'video':
            continue
        relative = meta.video_path.format(video_key=key, chunk_index=0, file_index=index)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(column['video_source'], destination)
        if sha256(destination) != column['inventory']['sha256']:
            raise ConversionError(f'{key}: encoded video copy hash mismatch')
        ep_meta.update({f'videos/{key}/chunk_index': 0, f'videos/{key}/file_index': index,
                        f'videos/{key}/from_timestamp': 0., f'videos/{key}/to_timestamp': count / fps})
    return ep_meta


def _verify_episode(root, index, columns, count, evidence):
    dataset = local_official(root, [index])
    raw = dataset.hf_dataset.with_format(None)
    if len(raw) != count:
        raise ConversionError('Readback episode row count mismatch')
    from framework.readers.video import video_frames
    checks = []
    for key, column in columns.items():
        if column['dtype'] == 'video':
            destination = root / dataset.meta.get_video_file_path(index, key)
            timestamps = [frame['source_time'] for frame in video_frames(destination)]
            decoded = len(timestamps)
            if (decoded != count or sha256(destination) != column['inventory']['sha256'] or
                    not np.allclose(timestamps, np.arange(count)/dataset.meta.fps, rtol=0, atol=1e-4)):
                raise ConversionError(f'{key}: video byte/frame boundary verification failed')
            checks.append({'feature': key, 'check': 'encoded_bytes_and_all_frame_boundaries', 'frames': decoded, 'equal': True})
            continue
        selected = raw.select_columns([key])
        encoded_ledger = evidence / (hashlib.sha256(key.encode()).hexdigest()[:16]+'.frames.jsonl')
        source_digest, output_digest = hashlib.sha256(), hashlib.sha256()
        special = {'nan': 0, 'positive_inf': 0, 'negative_inf': 0}
        for i in range(count):
            source = np.asarray(column['load'](i))
            value = selected[i][key]
            actual = np.asarray(value, dtype='uint8' if column['dtype'] == 'image' else column['dtype']).reshape(column['source_shape'])
            if source.dtype != actual.dtype or source.shape != actual.shape or not np.array_equal(source, actual, equal_nan=True):
                raise ConversionError(f'{key}: readback dtype/shape/value mismatch at frame {i}')
            if source.dtype.kind == 'f':
                for label, test in [('nan', np.isnan), ('positive_inf', np.isposinf), ('negative_inf', np.isneginf)]:
                    if not np.array_equal(test(source), test(actual)):
                        raise ConversionError(f'{key}: {label} positions differ at frame {i}')
                    special[label] += int(test(source).sum())
            source_digest.update(source.tobytes())
            output_digest.update(actual.tobytes())
            if column.get('encoded'):
                append_record(encoded_ledger, {'frame_index': i, 'source_field': column['inventory']['field'],
                    'source_file': column['inventory']['source_file'], 'source_encoded_sha256': hashlib.sha256(column['encoded'](i)).hexdigest(),
                    'decoded_pixels_sha256': hashlib.sha256(source.tobytes()).hexdigest()})
        checks.append({'feature': key, 'check': 'every_element_dtype_shape_nonfinite_and_pixels', 'frames': count,
                       'equal': True, 'source_sha256': source_digest.hexdigest(), 'readback_sha256': output_digest.hexdigest(),
                       'nonfinite': special})
    return checks


def convert_to_lerobot(source, *, profile, output, fps=None, repo_id='local/dqs-converted'):
    """Convert HDF5 or v2.1 to a new v3 directory, raising on any fidelity failure.

    The output includes catalog.json and _runs evidence. A failed partial output
    remains diagnostic only; acceptance.status is failed and must not be reused.
    """
    from framework.ir.dataset import open_dataset
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if output.exists():
        raise FileExistsError('Conversion requires a completely new output directory')
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Conversion output must not overlap the source')
    configuration = load_profile(profile)
    official_class()  # Lazy optional dependency, checked before creating output.
    source_files = [source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file())
    hashes = {str(p): sha256(p) for p in source_files}
    source_dataset = open_dataset(source, profile=configuration, backend='compat')
    episodes = list(source_dataset.iter_episodes())
    if any(ep.format not in {'hdf5', 'lerobot'} for ep in episodes):
        raise ConversionError('First-round conversion supports HDF5 and LeRobot v2.1 only')
    if fps is None:
        rates = {ep.selector.get('info', {}).get('fps') for ep in episodes}
        fps = rates.pop() if len(rates) == 1 else None
    if type(fps) is not int or fps <= 0:
        raise ConversionError('Explicit positive integer nominal index fps required; do not infer it from source times')
    if any(ep.format == 'lerobot' and ep.selector['info']['fps'] != fps for ep in episodes):
        raise ConversionError('Existing LeRobot FPS cannot change without explicit resampling; conversion does not resample')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:10]
    # Official metadata creator owns creation of the fresh output root.
    meta = None
    evidence = output / '_runs' / run_id
    result = {'schema_version': 'dqs_conversion_v1', 'run_id': run_id,
              'conversion_status': 'running', 'fidelity_status': 'not_verified',
              'detection_status': 'not_run', 'quality_status': 'not_assessed', 'output': str(output),
              'official_version': OFFICIAL_VERSION, 'format_version': 'v3.0', 'nominal_index_fps': fps,
              'index_time_source': 'synthetic', 'source': str(source), 'episodes': []}
    try:
        global_offset = 0
        final_mapping = None
        final_profile = copy.deepcopy(configuration)
        for index, episode in enumerate(episodes):
            scratch = output.parent / ('.'+output.name+'-'+run_id+'-'+str(index))
            scratch.mkdir(parents=True, exist_ok=False)
            generator = None
            try:
                if episode.format == 'hdf5':
                    generator = _hdf_sources(episode, configuration, scratch)
                    count, columns, mapping, inventory, source_metadata, time_key = next(generator)
                else:
                    count, columns, mapping, inventory, source_metadata, time_key = _v2_sources(episode, configuration, scratch)
                features = {k: {a: v for a, v in col.items() if a in {'dtype', 'shape', 'names', 'info'}} for k, col in columns.items()}
                if meta is None:
                    from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata
                    meta = LeRobotDatasetMetadata.create(repo_id=repo_id, fps=fps, features=features,
                        root=output, robot_type=configuration.get('robot_type'), use_videos=any(f['dtype']=='video' for f in features.values()))
                    evidence.mkdir(parents=True)
                    final_mapping = mapping
                    result['source_hashes'] = hashes
                    write_json(evidence / 'run.json', {'source': str(source), 'profile': configuration,
                        'fps': fps, 'repo_id': repo_id, 'official_version': OFFICIAL_VERSION,
                        'dependencies': _dependency_versions(),
                        'statistics': 'omitted; no cleaning or finite-only replacement is performed'})
                    write_json(output / 'acceptance.json', result)
                elif features != {k: v for k, v in meta.features.items() if k in features} or mapping != final_mapping:
                    raise ConversionError('Episodes have incompatible feature schemas or source mappings')
                episode_evidence = evidence / f'episode-{index:06d}'
                shutil.move(str(scratch), str(episode_evidence))
                tasks = source_metadata.get('tasks') or ['']
                meta.save_episode_tasks(tasks)
                if episode.format == 'lerobot':
                    old_tasks = [json.loads(line) for line in (source / 'meta/tasks.jsonl').read_text().splitlines() if line]
                    old_task_lookup = {r['task_index']: r['task'] for r in old_tasks}
                    old_task_column = columns['source.task_index']
                    task_indices = []
                    for i in range(count):
                        task = old_task_lookup[int(old_task_column['load'](i))]
                        meta.save_episode_tasks([task])
                        task_indices.append(meta.get_task_index(task))
                else:
                    task_indices = [meta.get_task_index(tasks[0])]*count
                ep_meta = _write_episode(meta, columns, count, index, output, global_offset, task_indices, fps)
                meta.save_episode(index, count, tasks, {}, ep_meta)
                # Preserve source identities/annotations separately from new standard indices.
                item = {'episode_index': index, 'id': episode.id, 'source': str(episode.path),
                        'selector': episode.selector, 'source_metadata': source_metadata, 'frames': count,
                        'row_mapping': {'kind': 'identity', 'source_start': 0, 'output_start': global_offset, 'length': count},
                        'inventory': inventory}
                write_json(episode_evidence / 'source-mapping.json', item)
                result['episodes'].append(item)
                global_offset += count
                raw_time_feature = mapping[time_key]['feature'] if time_key in mapping else time_key
                final_profile['lerobot'] = {**configuration.get('lerobot', {}), 'feature_mapping': mapping,
                    'official_version': OFFICIAL_VERSION, 'index_time_source': 'synthetic',
                    'original_time': {'feature': raw_time_feature, 'unit': configuration.get('timestamp_unit', 'unknown'),
                                      'time_source': configuration.get('lerobot', {}).get('original_time', {}).get('time_source', 'unknown'),
                                      'clock': configuration.get('lerobot', {}).get('original_time', {}).get('clock')},
                    'provenance': {'run_id': run_id, 'source_mapping': str((episode_evidence / 'source-mapping.json').relative_to(output))}}
            finally:
                if generator is not None:
                    generator.close()
                if scratch.exists():
                    # Diagnostics are retained even if scanning failed before meta creation.
                    failed_evidence = output / '_runs' / run_id / f'episode-{index:06d}'
                    failed_evidence.parent.mkdir(parents=True, exist_ok=True)
                    if not failed_evidence.exists():
                        shutil.move(str(scratch), str(failed_evidence))
        meta.finalize()
        for index, episode in enumerate(episodes):
            episode_evidence = evidence / f'episode-{index:06d}'
            generator = None
            try:
                if episode.format == 'hdf5':
                    generator = _hdf_sources(episode, configuration, episode_evidence)
                    count, columns, *_ = next(generator)
                else:
                    count, columns, *_ = _v2_sources(episode, configuration, episode_evidence)
                result['episodes'][index]['checks'] = _verify_episode(output, index, columns, count, episode_evidence)
            finally:
                if generator is not None:
                    generator.close()
        for name, binding in final_mapping.items():
            spec = _spec(configuration, name)
            if not spec:
                feature = meta.features[binding['feature']]
                spec = {'modality': 'rgb' if feature['dtype'] in {'image', 'video'} else 'other'}
            if 'field' in binding:
                spec.setdefault('fields', [binding['field']])
            final_profile['channels'] = {name: spec, **{k: v for k, v in final_profile.get('channels', {}).items() if k != name}}
        (output / 'robot.yaml').write_text(yaml.safe_dump(final_profile, allow_unicode=True, sort_keys=False))
        write_json(output / 'dqs-source-mapping.json', {'schema_version': 'dqs_source_mapping_v1', 'episodes': result['episodes']})
        # Validate the detection wrapper, then the official default torch DataLoader.
        with open_dataset(output, backend='official') as wrapped:
            restored = list(wrapped.iter_episodes())
            if [ep.id for ep in restored] != [ep.id for ep in episodes]:
                raise ConversionError('Stable episode IDs changed during wrapping')
            for ep in restored:
                with ep.opened():
                    if not ep.channels:
                        raise ConversionError('Detection wrapper recovered no channels')
        from torch.utils.data import DataLoader, Subset
        training = local_official(output)
        probe_indices = sorted({item['row_mapping']['output_start'] + local
                                for item in result['episodes']
                                for local in (0, item['frames']//2, item['frames']-1)})
        batches = 0
        for batch in DataLoader(Subset(training, probe_indices), batch_size=1, num_workers=0):
            batches += 1
        if batches != len(probe_indices):
            raise ConversionError('DataLoader frame count mismatch')
        current_files = [source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file())
        if {str(p) for p in current_files} != set(hashes):
            raise ConversionError('Source file inventory changed during conversion')
        for path, before in hashes.items():
            if sha256(path) != before:
                raise ConversionError(f'Source changed during conversion: {path}')
        result.update(conversion_status='completed', fidelity_status='passed',
                      training_status='LeRobotDataset_and_DataLoader_passed', training_frames=batches,
                      training_probe_indices=probe_indices, training_scope='first_middle_last_each_episode',
                      source_unchanged=True, total_frames=global_offset)
    except Exception as exc:
        result.update(conversion_status='failed', fidelity_status='failed', error=f'{type(exc).__name__}: {exc}')
        if meta is not None:
            meta.finalize()
        evidence.mkdir(parents=True, exist_ok=True)
        write_json(evidence / 'errors.json', {'error': result['error']})
        raise
    finally:
        source_dataset.__exit__(None, None, None)
        evidence.mkdir(parents=True, exist_ok=True)
        write_json(evidence / 'consistency.json', result)
        record_path = evidence / 'records.jsonl'
        append_record(record_path, {'schema_version': 'dqs_record_v1',
            'algorithm': {'id': 'dqs__convert_to_lerobot', 'version': '1'},
            'object': {'type': 'dataset', 'id': source.name, 'source': str(source)},
            'status': 'completed' if result['conversion_status'] == 'completed' else 'processing_error',
            'reason': result.get('error', ''),
            'result': {'output': str(output), 'params': {'fps': fps, 'repo_id': repo_id},
                       'source_mapping': 'dqs-source-mapping.json',
                       'fidelity_status': result['fidelity_status'], 'quality_status': 'not_assessed'},
            'artifacts': [str((evidence / 'consistency.json').relative_to(output))]})
        write_json(output / 'acceptance.json', result)
        write_json(output / 'catalog.json', {'schema_version': 'dqs_catalog_v1', 'runs': [],
                    'conversions': [{'run_id': run_id, 'acceptance': 'acceptance.json',
                                     'report': str((evidence / 'consistency.json').relative_to(output)),
                                     'records': [str(record_path.relative_to(output))],
                                     'status': result['conversion_status']}]})
    return result


def _dependency_versions():
    from importlib.metadata import version, PackageNotFoundError
    result = {}
    for name in ('embodied-dqs', 'lerobot', 'datasets', 'torch', 'torchvision', 'numpy', 'av', 'pyarrow', 'Pillow', 'h5py'):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = 'not-installed'
    return result
