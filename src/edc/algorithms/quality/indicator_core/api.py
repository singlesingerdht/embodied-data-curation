"""对外唯一入口（Facade）：run_qc / detect / run_task。

- run_qc/detect：纯文件 API（透传 pipeline.runner）
- run_task：任务 API（HTTP 控制面：分批领取 → 质检 → 逐条回调）

设计文档：doc/HTTP控制面设计.md
"""
import logging
from pathlib import Path
from typing import Optional

from .pipeline.runner import run_qc, run_batch, detect  # noqa: F401  re-export

logger = logging.getLogger("edc.algorithms.quality.indicator_core.task")


def run_task(task_id: Optional[str] = None, repository=None,
             batch_size: Optional[int] = None,
             mode: str = "fast", workers: int = 4, output_dir: str = "output",
             robot: str = None, scenario: str = None, profile_path: str = None,
             strict: bool = False, no_points: bool = False,
             gzip: bool = False) -> dict:
    """任务式质检（HTTP-only）：分批领取 → 规模判断 → 批处理 → 逐条回写结果。

    循环：claim_batch 领取一批（默认 20，可调）→ 引擎质检该批 → 每条立即
    mark_record 回写 SUCCESS/FAIL（终态），直到任务取尽。算子不持有状态，
    CHECKING 由后端在 claim_batch 时原子写入；崩溃/回调失败不留终态，归后端
    TTL 回收重派。

    同 file_path 去重：lerobot 等数据集的多条轨迹 record 可能指向同一目录，
    任务内按 file_path 缓存质检结果，只质检一次、逐条回写（回调 payload 的
    dataset_id/segment_id/episode_id 原样回传，消费端按 episode_id 与结果
    points 的 episode 列等值匹配定位该轨迹问题）。

    repository 缺省构造 HttpTaskRepository()（env: QC_API_CLAIM_URL/QC_API_RESULT_URL 等）。
    batch_size 缺省取 repository.batch_size（HttpTaskRepository 读 QC_BATCH_SIZE），
    再缺省 20。

    返回 {"task_id", "total", "success", "failed", "records": [...]}。
    """
    from .tasks.http_source import HttpTaskRepository

    if task_id is None:
        raise ValueError("task_id 必填（任务式质检只走 HTTP 控制面）")
    if repository is None:
        repository = HttpTaskRepository()
        own_repo = True
    else:
        own_repo = False
    if not hasattr(repository, "claim_batch") or not hasattr(repository, "mark_record"):
        raise TypeError("repository 必须实现 TaskRepository 协议（claim_batch/mark_record）")
    if batch_size is None:
        batch_size = getattr(repository, "batch_size", 20)

    logger.info("run_task 开始 task_id=%s mode=%s batch_size=%s",
                task_id, mode, batch_size)
    _sev_rank = {"pass": 0, "warning": 1, "error": 2}
    out_records, ok, failed = [], 0, 0
    qc_cache: dict = {}   # file_path → 质检结果（任务内同 file_path 只质检一次）
    try:
        while True:
            batch = repository.claim_batch(task_id, limit=batch_size)
            records = batch.records
            if not records:
                logger.info("run_task 任务取尽 task_id=%s 共处理 %d 条", task_id, len(out_records))
                break                                 # 任务取尽
            logger.info("run_task 处理批次 task_id=%s 本批 %d 条 has_more=%s remaining=%d",
                        task_id, len(records), batch.has_more, batch.remaining)
            files = list(dict.fromkeys(r.file_path for r in records if r.file_path))
            if not files:
                raise ValueError(f"批次内记录缺少 file_path: {[r.id for r in records]}")
            missing = [f for f in files if not Path(f).exists()]
            if missing:
                raise FileNotFoundError(f"任务记录中的文件不存在: {missing[:5]}")

            # 去重质检：只对缓存中缺失的路径跑引擎，其余复用（跨批也生效）
            new_files = [f for f in files if f not in qc_cache]
            if new_files:
                logger.info("run_task 质检去重 task_id=%s 新文件 %d 个 / 复用缓存 %d 个",
                            task_id, len(new_files), len(files) - len(new_files))
                results = (run_batch(new_files, workers=workers, output_dir=output_dir, save=True,
                                     robot=robot, scenario=scenario, profile_path=profile_path,
                                     unpack=(mode == "deep"),
                                     no_points=no_points, gzip=gzip)
                           if len(new_files) > 1 else
                           [run_qc(new_files[0], output_dir=output_dir, save=True,
                                   robot=robot, scenario=scenario, profile_path=profile_path,
                                   unpack=(mode == "deep"), strict=strict, workers=workers,
                                   no_points=no_points, gzip=gzip)])
                # 结果对齐顺序（run_batch 按 run_qc 返回 {"file": ...}；逐记录找匹配）
                for r in results:
                    _key = r.get("file") or (r.get("file_info") or {}).get("path")
                    if _key:
                        qc_cache[_key] = r
            else:
                logger.info("run_task 质检全部复用缓存 task_id=%s 本批 %d 个文件",
                            task_id, len(files))
            by_file = {f: qc_cache.get(f, {}) for f in files}

            for rec in records:
                r = by_file.get(rec.file_path, {})
                logger.info("run_task 质检完成 record=%s dataset=%s segment=%s episode=%s file=%s",
                            rec.record_id, rec.dataset_id, rec.segment_id,
                            rec.episode_id, rec.file_path)
                inds = r.get("indicators") or []
                # level = indicators 最差档（qc-2.2 删 overview 后由消费端现算）
                level = max((z.get("level") for z in inds),
                            key=lambda s: _sev_rank.get(s, 0), default="")
                if r.get("exception") or r.get("error"):
                    # 程序异常：仍回调 FAIL + 异常信息（后端返回 422 语义拒绝，mark_record 吞掉）
                    status, summary = "FAIL", str(r.get("exception") or r.get("error"))
                else:
                    status, summary = "SUCCESS", f"level={level} indicators={len(inds)}"
                repository.mark_record(rec.record_id, status, level=level or None,
                                       result_json=r, summary=summary,
                                       dataset_id=rec.dataset_id,
                                       segment_id=rec.segment_id,
                                       episode_id=rec.episode_id)
                out_records.append({"record_id": rec.record_id, "file": rec.file_path,
                                    "status": status, "level": level, "summary": summary})
                ok += status == "SUCCESS"
                failed += status == "FAIL"
            if not batch.has_more:
                break
    finally:
        if own_repo:
            repository.close()
    return {"task_id": task_id, "total": len(out_records),
            "success": ok, "failed": failed, "records": out_records}
