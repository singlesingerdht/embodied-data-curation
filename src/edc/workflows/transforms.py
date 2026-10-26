"""Validate identity-row derived datasets before publishing them to downstream stages."""
from pathlib import Path
import numpy as np
from edc.data.ir.dataset import Dataset, open_dataset
from .references import dataset_reference, open_dataset_reference


def validate_transform(record, parents, output_root):
    output_root = Path(output_root).resolve()
    if record['status'] != 'completed':
        raise ValueError('Transform did not complete')
    parent_index = {ep.id: ep for ep in parents.iter_episodes()}
    outputs = []
    profile = None
    parent_ref = dataset_reference(parents)
    for report in record['result']['reports']:
        if not all(k in report for k in ('output', 'params', 'source_mapping')):
            raise ValueError('Transform requires output, params and source_mapping')
        target = report['output']
        location = Path(target['path'] if isinstance(target, dict) else target).resolve()
        # Plugins only publish datasets from their own artifact directory.
        run_path = output_root / '_runs' / record['_run_id'] / record['algorithm']['id'] / 'artifacts'
        if not location.is_relative_to(run_path.resolve()):
            raise ValueError('Transform output must remain inside its algorithm artifacts')
        derived = open_dataset_reference(target) if isinstance(target, dict) else open_dataset(location)
        for item in dataset_reference(derived)['files']:
            if not Path(item['path']).is_relative_to(location):
                raise ValueError('Derived files must remain inside published output')
        mapping = report['source_mapping']
        if not isinstance(mapping, dict) or mapping.get('parent_fingerprint') != parent_ref['fingerprint']:
            raise ValueError('Transform must declare the exact parent dataset fingerprint')
        rows = mapping.get('episodes')
        if not isinstance(rows, list):
            raise ValueError('Transform needs an explicit episode source mapping')
        index = {row['output_episode_id']: row for row in rows}
        children = list(derived.iter_episodes())
        if len(index) != len(rows) or set(index) != {ep.id for ep in children}:
            raise ValueError('Output episode mapping missing or duplicated')
        if (derived.id, derived.version) == (parents.id, parents.version):
            raise ValueError('Transform must create a new dataset identity or version')
        if profile is not None and profile != derived.profile:
            raise ValueError('Transform output profiles disagree')
        profile = derived.profile
        try:
            for child in children:
                if not child.path.resolve().is_relative_to(location):
                    raise ValueError('Derived episodes must remain inside published output')
                row = index[child.id]
                parent_id = row.get('parent_episode_id')
                if parent_id not in parent_index or child.id in parent_index:
                    raise ValueError('Derived episode needs a new ID and an existing parent')
                if record['object']['type'] == 'episode' and parent_id != record['object']['id']:
                    raise ValueError('Episode transform cannot publish another episode')
                if row.get('rows') != 'identity':
                    raise ValueError('v1 examples support identity row mapping only; temporal edits need another contract')
                changed = row.get('changed_channels', [])
                if not isinstance(changed, list):
                    raise ValueError('changed_channels must be explicit')
                parent = parent_index[parent_id]
                with parent.opened(), child.opened():
                    if set(parent.channels) != set(child.channels) or not set(changed).issubset(parent.channels):
                        raise ValueError('Transform lost or invented channels')
                    for name, old in parent.channels.items():
                        new = child.channels[name]
                        if any(getattr(old, key) != getattr(new, key) for key in (
                                'modality', 'fields', 'count', 'unit', 'dimensions', 'time_source', 'clock',
                                'source_kind', 'coordinate_frame', 'side', 'field_metadata', 'representation')):
                            raise ValueError(f'{name}: identity-row transform changed undeclared semantics or shape')
                        if old.timestamps != new.timestamps:
                            raise ValueError(f'{name}: identity mapping changed timestamps')
                        if old.value_available:
                            for field in old.fields:
                                a, b = parent.read_signal(name, field), child.read_signal(name, field)
                                if a.dtype != b.dtype or a.shape != b.shape:
                                    raise ValueError(f'{name}.{field}: transform changed dtype or shape')
                                if name not in changed and not np.array_equal(a, b, equal_nan=True):
                                    raise ValueError(f'{name}.{field}: undeclared numeric change')
                        if old.image_available:
                            from itertools import zip_longest
                            for a, b in zip_longest(parent.iter_frames(name), child.iter_frames(name)):
                                if a is None or b is None:
                                    raise ValueError(f'{name}: frame count changed')
                                # The IR frame dictionary preserves index/time and decoded pixels.
                                pa = a['image']
                                pb = b['image']
                                if pa.shape != pb.shape or pa.dtype != pb.dtype:
                                    raise ValueError(f'{name}: frame shape/dtype changed')
                                if name not in changed and not np.array_equal(pa, pb):
                                    raise ValueError(f'{name}: undeclared pixel change')
                    child.metadata['curation_parent'] = {'dataset_fingerprint': parent_ref['fingerprint'],
                        'episode_id': parent_id, 'selector': parent.selector, 'rows': 'identity',
                        'changed_channels': changed}
                outputs.append(child)
        finally:
            derived.__exit__(None, None, None)
    ids = [ep.id for ep in outputs]
    if len(ids) != len(set(ids)):
        raise ValueError('Transform output IDs collide')
    return Dataset(output_root / '_runs' / record['_run_id'], profile or parents.profile,
                   {'id': parents.id + '__derived', 'version': record['_run_id'],
                    'tasks': parents.tasks, 'metadata': {'parent': parent_ref['fingerprint']}}, outputs)
