"""SI-only algorithm inputs over an unchanged, read-only source Episode."""

from edc.schemas import UNITS
import copy
from contextlib import contextmanager

from edc.data.transforms.core import UNITS, convert_units

SI_BY_DIMENSION = {dimension: unit for unit, (dimension, factor) in UNITS.items() if factor == 1.0}


class UnitInputError(ValueError):
    """Missing unit declarations or caller-managed conversion prevents execution."""


def validate_unit_policy(policy):
    if policy not in {"auto", "manual"}:
        raise ValueError("unit_policy must be auto or manual")
    return policy


def si_unit(unit):
    """Return the SI unit for an explicitly supported source physical dimension."""
    if not isinstance(unit, str) or unit not in UNITS:
        raise UnitInputError(f"Unknown/unsupported source unit {unit!r}; confirm the source unit first")
    return SI_BY_DIMENSION[UNITS[unit][0]]


def require_si_field(episode, name, field, expected_unit=None):
    """Validate declared semantics and SI dimension without reading payloads."""
    channel = episode.channels[name]
    spec = channel.field_spec(field)
    semantic = {**(channel.representation or {}),
                **{key: spec[key] for key in ('quantity', 'semantic_status') if key in spec}}
    if (semantic.get('semantic_status') != 'confirmed' or
            semantic.get('quantity') in {None, '', 'unknown'}):
        raise UnitInputError(f"{name}.{field}: physical quantity is unconfirmed; explicitly confirm quantity and semantic_status before SI execution")
    if (semantic.get("quantity") == "unknown" or
            semantic.get("semantic_status") in {"unknown", "pending", "pending_owner_confirmation"}):
        raise UnitInputError(f"{name}.{field}: physical quantity is unconfirmed; ask the data owner to declare quantity and unit")
    try:
        target = si_unit(spec["unit"])
    except UnitInputError as exc:
        raise UnitInputError(f"{name}.{field}: {exc}") from exc
    if getattr(episode, "unit_policy", "auto") == "manual" and spec["unit"] != target:
        raise UnitInputError(f"{name}.{field}: automatic conversion disabled; supply SI unit {target}")
    if expected_unit is not None and target != expected_unit:
        raise UnitInputError(f"{name}.{field}: requires SI unit {expected_unit}, declared {spec['unit']!r} maps to {target}; physical dimensions do not match")
    return target


class SIEpisode:
    """SI scalars/vectors and canonical XYZW orientations for every algorithm."""

    def __init__(self, source, unit_policy="auto"):
        from edc.data.ir.canonical import CanonicalEpisode
        self._source = source if isinstance(source, CanonicalEpisode) else CanonicalEpisode(source)
        self.unit_policy = validate_unit_policy(unit_policy)
        self.channels = {}
        self._rules = {}
        self._read_counts = {}

    def __getattr__(self, name):
        if name in {"id", "path", "format", "profile", "selector", "metadata", "raw",
                    "iter_frames", "image_storage", "video_source", "materialize_video", "video_evidence"}:
            return getattr(self._source, name)
        raise AttributeError(name)

    def open(self):
        self._source.open()
        if self.channels:
            return self
        self.channels = copy.deepcopy(self._source.channels)
        for name, channel in self.channels.items():
            if not channel.value_available:
                continue
            for field in channel.fields:
                spec = self._source.channels[name].field_spec(field)
                rule = {"channel": name, "field": field, "source_unit": spec["unit"],
                        "source_mapping": {"episode_id": self.id, "source": str(self.path),
                            "selector": self.selector, "channel": channel.selection.get("channel", name),
                            "field": channel.selection.get("field", field),
                            "selection": channel.selection, "rows": "identity",
                            "elements": channel.selection.get("indices", "identity")}}
                if field == 'orientation':
                    rule.update(target_unit='1', factor=1., status='identity',
                                representation_normalization='orientation-normalization.json')
                    self._rules[(name, field)] = rule
                    continue
                try:
                    target = require_si_field(self._source, name, field)
                    rule.update(target_unit=target, factor=UNITS[spec["unit"]][1],
                                status="identity" if target == spec["unit"] else "conversion_required")
                    if self.unit_policy == "manual" and target != spec["unit"]:
                        raise UnitInputError("Automatic conversion disabled; supply SI data and declare its SI source unit")
                    channel.field_metadata.setdefault(field, {}).update(
                        unit=target, source_unit=spec["unit"])
                except UnitInputError as exc:
                    rule.update(status="blocked", reason=str(exc))
                self._rules[(name, field)] = rule
            units = {channel.field_spec(field)["unit"] for field in channel.fields}
            channel.unit = next(iter(units)) if len(units) == 1 else None
        return self

    def close(self):
        self._source.close()
        self.channels = {}
        self._rules = {}

    @contextmanager
    def opened(self):
        try:
            yield self.open()
        finally:
            self.close()

    def unit_evidence(self):
        self.open()
        fields = copy.deepcopy(list(self._rules.values()))
        for rule in fields:
            rule["si_read_count"] = self._read_counts.get((rule["channel"], rule["field"]), 0)
            rule["conversion_applied"] = rule["status"] == "conversion_required" and rule["si_read_count"] > 0
        return {"schema_version": UNITS, "unit_policy": self.unit_policy,
                "algorithm_unit_system": "SI", "source_unchanged": True,
                "timeline_unit": "s", "timeline_conversion": "already performed by reader; no second scaling",
                "fields": fields, "orientation_normalization": self.orientation_evidence()}

    def reset_unit_evidence(self):
        """Start a per-algorithm read ledger; conversion plans alone are not execution."""
        self._read_counts.clear()
        self._source.reset_orientation_evidence()

    def orientation_evidence(self):
        return self._source.orientation_evidence()

    @property
    def recorded_channels(self):
        return self._source.recorded_channels

    def read_signal(self, channel, field):
        self.open()
        from edc.algorithms.channel_names import resolve_channel
        channel = resolve_channel(self, channel)
        if field == 'orientation':
            return self._source.read_signal(channel, field)
        # Validate the source field before consulting its execution rule.
        self.channels[channel].field_spec(field)
        rule = self._rules.get((channel, field))
        if not rule or rule["status"] == "blocked":
            reason = rule.get("reason") if rule else "Numeric field unavailable"
            raise UnitInputError(f"{channel}.{field}: {reason}")
        values = self._source.read_signal(channel, field)
        if rule["status"] == "conversion_required":
            result = convert_units(values, rule["source_unit"], rule["target_unit"]).output
        else:
            result = values.copy()
        self._read_counts[(channel, field)] = self._read_counts.get((channel, field), 0) + 1
        return result

    def read_values(self, channel, indices=None):
        self.open()
        from edc.algorithms.channel_names import resolve_channel
        channel = resolve_channel(self, channel)
        fields = self.channels[channel].fields
        signals = {field: self.read_signal(channel, field) for field in fields}
        rows = range(self.channels[channel].count) if indices is None else indices
        return [{field: values[index] for field, values in signals.items()} for index in rows]

    def inspect_recorded_signal(self, channel, field):
        """Raw values ONLY for unit-independent finite/equality diagnostics, never physics."""
        return self._source.inspect_recorded_signal(channel, field)

    def describe(self):
        description = self._source.describe()
        self.open()
        return {**description, "channels": [c.describe() for c in self.channels.values()],
                "unit_normalization": self.unit_evidence()}

    def transform_signal(self, channel, field, **kwargs):
        from edc.data.transforms.episode import signal
        return signal(self, channel, field, **kwargs)

    def transform_timestamps(self, channel, target_clock):
        from edc.data.transforms.episode import timestamps
        return timestamps(self, channel, target_clock)

    def transform_orientation(self, channel, *, target, field='orientation', tolerance=1e-6):
        # Representation includes its own units: convert raw data exactly once.
        return self._source.transform_orientation(channel, target=target, field=field, tolerance=tolerance)

    def forward_kinematics(self, chain_name):
        from edc.data.transforms.episode import fk
        return fk(self, chain_name)

    def align_signal(self, channel, field, reference, **kwargs):
        from edc.data.transforms.resampling import align_signal
        return align_signal(self, channel, field, reference, **kwargs)


class SIDataset:
    """Ensure plugins reopening context.dataset also receive the SI view."""

    def __init__(self, source, unit_policy="auto"):
        self._source = source
        self.unit_policy = validate_unit_policy(unit_policy)
        self._episodes = [SIEpisode(ep, unit_policy) for ep in source.iter_episodes()]

    def __getattr__(self, name):
        if name in {"id", "path", "version", "profile", "tasks", "task_catalog", "metadata"}:
            return getattr(self._source, name)
        raise AttributeError(name)

    def iter_episodes(self):
        yield from self._episodes

    def describe(self):
        result = {"id": self.id, "version": self.version, "tasks": self.tasks,
                  "metadata": self.metadata, "episodes": []}
        for ep in self.iter_episodes():
            try:
                with ep.opened():
                    result["episodes"].append(ep.describe())
            except Exception as exc:
                result["episodes"].append({"id": ep.id, "source": str(ep.path), "error": str(exc)})
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for episode in self._episodes:
            episode.close()
