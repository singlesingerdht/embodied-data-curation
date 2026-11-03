"""Robot profiles are explicit data mappings, never calibration guesses."""
import os
import copy
import re
from pathlib import Path
import yaml
from edc.data.channel_registry import read_unique_yaml


def profile_dir():
    return Path(os.environ.get("EDC_CONFIG_HOME", Path.home() / ".config" / "edc")) / "robots"


def load_profile(value=None, *, base=None):
    if value is None:
        return {}
    profile = copy.deepcopy(value) if isinstance(value, dict) else read_unique_yaml(Path(value).read_text(encoding='utf-8'))
    from edc.robots.card import is_card, compile_card
    if is_card(profile):  # The robot yaml a user writes, compiled in memory.
        profile, _, _ = compile_card(profile, Path(value).resolve().parent if not isinstance(value, dict) else base)
    if not isinstance(profile, dict):
        raise ValueError("Robot profile must be a mapping")
    if profile.get("schema_version") != 1 or not profile.get("version"):
        raise ValueError("Robot profile requires schema_version: 1 and version")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", str(profile.get("id", ""))):
        raise ValueError("Invalid robot id")
    if not isinstance(profile.get("channels", {}), dict):
        raise ValueError("channels must be a mapping")
    if {'path', 'tasks', 'episodes', 'dataset', 'allowed_tasks'} & set(profile):
        raise ValueError('Robot profiles describe hardware and signals, not a dataset location or task list')
    requirements = profile.get("camera_requirements", {})
    if not isinstance(requirements, dict):
        raise ValueError("camera_requirements must map sensor_id to required modalities")
    for sensor, modalities in requirements.items():
        if (not isinstance(sensor, str) or not sensor or not isinstance(modalities, list)
                or not modalities or any(m not in {"rgb", "depth", "infrared"} for m in modalities)
                or len(modalities) != len(set(modalities))):
            raise ValueError(f"Invalid camera_requirements: {sensor}")
    from edc.robots.numeric_review import numeric_review
    numeric_review(profile)  # Validate editable thresholds and confirmation structure.
    for name, channel in profile.get("channels", {}).items():
        if not isinstance(channel, dict):
            raise ValueError(f"Invalid channel {name}")
        if channel.get("side", "unknown") not in {"left", "right", "both", "unknown"}:
            raise ValueError(f"Invalid side: {name}")
        from edc.data.ir.modalities import normalize_modality
        normalize_modality(channel.get("modality", "other"))
        if not isinstance(channel.get("representation", {}), dict):
            raise ValueError(f"representation must be a mapping: {name}")
        if channel.get("sensor_id") is not None and not isinstance(channel["sensor_id"], str):
            raise ValueError(f"sensor_id must be a string: {name}")
        fields = channel.get("fields", [])
        if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields) or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid fields: {name}")
        metadata = channel.get("field_metadata", {})
        if not isinstance(metadata, dict) or any(f not in fields for f in metadata):
            raise ValueError(f"field_metadata must refer to declared fields: {name}")
        for item in [channel, *metadata.values()]:
            if not isinstance(item, dict):
                raise ValueError(f"Invalid field metadata: {name}")
            dims = item.get("dimensions")
            if dims is not None and (not isinstance(dims, list) or any(type(d) is not int or d <= 0 for d in dims)):
                raise ValueError(f"Invalid dimensions: {name}")
            names = item.get("joint_names")
            if names is not None and (not isinstance(names, list) or any(not isinstance(n, str) for n in names) or len(names) != len(set(names))):
                raise ValueError(f"Invalid joint_names: {name}")
            if item.get("source_kind", "unknown") not in {"command", "feedback", "derived", "unknown"}:
                raise ValueError(f"Invalid source_kind: {name}")
            if item.get("calibration_status", "unknown") not in {"confirmed", "pending", "unknown"}:
                raise ValueError(f"Invalid calibration_status: {name}")
            if 'representation' in item and not isinstance(item['representation'], dict):
                raise ValueError(f'representation must be a mapping: {name}')
        if 'orientation' in fields:
            item = metadata.get('orientation', {})
            representation = item.get('representation') or channel.get('representation', {})
            if representation.get('kind') in {'quaternion', 'rpy', 'rotation_matrix'}:
                from edc.data.transforms.orientation import orientation_spec, sample_shape
                declared = dict(representation)
                unit = item.get('unit', channel.get('unit'))
                if 'unit' not in declared and unit is not None:
                    declared['unit'] = unit
                normalized = orientation_spec(declared)
                if unit not in (None, 'unknown', normalized['unit']):
                    raise ValueError(f'{name}: orientation representation unit conflicts with field unit')
                if item.get('dimensions') is not None and tuple(item['dimensions']) != sample_shape(declared):
                    raise ValueError(f'{name}: orientation dimensions conflict with representation')
        origin = channel.get("time_source", "unknown")
        if origin not in {"unknown", "acquisition", "container", "playback", "synthetic"}:
            raise ValueError(f"Invalid time_source: {origin}")
    if 'channel_registry' in profile:
        from edc.data.channel_registry import load_channel_registry
        registry_base = (Path(value).resolve().parent if not isinstance(value, dict)
                         else Path(base) if base is not None else Path.cwd())
        profile['channel_registry'] = load_channel_registry(profile['channel_registry'], base=registry_base)
    from edc.algorithms.channel_names import validate_logical_names
    validate_logical_names(profile)
    from edc.robots.mapping import validate_mapping_config
    validate_mapping_config(profile)
    for chain in profile.get('transforms', {}).get('chains', {}).values():
        if 'urdf' in chain and not isinstance(value, dict):
            chain['urdf'] = str((Path(value).resolve().parent / chain['urdf']).resolve())
    from edc.data.transforms import RobotTransforms
    transforms = RobotTransforms(profile)  # Freeze imported geometry and its source hash.
    if "transforms" in profile:
        profile["transforms"] = transforms.config
    return profile


def add_profile(path):
    profile = load_profile(path)
    target = profile_dir() / (profile["id"] + ".yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never silently overwrite a previously registered configuration.
    with target.open("x") as stream:
        yaml.safe_dump(profile, stream, allow_unicode=True, sort_keys=False)
    return target


# Public compatibility exports through edc.robots.profiles; implementation remains internal.
from edc.robots.numeric_review import confirm_numeric_profile, numeric_review
