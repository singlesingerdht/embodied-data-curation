"""通用折叠式数据集读取器（dataset root → 每集一个文件夹，含任意格式数据）

结构示例（支持任意格式的 episode 文件夹）：
    dataset1/<UUID>/episode_000000002/action.parquet, observation.state.parquet, ...   # fourier
    zerith/tasks/zerith/<UUID>/<UUID>_sub0.hdf5                                        # hdf5 多集
    <task>/<UUID>/episode.hdf5                                                          # luoshi

episode = 一个直接含数据文件（parquet/hdf5/bag/mcap/mp4）的文件夹；
数据集根含多个 episode 文件夹。本 reader 把整个数据集作为一个源：多集时间轴拼接、
内容按集聚合、事件可定位到具体 episode。每集用对应格式的子 reader 打开，
聚合只在 reader 契约（channel_values/iter_values/image_frames/image_pixels）上做。
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
import numpy as np

from ..ir import (
    DataInfo, TopicInfo, RawMessage,
)
from .base import DataReader

_DATA_EXTS = (".parquet", ".pq", ".h5", ".hdf5", ".bag", ".db3", ".mcap", ".mp4")


def has_data_files(d: Path) -> bool:
    """目录是否直接含受支持数据文件（视为一个 episode 文件夹）"""
    if not d.is_dir():
        return False
    return any(f.suffix.lower() in _DATA_EXTS for f in d.iterdir() if f.is_file())


def find_episode_dirs(root: Path) -> List[Path]:
    """递归收集数据集下的 episode 文件夹（直接含受支持数据文件的目录）。

    跳过垃圾目录（__MACOSX 等）与已被父级 episode 覆盖的深层目录。
    """
    _GARBAGE = ("__MACOSX", ".git", ".svn", "node_modules")
    found = []
    for dp, dirnames, filenames in os.walk(root):
        rel = Path(dp).name
        if rel in _GARBAGE or rel.startswith("."):
            dirnames[:] = []
            continue
        dirnames[:] = [x for x in dirnames if x not in _GARBAGE and not x.startswith(".")]
        if any(f.lower().endswith(_DATA_EXTS) for f in filenames):
            found.append(Path(dp))
            dirnames[:] = []  # 不再下钻（该目录即一个 episode）
    return sorted(found)


class DatasetReader(DataReader):
    """数据集根 = 1 个源；episode = 直接含任意格式数据文件的文件夹。

    泛化自 FourierReader：支持 hdf5/bag/mcap/mp4 等多集数据集的按集聚合，
    事件可定位到具体 episode。
    """

    def __init__(self):
        super().__init__()
        self._subs: List[Tuple[Path, DataReader, DataInfo]] = []
        self._episode_rows: List[int] = []

    def _open_episode(self, d: Path) -> DataReader:
        """按 episode 文件夹内的数据文件类型选择子 reader。"""
        from .parquet import ParquetReader
        from .hdf5 import Hdf5Reader
        from .rosbag import RosBagReader
        from .mcap import McapReader
        from .mp4 import Mp4Reader
        files = [f for f in d.iterdir() if f.is_file()
                 and f.suffix.lower() in _DATA_EXTS]
        # A2-14: 单集混合格式时按数据格式优先（parquet/hdf5 > bag/mcap > mp4/bvh），
        # 避免视频/媒体文件排首位时状态数据被忽略
        _fmt_rank = {".parquet": 0, ".pq": 0, ".h5": 1, ".hdf5": 1,
                     ".bag": 2, ".db3": 2, ".mcap": 3, ".mp4": 4}
        files.sort(key=lambda f: _fmt_rank.get(f.suffix.lower(), 9))
        ext = files[0].suffix.lower() if files else ""
        if ext in (".parquet", ".pq"):
            rd = ParquetReader()
        elif ext in (".h5", ".hdf5"):
            rd = Hdf5Reader()
        elif ext in (".bag", ".db3"):
            rd = RosBagReader()
        elif ext == ".mcap":
            rd = McapReader()
        elif ext == ".mp4":
            rd = Mp4Reader()
        else:
            raise ValueError(f"episode 文件夹无受支持数据文件: {d}")
        rd._hints = self._hints
        rd._column_roles = self._column_roles
        return rd

    @staticmethod
    def _episode_format(d: Path) -> Optional[str]:
        """探测 episode 文件夹内部真实格式（聚合是处理维度，非数据格式）"""
        _ext_fmt = {".parquet": "parquet", ".pq": "parquet", ".h5": "hdf5",
                    ".hdf5": "hdf5", ".bag": "ros1_bag", ".db3": "ros2_bag",
                    ".mcap": "mcap", ".mp4": "mp4", ".bvh": "bvh"}
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in _ext_fmt:
                return _ext_fmt[f.suffix.lower()]
        return None

    def open(self, path: str) -> DataInfo:
        root = Path(path)
        episode_dirs = find_episode_dirs(root)
        if not episode_dirs:
            raise ValueError(f"未找到含数据文件的 episode 文件夹: {path}")
        return self._aggregate(root, episode_dirs)

    def open_episodes(self, episode_dirs) -> DataInfo:
        """只聚合显式给出的 episode 目录列表（内容单元并行：每个 worker 打开自己那组）。

        与 open() 同一聚合逻辑（时间轴拼接/通道并集/缺列跳过），
        root 取首个 episode 的父目录仅用于展示路径。
        """
        dirs = [Path(d) for d in episode_dirs]
        if not dirs:
            raise ValueError("open_episodes 需要至少一个 episode 目录")
        root = dirs[0].parent
        return self._aggregate(root, dirs)

    def _aggregate(self, root: Path, episode_dirs: List[Path]) -> DataInfo:
        # 聚合是处理维度，真实格式取首个 episode 内部格式（如 hdf5/parquet）
        inner = self._episode_format(episode_dirs[0]) if episode_dirs else None
        self._format = inner or "dataset"
        self._episode_dirs = episode_dirs

        subs = []
        corrupted = []
        for d in episode_dirs:
            try:
                rd = self._open_episode(d)
                info = rd.open(str(d))
                subs.append((d, rd, info))
            except Exception as e:
                # 单集损坏不影响整体：跳过并在 extra 中记录（与 corrupted_episodes 语义一致）
                corrupted.append({"dir": str(d), "error": f"{type(e).__name__}: {e}"})
        if not subs:
            raise ValueError(f"所有 episode 均无法打开: {root}")
        self._subs = subs

        # 通道集合（各集并集；缺失集按 None/空处理）
        all_names: Set[str] = set()
        for _d, _rd, info in subs:
            all_names.update(info.topics)
        all_names = sorted(all_names)

        # 时间轴拼接（每集相对本集 0 起，按中位 dt 偏移）
        ts_map: Dict[str, List[float]] = {n: [] for n in all_names}
        start_time = float('inf')
        end_time = float('-inf')
        offset = 0.0
        episodes: List[dict] = []
        episode_rows: List[int] = []
        chan_rows: Dict[str, int] = {n: 0 for n in all_names}
        chan_offset: Dict[str, float] = {n: 0.0 for n in all_names}
        for d, _rd, info in subs:
            local = None
            for ti in info.topics.values():
                if ti.timestamps:
                    local = np.asarray(ti.timestamps, dtype=np.float64)
                    break
            rows = int(local.size) if local is not None else 0
            episodes.append({"file": d.name, "dir": str(d), "rows": rows,
                             "start_time": offset})   # 本集全局起始时间（供并行 base_time 对齐）
            episode_rows.append(rows)
            if local is not None and local.size:
                local0 = local - float(local[0])
                eps_ts = local0 + offset
                for n in all_names:
                    if n not in info.topics:
                        continue   # 某集缺某通道：跳过，保持该通道真实行数
                    ti_n = info.topics[n]
                    chan_rows[n] += ti_n.count
                    own = ti_n.timestamps
                    if own is not None and len(own) >= 2:
                        # A2-5: 每通道用自己的时间戳按各自偏移拼接——fourier 为
                        # 真多速率（图像 7Hz 822 帧 vs state/action ~28Hz 1713 帧），
                        # 共用首通道时间轴会让图像时序检测跑在错误帧数上（假掉帧）
                        own_arr = np.asarray(own, dtype=np.float64)
                        own0 = own_arr - float(own_arr[0])
                        ts_map[n].extend((own0 + chan_offset[n]).tolist())
                        d_own = np.diff(own_arr)
                        d_own = d_own[d_own > 0]
                        chan_offset[n] += (float(own0[-1])
                                           + (float(np.median(d_own)) if d_own.size else 0.1))
                    elif local is not None and local.size:
                        # 无独立时间戳：跟随主时间轴
                        ts_map[n].extend(eps_ts.tolist())
                        chan_offset[n] = offset
                start_time = min(start_time, float(eps_ts.min()))
                end_time = max(end_time, float(eps_ts.max()))
                dts = np.diff(local)
                dts = dts[dts > 0]
                offset += float(local0[-1]) + (float(np.median(dts)) if dts.size else 0.1)
        self._episode_rows = episode_rows
        self._chan_rows = chan_rows

        topics: Dict[str, TopicInfo] = {}
        for name in all_names:
            first = None
            for _d, _rd, info in subs:
                if name in info.topics:
                    first = info.topics[name]
                    break
            typ = first.msg_type if first else "unknown"
            ts = ts_map.get(name)
            # 模态：子 reader（含 HDF5_FORMAT.md 映射）优先，否则 classify
            modality = first.modality if first else self.classify(name, str(typ))
            value_ok = bool(first.value_available) if first else False
            image_ok = bool(first.image_available) if first else False
            topics[name] = TopicInfo(
                name=name, msg_type=str(typ),
                count=chan_rows.get(name, len(ts) if ts else 0),  # A2-5: 真实行数
                modality=modality, value_available=value_ok,
                image_available=image_ok,
                image_compressed=True if image_ok else None,
                timestamps=ts, seqs=list(range(chan_rows.get(name, 0))) if ts else None,
                source=f"dataset:{len(episode_dirs)} episodes",
            )

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0
        # 透传子 reader 的结构说明（HDF5_FORMAT.md：rate_hz/缺多通道）与数据驱动 fps
        fmt_spec = None
        fps = None
        for _d, _rd, info in subs:
            if fmt_spec is None:
                fmt_spec = (info.extra or {}).get("format_spec")
            if fps is None:
                fps = (info.extra or {}).get("fps")
            if fmt_spec is not None and fps is not None:
                break
        extra: Dict[str, Any] = {
            "episode_count": len(episode_dirs),
            "episode_files": [d.name for d in episode_dirs],
            "episodes": episodes,
            "corrupted_episodes": corrupted,   # 打开失败被跳过的集（此前恒为空）
            "aggregation": "dataset",   # 聚合模式标记（真实格式见 self._format）
        }
        if fps:
            extra["fps"] = fps
        if fmt_spec:
            extra["format_spec"] = fmt_spec
        self._info = DataInfo(
            path=str(root), format=self._format, topics=topics,
            duration=end - start, start_time=start, end_time=end,
            extra=extra,
        )
        return self._info

    def _rows(self) -> List[int]:
        return self._episode_rows

    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        if name not in self._info.topics or not self._info.topics[name].value_available:
            return None
        target = set(indices)
        out = []
        idx = 0
        rows = self._channel_rows(name)   # A2-5: 按通道行数（分表列可能差帧）
        for ei, (_d, rd, info) in enumerate(self._subs):
            n = rows[ei] if ei < len(rows) else 0
            lo, hi = idx, idx + n
            want = [p - lo for p in target if lo <= p < hi]
            if want and name in info.topics:
                vals = rd.channel_values(name, want)
                if vals:
                    out.extend(vals)
            idx = hi
        return out

    def _channel_rows(self, name: str) -> List[int]:
        """各 episode 中该通道的真实行数（缺列=0）——分表列行数可能差 1 帧，
        不能用统一的时间轴行数做全局索引映射（A2-5）"""
        return [info.topics[name].count if name in info.topics else 0
                for _d, _rd, info in self._subs]

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        if name not in self._info.topics or not self._info.topics[name].value_available:
            return None

        def gen():
            for _d, rd, info in self._subs:
                if name in info.topics:
                    it = rd.iter_values(name)
                    if it:
                        yield from it
        return gen()

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        if name not in self._info.topics or not self._info.topics[name].image_available:
            return None

        def gen():
            idx = 0
            rows = self._channel_rows(name)   # A2-5: 按通道行数
            for ei, (_d, rd, info) in enumerate(self._subs):
                n = rows[ei] if ei < len(rows) else 0
                if name in info.topics and rd.info().topics.get(name).image_available:
                    it = rd.image_frames(name)
                    if it:
                        for i, size, head, tail in it:
                            yield idx + i, size, head, tail
                idx += n
        return gen()

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        if name not in self._info.topics or not self._info.topics[name].image_available:
            return None
        target = set(indices)
        out = [None] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}
        idx = 0
        rows = self._channel_rows(name)   # A2-5: 按通道行数
        for ei, (_d, rd, info) in enumerate(self._subs):
            n = rows[ei] if ei < len(rows) else 0
            lo, hi = idx, idx + n
            want = [p - lo for p in target if lo <= p < hi]
            if want and name in info.topics and rd.info().topics.get(name).image_available:
                vals = rd.image_pixels(name, want)
                if vals:
                    for j, local_p in enumerate(want):
                        out[pos[lo + local_p]] = vals[j]
            idx = hi
        return out

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        if name not in self._info.topics or not self._info.topics[name].image_available:
            return None

        def gen():
            idx = 0
            rows = self._channel_rows(name)
            for ei, (_d, rd, info) in enumerate(self._subs):
                n = rows[ei] if ei < len(rows) else 0
                if name in info.topics and info.topics.get(name) \
                        and info.topics.get(name).image_available:
                    it = rd.image_bytes(name)
                    if it:
                        for i, t, payload in it:
                            yield idx + i, t, payload
                idx += n
        return gen()

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        for _d, rd, _info in self._subs:
            yield from rd.messages(topic_filter)

    def close(self):
        for _d, rd, _info in self._subs:
            try:
                rd.close()
            except Exception:
                pass
        self._subs.clear()


# 兼容别名：fourier 折叠式数据集（parquet 分表）即通用数据集
FourierReader = DatasetReader
