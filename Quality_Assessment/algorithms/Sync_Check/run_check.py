"""模块: 多通道同步 — 可直接运行的评估入口。

用法:
    python -m algorithms.Sync_Check.run_check <ts.json> [--primary cam_a]
    ts.json 格式: {"topic_a": [0.0, 0.033, ...], "topic_b": [0.0, 0.033, ...]}
"""
from pathlib import Path
import sys
import os
import json
import argparse

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from Quality_Assessment.shared.attribution import format_report
from Quality_Assessment.shared.quality_reporter import compute_quality, to_grade, normalize_weights
from Quality_Assessment.algorithms.Sync_Check.sync_check import check_coverage, check_alignment, check_stability, check_sync

SYNC_DEFAULTS = {"时间覆盖": 0.30, "时间对齐": 0.35, "时钟稳定性": 0.35}


def _severity_to_score(sev: str) -> float:
    return {"pass": 100.0, "warning": 70.0, "error": 0.0}.get(sev, 0.0)


def assess(timestamps: dict, primary: str = None, weights: dict = None) -> dict:
    s_weights, s_warns = normalize_weights(SYNC_DEFAULTS, weights, "多通道同步")

    result = check_sync(timestamps, primary)
    cov = result["coverage"]
    aln = result["alignment"]
    stb = result["stability"]

    cov_score = round(cov["coverage_ratio"] * 100.0, 1)
    cov_raw = f"coverage={cov['coverage_ratio']:.3f} inter={cov['intersection_sec']:.1f}s total={cov['total_sec']:.1f}s"

    aln_score = _severity_to_score(aln["severity"])
    aln_raw = f"primary={aln['primary']}"
    for t, a in aln.get("alignments", {}).items():
        aln_raw += f" {t}:p95={a['p95_delta_ms']:.1f}ms"

    stb_score = _severity_to_score(stb["severity"])
    stb_raw = f"primary={stb['primary']}"
    for t, s in stb.get("stability", {}).items():
        stb_raw += f" {t}:drift={s['drift_rate_ms_per_min']:.1f}ms/min jump={s['has_jump']}"

    subs = [
        {"name": "时间覆盖", "weight": s_weights["时间覆盖"],
         "passed": cov["severity"] != "error", "score": cov_score,
         "gap": round(100 - cov_score, 1), "weighted_gap": round((100 - cov_score) * s_weights["时间覆盖"], 1),
         "raw_value": cov_raw, "direction": "forward",
         "t_min": 0.8, "t_max": 1.0, "current_value": cov["coverage_ratio"],
         "details": [], "grade": to_grade(cov_score)},
        {"name": "时间对齐", "weight": s_weights["时间对齐"],
         "passed": aln["severity"] != "error", "score": aln_score,
         "gap": round(100 - aln_score, 1), "weighted_gap": round((100 - aln_score) * s_weights["时间对齐"], 1),
         "raw_value": aln_raw, "direction": "inverse",
         "t_min": 200.0, "t_max": 0.0, "current_value": aln_score,
         "details": [], "grade": to_grade(aln_score)},
        {"name": "时钟稳定性", "weight": s_weights["时钟稳定性"],
         "passed": stb["severity"] != "error", "score": stb_score,
         "gap": round(100 - stb_score, 1), "weighted_gap": round((100 - stb_score) * s_weights["时钟稳定性"], 1),
         "raw_value": stb_raw, "direction": "inverse",
         "t_min": 20.0, "t_max": 0.0, "current_value": stb_score,
         "details": [], "grade": to_grade(stb_score)},
    ]

    stability_complete = len(stb.get("stability", {})) == len(timestamps) - 1
    if not stability_complete:
        subs[2].update(score=None, passed=None, weight=0.0, gap=0.0, weighted_gap=0.0,
                       grade="N/A", current_value=None, raw_value="部分或全部通道稳定性样本不足，未完整评估")
        total_weight = sum(sub["weight"] for sub in subs[:2])
        for sub in subs[:2]:
            sub["weight"] /= total_weight
            sub["weighted_gap"] = round(sub["gap"] * sub["weight"], 1)
    any_zero = any(s["score"] == 0 for s in subs)
    total = 0 if any_zero else round(sum(s["score"] * s["weight"] for s in subs if s["score"] is not None), 1)
    passed = not any_zero

    module = {
        "name": "多通道同步", "module_key": "sync_check", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": s_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(SYNC_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    report["sync"] = result
    report["assessment_complete"] = stability_complete
    report["method_version"] = "sync_available_evidence_v2"
    return report


def assess_from_ir(info, reader, weights: dict = None) -> dict:
    """从 IR DataInfo 评估多通道同步。

    需要≥2通道时间戳; 缺则返回 IRNotProvided 降级报告。
    """
    try:
        ts_map = reader.require_timestamps(info, min_channels=2)
    except Exception as exc:
        from framework.readers.base import make_ir_report
        return make_ir_report("多通道同步", "sync_check", exc)
    from framework.readers.base import filter_real_timestamps, make_synthetic_report, _all_same_axis
    ts_map, has_synthetic = filter_real_timestamps(info, ts_map)
    if ts_map is None:
        return make_synthetic_report("多通道同步", "sync_check", info)
    if _all_same_axis(ts_map):
        from framework.readers.base import make_ir_report
        from framework.ir.metadata import IRNotProvided
        return make_ir_report("多通道同步", "sync_check",
            IRNotProvided("全通道共享同一时间戳 (同轴采样)", info.format,
                          "跨通道同步检测仅对独立采集的时间戳有意义"))
    return assess(ts_map, weights=weights)


def main():
    ap = argparse.ArgumentParser(description="模块 多通道同步评估")
    ap.add_argument("input", help="时间戳 JSON 或数据目录路径 (IR: episode/dataset)")
    ap.add_argument("--primary", default=None, help="主参考通道名称")
    ap.add_argument("--weights", default=None, help="权重 JSON 字符串")
    args = ap.parse_args()

    from framework.ir.metadata import detect_format, FORMAT_JSON, FORMAT_UNKNOWN
    fmt = detect_format(args.input)
    weights = json.loads(args.weights) if args.weights else None

    if fmt != FORMAT_JSON and fmt != FORMAT_UNKNOWN:
        from Quality_Assessment.config import open_info
        info, reader = open_info(args.input)
        report = assess_from_ir(info, reader, weights)
    else:
        with open(args.input) as f:
            timestamps = json.load(f)
        report = assess(timestamps, args.primary, weights)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()