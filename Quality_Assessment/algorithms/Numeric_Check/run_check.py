"""模块: 数值内容检查 — 可直接运行的评估入口。

用法:
    python -m algorithms.Numeric_Check.run_check <data.json>
    data.json 格式: {"joint_positions": [[...]], "gyro": [[...]], "accel": [[...]],
                      "cmd_positions": [[...]], "pose_freeze_positions": [[...]]}
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

from Quality_Assessment.shared.attribution import format_report
from Quality_Assessment.shared.quality_reporter import compute_quality, to_grade, normalize_weights
from Quality_Assessment.algorithms.Numeric_Check.numeric_check import (
    check_joint_anomaly, check_imu_anomaly, check_cmd_anomaly, check_pose_freeze,
)

NUM_DEFAULTS = {"关节异常": 0.30, "IMU异常": 0.25, "CMD异常": 0.20, "姿态冻结": 0.25}
EVENT_LABELS = {"joint_jump": "相邻关节位置差超阈值",
                "joint_step_peak": "最大相邻关节位置差超阈值",
                "frequent_joint_steps": "相邻关节位置差频繁超阈值"}


def _severity_to_score(sev: str) -> float:
    return {"pass": 100.0, "warning": 70.0, "error": 0.0}.get(sev, 0.0)


def assess(joint_positions: np.ndarray = None,
           gyro: np.ndarray = None, accel: np.ndarray = None,
           cmd_positions: np.ndarray = None,
           pose_freeze_positions: np.ndarray = None,
           weights: dict = None, thresholds: dict = None) -> dict:
    from Quality_Assessment.algorithms.Numeric_Check.settings import thresholds as resolve_thresholds
    settings = resolve_thresholds(thresholds)
    n_weights, n_warns = normalize_weights(NUM_DEFAULTS, weights, "数值内容检查")

    if joint_positions is not None and settings['joint']['limits'] is not None:
        if joint_positions.ndim != 2 or len(settings['joint']['limits']) != joint_positions.shape[1]:
            raise ValueError('Joint limits must match every component of this input field')
    joint = check_joint_anomaly(joint_positions, **settings['joint']) if joint_positions is not None and joint_positions.size > 0 else None
    imu = check_imu_anomaly(gyro if gyro is not None else np.empty((0, 3)), accel if accel is not None else np.empty((0, 3)), **settings['imu']) if any(x is not None and x.size > 0 for x in (gyro, accel)) else None
    cmd = check_cmd_anomaly(cmd_positions, **settings['cmd']) if cmd_positions is not None and cmd_positions.size > 0 else None
    pose = check_pose_freeze(pose_freeze_positions, **settings['pose_freeze']) if pose_freeze_positions is not None and pose_freeze_positions.size > 0 else None

    def _make_sub(name: str, result: dict, weight: float, raw_val: str, skip: bool = False) -> dict:
        if result is None or skip:
            return {"name": name, "weight": 0.0, "passed": None, "score": None,
                    "gap": 0.0, "weighted_gap": 0.0, "raw_value": "跳过 (无数据)",
                    "direction": "binary", "t_min": 1.0, "t_max": 0.0, "current_value": 100.0,
                    "details": [], "grade": "N/A"}
        sev = result.get("severity", "pass")
        score = _severity_to_score(sev)
        return {"name": name, "weight": weight,
                "passed": sev != "error", "score": score,
                "gap": round(100 - score, 1), "weighted_gap": round((100 - score) * weight, 1),
                "raw_value": raw_val, "direction": "binary",
                "t_min": 1.0, "t_max": 0.0, "current_value": score,
                "details": [{"name": EVENT_LABELS.get(e.get("type"), e.get("type", "")), "val": e.get("count", 0),
                             "t_min": 0, "t_max": 0} for e in result.get("events", [])],
                "grade": to_grade(score)}

    subs = [
        _make_sub("关节异常", joint, n_weights["关节异常"],
                  "; ".join(f"{EVENT_LABELS.get(e['type'], e['type'])}={e.get('count','?')}" for e in (joint or {}).get("events", [])) or "无异常"),
        _make_sub("IMU异常", imu, n_weights["IMU异常"],
                  "; ".join(f"{e['type']}={e.get('count','?')}" for e in (imu or {}).get("events", [])) or "无异常"),
        _make_sub("CMD异常", cmd, n_weights["CMD异常"],
                  "; ".join(f"{e['type']}={e.get('count','?')}" for e in (cmd or {}).get("events", [])) or "无异常"),
        _make_sub("姿态冻结", pose, n_weights["姿态冻结"],
                  f"freeze={pose['has_freeze']} count={pose['freeze_count']}" if pose else "跳过",
                  skip=pose is None),
    ]

    measured = [s for s in subs if s["score"] is not None]
    if not measured:
        from framework.readers.base import make_ir_report
        from framework.ir.metadata import IRNotProvided
        return make_ir_report("数值内容检查", "numeric_check", IRNotProvided("数值输入", "numeric"))
    weight_sum = sum(s["weight"] for s in measured)
    for sub in measured:
        sub["weight"] = sub["weight"] / weight_sum if weight_sum else 1 / len(measured)
        sub["weighted_gap"] = round(sub["gap"] * sub["weight"], 1)
    any_zero = any(s["score"] == 0 for s in measured)
    total = 0 if any_zero else round(sum(s["score"] * s["weight"] for s in measured), 1)
    passed = not any_zero

    module = {
        "name": "数值内容检查", "module_key": "numeric_check", "max_score": 100,
        "total_score": total, "passed": passed, "sub_indicators": subs,
        "warnings": n_warns, "grade": to_grade(total),
        "threshold_profile": {"thresholds": dict(NUM_DEFAULTS)},
    }
    report = format_report(module, threshold=85.0)
    report["method_version"] = "numeric_available_inputs_v4"
    report["numeric_thresholds"] = settings
    report["difference_basis"] = "sample-index differences; not physical velocity/jerk or Home reset"
    report["numeric"] = {"joint": joint, "imu": imu, "cmd": cmd, "pose_freeze": pose}
    return report


def _load_channel_values(info, reader, channel_name, field):
    """从 IR 通道按物理量提取 N×D 数组 (走 read_field 统一路径)。"""
    return reader.read_field(info, channel_name, field)


def assess_from_ir(info, reader, weights: dict = None) -> dict:
    """从 IR DataInfo 评估数值内容。"""
    joint = gyro = accel = cmd = pose = None
    try:
        name, ti = reader.require_joint(info, field="position")
        joint = reader.read_field(info, name, "position")
    except Exception:
        pass
    try:
        name, ti = reader.require_pose(info, side="right", field="position")
        pose = reader.read_field(info, name, "position")
    except Exception:
        pass
    for name, topic in info.topics.items():
        if not topic.value_available:
            continue
        if topic.modality == "imu":
            if gyro is None and "ang_vel" in (topic.fields or []):
                gyro = reader.read_field(info, name, "ang_vel")
            if accel is None and "lin_acc" in (topic.fields or []):
                accel = reader.read_field(info, name, "lin_acc")
        if topic.modality == "cmd" and cmd is None and "joint_q" in (topic.fields or []):
            cmd = reader.read_field(info, name, "joint_q")
    if all(value is None for value in (joint, pose, gyro, accel, cmd)):
        from framework.readers.base import make_ir_report
        from framework.ir.metadata import IRNotProvided
        exc = IRNotProvided("joint/pose/imu/cmd 数值通道", info.format, info.capability_summary())
        return make_ir_report("数值内容检查", "numeric_check", exc)
    return assess(joint, gyro, accel, cmd, pose, weights)


def main():
    ap = argparse.ArgumentParser(description="模块 数值内容检查")
    ap.add_argument("input", help="数值数据 JSON 或数据目录路径 (IR: episode/dataset)")
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
            data = json.load(f)
        joint = np.array(data.get("joint_positions", [])) if "joint_positions" in data else None
        gyro = np.array(data.get("gyro", [])) if "gyro" in data else None
        accel = np.array(data.get("accel", [])) if "accel" in data else None
        cmd = np.array(data.get("cmd_positions", [])) if "cmd_positions" in data else None
        pose = np.array(data.get("pose_freeze_positions", [])) if "pose_freeze_positions" in data else None
        report = assess(joint, gyro, accel, cmd, pose, weights)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
