"""采集同步用的起止和交集计算；check_stability 现在只给时间异常归因（Root_Cause）用，采集同步不再用它。"""
import numpy as np
from typing import Dict, List, Optional
from scipy.stats import linregress


def check_coverage(timestamps: Dict[str, List[float]],
                   coverage_low: float = 0.8,
                   coverage_high: float = 0.95,
                   late_start_sec: float = 5.0,
                   early_end_sec: float = 5.0) -> dict:
    """多通道时间覆盖: 所有通道的时间交集占比"""
    if not timestamps:
        return {"coverage_ratio": 0.0, "intersection_sec": 0.0, "total_sec": 0.0, "late_start": {}, "early_end": {}, "severity": "error"}
    ranges = [(min(ts), max(ts)) for ts in timestamps.values() if ts]
    if not ranges:
        return {"coverage_ratio": 0.0, "intersection_sec": 0.0, "total_sec": 0.0, "late_start": {}, "early_end": {}, "severity": "error"}
    global_start = min(r[0] for r in ranges)
    global_end = max(r[1] for r in ranges)
    inter_start = max(r[0] for r in ranges)
    inter_end = min(r[1] for r in ranges)
    inter_dur = max(0.0, inter_end - inter_start)
    total_dur = max(0.0, global_end - global_start)
    ratio = inter_dur / total_dur if total_dur > 0 else 0.0
    # 晚开始／早结束的话题和差了几秒
    late = {k: round(min(ts) - global_start, 3) for k, ts in timestamps.items() if ts and min(ts) - global_start > late_start_sec}
    early = {k: round(global_end - max(ts), 3) for k, ts in timestamps.items() if ts and global_end - max(ts) > early_end_sec}
    sev = "error" if ratio < coverage_low else ("warning" if ratio < coverage_high else "pass")
    return {
        "coverage_ratio": round(ratio, 4),
        "intersection_sec": round(inter_dur, 3),
        "total_sec": round(total_dur, 3),
        "late_start": late,
        "early_end": early,
        "severity": sev,
    }


def check_stability(timestamps: Dict[str, List[float]],
                    primary: Optional[str] = None,
                    drift_low: float = 5.0,
                    drift_high: float = 20.0,
                    jump_threshold_ms: float = 30.0,
                    stable_threshold_ms: float = 5.0,
                    min_samples: int = 100) -> dict:
    """时钟稳定性: 漂移 + 跳变检测"""
    if not timestamps or len(timestamps) < 2:
        return {"stability": {}, "severity": "pass"}
    if primary is None:
        primary = max(timestamps.keys(), key=lambda k: len(timestamps[k]))
    ref = np.array(timestamps[primary])
    result = {}
    max_drift = 0.0
    any_jump = False
    for topic, ts in timestamps.items():
        if topic == primary or not ts or len(ts) < min_samples:
            continue
        target = np.array(ts)
        deltas = []
        matched_times = []
        for t in ref:
            idx = np.searchsorted(target, t)
            if 0 < idx < len(target):
                deltas.append((target[idx] - t) * 1000.0)
                matched_times.append(t)
        if len(deltas) < 10:
            continue
        std = float(np.std(deltas))
        drift = 0.0
        jumps = []
        has_jump = False
        if len(deltas) >= 50:
            times = np.asarray(matched_times)
            slope, _, r, _, _ = linregress(times, deltas)
            drift = abs(slope * 60.0) if abs(r) > 0.5 else 0.0
            window = max(50, len(deltas) // 20)
            windows = [np.median(deltas[i:i+window]) for i in range(0, len(deltas)-window, window)]
            for i in range(1, len(windows)):
                if abs(windows[i] - windows[i-1]) > jump_threshold_ms:
                    jumps.append({"pos": i * window, "delta_ms": round(abs(windows[i] - windows[i-1]), 2)})
            has_jump = len(jumps) > 0
        result[topic] = {
            "std_ms": round(std, 2),
            "mode": "fixed_offset" if std < stable_threshold_ms else ("jump" if has_jump else "drift" if drift > 0 else "unstable"),
            "drift_rate_ms_per_min": round(drift, 3),
            "has_jump": has_jump,
            "jump_positions": jumps,
        }
        max_drift = max(max_drift, drift)
        any_jump = any_jump or has_jump
    sev = "error" if max_drift > drift_high else ("warning" if max_drift > drift_low or any_jump else "pass")
    return {"stability": result, "primary": primary, "severity": sev}

