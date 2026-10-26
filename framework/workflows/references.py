"""Dataset references are metadata over existing storage, not a data format."""
import copy
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from framework.ir.dataset import Dataset, open_dataset


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, default=str).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class DatasetView(Dataset):
    def __init__(self, source, ids):
        requested = list(ids)
        episodes = {e.id: e for e in source.iter_episodes()}
        if len(requested) != len(set(requested)) or any(i not in episodes for i in requested):
            raise ValueError('Subset must contain unique existing episode IDs')
        super().__init__(source.path, copy.deepcopy(source.profile),
            {'id': source.id, 'version': source.version, 'tasks': source.tasks,
             'metadata': source.metadata}, [episodes[i] for i in requested])


def dataset_reference(dataset):
    dataset = getattr(dataset, '_source', dataset)
    files = {}
    rows = []
    for ep in dataset.iter_episodes():
        source = Path(ep.path).resolve()
        paths = [source] if source.is_file() else sorted(p for p in source.rglob('*') if p.is_file())
        if source.is_file():
            paths += [p for suffix in ('.frames.json', '.timeline.json')
                      if (p := Path(str(source) + suffix)).is_file()]
        inventory = []
        for path in paths:
            key = str(path.resolve())
            if key not in files:
                files[key] = {'path': key, 'size': path.stat().st_size, 'sha256': file_digest(path)}
            inventory.append(key)
        rows.append({'id': ep.id, 'source': str(source), 'format': ep.format,
                     'selector': copy.deepcopy(ep.selector), 'backend': ep.backend,
                     'metadata': copy.deepcopy(ep.metadata), 'files': inventory})
    for name in ('dataset.yaml', 'robot.yaml'):
        path = dataset.path / name
        if path.is_file() and str(path.resolve()) not in files:
            files[str(path.resolve())] = {'path': str(path.resolve()), 'size': path.stat().st_size, 'sha256': file_digest(path)}
    result = {'schema_version': 'dqs_dataset_ref_v1', 'id': dataset.id,
              'version': dataset.version, 'path': str(dataset.path.resolve()),
              'profile': copy.deepcopy(dataset.profile), 'tasks': copy.deepcopy(dataset.tasks),
              'metadata': copy.deepcopy(dataset.metadata), 'episodes': rows,
              'files': sorted(files.values(), key=lambda x: x['path'])}
    result['fingerprint'] = digest(result)
    return result


def open_dataset_reference(reference):
    if reference.get('schema_version') != 'dqs_dataset_ref_v1':
        raise ValueError('Unsupported dataset reference')
    expected = {k: v for k, v in reference.items() if k != 'fingerprint'}
    if digest(expected) != reference.get('fingerprint'):
        raise ValueError('Dataset reference metadata was modified')
    from framework.ir.dataset import Episode
    from framework.robots.profiles import load_profile
    profile = load_profile(reference['profile'])
    episodes = [Episode(e['id'], Path(e['source']), e['format'], profile,
                        copy.deepcopy(e['selector']), copy.deepcopy(e['metadata']), e['backend'])
                for e in reference['episodes']]
    view = Dataset(Path(reference['path']), profile, {'id': reference['id'],
        'version': reference['version'], 'tasks': reference.get('tasks', []),
        'metadata': reference.get('metadata', {})}, episodes)
    try:
        if dataset_reference(view) != reference:
            raise ValueError('Dataset reference is stale: source content, identity or selector changed')
        return view
    except Exception:
        view.__exit__(None, None, None)
        raise
