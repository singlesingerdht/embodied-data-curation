"""时间戳对齐：头部相机当参考，其他每一路和它逐帧配对打分；头部自己只看节奏。框架通过 assess 调用。"""
from __future__ import annotations

import argparse
import math
from bisect import bisect_left
from dataclasses import dataclass
from statistics import median, pstdev
from typing import Any

# ── 接入统一输出契约 (shared.format_report) ──
from edc.algorithms.quality.shared.attribution import format_report

DEFAULT_IOU_THRESHOLD = 0.95
DEFAULT_MATCH_WINDOW_FACTOR = 0.5
DEFAULT_MIN_MATCH_WINDOW_MS = 8.0
DEFAULT_ABSOLUTE_ERROR_FLOOR_MS = 2.0
DEFAULT_LONG_GAP_MULTIPLE = 2.0
DEFAULT_LONG_GAP_RATIO_WARNING = 0.0025
DEFAULT_LONG_GAP_RATIO_ZERO = 0.02
DEFAULT_DROP_GAP_MIN_MS = 20.0
DEFAULT_DT_CV_NO_PENALTY = 0.10
DEFAULT_DT_CV_ZERO = 0.30
DEFAULT_P95_MULTIPLIER = 1.5
DEFAULT_DRIFT_RATE_NO_PENALTY_MS_PER_MIN = 1.0
DEFAULT_DRIFT_RATE_ZERO_MS_PER_MIN = 5.0
DEFAULT_DRIFT_R2_THRESHOLD = 0.50
DEFAULT_DRIFT_MIN_POINTS = 20
DEFAULT_DRIFT_MIN_DURATION_S = 2.0


@dataclass
class RawStream:
    topic: str
    kind: str
    timestamps: list[float]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def percentile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * quantile
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] * (upper - index) + sorted_values[upper] * (index - lower)


def period_ms(timestamps: list[float]) -> float | None:
    if len(timestamps) < 2:
        return None
    deltas = [current - previous for previous, current in zip(timestamps, timestamps[1:])]
    if any(delta <= 0 for delta in deltas):
        return None
    return median(deltas) * 1000.0


def window_iou(reference: list[float], target: list[float]) -> float:
    if not reference or not target:
        return 0.0
    overlap = max(0.0, min(reference[-1], target[-1]) - max(reference[0], target[0]))
    union = max(reference[-1], target[-1]) - min(reference[0], target[0])
    return 0.0 if union <= 0 else overlap / union


def nearest_pairs_ms(source: list[float], target: list[float], window_ms: float) -> tuple[list[dict[str, Any]], int]:
    pairs: list[dict[str, Any]] = []
    missing = 0
    if not target:
        return pairs, len(source)
    for source_index, timestamp in enumerate(source):
        index = bisect_left(target, timestamp)
        candidates = []
        if index < len(target):
            candidates.append((abs(target[index] - timestamp), index))
        if index > 0:
            candidates.append((abs(target[index - 1] - timestamp), index - 1))
        if not candidates:
            missing += 1
            continue
        _, target_index = min(candidates)
        signed_error_ms = (target[target_index] - timestamp) * 1000.0
        error_ms = abs(signed_error_ms)
        within_window = error_ms <= window_ms
        if not within_window:
            missing += 1
        pairs.append({"source_index": source_index, "target_index": target_index,
                      "source_timestamp": timestamp, "target_timestamp": target[target_index],
                      "signed_error_ms": signed_error_ms, "error_ms": error_ms,
                      "within_window": within_window})
    return pairs, missing


def camera_priority(stream: RawStream) -> tuple[int, str]:
    topic = stream.topic.lower()
    if "head" in topic and ("rgb" in topic or "color" in topic):
        return 0, topic
    if ("front" in topic or "cam_h" in topic) and ("rgb" in topic or "color" in topic):
        return 1, topic
    if "rgb" in topic or "color" in topic:
        return 2, topic
    return 3, topic


def choose_reference(streams: list[RawStream]) -> RawStream:
    cameras = [stream for stream in streams if stream.kind == "camera"]
    if not cameras:
        raise ValueError("no valid camera topic available for automatic reference selection")
    return min(cameras, key=camera_priority)


def build_match_window_ms(reference_period_ms: float | None, target_period_ms: float | None, args: argparse.Namespace) -> float:
    periods = [value for value in (reference_period_ms, target_period_ms) if value is not None]
    return max(args.min_match_window_ms, args.match_window_factor * max(periods)) if periods else args.min_match_window_ms


def grade_from_score(score: float) -> tuple[str, bool, str]:
    if score > 95.0:
        return "S", True, "ACCEPTABLE"
    if score > 90.0:
        return "A", True, "ACCEPTABLE"
    if score > 80.0:
        return "B", True, "NEEDS_REVIEW"
    if score > 60.0:
        return "C", False, "NEEDS_REVIEW"
    return "D", False, "DO_NOT_USE"


def linear_component(value: float, no_penalty: float, zero: float) -> float:
    if zero <= no_penalty:
        return 0.0 if value > no_penalty else 1.0
    return 1.0 - clamp((value - no_penalty) / (zero - no_penalty))


def stream_dt_metrics(timestamps: list[float], args: argparse.Namespace) -> dict[str, Any]:
    if len(timestamps) < 2:
        return {"dt_count": 0, "dt_mean_ms": None, "dt_median_ms": None, "dt_cv": None,
                "actual_rate_hz": None, "long_gap_count": 0, "long_gap_ratio": 1.0,
                "max_gap_ms": None, "max_gap_multiple": None, "backward_count": 0,
                "duplicate_count": 0, "dt_cv_component": 0.0,
                "long_gap_component": 0.0, "cadence_component": 0.0,
                "long_gap_events": []}
    dts = [(b - a) * 1000.0 for a, b in zip(timestamps, timestamps[1:])]
    positive = [dt for dt in dts if dt > 0]
    if not positive:
        return {"dt_count": len(dts), "dt_mean_ms": None, "dt_median_ms": None, "dt_cv": None,
                "actual_rate_hz": None, "long_gap_count": len(dts), "long_gap_ratio": 1.0,
                "max_gap_ms": None, "max_gap_multiple": None, "backward_count": sum(dt < 0 for dt in dts),
                "duplicate_count": sum(dt == 0 for dt in dts), "dt_cv_component": 0.0,
                "long_gap_component": 0.0, "cadence_component": 0.0,
                "long_gap_events": []}
    med = median(positive)
    mean = sum(positive) / len(positive)
    cv = pstdev(positive) / mean if mean else 1.0
    gap_threshold = max(args.long_gap_multiple * med, DEFAULT_DROP_GAP_MIN_MS)
    long_gaps = [dt for dt in positive if dt > gap_threshold]
    long_gap_events = []
    topic_headtime = timestamps[0]
    for index, dt in enumerate(dts):
        if dt <= 0 or dt <= gap_threshold:
            continue
        gap_multiple = dt / med
        estimated = max(0, math.floor(gap_multiple) - 1)
        if estimated == 1:
            prev_ok = index == 0 or (index > 0 and dts[index - 1] <= gap_threshold)
            next_ok = index == len(dts) - 1 or (index < len(dts) - 1 and dts[index + 1] <= gap_threshold)
            if prev_ok and next_ok:
                continue
        long_gap_events.append({
            "relative_start_s": timestamps[index] - topic_headtime,
            "relative_end_s": timestamps[index + 1] - topic_headtime,
            "gap_ms": dt,
            "gap_multiple": gap_multiple,
            "estimated_missing_frames": estimated,
        })
    long_ratio = len(long_gap_events) / len(dts) if dts else 0.0
    max_gap = max(positive)
    max_multiple = max_gap / med if med else None
    cv_component = linear_component(cv, args.dt_cv_no_penalty, args.dt_cv_zero)
    gap_component = linear_component(long_ratio, args.long_gap_ratio_warning, args.long_gap_ratio_zero)
    backward = sum(dt < 0 for dt in dts)
    duplicate = sum(dt == 0 for dt in dts)
    cadence = 0.65 * cv_component + 0.35 * gap_component
    if backward or duplicate:
        cadence = 0.0
    p95_warning = False
    p95_dt = None
    if cv <= 0.3 and len(positive) >= 3:
        p95_dt = percentile(sorted(positive), 0.95)
        if p95_dt > DEFAULT_P95_MULTIPLIER * med:
            p95_warning = True
    return {"dt_count": len(dts), "dt_mean_ms": mean, "dt_median_ms": med,
            "dt_std_ms": pstdev(positive), "dt_cv": cv, "dt_cv_pct": cv * 100.0,
            "actual_rate_hz": 1000.0 / mean if mean else None, "long_gap_count": len(long_gaps),
            "long_gap_ratio": long_ratio, "max_gap_ms": max_gap, "max_gap_multiple": max_multiple,
            "backward_count": backward, "duplicate_count": duplicate,
            "dt_cv_component": cv_component, "long_gap_component": gap_component,
            "cadence_component": cadence, "long_gap_events": long_gap_events,
             "p95_dt_ms": round(p95_dt, 2) if p95_dt is not None else None,
             "p95_warning": p95_warning}


def threshold_profile(kind: str, ref_period_ms: float, target_period_ms: float,
                      args: argparse.Namespace) -> dict[str, float]:
    """Return the single threshold profile shared by scoring and attribution."""
    if kind == "action":
        # Action streams have their own high-rate cadence; using a slow camera
        # reference period makes their cross-stream error thresholds too loose.
        scale = max(0.5 * target_period_ms, args.absolute_error_floor_ms)
    else:
        scale = max(0.5 * ref_period_ms, 0.5 * target_period_ms, args.absolute_error_floor_ms)
    if kind == "camera":
        offset_no_penalty = max(10.0, 0.35 * target_period_ms)
        offset_zero = max(40.0, 1.20 * target_period_ms)
    else:
        offset_no_penalty = max(0.2, 0.05 * target_period_ms)
        offset_zero = max(2.0, 0.50 * target_period_ms)
    return {
        "scale_ms": scale,
        "coverage_no_penalty": 0.98,
        "coverage_zero": 0.90,
        "p50_no_penalty_ms": 0.75 * scale,
        "p50_zero_ms": 2.0 * scale,
        "p99_no_penalty_ms": 0.75 * scale,
        "p99_zero_ms": 1.75 * scale,
        "offset_no_penalty_ms": offset_no_penalty,
        "offset_zero_ms": offset_zero,
        "jitter_no_penalty_ms": max(2.0, 0.10 * target_period_ms),
        "jitter_zero_ms": max(8.0, 0.50 * target_period_ms),
        "drift_rate_no_penalty_ms_per_min": args.drift_rate_no_penalty_ms_per_min,
        "drift_rate_zero_ms_per_min": args.drift_rate_zero_ms_per_min,
        "drift_r2_threshold": args.drift_r2_threshold,
    }


def fit_drift(times: list[float], signed: list[float], ref_period_ms: float,
              args: argparse.Namespace) -> dict[str, Any]:
    """Fit signed error versus reference time and expose trend reliability."""
    result = {
        "drift_span_ms": 0.0,
        "drift_rate_ms_per_min": 0.0,
        "drift_r2": 0.0,
        "drift_slope_ms_per_s": 0.0,
        "drift_intercept_ms": None,
        "observation_duration_s": 0.0,
        "drift_fit_valid": False,
        "drift_reliable": False,
        "predicted_error_ms": list(signed),
    }
    if len(signed) < 3 or len(times) < 3 or times[-1] <= times[0]:
        return result
    mean_t = sum(times) / len(times)
    mean_e = sum(signed) / len(signed)
    den = sum((t - mean_t) ** 2 for t in times)
    if den <= 0:
        return result
    slope = sum((t - mean_t) * (e - mean_e) for t, e in zip(times, signed)) / den
    intercept = mean_e - slope * mean_t
    # Centering timestamps avoids precision loss when Unix epoch values are large.
    predicted = [mean_e + slope * (t - mean_t) for t in times]
    duration = times[-1] - times[0]
    drift_span = predicted[-1] - predicted[0]
    ss_tot = sum((e - mean_e) ** 2 for e in signed)
    ss_res = sum((e - y) ** 2 for e, y in zip(signed, predicted))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 0.0
    result.update({
        "drift_span_ms": drift_span,
        "drift_rate_ms_per_min": slope * 60.0,
        "drift_r2": r2,
        "drift_slope_ms_per_s": slope,
        "drift_intercept_ms": intercept,
        "observation_duration_s": duration,
        "drift_fit_valid": True,
        "drift_reliable": (len(signed) >= args.drift_min_points and
                            duration >= max(args.drift_min_duration_s, 10.0 * ref_period_ms / 1000.0) and
                            r2 >= args.drift_r2_threshold),
        "predicted_error_ms": predicted,
    })
    return result


def attribution_for_topic(kind: str, metrics: dict[str, Any], args: argparse.Namespace) -> list[str]:
    labels: list[str] = []
    thresholds = metrics["thresholds"]
    coverage = metrics["source_match_coverage"]
    offset = abs(metrics["signed_offset_ms"])
    drift_reliable = metrics["drift_reliable"]
    drift_bad = (drift_reliable and
                 abs(metrics["drift_rate_ms_per_min"]) > thresholds["drift_rate_no_penalty_ms_per_min"])
    jitter_bad = metrics["residual_jitter_ms"] > thresholds["jitter_no_penalty_ms"]
    offset_bad = offset > thresholds["offset_no_penalty_ms"]
    if coverage < thresholds["coverage_no_penalty"]:
        labels.append("\u53c2\u8003\u5e27\u5339\u914d\u8986\u76d6\u7387\u4e0d\u8db3")
    if metrics["p50_component"] < 1.0:
        labels.append("P50 \u5bf9\u9f50\u8bef\u5dee\u504f\u5927")
    if metrics["p99_component"] < 1.0:
        labels.append("P99 \u5c3e\u90e8\u8bef\u5dee\u504f\u5927")
    if offset_bad and not jitter_bad and not drift_bad:
        labels.append("\u56fa\u5b9a\u5ef6\u8fdf")
    elif offset_bad:
        labels.append("\u57fa\u51c6\u65f6\u95f4\u504f\u79fb")
    if drift_bad:
        labels.append("\u65f6\u949f\u6f02\u79fb")
    if jitter_bad:
        labels.append("\u8de8\u6d41\u6296\u52a8")
    if kind == "action" and metrics["dt_cv"] is not None and metrics["dt_cv"] > args.dt_cv_no_penalty:
        labels.append("\u52a8\u4f5c\u6d41\u91c7\u6837\u4e0d\u7a33\u5b9a")
    if kind == "camera" and metrics["dt_cv"] is not None and metrics["dt_cv"] > args.dt_cv_no_penalty:
        labels.append("\u76f8\u673a\u6d41\u91c7\u6837\u4e0d\u7a33\u5b9a")
    if kind == "action" and metrics["long_gap_ratio"] > args.long_gap_ratio_warning:
        labels.append("\u52a8\u4f5c\u6d41\u5c40\u90e8\u4e2d\u65ad")
    if kind == "camera" and metrics["long_gap_ratio"] > args.long_gap_ratio_warning:
        labels.append("\u76f8\u673a\u6d41\u6389\u5e27")
    if not labels:
        labels.append("\u6b63\u5e38")
    return labels

def score_topic(reference: RawStream, target: RawStream, args: argparse.Namespace) -> dict[str, Any]:
    if not target.timestamps:
        return {"topic": target.topic, "kind": target.kind, "score": 0.0,
                "status": "invalid_timestamps", "attribution": ['时间戳无效']}
    iou = window_iou(reference.timestamps, target.timestamps)
    ref_period = period_ms(reference.timestamps)
    target_period = period_ms(target.timestamps)
    # 完全不重合或算不出采样周期才记 0 分；只是重合不够在下面按比例扣分
    if iou <= 0.0 or ref_period is None or target_period is None:
        return {"topic": target.topic, "kind": target.kind, "score": 0.0,
                "status": "window_iou_failed" if iou <= 0.0 else "period_unavailable",
                "window_iou": round(iou, 6), "period_ms": target_period,
                "attribution": ['采集时间范围或采样周期不可评价']}
    window_ms = build_match_window_ms(ref_period, target_period, args)
    pairs, missing = nearest_pairs_ms(reference.timestamps, target.timestamps, window_ms)
    matched = [pair for pair in pairs if pair["within_window"]]
    source_count = len(reference.timestamps)
    coverage = len(matched) / source_count if source_count else 0.0
    errors = sorted(pair["error_ms"] for pair in matched)
    signed = [pair["signed_error_ms"] for pair in matched]
    if not errors:
        return {"topic": target.topic, "kind": target.kind, "score": 0.0,
                "status": "no_matches", "attribution": ['参考帧没有可用目标匹配']}
    p50, p95, p99 = (percentile(errors, q) for q in (0.50, 0.95, 0.99))
    times = [pair["source_timestamp"] for pair in matched]
    signed_offset = median(signed)
    drift = fit_drift(times, signed, ref_period, args)
    predicted = drift["predicted_error_ms"]
    if drift["drift_fit_valid"]:
        residual = [abs(value - fitted) for value, fitted in zip(signed, predicted)]
    else:
        residual = [abs(value - signed_offset) for value in signed]
    residual_jitter = percentile(sorted(residual), 0.95)
    thresholds = threshold_profile(target.kind, ref_period, target_period, args)
    coverage_component = clamp(
        (coverage - thresholds["coverage_zero"]) /
        (thresholds["coverage_no_penalty"] - thresholds["coverage_zero"])
    )
    p50_component = linear_component(p50, thresholds["p50_no_penalty_ms"], thresholds["p50_zero_ms"])
    p99_component = linear_component(p99, thresholds["p99_no_penalty_ms"], thresholds["p99_zero_ms"])
    offset_component = linear_component(abs(signed_offset), thresholds["offset_no_penalty_ms"], thresholds["offset_zero_ms"])
    jitter_component = linear_component(residual_jitter, thresholds["jitter_no_penalty_ms"], thresholds["jitter_zero_ms"])
    if drift["drift_reliable"]:
        drift_component = linear_component(abs(drift["drift_rate_ms_per_min"]),
                                           thresholds["drift_rate_no_penalty_ms_per_min"],
                                           thresholds["drift_rate_zero_ms_per_min"])
    else:
        drift_component = 1.0
    alignment = 100.0 * (0.20 * coverage_component + 0.20 * p50_component + 0.25 * p99_component +
                         0.10 * offset_component + 0.15 * jitter_component + 0.10 * drift_component)
    dt = stream_dt_metrics(target.timestamps, args)
    cadence = 100.0 * dt["cadence_component"]
    alignment_weight, cadence_weight = (0.90, 0.10) if target.kind == "camera" else (0.70, 0.30)
    topic_score = alignment_weight * alignment + cadence_weight * cadence
    # 和参考的录制时间段（按采集时间）重合不到 iou_threshold：每少 1 个百分点扣 2 分。2026-11-04 用户决定，
    # 原来直接记 0 分太重：处理延迟大的相机在短数据里重合率天然偏低（乐聚头部深度晚 0.4 秒，7 秒的 bag 只重合 89%）
    overlap_penalty = max(0.0, args.iou_threshold - iou) * 200.0
    topic_score = max(0.0, topic_score - overlap_penalty)
    component_losses = {
        "window_iou": overlap_penalty,
        "coverage": alignment_weight * 20.0 * (1.0 - coverage_component),
        "p50_error": alignment_weight * 20.0 * (1.0 - p50_component),
        "p99_error": alignment_weight * 25.0 * (1.0 - p99_component),
        "signed_offset": alignment_weight * 10.0 * (1.0 - offset_component),
        "residual_jitter": alignment_weight * 15.0 * (1.0 - jitter_component),
        "drift_rate": alignment_weight * 10.0 * (1.0 - drift_component),
        "dt_cv": cadence_weight * 65.0 * (1.0 - dt["dt_cv_component"]),
        "long_gap_ratio": cadence_weight * 35.0 * (1.0 - dt["long_gap_component"]),
    }
    metrics = {"source_match_coverage": coverage, "source_missing_ratio": 1.0 - coverage,
               "p50_error_ms": p50, "p95_error_ms": p95, "p99_error_ms": p99,
               "p50_component": p50_component, "p99_component": p99_component,
               "coverage_component": coverage_component, "offset_component": offset_component,
               "jitter_component": jitter_component, "drift_component": drift_component,
               "signed_offset_ms": signed_offset, "residual_jitter_ms": residual_jitter,
               **{key: value for key, value in drift.items() if key != "predicted_error_ms"},
               "target_period_ms": target_period, "match_window_ms": window_ms,
               "missing_match_count": missing, "alignment_score": alignment, "cadence_score": cadence,
               "window_iou_penalty": overlap_penalty,
               "dt_cv": dt.get("dt_cv", 1.0), "long_gap_ratio": dt.get("long_gap_ratio", 1.0),
               "long_gap_count": dt.get("long_gap_count", 0), "max_gap_ms": dt.get("max_gap_ms"),
               "max_gap_multiple": dt.get("max_gap_multiple"), "dt_metrics": dt,
               "thresholds": thresholds}
    attribution = attribution_for_topic(target.kind, {**metrics, **dt}, args)
    losses = {
        "alignment": 100.0 - alignment,
        "cadence": 100.0 - cadence,
        "components": {key: round(value, 3) for key, value in component_losses.items()},
    }
    # Rebuild detail reasons from the same components used by scoring.
    reasons = []
    if overlap_penalty > 0:
        reasons.append(f"录制时间段重合 {iou:.1%}，低于 {args.iou_threshold:.0%}，扣 {overlap_penalty:.1f} 分")
    if coverage_component < 1.0:
        reasons.append(f"\u53c2\u8003\u5e27\u5339\u914d\u8986\u76d6\u7387\u4e3a {coverage:.1%}")
    if p50_component < 1.0:
        reasons.append(f"P50 \u6700\u8fd1\u90bb\u8bef\u5dee\u4e3a {p50:.2f} ms")
    if p99_component < 1.0:
        reasons.append(f"P99 \u6700\u8fd1\u90bb\u8bef\u5dee\u4e3a {p99:.2f} ms")
    if offset_component < 1.0:
        reasons.append(f"signed offset \u4e3a {signed_offset:+.2f} ms")
    if jitter_component < 1.0:
        reasons.append(f"\u53bb\u8d8b\u52bf\u6296\u52a8 P95 \u4e3a {residual_jitter:.2f} ms")
    if drift_component < 1.0:
        reasons.append(
            f"\u6f02\u79fb\u901f\u7387\u4e3a {drift['drift_rate_ms_per_min']:+.3f} ms/min，"
            f"\u603b\u6f02\u79fb\u65f6\u95f4\u4e3a {drift['drift_span_ms']:+.3f} ms"
        )
    if dt.get("dt_cv", 0) > args.dt_cv_no_penalty:
        reasons.append(f"target dt CV \u4e3a {dt['dt_cv']:.1%}")
    if dt.get("long_gap_ratio", 0) > args.long_gap_ratio_warning:
        reasons.append(f"\u957f\u95f4\u9694\u6bd4\u4f8b\u4e3a {dt['long_gap_ratio']:.1%}")
        if target.kind == "camera":
            gap_label = "\u76f8\u673a\u6d41\u6389\u5e27"
        elif target.kind == "action":
            gap_label = "\u52a8\u4f5c\u6d41\u5c40\u90e8\u4e2d\u65ad"
        else:
            gap_label = "topic \u957f\u65f6\u95f4\u9694"
        gap_events = dt.get("long_gap_events", [])

        def describe_gap(event: dict[str, Any]) -> str:
            return (
                f"{event['relative_start_s']:.3f} s \u81f3 {event['relative_end_s']:.3f} s"
                f"\uff08\u95f4\u9694 {event['gap_ms']:.2f} ms\uff0c"
                f"{event['gap_multiple']:.2f} \u500d\u4e2d\u4f4d\u5468\u671f\uff0c"
                f"\u4f30\u8ba1\u7f3a\u5931 {event['estimated_missing_frames']} \u5e27\uff09"
            )

        if len(gap_events) <= 8:
            for event in gap_events:
                reasons.append(
                    f"topic {target.topic}\uff1ascore={topic_score:.3f}\uff0c"
                    f"\u5728 topic \u65f6\u95f4 {describe_gap(event)} \u51fa\u73b0{gap_label}"
                )
        else:
            preview_events = gap_events[:4] + gap_events[-4:]
            preview = "\uff1b".join(describe_gap(event) for event in preview_events)
            reasons.append(
                f"topic {target.topic}\uff1ascore={topic_score:.3f}\uff0c"
                f"\u5728 topic \u65f6\u95f4 {gap_events[0]['relative_start_s']:.3f} s \u81f3 "
                f"{gap_events[-1]['relative_end_s']:.3f} s \u5171\u68c0\u6d4b\u5230 "
                f"{len(gap_events)} \u4e2a{gap_label}\uff0c\u4ee3\u8868\u533a\u95f4\uff1a{preview}"
            )
    return {"topic": target.topic, "kind": target.kind, "score": round(topic_score, 3),
            "status": "scored_with_warnings" if reasons else "scored", "window_iou": round(iou, 6),
            "direction": "reference_to_target", "alignment_score": round(alignment, 3),
            "cadence_score": round(cadence, 3), "losses": losses, "metrics": metrics,
            "attribution": attribution, "reasons": reasons or ["\u5404\u9879\u6307\u6807\u5728\u5f53\u524d\u9608\u503c\u5185"]}


def _add_topic_grade(report: dict[str, Any]) -> None:
    value = report.get("score")
    if isinstance(value, (int, float)):
        grade, passed, verdict = grade_from_score(float(value))
        report["grade"] = grade
        report["passed"] = passed
        report["verdict"] = verdict
    else:
        report["grade"] = "D"
        report["passed"] = False
        report["verdict"] = "DO_NOT_USE"


def score_reference(reference: RawStream, args: argparse.Namespace) -> tuple[dict | None, str | None]:
    """参考相机（头部）不和自己比对齐，只按和其他路一样的节奏规则打一行分，计入平均（2026-11-04 用户决定）。
    它时间倒退、重复或算不出采样周期时不能当基准，返回 (None, 原因)，整项排除。"""
    dt = stream_dt_metrics(reference.timestamps, args)
    broken = "、".join(f"{text} {count} 次" for count, text in ((dt["backward_count"], "时间倒退"),
                                                                (dt["duplicate_count"], "时间重复")) if count)
    if broken or dt["dt_cv"] is None:
        return None, f"{broken or '没法算出采样周期'}，不能当对齐基准，整项记 0 分"
    labels = [label for bad, label in ((dt["dt_cv"] > args.dt_cv_no_penalty, "相机流采样不稳定"),
                                       (dt["long_gap_ratio"] > args.long_gap_ratio_warning, "相机流掉帧")) if bad]
    cadence = 100.0 * dt["cadence_component"]
    report = {"topic": reference.topic, "kind": reference.kind, "score": round(cadence, 3),
              "status": "reference_cadence", "cadence_score": round(cadence, 3),
              "metrics": {"cadence_score": cadence, "dt_metrics": dt},
              "attribution": ["参考相机自身节奏不稳", *labels] if labels else ["正常"]}
    _add_topic_grade(report)
    return report, None


def _reference_failed(reference: RawStream, reason: str) -> dict:
    return _emit_module(
        score=0.0, passed=False,
        sub_indicators=[{"name": reference.topic, "weight": 1.0, "passed": False, "score": 0.0, "gap": 100.0,
                         "weighted_gap": 100.0, "details": [], "grade": "D", "raw_value": reason}],
        reasons=[f"reference topic 为 {reference.topic}。", f"topic {reference.topic}：score=0.000，{reason}"],
        threshold_profile={"reference_topic": reference.topic, "failure": "reference_hard_fail"})


def _topic_to_sub_indicator(report: dict, weight: float) -> dict:
    """把 score_topic 的 per-topic report 转成标准 sub_indicator (含 7 个子项 details)。

    失败 topic (IoU 失败/无匹配/非法) details=[], passed=False; 正常 topic details 含 7 子项。
    """
    score = float(report.get("score", 0.0) or 0.0)
    status = report.get("status", "scored")
    failed = status in {"invalid_timestamps", "window_iou_failed", "period_unavailable", "no_matches"}
    gap = round(100.0 - score, 1)
    metrics = report.get("metrics") or {}
    th = metrics.get("thresholds") or {}
    dt_m = metrics.get("dt_metrics") or {}
    coverage = metrics.get("source_match_coverage", 0.0)
    raw = (f"score={score:.1f} | status={status} | IoU={report.get('window_iou', '-')}"
           f" | IoU_penalty={metrics.get('window_iou_penalty', 0):.1f}"
           f" | coverage={coverage:.1%} | P99={metrics.get('p99_error_ms', 0):.2f}ms"
           f" | dt_cv={dt_m.get('dt_cv', '-')}")

    def _atom(name: str, comp: float, val: float, t_min: float, t_max: float) -> dict:
        s = round(comp * 100.0, 1)
        return {"name": name, "score": s, "gap": round(100.0 - s, 1),
                "val": val, "t_min": t_min, "t_max": t_max}

    if failed:
        details = []
    else:
        details = [
            _atom("匹配覆盖率", metrics.get("coverage_component", 1.0), round(coverage * 100, 1),
                  th.get("coverage_no_penalty", 0), th.get("coverage_zero", 0)),
            _atom("P50近邻误差", metrics.get("p50_component", 1.0), round(metrics.get("p50_error_ms", 0), 2),
                  th.get("p50_no_penalty_ms", 0), th.get("p50_zero_ms", 0)),
            _atom("P99近邻误差", metrics.get("p99_component", 1.0), round(metrics.get("p99_error_ms", 0), 2),
                  th.get("p99_no_penalty_ms", 0), th.get("p99_zero_ms", 0)),
            _atom("系统偏移", metrics.get("offset_component", 1.0), round(metrics.get("signed_offset_ms", 0), 2),
                  th.get("offset_no_penalty_ms", 0), th.get("offset_zero_ms", 0)),
            _atom("残余抖动", metrics.get("jitter_component", 1.0), round(metrics.get("residual_jitter_ms", 0), 2),
                  th.get("jitter_no_penalty_ms", 0), th.get("jitter_zero_ms", 0)),
            _atom("漂移速率", metrics.get("drift_component", 1.0), round(metrics.get("drift_rate_ms_per_min", 0), 3),
                  th.get("drift_rate_no_penalty_ms_per_min", 0), th.get("drift_rate_zero_ms_per_min", 0)),
            _atom("采样稳定性", (metrics.get("cadence_score", 0.0) / 100.0), round(dt_m.get("dt_cv", 0) * 100, 1),
                  DEFAULT_DT_CV_NO_PENALTY, DEFAULT_DT_CV_ZERO),
        ]
    if status == "reference_cadence":  # 参考相机只有节奏这一项
        details = details[-1:]
        raw = f"score={score:.1f} | status={status} | dt_cv={dt_m.get('dt_cv', '-')}"
    grade, _, _ = grade_from_score(score)
    return {
        "name": report.get("topic", "?"), "weight": weight,
        "passed": not failed, "score": score, "gap": gap,
        "weighted_gap": round(gap * weight, 1), "raw_value": raw,
        "details": details, "grade": grade,
    }


def _emit_module(score: float, passed: bool, sub_indicators: list,
                 reasons: list, threshold_profile: dict,
                 threshold: float = 90.0) -> dict:
    """构造标准模块 dict → format_report → 用 yjy 自定义 reasons 覆盖。

    grade/passed 沿用 yjy 的 grade_from_score 语义 (不用 shared.to_grade, 分界不同)；
    verdict 由 format_report 基于 passed + threshold 生成 (threshold=90 与 yjy 原口径一致)。
    """
    grade, _, _ = grade_from_score(score)
    module = {
        "name": "时间戳对齐质量", "module_key": "check_timestamp", "max_score": 100,
        "total_score": round(score, 3), "passed": passed,
        "sub_indicators": sub_indicators, "warnings": [],
        "grade": grade, "threshold_profile": threshold_profile,
    }
    report = format_report(module, threshold)
    report["reasons"] = reasons
    return report


def assess(ts_map: dict, cameras, fmt: str = '', available: str = '', weights: dict = None) -> dict:
    """各路时间轴 {topic: [秒]}（已去掉生成的时间和 metadata 通道）→ 时间戳对齐报告。
    cameras：哪些 topic 是相机（参考从相机里选）；fmt、available：不能评估时写进说明的数据格式和可用内容。"""
    args = argparse.Namespace(
        iou_threshold=DEFAULT_IOU_THRESHOLD,
        match_window_factor=DEFAULT_MATCH_WINDOW_FACTOR,
        min_match_window_ms=DEFAULT_MIN_MATCH_WINDOW_MS,
        absolute_error_floor_ms=DEFAULT_ABSOLUTE_ERROR_FLOOR_MS,
        long_gap_multiple=DEFAULT_LONG_GAP_MULTIPLE,
        long_gap_ratio_warning=DEFAULT_LONG_GAP_RATIO_WARNING,
        long_gap_ratio_zero=DEFAULT_LONG_GAP_RATIO_ZERO,
        dt_cv_no_penalty=DEFAULT_DT_CV_NO_PENALTY,
        dt_cv_zero=DEFAULT_DT_CV_ZERO,
        drift_rate_no_penalty_ms_per_min=DEFAULT_DRIFT_RATE_NO_PENALTY_MS_PER_MIN,
        drift_rate_zero_ms_per_min=DEFAULT_DRIFT_RATE_ZERO_MS_PER_MIN,
        drift_r2_threshold=DEFAULT_DRIFT_R2_THRESHOLD,
        drift_min_points=DEFAULT_DRIFT_MIN_POINTS,
        drift_min_duration_s=DEFAULT_DRIFT_MIN_DURATION_S,
    )

    streams = []
    for topic_name, ts_list in sorted(ts_map.items()):
        kind = "camera" if topic_name in cameras else "action"
        streams.append(RawStream(topic_name, kind, list(ts_list)))

    if len(streams) < 2:
        from edc.data.readers.base import make_ir_report
        from edc.data.ir.metadata import IRNotProvided
        exc = IRNotProvided(">=2 通道时间戳", fmt, available)
        return make_ir_report("时间戳对齐质量", "check_timestamp", exc)

    try:
        reference = choose_reference(streams)
    except ValueError:
        from edc.data.readers.base import make_ir_report
        from edc.data.ir.metadata import IRNotProvided
        exc = IRNotProvided("可用参考 topic (camera)", fmt, available)
        return make_ir_report("时间戳对齐质量", "check_timestamp", exc)

    reference_row, broken = score_reference(reference, args)
    if broken:
        return _reference_failed(reference, broken)
    topic_reports = [reference_row]
    for stream in streams:
        if stream.topic == reference.topic:
            continue
        report = score_topic(reference, stream, args)
        _add_topic_grade(report)
        topic_reports.append(report)

    if len(topic_reports) < 2:
        from edc.data.readers.base import make_ir_report
        from edc.data.ir.metadata import IRNotProvided
        exc = IRNotProvided("非参考 topic", fmt, available)
        return make_ir_report("时间戳对齐质量", "check_timestamp", exc)

    # 头部只扣分：节奏分低于其他路的平均时才计入平均（拉低），否则不计入
    others = [float(r.get("score", 0.0) or 0.0) for r in topic_reports[1:]]
    counted = others + ([reference_row["score"]] if reference_row["score"] < sum(others) / len(others) else [])
    bag_score = sum(counted) / len(counted)
    passed = grade_from_score(bag_score)[1]
    n_topics = len(topic_reports) or 1
    topic_weight = round(1.0 / n_topics, 4)
    topic_subs = [_topic_to_sub_indicator(r, topic_weight) for r in topic_reports]
    reasons = [
        f"reference topic 为 {reference.topic}。",
        f"共评价 {len(counted)} 个 topic，算术平均分为 {bag_score:.3f}。",
    ]
    for r in sorted(topic_reports, key=lambda x: float(x.get("score", 0.0) or 0.0)):
        if float(r.get("score", 0.0) or 0.0) <= 80.0:
            attr = "；".join(r.get("attribution", [])) or "未归因"
            reasons.append(f"topic {r['topic']}：score={float(r.get('score', 0.0) or 0.0):.3f}，{attr}")
    return _emit_module(
        score=bag_score, passed=passed, sub_indicators=topic_subs, reasons=reasons,
        threshold_profile={"reference_topic": reference.topic, "aggregate": "mean(topic_scores), reference only when below the others' mean"},
        threshold=90.0,
    )


