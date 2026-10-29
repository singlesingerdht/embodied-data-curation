"""预处理：parquet 重写为多 row-group + side-car 尺寸/头尾索引

针对 fourier 类「未压缩 list<uint8> 图像列 + 单 row-group」的源：
- 多 row-group 让 pyarrow 并行读（3.5×）
- side-car 索引让层1 免读整列图像数据
建议仅在会复检的大数据集上用（一次性检查直接跑更快）。
"""

from pathlib import Path
from typing import Optional


def preprocess(source: str, output_dir: Optional[str] = None,
               row_group_size: int = 200, workers: int = 4,
               rewrite: bool = False):
    """预处理。

    **默认（rewrite=False，方案 B，2026-08）**：仅在源 parquet 旁生成 side-car 索引
    （`<parquet>.idx.parquet`），不重写、不镜像；返回生成的 idx 文件路径列表。
    检测端自动发现索引 → 层1 免读整列（fourier 大列 ~30× 提速）。

    **rewrite=True（完整预处理）**：镜像目录 + 多 row-group 重写 + side-car 索引；
    返回镜像目录路径（检测时需指向该路径）。

    两者都**默认不开启**，需显式调用（CLI `--preprocess` 默认 sidecar，`--preprocess --rewrite` 走镜像）。

    Args:
        source:      源目录（fourier 数据集根 / episode 文件夹 / 任意含 parquet 目录）
        output_dir:  rewrite=True 时输出目录（默认 <source>_preprocessed）；sidecar 模式忽略
        row_group_size: 重写行组大小（默认 200 帧/组，仅 rewrite）
        workers:     并行进程数（默认 4）
        rewrite:     False（默认）= 仅源旁写 side-car 索引；True = 完整镜像重写

    Returns: sidecar 模式 → idx 文件路径列表；rewrite 模式 → 镜像目录路径
    """
    if not rewrite:
        return write_sidecar(source, workers=workers)
    import shutil as _sh
    src = Path(source)
    if not src.is_dir():
        raise ValueError(f"预处理需目录输入: {source}")
    out = Path(output_dir) if output_dir else Path(str(src) + "_preprocessed")
    if out == src:
        raise ValueError("输出目录不能等于源目录")
    out.mkdir(parents=True, exist_ok=True)
    # 先镜像目录结构 + 复制非 parquet 文件
    pq_files = []
    for f in src.rglob("*"):
        if "__MACOSX" in f.parts or f.name.startswith("._") or f.name == ".DS_Store":
            continue
        rel = f.relative_to(src)
        dst = out / rel
        if f.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        elif f.suffix.lower() in (".parquet", ".pq"):
            pq_files.append((str(f), str(dst)))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(f, dst)
    # 并行重写 parquet
    from concurrent.futures import ProcessPoolExecutor
    if workers <= 1 or len(pq_files) <= 1:
        for s, d in pq_files:
            _rewrite_parquet((s, d, row_group_size))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_rewrite_parquet, [(s, d, row_group_size) for s, d in pq_files]))
    return str(out)


def write_sidecar(source: str, workers: int = 4) -> list:
    """方案 B：仅在源 parquet 旁生成 side-car 索引（不重写 parquet、不建镜像）。

    对含大图像列的 parquet 写 `<parquet 去后缀>.idx.parquet`（size/head/tail，几 MB），
    检测端 `ParquetReader.image_frames` 自动发现 → 层1 免读整列（fourier 大列 ~30× 提速）。
    源数据零改动（只新增旁挂索引文件），适合"源不动、只挂索引"的轻量预处理。

    Returns: 生成的 idx 文件路径列表
    """
    src = Path(source)
    if not src.is_dir():
        raise ValueError(f"side-car 索引需目录输入: {source}")
    targets = []
    for f in sorted(src.rglob("*.parquet")):
        if "_preprocessed" in f.parts or "__MACOSX" in f.parts:
            continue
        base = str(f)[:-len(f.suffix)]
        # 已有旧式单文件索引或任一按列索引（多图列修复后按列命名）→ 跳过
        if Path(base + ".idx.parquet").exists() \
                or list(Path(base).parent.glob(Path(base).name + ".*.idx.parquet")):
            continue
        if _has_large_column(f):
            targets.append(f)
    if not targets:
        return []

    import pyarrow.parquet as pq

    def _one(f: Path):
        try:
            tbl = pq.read_table(f, pre_buffer=True)
            return _write_image_index(tbl, f)   # 返回实际生成的按列索引路径列表
        except Exception:
            return []

    if workers <= 1 or len(targets) <= 1:
        out = [_one(f) for f in targets]
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(_one, targets))
    return [str(p) for sub in out for p in sub]


def _has_large_column(f: Path) -> bool:
    """parquet 是否含大列（未压缩 >50MB）——有则可能含大图像列，值得写 side-car。

    遍历全部 row group 按列累计未压缩大小（只查第 0 行组会漏判多 row-group 大列）。
    """
    import pyarrow.parquet as pq
    try:
        pf = pq.ParquetFile(f)
        per_col = [0] * pf.metadata.num_columns
        for rg in range(pf.metadata.num_row_groups):
            rg_md = pf.metadata.row_group(rg)
            for i in range(rg_md.num_columns):
                per_col[i] += rg_md.column(i).total_uncompressed_size
        return any(s > 50 * 1e6 for s in per_col)
    except Exception:
        return False


def should_preprocess(source: str) -> bool:
    """判断预处理是否划算：源含大图列（未压缩 list<uint8>/binary > 50MB）才值得。

    小源/无大图列（r2v2/lerobot/bag/hdf5 等）检测本来就快，跳过预处理。
    """
    src = Path(source)
    if not src.is_dir():
        return False
    for f in list(src.rglob("*.parquet"))[:32]:
        if "_preprocessed" in f.parts or "__MACOSX" in f.parts:
            continue
        if _has_large_column(f):
            return True
    return False


def _rewrite_parquet(sd):
    """模块级 worker：把 parquet 重写为多 row-group，并为图像列写 side-car 索引"""
    import shutil as _sh
    import pyarrow.parquet as pq
    s, d, row_group_size = sd
    try:
        tbl = pq.read_table(s, pre_buffer=True)
        pq.write_table(tbl, d, row_group_size=row_group_size)
        _write_image_index(tbl, d)
    except Exception:
        _sh.copy2(s, d)


def _write_image_index(tbl, dst_parquet: Path):
    """为图像列写 side-car 索引 <dst>.idx.parquet（每帧 size/head/tail）。

    层1 的黑/白/模糊/冻结/码流只依赖尺寸+头尾字节；索引仅几 MB，
    使层1 免读整列图像数据。非图像列或无大图列时跳过。
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    from ..data.readers.parquet import ParquetReader
    dst_parquet = Path(dst_parquet)
    sch = tbl.schema
    written: list = []
    for i in range(len(sch)):
        f = sch.field(i)
        typ = f.type
        # 与 readers/parquet.py 一致：ListType 按 value_type 判断（兼容 pyarrow
        # item/element 命名差异，如 list<item: uint8> 与 list<element: uint8>）
        is_img = False
        if f.name.lower() not in ("head", "tail", "size"):
            if isinstance(typ, pa.ListType):
                is_img = str(typ.value_type) in ("uint8", "binary")
            elif "binary" in str(typ):
                is_img = True
        if not is_img:
            continue
        arr = tbl.column(i)
        n = len(arr)
        sizes = np.zeros(n, dtype=np.int32)
        heads = [None] * n
        tails = [None] * n
        for r in range(n):
            b = ParquetReader._array_row_bytes(arr, r)
            sizes[r] = len(b)
            heads[r] = b[:4096]
            tails[r] = b[-16:]
        # 按列命名索引（<parquet>.<列名>.idx.parquet）：多图像列文件各列独立索引，
        # 避免共用单文件索引导致 image_frames 读到错误列的尺寸（列错位）
        idx_path = Path(str(dst_parquet)[:-len(dst_parquet.suffix)]
                        + "." + f.name + ".idx.parquet")
        pq.write_table(pa.table({"size": sizes,
                                 "head": pa.array(heads, type=pa.binary()),
                                 "tail": pa.array(tails, type=pa.binary())}),
                       idx_path)
        written.append(idx_path)
    return written
