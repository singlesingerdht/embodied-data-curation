#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


STANDARD_COLUMNS = [
    "topic",
    "msgtype",
    "msgcount",
    "export_kind",
    "relative_output",
]


@dataclass(frozen=True)
class EpisodeTopic:
    topic: str
    kind: str
    source_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize one episode's timestamp files in place for run_check.py."
    )
    parser.add_argument("episode_path", type=Path, help="Episode directory containing topics.episode.csv.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate the timestamps/ directory before conversion.",
    )
    return parser.parse_args()


def load_episode_topics(episode_dir: Path) -> list[EpisodeTopic]:
    topics_path = episode_dir / "topics.episode.csv"
    if not topics_path.is_file():
        topics_path = episode_dir / "topics.csv"
    if not topics_path.is_file():
        raise FileNotFoundError(f"topics.csv not found: {topics_path}")

    with topics_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"topic", "kind", "relative_output"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("episode topics.csv must contain topic,kind,relative_output")

    topics: list[EpisodeTopic] = []
    for row in rows:
        topic = row.get("topic", "").strip()
        kind = row.get("kind", "").strip().lower()
        relative_output = row.get("relative_output", "").strip().replace("\\", "/")
        if not topic or kind not in {"camera", "action"} or not relative_output:
            continue
        topics.append(
            EpisodeTopic(
                topic=topic,
                kind=kind,
                source_path=episode_dir / Path(PurePosixPath(relative_output)),
            )
        )
    if not topics:
        raise ValueError("no camera or action topics found in episode topics.csv")
    return topics


def timestamp_to_header(timestamp: Any) -> dict[str, int] | None:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None

    sec = math.floor(value)
    nanosec = int(round((value - sec) * 1_000_000_000))
    if nanosec >= 1_000_000_000:
        sec += 1
        nanosec -= 1_000_000_000
    return {"header_stamp_sec": sec, "header_stamp_nanosec": nanosec}


def normalize_record(record: dict[str, Any], source_line: int) -> dict[str, Any]:
    sec = record.get("header_stamp_sec")
    nanosec = record.get("header_stamp_nanosec")
    if sec not in (None, "") and nanosec not in (None, ""):
        return {
            "header_stamp_sec": sec,
            "header_stamp_nanosec": nanosec,
            "source_line": source_line,
        }

    for key in ("timestamp", "timestamp_seconds"):
        if key in record:
            header = timestamp_to_header(record[key])
            if header is not None:
                return {**header, "source_line": source_line}

    # Keep a record without header fields so run_check reports the source issue.
    return {"source_line": source_line}


def output_relative_path(topic: EpisodeTopic) -> Path:
    cleaned = [part for part in topic.topic.strip("/").split("/") if part]
    return Path("timestamps", *cleaned).with_suffix(".jsonl")


def msgtype_for_kind(kind: str) -> str:
    if kind == "camera":
        return "sensor_msgs/msg/CompressedImage"
    return "episode_msgs/msg/Action"


def convert_topic(topic: EpisodeTopic, output_dir: Path) -> tuple[dict[str, str], dict[str, int]]:
    if not topic.source_path.is_file():
        raise FileNotFoundError(f"timestamp source not found for {topic.topic}: {topic.source_path}")

    relative_output = output_relative_path(topic)
    output_path = output_dir / relative_output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_count = 0
    header_count = 0
    missing_or_invalid_count = 0
    with topic.source_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as target:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                normalized = {"source_line": line_number}
            else:
                source_count += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    normalized = {"source_line": line_number}
                else:
                    normalized = normalize_record(record, line_number)
            if "header_stamp_sec" in normalized and "header_stamp_nanosec" in normalized:
                header_count += 1
            else:
                missing_or_invalid_count += 1
            target.write(json.dumps(normalized, ensure_ascii=False) + "\n")

    return (
        {
            "topic": topic.topic,
            "msgtype": msgtype_for_kind(topic.kind),
            "msgcount": str(source_count),
            "export_kind": "jsonl",
            "relative_output": relative_output.as_posix(),
        },
        {
            "source_count": source_count,
            "header_count": header_count,
            "missing_or_invalid_count": missing_or_invalid_count,
        },
    )


def main() -> int:
    args = parse_args()
    episode_dir = args.episode_path.expanduser().resolve()
    if not episode_dir.is_dir():
        raise SystemExit(f"episode directory not found: {episode_dir}")
    topics = load_episode_topics(episode_dir)
    output_dir = episode_dir
    if args.overwrite:
        timestamp_dir = output_dir / "timestamps"
        if timestamp_dir.exists():
            shutil.rmtree(timestamp_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    try:
        for topic in topics:
            row, _stats = convert_topic(topic, output_dir)
            rows.append(row)
        with (output_dir / "topics.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=STANDARD_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

    print(output_dir)
    print(f"Converted {len(rows)} topics. Run:")
    print(f"  python run_check.py {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
