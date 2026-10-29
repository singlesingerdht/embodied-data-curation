"""包级入口：python -m edc.algorithms.quality.indicator_core.qc <path> [--json] ...

转发到 cli.qc 的 main()（兼容历史调用路径，等价 python -m edc.algorithms.quality.indicator_core）。
"""
from .cli.qc import main

if __name__ == "__main__":
    main()
