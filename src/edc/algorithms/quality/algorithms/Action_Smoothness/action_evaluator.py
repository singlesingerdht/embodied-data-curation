import numpy as np
from scipy.signal import savgol_filter
from scipy.ndimage import binary_erosion, binary_dilation
from typing import List, Dict, Any, Optional
import json
import os
from pathlib import Path

from edc.algorithms.quality.shared.models import PosePoint


class ActionEvaluator:
    """动作质量评估器（基于jerk的抖动检测实现）

    evaluate 返回一个 dict，包含 score（float）、is_bad（bool）和 details（dict）
    """

    def __init__(self, 
                 window_length: int = 11, 
                 polyorder: int = 3, 
                 threshold: float = 15.0,
                 vel_threshold_factor: float = 0.1,
                 min_segment_frames: int = 20):
        self.window_length = window_length
        self.polyorder = polyorder
        self.threshold = threshold
        self.vel_threshold_factor = vel_threshold_factor
        self.min_segment_frames = min_segment_frames

    def evaluate(self, poses: List[PosePoint]):
        if not poses:
            return {"score": 0.0, "is_bad": False, "details": {"message": "no poses provided"}}
        positions = np.array([[p.x, p.y, p.z] for p in poses], dtype=float)
        timestamps = np.array([p.timestamp for p in poses])
        return self.evaluate_arrays(positions, timestamps)

    def evaluate_arrays(self, positions: np.ndarray, timestamps: np.ndarray):
        """直接评估 numpy 数组 (N×3 位置 + N 时间戳)。"""
        n = positions.shape[0]
        if n <= 3:
            return {"score": 0.0, "is_bad": False, "details": {"message": "insufficient data points"}}

        jitter_score, stationary_mask, smooth_positions, stationary_jerk, motion_jerk_mean = self.calculate_jitter_stationary_with_timestamps(
            positions, timestamps, self.window_length, self.polyorder,
            self.vel_threshold_factor, self.min_segment_frames
        )

        smooth_velocity = np.column_stack([
            np.gradient(smooth_positions[:, axis], timestamps, edge_order=1)
            for axis in range(3)
        ])
        speed = np.linalg.norm(smooth_velocity, axis=1)
        max_speed_index = int(np.argmax(speed)) if len(speed) else 0

        score = float(jitter_score)
        is_bad = bool(score > self.threshold)

        details = {
            "n_frames": int(n),
            "stationary_frames": int(np.sum(stationary_mask)),
            "max_jerk_magnitude": float(np.max(stationary_jerk)) if len(stationary_jerk) > 0 else 0.0,
            "mean_jerk_magnitude": float(np.mean(stationary_jerk)) if len(stationary_jerk) > 0 else 0.0,
            "motion_jerk_mean": float(motion_jerk_mean),
            "jerk_signal": "filtered_position",
            "filter": {"name": "savgol", "window_length": self.window_length,
                        "polyorder": self.polyorder},
            "time_axis": self._time_axis_details(timestamps),
            "speed": {
                "unit": "m/s",
                "p50": float(np.percentile(speed, 50)),
                "p95": float(np.percentile(speed, 95)),
                "p99": float(np.percentile(speed, 99)),
                "max": float(speed[max_speed_index]),
                "max_index": max_speed_index,
                "max_time_seconds_from_start": float(timestamps[max_speed_index] - timestamps[0]),
            },
            "stationary_jerk_evaluated": bool(np.any(stationary_mask)),
            "stationary_segmentation": {'vel_threshold_factor': self.vel_threshold_factor,
                                         'min_segment_frames': self.min_segment_frames,
                                         'transition_guard_frames': max(1, int(self.window_length) // 2)},
        }
        evaluated = details['stationary_jerk_evaluated']
        return {"score": score, "is_bad": is_bad, "details": details,
                "assessment_complete": evaluated,
                "warnings": [] if evaluated else [
                    '未识别到满足当前分段条件的静止段，静止抖动未评估；请检查轨迹覆盖与分段参数']}

    def calculate_jitter_stationary_with_timestamps(self, positions: np.ndarray, timestamps: np.ndarray, 
                                                  window_length: int = 11, polyorder: int = 3,
                                                  vel_threshold_factor: float = 0.2,
                                                  min_segment_frames: int = 10) -> tuple:
        """
        计算专门用于静止期的抖动分数，使用实际时间戳。
        使用基于速度的掩码识别低运动段，应用腐蚀去除短片段，
        对平滑后的位置计算 jerk，并用静止掩码提取静止段评分。
        """
        if len(positions.shape) != 2 or positions.shape[1] != 3:
            raise ValueError("Positions must be a 2D array of shape (N, 3)")
        
        if positions.shape[0] < 4:
            return 0.0, np.zeros(len(positions), dtype=bool), np.zeros_like(positions), np.array([0.0]), 0.0
        
        n = positions.shape[0]
        
        # 如果时间戳数量不匹配，扩展为统一的时间间隔
        if len(timestamps) != n:
            raise ValueError(f"Timestamp count ({len(timestamps)}) does not match position count ({n})")

        timestamps = np.asarray(timestamps, dtype=float)
        if not np.isfinite(timestamps).all():
            raise ValueError("Timestamps must be finite")
        dt_values = np.diff(timestamps)
        if np.any(dt_values <= 0):
            raise ValueError("Timestamps must be strictly increasing")

        # Savitzky-Golay assumes a uniform sample grid.  The recorded time
        # axis is retained for velocity segmentation, while the derivative
        # signal is evaluated on a uniform grid using the robust median period.
        nominal_dt = float(np.median(dt_values))
        uniform_count = max(4, int(np.floor((timestamps[-1] - timestamps[0]) / nominal_dt)) + 1)
        uniform_timestamps = timestamps[0] + np.arange(uniform_count, dtype=float) * nominal_dt
        if uniform_timestamps[-1] < timestamps[-1]:
            uniform_timestamps = np.append(uniform_timestamps, uniform_timestamps[-1] + nominal_dt)
        resampled_positions = np.column_stack([
            np.interp(uniform_timestamps, timestamps, positions[:, axis])
            for axis in range(3)
        ])
        
        # 应用Savitzky-Golay平滑滤波器到整个轨迹
        if window_length > resampled_positions.shape[0]:
            window_length = min(resampled_positions.shape[0], 11)
            if window_length % 2 == 0:
                window_length -= 1
        
        if window_length < polyorder + 2:
            polyorder = min(polyorder, window_length - 2)
            if polyorder < 0:
                polyorder = 2
        
        smooth_uniform_positions = np.zeros_like(resampled_positions)
        for i in range(3):  # 分别对每个轴应用平滑
            smooth_uniform_positions[:, i] = savgol_filter(
                resampled_positions[:, i], window_length, polyorder)

        # Return the filtered signal on the original row mapping.  This keeps
        # the stationary mask and evidence aligned to source samples.
        smooth_positions = np.column_stack([
            np.interp(timestamps, uniform_timestamps, smooth_uniform_positions[:, axis])
            for axis in range(3)
        ])
        
        # 计算用于静止检测的速度幅值 (使用平滑后位置, DF 方案: 防高频噪声干扰)
        if len(timestamps) > 1:
            smooth_velocities = np.zeros_like(smooth_positions)
            for i in range(3):
                smooth_velocities[:, i] = np.gradient(
                    smooth_positions[:, i], timestamps, edge_order=1)
        else:
            smooth_velocities = np.zeros_like(smooth_positions)

        velocity_magnitudes = np.linalg.norm(smooth_velocities, axis=1)
        
        # 确定速度阈值
        max_vel = np.max(velocity_magnitudes)
        if max_vel == 0:
            # 如果最大速度为0，设置一个很小的阈值以避免阈值为0
            vel_threshold = 1e-6
        else:
            vel_threshold = vel_threshold_factor * max_vel
        
        # 创建低速（静止）段的掩码
        stationary_mask = velocity_magnitudes < vel_threshold
        
        # 计算最小段长度，确保不超过掩码长度
        calculated_min_frames = max(int(0.01 * len(positions)), min_segment_frames)
        if calculated_min_frames > len(stationary_mask):
            calculated_min_frames = len(stationary_mask)
        
        # 只保留足够长的低速连续段。不要先膨胀再恢复，否则一个短于
        # min_segment_frames 的真实运动片段会被重新填回静止掩码，并把
        # 运动边界的高 jerk 错算为静止抖动。
        if calculated_min_frames <= len(stationary_mask) and calculated_min_frames > 0:
            # 确保结构元素大小为奇数
            if calculated_min_frames % 2 == 0:
                calculated_min_frames -= 1
                if calculated_min_frames <= 0:
                    calculated_min_frames = 1
            
            # 创建腐蚀结构元素
            structure = np.ones(calculated_min_frames, dtype=bool)
            eroded_mask = binary_erosion(stationary_mask, structure=structure)
            # 恢复长静止段的原始覆盖，但保留所有原始动态帧为 false。
            final_mask = stationary_mask & binary_dilation(eroded_mask, structure=structure)
        else:
            # 如果最小帧要求太大，使用原始掩码
            final_mask = stationary_mask

        # 平滑窗口跨越静止/运动边界时，边界附近的位置导数仍含有运动
        # 过渡。将半个滤波窗口作为证据保护带，避免把边界瞬态计入静止 jerk。
        transition_guard = max(1, int(window_length) // 2)
        if transition_guard and np.any(~stationary_mask):
            guard_structure = np.ones(2 * transition_guard + 1, dtype=bool)
            guarded_motion = binary_dilation(~stationary_mask, structure=guard_structure)
            final_mask = final_mask & ~guarded_motion
        
        # 检查是否有任何有效的静止段
        if not np.any(final_mask):
            # Keep the filtered signal for diagnostics; evaluate_arrays marks
            # the result unassessed so zero cannot become a perfect grade.
            return 0.0, final_mask, smooth_positions, np.array([0.0]), 0.0
        
        # 在均匀时间轴的平滑位置上计算 jerk（三阶导数）。
        # 直接连续除以平均 dt 会把不规则采样误差带入三阶导；对极短
        # 容器间隔尤其敏感。这里保留源行映射，只在导数阶段使用均匀网格。
        smooth_velocity = np.gradient(
            smooth_uniform_positions, nominal_dt, axis=0, edge_order=2)
        smooth_acceleration = np.gradient(
            smooth_velocity, nominal_dt, axis=0, edge_order=2)
        uniform_jerk = np.gradient(
            smooth_acceleration, nominal_dt, axis=0, edge_order=2)
        smooth_jerk = np.column_stack([
            np.interp(timestamps, uniform_timestamps, uniform_jerk[:, axis])
            for axis in range(3)
        ])
        
        # 计算每个时间点的jerk幅值
        jerk_magnitude = np.linalg.norm(smooth_jerk, axis=1)
        
        # 仅考虑静止期间的jerk值
        stationary_jerk = jerk_magnitude[final_mask]
        
        # 运动段jerk (jerk_min基线: 正常运动中的自然抖动)
        motion_mask = ~final_mask
        motion_jerk = jerk_magnitude[motion_mask] if np.any(motion_mask) else np.array([0.0])
        motion_jerk_mean = float(np.sqrt(np.mean(motion_jerk**2))) if len(motion_jerk) > 0 else 0.0
        
        # 计算jerk幅值的RMS作为抖动分数
        if len(stationary_jerk) == 0:
            print("Warning: No stationary frames left after filtering")
            return 0.0, final_mask, smooth_positions, np.array([0.0])
        
        jitter_score = np.sqrt(np.mean(stationary_jerk**2))
        
        return jitter_score, final_mask, smooth_positions, stationary_jerk, motion_jerk_mean

    @staticmethod
    def _time_axis_details(timestamps: np.ndarray) -> Dict[str, Any]:
        """Describe the recorded timeline and derivative grid used by jerk."""
        values = np.asarray(timestamps, dtype=float)
        if values.size < 2 or not np.isfinite(values).all():
            return {
                "source": "provided_timestamps",
                "resampled_uniformly": False,
                "nominal_dt_seconds": None,
            }
        intervals = np.diff(values)
        nominal = float(np.median(intervals)) if np.all(intervals > 0) else None
        uniform_count = None
        if nominal is not None:
            uniform_count = max(4, int(np.floor((values[-1] - values[0]) / nominal)) + 1)
            if values[0] + (uniform_count - 1) * nominal < values[-1]:
                uniform_count += 1
        return {
            "source": "provided_timestamps",
            "resampled_uniformly": nominal is not None,
            "derivative_method": "median_dt_uniform_resample_then_filtered_position_gradient",
            "nominal_dt_seconds": nominal,
            "interval_min_seconds": float(np.min(intervals)),
            "interval_median_seconds": float(np.median(intervals)),
            "interval_mean_seconds": float(np.mean(intervals)),
            "interval_max_seconds": float(np.max(intervals)),
            "resampled_frames": uniform_count,
        }

    def calculate_dynamic_threshold(self, scores: List[float], k: float = 3.0, floor_threshold: float = 15.0) -> Dict[str, Any]:
        """
        计算动态阈值，类似于Real_Data_Jitter_Quality_Assessment.py中的方法
        结合相对统计分布和绝对安全底限
        """
        if not scores:
            return {
                "final_threshold": floor_threshold,
                "relative_threshold": floor_threshold,
                "floor_threshold": floor_threshold,
                "median_score": 0.0,
                "iqr": 0.0,
                "k": k
            }

        scores_array = np.array(scores)
        
        # Calculate Q1, Q3, and IQR
        q1 = np.percentile(scores_array, 25)
        q3 = np.percentile(scores_array, 75)
        iqr = q3 - q1
        
        median_score = np.median(scores_array)
        relative_threshold = q3 + (k * iqr)
        
        # Calculate final threshold (max of relative and floor)
        final_threshold = max(relative_threshold, floor_threshold)
        
        return {
            "final_threshold": final_threshold,
            "relative_threshold": relative_threshold,
            "floor_threshold": floor_threshold,
            "median_score": float(median_score),
            "iqr": float(iqr),
            "k": k,
            "q1": float(q1),
            "q3": float(q3)
        }

    # ── SLAM 冻结检测 ──

    def detect_slam_freeze(self, poses: List[PosePoint],
                           min_freeze_duration_sec: float = 0.0,
                           fps: float = 20.0,
                           vel_threshold_m_s: float = 0.01) -> dict:
        positions = np.array([[p.x, p.y, p.z] for p in poses], dtype=float)
        timestamps = np.array([p.timestamp for p in poses])
        return self.detect_slam_freeze_arrays(positions, timestamps,
                                              min_freeze_duration_sec, vel_threshold_m_s)

    def detect_slam_freeze_arrays(self, positions: np.ndarray, timestamps: np.ndarray,
                                  min_freeze_duration_sec: float = 1.0,
                                  vel_threshold_m_s: float = 0.01) -> dict:
        """
        SLAM 冻结检测 (数组入口)。fps 从 timestamps 中位 dt 推算。

        排除机器人本来就静止的情况: 冻结段之前必须有运动 (平均速度 > vel_threshold_m_s)。
        """
        n = len(positions)
        if len(timestamps) > 1:
            dt = np.diff(timestamps)
            dt = dt[dt > 0]
            fps = 1.0 / np.median(dt) if len(dt) > 0 else 20.0
        else:
            fps = 20.0

        min_frames = max(3, int(min_freeze_duration_sec * fps)) if min_freeze_duration_sec > 0 else 3
        if n < min_frames:
            return {'has_freeze': False, 'freeze_count': 0, 'max_freeze_length': 0}

        velocities = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        lookback = max(5, min_frames // 2)

        diffs = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        is_same = diffs < 1e-6

        freeze_segments = []
        start = None
        for i, same in enumerate(is_same):
            if same and start is None:
                start = i
            elif not same and start is not None:
                length = i - start + 1
                if length >= min_frames:
                    pre_start = max(0, start - lookback)
                    pre_vel = np.mean(velocities[pre_start:max(pre_start+1, start)]) if start > 0 else 0
                    if pre_vel > vel_threshold_m_s:
                        freeze_segments.append({'start': start, 'length': length,
                                                'duration_sec': round(length / fps, 2),
                                                'pre_velocity_m_s': round(float(pre_vel), 4)})
                start = None
        if start is not None:
            length = n - start
            if length >= min_frames:
                pre_start = max(0, start - lookback)
                pre_vel = np.mean(velocities[pre_start:max(pre_start+1, start)]) if start > 0 else 0
                if pre_vel > vel_threshold_m_s:
                    freeze_segments.append({'start': start, 'length': length,
                                            'duration_sec': round(length / fps, 2),
                                            'pre_velocity_m_s': round(float(pre_vel), 4)})

        max_len = max((s['length'] for s in freeze_segments), default=0)
        return {
            'has_freeze': len(freeze_segments) > 0,
            'freeze_count': len(freeze_segments),
            'max_freeze_length': max_len,
            'segments': freeze_segments,
        }

    # ── JSON 读取 (follow_left/right_position 格式) ──

    @staticmethod
    def read_poses_from_trajectory_json(file_path: str, side: str = "right",
                                         fps: float = 20.0) -> List[PosePoint]:
        """从轨迹 JSON 中提取位姿。timestamp 单位: 秒 (s), 由帧索引 / fps 得到。"""
        with open(file_path) as f:
            data = json.load(f)
        fps = data.get("fps", fps)
        key = f"follow_{side}_position"
        rot_key = f"follow_{side}_rotation"
        poses = []
        for i, item in enumerate(data.get('data', [])):
            if key in item and rot_key in item:
                pos = item[key]
                rot = item[rot_key]
                poses.append(PosePoint(
                    timestamp=float(i) / fps,
                    x=float(pos[0]), y=float(pos[1]), z=float(pos[2]),
                    qx=float(rot[0]), qy=float(rot[1]), qz=float(rot[2]),
                    qw=0.0,
                ))
        return poses
