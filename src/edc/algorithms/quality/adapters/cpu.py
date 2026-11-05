"""内置九项算法的共同基类和调用流程（预检、选通道、运行）。各算法自己的输入准备在 algorithms/<名字>/algorithm.py。"""
import copy
from functools import lru_cache
from importlib import import_module, util
import shutil
from edc.algorithms.base import QualityAlgorithm
from edc.algorithms.registry import AlgorithmSpec
from edc.data.compat import legacy
from edc.algorithms.channel_names import resolve_channel_parameters, channel_parameter_bindings

# 内置算法的 id（algorithms/ 下的文件夹名），也是报告里的列顺序。旧的短别名由 CLI 解析。
ORDER = ('Structure_Check', 'Numeric_Check', 'Action_Smoothness', 'Check_Timestamp', 'Sync_Check',
         'Root_Cause', 'Dedup_Check', 'Reset_Detection', 'Video_Integrity')
TIMING_MISSING = ('时间条件不足：需要有效且单位明确的时间轴；跨通道检查还需要独立的原始时间轴和共同 clock。'
                '只有公共行时间、未知单位或合成时间时，不能评估原始采集同步；现有时间仍保留。')


def topic_metadata(episode):
    """Read-only recorded topic metadata (modality, msg_type, count, recorded timestamps).

    Unit/orientation wrappers deep-copy the whole legacy info on every describe_legacy() call,
    timelines included; preflight gating only reads fields those wrappers never change, so it
    uses the innermost episode's info directly. Callers must not mutate the result."""
    source = episode
    while getattr(source, '_source', None) is not None or getattr(source, '_episode', None) is not None:
        source = getattr(source, '_source', None) or source._episode
    describe = getattr(source, 'describe_legacy', None)
    return describe() if callable(describe) else None


def capabilities(episode):
    channels = list(episode.channels.values())
    caps = set()
    if getattr(episode, 'path', None) is not None:
        caps.add('dataset_file')
    if any(c.value_available for c in channels):
        caps.add("numeric")
    if any(c.value_available and c.modality == "pose" and "position" in c.fields for c in channels):
        caps.add("pose")
    if any(c.value_available and c.modality == "joint_space" and "position" in c.fields for c in channels):
        caps.add("joint")
    if any(c.image_available for c in channels):
        caps.add("video")
    timed = [c for c in channels if c.timestamps and c.time_source in {"acquisition", "container"}]
    if timed:
        caps.add("timestamps")
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    sampled = [c for c in timed if timing_exclusion_reason(c.name, c) is None
               and getattr(c, 'time_evidence', {}).get('timeline_scope') != 'shared_dataset_rows']
    if (len(sampled) >= 2 and all(c.clock for c in sampled)
            and len({c.clock for c in sampled}) == 1 and len({c.time_source for c in sampled}) == 1):
        caps.add("multi_timestamps")
    acquired = [c for c in sampled if c.time_source == "acquisition"]
    clocks = [c.clock for c in acquired]
    if len(clocks) >= 2 and all(clocks) and len(set(clocks)) == 1:
        caps.add("sync")
    return caps


def preflight(plugin, episode, params=None):
    """The executor and recommendation pool share the same isolated checks."""
    try:
        return _preflight(plugin, episode, params)
    except Exception as exc:
        from edc.data.ir.units import UnitInputError
        from edc.data.ir.metadata import IRNotProvided
        return ('missing_input' if isinstance(exc, (UnitInputError, IRNotProvided)) else
                'missing_dependency' if isinstance(exc, ImportError) else 'processing_error',
                f'{type(exc).__name__}: {exc}')


@lru_cache(maxsize=None)
def builtin(ident):
    """一个内置算法：algorithms/<ident>/algorithm.py 里的 Algorithm。"""
    return import_module(f'edc.algorithms.quality.algorithms.{ident}.algorithm').Algorithm()


def selected_inputs(episode, params, ident):
    """按参数选出本算法要看的通道（各算法的特殊规则在自己的 select）；时间类算法可换到共同 clock。"""
    algorithm = builtin(ident)
    view = algorithm.select(episode, params)
    if algorithm.timed and params.get('target_clock'):
        from edc.data.clock_view import ClockMappedEpisode
        view = ClockMappedEpisode(view, params['target_clock'])
    return view


def _preflight(plugin, episode, params=None):
    spec = plugin.spec
    if (params or {}).get('skip'):
        return 'missing_input', 'yaml 的 checks.skip 里写了不查这项'
    from edc.algorithms.contracts import resolve_parameters, evaluate_requirements
    try:
        params = resolve_parameters(spec, params)
        from edc.algorithms.contracts import bind_parameters
        params, _ = bind_parameters(spec, episode, params)
    except ValueError as exc:
        return 'missing_input', f'Invalid algorithm parameters: {exc}'
    builtin_ = isinstance(plugin, BuiltinAlgorithm)
    if builtin_:
        try:
            episode = selected_inputs(episode, params, spec.id)
        except ValueError as exc:
            return 'missing_input', str(exc)
    requirements = evaluate_requirements(spec, episode, params)
    custom = getattr(plugin, 'preflight', None)
    unmet = [r for r in requirements if r['status'] == 'missing_input']
    if unmet:
        return 'missing_input', '; '.join(reason for row in unmet for reason in row['reasons'])
    if any(r['status'] == 'deferred' for r in requirements) and not callable(custom):
        return 'missing_input', 'Select channels explicitly or provide algorithm preflight for the default scope'
    early = plugin.check_inputs(episode, params) if builtin_ else None
    if early:
        return early
    missing = set(spec.required_inputs) - capabilities(episode)
    if builtin_:
        missing = plugin.missing(episode, missing)
    if missing:
        return 'missing_input', (builtin_ and plugin.missing_reason) or 'Missing capabilities: ' + ', '.join(sorted(missing))
    from edc.data.ir.units import require_si_field
    for requirement in spec.input_requirements:
        from edc.algorithms.channel_names import resolve_channel
        name, field = resolve_channel(episode, requirement['channel']), requirement['field']
        channel = episode.channels.get(name)
        if channel is None or not channel.value_available or field not in channel.fields:
            return 'missing_input', f'Required numeric input unavailable: {name}.{field}'
        require_si_field(episode, name, field, requirement['unit'])
    # Optional algorithm-owned validation keeps model/configuration rules out of
    # the shared dispatcher. Existing plugins without a hook retain v1 behavior.
    if callable(custom):
        try:
            status, reason = custom(episode, params or {})
            if status not in {'runnable', 'missing_input', 'needs_calibration', 'missing_dependency', 'processing_error'}:
                raise ValueError(f'Invalid preflight status: {status}')
            if status != 'runnable':
                return status, reason
        except Exception as exc:
            from edc.data.ir.units import UnitInputError
            from edc.data.ir.metadata import IRNotProvided
            return ('missing_input' if isinstance(exc, (UnitInputError, IRNotProvided)) else
                    'missing_dependency' if isinstance(exc, ImportError) else 'processing_error',
                    f'{type(exc).__name__}: {exc}')
    if spec.calibration and not episode.profile.get("calibration", {}).get(spec.id):
        return "needs_calibration", "Provide algorithm-specific calibration"
    dependencies = [dep for dep in spec.dependencies if util.find_spec(dep) is None]
    if builtin_:
        dependencies += [exe for exe in plugin.executables(episode, params) if not shutil.which(exe)]
    if dependencies:
        return "missing_dependency", ", ".join(dependencies)
    if spec.device != "cpu" and not callable(custom):
        return "missing_dependency", f"Device {spec.device} requires an external executor"
    return "runnable", ""


# Used when a profile has no min_duration_s. None here (library calls, tests); `edc run` sets
# CLI_MIN_DURATION_S so every robot and input format checked from the command line gets 3 s.
DEFAULT_MIN_DURATION_S = None


def too_short(episode):
    """{'seconds', 'min_duration_s'} when the episode is shorter than min_duration_s, else None.

    The robot profile may set its own min_duration_s, or null to switch the rule off.
    Duration is the longest single channel's own span, so device-counter clocks are never mixed.
    """
    profile = getattr(episode, 'profile', None) or {}
    minimum = profile.get('min_duration_s', DEFAULT_MIN_DURATION_S)
    if minimum is None:
        return None
    if type(minimum) not in (int, float) or not minimum > 0:
        raise ValueError('min_duration_s must be a positive number of seconds')
    spans = [c.timestamps[-1] - c.timestamps[0] for c in episode.channels.values()
             if c.timestamps is not None and len(c.timestamps) > 1]
    if not spans or max(spans) >= minimum:
        return None
    return {'seconds': round(float(max(spans)), 2), 'min_duration_s': float(minimum)}


# 所有内置算法都有的参数；带原方法子项权重的、时间类的另加
COMMON_PARAMETERS = {
    'channels': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'},
        'description': '选择源 topic/Channel 名称或机器人配置的 logical_name；支持多个，缺失或解析后重复拒绝，不猜测或回退。不填按 arms 与声明的输入要求选择。'},
    'arms': {'type': ['string', 'array'], 'items': {'type': 'string'},
        'description': 'all 或已声明的机械臂部件 ID 列表；默认按机器人拓扑选择全部臂'},
    'skip': {'type': 'boolean', 'description': '由 yaml 的 checks.skip 生成：这项不查'}}
WEIGHTS = {'weights': {'type': ['object', 'null'], 'description': '原方法的子项权重；未提供时沿用原默认值'}}
TIMED_PARAMETERS = {
    'record_header_deviation_threshold_ms': {'type': ['number', 'null'], 'minimum': 0, 'default': None,
        'description': '记录时间减header原值，列中位差与最大偏离；仅显式填写时列绝对偏离超限项（明细最多20条），不改变评分或判断'},
    'target_clock': {'type': 'string',
        'description': '执行时使用的共同clock；仅应用配置中明确的仿射映射，不重采样、不修改原时间或时间来源'}}


class BuiltinAlgorithm(QualityAlgorithm):
    """内置算法的共同基类。和新算法一样有 spec、走同一套预检和注册；不同的是 run() 交回原方法的报告，
    由各自的 result.py 翻译成统一的 Item（原报告留作证据）。

    子类是 algorithms/<名字>/algorithm.py 里的 Algorithm，写下面这些（用不到的不写）：
    name 报告里的中文名；version 适配版本；inputs 需要的数据能力；dependencies 依赖库；descriptions 最低输入说明；
    parameters 自己的参数；assess() 运行一次；按需覆盖 select / check_inputs / missing / preflight / executables /
    available / instances。
    """
    name = version = ''
    inputs = dependencies = descriptions = ()
    parameters = {}
    timed = False          # 时间类：多两个参数、附录包时间证据、可换共同 clock、缺时间时统一说明
    weights = True         # 原方法有子项权重参数
    calibration = False    # 要 yaml 里的标定（复位的 home）
    checks_short = False   # 数据太短也照常检查（只有结构完整性，由它排除）
    instance_mode = 'channels'  # 按什么拆成几次运行（每条臂、每路相机……），见 robots/scope.py

    def __init__(self):
        ident = type(self).__module__.split('.')[-2]
        properties = copy.deepcopy({**COMMON_PARAMETERS, **(WEIGHTS if self.weights else {}),
                                    **(TIMED_PARAMETERS if self.timed else {}), **self.parameters})
        self.spec = AlgorithmSpec(ident, self.version + ('-evidence-scope3' if self.timed else '-evidence-scope2'),
            self.name, "Repository contributors", "Existing CPU implementation; see edc.algorithms.quality/algorithms/" + ident,
            required_inputs=self.inputs, dependencies=self.dependencies, calibration=self.calibration,
            presets=("quality_cpu", "quality"),
            parameters={k: v['description'] for k, v in properties.items()},
            parameter_schema={'type': 'object', 'properties': copy.deepcopy(properties), 'additionalProperties': False},
            input_descriptions=self.descriptions)

    input_bindings = staticmethod(channel_parameter_bindings)

    @property
    def missing_reason(self):
        """缺数据能力时的说明（时间类统一说明；其它写明缺什么）。"""
        return TIMING_MISSING if self.timed else None

    def instances(self, episode, params):
        from edc.robots.scope import instances
        return instances(episode, params, self.instance_mode)

    def select(self, episode, params):
        """按 channels / arms 选通道。"""
        from .selection import select_episode
        return select_episode(episode, params)

    def check_inputs(self, episode, params):
        """查数据能力之前的专属检查；有问题返回 (状态, 原因)。"""

    def missing(self, episode, missing):
        """调整缺少的数据能力。"""
        return missing

    def preflight(self, episode, params):
        """专属预检：参数、时间轴、标定形状等。"""
        return 'runnable', ''

    def executables(self, episode, params):
        """要用到的命令行程序。"""
        return ()

    def availability(self, episode, params):
        params = resolve_channel_parameters(episode, params)
        episode = selected_inputs(episode, params, self.spec.id)
        return self.available(episode, params) or {'inputs': [{'channel': c.name} for c in episode.channels.values()]}

    def available(self, episode, params):
        """推荐时“能查什么、只能查一部分”的说明；不写就列出选中的通道。"""

    def method(self):
        """原检查方法 method.py。"""
        return import_module(f"edc.algorithms.quality.algorithms.{self.spec.id}.method")

    def assess(self, ep, params, context):
        """运行一次，返回原方法的报告（一个或一组）。"""
        raise NotImplementedError

    def run(self, context, params):
        """The algorithm's own report(s), each with the common Item attached (quality/shared/verdicts.py)."""
        from edc.algorithms.quality.shared.verdicts import emit
        labels = (getattr(context.episode, 'profile', None) or {}).get('labels') or {}
        raw = emit(self.spec.id, self._assess(context, params), labels)
        first = raw[0] if isinstance(raw, list) else raw
        gap = first.pop('unchecked', None) or ((getattr(context.episode, 'profile', None) or {}).get('unchecked') or {}).get(self.spec.id)
        if gap:  # 格子里写明没查什么、为什么
            first['edc_note'] = '；'.join(filter(None, [first.get('edc_note'), gap]))
        return raw

    def _assess(self, context, params):
        timed = self.timed
        params = dict(params)
        threshold = params.pop('record_header_deviation_threshold_ms', None) if timed else None
        evidence = None
        if timed:
            from edc.algorithms.quality.shared.record_header import paired_time_evidence
            resolved = resolve_channel_parameters(context.episode, params)
            # Raw integer evidence survives the execution clock view unchanged.
            evidence = paired_time_evidence(selected_inputs(context.episode, resolved, self.spec.id), threshold)
        result = self._assess_core(context, params)
        if evidence is not None:
            for report in result if isinstance(result, list) else [result]:
                report['record_header_differences'] = evidence
        return result

    def _assess_core(self, context, params):
        legacy()
        short = too_short(context.episode)
        if short and not self.checks_short:
            # 结构完整性（checks_short=True）已经以此排除，其他项不必再查
            return {'score': None, 'passed': None, 'verdict': 'NOT_APPLICABLE', 'assessment_complete': True,
                    'skipped': 'too_short', 'duration': short,
                    'reasons': [f"数据只有 {short['seconds']:.1f} 秒，短于本机型要求的 {short['min_duration_s']:g} 秒，"
                                '已在结构完整性里排除，本项不检查']}
        params = resolve_channel_parameters(context.episode, params)
        ep = selected_inputs(context.episode, params, self.spec.id)
        params = {k: v for k, v in params.items() if k not in {'channels', 'target_clock'}}
        return self.assess(ep, params, context)


def builtins():
    """内置九项，按报告顺序。"""
    return [type(builtin(ident))() for ident in ORDER]
