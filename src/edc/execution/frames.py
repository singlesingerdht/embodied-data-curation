"""Native RGB frames and auditable multi-view pairing, independent of encoding rate."""
from pathlib import Path
import hashlib
import numpy as np

from edc.algorithms.channel_names import resolve_channel
from edc.reporting.results import write_json


def _axis(values, count):
    if values is None:
        return None
    a = np.asarray(values, dtype=float)
    if a.shape != (count,) or not np.isfinite(a).all() or np.any(np.diff(a) <= 0):
        return None
    return a.tolist()


def _row_identity(channel):
    e = channel.time_evidence
    return (e.get('timeline_scope') == 'shared_dataset_rows'
            and str(e.get('source_row_mapping', '')).startswith('identity'))


def _plain(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def inspect_frames(episode, bindings, *, reference='cam_high', upstream=None):
    """Inspect original frames without materializing video or asserting synchronization.

    Decoder source_time may be a presentation timestamp. Only declared channel
    evidence binds a recorded timeline to decoded video frames.
    """
    episode.open()
    channels = {role: resolve_channel(episode, name) for role, name in bindings.items()}
    if reference not in channels:
        raise ValueError('Reference view is not bound')
    views = {}
    for role, name in channels.items():
        c = episode.channels[name]
        if c.modality != 'rgb' or not c.image_available:
            raise ValueError(f'{name}: RGB images unavailable')
        storage = episode.image_storage(name)
        frames = []
        for f in episode.iter_frames(name):
            if f['index'] != len(frames):
                raise ValueError(f'{name}: non-contiguous decoded frame identity')
            frames.append({'index': int(f['index']), 'source_index': int(f.get('source_index', f['index'])),
                           'playback_time': f.get('playback_time'), 'container_time': f.get('container_time'),
                           'decoder_source_time': f.get('source_time'),
                           'decoder_time_source': f.get('time_source', 'unspecified')})
        if len(frames) < (2 if role == reference else 1):
            raise ValueError(f'{name}: insufficient decodable images')
        source = _axis(c.timestamps, len(frames)) if c.time_source in {'acquisition', 'container'} else None
        bound = (storage == 'frames' or episode.format in {'mcap', 'ros1_bag', 'ros2_bag'}
                 or _row_identity(c) or bool(c.time_evidence.get('video_frame_mapping'))
                 or bool(getattr(c, 'frame_evidence', {}).get('source_mapping')))
        if not bound:
            source = None
        playback = _axis([f['playback_time'] for f in frames], len(frames)) if storage == 'video' else None
        if playback is not None:
            playback = (np.asarray(playback) - playback[0]).tolist()
        views[role] = {'channel': name, 'source': c.source, 'storage': storage, 'count': len(frames),
                       'time_source': c.time_source, 'clock': c.clock, 'source_timestamps': source,
                       'declared_timestamps': _plain(c.timestamps.tolist() if isinstance(c.timestamps,np.ndarray) else c.timestamps),
                       'source_time_available': source is not None,
                       'playback_timestamps': playback, 'frames': frames,
                       'time_evidence': c.time_evidence, 'source_mapping': c.source_mapping}
    ref = views[reference]; rc = episode.channels[ref['channel']]
    count = ref['count']
    for role, row in views.items():
        c = episode.channels[row['channel']]
        mapping = None
        mapped = row['source_timestamps']
        same_rows = _row_identity(rc) and _row_identity(c)
        if role == reference or row['channel'] == ref['channel'] or same_rows:
            by_row = {frame['source_index']: frame['index'] for frame in row['frames']}
            if len(by_row) != row['count']:
                raise ValueError(f"{row['channel']}: ambiguous declared row identities")
            indices = [by_row.get(frame['source_index']) for frame in ref['frames']]
            offsets = [None] * count
            if c.clock == rc.clock and c.clock and mapped is not None and ref['source_timestamps'] is not None:
                offsets = [float(mapped[j]-ref['source_timestamps'][i]) if j is not None else None
                           for i,j in enumerate(indices)]
            basis = ('reference_identity' if role == reference else 'same_channel_identity'
                     if row['channel']==ref['channel'] else 'declared_shared_rows')
        else:
            if mapped is not None and rc.clock and c.clock:
                if c.clock != rc.clock:
                    try:
                        transformed = episode.transform_timestamps(row['channel'], rc.clock)
                        mapped = _axis(transformed.output, row['count'])
                        mapping = transformed.source_mapping
                    except ValueError:
                        mapped = None
                if mapped is not None and ref['source_timestamps'] is not None:
                    t = np.asarray(mapped); indices = []; offsets = []
                    for x in ref['source_timestamps']:
                        if x < t[0] or x > t[-1]:
                            indices.append(None); offsets.append(None); continue
                        hi = int(np.searchsorted(t, x)); lo = max(0, hi-1)
                        j = lo if x-t[lo] <= t[hi]-x else hi
                        indices.append(j); offsets.append(float(t[j]-x))
                    basis = 'nearest_recorded_time'
                else:
                    indices = [None]*count; offsets = [None]*count; basis = 'unavailable'
            else:
                indices = [None]*count; offsets = [None]*count; basis = 'unavailable'
        row.update(reference_indices=indices, pairing_offsets_sec=offsets, pairing_basis=basis,
                   mapped_timestamps=mapped, clock_mapping=mapping, reference_clock=rc.clock,
                   missing_reference_indices=[i for i,j in enumerate(indices) if j is None])
    quality_references = []
    for stage, evidence in (upstream or {}).items():
        decision = evidence.get('quality', {}).get(episode.id, {})
        dataset = evidence.get('dataset', {})
        identities = dataset.get('episodes', [])
        matches = any(row.get('id') == episode.id and row.get('selector', {}) == _plain(episode.selector)
                      and Path(row.get('source', '')).resolve() == Path(episode.path).resolve() for row in identities)
        if decision.get('decision') == 'keep' and dataset.get('fingerprint') and matches:
            quality_references.append({'stage': stage, 'run_id': evidence.get('run_id'),
                'dataset_fingerprint': dataset['fingerprint'], 'decision': _plain(decision)})
    complete = all(not v['missing_reference_indices'] for v in views.values())
    return {'schema': 'edc_frame_input_v1', 'episode_id': episode.id, 'source': str(episode.path),
            'selector': episode.selector, 'reference': reference, 'frame_count': count, 'views': views,
            'coverage_complete': complete, 'upstream': _plain(upstream or {}),
            'quality_evidence': 'upstream_reference' if quality_references else 'not_supplied',
            'quality_references': quality_references,
            'proves_acquisition_sync': False}


def cache_frames(episode, manifest, directory, indices):
    """Cache only requested original RGB images as lossless PNGs."""
    from PIL import Image
    directory = Path(directory).resolve()
    source = Path(episode.path).resolve()
    if directory == source or directory.is_relative_to(source):
        raise ValueError('Frame cache must not be written inside the source episode')
    requested = sorted(set(map(int, indices)))
    if any(i < 0 or i >= manifest['frame_count'] for i in requested):
        raise ValueError('Requested frame is outside the episode')
    directory.mkdir(parents=True, exist_ok=True)
    for role, row in manifest['views'].items():
        selected = {row['reference_indices'][i] for i in requested} - {None}
        cache = row.setdefault('cached_images', {})
        selected -= {int(i) for i in cache}
        if not selected:
            continue
        folder = directory / role
        folder.mkdir(exist_ok=True)
        for frame in episode.iter_frames(row['channel']):
            index = frame['index']
            if index not in selected:
                continue
            pixels = np.asarray(frame['image'])
            if pixels.dtype.kind == 'f' and np.isfinite(pixels).all() and pixels.min() >= 0 and pixels.max() <= 1:
                pixels = np.rint(pixels*255).astype(np.uint8)
            if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
                raise ValueError(f'{role}/{index}: expected RGB uint8 or normalized RGB floats')
            path = folder / f'frame_{index:06d}.png'
            Image.fromarray(pixels).save(path)
            cache[str(index)] = {'path': str(path), 'rgb_sha256': hashlib.sha256(pixels.tobytes()).hexdigest()}
        if selected - {int(i) for i in cache}:
            raise ValueError(f'{role}: requested decoded frames are missing')
    write_json(directory.parent/'input-mapping.json', manifest)
    return manifest


class PreparedFrames:
    def __init__(self, manifest):
        self.manifest = manifest

    def image_path(self, role, reference_index):
        row = self.manifest['views'][role]
        if not 0 <= reference_index < self.manifest['frame_count']:
            raise ValueError('Frame request outside reference sequence')
        source_index = row['reference_indices'][reference_index]
        if source_index is None:
            raise ValueError(f'{role}: no image paired to reference frame {reference_index}')
        return row['cached_images'][str(source_index)]['path']


def render_videos(manifest, output, source_times, *, fps=30):
    """Derived presentation videos only; map every output frame to its source."""
    from PIL import Image
    from edc.data.readers.video import encode_frames
    source = np.asarray(source_times, float)
    if (source.shape != (manifest['frame_count'],) or len(source)<2 or not np.isfinite(source).all()
            or np.any(np.diff(source) <= 0) or not np.isfinite(fps) or fps <= 0):
        raise ValueError('Video presentation requires a valid recorded timeline')
    relative = source-source[0]
    times = np.arange(int(np.floor(relative[-1]*fps))+1)/fps
    hi = np.searchsorted(relative,times); lo = np.maximum(0,hi-1)
    indices = np.where(times-relative[lo] <= relative[hi]-times,lo,hi).tolist()
    prepared = PreparedFrames(manifest); output = Path(output); output.mkdir(parents=True,exist_ok=True)
    rows = {}
    for role in manifest['views']:
        def frames():
            for i, reference_index in enumerate(indices):
                with Image.open(prepared.image_path(role, reference_index)) as image:
                    pixels = np.asarray(image.convert('RGB'))
                yield {'index':i,'source_index':reference_index,'source_time':float(source[reference_index]),'image':pixels}
        path = encode_frames(frames(),output/f'{role}.mp4',fps=fps,expected_count=len(indices))
        rows[role] = {'path':path,'output_reference_indices':indices}
    mapping = {'fps':fps,'presentation_times':times.tolist(),'reference_indices':indices,
               'source_relative_times':relative.tolist(),'duration_sec':float(relative[-1]),
               'encoded_duration_sec':len(indices)/fps,'time_source':'derived_presentation',
               'proves_acquisition_sync':False,'views':rows}
    write_json(output/'video-mapping.json',mapping)
    return mapping
