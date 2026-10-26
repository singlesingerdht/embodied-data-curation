"""模块 A: 视频完整性 — 从 health_checker + frame_quality 结果组装评分模块。

4 个连续指标 (forward/inverse), 任一原子 score=0 → 模块熔断 (total=0, passed=False)。
"""
from edc.algorithms.quality.shared.quality_reporter import normalize_weights, compute_quality, to_grade

_W = 1.0 / 9.0
A_DEFAULTS = {
    "黑屏检测": _W, "帧完整性": _W, "帧率稳定": _W, "画面冻结": _W,
    "遮挡检测": _W, "模糊检测": _W, "过曝检测": _W, "欠曝检测": _W, "偏色检测": _W,
}

A_THRESHOLD_PROFILE = {
    "黑屏检测": {"t_min": 0.30, "t_max": 0.05, "direction": "inverse"},
    "帧完整性": {"t_min": 0.02, "t_max": 0.0,  "direction": "inverse"},
    "帧率稳定": {"t_min": 10.0, "t_max": 0.0,  "direction": "inverse"},
    "画面冻结": {"t_min": 0.30, "t_max": 0.0,  "direction": "inverse"},
    "遮挡检测": {"t_min": 0.30, "t_max": 0.05, "direction": "inverse"},
    "模糊检测": {"t_min": 0.30, "t_max": 0.10, "direction": "inverse"},
    "过曝检测": {"t_min": 0.30, "t_max": 0.05, "direction": "inverse"},
    "欠曝检测": {"t_min": 0.30, "t_max": 0.05, "direction": "inverse"},
    "偏色检测": {"t_min": 0.30, "t_max": 0.05, "direction": "inverse"},
}


def build_video_integrity_module(hc_result: dict, weights: dict = None,
                                 cam_type: str = None, fq_result: dict = None) -> dict:
    """hc_result: VideoHealthChecker.check() 返回的 dict。
    fq_result: FrameQualityDetector.detect() 返回的 dict (黑屏/白屏/花屏/冻结)。
    """
    a_weights, a_warns = normalize_weights(A_DEFAULTS, weights, "视频完整性")

    if not hc_result.get("is_healthy", True):
        issues = hc_result.get("issues", ["unhealthy"])
        issue_sub = {
            "name": "健康检查",
            "default_weight": 1.0,
            "weight": 1.0,
            "threshold_rule": "视频不可读 → 模块熔断",
            "raw_value": f"issues={issues}",
            "quality": 0.0,
            "passed": False,
            "score": 0.0,
            "direction": "binary",
            "t_min": 1.0,
            "t_max": None,
            "current_value": 0.0,
            "gap": 100.0,
            "weighted_gap": 100.0,
            "details": [],
            "grade": to_grade(0.0),
        }
        return {
            "name": "视频完整性",
            "module_key": "video_integrity",
            "max_score": 100,
            "total_score": 0,
            "passed": False,
            "sub_indicators": [issue_sub],
            "warnings": a_warns + [f"视频不健康: {issues}"],
            "grade": to_grade(0.0),
            "threshold_profile": {
                "cam_type": cam_type,
                "threshold_set": "camera_agnostic (head/wrist 相同)",
                "thresholds": A_THRESHOLD_PROFILE,
            },
        }

    drop_ratio = hc_result.get("frame_drops", {}).get("drop_ratio", 0)
    pts_std = hc_result.get("frame_rate_stability", {}).get("interval_std_ms", 0)

    if fq_result:
        black_ratio = fq_result.get("black_ratio", 0)
        freeze_ratio = fq_result.get("freeze_ratio", 0)
        occlusion_ratio = fq_result.get("occlusion_ratio", 0)
        blur_ratio = fq_result.get("blur_ratio", 0)
        overexpo_ratio = fq_result.get("overexpo_ratio", 0)
        underexpo_ratio = fq_result.get("underexpo_ratio", 0)
        color_anomaly_ratio = fq_result.get("color_anomaly_ratio", 0)
        color_persistent = fq_result.get("color_persistent", False)
    else:
        black_ratio = 0
        freeze_ratio = hc_result.get("duplicate_frames", {}).get("dup_ratio", 0)
        occlusion_ratio = blur_ratio = overexpo_ratio = underexpo_ratio = color_anomaly_ratio = 0
        color_persistent = False

    a_atoms = [
        ("黑屏检测", black_ratio, 0.30, 0.05, "inverse", "黑屏率>30%→熔断, ≤5%→满分"),
        ("帧完整性", drop_ratio, 0.02, 0.0, "inverse", "丢帧率>2%→熔断, 0→满分"),
        ("帧率稳定", pts_std, 10.0, 0.0, "inverse", "PTS std>10ms→熔断, 0→满分"),
        ("画面冻结", freeze_ratio, 0.30, 0.0, "inverse", "冻结率>30%→熔断, 0→满分"),
        ("遮挡检测", occlusion_ratio, 0.30, 0.05, "inverse", "遮挡率>30%→熔断, ≤5%→满分"),
        ("模糊检测", blur_ratio, 0.30, 0.10, "inverse", "模糊率>30%→熔断, ≤10%→满分"),
        ("过曝检测", overexpo_ratio, 0.30, 0.05, "inverse", "过曝率>30%→熔断, ≤5%→满分"),
        ("欠曝检测", underexpo_ratio, 0.30, 0.05, "inverse", "欠曝率>30%→熔断, ≤5%→满分"),
        ("偏色检测", color_anomaly_ratio, 0.30, 0.05, "inverse", "偏色率>30%→熔断, ≤5%→满分"),
    ]

    a_any_zero = False
    a_subs = []
    for name, raw_val, t_min, t_max, direction, rule in a_atoms:
        q = compute_quality(raw_val, t_min, t_max, direction)
        s = round(q * 100, 1)
        if name == "偏色检测" and color_persistent and s == 0:
            s = 85.0
            q = 0.85
            rule = "相机级持续偏色 (白平衡), 不熔断仅扣分"
        gap = round(100 - s, 1)
        if s == 0:
            a_any_zero = True
        a_subs.append({
            "name": name,
            "default_weight": A_DEFAULTS[name],
            "weight": a_weights[name],
            "threshold_rule": rule,
            "raw_value": f"{name}={raw_val:.3f} (t_min={t_min}, t_max={t_max})"
                         + (" [持续偏色]" if (name == "偏色检测" and color_persistent) else ""),
            "quality": q,
            "passed": s > 0,
            "score": s,
            "direction": direction,
            "t_min": t_min,
            "t_max": t_max,
            "current_value": round(raw_val, 4),
            "gap": gap,
            "weighted_gap": round(gap * a_weights[name], 1),
            "details": [],
            "grade": to_grade(s),
        })

    if a_any_zero:
        a_total = 0
        a_passed = False
    else:
        a_total = round(sum(s["score"] for s in a_subs) / len(a_subs), 1)
        a_passed = True

    return {
        "name": "视频完整性",
        "module_key": "video_integrity",
        "max_score": 100,
        "total_score": a_total,
        "passed": a_passed,
        "sub_indicators": a_subs,
        "warnings": a_warns,
        "grade": to_grade(a_total),
        "threshold_profile": {
            "cam_type": cam_type,
            "threshold_set": "camera_agnostic (head/wrist 相同)",
            "thresholds": A_THRESHOLD_PROFILE,
        },
    }