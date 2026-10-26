#!/usr/bin/env python3
"""Timestamp alignment checker for raw_export directories."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import types
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from statistics import mean, median
from typing import Any



@dataclass(frozen=True)
class RawStreamSpec:
    name: str
    raw_topic: str
    kind: str


@dataclass(frozen=True)
class HeaderTimestampLoad:
    timestamps: list[float]
    total_count: int
    zero_header_count: int
    missing_header_count: int

SUPPORTED_EXPORT_KINDS = {"jsonl"}



DEFAULT_DT_GAP_FACTOR = 5.0                           #采样间隔异常阈值：dt_max / dt_median
DEFAULT_COMPARABLE_WINDOW_FACTOR = 0.5                #与 run_check.py 的 match_window 一致
DEFAULT_COMPARABLE_MIN_WINDOW_MS = 8.0
DEFAULT_COMPARABLE_MISSING_RATIO = 0.10               #与 run_check.py 的 missing fail 阈值一致
DEFAULT_COMPARABLE_WINDOW_IOU = 0.95                  #与 run_check.py 的 IoU 阈值一致
DEFAULT_NEAREST_P95_PERIOD_RATIO = 0.6
DEFAULT_NEAREST_MAX_PERIOD_RATIO = 1.5

STEP1_STATUS_TEXT = {
    "pass": "通过",
    "warning": "警告",
    "fail": "失败",
}

COMPARABLE_TEXT = {
    True: "可比较",
    False: "不可比较",
}

DIAGNOSIS_TEXT = {
    "reference": "参考流",
    "strict_aligned": "严格对齐",
    "coarse_comparable_but_not_strict": "可比较但未达严格对齐",
    "timestamps_not_comparable": "时间戳不可比较",
    "invalid_timestamps": "时间戳非法",
}


@dataclass
class StreamStats:
    name: str
    kind: str
    count: int
    start: float
    end: float
    duration: float
    dt_mean: float | None
    dt_median: float | None
    dt_min: float | None
    dt_max: float | None


@dataclass
class DtAnomaly:
    start_frame: int
    end_frame: int
    start_ts: float
    end_ts: float
    dt_ms: float
    threshold_ms: float


@dataclass
class Step1Result:
    non_empty_ok: bool
    monotonic_ok: bool
    duplicate_free_ok: bool
    abnormal_free_ok: bool
    dt_reasonable_ok: bool
    status: str
    notes: list[str]
    dt_anomalies: list[DtAnomaly]


@dataclass
class Step2Result:
    comparable: bool
    match_window_ms: float
    overlap_count: int
    overlap_duration_s: float
    window_iou: float
    nearest_median_ms: float | None
    nearest_p95_ms: float | None
    nearest_p99_ms: float | None
    nearest_max_ms: float | None
    missing_match_ratio: float | None
    nearest_signed_median_ms: float | None
    nearest_signed_mean_ms: float | None
    nearest_signed_std_ms: float | None
    residual_jitter_ms: float | None
    drift_span_ms: float | None


@dataclass
class Diagnosis:
    category: str
    note: str


@dataclass
class StreamReport:
    stats: StreamStats
    step1: Step1Result
    step2: Step2Result | None
    diagnosis: Diagnosis
    is_reference: bool = False


def infer_stream_kind(name: str) -> str:
    if name.startswith("sensors/"):
        return "camera"
    if name.startswith("actions/"):
        return "action"
    return "unknown"


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("percentile 至少需要一个数值")
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    weight = index - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def summarize_stream(name: str, timestamps: list[float], args: argparse.Namespace) -> StreamStats:
    if not timestamps:
        return StreamStats(
            name=name,
            kind=infer_stream_kind(name),
            count=0,
            start=0.0,
            end=0.0,
            duration=0.0,
            dt_mean=None,
            dt_median=None,
            dt_min=None,
            dt_max=None,
        )

    kind = infer_stream_kind(name)
    if len(timestamps) == 1:
        return StreamStats(
            name=name,
            kind=kind,
            count=1,
            start=timestamps[0],
            end=timestamps[0],
            duration=0.0,
            dt_mean=None,
            dt_median=None,
            dt_min=None,
            dt_max=None,
        )

    deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]
    dt_median = median(deltas)
    return StreamStats(
        name=name,
        kind=kind,
        count=len(timestamps),
        start=timestamps[0],
        end=timestamps[-1],
        duration=timestamps[-1] - timestamps[0],
        dt_mean=mean(deltas),
        dt_median=dt_median,
        dt_min=min(deltas),
        dt_max=max(deltas),
    )


def step1_check(timestamps: list[float], stats: StreamStats, args: argparse.Namespace) -> Step1Result:
    non_empty_ok = len(timestamps) > 0
    finite_ok = all(math.isfinite(ts) for ts in timestamps)
    negative_count = sum(1 for ts in timestamps if math.isfinite(ts) and ts < 0)
    backward_count = sum(1 for a, b in zip(timestamps, timestamps[1:]) if b < a)
    duplicate_count = sum(1 for a, b in zip(timestamps, timestamps[1:]) if b == a)
    deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]

    monotonic_ok = backward_count == 0 and duplicate_count == 0
    duplicate_free_ok = duplicate_count == 0
    abnormal_free_ok = finite_ok and negative_count == 0

    dt_reasonable_ok = True
    dt_anomalies: list[DtAnomaly] = []
    if (
        stats.dt_median is not None
        and stats.dt_max is not None
        and stats.dt_median > 0
        and stats.dt_max > stats.dt_median * args.dt_gap_factor
    ):
        dt_reasonable_ok = False

    notes: list[str] = []
    if not non_empty_ok:
        notes.append("空值/空流")
    if not finite_ok:
        notes.append("存在非有限时间戳")
    if negative_count:
        notes.append("存在负时间戳")
    if backward_count:
        notes.append(f"存在 {backward_count} 处非单调回退")
    if duplicate_count:
        notes.append(f"存在 {duplicate_count} 处重复值")
    if not dt_reasonable_ok:
        dt_threshold = stats.dt_median * args.dt_gap_factor
        abnormal_gap_indices = [index for index, delta in enumerate(deltas) if delta > dt_threshold]
        for index in abnormal_gap_indices:
            dt_anomalies.append(
                DtAnomaly(
                    start_frame=index + 1,
                    end_frame=index + 2,
                    start_ts=timestamps[index],
                    end_ts=timestamps[index + 1],
                    dt_ms=deltas[index] * 1000.0,
                    threshold_ms=dt_threshold * 1000.0,
                )
            )
        first_index_text = "n/a"
        if abnormal_gap_indices:
            first_index = abnormal_gap_indices[0]
            first_index_text = f"{first_index + 1}->{first_index + 2}"
        notes.append(
            "采样出现波动"
            f" (dt median={stats.dt_median * 1000.0:.3f} ms, "
            f"dt max={stats.dt_max * 1000.0:.3f} ms, "
            f"max/median={stats.dt_max / stats.dt_median:.3f}x > {args.dt_gap_factor:.3f}x, "
            f"abnormal gaps={len(abnormal_gap_indices)}, "
            f"first abnormal pair={first_index_text})"
        )
    hard_fail = (
        not non_empty_ok
        or not finite_ok
        or not monotonic_ok
        or not duplicate_free_ok
        or not abnormal_free_ok
    )
    if hard_fail:
        status = "fail"
    elif not dt_reasonable_ok:
        status = "warning"
    else:
        status = "pass"

    return Step1Result(
        non_empty_ok=non_empty_ok,
        monotonic_ok=monotonic_ok,
        duplicate_free_ok=duplicate_free_ok,
        abnormal_free_ok=abnormal_free_ok,
        dt_reasonable_ok=dt_reasonable_ok,
        status=status,
        notes=notes,
        dt_anomalies=dt_anomalies,
    )


def build_match_window_ms(reference_stats: StreamStats, target_stats: StreamStats, args: argparse.Namespace) -> float:
    candidates_ms: list[float] = []
    if reference_stats.dt_median is not None and reference_stats.dt_median > 0:
        candidates_ms.append(reference_stats.dt_median * 1000.0)
    if target_stats.dt_median is not None and target_stats.dt_median > 0:
        candidates_ms.append(target_stats.dt_median * 1000.0)
    if not candidates_ms:
        return args.comparable_min_window_ms
    return max(args.comparable_min_window_ms, max(candidates_ms) * args.comparable_window_factor)


def compute_mean_and_std(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    avg = mean(values)
    if len(values) == 1:
        return avg, 0.0
    variance = sum((value - avg) * (value - avg) for value in values) / len(values)
    return avg, math.sqrt(variance)


def step2_check(reference: list[float], target: list[float], match_window_ms: float, args: argparse.Namespace) -> Step2Result:
    overlap_start = max(reference[0], target[0])
    overlap_end = min(reference[-1], target[-1])
    reference_duration = reference[-1] - reference[0]
    target_duration = target[-1] - target[0]
    overlap_duration = max(0.0, overlap_end - overlap_start)
    union_duration = reference_duration + target_duration - overlap_duration
    window_iou = overlap_duration / union_duration if union_duration > 0 else 0.0

    if overlap_end <= overlap_start:
        return Step2Result(False, match_window_ms, 0, overlap_duration, window_iou, None, None, None, None, None, None, None, None, None, None)

    overlap_ref = [ts for ts in reference if overlap_start <= ts <= overlap_end]
    if not overlap_ref:
        return Step2Result(False, match_window_ms, 0, overlap_duration, window_iou, None, None, None, None, None, None, None, None, None, None)

    errors_ms: list[float] = []
    signed_errors_ms: list[float] = []
    for ts in overlap_ref:
        idx = bisect_left(target, ts)
        candidates: list[tuple[float, float]] = []
        if idx < len(target):
            delta_ms = (target[idx] - ts) * 1000.0
            candidates.append((abs(delta_ms), delta_ms))
        if idx > 0:
            delta_ms = (target[idx - 1] - ts) * 1000.0
            candidates.append((abs(delta_ms), delta_ms))
        best_abs_ms, best_signed_ms = min(candidates, key=lambda item: item[0])
        errors_ms.append(best_abs_ms)
        signed_errors_ms.append(best_signed_ms)

    errors_ms.sort()
    missing_count = sum(1 for value in errors_ms if value > match_window_ms)
    missing_ratio = missing_count / len(errors_ms)
    nearest_median_ms = median(errors_ms)
    nearest_p95_ms = percentile(errors_ms, 0.95)
    nearest_p99_ms = percentile(errors_ms, 0.99)
    nearest_max_ms = errors_ms[-1]
    nearest_signed_median_ms = median(signed_errors_ms)
    nearest_signed_mean_ms, nearest_signed_std_ms = compute_mean_and_std(signed_errors_ms)
    signed_offset_ms = nearest_signed_median_ms
    residual_jitter_ms = percentile(sorted(abs(value - signed_offset_ms) for value in signed_errors_ms), 0.95)
    if len(signed_errors_ms) > 1:
        x_mean = (len(signed_errors_ms) - 1) / 2.0
        y_mean = mean(signed_errors_ms)
        denominator = sum((index - x_mean) ** 2 for index in range(len(signed_errors_ms)))
        slope = sum((index - x_mean) * (value - y_mean) for index, value in enumerate(signed_errors_ms)) / denominator
        drift_span_ms = slope * (len(signed_errors_ms) - 1)
    else:
        drift_span_ms = 0.0
    comparable = (
        window_iou >= args.comparable_window_iou
        and missing_ratio <= args.comparable_missing_ratio
    )
    return Step2Result(
        comparable=comparable,
        match_window_ms=match_window_ms,
        overlap_count=len(errors_ms),
        overlap_duration_s=overlap_duration,
        window_iou=window_iou,
        nearest_median_ms=nearest_median_ms,
        nearest_p95_ms=nearest_p95_ms,
        nearest_p99_ms=nearest_p99_ms,
        nearest_max_ms=nearest_max_ms,
        missing_match_ratio=missing_ratio,
        nearest_signed_median_ms=nearest_signed_median_ms,
        nearest_signed_mean_ms=nearest_signed_mean_ms,
        nearest_signed_std_ms=nearest_signed_std_ms,
        residual_jitter_ms=residual_jitter_ms,
        drift_span_ms=drift_span_ms,
    )


def format_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f} ms"


def format_ms_signed(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f} ms"


def format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.3f}%"


def infer_stream_period_ms(stats: StreamStats) -> float | None:
    if stats.dt_median is not None and stats.dt_median > 0:
        return stats.dt_median * 1000.0
    return None


def build_step2_failure_reason(
    step2: Step2Result,
    allowed_missing_ratio: float,
    allowed_window_iou: float,
) -> str:
    if step2.overlap_count == 0:
        return (
            "overlap=0，参考流与目标流无有效重叠区间，"
            f"window IoU={step2.window_iou:.3f} < {allowed_window_iou:.3f}"
        )

    parts: list[str] = []
    relation = ">=" if step2.window_iou >= allowed_window_iou else "<"
    parts.append(f"window IoU={step2.window_iou:.3f} {relation} {allowed_window_iou:.3f}")
    if step2.missing_match_ratio is not None:
        relation = "<=" if step2.missing_match_ratio <= allowed_missing_ratio else ">"
        parts.append(
            f"missing={format_pct(step2.missing_match_ratio)} {relation} {allowed_missing_ratio * 100.0:.3f}%"
        )
    parts.append(f"window={step2.match_window_ms:.3f} ms")
    return "，".join(parts)


def build_strict_failure_reason(
    step2: Step2Result,
    period_ms: float | None,
    allowed_p95_ms: float,
    allowed_max_ms: float,
) -> str:
    parts: list[str] = []
    if period_ms is not None:
        parts.append(f"T={period_ms:.3f} ms")
    if step2.nearest_p95_ms is not None:
        relation = "<=" if step2.nearest_p95_ms <= allowed_p95_ms else ">"
        parts.append(f"P95={step2.nearest_p95_ms:.3f} ms {relation} {allowed_p95_ms:.3f} ms")
    if step2.nearest_max_ms is not None:
        relation = "<=" if step2.nearest_max_ms <= allowed_max_ms else ">"
        parts.append(f"max={step2.nearest_max_ms:.3f} ms {relation} {allowed_max_ms:.3f} ms")
    return "，".join(parts)


def diagnose_stream(stats: StreamStats, step1: Step1Result, step2: Step2Result, args: argparse.Namespace) -> Diagnosis:
    if step1.status == "fail":
        note = "Step 1 未通过"
        if step1.notes:
            note += "；" + "；".join(step1.notes)
        return Diagnosis("invalid_timestamps", note)

    if not step2.comparable:
        reason = build_step2_failure_reason(
            step2,
            allowed_missing_ratio=args.comparable_missing_ratio,
            allowed_window_iou=args.comparable_window_iou,
        )
        return Diagnosis("timestamps_not_comparable", f"Step 2 未通过；{reason}")

    period_ms = infer_stream_period_ms(stats)
    if period_ms is None:
        return Diagnosis(
            "coarse_comparable_but_not_strict",
            "Step 2 可比较，但无法估计该 topic 的采样周期 T，未执行严格阈值判定",
        )

    allowed_p95_ms = period_ms * args.nearest_p95_period_ratio
    allowed_max_ms = period_ms * args.nearest_max_period_ratio

    if (
        step2.nearest_p95_ms is not None
        and step2.nearest_max_ms is not None
        and step2.nearest_p95_ms <= allowed_p95_ms
        and step2.nearest_max_ms <= allowed_max_ms
    ):
        return Diagnosis(
            "strict_aligned",
            (
                f"P95={format_ms(step2.nearest_p95_ms)}，"
                f"max={format_ms(step2.nearest_max_ms)}，"
                f"T={period_ms:.3f} ms，"
                f"阈值为 P95<={allowed_p95_ms:.3f} ms，max<={allowed_max_ms:.3f} ms"
            ),
        )

    return Diagnosis(
        "coarse_comparable_but_not_strict",
        (
            "Step 2 可比较，但未达严格对齐阈值；"
            f"{build_strict_failure_reason(step2, period_ms, allowed_p95_ms, allowed_max_ms)}"
        ),
    )


def step1_issue_text(step1: Step1Result) -> str:
    if step1.status == "pass":
        return ""
    if step1.notes:
        return f"{STEP1_STATUS_TEXT[step1.status]}: " + "；".join(step1.notes)
    return STEP1_STATUS_TEXT[step1.status]


def format_metric_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def format_metric_signed(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}"


def format_metric_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:.3f}%"


def kv_line(label: str, value: str) -> str:
    return f"  {label:<28}: {value}"


def format_dt_anomaly(anomaly: DtAnomaly) -> str:
    return (
        f"frame {anomaly.start_frame}->{anomaly.end_frame}, "
        f"ts={anomaly.start_ts:.6f}->{anomaly.end_ts:.6f}, "
        f"dt={anomaly.dt_ms:.3f} ms > {anomaly.threshold_ms:.3f} ms"
    )


def append_step1_detail_lines(lines: list[str], step1: Step1Result, limit: int = 5) -> None:
    if not step1.dt_anomalies:
        return
    lines.append(kv_line("abnormal dt count", str(len(step1.dt_anomalies))))
    for index, anomaly in enumerate(step1.dt_anomalies[:limit], start=1):
        lines.append(kv_line(f"dt anomaly {index}", format_dt_anomaly(anomaly)))
    remaining = len(step1.dt_anomalies) - limit
    if remaining > 0:
        lines.append(kv_line("dt anomaly more", f"... {remaining} more"))


def build_conclusion_text(report: StreamReport) -> str:
    label = DIAGNOSIS_TEXT[report.diagnosis.category]
    if not report.diagnosis.note:
        return label
    return f"{label}；{report.diagnosis.note}"


def collect_uniform_failed_metrics(report: StreamReport, args: argparse.Namespace) -> list[dict[str, object]]:
    """Return failed metrics for the shared nearest-neighbor evaluation."""
    failed: list[dict[str, object]] = []
    if report.step1.status != "pass":
        failed.append(
            {
                "metric": "timestamp_validity",
                "status": report.step1.status,
                "detail": step1_issue_text(report.step1),
            }
        )

    if report.is_reference or report.step2 is None:
        return failed

    if not report.step2.comparable:
        if report.step2.window_iou < args.comparable_window_iou:
            failed.append(
                {
                    "metric": "window_iou",
                    "value": report.step2.window_iou,
                    "threshold": args.comparable_window_iou,
                    "detail": f"{report.step2.window_iou:.3f} < {args.comparable_window_iou:.3f}",
                }
            )
        if (
            report.step2.missing_match_ratio is not None
            and report.step2.missing_match_ratio > args.comparable_missing_ratio
        ):
            failed.append(
                {
                    "metric": "missing_match_ratio",
                    "value": report.step2.missing_match_ratio,
                    "threshold": args.comparable_missing_ratio,
                    "detail": f"{format_pct(report.step2.missing_match_ratio)} > {args.comparable_missing_ratio * 100.0:.3f}%",
                }
            )
        return failed

    period_ms = infer_stream_period_ms(report.stats)
    if period_ms is None:
        failed.append(
            {
                "metric": "period_ms",
                "detail": "无法估计该 topic 的采样周期 T，未执行严格阈值判定",
            }
        )
        return failed

    allowed_p95_ms = period_ms * args.nearest_p95_period_ratio
    allowed_max_ms = period_ms * args.nearest_max_period_ratio
    if report.step2.nearest_p95_ms is not None and report.step2.nearest_p95_ms > allowed_p95_ms:
        failed.append(
            {
                "metric": "nearest_p95_ms",
                "value": report.step2.nearest_p95_ms,
                "threshold": allowed_p95_ms,
                "detail": f"{report.step2.nearest_p95_ms:.3f} ms > {allowed_p95_ms:.3f} ms (T={period_ms:.3f} ms)",
            }
        )
    if report.step2.nearest_max_ms is not None and report.step2.nearest_max_ms > allowed_max_ms:
        failed.append(
            {
                "metric": "nearest_max_ms",
                "value": report.step2.nearest_max_ms,
                "threshold": allowed_max_ms,
                "detail": f"{report.step2.nearest_max_ms:.3f} ms > {allowed_max_ms:.3f} ms (T={period_ms:.3f} ms)",
            }
        )
    return failed


def write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def append_verbose_lines(lines: list[str], report: StreamReport) -> None:
    lines.append(kv_line("count", str(report.stats.count)))
    lines.append(
        kv_line(
            "dt median ms",
            format_metric_float(None if report.stats.dt_median is None else report.stats.dt_median * 1000.0),
        )
    )
    lines.append(
        kv_line(
            "dt max ms",
            format_metric_float(None if report.stats.dt_max is None else report.stats.dt_max * 1000.0),
        )
    )


ct = types.SimpleNamespace(
    DEFAULT_DT_GAP_FACTOR=DEFAULT_DT_GAP_FACTOR,
    DEFAULT_COMPARABLE_WINDOW_FACTOR=DEFAULT_COMPARABLE_WINDOW_FACTOR,
    DEFAULT_COMPARABLE_MIN_WINDOW_MS=DEFAULT_COMPARABLE_MIN_WINDOW_MS,
    DEFAULT_COMPARABLE_MISSING_RATIO=DEFAULT_COMPARABLE_MISSING_RATIO,
    DEFAULT_COMPARABLE_WINDOW_IOU=DEFAULT_COMPARABLE_WINDOW_IOU,
    DEFAULT_NEAREST_P95_PERIOD_RATIO=DEFAULT_NEAREST_P95_PERIOD_RATIO,
    DEFAULT_NEAREST_MAX_PERIOD_RATIO=DEFAULT_NEAREST_MAX_PERIOD_RATIO,
    STEP1_STATUS_TEXT=STEP1_STATUS_TEXT,
    COMPARABLE_TEXT=COMPARABLE_TEXT,
    DIAGNOSIS_TEXT=DIAGNOSIS_TEXT,
    StreamStats=StreamStats,
    DtAnomaly=DtAnomaly,
    Step1Result=Step1Result,
    Step2Result=Step2Result,
    Diagnosis=Diagnosis,
    StreamReport=StreamReport,
    summarize_stream=summarize_stream,
    step1_check=step1_check,
    build_match_window_ms=build_match_window_ms,
    step2_check=step2_check,
    diagnose_stream=diagnose_stream,
    step1_issue_text=step1_issue_text,
    format_metric_float=format_metric_float,
    format_metric_signed=format_metric_signed,
    format_metric_pct=format_metric_pct,
    format_dt_anomaly=format_dt_anomaly,
    append_step1_detail_lines=append_step1_detail_lines,
    build_conclusion_text=build_conclusion_text,
    collect_failed_metrics=collect_uniform_failed_metrics,
    infer_stream_period_ms=infer_stream_period_ms,
    append_verbose_lines=append_verbose_lines,
    kv_line=kv_line,
    write_json_file=write_json_file,
    append_jsonl_file=append_jsonl_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check timestamp alignment in raw_export directories using ROS header timestamps."
    )
    parser.add_argument(
        "export",
        nargs="?",
        default=None,
        help="raw_export directory path",
    )
    parser.add_argument(
        "--export-path",
        dest="export_path",
        default=None,
        help="Explicit raw_export directory path. Overrides positional export.",
    )
    parser.add_argument(
        "--reference",
        default=None,
        help=(
            "Reference stream name or raw topic. "
            "If omitted, choose the best available camera stream automatically."
        ),
    )
    parser.add_argument(
        "--dt-gap-factor",
        type=float,
        default=ct.DEFAULT_DT_GAP_FACTOR,
        help="If dt max > dt median * factor, sampling is considered unstable.",
    )
    parser.add_argument(
        "--comparable-window-factor",
        type=float,
        default=ct.DEFAULT_COMPARABLE_WINDOW_FACTOR,
        help="Nearest-neighbor match window factor applied to the slower stream period.",
    )
    parser.add_argument(
        "--comparable-min-window-ms",
        type=float,
        default=ct.DEFAULT_COMPARABLE_MIN_WINDOW_MS,
        help="Lower bound of the Step 2 nearest-neighbor match window in ms.",
    )
    parser.add_argument(
        "--comparable-missing-ratio",
        type=float,
        default=ct.DEFAULT_COMPARABLE_MISSING_RATIO,
        help="Allowed upper bound of missing match ratio in Step 2.",
    )
    parser.add_argument(
        "--comparable-window-iou",
        type=float,
        default=ct.DEFAULT_COMPARABLE_WINDOW_IOU,
        help="Required lower bound of capture-window IoU in Step 2.",
    )
    parser.add_argument(
        "--nearest-p95-period-ratio",
        type=float,
        default=ct.DEFAULT_NEAREST_P95_PERIOD_RATIO,
        help="Strict threshold ratio for nearest P95 relative to target stream period T.",
    )
    parser.add_argument(
        "--nearest-max-period-ratio",
        type=float,
        default=ct.DEFAULT_NEAREST_MAX_PERIOD_RATIO,
        help="Strict threshold ratio for nearest max relative to target stream period T.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra raw statistics.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Write a compact JSON report to the given file.",
    )
    parser.add_argument(
        "--jsonl-out",
        default=None,
        help="Append a compact JSON record to the given JSONL file.",
    )
    return parser.parse_args()


def resolve_export_dir(args: argparse.Namespace) -> Path:
    raw_path = args.export_path or args.export
    if not raw_path:
        raise SystemExit("Please provide a raw_export directory path.")
    export_dir = Path(raw_path).expanduser()
    if export_dir.is_file():
        return export_dir.parent
    return export_dir


def is_raw_export_dir(path: Path) -> bool:
    return path.is_dir() and (path / "topics.csv").is_file()


def load_topics_table(export_dir: Path) -> dict[str, dict[str, str]]:
    topics_csv = export_dir / "topics.csv"
    rows: dict[str, dict[str, str]] = {}
    with topics_csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            topic = row.get("topic", "").strip()
            if topic:
                rows[topic] = row
    return rows


def parse_stream_count(row: dict[str, str]) -> int:
    raw = row.get("msgcount", "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def normalize_topic_slug(raw_topic: str) -> str:
    stripped = raw_topic.strip("/")
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", stripped.replace("/", "_"))
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "unnamed_topic"


def is_likely_camera_topic(raw_topic: str, row: dict[str, str]) -> bool:
    export_kind = row.get("export_kind", "").strip()
    msgtype = row.get("msgtype", "").strip().lower()
    topic_lower = raw_topic.lower()

    if "compressedimage" in msgtype or msgtype.endswith("/image"):
        return True
    if msgtype.endswith("/camerainfo"):
        return True
    if "/image" in topic_lower or topic_lower.endswith("/camera_info"):
        return True
    return False


def build_generic_specs(topics_table: dict[str, dict[str, str]]) -> list[RawStreamSpec]:
    specs: list[RawStreamSpec] = []
    used_names: set[str] = set()

    for raw_topic in sorted(topics_table):
        row = topics_table[raw_topic]
        topic_lower = raw_topic.lower()
        export_kind = row.get("export_kind", "").strip()
        if parse_stream_count(row) <= 1:
            continue
        if export_kind not in SUPPORTED_EXPORT_KINDS:
            continue
        if topic_lower.endswith("/camera_info") or topic_lower.endswith("/metadata"):
            continue

        kind = "camera" if is_likely_camera_topic(raw_topic, row) else "action"
        prefix = "sensors" if kind == "camera" else "actions"
        base_name = f"{prefix}/{normalize_topic_slug(raw_topic)}"
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)
        specs.append(
            RawStreamSpec(
                name=name,
                raw_topic=raw_topic,
                kind=kind,
            )
        )

    return specs


def row_path(export_dir: Path, row: dict[str, str]) -> Path:
    relative = row.get("relative_output", "").replace("\\", "/")
    return export_dir / Path(PurePosixPath(relative))


def header_seconds(sec_value: Any, nanosec_value: Any, source: str) -> float:
    if sec_value in (None, "") or nanosec_value in (None, ""):
        raise ValueError(f"{source} missing header stamp")
    sec = int(sec_value)
    nanosec = int(nanosec_value)
    return sec + nanosec / 1_000_000_000.0


def is_zero_header(sec_value: Any, nanosec_value: Any) -> bool:
    return int(sec_value) == 0 and int(nanosec_value) == 0


def extract_json_header_seconds(record: dict[str, Any], source: str) -> float:
    top_sec = record.get("header_stamp_sec")
    top_nanosec = record.get("header_stamp_nanosec")
    if top_sec not in (None, "") and top_nanosec not in (None, ""):
        return header_seconds(top_sec, top_nanosec, source)

    message = record.get("message")
    if isinstance(message, dict):
        header = message.get("header")
        if isinstance(header, dict):
            stamp = header.get("stamp")
            if isinstance(stamp, dict):
                return header_seconds(stamp.get("sec"), stamp.get("nanosec"), source)

    raise ValueError(f"{source} missing header stamp")


def read_jsonl_header_timestamps(path: Path) -> HeaderTimestampLoad:
    timestamps: list[float] = []
    total_count = 0
    zero_header_count = 0
    missing_header_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            total_count += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSON") from exc
            top_sec = record.get("header_stamp_sec")
            top_nanosec = record.get("header_stamp_nanosec")
            if top_sec not in (None, "") and top_nanosec not in (None, ""):
                if is_zero_header(top_sec, top_nanosec):
                    zero_header_count += 1
                    continue
                timestamps.append(header_seconds(top_sec, top_nanosec, f"{path}:{line_number}"))
                continue

            message = record.get("message")
            if isinstance(message, dict):
                header = message.get("header")
                if isinstance(header, dict):
                    stamp = header.get("stamp")
                    if isinstance(stamp, dict):
                        sec_value = stamp.get("sec")
                        nanosec_value = stamp.get("nanosec")
                        if sec_value in (None, "") or nanosec_value in (None, ""):
                            missing_header_count += 1
                            continue
                        if is_zero_header(sec_value, nanosec_value):
                            zero_header_count += 1
                            continue
                        timestamps.append(
                            header_seconds(sec_value, nanosec_value, f"{path}:{line_number}")
                        )
                        continue

            missing_header_count += 1
    return HeaderTimestampLoad(
        timestamps=timestamps,
        total_count=total_count,
        zero_header_count=zero_header_count,
        missing_header_count=missing_header_count,
    )


def read_csv_header_timestamps(path: Path) -> HeaderTimestampLoad:
    timestamps: list[float] = []
    total_count = zero_header_count = missing_header_count = 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            total_count += 1
            sec_value = row.get("header_stamp_sec")
            nanosec_value = row.get("header_stamp_nanosec")
            if sec_value in (None, "") or nanosec_value in (None, ""):
                missing_header_count += 1
                continue
            if is_zero_header(sec_value, nanosec_value):
                zero_header_count += 1
                continue
            timestamps.append(header_seconds(sec_value, nanosec_value, str(path)))
    return HeaderTimestampLoad(
        timestamps=timestamps,
        total_count=total_count,
        zero_header_count=zero_header_count,
        missing_header_count=missing_header_count,
    )


def read_spec_timestamps(path: Path, export_kind: str) -> HeaderTimestampLoad:
    if export_kind == "jsonl":
        return read_jsonl_header_timestamps(path)
    if export_kind == "compressed_binary_frames":
        return read_csv_header_timestamps(path)
    raise ValueError(f"{path} unsupported export_kind: {export_kind}")


def select_stream_specs(
    topics_table: dict[str, dict[str, str]],
) -> list[RawStreamSpec]:
    specs = build_generic_specs(topics_table)
    if specs:
        return specs
    raise SystemExit("No supported stream with msgcount > 0 found in this raw_export directory.")


def reference_priority(spec: RawStreamSpec) -> tuple[int, str]:
    topic_lower = spec.raw_topic.lower()
    score = 0

    if spec.kind == "camera":
        score += 1000
    if "/camera_info" in topic_lower:
        score -= 150
    if "/metadata" in topic_lower:
        score -= 250
    if "/image" in topic_lower:
        score += 120
    if "compresseddepth" in topic_lower or "/depth/" in topic_lower:
        score += 20
    if "/color/" in topic_lower or "rgb" in topic_lower:
        score += 40
    if any(token in topic_lower for token in ("/cam_h/", "/camera_head_front/", "head_front", "cam_front")):
        score += 80
    if "/front/" in topic_lower:
        score += 30

    return score, spec.raw_topic


def resolve_reference_name(
    reference_arg: str | None,
    available_specs: list[RawStreamSpec],
    valid_names: set[str],
) -> str:
    spec_by_topic = {spec.raw_topic: spec for spec in available_specs}

    if reference_arg:
        if reference_arg in valid_names:
            return reference_arg
        if reference_arg in spec_by_topic:
            mapped_name = spec_by_topic[reference_arg].name
            if mapped_name in valid_names:
                return mapped_name
        raise SystemExit(f"Reference stream not found: {reference_arg}")

    valid_specs = [spec for spec in available_specs if spec.name in valid_names]
    if not valid_specs:
        raise SystemExit("No valid stream with usable non-zero header timestamps can be used as reference.")
    return max(valid_specs, key=reference_priority).name


def make_empty_stats(name: str, kind: str) -> ct.StreamStats:
    return ct.StreamStats(
        name=name,
        kind=kind,
        count=0,
        start=0.0,
        end=0.0,
        duration=0.0,
        dt_mean=None,
        dt_median=None,
        dt_min=None,
        dt_max=None,
    )


def make_invalid_step1(reason: str) -> ct.Step1Result:
    return ct.Step1Result(
        non_empty_ok=False,
        monotonic_ok=False,
        duplicate_free_ok=False,
        abnormal_free_ok=False,
        dt_reasonable_ok=False,
        status="fail",
        notes=[reason],
        dt_anomalies=[],
    )


def build_invalid_report(spec: RawStreamSpec, args: argparse.Namespace, reason: str) -> ct.StreamReport:
    stats = make_empty_stats(spec.name, spec.kind)
    step1 = make_invalid_step1(reason)
    diagnosis = ct.Diagnosis("invalid_timestamps", f"Step 1 未通过；{reason}")
    return ct.StreamReport(
        stats=stats,
        step1=step1,
        step2=None,
        diagnosis=diagnosis,
        is_reference=False,
    )


def build_summary(sensor_reports: list[ct.StreamReport], action_reports: list[ct.StreamReport]) -> dict[str, object]:
    all_reports = sensor_reports + action_reports
    diagnosis_counts: dict[str, int] = {}
    for report in all_reports:
        category = report.diagnosis.category
        diagnosis_counts[category] = diagnosis_counts.get(category, 0) + 1
    return {
        "stream_count": len(all_reports),
        "camera_stream_count": len(sensor_reports),
        "action_stream_count": len(action_reports),
        "diagnosis_counts": diagnosis_counts,
    }


def serialize_stream_report(
    report: ct.StreamReport,
    spec: RawStreamSpec,
    source_path: Path,
    header_load: HeaderTimestampLoad | None,
    args: argparse.Namespace,
) -> dict[str, object]:
    stream: dict[str, object] = {
        "raw_topic": spec.raw_topic,
        "source_path": PurePosixPath(source_path.relative_to(resolve_export_dir(args))).as_posix(),
        "timestamp_source": "non_zero_header",
        "kind": report.stats.kind,
        "is_reference": report.is_reference,
        "count": report.stats.count,
        "timestamp_valid": report.step1.status == "pass",
    }
    if report.step1.dt_anomalies:
        stream["abnormal_dt_count"] = len(report.step1.dt_anomalies)
        stream["dt_anomalies"] = [
            {
                "start_frame": anomaly.start_frame,
                "end_frame": anomaly.end_frame,
                "start_ts": anomaly.start_ts,
                "end_ts": anomaly.end_ts,
                "dt_ms": anomaly.dt_ms,
                "threshold_ms": anomaly.threshold_ms,
                "detail": ct.format_dt_anomaly(anomaly),
            }
            for anomaly in report.step1.dt_anomalies
        ]
    if header_load is not None:
        stream["raw_count"] = header_load.total_count
        stream["zero_header_count"] = header_load.zero_header_count
        stream["missing_header_count"] = header_load.missing_header_count

    if report.is_reference:
        stream["comparable"] = "reference"
    elif report.step2 is None:
        stream["comparable"] = "n/a"
        stream["nearest_median_ms"] = None
        stream["nearest_p95_ms"] = None
        stream["nearest_p99_ms"] = None
        stream["nearest_max_ms"] = None
        stream["missing_match_ratio"] = None
        stream["overlap_duration_s"] = None
        stream["window_iou"] = None
        stream["nearest_signed_median_ms"] = None
        stream["nearest_signed_mean_ms"] = None
        stream["nearest_signed_std_ms"] = None
        stream["source_match_coverage"] = None
        stream["residual_jitter_ms"] = None
        stream["drift_span_ms"] = None
    else:
        stream["comparable"] = ct.COMPARABLE_TEXT[report.step2.comparable]
        stream["nearest_median_ms"] = report.step2.nearest_median_ms
        stream["nearest_p95_ms"] = report.step2.nearest_p95_ms
        stream["nearest_p99_ms"] = report.step2.nearest_p99_ms
        stream["nearest_max_ms"] = report.step2.nearest_max_ms
        stream["missing_match_ratio"] = report.step2.missing_match_ratio
        stream["overlap_duration_s"] = report.step2.overlap_duration_s
        stream["window_iou"] = report.step2.window_iou
        stream["nearest_signed_median_ms"] = report.step2.nearest_signed_median_ms
        stream["nearest_signed_mean_ms"] = report.step2.nearest_signed_mean_ms
        stream["nearest_signed_std_ms"] = report.step2.nearest_signed_std_ms
        stream["source_match_coverage"] = 1.0 - (report.step2.missing_match_ratio or 0.0)
        stream["residual_jitter_ms"] = report.step2.residual_jitter_ms
        stream["drift_span_ms"] = report.step2.drift_span_ms

    period_ms = ct.infer_stream_period_ms(report.stats)
    if period_ms is not None:
        stream["period_ms"] = period_ms

    return stream


def build_export_result(
    export_dir: Path,
    reference_name: str,
    sensor_reports: list[ct.StreamReport],
    action_reports: list[ct.StreamReport],
    spec_map: dict[str, RawStreamSpec],
    source_path_map: dict[str, Path],
    header_load_map: dict[str, HeaderTimestampLoad],
    args: argparse.Namespace,
) -> dict[str, object]:
    all_reports = sensor_reports + action_reports
    topics: dict[str, object] = {}
    for report in all_reports:
        spec = spec_map[report.stats.name]
        topics[report.stats.name] = serialize_stream_report(
            report,
            spec,
            source_path_map[report.stats.name],
            header_load_map.get(report.stats.name),
            args,
        )
    return {
        "file": export_dir.name,
        "timestamp_source": "non_zero_header",
        "reference_stream": reference_name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": build_summary(sensor_reports, action_reports),
        "topics": topics,
    }


def build_stream_report_text(
    report: ct.StreamReport,
    spec: RawStreamSpec,
    header_load: HeaderTimestampLoad | None,
    verbose: bool,
) -> str:
    lines = [f"[{report.stats.name}]"]
    lines.append(ct.kv_line("topic", spec.raw_topic))
    if report.is_reference:
        lines.append(ct.kv_line("count", str(report.stats.count)))
        return "\n".join(lines)

    if report.step1.status != "pass":
        lines.append(ct.kv_line("timestamp validity", ct.step1_issue_text(report.step1)))
        ct.append_step1_detail_lines(lines, report.step1)
    if header_load is not None and (header_load.zero_header_count or header_load.missing_header_count):
        lines.append(
            ct.kv_line(
                "filtered headers",
                f"usable={report.stats.count}, zero={header_load.zero_header_count}, missing={header_load.missing_header_count}",
            )
        )

    lines.append(ct.kv_line("count", str(report.stats.count)))
    if report.step2 is None:
        lines.append(ct.kv_line("comparable", "n/a"))
        lines.append(ct.kv_line("nearest median ms", "n/a"))
        lines.append(ct.kv_line("nearest signed median ms", "n/a"))
        lines.append(ct.kv_line("nearest p95 ms", "n/a"))
        lines.append(ct.kv_line("nearest max ms", "n/a"))
        lines.append(ct.kv_line("missing match ratio", "n/a"))
        lines.append(ct.kv_line("window IoU", "n/a"))
        if verbose:
            ct.append_verbose_lines(lines, report)
        return "\n".join(lines)

    lines.append(ct.kv_line("comparable", str(report.step2.comparable).lower()))
    lines.append(ct.kv_line("source match coverage", ct.format_metric_pct(1.0 - (report.step2.missing_match_ratio or 0.0))))
    lines.append(ct.kv_line("nearest median ms", ct.format_metric_float(report.step2.nearest_median_ms)))
    lines.append(ct.kv_line("nearest signed median ms", ct.format_metric_signed(report.step2.nearest_signed_median_ms)))
    lines.append(ct.kv_line("nearest p95 ms", ct.format_metric_float(report.step2.nearest_p95_ms)))
    lines.append(ct.kv_line("nearest p99 ms", ct.format_metric_float(report.step2.nearest_p99_ms)))
    lines.append(ct.kv_line("nearest max ms", ct.format_metric_float(report.step2.nearest_max_ms)))
    lines.append(ct.kv_line("missing match ratio", ct.format_metric_pct(report.step2.missing_match_ratio)))
    lines.append(ct.kv_line("window IoU", f"{report.step2.window_iou:.3f}"))
    lines.append(ct.kv_line("residual jitter ms", ct.format_metric_float(report.step2.residual_jitter_ms)))
    lines.append(ct.kv_line("drift span ms", ct.format_metric_signed(report.step2.drift_span_ms)))
    if verbose:
        lines.append(ct.kv_line("nearest signed mean ms", ct.format_metric_signed(report.step2.nearest_signed_mean_ms)))
        lines.append(ct.kv_line("nearest signed std ms", ct.format_metric_float(report.step2.nearest_signed_std_ms)))
        ct.append_verbose_lines(lines, report)
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    export_dir = resolve_export_dir(args)
    if not is_raw_export_dir(export_dir):
        raise SystemExit(f"Not a valid raw_export directory: {export_dir}")

    topics_table = load_topics_table(export_dir)
    available_specs = select_stream_specs(topics_table)
    if not available_specs:
        raise SystemExit(f"No supported streams found in {export_dir}")

    loaded_timestamps: dict[str, list[float]] = {}
    header_load_map: dict[str, HeaderTimestampLoad] = {}
    source_path_map: dict[str, Path] = {}
    spec_map: dict[str, RawStreamSpec] = {}
    load_error_reports: list[ct.StreamReport] = []

    for spec in available_specs:
        row = topics_table[spec.raw_topic]
        path = row_path(export_dir, row)
        source_path_map[spec.name] = path
        spec_map[spec.name] = spec
        try:
            header_load = read_spec_timestamps(path, row.get("export_kind", ""))
            header_load_map[spec.name] = header_load
            if header_load.timestamps:
                loaded_timestamps[spec.name] = header_load.timestamps
        except Exception as exc:
            load_error_reports.append(build_invalid_report(spec, args, str(exc)))

    valid_names = {name for name, timestamps in loaded_timestamps.items() if timestamps}
    if not valid_names:
        raise SystemExit(f"No stream with usable non-zero header timestamps found in {export_dir}")
    reference_name = resolve_reference_name(
        args.reference,
        available_specs,
        valid_names,
    )
    reference_timestamps = loaded_timestamps[reference_name]
    reference_stats = ct.summarize_stream(reference_name, reference_timestamps, args)
    reference_step1 = ct.step1_check(reference_timestamps, reference_stats, args)
    reference_report = ct.StreamReport(
        stats=reference_stats,
        step1=reference_step1,
        step2=None,
        diagnosis=ct.Diagnosis("reference", "reference"),
        is_reference=True,
    )

    sensor_reports: list[ct.StreamReport] = []
    action_reports: list[ct.StreamReport] = []

    sensor_reports.append(reference_report)

    for spec in available_specs:
        if spec.name == reference_name or spec.name not in loaded_timestamps:
            continue

        timestamps = loaded_timestamps[spec.name]
        stats = ct.summarize_stream(spec.name, timestamps, args)
        step1 = ct.step1_check(timestamps, stats, args)
        match_window_ms = ct.build_match_window_ms(reference_stats, stats, args)
        step2 = ct.step2_check(reference_timestamps, timestamps, match_window_ms, args)

        diagnosis = ct.diagnose_stream(stats, step1, step2, args)
        report = ct.StreamReport(
            stats=stats,
            step1=step1,
            step2=step2,
            diagnosis=diagnosis,
            is_reference=False,
        )
        if spec.kind == "camera":
            sensor_reports.append(report)
        else:
            action_reports.append(report)

    for report in load_error_reports:
        if report.stats.kind == "camera":
            sensor_reports.append(report)
        else:
            action_reports.append(report)

    reference_reports = [report for report in sensor_reports if report.is_reference]
    other_sensor_reports = [report for report in sensor_reports if not report.is_reference]
    sensor_reports = reference_reports + sorted(other_sensor_reports, key=lambda report: report.stats.name)
    action_reports = sorted(action_reports, key=lambda report: report.stats.name)

    export_result = build_export_result(
        export_dir=export_dir,
        reference_name=reference_name,
        sensor_reports=sensor_reports,
        action_reports=action_reports,
        spec_map=spec_map,
        source_path_map=source_path_map,
        header_load_map=header_load_map,
        args=args,
    )

    if args.json_out:
        ct.write_json_file(Path(args.json_out).expanduser(), export_result)
    if args.jsonl_out:
        ct.append_jsonl_file(Path(args.jsonl_out).expanduser(), export_result)

    print("timestamp source: non-zero header")
    print(f"reference: {reference_name}")
    if reference_step1.status != "pass":
        print(ct.kv_line("reference validity", ct.step1_issue_text(reference_step1)))
        reference_lines: list[str] = []
        ct.append_step1_detail_lines(reference_lines, reference_step1)
        for line in reference_lines:
            print(line)

    blocks: list[str] = []
    for report in sensor_reports:
        spec = spec_map[report.stats.name]
        blocks.append(build_stream_report_text(report, spec, header_load_map.get(report.stats.name), args.verbose))
    for report in action_reports:
        spec = spec_map[report.stats.name]
        blocks.append(build_stream_report_text(report, spec, header_load_map.get(report.stats.name), args.verbose))

    print("\n\n".join(blocks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
