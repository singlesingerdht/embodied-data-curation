"""内容检测单元级并行（lerobot/fourier 按 episode 拆进程）

多文件数据集（每集一个 parquet 文件 / 文件夹）的内容检测是各集独立的
map-reduce：worker 处理自己的 episode 组，主进程按全局偏移归并事件。
拆集阈值由 profile `thresholds.pipeline.episode_min_units` 控制。
"""

from pathlib import Path
from typing import Optional

import numpy as np

# 内容并行默认拆集阈值（可由 profile thresholds.pipeline.episode_min_units 覆盖）
DEFAULT_MIN_UNITS = 200

_SEV_RANK = {"pass": 0, "warning": 1, "error": 2}


def _unit_kind(path: str) -> Optional[str]:
    """数据集类型：lerobot（每集=parquet 文件） / dataset（折叠式，每集=文件夹）。"""
    from ..data.ir import detect_format, is_folded_dataset
    try:
        if is_folded_dataset(path):
            return "dataset"
        fmt = detect_format(path)
    except Exception:
        return None
    if fmt == "lerobot":
        return "lerobot"
    return None


def _collect_units(path: str):
    """识别可并行单元：
    - lerobot：每集一个 parquet 文件
    - fourier：每集一个文件夹（含 parquet 分表）
    单文件/其它返回 None（自动回退串行 run_qc）。
    """
    kind = _unit_kind(path)
    if kind is None:
        return None
    p = Path(path)
    if kind == "lerobot":
        files = (sorted(p.glob("episode_*.parquet"))
                 or sorted(p.glob("data/chunk-*/episode_*.parquet")))
        return [str(f) for f in files] or None
    # 折叠式数据集：递归找 episode 文件夹
    from ..data.readers.dataset import find_episode_dirs
    eps = find_episode_dirs(p)
    return [str(e) for e in eps] or None


def _content_worker(args):
    """单组 episode 的内容检测（顶层函数，供 ProcessPool 序列化）。

    args = (group, dataset_path, config, base_index, base_time, kind)
    kind: lerobot（group=parquet 文件） / fourier（group=episode 文件夹）
    返回 (content_dict, base_index, base_time)。
    """
    group, dataset_path, config, base_index, base_time, kind = args
    from ..filters.content_checks import ContentFilter
    try:
        if kind in ("fourier", "dataset"):
            # 折叠式数据集（fourier/zerith/luoshi）：每集=文件夹，用数据集聚合 reader
            # 打开组内全部 episode（此前误用 LerobotReader 打开文件夹 → 内容检测静默为空）
            from ..data.readers.dataset import DatasetReader
            sub = DatasetReader()
            sub.open_episodes(group)
        else:
            from ..data.readers.lerobot import LerobotReader
            sub = LerobotReader()
            sub.open_files(group)
        try:
            cr = ContentFilter(config=config).analyze(dataset_path, reader=sub)
            d = cr.to_dict() if hasattr(cr, "to_dict") else {
                "checks": [], "overall_severity": "error", "summary": {}}
        finally:
            sub.close()
        return {"content": d, "base_index": base_index, "base_time": base_time}
    except Exception as e:
        return {"content": None, "error": f"{type(e).__name__}: {e}",
                "base_index": base_index, "base_time": base_time}


def _chunk(items, n):
    """把列表尽量均分为 n 组。"""
    k = max(1, min(n, len(items)))
    size = (len(items) + k - 1) // k
    return [items[i:i + size] for i in range(0, len(items), size)]


def _run_content_parallel(path, units, content_config, content_workers, reader, kind):
    """内容检测按 episode 分组并行，返回归并后的 ContentResult dict。

    layer1（压缩域）+ 数值按组并行；layer2（像素采样）固定回主进程以保证
    与串行一致的全局采样。base_index/base_time 从数据集 reader 的全局
    时间轴推算（损坏集 rows=0，迷你读取器跳过，偏移仍对齐）。
    """
    from concurrent.futures import ProcessPoolExecutor
    from dataclasses import replace
    ep_rows = {}
    ep_start = {}
    if reader is not None:
        info = reader.info()
        for ep in info.extra.get("episodes", []):
            ep_rows[ep.get("file")] = ep.get("rows", 0)
            ep_start[ep.get("file")] = float(ep.get("start_time", 0.0) or 0.0)
    rows = [ep_rows.get(Path(u).name, 0) for u in units]
    groups = _chunk(units, content_workers)
    # lerobot：层2 采样固定主进程保证确定性；fourier/dataset：每 worker 一个 episode 组，
    # 各自按 profile 的抽查采样（head/mid/tail 各1帧）做像素质量检查
    worker_config = content_config if kind in ("fourier", "dataset")         else replace(content_config, image_layer2=False)
    tasks = []
    idx = 0
    for g in groups:
        base_index = sum(rows[:idx])
        # 全局相对偏移（与串行事件 time 同一口径：相对数据集起点秒）。
        # 用 episode.start_time 而非时间列索引：schema 漂移（某集缺时间列）
        # 时时间列会短于累计行数，索引映射会错位（此前取 ts_ch[base_index]）
        base_time = ep_start.get(Path(g[0]).name, 0.0)
        tasks.append((g, path, worker_config, base_index, base_time, kind))
        idx += len(g)
    with ProcessPoolExecutor(max_workers=content_workers) as ex:
        parts = list(ex.map(_content_worker, tasks))
    merged = _merge_content_parts(parts)
    if content_config.unpack and content_config.image_layer2 and reader is not None \
            and kind == "lerobot":
        _layer2_in_main(reader, content_config, merged)
    return merged


def _check_to_dict(c):
    """CheckResult -> dict（与 ContentResult.to_dict 的 check 结构一致）。"""
    return {
        "name": c.name, "passed": c.passed, "severity": c.severity,
        "message": c.message,
        "events": [
            {"type": e.type, "severity": e.severity, "topic": e.topic,
             "index": e.index, "time": e.time,
             "range": e.detail.get("range", [e.index, e.index]),
             "detail": e.detail}
            for e in c.events
        ],
        "detail": c.detail,
    }


def _layer2_in_main(reader, config, merged):
    """主进程跑 layer2（像素采样 9 帧/通道），保证全局采样确定性。

    只读取包含采样位置的少数 episode 文件（避免重复全量读图像字节）。
    """
    from ..filters.content_checks import ContentFilter
    from ..data.ir import MODALITY_IMAGE
    from ..data.ir import decode_image_bytes
    import pyarrow.parquet as pq
    info = reader.info()
    cf = ContentFilter(config=config)
    ep_rows = [ep.get("rows", 0) for ep in info.extra.get("episodes", [])]
    # 公开能力接口（DataReader 契约）：episode 文件布局；无此能力的 reader 返回空
    files = list(reader.episode_files()) if hasattr(reader, "episode_files") else []
    keep = [c for c in merged["checks"]
            if not c["name"].startswith("content_image_pixels_")]
    # 从合并的层1检查收集异常帧（自适应采样的焦点）
    anomaly_by_channel = {}
    for c in merged["checks"]:
        if not c["name"].startswith("content_image_"):
            continue
        ch = c["name"][len("content_image_"):]
        anomaly = set()
        for e in c.get("events", []):
            if e.get("index") is not None:
                anomaly.add(e["index"])
            rng = e.get("range")
            if rng and rng[0] is not None and rng[1] is not None:
                a, b = rng[0], rng[1]
                anomaly.update((a, (a + b) // 2, b))
        anomaly_by_channel[ch] = anomaly
    for name, ti in info.topics.items():
        if ti.modality != MODALITY_IMAGE or not ti.image_available or ti.count < 3:
            continue
        base = cf.sample_positions(ti.count)
        positions = cf.focus_positions(ti.count, base, anomaly_by_channel.get(name, set()))
        frames_px = [None] * len(positions)
        cache = {}
        cum = 0
        for fi, f in enumerate(files):
            rows = ep_rows[fi] if fi < len(ep_rows) else 0
            lo, hi = cum, cum + rows
            want = [i for i, p in enumerate(positions) if lo <= p < hi]
            if want:
                # 文件缺该列（schema 漂移）时跳过，保持偏移
                if hasattr(reader, "file_columns") \
                        and name not in reader.file_columns().get(f.name, set()):
                    cum = hi
                    continue
                col = cache.get(fi)
                if col is None:
                    cache[fi] = pq.read_table(f, columns=[name]).column(0).to_pylist()
                    col = cache[fi]
                for i in want:
                    p = positions[i]
                    v = col[p - lo]
                    b = b""
                    if isinstance(v, dict) and isinstance(v.get("bytes"), (bytes, bytearray)):
                        b = bytes(v["bytes"])
                    elif isinstance(v, (bytes, bytearray)):
                        b = bytes(v)
                    elif isinstance(v, list) and v and isinstance(v[0], dict) \
                            and isinstance(v[0].get("bytes"), (bytes, bytearray)):
                        b = bytes(v[0]["bytes"])
                    frames_px[i] = decode_image_bytes(b)
            cum = hi
        if not any(f is not None for f in frames_px):
            continue
        check = cf.check_image_pixels(name, ti, frames_px, positions)
        # 层2 事件富集（与串行 _enrich_events 同口径）：补 time/range 定位，
        # 否则并行模式层2 事件 time 恒为 0.0（三种时间语义并存）
        ts_arr = np.asarray(ti.timestamps, dtype=float) if ti.timestamps else None
        base = info.start_time
        for ev in check.events:
            if ev.index is not None and ev.index >= 0 \
                    and ts_arr is not None and ev.index < len(ts_arr):
                ev.time = float(ts_arr[ev.index] - base)
            if ev.index is not None and ev.index >= 0 and "range" not in ev.detail:
                ev.detail["range"] = [ev.index, ev.index]
        keep.append(_check_to_dict(check))
    merged["checks"] = keep
    merged["events"] = [e for c in keep for e in c["events"]]
    merged["overall_severity"] = _sev_max(keep)
    merged["summary"] = {
        "error_count": sum(1 for c in keep if c["severity"] == "error"),
        "warning_count": sum(1 for c in keep if c["severity"] == "warning"),
        "pass_count": sum(1 for c in keep if c["severity"] == "pass"),
        "unit_parallel": True,
    }


def _merge_content_parts(parts):
    """按通道归并各组内容事件；事件 index/time 平移全局偏移。"""
    checks = {}
    part_errors = [p.get("error") for p in parts if p.get("error")]
    for part in parts:
        ct = part.get("content")
        if not ct:
            continue  # 失败组在下方汇总中显式暴露（part_errors），不静默丢失
        base_index = part.get("base_index", 0)
        base_time = part.get("base_time", 0.0)
        for c in ct.get("checks", []):
            name = c.get("name", "")
            t = checks.setdefault(name, {
                "name": name, "passed": True, "severity": "pass",
                "message": "", "events": [], "detail": None,
            })
            if _SEV_RANK.get(c.get("severity"), 0) > _SEV_RANK[t["severity"]]:
                t["severity"] = c["severity"]
                t["passed"] = c.get("passed", True)
                t["message"] = c.get("message", "")
                if c.get("detail") is not None:
                    t["detail"] = c["detail"]   # 回填最高档 part 的细节（freeze_segments 等）
            elif t["detail"] is None and c.get("detail") is not None:
                t["detail"] = c["detail"]
            for e in c.get("events", []):
                shifted = dict(e)
                if e.get("index") is not None:
                    shifted["index"] = int(e["index"]) + base_index
                if e.get("time") is not None:
                    shifted["time"] = round(e["time"] + base_time, 3)
                if e.get("range") and e["range"][0] is not None:
                    shifted["range"] = [e["range"][0] + base_index,
                                        (e["range"][1] + base_index)
                                        if e["range"][1] is not None else None]
                t["events"].append(shifted)
    sev = _sev_max(checks.values()) if checks else "pass"
    if part_errors and _SEV_RANK[sev] < _SEV_RANK["warning"]:
        sev = "warning"  # 部分内容检测失败：不能报 pass（存在未覆盖的集）
    return {
        "bag_path": None, "checks": list(checks.values()),
        "events": [e for c in checks.values() for e in c["events"]],
        "overall_severity": sev,
        "summary": {
            "error_count": sum(1 for c in checks.values() if c["severity"] == "error"),
            "warning_count": sum(1 for c in checks.values() if c["severity"] == "warning"),
            "pass_count": sum(1 for c in checks.values() if c["severity"] == "pass"),
            "unit_parallel": True,
            "part_errors": part_errors,   # 失败组明细（异常信息），评审可追溯
            "failed_parts": len(part_errors),
        },
    }


def _sev_max(items):
    best = "pass"
    for it in items:
        if _SEV_RANK.get(it.get("severity"), 0) > _SEV_RANK[best]:
            best = it["severity"]
    return best
