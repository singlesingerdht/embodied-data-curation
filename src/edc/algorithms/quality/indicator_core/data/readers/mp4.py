"""MP4 读取器（PyAV demux，仅包级 pts，不解码）"""
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
import numpy as np
from ..ir import DataInfo, TopicInfo, RawMessage, MODALITY_IMAGE, MODALITY_OTHER
from .base import DataReader

class Mp4Reader(DataReader):
    """MP4 读取器

    通过 PyAV demux 获取各流（视频/音频）的帧时间轴（pts）与流元数据，
    不做像素解码，开销极低。
    """

    def __init__(self):
        super().__init__()
        self._container = None

    def open(self, path: str) -> DataInfo:
        import av
        path = self._resolve_data_file(path, (".mp4",)) or path
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._format = 'mp4'
        self._path = path
        self._container = av.open(str(p))

        topics: Dict[str, TopicInfo] = {}
        start_time = float('inf')
        end_time = float('-inf')

        for s in self._container.streams:
            name = s.type  # video / audio / subtitle
            pts_list = []
            for pkt in self._container.demux(s):
                if pkt.pts is not None:
                    pts_list.append(pkt.pts)
            if not pts_list:
                continue
            tb = s.time_base
            ts = [float(pt * tb) for pt in pts_list]
            width = s.codec_context.width if s.codec_context else None
            height = s.codec_context.height if s.codec_context else None
            modality = MODALITY_IMAGE if s.type == 'video' else MODALITY_OTHER
            fps = float(s.average_rate) if s.average_rate else None
            is_video = s.type == 'video'
            topics[name] = TopicInfo(
                name=name, msg_type=s.codec_context.name if s.codec_context else s.type,
                count=len(ts), modality=modality, value_available=False,
                image_available=is_video, image_compressed=True if is_video else None,
                timestamps=ts, seqs=list(range(len(ts))),
                source=f"fps={fps}, {width}x{height}" if width else f"fps={fps}",
            )
            if ts:
                start_time = min(start_time, min(ts))
                end_time = max(end_time, max(ts))

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0
        self._info = DataInfo(
            path=path, format='mp4', topics=topics,
            duration=end - start, start_time=start, end_time=end,
        )
        return self._info

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        if self._container is None:
            return
        for s in self._container.streams:
            if topic_filter and s.type not in topic_filter:
                continue
            for pkt in self._container.demux(s):
                t = float(pkt.pts * s.time_base) if pkt.pts is not None else 0.0
                yield RawMessage(topic=s.type, msg_type=s.codec_context.name if s.codec_context else s.type,
                                 timestamp=t, raw=b'')

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        if self._container is None or name != 'video':
            return None
        self._container.seek(0)

        def gen():
            idx = 0
            for s in self._container.streams:
                if s.type != 'video':
                    continue
                for pkt in self._container.demux(s):
                    b = bytes(pkt)
                    yield idx, len(b), b[:4096], b[-16:]
                    idx += 1
        return gen()

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        if self._container is None or name != 'video':
            return None
        target = set(indices)
        if not target:
            return []
        maxidx = max(target)
        self._container.seek(0)
        out = [None] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}
        ci = 0
        for s in self._container.streams:
            if s.type != 'video':
                continue
            for frame in self._container.decode(video=0):
                if ci in target:
                    try:
                        out[pos[ci]] = np.asarray(frame.to_ndarray(format="rgb24"))
                    except Exception:
                        out[pos[ci]] = None
                ci += 1
                if ci > maxidx:
                    break
        return out

    def image_bytes(self, name: str):
        """逐帧解码（灰度 ndarray），供全帧像素管线。

        帧间编码包尺寸∝运动残差而非内容熵（Tier-1 尺寸启发式对其跳过），
        黑/白屏/模糊/遮挡/冻结等像素级检测由全帧管线覆盖。
        """
        if self._container is None or name != 'video':
            return None
        ts = (self._info.topics.get(name).timestamps
              if self._info and name in self._info.topics else None) or []

        def gen():
            self._container.seek(0)
            idx = 0
            for s in self._container.streams:
                if s.type != 'video':
                    continue
                try:
                    for frame in self._container.decode(video=0):
                        try:
                            arr = frame.to_ndarray(format="gray")
                        except Exception:
                            arr = None
                        t = ts[idx] if idx < len(ts) else 0.0
                        yield idx, t, arr
                        idx += 1
                except Exception:
                    continue
        return gen()

    def close(self):
        if self._container is not None:
            self._container.close()
            self._container = None
