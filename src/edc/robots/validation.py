"""Robot onboarding validation checks values, not just schema annotations."""
import fnmatch
import numpy as np


def check_robot(dataset):
    issues = []
    for episode in dataset.iter_episodes():
        try:
            with episode.opened():
                for name, camera in dataset.profile.get("transforms", {}).get("cameras", {}).items():
                    channel = episode.channels.get(camera["channel"])
                    if channel is None or not channel.image_available:
                        issues.append(f"{episode.id}: camera {name} has no decodable channel {camera['channel']}")
                for pattern in dataset.profile.get("channels", {}):
                    if not any(fnmatch.fnmatchcase(name, pattern) for name in episode.channels):
                        issues.append(f"{episode.id}: unmapped channel {pattern}")
                for channel in episode.channels.values():
                    if channel.modality == 'joint_space' and not channel.robot_part:
                        issues.append(f"{episode.id}/{channel.name}: robot_part/DOF unconfirmed")
                    if channel.value_available:
                        for field in channel.fields:
                            spec = channel.field_spec(field)
                            label = f"{episode.id}/{channel.name}.{field}"
                            if not spec["unit"] or spec["unit"] == "unknown" or not spec["dimensions"]:
                                issues.append(f"{label}: unit/dimensions unconfirmed")
                            signal = episode.read_signal(channel.name, field)
                            if spec["dimensions"] and list(signal.shape[1:]) != spec["dimensions"]:
                                issues.append(f"{label}: dimension mismatch")
                            if spec["joint_names"] and (signal.ndim != 2 or len(spec["joint_names"]) != signal.shape[1]):
                                issues.append(f"{label}: joint_names dimension mismatch")
                            if not np.isfinite(signal).all():
                                issues.append(f"{label}: nonfinite values")
                            if channel.body_part == "hand":
                                if channel.side == "unknown" or spec["source_kind"] == "unknown":
                                    issues.append(f"{label}: hand side/source_kind unconfirmed")
                    ts = channel.timestamps
                    if channel.time_source == "unknown":
                        issues.append(f"{episode.id}/{channel.name}: unknown time source")
                    if ts is not None and (len(ts) != channel.count or not np.isfinite(ts).all() or np.any(np.diff(ts) <= 0)):
                        issues.append(f"{episode.id}/{channel.name}: invalid timeline")
        except Exception as exc:
            issues.append(f"{episode.id}: {exc}")
    return issues
