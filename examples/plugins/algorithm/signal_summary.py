"""Minimal independently installed algorithm with evidence."""
from edc.algorithms.registry import AlgorithmSpec


class SignalSummary:
    spec = AlgorithmSpec("team_example__signal_summary", "0.1.0", "数值通道摘要", "Example team",
        "Count available numeric signals; does not assign a data quality score.", required_inputs=("numeric",))

    def run(self, context, params):
        channels = [c.describe() for c in context.episode.channels.values() if c.value_available]
        evidence = context.write_result("channels.json", channels)
        return {"channel_count": len(channels), "evidence": evidence, "verdict": "OBSERVATION"}


plugin = SignalSummary()
plugin.spec = __import__("dataclasses").replace(plugin.spec, example=True)


class FiniteCheck:
    """Example CPU quality algorithm; checks finite values, not physical limits."""
    spec = AlgorithmSpec(
        "team_example__finite_check", "0.1.0", "数值有效性检查", "Example team",
        "Check NaN/Inf in explicitly selected numeric channel fields; preserve row indices.",
        required_inputs=("numeric",), dependencies=("numpy",))

    def run(self, context, params):
        import numpy as np
        if set(params) - {"channels"}:
            raise ValueError("Supported parameter: channels")
        episode = context.episode
        names = params.get("channels")
        if names is None:
            names = [c.name for c in episode.channels.values() if c.value_available]
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            raise ValueError("channels must be a nonempty list of channel names")
        if len(names) != len(set(names)):
            raise ValueError("Duplicate channels")
        evidence = []
        for name in names:
            channel = episode.channels[name]
            if not channel.value_available or not channel.fields:
                raise ValueError(f"Numeric fields unavailable: {name}")
            for field in channel.fields:
                values = episode.inspect_recorded_signal(name, field)
                if values.size == 0:
                    raise ValueError(f"Empty signal: {name}.{field}")
                invalid = ~np.isfinite(values)
                evidence.append({"channel": name, "field": field, "shape": list(values.shape),
                    "invalid_count": int(invalid.sum()), "invalid_indices": np.argwhere(invalid).tolist(),
                    "semantics": channel.field_spec(field)})
        artifact = context.write_result("finite_values.json", evidence)
        count = sum(row["invalid_count"] for row in evidence)
        return {"verdict": "NEEDS_REVIEW" if count else "ACCEPTABLE", "score": None,
                "invalid_count": count, "evidence": artifact,
                "scope": "Finite values only; no physical range or hand-state judgment"}


finite_plugin = FiniteCheck()
finite_plugin.spec = __import__("dataclasses").replace(finite_plugin.spec, example=True)
