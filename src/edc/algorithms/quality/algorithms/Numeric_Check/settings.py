"""Explicit, editable defaults; values are suggestions until the Numeric configuration is confirmed."""
import copy
import math

DEFAULT_THRESHOLDS = {
    "joint": {"vel_threshold": 1.0, "velocity_peak": 20.0, "reset_ratio": 0.6, "limits": None},
    "imu": {"gyro_max": 10.0, "acc_max": 196.2, "gyro_bias_max": 0.05,
            "acc_bias_max": 0.5, "min_static_frames": 50},
    "cmd": {"jerk_threshold": 10.0},
    "pose_freeze": {"min_run": 3, "lookback": 10, "vel_threshold": 0.01, "freeze_eps": 1e-6},
    # Adapter-only supplements: never forwarded to or scored by legacy detectors.
    "command_limits": {"limits": None},
    **{group: {"min_run": 3, "lookback": 10, "freeze_eps": 1e-6,
               "motion_threshold": 0.01}
       for group in ("joint_freeze", "cmd_freeze", "gyro_freeze", "accel_freeze")},
}


def thresholds(overrides=None):
    result = copy.deepcopy(DEFAULT_THRESHOLDS)
    if overrides is None:
        return result
    if not isinstance(overrides, dict) or set(overrides) - set(result):
        raise ValueError("Unknown Numeric_Check threshold group")
    for group, values in overrides.items():
        if not isinstance(values, dict) or set(values) - set(result[group]):
            raise ValueError(f"Unknown Numeric_Check thresholds: {group}")
        result[group].update(values)
    for group, values in result.items():
        for name, value in values.items():
            if name == 'limits':
                if value is None:
                    continue
                if not isinstance(value, list) or not value or any(
                    not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in pair)
                    or pair[0] >= pair[1] for pair in value):
                    raise ValueError(f'{group}.limits requires finite ordered [lower, upper] pairs')
            elif name in {'min_run', 'lookback', 'min_static_frames'}:
                minimum = 2 if name == 'min_run' else 1
                if type(value) is not int or value < minimum:
                    raise ValueError(f'{group}.{name} must be an integer >= {minimum}')
            elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{group}.{name} must be finite and positive')
            elif name == 'reset_ratio' and value > 1:
                raise ValueError('joint.reset_ratio must be <= 1')
        if 'motion_threshold' in values and values['motion_threshold'] <= values['freeze_eps']:
            raise ValueError(f'{group}.motion_threshold must exceed freeze_eps')
    return result
