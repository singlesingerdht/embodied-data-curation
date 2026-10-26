from __future__ import annotations

import argparse
import base64
import csv
import dataclasses
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = Path.cwd()
DEFAULT_ROSBAGS_WHEEL = SCRIPT_DIR / "rosbags-0.11.3-py3-none-any.whl"
_WHEEL_TEMP_DIR: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export one ROS1 bag, or recursively export all ROS1 bags in a directory."
    )
    parser.add_argument("bag", type=Path, help="Path to the ROS1 .bag file")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Treat bag as a directory and recursively export every .bag file inside it.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Root directory for final outputs (default: {DEFAULT_OUTPUT_ROOT})",
    )
    parser.add_argument(
        "--rosbags-wheel",
        type=Path,
        default=DEFAULT_ROSBAGS_WHEEL,
        help=f"Path to the downloaded rosbags wheel (default: {DEFAULT_ROSBAGS_WHEEL})",
    )
    return parser.parse_args()


def ensure_rosbags(wheel_path: Path) -> None:
    global _WHEEL_TEMP_DIR
    if not wheel_path.exists():
        raise FileNotFoundError(
            f"rosbags wheel not found: {wheel_path}. "
            "Place the wheel next to this script or pass --rosbags-wheel <path-to-wheel>."
        )
    if wheel_path.suffix.lower() == ".whl":
        _WHEEL_TEMP_DIR = tempfile.mkdtemp(prefix="rosbags_")
        with zipfile.ZipFile(wheel_path) as archive:
            archive.extractall(_WHEEL_TEMP_DIR)
        sys.path.insert(0, _WHEEL_TEMP_DIR)
    else:
        sys.path.insert(0, str(wheel_path))


def topic_to_relpath(topic: str) -> Path:
    clean = topic.lstrip("/")
    return Path(*[sanitize_path_part(part) for part in clean.split("/") if part])


def sanitize_path_part(value: str) -> str:
    value = value.strip().replace(" ", "_")
    value = re.sub(r'[<>:"/\\|?*]', "_", value)
    return value or "_"


def header_stamp_parts(message: Any) -> tuple[int | None, int | None]:
    header = getattr(message, "header", None)
    stamp = getattr(header, "stamp", None) if header is not None else None
    if stamp is None:
        return None, None
    return int(getattr(stamp, "sec", 0)), int(getattr(stamp, "nanosec", 0))


def has_header_stamp(message: Any) -> bool:
    """Return whether the decoded message exposes a top-level header.stamp."""
    header = getattr(message, "header", None)
    return header is not None and getattr(header, "stamp", None) is not None


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, np.ndarray):
        return value.tolist()

    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {
            "__encoding__": "base64",
            "size": len(raw),
            "data": base64.b64encode(raw).decode("ascii"),
        }

    if dataclasses.is_dataclass(value):
        result: dict[str, Any] = {}
        for field in dataclasses.fields(value):
            result[field.name] = to_jsonable(getattr(value, field.name))
        return result

    if isinstance(value, dict):
        return {str(key): to_jsonable(val) for key, val in value.items()}

    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]

    if hasattr(value, "__slots__"):
        result = {}
        for name in value.__slots__:
            if name.startswith("_"):
                continue
            result[name] = to_jsonable(getattr(value, name))
        return result

    return repr(value)


def choose_binary_extension(fmt: str) -> str:
    lower = fmt.lower()
    if "compresseddepth" in lower:
        return ".bin"
    if "jpeg" in lower or "jpg" in lower:
        return ".jpg"
    if "png" in lower:
        return ".png"
    return ".bin"


def export_bag(bag_path: Path, output_root: Path) -> Path:
    from rosbags.rosbag1 import Reader
    from rosbags.typesys import Stores, get_typestore, get_types_from_msg

    if not bag_path.exists():
        raise FileNotFoundError(f"bag file not found: {bag_path}")

    export_root = output_root / sanitize_path_part(bag_path.stem)
    export_root.mkdir(parents=True, exist_ok=True)

    typestore = get_typestore(Stores.ROS1_NOETIC)
    summary_rows: list[dict[str, Any]] = []
    jsonl_files: dict[str, Any] = {}
    image_counters: dict[str, int] = {}
    header_seen: dict[str, bool] = {}

    with Reader(bag_path) as reader:
        for connection in reader.connections:
            typestore.register(get_types_from_msg(connection.msgdef.data, connection.msgtype))

        with (export_root / "topics.csv").open("w", newline="", encoding="utf-8") as summary_file:
            summary_writer = csv.DictWriter(
                summary_file,
                fieldnames=["topic", "msgtype", "msgcount", "export_kind", "relative_output"],
            )
            summary_writer.writeheader()

            connections = sorted(reader.connections, key=lambda conn: conn.topic)
            for connection in connections:
                header_seen[connection.topic] = False
                relpath = topic_to_relpath(connection.topic)
                if connection.msgtype == "sensor_msgs/msg/CompressedImage":
                    out_dir = export_root / relpath
                    out_dir.mkdir(parents=True, exist_ok=True)
                    json_path = out_dir / "timestamps.jsonl"
                    jsonl_files[connection.topic] = json_path.open("w", encoding="utf-8")
                    image_counters[connection.topic] = 0
                    export_kind = "jsonl"
                    relative_output = str(relpath / "timestamps.jsonl")
                else:
                    json_path = export_root / relpath.with_suffix(".jsonl")
                    json_path.parent.mkdir(parents=True, exist_ok=True)
                    json_file = json_path.open("w", encoding="utf-8")
                    jsonl_files[connection.topic] = json_file
                    export_kind = "jsonl"
                    relative_output = str(relpath.with_suffix(".jsonl"))

                row = {
                    "topic": connection.topic,
                    "msgtype": connection.msgtype,
                    "msgcount": connection.msgcount,
                    "export_kind": export_kind,
                    "relative_output": relative_output,
                }
                summary_rows.append(row)
                summary_writer.writerow(row)

        try:
            for connection, timestamp_ns, rawdata in reader.messages():
                msg = typestore.deserialize_ros1(rawdata, connection.msgtype)
                header_seen[connection.topic] = header_seen[connection.topic] or has_header_stamp(msg)
                if connection.msgtype == "sensor_msgs/msg/CompressedImage":
                    out_dir = export_root / topic_to_relpath(connection.topic)
                    counter = image_counters[connection.topic]
                    image_counters[connection.topic] += 1
                    fmt = getattr(msg, "format", "")
                    ext = choose_binary_extension(fmt)
                    filename = f"{counter:06d}{ext}"
                    frame_path = out_dir / filename
                    payload = bytes(getattr(msg, "data"))
                    frame_path.write_bytes(payload)
                    sec, nanosec = header_stamp_parts(msg)
                    jsonl_files[connection.topic].write(
                        json.dumps(
                            {
                                "bag_timestamp_ns": int(timestamp_ns),
                                "header_stamp_sec": sec,
                                "header_stamp_nanosec": nanosec,
                                "format": fmt,
                                "relative_file": filename,
                                "size_bytes": len(payload),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    continue

                sec, nanosec = header_stamp_parts(msg)
                record = {
                    "topic": connection.topic,
                    "msgtype": connection.msgtype,
                    "bag_timestamp_ns": int(timestamp_ns),
                    "header_stamp_sec": sec,
                    "header_stamp_nanosec": nanosec,
                    "message": to_jsonable(msg),
                }
                jsonl_files[connection.topic].write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            for handle in jsonl_files.values():
                handle.close()

    for row in summary_rows:
        has_header = header_seen.get(row["topic"], False)
        row["has_header"] = str(has_header).lower()
        row["header_path"] = "header.stamp" if has_header else ""
        row["timestamp_source"] = "header_stamp" if has_header else "none"
        row["alignment_eligible"] = str(has_header).lower()
    with (export_root / "topics.csv").open("w", newline="", encoding="utf-8") as summary_file:
        fieldnames = [
            "topic", "msgtype", "msgcount", "export_kind", "relative_output",
            "has_header", "header_path", "timestamp_source", "alignment_eligible",
        ]
        summary_writer = csv.DictWriter(summary_file, fieldnames=fieldnames)
        summary_writer.writeheader()
        summary_writer.writerows(summary_rows)

    manifest = {
        "bag": str(bag_path),
        "export_root": str(export_root),
        "topics": summary_rows,
    }
    (export_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return export_root


def collect_bag_paths(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        if path.suffix.lower() != ".bag":
            raise ValueError(f"input file is not a ROS1 .bag: {path}")
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"ROS1 bag input not found: {path}")
    if not recursive:
        raise ValueError("input is a directory; add --recursive to export all .bag files")
    pattern = "*.bag"
    bags = [item for item in path.glob(pattern) if item.is_file() and item.suffix.lower() == ".bag"]
    if not bags:
        raise FileNotFoundError(f"no ROS1 .bag files found in: {path}")
    return sorted(bags)


def main() -> int:
    args = parse_args()
    args.output_root = args.output_root.expanduser()
    ensure_rosbags(args.rosbags_wheel)
    args.output_root.mkdir(parents=True, exist_ok=True)

    input_path = args.bag.expanduser().resolve()
    bag_paths = collect_bag_paths(input_path, args.recursive)
    failed = 0
    for bag_path in bag_paths:
        try:
            export_root = export_bag(bag_path, args.output_root)
            print(f"OK\t{bag_path}\t{export_root}")
        except Exception as exc:
            failed += 1
            print(f"FAILED\t{bag_path}\t{exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
