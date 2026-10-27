"""mcap 格式适配器: .mcap 文件。

参考: dayan-rough-datachecking src/datafilter/data/readers/mcap.py (main 分支)
依赖: mcap；Protobuf 通过可选 protobuf 依赖动态解码内嵌 descriptor。
"""
from pathlib import Path
from typing import Dict, Optional, List

from .base import DataReader, DependencyMissing
from edc.data.ir.metadata import (
    DataInfo, TopicInfo, classify_channel,
    FORMAT_MCAP, MODALITY_IMAGE, MODALITY_IMU,
    MODALITY_JOINT_STATE, MODALITY_CMD, MODALITY_POSE,
)
from edc.data.ir.values import infer_fields, recorded_ros_fields, ros_values_to_dict

try:
    from mcap.reader import make_reader
    _HAS_MCAP = True
except ImportError:
    _HAS_MCAP = False

try:
    from rosbags.highlevel import AnyReader
    _HAS_ROSBAGS = True
except ImportError:
    _HAS_ROSBAGS = False

_NUMERIC_MODALITIES = {MODALITY_IMU, MODALITY_JOINT_STATE, MODALITY_CMD, MODALITY_POSE, 'odometry'}


class McapReader(DataReader):

    def __init__(self, hints: Optional[Dict] = None):
        if not _HAS_MCAP:
            raise DependencyMissing("mcap 格式需要安装 mcap: pip install mcap")
        self.hints = hints or {}
        self._path = None
        self._topics_cache = {}

    def open(self, path: str) -> DataInfo:
        p = Path(path)
        self._path = str(p)

        topics: Dict[str, TopicInfo] = {}
        global_start = float("inf")
        global_end = 0.0

        self._protobuf_classes = {}
        self._protobuf_values = {}
        errors = {}
        signatures = {}
        raw_container, raw_header, ros_stores = {}, {}, {}
        with open(p, "rb") as f:
            reader = make_reader(f)
            # One pass works with and without MCAP summary/index records.
            for schema, ch, msg in reader.iter_messages(log_time_order=False):
                signature = (ch.message_encoding, schema.name if schema else None,
                             schema.data if schema else None)
                if ch.topic in signatures and signatures[ch.topic] != signature:
                    raise ValueError(f"Conflicting schemas for MCAP topic: {ch.topic}")
                signatures[ch.topic] = signature
                if ch.topic not in topics:
                    msg_type = schema.name if schema else ch.message_encoding
                    mod = classify_channel(ch.topic, msg_type, self.hints)
                    ros = ch.message_encoding in {"cdr", "ros1"} and _HAS_ROSBAGS
                    numeric = mod in _NUMERIC_MODALITIES or recorded_ros_fields(msg_type) is not None
                    ti = TopicInfo(name=ch.topic, msg_type=msg_type, modality=mod,
                        value_available=numeric and ros,
                        image_available=mod == MODALITY_IMAGE and ros,
                        fields=infer_fields(ch.topic, msg_type) if numeric else None,
                        timestamps=[], source=self._path, time_source="container")
                    topics[ch.topic] = ti
                    raw_container[ch.topic], raw_header[ch.topic] = [], []
                    if ch.message_encoding == 'protobuf':
                        from .protobuf import message_class, image
                        try:
                            cls = message_class(schema)
                            first = cls.FromString(msg.data)
                            self._protobuf_classes[ch.topic] = cls
                            if msg_type in {'foxglove.RawImage', 'foxglove.CompressedImage'}:
                                image(first)  # Do not advertise an unsupported pixel encoding.
                                ti.modality, ti.image_available = MODALITY_IMAGE, True
                                ti.image_compressed = msg_type == 'foxglove.CompressedImage'
                            else:
                                self._protobuf_values[ch.topic] = []
                                ti.fields = []
                        except (ImportError, ValueError, KeyError) as exc:
                            self._protobuf_classes.pop(ch.topic, None)
                            errors[ch.topic] = str(exc)
                ti = topics[ch.topic]
                raw_container[ch.topic].append(int(msg.log_time))
                stamp = None
                if ch.message_encoding in {'cdr', 'ros1'} and _HAS_ROSBAGS and schema:
                    try:
                        if ch.topic not in ros_stores:
                            from rosbags.typesys import get_typestore, get_types_from_msg, Stores
                            store = get_typestore(Stores.EMPTY)
                            typename = schema.name if '/msg/' in schema.name else schema.name.replace('/', '/msg/', 1)
                            store.register(get_types_from_msg(schema.data.decode(), typename))
                            ros_stores[ch.topic] = (store, typename)
                        store, typename = ros_stores[ch.topic]
                        from .timeline import header_nanoseconds
                        decode = store.deserialize_cdr if ch.message_encoding == 'cdr' else store.deserialize_ros1
                        decoded = decode(msg.data, typename)
                        stamp = header_nanoseconds(decoded)
                        from .ros_metadata import record_odometry_frames
                        record_odometry_frames(ti, decoded, ti.count)
                    except Exception as exc:
                        errors.setdefault(ch.topic + ':header', str(exc))
                raw_header[ch.topic].append(stamp)
                if ch.topic in self._protobuf_values:
                    from .protobuf import numeric_values
                    try:
                        values = numeric_values(self._protobuf_classes[ch.topic].FromString(msg.data))
                    except Exception as exc:
                        raise ValueError(f"Protobuf decode failed: {ch.topic}, row {ti.count}") from exc
                    self._protobuf_values[ch.topic].append(values)
                    ti.fields = sorted(set(ti.fields) | set(values))
                    ti.value_available = bool(ti.fields)
                ti.count += 1
                seconds = msg.log_time * 1e-9
                ti.timestamps.append(seconds)
                global_start = min(global_start, seconds)
                global_end = max(global_end, seconds)

        from .timeline import set_recorded_timelines
        for name, topic in topics.items():
            set_recorded_timelines(topic, raw_container[name], raw_header[name])
        self._ros_stores = ros_stores
        self._topics_cache = topics
        duration = global_end - global_start if global_start < float("inf") else 0.0
        self._info = DataInfo(
            path=self._path, format=FORMAT_MCAP, duration=duration,
            start_time=global_start if global_start < float("inf") else 0.0,
            end_time=global_end,
            topics=topics,
            extra={"channel_count": len(topics), "decode_errors": errors},
        )

        return self._info

    def channel_values(self, name: str, indices: Optional[List[int]] = None) -> Optional[List[dict]]:
        """Return decoded Protobuf fields or ROS values without changing physical semantics."""
        if name not in self._topics_cache:
            return None
        if name in self._protobuf_values:
            rows = self._protobuf_values[name]
            return rows if indices is None else [rows[i] for i in indices]
        if name in self._protobuf_classes or not self._topics_cache[name].value_available:
            return None
        if not _HAS_ROSBAGS:
            return None
        result = []
        target_set = set(indices) if indices is not None else None
        idx = 0
        for index, (_, message, typename) in enumerate(self._iter_ros_messages(name)):
            if target_set is not None and index not in target_set:
                continue
            try:
                result.append(ros_values_to_dict(message, typename))
            except Exception:
                result.append({})
        return result

    def _iter_ros_messages(self, name):
        if name not in self._ros_stores:
            raise ValueError(f'ROS schema decoder unavailable: {name}')
        store, typename = self._ros_stores[name]
        with open(self._path, 'rb') as stream:
            for _, channel, message in make_reader(stream).iter_messages(topics=[name], log_time_order=False):
                decode = store.deserialize_cdr if channel.message_encoding == 'cdr' else store.deserialize_ros1
                yield message.log_time * 1e-9, decode(message.data, typename), typename

    def image_storage(self, name):
        return 'frames' if self._topics_cache[name].image_available else 'unknown'

    def iter_frames(self, name):
        if name in self._protobuf_classes:
            from .protobuf import image
            cls = self._protobuf_classes[name]
            start = self._topics_cache[name].timestamps[0]
            with open(self._path, 'rb') as stream:
                for index, (_, _, msg) in enumerate(make_reader(stream).iter_messages(topics=[name], log_time_order=False)):
                    seconds = msg.log_time * 1e-9
                    yield {"index": index, "source_time": seconds,
                           "playback_time": seconds - start,
                           "image": image(cls.FromString(msg.data))}
            return
        if not self._topics_cache[name].image_available:
            raise ValueError(f"Image decoder unavailable: {name}")
        from .rosbag import decode_ros_image
        topic = self._topics_cache[name]
        start = None
        for index, (container_time, message, typename) in enumerate(self._iter_ros_messages(name)):
            if start is None:
                start = container_time
            yield {'index': index, 'source_time': topic.timestamps[index] if topic.timestamps is not None else None,
                   'container_time': container_time, 'playback_time': container_time-start,
                   'time_source': topic.time_source, 'clock': getattr(topic, 'clock', None),
                   'playback_time_source': 'container', 'image': decode_ros_image(message, typename)}

    def materialize_image_channel(self, info, name, output_path=None):
        from .video import encode_frames, cache_path
        return encode_frames(self.iter_frames(name), output_path or cache_path(self._path, name),
                             expected_count=info.topics[name].count)

