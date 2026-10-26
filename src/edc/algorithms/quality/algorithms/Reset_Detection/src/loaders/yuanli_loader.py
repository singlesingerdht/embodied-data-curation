from __future__ import annotations

from pathlib import Path

from src.loader import Episode, GroupData, load_episode as _load_episode
from .base_loader import BaseLoader

_STANDARD_FIELDS = {"joint_positions", "ee_positions", "gripper"}

_FIELD_ALIASES: dict[str, list[str]] = {
    "joint_positions": ["joint_position", "qpos", "pos"],
    "ee_positions": ["ee_pose", "ee_position", "end_effector", "ee_pos"],
    "gripper": ["gripper_position", "grip"],
}


def _normalize_frame(frame: dict) -> dict:
    result = dict(frame)
    for standard, aliases in _FIELD_ALIASES.items():
        if standard not in result:
            for alias in aliases:
                if alias in result:
                    result[standard] = result[alias]
                    break
    return _split_ee_positions(result)


def _split_ee_positions(frame: dict) -> dict:
    if "ee_positions" not in frame:
        return frame
    val = frame["ee_positions"]
    if len(val) == 7:
        frame["ee_position"] = val[:3]
        frame["ee_orientation"] = val[3:7]
    else:
        print(f"Warning: Unexpected ee_positions length: {len(val)}")
    return frame


class YuanliLoader(BaseLoader):
    def load_episode(self, episode_path: Path) -> Episode:
        episode = _load_episode(episode_path)
        for group_name in list(episode.groups.keys()):
            group = episode.groups[group_name]
            normalized = [_normalize_frame(f) for f in group.frames]
            has_standard = any(k in normalized[0] for k in _STANDARD_FIELDS) if normalized else False
            if not has_standard:
                del episode.groups[group_name]
                continue
            episode.groups[group_name] = GroupData(
                group_name=group_name,
                frames=normalized,
                fps=group.fps,
            )
        return episode
