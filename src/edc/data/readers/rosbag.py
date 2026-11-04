"""Public ROS1/ROS2 reader: storage directories, recorded clocks, lazy payloads."""
from pathlib import Path
import numpy as np
from .base import DataReader, DependencyMissing
from edc.data.ir.metadata import DataInfo, TopicInfo, classify_channel
from edc.data.ir.values import infer_fields, recorded_ros_fields, ros_values_to_dict


PIXEL_TYPES = ('/Image', '/CompressedImage', '/PointCloud2')


def _paths(path):
    path = Path(path)
    # ROS2 metadata owns the storage-file list, including MCAP and split DB3.
    if path.suffix == '.db3' and (path.parent / 'metadata.yaml').exists():
        return [path.parent]
    return [path]


def _reader(path):
    try:
        from rosbags.highlevel import AnyReader
    except ImportError as exc:
        raise DependencyMissing('ROS containers require rosbags') from exc
    return AnyReader(_paths(path))


def _header_time(message):
    header = getattr(message, 'header', None)
    stamp = getattr(header, 'stamp', None)
    if stamp is None:
        return None
    return float(stamp.sec) + float(stamp.nanosec) * 1e-9


def decode_ros_image(message, msgtype, decoders=None):
    """Shared pixel decoding only; clock selection and row order belong to the reader."""
    from edc.data.readers.video import decode_image
    if msgtype.endswith('/CompressedImage'):
        return decode_image(bytes(message.data))
    if msgtype.endswith('/CompressedVideo'):
        # H.264/H.265 视频流：后一帧依赖前面的帧，同一路相机要按顺序喂同一个解码器（decoders 由调用方按路保存）
        import av
        decoder = decoders.setdefault(message.format, av.CodecContext.create(message.format, 'r'))
        frames = decoder.decode(av.Packet(bytes(message.data)))
        if len(frames) != 1:
            raise ValueError(f'{message.format} 视频包解出 {len(frames)} 帧（应为 1 帧）')
        return frames[0].to_ndarray(format='rgb24')
    encoding = message.encoding.lower()
    channels = {'rgb8': 3, 'bgr8': 3, 'mono8': 1}.get(encoding)
    if channels is None:
        raise ValueError(f'Unsupported ROS pixel encoding: {encoding}')
    data = np.asarray(message.data, dtype=np.uint8).reshape(message.height, message.step)
    image = data[:, :message.width*channels].reshape(message.height, message.width, channels)
    if encoding == 'bgr8':
        image = image[:, :, ::-1].copy()
    return image[:, :, 0] if channels == 1 else image


class RosBagReader(DataReader):
    def __init__(self, hints=None):
        self.hints = hints or {}
        self._path = None
        self._topics_cache = {}

    def open(self, path):
        self._path = str(path)
        topics, acquired, sequences, recorded = {}, {}, {}, {}
        # Every message is already deserialized here for its header; keep numeric rows of
        # non-image topics so channel_values() never rescans the (image-dominated) bag.
        values, uncached = {}, set()
        with _reader(path) as reader:
            for connection, timestamp, raw in reader.messages():
                name = connection.topic
                if name not in topics:
                    modality = classify_channel(name, connection.msgtype, self.hints)
                    image = modality == 'image' and 'depth' not in name.lower() and connection.msgtype.endswith(('/Image', '/CompressedImage'))
                    topics[name] = TopicInfo(name=name, msg_type=connection.msgtype, modality=modality,
                        fields=infer_fields(name, connection.msgtype),
                        value_available=(modality in {'joint_state','cmd','imu','pose','odometry'}
                                         or recorded_ros_fields(connection.msgtype) is not None),
                        image_available=image, image_compressed=connection.msgtype.endswith('/CompressedImage'),
                        source=self._path, timestamps=[], time_source='container')
                    acquired[name], sequences[name], recorded[name] = [], [], []
                topic = topics[name]
                topic.timestamps.append(timestamp * 1e-9)
                recorded[name].append(int(timestamp))
                topic.count += 1
                try:
                    message = reader.deserialize(raw, connection.msgtype)
                    from .timeline import header_nanoseconds
                    from .ros_metadata import record_odometry_frames
                    record_odometry_frames(topic, message, topic.count - 1)
                    acquired[name].append(header_nanoseconds(message))
                    sequences[name].append(getattr(getattr(message, 'header', None), 'seq', None))
                except Exception:
                    acquired[name].append(None)
                    sequences[name].append(None)
                    uncached.add(name)
                    continue
                # value_available may still be granted later by the profile (e.g. custom robot messages),
                # so cache every non-pixel topic, not only those classified as numeric here.
                if not connection.msgtype.endswith(PIXEL_TYPES) and name not in uncached:
                    value = ros_values_to_dict(message, connection.msgtype)
                    if hasattr(message, 'name'):
                        value['names'] = list(message.name)
                    values.setdefault(name, []).append(value)
            for name, topic in topics.items():
                from .timeline import set_recorded_timelines
                set_recorded_timelines(topic, recorded[name], acquired[name], container_field='bag_record_time')
                if all(s is not None for s in sequences[name]):
                    topic.seqs = sequences[name]
        times = [t for topic in topics.values() for t in topic.timestamps]
        if not topics:
            raise ValueError('ROS container contains no messages')
        source = Path(path)
        fmt = 'ros2_bag' if source.is_dir() or source.suffix in {'.db3','.mcap'} else 'ros1_bag'
        self._topics_cache = topics
        self._values_cache = {name: rows for name, rows in values.items()
                              if name not in uncached and len(rows) == topics[name].count}
        self._info = DataInfo(path=str(path), format=fmt, topics=topics,
            start_time=min(times), end_time=max(times), duration=max(times)-min(times))
        return self._info

    def channel_values(self, name, indices=None):
        if name not in self._topics_cache:
            return None
        cached = getattr(self, '_values_cache', {}).get(name)
        if cached is not None:
            # Fresh dicts per call: callers may add keys; values are never modified in place.
            if indices is None:
                return [dict(row) for row in cached]
            # Same contract as the scan below: message order, duplicates and out-of-range ignored.
            return [dict(cached[i]) for i in sorted(set(indices)) if 0 <= i < len(cached)]
        rows = []
        selected = None if indices is None else set(indices)
        with _reader(self._path) as reader:
            connections = [c for c in reader.connections if c.topic == name]
            for i, (connection, _, raw) in enumerate(reader.messages(connections=connections)):
                if selected is None or i in selected:
                    message = reader.deserialize(raw, connection.msgtype)
                    value = ros_values_to_dict(message, connection.msgtype)
                    if hasattr(message, 'name'):
                        value['names'] = list(message.name)
                    rows.append(value)
        return rows

    def image_storage(self, name):
        return 'frames' if self._topics_cache[name].image_available else 'unknown'

    def iter_frames(self, name):
        from edc.data.readers.video import decode_image
        topic = self._topics_cache[name]
        if not topic.image_available:
            raise ValueError(f'{name}: image decoding unavailable')
        with _reader(self._path) as reader:
            connections = [c for c in reader.connections if c.topic == name]
            start = None
            for i, (connection, timestamp, raw) in enumerate(reader.messages(connections=connections)):
                message = reader.deserialize(raw, connection.msgtype)
                image = decode_ros_image(message, connection.msgtype)
                source_time = timestamp * 1e-9
                if start is None:
                    start = source_time
                selected = topic.timestamps[i] if topic.timestamps is not None else None
                yield {'index': i, 'playback_time': source_time-start, 'source_time': selected,
                       'container_time': source_time, 'time_source': topic.time_source,
                       'clock': getattr(topic, 'clock', None), 'playback_time_source': 'container',
                       'image': image}

    def materialize_image_channel(self, info, name, output_path=None):
        from edc.data.readers.video import encode_frames, cache_path
        output = output_path or cache_path(self._path, name)
        return encode_frames(self.iter_frames(name), output, expected_count=info.topics[name].count)
