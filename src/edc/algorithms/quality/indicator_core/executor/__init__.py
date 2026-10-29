"""执行器层：跨文件批处理进程池 + 单文件内容按集并行。"""
from .parallel import _unit_kind, _collect_units, _run_content_parallel, DEFAULT_MIN_UNITS  # noqa: F401
