from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

ParameterType = Literal["scalar", "vector", "quaternion"]

_PARAMETER_TYPES: set[str] = {"scalar", "vector", "quaternion", "angle", "enum"}


@dataclass
class ParameterDef:
    group: str
    key: str
    param_type: ParameterType
    detect: bool = False
    calibrate: bool = True

    @property
    def path(self) -> str:
        return f"{self.group}.{self.key}"


@dataclass
class RobotParameters:
    robot_name: str
    params: list[ParameterDef] = field(default_factory=list)
    _groups: dict[str, list[ParameterDef]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, robot_config: dict) -> RobotParameters:
        robot_section = robot_config.get("robot", {})
        robot_name = robot_section.get("name", "unknown")
        params_raw = robot_section.get("parameters", robot_config.get("parameters", {}))
        params: list[ParameterDef] = []
        groups: dict[str, list[ParameterDef]] = {}
        for group_name, group_params in params_raw.items():
            group_list: list[ParameterDef] = []
            for key, meta in group_params.items():
                ptype = meta.get("type", "scalar")
                if ptype not in _PARAMETER_TYPES:
                    raise ValueError(
                        f"Unknown parameter type '{ptype}' for {group_name}.{key}. "
                        f"Supported: {sorted(_PARAMETER_TYPES)}"
                    )
                detect = meta.get("detect", False)
                calibrate = meta.get("calibrate", True)
                pd = ParameterDef(
                    group=group_name, key=key, param_type=ptype,
                    detect=detect, calibrate=calibrate,
                )
                params.append(pd)
                group_list.append(pd)
            groups[group_name] = group_list
        return cls(robot_name=robot_name, params=params, _groups=groups)

    def get_group(self, group: str) -> list[ParameterDef]:
        return self._groups.get(group, [])

    def get_groups(self) -> dict[str, list[ParameterDef]]:
        return dict(self._groups)

    def get_param(self, path: str) -> ParameterDef | None:
        for p in self.params:
            if p.path == path:
                return p
        return None

    def get_detect_enabled(self) -> list[str]:
        return [p.path for p in self.params if p.detect]

    def get_calibrate_enabled(self) -> list[str]:
        return [p.path for p in self.params if p.calibrate]


# ── Error computation ─────────────────────────────────────────


def compute_scalar_error(current: float, home: float) -> float:
    return abs(current - home)


def compute_vector_error(
    current: np.ndarray | list[float],
    home: np.ndarray | list[float],
) -> dict[str, Any]:
    cur = np.asarray(current, dtype=np.float32)
    h = np.asarray(home, dtype=np.float32)
    per_dim = np.abs(cur - h)
    return {
        "magnitude": float(np.linalg.norm(per_dim)),
        "dimensions": per_dim.tolist(),
    }


def compute_quaternion_error(
    current: np.ndarray | list[float],
    home: np.ndarray | list[float],
) -> float:
    q1 = np.asarray(current, dtype=np.float64)
    q2 = np.asarray(home, dtype=np.float64)
    n1 = np.linalg.norm(q1)
    n2 = np.linalg.norm(q2)
    if n1 > 0:
        q1 = q1 / n1
    if n2 > 0:
        q2 = q2 / n2
    dot = np.clip(np.abs(np.dot(q1, q2)), 0.0, 1.0)
    return float(2.0 * np.arccos(dot))


def compute_error(current, home, param_type: ParameterType):
    if param_type == "scalar":
        return compute_scalar_error(float(current), float(home))
    if param_type == "vector":
        return compute_vector_error(current, home)
    if param_type == "quaternion":
        return compute_quaternion_error(current, home)
    raise ValueError(f"Unknown parameter type: {param_type}")


# ── Parameter Score computation ─────────────────────────────────


def _scalar_score(error: float, tolerance1: float, tolerance2: float) -> float:
    if error <= tolerance1:
        return 100.0
    if error >= tolerance2:
        return 0.0
    return 100.0 * (tolerance2 - error) / (tolerance2 - tolerance1)


def _vector_score(
    error: dict[str, Any],
    tolerance1: list[float] | float,
    tolerance2: list[float] | float,
) -> float:
    dims = error["dimensions"]
    n = len(dims)
    if isinstance(tolerance1, (int, float)):
        t1 = [float(tolerance1)] * n
    else:
        t1 = list(tolerance1)
    if isinstance(tolerance2, (int, float)):
        t2 = [float(tolerance2)] * n
    else:
        t2 = list(tolerance2)

    per_dim: list[float] = []
    for i in range(n):
        if dims[i] <= t1[i]:
            per_dim.append(100.0)
        elif dims[i] >= t2[i]:
            per_dim.append(0.0)
        else:
            per_dim.append(100.0 * (t2[i] - dims[i]) / (t2[i] - t1[i]))

    if any(s == 0.0 for s in per_dim):
        return 0.0
    return float(np.mean(per_dim))


def compute_parameter_score(
    error,
    tolerance1,
    tolerance2,
    param_type: ParameterType,
) -> float:
    if param_type == "scalar":
        return _scalar_score(float(error), float(tolerance1), float(tolerance2))
    if param_type == "vector":
        return _vector_score(error, tolerance1, tolerance2)
    if param_type == "quaternion":
        return _scalar_score(float(error), float(tolerance1), float(tolerance2))
    raise ValueError(f"Unknown parameter type: {param_type}")


# ── Statistics ────────────────────────────────────────────────


def compute_scalar_statistics(values: list[np.ndarray]) -> dict[str, Any]:
    arr = np.array([float(v) for v in values])
    n = len(arr)
    ddof = 1 if n > 1 else 0
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr, ddof=ddof)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "count": n,
    }


def compute_vector_statistics(values: list[np.ndarray]) -> dict[str, Any]:
    arr = np.array([np.asarray(v, dtype=np.float32) for v in values])
    n = len(arr)
    ddof = 1 if n > 1 else 0
    return {
        "mean": np.mean(arr, axis=0).tolist(),
        "std": np.std(arr, axis=0, ddof=ddof).tolist(),
        "min": np.min(arr, axis=0).tolist(),
        "max": np.max(arr, axis=0).tolist(),
        "count": n,
    }


def compute_quaternion_statistics(values: list[np.ndarray]) -> dict[str, Any]:
    arr = np.array([np.asarray(v, dtype=np.float64) for v in values])
    n = len(arr)
    ddof = 1 if n > 1 else 0

    mean_q = np.mean(arr, axis=0)
    mn = np.linalg.norm(mean_q)
    if mn > 0:
        mean_q = mean_q / mn

    errors = [compute_quaternion_error(v, mean_q) for v in arr]
    ang_arr = np.array(errors)

    return {
        "mean": mean_q.tolist(),
        "std": float(np.std(ang_arr, ddof=ddof)),
        "min": float(np.min(ang_arr)),
        "max": float(np.max(ang_arr)),
        "count": n,
    }


def compute_statistics(values: list[np.ndarray], param_type: ParameterType) -> dict[str, Any]:
    if param_type == "scalar":
        return compute_scalar_statistics(values)
    if param_type == "vector":
        return compute_vector_statistics(values)
    if param_type == "quaternion":
        return compute_quaternion_statistics(values)
    raise ValueError(f"Unknown parameter type: {param_type}")


def compute_tolerance(
    stats: dict[str, Any],
    param_type: ParameterType,
    sigma_factor: float = 3.0,
) -> Any:
    if param_type == "scalar":
        return float(sigma_factor * stats["std"])
    if param_type == "vector":
        raw = sigma_factor * np.asarray(stats["std"], dtype=np.float32)
        return raw.tolist()
    if param_type == "quaternion":
        return float(sigma_factor * stats["std"])
    raise ValueError(f"Unknown parameter type: {param_type}")
