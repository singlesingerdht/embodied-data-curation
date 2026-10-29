"""任务控制面端口与请求/响应契约 dataclass。

契约语义（与业务端对齐，详见 doc/03-配置与部署.md §五）：
- 统一响应包裹 ApiResponse{code, msg, data}，code="200" 成功、非 "200" 失败；
- 质检结论失败（数据质量问题）→ 回调 HTTP 200，status=FAIL；
- 程序异常（引擎报错/文件找不到）→ 仍回调（status=FAIL+异常信息），后端返回
  HTTP 422（语义拒绝：已按失败处理，勿重试）；
- 重试策略只针对超时：查询/回调接口超时（HTTP 408 / httpx 超时异常）共尝试
  3 次（1 次 + 2 次重试，指数退避）；其他任何错误一律不重试。
- **无 run_id**：幂等由业务端保证（同一质检任务同一时间只允许一个运行实例），
  算子只按 task_id 领取/回写；checking_run_id 属后端内部实现（TTL 回收用），契约不暴露。
"""
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class ApiResponse:
    """后端统一响应包裹（平台惯例：最外层 msg+code，data 为数据实体）。"""
    code: str = "200"      # 业务码："200"=成功；非 "200"=失败（404 不存在/400 非法/422 语义拒绝/500 系统异常）
    msg: str = ""          # 失败时的业务异常信息（成功为空串）
    data: Optional[dict] = None  # 数据实体（claim → records/has_more/remaining；result → record_id）


@dataclass
class ClaimRecord:
    """claim_batch 响应 records[] 元素：一条可质检轨迹记录（算子读取与日志排查）。"""
    record_id: str = ""        # 记录主键（result 回调的 URL 目标）
    task_id: str = ""          # 质检任务 ID（URL 中已有，冗余带回便于日志）
    dataset_id: str = ""       # 来源数据集 ID（日志排查：反查数据来源）
    segment_id: str = ""       # 通用轨迹标识：片段/轨迹 ID，与 file_path 对应（所有数据集适用）
    episode_id: str = ""       # 数据集内分集标识：仅多轨迹数据集有值；lerobot=episode 文件名
                               # （如 episode_000000.parquet，数据集内唯一，与结果
                               #  points[].rows[].episode 列等值匹配）；单文件数据留空
    file_path: str = ""        # 轨迹文件路径（引擎直读本地/共享存储，不经过 HTTP）
    data_type: str = ""        # 数据类型快照（bag/lerobot/parquet...）


@dataclass
class ClaimBatchResult:
    """claim_batch 返回：一批记录 + 剩余量。"""
    records: list = field(default_factory=list)  # 本批记录（List[ClaimRecord]；空=任务取尽）
    has_more: bool = False      # 是否还有后续批
    remaining: int = 0          # 任务剩余未完成记录数（TODO+CHECKING；进度显示/日志用）


@dataclass
class ResultRequest:
    """result 回调请求体（逐条质检结果回写）。"""
    status: str = ""           # 终态："SUCCESS"=质检完成且数据无问题；"FAIL"=数据有质量问题 或 程序异常
    level: Optional[str] = None   # 质检评级：pass/warning/error（indicators 最差档；异常时可为 null）
    result_json: Optional[dict] = None  # 完整质检结果（qc-2.2 dict，回写数据表 result_json 列；异常时为最小错误体）
    summary: str = ""          # 人读摘要（SUCCESS=level+指标数；FAIL=质量问题摘要或异常信息）
    dataset_id: str = ""       # 来源数据集 ID（claim 原样回传，落库自关联）
    segment_id: str = ""       # 通用轨迹标识（claim 原样回传，落库自关联）
    episode_id: str = ""       # 数据集内分集标识（claim 原样回传；消费端与
                               # result_json 的 points[].rows[].episode 列等值匹配定位轨迹问题）


class TaskRepository(Protocol):
    """端口：任务仓储。实现为 HttpTaskRepository（见 http_source.py）。"""

    def claim_batch(self, task_id: str, limit: int) -> ClaimBatchResult:
        """领取一批可质检记录：后端把该任务中至多 limit 条 TODO 原子置 CHECKING 并返回。

        幂等语义：业务端保证同一任务同时只有一个运行实例（无并发双领）；
        网络重试可能领到下一批（已领的保持 CHECKING，超时由后端 TTL 回收兜底）。
        records 空 / has_more=False 即任务取尽。"""

    def mark_record(self, record_id: str, status: str, level: Optional[str] = None,
                    result_json: Optional[dict] = None, summary: str = "",
                    dataset_id: str = "", segment_id: str = "",
                    episode_id: str = "") -> None:
        """逐条质检结果回调（CHECKING → SUCCESS/FAIL 终态；重复提交幂等）。

        dataset_id/segment_id/episode_id 为 claim 时业务端给的归属标识，原样回传
        供落库自关联（不切分 result_json）；lerobot 多轨迹场景靠 episode_id 区分。

        程序异常回调时后端返回 422（语义拒绝），本方法吞掉不抛（已按失败处理）；
        404 抛 KeyError（契约/数据错误）；超时重试耗尽抛异常（记录保持 CHECKING
        归后端 TTL 回收）。"""
