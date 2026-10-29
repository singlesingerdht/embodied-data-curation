"""变体注册表：把格式变体知识做成机器可读 profile

每个「场景 × 机器人」一份 YAML（src/edc.algorithms.quality.indicator_core/config/profiles/<场景>/<机型>.yaml，
无场景覆盖时用 profiles/<机型>.yaml）。顶层结构：
- filters:           机型级全局阈值（filters.<组>.<检测项>.{code, config}，flatten 进 thresholds）
- topics.<名>:       per-topic 配置 {modality, expected_freq, limits, filters}
                       （filters 结构同顶层，只对该 topic 生效）
- required_channels: 必选通道（注入 StructureFilter 的通道完整性检查）
- cameras:           相机描述（注入相机对称/RGBD 检测）
- time_columns:      各格式的时间列（可选，备用）
- aliases:           topic 别名（可选）

检测前通过路径自动识别机器人（robot_data 目录结构 robots/<name>/...），
也可用 --robot 或 --profile 显式指定。
"""

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..data.readers import DataReader
from ..data.ir import detect_format
from ..filters.structure_filter import StructureFilter
from ..filters.timing_filter import TimingConfig
from ..filters.sync_filter import SyncConfig
from ..filters.content_checks import ContentConfig


PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


@dataclass
class VariantProfile:
    """机器可读的格式变体 profile"""
    robot: str                       # 机器人名称（机型 key，如 kuavo / r2v2）
    formats: List[str] = field(default_factory=list)  # 适用格式列表（如 rosbag / parquet）
    modality_hints: Dict[str, str] = field(default_factory=dict)  # 通道名(支持 * 通配)->规范模态（YAML: topics.<名>.modality）
    expected_frequencies: Dict[str, float] = field(default_factory=dict)  # 通道期望频率(Hz)（YAML: topics.<名>.expected_freq）
    required_channels: List[str] = field(default_factory=list)  # 必选通道，注入通道完整性检查
    joint_limits: Dict[str, Dict[str, float]] = field(default_factory=dict)  # 通道数值限位（YAML: topics.<名>.limits）
    cameras: Dict[str, Dict[str, str]] = field(default_factory=dict)  # 相机描述 {topic: {…}}
    time_columns: Dict[str, List[str]] = field(default_factory=dict)  # 各格式的时间列（可选，备用）
    aliases: Dict[str, str] = field(default_factory=dict)  # topic 别名 {别名: 标准名}
    home_position: Dict[str, list] = field(default_factory=dict)  # 通道 home 各维值 {channel: [..]}，注入位姿复位检测
    thresholds: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # 机型级全局阈值（YAML: 顶层 filters 平铺）
    topic_thresholds: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)  # per-topic 阈值 {topic: {组: {字段: 值}}}（YAML: topics.<名>.filters 平铺）
    note: str = ""                   # 备注说明
    indicators: Dict[str, bool] = field(default_factory=dict)   # 指标开关 {code|组名: bool}
    sampling: Dict[str, Any] = field(default_factory=dict)      # 执行策略（采样/层级开关）
    executor: Dict[str, Any] = field(default_factory=dict)      # 执行器参数（并行拆集等）

    @classmethod
    def load(cls, path: str) -> 'VariantProfile':
        import yaml
        from .loader import flatten_filters, parse_topics
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        rc = data.get("required_channels", [])
        # per-topic 配置统一挂 topics（modality/expected_freq/limits/filters）；
        # 兼容旧顶层键（modality_hints 等），无 topics 时回退
        mh, ef, jl, tt = parse_topics(data)
        if not mh:
            mh = data.get("modality_hints", {})
        if not ef:
            ef = data.get("expected_frequencies", {})
        if not jl:
            jl = data.get("joint_limits", {})
        if not tt:
            tt = data.get("topic_thresholds", {})
        return cls(
            robot=data.get("robot", Path(path).stem),
            formats=list(data.get("formats", [])),
            modality_hints={str(k): str(v) for k, v in mh.items()},
            expected_frequencies={str(k): float(v) for k, v in ef.items()},
            required_channels=rc if isinstance(rc, dict) else list(rc),
            joint_limits={str(k): dict(v) for k, v in jl.items()},
            cameras={str(k): dict(v) for k, v in data.get("cameras", {}).items()},
            time_columns={str(k): list(v) for k, v in data.get("time_columns", {}).items()},
            aliases={str(k): str(v) for k, v in data.get("aliases", {}).items()},
            home_position={str(k): [float(x) for x in v]
                           for k, v in (data.get("home_position") or {}).items()},
            thresholds={str(k): dict(v) for k, v in flatten_filters(data).items()},
            topic_thresholds={
                str(k): {str(g): dict(v) for g, v in tt.get(k, {}).items()}
                for k in tt
            },
            note=str(data.get("note", "")),
            indicators={str(k): bool(v) for k, v in (data.get("indicators") or {}).items()},
            sampling={str(k): v for k, v in (data.get("sampling") or {}).items()},
            executor={str(k): v for k, v in (data.get("executor") or {}).items()},
        )

    def threshold_overrides(self, group: str) -> Dict[str, Any]:
        """取某组的阈值覆盖（content/timing/sync/structure）"""
        return dict(self.thresholds.get(group, {}))

    def topic_override(self, group: str) -> Dict[str, Dict[str, Any]]:
        """取某组按 topic 的阈值覆盖：{topic: {field: value}}"""
        out: Dict[str, Dict[str, Any]] = {}
        for topic, groups in self.topic_thresholds.items():
            if group in groups:
                out[topic] = dict(groups[group])
        return out

    def match_modality(self, name: str) -> Optional[str]:
        """按通道名（精确或通配）返回模态提示"""
        if name in self.modality_hints:
            return self.modality_hints[name]
        for pat, mod in self.modality_hints.items():
            if fnmatch.fnmatch(name, pat):
                return mod
        return None

    def resolve_channel(self, name: str) -> str:
        """应用 topic 别名"""
        return self.aliases.get(name, name)

    def resolve_required(self) -> List[str]:
        return [self.resolve_channel(t) for t in self.required_channels]


def available_profiles(scenario: Optional[str] = None) -> List[str]:
    """可用机器人名（顶层 <robot>.yaml，不含 defaults 基线）"""
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def available_scenarios() -> List[str]:
    """可用场景（profiles/<scenario>/ 子目录）"""
    return sorted(d.name for d in PROFILES_DIR.iterdir() if d.is_dir())


def detect_robot(path: str) -> Optional[str]:
    """从路径识别机器人（robot_data 目录为 robots/<name>/<format>/...）"""
    known = set(available_profiles())
    if not known:
        return None
    parts = list(Path(path).parts)
    for part in reversed(parts):
        if part in known:
            return part
    # 前缀匹配：路径段作为某 profile 名的前缀（如 robots/lerobot → lerobot_example）
    for part in reversed(parts):
        matches = sorted(k for k in known if k.startswith(part + "_"))
        if matches:
            return matches[0]
    return None


def detect_scenario(path: str) -> Optional[str]:
    """从路径识别场景（profiles/<scenario>/ 子目录名出现在路径中）"""
    known = set(available_scenarios())
    if not known:
        return None
    parts = list(Path(path).parts)
    for part in reversed(parts):
        if part in known:
            return part
    return None


def _find_profile(scenario: Optional[str], robot: str) -> Optional[Path]:
    """按 (场景, 机器人) → 机器人 的顺序找 profile 文件"""
    if scenario:
        p = PROFILES_DIR / scenario / f"{robot}.yaml"
        if p.exists():
            return p
    p = PROFILES_DIR / f"{robot}.yaml"
    if p.exists():
        return p
    return None


def load_profile(robot: Optional[str], path: Optional[str] = None,
                 profile_path: Optional[str] = None,
                 scenario: Optional[str] = None) -> Optional[VariantProfile]:
    """加载 profile

    解析顺序：显式 profile_path > (场景, 机器人) > 机器人 > 路径自动识别兜底
    （resolve_and_build 在 robot 为空时按 robots/<name>/ 目录结构调 detect_robot）。
    全局基线 defaults.yaml 对所有 profile 生效：defaults → 机型 → 场景（后者覆盖前者）。
    """
    defaults = _load_defaults()
    if profile_path:
        p = Path(profile_path)
        if p.exists():
            pf = VariantProfile.load(str(p))
            return _merge_profiles(defaults, pf) if defaults is not None else pf
        # 显式指定的 profile 不存在时直接报错（修复：此前静默 return None，
        # 用户 --profile typo.yaml 后全部机型阈值悄悄失效，产出错误质检结论）
        raise FileNotFoundError(f"profile 文件不存在: {p}")
    if not robot:
        return None
    if not scenario and path:
        scenario = detect_scenario(path)
    p = _find_profile(scenario, robot)
    if p is None:
        return None
    merged = VariantProfile.load(str(p))
    if scenario:
        base_p = _find_profile(None, robot)
        if base_p is not None and str(base_p) != str(p):
            merged = _merge_profiles(VariantProfile.load(str(base_p)), merged)
    if defaults is not None:
        merged = _merge_profiles(defaults, merged)
    return merged


def _load_defaults() -> Optional[VariantProfile]:
    """全局阈值基线（config/defaults.yaml），所有 profile 叠加（路径随文件迁移修正）"""
    from .loader import DEFAULTS_PATH
    if DEFAULTS_PATH.exists():
        return VariantProfile.load(str(DEFAULTS_PATH))
    return None


def _merge_profiles(base: Optional[VariantProfile],
                    scenario: VariantProfile) -> VariantProfile:
    """场景 profile 叠加在基础机型 profile 之上（场景优先）。

    - dict 字段（modality_hints/expected_frequencies/cameras/joint_limits/
      time_columns/aliases/thresholds）：按 key 合并，场景覆盖/新增
    - list 字段（formats/required_channels）：场景非空则替换，否则用基础
    - 标量（robot/note）：场景非空则覆盖
    """
    if base is None:
        return scenario
    def _merge_dict(a, b):
        out = dict(a or {})
        for k, v in (b or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = {**out[k], **v}
            else:
                out[k] = v
        return out
    def _merge_list(a, b):
        return b if b else (a or [])
    def _merge_required(base, scenario):
        """required_channels 可为 list 或 {format: [...]}：类型一致按各自规则合并；
        类型不一致时以非空者为准（防 dict×list 碰撞 AttributeError 崩溃）"""
        if isinstance(base, list) and isinstance(scenario, list):
            return _merge_list(base, scenario)
        if isinstance(base, dict) and isinstance(scenario, dict):
            return _merge_dict(base, scenario)
        if scenario:
            return scenario
        return base
    return VariantProfile(
        robot=scenario.robot or base.robot,
        formats=_merge_list(base.formats, scenario.formats),
        modality_hints=_merge_dict(base.modality_hints, scenario.modality_hints),
        expected_frequencies=_merge_dict(base.expected_frequencies, scenario.expected_frequencies),
        required_channels=_merge_required(base.required_channels, scenario.required_channels),
        joint_limits=_merge_dict(base.joint_limits, scenario.joint_limits),
        cameras=_merge_dict(base.cameras, scenario.cameras),
        time_columns=_merge_dict(base.time_columns, scenario.time_columns),
        aliases=_merge_dict(base.aliases, scenario.aliases),
        home_position=_merge_dict(base.home_position, scenario.home_position),
        thresholds=_merge_dict(base.thresholds, scenario.thresholds),
        topic_thresholds=_merge_dict(base.topic_thresholds, scenario.topic_thresholds),
        note=scenario.note or base.note,
        indicators=_merge_dict(base.indicators, scenario.indicators),
        sampling=_merge_dict(base.sampling, scenario.sampling),
        executor=_merge_dict(base.executor, scenario.executor),
    )




def expand_disabled_codes(profile: Optional[VariantProfile]) -> set:
    """profile.indicators → 被关断的 code 集合。

    键可为 code（"C1"）或组名（"content"→该组全部已实现 code）；未知键报错。
    """
    from ..indicators.registry import Indicator, Group
    if profile is None:
        return set()
    disabled: set = set()
    for key, onoff in (profile.indicators or {}).items():
        if onoff:
            continue
        k = key.upper()
        if k in Indicator.__members__:
            disabled.add(k)
        elif k in Group.__members__:
            disabled |= {i.code for i in Indicator
                         if i.group is Group[k] and i.implemented}
        else:
            raise ValueError(
                f"indicators 开关键 '{key}' 不是注册表 code 或组名"
                f"（可用：C1..M10 / timing/sync/structure/content/dedup）")
    return disabled


def _apply_code_switches(cfg, group: str, disabled: set) -> None:
    """把 code 级开关落到对应 Config 字段（CODE_SWITCH_FIELDS 接线表）。"""
    from ..indicators.params import CODE_SWITCH_FIELDS
    for code in disabled:
        field_name, grp = CODE_SWITCH_FIELDS.get(code, (None, None))
        if grp == group and field_name and hasattr(cfg, field_name):
            setattr(cfg, field_name, False)

def _required_for(profile: Optional[VariantProfile], fmt: Optional[str]) -> List[str]:
    """取 profile 对指定格式的必选通道（required_channels 可为 list 或 {format: [...]}）"""
    if profile is None:
        return []
    rc = profile.required_channels
    if isinstance(rc, dict):
        return [profile.resolve_channel(t) for t in rc.get(fmt or "", [])]
    return [profile.resolve_channel(t) for t in rc]


def _apply_threshold_overrides(cfg, group: str, overrides: dict,
                              source: str) -> None:
    """把 profile thresholds.<group> 覆盖到配置类；未知键给出警告（防拼写错静默失效）"""
    import warnings
    for k, v in (overrides or {}).items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            warnings.warn(
                f"{source}: profile 阈值键 {group}.{k} 不是有效配置项，已忽略"
                f"（可用项见配置项总表）",
                stacklevel=3)


def build_structure_filter(profile: Optional[VariantProfile], fmt: Optional[str] = None,
                           unpack: bool = True, **kw) -> StructureFilter:
    from ..filters.structure_filter import StructureConfig
    cfg = kw.pop("config", None) or StructureConfig(unpack=unpack)
    if profile:
        _apply_threshold_overrides(cfg, "structure",
                                   profile.threshold_overrides("structure"),
                                   f"profile:{profile.robot}")
        if profile.cameras:
            cfg.camera_defs = profile.cameras
    cfg.unpack = unpack   # 运行时模式开关：显式参数优先（同 build_content_config）
    if profile:
        cfg.disabled_codes = expand_disabled_codes(profile) & {"M3", "M7", "M8", "C7"}
    required = _required_for(profile, fmt)
    if required:
        kw.setdefault("required_topics", required)
    return StructureFilter(config=cfg, **kw)


def build_timing_config(profile: Optional[VariantProfile]) -> TimingConfig:
    cfg = TimingConfig()
    if profile:
        cfg.topic_expected_freq.update(profile.expected_frequencies)
        cfg.topic_overrides.update(profile.topic_override("timing"))
        _apply_threshold_overrides(cfg, "timing",
                                   profile.threshold_overrides("timing"),
                                   f"profile:{profile.robot}")
    return cfg


def build_sync_config(profile: Optional[VariantProfile]) -> "SyncConfig":
    cfg = SyncConfig()
    if profile:
        cfg.topic_overrides.update(profile.topic_override("sync"))
        _apply_threshold_overrides(cfg, "sync",
                                   profile.threshold_overrides("sync"),
                                   f"profile:{profile.robot}")
        _apply_code_switches(cfg, "sync", expand_disabled_codes(profile))
    return cfg


def build_content_config(profile: Optional[VariantProfile], unpack: bool = True) -> ContentConfig:
    cfg = ContentConfig(unpack=unpack)
    if profile:
        cfg.joint_limits = profile.joint_limits
        cfg.topic_overrides.update(profile.topic_override("content"))
        _apply_threshold_overrides(cfg, "content",
                                   profile.threshold_overrides("content"),
                                   f"profile:{profile.robot}")
        # 顶层 sampling 段注入（执行策略：层级开关/采样帧数）
        _apply_threshold_overrides(cfg, "content", dict(profile.sampling),
                                   f"profile:{profile.robot}:sampling")
        cfg.disabled_codes = expand_disabled_codes(profile)
        if profile.home_position:
            cfg.home_position = profile.home_position
        # 数值类 code 全关 → 整组执行开关同步关（省算力）
        if {"I5", "I6", "I7", "J5", "J7", "A5", "A7", "P5", "P7"} <= cfg.disabled_codes:
            cfg.numeric_enabled = False
    # unpack 是运行时模式开关（--mode/--no-unpack），显式参数优先于 profile 覆盖
    # （修复：defaults.yaml sampling.unpack=true 曾把 mode=fast 静默盖回 deep）
    cfg.unpack = unpack
    return cfg


def build_reader(path: str, profile: Optional[VariantProfile] = None) -> DataReader:
    """构建读取器并注入模态提示与列角色（时间/seq/meta，profile 优先）"""
    hints = profile.modality_hints if profile else None
    roles = _column_roles_for(profile, detect_format(path))
    return DataReader.create(path, hints=hints, column_roles=roles)


def _column_roles_for(profile: Optional[VariantProfile], fmt: Optional[str]) -> Dict[str, List[str]]:
    """从 profile 提取列角色：time_columns（按格式）→ {"time": [...], "seq": [...], "meta": [...]}"""
    if profile is None:
        return {}
    tc = profile.time_columns
    if isinstance(tc, dict):
        cols = tc.get(fmt or "", []) or tc.get("*", [])
    else:
        cols = list(tc)
    roles: Dict[str, List[str]] = {}
    if cols:
        roles["time"] = [str(c) for c in cols]
    return roles


def resolve_and_build(path: str, robot: Optional[str] = None,
                      profile_path: Optional[str] = None,
                      unpack: bool = True,
                      scenario: Optional[str] = None) -> dict:
    """一站式：识别 profile 并构建全套过滤器，供 CLI/web 复用

    unpack=False 时关闭所有需要「解包到内存」的检测（数值内容、图像第二层、
    字段稳定性），保留纯压缩域/元数据检测（结构、时序、同步、图像第一层）。

    Returns:
        {"profile": ..., "structure": StructureFilter, "timing_config": TimingConfig,
         "content_config": ContentConfig, "reader_hints": {...}}
    """
    # 路径自动识别兜底：未显式指定 robot/profile 时按 robots/<name>/ 目录结构识别
    # （此前 detect_robot 从未接线，仅传路径时机型 profile 静默不生效）
    if not robot and not profile_path and path:
        robot = detect_robot(path)
    profile = load_profile(robot, path, profile_path, scenario=scenario)
    fmt = detect_format(path)
    disabled = expand_disabled_codes(profile)
    return {
        "profile": profile,
        "scenario": scenario or (detect_scenario(path) if path else None),
        "format": fmt,
        "unpack": unpack,
        "structure": build_structure_filter(profile, fmt, unpack=unpack),
        "timing_config": build_timing_config(profile),
        "sync_config": build_sync_config(profile),
        "content_config": build_content_config(profile, unpack=unpack),
        "reader_hints": profile.modality_hints if profile else None,
        "column_roles": _column_roles_for(profile, fmt),
        # 指标级开关展开结果（供 pipeline M10 门控 / skipped 上报）
        "disabled_codes": disabled,
    }
