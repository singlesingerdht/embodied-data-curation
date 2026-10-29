"""HttpTaskRepository：HTTP 任务控制面适配器（云端/边缘后端实现同一套契约）。

契约两接口（见 doc/HTTP任务控制面契约.md）：
① POST {claim_url}    领取一批（limit 可调）
② POST {result_url}   逐条质检结果回调（幂等，终态）

两接口均为**完整地址**，由部署方以环境变量注入（含 {task_id}/{record_id} 占位符，
算子只做占位符填充、不拼接任何 path——后端路径/API 版本前缀变更无需改算子代码；
占位符规范写法下划线，兼容后端 Java 风格驼峰 {taskId}/{recordId}）：
QC_API_CLAIM_URL / QC_API_RESULT_URL / QC_API_TOKEN / QC_API_TIMEOUT / QC_BATCH_SIZE。
"""
import logging
import os
import time
from typing import Optional

import httpx

from .source import ApiResponse, ClaimRecord, ClaimBatchResult

logger = logging.getLogger("edc.algorithms.quality.indicator_core.tasks.http")


class ApiError(RuntimeError):
    """业务失败（响应包裹 code 非 200）：携带业务码与后端错误信息。"""
    def __init__(self, url: str, code, msg: str = ""):
        self.code = str(code)
        self.msg = msg
        super().__init__(f"HTTP 任务控制面业务失败 {url}: code={self.code} msg={self.msg}")


class HttpTaskRepository:
    """HTTP 控制面适配器：只发请求，不持有任何状态。

    网络策略：仅超时指数退避重试（claim_batch/mark_record 均幂等，重试安全）；
    4xx 不重试，404 → KeyError，其余 4xx 抛含响应体的异常。
    """

    def __init__(self, claim_url: Optional[str] = None, result_url: Optional[str] = None,
                 token: Optional[str] = None, timeout: Optional[float] = None,
                 batch_size: Optional[int] = None, retries: int = 2, backoff: float = 1.0,
                 transport: Optional[httpx.BaseTransport] = None):
        # retries=2 → 共尝试 3 次（1 次 + 2 次重试）；只对超时（408/httpx 超时）重试
        self.claim_url = (claim_url or os.environ.get("QC_API_CLAIM_URL") or "").rstrip("/")
        self.result_url = (result_url or os.environ.get("QC_API_RESULT_URL") or "").rstrip("/")
        if not self.claim_url or not self.result_url:
            raise ValueError("HttpTaskRepository 需要 claim_url/result_url 参数或 "
                             "QC_API_CLAIM_URL/QC_API_RESULT_URL 环境变量")
        self.token = token if token is not None else os.environ.get("QC_API_TOKEN")
        self.timeout = timeout if timeout is not None else float(
            os.environ.get("QC_API_TIMEOUT", "10"))
        self.batch_size = batch_size if batch_size is not None else int(
            os.environ.get("QC_BATCH_SIZE", "20"))
        self._retries, self._backoff = retries, backoff
        self._client = httpx.Client(transport=transport, timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpTaskRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _headers(self) -> dict:
        # inner:true 标记引擎内部调用（后端据此区分外部/内部流量，做路由/限流/审计）
        h = {"inner": "true"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    @staticmethod
    def _build_url(template: str, **kw) -> str:
        """占位符填充：规范写法 {task_id}/{record_id}，兼容后端 Java 风格驼峰
        {taskId}/{recordId}（先归一化为下划线再填充）；其余未预期占位符显式失败。"""
        original = template
        template = (original.replace("{taskId}", "{task_id}")
                    .replace("{recordId}", "{record_id}"))
        try:
            return template.format(**kw)
        except KeyError as e:
            raise ValueError(f"URL 模板含未预期占位符 {e}: {original}") from e

    def _post(self, url: str, payload: dict) -> ApiResponse:
        """POST 完整地址并解析 {code,msg,data} 包裹。

        重试策略（与业务端对齐）：只对**超时**重试——HTTP 408 与 httpx 超时异常
        （ReadTimeout/ConnectTimeout/WriteTimeout/PoolTimeout），共尝试 3 次
        （1 + retries），指数退避；其他任何错误（4xx/5xx/网络错）一律不重试。
        """
        headers = self._headers()
        _brief = {k: (v if not isinstance(v, dict) else f"<dict {len(v)} keys>")
                  for k, v in payload.items()}
        for attempt in range(self._retries + 1):
            logger.info("POST %s attempt=%d payload=%s", url, attempt + 1, _brief)
            try:
                resp = self._client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as e:
                logger.warning("POST %s 超时 attempt=%d: %s", url, attempt + 1, e)
                if attempt >= self._retries:
                    raise
                time.sleep(self._backoff * (2 ** attempt))
                continue
            except httpx.TransportError:
                raise   # 非超时网络错误：不重试
            logger.info("POST %s -> %d (attempt=%d)", url, resp.status_code, attempt + 1)
            if resp.status_code == 408:
                if attempt >= self._retries:
                    resp.raise_for_status()
                time.sleep(self._backoff * (2 ** attempt))
                continue
            if resp.status_code == 404:
                raise KeyError(f"HTTP 任务控制面 404: {url}")
            # 先解析统一包裹：业务码非 200 一律 ApiError（422 语义拒绝由 mark_record 吞掉；
            # 其余 4xx/5xx 不重试直接抛）
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = {}
            if isinstance(body, dict) and "code" in body:
                code = str(body.get("code"))
                if code == "200":
                    return ApiResponse(code=code, msg=body.get("msg") or "",
                                       data=body.get("data"))
                raise ApiError(url=url, code=code, msg=str(body.get("msg") or ""))
            resp.raise_for_status()   # 无包裹的其余状态码：不重试直接抛
            raise ValueError(f"响应缺少 {{code,msg,data}} 包裹: {url}")

    def claim_batch(self, task_id: str, limit: int) -> ClaimBatchResult:
        """领取一批；后端把该任务中至多 limit 条 TODO 原子置 CHECKING（幂等由业务端单实例保证）。"""
        resp = self._post(self._build_url(self.claim_url, task_id=task_id), {"limit": limit})
        data = resp.data or {}
        records = [ClaimRecord(record_id=r["record_id"], task_id=r.get("task_id", task_id),
                               dataset_id=str(r.get("dataset_id") or ""),
                               segment_id=str(r.get("segment_id") or ""),
                               episode_id=str(r.get("episode_id") or ""),
                               file_path=r.get("file_path", ""),
                               data_type=str(r.get("data_type") or ""))
                   for r in data.get("records") or []]
        result = ClaimBatchResult(records=records,
                                  has_more=bool(data.get("has_more", False)),
                                  remaining=int(data.get("remaining", 0)))
        logger.info("claim_batch task=%s -> 领取 %d 条, has_more=%s, remaining=%d",
                    task_id, len(records), result.has_more, result.remaining)
        return result

    def mark_record(self, record_id: str, status: str, level: Optional[str] = None,
                    result_json: Optional[dict] = None, summary: str = "",
                    dataset_id: str = "", segment_id: str = "",
                    episode_id: str = "") -> None:
        """逐条结果回调。422（程序异常回调的语义拒绝）吞掉并记日志——后端已按失败处理，
        不重试不抛；404 抛 KeyError；超时重试耗尽抛异常（记录保持 CHECKING 归 TTL）。

        dataset_id/segment_id/episode_id 为 claim 原样回传的归属标识（lerobot
        多轨迹场景靠 episode_id 区分），不切分 result_json。"""
        try:
            self._post(self._build_url(self.result_url, record_id=record_id),
                       {"status": status, "level": level,
                        "result_json": result_json, "summary": summary,
                        "dataset_id": dataset_id, "segment_id": segment_id,
                        "episode_id": episode_id})
        except ApiError as e:
            if str(e.code) == "422":
                logger.warning("mark_record id=%s 语义拒绝（已按质检失败处理）: %s",
                               record_id, e.msg)
                return
            raise
        logger.info("mark_record id=%s -> %s (level=%s)", record_id, status, level)
