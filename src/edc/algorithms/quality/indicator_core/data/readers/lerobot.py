"""LeRobot 目录读取器（data/chunk-*/episode_*.parquet + meta/）"""
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
import numpy as np
from ..ir import (
    DataInfo, TopicInfo, RawMessage, MODALITY_IMAGE, normalize_time_seconds, decode_image_bytes,
)
from .base import DataReader
from .parquet import _column_to_fields

class LerobotReader(DataReader):
    """LeRobot v2.1 chunk 目录读取器

    各 episode_*.parquet 共享同一列集合；把每列聚合为一个通道，时间轴跨
    episode 拼接（episode 边界自然形成缺口），meta/ 信息记入 extra。
    """

    def __init__(self):
        super().__init__()
        self._files: List[Path] = []
        self._video_mp4s: Dict[str, List[Path]] = {}   # 图像通道名 -> 各 episode mp4（按集排序）
        self._video_total: Dict[str, int] = {}          # 图像通道名 -> 总帧数

    def open(self, path: str) -> DataInfo:
        p = Path(path)
        if p.is_file() and p.suffix.lower() in ('.parquet', '.pq'):
            # 单 episode 文件
            return self.open_files([p], source=f"lerobot:{path}")
        # A2-15: 松散与嵌套（data/、examples/）parquet 合并发现，不再 or 短路
        files = sorted(set(p.glob('*.parquet')) | set(p.glob('data/**/*.parquet'))
                       | set(p.glob('examples/*.parquet')))
        if not files:
            raise ValueError(f"未找到 lerobot episode 文件: {path}")
        return self.open_files(files, source=f"lerobot:{path}")

    # ---- 公开能力接口（executor 并行层使用；DataReader 契约的扩展能力） ----
    def episode_files(self):
        """已打开的 episode parquet 文件列表（List[Path]）。"""
        return list(getattr(self, "_files", []) or [])

    def file_columns(self):
        """{文件名: 该文件包含的通道列名集合}。"""
        return dict(getattr(self, "_file_cols", {}) or {})

    def open_files(self, files, source: Optional[str] = None) -> DataInfo:
        """以显式文件列表打开（单元级并行用：每个 worker 只打开自己那组 episode）"""
        files = [Path(f) for f in files]
        p = Path(files[0]).parent if files else Path(".")
        self._format = 'lerobot'
        self._files = sorted(files)

        import pyarrow.parquet as pq
        cols: Dict[str, Any] = {}
        corrupted: List[str] = []
        good_files: List[Path] = []
        self._file_cols: Dict[str, Set[str]] = {}
        for f in files:
            try:
                schema = pq.read_schema(f)
            except Exception:
                corrupted.append(f.name)
                continue  # 跳过损坏文件，其余照常分析
            good_files.append(f)
            self._file_cols[f.name] = {schema.field(i).name for i in range(len(schema))}
            for i in range(len(schema)):
                fn = schema.field(i).name
                if fn not in cols:
                    cols[fn] = schema.field(i).type
        self._files = good_files

        # schema 漂移检测：某文件缺某公共列
        all_cols = set(cols)
        schema_drift = [
            {"file": fname, "missing": sorted(all_cols - fcols)}
            for fname, fcols in self._file_cols.items() if fcols != all_cols
        ]

        topics: Dict[str, TopicInfo] = {}
        tcol = None
        for cand in (self._column_roles.get("time", []) + ["timestamp", "timestamp_utc"]):
            if cand in cols:
                tcol = cand
                break

        # 逐个文件聚合时间轴（按 episode 偏移拼接）与 episode 级完整性统计
        ts_map: Dict[str, List[float]] = {c: [] for c in cols}
        start_time = float('inf')
        end_time = float('-inf')
        offset = 0.0
        episodes: List[dict] = []
        file_rows: List[int] = []
        for f in good_files:
            local = None
            tcol_error = False
            if tcol:
                try:
                    local = normalize_time_seconds(
                        pq.read_table(f, columns=[tcol]).column(0).to_numpy())
                except Exception:
                    corrupted.append(f.name)
                    tcol_error = True
            ep = self._episode_stats(f, tcol, local)
            ep["start_time"] = offset   # 本集全局起始时间（相对数据集起点，供并行 base_time 对齐）
            if local is not None and local.size:
                local0 = local - float(local[0])   # 以本集首帧为 0
                eps_ts = local0 + offset
                # 只对本文件实际含有的列 extend 时间轴：schema 漂移（缺列）文件跳过，
                # 使 count 与该列真实帧数一致（否则 count 偏大，采样/索引错位）
                f_cols = self._file_cols.get(f.name, set())
                for c in cols:
                    if c in f_cols:
                        ts_map[c].extend(eps_ts.tolist())
                start_time = min(start_time, float(eps_ts.min()))
                end_time = max(end_time, float(eps_ts.max()))
                offset += float(local0[-1]) + self._median_dt(local)
                ep["rows"] = int(local.size)
            else:
                # 时间列读取失败/无时间轴：占位 0 行并保留在 _files 中，保持
                # file_rows 与 _files 索引一致（否则失败文件之后所有文件的全局
                # 索引错位、末集数据静默丢失）
                ep["rows"] = 0
                if tcol_error:
                    ep["time_column_error"] = "unreadable"
            episodes.append(ep)
            file_rows.append(ep["rows"])
        self._file_rows = file_rows

        # videos 单独存储的图像（videos/chunk-*/cam_*.compressed/episode_*.mp4，
        # 按当前打开的 episode 文件限定，单文件只取本集 mp4）
        self._video_mp4s, self._video_total = self._collect_videos(
            p, {f.stem for f in self._files})

        for c, typ in cols.items():
            if self.is_time_column(c) or self.is_seq_column(c) or self.is_meta_column(c):
                continue
            ts = ts_map.get(c)
            # 列名规范化后分类：observation.images.camera_top → /observation/images/camera_top，
            # 命中 profile modality_hints（fourier.yaml 已注册）与 classify 的 /images/ 规则，
            # 否则内嵌图像列无 hints 时被判 other → 图像检测静默跳过（A2-9）
            modality = self.classify("/" + c.replace(".", "/"), str(typ))
            is_list_u8 = str(typ) in ("list<item: uint8>", "list<item: binary>") or "binary" in str(typ)
            value_ok = not is_list_u8 and "binary" not in str(typ)
            image_ok = modality == MODALITY_IMAGE
            topics[c] = TopicInfo(
                name=c, msg_type=str(typ), count=len(ts) if ts else 0,
                modality=modality, value_available=value_ok,
                image_available=image_ok,
                image_compressed=True if image_ok else None,
                timestamps=ts, seqs=list(range(len(ts) or 0)) if ts else None,
                source=f"lerobot:{len(files)} episodes",
            )

        # 图像通道：videos 单独存储的相机视频（与 parquet 帧按 episode 对齐）
        video_channels = []
        if self._video_mp4s:
            ts_ref = next((ts_map[c] for c in cols if ts_map.get(c)), None)
            total_rows = sum(file_rows)
            for cam, mp4s in self._video_mp4s.items():
                vname = f"/{cam}"
                # count 用实际 mp4 解码帧数（截断 mp4 时 parquet 行数会高估；
                # _video_total 是打开容器统计的真实帧数）
                vcount = int(self._video_total.get(cam, total_rows))
                topics[vname] = TopicInfo(
                    name=vname, msg_type="video/mp4", count=vcount,
                    modality=MODALITY_IMAGE, value_available=False,
                    image_available=True, image_compressed=True,
                    timestamps=ts_ref, seqs=list(range(vcount)),
                    source=f"lerobot:videos/{len(mp4s)} mp4",
                )
                video_channels.append(cam)

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0
        self._info = DataInfo(
            path=str(p), format='lerobot', topics=topics,
            duration=end - start, start_time=start, end_time=end,
            extra={"episode_count": len(self._files),
                   "episode_files": [f.name for f in self._files],
                   "corrupted_episodes": sorted(set(corrupted)),
                   "schema_drift": schema_drift,
                   "episodes": episodes,
                   "fps": self._read_fps(p),
                   "video_channels": video_channels},
        )
        return self._info

    def _collect_videos(self, root: Path, episode_ids: Optional[Set[str]] = None,
                        ) -> Tuple[Dict[str, List[Path]], Dict[str, int]]:
        """收集 videos/ 单独存储的图像通道（videos/chunk-*/cam_*.compressed/episode_*.mp4）。

        返回 {相机名: [各 episode mp4 按集排序]} 与 {相机名: 总帧数}。
        episode_ids 非空时只保留匹配的 episode（单文件/单元并行只取本集 mp4）。
        """
        cur = root
        videos_dir = None
        for _ in range(5):
            cand = cur / "videos"
            if cand.is_dir():
                videos_dir = cand
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        if videos_dir is None:
            return {}, {}
        channels: Dict[str, List[Path]] = {}
        for mp4 in sorted(videos_dir.rglob("*.mp4")):
            if "__MACOSX" in mp4.parts or mp4.name.startswith("._"):
                continue
            if episode_ids is not None and mp4.stem not in episode_ids:
                continue
            cam = mp4.parent.name           # cam_h.color.image_raw.compressed
            # A2-8: 不按 split('.')[0] 合并子流——cam_h.color 与 cam_h.depth 是不同
            # 相机子流，作为独立通道（此前合并导致两路视频流混检/通道未分割）
            channels.setdefault(cam, []).append(mp4)
        totals: Dict[str, int] = {}
        for cam, mp4s in channels.items():
            mp4s.sort(key=lambda f: f.name)
            totals[cam] = self._count_video_frames(mp4s)
        return channels, totals

    @staticmethod
    def _count_video_frames(mp4s: List[Path]) -> int:
        import av
        total = 0
        for mp4 in mp4s:
            try:
                with av.open(str(mp4)) as cont:
                    for s in cont.streams:
                        if s.type == "video":
                            total += s.frames
            except Exception:
                pass
        return total

    @staticmethod
    def _read_fps(root: Path) -> Optional[float]:
        """从数据集自身元数据读取帧率（meta/info.json 或 info.json）。

        数据驱动：无需 profile 即可注入期望频率检测（开源参考数据集）。
        root 可能是 chunk 目录（data/chunk-000），向上查找数据集根。
        """
        import json
        cur = root
        for _ in range(6):
            for cand in (cur / "meta" / "info.json", cur / "info.json"):
                try:
                    with open(cand, "r", encoding="utf-8") as f:
                        fps = json.load(f).get("fps")
                    if fps:
                        return float(fps)
                except Exception:
                    continue
            cur = cur.parent
        return None

    @staticmethod
    def _median_dt(local: np.ndarray) -> float:
        d = np.diff(local)
        d = d[d > 0]
        return float(np.median(d)) if d.size else 0.1

    def _episode_stats(self, f: Path, tcol: Optional[str],
                       local: Optional[np.ndarray]) -> dict:
        """单 episode 完整性：frame_index 连续性、时间戳单调、episode/task 一致性"""
        import pyarrow.parquet as pq
        stats: Dict[str, Any] = {"file": f.name}
        try:
            schema_names = [pq.read_schema(f).field(i).name for i in range(pq.read_schema(f).num_fields)]
            want = [c for c in ("frame_index", "episode_index", "task_index") if c in schema_names]
            if not want and local is None:
                return stats
            t = pq.read_table(f, columns=want) if want else None
            if "frame_index" in (t.column_names if t else ()):
                fi = t.column("frame_index").to_numpy()
                if len(fi) > 1:
                    stats["frame_gaps"] = int(np.sum(np.diff(fi) != 1))
                else:
                    stats["frame_gaps"] = 0
                stats["frame_first"] = int(fi[0])
                stats["frame_last"] = int(fi[-1])
            if local is not None and len(local) > 1:
                stats["ts_non_monotonic"] = int(np.sum(np.diff(local) <= 0))
            if t is not None:
                if "episode_index" in t.column_names:
                    stats["episode_values"] = sorted(set(int(x) for x in t.column("episode_index").to_numpy()))
                if "task_index" in t.column_names:
                    stats["task_values"] = sorted(set(int(x) for x in t.column("task_index").to_numpy()))
        except Exception:
            pass
        return stats

    def _read_col(self, f: Path, name: str):
        """读取某文件某列；文件缺该列或不可读返回 None（跳过，不中断）"""
        if name not in self._file_cols.get(f.name, set()):
            return None
        import pyarrow.parquet as pq
        try:
            return pq.read_table(f, columns=[name]).column(0).to_pylist()
        except Exception:
            return None

    def _iter_col_rows(self, f: Path, name: str):
        """逐行产出列值（免 to_pylist 全量物化，内存/CPU 双省）。

        用 as_py() 转成 Python 值（struct->dict / list->list / binary->bytes），
        与旧 to_pylist 输出一致但按需产出。文件缺列或不可读时产出空。
        """
        if name not in self._file_cols.get(f.name, set()):
            return
        import pyarrow.parquet as pq
        try:
            arr = pq.read_table(f, columns=[name]).column(0)
        except Exception:
            return
        for v in arr:
            yield v.as_py()

    @staticmethod
    def _extract_bytes(v) -> bytes:
        """从 struct/bytes/list<uint8> 值中提取图像字节。"""
        if isinstance(v, dict) and isinstance(v.get("bytes"), (bytes, bytearray)):
            return bytes(v["bytes"])
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, list) and v and isinstance(v[0], dict) \
                and isinstance(v[0].get("bytes"), (bytes, bytearray)):
            return bytes(v[0]["bytes"])
        if isinstance(v, list) and v and isinstance(v[0], int):
            return bytes(v)
        return b""

    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        if name not in self._info.topics or not self._info.topics[name].value_available:
            return None
        out = []
        idx = 0
        target = set(indices)
        rows = getattr(self, "_file_rows", None) or [0] * len(self._files)
        for fi, f in enumerate(self._files):
            n = rows[fi] if fi < len(rows) else 0
            lo, hi = idx, idx + n
            want = [p - lo for p in target if lo <= p < hi]
            if want:
                want_set = set(want)
                for i, v in enumerate(self._iter_col_rows(f, name)):
                    if i in want_set:
                        out.append(_column_to_fields(name, v))
            idx = hi
        return out

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        if name not in self._info.topics or not self._info.topics[name].value_available:
            return None

        def gen():
            for f in self._files:
                for v in self._iter_col_rows(f, name):
                    yield _column_to_fields(name, v)
        return gen()

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        if name not in self._info.topics or not self._info.topics[name].image_available:
            return None
        # videos 单独存储的相机通道：遍历各 episode mp4 取视频包
        if name.startswith("/") and name[1:] in self._video_mp4s:
            return self._video_frames(self._video_mp4s[name[1:]])

        def gen():
            idx = 0
            for f in self._files:
                for v in self._iter_col_rows(f, name):
                    b = self._extract_bytes(v)
                    yield idx, len(b), b[:4096], b[-16:]
                    idx += 1
        return gen()

    @staticmethod
    def _video_frames(mp4s: List[Path]) -> Iterator[Tuple[int, int, bytes, bytes]]:
        import av
        idx = 0
        for mp4 in mp4s:
            try:
                with av.open(str(mp4)) as cont:
                    for s in cont.streams:
                        if s.type != "video":
                            continue
                        for pkt in cont.demux(s):
                            b = bytes(pkt)
                            yield idx, len(b), b[:4096], b[-16:]
                            idx += 1
            except Exception:
                pass

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        if name not in self._info.topics or not self._info.topics[name].image_available:
            return None
        # videos 单独存储的相机通道：从各 episode mp4 解码目标帧
        if name.startswith("/") and name[1:] in self._video_mp4s:
            return self._video_pixels(self._video_mp4s[name[1:]], indices)
        target = set(indices)
        out = [None] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}
        idx = 0
        rows = getattr(self, "_file_rows", None) or [0] * len(self._files)
        for fi, f in enumerate(self._files):
            n = rows[fi] if fi < len(rows) else 0
            lo, hi = idx, idx + n
            want = [p - lo for p in target if lo <= p < hi]
            if want:
                want_set = set(want)
                for i, v in enumerate(self._iter_col_rows(f, name)):
                    if i in want_set:
                        out[pos[lo + i]] = decode_image_bytes(self._extract_bytes(v))
            idx = hi  # 始终按每集行数前进，保持全局索引对齐
        return out

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        if name not in self._info.topics or not self._info.topics[name].image_available:
            return None
        # videos 单独存储的相机通道：逐帧解码为灰度 ndarray（全帧像素管线的输入契约，
        # payload 为 bytes 或 ndarray 均可）。H.264 包尺寸∝帧间运动而非内容熵，
        # 压缩域尺寸启发式对视频编码流无效（Tier-1 对 video/mp4 跳过尺寸类判定），
        # 黑/白屏/遮挡/花屏/模糊/冻结由像素级全帧检测覆盖。
        if name.startswith("/") and name[1:] in self._video_mp4s:
            ts = self._info.topics[name].timestamps or []
            return self._video_image_bytes(self._video_mp4s[name[1:]], ts)
        ts = self._info.topics[name].timestamps or []

        def gen():
            idx = 0
            for f in self._files:
                for v in self._iter_col_rows(f, name):
                    b = self._extract_bytes(v)
                    t = ts[idx] if idx < len(ts) else 0.0
                    yield idx, t, b
                    idx += 1
        return gen()

    @staticmethod
    def _video_image_bytes(mp4s: List[Path],
                           ts: List[float]) -> Iterator[Tuple[int, float, Any]]:
        """各 episode mp4 逐帧解码（灰度 ndarray），供全帧像素管线。

        解码失败的单帧 yield None（FrameQualityAnalyzer 记 undecodable，
        不中断流）；某 mp4 整体损坏则跳过该集，索引仍按已产出帧前进。
        """
        import av
        idx = 0
        for mp4 in mp4s:
            try:
                with av.open(str(mp4)) as cont:
                    for s in cont.streams:
                        if s.type != "video":
                            continue
                        for frame in cont.decode(s):
                            try:
                                arr = frame.to_ndarray(format="gray")
                            except Exception:
                                arr = None
                            t = ts[idx] if idx < len(ts) else 0.0
                            yield idx, t, arr
                            idx += 1
            except Exception:
                continue

    def _video_pixels(self, mp4s: List[Path], indices: List[int]) -> List[Any]:
        """从各 episode mp4 解码目标帧（按全局帧索引定位）"""
        import av
        target = sorted(indices)
        tset = set(target)
        out = [None] * len(indices)
        pos = {idx: i for i, idx in enumerate(indices)}
        global_idx = 0
        for mp4 in mp4s:
            try:
                with av.open(str(mp4)) as cont:
                    for s in cont.streams:
                        if s.type != "video":
                            continue
                        for i, frame in enumerate(cont.decode(s)):
                            gi = global_idx + i
                            if gi in tset:
                                arr = frame.to_ndarray(format="rgb24")
                                out[pos[gi]] = arr
                        global_idx += s.frames
            except Exception:
                pass
        return out

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        for f in self._files:
            cols = self._file_cols.get(f.name, set())
            for name in sorted(cols):
                if (self.is_time_column(name) or self.is_seq_column(name)
                        or self.is_meta_column(name)):
                    continue   # 时间/序号/元数据列不是通道（A2-12：防 KeyError）
                if topic_filter and name not in topic_filter:
                    continue
                if name not in self._info.topics:
                    continue
                vals = self._read_col(f, name) or []
                for v in vals:
                    yield RawMessage(topic=name,
                                     msg_type=str(self._info.topics[name].msg_type),
                                     timestamp=0.0, raw=b'')

    def close(self):
        self._files.clear()
