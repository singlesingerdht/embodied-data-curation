#!/usr/bin/env python3
"""
子任务分段评估与进度跌落筛选脚本

功能：
1. 根据子任务时间分割，分别评估每个子任务
2. 在每个子任务内部检测进度跌落（回退）
3. 根据跌落情况筛选轨迹
4. 生成综合报告（含子任务级别的跌落分析）

特点：
- 支持Average模式（Forward + Incremental + Backward）
- 子任务内回退检测
- 回退模式分类（V形/凹槽）
- 误判风险评估
- 子任务完成度记录（<80%进度，仅记录不参与裁决）
"""

import os
import sys
import json
import time
import cv2
import shutil
import re
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .grm_inference import GRMInference, plot_video_reward

class FilterConfig:
    """筛选阈值和参数"""
    REGRESSION_THRESHOLD = -0.10      # 瞬时回退阈值（单帧 hop < -10%）
    NEAR_ZERO_THRESHOLD = 0.03        # 判断 hop 是否接近 0 的阈值
    POST_WINDOW_SIZE = 5              # 回退后检查的样本数
    HIGH_PROGRESS_THRESHOLD = 0.8     # 高进度阈值（用于误判判断）
    LOW_PROGRESS_THRESHOLD = 0.5      # 低进度阈值
    MIN_CONSECUTIVE_REGRESSIONS = 2   # 最小连续回退数
    SUBTASK_COMPLETION_THRESHOLD = 0.8  # 子任务完成度阈值

    # 累计回退检测 — 连续负 hop 窗口
    CUM_REGRESSION_THRESHOLD = -0.10  # 累计 drop < -10% 则触发
    CUM_REGRESSION_MAX_SAMPLES = 5   # 最多连续 5 个负 hop 样本

    # 停滞检测 — 基于时间（秒），运行时用 frame_interval/fps 自动换算样本数
    STAG_MIN_STRICT_SEC = 5.0         # 执行区严格：连续5s不推进=卡死
    STAG_MIN_RELAXED_SEC = 10.0       # 启动/收尾区宽松：连续10s（原20s，修正后收紧）
    STAG_START_ZONE = 0.05            # 启动区：进度 <5%
    STAG_END_ZONE = 0.95              # 收尾区：进度 95%-98%（原0.90，修正后收紧）
    STAG_COMPLETION_SHIELD = 0.98     # 完成区豁免：进度 ≥98% 直接忽略
    STAGNATION_AMPLITUDE_MAX = 0.05   # 漏斗二：停滞段内进度振幅上限（防缓慢爬行）
    STAG_ACTIVITY_AMPLITUDE = 0.02    # 收尾区活动判据：振幅 > 此值表明仍有操作活动，拒用放松阈值

    # 漏斗三：二维分级（绝对时长 OR 相对占比，取更高等级）
    STAG_SEVERE_SEC = 10.0
    STAG_MEDIUM_SEC = 8.0
    STAG_SEVERE_RATIO = 0.20
    STAG_MEDIUM_RATIO = 0.10


class SubtaskTrajectoryEvaluator:
    """子任务分段评估器 + 进度跌落筛选器"""

    def __init__(self, model_path: str, subtasks: List[Dict], episode_path: str,
                 output_root: str, frame_interval: int = 10, mode: str = "average",
                 task_name: str = "unknown", filter_config: FilterConfig = None,
                 keep_model: bool = False, gpu_memory_utilization: float = 0.8,
                 timeline=None, frame_source=None, export_video=False):
        """
        Args:
            model_path: 模型路径
            subtasks: 子任务列表，每个包含 {step, query, clip_start_sec, clip_end_sec}
            episode_path: 轨迹目录路径
            output_root: 输出目录
            frame_interval: 采样间隔（默认10帧）
            mode: 评估模式 "incremental", "forward", "backward", "average"
            task_name: 任务名称（用于可视化）
            filter_config: 筛选配置
            keep_model: 批量模式下不释放模型（由外部管理）
            timeline: 与视频帧逐帧对应的真实时间；手动子任务区间仍使用播放秒
        """
        self.model_path = model_path
        self.subtasks = subtasks
        self.episode_path = Path(episode_path)
        self.output_root = Path(output_root)
        self.frame_interval = frame_interval
        self.mode = mode
        self.model = None
        self.task_name = task_name
        self.filter_config = filter_config or FilterConfig()
        self.keep_model = keep_model
        self.gpu_memory_utilization = gpu_memory_utilization
        self.timeline = timeline
        self.frame_source = frame_source
        self.export_video = export_video

        # 确保输出目录存在
        self.output_root.mkdir(parents=True, exist_ok=True)

        # 持久帧缓存（跨子任务累积，用于生成总体视频）
        self.frame_cache = self.output_root / ".frame_cache"
        if frame_source is None:
            for cam in ["cam_high", "cam_left_wrist", "cam_right_wrist"]:
                (self.frame_cache / cam).mkdir(parents=True, exist_ok=True)

        # 跨子任务的合并样本（用于总体进度视频和图表）
        self.combined_samples: List[Dict] = []

        if frame_source is not None:
            self.total_frames = timeline.count
            self.fps = timeline.fps
            self.total_duration = timeline.duration('playback')
            self.source_duration = timeline.duration('source')
            if self.total_duration is None: self.total_duration = self.source_duration
            self.display_basis = ('playback' if timeline.playback_times is not None else
                                  'source' if timeline.times is not None else 'frame')
        else:
            video_path = self.episode_path / 'cam_high.mp4'
            cap = cv2.VideoCapture(str(video_path))
            self.fps = cap.get(cv2.CAP_PROP_FPS)
            self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.total_duration = self.total_frames / self.fps
            cap.release()
            if self.timeline is not None and self.timeline.count != self.total_frames:
                raise ValueError('Source timeline must contain one timestamp per video frame')
            self.source_duration = timeline.duration('source') if timeline is not None else None
            self.display_basis = 'playback'

        self.total_subtasks = len(subtasks)

        # 统计信息
        self.stats = {
            'total_subtasks': self.total_subtasks,
            'evaluated_subtasks': 0,
            'subtasks_with_regressions': 0,
            'subtasks_with_stagnations': 0,
            'subtasks_below_threshold': 0,
            'low_completion_subtasks': [],
            'pattern_distribution': {'v_shape': 0, 'valley': 0, 'no_regression': 0},
            'severity_distribution': {'严重': 0, '中等': 0, '轻微': 0},
            'stagnation_distribution': {'严重': 0, '中等': 0, '轻微': 0},
        }

        print(f"输入信息: {self.total_frames}帧, 源时长={self.source_duration}, 播放时长={self.total_duration}")
        print(f"子任务数量: {self.total_subtasks}")
        print(f"评估模式: {self.mode}")

    def load_model(self):
        """只加载一次模型，所有子任务共享"""
        if self.model is None:
            print(f"\n加载模型: {self.model_path}")
            self.model = GRMInference(self.model_path, gpu_memory_utilization=self.gpu_memory_utilization)
            print("✓ 模型加载完成")

    def time_to_frame(self, time_sec: float) -> int:
        """将时间转换为帧号"""
        if getattr(self,'frame_source',None) is not None:
            axis=self.timeline.axis(self.display_basis)
            if axis is None: raise ValueError('No seconds axis available')
            return min(int(np.searchsorted(axis,time_sec)),self.total_frames-1)
        return int(time_sec * self.fps)

    def frame_to_time(self, frame: int) -> float:
        """将帧号转换为播放时间；不能据此证明采集同步。"""
        if getattr(self,'frame_source',None) is not None:
            axis=self.timeline.axis(self.display_basis)
            return float(axis[frame]) if axis is not None else None
        return frame / self.fps

    def _frame_time_evidence(self, frame: int) -> Dict:
        timeline = getattr(self, 'timeline', None)
        return {
            'playback_time_sec': (float(timeline.playback_times[frame]) if timeline is not None and timeline.playback_times is not None else self.frame_to_time(frame) if timeline is None else None),
            'source_time_sec': timeline.time_at(frame) if timeline is not None else None,
        }

    def _hop_time_evidence(self, indices: List[int], sample: int) -> Dict:
        """保留冻结的输出帧绑定，同时明确一次模型观察覆盖的原始帧区间。"""
        start_frame, end_frame = indices[sample:sample + 2]
        timeline = getattr(self, 'timeline', None)
        return {
            'input_after_frame_index': end_frame,
            'hop_start_frame_index': start_frame,
            'hop_end_frame_index': end_frame,
            'playback_start_time_sec': self._frame_time_evidence(start_frame)['playback_time_sec'],
            'playback_end_time_sec': self._frame_time_evidence(end_frame)['playback_time_sec'],
            'source_start_time_sec': timeline.time_at(start_frame) if timeline is not None else None,
            'source_end_time_sec': timeline.time_at(end_frame) if timeline is not None else None,
        }

    def extract_frames(self, indices: List[int], cache_root: Path):
        """从视频中提取指定帧"""
        if self.frame_source is not None:
            # EDC owns the one cache, including non-identity multi-view pairing.
            for role in ("cam_high", "cam_left_wrist", "cam_right_wrist"):
                for index in indices:
                    self.frame_source.image_path(role, index)
            return
        cam_dirs = {cam: cache_root / cam for cam in
                    ("cam_high", "cam_left_wrist", "cam_right_wrist")}
        for folder in cam_dirs.values():
            folder.mkdir(parents=True, exist_ok=True)
        for cam_name, folder in cam_dirs.items():
            cap=cv2.VideoCapture(str(self.episode_path/f'{cam_name}.mp4'))
            try:
                for idx in indices:
                    if idx<0 or idx>=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)):
                        raise ValueError('Requested image outside video coverage')
                    cap.set(cv2.CAP_PROP_POS_FRAMES,idx)
                    ok,frame=cap.read()
                    if not ok: raise ValueError('Cannot decode requested original image')
                    cv2.imwrite(str(folder/f'frame_{idx:06d}.png'),frame)
            finally:
                cap.release()

    def image_path(self, role, index, cache_root=None):
        if getattr(self, 'frame_source', None) is not None:
            return self.frame_source.image_path(role, index)
        return str((cache_root or self.frame_cache) / role / f"frame_{index:06d}.png")

    def build_samples(self, indices: List[int], task_desc: str,
                      cache_root: Path, ref_start_path: str, ref_end_path: str,
                      mode: str) -> List[Dict]:
        """构建推理样本"""
        items = []

        if len(indices) < 2:
            return items

        for k in range(len(indices) - 1):
            af = indices[k + 1]

            # Define "Before" images based on mode
            if mode == "incremental":
                bf = indices[k]
                bf_images = [
                    self.image_path("cam_high", bf, cache_root),
                    self.image_path("cam_left_wrist", bf, cache_root),
                    self.image_path("cam_right_wrist", bf, cache_root),
                ]
            elif mode == "forward":
                bf = indices[0]
                bf_images = [
                    self.image_path("cam_high", bf, cache_root),
                    self.image_path("cam_left_wrist", bf, cache_root),
                    self.image_path("cam_right_wrist", bf, cache_root),
                ]
            elif mode == "backward":
                bf_images = [ref_end_path, ref_end_path, ref_end_path]
            else:
                raise ValueError(f"Unknown mode: {mode}")

            af_images = [
                self.image_path("cam_high", af, cache_root),
                self.image_path("cam_left_wrist", af, cache_root),
                self.image_path("cam_right_wrist", af, cache_root),
            ]

            items.append({
                "id": f"step-{k}",
                "task": task_desc,
                "image": [
                    ref_start_path,
                    ref_end_path,
                    bf_images[0],
                    bf_images[1],
                    bf_images[2],
                    af_images[0],
                    af_images[1],
                    af_images[2],
                ]
            })
        return items

    def _compute_progress(self, results: List[Dict], mode: str) -> tuple:
        """
        根据模式计算进度

        Returns:
            tuple: (final_progress, progress_details)
        """
        if not results:
            return 0.0, []

        prev_prog = 0.0
        progress_details = []

        for idx, item in enumerate(results):
            raw = item.get("pred", "")
            try:
                val_str = raw.split("<score>")[-1].split("</score>")[0].replace("%", "").strip()
                raw_score = max(-100.0, min(100.0, float(val_str))) / 100.0
            except Exception:
                raw_score = 0.0

            if mode == "forward":
                curr_progress = raw_score
            elif mode == "backward":
                curr_progress = 1.0 + raw_score
            else:  # incremental
                if idx == 0:
                    curr_progress = raw_score
                else:
                    if raw_score >= 0:
                        curr_progress = prev_prog + (1 - prev_prog) * raw_score
                    else:
                        curr_progress = prev_prog + prev_prog * raw_score

            curr_progress = max(0.0, min(1.0, curr_progress))
            prev_prog = curr_progress

            progress_details.append({
                'sample_idx': idx,
                'raw_score': raw_score,
                'progress': curr_progress
            })

        return prev_prog, progress_details

    def calculate_hops(self, progress_details: List[Dict]) -> List[Dict]:
        """从progress_details计算hop值"""
        hops = []
        for detail in progress_details:
            sample_idx = detail['sample_idx']
            progress = detail['progress']

            if sample_idx == 0:
                hop = progress
            else:
                hop = progress - progress_details[sample_idx - 1]['progress']

            hops.append({
                'sample': sample_idx,
                'hop': hop,
                'progress': progress
            })
        return hops

    def detect_regressions(self, hops: List[Dict]) -> List[Dict]:
        """找出 hop < threshold 的样本（瞬时回退）"""
        threshold = self.filter_config.REGRESSION_THRESHOLD
        return [h for h in hops if h['hop'] < threshold]

    def detect_cumulative_regressions(self, hops: List[Dict],
                                       frame_indices: List[int]) -> List[Dict]:
        """检测连续负 hop 累计回退。

        条件：连续负 hop（中间不能出现 hop >= 0），
        窗口内最多 MAX_SAMPLES 个样本，
        累计 drop < CUM_REGRESSION_THRESHOLD 则触发。
        """
        fc = self.filter_config
        threshold = fc.CUM_REGRESSION_THRESHOLD
        max_samples = fc.CUM_REGRESSION_MAX_SAMPLES
        n = len(hops)
        regs = []

        i = 1  # hop[0] 是起始 progress，跳过
        while i < n:
            if hops[i]['hop'] >= 0:
                i += 1
                continue

            # 收集连续负 hop
            start = i
            total_drop = 0.0
            best_drop = 0.0
            best_end = None

            j = i
            while j < n and (j - start) < max_samples:
                if hops[j]['hop'] >= 0:
                    break
                total_drop += hops[j]['hop']
                if total_drop < best_drop:
                    best_drop = total_drop
                    best_end = j
                j += 1

            if best_end is not None and best_drop < threshold:
                peak_idx = start - 1
                trough_idx = best_end
                fi_peak = frame_indices[peak_idx] if peak_idx < len(frame_indices) else peak_idx
                fi_trough = frame_indices[trough_idx] if trough_idx < len(frame_indices) else trough_idx

                regs.append({
                    'sample': trough_idx,
                    'start_sample': start,
                    'end_sample': trough_idx,
                    'frame_index': fi_trough,
                    'hop': best_drop,
                    'progress': hops[trough_idx]['progress'],
                    'progress_before': hops[peak_idx]['progress'],
                    'regression_type': 'cumulative',
                    'peak_frame': fi_peak,
                    'trough_frame': fi_trough,
                })
                i = best_end + 1
            else:
                i = start + 1

        return regs

    def merge_regressions(self, instant_regs: List[Dict],
                          cumulative_regs: List[Dict]) -> List[Dict]:
        """合并瞬时回退和累计回退，基于包含关系去重。

        只有当 instant regression 的 sample 落在 cumulative regression 的
        [start_sample, end_sample] 区间内时，才合并为 instant+cumulative。
        """
        merged = []
        used_inst = set()

        for cum in cumulative_regs:
            cum_start = cum.get('start_sample', cum['sample'])
            cum_end = cum.get('end_sample', cum['sample'])
            matched_inst = []

            for idx, inst in enumerate(instant_regs):
                inst_sample = inst['sample']
                if cum_start <= inst_sample <= cum_end:
                    matched_inst.append((idx, inst))

            if matched_inst:
                worst_inst = min((x[1] for x in matched_inst),
                                 key=lambda r: r['hop'])
                merged.append({
                    **cum,
                    'hop': min(cum['hop'], worst_inst['hop']),
                    'regression_type': 'instant+cumulative',
                })
                used_inst.update(x[0] for x in matched_inst)
            else:
                merged.append(cum)

        for idx, inst in enumerate(instant_regs):
            if idx not in used_inst:
                merged.append({**inst, 'regression_type': 'instant'})

        return sorted(merged, key=lambda r: r['sample'])

    def classify_pattern(self, hops: List[Dict], regressions: List[Dict]) -> str:
        """
        分类回退模式：v_shape 或 valley
        """
        if not regressions:
            return 'no_regression'

        first_regression_idx = regressions[0]['sample']

        post_regression_hops = []
        for h in hops[first_regression_idx+1:]:
            post_regression_hops.append(h['hop'])
            if len(post_regression_hops) >= self.filter_config.POST_WINDOW_SIZE:
                break

        if not post_regression_hops:
            return 'v_shape'

        has_near_zero = any(abs(h) < self.filter_config.NEAR_ZERO_THRESHOLD
                           for h in post_regression_hops)
        has_positive = any(h > 0 for h in post_regression_hops)

        if has_near_zero and has_positive:
            return 'valley'
        elif has_positive:
            return 'v_shape'
        else:
            return 'v_shape'

    def count_consecutive_regressions(self, regressions: List[Dict]) -> int:
        """计算连续回退的最大数量"""
        if not regressions:
            return 0

        max_consecutive = 1
        current_consecutive = 1

        for i in range(1, len(regressions)):
            if regressions[i]['sample'] == regressions[i-1]['sample'] + 1:
                current_consecutive += 1
                max_consecutive = max(max_consecutive, current_consecutive)
            else:
                current_consecutive = 1

        return max_consecutive

    def assess_regression_risk(self, regressions: List[Dict]) -> Tuple[int, List[str]]:
        """评估回退风险"""
        if not regressions:
            return 0, []

        risk_score = 0
        reasons = []

        first_regression = regressions[0]
        regression_progress = first_regression['progress']

        # 检查进度水平
        if regression_progress > self.filter_config.HIGH_PROGRESS_THRESHOLD:
            risk_score += 1
            reasons.append(f"回退发生在高进度时 ({regression_progress:.2f})")

        # 检查持续性（cumulative类型天生连续，只对纯instant检查）
        has_cumulative = any('cumulative' in r.get('regression_type', '') for r in regressions)
        if not has_cumulative:
            consecutive = self.count_consecutive_regressions(regressions)
            if consecutive < self.filter_config.MIN_CONSECUTIVE_REGRESSIONS:
                total = len(regressions)
                risk_score += 1
                reasons.append(f"{total}次回退中最大连续仅{consecutive}（非连续）")

        return risk_score, reasons

    def get_severity(self, regressions: List[Dict]) -> str:
        """按最大回退分类严重程度"""
        if not regressions:
            return 'none'

        max_regression = min(r['hop'] for r in regressions)

        if max_regression < -0.30:
            return '严重'
        elif max_regression < -0.20:
            return '中等'
        else:
            return '轻微'

    # ===================== 停滞检测 =====================

    def _min_stagnation_seconds(self, avg_progress: float) -> Optional[float]:
        """各进度区域使用相同秒数阈值；完成区保持豁免。"""
        fc = self.filter_config
        if avg_progress >= fc.STAG_COMPLETION_SHIELD:
            return None
        elif avg_progress < fc.STAG_START_ZONE:
            return fc.STAG_MIN_RELAXED_SEC
        elif avg_progress >= fc.STAG_END_ZONE:
            return fc.STAG_MIN_RELAXED_SEC
        return fc.STAG_MIN_STRICT_SEC

    def _min_stagnation_samples(self, avg_progress: float) -> Optional[int]:
        """均匀播放时间下保留冻结基线的样本数舍入规则。"""
        target_sec = self._min_stagnation_seconds(avg_progress)
        if target_sec is None:
            return None
        sps = self.frame_interval / (self.timeline.rule_fps if getattr(self,'frame_source',None) is not None else self.fps)
        return max(1, round(target_sec / sps))

    def _try_append_stagnation_phase(self, phases: List[Dict],
                                      hops: List[Dict],
                                      start: int, duration: int,
                                      total_samples: int,
                                      frame_indices: Optional[List[int]] = None):
        """漏斗二+漏斗三：校验并追加一个停滞阶段"""
        fc = self.filter_config
        if duration < 1:
            return

        window = hops[start:start + duration]
        window_prog = [h['progress'] for h in window]
        amplitude = max(window_prog) - min(window_prog)
        avg_prog = sum(window_prog) / len(window_prog)

        min_seconds = self._min_stagnation_seconds(avg_prog)
        if min_seconds is None:
            return  # 完成区豁免

        timeline = getattr(self, 'timeline', None)
        use_source_time = timeline is not None and timeline.rule_fps is None
        min_samples = self._min_stagnation_samples(avg_prog) if not use_source_time else None

        # 修正：启动区/收尾区内有操作活动（振幅 > 0.02）→ 用执行区严格门槛
        in_start_zone = avg_prog < fc.STAG_START_ZONE
        in_end_zone = fc.STAG_END_ZONE <= avg_prog < fc.STAG_COMPLETION_SHIELD
        if (in_start_zone or in_end_zone) and amplitude >= fc.STAG_ACTIVITY_AMPLITUDE:
            if not use_source_time:
                sps = self.frame_interval / (self.timeline.rule_fps if getattr(self,'frame_source',None) is not None else self.fps)
                strict_min = max(1, round(fc.STAG_MIN_STRICT_SEC / sps))
                min_samples = min(min_samples, strict_min)
            min_seconds = min(min_seconds, fc.STAG_MIN_STRICT_SEC)

        if use_source_time:
            # hop k 属于原采样区间 [indices[k], indices[k+1]]，尾部只计实际跨度。
            start_frame = frame_indices[start]
            end_frame = frame_indices[start + duration]
            duration_sec = timeline.elapsed(start_frame, end_frame)
            total_sec = timeline.elapsed(frame_indices[0], frame_indices[total_samples])
            if duration_sec < min_seconds:
                return
            proportion = duration_sec / total_sec if total_sec > 0 else 0
        else:
            if duration < min_samples:
                return  # 漏斗二：均匀采样保持原样本数判定
            duration_sec = duration * (self.frame_interval / (timeline.rule_fps if timeline is not None else self.fps))
            proportion = duration / total_samples if total_samples > 0 else 0

        if amplitude >= fc.STAGNATION_AMPLITUDE_MAX:
            return  # 漏斗二：缓慢爬行，非真停滞

        # 漏斗三：二维分级
        if duration_sec > fc.STAG_SEVERE_SEC or proportion > fc.STAG_SEVERE_RATIO:
            sev = '严重'
        elif duration_sec > fc.STAG_MEDIUM_SEC or proportion > fc.STAG_MEDIUM_RATIO:
            sev = '中等'
        else:
            sev = '轻微'

        phase = {
            'start_sample': start,
            'end_sample': start + duration - 1,
            'duration_samples': duration,
            'duration_sec': round(duration_sec, 2),
            'proportion': round(proportion, 3),
            'avg_progress': round(avg_prog, 4),
            'amplitude': round(amplitude, 4),
            'severity': sev,
        }
        if use_source_time:
            phase.update({
                'duration_time_basis': 'source',
                'duration_start_frame': start_frame,
                'duration_end_frame': end_frame,
                'source_start_time_sec': timeline.time_at(start_frame),
                'source_end_time_sec': timeline.time_at(end_frame),
            })
        phases.append(phase)

    def detect_stagnations(self, hops: List[Dict],
                          frame_indices: Optional[List[int]] = None) -> List[Dict]:
        """三层漏斗停滞检测：触发 → 校验 → 分级"""
        phases = []
        if getattr(self,'timeline',None) is not None and self.timeline.times is None:
            return phases  # Unknown recorded duration is not zero stagnation evidence.
        in_stagnation = False
        start_idx = 0
        n = len(hops)
        total_samples = n
        timeline = getattr(self, 'timeline', None)
        if timeline is not None and timeline.rule_fps is None and n:
            if frame_indices is None or len(frame_indices) != n + 1:
                raise ValueError("Nonuniform stagnation timing requires all sampled interval boundaries")
            if any(b <= a for a, b in zip(frame_indices, frame_indices[1:])):
                raise ValueError("Sampled interval boundaries must be strictly increasing")
            if frame_indices[0] < 0 or frame_indices[-1] >= len(timeline.times):
                raise ValueError("Sampled interval boundaries exceed the source timeline")

        for i in range(n):
            hop_val = hops[i]['hop']
            if abs(hop_val) < self.filter_config.NEAR_ZERO_THRESHOLD:
                if not in_stagnation:
                    start_idx = i
                    in_stagnation = True
            else:
                if in_stagnation:
                    in_stagnation = False
                    self._try_append_stagnation_phase(
                        phases, hops, start_idx, i - start_idx, total_samples, frame_indices)

        # 收尾结算：停滞持续到最后一帧
        if in_stagnation:
            self._try_append_stagnation_phase(
                phases, hops, start_idx, n - start_idx, total_samples, frame_indices)

        return phases

    def get_stagnation_severity(self, stagnations: List[Dict]) -> str:
        """返回停滞的最高严重等级"""
        if not stagnations:
            return 'none'
        priority = {'严重': 3, '中等': 2, '轻微': 1, 'none': 0}
        return max(stagnations, key=lambda s: priority.get(s['severity'], 0))['severity']

    def evaluate_subtask_with_regressions(self, subtask_idx: int, run_root: Path) -> Dict:
        """评估单个子任务并检测回退"""
        subtask = self.subtasks[subtask_idx]
        start_frame = subtask['_start_frame'] if '_start_frame' in subtask else self.time_to_frame(subtask['clip_start_sec'])
        end_frame = subtask['_end_frame'] if '_end_frame' in subtask else min(self.time_to_frame(subtask['clip_end_sec']), self.total_frames - 1)
        task_desc = subtask['query']

        print(f"\n{'='*70}")
        print(f"评估子任务 {subtask_idx}: {task_desc}")
        print(f"{'='*70}")
        print(f"区间: 原始参考帧 {start_frame} - {end_frame}")
        print(f"帧范围: {start_frame} - {end_frame}")

        cache_root = self.frame_cache

        # 在子任务范围内采样帧
        subtask_frames = end_frame - start_frame
        if subtask_frames <= self.frame_interval:
            indices = [start_frame, end_frame]
        else:
            full_indices = list(range(start_frame, end_frame + 1, self.frame_interval))
            if full_indices[-1] != end_frame:
                full_indices.append(end_frame)
            indices = full_indices

        print(f"采样 {len(indices)} 帧 (间隔={self.frame_interval})")

        # 提取帧
        self.extract_frames(indices, cache_root)

        # 设置ref_start和ref_end
        ref_start_path = self.image_path("cam_high", start_frame, cache_root)
        ref_end_path = self.image_path("cam_high", end_frame, cache_root)

        # 确定使用的模式
        if self.mode == "average":
            modes = ["forward", "incremental", "backward"]
        else:
            modes = [self.mode]

        mode_results = {}

        for mode in modes:
            # 构建样本
            samples = self.build_samples(indices, task_desc, cache_root, ref_start_path, ref_end_path, mode)
            print(f"  [{mode.upper()}] 构建 {len(samples)} 个样本")

            # 批量推理 (整批提交, 由 vLLM continuous batching 调度)
            results = self.model.inference_batch(samples)

            # Preserve malformed/incomplete responses as execution evidence;
            # never turn an unparsed model answer into zero progress.
            (run_root / f"raw_{mode}.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
            if len(results) != len(samples):
                raise ValueError("GRM response count differs from the requested sample count")
            for response in results:
                raw = response.get("pred", "")
                if "<score>" not in raw or "</score>" not in raw:
                    raise ValueError("GRM response lacks score tags; raw responses retained")
                score = float(raw.split("<score>")[-1].split("</score>")[0].replace("%", "").strip())
                if not np.isfinite(score):
                    raise ValueError("GRM returned a nonfinite score")
            # 计算进度
            final_progress, progress_details = self._compute_progress(results, mode)

            # 计算hop
            hops = self.calculate_hops(progress_details)

            mode_results[mode] = {
                'final_progress': final_progress,
                'progress_details': progress_details,
                'hops': hops
            }
            print(f"  [{mode.upper()}] 进度: {final_progress*100:.1f}%")

        # 如果是average模式，计算平均结果
        if self.mode == "average":
            combined = self.combine_mode_results(mode_results, indices)
            mode_results["average"] = combined
            final_progress = combined['final_progress']
            progress_details = combined['progress_details']
            hops = combined['hops']
            print(f"  [AVERAGE] 进度: {final_progress*100:.1f}%")
        else:
            final_progress = mode_results[self.mode]['final_progress']
            progress_details = mode_results[self.mode]['progress_details']
            hops = mode_results[self.mode]['hops']

        # 保存pred_vllm.json
        pred_data = []
        for idx, detail in enumerate(progress_details):
            frame = indices[idx]
            time_evidence = {**self._frame_time_evidence(frame),
                             **self._hop_time_evidence(indices, idx)}
            detail.update(time_evidence)
            hops[idx].update(time_evidence)
            pred_data.append({
                'sample': idx,
                'subtask_idx': subtask_idx,
                'frame_index': frame,
                'progress': detail['progress'],
                'hop': hops[idx]['hop'] if idx < len(hops) else 0.0,
                **time_evidence,
                'image': [ref_start_path, ref_end_path,
                          *[self.image_path(cam, indices[idx], cache_root)
                            for cam in ('cam_high', 'cam_left_wrist', 'cam_right_wrist')],
                          *[self.image_path(cam, indices[idx + 1], cache_root)
                            for cam in ('cam_high', 'cam_left_wrist', 'cam_right_wrist')]],
            })

        pred_path = run_root / "pred_vllm.json"
        with open(pred_path, 'w', encoding='utf-8') as f:
            json.dump(pred_data, f, indent=2, ensure_ascii=False)

        # 检测回退（双通道：瞬时 + 累计）
        instant_regs = self.detect_regressions(hops)
        cumulative_regs = self.detect_cumulative_regressions(hops, indices)
        regressions = self.merge_regressions(instant_regs, cumulative_regs)
        pattern = self.classify_pattern(hops, regressions)
        severity = self.get_severity(regressions)
        risk_score, risk_reasons = self.assess_regression_risk(regressions)

        # 检测停滞
        stagnations = self.detect_stagnations(hops, indices)
        stagnation_severity = self.get_stagnation_severity(stagnations)

        # 更新统计
        self.stats['evaluated_subtasks'] += 1
        if regressions:
            self.stats['subtasks_with_regressions'] += 1
        if stagnations:
            self.stats['subtasks_with_stagnations'] += 1
        if pattern in self.stats['pattern_distribution']:
            self.stats['pattern_distribution'][pattern] += 1
        if severity in self.stats['severity_distribution']:
            self.stats['severity_distribution'][severity] += 1
        if stagnation_severity in self.stats['stagnation_distribution']:
            self.stats['stagnation_distribution'][stagnation_severity] += 1
        if final_progress < self.filter_config.SUBTASK_COMPLETION_THRESHOLD:
            self.stats['subtasks_below_threshold'] += 1

        if self.export_video:
            self._render_video(run_root)

        return {
            'subtask_idx': subtask_idx,
            'description': task_desc,
            'start_frame': start_frame,
            'end_frame': end_frame,
            'start_time': self.frame_to_time(start_frame),
            'end_time': self.frame_to_time(end_frame),
            'source_start_time_sec': self._frame_time_evidence(start_frame)['source_time_sec'],
            'source_end_time_sec': self._frame_time_evidence(end_frame)['source_time_sec'],
            'sample_frame_indices': indices,
            'sample_source_times_sec': [self._frame_time_evidence(f)['source_time_sec'] for f in indices],
            'progress': final_progress,
            'num_samples': len(progress_details),
            'progress_details': progress_details,
            'hops': hops,
            'regressions': regressions,
            'pattern': pattern,
            'severity': severity,
            'risk_score': risk_score,
            'risk_reasons': risk_reasons,
            'stagnations': stagnations,
            'stagnation_severity': stagnation_severity,
            'is_below_threshold': final_progress < self.filter_config.SUBTASK_COMPLETION_THRESHOLD
        }

    def combine_mode_results(self, mode_results: Dict, indices: List[int]) -> Dict:
        """组合三种模式的结果为Average模式"""
        forward = mode_results["forward"]
        incremental = mode_results["incremental"]
        backward = mode_results["backward"]

        num_samples = len(forward['progress_details'])
        combined_progress_details = []
        combined_hops = []

        for i in range(num_samples):
            # 计算平均进度
            forward_prog = forward['progress_details'][i]['progress']
            incremental_prog = incremental['progress_details'][i]['progress']
            backward_prog = backward['progress_details'][i]['progress']
            avg_progress = (forward_prog + incremental_prog + backward_prog) / 3

            # 计算平均hop
            forward_hop = forward['hops'][i]['hop']
            incremental_hop = incremental['hops'][i]['hop']
            backward_hop = backward['hops'][i]['hop']
            avg_hop = (forward_hop + incremental_hop + backward_hop) / 3

            combined_progress_details.append({
                'sample_idx': i,
                'raw_score': 0,  # average模式不保留原始score
                'progress': avg_progress
            })

            combined_hops.append({
                'sample': i,
                'hop': avg_hop,
                'progress': avg_progress
            })

        final_progress = combined_progress_details[-1]['progress'] if combined_progress_details else 0.0

        return {
            'final_progress': final_progress,
            'progress_details': combined_progress_details,
            'hops': combined_hops
        }

    def evaluate_all_subtasks_with_filtering(self) -> Tuple[List[Dict], Dict]:
        """评估所有子任务并进行跌落筛选"""
        total_start = time.time()

        print(f"\n{'='*70}")
        print(f"开始子任务分段评估与跌落筛选")
        print(f"{'='*70}")

        # 只加载一次模型
        self.load_model()

        results = []
        cumulative_progress = []
        self.combined_samples = []

        for i in range(self.total_subtasks):
            # 低完成度仅记录：GRM 绝对进度分只反映视觉进度观察，校准不确定性高，
            # 不参与子任务成败裁决，后续子任务照常评估并输出。
            if i > 0 and results[-1]['progress'] < self.filter_config.SUBTASK_COMPLETION_THRESHOLD:
                self.stats['low_completion_subtasks'].append(i - 1)
                print(f"\n注意：子任务 {i-1} 完成度 {results[-1]['progress']*100:.1f}% "
                      f"< {self.filter_config.SUBTASK_COMPLETION_THRESHOLD*100:.0f}%（仅记录，评估继续）")

            # 创建子任务输出目录
            ts = datetime.now().strftime("%y-%m-%d-%H-%M-%S")
            run_root = self.output_root / f"subtask_{i}_{ts}"
            run_root.mkdir(parents=True, exist_ok=True)

            # 评估子任务
            result = self.evaluate_subtask_with_regressions(i, run_root)
            results.append(result)

        total_time = time.time() - total_start
        print(f"\n{'='*70}")
        print(f"评估完成")
        print(f"总推理时间: {total_time:.2f}秒")
        print(f"{'='*70}")

        # 释放模型（批量模式下由外部管理）
        if self.model is not None and not self.keep_model:
            self.model.shutdown()

        cumulative_progress = self._build_cumulative_progress(results)
        filter_conclusion = self._finalize_and_save(results, cumulative_progress, total_time)

        return results, filter_conclusion

    def _build_cumulative_progress(self, results: List[Dict]) -> List[Dict]:
        """从 results 列表构建 cumulative_progress（含 enrichment），同时填充 combined_samples"""
        cumulative_progress = []
        self.combined_samples = []
        scale = 1.0 / self.total_subtasks
        fi = self.frame_interval

        for i, result in enumerate(results):
            # 累积合并样本（总体进度，用于视频和图表）
            subtask_base = i * scale
            frame_indices = result.get('sample_frame_indices', [])
            pdetails = result.get('progress_details', [])
            for j, detail in enumerate(pdetails):
                frame_idx = frame_indices[j] if j < len(frame_indices) else (frame_indices[-1] if frame_indices else 0)
                overall_progress = subtask_base + detail['progress'] * scale
                self.combined_samples.append({
                    'subtask_idx': i,
                    'sample_idx': j,
                    'frame_index': frame_idx,
                    'progress': overall_progress,
                    **self._frame_time_evidence(frame_idx),
                    **({key: detail[key] for key in (
                        'input_after_frame_index', 'hop_start_frame_index', 'hop_end_frame_index',
                        'playback_start_time_sec', 'playback_end_time_sec',
                        'source_start_time_sec', 'source_end_time_sec') if key in detail}),
                })

            scaled_progress = i * scale + (result['progress'] * scale)

            # regressions enrichment
            enriched_regressions = []
            for reg in result.get('regressions', []):
                sidx = reg.get('sample', 0)
                reg_frame = reg.get('frame_index',
                                    frame_indices[sidx] if sidx < len(frame_indices) else sidx * fi)
                progress_before = reg.get('progress_before',
                                          pdetails[sidx - 1]['progress'] if sidx > 0 else reg['progress'])
                after = pdetails[sidx + 1: sidx + 1 + 7]
                out_reg = {
                    'frame_index': reg_frame,
                    **self._frame_time_evidence(reg_frame),
                    'hop': reg['hop'],
                    'progress_at_regression': reg['progress'],
                    'progress_before': progress_before,
                    'progress_after_7': [d['progress'] for d in after],
                    'samples_after_available': len(after),
                    'regression_type': reg.get('regression_type', 'instant'),
                }
                if 'peak_frame' in reg:
                    out_reg['peak_frame'] = reg['peak_frame']
                    out_reg['trough_frame'] = reg['trough_frame']
                enriched_regressions.append(out_reg)

            # stagnations enrichment
            enriched_stagnations = []
            for stag in result.get('stagnations', []):
                si, ei = stag['start_sample'], stag['end_sample']
                enriched_stag = {
                    'start_frame':   frame_indices[si] if si < len(frame_indices) else si * fi,
                    'end_frame':     frame_indices[ei] if ei < len(frame_indices) else ei * fi,
                    'avg_progress':  stag['avg_progress'],
                    'amplitude':     stag['amplitude'],
                    'severity':      stag['severity'],
                    **({key: stag[key] for key in (
                        'duration_sec', 'duration_time_basis', 'duration_start_frame',
                        'duration_end_frame', 'source_start_time_sec', 'source_end_time_sec',
                        'proportion') if key in stag}),
                }
                if stag.get('duration_time_basis') == 'source':
                    # The final hop ends at the next sampled boundary, including
                    # a short tail. Attribution must use the same full interval
                    # used to calculate source duration, rather than end_sample.
                    enriched_stag['start_frame'] = stag['duration_start_frame']
                    enriched_stag['end_frame'] = stag['duration_end_frame']
                    enriched_stag['start_time'] = self.frame_to_time(enriched_stag['start_frame'])
                    enriched_stag['end_time'] = self.frame_to_time(enriched_stag['end_frame'])
                enriched_stagnations.append(enriched_stag)

            cumulative_progress.append({
                'step': i,
                'start_frame': result['start_frame'],
                'end_frame': result['end_frame'],
                'start_time': result['start_time'],
                'end_time': result['end_time'],
                'source_start_time_sec': self._frame_time_evidence(result['start_frame'])['source_time_sec'],
                'source_end_time_sec': self._frame_time_evidence(result['end_frame'])['source_time_sec'],
                'subtask_progress': result['progress'],
                'total_progress': scaled_progress,
                'description': result['description'],
                'has_regressions': len(result['regressions']) > 0,
                'regression_count': len(result['regressions']),
                'regressions': enriched_regressions,
                'pattern': result['pattern'],
                'severity': result['severity'],
                'has_stagnations': len(result.get('stagnations', [])) > 0,
                'stagnation_count': len(result.get('stagnations', [])),
                'stagnation_severity': result.get('stagnation_severity', 'none'),
                'stagnations': enriched_stagnations,
                'is_below_threshold': result['is_below_threshold']
            })

            print(f"✓ 子任务{i}进度: {result['progress']*100:.1f}% → 总进度: {scaled_progress*100:.1f}%")
            if result['regressions']:
                print(f"  检测到 {len(result['regressions'])} 次回退，模式: {result['pattern']}, 严重程度: {result['severity']}")
            if result.get('stagnations'):
                print(f"  检测到 {len(result['stagnations'])} 段停滞，最高严重程度: {result['stagnation_severity']}")

        return cumulative_progress

    def _finalize_and_save(self, results: List[Dict], cumulative_progress: List[Dict],
                            total_time: float, generate_video: bool = True) -> Dict:
        """生成 filter_conclusion、保存 JSON/报告/图表/视频"""
        filter_conclusion = self.generate_filter_conclusion(results, cumulative_progress)
        self.save_results(results, cumulative_progress, total_time, filter_conclusion)
        if self.combined_samples:
            self.save_overall_predictions()
        if generate_video and self.export_video:
            self._render_video(self.output_root)
        return filter_conclusion

    def recompute_from_existing(self) -> Tuple[List[Dict], Dict]:
        """从已有的 subtask_*/pred_vllm.json 重算所有检测结果，不需要模型。

        当检测逻辑变更后，用此方法更新 evaluation_summary.json 和 assessment_report.txt。
        不会重新生成视频（缺少帧缓存）。
        """
        import glob as _glob

        print(f"\n{'='*70}")
        print(f"重算模式：从已有数据重新检测（不加载模型）")
        print(f"{'='*70}")

        results = []

        for i in range(self.total_subtasks):
            # 读取 pred_vllm.json
            pred_paths = sorted(_glob.glob(str(self.output_root / f"subtask_{i}_*" / "pred_vllm.json")))
            if not pred_paths:
                print(f"⚠ 子任务{i} 无 pred_vllm.json，跳过")
                continue

            with open(pred_paths[0], 'r', encoding='utf-8') as f:
                preds = json.load(f)

            # 读取已有 summary 中的子任务元信息
            summary_path = self.output_root / "evaluation_summary.json"
            sr_meta = None
            if summary_path.exists():
                with open(summary_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                    for sr in existing.get('subtask_results', []):
                        if sr.get('step') == i:
                            sr_meta = sr
                            break

            indices = [p['frame_index'] for p in preds]
            if preds and all('input_after_frame_index' in p for p in preds):
                after_indices = [p['input_after_frame_index'] for p in preds]
                if indices[1:] != after_indices[:-1]:
                    raise ValueError("Stored model sample intervals are not contiguous")
                indices.append(after_indices[-1])
            progress_details = [{'sample_idx': p['sample'], 'progress': p['progress'],
                                  'raw_score': 0} for p in preds]
            hops = self.calculate_hops(progress_details)
            for idx, detail in enumerate(progress_details):
                time_evidence = self._frame_time_evidence(indices[idx])
                if len(indices) == len(preds) + 1:
                    time_evidence.update(self._hop_time_evidence(indices, idx))
                detail.update(time_evidence)
                hops[idx].update(time_evidence)

            # 双通道回退检测
            instant_regs = self.detect_regressions(hops)
            cumulative_regs = self.detect_cumulative_regressions(hops, indices)
            regressions = self.merge_regressions(instant_regs, cumulative_regs)

            pattern = self.classify_pattern(hops, regressions)
            severity = self.get_severity(regressions)
            risk_score, risk_reasons = self.assess_regression_risk(regressions)

            # 停滞检测
            stagnations = self.detect_stagnations(hops, indices)
            stagnation_severity = self.get_stagnation_severity(stagnations)

            final_progress = preds[-1]['progress'] if preds else 0.0
            start_frame = sr_meta['start_frame'] if sr_meta else (indices[0] if indices else 0)
            end_frame = sr_meta['end_frame'] if sr_meta else (indices[-1] if indices else 0)
            task_desc = sr_meta['description'] if sr_meta else self.subtasks[i]['query']

            result = {
                'subtask_idx': i,
                'description': task_desc,
                'start_frame': start_frame,
                'end_frame': end_frame,
                'start_time': self.frame_to_time(start_frame),
                'end_time': self.frame_to_time(end_frame),
                'source_start_time_sec': self._frame_time_evidence(start_frame)['source_time_sec'],
                'source_end_time_sec': self._frame_time_evidence(end_frame)['source_time_sec'],
                'sample_frame_indices': indices,
                'sample_source_times_sec': [self._frame_time_evidence(f)['source_time_sec'] for f in indices],
                'progress': final_progress,
                'num_samples': len(progress_details),
                'progress_details': progress_details,
                'hops': hops,
                'regressions': regressions,
                'pattern': pattern,
                'severity': severity,
                'risk_score': risk_score,
                'risk_reasons': risk_reasons,
                'stagnations': stagnations,
                'stagnation_severity': stagnation_severity,
                'is_below_threshold': final_progress < self.filter_config.SUBTASK_COMPLETION_THRESHOLD,
            }
            results.append(result)
            print(f"✓ 子任务{i}: regressions={len(regressions)}, stagnations={len(stagnations)}")

        cumulative_progress = self._build_cumulative_progress(results)
        filter_conclusion = self._finalize_and_save(results, cumulative_progress, 0, generate_video=False)

        print(f"\n{'='*70}")
        print(f"重算完成")
        print(f"{'='*70}")

        return results, filter_conclusion

    def generate_filter_conclusion(self, results: List[Dict], cumulative_progress: List[Dict]) -> Dict:
        """生成筛选结论"""
        conclusion = {
            'episode': self.episode_path.name,
            'overall_status': 'pass',
            'problematic_subtasks': [],
            'summary': {
                'total_subtasks': self.total_subtasks,
                'evaluated_subtasks': len(results),
                'subtasks_with_regressions': 0,
                'subtasks_with_stagnations': 0,
                'subtasks_below_threshold': 0,
                'total_regression_count': 0,
                'total_stagnation_count': 0
            },
            'statistics': self.stats.copy(),
            'classification_details': []
        }

        for idx, result in enumerate(results):
            cp = cumulative_progress[idx] if idx < len(cumulative_progress) else {}
            enriched_regs = cp.get('regressions', [])
            enriched_stags = cp.get('stagnations', [])
            stag_sev = result.get('stagnation_severity', 'none')

            detail = {
                'subtask_idx': result['subtask_idx'],
                'description': result['description'],
                'progress': result['progress'],
                'has_regressions': len(result['regressions']) > 0,
                'regression_count': len(result['regressions']),
                'regressions': enriched_regs,
                'pattern': result['pattern'],
                'severity': result['severity'],
                'has_stagnations': len(result.get('stagnations', [])) > 0,
                'stagnation_count': len(result.get('stagnations', [])),
                'stagnation_severity': stag_sev,
                'stagnations': enriched_stags,
                'is_below_threshold': result['is_below_threshold'],
                'risk_score': result['risk_score'],
                'risk_reasons': result['risk_reasons']
            }

            # 判断该子任务是否有问题（回退/停滞模式；is_below_threshold 仅作
            # 信息性标记随 detail 与 summary 输出，不改变 status 与 overall_status）
            has_regressions = len(result['regressions']) > 0
            has_stagnations = len(result.get('stagnations', [])) > 0
            if result['pattern'] == 'valley':
                detail['status'] = 'problematic'
                detail['reason'] = f'凹槽模式，{result["severity"]}回退'
                conclusion['problematic_subtasks'].append(result['subtask_idx'])
                if conclusion['overall_status'] == 'pass':
                    conclusion['overall_status'] = 'warning'
            elif result['pattern'] == 'v_shape' and result['risk_score'] < 2:
                detail['status'] = 'potential_problem'
                detail['reason'] = f'V形模式，{result["severity"]}回退，低误判风险'
                if conclusion['overall_status'] == 'pass':
                    conclusion['overall_status'] = 'warning'
            elif has_stagnations and stag_sev in ('严重', '中等'):
                detail['status'] = 'problematic'
                detail['reason'] = f'{stag_sev}停滞（{detail["stagnation_count"]}段）'
                conclusion['problematic_subtasks'].append(result['subtask_idx'])
                if conclusion['overall_status'] == 'pass':
                    conclusion['overall_status'] = 'warning'
            elif has_stagnations:
                detail['status'] = 'potential_problem'
                detail['reason'] = f'轻微停滞（{detail["stagnation_count"]}段）'
                if conclusion['overall_status'] == 'pass':
                    conclusion['overall_status'] = 'warning'
            else:
                detail['status'] = 'ok'
                detail['reason'] = '通过'

            conclusion['classification_details'].append(detail)

            # 更新汇总统计
            if has_regressions:
                conclusion['summary']['subtasks_with_regressions'] += 1
            if has_stagnations:
                conclusion['summary']['subtasks_with_stagnations'] += 1
            if result['is_below_threshold']:
                conclusion['summary']['subtasks_below_threshold'] += 1
            conclusion['summary']['total_regression_count'] += len(result['regressions'])
            conclusion['summary']['total_stagnation_count'] += len(result.get('stagnations', []))

        return conclusion

    def save_results(self, results: List[Dict], cumulative_progress: List[Dict],
                     total_time: float, filter_conclusion: Dict):
        """保存结果JSON和生成进度图"""
        # 保存JSON
        summary = {
            'episode': self.episode_path.name,
            'mode': self.mode,
            'frame_interval': self.frame_interval,
            'total_subtasks': self.total_subtasks,
            'evaluated_subtasks': len(results),
            'total_time': total_time,
            'video_fps': self.fps,
            'video_duration_sec': round(self.total_duration, 2) if self.total_duration is not None else None,
            'frame_count': self.total_frames,
            'frame_timeline': self.timeline.describe() if self.timeline is not None else None,
            'stagnation_evaluated': self.timeline is None or self.timeline.times is not None,
            'source_duration_sec': getattr(self, 'source_duration', None),
            'duration_time_basis': ('source' if self.timeline is not None and self.timeline.times is not None else 'unavailable' if self.timeline is not None else 'legacy_playback'),
            'subtask_results': cumulative_progress,
            'filter_conclusion': filter_conclusion
        }

        summary_path = self.output_root / "evaluation_summary.json"
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"✓ 结果已保存: {summary_path}")

        # 生成文本报告
        self.save_text_report(summary, self.output_root / "assessment_report.txt")

        # 生成进度图
        self.plot_progress_chart(cumulative_progress, filter_conclusion)

    def save_text_report(self, summary: Dict, report_path: Path):
        """保存人类可读的文本报告"""
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write("子任务分段评估与跌落筛选报告\n")
            f.write("="*80 + "\n\n")

            f.write(f"轨迹: {summary['episode']}\n")
            f.write(f"评估模式: {summary['mode']}\n")
            f.write(f"采样间隔: {summary['frame_interval']} 帧\n")
            duration, fps = summary.get('video_duration_sec'), summary.get('video_fps')
            f.write(f"展示时长: {duration if duration is not None else '未知'} 秒; 播放帧率: {fps if fps is not None else '不适用'}\n")
            f.write(f"源时长: {summary.get('source_duration_sec')} 秒\n")
            f.write("本报告为视觉进度与回退候选；完整物理归因状态见 native-result.json。\n")
            f.write("开启视频导出时，reward_vis.mp4 为 4 FPS 采样回放，不代表原始动作时长。\n")
            if not summary.get('stagnation_evaluated', True):
                f.write("停滞未评估：缺少可靠源时间；零条记录不表示已确认无停滞。\n")
            f.write(f"评估时间: {summary['total_time']:.2f}秒\n\n")

            conclusion = summary['filter_conclusion']
            f.write("="*80 + "\n")
            f.write("筛选结论\n")
            f.write("="*80 + "\n\n")

            status_map = {
                'pass': '✓ 通过',
                'warning': '⚠ 警告',
                'fail': '❌ 失效'
            }
            f.write(f"整体状态: {status_map.get(conclusion['overall_status'], conclusion['overall_status'])}\n\n")

            summary_stats = conclusion['summary']
            f.write(f"评估子任务数: {summary_stats['evaluated_subtasks']}/{summary_stats['total_subtasks']}\n")
            f.write(f"有回退的子任务: {summary_stats['subtasks_with_regressions']}\n")
            f.write(f"有停滞的子任务: {summary_stats.get('subtasks_with_stagnations', 0)}\n")
            f.write(f"低于阈值的子任务: {summary_stats['subtasks_below_threshold']}\n")
            f.write(f"总回退次数: {summary_stats['total_regression_count']}\n")
            f.write(f"总停滞段数: {summary_stats.get('total_stagnation_count', 0)}\n\n")

            if conclusion['problematic_subtasks']:
                f.write(f"有问题的子任务: {conclusion['problematic_subtasks']}\n\n")

            f.write("="*80 + "\n")
            f.write("子任务详情\n")
            f.write("="*80 + "\n\n")

            for detail in conclusion['classification_details']:
                f.write(f"子任务 {detail['subtask_idx']}: {detail['description']}\n")
                f.write(f"  进度: {detail['progress']:.2%}\n")
                f.write(f"  状态: {detail['status']} - {detail['reason']}\n")

                if detail['has_regressions']:
                    f.write(f"  回退次数: {detail['regression_count']}\n")
                    f.write(f"  模式: {detail['pattern']}\n")
                    f.write(f"  严重程度: {detail['severity']}\n")
                    if detail['risk_reasons']:
                        f.write(f"  风险因素: {', '.join(detail['risk_reasons'])}\n")
                    # 逐条回退详情（供归因系统校验）
                    for r in detail.get('regressions', []):
                        rtype = r.get('regression_type', 'instant')
                        peak_info = ""
                        if 'peak_frame' in r:
                            peak_info = f"  peak={r['peak_frame']} trough={r['trough_frame']}"
                        after_str = ', '.join(f'{p:.2f}' for p in r.get('progress_after_7', []))
                        f.write(f"    [{rtype}] frame={r['frame_index']} "
                                f"hop={r['hop']:.3f} "
                                f"before={r['progress_before']:.3f} "
                                f"after_7({r['samples_after_available']}): [{after_str}]"
                                f"{peak_info}\n")

                if detail.get('has_stagnations'):
                    f.write(f"  停滞段数: {detail['stagnation_count']}\n")
                    f.write(f"  停滞严重程度: {detail['stagnation_severity']}\n")
                    for s in detail.get('stagnations', []):
                        f.write(f"    [帧{s['start_frame']}-{s['end_frame']}] "
                                f"平均进度={s['avg_progress']:.1%} "
                                f"振幅={s['amplitude']:.3f} "
                                f"→ {s['severity']}\n")

                f.write("\n")

            f.write("="*80 + "\n")
            f.write("评估方法说明\n")
            f.write("="*80 + "\n\n")
            f.write("子任务评估:\n")
            f.write("  - 将任务按时间分割成子任务\n")
            f.write("  - 每个子任务独立评估进度\n")
            f.write(f"  - 子任务进度 < {self.filter_config.SUBTASK_COMPLETION_THRESHOLD:.0%} 仅记录低完成度，不单独裁决失效\n\n")
            f.write("回退检测（双通道）:\n")
            f.write(f"  通道一 瞬时: hop < {self.filter_config.REGRESSION_THRESHOLD:.0%}\n")
            f.write(f"  通道二 累计: 连续负hop(≤{self.filter_config.CUM_REGRESSION_MAX_SAMPLES}样本) 累计drop < {self.filter_config.CUM_REGRESSION_THRESHOLD:.0%}\n")
            f.write("  去重: instant在cumulative区间内则合并为 instant+cumulative\n")
            f.write("  - V形模式: 回退后立即恢复\n")
            f.write("  - 凹槽模式: 回退后平稳再恢复\n\n")
            f.write("停滞检测 (三层漏斗):\n")
            rule_fps = self.timeline.rule_fps if getattr(self,'frame_source',None) is not None else self.fps
            sps = self.frame_interval / rule_fps if rule_fps else None
            if sps is not None:
                f.write(f"  采样间隔: 每{sps:.2f}源秒一个样本\n")
            else:
                f.write("  按可用源时间计算持续时长；源时间缺失时不执行秒级停滞检测。\n")
            f.write(f"  漏斗一 (触发): abs(hop) < {self.filter_config.NEAR_ZERO_THRESHOLD}\n")
            f.write(f"  漏斗二 (校验):\n")
            f.write(f"    完成区豁免: 进度 ≥ {self.filter_config.STAG_COMPLETION_SHIELD:.0%} 直接忽略\n")
            f.write(f"    振幅上限: 停滞段内振幅 < {self.filter_config.STAGNATION_AMPLITUDE_MAX}（防缓慢爬行）\n")
            f.write(f"    区域最小持续时长（时间基准 → 样本最近邻换算）:\n")
            f.write(f"      启动区 (<{self.filter_config.STAG_START_ZONE:.0%}): ≥{self.filter_config.STAG_MIN_RELAXED_SEC}s\n")
            f.write(f"      执行区 ({self.filter_config.STAG_START_ZONE:.0%}~{self.filter_config.STAG_END_ZONE:.0%}): ≥{self.filter_config.STAG_MIN_STRICT_SEC}s\n")
            f.write(f"      收尾区 ({self.filter_config.STAG_END_ZONE:.0%}~{self.filter_config.STAG_COMPLETION_SHIELD:.0%}): ≥{self.filter_config.STAG_MIN_RELAXED_SEC}s\n")
            f.write(f"    活动修正: 启动区/收尾区内振幅 > {self.filter_config.STAG_ACTIVITY_AMPLITUDE} → 降为执行区严格门槛 ({self.filter_config.STAG_MIN_STRICT_SEC}s)\n")
            f.write(f"  漏斗三 (二维分级, 取OR更高级):\n")
            f.write(f"    严重: >{self.filter_config.STAG_SEVERE_SEC}s 或 >{self.filter_config.STAG_SEVERE_RATIO:.0%}占比\n")
            f.write(f"    中等: >{self.filter_config.STAG_MEDIUM_SEC}s 或 >{self.filter_config.STAG_MEDIUM_RATIO:.0%}占比\n")
            f.write(f"    轻微: 其余\n\n")
            f.write("风险评分:\n")
            f.write("  - 高进度时回退: +1分\n")
            f.write("  - 纯瞬时回退且非连续: +1分（cumulative类型豁免）\n")
            f.write("  - 评分 >= 2: 可能是误判\n")

        print(f"✓ 报告已保存: {report_path}")

    def _render_video(self, directory):
        """Record export failures without discarding valid model observations."""
        video = directory / 'reward_vis.mp4'
        try:
            plot_video_reward(directory)
            if not video.is_file() or not video.stat().st_size:
                raise RuntimeError('Video renderer did not produce reward_vis.mp4')
            status = {'status': 'completed', 'path': str(video), 'fps': 4,
                      'time_source': 'sampled_review', 'score_image_binding': 'input_after_frame_index'}
            print(f"✓ 进度视频已保存: {video}")
        except Exception as exc:
            status = {'status': 'failed', 'error': str(exc)}
            (directory / 'visualization.json').write_text(json.dumps(status, indent=2, ensure_ascii=False))
            raise RuntimeError(f"Video export failed: {exc}") from exc
        (directory / 'visualization.json').write_text(json.dumps(status, indent=2, ensure_ascii=False))

    def save_overall_predictions(self):
        """生成总体进度视频，合并所有子任务，显示总体进度和HOP"""
        if not self.combined_samples:
            print("⚠ 无合并样本，跳过视频生成")
            return

        print(f"\n{'='*70}")
        print(f"保存总体进度数据 (共 {len(self.combined_samples)} 个样本)")
        print(f"{'='*70}")

        # 全局参考帧
        first_frame = self.combined_samples[0]['frame_index']
        last_sample = self.combined_samples[-1]
        last_frame = last_sample.get('input_after_frame_index', last_sample['frame_index'])

        ref_start_path = self.image_path("cam_high", first_frame)
        ref_end_path = self.image_path("cam_high", last_frame)

        # 构建合并的 pred_vllm.json（总体进度 + 总体HOP）
        items = []
        prev_progress = 0.0

        for k, sample in enumerate(self.combined_samples):
            frame_idx = sample['frame_index']
            progress = sample['progress']

            if k == 0:
                hop = progress
            else:
                hop = progress - prev_progress

            after_frame_idx = sample.get('input_after_frame_index', frame_idx)
            prev_frame_idx = sample.get('hop_start_frame_index',
                self.combined_samples[k - 1].get('input_after_frame_index',
                    self.combined_samples[k - 1]['frame_index']) if k > 0 else frame_idx)

            af_high = self.image_path("cam_high", after_frame_idx)
            af_left = self.image_path("cam_left_wrist", after_frame_idx)
            af_right = self.image_path("cam_right_wrist", after_frame_idx)

            bf_high = self.image_path("cam_high", prev_frame_idx)
            bf_left = self.image_path("cam_left_wrist", prev_frame_idx)
            bf_right = self.image_path("cam_right_wrist", prev_frame_idx)

            items.append({
                'sample': k,
                'subtask_idx': sample['subtask_idx'],
                'frame_index': frame_idx,
                'progress': progress,
                'hop': hop,
                **self._frame_time_evidence(frame_idx),
                **({key: sample[key] for key in (
                    'input_after_frame_index', 'hop_start_frame_index', 'hop_end_frame_index',
                    'playback_start_time_sec', 'playback_end_time_sec',
                    'source_start_time_sec', 'source_end_time_sec') if key in sample}),
                'image': [ref_start_path, ref_end_path,
                          bf_high, bf_left, bf_right,
                          af_high, af_left, af_right]
            })

            prev_progress = progress

        # 保存到输出目录根
        pred_path = self.output_root / "pred_vllm.json"
        with open(pred_path, 'w', encoding='utf-8') as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        print(f"✓ 总体进度数据已保存: {pred_path}")

    def generate_overall_video(self):
        """Compatibility entry for explicitly requested native video export."""
        self.save_overall_predictions()
        self._render_video(self.output_root)

    def plot_progress_chart(self, cumulative_progress: List[Dict],
                            filter_conclusion: Dict):
        """生成进度可视化图表"""
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10),
                                        height_ratios=[2, 1])

        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
                  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']

        scale = 1.0 / self.total_subtasks

        # 1. 总进度图 (ax1) — 使用合并样本绘制完整进度曲线（含中间点）
        if self.combined_samples:
            all_frames = [s.get('input_after_frame_index', s['frame_index']) for s in self.combined_samples]
            all_progs = [s['progress'] for s in self.combined_samples]

            # 绘制连续进度线
            ax1.plot(all_frames, all_progs, '-', color='#333333',
                     linewidth=1.5, alpha=0.6, zorder=3)

            # 按子任务着色绘制散点
            for k, sample in enumerate(self.combined_samples):
                si = sample['subtask_idx']
                color = colors[si % len(colors)]
                # 根据子任务状态调整颜色
                cp = cumulative_progress[si] if si < len(cumulative_progress) else {}
                if cp.get('has_regressions'):
                    color = '#ff7f0e' if cp.get('pattern') != 'valley' else '#d62728'
                elif cp.get('is_below_threshold'):
                    color = '#d62728'
                ax1.scatter([sample.get('input_after_frame_index', sample['frame_index'])], [sample['progress']],
                           color=color, s=25, zorder=5, alpha=0.8)

            # 在每个子任务末尾标注百分比
            for i, cp in enumerate(cumulative_progress):
                end_y = cp['total_progress']
                color = colors[i % len(colors)]
                if cp.get('has_regressions'):
                    color = '#ff7f0e' if cp.get('pattern') != 'valley' else '#d62728'
                elif cp.get('is_below_threshold'):
                    color = '#d62728'
                ax1.scatter([cp['end_frame']], [end_y], color=color, s=100,
                           marker='o' if not cp.get('is_below_threshold') else 's',
                           zorder=6, edgecolor='black', linewidth=1.5,
                           label=f"Step {i}")
                ax1.text(cp['end_frame'], end_y - 0.04, f"{end_y*100:.0f}%",
                        ha='center', fontsize=9, color=color, fontweight='bold')
        else:
            # 回退：仅绘制起止点
            for i, cp in enumerate(cumulative_progress):
                start_frame = cp['start_frame']
                end_frame = cp['end_frame']
                base_progress = i * scale
                color = colors[i % len(colors)]
                if cp.get('has_regressions'):
                    color = '#ff7f0e' if cp.get('pattern') != 'valley' else '#d62728'
                elif cp.get('is_below_threshold'):
                    color = '#d62728'
                end_y = cp['total_progress']
                ax1.plot([start_frame, end_frame], [base_progress, end_y],
                        color=color, linewidth=2, label=f"Step {i}")
                ax1.scatter([end_frame], [end_y], color=color, s=100,
                           marker='o' if not cp.get('is_below_threshold') else 's',
                           zorder=5, edgecolor='black', linewidth=1.5)
                ax1.text(end_frame, end_y - 0.04, f"{end_y*100:.0f}%",
                        ha='center', fontsize=9, color=color, fontweight='bold')

        # 添加子任务分割线
        for i in range(1, self.total_subtasks):
            boundary_frame = (self.subtasks[i]['_start_frame'] if '_start_frame' in self.subtasks[i]
                              else self.time_to_frame(self.subtasks[i]['clip_start_sec']))
            ax1.axvline(x=boundary_frame, color='gray', linestyle='--', alpha=0.5, linewidth=1)
            ax1.text(boundary_frame, 1.02, f"Step {i}",
                    ha='center', fontsize=9, color='gray', rotation=0)

        # 停滞区间叠加（橙色半透明底色）
        if self.combined_samples:
            for i, cp in enumerate(cumulative_progress):
                stag_list = cp.get('stagnations', [])
                if not stag_list:
                    continue
                for stag in stag_list:
                    x0 = stag.get('start_frame', 0)
                    x1 = stag.get('end_frame', x0)
                    sev = stag.get('severity', '轻微')
                    alpha = 0.30 if sev == '严重' else (0.20 if sev == '中等' else 0.12)
                    ax1.axvspan(x0, x1, color='#FF8C00', alpha=alpha, zorder=1)

        # 标注失效点
        failed_at = filter_conclusion['statistics'].get('failed_at_subtask')
        if failed_at is not None and failed_at < len(cumulative_progress):
            fail_frame = cumulative_progress[failed_at]['end_frame']
            fail_progress = cumulative_progress[failed_at]['total_progress']
            ax1.scatter([fail_frame], [fail_progress],
                       color='red', s=300, marker='x', linewidths=4,
                       label=f'Failed at Step {failed_at}', zorder=10)

        ax1.set_xlabel('Frame Number', fontsize=12)
        ax1.set_ylabel('Total Progress', fontsize=12)
        ax1.set_title('Subtask-wise Progress Evaluation with Regression Detection', fontsize=14, fontweight='bold')
        ax1.set_ylim(-0.05, 1.15)
        ax1.set_xlim(0, self.total_frames)
        ax1.legend(loc='upper left', fontsize=9, ncol=2)
        ax1.grid(True, alpha=0.3, linestyle=':')
        ax1.set_yticks([i * scale for i in range(self.total_subtasks + 1)])
        ax1.set_yticklabels([f"{i*scale*100:.0f}%" for i in range(self.total_subtasks + 1)])

        # 2. 子任务内部进度图 (ax2) - 标注回退状态
        subtask_nums = [cp['step'] + 1 for cp in cumulative_progress]
        subtask_progresses = [cp['subtask_progress'] for cp in cumulative_progress]

        bar_colors = []
        for i, cp in enumerate(cumulative_progress):
            if cp.get('is_below_threshold'):
                bar_colors.append('#d62728')  # 红色 - 失效
            elif cp.get('pattern') == 'valley':
                bar_colors.append('#d62728')  # 红色 - 凹槽
            elif cp.get('has_regressions'):
                bar_colors.append('#ff7f0e')  # 橙色 - V形
            else:
                bar_colors.append(colors[i % len(colors)])

        bars = ax2.bar(subtask_nums, subtask_progresses,
                      color=bar_colors, alpha=0.7, edgecolor='black', linewidth=1.5)

        # 添加80%阈值线
        ax2.axhline(y=self.filter_config.SUBTASK_COMPLETION_THRESHOLD, color='red',
                   linestyle='--', linewidth=2, label=f'{self.filter_config.SUBTASK_COMPLETION_THRESHOLD:.0%} Threshold', alpha=0.7)

        # 标注进度值
        for bar, prog in zip(bars, subtask_progresses):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.02,
                    f'{prog*100:.0f}%',
                    ha='center', va='bottom', fontsize=10, fontweight='bold')

        ax2.set_xlabel('Subtask Number', fontsize=12)
        ax2.set_ylabel('Subtask Internal Progress', fontsize=12)
        ax2.set_title('Completion Rate per Subtask (Colored by Regression Status)', fontsize=14, fontweight='bold')
        ax2.set_ylim(0, 1.1)
        ax2.set_xticks(subtask_nums)
        ax2.set_xticklabels([f'Step {i}' for i in subtask_nums])
        ax2.legend()
        ax2.grid(True, alpha=0.3, axis='y', linestyle=':')

        plt.tight_layout()
        chart_path = self.output_root / 'progress_chart.png'
        plt.savefig(chart_path, dpi=150, bbox_inches='tight')
        print(f"✓ Progress chart saved: {chart_path}")
        plt.close()


def load_subtasks_from_config(config_path: str) -> tuple:
    """
    从JSON文件加载子任务配置

    Returns:
        tuple: (subtasks列表, task_name)
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if 'subtasks' in data:
        return data['subtasks'], data.get('task_name', 'unknown')
    elif 'language_queries' in data:
        return data['language_queries'], data.get('task_name', 'unknown')
    elif isinstance(data, list):
        return data, 'unknown'
    else:
        raise ValueError(f"无法解析的配置格式: {config_path}")
