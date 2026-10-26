"""模块 A: 视频完整性 — 可直接运行的评估入口。

用法:
    python -m video_integrity.run_check <video_path> [--cam-type wrist|head] [--weights ...]
    python video_integrity/run_check.py <video_path>

输出: score + grade + verdict + reasons + 归因 (JSON)。
"""
from pathlib import Path
import sys
import os
import json
import argparse
import time

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from edc.algorithms.quality.shared.health_checker import VideoHealthChecker
from edc.algorithms.quality.algorithms.Video_Integrity.scoring import build_video_integrity_module
from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
from edc.algorithms.quality.shared.attribution import format_report


def assess(video_path: str, cam_type: str = "wrist", weights: dict = None,
           sample_step: int = 5, downscale: int = 4) -> dict:
    """对单个视频执行视频完整性评估 → 统一输出契约。"""
    t0 = time.time()

    # 画面质量检测 (黑屏/白屏/花屏/冻结 — DF 方案, 默认 1/5 抽帧 + 1/4 降采样)
    fq = FrameQualityDetector(sample_step=sample_step, downscale=downscale).detect(video_path)

    # 健康检查 (丢帧/帧率稳定 — ffprobe, 快速)
    checker = VideoHealthChecker()
    hc_result = checker.check(video_path)

    module = build_video_integrity_module(hc_result, weights, cam_type=cam_type, fq_result=fq)
    report = format_report(module)
    report["cam_type"] = cam_type
    report["input"] = video_path
    report["health_check"] = {
        "is_healthy": hc_result.get("is_healthy"),
        "issues": hc_result.get("issues", []),
        "frame_drops": hc_result.get("frame_drops", {}),
        "frame_rate_stability": hc_result.get("frame_rate_stability", {}),
    }

    # 画面质量熔断 (DF 方案: 黑屏/白屏/花屏/冻结 任一占比≥50% → 熔断)
    report["frame_quality"] = fq
    if not fq.get("passed", True):
        report["passed"] = False
        report["score"] = 0
        report["grade"] = "D"
        report["verdict"] = "DO_NOT_USE"
        report["reasons"] = (report.get("reasons") or []) + \
            ["画面质量熔断: " + "; ".join(fq.get("reasons", []))]

    report["process_time_ms"] = round((time.time() - t0) * 1000, 1)
    return report


def assess_from_ir(info, reader, cam_type: str = "wrist", weights: dict = None,
                   sample_step: int = 5, downscale: int = 4) -> list:
    """从 IR 评估所有 image 通道的视频完整性。返回 [report, ...]。"""
    try:
        channels = reader.require_image_channels(info)
    except Exception as exc:
        from edc.data.readers.base import make_ir_report
        return [make_ir_report("视频完整性", "video_integrity", exc)]
    reports = []
    seen = set()
    for name, source in channels:
        if 'depth' in name.lower():
            continue
        real = f"{info.path}::{name}"
        if real in seen:
            continue
        seen.add(real)
        ct = "head" if any(k in name.lower() for k in ("high", "head", "face")) else "wrist"
        import tempfile
        tmp_path = tempfile.mktemp(prefix="vi_ir_", suffix=".mp4")
        materialized = None
        try:
            materialized = reader.materialize_image_channel(info, name)
            reports.append(assess(materialized, cam_type=ct, weights=weights,
                                  sample_step=sample_step, downscale=downscale))
            reports[-1]["input_channel"] = name
            reports[-1]["input"] = info.path
        except Exception as exc:
            from edc.data.readers.base import make_ir_report
            report = make_ir_report("视频完整性", "video_integrity", exc)
            report["input_channel"] = name
            reports.append(report)
        finally:
            if materialized == tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
    return reports


def main():
    ap = argparse.ArgumentParser(description="模块A 视频完整性评估")
    ap.add_argument("input", help="视频文件路径 或 数据目录路径 (IR: episode/dataset)")
    ap.add_argument("--cam-type", default="wrist", choices=["wrist", "head"])
    ap.add_argument("--weights", default=None, help="权重 JSON 字符串, 如 '{\"黑屏检测\":0.3}'")
    args = ap.parse_args()

    from edc.data.ir.metadata import detect_format, FORMAT_MP4, FORMAT_EPISODE, FORMAT_DATASET, FORMAT_UNKNOWN
    fmt = detect_format(args.input)
    weights = json.loads(args.weights) if args.weights else None

    if fmt not in (FORMAT_UNKNOWN, FORMAT_JSON):
        from edc.algorithms.quality.config import open_info
        info, reader = open_info(args.input)
        reports = assess_from_ir(info, reader, args.cam_type, weights)
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    elif fmt == FORMAT_MP4 and os.path.isdir(args.input):
        from edc.algorithms.quality.config import open_info
        info, reader = open_info(args.input)
        reports = assess_from_ir(info, reader, args.cam_type, weights)
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        report = assess(args.input, args.cam_type, weights)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
