"""模块: 去重检查，框架通过 assess 调用（algorithm.py）。"""
from edc.algorithms.quality.shared.attribution import format_report
from edc.algorithms.quality.shared.quality_reporter import to_grade, normalize_weights
from edc.algorithms.quality.algorithms.Dedup_Check.dedup_check import check_dedup

DEDUP_DEFAULTS = {"时间戳去重": 0.60, "序列号去重": 0.40}


def _severity_to_score(sev: str) -> float:
    return {"pass": 100.0, "warning": 70.0, "error": 0.0}.get(sev, 0.0)


def assess(timestamps: list, seqs: list = None, weights: dict = None,
           dedup_ratio_low: float = .01, dedup_ratio_high: float = .05) -> dict:
    d_weights, d_warns = normalize_weights(DEDUP_DEFAULTS, weights, "去重检查")

    result = check_dedup(timestamps, seqs, dedup_ratio_low, dedup_ratio_high)
    td = result["timestamp_dedup"]
    sd = result["seq_dedup"]

    td_score = _severity_to_score(td["severity"])
    td_raw = (f"涉及记录={td['involved_record_count']}，额外出现={td['extra_duplicate_count']}，"
              f"相邻相等={td['adjacent_equal_count']}；长段评分比例={td['dedup_ratio']:.3f} "
              f"({td['duplicate_count']}/{td['total_count']})")

    sd_score = _severity_to_score(sd["severity"])
    if sd.get("total_count", 0) > 0:
        sd_raw = f"dup={sd['dedup_ratio']:.3f} ({sd['duplicate_count']}/{sd['total_count']})"
    else:
        sd_raw = "无序列号数据, 跳过"
        sd_score = None

    subs = [
        {"name": "时间戳去重", "weight": d_weights["时间戳去重"],
         "passed": td["severity"] != "error", "score": td_score,
         "gap": round(100 - td_score, 1), "weighted_gap": round((100 - td_score) * d_weights["时间戳去重"], 1),
         "raw_value": td_raw, "direction": "inverse",
         "t_min": dedup_ratio_high, "t_max": 0.0, "current_value": td["dedup_ratio"],
         "details": [], "grade": to_grade(td_score)},
        {"name": "序列号去重", "weight": d_weights["序列号去重"],
         "passed": seqs is not None and sd["severity"] != "error",
         "score": sd_score, "gap": round(100 - sd_score, 1) if sd_score is not None else 0,
         "weighted_gap": round((100 - sd_score) * d_weights["序列号去重"], 1) if sd_score is not None else 0,
         "raw_value": sd_raw, "direction": "inverse",
         "t_min": dedup_ratio_high, "t_max": 0.0, "current_value": sd.get("dedup_ratio", 0),
         "details": [], "grade": to_grade(sd_score) if sd_score is not None else "N/A"},
    ]

    if sd_score is None:
        subs[0]["weight"] = 1.0
        subs[0]["weighted_gap"] = subs[0]["gap"]
        subs[1]["weight"] = 0.0
        subs[1]["passed"] = None
    any_zero = any(s["score"] == 0 for s in subs)
    total = 0 if any_zero else round(sum(s["score"] * s["weight"] for s in subs if s["score"] is not None), 1)
    passed = not any_zero

    module = {
        "name": "去重检查", "module_key": "dedup_check", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": d_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(DEDUP_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    report["dedup"] = result
    report['dedup_parameters'] = {'dedup_ratio_low': dedup_ratio_low,
        'dedup_ratio_high': dedup_ratio_high, 'scored_min_consecutive_samples': 5,
        'new_short_or_nonadjacent_threshold': None}
    report["method_version"] = "dedup_full_observation_legacy_scoring_v3"
    report["scope"] = "All equal timestamp groups in source order; legacy score uses consecutive runs >=5. Not payload deduplication or deletion."
    report['assessment_complete'] = not bool(td['observation_only_extra_count'])
    if td['observation_only_extra_count']:
        report['reasons'] = (report.get('reasons') or []) + [
            '发现短段或非相邻时间戳复用；已登记但尚未配置新判定阈值，不沿用旧评分比例阈值。时间相同不等于内容复制。']
        if report.get('verdict') == 'ACCEPTABLE':
            report.update(verdict='OBSERVATION', passed=None)
    if len(timestamps) < 2 and not seqs:
        report.update(score=None, verdict='NOT_APPLICABLE', passed=None, assessment_complete=False)
    return report
