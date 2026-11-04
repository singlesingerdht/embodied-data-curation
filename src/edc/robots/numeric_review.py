"""Numeric_Check 的阈值，以及 yaml 里写的确认人（robot.confirmed_by）。"""
import copy

SI_UNITS = {'length': 'm', 'angle': 'rad', 'time': 's', 'angular_velocity': 'rad/s',
            'acceleration': 'm/s^2', 'force': 'N', 'torque': 'N*m', 'current': 'A'}
TARGETS = {('joint_space', 'position'): ('joint_positions', 'rad'),
           ('pose', 'position'): ('pose_freeze_positions', 'm'),
           ('joint_space', 'joint_q'): ('cmd_positions', 'rad'),
           ('imu', 'ang_vel'): ('gyro', 'rad/s'), ('imu', 'lin_acc'): ('accel', 'm/s^2'),
           # Adapter-only target: recorded joint velocity, checked in numeric_supplements, never by the method.
           ('joint_space', 'velocity'): ('joint_velocities', 'rad/s')}
# Threshold groups the adapter owns. Stripped before the method validates or receives thresholds,
# and only present when configured.
ADAPTER_GROUPS = {'joint_velocity': {'max_abs'}}
ADAPTER_TARGETS = {'joint_velocities': 'joint_velocity'}


def method_settings(settings):
    """Settings for the original Numeric_Check method: adapter-only threshold groups removed."""
    result = copy.deepcopy(settings)
    for group in ADAPTER_GROUPS:
        result['thresholds'].pop(group, None)
    return result


def numeric_field_status(episode, channel, field, review):
    """Shared metadata eligibility for recommendations and the existing detector."""
    from edc.data.ir.units import UnitInputError, require_si_field
    spec = channel.field_spec(field)
    target = TARGETS.get((channel.modality, field))
    if channel.modality == 'joint_space' and field in {'position', 'joint_q'}:
        if spec['source_kind'] == 'command':
            target = ('cmd_positions', 'rad')
        elif spec['source_kind'] == 'feedback':
            target = ('joint_positions', 'rad')
    reasons = []
    try:
        require_si_field(episode, channel.name, field, target[1] if target else None)
    except UnitInputError as exc:
        reasons.append(str(exc))
    if not target:
        reasons.append('Physical quantity/field is undeclared or unsupported by Numeric_Check; finiteness only')
    else:
        if spec['calibration_status'] != 'confirmed':
            reasons.append(f'{channel.name}.{field}: calibration_status must be confirmed')
        if channel.modality == 'joint_space' and spec['source_kind'] not in {'command', 'feedback'}:
            reasons.append(f'{channel.name}.{field}: declare command or feedback')
        if review['status'] != 'confirmed':
            reasons.append('Numeric_Check: 没有确认人（yaml 的 robot.confirmed_by）')
        group = ADAPTER_TARGETS.get(target[0])
        if group and group not in review['settings']['thresholds']:
            reasons.append(f'numeric_check.thresholds.{group} is not configured; no default threshold is assumed')
    return {'channel': channel.name, 'field': field, 'source_unit': spec.get('source_unit', spec['unit']),
            'execution_unit': spec['unit'], 'required_unit': target[1] if target else None,
            'physical_checks_available': bool(target) and not reasons, 'reasons': reasons,
            'target': target}


def numeric_settings(profile=None, params=None):
    configured = (profile or {}).get('numeric_check', {})
    params = params or {}
    # Topic scope and the finiteness-only declaration are not thresholds.
    params = {k: v for k, v in params.items()
              if k not in {'channels', 'arms', 'finiteness_only', 'freeze_min_duration_s', 'joint_limits_pending',
                           'joint_stuck', 'ranges'}}
    for value in (configured, params):
        if not isinstance(value, dict) or set(value) - {'thresholds', 'weights'}:
            raise ValueError('Numeric_Check accepts thresholds and weights')
    base = _thresholds(configured.get('thresholds'))
    overrides = params.get('thresholds', {})
    if not isinstance(overrides, dict):
        raise ValueError('Numeric_Check thresholds must be a mapping')
    merged = copy.deepcopy(base)
    for group, values in overrides.items():
        if group in ADAPTER_GROUPS and isinstance(values, dict):
            merged.setdefault(group, {})
        if group not in merged or not isinstance(values, dict):
            raise ValueError(f'Unknown Numeric_Check threshold group: {group}')
        merged[group].update(values)
    weights = params.get('weights', configured.get('weights'))
    if weights is not None and not isinstance(weights, dict):
        raise ValueError('Numeric_Check weights must be a mapping')
    return {'thresholds': _thresholds(merged), 'weights': copy.deepcopy(weights)}


def _thresholds(overrides):
    """Method thresholds plus per-channel joint limits {channel or logical_name: [[lower, upper], ...]}.

    Each per-channel table is validated with the method's own rules; the method itself
    only ever receives the single table resolved for its input channel.
    """
    import math
    from edc.algorithms.quality.algorithms.Numeric_Check.settings import thresholds
    adapter = {}
    if isinstance(overrides, dict) and set(overrides) & set(ADAPTER_GROUPS):
        overrides = copy.deepcopy(overrides)
        for group, names in ADAPTER_GROUPS.items():
            if group not in overrides:
                continue
            values = overrides.pop(group)
            if not isinstance(values, dict) or set(values) != names:
                raise ValueError(f'{group} requires exactly {sorted(names)}')
            # One number for every joint, or one per joint (e.g. the URDF <limit velocity>);
            # or {channel or logical_name: either of these}, one table per arm.
            tables = [t for v in values.values() for t in (v.values() if isinstance(v, dict) and v else [v])]
            numbers = [x for t in tables for x in (t if isinstance(t, list) and t else [t])]
            if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in numbers):
                raise ValueError(f'{group} thresholds must be finite and positive (a number or a list of numbers)')
            adapter[group] = dict(values)
    result = _method_thresholds(overrides, thresholds)
    result.update(adapter)
    return result


LIMIT_GROUPS = ('joint', 'command_limits')  # Groups whose `limits` may be a per-channel table.


def _method_thresholds(overrides, thresholds):
    tables = {group: ((overrides or {}).get(group) or {}).get('limits') if isinstance(overrides, dict) else None
              for group in LIMIT_GROUPS}
    tables = {group: limits for group, limits in tables.items() if isinstance(limits, dict)}
    if not tables:
        return thresholds(overrides)
    stripped = copy.deepcopy(overrides)
    for group, limits in tables.items():
        if not limits:
            raise ValueError(f'Per-channel {group} limits require at least one channel')
        for table in limits.values():
            thresholds({group: {'limits': table}})
        stripped[group]['limits'] = None
    result = thresholds(stripped)
    for group, limits in tables.items():
        result[group]['limits'] = copy.deepcopy(limits)
    return result


def channel_settings(settings, channel):
    """Settings for one input channel; a channel without its own limits table stays unconfigured."""
    found = [(g, 'limits') for g in LIMIT_GROUPS if isinstance(settings['thresholds'][g]['limits'], dict)]
    if isinstance(settings['thresholds'].get('joint_velocity', {}).get('max_abs'), dict):
        found.append(('joint_velocity', 'max_abs'))  # One speed table per arm.
    if not found:
        return settings
    result = copy.deepcopy(settings)
    names = (channel.name, getattr(channel, 'logical_name', None))
    for group, key in found:
        tables = settings['thresholds'][group][key]
        result['thresholds'][group][key] = next((tables[n] for n in names if n in tables), None)
    return result


def numeric_review(profile=None, params=None):
    """当前阈值和确认人；写了确认人（非空）才做物理检查，否则只查 NaN/无穷大。"""
    profile = profile or {}
    settings = numeric_settings(profile, params)
    approval = profile.get('numeric_review') or {}
    if not isinstance(approval, dict):
        raise ValueError('numeric_review must be a mapping')
    who = approval.get('confirmed_by')
    confirmed = isinstance(who, str) and bool(who.strip())
    return {'status': 'confirmed' if confirmed else 'pending', 'confirmed_by': who if confirmed else None,
            'unit_system': 'SI', 'unit_suggestions': dict(SI_UNITS), 'settings': settings,
            'required_action': None if confirmed else '请在 yaml 的 robot.confirmed_by 写上确认单位和含义的人。'}
