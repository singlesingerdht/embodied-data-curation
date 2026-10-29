"""MCAP 读取器（含自定义 protobuf profile）"""
import struct
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
import numpy as np
from ..ir import (
    DataInfo, TopicInfo, RawMessage,
    MODALITY_IMAGE, MODALITY_POSE, _NUMERIC_MODALITIES, decode_image_bytes,
)
from .base import DataReader
from .protobuf import _PROTOBUF_SCHEMA_MODALITY, _build_protobuf_class, _protobuf_to_values


def _image_schema_kind(sch) -> Optional[str]:
    """foxglove 图像 schema 类型：'raw'（RawImage 原始像素）/ 'compressed'
    （CompressedImage，data 字段为 JPEG/PNG 等编码字节）/ 非图像 None"""
    n = getattr(sch, "name", "") or ""
    if n == "foxglove.RawImage":
        return "raw"
    if n == "foxglove.CompressedImage":
        return "compressed"
    return None

class McapReader(DataReader):
    """MCAP 读取器

    - 时间轴：通过 MessageIndex（索引记录）轻量获取，不解压 chunk。
    - 数值内容：用 mcap 内嵌的 protobuf schema（FileDescriptorSet）动态解码
      RobotState / ServoJoint / ServoEffector，供关节/指令内容检测
      （解码时才解压对应 chunk，按需发生）。
    - 图像（foxglove.RawImage，raw 像素）：暂不做内容检测。
    """

    def __init__(self):
        super().__init__()
        self._f = None
        self._channel_of = {}
        self._msg_classes: Dict[str, Any] = {}
        self._schema_of: Dict[str, Any] = {}   # topic -> schema 对象
        self._chunk_indexes: List[Any] = []
        self._chunk_msg_counts: List[Dict[int, int]] = []  # 每 chunk: {channel_id: count}
        self._name_to_id: Dict[str, int] = {}

    def open(self, path: str) -> DataInfo:
        from mcap.reader import make_reader

        self._path = path
        path = self._resolve_data_file(path, (".mcap",)) or path
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._format = 'mcap'

        f = open(p, 'rb')
        self._f = f
        reader = make_reader(f)
        summary = reader.get_summary()
        if summary is None:
            # 无 summary 段（流式 writer / 中断写入，规范允许）：线性扫描构建
            # 通道/计数/时间轴，避免 get_summary() 返回 None 后解引用崩溃
            self._scan_mode = True
            channels = {}
            schemas = {}
            counts: Dict[int, int] = {}
            ts_lists: Dict[int, List[float]] = {}
            f.seek(0)
            for _sch, _ch, _msg in reader.iter_messages():
                if _ch.id not in channels:
                    channels[_ch.id] = _ch
                    counts[_ch.id] = 0
                if _sch is not None and _sch.id not in schemas:
                    schemas[_sch.id] = _sch
                counts[_ch.id] += 1
                ts_lists.setdefault(_ch.id, []).append(_msg.log_time * 1e-9)
            chunk_indexes = []
            start_time = float('inf')
            end_time = float('-inf')
            for _cid, _ts in ts_lists.items():
                start_time = min(start_time, min(_ts))
                end_time = max(end_time, max(_ts))
        else:
            self._scan_mode = False
            channels = summary.channels
            schemas = summary.schemas
            chunk_indexes = list(summary.chunk_indexes)
            ts_lists = {}
            start_time = float('inf')
            end_time = float('-inf')
        self._channel_of = {cid: ch for cid, ch in channels.items()}
        # 统计段可能缺失（异常 mcap）或 channel_message_counts 为 None：兜底空 dict
        if summary is not None:
            counts = (summary.statistics.channel_message_counts
                      if summary.statistics and summary.statistics.channel_message_counts
                      else {})

        # 动态构建 protobuf 消息类
        self._msg_classes = {}
        self._schema_of = {}
        for sid, sch in schemas.items():
            if sch.name not in self._msg_classes and sch.encoding == 'protobuf' and sch.data:
                try:
                    self._msg_classes[sch.name] = _build_protobuf_class(sch.data, sch.name)
                except Exception:
                    pass
        for cid, ch in channels.items():
            sch = schemas.get(ch.schema_id)
            if sch is not None:
                self._schema_of[ch.topic] = sch

        topics: Dict[str, TopicInfo] = {}
        for cid, ch in channels.items():
            name = ch.topic
            modality = _PROTOBUF_SCHEMA_MODALITY.get(
                self._schema_of.get(name).name if name in self._schema_of else "",
                self.classify(name, ch.message_encoding),
            )
            value_ok = (modality in _NUMERIC_MODALITIES
                        and name in self._schema_of
                        and self._schema_of[name].name in self._msg_classes)
            is_raw_image = modality == MODALITY_IMAGE and self._schema_of.get(name) \
                and self._schema_of[name].name == "foxglove.RawImage"
            is_comp_image = modality == MODALITY_IMAGE and self._schema_of.get(name) \
                and self._schema_of[name].name == "foxglove.CompressedImage"
            topics[name] = TopicInfo(
                name=name,
                msg_type=self._schema_of[name].name if name in self._schema_of else ch.message_encoding,
                count=int(counts.get(cid, 0)),
                modality=modality,
                value_available=value_ok,
                image_available=is_raw_image or is_comp_image,
                image_compressed=True if is_comp_image else (False if is_raw_image else None),
                source=ch.message_encoding,
            )

        # 虚拟 pose 通道：RobotState schema 的物理通道额外注册一个
        # modality=POSE 的逻辑通道，位姿数据走 P5/P7 检测（关节走 J 族）。
        # timestamps=None → 时序/去重/同步层自动隐身（零改动）。
        for cid, ch in channels.items():
            sch = schemas.get(ch.schema_id)
            if sch is None or sch.name != "Locomotion.RobotState":
                continue
            vname = ch.topic + "/pose"
            vcount = int(counts.get(cid, 0))
            # schema shim：名字需为 "Locomotion.RobotState.pose" 以路由
            # _protobuf_to_values 新分支（iter_values 只读 sch.name）
            self._schema_of[vname] = type("_SchemaShim", (), {"name": "Locomotion.RobotState.pose"})()
            self._msg_classes["Locomotion.RobotState.pose"] = \
                self._msg_classes[sch.name]
            topics[vname] = TopicInfo(
                name=vname, msg_type="Locomotion.RobotState.pose",
                count=vcount, modality=MODALITY_POSE, value_available=True,
                image_available=False, source="virtual/pose",
            )

        # 通过 MessageIndex 取每个通道的 log_time（ns -> s），并记录每 chunk 消息数
        self._chunk_indexes = chunk_indexes
        self._chunk_msg_counts = []
        self._name_to_id = {ch.topic: cid for cid, ch in channels.items()}
        # 虚拟通道映射父通道 id（_name_to_id 重建后补加，避免被覆盖）
        for cid, ch in channels.items():
            sch = schemas.get(ch.schema_id)
            if sch is not None and sch.name == "Locomotion.RobotState":
                self._name_to_id[ch.topic + "/pose"] = cid
        for ci in self._chunk_indexes:
            cmap: Dict[int, int] = {}
            for cid, offset in ci.message_index_offsets.items():
                try:
                    f.seek(offset)
                    op, size = struct.unpack('<BQ', f.read(9))
                    if op != 0x07:
                        continue
                    payload = f.read(size)
                    _cid, rec_len = struct.unpack('<HI', payload[:6])
                    n_rec = rec_len // 16
                    cmap[cid] = n_rec
                    for i in range(n_rec):
                        lt, _o = struct.unpack('<QQ', payload[6 + 16 * i: 6 + 16 * i + 16])
                        ts_lists.setdefault(cid, []).append(lt * 1e-9)
                except Exception:
                    continue
            self._chunk_msg_counts.append(cmap)

        for cid, ch in channels.items():
            ts = ts_lists.get(cid)
            if ts:
                ts_lists[cid] = ts
                start_time = min(start_time, min(ts))
                end_time = max(end_time, max(ts))
                topics[ch.topic].timestamps = ts

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0
        self._info = DataInfo(
            path=path, format='mcap', topics=topics,
            duration=end - start, start_time=start, end_time=end,
        )
        return self._info

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        if self._f is None:
            return None
        ti = self._channel(name)
        sch = self._schema_of.get(name)
        if ti is None or not ti.value_available or sch is None:
            return None
        cls = self._msg_classes.get(sch.name)
        if cls is None:
            return None
        chan_id = self._name_to_id.get(name)

        def gen():
            import io as _io
            if self._scan_mode:
                # 无 summary：线性扫描消息流（每次调用从头读，正确性优先）
                from mcap.reader import make_reader as _mr
                self._f.seek(0)
                rd = _mr(self._f)
                for _sch, _ch, _msg in rd.iter_messages():
                    if _ch.id != chan_id:
                        continue
                    try:
                        m = cls()
                        m.ParseFromString(_msg.data)
                        yield _protobuf_to_values(sch.name, m)
                    except Exception:
                        yield {}
                return
            from mcap.records import Chunk, Message
            from mcap.data_stream import ReadDataStream
            from mcap.stream_reader import breakup_chunk
            for ci in self._chunk_indexes:
                if chan_id not in ci.message_index_offsets:
                    continue
                try:
                    self._f.seek(ci.chunk_start_offset + 1 + 8, _io.SEEK_SET)
                    chunk = Chunk.read(ReadDataStream(self._f))
                    for record in breakup_chunk(chunk, validate_crc=False):
                        if isinstance(record, Message) and record.channel_id == chan_id:
                            try:
                                m = cls()
                                m.ParseFromString(record.data)
                                yield _protobuf_to_values(sch.name, m)
                            except Exception:
                                yield {}
                except Exception:
                    continue  # 损坏 chunk：跳过，其余继续（与其它格式一致）
        return gen()

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        if self._f is None:
            return None
        ti = self._channel(name)
        sch = self._schema_of.get(name)
        kind = _image_schema_kind(sch)
        if ti is None or not ti.image_available or kind is None:
            return None
        # CompressedImage 的 Tier-1 尺寸启发式需要真实图像字节（protobuf data 字段），
        # 无法解析则放弃该检查（返回 None → CheckResult 显式 skipped）
        comp_cls = None
        if kind == "compressed":
            comp_cls = self._msg_classes.get(sch.name)
            if comp_cls is None:
                return None
        chan_id = self._name_to_id.get(name)

        def _img_payload(record_data: bytes):
            """CompressedImage → 编码字节；RawImage → 原始 protobuf 字节（仅供冻结指纹）"""
            if kind == "compressed":
                try:
                    m = comp_cls()
                    m.ParseFromString(record_data)
                    return bytes(m.data)
                except Exception:
                    return None
            return record_data

        def gen():
            import io as _io
            if self._scan_mode:
                from mcap.reader import make_reader as _mr
                self._f.seek(0)
                rd = _mr(self._f)
                ci = 0
                for _sch, _ch, _msg in rd.iter_messages():
                    if _ch.id == chan_id:
                        payload = _img_payload(_msg.data)
                        if payload is not None and len(payload) > 0:
                            yield ci, len(payload), payload[:4096], payload[-16:]
                        ci += 1
                return
            from mcap.records import Chunk, Message
            from mcap.data_stream import ReadDataStream
            from mcap.stream_reader import breakup_chunk
            ci = 0
            for cix in self._chunk_indexes:
                if chan_id not in cix.message_index_offsets:
                    continue
                try:
                    self._f.seek(cix.chunk_start_offset + 1 + 8, _io.SEEK_SET)
                    chunk = Chunk.read(ReadDataStream(self._f))
                    for record in breakup_chunk(chunk, validate_crc=False):
                        if isinstance(record, Message) and record.channel_id == chan_id:
                            payload = _img_payload(record.data)
                            if payload is not None and len(payload) > 0:
                                yield ci, len(payload), payload[:4096], payload[-16:]
                            ci += 1
                except Exception:
                    continue  # 损坏 chunk：跳过
        return gen()

    def _rawimage_to_arr(self, m) -> Optional[np.ndarray]:
        """foxglove.RawImage 原始像素 -> RGB 数组（免解码）"""
        enc = (m.encoding or "").lower()
        if enc in ("rgb8", "rgba8", "rgb", "bgr8"):
            bpp = 4 if enc == "rgba8" else 3
            arr = np.frombuffer(m.data, dtype=np.uint8).reshape(m.height, m.width, bpp)[:, :, :3]
            if enc == "bgr8":
                arr = arr[:, :, ::-1]
            return arr.copy()
        return None

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        if self._f is None:
            return None
        ti = self._channel(name)
        sch = self._schema_of.get(name)
        kind = _image_schema_kind(sch)
        if ti is None or kind is None:
            return None
        cls = self._msg_classes.get(sch.name)
        if cls is None:
            return None
        chan_id = self._name_to_id.get(name)
        if chan_id is None or not self._chunk_msg_counts:
            return None

        target = sorted(set(indices))
        if not target:
            return []
        out = [None] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}

        # 目标帧 -> 所在 chunk（按通道消息计数累计定位）
        chunk_of: Dict[int, List[int]] = {}
        run = 0
        for ci, cmap in enumerate(self._chunk_msg_counts):
            c = cmap.get(chan_id, 0)
            for idx in target:
                if idx < run:
                    continue
                if idx < run + c:
                    chunk_of.setdefault(ci, []).append(idx)
            run += c

        # 只解压含目标帧的 chunk
        import io as _io
        from mcap.records import Chunk, Message
        from mcap.data_stream import ReadDataStream
        from mcap.stream_reader import breakup_chunk

        for ci, idxs in sorted(chunk_of.items()):
            need = {i for i in idxs}
            cidx = self._chunk_indexes[ci]
            try:
                self._f.seek(cidx.chunk_start_offset + 1 + 8, _io.SEEK_SET)
                chunk = Chunk.read(ReadDataStream(self._f))
                base = 0
                for record in breakup_chunk(chunk, validate_crc=False):
                    if not isinstance(record, Message) or record.channel_id != chan_id:
                        continue
                    if base in need:
                        try:
                            m = cls()
                            m.ParseFromString(record.data)
                            if kind == "compressed":
                                out[pos[base]] = decode_image_bytes(bytes(m.data))
                            else:
                                out[pos[base]] = self._rawimage_to_arr(m)
                        except Exception:
                            out[pos[base]] = None
                    base += 1
            except Exception:
                pass
        return out

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        if self._f is None:
            return None
        ti = self._channel(name)
        sch = self._schema_of.get(name)
        kind = _image_schema_kind(sch)
        if ti is None or not ti.image_available or kind is None:
            return None
        cls = self._msg_classes.get(sch.name)
        if cls is None:
            return None
        chan_id = self._name_to_id.get(name)
        ts_list = ti.timestamps or []

        def gen():
            import io as _io
            from mcap.records import Chunk, Message
            from mcap.data_stream import ReadDataStream
            from mcap.stream_reader import breakup_chunk
            ci = 0
            for cix in self._chunk_indexes:
                if chan_id not in cix.message_index_offsets:
                    continue
                try:
                    self._f.seek(cix.chunk_start_offset + 1 + 8, _io.SEEK_SET)
                    chunk = Chunk.read(ReadDataStream(self._f))
                    for record in breakup_chunk(chunk, validate_crc=False):
                        if isinstance(record, Message) and record.channel_id == chan_id:
                            payload = None
                            try:
                                m = cls()
                                m.ParseFromString(record.data)
                                if kind == "compressed":
                                    payload = bytes(m.data)   # JPEG/PNG 编码字节
                                else:
                                    payload = self._rawimage_to_arr(m)  # RGB ndarray
                            except Exception:
                                payload = None
                            t = ts_list[ci] if ci < len(ts_list) else (record.log_time * 1e-9)
                            yield ci, t, payload
                            ci += 1
                except Exception:
                    continue
        return gen()

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        if self._f is None:
            return
        from mcap.reader import make_reader
        self._f.seek(0)
        reader = make_reader(self._f)
        name_of = {cid: ch.topic for cid, ch in self._channel_of.items()}
        for schema, channel, msg in reader.iter_messages(topics=list(topic_filter or name_of.values())):
            yield RawMessage(topic=channel.topic, msg_type=channel.message_encoding,
                             timestamp=msg.log_time * 1e-9, raw=msg.data)

    def close(self):
        if self._f is not None:
            self._f.close()
            self._f = None
