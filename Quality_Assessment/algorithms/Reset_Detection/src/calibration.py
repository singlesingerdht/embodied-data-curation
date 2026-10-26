from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .config import load_config
from .parameters import (
    RobotParameters,
    compute_statistics,
    compute_tolerance,
)
from .loaders.loader_factory import LoaderFactory


def find_quanta_x1_episode_dirs(root_dir: Path) -> list[Path]:
    episodes: list[Path] = []
    for item in sorted(root_dir.iterdir()):
        if item.is_dir():
            episodes.append(item)
    return episodes


def find_kuavo_episode_files(root_dir: Path) -> list[Path]:
    episodes: list[Path] = []
    for item in sorted(root_dir.iterdir()):
        if item.is_file() and item.suffix == ".bag":
            episodes.append(item)
    return episodes


def find_episode_dirs(root_dir: Path) -> list[Path]:
    episodes: list[Path] = []
    for item in sorted(root_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.name.startswith("episode"):
            episodes.append(item)
        else:
            episodes.extend(find_episode_dirs(item))
    return episodes


def collect_start_poses(
    episode_dirs: list[Path],
    loader,
    robot_params: RobotParameters,
) -> dict[str, list[np.ndarray]]:
    collected: dict[str, list[np.ndarray]] = {}
    for param in robot_params.params:
        collected[param.path] = []

    for ep_dir in episode_dirs:
        try:
            episode = loader.load_episode(ep_dir)
        except Exception:
            continue

        for group_name, group_params in robot_params.get_groups().items():
            if group_name not in episode.groups:
                continue
            group = episode.groups[group_name]
            if not group.frames:
                continue

            pose = group.frames[0]

            for param in group_params:
                if param.key in pose:
                    collected[param.path].append(
                        np.asarray(pose[param.key], dtype=np.float32)
                    )

    return collected


def build_home_position(
    stats: dict[str, dict[str, Any]],
    robot_params: RobotParameters,
) -> dict[str, dict[str, Any]]:
    home: dict[str, dict[str, Any]] = {}
    for group_name, group_params in robot_params.get_groups().items():
        home[group_name] = {}
        for param in group_params:
            if not param.calibrate:
                continue
            if param.path not in stats:
                continue
            s = stats[param.path]
            home[group_name][param.key] = s["mean"]
    return home


def _build_tolerance_group(
    stats: dict[str, dict[str, Any]],
    robot_params: RobotParameters,
    sigma_factor: float = 3.0,
) -> dict[str, dict[str, Any]]:
    tol: dict[str, dict[str, Any]] = {}
    for group_name, group_params in robot_params.get_groups().items():
        tol[group_name] = {}
        for param in group_params:
            if not param.calibrate:
                continue
            if param.path not in stats:
                continue
            s = stats[param.path]
            tol[group_name][param.key] = compute_tolerance(
                s, param.param_type, sigma_factor
            )
    return tol


def build_tolerance1(
    stats: dict[str, dict[str, Any]],
    robot_params: RobotParameters,
    sigma_factor: float = 3.0,
) -> dict[str, dict[str, Any]]:
    return _build_tolerance_group(stats, robot_params, sigma_factor)


def build_tolerance2(
    stats: dict[str, dict[str, Any]],
    robot_params: RobotParameters,
    sigma_factor: float = 6.0,
) -> dict[str, dict[str, Any]]:
    return _build_tolerance_group(stats, robot_params, sigma_factor)


def check_tolerance_warnings(
    tolerances: dict[str, dict[str, Any]],
    robot_params: RobotParameters,
) -> None:
    for group_name, group_params in robot_params.get_groups().items():
        tol_group = tolerances.get(group_name, {})
        for param in group_params:
            tval = tol_group.get(param.key)
            if tval is None:
                continue
            if param.param_type == "vector":
                for i, t in enumerate(tval):
                    if t > 1.0:
                        print(
                            f"  WARNING: {param.path}[{i}] tolerance={t:.3f}\n"
                            f"           Unusually large. Possible multimodal distribution.\n"
                            f"           Consider using calibration.exclude_episodes."
                        )
            elif tval > 1.0:
                print(
                    f"  WARNING: {param.path} tolerance={tval:.3f}\n"
                    f"           Unusually large. Possible multimodal distribution."
                )


def calibrate(
    data_path: str | Path,
    robot_name: str | None = None,
    arms: list[str] | None = None,
    config_path: str | Path = "configs/default.yaml",
    tolerance1_factor: float = 2.0,
    tolerance2_factor: float = 3.0,
    write_config: bool = True,
    exclude_episodes: list[str] | None = None,
) -> dict[str, Any]:
    data_path = Path(data_path)
    if not data_path.exists():
        raise FileNotFoundError(f"Data path not found: {data_path}")

    cfg = load_config(config_path)
    robot_name = robot_name or cfg.robot_name

    config_dir = Path(config_path).parent
    robot_config_path = config_dir / "robots" / robot_name / "calibrated.yaml"
    if not robot_config_path.exists():
        raise FileNotFoundError(f"Robot config not found: {robot_config_path}")

    with open(robot_config_path) as f:
        robot_raw = yaml.safe_load(f) or {}

    robot_params = RobotParameters.from_config(robot_raw)

    loader = LoaderFactory.get_loader(robot_name)

    exclude_set = set(exclude_episodes or [])

    if robot_name == "quanta_x1":
        all_episode_dirs = find_quanta_x1_episode_dirs(data_path)
    elif robot_name == "kuavo":
        all_episode_dirs = find_kuavo_episode_files(data_path)
    else:
        all_episode_dirs = find_episode_dirs(data_path)
    if not all_episode_dirs:
        raise ValueError(f"No episode directories found under {data_path}")

    excluded_dirs = [d for d in all_episode_dirs if d.name in exclude_set]
    episode_dirs = [d for d in all_episode_dirs if d.name not in exclude_set]

    if not episode_dirs:
        raise ValueError(
            f"All {len(all_episode_dirs)} episodes were excluded. "
            "Nothing to calibrate."
        )

    collected = collect_start_poses(episode_dirs, loader, robot_params)

    result: dict[str, Any] = {
        "home_position": {},
        "tolerance1": {},
        "tolerance2": {},
        "statistics": {},
        "tolerance_details": {},
        "skipped_parameters": [],
        "num_episodes": len(episode_dirs),
        "num_excluded": len(excluded_dirs),
        "num_total": len(all_episode_dirs),
        "excluded": [d.name for d in excluded_dirs],
        "used": [d.name for d in episode_dirs],
    }

    stats: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []

    for param in robot_params.params:
        if not param.calibrate:
            continue
        values = collected[param.path]
        if not values:
            skipped.append(param.path)
            continue
        s = compute_statistics(values, param.param_type)
        stats[param.path] = s

    result["skipped_parameters"] = skipped
    result["statistics"] = stats

    result["home_position"] = build_home_position(stats, robot_params)
    result["tolerance1"] = build_tolerance1(stats, robot_params, tolerance1_factor)
    result["tolerance2"] = build_tolerance2(stats, robot_params, tolerance2_factor)

    if write_config:
        write_calibration_to_robot_config(config_path, robot_name, result)

    return result


def write_calibration_to_robot_config(
    config_path: str | Path,
    robot_name: str,
    calibration_result: dict[str, Any],
) -> None:
    config_path = Path(config_path)
    robot_config_path = config_path.parent / "robots" / robot_name / "calibrated.yaml"

    if robot_config_path.exists():
        with open(robot_config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {"robot": {"name": robot_name}}

    raw["home_position"] = calibration_result["home_position"]
    raw["tolerance1"] = calibration_result["tolerance1"]
    raw["tolerance2"] = calibration_result["tolerance2"]
    raw.pop("calibration", None)
    raw.pop("tolerance_floor", None)

    with open(robot_config_path, "w") as f:
        yaml.dump(raw, f, sort_keys=False, allow_unicode=True)

    print(f"Calibration results written to {robot_config_path}")


def print_summary(result: dict[str, Any]) -> None:
    print(f"\nCalibration Path: {result.get('_data_path', '')}")
    print()

    excluded = result.get("excluded", [])
    used = result.get("used", [])

    if excluded:
        print("Excluded Episodes:")
        for name in excluded:
            print(f"  {name}")
        print()

    print("Used Episodes:")
    for name in used:
        print(f"  {name}")
    print()

    print(f"Total Episodes:  {result['num_total']}")
    print(f"Used Episodes:   {result['num_episodes']}")
    print(f"Excluded Episodes: {result['num_excluded']}")
    print()

    for path, s in result.get("statistics", {}).items():
        print(f"=== {path} ===")
        mean = s.get("mean", [])
        std = s.get("std", [])
        if isinstance(mean, list):
            print(f"    mean:   {mean}")
            print(f"    std:    {std}")
            if "min" in s:
                print(f"    min:    {s['min']}")
                print(f"    max:    {s['max']}")
        else:
            print(f"    mean:   {mean:.6f}")
            print(f"    std:    {std:.6f}")
            if "min" in s:
                print(f"    min:    {s['min']:.6f}")
                print(f"    max:    {s['max']:.6f}")
        print(f"    count:  {s.get('count', 0)}")
        print()

    for group, home_group in result.get("home_position", {}).items():
        print(f"=== {group} home ===")
        for key, val in home_group.items():
            print(f"  {key}: {val}")
        print()

    for label, key in [("tolerance1", "tolerance1"), ("tolerance2", "tolerance2")]:
        for group, tol_group in result.get(key, {}).items():
            print(f"=== {group} {label} ===")
            for k, val in tol_group.items():
                print(f"  {k}: {val}")
            print()

    skipped = result.get("skipped_parameters", [])
    if skipped:
        print("Skipped Parameters (no data available):")
        for p in skipped:
            print(f"  {p}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate home position from episodes")
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="configs/default.yaml",
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--data-path", "-d",
        type=str,
        default=None,
        help="Override calibration data path",
    )
    parser.add_argument(
        "--robot", "-r",
        type=str,
        default=None,
        help="Robot name (overrides config)",
    )
    parser.add_argument(
        "--tolerance1-factor",
        type=float,
        default=None,
        help="Multiplier for std to compute tolerance1 (default: from config)",
    )
    parser.add_argument(
        "--tolerance2-factor",
        type=float,
        default=None,
        help="Multiplier for std to compute tolerance2 (default: from config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results without writing to config",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    data_path = args.data_path or cfg.calibration_data_path
    if not data_path:
        print("No calibration data path configured. Set calibration.data_path in config YAML.")
        return

    result = calibrate(
        data_path,
        robot_name=args.robot or cfg.robot_name,
        config_path=args.config,
        tolerance1_factor=args.tolerance1_factor if args.tolerance1_factor is not None else cfg.tolerance1_factor,
        tolerance2_factor=args.tolerance2_factor if args.tolerance2_factor is not None else cfg.tolerance2_factor,
        write_config=not args.dry_run,
        exclude_episodes=cfg.calibration_exclude_episodes,
    )

    result["_data_path"] = str(data_path)
    print_summary(result)


if __name__ == "__main__":
    main()
