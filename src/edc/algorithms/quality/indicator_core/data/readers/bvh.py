"""BVH 读取器（骨骼动捕文本格式）"""
from pathlib import Path
from typing import List, Optional, Iterator, Set
from ..ir import DataInfo, TopicInfo, RawMessage, MODALITY_JOINT_STATE
from .base import DataReader

class BvhReader(DataReader):
    """BVH 读取器

    解析 HIERARCHY（骨骼通道）与 Motion（帧数据）；每帧一个通道，时间轴按
    Frame Time 重建。参考用，数值检测受限（通道语义为旋转量）。
    """

    def __init__(self):
        super().__init__()
        self._channels: List[str] = []
        self._frame_time: float = 0.0

    def open(self, path: str) -> DataInfo:
        path = self._resolve_data_file(path, (".bvh",)) or path
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._format = 'bvh'
        self._path = path

        text = p.read_text(encoding='utf-8', errors='replace')
        lines = text.splitlines()
        frame_time = 0.0166667
        frame_count = 0
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith('Frame Time:'):
                try:
                    frame_time = float(s.split(':')[1].strip())
                except Exception:
                    pass
            elif s.startswith('Frames:'):
                try:
                    frame_count = int(s.split(':')[1].strip())
                except Exception:
                    pass

        ts = [frame_time * i for i in range(frame_count)] if frame_count else []
        topics = {
            "motion": TopicInfo(
                name="motion", msg_type="bvh/Motion", count=frame_count,
                modality=MODALITY_JOINT_STATE, value_available=True,
                timestamps=ts, seqs=list(range(frame_count)),
                source=f"frame_time={frame_time:.4f}s",
            )
        }
        start = ts[0] if ts else 0.0
        end = ts[-1] if ts else 0.0
        self._info = DataInfo(
            path=path, format='bvh', topics=topics,
            duration=end - start, start_time=start, end_time=end,
        )
        return self._info

    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        p = Path(self._path)
        text = p.read_text(encoding='utf-8', errors='replace')
        lines = text.splitlines()
        values = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s and s != 'Motion' and not s.startswith(('HIERARCHY', 'JOINT', 'ROOT',
                                                          'OFFSET', 'CHANNELS', 'End', '}')
                                                       ) and (s[0].isdigit() or s[0] == '-'):
                try:
                    nums = [float(x) for x in s.split()]
                    if len(nums) > 1:
                        values.append(nums)
                except Exception:
                    pass
        out = []
        for i in indices:
            if 0 <= i < len(values):
                out.append({"position": values[i]})
            else:
                out.append({})
        return out

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        if topic_filter and 'motion' not in topic_filter:
            return
        for i in range(self._info.topics['motion'].count):
            yield RawMessage(topic='motion', msg_type='bvh/Motion',
                             timestamp=self._info.topics['motion'].timestamps[i], raw=b'')

    def close(self):
        pass
