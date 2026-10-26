"""模块 C: 动作平滑度 — 从 jerk + SLAM 冻结结果组装评分模块。

2 个指标: 轨迹抖动 (inverse 连续化) + SLAM 冻结 (二值)。
任一原子 score=0 → 模块熔断 (total=0, passed=False)。
从原 routers/action.py 的 _build_action_module 抽取。
"""
from edc.algorithms.quality.shared.quality_reporter import normalize_weights, compute_quality, to_grade

C_DEFAULTS = {"轨迹抖动": 0.60, "SLAM 冻结": 0.40}

# T_max: 北京 90 条轨迹×2 臂, jerk P80 ≈ 5; T_min=15 为熔断线
JERK_TMAX = 5.0
JERK_TMIN = 15.0


def build_action_smoothness_module(jerk_result: dict, slam_result: dict,
                                    weights: dict = None) -> dict:
    c_weights, c_warns = normalize_weights(C_DEFAULTS, weights, "动作平滑度")

    c_any_zero = False
    subs = []

    # ── 轨迹抖动: inverse 连续化 ──
    jerk_score = float(jerk_result.get("score", 0))
    jerk_th = float(jerk_result.get("details", {}).get("threshold", 15.0))
    q1 = compute_quality(jerk_score, JERK_TMIN, JERK_TMAX, "inverse")
    s1 = round(q1 * 100, 1)
    gap1 = round(100 - s1, 1)
    if s1 == 0:
        c_any_zero = True
    subs.append({
        "name": "轨迹抖动",
        "default_weight": 0.60,
        "weight": c_weights["轨迹抖动"],
        "threshold_rule": f"inverse: T_min={JERK_TMIN}, T_max={JERK_TMAX}(P80基线), 任一原子=0→C=0",
        "raw_value": f"Jerk={jerk_score:.2f}, P80基线={JERK_TMAX}, 阈值={jerk_th:.1f}",
        "quality": q1,
        "passed": s1 > 0,
        "score": s1,
        "direction": "inverse",
        "t_min": JERK_TMIN,
        "t_max": JERK_TMAX,
        "current_value": jerk_score,
        "gap": gap1,
        "weighted_gap": round(gap1 * c_weights["轨迹抖动"], 1),
        "details": [],
        "grade": to_grade(s1),
    })

    # ── SLAM 冻结: 二值 ──
    c2_failed = slam_result.get("has_freeze", False)
    segs = slam_result.get("segments", [])
    seg_detail = "; ".join(
        f"{s['duration_sec']}s@{s['start']}(vel={s.get('pre_velocity_m_s', 0):.3f})"
        for s in segs[:3]
    ) if segs else "无"
    q2 = 0.0 if c2_failed else 1.0
    s2 = round(q2 * 100, 1)
    gap2 = round(100 - s2, 1)
    if s2 == 0:
        c_any_zero = True
    subs.append({
        "name": "SLAM 冻结",
        "default_weight": 0.40,
        "weight": c_weights["SLAM 冻结"],
        "threshold_rule": "位姿连续≥1s相同 且 冻结前有运动(>5mm/s)",
        "raw_value": f"freezes={slam_result.get('freeze_count', 0)} | {seg_detail}",
        "quality": q2,
        "passed": not c2_failed,
        "score": s2,
        "direction": "binary",
        "t_min": 1.0,
        "t_max": None,
        "current_value": float(slam_result.get("freeze_count", 0)),
        "gap": gap2,
        "weighted_gap": round(gap2 * c_weights["SLAM 冻结"], 1),
        "details": [],
        "grade": to_grade(s2),
    })

    details = jerk_result.get('details', {})
    jerk_available = (jerk_result.get('assessment_complete', True)
                      and details.get('stationary_frames', 1) > 0
                      and details.get('message') not in {'no poses provided', 'insufficient data points'})
    slam_available = slam_result.get('assessment_complete', True)
    for sub, available in zip(subs, (jerk_available, slam_available)):
        if not available:
            sub.update(score=None, passed=None, quality=None, weight=0.0,
                       gap=0.0, weighted_gap=0.0, current_value=None, grade='N/A',
                       raw_value='未识别到可评估静止段，静止抖动未评估' if sub['name'] == '轨迹抖动'
                                 else '位姿样本不足，冻结检查未评估')
    c_warns.extend(jerk_result.get('warnings', []))
    if not jerk_available and not jerk_result.get('warnings'):
        c_warns.append('静止抖动缺少可评估样本，不能把原始零值解释为零抖动')
    c_any_zero = any(sub['score'] == 0 for sub in subs)

    if c_any_zero:
        total = 0
        passed = False
    else:
        available_scores = [sub['score'] for sub in subs if sub['score'] is not None]
        total = round(sum(available_scores) / len(available_scores), 1) if available_scores else 0.0
        passed = True

    return {
        "name": "动作平滑度",
        "module_key": "action_smoothness",
        "max_score": 100,
        "total_score": total,
        "passed": passed,
        "sub_indicators": subs,
        "warnings": c_warns,
        "grade": to_grade(total),
        "assessment_complete": bool(jerk_available and slam_available),
    }
