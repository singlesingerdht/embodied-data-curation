"""SI suggestions and an author confirmation tied to the actual numeric configuration."""
import copy
from datetime import datetime, timezone
import hashlib
import json

SI_UNITS = {'length': 'm', 'angle': 'rad', 'time': 's', 'angular_velocity': 'rad/s',
            'acceleration': 'm/s^2', 'force': 'N', 'torque': 'N*m', 'current': 'A'}
TARGETS = {('joint_space', 'position'): ('joint_positions', 'rad'),
           ('pose', 'position'): ('pose_freeze_positions', 'm'),
           ('joint_space', 'joint_q'): ('cmd_positions', 'rad'),
           ('imu', 'ang_vel'): ('gyro', 'rad/s'), ('imu', 'lin_acc'): ('accel', 'm/s^2')}


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
            reasons.append('Numeric_Check: author confirmation of the current configuration and thresholds is pending/stale')
    return {'channel': channel.name, 'field': field, 'source_unit': spec.get('source_unit', spec['unit']),
            'execution_unit': spec['unit'], 'required_unit': target[1] if target else None,
            'physical_checks_available': bool(target) and not reasons, 'reasons': reasons,
            'target': target}


def numeric_settings(profile=None, params=None):
    from edc.algorithms.quality.algorithms.Numeric_Check.settings import thresholds
    configured = (profile or {}).get('numeric_check', {})
    params = params or {}
    # Topic scope does not change any numerical threshold or author attestation.
    params = {k: v for k, v in params.items() if k not in {'channels', 'arms'}}
    for value in (configured, params):
        if not isinstance(value, dict) or set(value) - {'thresholds', 'weights'}:
            raise ValueError('Numeric_Check accepts thresholds and weights')
    base = thresholds(configured.get('thresholds'))
    overrides = params.get('thresholds', {})
    if not isinstance(overrides, dict):
        raise ValueError('Numeric_Check thresholds must be a mapping')
    merged = copy.deepcopy(base)
    for group, values in overrides.items():
        if group not in merged or not isinstance(values, dict):
            raise ValueError(f'Unknown Numeric_Check threshold group: {group}')
        merged[group].update(values)
    weights = params.get('weights', configured.get('weights'))
    if weights is not None and not isinstance(weights, dict):
        raise ValueError('Numeric_Check weights must be a mapping')
    return {'thresholds': thresholds(merged), 'weights': copy.deepcopy(weights)}


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
            'required_action': None if confirmed else '请作者确认字段单位、指令/反馈与数值阈值；修改配置后需重新确认。'}


def confirm_numeric_profile(profile, reviewer, params=None):
    """Explicit author attestation; never infer or rewrite source units/calibration."""
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
