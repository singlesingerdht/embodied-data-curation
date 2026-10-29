"""SyncFilter - 同步过滤器模块

检查多路数据之间的时间同步质量，检测覆盖/对齐/漂移/跳变。
"""

from dataclasses import dataclass, field
from . import topic_semantics
from typing import List, Dict, Optional, Literal, Any
import numpy as np
from scipy import stats


@dataclass
class LateStartTopic:
    """晚启动 topic（起点晚于公共交集起点，延迟 > 阈值）。"""
    topic: str                # 通道名
    start: float              # 该通道起点（相对秒）
    delay: float              # 相对交集起点的延迟（秒）


@dataclass
class EarlyEndTopic:
    """早停止 topic（终点早于公共交集终点，提前 > 阈值）。"""
    topic: str                # 通道名
    end: float                # 该通道终点（相对秒）
    advance: float            # 相对交集终点的提前量（秒）


@dataclass
class CoverageResult:
    """覆盖分析结果（M2 晚启动/早停止）。"""
    topic_count: int          # 参与通道数
    time_range_all: List[float]  # 全部通道时间并集 [最早起点, 最晚终点]
    intersection: List[float]    # 公共交集 [max起点, min终点]
    intersection_duration: float # 交集时长（秒）
    coverage_ratio: float        # 覆盖率 = 交集时长/最长通道时长
    late_start_topics: List[LateStartTopic]  # 晚启动通道（延迟 >5s）
    early_end_topics: List[EarlyEndTopic]    # 早停止通道（提前 >5s）
    severity: Literal["pass", "warning", "error"]  # ≥95% pass / 80-95% 或晚启早停 warning / <80% error


@dataclass
class AlignmentResult:
    """对齐分析结果（M1 多模态对不齐，主时间轴 vs 每通道最近邻时差）。"""
    mean_delta_ms: float      # 时差均值（毫秒）
    p95_delta_ms: float       # 时差 P95（毫秒，分级依据）
    p99_delta_ms: float       # 时差 P99（毫秒）
    std_delta_ms: float       # 时差标准差（毫秒）
    sample_count: int         # 采样点数（主时间轴点数）
    severity: Literal["pass", "warning", "error"]  # P95<100 pass / 100-200 warning / ≥200 error（模态对阈值作灵敏度）


@dataclass
class StabilityResult:
    """稳定性分析结果（M6 漂移/时钟跳变，时差 Δ(t) 随时间变化）。"""
    mode: Literal["fixed_offset", "drift", "jump", "unstable"]  # 固定偏移/漂移/跳变/不稳定
    offset_ms: float          # 时差均值偏移（毫秒）
    std_ms: float             # 时差标准差（毫秒）
    drift_rate_ms_per_min: float  # 漂移率（毫秒/分钟，Δ(t) 线性拟合斜率×60）
    has_jump: bool            # 是否存在跳变（相邻 Δ 突变 >30ms，且电平保持=真时钟跳变）
    jump_positions: List[float]  # 跳变时间点（相对秒）
    severity: Literal["pass", "warning", "error"]  # 跳变/漂移≥20 → error；漂移≥5 → warning
    median_interval_ms: float = 0.0   # 通道自身中位采样间隔（ms），时钟核对用
    est_freq_hz: float = 0.0          # 实际频率估计 = 1000/中位间隔
    gap_count: int = 0                # 自身时间戳缺口数（间隔 > 1.5×中位）


@dataclass
class SyncEvent:
    """同步事件（晚启动/早停止/漂移/跳变）。"""
    type: str                 # late_start/early_end/sync_drift/sync_jump
    severity: str             # warning（晚启早停）| error（漂移/跳变）
    topic: str                # 通道名
    time: float               # 事件时间（相对秒）
    end: Optional[float] = None  # 段事件终点（晚启动用）
    detail: Dict[str, Any] = field(default_factory=dict)  # delay_sec/advance_sec/drift_rate_ms_per_min/offset_ms


@dataclass
class SegmentBoundary:
    """切段点（M10 定位：晚启动/时钟跳变/结束）。"""
    time: float               # 切段时间（相对秒）
    reason: str               # start/late_start/clock_jump/end
    detail: Dict[str, Any] = field(default_factory=dict)  # {topic, delay} 等


@dataclass
class SyncResult:
    """同步检测总结果（sync 段 format_sync_detail 的对象来源）。"""
    coverage: CoverageResult      # 覆盖分析（M2）
    alignments: Dict[str, AlignmentResult]  # 各通道对齐（M1），键=通道名
    stability: Dict[str, StabilityResult]   # 各通道稳定性（M6），键=通道名
    segment_boundaries: List[SegmentBoundary]  # 切段点
    events: List[SyncEvent]       # 同步事件（events_timeline 来源）
    overall_severity: Literal["pass", "warning", "error"]  # 综合档（coverage/alignment/stability 最高）
    summary: Dict[str, int]       # {error_count, warning_count, pass_count}

    def to_dict(self) -> dict:
        return {
            "coverage": {
                "coverage_ratio": self.coverage.coverage_ratio,
                "severity": self.coverage.severity,
            },
            "alignments": {
                topic: {
                    "p95_delta_ms": a.p95_delta_ms,
                    "severity": a.severity,
                }
                for topic, a in self.alignments.items()
            },
            "segment_boundaries": [
                {"time": s.time, "reason": s.reason}
                for s in self.segment_boundaries
            ],
            "overall_severity": self.overall_severity,
            "summary": self.summary,
        }


# ---- 单一数值源：config/profiles/defaults.yaml（import 时读入并 flatten，字段默认值 = yaml 值）----



@dataclass
class SyncConfig:
    """同步检测配置"""
    # ---- 各子检测项开关（默认全开）----
    coverage_enabled: bool = None  # 延迟默认# 时间覆盖检测开关（M1）
    alignment_enabled: bool = None  # 延迟默认# 对齐偏差检测开关（M1：P95）
    stability_enabled: bool = None  # 延迟默认# 时钟稳定性/漂移检测开关
    coverage_low: float = None  # 延迟默认# 覆盖率 < 80% → error
    coverage_high: float = None  # 延迟默认# 覆盖率 < 95% → warning
    late_start_threshold_sec: float = None  # 延迟默认# 起播晚于全局起点 ≥ 5s → 起播异常
    early_end_threshold_sec: float = None  # 延迟默认# 早停早于全局终点 ≥ 5s → 早停异常
    alignment_sync_threshold_ms: Dict[str, float] = None  # 延迟默认（dict）
    stability_jump_threshold_ms: float = None  # 延迟默认# 时钟跳变阈值（ms）
    drift_low: float = None  # 延迟默认# 漂移 ≥ 5ms/min → warning
    drift_high: float = None  # 延迟默认# 漂移 ≥ 20ms/min → error
    stability_stable_threshold_ms: float = None  # 延迟默认# 稳定判定：抖动 ≤ 5ms
    # ---- M1 对齐分级（设计表 P95 三档，profile 可配）----
    alignment_low_ms: float = None  # 延迟默认# 对齐 P95 ≥ 100ms → warning
    alignment_high_ms: float = None  # 延迟默认# 对齐 P95 ≥ 200ms → error
    stability_min_samples: int = None  # 延迟默认# 稳定性评估最少样本数
    stability_cluster_window_ms: float = None  # 延迟默认# 时钟分段聚类窗口（ms）
    # topic -> {字段: 值} 按 topic 覆盖阈值（profile topics.<名>.filters.sync）
    topic_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # 按 topic 的阈值覆盖



# 单一源完整性校验：defaults.yaml 该组键 == 标量字段（防止 yaml/dataclass 漂移）
    def __post_init__(self):
        from ..config.loader import apply_group_defaults
        apply_group_defaults(self, "sync")

def _modal_of(topic: str) -> str:
    return topic_semantics.modal_of(topic)


class SyncFilter:
    """
    同步过滤器

    检查多路数据之间的时间同步质量。
    """

    def __init__(self, config: Optional[SyncConfig] = None):
        self.config = config or SyncConfig()

    def analyze(
        self,
        timestamps: Dict[str, List[float]],
        primary_topic: Optional[str] = None,
    ) -> SyncResult:
        """
        执行同步检测

        Args:
            timestamps: 各 topic 的 timestamp 序列
            primary_topic: 主时间轴 topic

        Returns:
            SyncResult: 检测结果
        """
        if not timestamps:
            return self._empty_result()

        # 选择主时间轴
        if primary_topic is None or primary_topic not in timestamps:
            primary_topic = self._select_primary_topic(timestamps)

        # 覆盖分析
        coverage = (self._analyze_coverage(timestamps)
                    if self.config.coverage_enabled
                    else CoverageResult(
                        topic_count=len(timestamps),
                        time_range_all=[0.0, 0.0], intersection=[0.0, 0.0],
                        intersection_duration=0.0, coverage_ratio=1.0,
                        late_start_topics=[], early_end_topics=[], severity="pass"))

        # 对齐分析
        alignments = (self._analyze_alignment(timestamps, primary_topic)
                      if self.config.alignment_enabled else {})

        # 稳定性分析
        stability = (self._analyze_stability(timestamps, primary_topic)
                     if self.config.stability_enabled else {})

        # 切段点检测
        segment_boundaries = self._detect_segments(
            coverage, stability, timestamps
        )

        # 收集事件
        events = self._collect_events(coverage, stability)

        # 综合判定
        overall_severity, summary = self._judge_overall(
            coverage, alignments, stability
        )

        return SyncResult(
            coverage=coverage,
            alignments=alignments,
            stability=stability,
            segment_boundaries=segment_boundaries,
            events=events,
            overall_severity=overall_severity,
            summary=summary,
        )

    def _empty_result(self) -> SyncResult:
        """返回空结果"""
        return SyncResult(
            coverage=CoverageResult(
                topic_count=0,
                time_range_all=[0.0, 0.0],
                intersection=[0.0, 0.0],
                intersection_duration=0.0,
                coverage_ratio=0.0,
                late_start_topics=[],
                early_end_topics=[],
                severity="pass",
            ),
            alignments={},
            stability={},
            segment_boundaries=[],
            events=[],
            overall_severity="pass",
            summary={"error_count": 0, "warning_count": 0, "pass_count": 0},
        )

    def _select_primary_topic(self, timestamps: Dict[str, List[float]]) -> str:
        """选择主时间轴 topic。

        优先选**样本多且自身时序稳定**的通道：自身抖动大（突发丢包）的通道作基准
        会把它自己的抖动污染到所有相对时差（实测：1kHz IMU 突发缺口 → 相机全标
        unstable）。规则：若样本最多的通道自身间隔 CV>0.5，改选 CV<=0.5 中样本最多者；
        全部不稳则选 CV 最小者。
        """
        cands = []
        for t, s in timestamps.items():
            if len(s) < 2:
                continue
            a = np.asarray(s, dtype=float)
            dt = np.diff(a) * 1000.0
            med = float(np.median(dt))
            if med <= 0:
                continue
            cv = float(np.std(dt)) / med
            cands.append((t, len(s), cv))
        if not cands:
            return max(timestamps.keys(), key=lambda t: len(timestamps[t]))
        best = max(cands, key=lambda c: c[1])  # 样本最多
        if best[2] <= 0.5:
            return best[0]
        stable = [c for c in cands if c[2] <= 0.5]
        if stable:
            return max(stable, key=lambda c: c[1])[0]
        return min(cands, key=lambda c: c[2])[0]

    def _analyze_coverage(
        self, timestamps: Dict[str, List[float]]
    ) -> CoverageResult:
        """分析时间覆盖"""
        topic_count = len(timestamps)

        # 计算各 topic 的时间范围
        ranges = {}
        for topic, stamps in timestamps.items():
            if stamps:
                ranges[topic] = [min(stamps), max(stamps)]

        if not ranges:
            return CoverageResult(
                topic_count=topic_count,
                time_range_all=[0.0, 0.0],
                intersection=[0.0, 0.0],
                intersection_duration=0.0,
                coverage_ratio=0.0,
                late_start_topics=[],
                early_end_topics=[],
                severity="pass",
            )

        all_starts = [r[0] for r in ranges.values()]
        all_ends = [r[1] for r in ranges.values()]
        all_durations = [r[1] - r[0] for r in ranges.values()]

        time_range_all = [min(all_starts), max(all_ends)]
        # 晚启动/早停止的参考点是数据集的全局起点/终点（最早开始/最晚结束），
        # 而非交集起点（交集起点=max 起点，会使 delay 恒 ≤0，检测恒为空）
        global_start = min(all_starts)
        global_end = max(all_ends)

        # 计算交集
        intersection_start = max(all_starts)
        intersection_end = min(all_ends)
        intersection_duration = max(0, intersection_end - intersection_start)

        # 覆盖率
        max_duration = max(all_durations) if all_durations else 1.0
        coverage_ratio = intersection_duration / max_duration

        # 晚启动检测
        late_start_topics = []
        for topic, (start, end) in ranges.items():
            delay = start - global_start
            if delay > self.config.late_start_threshold_sec:
                late_start_topics.append(
                    LateStartTopic(topic=topic, start=start, delay=delay)
                )

        # 早停止检测
        early_end_topics = []
        for topic, (start, end) in ranges.items():
            advance = global_end - end
            if advance > self.config.early_end_threshold_sec:
                early_end_topics.append(
                    EarlyEndTopic(topic=topic, end=end, advance=advance)
                )

        # 严重度（设计表 M2：≥95% 轻 / 80-95% 中 / <80% 重；晚启/早停归中档）
        if coverage_ratio < self.config.coverage_low:
            severity = "error"
        elif (coverage_ratio < self.config.coverage_high
              or late_start_topics or early_end_topics):
            severity = "warning"
        else:
            severity = "pass"

        return CoverageResult(
            topic_count=topic_count,
            time_range_all=time_range_all,
            intersection=[intersection_start, intersection_end],
            intersection_duration=intersection_duration,
            coverage_ratio=coverage_ratio,
            late_start_topics=late_start_topics,
            early_end_topics=early_end_topics,
            severity=severity,
        )

    @staticmethod
    def _nearest_delta_ms(primary: np.ndarray, ref: np.ndarray) -> np.ndarray:
        """primary 每个点距 ref 最近点的时差（ms），向量化 searchsorted

        O((N+M)logM) 替代逐点 argmin 的 O(N×M)。
        """
        if len(ref) == 0:
            return np.zeros(len(primary))
        ref_sorted = np.sort(ref)
        idx = np.searchsorted(ref_sorted, primary)
        idx = np.clip(idx, 1, len(ref_sorted) - 1)
        nearest = np.where(
            np.abs(ref_sorted[idx - 1] - primary) <= np.abs(ref_sorted[idx] - primary),
            ref_sorted[idx - 1], ref_sorted[idx],
        )
        return (nearest - primary) * 1000.0

    def _analyze_alignment(
        self,
        timestamps: Dict[str, List[float]],
        primary_topic: str,
    ) -> Dict[str, AlignmentResult]:
        """分析多模态对齐"""
        alignments = {}
        primary_stamps = np.array(timestamps[primary_topic])

        for topic, stamps in timestamps.items():
            if topic == primary_topic:
                continue

            if len(stamps) == 0:
                continue

            topic_stamps = np.array(stamps)
            deltas = self._nearest_delta_ms(primary_stamps, topic_stamps)

            mean_delta = float(np.mean(deltas))
            p95_delta = float(np.percentile(deltas, 95))
            p99_delta = float(np.percentile(deltas, 99))
            std_delta = float(np.std(deltas))
            # 分级幅值：带符号 P95 对负向偏移（ref 落后 primary）会拉低而漏检，
            # 分档用 |delta| 的 P95；上报同时保留带符号值（方向信息）
            p95_abs = float(np.percentile(np.abs(deltas), 95))

            # 严重度：模态对阈值作灵敏度（低于不检）；分级按 |P95| 100/200ms（设计表 M1）
            sensitivity = self._get_sync_threshold(primary_topic, topic)
            if p95_abs < sensitivity:
                severity = "pass"
            elif p95_abs >= self.config.alignment_high_ms:
                severity = "error"
            elif p95_abs >= self.config.alignment_low_ms:
                severity = "warning"
            else:
                severity = "pass"

            alignments[topic] = AlignmentResult(
                mean_delta_ms=mean_delta,
                p95_delta_ms=p95_delta,
                p99_delta_ms=p99_delta,
                std_delta_ms=std_delta,
                sample_count=len(deltas),
                severity=severity,
            )

        return alignments

    def _get_sync_threshold(self, topic1: str, topic2: str) -> float:
        """按模态对取对齐灵敏度阈值（相机 vs 各模态；无相机主时间轴走 default）"""
        m1, m2 = _modal_of(topic1), _modal_of(topic2)
        if m1 == "camera":
            key = "camera_to_camera" if m2 == "camera" else f"camera_to_{m2}"
        else:
            key = "default"
        return self.config.alignment_sync_threshold_ms.get(key,
                                                 self.config.alignment_sync_threshold_ms.get("default", 100))

    def _analyze_stability(
        self,
        timestamps: Dict[str, List[float]],
        primary_topic: str,
    ) -> Dict[str, StabilityResult]:
        """分析同步稳定性"""
        stability = {}
        primary_stamps = np.array(timestamps[primary_topic])

        for topic, stamps in timestamps.items():
            if topic == primary_topic or len(stamps) == 0:
                continue

            # per-topic 阈值覆盖（profile topics.<名>.filters.sync）
            cfg = self.config
            _ov = self.config.topic_overrides.get(topic)
            if _ov:
                from dataclasses import replace
                cfg = replace(cfg, **_ov)
            topic_stamps = np.array(stamps)

            # 计算 delta 序列（向量化最近邻）
            deltas = self._nearest_delta_ms(primary_stamps, topic_stamps)
            times = primary_stamps.astype(float)

            if len(deltas) < cfg.stability_min_samples:
                _med = float(np.median(np.diff(topic_stamps) * 1000.0)) if len(topic_stamps) > 1 else 0.0
                stability[topic] = StabilityResult(
                    mode="fixed_offset",
                    offset_ms=float(np.mean(deltas)),
                    std_ms=float(np.std(deltas)),
                    drift_rate_ms_per_min=0.0,
                    has_jump=False,
                    jump_positions=[],
                    severity="pass",
                    median_interval_ms=round(_med, 3),
                    est_freq_hz=round(1000.0 / _med, 2) if _med > 0 else 0.0,
                    gap_count=int(np.sum(np.diff(topic_stamps) * 1000.0 > 1.5 * _med)) if _med > 0 else 0,
                )
                continue

            # 检测跳变（时钟跳变=偏移**电平一步切换并保持**）。
            # ① 非重叠窗口的中位电平（消锯齿：最近邻 delta 对低速率参考呈锯齿——实测
            #    leju 200Hz IMU vs 30Hz 相机 4320 个假 sync_jump）；
            # ② 相邻窗中位差超阈值（候选）；
            # ③ 且跳变后电平保持 hold 个窗口（消锯齿重置与持续漂移：重置/漂移后电平立即
            #    继续爬升，保持失败——实测 dual_arm mcap servo 通道 ~10ms/样本漂移被误报）。
            # 注：近周期跳变（相位变化 < 阈值）受最近邻相位模糊限制，为 C3 类固有限制。
            jumps = []
            if len(deltas) >= max(cfg.stability_min_samples, 7):
                win = max(7, len(deltas) // 400)
                hold = 3
                meds = []
                med_times = []
                for i in range(0, len(deltas) - win, win):
                    meds.append(float(np.median(deltas[i:i + win])))
                    med_times.append(float(times[i + win // 2]))
                for j in range(1, len(meds) - hold):
                    if abs(meds[j] - meds[j - 1]) > cfg.stability_jump_threshold_ms:
                        if max(abs(meds[j + k] - meds[j])
                               for k in range(1, hold + 1)) <= cfg.stability_jump_threshold_ms:
                            # 缺帧缺口伪影判别：电平在 hold 窗后回归跳变前水平 →
                            # 该"跳变"是时间戳缺口造成的瞬态凸起（帧丢失），非时钟电平改变。
                            # 真时钟跳变电平会保持在新水平（|前-后|>阈值）。实测 leju 相机
                            # 157ms 缺帧缺口曾被误报为时钟跳变（error 级）。
                            if abs(meds[j - 1] - meds[j + hold]) > cfg.stability_jump_threshold_ms:
                                jumps.append(med_times[j])

            # 检测漂移
            slope, _, r_value, _, _ = stats.linregress(times, deltas)
            drift_rate = abs(slope) * 60  # 每分钟漂移

            # 判定模式（漂移两档：>warn 预警，>error 严重）
            std = float(np.std(deltas))
            if len(jumps) > 0:
                mode = "jump"
                severity = "error"
            elif drift_rate > cfg.drift_high:
                mode = "drift"
                severity = "error"
            elif drift_rate > cfg.drift_low:
                mode = "drift"
                severity = "warning"
            elif std < cfg.stability_stable_threshold_ms:
                mode = "fixed_offset"
                severity = "pass"
            else:
                mode = "unstable"
                severity = "warning"

            _med = float(np.median(np.diff(topic_stamps) * 1000.0)) if len(topic_stamps) > 1 else 0.0
            stability[topic] = StabilityResult(
                mode=mode,
                offset_ms=float(np.mean(deltas)),
                std_ms=std,
                drift_rate_ms_per_min=drift_rate,
                has_jump=len(jumps) > 0,
                jump_positions=jumps,
                severity=severity,
                median_interval_ms=round(_med, 3),
                est_freq_hz=round(1000.0 / _med, 2) if _med > 0 else 0.0,
                gap_count=int(np.sum(np.diff(topic_stamps) * 1000.0 > 1.5 * _med)) if _med > 0 else 0,
            )

        return stability

    def _detect_segments(
        self,
        coverage: CoverageResult,
        stability: Dict[str, StabilityResult],
        timestamps: Dict[str, List[float]],
    ) -> List[SegmentBoundary]:
        """检测切段点"""
        boundaries = [SegmentBoundary(time=0.0, reason="start")]

        # 检测晚启动点
        for late in coverage.late_start_topics:
            boundaries.append(
                SegmentBoundary(
                    time=late.start,
                    reason="late_start",
                    detail={"topic": late.topic, "delay": late.delay},
                )
            )

        # 检测漂移/跳变点
        for topic, stab in stability.items():
            if stab.has_jump:
                for jump_time in stab.jump_positions:
                    boundaries.append(
                        SegmentBoundary(
                            time=jump_time,
                            reason="clock_jump",
                            detail={"topic": topic},
                        )
                    )

        # 添加结束点
        if coverage.time_range_all[1] > 0:
            boundaries.append(
                SegmentBoundary(
                    time=coverage.time_range_all[1],
                    reason="end",
                )
            )

        # 按时间排序
        boundaries.sort(key=lambda b: b.time)

        return boundaries

    def _collect_events(
        self,
        coverage: CoverageResult,
        stability: Dict[str, StabilityResult],
    ) -> List[SyncEvent]:
        """收集事件"""
        events = []

        # 晚启动事件
        for late in coverage.late_start_topics:
            events.append(
                SyncEvent(
                    type="late_start",
                    severity="warning",
                    topic=late.topic,
                    time=0.0,
                    end=late.start,
                    detail={"delay_sec": late.delay},
                )
            )

        # 早停止事件
        for early in coverage.early_end_topics:
            events.append(
                SyncEvent(
                    type="early_end",
                    severity="warning",
                    topic=early.topic,
                    time=early.end,
                    detail={"advance_sec": early.advance},
                )
            )

        # 漂移/跳变事件
        for topic, stab in stability.items():
            if stab.mode == "drift":
                events.append(
                    SyncEvent(
                        type="sync_drift",
                        severity="error",
                        topic=topic,
                        time=0.0,
                        detail={"drift_rate_ms_per_min": stab.drift_rate_ms_per_min},
                    )
                )
            elif stab.mode == "jump":
                for jump_time in stab.jump_positions:
                    events.append(
                        SyncEvent(
                            type="sync_jump",
                            severity="error",
                            topic=topic,
                            time=jump_time,
                            detail={"offset_ms": stab.offset_ms},
                        )
                    )

        return events

    def _judge_overall(
        self,
        coverage: CoverageResult,
        alignments: Dict[str, AlignmentResult],
        stability: Dict[str, StabilityResult],
    ) -> tuple:
        """综合判定"""
        error_count = 0
        warning_count = 0
        pass_count = 0

        # 覆盖
        if coverage.severity == "error":
            error_count += 1
        elif coverage.severity == "warning":
            warning_count += 1
        else:
            pass_count += 1

        # 对齐
        for a in alignments.values():
            if a.severity == "error":
                error_count += 1
            elif a.severity == "warning":
                warning_count += 1
            else:
                pass_count += 1

        # 稳定性
        for s in stability.values():
            if s.severity == "error":
                error_count += 1
            elif s.severity == "warning":
                warning_count += 1
            else:
                pass_count += 1

        if error_count > 0:
            overall = "error"
        elif warning_count > 0:
            overall = "warning"
        else:
            overall = "pass"

        return overall, {
            "error_count": error_count,
            "warning_count": warning_count,
            "pass_count": pass_count,
        }
# ---- 指标-参数映射（参数↔指标对应；供 --config-dump / 完整性校验）----
INDICATOR_PARAMS: Dict[str, List[str]] = {
    # 键=检测项名（与 indicators.params.INDICATOR_ITEMS 一致）
    "coverage": ["coverage_enabled", "coverage_low", "coverage_high",
                 "late_start_threshold_sec", "early_end_threshold_sec"],
    "alignment": ["alignment_enabled", "alignment_sync_threshold_ms", "alignment_low_ms",
                  "alignment_high_ms"],
    "stability": ["stability_enabled", "stability_jump_threshold_ms", "drift_low",
                  "drift_high", "stability_stable_threshold_ms", "stability_min_samples",
                  "stability_cluster_window_ms"],
}
