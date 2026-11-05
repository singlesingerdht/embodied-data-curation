"""数值内容检查: 关节/IMU/CMD 异常 (DF 方案)

检查项:
  - 关节异常: NaN/Inf, 范围超限, 相邻位置差
  - IMU 异常: 角速度/加速度爆表, 偏置/噪声
  - CMD 异常: 位置/速度范围超限
  - 姿态冻结: 连续帧位姿不变 (跨模态)
"""
import numpy as np
from typing import List, Optional

FREEZE_EPS = 1e-6


def check_joint_anomaly(positions: np.ndarray,  # (N, D)
                        vel_threshold: float = 1.0,  # legacy parameter: rad per sample, not rad/s
                        velocity_peak: float = 20.0,
                        reset_ratio: float = 0.6,
                        limits: Optional[List[tuple]] = None) -> dict:
    """关节异常: NaN/Inf, 范围, 相邻位置差（未除以时间）。"""
    if positions.size == 0:
        return {"severity": "pass", "has_anomaly": False}
    result = {"nan_count": int(np.sum(~np.isfinite(positions))),
              "severity": "pass", "has_anomaly": False, "events": []}
    if result["nan_count"] > 0:
        result["severity"] = "error"
        result["has_anomaly"] = True
        result["events"].append({"type": "joint_nan", "count": result["nan_count"]})

    if limits:
        for j, (lo, hi) in enumerate(limits):
            out = np.sum((positions[:, j] < lo) | (positions[:, j] > hi))
            if out > 0:
                result["events"].append({"type": "joint_range", "joint": j, "count": int(out),
                                         "limit": [lo, hi]})
                result["has_anomaly"] = True
                if result["severity"] != "error":
                    result["severity"] = "warning"

    if positions.shape[0] >= 2:
        diffs = np.abs(np.diff(positions, axis=0))
        jumps = np.any(diffs > vel_threshold, axis=1)
        if np.any(jumps):
            result["events"].append({"type": "joint_jump", "count": int(np.sum(jumps)),
                                     "threshold": vel_threshold,
                                     "quantity": "absolute_adjacent_position_difference",
                                     "threshold_unit": "rad per adjacent sample",
                                     "count_basis": "adjacent_intervals",
                                     "component_exceedance_count": int(np.sum(diffs > vel_threshold)),
                                     "examples": [
                                         {"previous_row": int(i), "current_row": int(i + 1),
                                          "joint_index": int(j),
                                          "previous_value": float(positions[i, j]),
                                          "current_value": float(positions[i + 1, j]),
                                          "absolute_difference": float(diffs[i, j])}
                                         for i, j in np.argwhere(diffs > vel_threshold)[:100]],
                                     "examples_truncated": int(np.sum(diffs > vel_threshold)) > 100})
            result["has_anomaly"] = True
            if result["severity"] == "pass":
                result["severity"] = "warning"
        max_jump = float(np.max(diffs))
        if max_jump > velocity_peak:
            result["events"].append({"type": "joint_step_peak", "max": round(max_jump, 2),
                                     "threshold": velocity_peak,
                                     "quantity": "absolute_adjacent_position_difference",
                                     "threshold_unit": "rad per adjacent sample"})
            result["severity"] = "error"
        if diffs.shape[0] >= 2:
            jump_count = np.sum(np.any(diffs > vel_threshold, axis=1))
            if jump_count > reset_ratio * diffs.shape[0]:
                result["events"].append({"type": "frequent_joint_steps", "count": int(jump_count)})
                if result["severity"] != "error":
                    result["severity"] = "error"

    result["difference_basis"] = "adjacent samples; not velocity and not calibrated reset"
    return result


def check_imu_anomaly(gyro: np.ndarray, accel: np.ndarray,
                      gyro_max: float = 10.0,  # rad/s
                      acc_max: float = 196.2,  # m/s^2 (20g)
                      gyro_bias_max: float = 0.05,
                      acc_bias_max: float = 0.5,
                      min_static_frames: int = 50) -> dict:
    """IMU 异常: 爆表 + 偏置"""
    result = {"severity": "pass", "has_anomaly": False, "events": []}

    if gyro.size > 0:
        gyro_mag = np.linalg.norm(gyro, axis=1) if gyro.ndim == 2 else np.abs(gyro)
        gyro_above = np.sum(gyro_mag > gyro_max)
        if gyro_above > 0:
            result["events"].append({"type": "imu_gyro_over", "count": int(gyro_above),
                                     "threshold": gyro_max})
            result["severity"] = "error"
            result["has_anomaly"] = True

    if accel.size > 0:
        acc_mag = np.linalg.norm(accel, axis=1) if accel.ndim == 2 else np.abs(accel)
        acc_above = np.sum(acc_mag > acc_max)
        if acc_above > 0:
            result["events"].append({"type": "imu_acc_over", "count": int(acc_above),
                                     "threshold": acc_max})
            result["severity"] = "error"
            result["has_anomaly"] = True

    if gyro.size >= min_static_frames and gyro.ndim == 2:
        gmag = np.linalg.norm(gyro, axis=1)
        static = np.abs(np.diff(gmag)) < 0.02
        runs = _find_runs(~static, min_static_frames)
        for start, end in runs:
            seg = gyro[start:end]
            gmean = np.linalg.norm(np.mean(seg, axis=0))
            if gmean > gyro_bias_max:
                result["events"].append({"type": "imu_gyro_bias", "frames": int(end - start),
                                         "mean": round(float(gmean), 4),
                                         "threshold": gyro_bias_max})
                if result["severity"] == "pass":
                    result["severity"] = "warning"
                result["has_anomaly"] = True
            if accel.size > 0:
                aseg = accel[start:end]
                acc_offset = abs(np.linalg.norm(np.mean(aseg, axis=0)) - 9.81)
                if acc_offset > acc_bias_max:
                    result["events"].append({"type": "imu_acc_bias", "frames": int(end - start),
                                             "offset": round(float(acc_offset), 4),
                                             "threshold": acc_bias_max})

    return result


def _find_runs(mask: np.ndarray, min_len: int) -> List[tuple]:
    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
        elif not v and start is None:
            start = i
    if start is not None and len(mask) - start >= min_len:
        runs.append((start, len(mask)))
    return runs


def check_pose_freeze(positions: np.ndarray,
                      min_run: int = 3,
                      lookback: int = 10,
                      vel_threshold: float = 0.01,
                      freeze_eps: float = FREEZE_EPS) -> dict:
    """位姿冻结: 连续帧位姿差 < FREEZE_EPS"""
    if positions.shape[0] < min_run:
        return {"has_freeze": False, "freeze_count": 0, "segments": [], "severity": "pass"}
    diffs = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    is_same = diffs < freeze_eps
    velocities = diffs  # same as diffs for per-frame velocity
    segments = []
    start = None
    for i, same in enumerate(is_same):
        if same and start is None:
            start = i
        elif not same and start is not None:
            length = i - start + 1
            if length >= min_run:
                pre_start = max(0, start - lookback)
                pre_vel = float(np.mean(velocities[pre_start:max(pre_start + 1, start)])) if start > 0 else 0
                if pre_vel > vel_threshold:
                    segments.append({"start": int(start), "length": int(length), "pre_vel": round(pre_vel, 4)})
            start = None
    if start is not None:
        length = positions.shape[0] - start
        if length >= min_run:
            pre_start = max(0, start - lookback)
            pre_vel = float(np.mean(velocities[pre_start:max(pre_start + 1, start)])) if start > 0 else 0
            if pre_vel > vel_threshold:
                segments.append({"start": int(start), "length": int(length), "pre_vel": round(pre_vel, 4)})
    return {
        "severity": "warning" if segments else "pass",
        "has_freeze": len(segments) > 0,
        "freeze_count": len(segments),
        "segments": segments,
    }


def check_cmd_anomaly(positions: np.ndarray,
                      jerk_threshold: float = 10.0) -> dict:
    """CMD finite values and second sample differences; no physical jerk without time."""
    if positions.size == 0:
        return {"severity": "pass", "has_anomaly": False}
    result = {"severity": "pass", "has_anomaly": False, "events": []}
    nan_count = int(np.sum(~np.isfinite(positions)))
    if nan_count > 0:
        result["events"].append({"type": "cmd_nan", "count": nan_count})
        result["severity"] = "error"
        result["has_anomaly"] = True
    if positions.shape[0] >= 3:
        jerk = np.abs(np.gradient(np.gradient(positions, axis=0), axis=0))
        jerk_mag = np.linalg.norm(jerk, axis=1) if jerk.ndim == 2 else np.abs(jerk)
        above = np.sum(jerk_mag > jerk_threshold)
        if above > 0:
            result["events"].append({"type": "cmd_second_sample_difference", "count": int(above), "threshold": jerk_threshold})
            if result["severity"] == "pass":
                result["severity"] = "warning"
            result["has_anomaly"] = True
    return result
