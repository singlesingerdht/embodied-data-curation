"""
常规视频健康检查 — 基于 ffprobe/ffmpeg，不依赖 AI 模型。
检测: 丢帧 / 帧率不稳 (黑屏和卡顿已迁移至 frame_quality.py DF 方案)
"""
import os
import subprocess
import json
import numpy as np
from typing import List, Dict


class VideoHealthChecker:
    def __init__(self,
                 drop_ratio_threshold: float = 0.02):
        self.drop_ratio_threshold = drop_ratio_threshold

    def check(self, video_path: str) -> dict:
        """对单个视频执行健康检查 (丢帧 + 帧率稳定)"""
        video_path = str(video_path)
        result = {
            'path': video_path,
            'exists': os.path.exists(video_path),
            'is_healthy': True,
            'issues': [],
            'frame_drops': {'is_dropped': False},
            'frame_rate_stability': {'is_unstable': False},
        }
        if not result['exists']:
            result['is_healthy'] = False
            result['issues'].append('file_not_found')
            return result

        info = self._get_video_info(video_path)
        if not info:
            result['is_healthy'] = False
            result['issues'].append('ffprobe_failed')
            return result

        fd = self._check_frame_drops(info)
        fps_st = self._check_frame_rate_stability(video_path, info)

        result['frame_drops'] = fd
        result['frame_rate_stability'] = fps_st

        if fd['is_dropped']:
            result['issues'].append('frame_drops')
            result['is_healthy'] = False
        if fps_st['is_unstable']:
            result['issues'].append('unstable_fps')
            result['is_healthy'] = False

        return result

    def _get_video_info(self, video_path: str) -> dict:
        cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries',
            'stream=width,height,r_frame_rate,duration,nb_frames,nb_read_frames,codec_name',
            '-of', 'json', video_path
        ]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            data = json.loads(r.stdout.decode('utf-8', errors='ignore'))
            stream = data.get('streams', [{}])[0]
            w = int(stream.get('width', 0))
            h = int(stream.get('height', 0))
            dur = float(stream.get('duration', 0))
            fps_str = stream.get('r_frame_rate', '0/1')
            num, den = map(int, fps_str.split('/'))
            fps = num / den if den else 0
            nb = int(stream.get('nb_frames', 0) or stream.get('nb_read_frames', 0) or 0)
            return {
                'width': w, 'height': h, 'fps': fps, 'duration': dur,
                'nb_frames': nb, 'codec': stream.get('codec_name', ''),
                'expected_frames': max(1, round(dur * fps)),
            }
        except Exception:
            return {}

    def _check_frame_drops(self, info: dict) -> dict:
        expected = info.get('expected_frames', 0)
        actual = info.get('nb_frames', 0)
        if expected <= 0 or actual <= 0:
            return {'is_dropped': False, 'drop_ratio': 0, 'expected': expected, 'actual': actual}
        ratio = max(0, 1 - actual / expected)
        return {
            'is_dropped': ratio > self.drop_ratio_threshold,
            'drop_ratio': round(ratio, 4),
            'expected': expected,
            'actual': actual,
        }

    def _check_frame_rate_stability(self, video_path: str, info: dict) -> dict:
        cmd = ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
               '-show_entries', 'packet=pts_time', '-of', 'csv=p=0', video_path]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            lines = r.stdout.decode('utf-8', errors='ignore').strip().split('\n')
            pts = []
            for line in lines:
                try:
                    pts.append(float(line.strip()))
                except ValueError:
                    continue
            if len(pts) < 3:
                return {'is_unstable': False, 'interval_mean_ms': 0, 'interval_std_ms': 0}
            pts_sorted = sorted(pts)
            intervals = np.diff(pts_sorted) * 1000
            median = float(np.median(intervals))
            valid = [v for v in intervals if v < median * 3 and v > 0]
            if len(valid) < 3:
                return {'is_unstable': False, 'interval_mean_ms': round(median, 2), 'interval_std_ms': 0}
            mean_ms = float(np.mean(valid))
            std_ms = float(np.std(valid))
            is_unstable = (std_ms > mean_ms * 0.3) or (std_ms > 10)
            return {
                'is_unstable': is_unstable,
                'interval_mean_ms': round(mean_ms, 2),
                'interval_std_ms': round(std_ms, 2),
            }
        except Exception:
            return {'is_unstable': False, 'interval_mean_ms': 0, 'interval_std_ms': 0}