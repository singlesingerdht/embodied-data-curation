from __future__ import annotations

import argparse
import csv
import json
import sys
import types
from pathlib import Path
from typing import Any

from export_rosbag1 import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_ROSBAGS_WHEEL,
    choose_binary_extension,
    has_header_stamp,
    header_stamp_parts,
    sanitize_path_part,
    to_jsonable,
    topic_to_relpath,
)


SCRIPT_DIR = Path(__file__).resolve().parent

TYPESTORE_NAME_MAP = {
    "foxy": "ROS2_FOXY",
    "galactic": "ROS2_GALACTIC",
    "humble": "ROS2_HUMBLE",
    "iron": "ROS2_IRON",
    "jazzy": "ROS2_JAZZY",
    "kilted": "ROS2_KILTED",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export data from a ROS2 bag directory or standalone .mcap file into raw_export. "
            "If the source is a normal folder, the script "
            "treats it as a batch root and exports every ROS2 bag found inside."
        )
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Path to a ROS2 bag directory, standalone .mcap file, or a folder containing multiple ROS2 bags",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="When source is a batch root folder, recursively search subdirectories for ROS2 bags.",
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
    parser.add_argument(
        "--default-typestore",
        choices=sorted(TYPESTORE_NAME_MAP),
        default=None,
        help=(
            "Fallback ROS2 typestore to use when the bag does not embed message definitions, "
            "e.g. jazzy or humble."
        ),
    )
    return parser.parse_args()


def ensure_rosbags(wheel_path: Path) -> None:
    if wheel_path.exists():
        sys.path.insert(0, str(wheel_path))
        return

    try:
        import rosbags  # noqa: F401
    except ModuleNotFoundError as exc:
        raise FileNotFoundError(
            f"rosbags dependency not found. Expected wheel at {wheel_path} or an installed 'rosbags' package."
        ) from exc


def ensure_apsw_stub() -> None:
    try:
        import apsw  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    module = types.ModuleType("apsw")

    class _UnavailableConnection:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise ModuleNotFoundError(
                "apsw is required to read sqlite3-based ROS2 bags. "
                "Standalone .mcap files can work with the built-in stub, but sqlite3/.db3 storage cannot."
            )

    class _BaseVFS:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def xOpen(self, *_args: Any, **_kwargs: Any) -> Any:
            raise ModuleNotFoundError("apsw VFS backend is unavailable")

    class _BaseVFSFile:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def xFileControl(self, *_args: Any, **_kwargs: Any) -> bool:
            return False

    module.Connection = _UnavailableConnection
    module.VFS = _BaseVFS
    module.VFSFile = _BaseVFSFile
    module.URIFilename = str
    module.Binding = object
    module.SQLITE_OPEN_READONLY = 0x00000001
    module.SQLITE_OPEN_READWRITE = 0x00000002
    module.SQLITE_OPEN_CREATE = 0x00000004
    module.SQLITE_OPEN_URI = 0x00000040
    module.SQLITE_OPEN_DELETEONCLOSE = 0x00000008
    module.SQLITE_OPEN_EXCLUSIVE = 0x00000010
    module.SQLITE_OPEN_AUTOPROXY = 0x00000020
    module.SQLITE_OPEN_WAL = 0x00000080
    module.SQLITE_OPEN_SUPER_JOURNAL = 0x00000100
    sys.modules["apsw"] = module


def resolve_source_name(path: Path) -> str:
    return sanitize_path_part(path.stem if path.is_file() else path.name)


def is_ros2_bag_dir(path: Path) -> bool:
    return path.is_dir() and (path / "metadata.yaml").exists()


def is_mcap_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".mcap"


def is_single_source(path: Path) -> bool:
    return is_mcap_file(path) or is_ros2_bag_dir(path)


def collect_batch_sources(root: Path, recursive: bool) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(f"batch root not found: {root}")

    sources: list[Path] = []
    pending = [root]

    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            if is_mcap_file(child):
                sources.append(child)
                continue
            if not child.is_dir():
                continue
            if is_ros2_bag_dir(child):
                sources.append(child)
                continue
            if recursive:
                pending.append(child)

    return sources


def create_default_typestore(name: str | None) -> Any | None:
    if not name:
        return None

    from rosbags.typesys import Stores, get_typestore

    enum_name = TYPESTORE_NAME_MAP[name]
    return get_typestore(getattr(Stores, enum_name))


def build_any_reader(paths: list[Path], default_typestore: Any | None) -> Any:
    ensure_apsw_stub()
    from rosbags.highlevel import AnyReader

    if default_typestore is None:
        return AnyReader(paths)
    return AnyReader(paths, default_typestore=default_typestore)


def export_bag(source_path: Path, output_root: Path, default_typestore: Any | None) -> Path:
    if not source_path.exists():
        raise FileNotFoundError(f"source path not found: {source_path}")

    export_root = output_root / resolve_source_name(source_path)
    export_root.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    jsonl_files: dict[str, Any] = {}
    image_counters: dict[str, int] = {}
    header_seen: dict[str, bool] = {}

    with build_any_reader([source_path], default_typestore) as reader:
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

                summary_writer.writerow(
                    {
                        "topic": connection.topic,
                        "msgtype": connection.msgtype,
                        "msgcount": connection.msgcount,
                        "export_kind": export_kind,
                        "relative_output": relative_output,
                    }
                )
                summary_rows.append(
                    {
                        "topic": connection.topic,
                        "msgtype": connection.msgtype,
                        "msgcount": connection.msgcount,
                        "export_kind": export_kind,
                        "relative_output": relative_output,
                    }
                )

        try:
            for connection, timestamp_ns, rawdata in reader.messages():
                msg = reader.deserialize(rawdata, connection.msgtype)
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
        "source": str(source_path),
        "source_kind": "ros2_directory" if source_path.is_dir() else source_path.suffix.lower().lstrip("."),
        "export_root": str(export_root),
        "topics": summary_rows,
    }
    (export_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return export_root


def process_source(args: argparse.Namespace, source_path: Path, default_typestore: Any | None) -> Path:
    return export_bag(source_path, args.output_root, default_typestore)


def main() -> int:
    args = parse_args()
    args.source = args.source.expanduser()
    args.output_root = args.output_root.expanduser()
    ensure_rosbags(args.rosbags_wheel)
    default_typestore = create_default_typestore(args.default_typestore)
    args.output_root.mkdir(parents=True, exist_ok=True)

    if is_single_source(args.source):
        result_path = process_source(args, args.source, default_typestore)
        print(result_path)
        return 0

    batch_sources = collect_batch_sources(args.source, args.recursive)
    if not batch_sources:
        raise FileNotFoundError(
            f"no ROS2 bag directory or .mcap file found under batch root: {args.source}"
        )

    success_count = 0
    failed: list[tuple[Path, str]] = []
    for source_path in batch_sources:
        try:
            result_path = process_source(args, source_path, default_typestore)
            success_count += 1
            print(f"[OK] {source_path} -> {result_path}")
        except Exception as exc:
            failed.append((source_path, str(exc)))
            print(f"[FAIL] {source_path} -> {exc}", file=sys.stderr)

    print(f"Batch summary: success={success_count}, failed={len(failed)}")
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
