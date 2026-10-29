"""TimingFilter - 时序过滤器模块

评估每路数据自身的时间规整性，检测掉帧/抖动/乱序/延迟/频率/静默期。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal, Any
from math import ceil
import numpy as np


@dataclass
class Gap:
    """掉帧缺口（dt 超过阈值的一段间隔）。"""
    start: float              # 缺口起点（相对秒，排序后时间轴）
    end: float                # 缺口终点（相对秒）
    duration_ms: float        # 缺口时长（毫秒）
    lost: int                 # 估算丢失帧数 = round(dt/期望周期)-1
    isolated: bool = False    # 孤立单帧缺口（lost=1 且前后间隔正常）→ 视为计时抖动，不计入丢帧率


@dataclass
class DropResult:
    """掉帧检测结果（C1/I1/J1/A1）。"""
    gap_count: int            # 缺口总数（含孤立）
    lost_frames: int          # 丢失帧总数（真实缺口，不含孤立）
    drop_ratio: float         # 丢帧率 = lost/(有效帧+lost)
    max_gap_ms: float         # 最大真实缺口（毫秒）
    max_gap_start: float      # 最大缺口起点（相对秒）
    gaps: List[Gap]           # 全部缺口（含孤立，isolated 标记）
    seq_jumps: int            # seq 跳号次数（seqs 非合成时）
    severity: Literal["pass", "warning", "error"]  # 丢帧率 2%/10% 或缺口时长 0.5s/5s 分档


@dataclass
class SilencePeriod:
    """静默期（连续空窗口）。"""
    start: float              # 静默起点（相对秒）
    end: float                # 静默终点（相对秒）
    duration_sec: float       # 静默时长（秒）


@dataclass
class SilenceResult:
    """静默期检测结果。"""
    has_silence: bool         # 是否存在静默期
    silence_periods: List[SilencePeriod]  # 全部静默段（≥silence_min_windows 空窗口）
    total_silence_sec: float  # 静默总时长（秒）
    severity: Literal["pass", "warning", "error"]  # 总时长 >2s warning / >5s error


@dataclass
class JitterResult:
    """抖动检测结果（C2/I2/J2/A2，整通道统计非段事件）。"""
    dt_mean_ms: float         # 帧间隔均值（毫秒）
    dt_std_ms: float          # 帧间隔标准差（毫秒）
    dt_p95_ms: float          # 帧间隔 P95（毫秒，最差 5%）
    dt_p99_ms: float          # 帧间隔 P99（毫秒）
    cv: float                 # 变异系数 = std/mean（频率归一）
    level: Literal["stable", "jitter", "burst"]  # stable(≤0.3) / jitter(≤0.5) / burst(>0.5)
    severity: Literal["pass", "warning", "error"]  # 三档；参与时序总体（2026-08）


@dataclass
class FrequencyDeviationResult:
    """频率偏离检测结果（实测频率 vs 期望频率）。"""
    expected_freq: float      # 期望频率（Hz，profile expected_frequencies / 数据驱动 fps）
    estimated_freq: float     # 实测频率（Hz，1/中位 dt）
    deviation_ratio: float    # 偏离比例 = (实测-期望)/期望
    is_below: bool            # 实测 < 期望×(1-容差)
    is_above: bool            # 实测 > 期望×(1+容差)
    severity: Literal["pass", "warning"]  # 偏离 ±20% → warning（无 error 档）


@dataclass
class OutOfOrderPosition:
    """乱序位置。"""
    frame_index: int          # 乱序帧索引（原始顺序中的位置）
    time: float               # 该帧时间戳（相对秒）
    backward_ms: float        # 倒退幅度（毫秒，= |前帧-本帧|）


@dataclass
class OutOfOrderResult:
    """乱序检测结果（C3/I3/J3/A3）。"""
    count: int                # 乱序点数
    max_backward_ms: float    # 最大倒退幅度（毫秒）
    positions: List[OutOfOrderPosition]  # 全部乱序位置
    severity: Literal["pass", "error"]   # 占比 ≥5% 或倒退 ≥50ms → error；1-5% → warning


@dataclass
class TimestampDuplicatePosition:
    """时间戳重复位置。"""
    time: float               # 重复时间戳（相对秒）
    consecutive: int          # 连续重复次数


@dataclass
class TimestampDuplicateResult:
    """时间戳重复检测结果（同一 stamp 多条）。"""
    count: int                # 重复段数
    max_consecutive: int      # 最长连续重复帧数
    positions: List[TimestampDuplicatePosition]  # 全部重复段
    severity: Literal["pass", "warning", "error"]  # 存在→warning；最长连次 >5 → error


@dataclass
class DelaySpikePosition:
    """延迟尖峰位置。"""
    time: float               # 尖峰时间（相对秒）
    delay_ms: float           # 延迟（毫秒）


@dataclass
class DelayResult:
    """延迟检测结果（C8/M5，receipt_time - header.stamp）。"""
    mean_ms: float            # 延迟均值（毫秒）
    p95_ms: float             # 延迟 P95（毫秒）
    p99_ms: float             # 延迟 P99（毫秒，分级依据）
    spike_count: int          # 尖峰数（>max(mean+σ×3, 100ms)）
    spike_positions: List[DelaySpikePosition]  # 尖峰位置
    severity: Literal["pass", "warning"]       # P99>100ms warning / ≥500ms error（深度通道 300ms）


@dataclass
class TimestampValidity:
    """时间戳合法性检查。"""
    all_zero: bool            # 全部时间戳为 0
    has_negative: bool        # 存在负时间戳
    scale_suspect: bool       # 量纲可疑（0<max<1e6 疑似秒非纳秒；或 >1e18 疑似溢出）
    scale_note: Optional[str] # 量纲可疑说明
    severity: Literal["pass", "warning", "error"]  # 全零/负→error；量纲可疑→warning


@dataclass
class Event:
    """时序事件（掉帧/静默/乱序/时间戳重复/延迟尖峰/抖动）。

    段事件用 start_time/end_time/duration_ms；点事件用 time。
    """
    type: str                 # 事件类型：drop/silence/out_of_order/timestamp_duplicate/delay_spike/jitter
    severity: str             # pass|warning|error（跟随检测档位；乱序/尖峰事件见各检测实现）
    start_time: Optional[float] = None  # 段事件起点（相对秒）
    end_time: Optional[float] = None    # 段事件终点（相对秒）
    time: Optional[float] = None        # 点事件时间（相对秒）
    duration_ms: Optional[float] = None # 段事件时长（毫秒）
    detail: Dict[str, Any] = field(default_factory=dict)  # 事件专属字段（lost_frames/backward_ms/consecutive/cv 等）


@dataclass
class TimingResult:
    """单 topic 时序检测总结果（时序段 format_timing_detail 的对象来源）。"""
    topic: str                # 通道名
    msg_type: str             # 原生消息类型
    total_count: int          # 消息总数
    valid_count: int          # 有效（时间戳>0）消息数
    duration_sec: float       # 通道时长（秒，末帧-首帧）
    time_range: List[float]   # [首帧, 末帧] 相对秒
    estimated_freq: float     # 实测频率（Hz）
    expected_T_ms: float      # 期望周期（毫秒，掉帧检测用）
    timestamp_validity: TimestampValidity  # 时间戳合法性
    drop: DropResult          # 掉帧
    silence: SilenceResult    # 静默
    jitter: JitterResult      # 抖动
    frequency_deviation: FrequencyDeviationResult  # 频率偏离
    out_of_order: OutOfOrderResult  # 乱序
    timestamp_duplicate: TimestampDuplicateResult  # 时间戳重复
    delay: DelayResult        # 延迟
    events: List[Event]       # 事件时间线（events_timeline 来源）
    severity: Literal["pass", "warning", "error"]  # 综合档（drop/silence/ooo/dup/delay/jitter 最高）
    expected_T_source: str = ""   # 期望周期来源：config|median_dt|median_fallback|default

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "msg_type": self.msg_type,
            "total_count": self.total_count,
            "valid_count": self.valid_count,
            "duration_sec": self.duration_sec,
            "time_range": self.time_range,
            "estimated_freq": self.estimated_freq,
            "severity": self.severity,
        }


# ---- 单一数值源：config/profiles/defaults.yaml（import 时读入并 flatten，字段默认值 = yaml 值）----



@dataclass
class TimingConfig:
    """时序检测配置"""
    # ---- 各子检测项开关（默认全开，profile 可关）----
    drop_enabled: bool = None  # 延迟默认# 丢帧/缺口检测（C1）
    silence_enabled: bool = None  # 延迟默认# 静默检测（C2 区间）
    jitter_enabled: bool = None  # 延迟默认# 抖动检测（C2）
    frequency_deviation_enabled: bool = None  # 延迟默认# 频率偏离检测（C2）
    out_of_order_enabled: bool = None  # 延迟默认# 乱序检测（C3）
    timestamp_duplicate_enabled: bool = None  # 延迟默认# 时间戳重复检测
    timestamp_validity_enabled: bool = None  # 延迟默认# 时间戳有效性检测
    delay_enabled: bool = None  # 延迟默认# 延迟/尖峰检测（C8）
    drop_gap_multiplier: float = None  # 延迟默认# 缺口判定：dt > 期望周期×该倍数（2×T 量化容忍）
    drop_ratio_low: float = None  # 延迟默认# 缺口占比 > 2% → warning
    drop_ratio_high: float = None  # 延迟默认# 缺口占比 ≥ 10% → error
    # ---- 单次缺口分档（设计表 C1：单次缺口 ≥0.5s 中档，≥5s 重档）----
    drop_gap_low_sec: float = None  # 延迟默认# 单次缺口 ≥ 0.5s → warning（C1 中档）
    drop_gap_high_sec: float = None  # 延迟默认# 单次缺口 ≥ 5s → error（C1 重档）
    # ---- 高频容忍：缺口判定取 max(2×T, 绝对下限)，消除时间戳量化误报 ----
    drop_gap_min_ms: float = None  # 延迟默认# 缺口判定绝对下限：dt < 20ms 不计缺口
    jitter_cv_low: float = None  # 延迟默认# 抖动 CV > 0.3 → warning（C2）
    jitter_cv_high: float = None  # 延迟默认# 抖动 CV ≥ 0.5 → error（C2）
    # 设计表 C2：CV≤0.3 但 P95(dt) > 该倍数×期望周期 → 附加抖动预警
    jitter_p95_multiplier: float = None  # 延迟默认# P95(dt) 超期望周期的倍数
    frequency_tolerance: float = None  # 延迟默认# 频率偏离容忍：|实测/期望-1| ≤ 20%
    # ---- 期望周期估计：无时间戳/异常时兜底与合法性范围（判定参数）----
    frequency_period_fallback_freq: float = None  # 延迟默认# 默认 30Hz（无实测 dt 时兜底）
    frequency_period_low_sec: float = None  # 延迟默认# 中位 dt 合法下界（低于视作无效→兜底）
    frequency_period_high_sec: float = None  # 延迟默认# 中位 dt 合法上界（高于视作无效→兜底）
    silence_window_sec: float = None  # 延迟默认# 静默判定窗口（秒）
    silence_min_windows: int = None  # 延迟默认# 连续该数量窗口无数据 → 静默
    silence_low_sec: float = None  # 延迟默认# 累计静默 ≥ 2s → warning
    silence_high_sec: float = None  # 延迟默认# 累计静默 ≥ 5s → error
    small_backward_threshold_ms: float = None  # 延迟默认# 时间回退 < 50ms 视为微小回退
    duplicate_consecutive_threshold: int = None  # 延迟默认# 连续重复时间戳 ≥ 5 → 重复异常
    delay_spike_sigma: float = None  # 延迟默认# 延迟尖峰：P99 + 3σ 以上
    delay_spike_absolute_ms: float = None  # 延迟默认# 延迟尖峰绝对阈值（ms）
    # ---- 判定阈值（设计表 C8/M5 分档，profile 可配）----
    delay_low_ms: float = None  # 延迟默认# P99 > 该值 → warning
    delay_high_ms: float = None  # 延迟默认# P99 ≥ 该值 → error
    # 设计表 C8：深度图像通道 P99 > 该值即异常（专用预警线；弃置仍用全局 500ms）
    delay_depth_low_ms: float = None  # 延迟默认# 深度通道专用预警线（ms）
    # ---- 乱序占比判定（设计表 C3，profile 可配）----
    out_of_order_low: float = None  # 延迟默认# 占比 > 1% → warning
    out_of_order_high: float = None  # 延迟默认# 占比 ≥ 5% → error
    # topic -> 期望频率（由 profile topics.<名>.expected_freq 注入）
    topic_expected_freq: Dict[str, float] = field(default_factory=dict)  # 按通道期望频率（Hz）
    # 全局默认期望频率（defaults 配置项）：>0 时未显式配置的 topic 用它做频率偏离基准；0 = 不启用
    topic_expected_freq_default: float = None  # 延迟默认
    # topic -> {字段: 值} 按 topic 覆盖判定阈值（profile topics.<名>.filters.timing）
    topic_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # 按 topic 的阈值覆盖



# 单一源完整性校验：defaults.yaml 该组键 == 标量字段（防止 yaml/dataclass 漂移）
    def __post_init__(self):
        from ..config.loader import apply_group_defaults
        apply_group_defaults(self, "timing")

class TimingFilter:
    """
    时序过滤器

    评估每路数据自身的时间规整性，不解 payload。
    """

    def __init__(self, config: Optional[TimingConfig] = None):
        self.config = config or TimingConfig()

    def scan(
        self,
        timestamps: List[float],
        receipt_times: Optional[List[float]] = None,
        seqs: Optional[List[int]] = None,
        topic: str = "",
        msg_type: str = "",
        expected_freq: Optional[float] = None,
    ) -> TimingResult:
        """
        执行单 topic 时序检测

        Args:
            timestamps: header.stamp 序列（相对时间）
            receipt_times: receipt_time 序列（可选）
            seqs: seq 序列（可选）
            topic: topic 名称
            msg_type: 消息类型
            expected_freq: 期望频率（可选）

        Returns:
            TimingResult: 检测结果
        """
        total_count = len(timestamps)

        if expected_freq is None:
            expected_freq = self._resolve_expected_freq(topic)

        # per-topic 阈值覆盖（profile topics.<名>.filters.timing）
        from dataclasses import replace as _dc_replace
        cfg = _dc_replace(self.config, **self.config.topic_overrides.get(topic, {}))

        # 数据准备：时序统计只用正戳（负/0 戳在时间轴上无意义）；
        # 合法性检查用全量（含 0/负 → all_zero/has_negative 分支可达，A1-4/C7）
        raw_arr = np.array(timestamps, dtype=float)
        finite = np.array([(t == t and t not in (float("inf"), float("-inf")))
                           for t in timestamps])
        valid_mask = np.array([t > 0 for t in timestamps])
        valid_stamps = raw_arr[valid_mask]
        valid_count = len(valid_stamps)

        if valid_count < 2:
            # C7: 空结果不谎报——all_zero 仅当存在时间戳且全部 <=0；valid_count 按实际
            n_finite = int(np.sum(finite))
            all_zero = n_finite > 0 and bool(np.all(raw_arr[finite] <= 0))
            has_neg = n_finite > 0 and bool(np.any(raw_arr[finite] < 0))
            return self._empty_result(
                topic, msg_type, total_count,
                valid_count=valid_count,
                all_zero=all_zero, has_negative=has_neg,
                severity="warning" if valid_count >= 1 else "error")

        # 排序（保留原始顺序用于乱序检测）
        original_order = valid_stamps.copy()
        sorted_stamps = np.sort(valid_stamps)

        # 时间戳合法性检查（全量含 0/负）
        timestamp_validity = (self._check_timestamp_validity(raw_arr[finite])
                             if cfg.timestamp_validity_enabled
                             else TimestampValidity(
                                 all_zero=False, has_negative=False,
                                 scale_suspect=False, scale_note=None,
                                 severity="pass"))

        # 估计期望周期
        expected_T, estimated_freq, expected_T_source = self._estimate_period(
            sorted_stamps, expected_freq
        )
        expected_T_ms = expected_T * 1000

        # 时间范围
        duration_sec = sorted_stamps[-1] - sorted_stamps[0]
        time_range = [float(sorted_stamps[0]), float(sorted_stamps[-1])]

        # 各项检测
        drop_result = (self._detect_drop(sorted_stamps, expected_T, seqs, cfg)
                       if cfg.drop_enabled else DropResult(0, 0, 0.0, 0.0, 0.0, [], 0, "pass"))
        silence_result = (self._detect_silence(sorted_stamps, duration_sec, cfg)
                           if cfg.silence_enabled else SilenceResult(False, [], 0.0, "pass"))
        jitter_result = (self._detect_jitter(sorted_stamps, expected_T, cfg)
                        if cfg.jitter_enabled else JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, "stable", "pass"))
        freq_result = (self._detect_frequency_deviation(
                        estimated_freq, expected_freq, cfg)
                        if cfg.frequency_deviation_enabled
                        else FrequencyDeviationResult(0.0, 0.0, 0.0, False, False, "pass"))
        ooo_result = (self._detect_out_of_order(original_order, cfg)
                        if cfg.out_of_order_enabled else OutOfOrderResult(0, 0.0, [], "pass"))
        dup_result = (self._detect_timestamp_duplicate(original_order, cfg)
                        if cfg.timestamp_duplicate_enabled else TimestampDuplicateResult(0, 0, 0.0, [], "pass"))
        delay_result = (self._detect_delay(
                        valid_stamps, receipt_times, valid_mask, timestamps, cfg,
                        topic=topic)
                        if cfg.delay_enabled else DelayResult(0.0, 0.0, 0.0, 0, [], "pass"))

        # 收集事件（jitter 为整通道统计，非段事件：检出抖动时产生单点事件）
        events = self._collect_events(
            drop_result, silence_result, ooo_result, dup_result, delay_result,
            jitter_result,
        )

        # 综合严重度（drop/silence/ooo/dup/delay/jitter 取最高档；frequency_deviation
        # 仅 warning 档且依赖期望频率注入，不参与总体）
        severity = self._judge_overall_severity(
            drop_result, silence_result, ooo_result, dup_result, delay_result,
            jitter_result,
        )

        return TimingResult(
            topic=topic,
            msg_type=msg_type,
            total_count=total_count,
            valid_count=valid_count,
            duration_sec=duration_sec,
            time_range=time_range,
            estimated_freq=estimated_freq,
            expected_T_ms=expected_T_ms,
            expected_T_source=expected_T_source,
            timestamp_validity=timestamp_validity,
            drop=drop_result,
            silence=silence_result,
            jitter=jitter_result,
            frequency_deviation=freq_result,
            out_of_order=ooo_result,
            timestamp_duplicate=dup_result,
            delay=delay_result,
            events=events,
            severity=severity,
        )

    def _resolve_expected_freq(self, topic: str) -> Optional[float]:
        """从 config.topic_expected_freq 解析期望频率（精确 + 前缀/通配匹配）；
        未命中时回退全局默认 topic_expected_freq_default（>0 才生效）。"""
        import fnmatch
        cfg = self.config.topic_expected_freq
        if topic in cfg:
            return cfg[topic]
        for t, f in cfg.items():
            if "*" in t:
                if fnmatch.fnmatch(topic, t):
                    return f
            elif topic.startswith(t) or t.startswith(topic):
                return f
        dflt = self.config.topic_expected_freq_default
        return dflt if dflt and dflt > 0 else None

    def _empty_result(
        self, topic: str, msg_type: str, total_count: int,
        valid_count: int = 0, all_zero: bool = True,
        has_negative: bool = False, severity: str = "error",
    ) -> TimingResult:
        """返回空结果（C7：字段按实际回填，不谎报全零/valid_count）"""
        return TimingResult(
            topic=topic,
            msg_type=msg_type,
            total_count=total_count,
            valid_count=valid_count,
            duration_sec=0.0,
            time_range=[0.0, 0.0],
            estimated_freq=0.0,
            expected_T_ms=0.0,
            timestamp_validity=TimestampValidity(
                all_zero=all_zero, has_negative=has_negative,
                scale_suspect=False, scale_note=None, severity="pass"
            ),
            drop=DropResult(0, 0, 0.0, 0.0, 0.0, [], 0, "pass"),
            silence=SilenceResult(False, [], 0.0, "pass"),
            jitter=JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, "stable", "pass"),
            frequency_deviation=FrequencyDeviationResult(
                0.0, 0.0, 0.0, False, False, "pass"
            ),
            out_of_order=OutOfOrderResult(0, 0.0, [], "pass"),
            timestamp_duplicate=TimestampDuplicateResult(0, 0, [], "pass"),
            delay=DelayResult(0.0, 0.0, 0.0, 0, [], "pass"),
            events=[],
            severity=severity,
        )

    def _check_timestamp_validity(
        self, stamps: np.ndarray
    ) -> TimestampValidity:
        """检查时间戳合法性"""
        all_zero = np.all(stamps == 0)
        has_negative = np.any(stamps < 0)

        max_stamp = np.max(stamps)
        scale_suspect = False
        scale_note = None

        if 0 < max_stamp < 1e6:
            scale_suspect = True
            scale_note = f"stamps in range {max_stamp:.0f}, maybe seconds not ns"
        elif max_stamp > 1e18:
            scale_suspect = True
            scale_note = "timestamps may have overflowed"

        severity = "pass"
        if all_zero or has_negative:
            severity = "error"
        elif scale_suspect:
            severity = "warning"

        return TimestampValidity(
            all_zero=all_zero,
            has_negative=has_negative,
            scale_suspect=scale_suspect,
            scale_note=scale_note,
            severity=severity,
        )

    def _estimate_period(
        self, sorted_stamps: np.ndarray, expected_freq: Optional[float]
    ) -> tuple:
        """估计期望周期

        expected_freq 只用于掉帧检测的周期阈值；estimated_freq 始终从数据
        实测（中位帧间隔），保证频率偏离检测有意义。
        """
        dt_list = np.diff(sorted_stamps)
        dt_list = dt_list[dt_list > 0]  # 过滤乱序

        if len(dt_list) == 0:
            # 兜底：配置默认频率（默认 30Hz）
            _fallback_T = 1.0 / self.config.frequency_period_fallback_freq
            measured_T, measured_freq = _fallback_T, self.config.frequency_period_fallback_freq
            measured_source = "default"
        else:
            # 中位 dt 微秒级 round，消除 float 误差（否则 dt≈2×T 边界判定随
            # config/median 来源抖动，漏判/误判缺 1 帧边界）
            measured_T = round(float(np.median(dt_list)), 6)
            if measured_T < self.config.frequency_period_low_sec:
                # 实测周期过小（异常）：回退默认频率
                measured_T = 1.0 / self.config.frequency_period_fallback_freq
                measured_source = "default"
            elif measured_T > self.config.frequency_period_high_sec:
                # 慢速通道（GPS/电量等周期 >1s，无期望频率注入时）：保留实测周期
                # 作为该通道真实周期，而非回退 30Hz——否则每个真实间隔被误判为
                # ~59 帧缺口 → drop=error + jitter=warning 系统性误报
                # （配置了 expected_freq 的通道由下方 cfg 分支用 median_fallback 兜底）
                measured_source = "median_dt_slow"
            else:
                measured_source = "median_dt"
            measured_freq = 1.0 / measured_T

        if expected_freq and expected_freq > 0:
            cfg_T = 1.0 / expected_freq
            # 配置期望频率明显偏高（实测中位 dt > 2×配置T，即实际帧率低于配置
            # 一半以上）：回退实测周期，避免掉帧系统性误报（来源记为 median_fallback）
            if measured_T > 2.0 * cfg_T:
                return measured_T, measured_freq, "median_fallback"
            return cfg_T, measured_freq, "config"
        return measured_T, measured_freq, measured_source

    def _detect_drop(
        self,
        sorted_stamps: np.ndarray,
        expected_T: float,
        seqs: Optional[List[int]],
        cfg,
    ) -> DropResult:
        """检测掉帧"""
        if expected_T <= 0:
            return DropResult(0, 0, 0.0, 0.0, 0.0, [], 0, "pass")

        # 缺口判定阈值：max(2×T, 绝对下限 ms)——高频下 2T 可能小到时间戳量化
        # 就超阈值（如 200Hz 2T=10ms），绝对下限消除系统性单帧误报
        threshold = max(expected_T * cfg.drop_gap_multiplier,
                        cfg.drop_gap_min_ms / 1000.0)
        dt_list = np.diff(sorted_stamps)
        n = len(dt_list)

        gaps = []
        lost_total = 0

        for i, dt in enumerate(dt_list):
            if dt > threshold:
                lost = round(dt / expected_T) - 1
                # 孤立单帧缺口（lost=1 且前后间隔正常）：视为计时抖动，
                # 保留定位但不计入丢帧率（不参与分级）
                isolated = (lost == 1
                            and (i == 0 or dt_list[i - 1] <= threshold)
                            and (i == n - 1 or dt_list[i + 1] <= threshold))
                gaps.append(Gap(
                    start=float(sorted_stamps[i]),
                    end=float(sorted_stamps[i + 1]),
                    duration_ms=float(dt * 1000),
                    lost=lost,
                    isolated=isolated,
                ))
                if not isolated:
                    lost_total += lost

        expected_total = len(sorted_stamps) + lost_total
        drop_ratio = lost_total / expected_total if expected_total > 0 else 0.0
        # 单次缺口分档只考虑真实（非孤立）缺口
        real_gaps = [g for g in gaps if not g.isolated]
        max_gap_ms = max([g.duration_ms for g in real_gaps]) if real_gaps else 0.0
        max_gap_start = real_gaps[0].start if real_gaps else 0.0

        # seq 跳号检测
        seq_jumps = 0
        if seqs and len(seqs) > 1 and len(seqs) == len(sorted_stamps):
            # C6: 长度一致才做（调用方负责按时间对齐）；不一致跳过，
            # 防越界（IndexError）与按位置硬配对的乱序错乱
            for i in range(1, len(sorted_stamps)):
                if int(seqs[i]) - int(seqs[i - 1]) > 1:
                    seq_jumps += 1

        # 严重度（设计表 C1：丢帧率 2%/10% 或 单次/总缺口 0.5s/5s；
        # 轻微档要求"缺口总时长<0.5s"，总缺口时长单独参与分级）
        max_gap_sec = max_gap_ms / 1000.0
        total_gap_ms = sum(g.duration_ms for g in real_gaps)
        total_gap_sec = total_gap_ms / 1000.0
        if drop_ratio >= cfg.drop_ratio_high \
                or max_gap_sec >= cfg.drop_gap_high_sec \
                or total_gap_sec >= cfg.drop_gap_high_sec:
            severity = "error"
        elif drop_ratio >= cfg.drop_ratio_low \
                or max_gap_sec >= cfg.drop_gap_low_sec \
                or total_gap_sec >= cfg.drop_gap_low_sec:
            severity = "warning"
        else:
            severity = "pass"

        return DropResult(
            gap_count=len(gaps),
            lost_frames=lost_total,
            drop_ratio=drop_ratio,
            max_gap_ms=max_gap_ms,
            max_gap_start=max_gap_start,
            gaps=gaps,
            seq_jumps=seq_jumps,
            severity=severity,
        )

    def _detect_silence(
        self, sorted_stamps: np.ndarray, duration_sec: float,
        cfg,
    ) -> SilenceResult:
        """检测静默期"""
        if duration_sec <= 0:
            return SilenceResult(False, [], 0.0, "pass")

        window_sec = cfg.silence_window_sec
        num_windows = ceil(duration_sec / window_sec)

        # 分桶
        buckets = [False] * num_windows
        for stamp in sorted_stamps:
            idx = int((stamp - sorted_stamps[0]) / window_sec)
            if 0 <= idx < num_windows:
                buckets[idx] = True

        # 找连续空窗口
        silence_periods = []
        start = None
        for i, has_data in enumerate(buckets):
            if not has_data:
                if start is None:
                    start = i
            else:
                if start is not None:
                    length = i - start
                    if length >= cfg.silence_min_windows:
                        silence_periods.append(
                            SilencePeriod(
                                start=float(sorted_stamps[0] + start * window_sec),
                                end=float(sorted_stamps[0] + i * window_sec),
                                duration_sec=float(length * window_sec),
                            )
                        )
                    start = None

        # 检查末尾
        if start is not None:
            length = num_windows - start
            if length >= cfg.silence_min_windows:
                silence_periods.append(
                    SilencePeriod(
                        start=float(sorted_stamps[0] + start * window_sec),
                        end=float(sorted_stamps[-1]),
                        duration_sec=float(length * window_sec),
                    )
                )

        total_silence = sum(p.duration_sec for p in silence_periods)

        if total_silence > cfg.silence_high_sec:
            severity = "error"
        elif total_silence > cfg.silence_low_sec:
            severity = "warning"
        else:
            severity = "pass"

        return SilenceResult(
            has_silence=len(silence_periods) > 0,
            silence_periods=silence_periods,
            total_silence_sec=total_silence,
            severity=severity,
        )

    def _detect_jitter(self, sorted_stamps: np.ndarray, expected_T: float,
                       cfg) -> JitterResult:
        """检测抖动

        expected_T：期望周期（秒），用于设计表 C2 附加判定 P95(dt)>1.5×T
        （P95 超线但 CV 未超时也给 warning 预警）。
        """
        dt_list = np.diff(sorted_stamps)
        dt_list = dt_list[dt_list > 0]

        if len(dt_list) == 0:
            return JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, "stable", "pass")

        dt_ms = dt_list * 1000
        mean = float(np.mean(dt_ms))
        std = float(np.std(dt_ms))
        p95 = float(np.percentile(dt_ms, 95))
        p99 = float(np.percentile(dt_ms, 99))
        cv = std / mean if mean > 0 else 0.0

        if cv <= cfg.jitter_cv_low:
            level = "stable"
            severity = "pass"
        elif cv <= cfg.jitter_cv_high:
            level = "jitter"
            severity = "warning"
        else:
            level = "burst"
            severity = "error"

        # 设计表 C2：CV≤0.3 但 P95(dt)>jitter_p95_multiplier×T（最差 5% 帧间隔超线）→ 附加抖动预警
        if (severity == "pass" and expected_T > 0
                and p95 > cfg.jitter_p95_multiplier * expected_T * 1000):
            level = "jitter"
            severity = "warning"

        return JitterResult(
            dt_mean_ms=mean,
            dt_std_ms=std,
            dt_p95_ms=p95,
            dt_p99_ms=p99,
            cv=cv,
            level=level,
            severity=severity,
        )

    def _detect_frequency_deviation(
        self, estimated_freq: float, expected_freq: Optional[float],
        cfg,
    ) -> FrequencyDeviationResult:
        """检测频率偏离"""
        if not expected_freq or expected_freq <= 0:
            return FrequencyDeviationResult(
                estimated_freq=estimated_freq,
                expected_freq=estimated_freq,
                deviation_ratio=0.0,
                is_below=False,
                is_above=False,
                severity="pass",
            )

        deviation = (estimated_freq - expected_freq) / expected_freq
        is_below = estimated_freq < expected_freq * (
            1 - cfg.frequency_tolerance
        )
        is_above = estimated_freq > expected_freq * (
            1 + cfg.frequency_tolerance
        )

        severity = "warning" if (is_below or is_above) else "pass"

        return FrequencyDeviationResult(
            expected_freq=expected_freq,
            estimated_freq=estimated_freq,
            deviation_ratio=deviation,
            is_below=is_below,
            is_above=is_above,
            severity=severity,
        )

    def _detect_out_of_order(
        self, original_stamps: np.ndarray,
        cfg,
    ) -> OutOfOrderResult:
        """检测乱序"""
        positions = []
        max_backward = 0.0

        for i in range(1, len(original_stamps)):
            dt = original_stamps[i] - original_stamps[i - 1]
            if dt < 0:
                backward_ms = abs(dt) * 1000
                positions.append(
                    OutOfOrderPosition(
                        frame_index=i,
                        time=float(original_stamps[i]),
                        backward_ms=backward_ms,
                    )
                )
                if backward_ms > max_backward:
                    max_backward = backward_ms

        # 分级（设计表 C3）：重档=乱序占比≥5% 或 存在大幅倒退(≥50ms)；
        # 中档=占比 1~5%；轻档=占比≤1% 且无大幅 → pass
        total = max(1, len(original_stamps))
        ratio = len(positions) / total
        if len(positions) == 0:
            severity = "pass"
        elif (ratio >= cfg.out_of_order_high
              or max_backward >= cfg.small_backward_threshold_ms):
            severity = "error"
        elif ratio > cfg.out_of_order_low:
            severity = "warning"
        else:
            severity = "pass"

        return OutOfOrderResult(
            count=len(positions),
            max_backward_ms=max_backward,
            positions=positions,
            severity=severity,
        )

    def _detect_timestamp_duplicate(
        self, original_stamps: np.ndarray,
        cfg,
    ) -> TimestampDuplicateResult:
        """检测时间戳重复"""
        positions = []
        max_consecutive = 0
        current_consecutive = 1

        for i in range(1, len(original_stamps)):
            if original_stamps[i] == original_stamps[i - 1]:
                current_consecutive += 1
            else:
                if current_consecutive > 1:
                    positions.append(
                        TimestampDuplicatePosition(
                            time=float(original_stamps[i - 1]),
                            consecutive=current_consecutive,
                        )
                    )
                    if current_consecutive > max_consecutive:
                        max_consecutive = current_consecutive
                current_consecutive = 1

        # 检查末尾
        if current_consecutive > 1:
            positions.append(
                TimestampDuplicatePosition(
                    time=float(original_stamps[-1]),
                    consecutive=current_consecutive,
                )
            )
            if current_consecutive > max_consecutive:
                max_consecutive = current_consecutive

        count = len(positions)
        if max_consecutive > cfg.duplicate_consecutive_threshold:
            severity = "error"
        elif count > 0:
            severity = "warning"
        else:
            severity = "pass"

        return TimestampDuplicateResult(
            count=count,
            max_consecutive=max_consecutive,
            positions=positions,
            severity=severity,
        )

    def _detect_delay(
        self,
        stamps: np.ndarray,
        receipt_times: Optional[List[float]],
        valid_mask: np.ndarray,
        all_timestamps: List[float],
        cfg,
        topic: str = "",
    ) -> DelayResult:
        """检测延迟"""
        if receipt_times is None:
            return DelayResult(0.0, 0.0, 0.0, 0, [], "pass")

        # 提取有效的 receipt_times
        valid_receipt = np.array(receipt_times)[valid_mask]

        if len(valid_receipt) != len(stamps):
            return DelayResult(0.0, 0.0, 0.0, 0, [], "pass")

        delays_all = (valid_receipt - stamps) * 1000
        keep = delays_all >= 0            # 过滤负值（负延迟帧不参与统计）
        delays_ms = delays_all[keep]

        if len(delays_ms) == 0:
            return DelayResult(0.0, 0.0, 0.0, 0, [], "pass")

        mean = float(np.mean(delays_ms))
        p95 = float(np.percentile(delays_ms, 95))
        p99 = float(np.percentile(delays_ms, 99))
        std = float(np.std(delays_ms))

        # 尖峰检测
        threshold = max(
            mean + cfg.delay_spike_sigma * std,
            cfg.delay_spike_absolute_ms,
        )

        spikes = []
        orig_idx = np.where(keep)[0]      # 过滤前的原始帧索引（此前误用过滤后索引，尖峰时间整体错位）
        for j, d in enumerate(delays_ms):
            if d > threshold:
                spikes.append(
                    DelaySpikePosition(
                        time=float(stamps[orig_idx[j]]),
                        delay_ms=float(d),
                    )
                )

        # 设计表 C8：深度图像通道用专用异常线（P99>300ms warning），
        # 未通过 topic_overrides 显式覆盖该 topic 的 delay_warn 时生效
        warn_threshold = cfg.delay_low_ms
        if ("depth" in (topic or "").lower()
                and "delay_low_ms" not in (cfg.topic_overrides.get(topic) or {})):
            warn_threshold = cfg.delay_depth_low_ms

        if p99 >= cfg.delay_high_ms:
            severity = "error"
        elif p99 > warn_threshold:
            severity = "warning"
        else:
            severity = "pass"

        return DelayResult(
            mean_ms=mean,
            p95_ms=p95,
            p99_ms=p99,
            spike_count=len(spikes),
            spike_positions=spikes,
            severity=severity,
        )

    def _collect_events(
        self,
        drop: DropResult,
        silence: SilenceResult,
        ooo: OutOfOrderResult,
        dup: TimestampDuplicateResult,
        delay: DelayResult,
        jitter: JitterResult,
    ) -> List[Event]:
        """收集事件"""
        events = []

        # 抖动事件（整通道统计：CV 超预警/重档线时产生单点事件）
        if jitter.severity != "pass":
            events.append(
                Event(
                    type="jitter",
                    severity=jitter.severity,
                    detail={
                        "cv": round(jitter.cv, 4),
                        "level": jitter.level,
                        "dt_mean_ms": round(jitter.dt_mean_ms, 2),
                        "dt_std_ms": round(jitter.dt_std_ms, 2),
                    },
                )
            )

        # 掉帧事件
        for gap in drop.gaps:
            events.append(
                Event(
                    type="drop",
                    severity=drop.severity,
                    start_time=gap.start,
                    end_time=gap.end,
                    duration_ms=gap.duration_ms,
                    detail={"lost_frames": gap.lost},
                )
            )

        # 静默期事件
        for period in silence.silence_periods:
            events.append(
                Event(
                    type="silence",
                    severity=silence.severity,
                    start_time=period.start,
                    end_time=period.end,
                    duration_ms=period.duration_sec * 1000,
                    detail={"duration_sec": period.duration_sec},
                )
            )

        # 乱序事件：severity 跟随通道分级（微小乱序=pass 档时事件也为 pass，
        # 不再无条件 error，避免 pass 通道带 error 事件污染下游统计/M10 聚合）
        for pos in ooo.positions:
            events.append(
                Event(
                    type="out_of_order",
                    severity=ooo.severity,
                    time=pos.time,
                    detail={"backward_ms": pos.backward_ms},
                )
            )

        # 时间戳重复事件
        for pos in dup.positions:
            events.append(
                Event(
                    type="timestamp_duplicate",
                    severity=dup.severity,
                    time=pos.time,
                    detail={"consecutive": pos.consecutive},
                )
            )

        # 延迟尖峰事件
        for pos in delay.spike_positions:
            events.append(
                Event(
                    type="delay_spike",
                    severity="warning",
                    time=pos.time,
                    detail={"delay_ms": pos.delay_ms},
                )
            )

        return events

    def _judge_overall_severity(
        self,
        drop: DropResult,
        silence: SilenceResult,
        ooo: OutOfOrderResult,
        dup: TimestampDuplicateResult,
        delay: DelayResult,
        jitter: JitterResult,
    ) -> Literal["pass", "warning", "error"]:
        """综合判定严重度（drop/silence/ooo/dup/delay/jitter 取最高档）"""
        severities = [
            drop.severity,
            silence.severity,
            ooo.severity,
            dup.severity,
            delay.severity,
            jitter.severity,
        ]

        if "error" in severities:
            return "error"
        elif "warning" in severities:
            return "warning"
        else:
            return "pass"
# ---- 指标-参数映射（参数↔指标对应；供 --config-dump / 完整性校验）----
INDICATOR_PARAMS: Dict[str, List[str]] = {
    # 键=检测项名（与 indicators.params.INDICATOR_ITEMS 一致，反查注册表得 codes）
    "drop": ["drop_enabled", "drop_gap_multiplier", "drop_ratio_low", "drop_ratio_high",
             "drop_gap_low_sec", "drop_gap_high_sec", "drop_gap_min_ms"],
    "jitter": ["jitter_enabled", "jitter_cv_low", "jitter_cv_high", "jitter_p95_multiplier"],
    "out_of_order": ["out_of_order_enabled", "out_of_order_low", "out_of_order_high",
                     "small_backward_threshold_ms"],
    "delay": ["delay_enabled", "delay_spike_sigma", "delay_spike_absolute_ms",
              "delay_low_ms", "delay_high_ms", "delay_depth_low_ms"],
    "silence": ["silence_enabled", "silence_window_sec", "silence_min_windows",
                "silence_low_sec", "silence_high_sec"],
    "frequency_deviation": ["frequency_deviation_enabled", "topic_expected_freq_default",
                            "frequency_tolerance", "frequency_period_fallback_freq",
                            "frequency_period_low_sec", "frequency_period_high_sec"],
    "timestamp_duplicate": ["timestamp_duplicate_enabled", "duplicate_consecutive_threshold"],
    "timestamp_validity": ["timestamp_validity_enabled"],
}
