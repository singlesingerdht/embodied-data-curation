"""Explicit robot topology and execution instances; no topology inference."""
import copy


def selected_arms(profile, selection='all'):
    declared = {k: v for k, v in profile.get('parts', {}).items() if v.get('type') == 'arm'}
    if not declared:
        raise ValueError('Declare parts.<id>.type: arm; observed topics do not establish robot topology')
    arms = list(declared) if selection == 'all' else selection
    if (not isinstance(arms, list) or not arms or any(not isinstance(a, str) for a in arms)
            or len(set(arms)) != len(arms) or set(arms) - set(declared)):
        raise ValueError(f'arms must be all or unique declared arm IDs: {list(declared)}')
    return arms


def channel_arm(profile, name, channel):
    part = getattr(channel, 'robot_part', None)
    parts = profile.get('parts', {})
    if part in parts:
        return part if parts[part].get('type') == 'arm' else parts[part].get('arm')
    return None  # A side or a topic spelling alone never declares an arm.


def instances(episode, supplied, mode):
    """Prepare explicit scopes, shared by the recommendation and the executor."""
    from edc.algorithms.channel_names import resolve_channel_parameters, resolve_channel
    profile = episode.profile
    enabled = 'arms' in supplied or any(p.get('type') == 'arm' for p in profile.get('parts', {}).values())
    if not enabled:
        return [{'id': 'episode', 'params': copy.deepcopy(supplied), 'scope': {}, 'complete': True}]
    arms = selected_arms(profile, supplied.get('arms', 'all'))
    params = copy.deepcopy(supplied)
    params.pop('arms', None)
    # Explicit channel selection restricts scope and may never reintroduce another arm.
    missing_channels = []
    reference_by_source = {}
    if 'channels' in params:
        references = params.pop('channels')
        if not isinstance(references, list) or not references:
            raise ValueError('channels must be a nonempty list')
        names = []
        for reference in references:
            try:
                name = resolve_channel(episode, reference)
            except ValueError:
                declaration = next((v for k, v in profile.get('channels', {}).items()
                    if k == reference or v.get('logical_name') == reference), None)
                if declaration is None:
                    raise
                part = declaration.get('robot_part')
                part_spec = profile.get('parts', {}).get(part, {})
                arm = part if part_spec.get('type') == 'arm' else part_spec.get('arm')
                if arm and arm not in arms:
                    raise ValueError(f'{reference}: explicit channel belongs to unselected arm {arm}')
                missing_channels.append(reference)
            else:
                if name in names:
                    raise ValueError('Duplicate channel selection after resolving logical names')
                names.append(name)
                reference_by_source[name] = reference
        params = resolve_channel_parameters(episode, params)
        params['channels'] = names
        resolved = params
    else:
        resolved = resolve_channel_parameters(episode, params)
    named = resolved.get('channels', [resolved['channel']] if resolved.get('channel') else None)
    selected = []
    for name, channel in episode.channels.items():
        arm = channel_arm(profile, name, channel)
        if named is not None and name not in named:
            continue
        if arm and arm not in arms:
            if named is not None:
                raise ValueError(f'{name}: explicit channel belongs to unselected arm {arm}')
            continue
        if mode == 'pose_feedback' and (channel.modality != 'pose' or channel.source_kind != 'feedback'):
            continue
        if mode == 'joint_feedback' and (channel.modality != 'joint_space' or channel.source_kind != 'feedback'
                                        or profile.get('parts', {}).get(channel.robot_part, {}).get('type') != 'arm'):
            continue
        if mode == 'numeric' and not channel.value_available:
            continue
        if mode == 'rgb' and (channel.modality != 'rgb' or not channel.image_available):
            continue
        selected.append(name)
    scope = {'arms': arms, 'selection': supplied.get('arms', 'all'), 'mode': mode,
             'channels': selected, 'missing_arms': [], 'missing_channels': missing_channels}
    if mode == 'pose_feedback':
        rows = []
        for arm in arms:
            # Explicit named pose wins. Otherwise select the unsuffixed standard name.
            candidates = [n for n in selected if channel_arm(profile, n, episode.channels[n]) == arm]
            reference = f'pose/arm/{arm}/feedback'
            if named is not None:
                if len(candidates) > 1:
                    raise ValueError(f'{arm}: choose exactly one feedback pose channel')
                name = candidates[0] if candidates else None
            else:
                try:
                    name = resolve_channel(episode, reference)
                except ValueError:
                    name = None
                if name not in candidates:
                    name = None
            local = copy.deepcopy(params)
            local.pop('channels', None)
            if name:
                local['channel'] = name
            else:
                local.pop('channel', None)
            rows.append({'id': arm, 'params': local, 'scope': {**scope, 'arms': [arm],
                'channels': [name] if name else [], 'missing_arms': [] if name else [arm]},
                'complete': bool(name), 'error': '' if name else f'{arm}: missing {reference}'})
        return rows
    expected_arms = arms
    if named is not None and mode in {'numeric', 'channels'}:
        # Explicit base/IMU selections do not imply evaluating every arm.
        # Missing explicitly selected arm channels remain in missing_channels.
        requested_arms = {channel_arm(profile, n, episode.channels[n]) for n in named}
        for reference in missing_channels:
            declaration = next(v for k, v in profile.get('channels', {}).items()
                if k == reference or v.get('logical_name') == reference)
            part = declaration.get('robot_part')
            part_spec = profile.get('parts', {}).get(part, {})
            requested_arms.add(part if part_spec.get('type') == 'arm' else part_spec.get('arm'))
        expected_arms = [arm for arm in arms if arm in requested_arms]
    if mode == 'rgb':
        # An arm does not imply a wrist camera. Only explicitly declared RGB
        # cameras in this run's scope establish arm-specific media requirements.
        expected_arms = []
        for source, declaration in profile.get('channels', {}).items():
            if declaration.get('modality') not in {'rgb', 'image'}:
                continue
            if named is not None and source not in named and declaration.get('logical_name') not in missing_channels:
                continue
            part = declaration.get('robot_part')
            part_spec = profile.get('parts', {}).get(part, {})
            arm = part if part_spec.get('type') == 'arm' else part_spec.get('arm')
            if arm in arms and arm not in expected_arms:
                expected_arms.append(arm)
    missing = [a for a in expected_arms if not any(channel_arm(profile, n, episode.channels[n]) == a for n in selected)]
    scope['missing_arms'] = missing
    if not selected:
        return [{'id': 'episode', 'params': params, 'scope': scope, 'complete': False,
                 'error': 'No channels in the declared arm scope'}]
    params.pop('channel', None)
    params['channels'] = [reference_by_source.get(n, n) for n in selected]
    return [{'id': 'episode', 'params': params, 'scope': scope, 'complete': not missing and not missing_channels}]
