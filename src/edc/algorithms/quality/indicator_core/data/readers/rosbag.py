"""ROS bag 读取器（ros1 .bag / ros2 .db3）"""
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
from ..ir import (
    DataInfo, TopicInfo, RawMessage,
    MODALITY_IMAGE, _NUMERIC_MODALITIES, decode_image_bytes,
)
from .base import DataReader

def ros_values_to_dict(msg: Any, msg_type: str) -> dict:
    """把 ROS 解析结果映射为规范值字典"""
    mt = (msg_type or "").lower()
    try:
        if "imu" in mt:
            return {
                "ang_vel": [msg.angular_velocity.x, msg.angular_velocity.y, msg.angular_velocity.z],
                "lin_acc": [msg.linear_acceleration.x, msg.linear_acceleration.y, msg.linear_acceleration.z],
                "orientation": [msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w],
            }
        if "jointstate" in mt:
            return {
                "position": list(msg.position),
                "velocity": list(msg.velocity),
                "effort": list(msg.effort),
            }
        if "jointcmd" in mt or "joint_cmd" in mt:
            return {
                "joint_q": list(msg.joint_q),
                "joint_v": list(msg.joint_v),
                "tau": list(msg.tau),
            }
        if "sensorsdata" in mt:
            jd = msg.joint_data
            return {
                "position": list(jd.joint_q),
                "velocity": list(jd.joint_v),
                "effort": list(jd.joint_torque),
            }
        if "pose" in mt:
            # geometry_msgs/Pose|PoseStamped|PoseWithCovariance(Stamped)、
            # nav_msgs/Odometry：position/orientation 是消息定义的命名字段
            # （识别码），平移/姿态天然分离——修复此前 Pose 消息返回空字典、
            # 整条通道无数值检测的缺口
            pose = getattr(msg, "pose", msg)   # Stamped → .pose；Pose → msg
            pos = getattr(pose, "position", None)
            ori = getattr(pose, "orientation", None)
            if pos is not None and ori is not None:
                return {
                    "position": [pos.x, pos.y, pos.z],
                    "orientation": [ori.x, ori.y, ori.z, ori.w],
                }
    except Exception:
        return {}
    return {}


class RosBagReader(DataReader):
    """ROS bag 读取器（ros1 .bag / ros2 .db3）

    open() 仅做一次 header 级遍历（rosbags 不解序列化），得到每个 topic 的
    计数与时间轴。payload 不缓存，避免大 bag 内存膨胀。
    """

    def __init__(self):
        super().__init__()
        self._reader = None
        self._conn_map = {}

    def open(self, path: str) -> DataInfo:
        from rosbags.highlevel import AnyReader

        self._path = path
        path = self._resolve_data_file(path, (".bag", ".db3")) or path
        bag_path = Path(path)
        if not bag_path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._format = 'ros2_bag' if bag_path.suffix.lower() == '.db3' else 'ros1_bag'

        reader = AnyReader([bag_path])
        reader.open()
        self._reader = reader
        self._conn_map = {c.topic: c for c in reader.connections}

        topics: Dict[str, TopicInfo] = {}
        start_time = float('inf')
        end_time = float('-inf')

        for conn, ts, _raw in reader.messages():
            sec = ts * 1e-9
            name = conn.topic
            ti = topics.get(name)
            if ti is None:
                ti = TopicInfo(name=name, msg_type=conn.msgtype, count=0,
                               modality=self.classify(name, conn.msgtype))
                topics[name] = ti
            ti.count += 1
            if ti.timestamps is None:
                ti.timestamps = []
            ti.timestamps.append(sec)
            if sec < start_time:
                start_time = sec
            if sec > end_time:
                end_time = sec

        for name, ti in topics.items():
            ti.receipt_times = list(ti.timestamps)
            if ti.modality in _NUMERIC_MODALITIES:
                ti.value_available = True
            elif ti.modality == MODALITY_IMAGE and "camerainfo" not in (ti.msg_type or "").lower():
                # 图像内容通道：可读压缩帧（CompressedImage/CompressedDepth 或原始 Image）
                ti.image_available = True
                lc = (ti.msg_type or "").lower()
                ti.image_compressed = "compressed" in lc

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0

        self._info = DataInfo(
            path=path,
            format=self._format,
            topics=topics,
            duration=end - start,
            start_time=start,
            end_time=end,
        )
        return self._info

    def _read_raw(self, name: str, indices: Optional[Set[int]] = None) -> Iterator[Tuple[int, bytes]]:
        """流式读取指定通道的原始字节（可选按索引过滤）"""
        from ._parsers.ros_msg import parse_message  # noqa: F401  (校验可解析性)
        conn = self._conn_map.get(name)
        if conn is None or self._reader is None:
            return
        idx = 0
        for c, _ts, raw in self._reader.messages(connections=[conn]):
            if indices is None or idx in indices:
                yield idx, raw
            idx += 1

    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        if self._reader is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.value_available:
            return None
        from ._parsers.ros_msg import parse_message
        target = set(indices)
        out = [{}] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}
        remaining = len(target)
        if remaining == 0:
            return out
        idx = 0
        conn = self._conn_map.get(name)
        for c, _ts, raw in self._reader.messages(connections=[conn]):
            if idx in target:
                try:
                    msg = parse_message(raw, ti.msg_type)
                except Exception:
                    msg = None  # 单条半截/损坏消息：跳过该条，不中断整段数值检测
                out[pos[idx]] = ros_values_to_dict(msg, ti.msg_type) if msg is not None else {}
                remaining -= 1
                if remaining <= 0:
                    break
            idx += 1
        return out

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        if self._reader is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.value_available:
            return None
        from ._parsers.ros_msg import parse_message

        def gen():
            conn = self._conn_map.get(name)
            for _c, _ts, raw in self._reader.messages(connections=[conn]):
                try:
                    msg = parse_message(raw, ti.msg_type)
                except Exception:
                    msg = None  # 单条半截/损坏消息：跳过该条，不中断整段数值检测
                yield ros_values_to_dict(msg, ti.msg_type) if msg is not None else {}
        return gen()

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        if self._reader is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        from ._parsers.ros_msg import compressed_image_head_tail, raw_image_head_tail

        conn = self._conn_map.get(name)
        if conn is None:
            return None
        lc = (ti.msg_type or "").lower()
        is_compressed = "compressed" in lc

        def gen():
            idx = 0
            for _c, _ts, raw in self._reader.messages(connections=[conn]):
                try:
                    if is_compressed:
                        n, head, tail = compressed_image_head_tail(raw)
                    else:
                        n, head, tail = raw_image_head_tail(raw)
                except Exception:
                    n, head, tail = 0, b"", b""
                yield idx, n, head, tail
                idx += 1
        return gen()

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        if self._reader is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        from ._parsers.ros_msg import compressed_image_bytes, raw_image_bytes, raw_image_arr

        conn = self._conn_map.get(name)
        if conn is None:
            return None
        lc = (ti.msg_type or "").lower()
        is_compressed = "compressed" in lc

        target = set(indices)
        if not target:
            return []
        out = [None] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}
        remaining = len(target)
        idx = 0
        for _c, _ts, raw in self._reader.messages(connections=[conn]):
            if idx in target:
                try:
                    if is_compressed:
                        data = compressed_image_bytes(raw)
                        out[pos[idx]] = decode_image_bytes(data)
                    else:
                        out[pos[idx]] = raw_image_arr(raw)
                except Exception:
                    out[pos[idx]] = None
                remaining -= 1
                if remaining <= 0:
                    break
            idx += 1
        return out

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        if self._reader is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        lc = (ti.msg_type or "").lower()
        is_compressed = "compressed" in lc
        from ._parsers.ros_msg import compressed_image_bytes, raw_image_arr
        conn = self._conn_map.get(name)
        if conn is None:
            return None

        def gen():
            idx = 0
            for _c, ts, raw in self._reader.messages(connections=[conn]):
                try:
                    if is_compressed:
                        data = compressed_image_bytes(raw)
                    else:
                        data = raw_image_arr(raw)
                except Exception:
                    data = b"" if is_compressed else None
                if data is not None:
                    yield idx, ts * 1e-9, data
                idx += 1
        return gen()

    def collect_content(self, numeric: Set[str], image: Set[str],
                        pixel_indices: Dict[str, List[int]]) -> Optional[dict]:
        """单遍遍历 bag，同时收集数值内容、图像统计与采样像素（免反复遍历）"""
        if self._reader is None:
            return None
        from ._parsers.ros_msg import (
            parse_message, compressed_image_head_tail, raw_image_head_tail,
            compressed_image_bytes, raw_image_bytes, raw_image_arr,
        )

        plan: Dict[str, dict] = {}
        for name in numeric:
            plan[name] = {"values": []}
        for name in image:
            plan[name] = {"stats": [], "pixels": {}}
        if not plan:
            return plan

        px_target = {name: set(v) for name, v in pixel_indices.items()}
        for conn, ts, raw in self._reader.messages():
            entry = plan.get(conn.topic)
            if entry is None:
                continue
            ti = self._channel(conn.topic)
            if ti is None:
                continue
            if "values" in entry:
                msg = parse_message(raw, ti.msg_type)
                entry["values"].append(
                    ros_values_to_dict(msg, ti.msg_type) if msg is not None else {})
            else:
                lc = (ti.msg_type or "").lower()
                comp = "compressed" in lc
                try:
                    if comp:
                        n, head, tail = compressed_image_head_tail(raw)
                    else:
                        n, head, tail = raw_image_head_tail(raw)
                except Exception:
                    n, head, tail = 0, b"", b""
                idx = len(entry["stats"])
                entry["stats"].append((idx, n, head, tail))
                if idx in px_target.get(conn.topic, set()):
                    try:
                        if comp:
                            data = compressed_image_bytes(raw)
                            entry["pixels"][idx] = decode_image_bytes(data)
                        else:
                            entry["pixels"][idx] = raw_image_arr(raw)
                    except Exception:
                        entry["pixels"][idx] = None
        return plan

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        if self._reader is None:
            return
        for conn, ts, raw in self._reader.messages():
            if topic_filter and conn.topic not in topic_filter:
                continue
            yield RawMessage(topic=conn.topic, msg_type=conn.msgtype,
                             timestamp=ts * 1e-9, raw=raw)

    def close(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None
