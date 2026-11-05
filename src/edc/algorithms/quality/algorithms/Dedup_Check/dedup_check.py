"""去重检查: 时间戳/序列号重复 (DF 方案)"""
from typing import List, Optional


def check_timestamp_dedup(timestamps: List[float],
                          dedup_ratio_low: float = 0.01,
                          dedup_ratio_high: float = 0.05) -> dict:
    """Observe every repeated timestamp; score only the historical >=5-row runs.

    Equality is within one source stream, in source order. Repeated timestamps
    are not proof of repeated payloads. ``duplicate_count``/``dedup_ratio``
    retain their historical long-run meaning so existing thresholds do not
    silently change when the observation scope expands.
    """
    if len(timestamps) < 2:
        return {"dedup_ratio": 0.0, "duplicate_count": 0, "total_count": len(timestamps),
                "segments": [], "legacy_segments": [], "has_dedup": False, "severity": "pass",
                "involved_record_count": 0, "extra_duplicate_count": 0,
                "extra_duplicate_ratio": 0.0, "adjacent_equal_count": 0,
                "nonadjacent_reuse_count": 0, "max_consecutive_count": len(timestamps),
                "repeated_group_count": 0, "repeated_groups": [],
                "observation_only_extra_count": 0,
                "scoring_scope": "legacy_consecutive_runs_ge_5_involved_records"}
    run = 0
    segs = []
    start = None
    for i, (a, b) in enumerate(zip(timestamps, timestamps[1:])):
        if a == b:
            run += 1
            if start is None:
                start = i
        else:
            if run >= 1:
                segs.append({"start": start, "end": i, "count": run + 1})
            run = 0
            start = None
    if run >= 1:
        segs.append({"start": start, "end": len(timestamps) - 1, "count": run + 1})
    legacy_segments = [s for s in segs if s["count"] >= 5]
    total_dup = sum(s["count"] for s in legacy_segments)
    groups = {}
    nonadjacent = 0
    for row, value in enumerate(timestamps):
        if value not in groups:
            groups[value] = {"timestamp": value, "first_row": row, "last_row": row, "count": 1, "example_rows": [row]}
        else:
            group = groups[value]
            nonadjacent += int(group["last_row"] != row - 1)
            group["last_row"] = row
            group["count"] += 1
            if len(group["example_rows"]) < 20:
                group["example_rows"].append(row)
    repeated = [g for g in groups.values() if g["count"] > 1]
    extra = sum(g["count"] - 1 for g in repeated)
    ratio = total_dup / len(timestamps) if timestamps else 0
    sev = "error" if ratio >= dedup_ratio_high else ("warning" if ratio > dedup_ratio_low else "pass")
    return {
        "dedup_ratio": round(ratio, 4),
        "duplicate_count": total_dup,
        "total_count": len(timestamps),
        "segments": segs,
        "legacy_segments": legacy_segments,
        "involved_record_count": sum(g["count"] for g in repeated),
        "extra_duplicate_count": extra,
        "extra_duplicate_ratio": extra / len(timestamps),
        "adjacent_equal_count": sum(s["count"] - 1 for s in segs),
        "nonadjacent_reuse_count": nonadjacent,
        "max_consecutive_count": max((s["count"] for s in segs), default=1),
        "repeated_group_count": len(repeated),
        "repeated_groups": repeated[:100],
        "repeated_groups_truncated": len(repeated) > 100,
        "observation_only_extra_count": extra - sum(s["count"] - 1 for s in legacy_segments),
        "scoring_scope": "legacy_consecutive_runs_ge_5_involved_records",
        "has_dedup": extra > 0,
        "severity": sev,
    }


def check_seq_dedup(seqs: List[int],
                    dedup_ratio_low: float = 0.01,
                    dedup_ratio_high: float = 0.05) -> dict:
    """序列号重复检测"""
    if not seqs or len(seqs) < 2:
        return {"dedup_ratio": 0.0, "has_dedup": False, "severity": "pass"}
    seen = set()
    dups = []
    for i, s in enumerate(seqs):
        if s in seen:
            dups.append(i)
        seen.add(s)
    ratio = len(dups) / len(seqs) if seqs else 0
    sev = "error" if ratio >= dedup_ratio_high else ("warning" if ratio > dedup_ratio_low else "pass")
    return {
        "dedup_ratio": round(ratio, 4),
        "duplicate_count": len(dups),
        "total_count": len(seqs),
        "has_dedup": ratio > 0,
        "severity": sev,
    }


def check_dedup(timestamps: List[float], seqs: Optional[List[int]] = None,
                dedup_ratio_low: float = .01, dedup_ratio_high: float = .05) -> dict:
    """一站式去重检查"""
    if not 0 <= dedup_ratio_low < dedup_ratio_high <= 1:
        raise ValueError('Require 0 <= dedup_ratio_low < dedup_ratio_high <= 1')
    ts = check_timestamp_dedup(timestamps, dedup_ratio_low, dedup_ratio_high)
    sq = check_seq_dedup(seqs, dedup_ratio_low, dedup_ratio_high) if seqs else {"dedup_ratio": 0.0, "has_dedup": False, "severity": "pass"}
    rank = {"pass": 0, "warning": 1, "error": 2}
    sev = max(ts["severity"], sq["severity"], key=lambda s: rank.get(s, 0))
    return {
        "timestamp_dedup": ts,
        "seq_dedup": sq,
        "severity": sev,
        "passed": sev == "pass",
    }
