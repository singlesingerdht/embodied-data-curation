from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .parameters import RobotParameters


@dataclass
class Config:
    data_raw_path: str = ""
    calibration_data_path: str = ""
    calibration_exclude_episodes: list[str] = field(default_factory=list)

    robot_name: str = "yuanli"
    robot_params: RobotParameters | None = None

    enabled_parameters: list[str] | None = None

    home_position: dict | None = None
    tolerance1: dict | None = None
    tolerance2: dict | None = None
    tolerance1_factor: float = 3.0
    tolerance2_factor: float = 6.0

    output_dir: str = "detection_summary"

    @classmethod
    def load(cls, path: str | Path = "configs/default.yaml") -> Config:
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        base_dir = path.parent

        robot_name = raw.get("robot", "yuanli")

        robot_config: dict = {}
        robot_path = base_dir / "robots" / robot_name / "calibrated.yaml"
        if robot_path.exists():
            with open(robot_path) as f:
                robot_config = yaml.safe_load(f) or {}

        robot_inner = robot_config.get("robot", {})
        robot_name = robot_inner.get("name", robot_name)

        robot_params = RobotParameters.from_config(robot_config)

        home_position = robot_config.get("home_position")
        tolerance1 = robot_config.get("tolerance1")
        tolerance2 = robot_config.get("tolerance2")

        enabled_params = robot_params.get_detect_enabled() if robot_params else None

        robot_cal = robot_config.get("calibration", {})
        global_cal = raw.get("calibration", {})
        tolerance1_factor = robot_cal.get("tolerance1_factor",
                            global_cal.get("tolerance1_factor", 3.0))
        tolerance2_factor = robot_cal.get("tolerance2_factor",
                            global_cal.get("tolerance2_factor", 6.0))

        return cls(
            robot_name=robot_name,
            robot_params=robot_params,
            data_raw_path=raw.get("data", {}).get("raw_path", ""),
            calibration_data_path=raw.get("calibration", {}).get("data_path", ""),
            calibration_exclude_episodes=raw.get("calibration", {}).get("exclude_episodes", []),
            enabled_parameters=enabled_params,
            home_position=home_position,
            tolerance1=tolerance1,
            tolerance2=tolerance2,
            tolerance1_factor=tolerance1_factor,
            tolerance2_factor=tolerance2_factor,
            output_dir=raw.get("output", {}).get("dir", "detection_summary"),
        )


def load_config(path: str | Path = "configs/default.yaml") -> Config:
    return Config.load(path)
