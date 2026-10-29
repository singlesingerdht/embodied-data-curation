"""HDF5 读取器（嵌套 group 结构）"""
from pathlib import Path
from typing import Dict, List, Optional, Iterator, Tuple, Any, Set
import numpy as np
from ..ir import (
    DataInfo, TopicInfo, RawMessage, MODALITY_IMAGE, normalize_time_seconds, decode_image_bytes,
)
from ._parsers.hdf5_format import find_format_file, parse_hdf5_format, Hdf5Format
from .base import DataReader

class Hdf5Reader(DataReader):
    """HDF5 读取器

    每个数值 dataset 视为一个通道（路径作通道名）；时间轴取自
    /timestamp/t（或任意名为 timestamp 的 dataset）。open() 只读元数据（shape/dtype）。
    数据集根若附带 HDF5_FORMAT.md（结构说明），自动解析并利用：
    rate_hz 注入期望频率、通道结构校验（缺/多）、模态映射（覆盖文件名启发式）。
    """

    def __init__(self):
        super().__init__()
        self._file = None
        self._ts = None
        self._ds_of: Dict[str, str] = {}
        self._fmt: Optional[Hdf5Format] = None

    def open(self, path: str) -> DataInfo:
        import h5py

        self._path = path
        p = Path(path)
        # 目录（数据集根，如 kaiyang_luoshi/<UUID>/episode.hdf5）：解析到具体 hdf5 文件
        if p.is_dir():
            hdf5_files = [f for f in p.rglob("*.hdf5") if f.is_file()
                          and "__MACOSX" not in f.parts and not f.name.startswith("._")]
            hdf5_files += [f for f in p.rglob("*.h5") if f.is_file()
                           and "__MACOSX" not in f.parts and not f.name.startswith("._")]
            if not hdf5_files:
                raise FileNotFoundError(f"目录中未找到 hdf5 文件: {path}")
            p = hdf5_files[0]
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._format = 'hdf5'
        self._file = h5py.File(str(p), 'r')

        # 数据集根若附带 HDF5_FORMAT.md 结构说明：自动解析（rate_hz/通道/模态）
        try:
            fmt_file = find_format_file(self._path)
            self._fmt = parse_hdf5_format(fmt_file) if fmt_file is not None else None
        except Exception:
            self._fmt = None

        # 收集所有 dataset 路径
        def collect(g, prefix=''):
            for k, v in g.items():
                pk = f'{prefix}/{k}' if prefix else k
                if isinstance(v, h5py.Dataset):
                    self._ds_of[pk] = pk
                elif isinstance(v, h5py.Group):
                    collect(v, pk)
        collect(self._file)

        # 时间轴：/timestamp/t 优先
        ts_arr = None
        if 'timestamp' in self._file:
            tsg = self._file['timestamp']
            if 't' in tsg:
                ts_arr = tsg['t'][:]
            elif isinstance(tsg, h5py.Dataset):
                ts_arr = tsg[:]
        if ts_arr is None:
            for key, _v in self._ds_of.items():
                if key.split('/')[-1] == 'timestamp':
                    try:
                        ts_arr = self._file[key][:]
                        break
                    except Exception:
                        continue
        self._ts = normalize_time_seconds(ts_arr) if ts_arr is not None else None

        topics: Dict[str, TopicInfo] = {}
        start_time = float('inf')
        end_time = float('-inf')
        if self._ts is not None and self._ts.size:
            start_time = float(self._ts.min())
            end_time = float(self._ts.max())

        for pk in self._ds_of:
            ds = self._file[pk]
            if not hasattr(ds, 'shape') or ds.ndim < 1:
                continue
            name = f'/{pk}'
            if pk.split('/')[-1] == 'timestamp':
                continue
            # 业务决策（评审第十轮 E10）：count 用真实行数，尾部行全检。
            # 时间戳轴短于数据行时，事件 time 定位由 scoring/freeze 的 index 越界保护兜底
            n = ds.shape[0]
            is_obj = ds.dtype.kind == 'O'
            # 模态：HDF5_FORMAT.md 路径映射优先，否则 classify 启发式
            modality = self._fmt.modality(pk) if self._fmt is not None else None
            if modality is None:
                modality = self.classify(name, str(ds.dtype))
            # 原始图像：对象列（压缩字节）或 uint8/uint16 多维数组（像素矩阵，如
            # HDF5_FORMAT.md 声明的 color: shape=(N,H,W,3), dtype=uint8）都算图像；
            # 像素矩阵不做数值内容检测（此前被当数值通道空跑且图像检测静默跳过）
            is_uint_image = (modality == MODALITY_IMAGE
                             and ds.dtype.kind in ('u',) and ds.ndim >= 2)
            image_ok = (is_obj or is_uint_image) and modality == MODALITY_IMAGE
            value_ok = (not is_obj and ds.dtype.kind in ('f', 'i', 'u') and ds.ndim >= 1)
            if is_uint_image:
                value_ok = False
            topics[name] = TopicInfo(
                name=name, msg_type=f'hdf5/{pk}', count=n,
                modality=modality, value_available=value_ok,
                image_available=image_ok,
                image_compressed=True if (is_obj and image_ok)
                else (False if is_uint_image else None),
                timestamps=self._ts.tolist() if self._ts is not None else None,
                seqs=list(range(n)),
                source=pk,
            )

        start = start_time if start_time != float('inf') else 0.0
        end = end_time if end_time != float('-inf') else 0.0
        extra: Dict[str, Any] = {}
        # A1-8: 行数 ≠ 时间戳数显式告警（E10 后 count=真实行数，时间轴可能更短——
        # 尾部行有数据无时间定位，显式上报而非静默）
        _mismatch = []
        for _name, _ti in topics.items():
            _n_ts = len(_ti.timestamps) if _ti.timestamps else None
            if _n_ts is not None and _ti.count > _n_ts:
                _mismatch.append({"channel": _name, "rows": _ti.count,
                                  "timestamps": _n_ts})
        if _mismatch:
            extra["timestamp_mismatch"] = _mismatch
        if self._fmt is not None:
            if self._fmt.rate_hz:
                extra["fps"] = self._fmt.rate_hz      # 数据驱动期望频率（同 lerobot info.json）
            expect = set(self._fmt.channel_paths())
            actual = set(self._ds_of)
            extra["format_spec"] = {
                "rate_hz": self._fmt.rate_hz,
                "source": self._fmt.source,
                "channel_count": len(expect),
                "missing_channels": sorted(expect - actual),
                "extra_channels": sorted(actual - expect),
            }
        self._info = DataInfo(
            path=path, format='hdf5', topics=topics,
            duration=end - start, start_time=start, end_time=end,
            extra=extra,
        )
        return self._info

    def _row_values(self, name: str, row: np.ndarray) -> dict:
        pk = self._ds_of.get(name.lstrip('/'), '')
        n = (pk or name).lower()
        arr = np.asarray(row, dtype=float).ravel().tolist()
        if 'velocity' in n:
            return {"velocity": arr}
        if 'torque' in n or 'effort' in n:
            return {"effort": arr}
        return {"position": arr}

    def channel_values(self, name: str, indices: List[int]) -> Optional[List[dict]]:
        if self._file is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.value_available:
            return None
        pk = self._ds_of.get(name.lstrip('/'), name.lstrip('/'))
        ds = self._file[pk]
        out = []
        for i in indices:
            try:
                out.append(self._row_values(name, ds[i]))
            except Exception:
                out.append({})
        return out

    def iter_values(self, name: str) -> Optional[Iterator[dict]]:
        if self._file is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.value_available:
            return None
        pk = self._ds_of.get(name.lstrip('/'), name.lstrip('/'))
        ds = self._file[pk]

        def gen():
            for i in range(ds.shape[0]):
                try:
                    yield self._row_values(name, ds[i])
                except Exception:
                    yield {}
        return gen()

    def image_frames(self, name: str) -> Optional[Iterator[Tuple[int, int, bytes, bytes]]]:
        if self._file is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        pk = self._ds_of.get(name.lstrip('/'), name.lstrip('/'))
        ds = self._file[pk]

        def gen():
            for i in range(ds.shape[0]):
                b = b""
                try:
                    cell = ds[i]
                    if cell is not None:
                        if isinstance(cell, (bytes, bytearray)):
                            b = bytes(cell)
                        else:
                            b = np.asarray(cell).tobytes()
                except Exception:
                    b = b""
                yield i, len(b), b[:4096], b[-16:]
        return gen()

    def image_pixels(self, name: str, indices: List[int]) -> Optional[List[Any]]:
        if self._file is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        pk = self._ds_of.get(name.lstrip('/'), name.lstrip('/'))
        ds = self._file[pk]
        out = []
        for i in indices:
            b = b""
            if 0 <= i < ds.shape[0]:
                try:
                    cell = ds[i]
                    if cell is not None:
                        b = np.asarray(cell).tobytes() if not isinstance(cell, (bytes, bytearray)) else bytes(cell)
                except Exception:
                    b = b""
            out.append(decode_image_bytes(b))
        return out

    def image_bytes(self, name: str) -> Optional[Iterator[Tuple[int, float, Any]]]:
        if self._file is None:
            return None
        ti = self._channel(name)
        if ti is None or not ti.image_available:
            return None
        pk = self._ds_of.get(name.lstrip('/'), name.lstrip('/'))
        ds = self._file[pk]
        ts = ti.timestamps or []

        def gen():
            for i in range(ds.shape[0]):
                try:
                    cell = ds[i]
                    if cell is None:
                        b = b""
                    elif isinstance(cell, (bytes, bytearray)):
                        b = bytes(cell)
                    else:
                        b = np.asarray(cell).tobytes()
                except Exception:
                    b = b""
                t = ts[i] if i < len(ts) else 0.0
                yield i, t, b
        return gen()

    def messages(self, topic_filter: Optional[Set[str]] = None) -> Iterator[RawMessage]:
        if self._file is None:
            return
        for pk in self._ds_of:
            name = f'/{pk}'
            if topic_filter and name not in topic_filter:
                continue
            ds = self._file[pk]
            for i in range(ds.shape[0]):
                try:
                    raw = ds[i].tobytes()
                except Exception:
                    raw = b''
                t = float(self._ts[i]) if self._ts is not None and i < len(self._ts) else 0.0
                yield RawMessage(topic=name, msg_type=f'hdf5/{pk}', timestamp=t, raw=raw)

    def close(self):
        if self._file is not None:
            self._file.close()
            self._file = None
