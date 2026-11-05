"""Logical channel names are references, never destructive source renames."""
import copy

from edc.data.channel_registry import NAME_PATTERN, load_channel_registry

# Compatibility export; validation always reads an isolated registry snapshot.


def _validate_name(name, registry=None):
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError(f'Invalid logical_name: {name!r}; use rgb/chest or rgb/chest@front')
    standards = registry['channels'] if registry is not None else load_channel_registry()['channels']
    base = name.split('@', 1)[0]
    if base not in standards and '__' not in base.split('/')[0]:
        raise ValueError(f'Unknown logical_name: {name}; custom names require a team__ namespace')
    return standards.get(base, {})


def _check_semantics(name, spec, source, registry=None):
    expected = _validate_name(name, registry)
    representation = spec.get('representation', {})
    actual = {**spec, 'camera_role': representation.get('camera_role'),
              'device_type': representation.get('device_type')}
    for field, metadata in spec.get('field_metadata', {}).items():
        role = metadata.get('source_kind')
        if expected.get('source_kind') and role not in (None, 'unknown', expected['source_kind']):
            raise ValueError(f'{source}.{field}: logical_name {name} conflicts with source_kind={role!r}')
    from edc.data.ir.modalities import normalize_modality
    if actual.get('modality'):
        actual['modality'] = normalize_modality(actual['modality'])
    for key, value in expected.items():
        declared = actual.get(key)
        if declared not in (None, '', 'unknown', value):
            raise ValueError(f'{source}: logical_name {name} conflicts with {key}={declared!r}')


def validate_logical_names(profile):
    """Validate explicit declarations without inferring missing physical metadata."""
    registry = load_channel_registry(profile.get('channel_registry'))
    seen = {}
    for source, spec in profile.get('channels', {}).items():
        name = spec.get('logical_name')
        if name is None:
            continue
        _check_semantics(name, spec, source, registry)
        if any(ch in source for ch in '*?['):
            raise ValueError(f'{source}: logical_name requires an exact channel binding, not a wildcard')
        if name in seen:
            raise ValueError(f'Duplicate logical_name {name}: {seen[name]}, {source}')
        seen[name] = source


def validate_channel_index(channels, registry=None):
    registry = load_channel_registry(registry)
    index = {}
    for source, channel in channels.items():
        name = getattr(channel, 'logical_name', None)
        if name is None:
            continue
        _check_semantics(name, {key: getattr(channel, key, None) for key in
            ('modality', 'body_part', 'side', 'source_kind', 'field_metadata', 'representation')}, source, registry)
        if name in index:
            raise ValueError(f'Ambiguous logical_name {name}: {index[name]}, {source}')
        if name in channels and name != source:
            raise ValueError(f'Logical name shadows a different source channel: {name}')
        index[name] = source
    return index


def resolve_channel(episode, reference):
    """Resolve one exact source or logical name; never guess or choose a fallback."""
    if not isinstance(reference, str) or not reference:
        raise ValueError('Channel reference must be a nonempty string')
    if not episode.channels and callable(getattr(episode, 'open', None)):
        episode.open()
    index = validate_channel_index(episode.channels, getattr(episode, 'profile', {}).get('channel_registry'))
    if reference in index:
        return index[reference]
    if reference in episode.channels:
        return reference
    raise ValueError(f'Channel is unavailable: {reference}; no fallback is allowed')


def channel_catalog(episode):
    """List loaded channels and their source/feature evidence, without reading payloads."""
    episode.open()
    validate_channel_index(episode.channels, getattr(episode, 'profile', {}).get('channel_registry'))
    storage = getattr(episode, 'image_storage', None)
    declared = getattr(episode, 'profile', {}).get('channels', {})
    logical = {c.logical_name for c in episode.channels.values() if c.logical_name}
    return {'schema_version': 'dqs_channel_catalog_v1', 'episode_id': episode.id,
        'channel_registry': load_channel_registry(getattr(episode, 'profile', {}).get('channel_registry')),
        'binding_coverage': {
            'source_channel_count': len(episode.channels),
            'logical_channel_count': len(logical),
            'without_logical_name': [c.name for c in episode.channels.values() if not c.logical_name],
            'missing_declared_logical_names': [spec['logical_name'] for spec in declared.values()
                if spec.get('logical_name') and spec['logical_name'] not in logical]},
        'channels': [{'logical_name': getattr(c, 'logical_name', None), 'channel': c.name,
            'modality': c.modality, 'fields': list(c.fields), 'sensor_id': c.sensor_id,
            'robot_part': c.robot_part,
            'part_definition': copy.deepcopy(getattr(episode, 'profile', {}).get('parts', {}).get(c.robot_part)),
            'value_available': c.value_available, 'image_available': c.image_available,
            'side': c.side, 'source_kind': c.source_kind, 'unit': c.unit,
            'field_metadata': copy.deepcopy(c.field_metadata),
            'frame_evidence': copy.deepcopy(c.frame_evidence),
            'representation': copy.deepcopy(c.representation),
            'image_storage': (storage(c.name) if callable(storage) else 'unknown') if c.image_available else None,
            'time_source': c.time_source, 'clock': c.clock, 'count': c.count,
            'source': c.source, 'source_mapping': copy.deepcopy(c.source_mapping)}
            for c in episode.channels.values()]}


def resolve_channel_parameters(episode, params):
    """Shared adapter hook for explicitly declared channel-reference parameters."""
    result = copy.deepcopy(params)
    if result.get('channels') is not None:
        names = result['channels']
        if not isinstance(names, list) or not names:
            raise ValueError('channels must be a nonempty list')
        result['channels'] = [resolve_channel(episode, n) for n in names]
        if len(set(result['channels'])) != len(names):
            raise ValueError('Duplicate channel selection after resolving logical names')
    if result.get('required_channels') is not None:
        names = result['required_channels']
        if not isinstance(names, list):
            raise ValueError('required_channels must be a list')
        result['required_channels'] = [resolve_channel(episode, n) for n in names]
        if len(set(result['required_channels'])) != len(names):
            raise ValueError('Duplicate required channel selection after resolving logical names')
    for key in ('channel', 'primary', 'camera_channel'):
        if result.get(key) is not None:
            result[key] = resolve_channel(episode, result[key])
    poses = result.get('poses') or {}
    if not isinstance(poses, dict):
        raise ValueError('poses must map sides to explicit bindings')
    for binding in poses.values():
        if isinstance(binding, dict) and 'channel' in binding:
            binding['channel'] = resolve_channel(episode, binding['channel'])
    if result.get('camera_types') is not None:
        if not isinstance(result['camera_types'], dict):
            raise ValueError('camera_types must map channel names to head/wrist')
        resolved = {}
        for name, kind in result['camera_types'].items():
            source = resolve_channel(episode, name)
            if source in resolved:
                raise ValueError(f'Duplicate camera_types binding: {source}')
            resolved[source] = kind
        result['camera_types'] = resolved
    if result.get('freeze_motion_review') is not None:
        mapping = result['freeze_motion_review']
        if not isinstance(mapping, dict):
            raise ValueError('freeze_motion_review must map cameras to lists of motion feedback channels')
        resolved = {}
        for camera, motions in mapping.items():
            source = resolve_channel(episode, camera)
            if source in resolved:
                raise ValueError(f'Duplicate freeze_motion_review camera binding: {source}')
            if not isinstance(motions, list) or any(not isinstance(n, str) or not n for n in motions):
                raise ValueError('freeze_motion_review values must be lists of channel names')
            names = []
            for motion in motions:
                try:
                    name = resolve_channel(episode, motion)
                except ValueError:
                    name = motion  # Optional evidence shortage must not block the image assessment.
                if name in names:
                    raise ValueError(f'Duplicate freeze_motion_review motion binding: {name}')
                names.append(name)
            resolved[source] = names
        result['freeze_motion_review'] = resolved
    if result.get('field_map') is not None:
        if not isinstance(result['field_map'], dict):
            raise ValueError('field_map must map channel names to fields')
        mapped = {}
        for name, fields in result['field_map'].items():
            if (not isinstance(fields, dict) or any(
                    not isinstance(key, str) or not key or not isinstance(value, str) or not value
                    for key, value in fields.items())):
                raise ValueError('field_map entries must map target fields to source field names')
            source = resolve_channel(episode, name)
            if source in mapped:
                raise ValueError(f'Duplicate field_map binding: {source}')
            mapped[source] = fields
        result['field_map'] = mapped
    if result.get('families') is not None:
        if not isinstance(result['families'], dict):
            raise ValueError('families must map channel names to source groups')
        families = {}
        for name, group in result['families'].items():
            source = resolve_channel(episode, name)
            if source in families:
                raise ValueError(f'Duplicate families binding: {source}')
            families[source] = group
        result['families'] = families
    return result


def channel_parameter_bindings(episode, params):
    """Preserve requested references and actual reader inputs in run evidence."""
    resolved = resolve_channel_parameters(episode, params)
    rows = []
    def append(path, requested, source):
        c = episode.channels[source]
        rows.append({'parameter': path, 'requested': requested, 'channel': source,
            'logical_name': c.logical_name, 'source': c.source,
            'source_mapping': copy.deepcopy(c.source_mapping)})
    for i, requested in enumerate(params.get('channels') or []):
        append(f'channels[{i}]', requested, resolved['channels'][i])
    for i, requested in enumerate(params.get('required_channels') or []):
        append(f'required_channels[{i}]', requested, resolved['required_channels'][i])
    for key in ('channel', 'primary', 'camera_channel'):
        if params.get(key) is not None:
            append(key, params[key], resolved[key])
    for side, binding in (params.get('poses') or {}).items():
        if isinstance(binding, dict) and 'channel' in binding:
            append(f'poses.{side}.channel', binding['channel'], resolved['poses'][side]['channel'])
    for key in ('camera_types', 'families', 'field_map'):
        for requested in (params.get(key) or {}):
            append(f'{key}.{requested}', requested, resolve_channel(episode, requested))
    for camera, motions in (params.get('freeze_motion_review') or {}).items():
        source = resolve_channel(episode, camera)
        append(f'freeze_motion_review.{camera}', camera, source)
        for i, motion in enumerate(motions):
            name = resolved['freeze_motion_review'][source][i]
            if name in episode.channels:
                append(f'freeze_motion_review.{camera}[{i}]', motion, name)
            else:
                rows.append({'parameter': f'freeze_motion_review.{camera}[{i}]',
                             'requested': motion, 'status': 'unavailable', 'reason': 'Optional channel missing'})
    return rows
