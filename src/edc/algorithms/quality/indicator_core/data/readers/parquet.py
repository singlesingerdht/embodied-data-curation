"""Parquet 读取器（单文件 / 目录 / fourier 按模态分表）"""
import math
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
import numpy as np
from ..ir import (
    DataInfo, TopicInfo, RawMessage, MODALITY_IMAGE, MODALITY_ENCODED, normalize_time_seconds, decode_image_bytes,
    _column_role_heuristic,
)
from .base import DataReader

_TIME_COLUMN_CANDIDATES = ("timestamp", "timestamp_utc", "timestamp_uhlc",
                           "user_timestamp_utc", "time", "t")


def _is_time_column(name: str) -> bool:
    n = name.lower()
    return n in _TIME_COLUMN_CANDIDATES or n.endswith("_timestamp") or n == "timestamp_uhlc"


def _is_seq_column(name: str) -> bool:
    n = name.lower()
    return n in ("seq", "sequence", "frame_index", "index", "frame_idx")


def _is_meta_column(name: str) -> bool:
    n = name.lower()
    return n in ("task_name", "trace_id", "span_id", "parameters") or n.startswith("metadata")


def _column_to_fields(col_name: str, value: Any) -> dict:
    """把 parquet 列值映射为规范值字典片段"""
    n = col_name.lower()
    # list<struct{name,value}> -> {position: [values]}；复杂 struct 无法数值化则跳过
    if isinstance(value, list) and value and isinstance(value[0], dict):
        if "value" in value[0]:
            vals = [v.get("value") for v in value if "value" in v]
            names = [v.get("name", "") for v in value]
            return {"position": list(vals), "names": list(names)}
        return {}
    if isinstance(value, list):
        vals = []
        for v in value:
            try:
                fv = float(v)
                # Preserve joint indices even when a sensor value is invalid.
                vals.append(fv if math.isfinite(fv) else float('nan'))
            except (TypeError, ValueError):
                vals.append(float('nan'))
        if "velocity" in n or n == "joint_v":
            return {"velocity": vals}
        if "effort" in n or "torque" in n or "tau" in n:
            return {"effort": vals}
        if n in ("joint_q",):
            return {"joint_q": vals, "position": vals}
        return {"position": vals}
    if isinstance(value, (int, float)) and np.isfinite(value):
        # A2-13: 标量列按列名映射（velocity/torque 标量不再误判为 position）
        if "velocity" in n or n == "joint_v":
            return {"velocity": [float(value)]}
        if "effort" in n or "torque" in n or "tau" in n:
            return {"effort": [float(value)]}
        if n in ("joint_q",):
            return {"joint_q": [float(value)], "position": [float(value)]}
        return {"position": [float(value)]}
    return {}


class _ParquetFile:
    """单个 parquet 文件的元数据 + 惰性列访问"""

    def __init__(self, path: Path, column_roles: Optional[Dict[str, List[str]]] = None):
        import pyarrow.parquet as pq
        self.path = path
        self._pf = pq.ParquetFile(path)
        self.meta = self._pf.metadata
        self.num_rows = self.meta.num_rows
        self.schema = self._pf.schema_arrow
        self.column_types = {self.schema.field(i).name: self.schema.field(i).type
                             for i in range(len(self.schema))}
        self._cache: Dict[str, Any] = {}
        self._roles: Dict[str, List[str]] = column_roles or {}

    def _role(self, role: str, name: str) -> bool:
        if name in self._roles.get(role, []):
            return True
        return _column_role_heuristic(role, name)

    def time_column(self) -> Optional[str]:
        for c in self._roles.get("time", []):
            if c in self.column_types:
                return c
        # UHLC is an encoded logical clock, not ordinary epoch nanoseconds.
        # Prefer the explicitly typed wall-clock columns when available.
        for c in ("timestamp_utc", "user_timestamp_utc", "timestamp", "time", "t"):
            if c in self.column_types and self._role("time", c):
                return c
        for cand in self.column_types:
            if self._role("time", cand) and cand != "timestamp_uhlc":
                return cand
        return None

    def data_columns(self) -> List[str]:
        return [c for c in self.column_types
                if not self._role("time", c)
                and not self._role("seq", c)
                and not self._role("meta", c)]

    def column(self, name: str) -> Any:
        """读取整列为 Python 值（to_pylist，供数值通道使用，列较小）。"""
        if name not in self._cache:
            import pyarrow.parquet as pq
            table = pq.read_table(self.path, columns=[name], pre_buffer=True)
            self._cache[name] = table.column(0).to_pylist()
        return self._cache[name]

    def column_array(self, name: str):
        """读取整列为 pyarrow 数组（**不做 to_pylist**）。

        图像大列（binary/list<uint8>）用它避免物化成海量 Python 对象，
        逐行 as_py() 按需取值。
        """
        key = f"__arr__{name}"
        if key not in self._cache:
            import pyarrow.parquet as pq
            self._cache[key] = pq.read_table(self.path, columns=[name], pre_buffer=True).column(0)
        return self._cache[key]

    def is_list_type(self, name: str) -> bool:
        import pyarrow as pa
        return isinstance(self.column_types.get(name), pa.ListType)


class ParquetReader(DataReader):
    """Parquet 读取器

    布局约定：
    - 单文件：每个「数据列」为一个通道（r2v2 12 列统一 schema 适用）。
    - 目录：每个文件视为独立来源；文件内数据列作为通道
      （fourier 按模态分表适用；旧约定「一文件一 topic」时以文件名为通道）。
    时间列从候选列自动识别，量纲自动归一化到秒。
    """

    def __init__(self):
        super().__init__()
        self._files: List[_ParquetFile] = []
        self._col_of: Dict[str, Tuple[_ParquetFile, str]] = {}

    def open(self, path: str) -> DataInfo:
        self._path = path
        p = Path(path)
        if p.is_file() and p.suffix.lower() in ('.parquet', '.pq'):
            files = [p]
        elif p.is_dir():
            files = sorted(p.glob('*.parquet')) + sorted(p.glob('*.pq'))
        else:
            raise FileNotFoundError(f"路径不存在: {path}")
        if not files:
            raise ValueError(f"未找到 parquet 文件: {path}")
        self._format = 'parquet'

        self._files = [_ParquetFile(f, self._column_roles) for f in files]
        topics: Dict[str, TopicInfo] = {}
        start_time = float('inf')
        end_time = float('-inf')

        offset = 0.0
        multi = len(self._files) > 1
        layouts = [set(pf.data_columns()) for pf in self._files]
        # Distinct modality tables share a source clock; only same-schema
        # segments retain the historical concatenation behavior.
        segmented = multi and all(cols == layouts[0] for cols in layouts)
        if multi and not segmented:
            seen = set()
            for cols in layouts:
                if seen.intersection(cols):
                    raise ValueError("混合分表/分段 Parquet 布局不明确，请分别打开 episode")
                seen.update(cols)
        for pf in self._files:
            data_cols = pf.data_columns()
            tcol = pf.time_column()
            ts = None
            if tcol:
                arr = normalize_time_seconds(pf.column(tcol))
                if arr.size:
                    if segmented:
                        # 目录模式多文件（分段/分表同 schema）：时间轴按集拼接
                        # （与 lerobot/dataset 同口径），供同名通道跨文件聚合
                        local0 = arr - float(arr[0])
                        ts = (local0 + offset).tolist()
                        start_time = min(start_time, float(ts[0]))
                        end_time = max(end_time, float(ts[-1]))
                        dts = np.diff(arr)
                        dts = dts[dts > 0]
                        offset += float(local0[-1]) + (float(np.median(dts)) if dts.size else 0.1)
                    else:
                        ts = arr.tolist()
                        if ts:
                            start_time = min(start_time, float(min(ts)))
                            end_time = max(end_time, float(max(ts)))

            # 一律以「数据列」为通道（列名规范化），保证单列文件也能正确识别内容
            for col in data_cols:
                name = self._name_to_topic(col, split_underscore=False)
                self._register_channel(topics, name, col, pf, ts)

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0
        self._info = DataInfo(
            path=path, format='parquet', topics=topics,
            duration=end - start, start_time=start, end_time=end,
        )
        return self._info

    def _register_channel(self, topics, name, col, pf, ts):
        typ = pf.column_types.get(col)
        import pyarrow as pa
        is_list_u8 = isinstance(typ, pa.ListType) and str(typ.value_type) in ('uint8', 'binary')
        is_binary = str(typ) in ('binary', 'large_binary')
        modality = self.classify(name, str(typ))
        value_ok = False
        image_ok = False
        image_compressed = None
        if modality == MODALITY_IMAGE and (is_binary or is_list_u8):
            # 图像字节列（binary 或 uint8 列表，如 r2v2 compressed_image_image / fourier camera）
            image_ok = True
            image_compressed = True
        elif is_list_u8:
            modality = MODALITY_ENCODED
        elif not is_binary and not self.is_meta_column(col):
            value_ok = True
        if name in topics:
            # A1-5: 同名通道跨文件聚合（目录模式分段/分表同 schema），不再静默覆盖——
            # 追加 (文件, 列) 条目、累计 count、拼接时间轴，读取路径按文件顺序提供数据
            self._col_of[name].append((pf, col))
            prev = topics[name]
            prev.count += pf.num_rows
            if ts:
                prev.timestamps = (prev.timestamps or []) + ts
            return
        self._col_of[name] = [(pf, col)]
        topics[name] = TopicInfo(
            name=name, msg_type=str(typ), count=pf.num_rows,
            modality=modality, value_available=value_ok,
            image_available=image_ok, image_compressed=image_compressed,
            timestamps=ts, source=f"{pf.path.name}:{col}",
        )

    def _name_to_topic(self, stem: str, split_underscore: bool = False) -> str:
        if '.' in stem:
            parts = stem.split('.')
        elif split_underscore and '_' in stem:
            parts = stem.split('_')
        else:
            return f'/{stem}'
        return '/' + '/'.join(parts)

    def _values_of(self, col: str, row: Any) -> dict:
        return _column_to_fields(col, row)

    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        entries = self._col_of.get(name)
        if not entries:
            return None
        if not self._channel(name).value_available:
            return None
        out = []
        for i in indices:
            cur = i
            val = {}
            for pf, col in entries:
                values = pf.column(col)
                n = len(values)
                if cur < n:
                    val = self._values_of(col, values[cur]) if cur >= 0 else {}
                    break
                cur -= n
            out.append(val)
        return out

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        entries = self._col_of.get(name)
        if not entries:
            return None
        if not self._channel(name).value_available:
            return None
        for pf, col in entries:
            values = pf.column(col)
            for v in values:
                yield self._values_of(col, v)

    @staticmethod
    def _array_row_bytes(values, i: int) -> bytes:
        """从 pyarrow 数组取第 i 行图像字节。

        list<uint8>/binary 用「offsets + 扁平数据缓冲」零拷贝切片，
        避免 as_py() 把每帧物化成海量 Python int（fourier 210KB/帧 20万 int）。
        """
        import pyarrow as pa
        if isinstance(values, pa.ChunkedArray):
            for ch in values.chunks:
                if i < len(ch):
                    return ParquetReader._chunk_row_bytes(ch, i)
                i -= len(ch)
            return b""
        return ParquetReader._chunk_row_bytes(values, i)

    @staticmethod
    def _chunk_row_bytes(ch, i: int) -> bytes:
        import pyarrow as pa
        import numpy as np
        if isinstance(ch.type, pa.ListType) \
                and str(ch.type.value_type) in ("uint8", "binary"):
            try:
                offs = np.frombuffer(ch.buffers()[1], dtype=np.int32).astype(np.int64)
                data = np.frombuffer(ch.values.buffers()[1], dtype=np.uint8)
                s, e = int(offs[i]), int(offs[i + 1])
                return data[s:e].tobytes()
            except Exception:
                pass
        v = ch[i].as_py()
        if isinstance(v, dict):
            b = v.get("bytes")
            return bytes(b) if isinstance(b, (bytes, bytearray)) else b""
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, list) and v and isinstance(v[0], int):
            return bytes(v)
        return b""

    def preload_image_columns(self, names: List[str]) -> None:
        """并发预取多个图像列的 pyarrow 数组（大列 BROTLI 解压慢，多列并发读显著提速）。

        层1 全帧扫描每通道需读整列（单列 ~3s），多相机/多通道串行读浪费；
        这里用线程池并发触发 column_array 的 read_table（不同文件互不冲突），
        预取后层1/层2 复用缓存，单次检测即体现加速（无需预处理）。
        """
        entries = []
        for n in names:
            for pf, col in self._col_of.get(n, []):
                entries.append((n, col, pf))
        if len(entries) <= 1:
            return
        from concurrent.futures import ThreadPoolExecutor
        import pyarrow.parquet as pq

        def _load(e):
            n, col, pf = e
            # 已有该列的 side-car 索引时层1 走索引（免读整列），无需预取
            if self._image_idx_path(pf, col) is not None:
                return
            key = f"__arr__{col}"
            if key not in pf._cache:
                # 单 row-group 大列：use_threads=False 免线程池开销（实测更快）
                pf._cache[key] = pq.read_table(
                    str(pf.path), columns=[col], pre_buffer=True,
                    use_threads=False).column(0)

        with ThreadPoolExecutor(max_workers=min(4, len(entries))) as ex:
            list(ex.map(_load, entries))

    @staticmethod
    def _image_idx_path(pf, col: str) -> Optional[Path]:
        """定位图像列的 side-car 索引。

        新格式按列命名（<parquet>.<列名>.idx.parquet），多图像列文件各列独立；
        旧版单文件索引（<parquet>.idx.parquet）仅在文件恰好只有一个图像列时
        归属明确，才允许回退使用（避免读到别的列的尺寸）。
        """
        import pyarrow as pa
        base = str(pf.path)[:-len(pf.path.suffix)]
        col_idx = Path(base + "." + col + ".idx.parquet")
        if col_idx.exists():
            return col_idx
        img_cols = [c for c, t in pf.column_types.items()
                    if (isinstance(t, pa.ListType) and str(t.value_type) in ("uint8", "binary"))
                    or "binary" in str(t)]
        plain = Path(base + ".idx.parquet")
        if len(img_cols) == 1 and plain.exists():
            return plain
        return None

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        entries = self._col_of.get(name)
        if not entries:
            return None
        if not self._channel(name).image_available:
            return None
        import pyarrow.parquet as _pq

        def gen():
            gi = 0
            for pf, col in entries:
                # side-car 索引（预处理产物）：层1 只读 size/head/tail，免读整列图像数据
                idx = self._image_idx_path(pf, col)
                if idx is not None:
                    try:
                        it = _pq.read_table(idx, pre_buffer=True)
                        sizes = it.column("size").to_numpy()
                        heads = it.column("head")
                        tails = it.column("tail")
                        for i in range(len(sizes)):
                            h = heads[i].as_py() or b""
                            t = tails[i].as_py() or b""
                            yield gi + i, int(sizes[i]), bytes(h[:4096]), bytes(t[-16:])
                        gi += len(sizes)
                        continue
                    except Exception:
                        pass  # 索引不可用则回退读图像列
                values = pf.column_array(col)
                for i in range(len(values)):
                    b = self._array_row_bytes(values, i)
                    yield gi, len(b), b[:4096], b[-16:]
                    gi += 1
        return gen()

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        entries = self._col_of.get(name)
        if not entries:
            return None
        if not self._channel(name).image_available:
            return None
        out = []
        for i in indices:
            cur = i
            b = b""
            for pf, col in entries:
                values = pf.column_array(col)
                n = len(values)
                if cur < n:
                    b = self._array_row_bytes(values, cur) if cur >= 0 else b""
                    break
                cur -= n
            out.append(decode_image_bytes(b))
        return out

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        entries = self._col_of.get(name)
        if not entries:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        ts = ti.timestamps or []

        def gen():
            gi = 0
            for pf, col in entries:
                values = pf.column_array(col)
                for i in range(len(values)):
                    b = self._array_row_bytes(values, i)
                    t = ts[gi] if gi < len(ts) else 0.0
                    yield gi, t, b
                    gi += 1
        return gen()

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        for name, entries in self._col_of.items():
            if topic_filter and name not in topic_filter:
                continue
            for pf, col in entries:
                ts = pf.time_column()
                if ts:
                    # A2-11: 时间戳归一化到秒（与 TopicInfo 口径一致）
                    tvals = normalize_time_seconds(pf.column(ts))
                else:
                    tvals = None
                for i, v in enumerate(pf.column(col)):
                    t = float(tvals[i]) if tvals is not None and i < len(tvals) else 0.0
                    yield RawMessage(topic=name, msg_type=str(pf.column_types[col]),
                                     timestamp=t, raw=b'')

    def close(self):
        self._col_of.clear()
        self._files.clear()
