"""Repository-owned indicator registrations.

Each row in ``inventory.json`` is an algorithm in the core EDC pool.  The
inventory describes implementation coverage; it is not a dependency manifest.
"""
import json
import math
from pathlib import Path
from edc.algorithms.registry import AlgorithmSpec

INVENTORY = json.loads(Path(__file__).with_name('inventory.json').read_text())
ROWS = {r['code']: r for r in INVENTORY['indicators']}


def family(channel):
    if channel.modality == 'rgb':
        return 'camera'
    if channel.modality == 'imu':
        return 'imu'
    if channel.modality == 'pose':
        return 'pose'
    if channel.modality == 'lidar':
        return 'lidar'
    if channel.modality == 'joint_space':
        return {'command': 'cmd', 'feedback': 'joint'}.get(channel.source_kind)
    return None


def input_requirements(row):
    """Build the declarative minimum input contract for one indicator.

    The indicator preflight remains authoritative for complex relationships.  This
    metadata lets the configuration/recommendation endpoint show the basic
    modality, field, SI, timing and clock requirements before execution.
    """
    code, modality, group = row['code'], row['modality'], row['group']
    if code in {'M8', 'M9'}:
        # File integrity and TF-chain checks are dataset/metadata contracts, not
        # channel contracts supported by data_requirements yet.
        return ()
    channel_param = 'required_channels' if code == 'M3' else 'channels'
    canonical = {'camera': 'rgb', 'imu': 'imu', 'lidar': 'lidar',
                 'joint': 'joint_space', 'cmd': 'joint_space', 'pose': 'pose'}
    base = {'id': f'{code}_channels', 'channel_param': channel_param}
    if modality in canonical:
        base['modality'] = canonical[modality]
    if modality == 'camera':
        base['frames_required'] = group == 'content'
    if modality in {'joint', 'cmd'}:
        base['source_kind'] = 'command' if modality == 'cmd' else 'feedback'
    if group in {'timing', 'dedup', 'content', 'sync'}:
        base.update(time_sources=['acquisition', 'container'], min_samples=3)
    if group == 'sync':
        base.update(time_sources=['acquisition'], clock_required=True, min_channels=2)

    fields = {
        'joint': (('position', 'rad'),),
        'cmd': (('joint_q', 'rad'),),
        'pose': (('position', 'm'),),
        'imu': (('ang_vel', 'rad/s'), ('lin_acc', 'm/s^2')),
    }
    if group == 'content' and modality in fields:
        requirements = []
        for field, unit in fields[modality]:
            item = {**base, 'id': f'{code}_{field}', 'field': field, 'unit': unit,
                    'calibration_status': 'confirmed'}
            if modality in {'joint', 'cmd', 'pose', 'imu'}:
                item['source_kind'] = 'command' if modality == 'cmd' else 'feedback'
            requirements.append(item)
        return tuple(requirements)
    if group == 'structure' and modality in {'joint', 'cmd', 'pose'}:
        field, unit = fields[modality][0]
        item = {**base, 'id': f'{code}_{field}', 'field': field, 'unit': unit}
        item['source_kind'] = 'command' if modality == 'cmd' else 'feedback'
        return (item,)
    return (base,)


def input_description(row):
    scope = 'explicit channels or all matching declared channels'
    text = f"{row['modality']} / {row['group']}; {scope}; episode boundaries required"
    if row['group'] in {'timing', 'dedup', 'content', 'sync'}:
        text += '; recorded per-row timestamps required'
    if row['group'] == 'sync':
        text += '; at least two independent acquisition channels with one explicit clock'
    if row['group'] == 'content' and row['modality'] != 'camera':
        text += '; confirmed physical semantics and SI execution fields required'
    return text


def required_capabilities(row):
    """Declare non-channel resources in the common recommendation contract."""
    return {'M8': ('dataset_file',), 'M9': ('tf_chain',)}.get(row['code'], ())


def runtime_dependencies(row):
    """Optional execution libraries are checked without importing them."""
    if row['group'] == 'content' and row['modality'] == 'camera':
        return ('cv2', 'xxhash', 'scipy')
    if row['group'] == 'sync' or (row['group'] == 'content' and row['modality'] == 'pose'):
        return ('scipy',)
    return ()


def selected(episode, row, params):
    names = params.get('channels')
    if names is not None:
        if len(names) != len(set(names)):
            raise ValueError('channels must contain unique references')
        from edc.algorithms.channel_names import resolve_channel
        channels = [episode.channels[resolve_channel(episode, name)] for name in names]
        if row['modality'] != 'multimodal' and any(family(c) != row['modality'] for c in channels):
            raise ValueError('Selected channels do not match indicator modality or command/feedback role')
    else:
        channels = [c for c in episode.channels.values()
                    if row['modality'] == 'multimodal' or family(c) == row['modality']]
    return channels


class IndicatorPlugin:
    def __init__(self, row):
        self.row = row
        self.spec = AlgorithmSpec(row['code'], '0.1.0', row['name'],
            'EDC contributors',
            f"Repository-owned {row['code']} indicator for {row['modality']} {row['group']} checks.",
            presets=('quality_indicators',),
            required_inputs=required_capabilities(row),
            dependencies=runtime_dependencies(row),
            parameter_schema={'type': 'object', 'additionalProperties': False, 'properties': {
                'channels': {'type': ['array', 'null'], 'default': None, 'minItems': 1,
                             'items': {'type': 'string'}},
                'thresholds': {'type': 'object', 'default': {}},
                'required_channels': {'type': 'array', 'items': {'type': 'string'}, 'default': []},
                'field_map': {'type': 'object', 'default': {}},
            }},
            data_requirements=input_requirements(row),
            input_descriptions=(input_description(row),))

    def preflight(self, episode, params):
        code = self.row['code']
        if not self.row['implemented']:
            return 'missing_input', f'Indicator implementation is not available in this release: {code}'
        try:
            channels = selected(episode, self.row, params)
        except ValueError as exc:
            return 'missing_input', str(exc)
        if not channels and code not in {'M3', 'M8'}:
            return 'missing_input', 'No matching declared channels'
        if code in {'J6', 'A6', 'M7'}:
            return 'missing_input', 'Upstream format-specific schema/scale evidence has no verified IR mapping; registered only'
        if code in {'C8', 'M5', 'M11'}:
            return 'missing_input', 'Requires separately mapped sensor and receipt clocks; no verified dual-clock adapter yet'
        if code == 'C6':
            return 'missing_input', 'Requires original compressed frame headers; decoded/re-encoded RGB cannot substitute'
        if code == 'C7' and episode.format not in {'ros1_bag', 'ros2_bag'}:
            return 'missing_input', 'Upstream camera_info pairing only covers ROS bag formats'
        if code == 'M3' and not params.get('required_channels'):
            return 'missing_input', 'Declare required_channels explicitly'
        if code == 'M3' and params.get('channels') is not None:
            if set(params['required_channels']) - {c.name for c in channels}:
                return 'missing_input', 'required_channels must be included in selected channels'
        # The shared contract cannot expand a deferred modality scope itself.
        # Once selected here, apply exactly the same gates as an explicit list.
        if params.get('channels') is None and channels:
            from edc.algorithms.contracts import evaluate_requirements
            checks = evaluate_requirements(self.spec, episode,
                                           {**params, 'channels': [c.name for c in channels]})
            reasons = [reason for check in checks if check['status'] == 'missing_input'
                       for reason in check['reasons']]
            if reasons:
                return 'missing_input', '; '.join(reasons)
        timed = self.row['group'] in {'timing', 'dedup', 'sync'} or self.row['group'] == 'content'
        if timed:
            from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
            for c in channels:
                if (c.time_source not in {'acquisition', 'container'} or c.timestamps is None
                        or len(c.timestamps) != c.count or c.count < 3
                        or timing_exclusion_reason(c.name, c)):
                    return 'missing_input', 'Recorded, independently sampled timeline required: ' + c.name
        if self.row['group'] == 'sync':
            if (len(channels) < 2 or any(c.time_source != 'acquisition' or not c.clock for c in channels)
                    or len({c.clock for c in channels}) != 1
                    or any(c.time_evidence.get('timeline_scope') == 'shared_dataset_rows' for c in channels)):
                return 'missing_input', 'Independent acquisition channels in one explicit clock required'
        if self.row['group'] == 'content':
            from .bridge import field_mapping
            for c in channels:
                if (any(not math.isfinite(t) for t in c.timestamps)
                        or any(b <= a for a, b in zip(c.timestamps, c.timestamps[1:]))):
                    return 'missing_input', 'Content physics requires finite increasing recorded time; no fallback cadence: ' + c.name
                if family(c) == 'camera':
                    if not c.image_available:
                        return 'missing_input', 'RGB decoding unavailable: ' + c.name
                else:
                    if not c.value_available:
                        return 'missing_input', 'Numeric values unavailable: ' + c.name
                    if c.calibration_status != 'confirmed':
                        return 'needs_calibration', 'Confirm physical threshold calibration: ' + c.name
                    from edc.data.ir.units import require_si_field
                    try:
                        mapping = field_mapping(c, params)
                    except ValueError as exc:
                        return 'missing_input', str(exc)
                    for target, (field, unit) in mapping.items():
                        require_si_field(episode, c.name, field, unit)
        return 'runnable', ''

    def availability(self, episode, params):
        return {'availability': 'available', 'assessment_complete': False,
                'scope': 'repository-owned indicator execution; result acceptance is tracked separately',
                'reason': 'Runs the declared indicator on the selected channels; quality judgment remains in the report.',
                'inputs': [c.name for c in selected(episode, self.row, params)]}

    def input_bindings(self, episode, params):
        """Record default scope as well as explicitly selected source channels."""
        from edc.algorithms.channel_names import channel_parameter_bindings
        if params.get('channels') is not None:
            return []  # The shared binder already preserves the requested names.
        names = [c.name for c in selected(episode, self.row, params)]
        if not names:
            return []
        return [{**row, 'selection': 'default_modality_scope'} for row in
                channel_parameter_bindings(episode, {'channels': names})]

    def run(self, context, params):
        status, reason = self.preflight(context.episode, params)
        if status != 'runnable':
            raise ValueError(reason)
        from .execute import execute
        return execute(self, context, params)


for _code, _row in ROWS.items():
    globals()[_code] = IndicatorPlugin(_row)


def plugins():
    """Return all repository-owned indicator registrations in inventory order."""
    return [globals()[code] for code in ROWS]
