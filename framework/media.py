"""Run-local materialization shared by adapters, without altering model inputs."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

from framework.results import clean, write_json


class MediaCache:
    """No cross-run reuse: source/config changes never inherit a previous run."""

    def __init__(self, root):
        self.root = Path(root)
        self.entries = {}

    def obtain(self, episode, channel):
        c = episode.channels[channel]
        files = {Path(episode.path), Path(c.source)}
        source = getattr(episode, '_source', episode)
        encoded = getattr(getattr(source, '_reader', None), 'video_sources', {}).get(channel)
        if encoded:
            files.add(Path(encoded[0]))
        stats = [{'path': str(p.resolve()), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
                 for p in sorted(files) if p.exists()]
        contract = clean({'episode_id': episode.id, 'source': str(episode.path),
            'selector': getattr(episode, 'selector', {}), 'channel': asdict(c),
            'profile': episode.profile, 'sources': stats,
            'encoding_policy': 'reader_materialization_v1_libx264_yuv420p'})
        key = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row = self.entries.get(key)
        if row and Path(row['video']).is_file():
            return row['video'], key, True
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f'{key}.mp4'
        path = episode.materialize_video(channel, destination)
        if not Path(path).is_file():
            raise ValueError('Materialization did not produce a video')
        self.entries[key] = {'video': str(path), 'contract': contract,
            'evidence': episode.video_evidence(channel, inspection_input=path)}
        write_json(self.root / 'manifest.json', {'schema_version': 'dqs_media_cache_v1',
            'reuse_scope': 'single_run', 'entries': self.entries})
        return str(path), key, False


def materialize(context, channel, local_name):
    """Retain legacy fixed filenames where an upstream script requires them."""
    target = (context.workdir / local_name).resolve()
    if not target.is_relative_to(context.workdir.resolve()):
        raise ValueError('Media artifact must remain inside workdir')
    if context.media_cache is None:
        return context.episode.materialize_video(channel, target)
    path, key, reused = context.media_cache.obtain(context.episode, channel)
    # Keep algorithm workdirs independently usable. Copying an existing encoded
    # file introduces no further encoding and preserves bytes and sidecars.
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    for suffix in ('.frames.json', '.evidence.json', '.timeline.json'):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_file():
            shutil.copyfile(sidecar, Path(str(target) + suffix))
    ref = context.workdir / 'media-inputs.json'
    rows = json.loads(ref.read_text()) if ref.exists() else []
    rows.append({'channel': channel, 'cache_key': key, 'reused': reused,
        'shared_video': path, 'local_video': str(target),
        'inspection': context.media_cache.entries[key]['evidence']})
    context.write_result('media-inputs.json', rows)
    return str(target)


def video_evidence(context, channel, inspection_input):
    """A byte copy preserves the original/derived classification of its input."""
    import copy
    ref = context.workdir / 'media-inputs.json'
    for row in json.loads(ref.read_text()) if ref.is_file() else []:
        if row['channel'] == channel and Path(row['local_video']).resolve() == Path(inspection_input).resolve():
            evidence = copy.deepcopy(row['inspection'])
            evidence.setdefault('source_evidence', {})['inspection_input'] = str(inspection_input)
            evidence['materialization_cache'] = {'key': row['cache_key'], 'reused': row['reused'],
                'scope': 'single_run', 'byte_copy': True}
            return evidence
    return context.episode.video_evidence(channel, inspection_input=inspection_input)
