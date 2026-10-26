"""User-declared joint dimensions and explicit array selection; no side inference."""
import copy
import fnmatch
import numpy as np


def validate_mapping_config(profile):
    parts = profile.get('parts', {})
    if not isinstance(parts, dict):
        raise ValueError('parts must map robot part names to {dof: positive integer}')
    for name, spec in parts.items():
        if not isinstance(name, str) or not name or not isinstance(spec, dict):
            raise ValueError('Each robot part requires a stable ID and mapping')
        if spec.get('side', 'unknown') not in {'left', 'right', 'unknown'}:
            raise ValueError(f'{name}: invalid physical side')
        if 'dof' in spec and (type(spec['dof']) is not int or spec['dof'] <= 0):
            raise ValueError('Declared dof must be a positive integer')
    for name, spec in profile.get('channels', {}).items():
        part = spec.get('robot_part')
        if part is not None and part not in parts:
            raise ValueError(f'{name}: robot_part must reference declared parts')
        binding = spec.get('select')
        if binding is None:
            continue
        if not isinstance(binding, dict) or set(binding) != {'channel', 'field', 'indices'}:
            raise ValueError('select requires channel, field, indices')
        if not isinstance(binding['channel'], str) or not isinstance(binding['field'], str):
            raise ValueError('select channel/field must be strings')
        indices = binding['indices']
        if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 for i in indices) or len(indices) != len(set(indices)):
            raise ValueError(f'{name}: fill explicit unique nonnegative indices; sides are never inferred')
        if len(spec.get('fields', [])) != 1 or any(c in name for c in '*?['):
            raise ValueError('Selected channels require one output field and a literal channel name')
        source = profile.get('channels', {}).get(binding['channel'], {})
        first_match = next((v for pat, v in profile['channels'].items() if fnmatch.fnmatchcase(name, pat)), None)
        if first_match is not spec:
            raise ValueError(f'{name}: selection rule is shadowed by an earlier channel pattern')
        if binding['channel'] == name or source.get('select') is not None:
            raise ValueError('Selected channels must refer directly to an original channel')
        if part and parts[part].get('dof') and len(indices) != parts[part]['dof']:
            raise ValueError(f'{name}: selected indices do not match {part} dof')


def add_selected_topics(info, profile):
    raw = dict(info.topics)
    for name, spec in profile.get('channels', {}).items():
        binding = spec.get('select')
        if binding is None:
            continue
        if name in raw:
            raise ValueError(f'{name}: selection must not replace an original channel')
        if binding['channel'] not in raw:
            raise ValueError(f'{name}: missing source channel {binding["channel"]}')
        topic = copy.deepcopy(raw[binding['channel']])
        if topic.image_available:
            raise ValueError('Array selection requires a numeric source')
        topic.name = name
        topic.fields = spec['fields']
        topic.value_available = True
        info.topics[name] = topic


def check_joint_shape(channel, field, values, profile):
    if channel.modality != 'joint_space':
        return
    part = channel.robot_part
    if not part:
        if profile.get('parts'):
            raise ValueError(f'{channel.name}: declare robot_part to validate joint DOF')
        return  # Older profiles remain readable; robot check reports unconfirmed DOF.
    dof = profile['parts'][part].get('dof')
    if dof is None:
        return  # Unknown dimensions are retained; no fabricated DOF.
    if values.ndim != 2 or values.shape[1] != dof:
        actual = list(values.shape[1:])
        raise ValueError(f'{channel.name}.{field}: DOF mismatch: observed {actual}, {part} declares {dof}; '
                         'use channels.<new_name>.select with explicit channel/field/indices. '
                         'See examples/onboarding/joint_split.yaml; never infer left/right indices')
    names = channel.field_spec(field).get('joint_names')
    if names and len(names) != dof:
        raise ValueError(f'{channel.name}.{field}: joint_names dimension mismatch with declared DOF')


def select_values(episode, channel):
    spec = episode.profile['channels'][channel]
    binding = spec['select']
    # Source remains accessible as recorded, including mixed arrays declared as other.
    rows = episode.read_values(binding['channel'])
    if any(binding['field'] not in row for row in rows):
        raise ValueError('Selection source field is absent')
    data = np.asarray([row[binding['field']] for row in rows])
    indices = binding['indices']
    if data.ndim != 2 or max(indices) >= data.shape[1]:
        raise ValueError(f'{channel}: selection indices outside source array dimensions')
    if data.dtype.kind not in 'fiu':
        raise ValueError('Selection requires numeric arrays')
    return data[:, indices].copy()
