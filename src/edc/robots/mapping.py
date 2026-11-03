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
        if 'type' in spec and (not isinstance(spec['type'], str) or not spec['type']):
            raise ValueError(f'{name}: type must be a nonempty part category')
    for name, spec in parts.items():
        if spec.get('arm') is not None:
            arm = spec['arm']
            if not isinstance(arm, str) or arm == name or parts.get(arm, {}).get('type') != 'arm':
                raise ValueError(f'{name}: arm must reference another declared arm part')
            if (spec.get('side') in {'left', 'right'} and parts[arm].get('side') in {'left', 'right'}
                    and spec['side'] != parts[arm]['side']):
                raise ValueError(f'{name}: part side conflicts with arm {arm}')
    for name, spec in profile.get('channels', {}).items():
        part = spec.get('robot_part')
        if part is not None and (not isinstance(part, str) or part not in parts):
            raise ValueError(f'{name}: robot_part must reference declared parts')
        if part is not None:
            side, part_side = spec.get('side'), parts[part].get('side')
            if side in {'left', 'right'} and part_side in {'left', 'right'} and side != part_side:
                raise ValueError(f'{name}: channel side conflicts with robot_part {part}')
        binding = spec.get('select')
        if binding is None:
            continue
        if isinstance(binding, dict) and set(binding) == {'fk'}:
            validate_fk_selection(profile, name, spec)
            continue
        if isinstance(binding, dict) and set(binding) in ({'channel', 'fields'}, {'channel', 'fields', 'indices'}):
            validate_field_selection(profile, name, spec)
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


def fk_bindings(chain):
    """Joint input bindings of a raw (urdf + inputs) or imported (joints[].input) chain."""
    if 'inputs' in chain:
        return chain['inputs'] or {}
    return {j['name']: j['input'] for j in chain.get('joints', []) if 'input' in j}


def validate_fk_selection(profile, name, spec):
    """select: {fk: chain} derives a tip position channel from a declared transforms chain."""
    chain_name = spec['select']['fk']
    chains = profile.get('transforms', {}).get('chains', {})
    if not isinstance(chain_name, str) or chain_name not in chains:
        raise ValueError(f'{name}: select.fk must name a declared transforms chain')
    if spec.get('modality') != 'pose' or spec.get('fields') != ['position'] or any(c in name for c in '*?['):
        raise ValueError(f'{name}: FK selection requires a literal pose channel with fields [position]')
    units = {spec.get('unit'), spec.get('field_metadata', {}).get('position', {}).get('unit')}
    if units - {None, 'm'}:
        raise ValueError(f'{name}: FK positions are in m')
    first_match = next((v for pat, v in profile['channels'].items() if fnmatch.fnmatchcase(name, pat)), None)
    if first_match is not spec:
        raise ValueError(f'{name}: selection rule is shadowed by an earlier channel pattern')
    bindings = fk_bindings(chains[chain_name])
    if not bindings:
        raise ValueError(f'{name}: FK chain {chain_name} needs inputs for its moving joints')
    for binding in bindings.values():
        source = profile['channels'].get(binding.get('channel')) if isinstance(binding, dict) else None
        if source is None or 'fk' in (source.get('select') or {}):
            raise ValueError(f'{name}: FK inputs must be declared joint channels, not derived poses')


def validate_field_selection(profile, name, spec):
    """select: {channel, fields: {output: source_field}[, indices]} renames fields of one recorded
    message, e.g. the IMU block of a robot state message, or joint_q/joint_v of one arm; optional
    indices pick the same components from every field; no unit conversion."""
    binding = spec['select']
    fields = binding['fields']
    if 'indices' in binding:
        indices = binding['indices']
        if (not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 for i in indices)
                or len(indices) != len(set(indices))):
            raise ValueError(f'{name}: fill explicit unique nonnegative indices; sides are never inferred')
        part = spec.get('robot_part')
        dof = profile.get('parts', {}).get(part, {}).get('dof') if part else None
        if dof and len(indices) != dof:
            raise ValueError(f'{name}: selected indices do not match {part} dof')
    if not isinstance(binding['channel'], str) or not isinstance(fields, dict) or not fields:
        raise ValueError(f'{name}: select.fields must map output fields to source fields')
    # A field may also come from another recorded channel with the same rows (e.g. joint velocity in
    # its own column): {channel, field, indices}. select.channel stays the timeline source.
    for key, value in fields.items():
        if isinstance(value, dict):
            other = profile['channels'].get(value.get('channel'))
            index = value.get('indices')
            if (set(value) - {'channel', 'field', 'indices'} or other is None or other.get('select') is not None
                    or not isinstance(value.get('field'), str) or not value['field']):
                raise ValueError(f'{name}.{key}: field source needs an original channel, field and optional indices')
            if index is not None and (not isinstance(index, list) or not index
                                      or any(type(i) is not int or i < 0 for i in index)):
                raise ValueError(f'{name}.{key}: indices must be nonnegative integers')
        elif not isinstance(key, str) or not isinstance(value, str) or not value:
            raise ValueError(f'{name}: select.fields keys and values must be field names')
    if list(fields) != list(spec.get('fields', [])) or any(c in name for c in '*?['):
        raise ValueError(f'{name}: select.fields must list exactly the declared fields, in order, on a literal channel')
    first_match = next((v for pat, v in profile['channels'].items() if fnmatch.fnmatchcase(name, pat)), None)
    if first_match is not spec:
        raise ValueError(f'{name}: selection rule is shadowed by an earlier channel pattern')
    source = profile['channels'].get(binding['channel'], {})
    if binding['channel'] == name or source.get('select') is not None:
        raise ValueError('Selected channels must refer directly to an original channel')


def fk_source(profile, binding):
    """Original recorded channel whose rows/timeline an FK-derived channel shares."""
    chain = profile['transforms']['chains'][binding['fk']]
    channels = {b['channel'] for b in fk_bindings(chain).values()}
    roots = {(profile['channels'][c].get('select') or {}).get('channel', c) for c in channels}
    if len(roots) != 1:
        raise ValueError(f'FK chain {binding["fk"]} inputs must come from one recorded channel; align explicitly first')
    return roots.pop()


def fk_metadata(profile, binding, channels):
    """Physical metadata an FK position inherits: metres in the chain base frame; command/feedback
    semantics and calibration only as strong as the weakest joint input."""
    chain = profile['transforms']['chains'][binding['fk']]
    specs = [channels[b['channel']].field_spec(b['field']) for b in fk_bindings(chain).values()]
    kinds = {s['source_kind'] for s in specs}
    calibrations = {s['calibration_status'] or 'unknown' for s in specs}
    return {'unit': 'm', 'coordinate_frame': chain['base_frame'],
            'source_kind': kinds.pop() if len(kinds) == 1 else 'unknown',
            'calibration_status': 'confirmed' if calibrations == {'confirmed'}
                                  else 'unknown' if 'unknown' in calibrations else 'pending'}


def add_selected_topics(info, profile):
    raw = dict(info.topics)
    for name, spec in profile.get('channels', {}).items():
        binding = spec.get('select')
        if binding is None:
            continue
        if name in raw:
            raise ValueError(f'{name}: selection must not replace an original channel')
        source = fk_source(profile, binding) if 'fk' in binding else binding['channel']
        if source not in raw:
            raise ValueError(f'{name}: missing source channel {source}')
        topic = copy.deepcopy(raw[source])
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
                         'in the robot yaml give each arm its joints index; never infer left/right indices')
    names = channel.field_spec(field).get('joint_names')
    if names and len(names) != dof:
        raise ValueError(f'{channel.name}.{field}: joint_names dimension mismatch with declared DOF')


def select_field_rows(episode, channel):
    """Rows of a select.fields channel: each output field is the named source field, unchanged."""
    binding = episode.profile['channels'][channel]['select']
    fields, indices = binding['fields'], binding.get('indices')
    rows = episode.read_values(binding['channel'])
    columns = {}
    for out, src in fields.items():
        if isinstance(src, dict):  # Another recorded channel with the same rows.
            source, key, picks = episode.read_values(src['channel']), src['field'], src.get('indices')
        else:
            source, key, picks = rows, src, indices
        if len(source) != len(rows):
            raise ValueError(f'{channel}.{out}: source rows differ from {binding["channel"]}')
        if any(key not in row for row in source):
            raise ValueError(f'{channel}: selection source fields absent: {key}')
        values = [row[key] for row in source]
        if picks is not None:
            if any(len(v) <= max(picks) for v in values):
                raise ValueError(f'{channel}: selection indices outside source array dimensions')
            values = [[v[i] for i in picks] for v in values]
        columns[out] = values
    return [{out: columns[out][i] for out in fields} for i in range(len(rows))]


def scaled(spec, field, data):
    """Raw numbers mapped to the declared unit (robot yaml `scale`: value = k * raw + b)."""
    scale = ((spec.get('field_metadata') or {}).get(field) or {}).get('scale')
    return data * scale[0] + scale[1] if scale else data


def select_values(episode, channel, field=None):
    spec = episode.profile['channels'][channel]
    binding = spec['select']
    if 'fields' in binding:
        field = field or spec['fields'][0]
        data = np.asarray([row[field] for row in select_field_rows(episode, channel)], dtype=float)
        return scaled(spec, field, data.reshape(len(data), -1) if data.ndim == 1 else data)
    if 'fk' in binding:
        cache = episode.__dict__.setdefault('_fk_positions', {})
        if channel not in cache:
            cache[channel] = np.asarray(episode.forward_kinematics(binding['fk']).output)[:, :3, 3].copy()
        return cache[channel].copy()
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
    return scaled(spec, spec['fields'][0], data[:, indices].astype(float))
