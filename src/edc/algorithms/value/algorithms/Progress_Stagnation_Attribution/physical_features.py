"""Normalize calibrated IR evidence for the existing attribution rules."""

import numpy as np
from .load_rules import load_rules as fixed_load_rules
from .signals import hand_rule_values, provisional_signal, validate_evidence_policy


class PhysicalFeatures:
    def __init__(self, signals, fps, *, load_rules=None, evidence_policy="confirmed_only"):
        self.signals, self.fps, self.n = signals, fps, len(signals.timestamps)
        self.evidence_policy = validate_evidence_policy(evidence_policy)
        self.assumptions = {arm: [] for arm in ("left", "right")}
        self.load_rules = fixed_load_rules(load_rules)
        self.load_evidence = {}
        self.arms = ["left", "right"]
        self.limitations = {}
        self.proactive_inputs = {}
        for key in (
            "gripper",
            "grip_vel",
            "cur_rms",
            "grip_cur_rms",
            "speed",
            "grip_p5",
            "grip_p90",
            "grip_p95",
            "grip_delta",
            "cur_baseline",
            "cur_std",
            "cur_spike_threshold",
            "max_grip_vel",
        ):
            setattr(self, key, {})
        for arm in self.arms:
            entries = signals.arms[arm]
            self.load_evidence[arm] = {}
            reasons = []
            empty = np.full(self.n, np.nan)
            hand = entries.get("hand_position", {})
            openness = hand.get("openness")
            if hand.get("config", {}).get("calibration_status") != "confirmed":
                openness = None
            if provisional_signal(hand.get("config", {}), "hand_position", evidence_policy):
                openness = hand_rule_values(hand["values"], hand["config"], evidence_policy=evidence_policy)
                if openness is not None:
                    self._record_assumption(arm, "hand_position", hand["config"])
            if openness is None:
                reasons.append("缺少经确认的手部开合映射（固定参考值或原生相对规则）")
            self.gripper[arm] = openness if openness is not None else empty.copy()
            for role, target in (
                ("hand_load", "grip_cur_rms"),
                ("arm_load", "cur_rms"),
            ):
                entry = entries.get(role, {})
                config = entry.get("config", {})
                legacy = "delta_threshold" in config
                threshold = config.get("delta_threshold")
                if legacy and load_rules is not None:
                    raise ValueError("load_rules cannot be combined with legacy delta_threshold scaling")
                if (
                    (config.get("calibration_status") != "confirmed"
                     and not provisional_signal(config, role, evidence_policy))
                    or config.get("unit", "unknown") == "unknown"
                ):
                    reasons.append(f"{role} 缺失或信号单位/含义尚未确认")
                    getattr(self, target)[arm] = empty.copy()
                elif legacy and (
                    isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                    or not np.isfinite(threshold) or threshold <= 0
                ):
                    reasons.append(f"{role} 旧版 delta_threshold 必须为正有限数")
                    getattr(self, target)[arm] = empty.copy()
                elif legacy and config["unit"] != "A":
                    reasons.append(f"{role} 旧版电流缩放不适用于 {config['unit']}")
                    getattr(self, target)[arm] = empty.copy()
                elif not legacy and config["unit"] != self.load_rules["unit"]:
                    reasons.append(f"{role} 负载规则单位 {self.load_rules['unit']} 与信号 {config['unit']} 不一致；力矩需显式 N*m 阈值")
                    getattr(self, target)[arm] = empty.copy()
                elif config["unit"] == "N*m" and config.get("quantity") != "torque":
                    reasons.append(f"{role} N*m 信号必须明确声明为 torque")
                    getattr(self, target)[arm] = empty.copy()
                elif entry["description"]["signal_state"] == "all_zero":
                    reasons.append(f"{role} 全零，无法验证负载证据")
                    getattr(self, target)[arm] = empty.copy()
                else:
                    if config.get("calibration_status") == "pending":
                        self._record_assumption(arm, role, config)
                    # Current inputs retain their physical scale. The old
                    # normalization is supported only when explicitly supplied.
                    scale = 0.3 / threshold if legacy else 1.0
                    getattr(self, target)[arm] = (
                        np.sqrt(np.mean(entry["values"] ** 2, axis=1)) * scale
                    )
                    self.load_evidence[arm][role] = {
                        "quantity": config.get("quantity"),
                        "input_unit": config["unit"],
                        "rule_unit": "legacy_normalized" if legacy else self.load_rules["unit"],
                        "scale": scale, "legacy_delta_threshold": threshold,
                        "proxy_of": config.get("proxy_of"),
                        "calibration_status": config.get("calibration_status"),
                        "calibration_reference": config.get("calibration_reference"),
                    }
            ee, joint = entries.get("ee_position"), entries.get("arm_velocity")
            speed = empty.copy()
            if ee is not None and ee["config"]["unit"] == "m":
                speed[1:] = np.linalg.norm(
                    np.diff(ee["values"], axis=0), axis=1
                ) / np.diff(signals.timestamps)
                speed[0] = speed[1] if self.n > 1 else np.nan
            elif joint is not None:
                cfg = joint["config"]
                threshold = cfg.get("static_threshold")
                if (
                    cfg.get("calibration_status") == "confirmed"
                    and cfg["unit"] != "unknown"
                    and isinstance(threshold, (int, float))
                    and np.isfinite(threshold)
                    and threshold > 0
                ):
                    speed = (
                        np.sqrt(np.mean(joint["values"] ** 2, axis=1))
                        * 0.01
                        / threshold
                    )
                else:
                    reasons.append("关节运动阈值尚未校准，不能套用末端线速度阈值")
            else:
                reasons.append("没有有效末端平移或经校准的关节运动信号")
            self.speed[arm] = speed
            grip = self.gripper[arm]
            velocity = empty.copy()
            velocity[1:] = np.abs(np.diff(grip)) / np.diff(signals.timestamps)
            velocity[0] = 0
            self.grip_vel[arm] = velocity
            finite = grip[np.isfinite(grip)]
            for p in (5, 90, 95):
                getattr(self, f"grip_p{p}")[arm] = (
                    float(np.percentile(finite, p)) if len(finite) else 0.0
                )
            self.grip_delta[arm] = (
                max(
                    float(np.ptp(finite)) * 0.5,
                    self.grip_p95[arm] - self.grip_p5[arm],
                    0.01,
                )
                if len(finite)
                else 0.01
            )
            v = velocity[np.isfinite(velocity)]
            self.max_grip_vel[arm] = float(v.max()) if len(v) else 0.0
            cur = self.cur_rms[arm]
            extremes = np.isfinite(cur) & (
                (grip <= self.grip_p5[arm]) | (grip >= self.grip_p95[arm])
            )
            base = cur[extremes]
            self.cur_baseline[arm] = float(np.median(base)) if len(base) else 0.0
            self.cur_std[arm] = float(np.std(base)) if len(base) else 0.0
            self.cur_spike_threshold[arm] = (
                self.cur_baseline[arm] + 1.5 * self.cur_std[arm]
            )
            self.limitations[arm] = reasons
            # The frozen proactive rules use raw >10 load and <5 travel units.
            # Those units are a separate declared calibration, never inferred
            # from normalized openness or from the general attribution scale.
            mapped = []
            for role in ("hand_position", "hand_load"):
                entry = entries.get(role, {})
                cfg = entry.get("config", {})
                transform = cfg.get("v8_native_scale", {})
                scale, offset = transform.get("scale"), transform.get("offset")
                if (cfg.get("calibration_status") != "confirmed"
                        or (role == "hand_load" and cfg.get("unit") != "A")
                        or not transform.get("reference")
                        or transform.get("verified") is not True
                        or not isinstance(scale, (int, float)) or not np.isfinite(scale) or scale == 0
                        or not isinstance(offset, (int, float)) or not np.isfinite(offset)):
                    break
                raw = entry["values"]
                if role == "hand_position":
                    if raw.shape[1] != 1:
                        break
                    value = raw[:, 0] * scale + offset
                else:
                    value = np.sqrt(np.mean(raw ** 2, axis=1)) * scale + offset
                if not np.isfinite(value).all():
                    break
                mapped.append(value)
            if len(mapped) == 2:
                self.proactive_inputs[arm] = tuple(mapped)
        if any(e['rule_unit'] == 'legacy_normalized'
               for roles in self.load_evidence.values() for e in roles.values()):
            self.load_rules['unit'] = 'legacy_normalized; see load_sources'

    def _record_assumption(self, arm, role, config):
        self.assumptions[arm].append({
            "role": role, "unit": config["unit"], "quantity": config["quantity"],
            "calibration_status": "pending", "calibration_reference": config["calibration_reference"],
            "direction_reference": config.get("direction_reference"),
            "proxy_of": config.get("proxy_of"), "proxy_reference": config.get("reference"),
        })

    def assessment_limitations(self, arm, start, end):
        return self.window_limitations(arm, start, end) + [
            f"{item['role']} 使用 {item['unit']}，标定待确认：{item['calibration_reference']}"
            for item in self.assumptions[arm]]

    def window_limitations(self, arm, start, end):
        reasons = list(self.limitations[arm])
        start, end = max(0, start), min(self.n, end + 1)
        if start >= end:
            reasons.append("事件超出物理信号时间范围")
        elif any(
            not np.isfinite(getattr(self, key)[arm][start:end]).all()
            for key in ("gripper", "speed", "cur_rms", "grip_cur_rms")
        ):
            reasons.append("事件附近存在缺失值或时间缺口")
        return reasons
