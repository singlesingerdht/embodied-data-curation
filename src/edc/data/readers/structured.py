"""Public-installable structured readers. Unsupported layouts fail explicitly."""
from pathlib import Path
import json
import fnmatch
import numpy as np
from edc.data.compat import legacy
from edc.data.ir.modalities import normalize_modality, NUMERIC_MODALITIES


def arrow_source_values(column):
    """Preserve declared numeric scalar/list dtypes without filling nulls."""
    import pyarrow as pa
    rows = column.to_pylist()
    leaf = column.type
    while pa.types.is_list(leaf) or pa.types.is_large_list(leaf) or pa.types.is_fixed_size_list(leaf):
        leaf = leaf.value_type
    if pa.types.is_integer(leaf) or pa.types.is_floating(leaf) or pa.types.is_boolean(leaf):
        def contains_null(value):
            return value is None or isinstance(value, list) and any(contains_null(v) for v in value)
        if not contains_null(rows):
            # Older Arrow versions require pandas even for to_pandas_dtype().
            # Primitive Arrow widths map directly to NumPy without that dependency.
            if pa.types.is_boolean(leaf):
                dtype = np.dtype(np.bool_)
            else:
                kind = 'f' if pa.types.is_floating(leaf) else 'u' if pa.types.is_unsigned_integer(leaf) else 'i'
                dtype = np.dtype(f'{kind}{leaf.bit_width // 8}')
            try:
                return np.asarray(rows, dtype=dtype)
            except ValueError:
                pass  # Ragged shapes remain literal rows; the selected field fails explicitly.
    return rows


def declared_numeric(values, fields):
    """Explicit raw numeric fields are readable without guessing a modality."""
    if not fields:
        return False
    try:
        dtype = getattr(values, 'dtype', None)
        if dtype is not None and dtype.kind in 'fiu':
            return len(fields) == 1
        if len(values) and isinstance(values[0], dict):
            return all(np.asarray([row[field] for row in values]).dtype.kind in 'fiu' for field in fields)
        return len(fields) == 1 and np.asarray(values).dtype.kind in 'fiu'
    except (KeyError, TypeError, ValueError):
        return False


def discover(path, profile):
    from edc.data.ir.dataset import Episode
    legacy()
    from edc.data.ir.metadata import detect_format
    episodes = []

    def visit(source):
        relative = source.relative_to(path).as_posix() if source != path else source.name
        if source.is_dir() and (source / "meta/info.json").exists():
            info = json.loads((source / "meta/info.json").read_text())
            version = info.get("codebase_version")
            if version not in {"v2.1", "v3.0"}:
                raise ValueError(f"Unsupported LeRobot version: {version}")
            if version == "v2.1":
                rows = [json.loads(line) for line in (source / "meta/episodes.jsonl").read_text().splitlines() if line]
            else:
                import pyarrow.parquet as pq
                files = sorted((source / "meta/episodes").rglob("*.parquet"))
                if not files:
                    raise ValueError("LeRobot v3 requires meta/episodes Parquet metadata")
                rows = [row for f in files for row in pq.read_table(f).to_pylist()]
            for row in rows:
                index = int(row["episode_index"])
                episodes.append(Episode(f"{relative}:{index}", source, "lerobot", profile,
                    {"episode_index": index, "length": row["length"], "metadata": row, "info": info}))
            return
        if source.is_dir() and (source / "metadata.yaml").exists():
            episodes.append(Episode(relative, source, "ros2_bag", profile))
            return
        if source.is_dir() and (source / "trajectory.json").exists():
            episodes.append(Episode(relative, source, "episode", profile))
            return
        fmt = detect_format(str(source))
        if source.is_dir() and fmt == "episode":
            episodes.append(Episode(relative, source, fmt, profile))
            return
        if source.is_dir():
            for child in sorted(source.iterdir()):
                if not child.name.startswith((".", "_")) and child.name not in {"output", "results"}:
                    safe_visit(child)
            return
        if source.suffix.lower() in {".parquet", ".pq"}:
            import pyarrow.parquet as pq
            schema = pq.read_schema(source)
            key = profile.get("episode_column", "episode_index")
            if key in schema.names:
                ids = pq.read_table(source, columns=[key])[key].unique().to_pylist()
                for index in ids:
                    episodes.append(Episode(f"{relative}:{index}", source, "parquet", profile, {key: index}))
            else:
                episodes.append(Episode(relative, source, "parquet", profile))
        elif source.suffix.lower() in {".h5", ".hdf5"}:
            import h5py
            with h5py.File(source) as handle:
                group_key = profile.get("episode_group")
                if group_key:
                    groups = [f"{group_key.rstrip('/')}/{key}" for key in sorted(handle[group_key])]
                elif "episodes" in handle and isinstance(handle["episodes"], h5py.Group):
                    groups = [f"/episodes/{key}" for key in sorted(handle["episodes"])]
                else:
                    groups = ["/"]
                for group in groups:
                    episodes.append(Episode(f"{relative}:{group}", source, "hdf5", profile, {"group": group}))
        elif fmt in {"mp4", "ros1_bag", "ros2_bag", "mcap"}:
            episodes.append(Episode(relative, source, fmt, profile))
    def safe_visit(source):
        try:
            visit(source)
        except Exception as exc:
            relative = source.relative_to(path).as_posix() if source != path else source.name
            episodes.append(Episode(relative + ":unresolved", source, "unresolved", profile,
                {"discovery_error": f"{type(exc).__name__}: {exc}", "missing_dependency": isinstance(exc, ImportError)}))

    safe_visit(path)
    return episodes


def open_structured(episode):
    base = legacy()
    from edc.data.ir.metadata import DataInfo, TopicInfo, classify_channel
    from edc.data.ir.values import infer_fields

    class StructuredReader(base.DataReader):
        def open(self, path):
            return self.info

        def channel_values(self, name, indices=None):
            values = self.loaders[name]()
            selected = range(len(values)) if indices is None else indices
            field = self.info.topics[name].fields
            if not field:
                raise ValueError(f"Explicit field mapping required for {name}")
            result = []
            for i in selected:
                value = values[i]
                if isinstance(value, dict):
                    if any(key not in value for key in field):
                        raise ValueError(f"{name}: missing declared fields")
                    result.append(value)
                elif len(field) == 1:
                    result.append({field[0]: value})
                else:
                    raise ValueError(f"{name}: multiple fields require structured rows")
            return result

        def iter_frames(self, name):
            from edc.data.readers.video import video_frames, decode_image
            if name in self.video_sources:
                path, start, end = self.video_sources[name]
                yield from video_frames(path, start, end)
            elif name in self.frame_loaders:
                timestamps = self.info.topics[name].timestamps
                for i, payload in enumerate(self.frame_loaders[name]()):
                    value = timestamps[i] if timestamps else None
                    yield {"index": i, "playback_time": value - timestamps[0] if value is not None else None,
                           "source_time": value, "image": decode_image(payload)}
            else:
                yield from super().iter_frames(name)

        def materialize_image_channel(self, info, name, output_path=None):
            from edc.data.readers.video import encode_frames, cache_path
            if name in self.video_sources and self.video_sources[name][1:] == (None, None):
                return str(self.video_sources[name][0])
            if name not in self.video_sources and name not in self.frame_loaders:
                return super().materialize_image_channel(info, name, output_path)
            output = output_path or cache_path(episode.path, name, episode.selector)
            fps = episode.selector.get("info", {}).get("fps") or episode.profile.get("video_fps")
            return encode_frames(self.iter_frames(name), output, fps=fps, expected_count=info.topics[name].count)

        def close(self):
            if getattr(self, "handle", None) is not None:
                self.handle.close()
                self.handle = None

    reader = StructuredReader()
    reader.loaders = {}
    reader.frame_loaders = {}
    reader.video_sources = {}
    topics = {}
    timestamps = None
    recorded_timestamps = None
    timestamp_unit = 'unknown'
    timestamp_unit_source = 'unconfirmed'
    synthetic = False
    columns = {}
    try:
        if episode.format == "episode":
            path = episode.path / "trajectory.json"
            if not path.exists():
                from edc.data.readers.episode import _find_traj_json
                path = _find_traj_json(episode.path)
            if path is None:
                raise ValueError("Missing trajectory JSON")
            content = json.loads(path.read_text())
            rows = content["data"]
            if not rows or not all(isinstance(r, dict) for r in rows):
                raise ValueError("Trajectory requires nonempty object rows")
            keys = set().union(*(r.keys() for r in rows))
            time_key = episode.profile.get("timestamp_column", "timestamp")
            if time_key in keys:
                timestamps = [row[time_key] for row in rows]
            elif "fps" in content:
                fps = float(content["fps"])
                if not np.isfinite(fps) or fps <= 0:
                    raise ValueError("fps must be finite and positive")
                timestamps = [i / fps for i in range(len(rows))]
                synthetic = True
            for name in sorted(keys - {time_key}):
                columns[name] = [row.get(name) for row in rows]
            # Profile-declared groups join per-quantity columns of one device (e.g. joint position,
            # velocity and effort) into one structured channel, row by row; the columns stay as recorded.
            for group, members in (episode.profile.get("episode_column_groups") or {}).items():
                if group in keys or not isinstance(members, dict) or not members:
                    raise ValueError(f"{group}: column group must be a new name mapping fields to columns")
                absent = sorted(set(members.values()) - keys)
                if absent:
                    raise ValueError(f"{group}: column group source columns absent: {', '.join(absent)}")
                columns[group] = [{field: row.get(column) for field, column in members.items()} for row in rows]
        elif episode.format == "hdf5":
            import h5py
            reader.handle = h5py.File(episode.path)
            group = reader.handle[episode.selector.get("group", "/")]
            datasets = {}
            group.visititems(lambda name, obj: datasets.update({name: obj}) if isinstance(obj, h5py.Dataset) else None)
            # Multiple independent clocks require explicit episode/channel mapping.
            clocks = [name for name in datasets if name.rsplit("/", 1)[-1] in {"timestamp", "timestamps", "t"}]
            time_key = episode.profile.get("timestamp_column")
            if time_key is None and len(clocks) > 1:
                raise ValueError("Multiple HDF5 timelines: specify episode_group and timestamp_column")
            time_key = time_key or (clocks[0] if clocks else None)
            timestamps = datasets[time_key][:].tolist() if time_key else None
            for name, ds in datasets.items():
                spec = next((v for pattern, v in episode.profile.get("channels", {}).items() if fnmatch.fnmatchcase(name, pattern)), {})
                declared = normalize_modality(spec.get("modality", "depth" if "depth" in name.lower() else classify_channel(name)))
                image = declared == "rgb"
                if declared == "depth" and not (spec.get("fields") and ds.dtype.kind in "fiu") :
                    topics[name] = TopicInfo(name=name, modality="depth", count=len(ds), source=str(episode.path))
                    continue
                if image and ds.shape and (ds.ndim >= 3 or ds.dtype.kind == "O"):
                    reader.frame_loaders[name] = lambda ds=ds: ds
                    topics[name] = TopicInfo(name=name, modality="image", count=len(ds), image_available=True,
                        timestamps=timestamps, source=str(episode.path), time_source=spec.get("time_source", "unknown"))
                    if timestamps is not None and len(ds) != len(timestamps):
                        raise ValueError(f"{name}: image/timestamp length mismatch")
                    continue
                if (name == time_key and not spec.get("fields")) or not ds.shape or ds.dtype.kind not in "fiu":
                    continue
                if 0 in ds.shape:
                    continue
                if ds.ndim > 2 and not (spec.get("fields") and declared in NUMERIC_MODALITIES):
                    continue  # Explicit tensor fields required; never decode depth as RGB.
                columns[name] = ds
        else:
            import pyarrow.parquet as pq
            import pyarrow as pa
            if episode.format == "lerobot":
                index = episode.selector["episode_index"]
                files = sorted((episode.path / "data").rglob("*.parquet"))
                filters = [("episode_index", "=", index)]
            else:
                files = [episode.path]
                filters = [(key, "=", value) for key, value in episode.selector.items()] or None
            if not files:
                raise ValueError("No data Parquet files")
            table = pa.concat_tables([pq.read_table(file, filters=filters) for file in files])
            if episode.format == "lerobot" and len(table) != episode.selector["length"]:
                raise ValueError("LeRobot episode length differs from metadata")
            if episode.format == "lerobot" and "frame_index" in table.column_names:
                table = table.sort_by([("frame_index", "ascending")])
                if table["frame_index"].to_pylist() != list(range(len(table))):
                    raise ValueError("LeRobot frame_index must be unique and contiguous within episode")
            time_key = episode.profile.get("timestamp_column", "timestamp")
            timestamps = table[time_key].to_pylist() if time_key in table.column_names else None
            for name in table.column_names:
                if name not in {time_key, "episode_index", "frame_index", "index", "task_index"}:
                    columns[name] = arrow_source_values(table[name])
        if timestamps is not None:
            recorded_timestamps = list(timestamps)
            from edc.data.readers.timeline import declared_timestamp_unit
            standard_time = episode.format == 'lerobot' and time_key == 'timestamp'
            if standard_time:
                declared_unit = declared_timestamp_unit(episode.profile.get('timestamp_unit'))
                if declared_unit not in {'s', 'unknown'}:
                    raise ValueError('LeRobot standard timestamp is in seconds; use an original-time column for other units')
                synthetic = True  # LeRobot frame-index time is not a sensor acquisition clock.
            if synthetic:
                timestamp_unit = 's'
                timestamp_unit_source = 'format_standard' if standard_time else 'generated_seconds'
            else:
                timestamp_unit = declared_timestamp_unit(episode.profile.get('timestamp_unit'))
                timestamp_unit_source = ('profile' if timestamp_unit != 'unknown' and 'timestamp_unit' in episode.profile
                                         else 'unconfirmed')
            if timestamp_unit == 'unknown':
                timestamps = None  # Raw time remains in source; do not mislabel unknown units as seconds.
            else:
                scale = {"s": 1, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}[timestamp_unit]
                timestamps = (np.asarray(timestamps, dtype=float) * scale).tolist()
        for topic in topics.values():
            if topic.modality == "image":
                topic.timestamps = timestamps
        for name, values in columns.items():
            spec = next((v for pat, v in episode.profile.get("channels", {}).items()
                         if fnmatch.fnmatchcase(name, pat)), {})
            modality = spec.get("modality", "joint_state" if "joint" in name.lower() and "pos" in name.lower() else classify_channel(name))
            fields = spec.get("fields", ["current"] if "joint_cur" in name.lower() else infer_fields(name)) or []
            count = len(values)
            canonical = normalize_modality(modality)
            numeric = bool(fields) and (canonical in NUMERIC_MODALITIES or
                (bool(spec.get("fields")) and declared_numeric(values, fields)))
            if canonical == "rgb" and len(values) and (isinstance(values[0], (bytes, bytearray)) or
                    isinstance(values[0], dict) and set(values[0]) & {"bytes", "path"}):
                def load_images(rows=values):
                    for row in rows:
                        if isinstance(row, (bytes, bytearray)):
                            yield row
                        elif isinstance(row, dict) and row.get("bytes") is not None:
                            yield row["bytes"]
                        elif isinstance(row, dict) and row.get("path"):
                            source_root = episode.path if episode.path.is_dir() else episode.path.parent
                            path = (source_root / row["path"]).resolve()
                            if not path.is_relative_to(source_root):
                                raise ValueError("Image reference outside dataset root")
                            yield path.read_bytes()
                        else:
                            raise ValueError("Missing image payload/reference")
                reader.frame_loaders[name] = load_images
                if recorded_timestamps is not None and len(recorded_timestamps) != count:
                    raise ValueError(f"Image/time length mismatch: {name}")
                topics[name] = TopicInfo(name=name, modality="image", count=count, image_available=True,
                    timestamps=timestamps, source=str(episode.path))
                continue
            if recorded_timestamps is not None and len(recorded_timestamps) != count and numeric:
                raise ValueError(f"Time/value length mismatch: {name}")
            reader.loaders[name] = lambda value=values: value
            topics[name] = TopicInfo(name=name, modality=modality, count=count,
                fields=fields, value_available=numeric, timestamps=timestamps if timestamps is None or len(timestamps) == count else None,
                timestamps_synthetic=synthetic, source=str(episode.path))
        if episode.format == "lerobot":
            _add_lerobot_videos(episode, reader, topics)
        if episode.format == "episode":
            from edc.data.readers.mp4 import _ffprobe
            for video in sorted(episode.path.glob("*.mp4")):
                fps, frames, duration, width, height = _ffprobe(video)
                reader.video_sources[video.stem] = (video, None, None)
                topics[video.stem] = TopicInfo(name=video.stem, modality="image", count=frames,
                    image_available=True, source=str(video), msg_type="video/mp4",
                    timestamps=[i / fps for i in range(frames)] if fps > 0 else None,
                    timestamps_synthetic=True)
                # Profile-declared `video_frame_time: rows`: the export wrote one row per video frame,
                # so frame i takes row i's time. Only when the counts match; otherwise FPS time stays.
                if episode.profile.get("video_frame_time") == "rows" and timestamps is not None:
                    if frames == len(timestamps):
                        topics[video.stem].timestamps = list(timestamps)
                        topics[video.stem].frame_time_from_rows = True
                    else:
                        topics[video.stem].time_evidence = {
                            'video_frame_time': 'fps_generated',
                            'unavailable_reason': f'video has {frames} frames but the trajectory has '
                                                  f'{len(timestamps)} rows; frame i = row i cannot be applied'}
        for topic in topics.values():
            # A single stored row timeline is shared, not independently measured
            # sensor clocks. Keep exact source values even if their unit is unknown.
            if recorded_timestamps is not None and (topic.msg_type != 'video/mp4'
                                                    or getattr(topic, 'frame_time_from_rows', False)):
                topic.recorded_timestamp_values = recorded_timestamps
                topic.timestamps_synthetic = synthetic
                topic.recorded_timestamp_unit = timestamp_unit
                topic.time_evidence = {
                    'timeline_scope': 'shared_dataset_rows', 'timestamp_field': time_key,
                    'timestamp_unit': topic.recorded_timestamp_unit,
                    'timestamp_unit_source': timestamp_unit_source,
                    'algorithm_timestamp_unit': 's' if timestamps is not None else None,
                    'unavailable_reason': ('Original timestamp unit is unconfirmed; no seconds or clock inferred'
                                           if timestamp_unit == 'unknown' else None),
                    'time_generated': synthetic, 'independent_acquisition_timeline': False,
                    'source_row_mapping': 'identity; no sorting, dropping, or resampling',
                    'establishes_acquisition_sync': False,
                    'limitation': 'Shared row time cannot establish original cross-channel acquisition synchronization'}
                if topic.msg_type == 'video/mp4':
                    topic.time_evidence['video_frame_mapping'] = ('frame i = trajectory row i (profile video_frame_time: rows; '
                                                                  'frame count equals row count)')
        if not topics:
            raise ValueError("No readable channels")
        finite = [t for t in (timestamps or []) if np.isfinite(t)]
        reader.info = DataInfo(path=str(episode.path), format=episode.format, topics=topics,
            start_time=min(finite, default=0), end_time=max(finite, default=0),
            duration=max(finite, default=0) - min(finite, default=0))
        reader._info = reader.info
        return reader, reader.info
    except Exception:
        reader.close()
        raise


def _add_lerobot_videos(episode, reader, topics):
    from edc.data.ir.metadata import TopicInfo
    info = episode.selector["info"]
    row = episode.selector["metadata"]
    index = episode.selector["episode_index"]
    for key, feature in info.get("features", {}).items():
        if feature.get("dtype") != "video":
            continue
        if info["codebase_version"] == "v2.1":
            chunk = index // int(info.get("chunks_size", 1000))
            source = info["video_path"].format(episode_chunk=chunk, episode_index=index, video_key=key)
            start = end = None
        else:
            prefix = f"videos/{key}/"
            source = info["video_path"].format(video_key=key, chunk_index=int(row[prefix + "chunk_index"]),
                                                file_index=int(row[prefix + "file_index"]))
            start, end = float(row[prefix + "from_timestamp"]), float(row[prefix + "to_timestamp"])
            if not np.isfinite([start, end]).all() or end <= start:
                raise ValueError(f"{key}: invalid video offset")
        path = (episode.path / source).resolve()
        if not path.is_relative_to(episode.path) or not path.is_file():
            raise ValueError(f"{key}: video source missing or outside dataset")
        fps = float(info["fps"])
        if fps <= 0 or not np.isfinite(fps):
            raise ValueError("Invalid LeRobot FPS")
        reader.video_sources[key] = (path, start, end)
        # Frame times generated from FPS are never acquisition evidence.
        topics[key] = TopicInfo(name=key, modality="image", count=episode.selector["length"],
            image_available=True, source=str(path), msg_type="video/mp4", time_source="synthetic",
            timestamps=[i / fps for i in range(episode.selector["length"])], timestamps_synthetic=True)
