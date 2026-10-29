"""指标↔参数映射 + yaml 检测项双射表（全部以注册表 code 为键）。

- INDICATOR_ITEMS: yaml 检测项名 → (组, (codes...))，供 loader 加载期校验
- all_indicator_params(): {code: [(参数, 组), ...]}，聚合各 filter 模块声明
- validate_mapping(): 完整性校验（参数无孤儿/指标有参数/键是注册表 code），
  由 config_dump 与测试显式调用（不再 import 即执行）
"""
from typing import Dict, List, Tuple

from .registry import Indicator, _FAMILY_SEMANTIC

# yaml filters.<组>.<检测项> → (组, codes)。检测项归属唯一权威（反查注册表）。
_FAM = lambda key: tuple(_FAMILY_SEMANTIC[key])  # noqa: E731
INDICATOR_ITEMS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    # timing
    "drop": ("timing", _FAM("drop")),
    "jitter": ("timing", _FAM("jitter")),
    "out_of_order": ("timing", _FAM("out_of_order")),
    "delay": ("timing", ("C8", "M5")),
    "silence": ("timing", _FAM("drop")),
    "frequency_deviation": ("timing", _FAM("jitter")),
    "timestamp_duplicate": ("timing", _FAM("duplicate")),
    "timestamp_validity": ("timing", ("C8",)),
    # sync
    "coverage": ("sync", ("M2",)),
    "alignment": ("sync", ("M1",)),
    "stability": ("sync", ("M6",)),
    # content
    "black_screen": ("content", ("C5",)),
    "bad_frame_summary": ("content", ("C5",)),   # C5 全族坏帧汇总分级
    "occlusion": ("content", ("C5",)),
    "garbled": ("content", ("C5",)),
    "overexpo": ("content", ("C5",)),
    "underexpo": ("content", ("C5",)),
    "color_anomaly": ("content", ("C5",)),
    "blur": ("content", ("C9",)),
    "resolution_change": ("content", ("C6",)),       # 分辨率/编码突变（层1 压缩域）
    "freeze": ("content", ("C10",)),
    "joint_freeze": ("content", ("J7",)),
    "imu_freeze": ("content", ("I7",)),
    "cmd_freeze": ("content", ("A7",)),
    "pose": ("content", ("P5", "P7")),
    "joint_anomaly": ("content", ("J5",)),
    "cmd_anomaly": ("content", ("A5",)),
    "imu_anomaly": ("content", ("I5",)),
    "bias": ("content", ("I6",)),
    # structure
    "file_integrity": ("structure", ("M8",)),
    "lerobot_integrity": ("structure", ("M8",)),
    "scale_change": ("structure", ("M7",)),
    "camera_info_pairing": ("structure", ("C7",)),
    "camera_symmetry": ("structure", ("C7",)),
    "rgbd_pairing": ("structure", ("C7",)),
    "topic_completeness": ("structure", ("M3",)),
    "unit_convention": ("structure", ("J6", "A6")),   # 单位/坐标约定（schema 派生）
    "metadata_completeness": ("structure", ("M8",)),
    "format_recognition": ("structure", ("M8",)),
    "schema_stability": ("structure", ("M7",)),   # 字段稳定性采样（M7 的解包部分）
    # pipeline（M10 聚合参数，非 dataclass 组；M10 切段聚合归编排层）
    "episode_consistency": ("pipeline", ("M10",)),
    # dedup
    "duplicate": ("dedup", _FAM("duplicate")),
}

# 指标级开关 code → (Config 字段, 组)：indicators:{code:false} 的接线表
CODE_SWITCH_FIELDS: Dict[str, Tuple[str, str]] = {
    **{code: ("drop_enabled", "timing") for code in _FAMILY_SEMANTIC["drop"]},
    **{code: ("jitter_enabled", "timing") for code in _FAMILY_SEMANTIC["jitter"]},
    **{code: ("out_of_order_enabled", "timing") for code in _FAMILY_SEMANTIC["out_of_order"]},
    "C8": ("delay_enabled", "timing"),
    "M5": ("delay_enabled", "timing"),
    "M1": ("alignment_enabled", "sync"),
    "M2": ("coverage_enabled", "sync"),
    "M6": ("stability_enabled", "sync"),
    **{code: ("enabled", "dedup") for code in _FAMILY_SEMANTIC["duplicate"]},
}

# 无参数指标白名单（纯结构判定：C6/C7/J6/A6/M3——无可调阈值，声明存在即可）
NO_PARAM_INDICATORS = {"C6", "C7", "J6", "A6", "M3"}

# pipeline 组参数（runner 的 M10 聚合，非 dataclass）
PIPELINE_INDICATOR_PARAMS: Dict[str, List[str]] = {
    "M10": ["episode_min_topics", "episode_window_ms", "episode_merge_sec"],
}

# 各 filter 模块 → (组, INDICATOR_PARAMS 声明)
def _module_tables():
    from ..filters import timing_filter, sync_filter, dedup_filter, structure_filter
    from ..filters.content_checks import model as _cm
    return [(timing_filter, "timing"), (sync_filter, "sync"),
            (structure_filter, "structure"), (_cm, "content"), (dedup_filter, "dedup")]


def system_params() -> Dict[str, str]:
    """系统段参数（_ 开头键：采样/执行开关）→ {参数: 组}，不挂单一指标。"""
    out: Dict[str, str] = {}
    for mod, grp in _module_tables():
        for key, params in getattr(mod, "INDICATOR_PARAMS", {}).items():
            if key.startswith("_"):
                for p in params:
                    out.setdefault(p, grp)
    return out


def all_indicator_params() -> Dict[str, List[Tuple[str, str]]]:
    """聚合全部映射 → {code: [(参数, 组), ...]}（族参数复制到每个成员 code）。"""
    merged: Dict[str, List[Tuple[str, str]]] = {}
    for mod, grp in _module_tables():
        for key, params in getattr(mod, "INDICATOR_PARAMS", {}).items():
            codes = _resolve_key(key, grp)
            if not codes:
                continue
            for code in codes:
                merged.setdefault(code, [])
                for p in params:
                    if (p, grp) not in merged[code]:
                        merged[code].append((p, grp))
    for key, params in PIPELINE_INDICATOR_PARAMS.items():
        merged.setdefault(key, [])
        for p in params:
            if (p, "pipeline") not in merged[key]:
                merged[key].append((p, "pipeline"))
    return merged


def _resolve_key(key: str, grp: str) -> Tuple[str, ...]:
    """键 → codes。优先检测项名（INDICATOR_ITEMS，要求组一致）；兼容复合键/裸 code。"""
    if key.startswith("_"):
        return ()   # 系统段（_系统开关与采样 等），不参与 code 映射
    ent = INDICATOR_ITEMS.get(key)
    if ent is not None:
        if ent[0] != grp:
            raise ValueError(f"INDICATOR_PARAMS 键 '{key}' 声明在组 '{grp}'，注册表归属 '{ent[0]}'")
        return ent[1]
    codes = tuple(c for c in key.replace(")", "").split("(")[1].split("/") if c) \
        if "(" in key else (key,)
    for c in codes:
        if c not in Indicator.__members__:
            raise ValueError(f"INDICATOR_PARAMS 键 '{key}' 的 '{c}' 不在指标注册表")
    return codes


def validate_mapping() -> None:
    """完整性校验：① 每个配置字段都被某指标映射（无孤儿参数）② 每个已实现指标有参数。"""
    import dataclasses
    from ..filters.timing_filter import TimingConfig
    from ..filters.sync_filter import SyncConfig
    from ..filters.content_checks.model import ContentConfig
    from ..filters.structure_filter import StructureConfig
    from ..config.loader import RUNTIME_FIELDS
    mapped = set()
    for _ind, params in all_indicator_params().items():
        for p, _grp in params:
            mapped.add(p)
    mapped |= set(system_params())
    from ..config.loader import SWITCH_DEFAULTS
    all_fields = set()
    for cls in (TimingConfig, SyncConfig, ContentConfig, StructureConfig):
        all_fields |= {f.name for f in dataclasses.fields(cls)
                       if f.name not in RUNTIME_FIELDS and f.name not in SWITCH_DEFAULTS}
    # 采样/执行参数归系统段（允许不映射到单一指标）
    from ..config.loader import group_defaults
    sampling_keys = set(group_defaults("content")) - all_fields
    orphan = sorted(all_fields - mapped - sampling_keys)
    assert not orphan, f"未映射参数（孤儿，请加入 INDICATOR_PARAMS）: {orphan}"
    params = all_indicator_params()
    empty = [c for c, ps in params.items() if not ps and c not in NO_PARAM_INDICATORS]
    assert not empty, f"空指标映射: {empty}"
    missing = sorted(i.code for i in Indicator
                     if i.implemented and i.code not in params)
    assert not missing, f"已实现指标缺少参数映射: {missing}"


def config_dump(path: str, robot=None, profile_path=None,
                scenario=None, unpack: bool = True) -> None:
    """按指标打印：参数 → defaults 基线 → 生效值 → 覆盖来源（code 口径）。"""
    from ..config.variants import resolve_and_build
    from ..config.loader import group_defaults
    validate_mapping()
    built = resolve_and_build(path, robot=robot, profile_path=profile_path,
                              unpack=unpack, scenario=scenario)
    profile = built.get("profile")
    cfgs = {"timing": built["timing_config"], "sync": built["sync_config"],
            "content": built["content_config"], "structure": built["structure"].config}
    robot_name = profile.robot if profile is not None else "-"
    print(f"# config dump  robot={robot_name}  "
          f"format={built.get('format')}  profile={'yes' if profile else 'no'}")
    for code in sorted(all_indicator_params()):
        ind = Indicator[code]
        print(f"## [{code}] {ind.name_zh}  ({ind.group.value}/{ind.stage.value})")
        for p, grp in all_indicator_params()[code]:
            base = group_defaults(grp).get(p, "(代码兜底)")
            if grp == "dedup":
                eff = profile.threshold_overrides("dedup").get(p, base) if profile else base
            else:
                eff = getattr(cfgs.get(grp), p, base)
            src = "defaults 基线"
            if profile is not None:
                ov = profile.threshold_overrides(grp)
                if p in ov and ov[p] != base:
                    src = f"被覆盖 profile:{profile.robot}"
                elif eff != base:
                    src = "运行时参数"
            print(f"  {p:<42} = {eff}       [{src}]")
