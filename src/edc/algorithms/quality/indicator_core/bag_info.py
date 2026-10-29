"""包级入口：python -m edc.algorithms.quality.indicator_core.bag_info <file_or_dir> [更多] ...

转发到 cli.bag_info 的 main()（README/文档均按此模块路径调用）。
"""
from .cli.bag_info import main

if __name__ == "__main__":
    main()
