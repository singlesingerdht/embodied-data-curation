"""跨通道根因分析: gap 关联 + 周期性检测 + 系统级 stall (DF 方案)

输入: sync 检查产出的 stability/drift 结果 + timing 检查的 gap 事件
输出: 人读 verdict 字符串 + 结构化关联事件
"""
import numpy as np
from typing import Dict, List, Any, Optional

GAP_MIN_MS = 20.0
CORR_WINDOW_MS = 100.0
CORR_MIN_FAMILIES = 3
PERIOD_MIN_GAPS = 8
PERIOD_CV_MAX = 0.35
REAL_STALL_MIN_GAP_S = 0.5       # 兼容名称；单次长间隔门槛，不证明采集丢失
REAL_STALL_MIN_EXCESS_S = 0.5    # 单通道估算间隔超额门槛


def _channel_gap_evidence(ts) -> dict:
    """Observe one stream; a timestamp gap alone does not prove lost acquisition."""
    if len(ts) < 2:
        return {'long_gap': False, 'max_gap_s': 0.0, 'gap_excess_s': 0.0, 'events': []}
    dt = np.diff(np.asarray(ts, dtype=float))
    if not np.isfinite(ts).all() or np.any(dt < 0):
        raise ValueError('Root cause requires finite, nondecreasing timestamps')
    med = np.median(dt[dt > 0]) if len(dt[dt > 0]) > 0 else 0.033
    thr = max(2 * med, GAP_MIN_MS / 1000.0)
    indices = np.flatnonzero(dt > thr)
    gaps = dt[indices]
    if len(gaps) == 0:
        return {'long_gap': False, 'max_gap_s': 0.0, 'gap_excess_s': 0.0, 'events': []}
    gap_excess = float(sum(gaps - med))
    max_gap = float(max(gaps))
    is_real = max_gap >= REAL_STALL_MIN_GAP_S or gap_excess >= REAL_STALL_MIN_EXCESS_S
    return {'long_gap': bool(is_real), 'max_gap_s': max_gap, 'gap_excess_s': gap_excess,
            'typical_interval_s': float(med),
            'events': [{'before_index': int(i), 'after_index': int(i + 1),
                        'before_time_s': float(ts[i]), 'after_time_s': float(ts[i + 1]),
                        'gap_s': float(dt[i])} for i in indices]}


def _detect_real_stall(ts_map: dict) -> tuple:
    """Compatibility tuple: any long gap, largest gap, largest per-stream excess.

    Never sum the same outage across channels, or depend on dictionary order.
    The historical is_real_hole field denotes thresholded time evidence only.
    """
    rows = [_channel_gap_evidence(ts) for ts in ts_map.values()]
    return (any(row['long_gap'] for row in rows),
            max((row['max_gap_s'] for row in rows), default=0.0),
            max((row['gap_excess_s'] for row in rows), default=0.0))


def analyze_root_cause(ts_map: Dict[str, List[float]],
                       stability: Optional[Dict[str, Any]] = None,
                       drop_details: Optional[Dict[str, Any]] = None,
                       drop_ratio_low: float = 0.02,
                       drop_ratio_high: float = 0.10,
                       families: Optional[Dict[str, str]] = None) -> dict:
    """跨通道 gap 关联分析 + span 验证 + 根因 verdict"""
    ts_map = dict(sorted(ts_map.items()))
    if families is not None and (not isinstance(families, dict)
            or any(name not in ts_map or not isinstance(value, str) or not value.strip()
                   for name, value in families.items())):
        raise ValueError('families must map input topics to nonempty source-group names')
    channel_evidence = {name: _channel_gap_evidence(ts) for name, ts in ts_map.items()}
    gap_patterns = _extract_gap_patterns(ts_map)
    correlated = _find_correlated_gaps(ts_map, families)
    drop_summary = _summarize_drops(ts_map, drop_ratio_low, drop_ratio_high)
    is_real_hole, max_gap_s, gap_excess_s = _detect_real_stall(ts_map)

    # An unrelated long gap must not promote a short correlated event to a stall.
    correlated_series = {}
    for event in correlated:
        for gap in event['gaps']:
            correlated_series.setdefault(gap['topic'], []).append(gap)
    correlated_long = any(
        max(g['gap_s'] for g in gaps) >= REAL_STALL_MIN_GAP_S
        or sum(g['gap_s'] - channel_evidence[name]['typical_interval_s'] for g in gaps) >= REAL_STALL_MIN_EXCESS_S
        for name, gaps in correlated_series.items())

    rows = (stability or {}).get('stability', {})
    reference = (stability or {}).get('primary')
    expected = set(ts_map) - ({reference} if reference in ts_map else set())
    # Older standalone stability files can omit primary; still require N-1 rows.
    covered = set(rows) & set(ts_map)
    coverage_complete = (len(ts_map) >= 2 and len(covered) >= len(ts_map) - 1
                         and (reference not in ts_map or expected <= covered))
    drift_known = {name: row['drift_rate_ms_per_min'] for name, row in rows.items()
                   if name in ts_map and isinstance(row, dict)
                   and isinstance(row.get('drift_rate_ms_per_min'), (int, float))
                   and np.isfinite(row['drift_rate_ms_per_min'])}
    jump_known = {name: row['has_jump'] for name, row in rows.items()
                  if name in ts_map and isinstance(row, dict) and type(row.get('has_jump')) is bool}
    drift_complete = coverage_complete and covered <= drift_known.keys()
    jump_complete = coverage_complete and covered <= jump_known.keys()
    observed_drift = any(abs(value) > 5.0 for value in drift_known.values())
    observed_jump = any(jump_known.values())
    has_drift = True if observed_drift else False if drift_complete else None
    has_jump = True if observed_jump else False if jump_complete else None

    verdict_parts = []
    if drop_details:
        verdict_parts.append(str(drop_details.get("verdict", "")))
    if correlated:
        if correlated_long:
            verdict_parts.append(
                f"跨通道长间隔: {len(correlated)} 次关联事件；可能存在共同记录/发布中断，原因待确认"
            )
        else:
            verdict_parts.append(
                f"关联时间间隔异常: {len(correlated)} 次；仅凭时间戳不能确定丢帧或毛刺"
            )
    if is_real_hole:
        verdict_parts.append(f"检测到长间隔，最大{max_gap_s*1000:.0f}ms；各通道估算缺失时长最大≈{gap_excess_s:.1f}s")
    if has_drift:
        verdict_parts.append("时钟漂移 >5ms/min")
    if has_jump:
        verdict_parts.append("时钟跳变")
    if not verdict_parts:
        verdict_parts.append("未发现跨通道关联异常")

    return {
        "gap_patterns": gap_patterns,
        "correlated_gaps": correlated,
        "drop_summary": drop_summary,
        "has_drift": has_drift,
        "has_jump": has_jump,
        "is_real_hole": is_real_hole,
        "max_gap_s": round(max_gap_s, 3),
        "gap_excess_s": round(gap_excess_s, 3),
        "correlated_is_real_hole": bool(correlated_long),
        "channel_gap_evidence": channel_evidence,
        "gap_excess_aggregation": "max_per_channel_not_sum",
        "source_groups": {name: (families or {}).get(name, _sensor_family(name)) for name in ts_map},
        "source_group_basis": "explicit overrides or topic namespace; not proof of independent hardware",
        "stability_evidence_complete": bool(drift_complete and jump_complete),
        "verdict": "; ".join(verdict_parts),
    }


def _extract_gap_patterns(ts_map: Dict[str, List[float]]) -> Dict[str, dict]:
    patterns = {}
    for topic, ts in ts_map.items():
        if len(ts) < 2:
            continue
        dts = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:])]
        positive = [d for d in dts if d > 0]
        if not positive:
            continue
        med = np.median(positive)
        gaps = [(i, d) for i, d in enumerate(dts) if d > max(2 * med, GAP_MIN_MS)]
        if not gaps:
            continue
        gap_intervals = []
        if len(gaps) >= 2:
            for i in range(1, len(gaps)):
                gap_intervals.append(gaps[i][0] - gaps[i - 1][0])
        periodic = False
        if len(gap_intervals) >= PERIOD_MIN_GAPS:
            cv = float(np.std(gap_intervals)) / max(float(np.mean(gap_intervals)), 1e-9)
            periodic = cv < PERIOD_CV_MAX
        patterns[topic] = {
            "gap_count": len(gaps),
            "max_gap_ms": round(max(g[1] for g in gaps), 2),
            "median_interval_ms": round(float(med), 2),
            "periodic": periodic,
        }
    return patterns


def _find_correlated_gaps(ts_map: Dict[str, List[float]], families=None) -> List[dict]:
    all_gaps = []
    for topic, ts in ts_map.items():
        if len(ts) < 2:
            continue
        dts = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:])]
        positive = [d for d in dts if d > 0]
        if not positive:
            continue
        med = np.median(positive)
        for i, d in enumerate(dts):
            if d > max(2 * med, GAP_MIN_MS):
                t = ts[i]
                family = (families or {}).get(topic, _sensor_family(topic))
                all_gaps.append({"topic": topic, "family": family, "time": t,
                                 "before_index": i, "after_index": i + 1,
                                 "gap_s": float(ts[i + 1] - ts[i]), "duration_ms": round(d, 2)})
    if not all_gaps:
        return []
    all_sorted = sorted(all_gaps, key=lambda x: x["time"])
    events = []
    i = 0
    while i < len(all_sorted):
        cluster = [all_sorted[i]]
        j = i + 1
        while j < len(all_sorted) and all_sorted[j]["time"] - cluster[0]["time"] < CORR_WINDOW_MS / 1000.0:
            cluster.append(all_sorted[j])
            j += 1
        cluster_families = set(g["family"] for g in cluster)
        if len(cluster_families) >= CORR_MIN_FAMILIES:
            events.append({
                "time": round(cluster[0]["time"], 3),
                "duration_ms": round(cluster[-1]["time"] - cluster[0]["time"], 3) * 1000,
                "families": sorted(cluster_families),
                "max_gap_ms": max(g['duration_ms'] for g in cluster),
                "gaps": cluster,
            })
        i = j
    return events


def _sensor_family(topic: str) -> str:
    parts = topic.strip('/').split('/')
    return parts[0] if len(parts) >= 2 else topic


def _summarize_drops(ts_map: Dict[str, List[float]],
                     low: float, high: float) -> Dict[str, dict]:
    summary = {}
    for topic, ts in ts_map.items():
        if len(ts) < 2:
            continue
        dts = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:])]
        positive = [d for d in dts if d > 0]
        if not positive:
            continue
        med = np.median(positive)
        gaps = [d for d in dts if d > max(2 * med, GAP_MIN_MS)]
        lost = sum(max(0, round(d / med) - 1) for d in gaps)
        ratio = lost / len(dts) if dts else 0
        level = "error" if ratio >= high else ("warning" if ratio > low else "pass")
        if level != "pass":
            summary[topic] = {"drop_ratio": round(ratio, 4), "lost_frames": lost, "estimate_only": True,
                              "gap_count": len(gaps), "level": level}
    return summary
