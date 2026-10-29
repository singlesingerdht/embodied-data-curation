"""事件富集 mixin（time/range 定位）"""
from typing import List
from ...data.ir import DataInfo
from .model import CheckResult

class ScoringMixin:
    def _enrich_events(self, info: "DataInfo", checks: List[CheckResult]) -> None:
        """给事件补全 time（通道时间轴）、detail['range']，供定位使用。

        - time: 相对通道起点的时间（秒）
        - detail['range']: 受影响帧范围；单点事件置 [idx, idx]
        """
        base = info.start_time
        for c in checks:
            topic = self._check_topic(c.name)
            ts = info.topics.get(topic).timestamps if topic and topic in info.topics else None
            for e in c.events:
                d = e.detail
                if e.index >= 0 and ts and e.index < len(ts):
                    e.time = ts[e.index] - base
                if "range" not in d and e.index >= 0:
                    d["range"] = [e.index, e.index]

    @staticmethod
    def _check_topic(check_name: str) -> str:
        for prefix in ("content_image_pixels_", "content_image_", "content_imu_bias_",
                       "content_imu_", "content_joint_", "content_cmd_",
                       "content_pose_", "content_numeric"):
            if check_name.startswith(prefix):
                return check_name[len(prefix):]
        return ""

    @staticmethod
    def _recompute_check(c: CheckResult) -> None:
        """按事件重新计算检查项严重度（降级后同步）"""
        has_error = any(e.severity == "error" for e in c.events)
        has_warning = any(e.severity == "warning" for e in c.events)
        if has_error:
            c.severity = "error"; c.passed = False
        elif has_warning:
            c.severity = "warning"; c.passed = False
        else:
            c.severity = "pass"; c.passed = True
