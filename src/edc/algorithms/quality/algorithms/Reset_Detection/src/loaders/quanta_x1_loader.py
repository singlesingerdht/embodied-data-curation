from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.loader import Episode, GroupData
from .base_loader import BaseLoader


_EXCLUDED_JSON = {"subtasks_quanta_x1.json", "subtasks_segmentation.json"}

_SOURCE_TO_GROUP: dict[str, str] = {
    "follow_left": "left_arm",
    "follow_right": "right_arm",
}

_GROUP_SOURCE_MAP: dict[str, dict[str, str]] = {
    "left_arm": {
        "joint_pos": "follow_left_joint_pos",
        "position": "follow_left_position",
        "rotation": "follow_left_rotation",
        "gripper": "follow_left_gripper",
    },
    "right_arm": {
        "joint_pos": "follow_right_joint_pos",
        "position": "follow_right_position",
        "rotation": "follow_right_rotation",
        "gripper": "follow_right_gripper",
    },
    "base": {
        "car_pose_position": "car_pose_position",
        "car_pose_rotation": "car_pose_rotation",
    },
    "lifting": {
        "lifting_mechanism_position": "lifting_mechanism_position",
    },
    "head": {
        "head_rotation": "head_rotation",
    },
}

_MASTER_SOURCES: set[str] = {"master_left", "master_right"}


def _find_data_file(episode_path: Path) -> Path | None:
    json_files = sorted(
        f for f in episode_path.glob("*.json")
        if f.name not in _EXCLUDED_JSON
    )
    if not json_files:
        return None
    dir_name = episode_path.name
    for f in json_files:
        if f.stem == dir_name:
            return f
    return json_files[0]


def _extract_group_frame(group_key: str, raw: dict[str, Any]) -> dict[str, Any]:
    field_map = _GROUP_SOURCE_MAP.get(group_key, {})
    frame: dict[str, Any] = {}
    for target_key, source_key in field_map.items():
        if source_key in raw:
            frame[target_key] = raw[source_key]
    return frame


class QuantaX1Loader(BaseLoader):
    def load_episode(self, episode_path: Path) -> Episode:
        episode_path = Path(episode_path)
        if not episode_path.is_dir():
            raise NotADirectoryError(f"Episode path is not a directory: {episode_path}")

        data_file = _find_data_file(episode_path)
        if data_file is None:
            raise FileNotFoundError(
                f"No quanta_x1 data file found in {episode_path}. "
                f"Expected a *.json file (excluding subtasks_*.json)"
            )

        with open(data_file) as f:
            raw = json.load(f)

        raw_frames = raw.get("data", raw)
        if isinstance(raw_frames, dict):
            raw_frames = [raw_frames]
        if not isinstance(raw_frames, list):
            raise ValueError(
                f"Expected JSON with a top-level 'data' array or a list, "
                f"got {type(raw_frames).__name__}"
            )

        group_frames: dict[str, list[dict[str, Any]]] = {}
        found_master: set[str] = set()

        for raw_frame in raw_frames:
            if not isinstance(raw_frame, dict):
                continue

            for group_key in _GROUP_SOURCE_MAP:
                sample = _extract_group_frame(group_key, raw_frame)
                if sample:
                    group_frames.setdefault(group_key, []).append(sample)

            for mkey in _MASTER_SOURCES:
                if f"{mkey}_joint_pos" in raw_frame:
                    found_master.add(mkey)

        meta: dict[str, Any] = {
            "_quanta_x1_sources": {
                "groups": sorted(group_frames.keys()),
                "ignored_master": sorted(found_master),
            }
        }

        episode = Episode(path=episode_path, meta=meta)

        for group_key, frames in group_frames.items():
            episode.groups[group_key] = GroupData(
                group_name=group_key,
                frames=frames,
                fps=0,
            )

        return episode
