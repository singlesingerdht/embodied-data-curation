"""DataReader 基类: 统一入口 + 工厂 + require 取数层。

统一数据读取接口的内部实现。
"""
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from edc.data.ir.metadata import (
    DataInfo, TopicInfo, IRNotProvided,
    detect_format, FORMAT_MP4, FORMAT_EPISODE, FORMAT_DATASET, FORMAT_ROS1_BAG, FORMAT_ROS2_BAG,
    FORMAT_MCAP, FORMAT_PARQUET, FORMAT_HDF5, FORMAT_LEROBOT, MODALITY_POSE,
)
import numpy as np


class DependencyMissing(Exception):
    """格式所需的第三方库未安装。"""
    pass


class DataReader(ABC):
    """格式适配器基类。

    生命周期: create(path) → open(path) → DataInfo → (按需取数) → close()
    """

    @abstractmethod
    def open(self, path: str) -> DataInfo:
        """只读元数据 + 时间轴, 不加载 payload。"""

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    # ── 工厂 ──

    @classmethod
    def create(cls, path: str, hints: Optional[Dict] = None) -> "DataReader":
        """格式探测 → 具体 Reader 子类 (延迟 import + 依赖检查)。"""
        fmt = detect_format(path)
        if fmt == FORMAT_MP4:
            from .mp4 import MP4Reader
            return MP4Reader(hints=hints)
        if fmt == FORMAT_EPISODE:
            from .episode import EpisodeReader
            return EpisodeReader(hints=hints)
        if fmt == FORMAT_DATASET:
            from .dataset import DatasetReader
            return DatasetReader(hints=hints)
        if fmt in (FORMAT_ROS1_BAG, FORMAT_ROS2_BAG):
            try:
                from .rosbag import RosBagReader
            except ImportError:
                raise DependencyMissing("rosbag 格式需要安装 rosbags: pip install rosbags")
            return RosBagReader(hints=hints)
        if fmt == FORMAT_MCAP:
            try:
                from .mcap import McapReader
            except ImportError:
                raise DependencyMissing("mcap 格式需要安装 mcap: pip install mcap")
            return McapReader(hints=hints)
        if fmt == FORMAT_PARQUET:
            try:
                from .parquet import ParquetReader
            except ImportError:
                raise DependencyMissing("parquet 格式需要安装 pyarrow: pip install pyarrow")
            return ParquetReader(hints=hints)
        if fmt == FORMAT_HDF5:
            try:
                from .hdf5 import Hdf5Reader
            except ImportError:
                raise DependencyMissing("hdf5 格式需要安装 h5py: pip install h5py")
            return Hdf5Reader(hints=hints)
        if fmt == FORMAT_LEROBOT:
            try:
                from .lerobot import LerobotReader
            except ImportError:
                raise DependencyMissing("lerobot 格式需要安装 pyarrow: pip install pyarrow")
            return LerobotReader(hints=hints)
        raise ValueError(f"不支持的格式: {fmt} (路径: {path})")

    # ── 内容 API (可选, 默认 None 表示不支持) ──

    def channel_values(self, name: str, indices: Optional[List[int]] = None) -> Optional[List[dict]]:
        """按索引批量取规范值字典。默认 None = 不支持。"""
        return None

    def materialize_image_channel(self, info: DataInfo, name: str,
                                  output_path: Optional[str] = None) -> str:
        """Return a file-backed video for an image channel.

        File-video readers return their source directly. Container readers
        override this method to decode embedded frames on demand.
        """
        ti = info.topics.get(name)
        if ti is None or not ti.image_available:
            raise IRNotProvided(f"image 通道 {name}", info.format,
                                info.capability_summary())
        ext = os.path.splitext(ti.source or "")[1].lower()
        if os.path.isfile(ti.source or "") and ext in (".mp4", ".avi", ".mov", ".mkv"):
            return ti.source
        raise IRNotProvided(f"可物化的视频通道 {name}", info.format,
                            "图像时间轴存在，但当前 reader 未提供帧解码")

    def image_storage(self, name):
        """Describe native storage without materializing or decoding a video."""
        if name in getattr(self, 'video_sources', {}):
            return 'video'
        if name in getattr(self, 'frame_loaders', {}):
            return 'frames'
        info = getattr(self, '_info', None) or getattr(self, 'info', None)
        topic = info.topics.get(name) if info is not None else None
        source = getattr(topic, 'source', None)
        if source and os.path.isfile(source) and os.path.splitext(source)[1].lower() in ('.mp4', '.avi', '.mov', '.mkv'):
            return 'video'
        return 'unknown'

    def iter_frames(self, name):
        from edc.data.readers.video import video_frames
        info = getattr(self, "_info", None)
        if info is None:
            raise ValueError("Reader must be opened before reading frames")
        yield from video_frames(self.materialize_image_channel(info, name))

    # ── require 层: 算法声明式取数 ──

    def require_pose(self, info: DataInfo, side: str = "right",
                     field: str = "position") -> Tuple[str, TopicInfo]:
        """返回 (channel_name, TopicInfo) 指定侧的 pose 通道, 含指定物理量。

        side: "left" / "right" / "any"
        field: "position" (笛卡尔 XYZ) / "orientation" (显式声明的姿态表示)
        """
        candidates = [(n, ti) for n, ti in info.topics.items()
                       if ti.modality == MODALITY_POSE and ti.value_available
                       and (ti.fields is None or field in ti.fields)]
        if not candidates:
            all_fields = sorted(set(f for ti in info.topics.values()
                                   for f in (ti.fields or []) if ti.modality == MODALITY_POSE))
            raise IRNotProvided(
                f"pose 通道 (side={side}, field={field})", info.format,
                f"可用 fields={all_fields}" if all_fields else info.capability_summary()
            )
        if side != "any":
            if side not in {"left", "right"}:
                raise ValueError(f"Invalid side: {side}")
            for n, ti in candidates:
                declared = getattr(ti, "side", "unknown")
                if declared == side or (declared == "unknown" and not getattr(ti, 'strict_side', False) and side in n.lower()):
                    return n, ti
            raise IRNotProvided(f"pose 通道 (side={side}, field={field})", info.format,
                                "No channel with the requested side; opposite-side fallback is forbidden")
        return candidates[0]

    def require_timestamps(self, info: DataInfo, min_channels: int = 2) -> Dict[str, List[float]]:
        """返回 {topic: [timestamps]} 多通道时间戳。

        Raises IRNotProvided: 不足 min_channels 个通道有时间戳。
        """
        ts_map = extract_timestamps(info)
        if len(ts_map) < min_channels:
            raise IRNotProvided(
                f">={min_channels} 通道时间戳", info.format,
                info.capability_summary()
            )
        return ts_map

    def read_field(self, info: DataInfo, name: str, field: str) -> Optional[np.ndarray]:
        """统一提取: 从通道 name 取物理量 field → N×D numpy 数组。

        必须提供声明字段且行数完整；不丢弃缺失行，不用其它物理量替代。
        """
        ti = info.topics.get(name)
        if ti is None or not ti.value_available or field not in (ti.fields or []):
            return None
        values = self.channel_values(name)
        if values is None:
            return None
        if len(values) != ti.count:
            raise ValueError(f"{name}: missing rows ({len(values)} != {ti.count})")
        if any(not isinstance(row, dict) or field not in row or row[field] is None for row in values):
            raise ValueError(f"{name}.{field}: missing field; cannot drop or substitute rows")
        # Source reads retain integer/float precision and depth dtype. Only the
        # execution unit transform may create floating-point physical values.
        array = np.asarray([row[field] for row in values])
        if array.dtype.kind not in 'bfiu':
            raise ValueError(f'{name}.{field}: expected numeric source values, got {array.dtype}')
        if field == 'orientation':
            metadata = getattr(ti, 'field_metadata', {}).get(field, {})
            representation = dict(metadata.get('representation') or getattr(ti, 'representation', {}))
            if 'unit' not in representation:
                unit = metadata.get('unit', getattr(ti, 'unit', None))
                if unit is not None:
                    representation['unit'] = unit
            if representation.get('kind') in {'quaternion', 'rpy', 'rotation_matrix'}:
                from edc.data.transforms.orientation import sample_shape
                expected = sample_shape(representation)
                if array.shape[1:] == expected:
                    return array  # Already a separate field; never re-slice a quaternion/matrix.
                if ti.pose_layout is not None:
                    sliced = self._slice_pose_layout(ti, array, field)
                    if sliced is not None and sliced.shape[1:] == expected:
                        return sliced
                raise ValueError(f'{name}.{field}: declared orientation shape {expected}, got {array.shape[1:]}')
        # ROS decoding already separates XYZ and XYZW. A quaternion-only array
        # must not enter the layout inference for a flattened XYZ+quaternion row.
        # The message schema establishes storage structure, never source units.
        message_type = (ti.msg_type or "").replace("/msg/", "/")
        separate_pose_types = {"geometry_msgs/Pose", "geometry_msgs/PoseStamped",
                               "geometry_msgs/PoseWithCovariance",
                               "geometry_msgs/PoseWithCovarianceStamped",
                               "nav_msgs/Odometry"}
        if (ti.modality == MODALITY_POSE and message_type in separate_pose_types
                and field in {"position", "orientation"}):
            width = 3 if field == "position" else 4
            if array.ndim != 2 or array.shape[1] != width:
                raise ValueError(f"{name}.{field}: decoded ROS pose requires {width} components, "
                                 f"got shape {array.shape}")
            return array
        return self._slice_pose_layout(ti, array, field)

    def _slice_pose_layout(self, ti, arr: np.ndarray, field: str) -> Optional[np.ndarray]:
        """pose 位姿布局切片: profile 声明优先, 否则四元数路标推断 (仅 D>3)。"""
        import warnings
        if ti.modality != MODALITY_POSE or arr.ndim != 2 or field not in {"position", "orientation"}:
            return arr
        D = arr.shape[1]
        if D <= 3:
            return arr  # D==3: 结构性, 整列即位置
        layout = ti.pose_layout
        # 原始声明 (来自 profile) 需解析
        if layout is not None and "method" not in layout:
            from edc.data.ir.pose_layout import resolve_declared
            layout = resolve_declared(layout, D)
            ti.pose_layout = layout
        # 未声明 → 推断
        if layout is None:
            from edc.data.ir.pose_layout import infer_pose_layout
            layout = infer_pose_layout(arr)
            ti.pose_layout = layout  # 缓存 (含 unknown)
        if layout is None:
            return arr  # shape 异常, 不拦截
        method = layout.get("method", "unknown")
        if method == "unknown":
            warnings.warn(
                f"pose 布局推断失败 ({ti.name}): {layout.get('reason', '未知')}; "
                f"可在 profile yaml 声明该通道的 pose_layout",
                stacklevel=3
            )
            return None  # 推不出 → 不返回不可靠数据 (调用方降级)
        sl = layout.get(field)
        if sl is None:
            return None  # 该 field 不存在 (如 orientation 无四元数)
        return arr[:, sl[0]:sl[1]]


# ── 模块级 API ──


def extract_timestamps(info: DataInfo) -> Dict[str, List[float]]:
    """从 DataInfo.topics 提取 {topic: [timestamps]}, 过滤 NaN/±inf。"""
    import numpy as np
    ts_map = {}
    for name, ti in info.topics.items():
        if ti.timestamps is None:
            continue
        ts = [float(t) for t in ti.timestamps if np.isfinite(t)]
        if ts:
            ts_map[name] = ts
    return ts_map


def make_synthetic_report(module_name: str, module_key: str, info: DataInfo) -> dict:
    """合成时间戳 → 降级报告 (时间类检测不适用)。"""
    exc = IRNotProvided(
        f"合成时间戳 (i/fps, 全通道同轴)",
        info.format, "时间类检测 (Sync/Dedup/Check_Timestamp) 仅在真实采集时间戳上有意义"
    )
    return make_ir_report(module_name, module_key, exc)


def filter_real_timestamps(info: DataInfo, ts_map: dict) -> tuple:
    """剔除合成时间戳通道, 返回 (filtered_ts_map, has_synthetic)。
    如果 filtered_ts_map 为空 → 全合成, 返回 (None, True)。
    """
    real = {}
    for name, ts in ts_map.items():
        ti = info.topics.get(name)
        if ti and ti.timestamps_synthetic:
            continue
        real[name] = ts
    return (real if real else None, len(real) < len(ts_map))


def make_ir_report(module_name: str, module_key: str, exc: Exception) -> dict:
    """IRNotProvided / DependencyMissing → 标准降级报告 (统一输出契约格式)。

    IRNotProvided   (结构性不适用, 格式缺模态/同轴/合成时间戳) → NOT_APPLICABLE
    DependencyMissing (环境问题, pip install 可修)                → NEEDS_REVIEW
    两者均 score=None, passed=True, 不计入数据不合格。
    """
    from edc.algorithms.quality.shared.attribution import format_report

    reason = str(exc)
    ir_info = {}
    if isinstance(exc, IRNotProvided):
        ir_info = {"need": exc.need, "format": exc.fmt, "available": exc.available}
        verdict = "NOT_APPLICABLE"
        reason_kind = "IRNotProvided"
    elif isinstance(exc, DependencyMissing):
        ir_info = {"dependency_missing": str(exc)}
        verdict = "NEEDS_REVIEW"
        reason_kind = "DependencyMissing"
    else:
        verdict = "NEEDS_REVIEW"
        reason_kind = type(exc).__name__

    sub = {
        "name": "IR 可用性", "weight": 1.0,
        "passed": None, "score": None, "gap": 0.0,
        "weighted_gap": 0.0,
        "raw_value": reason, "direction": "binary",
        "t_min": 1.0, "t_max": 0.0, "current_value": 0.0,
        "details": [], "grade": "N/A",
    }
    module = {
        "name": module_name, "module_key": module_key, "max_score": 100,
        "total_score": 0, "passed": True,
        "sub_indicators": [sub], "warnings": [],
        "grade": "N/A",
        "threshold_profile": {},
    }
    report = format_report(module, threshold=85.0)
    report["score"] = None
    report["grade"] = "N/A"
    report["passed"] = True
    report["verdict"] = verdict
    report["reasons"] = [f"{reason_kind}: {reason}"]
    report["ir_unavailable"] = ir_info
    return report
