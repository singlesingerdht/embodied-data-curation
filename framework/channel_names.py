"""Logical channel names are references, never destructive source renames."""
import copy
import re


# Framework vocabulary, not a LeRobot feature-name or ROS-topic standard.
STANDARD_CHANNELS = {
    'rgb/head': {'modality': 'rgb', 'camera_role': 'head'},
    'rgb/chest': {'modality': 'rgb', 'camera_role': 'chest'},
    'rgb/wrist/left': {'modality': 'rgb', 'camera_role': 'wrist', 'side': 'left'},
    'rgb/wrist/right': {'modality': 'rgb', 'camera_role': 'wrist', 'side': 'right'},
    'depth/head': {'modality': 'depth', 'camera_role': 'head'},
    'depth/chest': {'modality': 'depth', 'camera_role': 'chest'},
    **{f'{kind}/arm/{side}/{role}': {'modality': modality, 'side': side, 'source_kind': role}
       for kind, modality in [('joint', 'joint_space'), ('pose', 'pose')]
       for side in ('left', 'right') for role in ('feedback', 'command')},
    **{f'joint/gripper/{side}/{role}': {
        'modality': 'joint_space', 'side': side, 'device_type': 'gripper',
        **({'source_kind': role} if role != 'state' else {})}
       for side in ('left', 'right') for role in ('state', 'feedback', 'command')},
    'joint/head/state': {'modality': 'joint_space', 'body_part': 'head'},
    'joint/lift/state': {'modality': 'joint_space', 'body_part': 'lift'},
    'odometry/base': {'modality': 'odometry'},
}
_NAME = re.compile(r'[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)+(?:@[a-z][a-z0-9_]*)?')


def _validate_name(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f'Invalid logical_name: {name!r}; use rgb/chest or rgb/chest@front')
    base = name.split('@', 1)[0]
    if base not in STANDARD_CHANNELS and '__' not in base.split('/')[0]:
        raise ValueError(f'Unknown logical_name: {name}; custom names require a team__ namespace')
    return STANDARD_CHANNELS.get(base, {})


def _check_semantics(name, spec, source):
    expected = _validate_name(name)
    representation = spec.get('representation', {})
    actual = {**spec, 'camera_role': representation.get('camera_role'),
              'device_type': representation.get('device_type')}
    position = spec.get('field_metadata', {}).get('position', {})
    if position.get('source_kind') not in (None, 'unknown'):
        actual['source_kind'] = position['source_kind']
    from framework.ir.modalities import normalize_modality
    if actual.get('modality'):
        actual['modality'] = normalize_modality(actual['modality'])
    for key, value in expected.items():
        declared = actual.get(key)
        if declared not in (None, '', 'unknown', value):
            raise ValueError(f'{source}: logical_name {name} conflicts with {key}={declared!r}')


def validate_logical_names(profile):
    """Validate explicit declarations without inferring missing physical metadata."""
    seen = {}
    for source, spec in profile.get('channels', {}).items():
        name = spec.get('logical_name')
        if name is None:
            continue
        _check_semantics(name, spec, source)
        if any(ch in source for ch in '*?['):
            raise ValueError(f'{source}: logical_name requires an exact channel binding, not a wildcard')
        if name in seen:
            raise ValueError(f'Duplicate logical_name {name}: {seen[name]}, {source}')
        seen[name] = source


def validate_channel_index(channels):
    index = {}
    for source, channel in channels.items():
        name = getattr(channel, 'logical_name', None)
        if name is None:
            continue
        _check_semantics(name, {key: getattr(channel, key, None) for key in
            ('modality', 'body_part', 'side', 'source_kind', 'field_metadata', 'representation')}, source)
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
    index = validate_channel_index(episode.channels)
    if reference in index:
        return index[reference]
    if reference in episode.channels:
        return reference
    raise ValueError(f'Channel is unavailable: {reference}; no fallback is allowed')


def channel_catalog(episode):
    """List loaded channels and their source/feature evidence, without reading payloads."""
    episode.open()
    validate_channel_index(episode.channels)
    storage = getattr(episode, 'image_storage', None)
    return {'schema_version': 'dqs_channel_catalog_v1', 'episode_id': episode.id,
        'channels': [{'logical_name': getattr(c, 'logical_name', None), 'channel': c.name,
            'modality': c.modality, 'fields': list(c.fields), 'sensor_id': c.sensor_id,
            'side': c.side, 'source_kind': c.source_kind, 'unit': c.unit,
            'field_metadata': copy.deepcopy(c.field_metadata),
            'representation': copy.deepcopy(c.representation),
            'image_storage': (storage(c.name) if callable(storage) else 'unknown') if c.image_available else None,
            'time_source': c.time_source, 'clock': c.clock, 'count': c.count,
            'source': c.source, 'source_mapping': copy.deepcopy(c.source_mapping)}
            for c in episode.channels.values()]}


def resolve_channel_parameters(episode, params):
    """Shared adapter hook for explicitly declared channel-reference parameters."""
    result = copy.deepcopy(params)
    if 'channels' in result:
        names = result['channels']
        if not isinstance(names, list) or not names:
            raise ValueError('channels must be a nonempty list')
        result['channels'] = [resolve_channel(episode, n) for n in names]
        if len(set(result['channels'])) != len(names):
            raise ValueError('Duplicate channel selection after resolving logical names')
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
    for i, requested in enumerate(params.get('channels', [])):
        append(f'channels[{i}]', requested, resolved['channels'][i])
    for key in ('channel', 'primary', 'camera_channel'):
        if params.get(key) is not None:
            append(key, params[key], resolved[key])
    for side, binding in (params.get('poses') or {}).items():
        if isinstance(binding, dict) and 'channel' in binding:
            append(f'poses.{side}.channel', binding['channel'], resolved['poses'][side]['channel'])
    for key in ('camera_types', 'families'):
        for requested in (params.get(key) or {}):
            append(f'{key}.{requested}', requested, resolve_channel(episode, requested))
    return rows
