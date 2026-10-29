"""质检流水线：多格式统一检测编排 + 批处理并行 + episode 边界聚合

run_qc：单数据源完整流水线（Structure→Timing→Sync→Dedup→Content→M10）
run_batch：跨文件并行批处理
_detect_episode_boundaries：M10 多 topic 同窗口异常聚合
"""

import logging
import os
import re
import time
from typing import Dict, List, Optional
from ..data.readers import DataReader, extract_timestamps
from ..filters.timing_filter import TimingFilter
from ..filters.sync_filter import SyncFilter
from ..filters.content_checks import ContentFilter
from ..report.format import format_structure_detail, format_timing_detail, format_sync_detail
from ..config.variants import resolve_and_build

from ..executor.parallel import (
    _unit_kind, _collect_units, _run_content_parallel, DEFAULT_MIN_UNITS,
)
from ..report.report import annotate_lerobot_episodes, save_full_report
from ..tools.preprocess import preprocess, should_preprocess, write_sidecar
from .context import InternalResult

logger = logging.getLogger("edc.algorithms.quality.indicator_core.pipeline")
from ..filters.root_cause import analyze_root_cause_from_indicators

# 兼容导出（qc.py 从 pipeline 导入）
__all__ = ["run_qc", "run_batch", "detect", "preprocess", "should_preprocess", "write_sidecar"]


def run_qc(path: str, output_dir: str = "output", save: bool = True,
           robot: str = None, profile_path: str = None, unpack: bool = True,
           strict: bool = False, scenario: str = None,
           workers: int = 0, no_points: bool = False,
           gzip: bool = False, mode: str = None) -> dict:
    """对单个数据源执行完整质检，返回结构化结果

    mode（质检模式，显式给定时代替 unpack）：
    - "fast"：初筛，只跑不需要解包的指标（时序/同步/去重/结构 + 图像一层压缩域）
    - "deep"：精筛，包含 fast 全部指标 + 解包类（数值内容/图像二层/字段采样）
    被跳过的指标 code 列表写入 result["skipped_indicators"]（run 节点同名），
    消费方可区分「没检」与「检了无问题」。

    workers>1 时对多文件数据集（lerobot，每集一 parquet）做**内容检测单元级
    并行**：结构/时序/同步/去重保持目录模式不变（全局语义不丢），仅把最贵的
    内容检测按 episode 分组并行，主进程归并事件（索引/时间按全局偏移平移）。
    单文件或单元不足时自动回退串行。

    unpack=False（等价 mode="fast"）：关闭所有需要「解包到内存」的检测。

    strict=False（默认）：结构 error 但数据可读（如单条损坏/漂移）时继续
    检测时序与内容；仅数据完全不可读才终止。
    strict=True：结构 error 即终止流水线。
    """
    if mode is not None:
        if mode not in ("fast", "deep"):
            raise ValueError(f"mode 仅支持 fast|deep，收到: {mode!r}")
        unpack = (mode == "deep")
    # 识别机器人并构建带 profile 的过滤器
    built = resolve_and_build(path, robot=robot, profile_path=profile_path,
                              unpack=unpack, scenario=scenario)
    profile = built["profile"]
    structure = built["structure"]
    timing_config = built["timing_config"]
    sync_config = built["sync_config"]
    content_config = built["content_config"]
    reader_hints = built["reader_hints"]

    # pipeline/dedup 阈值覆盖（来自 profile thresholds）；执行器参数（顶层 executor 段）
    pipe_cfg = profile.threshold_overrides("pipeline") if profile else {}
    dedup_cfg = profile.threshold_overrides("dedup") if profile else {}
    exec_cfg = dict(getattr(profile, "executor", None) or {})
    disabled_codes = set(built.get("disabled_codes") or ())
    # 去重族任一 code 关断 → 整组去重关闭（去重按族实现，无单通道粒度）
    if disabled_codes & {"C4", "I4", "J4", "A4", "P4"}:
        dedup_cfg["enabled"] = False

    # 内容并行拆集阈值（profile 顶层 executor 段；兼容旧 pipeline 组）
    units = _collect_units(path) if (workers or 0) > 1 else None
    unit_kind = _unit_kind(path) if units is not None else None
    if units is not None:
        min_units = int(exec_cfg.get("episode_min_units",
                                     pipe_cfg.get("episode_min_units", DEFAULT_MIN_UNITS)))
        if len(units) < min_units:
            units = None  # 数据集过小，串行更快

    ctx = InternalResult(file=path, unpack=unpack,
                         mode=mode or ("deep" if unpack else "fast"))
    logger.info("run_qc 开始 path=%s mode=%s robot=%s profile=%s workers=%s",
                path, ctx.mode, profile.robot if profile else "-",
                profile_path or "-", workers or 0)
    ctx.scenario = built.get("scenario")
    ctx.skipped_indicators = sorted(
        set(_skipped_indicator_codes(timing_config, sync_config, content_config,
                                     structure, dedup_cfg)) | disabled_codes)
    if profile is not None:
        ctx.profile = {"robot": profile.robot, "note": profile.note}
    t0 = time.time()

    stages = dict(structure=structure, timing_config=timing_config,
                  sync_config=sync_config, content_config=content_config,
                  reader_hints=reader_hints, column_roles=built.get("column_roles"),
                  pipe_cfg=pipe_cfg, dedup_cfg=dedup_cfg,
                  disabled_codes=disabled_codes, units=units, unit_kind=unit_kind,
                  workers=workers, strict=strict, path=path)

    # ===== 阶段 1：Structure =====
    logger.info("[stage] structure 开始")
    reader = _stage_structure(ctx, **stages)
    can_continue = reader is not None and bool(reader.info().topics)
    if ctx.structure.get("overall_severity") == "error" and (strict or not can_continue):
        if reader is not None:
            reader.close()
        ctx.error = "结构检查失败，终止流水线"
        ctx.timing = {"note": "结构失败终止，时序/同步跳过"}
        ctx.content = {"overall_severity": "error", "checks": [], "events": [],
                       "summary": {"error_count": 0, "warning_count": 0, "pass_count": 0}}
        ctx.episode_boundaries = []
        ctx.elapsed_sec["total"] = round(time.time() - t0, 2)
        return _assemble(ctx, timing_config=timing_config, content_config=content_config,
                         sync_config=sync_config, structure_config=structure.config,
                         pipe_cfg=pipe_cfg, dedup_cfg=dedup_cfg, save=save,
                         output_dir=output_dir, path=path,
                         no_points=no_points, gzip=gzip)
    if ctx.structure.get("overall_severity") == "error" and can_continue:
        ctx.error = None  # 结构有 error（如损坏/漂移）但数据可读，继续其余检测
        ctx["structure_partial"] = True

    # ===== 阶段 2-3：Timing + Sync =====
    info = _stage_timing_sync(ctx, reader, reader_hints, timing_config, sync_config)

    # ===== 阶段 4：Dedup =====
    logger.info("[stage] dedup 开始")
    _stage_dedup(ctx, info, dedup_cfg)

    # ===== 阶段 5：Content（可按集并行）=====
    logger.info("[stage] content 开始 (units=%d, kind=%s)",
                len(units) if units else 0, unit_kind or "-")
    _stage_content(ctx, path, units, unit_kind, workers, content_config, reader,
                   output_dir=output_dir)

    # ===== 阶段 6：M10 聚合 + episode 标注 =====
    logger.info("[stage] M10+episodes 开始")
    _stage_episodes(ctx, reader, pipe_cfg, disabled_codes)

    ctx.elapsed_sec["total"] = round(time.time() - t0, 2)
    _attach_summary(ctx)
    logger.info("run_qc 完成 path=%s 总耗时=%.2fs",
                path, ctx.elapsed_sec["total"])

    return _assemble(ctx, timing_config=timing_config, content_config=content_config,
                     sync_config=sync_config, structure_config=structure.config,
                     pipe_cfg=pipe_cfg, dedup_cfg=dedup_cfg, save=save, output_dir=output_dir,
                     path=path, no_points=no_points,
                     gzip=gzip)


# ====================== 阶段函数（只读写 InternalResult） ======================

def _stage_structure(ctx: InternalResult, structure, path, reader_hints, **_kw):
    """打开 reader + 结构检测。返回打开的 reader（失败 None）。"""
    t0 = time.time()
    reader = None
    try:
        reader = DataReader.create(path, hints=reader_hints,
                                   column_roles=_kw.get("column_roles"))
        reader.open(path)
    except Exception:
        reader = None
    sr = structure.run(path, reader)
    ctx.structure = format_structure_detail(sr)
    ctx.format = sr.bag_format
    # 统一暴露损坏 episode（lerobot_integrity 检查的 detail）
    for _c in (ctx.structure.get("checks") or []):
        _d = _c.get("detail") or {}
        if isinstance(_d, dict) and _d.get("corrupted_episodes"):
            ctx.structure["corrupted_episodes"] = _d["corrupted_episodes"]
            break
    # HDF5_FORMAT.md 结构校验（缺/多通道）与 lerobot videos 图像通道标记
    if reader is not None and reader.info() is not None:
        _extra = reader.info().extra or {}
        for _k in ("format_spec", "video_channels", "timestamp_mismatch"):
            if _extra.get(_k):
                ctx.structure[_k] = _extra[_k]  # timestamp_mismatch: 行数≠时间戳数显式上报
    ctx.elapsed_sec["structure"] = round(time.time() - t0, 2)
    logger.info("[stage] structure 完成 耗时=%.2fs format=%s 通道=%d",
                ctx.elapsed_sec["structure"], ctx.format,
                len((ctx.structure or {}).get("topics", {})))
    return reader


def _stage_timing_sync(ctx: InternalResult, reader, reader_hints,
                       timing_config, sync_config, **_kw):
    """时序/同步。返回 DataInfo（复用 open 的 reader；打不开返回 None）。

    根因关联分析已后置为质检结束后的 indicators 纯后处理器（见 filters/root_cause）。
    """
    t1 = time.time()
    info = None
    try:
        if reader is None:
            from ..data.readers import DataReader as _DR
            reader = _DR.create(ctx.file, hints=reader_hints)
            reader.open(ctx.file)
        info = reader.info()
        # 数据驱动：reader 自带帧率（lerobot info.json）且无 profile 期望频率时注入
        if not timing_config.topic_expected_freq:
            fps = (info.extra or {}).get("fps")
            if fps:
                for topic in info.topics:
                    timing_config.topic_expected_freq[topic] = float(fps)
        ts_map = extract_timestamps(info)
        # receipt_times（延迟检测 C8/M5）：与时间轴相同过滤/相对化规则
        receipt_map: Dict[str, List[float]] = {}
        _ref = info.start_time
        for _topic, _ti in info.topics.items():
            if _ti.receipt_times and len(_ti.receipt_times) == len(_ti.timestamps or []):
                receipt_map[_topic] = [
                    rt - _ref for t, rt in zip(_ti.timestamps or [], _ti.receipt_times)
                    if t == t and t not in (float("inf"), float("-inf"))]
        if not ts_map:
            ctx.timing = {"note": "数据无时间轴列，时序/同步跳过"}
        else:
            timing_results = {}
            for topic, stamps in sorted(ts_map.items()):
                if len(stamps) < 2:
                    continue
                ti = info.topics[topic]
                tr = TimingFilter(config=timing_config).scan(
                    stamps, receipt_times=receipt_map.get(topic),
                    topic=topic, msg_type=ti.msg_type)
                timing_results[topic] = format_timing_detail(tr)
            ctx.timing = timing_results
            # 每 topic 规范模态（open 时 classify 好），供输出指标按模态落 code
            ctx["_topic_modality"] = {t: (info.topics[t].modality if t in info.topics else "")
                                      for t in ts_map}
            # 同步检测（≥2 通道才跑）；sync 显式初始化
            sync = None
            if len(ts_map) >= 2:
                sync = SyncFilter(config=sync_config).analyze(ts_map)
                ctx.sync = format_sync_detail(sync)
    except Exception as e:
        ctx.timing = {"error": str(e)}
    ctx.elapsed_sec["timing"] = round(time.time() - t1, 2)
    logger.info("[stage] timing+sync+root_cause 完成 耗时=%.2fs 通道=%d",
                ctx.elapsed_sec["timing"], len(ctx.timing or {}))
    return info


def _stage_dedup(ctx: InternalResult, info, dedup_cfg, **_kw):
    """重复消息检测（C4/I4/J4/A4，按时间戳/seq 双口径）。"""
    t = time.time()
    try:
        from ..filters.dedup_filter import DedupFilter
        dedup_results = {}
        if info is not None and dedup_cfg.get("enabled", True):
            for topic, ti in info.topics.items():
                if not ti.timestamps or len(ti.timestamps) < 2:
                    continue
                r = DedupFilter(dedup_key="stamp", **{
                    k: v for k, v in dedup_cfg.items()
                    if k in ("dedup_ratio_low", "dedup_ratio_high")
                }).run_from_series(ti.timestamps, topic=topic)
                if ti.seqs is not None and len(ti.seqs) > 1 \
                        and ti.seqs != list(range(len(ti.seqs))):
                    r_seq = DedupFilter(dedup_key="seq").run_from_series(
                        ti.timestamps, ti.seqs, topic=topic)
                    if r_seq.duplicate_count > r.duplicate_count:
                        r = r_seq
                if r.duplicate_count > 0 or r.severity != "pass":
                    dedup_results[topic] = {
                        "topic": topic, "total_count": r.total_count,
                        "duplicate_count": r.duplicate_count,
                        "duplicate_ratio": r.duplicate_ratio,
                        "severity": r.severity,
                    }
        ctx.dedup = dedup_results
    except Exception as e:
        ctx.dedup = {"error": str(e)}
    ctx.elapsed_sec["dedup"] = round(time.time() - t, 2)
    logger.info("[stage] dedup 完成 耗时=%.2fs", ctx.elapsed_sec["dedup"])


def _stage_content(ctx: InternalResult, path, units, unit_kind, workers,
                   content_config, reader, output_dir: str = "output", **_kw):
    """内容检测（数值+图像两层）；多集数据集可按 episode 并行。

    deep（unpack）模式下收尾做证据提取：图像族事件（C5/C9/C10/C8）问题帧
    解码落盘 QC_OUT/evidence/...，相对路径挂 ctx.content["_evidence"] 供
    assemble 写入 indicators[].detail.evidence。
    """
    t = time.time()
    try:
        if units and len(units) >= 2:
            ctx.content = _run_content_parallel(
                path, units, content_config, workers, reader, unit_kind)
            ctx["content_unit_parallel"] = True
        else:
            cr = ContentFilter(config=content_config).analyze(path, reader=reader)
            ctx.content = cr.to_dict() if hasattr(cr, "to_dict") else {"error": "no to_dict"}
    except Exception as e:
        ctx.content = {"error": str(e)}
    # 深度质检证据提取（仅 unpack/deep；fast 不提取）
    if ctx.unpack and isinstance(ctx.content, dict) and "error" not in ctx.content:
        try:
            from ..filters.content_checks.evidence import (
                collect_evidence_requests, extract_evidence)
            reqs = collect_evidence_requests(ctx.content)
            if reqs:
                from pathlib import Path as _P
                ev = extract_evidence(reader, reqs, output_dir,
                                      dataset_name=_P(path).name)
                if ev:
                    ctx.content["_evidence"] = ev
                    logger.info("证据提取完成 topic=%s 帧数=%d",
                                sorted(ev.keys()),
                                sum(len(v) for m in ev.values() for v in m.values()))
        except Exception as exc:
            logger.warning("证据提取失败: %s", exc)
    if reader is not None:
        reader.close()
    ctx.elapsed_sec["content"] = round(time.time() - t, 2)
    logger.info("[stage] content 完成 耗时=%.2fs", ctx.elapsed_sec["content"])


def _stage_episodes(ctx: InternalResult, reader, pipe_cfg, disabled_codes, **_kw):
    """M10 多 topic 同窗口异常聚合 + lerobot episode 标注。"""
    t = time.time()
    try:
        ctx.episode_boundaries = None if "M10" in disabled_codes \
            else _detect_episode_boundaries(
                ctx.timing, ctx.content,
                window_ms=pipe_cfg.get("episode_window_ms", 200),
                min_topics=pipe_cfg.get("episode_min_topics", 3),
                merge_sec=pipe_cfg.get("episode_merge_sec", 0.5))
    except Exception as e:
        ctx.episode_boundaries = {"error": str(e)}
    if reader is not None:
        info = reader.info()
        if info is not None and info.extra.get("episodes"):
            annotate_lerobot_episodes(ctx, info)
    ctx.elapsed_sec["episode"] = round(time.time() - t, 2)
    logger.info("[stage] M10+episodes 完成 耗时=%.2fs 切段=%d",
                ctx.elapsed_sec["episode"], len(ctx.episode_boundaries or []))


def _attach_summary(ctx: InternalResult) -> None:
    """顶层统计摘要（报告页面/统计/数据库写入支撑）。"""
    st = ctx.structure or {}
    ct = ctx.content or {}
    tm = ctx.timing or {}
    if isinstance(tm, dict) and tm.get("note"):
        timing_sev = str(tm.get("note"))
    else:
        timing_sev = "pass"
        if isinstance(tm, dict):
            sevs = [d.get("severity", "pass") for d in tm.values() if isinstance(d, dict)]
            if sevs:
                timing_sev = max(sevs, key=lambda s: {"pass": 0, "warning": 1, "error": 2}.get(s, 0))
    ctx["schema_version"] = "qc-1.1"   # 内部摘要版本（对外契约 qc-2.0 由 _assemble 产出）
    ctx["summary"] = {
        "file": ctx.file,
        "format": ctx.format,
        "structure_severity": st.get("overall_severity"),
        "content_severity": ct.get("overall_severity"),
        "timing_severity": timing_sev,
        "event_count": sum(len(c.get("events", [])) for c in ct.get("checks", [])),
        "episode_boundaries": len(ctx.episode_boundaries or []),
        "elapsed_sec": ctx.elapsed_sec.get("total"),
    }


def _assemble(result, timing_config, content_config, sync_config, structure_config,
              pipe_cfg, dedup_cfg, save, output_dir, path,
              no_points: bool = False, gzip: bool = False) -> dict:
    """组装 QcResult（按 topic 原生模型，qc-2.2）+ 落盘（唯一出口）。

    结构 error 提前终止也走这里，保证 qc-2.2 契约键（schema_version/file_info/
    sensors/config/run/root_cause/indicators）与落盘始终存在。
    """
    from ..report.report import RootCause, to_topic_structure
    from dataclasses import asdict as _asdict

    def _clean(v):
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple, set)):
            return [_clean(x) for x in v]
        if isinstance(v, bool):
            return bool(v)
        if isinstance(v, int):
            return int(v)
        if isinstance(v, float):
            return float(v)
        import numpy as np
        if isinstance(v, np.bool_):
            return bool(v)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.floating):
            return float(v)
        if isinstance(v, np.ndarray):
            return [_clean(x) for x in v.tolist()]
        return v

    # pipeline/dedup 组非 dataclass：并入默认值，保证 config.thresholds 六组均为
    # "实际生效阈值"（契约 qc-2.0），无 profile 覆盖时也下发默认值
    _pipe_defaults = {
        "episode_window_ms": 200, "episode_min_topics": 3,
        "episode_merge_sec": 0.5, "episode_min_units": DEFAULT_MIN_UNITS,
    }
    _dedup_defaults = {
        "enabled": True, "dedup_ratio_low": 0.01, "dedup_ratio_high": 0.05,
    }
    result["_config"] = _clean({
        "frequencies": dict(timing_config.topic_expected_freq),
        "thresholds": {
            "timing": _asdict(timing_config),
            "content": _asdict(content_config),
            "sync": _asdict(sync_config),
            "structure": _asdict(structure_config),
            "pipeline": {**_pipe_defaults, **(dict(pipe_cfg or {}))},
            "dedup": {**_dedup_defaults, **(dict(dedup_cfg or {}))},
        },
    })
    qr = to_topic_structure(result, no_points=no_points)  # QualityResult 对象模型（类层次）
    # 根因关联分析：质检结束后基于 indicators 的纯后处理器（缺口复用 C1 已检测 gaps，口径一致）
    try:
        qr.root_cause = analyze_root_cause_from_indicators(qr.indicators)
    except Exception as rc_err:  # 失败显式记录，不静默空对象
        qr.root_cause = RootCause(verdict=f"根因分析失败：{type(rc_err).__name__}: {rc_err}")
    qc = _clean(qr.to_dict())                       # 序列化 + numpy 清洗（与落盘同一份）
    if save:
        from datetime import datetime
        from pathlib import Path as _P
        _out = _P(output_dir)
        _out.mkdir(parents=True, exist_ok=True)
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")   # 微秒级，避免同秒覆盖
        _src = _P(path)
        if _src.is_file():
            bag_name = f"{_src.stem}_{qr.file_info.format or 'unknown'}"
        else:
            bag_name = _src.name or _src.stem
        files = [save_full_report(qc, output_dir, bag_name, ts=_ts, gzip=gzip)]
        qc["saved_files"] = files
    if result.get("content_unit_parallel"):
        qc["content_unit_parallel"] = True
    return qc


def _dir_is_aggregate(path) -> bool:
    """目录是否为聚合数据集（lerobot 数据集根 / 折叠式多集）——此类目录允许直接传入。

    lerobot 例外：业务层传的必然是文件夹（数据集根），由 reader 聚合多集。
    其他目录由业务层解析为文件列表。
    """
    from ..data.ir import detect_format, is_folded_dataset
    try:
        if is_folded_dataset(path):
            return True
        return detect_format(path) in ("lerobot", "parquet")  # parquet 目录=折叠式/分表一轨迹
    except Exception:
        return False


def is_single_dataset_root(path) -> bool:
    """目录是否**本身即单一数据集根**（--local 目录短路用，精确判定）。

    只认精确 lerobot 根（detect_format == "lerobot"，含 meta/info.json）——
    其结果内已按 episode 标注，任务式靠 episode_id 匹配轨迹，整数据集一份结果。

    **折叠式/UUID 会话数据集根（fourier 等）返回 False**：轨迹粒度=数据集内的
    每个会话目录（一条轨迹一份结果），由 discover_sources 展开为会话级数据源。
    """
    from ..data.ir import detect_format
    try:
        return detect_format(path) == "lerobot"
    except Exception:
        return False


# 轨迹文件后缀白名单（discover_sources 扫描用）
_TRAJ_SUFFIXES = (".bag", ".db3", ".mcap", ".parquet", ".pq",
                  ".h5", ".hdf5", ".mp4", ".bvh")
# 跳过目录/文件（macOS/压缩残留）
_SKIP_NAMES = ("__MACOSX",)


def discover_sources(root: str) -> list:
    """扫描根目录递归发现可质检数据源（本地调试 --local 用）。

    发现规则：
    - 轨迹文件（后缀白名单 _TRAJ_SUFFIXES）直接收集；
    - 子目录经 _dir_is_aggregate 结构识别（lerobot 根/折叠式/parquet 目录）
      → 作为**单一数据源**收集、不再深入（避免把 lerobot 内部 episode
      parquet 重复收集）；
    - 普通子目录继续递归；跳过 __MACOSX/.DS_Store/._* 与死符号链接；
      符号链接只跟随指向**扫描根内**的（根外链接跳过，防逃逸/pod 外路径）。
    返回排序去重的路径列表（文件或聚合数据集根目录）。
    """
    from pathlib import Path as _P

    out: list = []
    _seen: set = set()
    _root = _P(root).resolve()

    def _scan(p: _P) -> None:
        try:
            children = sorted(p.iterdir())
        except OSError:
            return
        for child in children:
            name = child.name
            if name in _SKIP_NAMES or name == ".DS_Store" or name.startswith("._"):
                continue
            if child.is_symlink():
                try:
                    resolved = child.resolve()
                except OSError:
                    continue                       # 死链接
                if not (resolved == _root or _root in resolved.parents):
                    continue                       # 指向扫描根外：不跟随
                child = resolved
            if child.is_dir():
                s = str(child)
                if s in _seen:
                    continue
                if _dir_is_aggregate(s):
                    _seen.add(s)
                    out.append(s)        # 聚合数据集整体作为一个数据源
                else:
                    _scan(child)
            elif child.is_file() and child.suffix.lower() in _TRAJ_SUFFIXES:
                s = str(child)
                if s not in _seen:
                    _seen.add(s)
                    out.append(s)

    _scan(_P(root))
    return sorted(out)


def detect(source, robot: Optional[str] = None, scenario: Optional[str] = None,
           output_dir: str = "output", save: bool = True, unpack: bool = True,
           strict: bool = False, workers: Optional[int] = None,
           no_points: bool = False,
           gzip: bool = False):
    """纯检测 API（无"源/目录"概念）。

    语义：传**单个轨迹文件**→返回该文件检测结果（dict）；
    传**文件列表**（一批轨迹）→ 批量检测，返回 list（每文件一结果，各自落盘）。
    文件层级的解析（哪些文件、轨迹归属）由业务层完成，本模块只消费文件。
    """
    from pathlib import Path as _P
    if isinstance(source, (list, tuple)):
        return run_batch(list(source), workers=workers, output_dir=output_dir,
                         save=save, robot=robot, scenario=scenario, unpack=unpack,
                         no_points=no_points, gzip=gzip)
    if _P(source).is_dir() and not _dir_is_aggregate(source):
        raise ValueError(f"检测 API 只接受轨迹文件、文件列表或 lerobot/折叠式数据集目录（目录解析由业务层完成）: {source}")
    return run_qc(source, output_dir=output_dir, save=save,
                  robot=robot, scenario=scenario, unpack=unpack,
                  strict=strict, workers=workers, no_points=no_points,
                  gzip=gzip)
def _batch_worker(args):
    """批处理 worker（顶层函数，供 ProcessPool 序列化）"""
    path, output_dir, save, robot, profile_path, unpack, scenario, workers, no_points, gzip = args
    try:
        return run_qc(path, output_dir=output_dir, save=save,
                      robot=robot, profile_path=profile_path, unpack=unpack,
                      scenario=scenario, workers=workers, no_points=no_points,
                      gzip=gzip)
    except Exception as e:
        return {"file": path, "format": "error", "error": str(e),
                "exception": f"{type(e).__name__}: {e}"}


def _group_trajectories(paths):
    """把同一条 lerobot 轨迹的 episode parquet + videos mp4 聚合为一项。

    识别规则：文件名 episode_[0-9]+.parquet 的 lerobot 单集 + 同 episode 号的
    videos/episode_N.mp4（路径含 videos/）。返回分组列表，每组首元素为
    代表文件（episode parquet 或普通单文件），其后为该轨迹的关联 mp4。
    """
    from pathlib import Path
    groups = []
    used = set()
    for p in paths:
        p = str(p)
        if p in used:
            continue
        name = Path(p).name
        m = re.match(r"episode_(\d+)\.parquet$", name)
        if m:
            ep = m.group(1)
            related = [q for q in paths
                       if q not in used and q != p
                       and f"episode_{ep}.mp4" in Path(q).name
                       and "videos" in q.replace("\\", "/")]
            groups.append([p] + related)
            used.add(p)
            used.update(related)
        else:
            groups.append([p])
            used.add(p)
    return groups


def run_batch(paths, workers: int = 4, output_dir: str = "output",
              save: bool = False, robot: str = None, profile_path: str = None,
              unpack: bool = True, scenario: str = None,
              no_points: bool = False, gzip: bool = False) -> list:
    """并行质检：workers 为统一并行进程数上限。

    - 多源：workers 个进程跨源并行；
    - 单个 lerobot 数据集：workers 个进程内容按集并行（含在批内）。
    """
    from concurrent.futures import ProcessPoolExecutor

    # 轨迹分组：同一条 lerobot 轨迹的 parquet+videos mp4 聚合为一项（用代表文件检测）
    groups = _group_trajectories(paths)
    if not workers or workers <= 0:
        workers = os.cpu_count() or 1
    # 单源：直接 run_qc（保留内容级并行），不套进程池
    if len(groups) == 1:
        g = groups[0]
        try:
            return [run_qc(g[0], output_dir=output_dir, save=save,
                           robot=robot, profile_path=profile_path, unpack=unpack,
                           scenario=scenario, workers=workers, no_points=no_points,
                           gzip=gzip)]
        except Exception as e:
            return [{"file": g[0], "format": "error", "error": str(e),
                     "exception": f"{type(e).__name__}: {e}"}]
    # 多源：池内每个 run_qc 传 workers=1，避免内容级并行再起进程池（workers² 进程超订）
    tasks = [(g[0], output_dir, save, robot, profile_path, unpack, scenario, 1,
              no_points, gzip)
             for g in groups]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_batch_worker, tasks))
    return results
def _content_check_topic(check_name: str) -> str:
    """从内容检查名提取通道名"""
    for prefix in ("content_image_pixels_", "content_image_", "content_imu_bias_",
                   "content_imu_", "content_joint_", "content_cmd_",
                   "content_pose_", "content_numeric"):
        if check_name.startswith(prefix):
            return check_name[len(prefix):]
    return check_name


def _detect_episode_boundaries(timing_results, content_result,
                               window_ms: float = 200, min_topics: int = 3,
                               merge_sec: float = 0.5) -> list:
    """M10 episode 一致性：聚合各通道 warning/error 级检测事件，找同一 200ms 窗口内
    ≥min_topics 个不同 topic 同时异常的切段点（重启/reset 语义不连续）"""
    events = []  # (time, topic)
    if isinstance(timing_results, dict):
        for topic, d in timing_results.items():
            if not isinstance(d, dict):
                continue
            for ev in d.get("events_timeline", []):
                if ev.get("severity") not in ("warning", "error"):
                    continue
                t = ev.get("time") or ev.get("start_time")
                if t is not None:
                    events.append((float(t), str(topic)))
    if isinstance(content_result, dict):
        for c in content_result.get("checks", []):
            topic = _content_check_topic(c.get("name", ""))
            for e in c.get("events", []):
                if e.get("severity") not in ("warning", "error"):
                    continue
                if e.get("index") == -1:
                    continue  # 相机级事件（无有效帧 index/time=0 哨兵）不参与切段定位
                t = e.get("time")
                if t is not None and float(t) >= 0:
                    events.append((float(t), topic))

    if len(events) < min_topics:
        return []
    events.sort(key=lambda x: x[0])
    win = window_ms / 1000.0
    boundaries = []
    i = 0
    n = len(events)
    while i < n:
        topics = {events[i][1]}
        j = i + 1
        while j < n and events[j][0] - events[i][0] <= win:
            topics.add(events[j][1])
            j += 1
        if len(topics) >= min_topics:
            boundaries.append({
                "time": round(events[i][0], 3),
                "topic_count": len(topics),
                "topics": sorted(topics),
                "event_count": j - i,
            })
            i = j
        else:
            i += 1
    # 合并 0.5s 内的相邻边界为一次切段
    merged = []
    for b in boundaries:
        if merged and b["time"] - merged[-1]["time"] <= merge_sec:
            merged[-1] = b
        else:
            merged.append(b)
    return merged


# ================= 内容检测单元级并行（多文件数据集，如 lerobot） =================
# 结构/时序/同步/去重保持目录模式（全局语义不丢）；只把最贵的内容检测按
# episode 分组并行：每个 worker 用 open_files() 打开自己那组 episode 作为
# 迷你 lerobot 数据集，跑 ContentFilter，返回迷你局部事件 + 全局偏移，主进程归并。

_SEV_RANK = {"pass": 0, "warning": 1, "error": 2}

# 单元并行最低集数：进程 spawn + 冷导入 ~2-5s；读层优化后内容已很便宜，
# 拆集只在数据集足够大时才划算（实测 89 集内容 ~4.4s < spawn 成本 → 串行更快）
_SPLIT_MIN_UNITS = 200
# fourier 每集读 850MB+ 图像字节，拆集收益大，2 集即可拆
_SPLIT_MIN_UNITS_FOURIER = 2

# ---- 指标-参数映射（pipeline 组非 dataclass；供 --config-dump / 完整性校验）----
INDICATOR_PARAMS: Dict[str, List[str]] = {
    'episode_consistency(M10)': ['episode_min_topics', 'episode_window_ms', 'episode_merge_sec'],
    'content_parallel': ['episode_min_units'],
}


def _skipped_indicator_codes(timing_config, sync_config, content_config,
                             structure_filter, dedup_cfg) -> list:
    """按当前生效开关计算被跳过的指标 code 列表（来自 Indicator 注册表）。

    快速模式（mode=fast / unpack=False）跳过：数值内容（I5/I6/I7/J5/J7/A5/A7/P5/P7）
    与字段稳定性采样（M7）；图像一层压缩域检测保留（C5/C9/C10 部分仍在跑）。
    """
    from ..indicators.registry import Indicator, Modality, family, _FAMILY_SEMANTIC

    def _fam(sem: str) -> list:
        return [i.code for i in Indicator if i.name_zh == sem and i.implemented]

    skipped = set()
    # content：解包/数值开关 → 数值族
    if not content_config.unpack or not content_config.numeric_enabled:
        skipped |= {"I5", "I6", "I7", "J5", "J7", "A5", "A7", "P5", "P7"}
    elif not content_config.imu_bias_detect:
        skipped.add("I6")
    # structure：字段稳定性采样（M7 需解包）
    if not structure_filter.config.unpack:
        skipped.add("M7")
    # timing：单项开关
    t = timing_config
    if not t.drop_enabled:
        skipped |= set(_fam("掉帧/缺帧"))
    if not t.jitter_enabled:
        skipped |= set(_fam("抖动/帧率不稳"))
    if not t.out_of_order_enabled:
        skipped |= set(_fam("乱序"))
    if not t.delay_enabled:
        skipped.add("M5")
    # sync：单项开关
    s = sync_config
    if not s.coverage_enabled:
        skipped.add("M2")
    if not s.alignment_enabled:
        skipped.add("M1")
    if not s.stability_enabled:
        skipped.add("M6")
    # dedup
    if not (dedup_cfg or {}).get("enabled", True):
        skipped |= {c for c in _FAMILY_SEMANTIC["duplicate"] if Indicator[c].implemented}
    return sorted(skipped)
