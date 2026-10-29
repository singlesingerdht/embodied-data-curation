"""规范 IR（规范数据契约）

通道信息、规范模态、格式检测、时间/图像工具。
"""
import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import numpy as np

MODALITY_IMAGE = "image"
MODALITY_IMU = "imu"
MODALITY_JOINT_STATE = "joint_state"
MODALITY_CMD = "cmd"
MODALITY_POSE = "pose"
MODALITY_ENCODED = "encoded"   # 值被压缩/编码，无法直接做数值检测
MODALITY_OTHER = "other"

_NUMERIC_MODALITIES = (MODALITY_IMU, MODALITY_JOINT_STATE, MODALITY_CMD, MODALITY_POSE)


# ======================================================================
# 统一数据结构
# ======================================================================

@dataclass
class Message:
    """消息元数据（不解 payload，兼容旧接口）。"""
    topic: str                # 通道名
    msg_type: str             # 原生消息类型
    timestamp: float          # 消息时间戳（秒）
    receipt_time: float       # 接收时间（秒）
    seq: Optional[int] = None # 序号（可选）


@dataclass
class TopicInfo:
    """通道信息（规范 IR，检测消费的统一通道契约）。

    - modality: 规范模态（image/imu/joint_state/cmd/pose/encoded/other）
    - timestamps: 相对时间轴（秒，可选）
    - seqs: 序号序列（可选）
    - value_available: 是否能提供数值内容（ContentFilter 数值层用）
    - image_available: 是否能提供图像帧（ContentFilter 图像层1 用）
    - image_compressed: 图像是否压缩（True=压缩域尺寸特征有效；False=原始帧）
    """
    name: str                 # 通道名（topic/列名/路径）
    msg_type: str             # 原生消息类型（ROS 类型/pyarrow 列类型/hdf5 路径）
    count: int                # 消息数/行数
    modality: str = MODALITY_OTHER  # 规范模态（classify_channel 判定，可被 profile modality_hints 覆盖）
    value_available: bool = False   # 是否可提供数值值字典（数值层检测前提）
    image_available: bool = False   # 是否可提供图像帧（图像层1 前提）
    image_compressed: Optional[bool] = None  # 图像是否压缩：True=压缩域特征有效 / False=原始帧 / None=未知
    messages: List[Message] = field(default_factory=list)  # 消息元数据（旧接口兼容）
    timestamps: Optional[List[float]] = None  # 时间轴（相对秒，绝对值经 start_time 相对化）
    seqs: Optional[List[int]] = None  # 序号序列
    receipt_times: Optional[List[float]] = None  # 接收时间（延迟检测 C8/M5 用）
    source: str = ""          # 原生来源描述（topic / 列名 / dataset 路径 / 流）


@dataclass
class RawMessage:
    """原始消息（含 payload 字节，兼容旧接口）。"""
    topic: str                # 通道名
    msg_type: str             # 原生消息类型
    timestamp: float          # 消息时间戳（秒）
    raw: bytes                # 原始 payload 字节


@dataclass
class DataInfo:
    """统一数据源信息（reader.open() 产出，检测消费的源契约）。"""
    path: str                 # 输入路径
    format: str               # 真实格式：ros1_bag|ros2_bag|mcap|parquet|hdf5|lerobot|mp4|bvh|dataset
    topics: Dict[str, TopicInfo] = field(default_factory=dict)  # 通道契约（键=通道名）
    duration: float = 0.0     # 时长（秒，end-start）
    start_time: float = 0.0   # 时间轴起点（秒，绝对）
    end_time: float = 0.0     # 时间轴终点（秒，绝对）
    extra: Dict[str, Any] = field(default_factory=dict)  # 源专属：episodes/schema_drift/fps/format_spec/video_channels 等


# ======================================================================
# 模态分类（命名 + 消息类型启发式，可被 profile modality_hints 覆盖）
# ======================================================================

def classify_channel(name: str, msg_type: str = "", hints: Optional[Dict[str, str]] = None) -> str:
    """把通道名 / 消息类型映射到规范模态

    Args:
        name: 通道名（topic / 列名 / dataset 路径）
        msg_type: 原生消息类型（可选）。仅当它看起来是 ROS 消息类型时才参与判定，
                  避免 pyarrow 列类型字符串（如含 struct 字段名）造成误判。
        hints: 显式注册表（通道名 -> 模态，支持 * 通配），优先于启发式

    Returns:
        规范模态字符串
    """
    if hints:
        if name in hints:
            return hints[name]
        for pat, mod in hints.items():
            if fnmatch.fnmatch(name, pat):
                return mod

    n = name.lower()
    t = (msg_type or "").lower()
    is_ros = ("/msg/" in t or t.endswith("/msg")
              or t.startswith(("sensor_msgs", "kuavo_msgs", "tf2_msgs",
                               "geometry_msgs", "std_msgs")))

    # ---- 相机内参元数据（非图像）----
    if "camera_info" in n or "camerainfo" in n:
        return MODALITY_OTHER

    # ---- 点云/激光（非图像）：显式排除出图像规则（camera/depth 名可能误导，
    # 如 /camera/depth/points(PointCloud2)；业务无点云数据时也保持防御）----
    if ("pointcloud" in t or "point_cloud" in t or "laserscan" in t
            or "laser_scan" in t or n.endswith(("/points", "_points")) or n == "points"):
        return MODALITY_OTHER

    # ---- 图像类 ----
    if ((is_ros and ("image" in t or "compressedimage" in t))
            or "/images/" in n or "/image/" in n
            or n.endswith(("/color", "/depth"))
            or n in ("image",) or n.endswith(("_image", "/image"))
            or ("image" in n and ("compressed" in n or "raw" in n or "camera" in n))
            or ("camera" in n and ("image" in n or "color" in n or "depth" in n))):
        return MODALITY_IMAGE

    # ---- IMU（仅按名字，避免列类型误判）----
    if "imu" in n:
        return MODALITY_IMU

    # ---- 控制指令（先于 joint，因为 JointCmd 含 joint）----
    if ((is_ros and ("jointcmd" in t or "command" in t or "control" in t))
            or "/action/" in n or n == "action" or n.endswith("/action")
            or "cmd" in n or "command" in n or "control" in n or "controller" in n
            or "target" in n or "servo" in n or "tauff" in n
            or "hand_control" in n or "xhand_control" in n):
        return MODALITY_CMD

    # ---- 相机位姿（外参，非末端位姿）：不参与位姿检测 ----
    # 相机安装外参随头部/躯干运动变化，不是腕部位姿；曾按 POSE 参与
    # P 族检测会产出"头部运动"伪 jerk，且其时序标签应与历史基线一致（C 族回退）
    if "camera_pose" in n or ("camera" in n and "pose" in n):
        return MODALITY_OTHER

    # ---- 位姿类型优先（geometry_msgs/PoseStamped 等）----
    # 消息类型语义强于名字启发：/processed/left_hand_pose 是末端位姿流，
    # 不应被 joint 规则的 "hand" 关键字截获（曾判 joint_state 走 J 族）
    if is_ros and ("posestamped" in t or "posewithcovariance" in t
                   or t.endswith("/pose")):
        return MODALITY_POSE

    # ---- 关节状态 ----
    if ((is_ros and ("jointstate" in t or "sensorsdata" in t or "joint" in t))
            or "joint" in n or "state" in n or "hand" in n):
        return MODALITY_JOINT_STATE

    # ---- 位姿 ----
    if "pose" in n or "position" in n:
        return MODALITY_POSE

    return MODALITY_OTHER


# ======================================================================
# 格式检测
# ======================================================================

_FORMAT_PRIORITY = {
    "lerobot": 0, "parquet": 0,      # 结构化轨迹最高
    "hdf5": 1,
    "ros1_bag": 2, "ros2_bag": 2, "mcap": 2,
    "mp4": 3, "bvh": 3,              # 单媒体最低
    "dataset": 4,
}


def _priority_format(inners) -> Optional[str]:
    """从多个候选内部格式中按优先级取最高（A1-2）。

    目录输入下子目录枚举顺序不定，videos/ 等含 mp4 的子目录若先被枚举会遮蔽
    lerobot/parquet 数据集根——聚合全部候选后按格式优先级取最高而非首个。
    """
    best, rank = None, 99
    for f in inners:
        r = _FORMAT_PRIORITY.get(f or "", 99)
        if r < rank:
            best, rank = f, r
    return best


def detect_format(path: str) -> str:
    """根据内容探测格式（不依赖文件名，只看目录里的文件类型与列结构）"""
    p = Path(path)

    if p.is_dir():
        direct = [x for x in p.iterdir() if x.is_file()
                  and not x.name.startswith("._") and x.name != ".DS_Store"
                  and x.name not in ("__MACOSX",)]
        subdirs = [d for d in p.iterdir() if d.is_dir()
                   and d.name not in ("__MACOSX",) and not d.name.startswith(".")]
        d_ext = [f.suffix.lower() for f in direct]
        has_pq = any(e in (".parquet", ".pq") for e in d_ext)
        has_h5 = any(e in (".h5", ".hdf5") for e in d_ext)

        # 1) 直接含 parquet：多列文件=lerobot（每文件一集），单列=parquet 文件夹（单集分表）
        if has_pq:
            pf = next((f for f in direct if f.suffix.lower() in (".parquet", ".pq")), None)
            return _classify_parquet_dir(p, pf)
        # 2) 直接含 hdf5：hdf5 数据集（可多文件）
        if has_h5:
            return "hdf5"
        # 3) 直接含 bag/mcap/mp4/bvh
        for e, fmt in (( ".bag", "ros1_bag"), (".db3", "ros2_bag"), (".mcap", "mcap"),
                       (".mp4", "mp4"), (".bvh", "bvh")):
            if e in d_ext:
                return fmt
        # 4) 折叠式数据集根：直接含 UUID 会话目录或 episode 文件夹（fourier/zerith/luoshi 等）
        if any(_is_uuid_dir(d.name) for d in subdirs) \
                or any(_data_exist(d) for d in subdirs):
            # 聚合模式：format 返回内部真实格式（按优先级取最高，videos/mp4 不遮蔽轨迹）
            inner = _priority_format(
                _detect_inner(d) for d in subdirs
                if _is_uuid_dir(d.name) or _data_exist(d))
            return inner or "dataset"  # 兜底（探测不到时保留聚合标记）
        # 5) 子目录含数据文件（数据集根，如 dataset1/<UUID>/...）
        inner = _priority_format(_detect_inner(d) for d in subdirs)
        if inner:
            return inner
        return "unknown"

    ext = p.suffix.lower()
    if ext == ".bag":
        return "ros1_bag"
    elif ext == ".db3":
        return "ros2_bag"
    elif ext == ".mcap":
        return "mcap"
    elif ext in (".parquet", ".pq"):
        # 单 parquet 文件也按列结构判定（与目录判定同一特征函数）：
        # lerobot 单集 → lerobot；r2v2/fourier 等单文件 → parquet
        try:
            import pyarrow.parquet as pq
            sch = pq.read_schema(p)
            cols = [sch.field(i).name for i in range(len(sch))]
            if _lerobot_like_columns(cols):
                return "lerobot"
        except Exception:
            pass
        return "parquet"
    elif ext in (".h5", ".hdf5"):
        return "hdf5"
    elif ext == ".mp4":
        return "mp4"
    elif ext == ".bvh":
        return "bvh"
    return "unknown"


# ======================================================================
# 时间量纲归一化
# ======================================================================

def normalize_time_seconds(values: Any) -> np.ndarray:
    """把各种量纲的时间戳归一化到秒（ns / us / ms / s 自动识别）"""
    if len(values) == 0:
        return np.array([], dtype=np.float64)
    first = values[0] if not isinstance(values, np.ndarray) else (values[0] if values.size else None)
    if first is not None and not isinstance(first, (int, float, np.integer, np.floating)):
        # pandas Timestamp / datetime / numpy datetime64 → 纳秒
        try:
            import pandas as pd
            arr = np.asarray(pd.to_datetime(values).astype("int64"), dtype=np.float64)
        except Exception:
            try:
                arr = np.asarray([float(v) for v in values], dtype=np.float64)
            except Exception:
                arr = np.asarray([np.datetime64(v).astype("int64") / 1e9 for v in values],
                                 dtype=np.float64)
    else:
        arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return arr
    mx = float(np.nanmax(arr))
    mn = float(np.nanmin(arr))
    # 纪元时间戳（绝对量级 ≥1e12，ms 纪元 2024≈1.7e12）与相对时间轴（从 0 附近起）
    # 量级区间重叠（相对 ns 长序列可达 1e11），需区分判定：
    if mn > 1e10:        # 明显纪元量级 → 按 mx 判 ns/us/ms
        if mx > 1e17:    # 纳秒（epoch ns ≈1.6e18）
            return arr / 1e9
        if mx > 1e14:    # 微秒（epoch us ≈1.6e15；此前 1e15 误落入 ns 分支被缩 1000×）
            return arr / 1e6
        if mx > 1e11:    # 毫秒（epoch ms ≈1.6e12）
            return arr / 1e3
        return arr
    # 相对时间轴：按跨度量级判单位（含全负序列：用 |min|/span 参与判定）
    span = max(abs(mx - mn), abs(mx), abs(mn))
    if span > 1e11:
        return arr / 1e9   # ns（相对 ns 长于 100s）
    if span > 1e8:
        return arr / 1e6   # us
    if span > 1e5:
        return arr / 1e3   # ms
    return arr


def decode_image_bytes(data: bytes) -> Optional[np.ndarray]:
    """内存解码 JPEG/PNG 字节为 RGB numpy 数组（第二层用，不落盘）"""
    if not data:
        return None
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        return np.asarray(im.convert("RGB"))
    except Exception:
        return None


# ================= 列角色启发式（profile column_roles 优先，此为兜底） =================

_TIME_COLUMN_CANDIDATES = ("timestamp", "timestamp_utc", "timestamp_uhlc",
                           "user_timestamp_utc", "time", "t")
_SEQ_COLUMN_CANDIDATES = ("seq", "sequence", "frame_index", "index", "frame_idx")
_META_COLUMN_CANDIDATES = ("episode_index", "task_index", "chunk_index",
                           "episode_id", "task_id", "trace_id", "span_id",
                           "parameters", "metadata")


def _column_role_heuristic(role: str, name: str) -> bool:
    n = name.lower()
    if role == "time":
        return (n in _TIME_COLUMN_CANDIDATES or n.endswith("_timestamp")
                or n == "timestamp_uhlc")
    if role == "seq":
        return n in _SEQ_COLUMN_CANDIDATES
    if role == "meta":
        return n in _META_COLUMN_CANDIDATES or n.endswith("_index") \
            and "image" not in n
    return False


def _is_uuid_dir(name: str) -> bool:
    """判断是否为 UUID 会话目录名（32 位 hex 或 8-4-4-4-12 形式）"""
    import re
    n = name.strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", n)
                or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", n))


def _lerobot_like_columns(cols) -> bool:
    """按列内容判定是否 lerobot 单集（与目录/文件无关，纯列特征）。

    判定条件（与格式探测其余分支一致）：
    - 数据列（非 time/seq/meta）≥2，且
    - 含裸 state / observation.state，或 observation.* 前缀列，
      或 lerobot 元数据三件套（frame_index+episode_index+task_index）。
    r2v2 parquet（joint_state/xhand_state 等裸列）与 fourier 单列分表均不满足。
    """
    data = [c for c in cols
            if not _column_role_heuristic("time", c)
            and not _column_role_heuristic("seq", c)
            and not _column_role_heuristic("meta", c)]
    low = {c.lower() for c in cols}
    return (len(data) >= 2
            and (any(c in ("state", "observation.state") for c in low)
                 or any(c.startswith("observation.") for c in low)
                 or {"frame_index", "episode_index", "task_index"} <= low))


def _classify_parquet_dir(p, first_pq) -> str:
    """目录直接含 parquet 时，按首个文件列结构判定：
    - 多数据列（≥2）且呈 lerobot 特征：每文件一集 → lerobot
    - 否则（单列分表 / r2v2 裸列多列）：单集分表 → parquet
    """
    try:
        import pyarrow.parquet as pq
        sch = pq.read_schema(first_pq)
        cols = [sch.field(i).name for i in range(len(sch))]
        if _lerobot_like_columns(cols):
            return "lerobot"
    except Exception:
        pass
    return "parquet"


def _detect_inner(d) -> Optional[str]:
    """递归探测子目录内的数据格式（不依赖文件名）。"""
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
        return _classify_parquet_dir(d, pf)
    if any(e in (".h5", ".hdf5") for e in exts):
        return "hdf5"
    if any(e in (".bag",) for e in exts):
        return "ros1_bag"
    if ".db3" in exts:
        return "ros2_bag"
    if ".mcap" in exts:
        return "mcap"
    if ".mp4" in exts:
        return "mp4"
    if ".bvh" in exts:
        return "bvh"
    # 继续深入一层
    for sub in d.iterdir():
        if sub.is_dir() and sub.name not in ("__MACOSX",) and not sub.name.startswith("."):
            r = _detect_inner(sub)
            if r:
                return r
    return None


def _data_exist(d) -> bool:
    """目录直接含受支持数据文件（episode 文件夹判定）"""
    exts = (".parquet", ".pq", ".h5", ".hdf5", ".bag", ".db3", ".mcap", ".mp4", ".bvh")
    try:
        return any(f.suffix.lower() in exts for f in d.iterdir() if f.is_file())
    except Exception:
        return False


def is_folded_dataset(path: str) -> bool:
    """是否为折叠式聚合数据集（含 UUID 会话目录或 episode 文件夹）。

    聚合是 reader/处理维度，不是数据格式；真实格式由 detect_format 返回
    （探测 episode 内部文件）。本函数用于 reader 选择与内容并行拆集判断。
    """
    try:
        p = Path(path)
        if not p.is_dir():
            return False
        subdirs = [d for d in p.iterdir() if d.is_dir()
                   and not d.name.startswith(".") and d.name != "__MACOSX"]
        return bool(any(_is_uuid_dir(d.name) for d in subdirs)
                    or any(_data_exist(d) for d in subdirs))
    except Exception:
        return False
