"""Pure statistics for dataset initial-state spatial distributions.

Each configured category is evaluated independently.  An episode contributes
unit mass to a category heatmap, split equally among all detected instances in
that episode.  The category score is

    100 * normalized_spatial_entropy * valid_episode_ratio

so repeated layouts score low, broad/balanced layouts score high, and missing
detections cannot silently inflate the distribution score.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_GRID_SIZE = (8, 8)
DEFAULT_SOURCE = "faceImg.mp4"
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_NMS_IOU = 0.70


def load_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the small user-facing configuration."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return normalize_config(raw)


def normalize_config(raw):
    """Validate a configuration supplied directly by an EDC plugin."""
    if not isinstance(raw, dict):
        raise ValueError("配置根节点必须是 JSON object")

    objects = raw.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("配置必须包含非空 objects 列表")

    normalized_objects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(objects):
        if not isinstance(item, dict):
            raise ValueError(f"objects[{index}] 必须是 JSON object")
        name = str(item.get("name", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not name or not prompt:
            raise ValueError(f"objects[{index}] 必须同时提供 name 和 prompt")
        if name in seen:
            raise ValueError(f"类别 name 重复: {name}")
        seen.add(name)
        threshold = float(
            item.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
        )
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"{name}.confidence_threshold 必须在 [0, 1]")
        normalized_objects.append(
            {
                "name": name,
                "prompt": prompt,
                "confidence_threshold": threshold,
            }
        )

    grid = raw.get("grid_size", list(DEFAULT_GRID_SIZE))
    if isinstance(grid, int):
        grid = [grid, grid]
    if not (
        isinstance(grid, list)
        and len(grid) == 2
        and all(isinstance(v, int) and v > 1 for v in grid)
    ):
        raise ValueError("grid_size 必须是大于 1 的整数或 [rows, cols]")

    frame_index = int(raw.get("frame_index", 0))
    if frame_index < 0:
        raise ValueError("frame_index 不能为负数")
    nms_iou = float(raw.get("nms_iou", DEFAULT_NMS_IOU))
    if not 0.0 <= nms_iou <= 1.0:
        raise ValueError("nms_iou 必须在 [0, 1]")

    return {
        "source": str(raw.get("source", DEFAULT_SOURCE)),
        "frame_index": frame_index,
        "grid_size": [int(grid[0]), int(grid[1])],
        "nms_iou": nms_iou,
        "objects": normalized_objects,
    }


def _category_distribution(
    episodes: list[dict[str, Any]],
    category_name: str,
    confidence_threshold: float,
    rows: int,
    cols: int,
) -> dict[str, Any]:
    heatmap = np.zeros((rows, cols), dtype=np.float64)
    valid_episodes = 0
    total_instances = 0
    confidences: list[float] = []

    for episode in episodes:
        category = (episode.get("categories") or {}).get(category_name) or {}
        instances = category.get("instances") or []
        usable = [
            instance
            for instance in instances
            if isinstance(instance.get("centroid_norm"), list)
            and len(instance["centroid_norm"]) == 2
            and float(instance.get("confidence", 1.0)) >= confidence_threshold
        ]
        if not usable:
            continue

        valid_episodes += 1
        total_instances += len(usable)
        episode_heatmap = np.zeros_like(heatmap)
        for instance in usable:
            x, y = (float(v) for v in instance["centroid_norm"])
            # A mask centroid may land exactly on the image boundary after
            # clipping/rounding.  Keep it inside the final cell.
            x = min(max(x, 0.0), np.nextafter(1.0, 0.0))
            y = min(max(y, 0.0), np.nextafter(1.0, 0.0))
            col = min(cols - 1, int(x * cols))
            row = min(rows - 1, int(y * rows))
            episode_heatmap[row, col] += 1.0 / len(usable)
            if "confidence" in instance:
                confidences.append(float(instance["confidence"]))
        heatmap += episode_heatmap

    total_episodes = len(episodes)
    valid_ratio = valid_episodes / total_episodes if total_episodes else 0.0
    mass = float(heatmap.sum())
    probabilities = heatmap / mass if mass > 0 else heatmap.copy()
    positive = probabilities[probabilities > 0]
    entropy = float(-(positive * np.log(positive)).sum()) if positive.size else 0.0
    max_entropy = math.log(rows * cols)
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0.0
    score = 100.0 * normalized_entropy * valid_ratio

    return {
        "score": round(score, 1),
        "normalized_entropy": round(normalized_entropy, 6),
        "entropy": round(entropy, 6),
        "valid_episode_ratio": round(valid_ratio, 6),
        "valid_episodes": valid_episodes,
        "total_episodes": total_episodes,
        "total_instances": total_instances,
        "occupied_cells": int(np.count_nonzero(heatmap)),
        "grid_cells": rows * cols,
        "mean_confidence": (
            round(float(np.mean(confidences)), 6) if confidences else None
        ),
        "heatmap": [[round(float(v), 6) for v in row] for row in probabilities],
    }


def compute_distribution_summary(
    detections: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Compute one spatial-distribution score per configured category."""
    episodes = detections.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("检测结果不包含有效 episodes")
    rows, cols = config["grid_size"]

    categories: dict[str, Any] = {}
    warnings: list[str] = []
    extraction_error_count = int(detections.get("extraction_error_count", 0))
    if extraction_error_count:
        warnings.append(
            f"{extraction_error_count}/{len(episodes)} 个 episode 首帧读取或检测失败；"
            "已作为无有效检测计入得分分母"
        )
    for obj in config["objects"]:
        result = _category_distribution(
            episodes,
            obj["name"],
            obj["confidence_threshold"],
            rows,
            cols,
        )
        result["prompt"] = obj["prompt"]
        result["confidence_threshold"] = obj["confidence_threshold"]
        categories[obj["name"]] = result
        if result["valid_episodes"] == 0:
            warnings.append(
                f"类别 {obj['name']} 在全部 episode 中均无有效检测，无法可靠评估"
            )
        elif result["valid_episode_ratio"] < 1.0:
            warnings.append(
                f"类别 {obj['name']} 仅在 "
                f"{result['valid_episodes']}/{result['total_episodes']} 个 episode 中检测到"
            )

    scores = [item["score"] for item in categories.values()]
    return {
        "metric": "initial_state_distribution",
        "formula": "category=100*normalized_entropy*valid_episode_ratio; total=mean(categories)",
        "grid_size": [rows, cols],
        "total_episodes": len(episodes),
        "extraction_error_count": extraction_error_count,
        "category_count": len(categories),
        "score": round(sum(scores) / len(scores), 1),
        "categories": categories,
        "warnings": warnings,
    }
