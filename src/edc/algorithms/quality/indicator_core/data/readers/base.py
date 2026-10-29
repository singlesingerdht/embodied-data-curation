"""DataReader 抽象基类与统一入口（格式适配器契约）"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
from ..ir import (
    DataInfo, TopicInfo, RawMessage, classify_channel, detect_format,
    _column_role_heuristic,
)

class DataReader(ABC):
    """数据读取器抽象基类

    统一约定：
    - open() 只读元数据 + 时间轴（轻量、一次遍历），不加载 payload。
    - 时间轴 / seq / receipt_time 在 open 后缓存，供 Timing/Sync/Dedup 使用。
    - channel_values / iter_values 按需提供数值内容（ContentFilter 使用），
      返回「规范值字典」：joint_state -> {position, velocity, effort}
                              imu        -> {ang_vel, lin_acc, orientation}
                              cmd        -> {joint_q, joint_v, tau}
      不支持时返回 None / 空。
    """

    def __init__(self):
        self._path: Optional[str] = None
        self._format: str = "unknown"
        self._info: Optional[DataInfo] = None
        self._hints: Optional[Dict[str, str]] = None
        self._column_roles: Dict[str, List[str]] = {}  # {"time": [...], "seq": [...], "meta": [...]}

    def classify(self, name: str, msg_type: str = "") -> str:
        """用规范 IR 的分类器（带 profile 提示）判断通道模态"""
        return classify_channel(name, msg_type, hints=self._hints)

    # ---- 列角色判定（profile 优先，启发式兜底）----
    def is_time_column(self, name: str) -> bool:
        if name in self._column_roles.get("time", []):
            return True
        return _column_role_heuristic("time", name)

    def is_seq_column(self, name: str) -> bool:
        if name in self._column_roles.get("seq", []):
            return True
        return _column_role_heuristic("seq", name)

    def is_meta_column(self, name: str) -> bool:
        if name in self._column_roles.get("meta", []):
            return True
        return _column_role_heuristic("meta", name)

    @abstractmethod
    def open(self, path: str) -> DataInfo:
        """打开数据源，返回元数据"""
        ...

    @abstractmethod
    def close(self):
        """关闭数据源"""
        ...

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ---- 元数据 ----
    @property
    def path(self) -> str:
        return self._path or ""

    @property
    def format(self) -> str:
        return self._format

    def info(self) -> DataInfo:
        return self._info

    # ---- 时间轴（open 后可用）----
    def channel_timestamps(self, name: str) -> Optional[List[float]]:
        ti = self._channel(name)
        return ti.timestamps if ti else None

    def channel_seqs(self, name: str) -> Optional[List[int]]:
        ti = self._channel(name)
        return ti.seqs if ti else None

    def channel_receipt_times(self, name: str) -> Optional[List[float]]:
        ti = self._channel(name)
        return ti.receipt_times if ti else None

    # ---- 数值内容（ContentFilter 用，按需提供）----
    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        """按索引取规范值字典；不支持返回 None"""
        return None

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        """流式遍历规范值字典；不支持返回 None"""
        return None

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        """流式遍历压缩图像帧，产出 (index, size, head, tail)

        head/tail 只取字节块的首尾片段（默认各 4096/16 字节），不物化全帧，
        供第一层压缩域检测（黑/白/模糊/冻结/码流完整性）使用，零像素解码。
        不支持返回 None。
        """
        return None

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        """按索引返回解码后的 RGB 图像（numpy），供第二层采样解码检测。

        只解码采样帧，不落盘。不支持返回 None。
        """
        return None

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        """流式遍历全帧图像数据，产出 (index, timestamp, payload)，供全帧管线使用。

        payload 为 bytes（JPEG/PNG 编码字节）或 np.ndarray（原始像素数组，免解码）。
        不支持时返回 None → ContentFilter 回退 image_pixels 采样路径。
        """
        return None

    def collect_content(self, numeric: Set[str], image: Set[str],
                         pixel_indices: Dict[str, List[int]]) -> Optional[dict]:
        """单遍收集内容检测所需数据，避免多路反复遍历。

        返回 {"values": {ch: [dict]}, "stats": {ch: [(idx,size,head,tail)]},
              "pixels": {ch: {idx: ndarray}}}；不支持返回 None。
        """
        return None

    # ---- 兼容：原始消息流（流式，不缓存）----
    @abstractmethod
    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        """迭代原始消息（流式，不缓存全部）"""
        ...

    def _channel(self, name: str) -> Optional[TopicInfo]:
        if self._info and name in self._info.topics:
            return self._info.topics[name]
        return None

    @classmethod
    def detect_format(cls, path: str) -> str:
        return detect_format(path)

    @classmethod
    def _resolve_data_file(cls, path: str, exts: tuple) -> Optional[str]:
        """目录输入时解析到其中的数据文件（取第一个，跳过垃圾）。"""
        p = Path(path)
        if p.is_file():
            return str(p)
        if not p.is_dir():
            return None
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in exts \
                    and not f.name.startswith("._") and f.name != ".DS_Store":
                return str(f)
        return None

    @classmethod
    def create(cls, path: str, hints: Optional[Dict[str, str]] = None,
               column_roles: Optional[Dict[str, List[str]]] = None) -> 'DataReader':
        """根据格式自动选择读取器，可选注入模态提示（变体 profile）与列角色。"""
        from .rosbag import RosBagReader
        from .mcap import McapReader
        from .parquet import ParquetReader
        from .hdf5 import Hdf5Reader
        from .lerobot import LerobotReader
        from .dataset import DatasetReader
        from .mp4 import Mp4Reader
        from .bvh import BvhReader
        from ..ir import is_folded_dataset

        # 折叠式聚合数据集优先选 DatasetReader（format 由 reader 探测内部真实格式）
        if is_folded_dataset(path):
            r = DatasetReader()
        else:
            fmt = detect_format(path)
            if fmt in ("ros1_bag", "ros2_bag"):
                r = RosBagReader()
            elif fmt == "mcap":
                r = McapReader()
            elif fmt == "parquet":
                r = ParquetReader()
            elif fmt == "hdf5":
                r = Hdf5Reader()
            elif fmt == "lerobot":
                r = LerobotReader()
            elif fmt == "mp4":
                r = Mp4Reader()
            elif fmt == "bvh":
                r = BvhReader()
            else:
                raise ValueError(f"不支持的文件格式: {path} (格式: {fmt})")
        r._hints = hints
        r._column_roles = column_roles or {}
        return r

def read_data(path: str, hints: Optional[Dict[str, str]] = None) -> DataInfo:
    """统一入口：自动检测格式并读取数据源信息（可选注入模态提示）"""
    reader = DataReader.create(path, hints=hints)
    try:
        return reader.open(path)
    finally:
        reader.close()

def extract_timestamps(info: DataInfo) -> Dict[str, List[float]]:
    """从 DataInfo 中提取各通道的相对 timestamp 序列。

    只过滤 NaN/Inf，保留 0 与负值：0 时间戳首帧是合法数据，不应被丢弃；
    全 0/全负通道交给 TimingFilter 的 timestamp_validity 上报（S3），
    而不是在提取阶段静默消失。
    """
    ref = info.start_time
    out = {}
    for topic, ti in info.topics.items():
        seq = ti.timestamps if ti.timestamps else             ([m.timestamp for m in ti.messages] if ti.messages else None)
        if seq is None:
            continue
        out[topic] = [t - ref for t in seq
                      if t == t and t not in (float("inf"), float("-inf"))]
    return out
