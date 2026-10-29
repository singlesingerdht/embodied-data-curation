"""多格式统一质检命令行入口（纯 CLI）

流水线逻辑见 pipeline/runner.py；本模块仅提供命令行参数解析与结果展示。

用法:
    uv run python -m edc.algorithms.quality.indicator_core <path> [--mode fast|deep] [--json]
    uv run python -m edc.algorithms.quality.indicator_core <path> --robot leju
    uv run python -m edc.algorithms.quality.indicator_core <path> --profile my_profile.yaml
    uv run python -m edc.algorithms.quality.indicator_core f1 f2 ... --workers 4          # 批处理并行
    uv run python -m edc.algorithms.quality.indicator_core --task-id T1                   # 任务式（HTTP 控制面）
    uv run python -m edc.algorithms.quality.indicator_core <path> --local                 # 本地调试（忽略 task_id env）
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from ..pipeline.runner import run_qc, run_batch
from ..api import run_task

# 环境变量只提供 CLI 参数的默认值（部署参数，不进 yaml 链）：
# 生效优先级 CLI 实参 > 环境变量 > 代码默认；阈值/指标开关仍只走 profile（结构化配置不塞扁平 env）
# 注意在 _build_parser 内读取（非模块级常量）：import 后设置 env 仍可生效（测试/嵌入式均如此）


def _build_parser() -> argparse.ArgumentParser:
    _env_mode = os.environ.get("QC_MODE")
    _env_workers = os.environ.get("QC_WORKERS")
    _env_out = (os.environ.get("OUTPUT_MOUNT_PATH")
                or os.environ.get("output") or os.environ.get("QC_OUT"))
    _env_task_id = os.environ.get("QC_TASK_ID")
    _env_local = os.environ.get("QC_LOCAL")
    _env_batch = os.environ.get("QC_BATCH_SIZE")
    ap = argparse.ArgumentParser(description="多格式统一质检 CLI")
    # env 值显式校验（argparse 对 default 不做 choices/type 校验，非法值须在此拦下）
    if _env_mode is not None and _env_mode not in ("fast", "deep"):
        ap.error(f"环境变量 QC_MODE 非法: {_env_mode!r}（可选 fast|deep）")
    if _env_workers is not None:
        try:
            int(_env_workers)
        except ValueError:
            ap.error(f"环境变量 QC_WORKERS 非法: {_env_workers!r}（须为整数）")
    if _env_batch is not None:
        try:
            int(_env_batch)
        except ValueError:
            ap.error(f"环境变量 QC_BATCH_SIZE 非法: {_env_batch!r}（须为正整数）")
    ap.add_argument("paths", nargs="*", help="轨迹文件或文件列表（bag/mcap/parquet/hdf5/lerobot/mp4/bvh）")
    ap.add_argument("--mode", choices=["fast", "deep"], default=_env_mode or "fast",
                    help="质检模式：fast=初筛（默认，不解包，速度快）；deep=精筛（含 fast 全部 + 数值内容/图像二层/字段采样）；env: QC_MODE")
    # argparse 对字符串 default 会应用 type 转换（env 值非法时在此报错，含参数名）
    ap.add_argument("--workers", type=int, default=_env_workers or 4,
                    help="统一并行进程数（文件列表批量与内容单元并行共用，默认 4）；env: QC_WORKERS")
    ap.add_argument("--out", default=_env_out or "output",
                    help="JSON 报告输出目录；env: OUTPUT_MOUNT_PATH（平台注入，优先；兼容 output/QC_OUT）")
    ap.add_argument("--robot", default=None, help="指定机器人名（跳过路径自动识别）")
    ap.add_argument("--scenario", default=None, help="指定场景（跳过路径自动识别，profiles/<场景>/<机型>.yaml）")
    ap.add_argument("--profile", default=None, help="显式变体 profile YAML 路径（不存在将直接报错）")
    ap.add_argument("--no-unpack", action="store_true",
                    help="[兼容别名] 等价 --mode fast；显式给出且与 --mode 冲突时以 --mode 为准")
    ap.add_argument("--strict", action="store_true",
                    help="结构 error 即终止流水线（默认：数据可读时继续检测其余部分）")
    ap.add_argument("--no-compact", action="store_true",
                    help="人类可读输出显示全部指标（默认只列 warning/error 问题指标；--json 契约恒为全量，不受此开关影响）")
    ap.add_argument("--no-points", action="store_true",
                    help="丢弃逐事件点位明细（points 数组，占体积大头），保留 detail 汇总；根因分析随之降级为 detail 级统计")
    ap.add_argument("--gzip", action="store_true",
                    help="gzip 无损压缩落盘为 .json.gz（JSON 默认去缩进，体积 -50%%+；gzip 再 -90%%；读写端 gunzip 后即原 JSON）")
    ap.add_argument("--task-id", default=_env_task_id,
                    help="任务式质检：HTTP 控制面按任务 ID 分批领取并逐条回调；缺省读 env QC_TASK_ID")
    ap.add_argument("--local", action="store_true",
                    default=(_env_local or "").strip().lower() in ("1", "true", "yes", "on"),
                    help="本地调试模式：忽略 task_id（QC_TASK_ID/--task-id）强制走文件式质检 paths，"
                         "不调用 HTTP 控制面；env: QC_LOCAL（1/true/yes/on）")
    ap.add_argument("--batch-size", type=int, default=_env_batch or 20,
                    help="任务式质检批大小（每批领取条数，默认 20）；env: QC_BATCH_SIZE")
    ap.add_argument("--profiles", action="store_true", help="列出可用变体 profile")
    ap.add_argument("--config-dump", action="store_true",
                    help="按指标打印检测参数对照（指标→参数→defaults基线→生效值→覆盖来源）")
    ap.add_argument("--json", action="store_true", help="打印 JSON 而不是可读摘要（进度信息走 stderr，stdout 保持纯 JSON）")
    ap.add_argument("--preprocess", action="store_true",
                    help="预处理后再检测（默认：仅在源 parquet 旁生成 side-car 索引，不重写/不镜像；配合 --rewrite 走完整镜像重写）")
    ap.add_argument("--rewrite", action="store_true",
                    help="配合 --preprocess：完整预处理（镜像目录 + 多 row-group 重写 + side-car 索引）")
    return ap


def main():
    # 日志统一到 stderr：--json 时 stdout 保持纯 JSON；库调用方自行配置 logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    ap = _build_parser()
    args = ap.parse_args()

    from ..config.variants import available_profiles, available_scenarios
    if args.profiles:
        print("可用机器人 profile:")
        for r in available_profiles():
            print(f"  - {r}")
        print("可用场景:")
        for s in available_scenarios():
            print(f"  - {s}/")
        return

    if args.config_dump:
        if not args.paths:
            ap.error("--config-dump 需要至少一个数据路径")
        from ..indicators.params import config_dump
        config_dump(args.paths[0], robot=args.robot, profile_path=args.profile,
                    scenario=args.scenario, unpack=(args.mode == "deep"))
        return

    # 任务式质检（--task-id）：HTTP 控制面分批领取 → 质检 → 逐条回调
    if args.task_id and not args.local:
        if args.paths:
            print(f"task_id 已设置（{args.task_id}）：命令行 paths 被忽略，走任务式；"
                  f"如需文件式质检请加 --local", file=sys.stderr)
        print(f"任务 {args.task_id}：分批领取（batch={args.batch_size}），"
              f"mode={args.mode}，workers={args.workers}", file=sys.stderr)
        summary = run_task(task_id=args.task_id, batch_size=args.batch_size,
                           mode=args.mode, workers=args.workers, output_dir=args.out,
                           robot=args.robot, scenario=args.scenario,
                           profile_path=args.profile, strict=args.strict,
                           no_points=args.no_points,
                           gzip=args.gzip)
        for row in summary["records"]:
            print(f"  [{row['status']:7}] {row['file']}  level={row['level'] or '-'}  {row['summary'][:80]}",
                  file=sys.stderr)
        print(json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str))
        return

    # 纯文件 API：接受轨迹文件或文件列表（无目录/源概念，目录解析由业务层完成）
    if args.local and args.task_id:
        print(f"本地调试模式（--local）：忽略 task_id={args.task_id}，走文件式质检",
              file=sys.stderr)
    paths = args.paths
    if not paths:
        ap.print_help()
        sys.exit(1)
    from ..pipeline.runner import _dir_is_aggregate, discover_sources, is_single_dataset_root
    dirs = [p for p in paths if Path(p).is_dir()]
    if dirs:
        if args.local:
            # 本地调试：普通目录=扫描根，递归发现数据源；
            # 目录本身即单一数据集根（lerobot/UUID 折叠式）时保留整体
            expanded: list = [p for p in paths if not Path(p).is_dir()]
            for d in dirs:
                if is_single_dataset_root(d):
                    expanded.append(d)
                else:
                    expanded += discover_sources(d)
            paths = list(dict.fromkeys(expanded))
            if not paths:
                print(f"本地扫描未发现可质检数据源（轨迹文件或 lerobot/折叠式数据集目录）: {dirs}",
                      file=sys.stderr)
                sys.exit(1)
            print(f"本地扫描：{len(paths)} 个数据源（轨迹文件/数据集目录）", file=sys.stderr)
        else:
            bad = [d for d in dirs if not _dir_is_aggregate(d)]
            if bad:
                print(f"检测 API 只接受轨迹文件、文件列表或 lerobot/折叠式数据集目录（其他目录由业务层解析）: {bad}", file=sys.stderr)
                sys.exit(1)
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        print(f"路径不存在: {missing}", file=sys.stderr)
        sys.exit(1)

    if len(paths) == 1:
        path = paths[0]
        _prep_temp = None
        try:
            if args.preprocess:
                from ..pipeline.runner import preprocess, should_preprocess
                import time as _t
                if args.rewrite:
                    import tempfile as _tf
                    if not should_preprocess(path):
                        print("源较小/无大图列，预处理不划算，跳过（直接检测）", file=sys.stderr)
                    else:
                        _prep_temp = _tf.mkdtemp(prefix="df_prep_")
                        _t0 = _t.time()
                        try:
                            path = preprocess(path, output_dir=_prep_temp, rewrite=True)
                        except Exception as _e:
                            print(f"预处理失败，回退直接检测: {_e}", file=sys.stderr)
                            path = paths[0]
                            _prep_temp = None
                        print(f"预处理完成: {path}（耗时 {_t.time()-_t0:.1f}s，临时镜像检测后清理）", file=sys.stderr)
                else:
                    _t0 = _t.time()
                    try:
                        made = preprocess(path)   # rewrite=False → 仅源旁 side-car 索引
                    except Exception as _e:
                        made = []
                        print(f"预处理失败，跳过: {_e}", file=sys.stderr)
                    if made:
                        print(f"side-car 索引已生成 {len(made)} 个（耗时 {_t.time()-_t0:.1f}s）：", file=sys.stderr)
                        for m in made[:4]:
                            print(f"    {m}", file=sys.stderr)
                        if len(made) > 4:
                            print(f"    ... 共 {len(made)} 个", file=sys.stderr)
                    else:
                        print("无大图列，无需 side-car 索引（直接检测）", file=sys.stderr)
            result = run_qc(path, output_dir=args.out,
                            robot=args.robot, profile_path=args.profile,
                            mode=args.mode, strict=args.strict,
                            scenario=args.scenario, workers=args.workers,
                            no_points=args.no_points,
                            gzip=args.gzip)
        finally:
            if _prep_temp:
                import shutil as _sh
                _sh.rmtree(_prep_temp, ignore_errors=True)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str))
            return
        return _print_single(result, show_all=args.no_compact)

    # 文件列表（一批轨迹）：批量并行（进度走 stderr，--json 时 stdout 保持纯 JSON）
    print(f"批量质检 {len(paths)} 个文件，{args.workers} 进程并行，mode={args.mode} ...",
          file=sys.stderr)
    import time
    t0 = time.time()
    results = run_batch(paths, workers=args.workers, output_dir=args.out,
                        save=True,
                        robot=args.robot, profile_path=args.profile,
                        unpack=(args.mode == "deep"), scenario=args.scenario,
                        no_points=args.no_points,
                        gzip=args.gzip)
    elapsed = time.time() - t0
    ok = [r for r in results if not r.get("exception")]
    fail = [r for r in results if r.get("exception")]

    def _level(r: dict) -> str:
        inds = r.get("indicators") or []
        return max((z.get("level") for z in inds),
                   key=lambda s: {"pass": 0, "warning": 1, "error": 2}.get(s, 0), default="unknown")

    sev_stat = {"pass": 0, "warning": 0, "error": 0}
    for r in ok:
        sev_stat[_level(r)] = sev_stat.get(_level(r), 0) + 1
    print(f"\n完成: 共 {len(results)} 个，成功 {len(ok)}，失败 {len(fail)}，耗时 {elapsed:.1f}s", file=sys.stderr)
    print(f"汇总: {sev_stat}", file=sys.stderr)
    print("\n逐文件明细:", file=sys.stderr)
    for r in ok:
        s = _level(r)
        fi = r.get("file_info", {}) or {}
        inds = r.get("indicators") or []
        print(f"  [{s:7}] {fi.get('path', '?')}  格式={fi.get('format', '?')} "
              f"指标={len(inds)} 总体={s}", file=sys.stderr)
        shown = inds if args.no_compact else [z for z in inds if z.get("level") in ("warning", "error")]
        for z in shown:
            print(f"        {z.get('level')}: {z.get('topic_name', '')} [{z.get('indicator_code', '')}] {z.get('name')}",
                  file=sys.stderr)
    if fail:
        print("\n失败清单:", file=sys.stderr)
        for r in fail:
            print(f"  {r['file']}: {r.get('exception')}", file=sys.stderr)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, separators=(",", ":"), default=str))
    return


def _print_single(result, show_all: bool = False):
    """单文件检测结果可读展示（qc-2.2；指标显示 [indicator_code] 中文名）。show_all 时列全部指标，默认只列问题指标。"""
    fi = result.get("file_info", {}) or {}
    run = result.get("run", {}) or {}
    inds = result.get("indicators") or []
    _sev_rank = {"pass": 0, "warning": 1, "error": 2}
    sev = max((z.get("level") for z in inds), key=lambda s: _sev_rank.get(s, 0), default="pass")
    counts = {"pass": 0, "warning": 0, "error": 0}
    for z in inds:
        counts[z.get("level")] = counts.get(z.get("level"), 0) + 1
    print(f"\n{'='*64}")
    print(f"质检报告  {fi.get('path')}")
    print(f"格式: {fi.get('format')}   耗时: {run.get('elapsed_sec', 0)}s   模式: {run.get('mode') or '-'}")
    print(f"总体质量: {sev}   [pass={counts.get('pass')} warning={counts.get('warning')} error={counts.get('error')}]")
    skipped = run.get("skipped_indicators") or []
    if skipped:
        print(f"未检指标（当前模式跳过）: {', '.join(skipped)}")
    errs = run.get("errors") or []
    for e_ in errs:
        print(f"阶段异常: {e_.get('stage')}: {e_.get('error')}")
    rc = result.get("root_cause", {}) or {}
    if rc.get("verdict"):
        print(f"根因概要: {rc['verdict']}")
        for e_ in rc.get("events", [])[:6]:
            _kind = e_.get("kind", "")
            _det = e_.get("detail", {}) or {}
            if _kind == "correlated_gap":
                print(f"   系统停顿 @{e_.get('time')}s  时长 {_det.get('duration_ms')}ms  {len(_det.get('families', []))} 传感器族")
            elif _kind.endswith("_anomaly"):
                print(f"   {_det.get('code')} 族异常（{_det.get('severity')}，{_det.get('topic_count')} 通道，"
                      f"{_det.get('count')} 次）")
    print(f"{'='*64}")
    print(f"传感器/通道({len(result.get('sensors', []))}): {', '.join(result.get('sensors', [])) or '-'}")
    bad = [z for z in inds if z.get("level") in ("error", "warning")]
    shown = inds if show_all else bad
    label = f"问题指标({len(bad)})" if not show_all else f"指标全量({len(inds)})"
    print(f"\n{label}:")
    for z in shown:
        indicator_code = z.get("indicator_code") or "--"
        where = "(全局)" if z.get("scope") == "global" else (z.get("topic_name") or "-")
        line = f"   [{z.get('level')}] {where} [{indicator_code}] {z.get('name')}"
        detail = z.get("detail") or {}
        if indicator_code == "M6" and detail.get("anomaly_topics"):
            n_d, n_j, n_u = detail.get("drift_count", 0), detail.get("jump_count", 0), detail.get("unstable_count", 0)
            line += f"  ← {n_d}drift/{n_j}jump/{n_u}unstable"
            dt = ", ".join(f"{t['topic']} {t.get('mode')}({t.get('drift_rate_ms_per_min', 0)}ms/min)"
                           for t in detail["anomaly_topics"][:6])
            if dt:
                line += f" | {dt}" + (" ..." if len(detail["anomaly_topics"]) > 6 else "")
        elif indicator_code == "M2" and isinstance(detail, dict):
            line += f"  ← ratio={detail.get('coverage_ratio')} late={detail.get('late_start')} early={detail.get('early_end')}"
        elif indicator_code == "M1" and isinstance(detail, dict):
            line += f"  ← pairs={detail.get('topic_pairs')}"
        print(line)
    print(f"\n统计: {run.get('event_count', 0)} 事件点")


if __name__ == "__main__":
    main()
