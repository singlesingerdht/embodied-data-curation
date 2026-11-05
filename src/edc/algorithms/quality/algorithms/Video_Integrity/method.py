"""模块 A: 视频完整性，框架通过 assess 调用（inputs.py）。

输出: score + grade + verdict + reasons + 归因 (JSON)。
"""
import time
from edc.algorithms.quality.algorithms.Video_Integrity.health_checker import VideoHealthChecker
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
