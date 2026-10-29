"""Profile YAML 加载与 flatten（配置加载/校验/延迟默认注入）。

- flatten_filters: filters.<组>.<检测项>.{config} → thresholds.<组>.{参数}
  （检测项归属 code/分组由 indicators 注册表反查，加载期双射校验，yaml 不写 code）
- group_defaults: defaults.yaml 某组默认值（lru_cache，Config 实例化时才读盘——
  无 import 副作用）
- apply_group_defaults: Config 字段 None 哨兵 → defaults 值；开关类字段缺省 True
- 顶层段：indicators（指标开关）/sampling（执行策略）/executor（并行调度）
"""
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any

import yaml

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
# 全局基线（非机型 profile，独立于 profiles/ 目录——避免被当成机型加载/枚举）
DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults.yaml"

# 从 defaults 移除的执行/开关类字段的代码内兜底（yaml 已不再承载；
# 指标级关断走顶层 indicators: {code: false}）
SWITCH_DEFAULTS: Dict[str, Any] = {
    # timing
    "drop_enabled": True, "jitter_enabled": True, "out_of_order_enabled": True,
    "delay_enabled": True, "silence_enabled": True,
    "frequency_deviation_enabled": True,
    "timestamp_duplicate_enabled": True, "timestamp_validity_enabled": True,
    # sync
    "coverage_enabled": True, "alignment_enabled": True, "stability_enabled": True,
    # content
    "unpack": True, "numeric_enabled": True, "image_layer1_enabled": True,
    "image_layer2": True, "image_fullframe": True, "imu_bias_detect": True,
}
# 运行时注入字段（不参与 defaults 回填）
RUNTIME_FIELDS = {"topic_expected_freq", "topic_overrides", "joint_limits",
                  "camera_defs", "disabled_codes", "home_position"}


def flatten_filters(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """filters.<组>.<检测项>.{config} → thresholds.<组>.{参数}。

    每检测项的 config 参数平铺进其组；组内参数名唯一（共享参数归属主检测项）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for group, indicators in (data.get("filters") or {}).items():
        g: Dict[str, Any] = {}
        for item_name, spec in indicators.items():
            if isinstance(spec, dict):
                cfg = spec.get("config") or {}
                if not isinstance(cfg, dict):
                    raise ValueError(
                        f"filters.{group}.{item_name}.config 必须是映射，收到 {type(cfg).__name__}")
                unknown = set(cfg) & {"code"}
                if unknown:
                    raise ValueError(
                        f"filters.{group}.{item_name} 含已废弃键 {sorted(unknown)}："
                        "code 已删除，归属由 indicators 注册表反查")
                g.update(cfg)
        out[group] = g
    return out


def validate_filter_items(data: Dict[str, Any]) -> None:
    """加载期双射校验：filters.<组>.<检测项> 必须存在于指标注册表且分组一致。

    写错检测项名/放错组直接报错（消灭 code 错标/空标类问题）。
    """
    from ..indicators.params import INDICATOR_ITEMS
    for group, items in (data.get("filters") or {}).items():
        for item_name in (items or {}):
            ent = INDICATOR_ITEMS.get(item_name)
            if ent is None:
                raise ValueError(
                    f"未知检测项 filters.{group}.{item_name}：不在指标注册表 "
                    "(indicators/params.py INDICATOR_ITEMS)，请核对检测项名")
            if ent[0] != group:
                raise ValueError(
                    f"检测项 {item_name} 属于组 '{ent[0]}'，yaml 放在 '{group}'（注册表反查不一致）")


@lru_cache(maxsize=8)
def _defaults_raw() -> Dict[str, Any]:
    p = DEFAULTS_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    validate_filter_items(data)
    return data


def group_defaults(group: str) -> Dict[str, Any]:
    """defaults.yaml 某组的全部默认值（flatten 后；含 sampling 段注入 content/structure）。"""
    data = _defaults_raw()
    g = dict(flatten_filters(data).get(group, {}))
    if group in ("content", "structure"):
        g.update(data.get("sampling") or {})
    return g


def apply_group_defaults(obj, group: str) -> None:
    """Config 延迟默认：None 哨兵字段 → defaults 值；开关字段缺省走 SWITCH_DEFAULTS。

    消除 import 副作用：Config 类定义时不再读盘，首次实例化才触发（lru_cache）。
    """
    import dataclasses
    defaults = group_defaults(group)
    for f in dataclasses.fields(obj):
        if f.name in RUNTIME_FIELDS:
            continue
        if getattr(obj, f.name, None) is None:
            if f.name in defaults:
                setattr(obj, f.name, defaults[f.name])
            elif f.name in SWITCH_DEFAULTS:
                setattr(obj, f.name, SWITCH_DEFAULTS[f.name])


def load_profile_data(path) -> Dict[str, Any]:
    """读任意 profile yaml（原始结构，含 filters；含双射校验）。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    validate_filter_items(data)
    return data


def parse_topics(data: Dict[str, Any]):
    """topics.<名> → (modality_hints, expected_frequencies, joint_limits, topic_thresholds)。

    topics 条目：{modality, expected_freq, limits, filters:{组:{检测项:{config:{...}}}}}
    - modality/expected_freq/limits → per-topic 元数据（键可含 * 通配）
    - filters → 该 topic 的检测参数覆盖（flatten 成 {组: {参数: 值}}）
    - indicators → per-topic 指标开关 {code: false}（并入 thresholds 注释见 variants）
    """
    topics = data.get("topics") or {}
    modality_hints: Dict[str, str] = {}
    expected_freq: Dict[str, float] = {}
    joint_limits: Dict[str, Any] = {}
    topic_thresholds: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for name, spec in topics.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("modality"):
            modality_hints[str(name)] = str(spec["modality"])
        if "expected_freq" in spec:
            expected_freq[str(name)] = float(spec["expected_freq"])
        if spec.get("limits"):
            joint_limits[str(name)] = dict(spec["limits"])
        ft = spec.get("filters")
        if isinstance(ft, dict):
            flat = flatten_filters({"filters": ft})
            topic_thresholds[str(name)] = {str(g): dict(v) for g, v in flat.items()}
    return modality_hints, expected_freq, joint_limits, topic_thresholds
