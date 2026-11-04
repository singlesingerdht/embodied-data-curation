"""模块: 根因分析 — 可直接运行的评估入口。

用法:
    python -m algorithms.Root_Cause.run_check <ts.json> [--stability-json stability.json]
    ts.json 格式: {"topic_a": [0.0, 0.033, ...], "topic_b": [0.0, 0.033, ...]}
"""
from pathlib import Path
import sys
import os
import json
import argparse
import numpy as np

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from edc.algorithms.quality.shared.attribution import format_report
from edc.algorithms.quality.shared.quality_reporter import compute_quality, to_grade, normalize_weights
from edc.algorithms.quality.algorithms.Root_Cause.root_cause import analyze_root_cause

RC_DEFAULTS = {"跨通道Stall": 0.30, "周期性丢帧": 0.25, "时钟漂移": 0.25, "时钟跳变": 0.20}


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


def assess(ts_map: dict, stability: dict = None, weights: dict = None, families: dict = None) -> dict:
    if sum(len(values) >= 2 for values in ts_map.values()) < 2:
        from edc.data.ir.metadata import IRNotProvided
        from edc.data.readers.base import make_ir_report
        report = make_ir_report('根因分析', 'root_cause', IRNotProvided('至少两路有间隔样本的时间轴', 'timestamps'))
        report['assessment_complete'] = False
        return report
    rc_weights, rc_warns = normalize_weights(RC_DEFAULTS, weights, "根因分析")

    result = analyze_root_cause(ts_map, stability, families=families)
    correlated = result["correlated_gaps"]
    gap_patterns = result["gap_patterns"]

    stall_count = len(correlated)
    if stall_count > 0:
        if result.get("correlated_is_real_hole"):
            stall_score = 0.0
            spans = stall_spans(correlated, min(ts[0] for ts in ts_map.values() if len(ts)))
            stall_raw = f"多路数据同时中断 {len(spans)} 处：{'、'.join(spans)}"
        else:
            stall_score = 70.0
            stall_raw = f"关联时间间隔异常: {stall_count}次；不能据此排除丢帧"
    else:
        stall_score = 100.0
        stall_raw = "未发现至少三类消息源分组的关联空档"

    periodic_count = sum(1 for p in gap_patterns.values() if p.get("periodic"))
    periodic_score = 0.0 if periodic_count > 0 else 100.0
    periodic_raw = f"periodic={periodic_count}个通道" if periodic_count > 0 else "未发现周期性间隔异常"

    drift_score = 0.0 if result["has_drift"] else 100.0
    drift_raw = "时钟漂移>5ms/min" if result["has_drift"] else "无时钟漂移"

    jump_score = 0.0 if result["has_jump"] else 100.0
    jump_raw = "有时钟跳变" if result["has_jump"] else "无时钟跳变"

    subs = [
        {"name": "跨通道Stall", "weight": rc_weights["跨通道Stall"],
         "passed": stall_count == 0, "score": stall_score,
         "gap": round(100 - stall_score, 1), "weighted_gap": round((100 - stall_score) * rc_weights["跨通道Stall"], 1),
         "raw_value": stall_raw, "direction": "binary",
         "t_min": 1.0, "t_max": 0.0, "current_value": stall_score,
         "details": [], "grade": to_grade(stall_score)},
        {"name": "周期性丢帧", "weight": rc_weights["周期性丢帧"],
         "passed": periodic_count == 0, "score": periodic_score,
         "gap": round(100 - periodic_score, 1), "weighted_gap": round((100 - periodic_score) * rc_weights["周期性丢帧"], 1),
         "raw_value": periodic_raw, "direction": "binary",
         "t_min": 1.0, "t_max": 0.0, "current_value": periodic_score,
         "details": [], "grade": to_grade(periodic_score)},
        {"name": "时钟漂移", "weight": rc_weights["时钟漂移"],
         "passed": not result["has_drift"], "score": drift_score,
         "gap": round(100 - drift_score, 1), "weighted_gap": round((100 - drift_score) * rc_weights["时钟漂移"], 1),
         "raw_value": drift_raw, "direction": "binary",
         "t_min": 1.0, "t_max": 0.0, "current_value": drift_score,
         "details": [], "grade": to_grade(drift_score)},
        {"name": "时钟跳变", "weight": rc_weights["时钟跳变"],
         "passed": not result["has_jump"], "score": jump_score,
         "gap": round(100 - jump_score, 1), "weighted_gap": round((100 - jump_score) * rc_weights["时钟跳变"], 1),
         "raw_value": jump_raw, "direction": "binary",
         "t_min": 1.0, "t_max": 0.0, "current_value": jump_score,
         "details": [], "grade": to_grade(jump_score)},
    ]

    for sub, evidence in zip(subs[2:], (result['has_drift'], result['has_jump'])):
        if evidence is None:
            sub.update(score=None, passed=None, weight=0.0, gap=0.0, weighted_gap=0.0,
                       current_value=None, grade="N/A", raw_value="稳定性证据缺失或覆盖不足，未完整评估")
    if any(sub['score'] is None for sub in subs):
        total_weight = sum(sub["weight"] for sub in subs)
        for sub in subs:
            if sub['score'] is None:
                continue
            sub["weight"] = sub["weight"] / total_weight if total_weight > 0 else 0.0
            sub["weighted_gap"] = round(sub["gap"] * sub["weight"], 1)
    any_zero = any(s["score"] == 0 for s in subs)
    total = 0 if any_zero else round(sum(s["score"] * s["weight"] for s in subs if s["score"] is not None), 1)
    passed = not any_zero

    module = {
        "name": "根因分析", "module_key": "root_cause", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": rc_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(RC_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    report["root_cause"] = result
    report["assessment_complete"] = result['stability_evidence_complete']
    report["method_version"] = "root_cause_per_channel_evidence_v3"
    report["limitations"] = ["Timestamp gap correlations are hypotheses, not proof of physical root cause"]
    report['warnings'].extend(
        f"{name}: 检测到长间隔，最大{row['max_gap_s']*1000:.3f}ms；时间证据不能确定物理根因"
        for name, row in result['channel_gap_evidence'].items() if row['long_gap'])
    if not result['stability_evidence_complete']:
        report["limitations"].append("Clock drift and jumps not fully assessed: stability evidence absent or incomplete")
        if report['verdict'] != 'DO_NOT_USE':
            report.update(passed=None, verdict='OBSERVATION')
            report['reasons'].append('仅完成有证据的子项；时钟漂移或跳变未完整评估，不给整体通过结论')
        if sum(sub['weight'] for sub in subs) == 0:
            report.update(score=None, grade='N/A')
    return report


def assess_from_ir(info, reader, weights: dict = None, families: dict = None) -> dict:
    """从 IR 评估根因分析。"""
    try:
        ts_map = reader.require_timestamps(info, min_channels=2)
    except Exception as exc:
        from edc.data.readers.base import make_ir_report
        return make_ir_report("根因分析", "root_cause", exc)
    from edc.data.readers.base import filter_real_timestamps, make_synthetic_report
    ts_map, has_synthetic = filter_real_timestamps(info, ts_map)
    if ts_map is None:
        return make_synthetic_report("根因分析", "root_cause", info)
    from edc.algorithms.quality.shared.timing_scope import partition_timing_topics
    ts_map, excluded = partition_timing_topics(info, ts_map)
    if len(ts_map) < 2:
        from edc.data.ir.metadata import IRNotProvided
        from edc.data.readers.base import make_ir_report
        report = make_ir_report('根因分析', 'root_cause', IRNotProvided('至少两路非 metadata 时间轴', info.format))
    else:
        report = assess(ts_map, weights=weights, families=families)
    report['excluded_topics'] = excluded
    report['timestamp_scope'] = {'included_topics': sorted(ts_map),
        'time_sources': {name: getattr(info.topics.get(name), 'time_source', 'unknown') for name in ts_map}}
    return report


def main():
    ap = argparse.ArgumentParser(description="模块 根因分析")
    ap.add_argument("input", help="时间戳 JSON 或数据目录路径 (IR: episode/dataset)")
    ap.add_argument("--stability-json", default=None, help="Sync 稳定性 JSON 文件路径")
    ap.add_argument("--weights", default=None, help="权重 JSON 字符串")
    args = ap.parse_args()

    from edc.data.ir.metadata import detect_format, FORMAT_JSON, FORMAT_UNKNOWN
    fmt = detect_format(args.input)
    weights = json.loads(args.weights) if args.weights else None

    if fmt != FORMAT_JSON and fmt != FORMAT_UNKNOWN:
        from edc.algorithms.quality.config import open_info
        info, reader = open_info(args.input)
        report = assess_from_ir(info, reader, weights)
    else:
        with open(args.input) as f:
            ts_map = json.load(f)
        stability = None
        if args.stability_json:
            with open(args.stability_json) as f:
                stability = json.load(f)
        report = assess(ts_map, stability, weights)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
