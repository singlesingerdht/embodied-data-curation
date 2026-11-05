"""模块: 根因分析，框架通过 assess 调用（algorithm.py）。"""
from edc.algorithms.quality.shared.attribution import format_report
from edc.algorithms.quality.shared.quality_reporter import to_grade, normalize_weights
from edc.algorithms.quality.algorithms.Root_Cause.root_cause import analyze_root_cause

RC_DEFAULTS = {"跨通道Stall": 0.40, "单路断档": 0.30, "周期性丢帧": 0.30}


def stall_spans(correlated, start):
    """关联事件按时间合并成几段（同一次卡顿常被拆成好几个 100 ms 内的事件），写成“第几秒到第几秒”，从录制开始算。"""
    merged = []
    for a, b, longest in sorted((min(g['time'] for g in e['gaps']), max(g['time'] + g['gap_s'] for g in e['gaps']),
                                 e['max_gap_ms']) for e in correlated):
        if merged and a <= merged[-1][1]:
            merged[-1] = [merged[-1][0], max(merged[-1][1], b), max(merged[-1][2], longest)]
        else:
            merged.append([a, b, longest])
    return [f'第 {a - start:.1f}–{b - start:.1f} 秒（最长断 {longest / 1000:.1f} 秒）' for a, b, longest in merged]


def assess(ts_map: dict, weights: dict = None, families: dict = None) -> dict:
    if sum(len(values) >= 2 for values in ts_map.values()) < 2:
        from edc.data.ir.metadata import IRNotProvided
        from edc.data.readers.base import make_ir_report
        report = make_ir_report('根因分析', 'root_cause', IRNotProvided('至少两路有间隔样本的时间轴', 'timestamps'))
        report['assessment_complete'] = False
        return report
    rc_weights, rc_warns = normalize_weights(RC_DEFAULTS, weights, "根因分析")

    result = analyze_root_cause(ts_map, families=families)
    correlated = result["correlated_gaps"]
    gap_patterns = result["gap_patterns"]
    start = min(ts[0] for ts in ts_map.values() if len(ts))

    stall_count = len(correlated)
    if stall_count > 0:
        if result.get("correlated_is_real_hole"):
            stall_score = 0.0
            spans = stall_spans(correlated, start)
            stall_raw = f"多路数据同时中断 {len(spans)} 处：{'、'.join(spans)}"
        else:
            stall_score = 70.0
            stall_raw = f"关联时间间隔异常: {stall_count}次；不能据此排除丢帧"
    else:
        stall_score = 100.0
        stall_raw = "未发现至少三类消息源分组的关联空档"

    single = result["single_gaps"]
    single_score = 70.0 if single else 100.0
    single_raw = ("只有一路中断、其他来源没有一起断：" + '；'.join(
        f"{name} 第 {e['before_time_s'] - start:.1f}–{e['after_time_s'] - start:.1f} 秒（断 {e['gap_s']:.1f} 秒）"
        for name, events in single.items() for e in events) if single else "没有单独一路中断 0.5 秒以上")

    periodic_count = sum(1 for p in gap_patterns.values() if p.get("periodic"))
    periodic_score = 0.0 if periodic_count > 0 else 100.0
    periodic_raw = f"periodic={periodic_count}个通道" if periodic_count > 0 else "未发现周期性间隔异常"

    def sub(name, score, raw):
        return {"name": name, "weight": rc_weights[name], "passed": score == 100, "score": score,
                "gap": round(100 - score, 1), "weighted_gap": round((100 - score) * rc_weights[name], 1),
                "raw_value": raw, "direction": "binary", "t_min": 1.0, "t_max": 0.0, "current_value": score,
                "details": [], "grade": to_grade(score)}
    subs = [sub("跨通道Stall", stall_score, stall_raw), sub("单路断档", single_score, single_raw),
            sub("周期性丢帧", periodic_score, periodic_raw)]
    any_zero = any(s["score"] == 0 for s in subs)
    total = 0 if any_zero else round(sum(s["score"] * s["weight"] for s in subs), 1)
    passed = not any_zero

    module = {
        "name": "根因分析", "module_key": "root_cause", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": rc_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(RC_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    if single and report["verdict"] == "ACCEPTABLE":
        report["verdict"] = "NEEDS_REVIEW"  # 单路断档是那一路设备的问题：判复核，人看视频（2026-11-04 用户决定）
    report["root_cause"] = result
    report["assessment_complete"] = True
    report["method_version"] = "root_cause_per_channel_evidence_v3"
    report["limitations"] = ["Timestamp gap correlations are hypotheses, not proof of physical root cause"]
    report['warnings'].extend(
        f"{name}: 检测到长间隔，最大{row['max_gap_s']*1000:.3f}ms；时间证据不能确定物理根因"
        for name, row in result['channel_gap_evidence'].items() if row['long_gap'])
    return report
