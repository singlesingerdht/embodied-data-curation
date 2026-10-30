"""SI suggestions and a configuration confirmation tied to Numeric_Check."""
import copy
from datetime import datetime, timezone
import hashlib
import json

SI_UNITS = {'length': 'm', 'angle': 'rad', 'time': 's', 'angular_velocity': 'rad/s',
            'acceleration': 'm/s^2', 'force': 'N', 'torque': 'N*m', 'current': 'A'}
TARGETS = {('joint_space', 'position'): ('joint_positions', 'rad'),
           ('pose', 'position'): ('pose_freeze_positions', 'm'),
           ('joint_space', 'joint_q'): ('cmd_positions', 'rad'),
           ('imu', 'ang_vel'): ('gyro', 'rad/s'), ('imu', 'lin_acc'): ('accel', 'm/s^2'),
           # Adapter-only target: recorded joint velocity, checked in numeric_supplements, never by the method.
           ('joint_space', 'velocity'): ('joint_velocities', 'rad/s')}
# Threshold groups the adapter owns. Stripped before the method validates or receives thresholds,
# and only present when configured, so existing confirmation digests are unchanged.
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
            reasons.append('Numeric_Check: configuration confirmation for the current fields and thresholds is pending/stale')
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
    # Topic scope and the finiteness-only declaration change no threshold or configuration confirmation.
    params = {k: v for k, v in params.items() if k not in {'channels', 'arms', 'finiteness_only', 'freeze_min_duration_s'}}
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
            if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values.values()):
                raise ValueError(f'{group} thresholds must be finite and positive')
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
    tables = [g for g in LIMIT_GROUPS if isinstance(settings['thresholds'][g]['limits'], dict)]
    if not tables:
        return settings
    result = copy.deepcopy(settings)
    names = (channel.name, getattr(channel, 'logical_name', None))
    for group in tables:
        limits = settings['thresholds'][group]['limits']
        result['thresholds'][group]['limits'] = next((limits[n] for n in names if n in limits), None)
    return result


def review_digest(profile, settings):
    # Logical addressing cannot change a previously attested physical contract.
    # Omit only this new metadata key, retaining existing pre-alias digests.
    channels = {name: {k: v for k, v in spec.items() if k != 'logical_name'}
                for name, spec in profile.get('channels', {}).items()}
    payload = {'unit_system': 'SI', 'channels': channels,
               'timestamp_column': profile.get('timestamp_column'),
               'timestamp_unit': profile.get('timestamp_unit'), 'settings': settings}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def numeric_review(profile=None, params=None):
    profile = profile or {}
    settings = numeric_settings(profile, params)
    digest = review_digest(profile, settings)
    approval = profile.get('numeric_review') or {}
    if not isinstance(approval, dict):
        raise ValueError('numeric_review must be a mapping')
    confirmed = (approval.get('status') == 'confirmed'
                 and isinstance(approval.get('confirmed_by'), str)
                 and bool(approval['confirmed_by'].strip())
                 and approval.get('configuration_sha256') == digest)
    return {'status': 'confirmed' if confirmed else 'stale' if approval.get('status') == 'confirmed' else 'pending',
            'confirmed_by': approval.get('confirmed_by') if confirmed else None,
            'configuration_sha256': digest, 'unit_system': 'SI',
            'unit_suggestions': dict(SI_UNITS), 'settings': settings,
            'required_action': None if confirmed else '请配置确认人确认字段单位、指令/反馈与数值阈值；修改配置后需重新确认。'}


def confirm_numeric_profile(profile, reviewer, params=None):
    """Explicit configuration-owner confirmation; never infer or rewrite source units/calibration."""
    from edc.robots.profiles import load_profile
    from edc.data.ir.modalities import normalize_modality
    profile = load_profile(profile)
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError('A nonempty reviewer name is required')
    eligible = 0
    for name, channel in profile.get('channels', {}).items():
        modality = normalize_modality(channel.get('modality', 'other'))
        for field in channel.get('fields', []):
            target = TARGETS.get((modality, field))
            if not target:
                continue
            eligible += 1
            metadata = {**channel, **channel.get('field_metadata', {}).get(field, {})}
            semantic = channel.get('representation', {})
            if (semantic.get('quantity') == 'unknown' or semantic.get('semantic_status') in
                    {'unknown', 'pending', 'pending_owner_confirmation'}):
                raise ValueError(f'{name}.{field}: confirm physical quantity before numeric approval')
            from edc.data.ir.units import si_unit, UnitInputError
            try:
                compatible = si_unit(metadata.get('unit')) == target[1]
            except UnitInputError:
                compatible = False
            if not compatible or metadata.get('calibration_status') != 'confirmed':
                raise ValueError(f'{name}.{field}: explicitly confirm source unit convertible to {target[1]} and calibration_status first')
            if modality == 'joint_space' and metadata.get('source_kind') not in {'command', 'feedback'}:
                raise ValueError(f'{name}.{field}: explicitly declare command/feedback first')
    if not eligible:
        raise ValueError('No declared fields supported by Numeric_Check physical checks')
    settings = numeric_settings(profile, params)
    profile['numeric_check'] = settings
    profile['numeric_review'] = {'status': 'confirmed', 'confirmed_by': reviewer.strip(),
        'confirmed_at': datetime.now(timezone.utc).isoformat(),
        'configuration_sha256': review_digest(profile, settings)}
    return profile
