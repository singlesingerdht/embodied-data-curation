"""Fixed load rules with explicit units, separate from signal statistics."""

import math


DEFAULT_LOAD_RULES = {"unit": "A", "close_delta": 0.3, "stagnation_empty_delta": 0.2}
LOAD_RULE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "unit": {"type": "string", "enum": ["A", "N*m"]},
        **{key: {"type": "number", "exclusiveMinimum": 0}
           for key, value in DEFAULT_LOAD_RULES.items() if key != "unit"},
    },
}


def load_rules(config=None):
    """Torque needs both thresholds explicitly; current defaults cannot transfer."""
    if config is not None and not isinstance(config, dict):
        raise ValueError("load_rules must be an object")
    config = {} if config is None else config
    if set(config) - set(DEFAULT_LOAD_RULES):
        raise ValueError("load_rules accepts only unit, close_delta and stagnation_empty_delta")
    rules = {**DEFAULT_LOAD_RULES, **config}
    if rules["unit"] not in {"A", "N*m"}:
        raise ValueError("load_rules.unit must be A or N*m")
    if rules["unit"] == "N*m" and not {"close_delta", "stagnation_empty_delta"} <= config.keys():
        raise ValueError("Torque rules require explicit close_delta and stagnation_empty_delta in N*m")
    for key in ("close_delta", "stagnation_empty_delta"):
        value = rules[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"load_rules.{key} must be a positive finite number in {rules['unit']}")
    return rules


def validate_load_configuration(params):
    rules = load_rules(params.get("load_rules"))
    if "load_rules" in params and any(
        "delta_threshold" in spec
        for roles in params.get("physical_signals", {}).values()
        for role, spec in roles.items() if role in {"hand_load", "arm_load"}
    ):
        raise ValueError("load_rules cannot be combined with legacy delta_threshold scaling")
    return rules
