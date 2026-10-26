#!/usr/bin/env python3
"""Compute timestamp-delta CV for each topic in a raw_export directory."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Iterable

import check_timestamps as crt


@dataclass
class TopicCvReport:
    name: str
    stream_kind: str
    path: str
    timestamp_source: str
    raw_count: int | None
    usable_count: int
    zero_header_count: int | None
    missing_header_count: int | None
    backward_count: int
    duplicate_count: int
    dt_count: int
    dt_mean_ms: float | None
    dt_median_ms: float | None
    dt_std_ms: float | None
    dt_cv: float | None
    dt_cv_pct: float | None
    dt_min_ms: float | None
    dt_max_ms: float | None
    actual_rate_hz: float | None
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute dt CV for each topic in a raw_export directory."
    )
    parser.add_argument(
        "input_path",
        help="raw_export directory path.",
    )
    parser.add_argument(
        "--match",
        default=None,
        help="Only include topics whose name contains this substring.",
    )
    parser.add_argument(
        "--sort-by",
        choices=("name", "cv", "count"),
        default="name",
        help="Sort output by topic name, CV descending, or usable count descending.",
    )
    parser.add_argument(
        "--show-empty",
        action="store_true",
        help="Also print topics with fewer than two usable timestamps.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Write the result to a JSON file.",
    )
    parser.add_argument(
        "--jsonl-out",
        default=None,
        help="Append one input-directory CV result as one JSONL record.",
    )
    return parser.parse_args()


def iter_raw_export_reports(root: Path) -> Iterable[TopicCvReport]:
    topics_table = crt.load_topics_table(root)
    for topic_name in sorted(topics_table):
        row = topics_table[topic_name]
        path = crt.row_path(root, row)
        export_kind = row.get("export_kind", "").strip()
        timestamp_source = "non_zero_header"
        stream_kind = "raw_topic"
        if not path.is_file():
            yield TopicCvReport(
                name=topic_name,
                stream_kind=stream_kind,
                path=str(path),
                timestamp_source=timestamp_source,
                raw_count=None,
                usable_count=0,
                zero_header_count=None,
                missing_header_count=None,
                backward_count=0,
                duplicate_count=0,
                dt_count=0,
                dt_mean_ms=None,
                dt_median_ms=None,
                dt_std_ms=None,
                dt_cv=None,
                dt_cv_pct=None,
                dt_min_ms=None,
                dt_max_ms=None,
                actual_rate_hz=None,
                note="topic file not found",
            )
            continue

        try:
            load = crt.read_spec_timestamps(path, export_kind)
        except ValueError as exc:
            yield TopicCvReport(
                name=topic_name,
                stream_kind=stream_kind,
                path=str(path),
                timestamp_source=timestamp_source,
                raw_count=None,
                usable_count=0,
                zero_header_count=None,
                missing_header_count=None,
                backward_count=0,
                duplicate_count=0,
                dt_count=0,
                dt_mean_ms=None,
                dt_median_ms=None,
                dt_std_ms=None,
                dt_cv=None,
                dt_cv_pct=None,
                dt_min_ms=None,
                dt_max_ms=None,
                actual_rate_hz=None,
                note=str(exc),
            )
            continue

        yield build_report(
            name=topic_name,
            stream_kind=stream_kind,
            path=path,
            timestamps=load.timestamps,
            timestamp_source=timestamp_source,
            raw_count=load.total_count,
            zero_header_count=load.zero_header_count,
            missing_header_count=load.missing_header_count,
        )


def build_report(
    *,
    name: str,
    stream_kind: str,
    path: Path,
    timestamps: list[float],
    timestamp_source: str,
    raw_count: int | None,
    zero_header_count: int | None,
    missing_header_count: int | None,
) -> TopicCvReport:
    if not timestamps:
        return TopicCvReport(
            name=name,
            stream_kind=stream_kind,
            path=str(path),
            timestamp_source=timestamp_source,
            raw_count=raw_count,
            usable_count=0,
            zero_header_count=zero_header_count,
            missing_header_count=missing_header_count,
            backward_count=0,
            duplicate_count=0,
            dt_count=0,
            dt_mean_ms=None,
            dt_median_ms=None,
            dt_std_ms=None,
            dt_cv=None,
            dt_cv_pct=None,
            dt_min_ms=None,
            dt_max_ms=None,
            actual_rate_hz=None,
            note=empty_note(raw_count, zero_header_count, missing_header_count),
        )

    if len(timestamps) == 1:
        return TopicCvReport(
            name=name,
            stream_kind=stream_kind,
            path=str(path),
            timestamp_source=timestamp_source,
            raw_count=raw_count,
            usable_count=1,
            zero_header_count=zero_header_count,
            missing_header_count=missing_header_count,
            backward_count=0,
            duplicate_count=0,
            dt_count=0,
            dt_mean_ms=None,
            dt_median_ms=None,
            dt_std_ms=None,
            dt_cv=None,
            dt_cv_pct=None,
            dt_min_ms=None,
            dt_max_ms=None,
            actual_rate_hz=None,
            note="only one usable timestamp",
        )

    deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]
    backward_count = sum(1 for delta in deltas if delta < 0)
    duplicate_count = sum(1 for delta in deltas if delta == 0)

    if any(not math.isfinite(delta) for delta in deltas):
        note = "non-finite dt exists; CV not computed"
        return TopicCvReport(
            name=name,
            stream_kind=stream_kind,
            path=str(path),
            timestamp_source=timestamp_source,
            raw_count=raw_count,
            usable_count=len(timestamps),
            zero_header_count=zero_header_count,
            missing_header_count=missing_header_count,
            backward_count=backward_count,
            duplicate_count=duplicate_count,
            dt_count=len(deltas),
            dt_mean_ms=None,
            dt_median_ms=None,
            dt_std_ms=None,
            dt_cv=None,
            dt_cv_pct=None,
            dt_min_ms=None,
            dt_max_ms=None,
            actual_rate_hz=None,
            note=note,
        )

    if backward_count or duplicate_count:
        note_parts: list[str] = []
        if backward_count:
            note_parts.append(f"backward dt count={backward_count}")
        if duplicate_count:
            note_parts.append(f"duplicate dt count={duplicate_count}")
        note_parts.append("CV not computed because non-positive dt exists")
        note = "; ".join(note_parts)
        return TopicCvReport(
            name=name,
            stream_kind=stream_kind,
            path=str(path),
            timestamp_source=timestamp_source,
            raw_count=raw_count,
            usable_count=len(timestamps),
            zero_header_count=zero_header_count,
            missing_header_count=missing_header_count,
            backward_count=backward_count,
            duplicate_count=duplicate_count,
            dt_count=len(deltas),
            dt_mean_ms=None,
            dt_median_ms=None,
            dt_std_ms=None,
            dt_cv=None,
            dt_cv_pct=None,
            dt_min_ms=None,
            dt_max_ms=None,
            actual_rate_hz=None,
            note=note,
        )

    dt_mean = mean(deltas)
    dt_median = median(deltas)
    dt_std = pstdev(deltas)
    dt_cv = None if dt_mean <= 0 else dt_std / dt_mean
    actual_rate_hz = None if dt_mean <= 0 else 1.0 / dt_mean

    note_parts = []
    if raw_count is not None:
        if zero_header_count:
            note_parts.append(f"zero header skipped={zero_header_count}")
        if missing_header_count:
            note_parts.append(f"missing header skipped={missing_header_count}")
    note = "; ".join(note_parts) if note_parts else "ok"

    return TopicCvReport(
        name=name,
        stream_kind=stream_kind,
        path=str(path),
        timestamp_source=timestamp_source,
        raw_count=raw_count,
        usable_count=len(timestamps),
        zero_header_count=zero_header_count,
        missing_header_count=missing_header_count,
        backward_count=backward_count,
        duplicate_count=duplicate_count,
        dt_count=len(deltas),
        dt_mean_ms=dt_mean * 1000.0,
        dt_median_ms=dt_median * 1000.0,
        dt_std_ms=dt_std * 1000.0,
        dt_cv=dt_cv,
        dt_cv_pct=None if dt_cv is None else dt_cv * 100.0,
        dt_min_ms=min(deltas) * 1000.0,
        dt_max_ms=max(deltas) * 1000.0,
        actual_rate_hz=actual_rate_hz,
        note=note,
    )


def empty_note(
    raw_count: int | None,
    zero_header_count: int | None,
    missing_header_count: int | None,
) -> str:
    if raw_count is None:
        return "empty stream"
    parts = ["no usable timestamps"]
    if zero_header_count:
        parts.append(f"zero header skipped={zero_header_count}")
    if missing_header_count:
        parts.append(f"missing header skipped={missing_header_count}")
    return "; ".join(parts)


def format_float(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def sort_reports(reports: list[TopicCvReport], sort_by: str) -> list[TopicCvReport]:
    if sort_by == "cv":
        return sorted(
            reports,
            key=lambda report: (
                report.dt_cv is None,
                -(report.dt_cv if report.dt_cv is not None else -1.0),
                report.name,
            ),
        )
    if sort_by == "count":
        return sorted(reports, key=lambda report: (-report.usable_count, report.name))
    return sorted(reports, key=lambda report: report.name)


def report_to_dict(report: TopicCvReport) -> dict[str, object]:
    payload = asdict(report)
    payload.pop("note", None)
    return payload


def print_report(report: TopicCvReport) -> None:
    print(f"[{report.name}]")
    print(f"  stream kind              : {report.stream_kind}")
    print(f"  timestamp source         : {report.timestamp_source}")
    print(f"  usable count             : {report.usable_count}")
    if report.raw_count is not None:
        print(f"  raw count                : {report.raw_count}")
        print(f"  zero header skipped      : {report.zero_header_count}")
        print(f"  missing header skipped   : {report.missing_header_count}")
    print(f"  dt count                 : {report.dt_count}")
    print(f"  backward dt count        : {report.backward_count}")
    print(f"  duplicate dt count       : {report.duplicate_count}")
    print(f"  dt mean ms               : {format_float(report.dt_mean_ms)}")
    print(f"  dt median ms             : {format_float(report.dt_median_ms)}")
    print(f"  dt std ms                : {format_float(report.dt_std_ms)}")
    print(f"  dt cv                    : {format_float(report.dt_cv, 6)}")
    print(f"  dt cv pct                : {format_float(report.dt_cv_pct)}%")
    print(f"  dt min ms                : {format_float(report.dt_min_ms)}")
    print(f"  dt max ms                : {format_float(report.dt_max_ms)}")
    print(f"  actual rate hz           : {format_float(report.actual_rate_hz, 6)}")
    print()


def main() -> None:
    args = parse_args()
    root = Path(args.input_path).expanduser()
    if not root.is_dir():
        raise SystemExit(f"Input path is not a directory: {root}")

    if not crt.is_raw_export_dir(root):
        raise SystemExit(f"Input is not a raw_export directory with topics.csv: {root}")
    source = "raw_export"
    reports = list(iter_raw_export_reports(root))

    if args.match:
        reports = [report for report in reports if args.match in report.name]

    if not args.show_empty:
        reports = [report for report in reports if report.usable_count >= 2]

    reports = sort_reports(reports, args.sort_by)

    print(f"input path                 : {root}")
    print(f"source                     : {source}")
    print("cv formula                 : population std(dt) / mean(dt)")
    print()

    for report in reports:
        print_report(report)

    payload = {
        "input_path": str(root),
        "source": source,
        "generated_by": "check_dt.py",
        "reports": [report_to_dict(report) for report in reports],
    }

    if args.json_out:
        Path(args.json_out).expanduser().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.jsonl_out:
        output_path = Path(args.jsonl_out).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")


if __name__ == "__main__":
    main()
