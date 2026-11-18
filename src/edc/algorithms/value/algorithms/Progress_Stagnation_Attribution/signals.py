"""Profile-selected numeric IR signals. No vendor reader lives in this module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import numpy as np


def validate_evidence_policy(policy):
    if policy not in {"confirmed_only", "allow_provisional"}:
        raise ValueError("evidence_policy must be confirmed_only or allow_provisional")
    return policy


def provisional_signal(config, role, policy):
    """Permit documented pending interpretations without upgrading metadata."""
    validate_evidence_policy(policy)
    units, quantity = (({"m", "cm", "mm", "rad", "deg", "1"}, "position") if role == "hand_position"
                       else ({"A", "N*m"}, "torque" if config.get("unit") == "N*m" else "current"))
    reference = config.get("calibration_reference")
    return (policy == "allow_provisional"
            and config.get("calibration_status") == "pending"
            and config.get("source_kind") == "feedback"
            and config.get("unit") in units
            and config.get("quantity") == quantity
            and isinstance(reference, str) and bool(reference.strip())
            and "delta_threshold" not in config)



@dataclass
class Signal:
    timestamps: np.ndarray
    values: np.ndarray
    source: dict

    def describe(self) -> dict:
        finite = np.isfinite(self.values)
        complete = finite.all(axis=1)
        a = self.values[complete]
        state = (
            "missing"
            if not len(a)
            else (
                "all_zero"
                if not np.any(a)
                else "constant" if np.all(np.ptp(a, axis=0) == 0) else "varying"
            )
        )
        return {
            **self.source,
            "samples": len(self.values),
            "dimensions": self.values.shape[1],
            "valid_samples": int(complete.sum()),
            "signal_state": state,
        }




def align_signal(
    signal: Signal, target: np.ndarray, max_gap_sec: float, method: str = "linear"
) -> np.ndarray:
    """Align in source-relative seconds, no extrapolation or gap bridging."""
    if max_gap_sec <= 0 or not np.isfinite(max_gap_sec):
        raise ValueError("max_gap_sec 必须是正有限数")
    if method not in {"linear", "previous"}:
        raise ValueError("数值对齐只支持 linear/previous")
    t, a = signal.timestamps, signal.values
    target = np.asarray(target, float)
    out = np.full((len(target), a.shape[1]), np.nan)
    for i, x in enumerate(target):
        hi = int(np.searchsorted(t, x))
        if hi < len(t) and np.isclose(t[hi], x, rtol=0, atol=1e-9):
            out[i] = a[hi]
        elif 0 < hi < len(t) and t[hi] - t[hi - 1] <= max_gap_sec:
            out[i] = (
                a[hi - 1]
                if method == "previous"
                else (
                    a[hi - 1]
                    + (a[hi] - a[hi - 1]) * (x - t[hi - 1]) / (t[hi] - t[hi - 1])
                )
            )
    return out


def hand_openness(values: np.ndarray, config: dict, *, evidence_policy="confirmed_only") -> np.ndarray | None:
    """0=closed, 1=open; never infer opening direction from array order."""
    if config.get("calibration_status") != "confirmed" and not provisional_signal(config, "hand_position", evidence_policy):
        return None
    opened, closed = np.asarray(config["open_reference"], float), np.asarray(
        config["closed_reference"], float
    )
    if opened.shape != (values.shape[1],) or closed.shape != opened.shape:
        raise ValueError("手部开合参考值必须覆盖每个已选择关节")
    if (
        not np.isfinite(opened).all()
        or not np.isfinite(closed).all()
        or np.any(opened == closed)
    ):
        raise ValueError("手部开合参考值无效或没有行程")
    return np.mean(np.clip((values - closed) / (opened - closed), 0, 1), axis=1)


@dataclass
class PhysicalSignals:
    episode_id: str
    timestamps: np.ndarray
    arms: dict
    warnings: list[str]

    def describe(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "frames": len(self.timestamps),
            "arms": {
                arm: {key: value["description"] for key, value in signals.items()}
                for arm, signals in self.arms.items()
            },
            "warnings": self.warnings,
        }




def hand_rule_values(values: np.ndarray, config: dict, *, evidence_policy="confirmed_only") -> np.ndarray | None:
    """Select calibrated openness or the frozen scalar relative-gripper rule.

    native_relative keeps the declared input scale (including the .01 floor).
    It does not estimate full-open/closed positions from observed extrema.
    """
    validate_evidence_policy(evidence_policy)
    mode = config.get("grip_mode", "references")
    if mode == "references":
        return hand_openness(values, config, evidence_policy=evidence_policy)
    if mode != "native_relative":
        raise ValueError("grip_mode must be references or native_relative")
    if config.get("calibration_status") != "confirmed" and not provisional_signal(config, "hand_position", evidence_policy):
        return None
    if values.shape[1] != 1:
        raise ValueError("native_relative requires one scalar gripper signal")
    if config.get("opening_direction") != "increasing" or not config.get("direction_reference"):
        raise ValueError("native_relative requires confirmed increasing-open direction and direction_reference")
    if "open_reference" in config or "closed_reference" in config:
        raise ValueError("native_relative does not use open/closed reference normalization")
    return values[:, 0].copy()
