"""折叠式数据集目录适配器: 多 episode 目录聚合。

数据集根 = 目录含多个 episode 子目录 (每个子目录是 episode 格式)
聚合策略: 每集独立 EpisodeReader 打开, 全局索引偏移 + 每通道独立时间轴拼接。
单集损坏跳过并记入 extra["corrupted_episodes"]。
"""
from pathlib import Path
from typing import Dict, Optional, List

from .base import DataReader
from .episode import EpisodeReader
from edc.data.ir.metadata import DataInfo, TopicInfo, FORMAT_DATASET, _is_episode_json


class DatasetReader(DataReader):
    """折叠式数据集: 多 episode 目录聚合。"""

    def __init__(self, hints: Optional[Dict] = None):
        self.hints = hints or {}

    def open(self, path: str) -> DataInfo:
        p = Path(path)
        episode_dirs = _find_episode_dirs(p)

        if not episode_dirs:
            raise ValueError(f"数据集目录中无 episode 子目录: {path}")

        all_topics: Dict[str, TopicInfo] = {}
        episodes: List[str] = []
        corrupted: List[dict] = []
        global_offset = 0.0

        for ep_dir in episode_dirs:
            try:
                reader = EpisodeReader(hints=self.hints)
                info = reader.open(str(ep_dir))

                for name, ti in info.topics.items():
                    if name not in all_topics:
                        all_topics[name] = TopicInfo(
                            name=name, msg_type=ti.msg_type, count=0,
                            modality=ti.modality,
                            value_available=ti.value_available,
                            image_available=ti.image_available,
                            image_compressed=ti.image_compressed,
                            timestamps=[], source=ti.source,
                        )
                    merged = all_topics[name]
                    merged.count += ti.count
                    if ti.timestamps:
                        offset_ts = [t + global_offset for t in ti.timestamps]
                        merged.timestamps = (merged.timestamps or []) + offset_ts

                ep_duration = info.duration
                episodes.append({"name": ep_dir.name, "duration": ep_duration,
                                  "topics": len(info.topics)})
                global_offset += ep_duration

            except Exception as e:
                corrupted.append({"name": ep_dir.name, "error": str(e)})

        total_duration = global_offset
        return DataInfo(
            path=path, format=FORMAT_DATASET,
            duration=total_duration,
            topics=all_topics,
            extra={
                "episodes": episodes,
                "corrupted_episodes": corrupted,
                "episode_count": len(episodes),
            },
        )


def _find_episode_dirs(root: Path) -> List[Path]:
    """递归发现 episode 目录 (含 data[].json + *.mp4)。"""
    result = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        if d.name.startswith(".") or d.name in ("__pycache__",):
            continue
        # 直接是 episode
        if _is_episode_dir(d):
            result.append(d)
        # 或者再深一层
        else:
            for sub in sorted(d.iterdir()):
                if sub.is_dir() and _is_episode_dir(sub):
                    result.append(sub)
    return result


def _is_episode_dir(d: Path) -> bool:
    """判断目录是否为 episode (含 data[].json + *.mp4)。"""
    try:
        json_files = list(d.glob("*.json"))
        mp4_files = list(d.glob("*.mp4"))
        if json_files and mp4_files:
            return _is_episode_json(json_files[0])
    except (OSError, PermissionError):
        pass
    return False
