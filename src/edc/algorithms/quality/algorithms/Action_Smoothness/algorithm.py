"""动作平滑度：框架怎么调用这项检查（选末端通道在这里，检查方法在 shake.py，适配在 inputs.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm
from edc.algorithms.quality.algorithms.Action_Smoothness.shake import DEFAULTS


def selected_pose(episode, params=None):
    params = params or {}
    if params.get('channel'):
        from edc.algorithms.quality.adapters.selection import ChannelSelection
        episode = ChannelSelection(episode, [params['channel']])
    if getattr(episode, '_explicit_pose_channel', None):
        return episode.channels[episode._explicit_pose_channel]
    info, reader = episode.algorithm_view()
    name, _ = reader.require_pose(info, side=(params or {}).get('side', 'right'))
    return episode.channels[name]


def number(key, text):
    return {'type': 'number', 'exclusiveMinimum': 0, 'default': DEFAULTS[key], 'description': text}


class Algorithm(BuiltinAlgorithm):
    name = '动作平滑度'
    version = 'ir-adapter-24'
    inputs = ('pose',)
    dependencies = ('scipy',)
    instance_mode = 'pose_feedback'
    weights = False
    descriptions = ('末端反馈 position 换算为 m，用该通道自身的时间（采集时间、容器时间或格式规定的帧时间）；采样率低于 min_rate_hz 时不适用。',)
    parameters = dict(
        channel={'type': 'string', 'description': '精确绑定实际末端 position topic；与 channels 二选一，不自动改为同侧另一通道'},
        side={'type': 'string', 'enum': ['left', 'right'],
              'description': '显式绑定的侧别校验；统一配置使用 arms 选择机械臂并逐臂绑定，禁止另一侧回退'},
        band_hz={'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'default': DEFAULTS['band_hz'],
                 'description': '抖动的运动频率范围（Hz，每秒来回几次）：低于下限算有意动作，上限还受 band_rate_ratio 限制'},
        band_rate_ratio=number('band_rate_ratio', '上限最多取采样率的这个倍数（30 Hz 采样 → 10 Hz，15 Hz 采样 → 6 Hz）'),
        min_rate_hz=number('min_rate_hz', '采样率低于这个值时不检查平滑度（记为不适用）'),
        still_speed_m_s=number('still_speed_m_s', '有意动作的速度低于这个值（米/秒）算停住'),
        still_min_s=number('still_min_s', '低速至少持续这么多秒才算停住'),
        still_review_m_s2=number('still_review_m_s2', '停住时抖动加速度（均方根，米/秒²）超过这个值进复核；运动时只列数值不判断'),
        freeze_min_s=number('freeze_min_s', '运动中位置读数完全不变至少这么多秒，算读数冻结（复核）'),
        freeze_speed_m_s=number('freeze_speed_m_s', '冻结前 0.1 秒的速度至少这么快（米/秒），才算“运动中”冻结'),
        gap_factor=number('gap_factor', '相邻两行相隔超过中位间隔的这个倍数，算时间断档，两边分开算'),
        edge_s=number('edge_s', '每段开头结尾各舍弃这么多秒（滤波在两端不准）'),
        min_piece_s=number('min_piece_s', '断档之间短于这么多秒的数据段不算'))

    def select(self, episode, params):
        from edc.algorithms.quality.adapters.selection import select_episode
        if params.get('channel'):
            if 'channels' in params:
                raise ValueError('Use channel or channels, not both')
            params = {**params, 'channels': [params['channel']]}
        view = select_episode(episode, params)
        if 'channels' in params and len(view.channels) != 1:
            raise ValueError('Action_Smoothness requires exactly one explicit pose channel')
        if 'channels' in params:
            c = next(iter(view.channels.values()))
            if c.modality != 'pose' or not c.value_available or 'position' not in c.fields:
                raise ValueError('Selected action channel requires readable Cartesian position')
            if c.field_spec('position')['source_kind'] != 'feedback':
                raise ValueError('Selected action channel requires explicitly declared feedback, not command/unknown')
            if 'side' in params and c.side != params['side']:
                raise ValueError('Explicit action topic conflicts with requested side; no fallback is allowed')
            view._explicit_pose_channel = c.name
        return view

    def available(self, episode, params):
        c = selected_pose(episode, params)
        return {'availability': 'available', 'scope': 'band_shake_acceleration',
                'inputs': [{'channel': c.name, 'field': 'position', 'unit': 'm',
                            'side': c.side, 'time_source': c.time_source}]}

    def preflight(self, episode, params):
        from edc.algorithms.quality.algorithms.Action_Smoothness.inputs import nominal_frame_time
        from edc.data.ir.units import require_si_field
        pose = selected_pose(episode, params)
        require_si_field(episode, pose.name, 'position', 'm')
        if (pose.timestamps is None or len(pose.timestamps) != pose.count
                or pose.time_source not in {'acquisition', 'container'} and not nominal_frame_time(pose)):
            return "missing_input", "动作平滑度需要单位明确且来源已确认的有效时间轴；未知、播放或合成时间不可替代。"
        return 'runnable', ''

    def assess(self, ep, params, context):
        params.pop('channel', None)
        from edc.algorithms.quality.algorithms.Action_Smoothness.inputs import assess
        return assess(ep, selected_pose(ep, params), params, context)
