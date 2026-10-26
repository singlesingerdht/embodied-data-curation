from __future__ import annotations

from typing import Any

import numpy as np

from .parameters import (
    RobotParameters,
    compute_error,
    compute_parameter_score,
)
from .loader import Episode


def compute_episode_metrics(
    episode: Episode,
    group: str,
    robot_params: RobotParameters,
    home_position: dict[str, dict[str, Any]] | None = None,
    tolerance1: dict[str, dict[str, Any]] | None = None,
    tolerance2: dict[str, dict[str, Any]] | None = None,
    enabled_parameters: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "group": group,
        "num_samples": episode.get_samples(group),
    }

    start = episode.get_start_pose(group)
    end = episode.get_end_pose(group)

    group_params = robot_params.get_group(group)
    enabled_set = set(enabled_parameters) if enabled_parameters else None

    param_results: list[dict[str, Any]] = []
    first_frame_results: list[dict[str, Any]] = []

    for param in group_params:
        if enabled_set is not None and param.path not in enabled_set:
            continue
        if param.key not in start:
            continue

        home_val = None
        if home_position and group in home_position:
            home_val = home_position[group].get(param.key)

        if home_val is None:
            home_val = start[param.key]

        current_val = end[param.key]
        err = compute_error(current_val, home_val, param.param_type)

        tol1 = None
        tol2 = None
        score = 100.0
        if tolerance1 and tolerance2 and group in tolerance1 and group in tolerance2:
            tol1 = tolerance1[group].get(param.key)
            tol2 = tolerance2[group].get(param.key)
            if tol1 is not None and tol2 is not None:
                score = compute_parameter_score(err, tol1, tol2, param.param_type)

        entry: dict[str, Any] = {
            "path": param.path,
            "key": param.key,
            "type": param.param_type,
            "error": err,
            "score": score,
            "home_value": home_val,
            "current_value": current_val,
        }
        if tol1 is not None and tol2 is not None:
            entry["tolerance1"] = tol1
            entry["tolerance2"] = tol2
        param_results.append(entry)

        start_val = start[param.key]
        first_err = compute_error(start_val, home_val, param.param_type)
        first_score = 100.0
        if tol1 is not None and tol2 is not None:
            first_score = compute_parameter_score(first_err, tol1, tol2, param.param_type)
        first_entry: dict[str, Any] = {
            "path": param.path,
            "key": param.key,
            "type": param.param_type,
            "error": first_err,
            "score": first_score,
            "home_value": home_val,
            "current_value": start_val,
        }
        if tol1 is not None and tol2 is not None:
            first_entry["tolerance1"] = tol1
            first_entry["tolerance2"] = tol2
        first_frame_results.append(first_entry)

    result["parameters"] = param_results
    result["first_frame_parameters"] = first_frame_results
    result["first_frame_scores"] = [p["score"] for p in first_frame_results]
    result["last_frame_scores"] = [p["score"] for p in param_results]

    return result
