#!/usr/bin/env python3


from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from statistics import median, pstdev
from typing import Any
import os

# ── 接入统一输出契约 (shared.format_report) ──
_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))
from Quality_Assessment.shared.attribution import format_report

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
    msgtype: str
    msgcount: int
    path: Path
    kind: str
    timestamps: list[float] = field(default_factory=list)
    timestamp_record_indices: list[int] = field(default_factory=list)
    raw_count: int = 0
    invalid_counts: dict[str, int] = field(default_factory=dict)
    invalid_events: list[dict[str, Any]] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score one raw_export bag, or recursively score all exported bags in a directory."
    )
    parser.add_argument("input_path", type=Path, help="raw_export directory path")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively find directories containing topics.csv and score each one.",
    )
    parser.add_argument(
        "--reference",
        default=None,
        help="Reference raw topic. If omitted, choose the best camera topic.",
    )
    parser.add_argument(
        "--iou-threshold",
        type=float,
        default=DEFAULT_IOU_THRESHOLD,
        help=f"Topic receives zero when window IoU is lower than this value (default: {DEFAULT_IOU_THRESHOLD}).",
    )
    parser.add_argument("--match-window-factor", type=float, default=DEFAULT_MATCH_WINDOW_FACTOR)
    parser.add_argument("--min-match-window-ms", type=float, default=DEFAULT_MIN_MATCH_WINDOW_MS)
    parser.add_argument("--absolute-error-floor-ms", type=float, default=DEFAULT_ABSOLUTE_ERROR_FLOOR_MS)
    parser.add_argument("--long-gap-multiple", type=float, default=DEFAULT_LONG_GAP_MULTIPLE)
    parser.add_argument("--long-gap-ratio-warning", type=float, default=DEFAULT_LONG_GAP_RATIO_WARNING)
    parser.add_argument("--long-gap-ratio-zero", type=float, default=DEFAULT_LONG_GAP_RATIO_ZERO)
    parser.add_argument("--dt-cv-no-penalty", type=float, default=DEFAULT_DT_CV_NO_PENALTY)
    parser.add_argument("--dt-cv-zero", type=float, default=DEFAULT_DT_CV_ZERO)
    parser.add_argument("--drift-rate-no-penalty-ms-per-min", type=float,
                        default=DEFAULT_DRIFT_RATE_NO_PENALTY_MS_PER_MIN)
    parser.add_argument("--drift-rate-zero-ms-per-min", type=float,
                        default=DEFAULT_DRIFT_RATE_ZERO_MS_PER_MIN)
    parser.add_argument("--drift-r2-threshold", type=float, default=DEFAULT_DRIFT_R2_THRESHOLD)
    parser.add_argument("--drift-min-points", type=int, default=DEFAULT_DRIFT_MIN_POINTS)
    parser.add_argument("--drift-min-duration-s", type=float, default=DEFAULT_DRIFT_MIN_DURATION_S)
    parser.add_argument("--nearest-mode", choices=("forward",), default="forward", help="reference -> target \u5355\u5411\u6700\u8fd1\u90bb\u5339\u914d")
    parser.add_argument("--json-out", type=Path, default=None, help="Write report JSON to this path.")
    parser.add_argument("--verbose", action="store_true", help="保留兼容参数；评分输出仍只包含包级结果和 C/D topic 归因。")
    return parser.parse_args()


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


def parse_count(value: str | None) -> int:
    try:
        return int((value or "").strip())
    except ValueError:
        return 0


def explicit_false(value: str | None) -> bool:
    return (value or "").strip().lower() in {"0", "false", "no", "off"}


def row_exclusion_reason(row: dict[str, str]) -> str | None:
    topic = row.get("topic", "").strip().lower()
    export_kind = row.get("export_kind", "").strip()
    if parse_count(row.get("msgcount")) <= 1:
        return "insufficient_message_count"
    if export_kind not in {"jsonl", "compressed_binary_frames"}:
        return "unsupported_export_kind"
    if topic.endswith("/camera_info") or topic.endswith("/metadata"):
        return "excluded_topic_category"
    # New exports mark topics without a top-level header.stamp explicitly.
    # Old exports omit these columns and continue through the legacy path.
    if explicit_false(row.get("alignment_eligible")) or explicit_false(row.get("has_header")):
        return "no_header_stamp"
    return None


def is_scored_row(row: dict[str, str]) -> bool:
    return row_exclusion_reason(row) is None


def detect_kind(topic: str, msgtype: str) -> str:
    topic_lower = topic.lower()
    msgtype_lower = msgtype.lower()
    if "compressedimage" in msgtype_lower or msgtype_lower.endswith("/image") or "/image" in topic_lower:
        return "camera"
    return "action"


def row_path(root: Path, row: dict[str, str]) -> Path:
    relative = row.get("relative_output", "").replace("\\", "/")
    return root / Path(PurePosixPath(relative))


def increment(counts: dict[str, int], reason: str) -> None:
    counts[reason] = counts.get(reason, 0) + 1


def record_invalid(
    stream: RawStream,
    reason: str,
    record_index: int | None,
    line_number: int | None = None,
    **details: Any,
) -> None:
    increment(stream.invalid_counts, reason)
    event: dict[str, Any] = {"type": reason}
    if record_index is not None:
        event["record_index"] = record_index
    if line_number is not None:
        event["line_number"] = line_number
    event.update({key: value for key, value in details.items() if value is not None})
    stream.invalid_events.append(event)


def load_stream(row: dict[str, str], root: Path) -> RawStream:
    topic = row["topic"].strip()
    export_kind = row.get("export_kind", "").strip()
    stream = RawStream(
        topic=topic,
        msgtype=row.get("msgtype", "").strip(),
        msgcount=parse_count(row.get("msgcount")),
        path=row_path(root, row),
        kind=detect_kind(topic, row.get("msgtype", "")),
    )
    if not stream.path.is_file():
        record_invalid(stream, "timestamp_file_missing", None)
        return stream

    def append_timestamp(record: dict[str, Any], record_index: int, line_number: int | None = None) -> None:
        sec = record.get("header_stamp_sec")
        nanosec = record.get("header_stamp_nanosec")
        if sec in (None, "") or nanosec in (None, ""):
            record_invalid(
                stream,
                "header_stamp_missing",
                record_index,
                line_number,
                raw_sec=sec,
                raw_nanosec=nanosec,
            )
            return
        try:
            sec_int = int(sec)
            nanosec_int = int(nanosec)
        except (TypeError, ValueError):
            record_invalid(
                stream,
                "header_stamp_not_integer",
                record_index,
                line_number,
                raw_sec=sec,
                raw_nanosec=nanosec,
            )
            return
        if sec_int == 0 and nanosec_int == 0:
            record_invalid(stream, "header_stamp_zero", record_index, line_number)
            return
        if sec_int < 0 or nanosec_int < 0:
            record_invalid(
                stream,
                "header_stamp_negative",
                record_index,
                line_number,
                raw_sec=sec_int,
                raw_nanosec=nanosec_int,
            )
            return
        if nanosec_int >= 1_000_000_000:
            record_invalid(
                stream,
                "header_nanosec_out_of_range",
                record_index,
                line_number,
                raw_sec=sec_int,
                raw_nanosec=nanosec_int,
            )
            return
        timestamp = sec_int + nanosec_int / 1_000_000_000.0
        if not math.isfinite(timestamp):
            record_invalid(
                stream,
                "header_stamp_non_finite",
                record_index,
                line_number,
                raw_sec=sec_int,
                raw_nanosec=nanosec_int,
            )
            return
        stream.timestamps.append(timestamp)
        stream.timestamp_record_indices.append(record_index)

    if export_kind == "jsonl":
        with stream.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    record_invalid(stream, "empty_jsonl_line", None, line_number)
                    continue
                stream.raw_count += 1
                record_index = stream.raw_count
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record_invalid(stream, "invalid_json", record_index, line_number)
                    continue
                if not isinstance(record, dict):
                    record_invalid(stream, "json_record_not_object", record_index, line_number)
                    continue
                append_timestamp(record, record_index, line_number)
    elif export_kind == "compressed_binary_frames":
        with stream.path.open("r", encoding="utf-8", newline="") as handle:
            for record_index, record in enumerate(csv.DictReader(handle), start=1):
                stream.raw_count += 1
                append_timestamp(record, record_index, record_index + 1)
    else:
        record_invalid(stream, "unsupported_export_kind", None)

    topic_headtime = stream.timestamps[0] if stream.timestamps else None
    for index, (previous, current) in enumerate(zip(stream.timestamps, stream.timestamps[1:])):
        previous_record_index = stream.timestamp_record_indices[index]
        current_record_index = stream.timestamp_record_indices[index + 1]
        if current < previous:
            details = {
                "previous_record_index": previous_record_index,
                "previous_timestamp": previous,
                "current_timestamp": current,
                "delta_ms": (current - previous) * 1000.0,
            }
            if topic_headtime is not None:
                details.update({
                    "relative_previous_s": previous - topic_headtime,
                    "relative_current_s": current - topic_headtime,
                })
            record_invalid(stream, "timestamp_backward", current_record_index, **details)
        elif current == previous:
            details = {
                "previous_record_index": previous_record_index,
                "previous_timestamp": previous,
                "current_timestamp": current,
                "delta_ms": 0.0,
            }
            if topic_headtime is not None:
                details.update({
                    "relative_previous_s": previous - topic_headtime,
                    "relative_current_s": current - topic_headtime,
                })
            record_invalid(stream, "timestamp_duplicate", current_record_index, **details)
    if not stream.timestamps:
        increment(stream.invalid_counts, "no_usable_timestamps")
    annotate_invalid_event_context(stream)
    return stream


def annotate_invalid_event_context(stream: RawStream) -> None:
    valid_indices = stream.timestamp_record_indices
    if not valid_indices or not stream.timestamps:
        return
    topic_headtime = stream.timestamps[0]
    order_events = {"timestamp_backward", "timestamp_duplicate"}
    for event in stream.invalid_events:
        if event.get("type") in order_events:
            continue
        record_index = event.get("record_index")
        if not isinstance(record_index, int):
            continue
        position = bisect_left(valid_indices, record_index)
        if position > 0:
            previous_index = position - 1
            event["previous_valid_record_index"] = valid_indices[previous_index]
            event["previous_valid_timestamp"] = stream.timestamps[previous_index]
            event["relative_previous_valid_s"] = stream.timestamps[previous_index] - topic_headtime
        if position < len(valid_indices):
            event["next_valid_record_index"] = valid_indices[position]
            event["next_valid_timestamp"] = stream.timestamps[position]
            event["relative_next_valid_s"] = stream.timestamps[position] - topic_headtime


def load_topic_rows(root: Path) -> list[dict[str, str]]:
    topics_path = root / "topics.csv"
    if not topics_path.is_file():
        raise FileNotFoundError(f"topics.csv not found: {topics_path}")
    with topics_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"topic", "msgtype", "msgcount", "export_kind", "relative_output"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("topics.csv must contain topic,msgtype,msgcount,export_kind,relative_output")
    return rows


def load_streams(root: Path) -> list[RawStream]:
    return [load_stream(row, root) for row in load_topic_rows(root) if is_scored_row(row)]


def camera_priority(stream: RawStream) -> tuple[int, str]:
    topic = stream.topic.lower()
    if "head" in topic and ("rgb" in topic or "color" in topic):
        return 0, topic
    if ("front" in topic or "cam_h" in topic) and ("rgb" in topic or "color" in topic):
        return 1, topic
    if "rgb" in topic or "color" in topic:
        return 2, topic
    return 3, topic


def choose_reference(streams: list[RawStream], requested: str | None) -> RawStream:
    valid_streams = [stream for stream in streams if not stream.invalid_counts]
    if requested:
        for stream in streams:
            if stream.topic == requested:
                return stream
        raise ValueError(f"reference topic not found: {requested}")
    cameras = [stream for stream in valid_streams if stream.kind == "camera"]
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
    if target.invalid_counts or not target.timestamps:
        return {"topic": target.topic, "kind": target.kind, "score": 0.0,
                "status": "invalid_timestamps", "attribution": ['时间戳无效'],
                "invalid_counts": target.invalid_counts,
                "invalid_events": target.invalid_events}
    iou = window_iou(reference.timestamps, target.timestamps)
    ref_period = period_ms(reference.timestamps)
    target_period = period_ms(target.timestamps)
    if iou < args.iou_threshold or ref_period is None or target_period is None:
        return {"topic": target.topic, "kind": target.kind, "score": 0.0,
                "status": "window_iou_failed" if iou < args.iou_threshold else "period_unavailable",
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
    component_losses = {
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


INVALID_EVENT_LABELS = {
    "header_stamp_missing": "\u65e0 header \u65f6\u95f4\u6233",
    "header_stamp_not_integer": "header \u65f6\u95f4\u6233\u4e0d\u662f\u6574\u6570",
    "header_stamp_zero": "header \u65f6\u95f4\u6233\u4e3a 0",
    "header_stamp_negative": "header \u65f6\u95f4\u6233\u4e3a\u8d1f\u6570",
    "header_nanosec_out_of_range": "nanosec \u8d85\u51fa\u8303\u56f4",
    "header_stamp_non_finite": "header \u65f6\u95f4\u6233\u4e0d\u662f\u6709\u9650\u503c",
    "timestamp_backward": "\u65f6\u95f4\u6233\u5012\u9000",
    "timestamp_duplicate": "\u91cd\u590d\u91c7\u6837",
    "empty_jsonl_line": "JSONL \u7a7a\u884c",
    "invalid_json": "JSON \u89e3\u6790\u5931\u8d25",
    "json_record_not_object": "JSON \u8bb0\u5f55\u4e0d\u662f\u5bf9\u8c61",
    "timestamp_file_missing": "\u65f6\u95f4\u6233\u6587\u4ef6\u4e0d\u5b58\u5728",
    "unsupported_export_kind": "\u4e0d\u652f\u6301\u7684\u5bfc\u51fa\u7c7b\u578b",
    "no_usable_timestamps": "\u6ca1\u6709\u53ef\u7528\u65f6\u95f4\u6233",
}


def _format_invalid_event(event: dict[str, Any]) -> str:
    event_type = event.get("type", "unknown_invalid_timestamp")
    label = INVALID_EVENT_LABELS.get(event_type, event_type)
    record_index = event.get("record_index")
    line_number = event.get("line_number")
    location_parts = []
    if record_index is not None:
        location_parts.append(f"\u7b2c{record_index}\u6761\u8bb0\u5f55")
    if line_number is not None:
        location_parts.append(f"\u6587\u4ef6\u7b2c{line_number}\u884c")
    location = "\uff0c".join(location_parts) or "\u6587\u4ef6\u7ea7"

    if event_type == "timestamp_backward":
        previous_index = event.get("previous_record_index", "?")
        previous = event.get("previous_timestamp")
        current = event.get("current_timestamp")
        delta_ms = event.get("delta_ms")
        if "relative_previous_s" in event and "relative_current_s" in event:
            return (
                f"{location}\u4e0e\u7b2c{previous_index}\u6761\u8bb0\u5f55\u4e4b\u95f4"
                f"\uff08topic\u65f6\u95f4 {event['relative_previous_s']:.6f} s"
                f" -> {event['relative_current_s']:.6f} s\uff09"
                f"{label}\uff1a{previous:.6f} s -> {current:.6f} s"
                f"\uff0c\u56de\u9000 {abs(delta_ms):.3f} ms"
            )
        return (
            f"{location}\u4e0e\u7b2c{previous_index}\u6761\u8bb0\u5f55\u4e4b\u95f4{label}"
            f"\uff1a{previous:.6f} s -> {current:.6f} s\uff0c\u56de\u9000 {abs(delta_ms):.3f} ms"
        )

    if event_type == "timestamp_duplicate":
        timestamp = event.get("current_timestamp")
        if "relative_current_s" in event:
            return (
                f"{location}\u4e0e\u7b2c{event.get('previous_record_index', '?')}\u6761\u8bb0\u5f55\u4e4b\u95f4"
                f"\uff08topic\u65f6\u95f4 {event['relative_current_s']:.6f} s\uff09{label}"
                f"\uff1a{timestamp:.6f} s"
            )
        return (
            f"{location}\u4e0e\u7b2c{event.get('previous_record_index', '?')}\u6761\u8bb0\u5f55\u4e4b\u95f4"
            f"{label}\uff1a{timestamp:.6f} s"
        )

    return f"{location}{label}"


def _group_invalid_events(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        events,
        key=lambda event: (
            event.get("record_index") is None,
            event.get("record_index", 0),
            event.get("line_number", 0),
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    for event in ordered:
        if not groups:
            groups.append([event])
            continue
        previous = groups[-1][-1]
        same_type = event.get("type") == previous.get("type")
        current_index = event.get("record_index")
        previous_index = previous.get("record_index")
        consecutive = (
            isinstance(current_index, int)
            and isinstance(previous_index, int)
            and current_index == previous_index + 1
        )
        if same_type and consecutive:
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def _format_invalid_event_group(group: list[dict[str, Any]]) -> str:
    if len(group) == 1:
        return _format_invalid_event(group[0])

    first = group[0]
    last = group[-1]
    event_type = first.get("type", "unknown_invalid_timestamp")
    label = INVALID_EVENT_LABELS.get(event_type, event_type)
    first_index = first.get("record_index", "?")
    last_index = last.get("record_index", "?")
    line_start = first.get("line_number")
    line_end = last.get("line_number")
    line_text = (
        f"\uff0c\u6587\u4ef6\u7b2c{line_start}\u884c\u81f3\u7b2c{line_end}\u884c"
        if line_start is not None and line_end is not None
        else ""
    )

    relative_times = []
    for event in group:
        for key in ("relative_previous_s", "relative_current_s"):
            value = event.get(key)
            if isinstance(value, (int, float)):
                relative_times.append(float(value))
    for event in (first, last):
        for key in ("relative_previous_valid_s", "relative_next_valid_s"):
            value = event.get(key)
            if isinstance(value, (int, float)):
                relative_times.append(float(value))
    if relative_times:
        time_text = (
            f"\uff0c\u7ea6\u4f4d\u4e8e topic \u65f6\u95f4 "
            f"{min(relative_times):.6f} s \u81f3 {max(relative_times):.6f} s \u4e4b\u95f4"
        )
    else:
        time_text = "\uff0ctopic \u65f6\u95f4\u65e0\u6cd5\u8ba1\u7b97\uff08\u6ca1\u6709\u8db3\u591f\u7684\u6709\u6548\u65f6\u95f4\u6233\uff09"

    if event_type == "timestamp_backward":
        return (
            f"\u7b2c{first_index}\u6761\u81f3\u7b2c{last_index}\u6761\u8bb0\u5f55"
            f"{line_text}"
            f"\u5171\u53d1\u751f {len(group)} \u6b21{label}{time_text}"
        )
    if event_type == "timestamp_duplicate":
        return (
            f"\u7b2c{first_index}\u6761\u81f3\u7b2c{last_index}\u6761\u8bb0\u5f55"
            f"{line_text}"
            f"\u5171\u53d1\u751f {len(group)} \u6b21{label}{time_text}"
        )
    return (
        f"\u7b2c{first_index}\u6761\u81f3\u7b2c{last_index}\u6761\u8bb0\u5f55"
        f"{line_text}\u8fde\u7eed {len(group)} \u6761{label}{time_text}"
    )


def _format_invalid_event_summary(events: list[dict[str, Any]]) -> str:
    if not events:
        return ""
    groups = _group_invalid_events(events)
    if len(groups) <= 8:
        selected = groups
        suffix = ""
    else:
        selected = groups[:4] + groups[-4:]
        suffix = f"\uff1b\u5171 {len(events)} \u4e2a\u4e8b\u4ef6\uff0c\u4ec5\u5c55\u793a\u9996 4 \u7ec4\u548c\u672b 4 \u7ec4"
    return "\uff1b".join(_format_invalid_event_group(group) for group in selected) + suffix


def _topic_failure_reason(report: dict[str, Any]) -> str:
    status = report.get("status")
    if status == "invalid_timestamps":
        counts = report.get("invalid_counts", {})
        details = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        event_details = _format_invalid_event_summary(report.get("invalid_events", []))
        reason = f"\u65f6\u95f4\u6233\u65e0\u6548 ({details or 'no usable timestamp'})"
        return f"{reason}\uff1b\u5177\u4f53\u5f02\u5e38\uff1a{event_details}" if event_details else reason
    if status == "window_iou_failed":
        return f"\u65f6\u95f4\u7a97\u53e3 IoU={report.get('window_iou', 0.0):.3f} \u4f4e\u4e8e\u9608\u503c"
    if status == "period_unavailable":
        return "\u91c7\u6837\u5468\u671f\u4e0d\u53ef\u8ba1\u7b97"
    if status == "no_matches":
        return "\u5339\u914d\u7a97\u53e3\u5185\u6ca1\u6709\u53ef\u7528\u76ee\u6807\u5e27"
    if report.get("score") == 0:
        return "topic \u5206\u6570\u4e3a 0"
    return ""


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


def build_report(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    streams = load_streams(root)
    if not streams:
        raise ValueError("no scored JSONL topics found in topics.csv")

    try:
        reference = choose_reference(streams, args.reference)
    except ValueError as exc:
        failure_topics = []
        for stream in streams:
            if stream.invalid_counts or not stream.timestamps:
                failure_topics.append({
                    "topic": stream.topic,
                    "kind": stream.kind,
                    "score": 0.0,
                    "grade": "D",
                    "status": "invalid_timestamps",
                    "attribution": ["\u65f6\u95f4\u6233\u65e0\u6548"],
                    "reasons": [_topic_failure_reason({
                        "status": "invalid_timestamps",
                        "invalid_counts": stream.invalid_counts,
                        "invalid_events": stream.invalid_events,
                    })],
                    "invalid_counts": stream.invalid_counts,
                    "invalid_events": stream.invalid_events,
                })
        reasons = ["\u65e0\u53ef\u7528\u53c2\u8003 topic\uff0c\u5305\u7ea7\u8bc4\u5206\u4e3a 0", str(exc)]
        reasons.extend(f"topic {item['topic']}\uff1aD\uff08score=0\uff09\uff0c{item['reasons'][0]}" for item in failure_topics)
        return _emit_module(
            score=0.0, passed=False,
            sub_indicators=[{"name": "timestamp_validity", "weight": 1.0, "passed": False,
                             "score": 0.0, "gap": 100.0, "weighted_gap": 100.0, "details": [],
                             "grade": "D", "raw_value": "无可用参考 topic，所有流时间戳非法"}],
            reasons=reasons,
            threshold_profile={"reference_topic": None, "aggregate": "mean", "failure": "no_reference"},
        )

    reference_dt = stream_dt_metrics(reference.timestamps, args)
    reference_cv = reference_dt.get("dt_cv")
    reference_long_gap_ratio = reference_dt.get("long_gap_ratio")
    reference_hard_fail = (
        reference_cv is None
        or reference_cv >= 0.20
        or reference_long_gap_ratio >= 0.05
        or reference_dt.get("backward_count", 0) > 0
        or reference_dt.get("duplicate_count", 0) > 0
    )
    reference_warning = (
        (reference_cv is not None and reference_cv > 0.10)
        or reference_long_gap_ratio > 0.01
    )
    reference_detail = (
        f"参考流不稳定：dt_cv={reference_cv:.1%}，"
        f"long_gap_ratio={reference_long_gap_ratio:.1%}。"
        if reference_cv is not None else
        "参考流不稳定：无法计算有效采样周期。"
    )
    if reference_hard_fail:
        return _emit_module(
            score=0.0, passed=False,
            sub_indicators=[
                {"name": "reference_cadence", "weight": 1.0, "passed": False, "score": 0.0,
                 "gap": 100.0, "weighted_gap": 100.0, "details": [], "grade": "D",
                 "raw_value": reference_detail},
                {"name": "timestamp_validity", "weight": 0.0, "passed": True, "score": 100.0,
                 "gap": 0.0, "weighted_gap": 0.0, "details": [], "grade": "S",
                 "raw_value": "时间戳合法"},
            ],
            reasons=[
                f"reference topic 为 {reference.topic}。",
                reference_detail,
                "参考流 cadence 未通过硬门控，无法作为可靠对齐基准，包级评分为 0。",
            ],
            threshold_profile={"reference_topic": reference.topic,
                               "reference_cv": reference_cv,
                               "reference_long_gap": reference_long_gap_ratio,
                               "failure": "reference_hard_fail"},
        )

    topic_reports: list[dict[str, Any]] = []
    for stream in streams:
        if stream.topic == reference.topic:
            continue
        report = score_topic(reference, stream, args)
        _add_topic_grade(report)
        topic_reports.append(report)
    if not topic_reports:
        raise ValueError("at least one non-reference topic is required for bag scoring")

    invalid_streams = [stream for stream in streams if stream.invalid_counts or not stream.timestamps]
    failure_topics = []
    for report in topic_reports:
        failure_reason = _topic_failure_reason(report)
        if report.get("status") in {"invalid_timestamps", "window_iou_failed", "period_unavailable", "no_matches"}:
            failure_topics.append({
                "topic": report["topic"], "kind": report.get("kind"), "score": report.get("score"),
                "grade": report.get("grade", "D"), "status": report.get("status"),
                "attribution": report.get("attribution", []), "reasons": [failure_reason],
                "invalid_counts": report.get("invalid_counts", {}),
                "invalid_events": report.get("invalid_events", []),
                "window_iou": report.get("window_iou"),
            })

    if invalid_streams:
        reasons = [
            "\u5b58\u5728\u975e\u6cd5\u65f6\u95f4\u6233\uff0c\u5305\u7ea7\u8bc4\u5206\u4e3a 0\uff0c\u4ee5\u4e0b\u5217\u51fa\u89e6\u53d1\u5931\u8d25\u7684 topic\u3002",
            f"reference topic \u4e3a {reference.topic}\uff0c\u5176\u4f59\u6709\u6548 topic \u4ecd\u5df2\u5b8c\u6210\u72ec\u7acb\u8bc4\u4ef7\u3002",
        ]
        for item in failure_topics:
            reasons.append(f"topic {item['topic']}\uff1aD\uff08score=0\uff09\uff0c{item['reasons'][0]}")
        if not failure_topics:
            for stream in invalid_streams:
                details = ", ".join(f"{key}={value}" for key, value in sorted(stream.invalid_counts.items()))
                reasons.append(f"topic {stream.topic}\uff1aD\uff08score=0\uff09\uff0c\u65f6\u95f4\u6233\u65e0\u6548\uff08{details or 'no usable timestamp'}\uff09")
        invalid_subs = [{"name": "timestamp_validity", "weight": 1.0, "passed": False,
                         "score": 0.0, "gap": 100.0, "weighted_gap": 100.0, "details": [],
                         "grade": "D", "raw_value": "存在非法时间戳"}]
        for ft in failure_topics:
            invalid_subs.append({"name": ft["topic"], "weight": 0.0, "passed": False,
                                 "score": 0.0, "gap": 100.0, "weighted_gap": 0.0, "details": [],
                                 "grade": "D", "raw_value": (ft.get("reasons") or [""])[0]})
        return _emit_module(
            score=0.0, passed=False, sub_indicators=invalid_subs, reasons=reasons,
            threshold_profile={"reference_topic": reference.topic, "failure": "invalid_timestamps"},
        )

    raw_bag_score = sum(float(report.get("score", 0.0) or 0.0) for report in topic_reports) / len(topic_reports)
    bag_score = min(raw_bag_score, 90.0) if reference_warning else raw_bag_score
    passed = grade_from_score(bag_score)[1]
    problem_topics = [
        report for report in topic_reports
        if float(report.get("score", 0.0) or 0.0) <= 80.0
    ]
    reasons = [
        f"reference topic \u4e3a {reference.topic}\u3002",
        f"\u5171\u8bc4\u4ef7 {len(topic_reports)} \u4e2a topic\uff0c\u7b97\u672f\u5e73\u5747\u5206\u4e3a {bag_score:.3f}\u3002",
    ]
    if reference_warning:
        reasons.insert(1, reference_detail + "包级等级最高为 B。")
    if not problem_topics:
        reasons.append("所有topic的评分都在B及以上")
    for report in sorted(problem_topics, key=lambda item: float(item.get("score", 0.0) or 0.0)):
        attribution_text = "；".join(report.get("attribution", [])) or "未归因"
        report_reasons = report.get("reasons", [])
        topic_prefix = f"topic {report['topic']}：score="
        if any(str(reason).startswith(topic_prefix) for reason in report_reasons):
            reasons.append("；".join(str(reason) for reason in report_reasons))
        else:
            reasons.append(f"topic {report['topic']}：score={float(report.get('score', 0.0) or 0.0):.3f}，{attribution_text}")
    n_topics = len(topic_reports) or 1
    topic_weight = round(1.0 / n_topics, 4)
    topic_subs = [_topic_to_sub_indicator(r, topic_weight) for r in topic_reports]
    return _emit_module(
        score=bag_score, passed=passed, sub_indicators=topic_subs, reasons=reasons,
        threshold_profile={
            "reference_topic": reference.topic,
            "reference_warning": reference_warning,
            "aggregate": "mean(topic_scores)",
            "score_cap": 90.0 if reference_warning else None,
            "iou_threshold": args.iou_threshold,
        },
        threshold=90.0,
    )

def assess_from_ir(info, reader, weights: dict = None) -> dict:
    """从 IR DataInfo 评估时间戳对齐 (使用 IR 提取的时间戳)。"""
    try:
        ts_map = reader.require_timestamps(info, min_channels=2)
    except Exception as exc:
        from framework.readers.base import make_ir_report
        return make_ir_report("时间戳对齐质量", "check_timestamp", exc)

    from framework.readers.base import filter_real_timestamps, make_synthetic_report
    ts_map, has_synthetic = filter_real_timestamps(info, ts_map)
    if ts_map is None:
        return make_synthetic_report("时间戳对齐质量", "check_timestamp", info)

    from Quality_Assessment.shared.timing_scope import partition_timing_topics
    ts_map, excluded = partition_timing_topics(info, ts_map)

    def scoped(report):
        report['excluded_topics'] = excluded
        if excluded:
            report.setdefault('reasons', []).append(
                f"{len(excluded)} 个非采样 metadata/文本通道未参与评分，详见 excluded_topics。")
        report['timestamp_scope'] = {
            'included_topics': sorted(ts_map),
            'time_sources': {name: getattr(info.topics.get(name), 'time_source', 'unknown') for name in ts_map},
            'metadata_in_score': False,
        }
        report['method_version'] = 'timestamp_sampled_stream_scope_v2'
        return report

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
        reference=None,
    )

    streams = []
    for topic_name, ts_list in sorted(ts_map.items()):
        ti = info.topics.get(topic_name)
        kind = "camera" if (ti and ti.modality in {"image", "rgb"}) else "action"
        stream = RawStream(
            topic=topic_name,
            msgtype=ti.msg_type if ti else "",
            msgcount=len(ts_list),
            path=Path(ti.source) if ti else Path(""),
            kind=kind,
            timestamps=list(ts_list),
            timestamp_record_indices=list(range(len(ts_list))),
            raw_count=len(ts_list),
        )
        streams.append(stream)

    if len(streams) < 2:
        from framework.readers.base import make_ir_report
        from framework.ir.metadata import IRNotProvided
        exc = IRNotProvided(">=2 通道时间戳", info.format, info.capability_summary())
        return scoped(make_ir_report("时间戳对齐质量", "check_timestamp", exc))

    try:
        reference = choose_reference(streams, None)
    except ValueError:
        from framework.readers.base import make_ir_report
        from framework.ir.metadata import IRNotProvided
        exc = IRNotProvided("可用参考 topic (camera)", info.format, info.capability_summary())
        return scoped(make_ir_report("时间戳对齐质量", "check_timestamp", exc))

    topic_reports = []
    for stream in streams:
        if stream.topic == reference.topic:
            continue
        report = score_topic(reference, stream, args)
        _add_topic_grade(report)
        topic_reports.append(report)

    if not topic_reports:
        from framework.readers.base import make_ir_report
        from framework.ir.metadata import IRNotProvided
        exc = IRNotProvided("非参考 topic", info.format, info.capability_summary())
        return scoped(make_ir_report("时间戳对齐质量", "check_timestamp", exc))

    bag_score = sum(float(r.get("score", 0.0) or 0.0) for r in topic_reports) / len(topic_reports)
    passed = grade_from_score(bag_score)[1]
    n_topics = len(topic_reports) or 1
    topic_weight = round(1.0 / n_topics, 4)
    topic_subs = [_topic_to_sub_indicator(r, topic_weight) for r in topic_reports]
    reasons = [
        f"reference topic 为 {reference.topic}。",
        f"共评价 {len(topic_reports)} 个 topic，算术平均分为 {bag_score:.3f}。",
    ]
    for r in sorted(topic_reports, key=lambda x: float(x.get("score", 0.0) or 0.0)):
        if float(r.get("score", 0.0) or 0.0) <= 80.0:
            attr = "；".join(r.get("attribution", [])) or "未归因"
            reasons.append(f"topic {r['topic']}：score={float(r.get('score', 0.0) or 0.0):.3f}，{attr}")
    return scoped(_emit_module(
        score=bag_score, passed=passed, sub_indicators=topic_subs, reasons=reasons,
        threshold_profile={"reference_topic": reference.topic, "aggregate": "mean(topic_scores)"},
        threshold=90.0,
    ))


def main() -> int:
    args = parse_args()
    root = args.input_path.expanduser().resolve()

    # IR 路径自动探测
    from framework.ir.metadata import detect_format, FORMAT_UNKNOWN, FORMAT_JSON
    fmt = detect_format(str(root))
    if fmt != FORMAT_UNKNOWN and fmt != FORMAT_JSON and not (root / "topics.csv").is_file():
        from Quality_Assessment.config import open_info
        info, reader = open_info(str(root))
        report = assess_from_ir(info, reader)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # 旧路径: raw_export 目录
    if args.recursive:
        export_dirs = [root] if (root / "topics.csv").is_file() else sorted(
            path.parent for path in root.rglob("topics.csv")
        ) if root.is_dir() else []
        if not export_dirs:
            raise SystemExit(f"no exported bag directories with topics.csv found: {root}")
        if args.json_out:
            args.json_out.expanduser().mkdir(parents=True, exist_ok=True)
        failed = 0
        for export_dir in export_dirs:
            try:
                report = build_report(export_dir, args)
                relative = export_dir.relative_to(root) if export_dir != root else Path(export_dir.name)
                filename = "__".join(relative.parts) + ".json"
                if args.json_out:
                    (args.json_out.expanduser() / filename).write_text(
                        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                print(json.dumps({"input_path": str(export_dir), **report}, ensure_ascii=False))
            except Exception as exc:
                failed += 1
                print(f"FAILED\t{export_dir}\t{exc}", file=sys.stderr)
        return 1 if failed else 0

    if not root.is_dir():
        raise SystemExit(f"raw_export directory not found: {root}")
    report = build_report(root, args)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    output_report = report
    text = json.dumps(output_report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        full_text = json.dumps(report, ensure_ascii=False, indent=2)
        args.json_out.expanduser().write_text(full_text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
