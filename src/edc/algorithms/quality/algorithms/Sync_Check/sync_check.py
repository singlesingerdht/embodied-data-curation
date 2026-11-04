"""采集同步用的起止和交集计算。"""
from typing import Dict, List


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

