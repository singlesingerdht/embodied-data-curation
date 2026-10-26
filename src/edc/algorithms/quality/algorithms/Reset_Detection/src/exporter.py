from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import Config

# ── 接入统一输出契约 (shared) ──
_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))
from edc.algorithms.quality.shared.attribution import format_report
from edc.algorithms.quality.shared.quality_reporter import to_grade


_PATH_MESSAGE_MAP: dict[str, str] = {
    "left_arm.ee_position": "左臂末端位置",
    "left_arm.ee_orientation": "左臂末端姿态",
    "left_arm.gripper": "左臂夹爪",
    "left_arm.joint_positions": "左臂关节",
    "left_arm.position": "左臂末端位置",
    "left_arm.rotation": "左臂末端姿态",
    "left_arm.joint_pos": "左臂关节",
    "right_arm.ee_position": "右臂末端位置",
    "right_arm.ee_orientation": "右臂末端姿态",
    "right_arm.gripper": "右臂夹爪",
    "right_arm.joint_positions": "右臂关节",
    "right_arm.position": "右臂末端位置",
    "right_arm.rotation": "右臂末端姿态",
    "base.car_pose_position": "移动底盘位置",
    "base.car_pose_rotation": "移动底盘姿态",
    "lifting.lifting_mechanism_position": "升降机构",
    "head.head_rotation": "头部姿态",
    "arm.left_arm_joint_positions": "左臂关节",
    "arm.right_arm_joint_positions": "右臂关节",
    "arm.gripper_joint_positions": "夹爪",
    "leg.leg_joint_positions": "机器人腿部关节",
    "leg.leg_joint_torques": "机器人腿部关节扭矩",
    "base.base_orientation": "机器人基座姿态",
    "imu.gravity_vector": "重力向量",
    "imu.angular_velocity": "角速度",
    "dexhand.left_dexhand_positions": "左手灵巧手",
    "dexhand.right_dexhand_positions": "右手灵巧手",
}


def _assessment_to_sub(result: dict[str, Any], assessment_type: str,
                       weight: float = 0.5) -> dict[str, Any]:
    """Init/Reset → 标准 sub_indicator (details = 各参数 atom)。

    score/passed 取自 result 的 first_frame_* / last_frame_* 字段;
    details 从 group_metrics 的各参数提取 (name/score/gap/error/tolerance)。
    """
    is_init = assessment_type == "Initialization"
    prefix = "first_frame" if is_init else "last_frame"
    score = float(result.get(f"{prefix}_score", 0.0))
    passed = bool(result.get(f"{prefix}_passed", False))
    params_key = "first_frame_parameters" if is_init else "parameters"

    atoms: list[dict[str, Any]] = []
    for gm in result.get("group_metrics", []):
        for p in gm.get(params_key, []):
            s = float(p.get("score", 100))
            atoms.append({
                "name": _PATH_MESSAGE_MAP.get(p.get("path", ""), p.get("path", "")),
                "score": round(s, 1),
                "gap": round(100.0 - s, 1),
                "val": p.get("error", 0),
                "t_min": p.get("tolerance1", 0),
                "t_max": p.get("tolerance2", 0),
            })
    gap = round(100.0 - score, 1)
    return {
        "name": assessment_type,
        "weight": weight,
        "passed": passed,
        "score": score,
        "gap": gap,
        "weighted_gap": round(gap * weight, 1),
        "raw_value": f"score={score:.1f} | params={len(atoms)} | passed={passed}",
        "details": atoms,
        "grade": to_grade(score),
    }


def build_module_report(result: dict[str, Any]) -> dict[str, Any]:
    """构造单模块 dict → format_report → 输出标准契约。

    module score = mean(Init, Reset); 任一评估熔断(passed=False) → 模块熔断 DO_NOT_USE。
    Init/Reset 作为 sub_indicators, 各参数作为 details 原子 (供 attribution 审计)。
    reasons 保留 Reset-Detection 原有中文归因 (加 [Init]/[Reset] 前缀)。
    """
    init = _assessment_to_sub(result, "Initialization")
    rst = _assessment_to_sub(result, "Reset")
    passed = init["passed"] and rst["passed"]
    total = 0.0 if not passed else round(min(init["score"], rst["score"]), 1)

    reasons: list[str] = []
    for sub, prefix in [(init, "first_frame"), (rst, "last_frame")]:
        for r in result.get(f"{prefix}_reasons", []):
            reasons.append(f"[{sub['name']}] {r}")

    module = {
        "name": "复位检测",
        "module_key": "reset_detection",
        "max_score": 100,
        "total_score": total,
        "passed": passed,
        "sub_indicators": [init, rst],
        "warnings": [],
        "grade": to_grade(total),
        "threshold_profile": {
            "tolerance1_factor": "2σ",
            "tolerance2_factor": "3σ",
            "aggregate": "min(Init,Reset), any-melt→DO_NOT_USE",
        },
    }
    report = format_report(module, 85.0)
    if reasons:
        report["reasons"] = reasons
    report["episode_path"] = result.get("episode_path")
    return report


def export_episode_result(
    result: dict[str, Any],
    config: Config,
    dataset_name: str,
    output_dir: str | Path,
) -> Path:
    """输出单对象模块报告 (统一契约)。load_error → DO_NOT_USE。"""
    episode_name = Path(result["episode_path"]).name
    out_path = Path(output_dir) / dataset_name / f"{episode_name}_detection_output.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if result.get("status") == "load_error":
        err_msg = (result.get("first_frame_reasons") or ["Episode 数据加载失败"])[0]
        fail_sub = lambda name: {                              # noqa: E731
            "name": name, "weight": 0.5, "passed": False, "score": 0.0,
            "gap": 100.0, "weighted_gap": 50.0, "details": [], "grade": "D",
            "raw_value": err_msg,
        }
        module = {
            "name": "复位检测", "module_key": "reset_detection", "max_score": 100,
            "total_score": 0.0, "passed": False,
            "sub_indicators": [fail_sub("Initialization"), fail_sub("Reset")],
            "warnings": [], "grade": "D",
            "threshold_profile": {"failure": "load_error"},
        }
        report = format_report(module, 85.0)
        report["episode_path"] = result.get("episode_path")
    else:
        report = build_module_report(result)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path
