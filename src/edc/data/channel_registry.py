"""Versioned logical vocabulary; declaring a name never installs a decoder."""
import copy
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
import re

import yaml

from edc.data.ir.modalities import normalize_modality


SCHEMA = 'edc_channel_registry_v1'
NAME_PATTERN = re.compile(r'[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)+(?:@[a-z][a-z0-9_]*)?')
_SEMANTICS = {'modality', 'side', 'source_kind', 'camera_role', 'device_type', 'body_part'}


class _UniqueLoader(yaml.SafeLoader):
    """A duplicate declaration is an error, never last-key-wins."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, (str, int, float, bool, type(None))):
            raise ValueError('Configuration mapping keys must be scalars')
        if key in result:
            raise ValueError(f'Duplicate configuration key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_unique_yaml(text):
    return yaml.load(text, Loader=_UniqueLoader)


def _validate(document):
    if (not isinstance(document, dict) or document.get('schema_version') != SCHEMA
            or set(document) - {'schema_version', 'version', 'base_version', 'channels'}):
        raise ValueError(f'Channel registry requires {SCHEMA}')
    if not isinstance(document.get('version'), str) or not document['version'].strip():
        raise ValueError('Channel registry version must be a nonempty string')
    if not isinstance(document.get('channels'), dict) or not document['channels']:
        raise ValueError('Channel registry channels must be a nonempty mapping')
    for name, spec in document['channels'].items():
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or '@' in name:
            raise ValueError(f'Invalid registry logical name: {name!r}; register the base name')
        if not isinstance(spec, dict) or set(spec) - _SEMANTICS or 'modality' not in spec:
            raise ValueError(f'{name}: registry entries require modality and naming semantics only')
        if any(not isinstance(v, str) or not v for v in spec.values()):
            raise ValueError(f'{name}: registry semantics must be nonempty strings')
        if normalize_modality(spec['modality']) != spec['modality']:
            raise ValueError(f'{name}: use a canonical modality in the registry')
        if 'side' in spec and spec['side'] not in {'left', 'right', 'main', 'both'}:
            raise ValueError(f'{name}: invalid registry side')
        if 'source_kind' in spec and spec['source_kind'] not in {'feedback', 'command', 'derived'}:
            raise ValueError(f'{name}: invalid registry source_kind')
    return document


@lru_cache(maxsize=1)
def _standard_registry():
    return _validate(read_unique_yaml(files('edc.data').joinpath('channel_registry.yaml').read_text(encoding='utf-8')))


def _read(value, base, stack=()):
    if isinstance(value, (str, Path)):
        path = (base / value).resolve()
        if path in stack:
            raise ValueError('Cyclic channel registry reference')
        return _read(read_unique_yaml(path.read_text(encoding='utf-8')), path.parent, (*stack, path))
    if isinstance(value, dict) and 'ref' in value:
        if set(value) != {'ref'} or not isinstance(value['ref'], str) or not value['ref']:
            raise ValueError('A channel registry ref accepts exactly one file reference')
        return _read(value['ref'], base, stack)
    return _validate(copy.deepcopy(value))


def load_channel_registry(value=None, *, base=None):
    """Return an isolated effective catalog from YAML/JSON, a mapping or defaults.

    Extensions use team__ names. Standard entries may be repeated unchanged for
    self-contained snapshots, but cannot be redefined. Physical field metadata
    stays in robot/capture configuration; no metadata is inferred from names.
    """
    standard = _standard_registry()
    if value is None:
        return {**copy.deepcopy(standard), 'base_version': standard['version']}
    document = _read(value, Path(base) if base is not None else Path.cwd())
    if document.get('base_version', standard['version']) != standard['version']:
        raise ValueError('Channel registry base_version differs from the installed framework; review migration')
    merged = copy.deepcopy(standard['channels'])
    for name, spec in document['channels'].items():
        if name in merged:
            if spec != merged[name]:
                raise ValueError(f'Cannot redefine standard logical channel: {name}')
        elif '__' not in name.split('/')[0]:
            raise ValueError(f'{name}: registry extensions require a team__ namespace')
        merged[name] = copy.deepcopy(spec)
    return {'schema_version': SCHEMA, 'version': document['version'],
            'base_version': standard['version'], 'channels': merged}
