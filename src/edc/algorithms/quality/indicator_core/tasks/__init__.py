"""任务层：TaskRepository 端口 + HttpTaskRepository（HTTP 控制面适配器）+ 契约 dataclass。"""
from .source import (  # noqa: F401
    TaskRepository, ApiResponse, ClaimRecord, ClaimBatchResult, ResultRequest,
)
from .http_source import HttpTaskRepository  # noqa: F401
