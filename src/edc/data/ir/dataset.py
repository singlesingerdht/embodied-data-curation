"""Dataset -> Episode -> Channel contract, with explicit episode boundaries."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import fnmatch
import copy
import numpy as np
import yaml
from edc.robots.profiles import load_profile
from edc.data.compat import legacy


@dataclass
class Channel:
    name: str
    modality: str
    fields: list
    count: int
    source: str
    unit: str | None = None
    dimensions: list | None = None
    time_source: str = "unknown"
    clock: str | None = None
    value_available: bool = False
    image_available: bool = False
    timestamps: list | None = None
    body_part: str | None = None
    side: str = "unknown"
    source_kind: str = "unknown"
    coordinate_frame: str | None = None
    calibration_status: str = "unknown"
    joint_names: list | None = None
    field_metadata: dict = field(default_factory=dict)
    sensor_id: str | None = None
    representation: dict = field(default_factory=dict)
    robot_part: str | None = None
    selection: dict = field(default_factory=dict)
    source_mapping: dict = field(default_factory=dict)
    time_evidence: dict = field(default_factory=dict)
    logical_name: str | None = None
    frame_evidence: dict = field(default_factory=dict)

    def __post_init__(self):
        from edc.data.ir.modalities import normalize_modality
        self.modality = normalize_modality(self.modality)

    def field_spec(self, name):
        """Per-field physical metadata overrides channel defaults; never converts values."""
        if name not in self.fields:
            raise ValueError(f"Undeclared field: {self.name}.{name}")
        defaults = {key: getattr(self, key) for key in ("unit", "dimensions", "source_kind",
            "coordinate_frame", "calibration_status", "joint_names")}
        return {**defaults, **self.field_metadata.get(name, {})}

    def describe(self):
        return {k: v for k, v in vars(self).items() if k != "timestamps"}


@dataclass
class Episode:
    id: str
    path: Path
    format: str
    profile: dict
    selector: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    backend: str = "compat"
    _reader: object = field(default=None, init=False, repr=False)
    _info: object = field(default=None, init=False, repr=False)
    _video_cache: dict = field(default_factory=dict, init=False, repr=False)
    _video_origins: dict = field(default_factory=dict, init=False, repr=False)
    channels: dict = field(default_factory=dict, init=False)

    def open(self):
        if "discovery_error" in self.selector:
            error = ImportError if self.selector.get("missing_dependency") else ValueError
            raise error(self.selector["discovery_error"])
        if self._reader is not None:
            return self
        base = legacy()
        reader = None
        try:
            if self.format == "lerobot" and self.backend == "official":
                from edc.data.readers.lerobot_official import open_official
                reader, info = open_official(self)
            elif self.format in {"hdf5", "parquet", "lerobot", "episode"}:
                from edc.data.readers.structured import open_structured
                reader, info = open_structured(self)
            else:
                reader = base.DataReader.create(str(self.path))
                info = reader.open(str(self.path))
            from edc.robots.mapping import add_selected_topics
            add_selected_topics(info, self.profile)
            if self.profile.get('declared_only'):  # 机器人 yaml：没写进去的数据（含时间）不读、不检查
                info.topics = {n: t for n, t in info.topics.items() if n in self.profile['channels']}
            channels = {}
            for name, topic in info.topics.items():
                mapping = next((v for pattern, v in self.profile.get("channels", {}).items()
                                if fnmatch.fnmatchcase(name, pattern)), {})
                if (topic.msg_type in {'nav_msgs/msg/Odometry', 'nav_msgs/Odometry'}
                        and mapping.get('modality', 'odometry') != 'odometry'):
                    raise ValueError(f'{name}: Odometry source requires modality odometry; use an explicit transform for other signals')
                if "joint_cur" in name.lower() and "fields" not in mapping:
                    topic.fields = ["current"]
                if mapping.get("modality"):
                    topic.modality = mapping["modality"]
                if "fields" in mapping:
                    topic.fields = mapping["fields"]
                if "pose_layout" in mapping:
                    topic.pose_layout = mapping["pose_layout"]
                origin = "synthetic" if topic.timestamps_synthetic else getattr(topic, "time_source", "unknown")
                requested_origin = mapping.get("time_source", "unknown")
                if origin == "container" and requested_origin == "acquisition":
                    if topic.acquisition_timestamps is None:
                        # A broken clock must not block unrelated channels or be
                        # silently replaced by the recorder clock. Raw evidence stays.
                        topic.timestamps = None
                        origin = "unknown"
                    else:
                        topic.timestamps = topic.acquisition_timestamps
                        origin = "acquisition"
                elif origin == "unknown":
                    origin = requested_origin
                unknown_recorded_time = (topic.timestamps is None and
                    getattr(topic, 'recorded_timestamp_values', None) is not None and
                    getattr(topic, 'recorded_timestamp_unit', 'unknown') == 'unknown')
                if unknown_recorded_time:
                    origin = 'unknown'
                topic.time_source = origin
                # A video whose frames were mapped 1:1 onto the trajectory rows (`video_frame_time: rows`)
                # carries the rows' recorded time, not its own playback time.
                if (self.backend != 'official' and topic.msg_type == "video/mp4" and not topic.timestamps_synthetic
                        and not getattr(topic, 'frame_time_from_rows', False)):
                    origin = "playback"
                from edc.data.ir.modalities import normalize_modality, legacy_modality
                declared_modality = mapping.get("modality", topic.modality)
                canonical_modality = normalize_modality(declared_modality)
                source_kind = mapping.get("source_kind", "command" if declared_modality == "cmd" else "unknown")
                topic.modality = legacy_modality(canonical_modality, source_kind)
                channels[name] = Channel(name, canonical_modality, topic.fields or [], topic.count,
                    topic.source, mapping.get("unit"), mapping.get("dimensions"), origin,
                    mapping.get("clock", getattr(topic, "clock", None)), topic.value_available, topic.image_available, topic.timestamps)
                channels[name].source_kind = source_kind
                channels[name].frame_evidence = copy.deepcopy(getattr(topic, 'frame_evidence', None) or {})
                channels[name].time_evidence = copy.deepcopy(getattr(topic, 'time_evidence', None) or {})
                channels[name].time_evidence.update(requested_time_source=requested_origin,
                    selected_time_source=origin, clock=channels[name].clock,
                    establishes_acquisition_sync=False)
                if unknown_recorded_time or (requested_origin == 'acquisition' and origin == 'unknown'):
                    channels[name].clock = None
                    channels[name].time_evidence['clock'] = None
                topic.clock = channels[name].clock
                channels[name].logical_name = mapping.get("logical_name")
                channels[name].robot_part = mapping.get("robot_part")
                channels[name].selection = copy.deepcopy(mapping.get("select", {}))
                for key in ("sensor_id", "representation", "body_part", "side", "source_kind", "coordinate_frame", "calibration_status", "joint_names", "field_metadata"):
                    if key in mapping:
                        setattr(channels[name], key, copy.deepcopy(mapping[key]))
                from edc.data.transforms.orientation import source_orientation_metadata
                source_orientation_metadata(channels[name], topic.msg_type or '')
                topic.field_metadata = copy.deepcopy(channels[name].field_metadata)
                topic.representation = copy.deepcopy(channels[name].representation)
                topic.unit = channels[name].unit
                topic.side = channels[name].side
                topic.strict_side = self.backend == 'official'
                if self.backend == "official":
                    channels[name].representation.setdefault("semantic_status", "unknown")
                    channels[name].source_mapping = {
                        **copy.deepcopy(self.selector.get('source_mapping', {})),
                        'feature': reader.mapping[name]['feature'] if name in reader.mapping else None,
                        'source_sample_shape': reader.mapping.get(name, {}).get('source_sample_shape'),
                        'dtype': reader.features[reader.mapping[name]['feature']]['dtype'] if name in reader.mapping else None}
                # Legacy timestamp algorithms must not interpret unknown/playback time as acquisition.
                topic.timestamps_synthetic = origin not in {"acquisition", "container"}
            # FK-derived channels last: they read metadata already resolved on their joint inputs.
            for name, c in sorted(channels.items(), key=lambda item: 'fk' in item[1].selection):
                if not c.selection:
                    continue
                if 'fk' in c.selection:
                    from edc.robots.mapping import fk_metadata, fk_source
                    source = channels[fk_source(self.profile, c.selection)]
                    known_values = fk_metadata(self.profile, c.selection, channels)
                elif 'fields' in c.selection:
                    # Source fields are not declared on the source channel; only its feedback/command role carries over.
                    source = channels[c.selection['channel']]
                    known_values = {'source_kind': source.source_kind}
                else:
                    source = channels[c.selection['channel']]
                    source_spec = source.field_spec(c.selection['field'])
                    known_values = {key: source_spec.get(key, source.representation.get(key)) for key in
                                    ('unit', 'source_kind', 'coordinate_frame', 'calibration_status', 'quantity', 'semantic_status')}
                mapping = self.profile['channels'][name]
                if mapping.get('clock', source.clock) != source.clock or mapping.get('time_source', source.time_source) != source.time_source:
                    raise ValueError(f'{name}: selection cannot change source clock/time_source; transform explicitly')
                c.clock, c.time_source, c.timestamps = source.clock, source.time_source, source.timestamps
                c.time_evidence = copy.deepcopy(source.time_evidence)
                c.count = source.count
                c.source_mapping = {**copy.deepcopy(source.source_mapping), 'selection': copy.deepcopy(c.selection)}
                for target in (c.fields if 'fields' in c.selection else c.fields[:1]):
                    for key, known in known_values.items():
                        supplied = c.field_spec(target).get(key)
                        unknown_values = (None, '', 'unknown') if key == 'unit' else (None, 'unknown')
                        if known not in unknown_values:
                            if supplied not in (None, 'unknown', known):
                                raise ValueError(f'{name}: selection cannot relabel {key}; transform explicitly')
                            c.field_metadata.setdefault(target, {})[key] = known
                if 'indices' in c.selection and 'fields' not in c.selection:
                    c.joint_names =([source_spec['joint_names'][i] for i in c.selection['indices']]
                                     if source_spec.get('joint_names') and max(c.selection['indices']) < len(source_spec['joint_names']) and not c.joint_names else c.joint_names)
                c.source_kind = c.field_spec(c.fields[0])['source_kind']
                topic = info.topics[name]
                topic.modality = legacy_modality(c.modality, c.source_kind)
                topic.timestamps, topic.time_source = c.timestamps, c.time_source
                topic.timestamps_synthetic = c.time_source not in {'acquisition', 'container'}
            from edc.algorithms.channel_names import validate_channel_index
            validate_channel_index(channels, self.profile.get('channel_registry'))
            info.extra["dqs_profile"] = self.profile
            self._reader, self._info, self.channels = reader, info, channels
            if self.selector.get('edc_channels') is not None:
                from edc.algorithms.channel_names import resolve_channel_parameters
                names = resolve_channel_parameters(self, {'channels': self.selector['edc_channels']})['channels']
                self.channels = {n: channels[n] for n in names}
                self._info.topics = {n: info.topics[n] for n in names}
            return self
        except Exception:
            if reader is not None:
                reader.close()
            raise

    def close(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = self._info = None
        self._video_cache.clear()
        self._video_origins.clear()

    @contextmanager
    def opened(self):
        try:
            yield self.open()
        finally:
            self.close()

    def describe(self):
        self.open()
        return {"id": self.id, "source": str(self.path), "format": self.format,
                "selector": self.selector, "metadata": copy.deepcopy(self.metadata), "start_time": self._info.start_time,
                "end_time": self._info.end_time,
                "channels": [c.describe() for c in self.channels.values()]}

    def describe_legacy(self):
        """Metadata view for legacy adapters; payload access remains through public methods."""
        self.open()
        return self._info

    def algorithm_view(self):
        from edc.data.ir.bridge import EpisodeAccess
        self.open()
        return self._info, EpisodeAccess(self)

    def read_values(self, channel, indices=None):
        self.open()
        if 'fields' in self.channels[channel].selection:
            from edc.robots.mapping import select_field_rows, scaled
            rows = select_field_rows(self, channel)
            spec = self.profile['channels'][channel]
            if any('scale' in (m or {}) for m in (spec.get('field_metadata') or {}).values()):
                rows = [{k: scaled(spec, k, np.asarray(v, dtype=float)) for k, v in row.items()} for row in rows]
            return rows if indices is None else [rows[i] for i in indices]
        if self.channels[channel].selection:
            from edc.robots.mapping import select_values, check_joint_shape
            values = select_values(self, channel)
            c = self.channels[channel]
            check_joint_shape(c, c.fields[0], values, self.profile)
            selected = range(len(values)) if indices is None else indices
            return [{c.fields[0]: values[i]} for i in selected]
        rows = self._reader.channel_values(channel, indices)
        if rows is None:
            raise ValueError(f"Numeric values unavailable: {channel}")
        c = self.channels[channel]
        if c.modality == 'joint_space' and self.profile.get('parts') and rows:
            from edc.robots.mapping import check_joint_shape
            for field in c.fields:
                if any(field not in row for row in rows):
                    raise ValueError(f'Missing joint field: {channel}.{field}')
                check_joint_shape(c, field, np.asarray([row[field] for row in rows]), self.profile)
        return rows

    def video_source(self, channel):
        self.open()
        c = self.channels[channel]
        source = Path(c.source)
        stat = source.stat() if source.exists() else self.path.stat()
        key = (channel, stat.st_size, stat.st_mtime_ns)
        cached = self._video_cache.get(key)
        if cached and Path(cached).is_file():
            return cached
        path = self._reader.materialize_image_channel(self._info, channel)
        self._video_cache[key] = str(path)
        return str(path)

    def video_evidence(self, channel, inspection_input=None):
        """Describe the actual inspection input without inferring source integrity."""
        self.open()
        source = inspection_input if inspection_input is not None else self.video_source(channel)
        origin = self._video_origins.get(str(Path(source).resolve()), source)
        candidates = getattr(self._reader, 'video_sources', {})
        encoded = candidates.get(channel)
        original = bool(encoded and Path(origin).resolve() == Path(encoded[0]).resolve())
        if not encoded and self.format == 'mp4':
            original = Path(origin).resolve() == self.path.resolve()
        return {'inspection_object': 'original_encoded_video' if original else 'derived_preview',
                'encoding_integrity_scope': ('Original encoded video file; episode boundaries retained' if original
                    else 'Derived preview only; does not assess original JPEG/video encoding integrity'),
                'source_evidence': {'channel': channel, 'source': self.channels[channel].source,
                    'inspection_input': str(source), 'source_mapping': self.channels[channel].source_mapping}}

    def read_signal(self, channel, field):
        self.open()
        c = self.channels[channel]
        if not c.value_available or field not in c.fields:
            raise ValueError(f"Field not provided: {channel}.{field}")
        if c.selection:
            from edc.robots.mapping import select_values
            result = select_values(self, channel, field)
        else:
            result = self._reader.read_field(self._info, channel, field)
        if result is None:
            raise ValueError(f"Signal unavailable: {channel}.{field}")
        if len(result) != c.count:
            raise ValueError(f"Missing signal rows: {channel}: {len(result)} != {c.count}")
        from edc.robots.mapping import check_joint_shape
        check_joint_shape(c, field, result, self.profile)
        return result

    def align_signal(self, channel, field, reference, *, method, max_gap=None):
        from edc.data.transforms.resampling import align_signal
        return align_signal(self, channel, field, reference, method=method, max_gap=max_gap)

    def transform_signal(self, channel, field, *, target_unit=None, target_frame=None, kind=None):
        from edc.data.transforms.episode import signal
        return signal(self, channel, field, target_unit=target_unit, target_frame=target_frame, kind=kind)

    def transform_timestamps(self, channel, target_clock):
        from edc.data.transforms.episode import timestamps
        return timestamps(self, channel, target_clock)

    def transform_orientation(self, channel, *, target, field='orientation', tolerance=1e-6):
        from edc.data.transforms.orientation import orientation
        return orientation(self, channel, target=target, field=field, tolerance=tolerance)

    def forward_kinematics(self, chain):
        from edc.data.transforms.episode import fk
        return fk(self, chain)

    def image_storage(self, channel):
        """Native image/video storage; never generate a video to answer."""
        self.open()
        if channel not in self.channels or not self.channels[channel].image_available:
            raise ValueError(f'Image decoding not available: {channel}')
        return self._reader.image_storage(channel)

    def iter_frames(self, channel):
        self.open()
        c = self.channels[channel]
        if not c.image_available:
            raise ValueError(f"Image decoding not available: {channel}")
        count = 0
        for frame in self._reader.iter_frames(channel):
            if self.format in {'mcap', 'ros1_bag', 'ros2_bag'}:
                frame = dict(frame)
                index = frame['index']
                frame['container_time'] = frame.get('container_time', frame.get('source_time'))
                frame['source_time'] = c.timestamps[index] if c.timestamps is not None else None
                frame['time_source'], frame['clock'] = c.time_source, c.clock
                frame['playback_time_source'] = 'container'
            count += 1
            yield frame
        if c.count and count != c.count:
            raise ValueError(f"{channel}: decoded {count} frames, expected {c.count}")

    def materialize_video(self, channel, output):
        self.open()
        from edc.reporting.results import write_json
        c = self.channels[channel]
        import shutil
        output = Path(output).resolve()
        if output == Path(c.source).resolve() or (self.path.is_dir() and output.is_relative_to(self.path)):
            raise ValueError("Video output must not overwrite episode source")
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(output)
        path = self.video_source(channel)
        if Path(path).resolve() != output:
            shutil.copyfile(path, output)
            for suffix in ('.frames.json', '.evidence.json'):
                evidence = Path(str(path) + suffix)
                if evidence.is_file():
                    shutil.copyfile(evidence, Path(str(output) + suffix))
        self._video_origins[str(output)] = str(Path(path).resolve())
        path = str(output)
        write_json(Path(str(output) + ".timeline.json"), {
            "episode_id": self.id, "channel": channel, "video": path,
            "source": c.source, "time_source": c.time_source,
            "source_timestamps": c.timestamps,
            "alignment": "unverified", "note": "No acquisition synchronization claim"})
        return path


class Dataset:
    def __init__(self, path, profile, metadata, episodes):
        self.path = path
        self.profile = profile
        self.id = metadata.get("id", path.name)
        self.version = str(metadata.get("version", "unversioned"))
        from edc.data.ir.task_metadata import validate_tasks, validate_episode_metadata
        self.tasks = copy.deepcopy(metadata.get("tasks", []))
        self.task_catalog = validate_tasks(self.tasks)
        self.metadata = copy.deepcopy(metadata.get("metadata", {}))
        if not isinstance(self.metadata, dict):
            raise ValueError("Dataset metadata must be a mapping")
        for episode in episodes:
            validate_episode_metadata(episode.metadata, self.task_catalog)
        self._episodes = episodes

    def iter_episodes(self):
        yield from self._episodes

    def describe(self):
        episodes = []
        for episode in self.iter_episodes():
            try:
                with episode.opened():
                    episodes.append(episode.describe())
            except Exception as exc:
                episodes.append({"id": episode.id, "source": str(episode.path), "error": str(exc)})
        return {"id": self.id, "version": self.version, "tasks": self.tasks, "metadata": self.metadata, "episodes": episodes}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for episode in self._episodes:
            episode.close()


def _set_episode_backend(episode, backend):
    if backend not in {"auto", "official", "compat"}:
        raise ValueError("backend must be auto, official or compat")
    if backend == "official" and episode.format != "lerobot":
        raise ValueError("The official backend requires a LeRobot dataset")
    version = episode.selector.get("info", {}).get("codebase_version")
    episode.backend = ("official" if episode.format == "lerobot" and
        (backend == "official" or backend == "auto" and version == "v3.0" and _official_installed()) else "compat")


def _official_installed():
    """auto uses the official LeRobot reader only when the validated version is installed."""
    from importlib.metadata import version, PackageNotFoundError
    from edc.data.readers.lerobot_official import OFFICIAL_VERSION
    try:
        return version('lerobot') == OFFICIAL_VERSION
    except PackageNotFoundError:
        return False


def open_dataset(path, profile=None, *, backend="auto"):
    from edc.data.readers.structured import discover
    from edc.algorithms.registry import Registry
    path = Path(path).resolve()
    if backend not in {"auto", "official", "compat"}:
        raise ValueError("backend must be auto, official or compat")
    if not path.exists():
        raise FileNotFoundError(path)
    acceptance = path / 'acceptance.json' if path.is_dir() else None
    if acceptance is not None and acceptance.is_file():
        import json
        state = json.loads(acceptance.read_text())
        if state.get('conversion_status') == 'failed':
            raise ValueError('Conversion acceptance failed; inspect catalog diagnostics instead of running detection')
    if profile is None and path.is_dir() and (path / "robot.yaml").exists():
        profile = path / "robot.yaml"
    profile = load_profile(profile)
    matches = [p for p in Registry("edc.readers").discover().plugins.values() if p.can_open(path)]
    if len(matches) > 1:
        raise ValueError("Multiple reader plugins claim the input")
    if matches:
        return matches[0].open_dataset(path, profile=profile)
    metadata = {}
    if path.is_dir() and (path / "dataset.yaml").exists():
        metadata = yaml.safe_load((path / "dataset.yaml").read_text())
        if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
            raise ValueError("dataset.yaml requires schema_version: 1")
    episodes = []
    if "episodes" in metadata:
        for item in metadata["episodes"]:
            source = (path / item["path"]).resolve()
            if not source.is_relative_to(path):
                raise ValueError("Episode path must remain within dataset root")
            episodes.append(Episode(str(item["id"]), source, item.get("format", "episode"), profile,
                                    item.get("selector", {}), metadata=copy.deepcopy(item.get("metadata", {}))))
    else:
        episodes = discover(path, profile)
    ids = [ep.id for ep in episodes]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Dataset must contain unique, nonempty episode IDs")
    for ep in episodes:
        _set_episode_backend(ep, backend)
        if profile.get('time_declared') is False and ep.format in {'hdf5', 'parquet', 'lerobot', 'episode'}:
            raise ValueError('表格数据（JSON、HDF5、Parquet、LeRobot）要在 yaml 的 data 里写 time（采集时间列）'
                             '或 time_note（没有采集时间的原因）')
    mapping_path = path / "dqs-source-mapping.json" if path.is_dir() else None
    if mapping_path is not None and mapping_path.is_file():
        import json
        provenance = json.loads(mapping_path.read_text())
        if provenance.get("schema_version") != "dqs_source_mapping_v1":
            raise ValueError("Unsupported source mapping schema")
        items = provenance.get("episodes", [])
        by_index = {item["episode_index"]: item for item in items}
        if len(by_index) != len(episodes):
            raise ValueError("Source mapping episode count/identity mismatch")
        for ep in episodes:
            item = by_index[ep.selector["episode_index"]]
            ep.id = item["id"]
            ep.selector["source_mapping"] = {key: item[key] for key in
                ("source", "selector", "row_mapping") if key in item}
            ep.metadata["original_source_metadata"] = item.get("source_metadata", {})
        if len({ep.id for ep in episodes}) != len(episodes):
            raise ValueError("Duplicate stable episode IDs in source mapping")
    return Dataset(path, profile, metadata, episodes)
