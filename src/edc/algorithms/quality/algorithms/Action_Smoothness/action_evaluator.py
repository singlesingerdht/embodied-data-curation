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

        score = float(jitter_score)
        is_bad = bool(score > self.threshold)

        details = {
            "n_frames": int(n),
            "stationary_frames": int(np.sum(stationary_mask)),
            "max_jerk_magnitude": float(np.max(stationary_jerk)) if len(stationary_jerk) > 0 else 0.0,
            "mean_jerk_magnitude": float(np.mean(stationary_jerk)) if len(stationary_jerk) > 0 else 0.0,
            "motion_jerk_mean": float(motion_jerk_mean),
            "stationary_jerk_evaluated": bool(np.any(stationary_mask)),
            "stationary_segmentation": {'vel_threshold_factor': self.vel_threshold_factor,
                                         'min_segment_frames': self.min_segment_frames},
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
        并在残差上计算jerk以进行评分。
        """
        if len(positions.shape) != 2 or positions.shape[1] != 3:
            raise ValueError("Positions must be a 2D array of shape (N, 3)")
        
        if positions.shape[0] < 4:
            return 0.0, np.zeros(len(positions), dtype=bool), np.zeros_like(positions), np.array([0.0]), 0.0
        
        n = positions.shape[0]
        
        # 如果时间戳数量不匹配，扩展为统一的时间间隔
        if len(timestamps) != n:
            raise ValueError(f"Timestamp count ({len(timestamps)}) does not match position count ({n})")
        
        # 应用Savitzky-Golay平滑滤波器到整个轨迹
        if window_length > positions.shape[0]:
            window_length = min(positions.shape[0], 11)
            if window_length % 2 == 0:
                window_length -= 1
        
        if window_length < polyorder + 2:
            polyorder = min(polyorder, window_length - 2)
            if polyorder < 0:
                polyorder = 2
        
        smooth_positions = np.zeros_like(positions)
        for i in range(3):  # 分别对每个轴应用平滑
            smooth_positions[:, i] = savgol_filter(positions[:, i], window_length, polyorder)
        
        # 计算残差（原始值和平滑值之间的差异）
        residuals = positions - smooth_positions
        
        # 计算用于静止检测的速度幅值 (使用平滑后位置, DF 方案: 防高频噪声干扰)
        if len(timestamps) > 1:
            dt_values = np.diff(timestamps)
            dt_values = np.where(dt_values == 0, np.mean(dt_values[dt_values > 0]) if np.any(dt_values > 0) else 1e-6, dt_values)
            avg_dt = np.mean(dt_values)
            smooth_velocities = np.zeros_like(smooth_positions)
            for i in range(3):
                smooth_velocities[:, i] = np.gradient(smooth_positions[:, i], axis=0) / avg_dt
        else:
            avg_dt = 1.0
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
        
        # gap 填充: 合并相邻静止段 (DF 方案)
        gap = max(2, calculated_min_frames // 2)
        if gap > 1 and len(stationary_mask) > gap:
            stationary_mask = binary_dilation(stationary_mask, structure=np.ones(gap, dtype=bool))

        # 应用腐蚀以去除短片段
        if calculated_min_frames <= len(stationary_mask) and calculated_min_frames > 0:
            # 确保结构元素大小为奇数
            if calculated_min_frames % 2 == 0:
                calculated_min_frames -= 1
                if calculated_min_frames <= 0:
                    calculated_min_frames = 1
            
            # 创建腐蚀结构元素
            structure = np.ones(calculated_min_frames, dtype=bool)
            eroded_mask = binary_erosion(stationary_mask, structure=structure)
            
            # 膨胀恢复原始长度
            dilated_mask = binary_dilation(eroded_mask, structure=structure)
            final_mask = stationary_mask & dilated_mask
        else:
            # 如果最小帧要求太大，使用原始掩码
            final_mask = stationary_mask
        
        # 检查是否有任何有效的静止段
        if not np.any(final_mask):
            # Keep the historical numerical return for callers; evaluate_arrays
            # explicitly marks it unassessed so zero cannot become a perfect grade.
            return 0.0, final_mask, np.zeros_like(positions), np.array([0.0]), 0.0
        
        # 计算平均时间步长用于后续计算
        if len(timestamps) > 1:
            dt_values = np.diff(timestamps)
            dt_values = np.where(dt_values == 0, np.mean(dt_values[dt_values > 0]) if np.any(dt_values > 0) else 1e-6, dt_values)
            avg_dt = np.mean(dt_values)
        else:
            avg_dt = 1.0  # 默认时间步长
        
        # 在残差上计算jerk（三阶导数），使用计算出的平均时间步长
        # 第一阶导数（速度）的残差
        residual_vx = np.gradient(residuals[:, 0]) / avg_dt
        residual_vy = np.gradient(residuals[:, 1]) / avg_dt
        residual_vz = np.gradient(residuals[:, 2]) / avg_dt
        
        # 第二阶导数（加速度）的残差
        residual_ax = np.gradient(residual_vx) / avg_dt
        residual_ay = np.gradient(residual_vy) / avg_dt
        residual_az = np.gradient(residual_vz) / avg_dt
        
        # 第三阶导数（jerk）的残差
        residual_jerk_x = np.gradient(residual_ax) / avg_dt
        residual_jerk_y = np.gradient(residual_ay) / avg_dt
        residual_jerk_z = np.gradient(residual_az) / avg_dt
        
        # 计算每个时间点的jerk幅值
        jerk_magnitude = np.sqrt(residual_jerk_x**2 + residual_jerk_y**2 + residual_jerk_z**2)
        
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
