"""DedupFilter - 去重过滤器模块

基于 header.stamp 或 seq 做 set 去重，检测并移除完全重复消息。
"""

from dataclasses import dataclass
from typing import List, Optional, Literal, Dict
from ..data.ir import Message

# ---- 单一数值源：config/profiles/defaults.yaml（import 时读入并 flatten，默认值 = yaml 值）----
from ..config.loader import group_defaults

_DEDUP_DEFAULTS = group_defaults("dedup")
assert "dedup_ratio_low" in _DEDUP_DEFAULTS and "dedup_ratio_high" in _DEDUP_DEFAULTS, \
    "defaults.yaml dedup 组缺 dedup_ratio_low/high"


@dataclass
class DuplicateRecord:
    """重复记录（同键多次出现）。"""
    key: str                  # 去重键类型："stamp"（时间戳）或 "seq"（序号）
    value: float              # 具体键值（重复的时间戳/seq）
    count: int                # 出现次数
    positions: List[int]      # 消息索引位置（含首次）


@dataclass
class DedupResult:
    """去重结果（C4/I4/J4/A4，dedup 段元素）。"""
    topic: str                # 通道名
    total_count: int          # 消息总数
    duplicate_count: int      # 重复消息数
    unique_count: int         # 去重后消息数
    duplicate_ratio: float    # 重复率 = duplicate/total
    duplicates: List[DuplicateRecord]  # 重复记录明细
    deduplicated_messages: List[Message]  # 去重后消息（首次出现）
    severity: Literal["pass", "info", "warning", "error"]  # <1% pass/info / <5% warning / ≥5% error（报告层归一 pass）

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "total_count": self.total_count,
            "duplicate_count": self.duplicate_count,
            "unique_count": self.unique_count,
            "duplicate_ratio": self.duplicate_ratio,
            "duplicates": [
                {
                    "key": d.key,
                    "value": d.value,
                    "count": d.count,
                    "positions": d.positions,
                }
                for d in self.duplicates
            ],
            "severity": self.severity,
        }


class DedupFilter:
    """
    去重过滤器

    检测并移除消息流中因采集/录制链路问题导致的完全重复消息。
    """

    def __init__(self, dedup_key: Literal["stamp", "seq"] = "stamp",
                 dedup_ratio_low: float = _DEDUP_DEFAULTS["dedup_ratio_low"],
                 dedup_ratio_high: float = _DEDUP_DEFAULTS["dedup_ratio_high"]):
        """
        初始化去重过滤器

        Args:
            dedup_key: 去重键类型，"stamp" 或 "seq"
            dedup_ratio_low: 重复率 < 该值 → pass/info
            dedup_ratio_high: 重复率 < 该值 → warning；否则 error
        """
        self.dedup_key = dedup_key
        self.dedup_ratio_low = dedup_ratio_low
        self.dedup_ratio_high = dedup_ratio_high

    def run(self, messages: List[Message], topic: str = "") -> DedupResult:
        """
        执行去重

        Args:
            messages: 消息列表
            topic: topic 名称

        Returns:
            DedupResult: 去重结果
        """
        total_count = len(messages)
        seen = {}  # key -> (count, first_index)
        duplicates = []
        deduped = []

        for idx, msg in enumerate(messages):
            # 获取去重键
            if self.dedup_key == "stamp":
                key = msg.timestamp
            else:
                key = msg.seq if msg.seq is not None else msg.timestamp

            if key in seen:
                # 已见过，记录重复
                count, first_idx = seen[key]
                seen[key] = (count + 1, first_idx)

                # 找到或创建重复记录
                found = False
                for dup in duplicates:
                    if dup.value == key:
                        dup.count = count + 1
                        dup.positions.append(idx)
                        found = True
                        break

                if not found:
                    duplicates.append(
                        DuplicateRecord(
                            key=self.dedup_key,
                            value=key,
                            count=count + 1,
                            positions=[first_idx, idx],
                        )
                    )
            else:
                # 首次出现
                seen[key] = (1, idx)
                deduped.append(msg)

        unique_count = len(deduped)
        duplicate_count = total_count - unique_count
        duplicate_ratio = duplicate_count / total_count if total_count > 0 else 0.0

        # 判定严重度
        severity = self._judge_severity(duplicate_ratio)

        return DedupResult(
            topic=topic,
            total_count=total_count,
            duplicate_count=duplicate_count,
            unique_count=unique_count,
            duplicate_ratio=duplicate_ratio,
            duplicates=duplicates,
            deduplicated_messages=deduped,
            severity=severity,
        )

    def run_from_series(self, timestamps: List[float], seqs: Optional[List[int]] = None,
                        topic: str = "") -> DedupResult:
        """从时间戳/seq 序列直接去重（无需构造 Message 对象，供流水线使用）

        Args:
            timestamps: 时间戳序列
            seqs: 序号序列（可选，None 时用时间戳作键）
            topic: 通道名

        Returns:
            DedupResult
        """
        keys: List = list(timestamps) if seqs is None else list(seqs)
        total = len(keys)
        seen = {}
        dup_count = 0
        duplicates = []
        for idx, k in enumerate(keys):
            if k in seen:
                first = seen[k]
                dup_count += 1
                found = next((d for d in duplicates if d.value == k), None)
                if found is None:
                    duplicates.append(DuplicateRecord(key="seq" if seqs is not None else "stamp",
                                                     value=k, count=2, positions=[first, idx]))
                else:
                    found.count += 1
                    found.positions.append(idx)
            else:
                seen[k] = idx
        unique = total - dup_count
        ratio = dup_count / total if total > 0 else 0.0
        return DedupResult(
            topic=topic, total_count=total, duplicate_count=dup_count, unique_count=unique,
            duplicate_ratio=ratio, duplicates=duplicates,
            deduplicated_messages=[], severity=self._judge_severity(ratio),
        )

    def _judge_severity(self, ratio: float) -> Literal["pass", "info", "warning", "error"]:
        """判定严重度"""
        if ratio == 0:
            return "pass"
        elif ratio <= self.dedup_ratio_low:
            return "info"          # 边界值 =0.01 保持 info（与 defaults 契约一致：>0.01 才 warning）
        elif ratio < self.dedup_ratio_high:
            return "warning"
        else:
            return "error"
# ---- 指标-参数映射（参数↔指标对应；供 --config-dump / 完整性校验）----
INDICATOR_PARAMS: Dict[str, List[str]] = {
    # 键=检测项名（与 indicators.params.INDICATOR_ITEMS 一致）
    "duplicate": ["dedup_ratio_low", "dedup_ratio_high"],
}
