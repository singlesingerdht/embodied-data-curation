"""冻结交叉验证 mixin（自然静止降级）"""
import numpy as np
from typing import List, Optional
from .model import CheckResult
from ...data.readers import DataReader

class FreezeMixin:
    def _cross_validate_freeze(self, info, reader: DataReader, checks: List[CheckResult]) -> None:
        """冻结交叉验证（C10/I7/J7）

        语义（与总表一致）：冻结 = 本通道内容不变 **且** 运动参考通道在同时刻仍在变化。
        对每个冻结窗口，采样运动参考通道（关节/IMU/cmd）在该时间窗内的值：
        - 参考通道在窗口内几乎不变（自然静止）→ 降级为 pass，标注 motion=static
        - 参考通道仍在变化但本通道冻结 → 真实传感器故障，保留
        """
        if not self.config.freeze_cross_validate:
            return

        base = info.start_time
        for c in checks:
            topic = self._check_topic(c.name)
            ti = info.topics.get(topic)
            ts = ti.timestamps if ti else None
            ref = self._motion_reference(topic, info)
            ref_ti = info.topics.get(ref) if ref else None
            if not ti or not ts or not ref_ti or not ref_ti.timestamps:
                continue
            ref_ts = np.asarray(ref_ti.timestamps, dtype=float)
            for e in c.events:
                if e.type != "data_freeze" or e.index < 0:
                    continue
                if e.index >= len(ts):
                    # 通道时间轴短于数据行（时间戳缺失/截断）：无法时间锚定该事件，跳过交叉验证
                    continue
                rng = e.detail.get("range", [e.index, e.index])
                t0 = ts[e.index] - base
                t1 = ts[min(rng[1], len(ts) - 1)] - base
                # 参考通道在窗口内的索引（按时间最近邻，精确窗口内）
                lo = int(np.searchsorted(ref_ts, t0 + base, side="left"))
                hi = int(np.searchsorted(ref_ts, t1 + base, side="right"))
                indices = list(range(max(0, lo), min(len(ref_ts), hi)))
                if len(indices) < 2:
                    if lo > 0:
                        indices.insert(0, lo - 1)
                    elif hi < len(ref_ts):
                        indices.append(hi)
                dur = max(t1 - t0, 1e-6)
                velocity = self._channel_motion(reader, ref, indices, dur)
                if velocity is not None and velocity < self.config.freeze_motion_threshold:
                    e.severity = "pass"
                    e.detail["motion"] = "static"
                    e.detail["motion_velocity"] = velocity
                    e.detail["note"] = "运动参考通道在窗口内几乎不变（自然静止），非传感器故障"

        for c in checks:
            self._recompute_check(c)

    def _channel_motion(self, reader: DataReader, ref: str, indices: List[int],
                        duration: float) -> Optional[float]:
        """采样参考通道在指定索引处的值，返回等效速度（rad/s，None=无法获取）"""
        if not indices:
            return None
        try:
            vals = reader.channel_values(ref, indices)
        except Exception:
            return None
        if not vals:
            return None
        series = []
        for v in vals:
            pos = (v or {}).get("position") or (v or {}).get("joint_q") \
                or (v or {}).get("ang_vel") or (v or {}).get("lin_acc")
            if pos:
                arr = np.asarray(pos, dtype=float)
                # 容忍数据损坏导致的维度不齐（如某行截断为 19 维）：跳过与主流维度不一致的行
                if arr.ndim == 1 and (not series or arr.shape == series[0].shape):
                    series.append(arr)
        if len(series) < 2:
            return None
        arr = np.vstack(series)
        total_change = float(np.abs(arr[-1] - arr[0]).max())
        return total_change / max(duration, 1e-6)

    @staticmethod
    def _motion_reference(topic: str, info) -> Optional[str]:
        """为冻结通道挑选运动参考通道（同模态内取一个可用的数值通道）"""
        ti = info.topics.get(topic)
        if ti is None:
            return None
        mod = ti.modality
        prefs = []
        if mod == "image":
            prefs = ["joint_state", "imu", "cmd"]
        elif mod == "joint_state":
            prefs = ["cmd", "imu"]
        elif mod == "imu":
            prefs = ["joint_state", "cmd"]
        elif mod == "cmd":
            prefs = ["joint_state", "imu"]
        elif mod == "pose":
            prefs = ["joint_state", "cmd", "imu"]
        else:
            return None
        for p in prefs:
            for t, t2 in info.topics.items():
                if t2.modality == p and t2.value_available and t != topic:
                    return t
        return None
