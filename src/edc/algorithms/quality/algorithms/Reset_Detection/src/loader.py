from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GroupData:
    group_name: str
    frames: list[dict[str, Any]]
    fps: int

    @property
    def start_pose(self) -> dict[str, Any]:
        return self.frames[0]

    @property
    def end_pose(self) -> dict[str, Any]:
        return self.frames[-1]


@dataclass
class Episode:
    path: Path
    meta: dict[str, Any]
    groups: dict[str, GroupData] = field(default_factory=dict)

    def get_groups(self) -> list[str]:
        return list(self.groups.keys())

    def get_samples(self, group: str) -> int:
        return len(self.groups[group].frames)

    def get_start_pose(self, group: str) -> dict[str, Any]:
        return self.groups[group].start_pose

    def get_end_pose(self, group: str) -> dict[str, Any]:
        return self.groups[group].end_pose


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                frames.append(json.loads(line))
    return frames


def load_episode(episode_path: str | Path) -> Episode:
    episode_path = Path(episode_path)
    if not episode_path.is_dir():
        raise NotADirectoryError(f"Episode path is not a directory: {episode_path}")

    meta_path = episode_path / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"meta.json not found in {episode_path}")

    with open(meta_path, "r") as f:
        meta = json.load(f)

    actions_dir = episode_path / "actions"
    if not actions_dir.is_dir():
        raise NotADirectoryError(f"actions directory not found in {episode_path}")

    episode = Episode(path=episode_path, meta=meta)

    action_schemas: dict[str, Any] = meta.get("actions", {})
    for group_name, schema in action_schemas.items():
        ext = schema.get("file_meta", {}).get("ext", "jsonl")
        action_path = actions_dir / f"{group_name}.{ext}"
        if not action_path.exists():
            continue
        frames = _read_jsonl(action_path)
        episode.groups[group_name] = GroupData(
            group_name=group_name,
            frames=frames,
            fps=schema.get("fps", 0),
        )

    return episode
