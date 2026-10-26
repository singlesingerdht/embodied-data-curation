#!/usr/bin/env python3
"""Standalone frame-level camera timestamp diagnostics for raw_export."""

from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from statistics import median



WINDOW_FACTOR = 0.5
MIN_WINDOW_MS = 8.0
MISSING_RATIO = 0.10
OFFSET_DRIFT_MS = 3.0
INDEX_P95_ABS_DEV_MS = 2.5
INDEX_DRIFT_MS = 2.0
CLOCK_DRIFT_MS = 5.0
TREND_RESIDUAL_P95_MS = 2.0


@dataclass
class CameraStream:
    name: str
    topic: str
    timestamps: list[float]
    zero_count: int = 0
    missing_count: int = 0


def percentile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return values[low] if low == high else values[low] * (high - index) + values[high] * (index - low)


def period_ms(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return median([b - a for a, b in zip(values, values[1:])]) * 1000.0


def is_camera(row: dict[str, str]) -> bool:
    topic = row.get("topic", "").lower()
    msgtype = row.get("msgtype", "").lower()
    return (
        "compressedimage" in msgtype
        or msgtype.endswith("/image")
        or "/image" in topic
    )


def stream_name(topic: str) -> str:
    return "sensors/" + "_".join(part for part in topic.strip("/").split("/") if part)


def read_timestamps(path: Path, export_kind: str) -> tuple[list[float], int, int]:
    timestamps: list[float] = []
    zero_count = missing_count = 0
    if export_kind == "compressed_binary_frames":
        with path.open("r", encoding="utf-8", newline="") as handle:
            records = csv.DictReader(handle)
            for record in records:
                sec, nsec = record.get("header_stamp_sec"), record.get("header_stamp_nanosec")
                if sec in (None, "") or nsec in (None, ""):
                    missing_count += 1
                elif int(sec) == 0 and int(nsec) == 0:
                    zero_count += 1
                else:
                    timestamps.append(int(sec) + int(nsec) / 1_000_000_000.0)
        return timestamps, zero_count, missing_count
    if export_kind != "jsonl":
        raise ValueError(f"unsupported export_kind: {export_kind}")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            sec, nsec = record.get("header_stamp_sec"), record.get("header_stamp_nanosec")
            if sec is None or nsec is None:
                stamp = record.get("message", {}).get("header", {}).get("stamp", {})
                sec, nsec = stamp.get("sec"), stamp.get("nanosec")
            if sec in (None, "") or nsec in (None, ""):
                missing_count += 1
            elif int(sec) == 0 and int(nsec) == 0:
                zero_count += 1
            else:
                timestamps.append(int(sec) + int(nsec) / 1_000_000_000.0)
    return timestamps, zero_count, missing_count


def nearest(reference: list[float], target: list[float], window_ms: float) -> tuple[float, float, float, float, float]:
    start, end = max(reference[0], target[0]), min(reference[-1], target[-1])
    refs = [ts for ts in reference if start <= ts <= end]
    if not refs:
        raise ValueError("overlap=0")
    errors: list[float] = []
    for ts in refs:
        index = bisect_left(target, ts)
        candidates = []
        if index < len(target):
            candidates.append((target[index] - ts) * 1000.0)
        if index > 0:
            candidates.append((target[index - 1] - ts) * 1000.0)
        errors.append(abs(min(candidates, key=abs)))
    errors.sort()
    missing = sum(value > window_ms for value in errors) / len(errors)
    return median(errors), percentile(errors, 0.95), percentile(errors, 0.99), errors[-1], missing


def trend_residual_p95(values: list[float]) -> float:
    count = len(values)
    if count <= 1:
        return 0.0
    mean_x, mean_y = (count - 1) / 2.0, sum(values) / count
    sxx = sum((index - mean_x) ** 2 for index in range(count))
    slope = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values)) / sxx
    residuals = sorted(abs(value - (mean_y + slope * (index - mean_x))) for index, value in enumerate(values))
    return percentile(residuals, 0.95)


def diagnose(reference: CameraStream, target: CameraStream, window_ms: float) -> dict[str, object]:
    ref, ts = reference.timestamps, target.timestamps
    if any(b <= a for a, b in zip(ts, ts[1:])):
        return {"timestamp_valid": False}
    try:
        nearest_median, nearest_p95, nearest_p99, nearest_max, missing = nearest(ref, ts, window_ms)
    except ValueError:
        return {"timestamp_valid": True, "comparable": False, "window_overlap_s": 0.0}
    start_offset = (ts[0] - ref[0]) * 1000.0
    end_offset = (ts[-1] - ref[-1]) * 1000.0
    drift = end_offset - start_offset
    target_period = period_ms(ts) or MIN_WINDOW_MS
    offset_no_penalty = max(10.0, 0.35 * target_period)
    pair_count = min(len(ref), len(ts))
    delays = [(ts[index] - ref[index]) * 1000.0 for index in range(pair_count)]
    delay_median = median(delays)
    delay_p95 = percentile(sorted(abs(value - delay_median) for value in delays), 0.95)
    delay_drift = delays[-1] - delays[0]
    count_diff = len(ts) - len(ref)
    comparable = missing <= MISSING_RATIO
    residual_jitter = percentile(sorted(abs(value - median(delays)) for value in delays), 0.95)
    return {
        "timestamp_valid": True,
        "count": len(ts),
        "comparable": comparable,
        "source_match_coverage": 1.0 - missing,
        "nearest_median_ms": nearest_median,
        "nearest_p95_ms": nearest_p95,
        "nearest_p99_ms": nearest_p99,
        "nearest_max_ms": nearest_max,
        "missing_match_ratio": missing,
        "start_offset_ms": start_offset,
        "end_offset_ms": end_offset,
        "signed_offset_ms": delay_median,
        "offset_drift_ms": drift,
        "drift_span_ms": drift,
        "frame_count_diff": count_diff,
        "residual_jitter_ms": residual_jitter,
        "window_iou": (max(0.0, min(reference.timestamps[-1], target.timestamps[-1]) - max(reference.timestamps[0], target.timestamps[0])) /
                       (reference.timestamps[-1] - reference.timestamps[0] + target.timestamps[-1] - target.timestamps[0] -
                        max(0.0, min(reference.timestamps[-1], target.timestamps[-1]) - max(reference.timestamps[0], target.timestamps[0])))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone camera timestamp diagnostics for raw_export.")
    parser.add_argument("export")
    parser.add_argument("--reference")
    parser.add_argument("--json-out", type=Path, help="Write numeric camera diagnostics to JSON.")
    args = parser.parse_args()
    export_dir = Path(args.export)
    with (export_dir / "topics.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    cameras: list[CameraStream] = []
    for row in rows:
        if not is_camera(row) or int(row.get("msgcount", "0") or 0) <= 1:
            continue
        path = export_dir / Path(PurePosixPath(row["relative_output"].replace("\\", "/")))
        timestamps, zero_count, missing_count = read_timestamps(path, row.get("export_kind", ""))
        if timestamps:
            cameras.append(CameraStream(stream_name(row["topic"]), row["topic"], timestamps, zero_count, missing_count))
    if not cameras:
        raise SystemExit("No camera stream with usable non-zero header timestamps found.")
    reference = next((item for item in cameras if args.reference in (item.name, item.topic)), cameras[0])
    ref_period = period_ms(reference.timestamps) or MIN_WINDOW_MS
    print("timestamp source: non-zero header")
    print(f"reference: {reference.name}")
    payload: dict[str, object] = {"reference": reference.topic, "topics": {}}
    for camera in cameras:
        print(f"\n[{camera.name}]")
        print(f"raw topic                   : {camera.topic}")
        if camera is reference:
            print(f"count                       : {len(camera.timestamps)}")
            print("is_reference                : true")
            payload["topics"][camera.topic] = {"count": len(camera.timestamps), "is_reference": True}
            continue
        target_period = period_ms(camera.timestamps) or MIN_WINDOW_MS
        window_ms = max(MIN_WINDOW_MS, WINDOW_FACTOR * max(ref_period, target_period))
        metrics = diagnose(reference, camera, window_ms)
        payload["topics"][camera.topic] = {"count": len(camera.timestamps), "is_reference": False, **metrics}
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"{key:28}: {value:.6f}")
            else:
                print(f"{key:28}: {str(value).lower() if isinstance(value, bool) else value}")
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
