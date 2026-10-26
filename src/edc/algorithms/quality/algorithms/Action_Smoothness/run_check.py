"""模块 C: 动作平滑度 — 可直接运行的评估入口。

用法:
    python -m action_smoothness.run_check <pose_file> [--side right] [--weights ...]
    支持格式: .txt (8列: ts x y z qx qy qz qw) / .json (follow_{side}_position)

输出: score + grade + verdict + reasons + 归因 (JSON)。
"""
from pathlib import Path
import sys
import os
import json
import argparse
import time
import numpy as np

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from edc.algorithms.quality.shared.models import PosePoint
from edc.algorithms.quality.shared.utils.timer import measure_time
from edc.algorithms.quality.algorithms.Action_Smoothness.action_evaluator import ActionEvaluator
from edc.algorithms.quality.algorithms.Action_Smoothness.scoring import build_action_smoothness_module
from edc.algorithms.quality.shared.attribution import format_report


def _report(module):
    report = format_report(module)
    report['assessment_complete'] = module['assessment_complete']
    report['method_version'] = 'smoothness_stationary_evidence_v3'
    if not module['assessment_complete'] and report['verdict'] != 'DO_NOT_USE':
        report.update(score=None, passed=None, grade='N/A', verdict='OBSERVATION')
        report['reasons'].append('仅完成有输入的子项；静止抖动或冻结检查缺少样本，整体未完整评估')
    return report


def read_poses_from_txt(file_path: str):
    """读取 8 列格式: timestamp x y z qx qy qz qw"""
    poses = []
    with open(file_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 8:
                try:
                    poses.append(PosePoint(
                        timestamp=float(parts[0]),
                        x=float(parts[1]), y=float(parts[2]), z=float(parts[3]),
                        qx=float(parts[4]), qy=float(parts[5]), qz=float(parts[6]), qw=float(parts[7]),
                    ))
                except ValueError:
                    continue
    return poses


def assess(pose_file: str, side: str = "right", weights: dict = None) -> dict:
    """对单条轨迹执行动作平滑度评估 → 统一输出契约。"""
    t0 = time.time()
    evaluator = ActionEvaluator()

    if pose_file.endswith(".json"):
        poses = evaluator.read_poses_from_trajectory_json(pose_file, side)
    else:
        poses = read_poses_from_txt(pose_file)

    # jerk
    jerk_result, _ = measure_time(evaluator.evaluate)(poses)
    jerk_result["details"]["threshold"] = evaluator.threshold

    # SLAM 冻结
    slam_result = (evaluator.detect_slam_freeze(poses)
                   if len(poses) >= 5
                   else {"has_freeze": False, "freeze_count": 0, "max_freeze_length": 0})
    slam_result['assessment_complete'] = len(poses) >= 5

    module = build_action_smoothness_module(jerk_result, slam_result, weights)
    report = _report(module)
    report["input"] = pose_file
    report["side"] = side
    report["num_poses"] = len(poses)
    report["raw"] = {
        "jerk": {"score": jerk_result.get("score"), "is_bad": jerk_result.get("is_bad"),
                 "details": jerk_result.get("details")},
        "slam_freeze": slam_result,
    }
    report["process_time_ms"] = round((time.time() - t0) * 1000, 1)
    return report


def assess_from_ir(info, reader, side: str = "right", weights: dict = None) -> dict:
    """从 IR 层评估动作平滑度 (使用 read_field 统一取数, 不重读 JSON)。"""
    try:
        name, ti = reader.require_pose(info, side=side)
    except Exception as exc:
        from edc.data.readers.base import make_ir_report
        return make_ir_report("动作平滑度", "action_smoothness", exc)

    t0 = time.time()
    evaluator = ActionEvaluator()

    positions = reader.read_field(info, name, "position")
    if positions is None or positions.size == 0 or positions.shape[1] != 3:
        from edc.data.readers.base import make_ir_report
        from edc.data.ir.metadata import IRNotProvided
        D = positions.shape[1] if (positions is not None and positions.ndim == 2) else "?"
        layout = getattr(ti, "pose_layout", None) or {}
        hint = f" (通道 {name}, D={D})"
        if layout.get("method") == "unknown":
            hint += f"; 布局推断失败: {layout.get('reason', '')}"
        hint += "; 可在 profile yaml 声明该通道的 pose_layout"
        return make_ir_report("动作平滑度", "action_smoothness",
                              IRNotProvided(f"pose 位置列{hint}", info.format))

    if ti.timestamps is None or len(ti.timestamps) != len(positions):
        from edc.data.ir.metadata import IRNotProvided
        from edc.data.readers.base import make_ir_report
        return make_ir_report("动作平滑度", "action_smoothness", IRNotProvided("显式采样时间轴", info.format))
    else:
        timestamps = np.array(ti.timestamps)

    if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
        return {"score": None, "verdict": "NEEDS_REVIEW", "reasons": ["时间轴非有限或非严格递增"], "method_version": "smoothness_explicit_time_v2"}
    jerk_result, _ = measure_time(evaluator.evaluate_arrays)(positions, timestamps)
    jerk_result["details"]["threshold"] = evaluator.threshold

    slam_result = (evaluator.detect_slam_freeze_arrays(positions, timestamps)
                   if len(positions) >= 5
                   else {"has_freeze": False, "freeze_count": 0, "max_freeze_length": 0})
    slam_result['assessment_complete'] = len(positions) >= 5

    module = build_action_smoothness_module(jerk_result, slam_result, weights)
    report = _report(module)
    report["input"] = ti.source
    report["side"] = side
    report["num_poses"] = len(positions)
    report["process_time_ms"] = round((time.time() - t0) * 1000, 1)
    report["pose_layout"] = ti.pose_layout
    report['raw'] = {'jerk': jerk_result, 'slam_freeze': slam_result}
    report['input_channel'] = name
    return report


def main():
    ap = argparse.ArgumentParser(description="模块C 动作平滑度评估")
    ap.add_argument("input", help="位姿文件路径 (.txt/.json) 或数据目录路径 (IR: episode/dataset)")
    ap.add_argument("--side", default="right", choices=["left", "right"],
                    help="JSON 文件的臂侧 (follow_{side}_position)")
    ap.add_argument("--weights", default=None, help="权重 JSON 字符串")
    args = ap.parse_args()

    from edc.data.ir.metadata import detect_format, FORMAT_EPISODE, FORMAT_DATASET, FORMAT_UNKNOWN, FORMAT_JSON
    fmt = detect_format(args.input)
    weights = json.loads(args.weights) if args.weights else None

    if fmt not in (FORMAT_UNKNOWN, FORMAT_JSON):
        from edc.algorithms.quality.config import open_info
        info, reader = open_info(args.input)
        report = assess_from_ir(info, reader, args.side, weights)
    elif os.path.exists(args.input):
        report = assess(args.input, args.side, weights)
    else:
        print(json.dumps({"verdict": "DO_NOT_USE", "reasons": [f"file not found: {args.input}"]},
                          ensure_ascii=False, indent=2))
        sys.exit(1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
