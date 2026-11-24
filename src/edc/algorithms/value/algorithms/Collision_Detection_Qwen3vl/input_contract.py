"""Safety input declarations and checks, independent of model imports."""
import json
from pathlib import Path
import re
import subprocess

import numpy as np

from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.value.runtime import environment


SIGNAL = {'type': 'object', 'additionalProperties': False, 'required': ['channel', 'field'],
          'properties': {'channel': {'type': 'string'}, 'field': {'type': 'string'},
              'indices': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 0}},
              'max_gap_sec': {'type': 'number', 'exclusiveMinimum': 0},
              'order': {'type': 'string', 'enum': ['xyzw', 'wxyz', 'rotvec']}}}
ARM = {'type': 'object', 'additionalProperties': False,
       'required': ['position', 'quaternion', 'gripper', 'terminal_effort', 'T_arm_to_camera'],
       'properties': {**{k: SIGNAL for k in ('position', 'quaternion', 'gripper', 'terminal_effort')},
                      'T_arm_to_camera': {'type': 'array', 'minItems': 4,
                          'items': {'type': 'array', 'minItems': 4, 'items': {'type': 'number'}}}}}
DEPTH = {'type': 'object', 'additionalProperties': False,
         'required': ['source', 'unit', 'registered_to_rgb', 'registration_id', 'episodes'],
         'properties': {'source': {'type': 'string', 'enum': ['monocular_estimate', 'sensor_depth']},
             'unit': {'type': 'string', 'enum': ['m', 'mm']},
             'registered_to_rgb': {'type': 'boolean', 'enum': [True]},
             'registration_id': {'type': 'string'}, 'model': {'type': 'string'},
             'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
             'episodes': {'type': 'object'}}}


def selected_frames(params, count):
    bounds = params.get('frame_range', [0, count])
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2 or
            any(type(i) is not int for i in bounds) or not 0 <= bounds[0] < bounds[1] <= count or
            bounds[1] - bounds[0] < 2):
        raise ValueError('frame_range must select at least two existing reference frames [start,end)')
    return list(range(*bounds))


def validate_configuration(episode, params):
    """Check method-specific metadata before model setup or media preparation."""
    from .task_graph import TaskGraph
    if not params['task'].strip():
        raise ValueError('A nonempty task description is required')
    if not re.fullmatch(r'cuda(?::[0-9]+)?', params['device']):
        raise ValueError('Safety requires a CUDA device such as cuda:0')
    reference = resolve_channel(episode, params['views']['faceImg'])
    selected = selected_frames(params, episode.channels[reference].count)
    if not params['arms']:
        raise ValueError('At least one calibrated arm is required')
    for arm, bindings in params['arms'].items():
        if arm not in {'left', 'right'}:
            raise ValueError('Arm keys must be left/right')
        for role in ('position', 'quaternion', 'gripper', 'terminal_effort'):
            binding = bindings[role]
            channel = episode.channels[resolve_channel(episode, binding['channel'])]
            if not channel.value_available or binding['field'] not in channel.fields:
                raise ValueError(f'{arm}.{role}: required numeric field is unavailable')
    K = np.asarray(params['intrinsics'], float)
    if (K.shape != (3, 3) or not np.isfinite(K).all() or min(K[0, 0], K[1, 1]) <= 0 or
            not np.allclose(K[2], [0, 0, 1]) or K[0, 1] != 0 or K[1, 0] != 0):
        raise ValueError('Explicit zero-skew pinhole intrinsics required')
    path = Path(params['task_graph'])
    if not path.is_file():
        raise ValueError('TaskGraph file required')
    try:
        TaskGraph.load(path)
    except (AttributeError, TypeError, KeyError, ValueError) as exc:
        raise ValueError(f'Invalid task graph: {exc}') from exc
    cfg = params['depth']
    if not cfg['registration_id'].strip():
        raise ValueError('Depth registration_id is required')
    item = cfg['episodes'].get(episode.id)
    if not isinstance(item, dict):
        raise ValueError(f'Depth input missing for episode {episode.id}')
    allowed = {'path', 'rgb_channel', 'correspondence_evidence', 'frame_range'}
    if set(item) - allowed or not (allowed - {'frame_range'}) <= set(item):
        raise ValueError('Depth episode requires path, rgb_channel and correspondence_evidence; only frame_range is optional')
    for key in ('path', 'rgb_channel', 'correspondence_evidence'):
        if not isinstance(item[key], str) or not item[key].strip():
            raise ValueError(f'Depth {key} must be a nonempty string')
    if resolve_channel(episode, item['rgb_channel']) != reference:
        raise ValueError('Depth input refers to another RGB channel')
    if item.get('frame_range', [0, episode.channels[reference].count]) != [selected[0], selected[-1]+1]:
        raise ValueError('Depth frame_range must match the selected original reference frames')
    for key in ('path', 'correspondence_evidence'):
        if not Path(item[key]).is_file():
            raise ValueError(f'Depth {key} file missing')
    if cfg['source'] == 'monocular_estimate' and not cfg.get('model'):
        raise ValueError('Monocular depth requires its model path for provenance')


def check_device(params):
    """Inspect the chosen worker's CUDA availability without loading weights."""
    code = ('import json,sys,torch; d=torch.device(sys.argv[1]); '
            'print(json.dumps(torch.cuda.is_available() and '
            '(d.index if d.index is not None else 0)<torch.cuda.device_count()))')
    try:
        result = subprocess.run([params['python'], '-c', code, params['device']], cwd='/',
            env=environment(), text=True, capture_output=True, timeout=60)
        if result.returncode or json.loads(result.stdout) is not True:
            raise ImportError(f"Worker CUDA device {params['device']} is unavailable")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise ImportError(f'Worker CUDA probe failed: {exc}') from exc


def input_bindings(episode, params):
    """Expose dynamic bindings without hard-coding the method in core dispatch."""
    rows = []
    refs = [(f'views.{role}', name, None) for role, name in params['views'].items()]
    for arm, bindings in params['arms'].items():
        refs.extend((f'arms.{arm}.{role}.channel', bindings[role]['channel'], bindings[role]['field'])
                    for role in ('position', 'quaternion', 'gripper', 'terminal_effort'))
    for parameter, requested, field in refs:
        name = resolve_channel(episode, requested); c = episode.channels[name]
        rows.append({'parameter': parameter, 'requested': requested, 'channel': name,
            'logical_name': c.logical_name, 'source': c.source, 'source_mapping': c.source_mapping,
            **({'field': field, 'metadata': c.field_spec(field)} if field else {})})
    return rows
