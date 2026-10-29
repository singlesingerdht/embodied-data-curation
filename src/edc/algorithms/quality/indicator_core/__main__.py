"""包级 CLI 入口：python -m edc.algorithms.quality.indicator_core <path> [--json] ...

转发到 cli/qc.py 的 main()。
"""
from .cli.qc import main

if __name__ == "__main__":
    main()
