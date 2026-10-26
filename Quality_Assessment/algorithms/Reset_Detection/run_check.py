"""模块: 复位检测 — 可直接运行的评估入口 (统一输出契约)。

用法:
    python -m algorithms.Reset_Detection.run_check <path> [--robot auto]
    支持 IR: episode 目录 / rosbag / parquet / lerobot / dataset

双臂支持: 有左右臂关节通道时分别检查, 每臂 2 个子指标, 取最差。
评分: 绝对角度 (mean |末-首|°, T_min=15°/T_max=5°), 归一化值放 details 参考。
"""
import sys
import os
import json
import argparse
import time
import numpy as np
from pathlib import Path

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from Quality_Assessment.shared.attribution import format_report
from Quality_Assessment.shared.quality_reporter import to_grade, normalize_weights, compute_quality

RD_DEFAULTS = {"首帧复位": 0.50, "末帧复位": 0.50}

ABS_TMIN = 15.0   # 平均绝对角度差 ≥15° → 0 分
ABS_TMAX = 5.0    # 平均绝对角度差 ≤5° → 100 分


def _find_arm_channels(info):
    """找到所有 joint_state 通道 (含 position 字段), 按 left/right 分组。"""
    candidates = []
    for name, ti in info.topics.items():
        if ti.modality == "joint_state" and ti.value_available:
            if ti.fields is None or "position" in ti.fields:
                candidates.append(name)
    arms = {}
    for name in candidates:
        if 'left' in name.lower():
            arms['left'] = name
        elif 'right' in name.lower():
            arms['right'] = name
    if not arms and candidates:
        arms['unknown'] = candidates[0]
    return arms


def _score_arm(joint_data, side):
    """对单臂计算首帧/末帧复位分数 (绝对角度制)。
    首帧: T_min=15°, T_max=5°  (真复位)
    末帧: T_min=30°, T_max=5°  (中→末差天然大于首→末, 阈值放宽)
    """
    n = joint_data.shape[0]
    first = joint_data[0]
    last = joint_data[-1]
    mid = joint_data[n // 2]

    diff_first = np.abs(last - first)
    diff_last = np.abs(last - mid)
    mean_deg_first = float(np.mean(np.degrees(diff_first)))
    mean_deg_last = float(np.mean(np.degrees(diff_last)))

    q1 = compute_quality(mean_deg_first, ABS_TMIN, ABS_TMAX, "inverse")
    q2 = compute_quality(mean_deg_last, 30.0, ABS_TMAX, "inverse")

    ranges = np.ptp(joint_data, axis=0) + 1e-9
    norm_first = float(np.mean(diff_first / ranges))
    norm_last = float(np.mean(diff_last / ranges))

    return {
        "side": side, "frames": n,
        "first_score": round(q1 * 100, 1),
        "last_score": round(q2 * 100, 1),
        "mean_deg_first": round(mean_deg_first, 1),
        "mean_deg_last": round(mean_deg_last, 1),
        "joint_deg_first": [round(d, 1) for d in np.degrees(diff_first)],
        "joint_deg_last": [round(d, 1) for d in np.degrees(diff_last)],
        "norm_first": round(norm_first, 3),
        "norm_last": round(norm_last, 3),
    }


def assess_from_ir(info, reader, weights: dict = None) -> dict:
    """从 IR 评估复位检测: 双臂首末帧关节位置一致性。"""
    from framework.readers.base import make_ir_report
    from framework.ir.metadata import IRNotProvided

    rd_weights, rd_warns = normalize_weights(RD_DEFAULTS, weights, "复位检测")

    arms = _find_arm_channels(info)
    if not arms:
        exc = IRNotProvided("joint_state 通道 (field=position)", info.format, info.capability_summary())
        return make_ir_report("复位检测", "reset_detection", exc)

    arm_results = {}
    for side, name in arms.items():
        data = reader.read_field(info, name, "position")
        if data is None or data.shape[0] < 2:
            continue
        arm_results[side] = _score_arm(data, side)

    if not arm_results:
        exc = IRNotProvided("关节数值数据", info.format, info.capability_summary())
        return make_ir_report("复位检测", "reset_detection", exc)

    # 取各臂最差
    worst_first = min(r["first_score"] for r in arm_results.values())
    worst_last = min(r["last_score"] for r in arm_results.values())

    first_side = [s for s, r in arm_results.items() if r["first_score"] == worst_first][0]
    last_side = [s for s, r in arm_results.items() if r["last_score"] == worst_last][0]
    first_deg = arm_results[first_side]["mean_deg_first"]
    last_deg = arm_results[last_side]["mean_deg_last"]

    # 构建 per-arm 描述文本
    arm_desc = []
    for side, r in arm_results.items():
        max_j = r["joint_deg_first"].index(max(r["joint_deg_first"]))
        arm_desc.append(
            f"{side}臂: 首帧差={r['mean_deg_first']}°(j{max_j} = {r['joint_deg_first'][max_j]}°) "
            f"末帧差={r['mean_deg_last']}° (归一化首={r['norm_first']}, 末={r['norm_last']})"
        )

    subs = [
        {"name": "首帧复位", "weight": rd_weights["首帧复位"],
         "passed": worst_first > 0, "score": worst_first,
         "gap": round(100 - worst_first, 1),
         "weighted_gap": round((100 - worst_first) * rd_weights["首帧复位"], 1),
         "raw_value": f"{first_side}臂 首帧vs末帧, mean_diff={first_deg}°  |  {'; '.join(arm_desc)}",
         "direction": "inverse", "t_min": ABS_TMIN, "t_max": ABS_TMAX,
         "current_value": first_deg, "details": list(arm_results.values()),
         "grade": to_grade(worst_first)},
        {"name": "末帧复位", "weight": rd_weights["末帧复位"],
         "passed": worst_last > 0, "score": worst_last,
         "gap": round(100 - worst_last, 1),
         "weighted_gap": round((100 - worst_last) * rd_weights["末帧复位"], 1),
         "raw_value": f"{last_side}臂 中帧vs末帧, mean_diff={last_deg}°  |  {'; '.join(arm_desc)}",
         "direction": "inverse", "t_min": ABS_TMIN, "t_max": ABS_TMAX,
         "current_value": last_deg, "details": list(arm_results.values()),
         "grade": to_grade(worst_last)},
    ]

    # 只首帧复位触发熔断 (末帧差大可能是任务动程, 非真漂移)
    any_zero = subs[0]["score"] == 0
    total = 0 if any_zero else round(sum(s["score"] * s["weight"] for s in subs), 1)
    passed = not any_zero

    module = {
        "name": "复位检测", "module_key": "reset_detection", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": rd_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(RD_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    report["reset_detection"] = {
        "arms": list(arm_results.keys()),
        "channels": {s: r.get("channel", arms[s]) for s, r in arm_results.items()},
        "scoring": "absolute_degrees",
        "arm_details": arm_results,
    }
    return report


def main():
    ap = argparse.ArgumentParser(description="模块 复位检测")
    ap.add_argument("input", help="数据路径 (IR: episode/rosbag/parquet/lerobot/dataset)")
    ap.add_argument("--weights", default=None, help="权重 JSON 字符串")
    args = ap.parse_args()

    from framework.ir.metadata import detect_format, FORMAT_UNKNOWN, FORMAT_JSON
    fmt = detect_format(args.input)
    weights = json.loads(args.weights) if args.weights else None

    if fmt not in (FORMAT_UNKNOWN, FORMAT_JSON):
        from Quality_Assessment.config import open_info
        info, reader = open_info(args.input)
        report = assess_from_ir(info, reader, weights)
    else:
        print(json.dumps({"verdict": "DO_NOT_USE", "reasons": [f"unsupported format: {fmt}"]},
                          ensure_ascii=False, indent=2))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()