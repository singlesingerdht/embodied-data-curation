"""时序扩展检查: 延迟/静默/频率偏离/时间戳合法性 (DF 方案)

在 stream_dt_metrics 基础上补充:
  - 延迟尖峰: P99 dt 偏差, spike 检测 (sigma > 3)
  - 静默: 连续 gap > 1s 分段
  - 频率偏离: |observed - expected| / expected > 0.2
  - 时间戳合法性: 零/负/非有限值
"""
import math
import numpy as np
from statistics import median, pstdev
from typing import List, Optional


def check_delay_spikes(timestamps: List[float],
                       spike_sigma: float = 3.0,
                       spike_absolute_ms: float = 100.0,
                       delay_low_ms: float = 100.0,
                       delay_high_ms: float = 500.0) -> dict:
    """延迟尖峰检测: P99 dt 偏差 > spike_sigma * median, 且 > spike_absolute_ms"""
    if len(timestamps) < 2:
        return {"delay_count": 0, "delay_ratio": 0.0, "p99_ms": 0.0, "has_delay": False}
    dts = [(b - a) * 1000.0 for a, b in zip(timestamps, timestamps[1:])]
    positive = [d for d in dts if d > 0]
    if not positive:
        return {"delay_count": 0, "delay_ratio": 0.0, "p99_ms": 0.0, "has_delay": False}
    med = median(positive)
    std = pstdev(positive) if len(positive) > 1 else 0.0
    threshold = max(spike_sigma * std, spike_absolute_ms)
    spikes = [d for d in positive if d - med > threshold]
    p99 = np.percentile(positive, 99) if positive else 0.0
    has = p99 > delay_high_ms or (p99 > delay_low_ms and len(spikes) > 0)
    return {
        "delay_count": len(spikes),
        "delay_ratio": round(len(spikes) / len(positive), 4) if positive else 0.0,
        "p99_ms": round(float(p99), 2),
        "has_delay": has,
        "severity": "error" if p99 > delay_high_ms else ("warning" if has else "pass"),
    }


def check_silence(timestamps: List[float],
                  window_sec: float = 1.0,
                  min_windows: int = 3,
                  silence_low_sec: float = 2.0,
                  silence_high_sec: float = 5.0) -> dict:
    """静默检测: 连续 gap > window_sec 分段, 总时长超阈值"""
    if len(timestamps) < 2:
        return {"silence_segments": [], "total_silence_sec": 0.0, "has_silence": False}
    segments = []
    start = None
    for i, (a, b) in enumerate(zip(timestamps, timestamps[1:])):
        gap = b - a
        if gap > window_sec:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_windows:
                duration = timestamps[i] - timestamps[start]
                segments.append({"start_idx": start, "end_idx": i, "duration_sec": round(duration, 3)})
            start = None
    if start is not None and len(timestamps) - 1 - start >= min_windows:
        duration = timestamps[-1] - timestamps[start]
        segments.append({"start_idx": start, "end_idx": len(timestamps) - 1, "duration_sec": round(duration, 3)})
    total = sum(s["duration_sec"] for s in segments)
    has = total > silence_low_sec
    sev = "error" if total > silence_high_sec else ("warning" if has else "pass")
    return {
        "silence_segments": segments,
        "total_silence_sec": round(total, 3),
        "has_silence": has,
        "severity": sev,
    }


def check_frequency_deviation(timestamps: List[float],
                              expected_freq: float = 0.0,
                              tolerance: float = 0.2) -> dict:
    """频率偏离检测: |observed - expected| / expected > tolerance"""
    if len(timestamps) < 2 or expected_freq <= 0:
        return {"observed_freq_hz": 0.0, "deviation": 0.0, "has_deviation": False}
    dts = [(b - a) * 1000.0 for a, b in zip(timestamps, timestamps[1:])]
    positive = [d for d in dts if d > 0]
    if not positive:
        return {"observed_freq_hz": 0.0, "deviation": 0.0, "has_deviation": False}
    med = median(positive)
    if med <= 0:
        return {"observed_freq_hz": 0.0, "deviation": 0.0, "has_deviation": False}
    obs = 1000.0 / med
    dev = abs(obs - expected_freq) / expected_freq
    return {
        "observed_freq_hz": round(obs, 2),
        "expected_freq_hz": expected_freq,
        "deviation": round(dev, 4),
        "has_deviation": dev > tolerance,
        "severity": "warning" if dev > tolerance else "pass",
    }


def check_timestamp_validity(timestamps: List[float]) -> dict:
    """时间戳合法性: 零/负/非有限"""
    zero = sum(1 for t in timestamps if t == 0)
    negative = sum(1 for t in timestamps if t < 0)
    non_finite = sum(1 for t in timestamps if not math.isfinite(t))
    total = len(timestamps) or 1
    bad = zero + negative + non_finite
    return {
        "total": len(timestamps),
        "zero_count": zero,
        "negative_count": negative,
        "non_finite_count": non_finite,
        "bad_ratio": round(bad / total, 4),
        "has_invalid": bad > 0,
        "severity": "error" if bad > 0 else "pass",
    }
