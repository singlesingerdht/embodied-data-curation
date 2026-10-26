from __future__ import annotations

from pathlib import Path
from typing import Any

from .calibration import find_episode_dirs, find_quanta_x1_episode_dirs, find_kuavo_episode_files
from .config import Config
from .exporter import export_episode_result
from .loaders.loader_factory import LoaderFactory
from .loader import Episode
from .metrics import compute_episode_metrics


def _build_reasons(
    param_results: list[dict[str, Any]],
    path_message_map: dict[str, str],
) -> list[str]:
    reasons: list[str] = []
    for pr in param_results:
        score = pr.get("score", 100)
        if score >= 100:
            continue
        path = pr.get("path", "")
        msg = path_message_map.get(path, path)
        if score == 0:
            reasons.append(f"{msg}严重偏离初始状态（0分）")
        else:
            reasons.append(f"{msg}未完全回到初始状态（{int(score)}分）")
    return reasons


def _compute_assessment_score(
    scores: list[float],
) -> tuple[float, bool]:
    if not scores:
        return 100.0, True
    if any(s == 0.0 for s in scores):
        return 0.0, False
    return float(sum(scores) / len(scores)), True


def check_episode(
    episode: Episode,
    home_position: dict[str, dict] | None = None,
    tolerance1: dict[str, dict] | None = None,
    tolerance2: dict[str, dict] | None = None,
    robot_params=None,
    enabled_parameters: list[str] | None = None,
    path_message_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    if robot_params is None:
        return {
            "episode_path": str(episode.path),
            "first_frame_score": 0.0,
            "last_frame_score": 0.0,
            "first_frame_passed": False,
            "last_frame_passed": False,
            "first_frame_reasons": ["no_robot_params"],
            "last_frame_reasons": ["no_robot_params"],
            "group_metrics": [],
        }

    if path_message_map is None:
        path_message_map = {}

    group_results: list[dict[str, Any]] = []

    for group in episode.get_groups():
        if group not in episode.groups:
            continue
        if not robot_params.get_group(group):
            continue

        metrics = compute_episode_metrics(
            episode, group,
            robot_params=robot_params,
            home_position=home_position,
            tolerance1=tolerance1,
            tolerance2=tolerance2,
            enabled_parameters=enabled_parameters,
        )

        group_results.append(metrics)

    first_scores: list[float] = []
    last_scores: list[float] = []
    for gm in group_results:
        first_scores.extend(gm.get("first_frame_scores", []))
        last_scores.extend(gm.get("last_frame_scores", []))

    first_score, first_passed = _compute_assessment_score(first_scores)
    last_score, last_passed = _compute_assessment_score(last_scores)

    first_reasons: list[str] = []
    last_reasons: list[str] = []
    for gm in group_results:
        first_reasons.extend(_build_reasons(
            gm.get("first_frame_parameters", []), path_message_map
        ))
        last_reasons.extend(_build_reasons(
            gm.get("parameters", []), path_message_map
        ))

    return {
        "episode_path": str(episode.path),
        "first_frame_score": first_score,
        "last_frame_score": last_score,
        "first_frame_passed": first_passed,
        "last_frame_passed": last_passed,
        "first_frame_reasons": first_reasons,
        "last_frame_reasons": last_reasons,
        "group_metrics": group_results,
    }


def scan_episodes(
    root_dir: str | Path,
    config: Config,
    output_dir: str | Path,
    dataset_name: str | None = None,
) -> list[dict[str, Any]]:
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"Root directory not found: {root}")

    if dataset_name is None:
        dataset_name = root.name + "_detection_output"

    loader = LoaderFactory.get_loader(config.robot_name)
    results: list[dict[str, Any]] = []

    tolerance1 = config.tolerance1
    tolerance2 = config.tolerance2
    home_position = config.home_position
    robot_params = config.robot_params

    def _emit(result: dict[str, Any]) -> None:
        export_episode_result(
            result, config,
            dataset_name=dataset_name,
            output_dir=output_dir,
        )

    if config.robot_name == "quanta_x1":
        episode_dirs = find_quanta_x1_episode_dirs(root)
        for ep_dir in episode_dirs:
            try:
                episode = loader.load_episode(ep_dir)
                result = check_episode(
                    episode=episode,
                    home_position=home_position,
                    tolerance1=tolerance1,
                    tolerance2=tolerance2,
                    robot_params=robot_params,
                    enabled_parameters=config.enabled_parameters,
                )
                results.append(result)
                _emit(result)
            except Exception as e:
                err = {
                    "episode_path": str(ep_dir),
                    "first_frame_score": 0.0,
                    "last_frame_score": 0.0,
                    "first_frame_passed": False,
                    "last_frame_passed": False,
                    "first_frame_reasons": [f"load_error: {e}"],
                    "last_frame_reasons": [f"load_error: {e}"],
                    "group_metrics": [],
                    "status": "load_error",
                }
                results.append(err)
                _emit(err)
    elif config.robot_name == "kuavo":
        episode_dirs = find_kuavo_episode_files(root)
        for ep_dir in episode_dirs:
            try:
                episode = loader.load_episode(ep_dir)
                result = check_episode(
                    episode=episode,
                    home_position=home_position,
                    tolerance1=tolerance1,
                    tolerance2=tolerance2,
                    robot_params=robot_params,
                    enabled_parameters=config.enabled_parameters,
                )
                results.append(result)
                _emit(result)
            except Exception as e:
                err = {
                    "episode_path": str(ep_dir),
                    "first_frame_score": 0.0,
                    "last_frame_score": 0.0,
                    "first_frame_passed": False,
                    "last_frame_passed": False,
                    "first_frame_reasons": [f"load_error: {e}"],
                    "last_frame_reasons": [f"load_error: {e}"],
                    "group_metrics": [],
                    "status": "load_error",
                }
                results.append(err)
                _emit(err)
    else:
        all_episode_dirs = find_episode_dirs(root)
        for ep_dir in all_episode_dirs:
            try:
                episode = loader.load_episode(ep_dir)
                result = check_episode(
                    episode=episode,
                    home_position=home_position,
                    tolerance1=tolerance1,
                    tolerance2=tolerance2,
                    robot_params=robot_params,
                    enabled_parameters=config.enabled_parameters,
                )
                results.append(result)
                _emit(result)
            except Exception as e:
                err = {
                    "episode_path": str(ep_dir),
                    "first_frame_score": 0.0,
                    "last_frame_score": 0.0,
                    "first_frame_passed": False,
                    "last_frame_passed": False,
                    "first_frame_reasons": [f"load_error: {e}"],
                    "last_frame_reasons": [f"load_error: {e}"],
                    "group_metrics": [],
                    "status": "load_error",
                }
                results.append(err)
                _emit(err)

    return results
