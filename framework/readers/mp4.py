"""MP4 格式适配器: 单个 MP4 文件 → 1 个 image 通道。

参考: dayan-rough-datachecking src/datafilter/data/readers/mp4.py
"""
import json
import subprocess
from pathlib import Path
from typing import Dict, Optional

from .base import DataReader
from framework.ir.metadata import DataInfo, TopicInfo, FORMAT_MP4, MODALITY_IMAGE


class MP4Reader(DataReader):
    """单个 MP4 文件 / 纯 MP4 目录 → image 通道。"""

    def __init__(self, hints: Optional[Dict] = None):
        self.hints = hints or {}

    def open(self, path: str) -> DataInfo:
        p = Path(path)
        mp4_files = []
        if p.is_file():
            mp4_files = [p]
        elif p.is_dir():
            mp4_files = sorted([f for f in p.iterdir()
                                if f.suffix.lower() == ".mp4"])

        topics = {}
        for mp4 in mp4_files:
            fps, frames, duration, width, height = _ffprobe(mp4)
            name = mp4.stem
            timestamps = None
            if fps > 0 and frames > 0:
                timestamps = [i / fps for i in range(frames)]
            topics[name] = TopicInfo(
                name=name,
                msg_type="video/mp4",
                count=frames,
                modality=MODALITY_IMAGE,
                image_available=True,
                image_compressed=True,
                timestamps=timestamps,
                source=str(mp4),
                timestamps_synthetic=True,
            )

        total_duration = max((t.timestamps[-1] if t.timestamps else 0)
                             for t in topics.values()) if topics else 0
        self._info = DataInfo(
            path=path, format=FORMAT_MP4,
            duration=total_duration,
            topics=topics,
            extra={"video_count": len(mp4_files)},
        )

        return self._info


def _ffprobe(path: Path) -> tuple:
    """ffprobe 取 fps / nb_frames / duration / width / height。"""
    try:
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(path)
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return 30.0, 0, 0, 0, 0
        data = json.loads(out.stdout)
        streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
        if not streams:
            return 30.0, 0, 0, 0, 0
        s = streams[0]
        fps_num, fps_den = 30, 1
        r_frame_rate = s.get("r_frame_rate", "30/1")
        if "/" in r_frame_rate:
            parts = r_frame_rate.split("/")
            try:
                fps_num, fps_den = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass
        fps = fps_num / fps_den if fps_den else 30.0
        frames = int(s.get("nb_frames", 0) or 0)
        if frames == 0:
            duration = float(data.get("format", {}).get("duration", 0) or 0)
            frames = int(duration * fps) if fps > 0 else 0
        duration = float(s.get("duration", data.get("format", {}).get("duration", 0)) or 0)
        width = int(s.get("width", 0) or 0)
        height = int(s.get("height", 0) or 0)
        return fps, frames, duration, width, height
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return 30.0, 0, 0, 0, 0
