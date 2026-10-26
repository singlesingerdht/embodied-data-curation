"""模块: 结构完整性 — 可直接运行的评估入口。

用法:
    python -m algorithms.Structure_Check.run_check <path> [--required topic1 topic2 ...]
    python algorithms/Structure_Check/run_check.py <path>
"""
import sys
import os
import json
import argparse
from pathlib import Path

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from edc.algorithms.quality.shared.attribution import format_report
from edc.algorithms.quality.shared.quality_reporter import compute_quality, to_grade, normalize_weights
from edc.algorithms.quality.algorithms.Structure_Check.structure_check import (
    check_file_integrity, check_topics, check_camera_pairs,
    check_camera_symmetry, check_structure,
)
from edc.algorithms.quality.shared.topic_info import TopicInfo, extract_modality_groups

S_DEFAULTS = {"文件存在": 0.20, "文件可读": 0.20, "格式支持": 0.15,
              "通道完整性": 0.20, "相机配对": 0.15, "相机对称": 0.10}


def _severity_to_score(sev: str) -> float:
    return {"pass": 100.0, "warning": 70.0, "error": 0.0}.get(sev, 0.0)


def _sev_to_passed(sev: str) -> bool:
    return sev != "error"


def assess(path: str, topics: dict = None, required_topics: list = None,
           weights: dict = None, camera_requirements=None, channel_config=None) -> dict:
    s_weights, s_warns = normalize_weights(S_DEFAULTS, weights, "结构完整性")

    # 直接调用一站式检查
    result = check_structure(path, topics, required_topics, camera_requirements, channel_config)
    fi = result["file_integrity"]

    subs = []
    atoms = [
        ("文件存在", fi["severity"] if not fi.get("exists", True) else "pass",
         "文件存在" if fi.get("exists", True) else "文件不存在"),
        ("文件可读", fi["severity"] if not fi.get("readable", True) else "pass",
         "可读" if fi.get("readable", True) else "不可读"),
        ("格式支持", "warning" if fi.get("format") == "unknown" else "pass",
         f"格式={fi.get('format', '?')}"),
    ]

    ti = result.get("topics", {})
    if ti and "total_topics" in ti:
        atoms.append(
            ("通道完整性", ti["severity"],
             f"total={ti['total_topics']} missing={ti.get('missing_required',[])} empty={ti.get('empty_topics',[])}")
        )
    else:
        atoms.append(("通道完整性", "pass", "无 topic 数据, 跳过"))

    cp = result.get("camera_pairs", {})
    if cp and "image_count" in cp:
        atoms.append(
            ("相机配对", cp["severity"],
             f"paired={cp['paired_count']}/{cp['image_count']} orphan={len(cp.get('orphan_images',[]))}")
        )
    else:
        atoms.append(("相机配对", "pass", "无图像 topic, 跳过"))

    cs = result.get("camera_configuration", {})
    atoms.append(("相机对称", cs.get("severity", "not_evaluated"),
                  json.dumps(cs.get("checks", []), ensure_ascii=False) if cs.get("evaluated")
                  else cs.get("reason", "未配置相机要求")))

    s_any_zero = False
    for name, sev, raw_val in atoms:
        if sev == "not_evaluated":
            subs.append({"name": "相机通道配置一致性", "legacy_weight_key": name,
                         "weight": 0., "passed": None, "score": None, "grade": "N/A",
                         "gap": 0., "weighted_gap": 0., "raw_value": raw_val, "details": []})
            continue
        score = _severity_to_score(sev)
        if score == 0:
            s_any_zero = True
        subs.append({
            "name": "相机通道配置一致性" if name == "相机对称" else name, "weight": s_weights[name],
            "passed": _sev_to_passed(sev), "score": score, "gap": round(100 - score, 1),
            "weighted_gap": round((100 - score) * s_weights[name], 1),
            "raw_value": raw_val, "direction": "binary",
            "t_min": 1.0, "t_max": 0.0, "current_value": score,
            "details": [], "grade": to_grade(score),
        })

    measured = [s for s in subs if s["score"] is not None]
    denominator = sum(s["weight"] for s in measured)
    for sub in measured:
        sub["weight"] /= denominator
        sub["weighted_gap"] = round((100 - sub["score"]) * sub["weight"], 1)
    total = 0 if s_any_zero else round(sum(s["score"] * s["weight"] for s in measured), 1)
    passed = not s_any_zero

    module = {
        "name": "结构完整性", "module_key": "structure_check", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": s_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(S_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    report["structure"] = result
    report["assessment_complete"] = bool(cs.get("evaluated"))
    report["method_version"] = "structure_camera_configuration_v2"
    if not report["assessment_complete"]:
        report.setdefault("warnings", []).append(cs.get("reason", "相机配置尚未检查"))
        if report.get("verdict") != "DO_NOT_USE":
            report.update(verdict="OBSERVATION", passed=None)
    return report


def assess_from_ir(info, weights: dict = None) -> dict:
    """从 IR DataInfo 评估结构完整性。"""
    topics = {name: ti for name, ti in info.topics.items()}
    return assess(info.path, topics, weights=weights)


def main():
    ap = argparse.ArgumentParser(description="模块 结构完整性评估")
    ap.add_argument("path", help="数据目录路径 (支持 IR: episode/dataset/mp4)")
    ap.add_argument("--required", nargs="*", default=None, help="必需 topic 名称列表")
    ap.add_argument("--weights", default=None, help="权重 JSON 字符串")
    ap.add_argument("--topics-json", default=None, help="TopicInfo JSON 文件路径 (旧格式兼容)")
    args = ap.parse_args()

    from edc.data.ir.metadata import detect_format, FORMAT_UNKNOWN
    fmt = detect_format(args.path)

    weights = json.loads(args.weights) if args.weights else None

    if fmt != FORMAT_UNKNOWN and not args.topics_json:
        from edc.algorithms.quality.config import open_info
        info, reader = open_info(args.path)
        report = assess_from_ir(info, weights)
    else:
        topics = None
        if args.topics_json:
            with open(args.topics_json) as f:
                raw = json.load(f)
            topics = {k: TopicInfo(name=k, **v) for k, v in raw.items()} if isinstance(raw, dict) else None
        report = assess(args.path, topics, args.required, weights)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
