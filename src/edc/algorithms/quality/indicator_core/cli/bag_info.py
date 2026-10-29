"""查看数据源信息的命令行工具（多格式，纯文件 API）"""

import sys
from pathlib import Path
from ..data.readers import read_data


def main():
    paths = sys.argv[1:]
    if not paths:
        print("Usage: uv run python -m edc.algorithms.quality.indicator_core.bag_info <file1> [file2 ...]")
        sys.exit(1)
    from ..pipeline.runner import _dir_is_aggregate
    dirs = [p for p in paths if Path(p).is_dir()]
    bad = [d for d in dirs if not _dir_is_aggregate(d)]
    if bad:
        print(f"bag_info 只接受轨迹文件、文件列表或 lerobot/折叠式数据集目录（其他目录由业务层解析）: {bad}",
              file=sys.stderr)
        sys.exit(1)
    for path in paths:
        try:
            print(f"Reading: {path}")
            info = read_data(path)

            print(f"\n{'='*60}")
            print("Data Info")
            print(f"{'='*60}")
            print(f"Path: {info.path}")
            print(f"Format: {info.format}")
            print(f"Duration: {info.duration:.2f} seconds")
            print(f"Channels: {len(info.topics)}")

            print(f"\n{'='*60}")
            print("Channels Detail")
            print(f"{'='*60}")

            for name, ti in info.topics.items():
                print(f"\n  {name}")
                print(f"    Type: {ti.msg_type}")
                print(f"    Modality: {ti.modality}")
                print(f"    Count: {ti.count}")
                print(f"    Value available: {ti.value_available}")
                if ti.timestamps:
                    print(f"    First timestamp: {ti.timestamps[0]:.3f}")
                    print(f"    Last timestamp: {ti.timestamps[-1]:.3f}")

        except Exception as e:
            print(f"Error reading {path}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
