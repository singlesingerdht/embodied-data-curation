"""IR 核心契约: Message / TopicInfo / DataInfo + 格式探测 + 模态分类 + 时间归一化。

参考: dayan-rough-datachecking src/datafilter/data/ir.py (main 分支)
设计原则: 能力位解耦 (value_available / image_available / image_compressed)
"""
import os
import json
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

# ── 模态常量 ──
MODALITY_IMAGE = "image"
MODALITY_IMU = "imu"
MODALITY_JOINT_STATE = "joint_state"
MODALITY_CMD = "cmd"
MODALITY_POSE = "pose"
MODALITY_OTHER = "other"

# ── 支持的格式 ──
FORMAT_MP4 = "mp4"
FORMAT_EPISODE = "episode"
FORMAT_DATASET = "dataset"
FORMAT_JSON = "json"
FORMAT_ROS1_BAG = "ros1_bag"
FORMAT_ROS2_BAG = "ros2_bag"
FORMAT_MCAP = "mcap"
FORMAT_PARQUET = "parquet"
FORMAT_HDF5 = "hdf5"
FORMAT_LEROBOT = "lerobot"
FORMAT_UNKNOWN = "unknown"

SUPPORTED_VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv"}
SUPPORTED_DATA_EXT = {".bag", ".db3", ".mcap", ".parquet", ".pq", ".h5", ".hdf5", ".bvh", ".jsonl", ".csv"}

_FORMAT_PRIORITY = {
    FORMAT_LEROBOT: 0, FORMAT_PARQUET: 0,
    FORMAT_HDF5: 1,
    FORMAT_ROS1_BAG: 2, FORMAT_ROS2_BAG: 2, FORMAT_MCAP: 2,
    FORMAT_MP4: 3, FORMAT_EPISODE: 3,
    "bvh": 3,
    FORMAT_DATASET: 4,
}


class IRNotProvided(Exception):
    """IR 层无法提供算法所需的数据类型。

    Attributes:
        need: 算法需要什么 (e.g. "pose 通道" / "多通道时间戳")
        fmt: 实际的输入格式
        available: 该格式能提供什么
    """

    def __init__(self, need: str, fmt: str = "", available: str = ""):
        self.need = need
        self.fmt = fmt
        self.available = available
        msg = f"IR 层未提供 {need}"
        if fmt:
            msg += f" (格式 {fmt}"
            if available:
                msg += f" 仅支持 {available}"
            msg += ")"
        super().__init__(msg)


@dataclass
class Message:
    """单条消息的元数据 (不解 payload)。"""
    topic: str
    msg_type: str = ""
    timestamp: float = 0.0
    seq: Optional[int] = None


@dataclass
class TopicInfo:
    """通道契约: 检测消费的最小单元。

    能力位:
      value_available  — 能否提供数值字典 (joint/imu/pose 的 position 等)
      image_available  — 能否提供图像帧
      image_compressed — True=压缩域有效 / False=原始帧 / None=未知
    """
    name: str
    msg_type: str = ""
    count: int = 0
    modality: str = MODALITY_OTHER
    value_available: bool = False
    image_available: bool = False
    image_compressed: Optional[bool] = None
    timestamps: Optional[List[float]] = None
    seqs: Optional[List[int]] = None
    source: str = ""
    time_source: str = "unknown"
    acquisition_timestamps: Optional[List[float]] = None
    timestamps_synthetic: bool = False
    fields: Optional[List[str]] = None  # 通道能产出的物理量清单 (position/velocity/effort/ang_vel/...)
    pose_layout: Optional[Dict] = None  # 位姿布局推断结果 (profile 声明 or 四元数路标 推断)
    container_timestamps_ns: Optional[List[int]] = None
    acquisition_timestamps_ns: Optional[List[Optional[int]]] = None
    recorded_timestamp_values: Optional[List[Any]] = None
    recorded_timestamp_unit: Optional[str] = None
    time_evidence: Optional[Dict] = None
    frame_evidence: Optional[Dict] = None

    def capability_summary(self) -> str:
        caps = []
        if self.value_available:
            caps.append("数值")
        if self.image_available:
            caps.append("图像")
        if self.timestamps is not None:
            caps.append("时间戳")
        if self.seqs is not None:
            caps.append("序列号")
        if self.fields:
            caps.append(f"fields={self.fields}")
        return ", ".join(caps) if caps else "无"


@dataclass
class DataInfo:
    """源契约: reader.open() 的返回值。"""
    path: str
    format: str = FORMAT_UNKNOWN
    duration: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0
    topics: Dict[str, TopicInfo] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def capability_summary(self) -> str:
        if not self.topics:
            return "无通道"
        caps = set()
        for ti in self.topics.values():
            caps.add(ti.capability_summary())
        return " / ".join(sorted(caps))


# ── 模态分类 ──

_EXCLUDE_TOPICS = {"camera_info", "metadata", "pointcloud", "point_cloud", "tf", "tf_static"}


def classify_channel(name: str, msg_type: str = "",
                     hints: Optional[Dict[str, str]] = None) -> str:
    """通道名 + ROS 类型启发式 → 模态。

    hints (fnmatch 通配) 优先级最高, 用于 profile 覆盖。
    """
    if hints:
        for pattern, modality in hints.items():
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(msg_type, pattern):
                return modality

    # A declared Odometry schema is stronger than a misleading topic spelling.
    if msg_type in {'nav_msgs/msg/Odometry', 'nav_msgs/Odometry'}:
        return 'odometry'

    lower = name.lower()
    parts = lower.split("/")

    for ex in _EXCLUDE_TOPICS:
        if ex in parts:
            return MODALITY_OTHER

    if any(k in lower for k in ("image", "compressedimage", "color", "rgb", "depth")) \
            and "camera_info" not in parts:
        return MODALITY_IMAGE
    if "imu" in lower or "accel" in lower or "gyro" in lower:
        return MODALITY_IMU
    if "joint" in lower and "state" in lower:
        return MODALITY_JOINT_STATE
    if any(k in lower for k in ("cmd", "command", "control", "action", "servo", "target")):
        return MODALITY_CMD
    if any(k in lower for k in ("pose", "position", "eef", "end_pose")):
        return MODALITY_POSE

    # ROS 类型推断
    mt = msg_type.lower()
    if "image" in mt or "compressedimage" in mt:
        return MODALITY_IMAGE
    if "imu" in mt:
        return MODALITY_IMU
    if "jointstate" in mt:
        return MODALITY_JOINT_STATE
    return MODALITY_OTHER


# ── 格式探测 ──

def detect_format(path: str) -> str:
    """内容级格式探测: 扩展名 + 目录结构 + 内容嗅探。"""
    p = Path(path)

    # 单文件
    if p.is_file():
        ext = p.suffix.lower()
        if ext in SUPPORTED_VIDEO_EXT:
            return FORMAT_MP4
        if ext == ".json":
            return FORMAT_JSON
        if ext == ".bag":
            return FORMAT_ROS1_BAG
        if ext == ".db3":
            return FORMAT_ROS2_BAG
        if ext == ".mcap":
            return FORMAT_MCAP
        if ext in (".h5", ".hdf5"):
            return FORMAT_HDF5
        if ext in (".parquet", ".pq"):
            if _lerobot_like_parquet(p):
                return FORMAT_LEROBOT
            return FORMAT_PARQUET
        if ext in SUPPORTED_DATA_EXT:
            return ext.lstrip(".")
        return FORMAT_UNKNOWN

    # 目录
    if p.is_dir():
        return _detect_dir_format(p)

    return FORMAT_UNKNOWN


def _detect_dir_format(p: Path) -> str:
    """目录格式探测: episode / lerobot / parquet / hdf5 / rosbag / mcap / dataset / mp4。"""
    if (p / "metadata.yaml").is_file():
        return FORMAT_ROS2_BAG
    entries = [e for e in p.iterdir()
               if not e.name.startswith(".") and e.name != "__pycache__"
               and e.name not in ("__MACOSX",)]

    files = [e for e in entries if e.is_file()]
    dirs = [e for e in entries if e.is_dir()]
    d_ext = [f.suffix.lower() for f in files]

    has_pq = any(e in (".parquet", ".pq") for e in d_ext)
    has_h5 = any(e in (".h5", ".hdf5") for e in d_ext)
    has_bag = ".bag" in d_ext
    has_db3 = ".db3" in d_ext
    has_mcap = ".mcap" in d_ext
    has_mp4 = any(e in SUPPORTED_VIDEO_EXT for e in d_ext)

    # 1) parquet 目录: lerobot or parquet
    if has_pq:
        pq_file = next(f for f in files if f.suffix.lower() in (".parquet", ".pq"))
        if _lerobot_like_parquet(pq_file):
            return FORMAT_LEROBOT
        return FORMAT_PARQUET

    # 2) hdf5
    if has_h5:
        return FORMAT_HDF5

    # 3) rosbag / mcap
    if has_bag:
        return FORMAT_ROS1_BAG
    if has_db3:
        return FORMAT_ROS2_BAG
    if has_mcap:
        return FORMAT_MCAP

    # 4) episode (quanta_x1/microwave)
    json_files = [f for f in files if f.suffix.lower() == ".json"]
    if json_files and has_mp4:
        for jf in json_files:
            if _is_episode_json(jf):
                return FORMAT_EPISODE

    # 5) 纯 mp4 目录
    if has_mp4 and not json_files:
        return FORMAT_MP4

    # 6) 折叠式数据集: 子目录都是 episode
    episode_dirs = [d for d in dirs if _is_episode_dir(d)]
    if episode_dirs:
        return FORMAT_DATASET

    # 7) 折叠式数据集: 子目录含数据文件 (lerobot/parquet/hdf5/bag/mcap)
    inner_formats = [_detect_inner(d) for d in dirs if not d.name.startswith(".")]
    inner = _priority_format(inner_formats)
    if inner:
        return inner if inner != FORMAT_EPISODE else FORMAT_DATASET

    # 8) 单个 mp4
    if has_mp4 and not dirs:
        return FORMAT_MP4

    return FORMAT_UNKNOWN


def _detect_inner(d: Path):
    """递归探测子目录内的数据格式。"""
    if not d.is_dir():
        return None
    try:
        files = [x for x in d.iterdir() if x.is_file()
                 and not x.name.startswith("._") and x.name != ".DS_Store"]
    except Exception:
        return None
    exts = [f.suffix.lower() for f in files]
    if any(e in (".parquet", ".pq") for e in exts):
        pf = next(f for f in files if f.suffix.lower() in (".parquet", ".pq"))
        return FORMAT_LEROBOT if _lerobot_like_parquet(pf) else FORMAT_PARQUET
    if any(e in (".h5", ".hdf5") for e in exts):
        return FORMAT_HDF5
    if ".bag" in exts:
        return FORMAT_ROS1_BAG
    if ".db3" in exts:
        return FORMAT_ROS2_BAG
    if ".mcap" in exts:
        return FORMAT_MCAP
    if ".mp4" in exts:
        return FORMAT_MP4
    for sub in d.iterdir():
        if sub.is_dir() and not sub.name.startswith("."):
            r = _detect_inner(sub)
            if r:
                return r
    return None


def _priority_format(inners) -> Optional[str]:
    """从多个候选内部格式中按优先级取最高。"""
    best, rank = None, 99
    for f in inners:
        r = _FORMAT_PRIORITY.get(f or "", 99)
        if r < rank:
            best, rank = f, r
    return best


def _lerobot_like_parquet(path: Path) -> bool:
    """parquet 文件列结构是否为 lerobot 单集。"""
    try:
        import pyarrow.parquet as pq
        sch = pq.read_schema(str(path))
        cols = [sch.field(i).name for i in range(len(sch))]
        return _lerobot_like_columns(cols)
    except Exception:
        return False


def _lerobot_like_columns(cols) -> bool:
    """lerobot 列特征: 数据列≥2 且含 state/observation.*/lerobot 三件套之一。"""
    meta_keys = {"timestamp", "timestamp_utc", "frame_index", "episode_index",
                 "task_index", "index", "episode_data_index", "task"}
    data = [c for c in cols if c.lower() not in meta_keys]
    low = {c.lower() for c in cols}
    return (len(data) >= 2
            and (any(c in ("state", "observation.state") for c in low)
                 or any(c.startswith("observation.") for c in low)
                 or {"frame_index", "episode_index", "task_index"} <= low))


def _is_episode_dir(d: Path) -> bool:
    """判断目录是否为 episode (含 data[].json + mp4)。"""
    try:
        json_files = list(d.glob("*.json"))
        mp4_files = list(d.glob("*.mp4"))
        if json_files and mp4_files:
            return _is_episode_json(json_files[0])
    except (OSError, PermissionError):
        pass
    return False


def _is_episode_json(path: Path) -> bool:
    """嗅探 JSON 是否为 episode 轨迹格式 (含 data[] + follow_*/joint 字段)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(4096)
        data = json.loads(head) if len(head) < 4096 else None
        if data is None:
            # 大文件: 只看头部
            if '"data"' in head and ("follow_" in head or "joint_pos" in head):
                return True
            return False
        if "data" not in data or not isinstance(data["data"], list):
            return False
        if not data["data"]:
            return False
        first = data["data"][0] if isinstance(data["data"][0], dict) else {}
        keys = set(first.keys())
        if any(k.startswith("follow_") for k in keys):
            return True
        if any("joint" in k for k in keys):
            return True
        return False
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False


# ── 时间归一化 ──

def normalize_time_seconds(values) -> "Any":
    """ns/us/ms/s 量级自动归一到秒 (numpy 数组)。

    判定逻辑: 如果中位数绝对值 >> 1e10 → 纳秒; >> 1e7 → 微秒; >> 1e4 → 毫秒。
    """
    import numpy as np
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    med = float(np.median(np.abs(arr)))
    if med > 1e10:
        return arr * 1e-9
    if med > 1e7:
        return arr * 1e-6
    if med > 1e4:
        return arr * 1e-3
    return arr
