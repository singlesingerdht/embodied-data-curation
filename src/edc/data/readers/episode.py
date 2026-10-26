from pathlib import Path
from typing import Optional
from .single import SingleEpisodeReader

class EpisodeReader(SingleEpisodeReader):
    format = "episode"

def _find_traj_json(p: Path) -> Optional[Path]:
    """找到轨迹 JSON 文件 (含 data[] + follow_/joint 字段)。"""
    for f in sorted(p.glob("*.json")):
        if f.name.startswith("label") or f.name.startswith("key_frames"):
            continue
        if f.name.startswith("gemini"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                head = fh.read(8192)
            if '"data"' not in head:
                continue
            if "follow_" in head or "joint_pos" in head:
                return f
        except (OSError, UnicodeDecodeError):
            continue
    return None
