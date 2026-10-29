"""数值检测 mixin（IMU/关节/cmd + 偏置 + 末端位姿）

位姿三算法（参考 Quality_Assessment）：
- 位姿冻结：SLAM 式 lookback 验证（冻结前回看窗口平均速度 > 阈值才算真冻结，
  排除"机器人本来就静止"的自然静止误报）
- 位姿抖动：Savitzky-Golay 残差 jerk，只在静止段计算（排除正常运动高 jerk 干扰），
  run-length 合并连续超标帧为段事件 — 仅 pose（末端位姿）。
- 位姿复位：首/末帧 vs profile 注入的 home_position，双阈值线性插值评分
"""
import math
import numpy as np
from typing import List, Optional, Tuple, Iterator
from .model import ContentEvent, CheckResult

# 手部通道识别（灵巧手/夹爪，joint_state/cmd 模态专用；位姿/相机不经过此处）。
# "hand" 覆盖 dexhand/xhand/control_robot_hand/hand_path；"effector"/"gripper"
# 覆盖 zerith/luoshi 的 effector 通道与 dual_arm 的 gripper/servo。
# 34 源通道名核对：三关键字命中且仅命中手部通道，臂关节（joint/servo、
# arm/end/head/waist position、target_q_pos 等）零误伤。
_HAND_KEYWORDS = ("hand", "effector", "gripper")


def _is_hand_channel(topic: str) -> bool:
    t = (topic or "").lower()
    return any(k in t for k in _HAND_KEYWORDS)


class NumericChecksMixin:

    @staticmethod
    def _imu_vectors(d: dict):
        """提取 IMU 三轴向量（兼容 ang_vel/lin_acc 与 position 打包两种表示）

        position 打包约定：前 3 为陀螺 (gx,gy,gz)，后 3 为加速度 (ax,ay,az)。
        """
        av = d.get("ang_vel")
        la = d.get("lin_acc")
        if av and la and len(av) >= 3 and len(la) >= 3:
            return list(av[:3]), list(la[:3])
        pos = d.get("position")
        if pos and len(pos) >= 6:
            return list(pos[:3]), list(pos[3:6])
        return None, None

    def _check_imu_bias(
        self, topic: str, ti, full_iter: Optional[Iterator[dict]],
    ) -> CheckResult:
        """静止段偏置检测：找角速度低的静止段，统计 gyro 均值偏离 0、|acc|-9.81 偏离"""
        events: List[ContentEvent] = []
        if full_iter is None:
            return CheckResult(
                name=f"content_imu_bias_{topic}", passed=True, severity="pass",
                message=f"{topic}: 偏置检测不可用，跳过", events=[], detail={"skipped": True},
            )

        # 流式找静止段：静止 = 连续帧 gyro 变化小（方差近似，与绝对偏置无关）
        static_run = 0
        static_segments = []   # (start_idx, end_idx, gyro_mean, acc_mean)
        seg_start = None
        seg_gyro_sum = 0.0
        seg_acc_sum = 0.0
        seg_min_g = float('inf')
        seg_max_g = 0.0
        prev_g = None
        idx = 0
        for d in full_iter:
            av, la = self._imu_vectors(d)
            if av is not None and la is not None:
                gmag = math.sqrt(av[0] ** 2 + av[1] ** 2 + av[2] ** 2)
                amag = math.sqrt(la[0] ** 2 + la[1] ** 2 + la[2] ** 2)
                # 静止还需 gyro 幅度接近 0（< 6× 偏置阈值）：仅看"变化小"会把
                # 匀速旋转（gyro 恒定 5 rad/s）误判为静止段 → 假偏置 error
                static_mag_ok = gmag < self.config.imu_bias_gyro_max * 6
                stable = (prev_g is None
                          or abs(gmag - prev_g) < self.config.imu_bias_static_diff) \
                    and static_mag_ok
                if stable and (seg_start is None or gmag - seg_min_g < self.config.imu_bias_static_spread):
                    static_run += 1
                    if seg_start is None:
                        seg_start = idx
                    seg_gyro_sum += gmag
                    seg_acc_sum += amag
                    seg_min_g = min(seg_min_g, gmag)
                    seg_max_g = max(seg_max_g, gmag)
                else:
                    if static_run >= self.config.imu_bias_min_static and seg_start is not None:
                        n = idx - seg_start
                        static_segments.append((seg_start, idx - 1, seg_gyro_sum / n, seg_acc_sum / n))
                    static_run = 1
                    seg_start = idx
                    seg_gyro_sum = gmag
                    seg_acc_sum = amag
                    seg_min_g = gmag
                    seg_max_g = gmag
                prev_g = gmag
            else:
                prev_g = None
            idx += 1
        if static_run >= self.config.imu_bias_min_static and seg_start is not None:
            n = idx - seg_start
            static_segments.append((seg_start, idx - 1, seg_gyro_sum / n, seg_acc_sum / n))

        if not static_segments:
            return CheckResult(
                name=f"content_imu_bias_{topic}", passed=True, severity="pass",
                message=f"{topic}: 无足够静止段，偏置检测跳过",
                events=[], detail={"static_segments": 0},
            )

        worst = max(static_segments, key=lambda s: max(abs(s[2]), abs(s[3] - 9.81)))
        gyro_bias = abs(worst[2])
        acc_dev = abs(worst[3] - 9.81)
        if gyro_bias > self.config.imu_bias_gyro_max or \
                acc_dev > self.config.imu_bias_acc_max:
            # 设计表 I6 三档：偏置轻微（仅记录）/ 明显（中）/ 严重（重）。
            # 质检映射：超阈值 → warning；超阈值 2 倍（严重）→ error
            severe = (gyro_bias > 2 * self.config.imu_bias_gyro_max
                      or acc_dev > 2 * self.config.imu_bias_acc_max)
            sev = "error" if severe else "warning"
            events.append(ContentEvent(
                type="bias_suspect", severity=sev, topic=topic, index=worst[0],
                detail={"gyro_mean": round(worst[2], 4),
                        "acc_mean": round(worst[3], 3), "acc_dev_from_g": round(acc_dev, 3),
                        "static_segments": len(static_segments),
                        "segment": [worst[0], worst[1]],
                        "gyro_threshold": self.config.imu_bias_gyro_max,
                        "acc_threshold": self.config.imu_bias_acc_max},
            ))
            severity, passed, msg = sev, False, \
                f"{topic}: 静止段 IMU 偏置{('严重' if severe else '可疑')} (gyro_mean={worst[2]:.3f} rad/s, |acc|-9.81={acc_dev:.2f})"
        else:
            severity, passed, msg = "pass", True, \
                f"{topic}: 静止段偏置正常 (gyro_mean={worst[2]:.3f}, |acc|-9.81={acc_dev:.2f})"

        return CheckResult(
            name=f"content_imu_bias_{topic}", passed=passed, severity=severity,
            message=msg, events=events,
            detail={"static_segments": len(static_segments),
                    "gyro_mean": round(worst[2], 4), "acc_mean": round(worst[3], 3)},
        )

    def _check_imu(
        self,
        topic: str,
        ti,
        samples: List[Tuple[int, dict]],
        full_iter: Optional[Iterator[dict]],
    ) -> CheckResult:
        events: List[ContentEvent] = []
        freeze_segments: List[dict] = []

        # 一次流式遍历：硬错误 + 冻结
        last_hash = None
        freeze_count = 0
        freeze_start = None
        anomaly_run = 0          # 连续异常帧数（设计表 I5 分档）
        max_anomaly_run = 0
        if full_iter is not None:
            for idx, d in enumerate(full_iter):
                if idx >= ti.count:
                    break  # 时间轴短于数据行时，只扫描声明范围内的帧（防事件 index 越界）
                last_idx = idx
                evs = self._check_imu_value(d, idx)
                if evs:
                    anomaly_run += 1
                    max_anomaly_run = max(max_anomaly_run, anomaly_run)
                    events.extend(evs)
                else:
                    anomaly_run = 0
                h = self._imu_hash(d)
                if h is not None and h == last_hash:
                    freeze_count += 1
                    if freeze_start is None:
                        freeze_start = idx
                else:
                    if freeze_count >= self.config.imu_freeze_low_frames:
                        freeze_segments.append({"start": freeze_start, "end": idx - 1, "count": freeze_count})
                        sev = "error" if freeze_count >= self.config.imu_freeze_high_frames else "warning"
                        events.append(ContentEvent(
                            type="data_freeze", severity=sev,
                            topic=topic, index=freeze_start,
                            detail={"count": freeze_count, "range": [freeze_start, idx - 1]},
                        ))
                    freeze_count = 0
                    freeze_start = None
                last_hash = h

        # I7: 末尾冻结段 flush（冻结延伸到流末尾时在循环内不触发 else 分支）
        if full_iter is not None and freeze_count >= self.config.imu_freeze_low_frames:
            freeze_segments.append({"start": freeze_start, "end": last_idx, "count": freeze_count})
            sev = "error" if freeze_count >= self.config.imu_freeze_high_frames else "warning"
            events.append(ContentEvent(
                type="data_freeze", severity=sev,
                topic=topic, index=freeze_start,
                detail={"count": freeze_count, "range": [freeze_start, last_idx]},
            ))

        # 采样点补充检查：全流不可用（reader 不支持 iter_values）时，
        # 退化为按采样帧检测，避免该通道数值检测静默全 pass
        if full_iter is None:
            for idx, d in samples:
                events.extend(self._check_imu_value(d, idx))

        has_bounds = any(e.type == 'imu_bounds' for e in events)
        has_nan = any(e.type == 'imu_nan' for e in events)
        has_freeze = len(freeze_segments) > 0

        if has_nan or has_bounds:
            # 设计表 I5 三档：零星单点（轻微）/ 连续异常帧<10（中度）/ ≥10（重度）。
            # 质检三档映射：<10 连续 → warning（记录），≥10 连续 → error（弃置）
            severity = "error" if max_anomaly_run >= 10 else "warning"
            passed = False
            n_nan = sum(1 for e in events if e.type == 'imu_nan')
            n_b = sum(1 for e in events if e.type == 'imu_bounds')
            msg = (f"{topic}: IMU NaN/Inf({n_nan}次)/爆表({n_b}次)"
                   + ("" if severity == "error" else f"，连续异常≤{max_anomaly_run}帧(中度)"))
        elif has_freeze:
            severity = ("error" if any(e.severity == "error" for e in events
                                       if e.type == "data_freeze") else "warning")
            passed = False
            msg = f"{topic}: IMU 冻结 {len(freeze_segments)} 段"
        else:
            severity = "pass"; passed = True
            msg = f"{topic}: IMU 数值正常 ({ti.count}条消息)"

        _detail = {"total_count": ti.count, "freeze_segments": freeze_segments}
        _ex_err = getattr(self, "extract_errors", 0)
        if _ex_err:   # 值提取失败显式上报（不再静默假 pass）
            _detail["extract_errors"] = _ex_err
            _detail["last_extract_error"] = getattr(self, "last_extract_error", "")
            self.extract_errors = 0
        return CheckResult(
            name=f"content_imu_{topic}",
            passed=passed, severity=severity, message=msg, events=events,
            detail=_detail,
        )

    def _check_imu_value(self, d: dict, index: int) -> List[ContentEvent]:
        """检查单帧 IMU 数值（I5）"""
        events = []
        try:
            av, la = self._imu_vectors(d)
            if av:
                for comp in ("x", "y", "z"):
                    v = av[0] if comp == "x" else av[1] if comp == "y" else av[2] if len(av) > 2 else None
                    if v is not None and not math.isfinite(v):
                        events.append(ContentEvent(
                            type="imu_nan", severity="error", topic="", index=index,
                            detail={"field": f"ang_vel_{comp}", "value": v}))
                finite = [c for c in av if c is not None and math.isfinite(c)]
                if len(finite) == len(av) and len(av) >= 3:
                    mag_gyro = math.sqrt(sum(c * c for c in av[:3]))
                    if mag_gyro > self.config.gyro_max_threshold:
                        events.append(ContentEvent(
                            type="imu_bounds", severity="error", topic="", index=index,
                            detail={"field": "gyro_magnitude", "value": mag_gyro,
                                    "threshold": self.config.gyro_max_threshold}))
            if la:
                for comp in ("x", "y", "z"):
                    v = la[0] if comp == "x" else la[1] if comp == "y" else la[2] if len(la) > 2 else None
                    if v is not None and not math.isfinite(v):
                        events.append(ContentEvent(
                            type="imu_nan", severity="error", topic="", index=index,
                            detail={"field": f"lin_acc_{comp}", "value": v}))
                finite = [c for c in la if c is not None and math.isfinite(c)]
                if len(finite) == len(la) and len(la) >= 3:
                    mag_acc = math.sqrt(sum(c * c for c in la[:3]))
                    if mag_acc > self.config.acc_max_threshold:
                        events.append(ContentEvent(
                            type="imu_bounds", severity="error", topic="", index=index,
                            detail={"field": "acc_magnitude", "value": mag_acc,
                                    "threshold": self.config.acc_max_threshold}))
        except Exception as e:
            # 不再静默吞：提取失败计数（detail 上报），避免质检假 pass
            self.extract_errors = getattr(self, "extract_errors", 0) + 1
            self.last_extract_error = f"{type(e).__name__}: {e}"
        return events

    def _imu_hash(self, d: dict) -> Optional[str]:
        try:
            av, la = self._imu_vectors(d)
            av = av or [0, 0, 0]
            la = la or [0, 0, 0]
            return (f"{av[0]:.4f}_{av[1]:.4f}_{av[2]:.4f}_"
                    f"{la[0]:.4f}_{la[1]:.4f}_{la[2]:.4f}")
        except Exception:
            return None

    def _frame_dt_sec(self, ti) -> float:
        """实测中位帧间隔（秒），供 |Δpos|/dt 归一化；无时间戳回退 30Hz（sampling_frame_dt_fallback_sec）"""
        ts = getattr(ti, "timestamps", None)
        if ts and len(ts) >= 2:
            d = np.diff(np.asarray(ts, dtype=float))
            d = d[d > 0]
            if len(d):
                med = float(np.median(d))
                if 0 < med < self.config.sampling_frame_dt_max_sec:
                    return med
        return self.config.sampling_frame_dt_fallback_sec

    def _check_joint_state(
        self,
        topic: str,
        ti,
        samples: List[Tuple[int, dict]],
        full_iter: Optional[Iterator[dict]],
    ) -> CheckResult:
        events: List[ContentEvent] = []
        changes: List[dict] = []
        mutation_points: List[dict] = []
        freeze_segments: List[dict] = []
        # 手部通道语义豁免：灵巧手/夹爪的常态就是"保持不动"与"快速开合"，
        # 跳变/复位/速度峰值/冻结四项按机械臂设计，对手必然误报 → 跳过；
        # NaN/越界（数据完整性）不受影响。真冻结兜底：J1-J4 时序（断流仍报）。
        hand_exempt = _is_hand_channel(topic)

        prev_pos = None
        last_hash = None
        freeze_count = 0
        freeze_start = None
        last_idx = -1

        if full_iter is not None:
            for idx, d in enumerate(full_iter):
                if idx >= ti.count:
                    break  # 时间轴短于数据行时，只扫描声明范围内的帧（防事件 index 越界）
                last_idx = idx
                events.extend(self._check_joint_value(d, idx, topic))

                pos = d.get("position") or []
                if pos:
                    # J5: 跳变 / 复位（手部豁免）
                    if not hand_exempt and prev_pos is not None and len(prev_pos) == len(pos):
                        dt_sec = self._frame_dt_sec(ti)
                        diff = np.abs(np.asarray(pos, dtype=float) - np.asarray(prev_pos, dtype=float))
                        jump_thr = self.config.joint_jump_threshold * dt_sec  # rad/s -> rad/帧
                        max_jump = float(np.max(diff))
                        max_jump_rate = max_jump / dt_sec if dt_sec > 0 else 0.0
                        # E8: 关节速度峰值（joint_velocity_peak，此前为未接线死配置）
                        if max_jump_rate > self.config.joint_velocity_peak:
                            events.append(ContentEvent(
                                type="joint_velocity", severity="warning",
                                topic=topic, index=idx,
                                detail={"max_velocity": round(max_jump_rate, 3),
                                        "dt_sec": round(dt_sec, 5)}))
                        # 浮点容差：匀速斜坡的 |Δpos| 可能比 阈值大 ~1e-16（0.1*r 累加误差），
                        # 不加容差会把平滑运动误报为 joint_jump/joint_reset
                        jump_count = int(np.sum(diff > jump_thr + 1e-9))
                        if jump_count > 0:
                            if jump_count >= len(diff) * self.config.joint_reset_ratio:
                                events.append(ContentEvent(
                                    type="joint_reset", severity="error",
                                    topic=topic, index=idx,
                                    detail={"jump_count": jump_count, "total_joints": len(diff),
                                            "max_jump": round(max_jump, 4),
                                        "max_jump_rate": round(max_jump_rate, 3),
                                        "dt_sec": round(dt_sec, 5)}))
                                changes.append({"index": idx, "type": "joint_reset",
                                                "detail": {"max_jump": round(max_jump, 4)}})
                                mutation_points.append({
                                    "between": [max(0, idx - 3), idx], "estimated_at": idx,
                                    "field": "joint_position",
                                    "from_val": "normal", "to_val": "reset_event"})
                            else:
                                events.append(ContentEvent(
                                    type="joint_jump", severity="warning",
                                    topic=topic, index=idx,
                                    detail={"jump_count": jump_count, "total_joints": len(diff),
                                            "max_jump": round(max_jump, 4),
                                        "max_jump_rate": round(max_jump_rate, 3),
                                        "dt_sec": round(dt_sec, 5)}))
                                changes.append({"index": idx, "type": "joint_jump",
                                                "detail": {"max_jump": round(max_jump, 4)}})
                    prev_pos = pos

                    # J7: 冻结（设计表 (position+velocity) hash 连续相同；手部豁免）
                    # 完整维度 + 量化容差（E13）：截断前 8 关节会让 >8 关节机型
                    # 后部关节运动时 hash 仍相同 → 假冻结；原始精度则让浮点噪声
                    # 细微差异导致真冻结漏检
                    if hand_exempt:
                        continue
                    vel = d.get("velocity") or []
                    pos_q = ",".join(f"{float(x):.4f}" for x in pos)
                    vel_q = ",".join(f"{float(x):.4f}" for x in vel) if vel else ""
                    h = f"{pos_q}_{vel_q}"
                    if h == last_hash:
                        freeze_count += 1
                        if freeze_start is None:
                            freeze_start = idx
                    else:
                        if freeze_count >= self.config.joint_freeze_low_frames:
                            freeze_segments.append({"start": freeze_start, "end": idx - 1, "count": freeze_count})
                            sev = "error" if freeze_count >= self.config.joint_freeze_high_frames else "warning"
                            events.append(ContentEvent(
                                type="data_freeze", severity=sev,
                                topic=topic, index=freeze_start,
                                detail={"count": freeze_count, "range": [freeze_start, idx - 1]},
                            ))
                        freeze_count = 0
                        freeze_start = None
                    last_hash = h

        # J7: 末尾冻结段 flush
        if full_iter is not None and freeze_count >= self.config.joint_freeze_low_frames:
            freeze_segments.append({"start": freeze_start, "end": last_idx, "count": freeze_count})
            sev = "error" if freeze_count >= self.config.joint_freeze_high_frames else "warning"
            events.append(ContentEvent(
                type="data_freeze", severity=sev,
                topic=topic, index=freeze_start,
                detail={"count": freeze_count, "range": [freeze_start, last_idx]},
            ))

        # 采样点补充检查：全流不可用（reader 不支持 iter_values，如 BVH）时
        # 退化为按采样帧检测，避免该通道数值检测静默全 pass
        if full_iter is None:
            for idx, d in samples:
                events.extend(self._check_joint_value(d, idx, topic))

        has_nan = any(e.type == 'joint_nan' for e in events)
        has_bounds = any(e.type == 'joint_bounds' for e in events)
        has_jump = any(e.type == 'joint_jump' for e in events)
        has_reset = any(e.type == 'joint_reset' for e in events)
        has_velocity = any(e.type == 'joint_velocity' for e in events)
        has_freeze = any(e.type == 'data_freeze' for e in events)

        if has_nan or has_bounds:
            severity = "error"; passed = False
            msg = f"{topic}: 关节数值异常 (NaN/Inf/越界)"
        elif has_reset:
            severity = "error"; passed = False
            msg = f"{topic}: 检测到关节复位事件"
        elif has_jump:
            severity = "warning"; passed = False
            msg = f"{topic}: 检测到关节跳变 ({sum(1 for e in events if e.type=='joint_jump')}次)"
        elif has_velocity:
            severity = "warning"; passed = False
            msg = f"{topic}: 关节速度峰值超限 ({max(e.detail.get('max_velocity', 0) for e in events if e.type=='joint_velocity'):.2f} rad/s > {self.config.joint_velocity_peak})"
        elif has_freeze:
            severity = ("error" if any(e.severity == "error" for e in events
                                       if e.type == "data_freeze") else "warning")
            passed = False
            msg = f"{topic}: 关节数据冻结"
        else:
            severity = "pass"; passed = True
            if hand_exempt:
                msg = f"{topic}: 关节数值正常 ({ti.count}条消息；手部通道，跳变/速度/冻结已豁免)"
            else:
                msg = f"{topic}: 关节数值正常 ({ti.count}条消息)"

        return CheckResult(
            name=f"content_joint_{topic}",
            passed=passed, severity=severity, message=msg, events=events,
            detail={
                "total_count": ti.count,
                "hand_semantics": "exempt" if hand_exempt else "default",
                "changes": changes,
                "mutation_points": mutation_points,
                "freeze_segments": freeze_segments,
            },
        )

    def _check_joint_value(self, d: dict, index: int, topic: str = "") -> List[ContentEvent]:
        """检查单帧关节数值（NaN/Inf + 限位越界）"""
        events = []
        for arr_name in ("position", "velocity", "effort"):
            arr = d.get(arr_name) or []
            for j, v in enumerate(arr):
                if v is None or not math.isfinite(v):
                    events.append(ContentEvent(
                        type="joint_nan", severity="error", topic="", index=index,
                        detail={"joint": j, "field": arr_name, "value": v}))

        # 限位越界（由变体 profile 注入的 joint_limits）
        limits = self.config.joint_limits.get(topic) or {}
        if limits:
            pmin = limits.get("position_min")
            pmax = limits.get("position_max")
            if pmin is not None or pmax is not None:
                pos = d.get("position") or []
                for j, v in enumerate(pos):
                    if v is None or not math.isfinite(v):
                        continue
                    if (pmin is not None and v < pmin) or (pmax is not None and v > pmax):
                        events.append(ContentEvent(
                            type="joint_bounds", severity="error", topic="", index=index,
                            detail={"joint": j, "value": v, "min": pmin, "max": pmax}))
        return events

    def _check_joint_cmd(
        self,
        topic: str,
        ti,
        samples: List[Tuple[int, dict]],
        full_iter: Optional[Iterator[dict]],
    ) -> CheckResult:
        events: List[ContentEvent] = []
        changes: List[dict] = []
        freeze_segments: List[dict] = []
        # 手部通道语义豁免：同 _check_joint_state（A5 jerk/A7 冻结按机械臂
        # 设计；NaN/越界保留；断流由 A1-A4 时序兜底）
        hand_exempt = _is_hand_channel(topic)

        prev_q = None
        last_hash = None
        freeze_count = 0
        freeze_start = None
        last_idx = -1
        # A5: jerk 归一化用实测中位帧间隔（与 J5 一致）；无时间戳回退 30Hz
        dt = self._frame_dt_sec(ti)

        if full_iter is not None:
            for idx, d in enumerate(full_iter):
                if idx >= ti.count:
                    break  # 时间轴短于数据行时，只扫描声明范围内的帧（防事件 index 越界）
                last_idx = idx
                events.extend(self._check_cmd_value(d, idx, topic))

                q = d.get("joint_q") or d.get("position") or []
                tau = d.get("tau") or []

                # A5: 抖动检测（手部豁免）
                if not hand_exempt and q and prev_q is not None and len(prev_q) == len(q):
                    jerk = np.abs(np.asarray(q, dtype=float) - np.asarray(prev_q, dtype=float)) / dt
                    if np.any(jerk > self.config.jerk_threshold):
                        max_jerk = float(np.max(jerk))
                        events.append(ContentEvent(
                            type="cmd_jerk", severity="warning",
                            topic=topic, index=idx,
                            detail={"max_jerk": round(max_jerk, 2),
                                    "threshold": self.config.jerk_threshold}))
                        changes.append({"index": idx, "type": "jerk_exceed",
                                        "detail": {"max_jerk": round(max_jerk, 2)}})
                prev_q = q

                # A7: 冻结检测（完整维度 + 量化容差，E13 同 joint；手部豁免）
                if hand_exempt:
                    continue
                if q:
                    q_q = ",".join(f"{float(x):.4f}" for x in q)
                    t_q = ",".join(f"{float(x):.4f}" for x in (tau or []))
                    h = f"{q_q}_{t_q}"
                else:
                    h = None
                if h is not None and h == last_hash:
                    freeze_count += 1
                    if freeze_start is None:
                        freeze_start = idx
                else:
                    if freeze_count >= self.config.cmd_freeze_low_frames:
                        freeze_segments.append({"start": freeze_start, "end": idx - 1, "count": freeze_count})
                        sev = "error" if freeze_count >= self.config.cmd_freeze_high_frames else "warning"
                        events.append(ContentEvent(
                            type="data_freeze", severity=sev,
                            topic=topic, index=freeze_start,
                            detail={"count": freeze_count, "range": [freeze_start, idx - 1]},
                        ))
                    freeze_count = 0
                    freeze_start = None
                last_hash = h

        # A7: 末尾冻结段 flush（冻结延伸到流末尾时在循环内不触发 else 分支）
        if freeze_count >= self.config.cmd_freeze_low_frames:
            freeze_segments.append({"start": freeze_start, "end": last_idx, "count": freeze_count})
            sev = "error" if freeze_count >= self.config.cmd_freeze_high_frames else "warning"
            events.append(ContentEvent(
                type="data_freeze", severity=sev,
                topic=topic, index=freeze_start,
                detail={"count": freeze_count, "range": [freeze_start, last_idx]},
            ))

        # 采样点补充检查：全流不可用（reader 不支持 iter_values）时退化为按采样帧检测
        if full_iter is None:
            for idx, d in samples:
                events.extend(self._check_cmd_value(d, idx, topic))

        has_bounds = any(e.type == 'cmd_bounds' for e in events)
        has_nan = any(e.type == 'cmd_nan' for e in events)
        has_jerk = any(e.type == 'cmd_jerk' for e in events)
        has_freeze = any(e.type == 'data_freeze' for e in events)

        if has_nan or has_bounds:
            severity = "error"; passed = False
            msg = f"{topic}: cmd 数值异常 (NaN/Inf/越界)"
        elif has_freeze:
            severity = ("error" if any(e.severity == "error" for e in events
                                       if e.type == "data_freeze") else "warning")
            passed = False
            msg = f"{topic}: cmd 冻结检测到 {len(freeze_segments)} 段"
        elif has_jerk:
            severity = "warning"; passed = False
            msg = f"{topic}: cmd 抖动过强 ({sum(1 for e in events if e.type=='cmd_jerk')}次)"
        else:
            severity = "pass"; passed = True
            if hand_exempt:
                msg = f"{topic}: cmd 数值正常 ({ti.count}条消息；手部通道，抖动/冻结已豁免)"
            else:
                msg = f"{topic}: cmd 数值正常 ({ti.count}条消息)"

        return CheckResult(
            name=f"content_cmd_{topic}",
            passed=passed, severity=severity, message=msg, events=events,
            detail={
                "total_count": ti.count,
                "hand_semantics": "exempt" if hand_exempt else "default",
                "changes": changes,
                "freeze_segments": freeze_segments,
            },
        )

    def _check_cmd_value(self, d: dict, index: int, topic: str = "") -> List[ContentEvent]:
        """检查单帧 cmd 数值（NaN/Inf + 限位越界）"""
        events = []
        for arr_name in ("joint_q", "joint_v", "tau", "position"):
            arr = d.get(arr_name) or []
            for j, v in enumerate(arr):
                if v is None or not math.isfinite(v):
                    events.append(ContentEvent(
                        type="cmd_nan", severity="error", topic="", index=index,
                        detail={"joint": j, "field": arr_name, "value": v}))

        limits = self.config.joint_limits.get(topic) or {}
        if limits:
            pmin = limits.get("position_min")
            pmax = limits.get("position_max")
            if pmin is not None or pmax is not None:
                q = d.get("joint_q") or d.get("position") or []
                for j, v in enumerate(q):
                    if v is None or not math.isfinite(v):
                        continue
                    if (pmin is not None and v < pmin) or (pmax is not None and v > pmax):
                        events.append(ContentEvent(
                            type="cmd_bounds", severity="error", topic="", index=index,
                            detail={"joint": j, "value": v, "min": pmin, "max": pmax}))
        return events

    # ---------- 末端位姿独立检查器（pose 模态） ----------

    _FREEZE_SAME_EPS = 1e-6

    @staticmethod
    def _infer_pose_semantics(stacked: np.ndarray) -> dict:
        """位姿布局语义推断（纯函数，全量数组统计）。

        无名数值数组（parquet/hdf5 裸列）没有字段识别码，按数学特征推断：
        1. 四元数路标：单位四元数满足 4 维切片范数恒 ≈1（唯一可靠特征），
           候选只取两种惯例布局——xyz 在前（quat=[3:7]，ROS Pose 序）与
           quat 在前（quat=[0:4]）；按范数达标行占比取高分者，并列取 xyz 在前；
        2. 排除法定位平移：路标之外恰有一段连续 3 维 → xyz（N=3 直接判；
           其余维数如 6 维欧拉角无路标 → unknown，不猜）；
        3. 单位归一：max|xyz| ∈ (50, 5000] → mm 源 ×0.001 换算成米。

        返回 {"layout", "xyz": (lo,hi), "quat": (lo,hi)|None, "extra": (lo,hi)|None,
              "scale", "raw_ndim"}；推不出 → layout="unknown"、xyz=全维（透传，
        与修复前行为一致，仅 detail 标注供审计）。
        """
        nd = stacked.shape[1] if stacked.ndim == 2 else 0
        sem = {"layout": "unknown", "xyz": (0, nd), "quat": None,
               "extra": None, "scale": 1.0, "raw_ndim": nd}

        def _unit_frac(lo: int) -> float:
            if lo + 4 > nd:
                return 0.0
            qn = np.linalg.norm(stacked[:, lo:lo + 4], axis=1)
            return float(np.mean((qn > 0.9) & (qn < 1.1)))

        if nd == 3:
            sem["layout"] = "xyz"          # pose 通道 3 个数无第二种解释
        elif nd in (7, 9):
            f_mid, f_head = _unit_frac(3), _unit_frac(0)
            if f_mid >= 0.95 and f_mid >= f_head:
                qlo, xlo = 3, 0            # [xyz, quat, extra?]
            elif f_head >= 0.95:
                qlo, xlo = 0, 4            # [quat, xyz, extra?]
            else:
                return sem                 # 无四元数路标（如欧拉角布局）→ 不猜
            sem["quat"] = (qlo, qlo + 4)
            sem["xyz"] = (xlo, xlo + 3)
            if nd == 9:
                sem["extra"] = (7, 9)      # 两种 9 维布局的尾段相同
                sem["layout"] = "xyz_quat_extra" if xlo == 0 else "quat_xyz_extra"
            else:
                sem["layout"] = "xyz_quat" if xlo == 0 else "quat_xyz"

        if sem["layout"] != "unknown":
            xyz = stacked[:, sem["xyz"][0]:sem["xyz"][1]]
            mx = float(np.abs(xyz).max()) if xyz.size else 0.0
            if 50.0 < mx <= 5000.0:
                sem["scale"] = 0.001       # mm 源 → 米
        return sem

    @staticmethod
    def _pose_arr(d: dict) -> Optional[np.ndarray]:
        """从规范值字典取位置向量（position / joint_q），转 float ndarray"""
        pos = d.get("position") or d.get("joint_q")
        if not pos:
            return None
        try:
            arr = np.asarray(pos, dtype=float)
        except (TypeError, ValueError):
            return None
        if arr.ndim != 1 or arr.size < 3 or not np.all(np.isfinite(arr)):
            return None
        return arr

    def _freeze_lookback_ok(self, xyz: np.ndarray, timestamps: List[float],
                            start: int) -> Tuple[bool, float]:
        """SLAM 式冻结前运动验证（平移切片，米制）。

        冻结段起点 start 之前 lookback 窗口内的平均帧间速度 > 阈值 → 真冻结
        （机器人之前在动，突然位姿不变 = 传感器故障）；
        否则视为自然静止（减速停稳/一直没动），不报。

        速度按真实秒归一（timestamps 与 xyz 同滤同长，单位 m/s）。
        返回 (真冻结?, 回看窗口平均速度 m/s)。start=0（从一开始就冻结）视为自然静止。
        """
        if start <= 0:
            return False, 0.0
        lookback = max(5, self.config.pose_freeze_lookback)
        lo = max(0, start - lookback)
        pre = xyz[lo:start]
        if len(pre) < 2:
            return False, 0.0
        diffs = np.linalg.norm(np.diff(pre, axis=0), axis=1)
        # diffs 覆盖的精确时间跨度：pre 行 [lo, start-1]，diffs 为 lo→start-1 共
        # start-1-lo 个间隔（span/diff 数严格配对，防均匀采样下 dt 偏大 1/N）
        if len(timestamps) > start - 1 >= lo:
            span = float(timestamps[start - 1] - timestamps[lo])
        else:
            span = float(len(diffs))
        avg_dt = span / len(diffs) if len(diffs) and span > 0 else 1.0
        pre_vel = float(np.mean(diffs)) / avg_dt
        return pre_vel > self.config.pose_freeze_vel_threshold, pre_vel

    def _emit_freeze_with_lookback(
        self, events: List[ContentEvent], freeze_segments: List[dict],
        topic: str, freeze_start: int, freeze_end: int, freeze_count: int,
        xyz: np.ndarray, timestamps: List[float],
        min_run: int, cut_run: int,
    ) -> None:
        """冻结段事件发射（含 SLAM lookback 验证）。

        连续相同位姿 ≥ min_run → 冻结候选 → lookback 验证冻结前是否在运动：
        - pre_vel > 阈值 → 真冻结，报 data_freeze（warning <cut_run / error ≥cut_run）
        - 否则 → 自然静止，不报（motion=static 记录在 detail 供参考）

        xyz 为平移切片（米制，3 维）；冻结候选本身由全维原始向量的
        run-length 判定（"整条消息陈旧"语义），本函数只做运动闸门与发射。
        """
        if freeze_count < min_run:
            return
        ok, pre_vel = self._freeze_lookback_ok(xyz, timestamps, freeze_start)
        if not ok:
            freeze_segments.append({"start": freeze_start, "end": freeze_end,
                                    "count": freeze_count, "motion": "static"})
            return
        sev = "error" if freeze_count >= cut_run else "warning"
        freeze_segments.append({"start": freeze_start, "end": freeze_end,
                                "count": freeze_count, "motion": "moving",
                                "pre_velocity": round(pre_vel, 5)})
        events.append(ContentEvent(
            type="data_freeze", severity=sev,
            topic=topic, index=freeze_start,
            detail={"count": freeze_count, "range": [freeze_start, freeze_end],
                    "pre_velocity": round(pre_vel, 5), "motion": "moving"},
        ))

    def _check_home_reset(self, topic: str, xyz_rows: List[np.ndarray],
                          timestamps: List[float],
                          sem: Optional[dict] = None,
                          raw_positions: Optional[List[np.ndarray]] = None) -> List[ContentEvent]:
        """首末帧复位检测：first/last 帧 vs profile home_position，双阈值评分。

        home 双约定（均按米比较）：
        - 长度 == 平移维数（3）→ 视作米制 home，直接比较；
        - 长度 == 原始通道维数（如 9）→ 按推断布局切片 + 同单位换算
          （兼容按源数据原始布局/单位书写的 profile）。
        xyz_rows 为平移切片行（布局未知时为原始全维行，home 按原始维数匹配）。

        平移检查：
        - error = per-dim |frame - home|
        - error ≤ tolerance1 → 达标（不报）
        - error ≥ tolerance2 → pose_reset error（复位失败）
        - 中间 → pose_reset warning（部分偏离）

        姿态检查（四元数，需 raw_positions + sem["quat"] + home 覆盖 quat 维）：
        - 双方归一化后 2·arccos(|dot|) → 角度差（rad）
        - 双阈值同平移逻辑，默认 0.2/0.5 rad
        """
        events: List[ContentEvent] = []
        home = self.config.home_position.get(topic)
        if home is None or not xyz_rows:
            return events
        home_arr = np.asarray(home, dtype=float)
        if sem is not None and sem["layout"] != "unknown" \
                and home_arr.size == sem["raw_ndim"]:
            home_arr = home_arr[sem["xyz"][0]:sem["xyz"][1]] * sem["scale"]
        for frame_tag, idx in (("first", 0), ("last", len(xyz_rows) - 1)):
            pos = xyz_rows[idx]
            if pos.shape != home_arr.shape:
                continue
            err = np.abs(pos - home_arr)
            err_max = float(err.max())
            t1 = self.config.pose_reset_tolerance1
            t2 = self.config.pose_reset_tolerance2
            if err_max <= t1:
                continue
            score = max(0.0, 100.0 * (t2 - err_max) / (t2 - t1))
            sev = "error" if err_max >= t2 else "warning"
            events.append(ContentEvent(
                type="pose_reset", severity=sev, topic=topic, index=idx,
                time=timestamps[idx] if timestamps else 0.0,
                detail={
                    "frame": frame_tag, "error_max": round(err_max, 4),
                    "error": [round(float(e), 4) for e in err],
                    "tolerance1": t1, "tolerance2": t2,
                    "score": round(score, 1),
                }))

        # ---- 姿态检查（四元数）----
        if raw_positions is not None and sem is not None and sem.get("quat") is not None:
            qlo, qhi = sem["quat"]
            raw_home = self.config.home_position.get(topic)
            if raw_home and len(raw_home) > qhi:
                rot_t1 = self.config.pose_reset_rot_tolerance1
                rot_t2 = self.config.pose_reset_rot_tolerance2
                for frame_tag, idx in (("first", 0), ("last", len(xyz_rows) - 1)):
                    raw_pos = raw_positions[idx]
                    if len(raw_pos) <= qhi:
                        continue
                    q_frame = np.asarray(raw_pos[qlo:qhi], dtype=float)
                    q_home = np.asarray(raw_home[qlo:qhi], dtype=float)
                    nf, nh = float(np.linalg.norm(q_frame)), float(np.linalg.norm(q_home))
                    if nf <= 0 or nh <= 0:
                        continue
                    q_f_n = q_frame / nf
                    q_h_n = q_home / nh
                    dot = float(np.dot(q_f_n, q_h_n))
                    dot = max(0.0, min(1.0, abs(dot)))
                    rot_err = float(2.0 * math.acos(dot))
                    if rot_err <= rot_t1:
                        continue
                    rot_score = max(0.0, 100.0 * (rot_t2 - rot_err) / (rot_t2 - rot_t1))
                    rot_sev = "error" if rot_err >= rot_t2 else "warning"
                    existing = [e for e in events if e.type == "pose_reset" and e.index == idx]
                    if existing:
                        ex = existing[0]
                        ex.detail.update({
                            "rot_error": round(rot_err, 4),
                            "rot_tolerance1": rot_t1, "rot_tolerance2": rot_t2,
                            "rot_score": round(rot_score, 1),
                        })
                        if rot_sev == "error" or ex.severity == "warning":
                            ex.severity = rot_sev
                    else:
                        events.append(ContentEvent(
                            type="pose_reset", severity=rot_sev, topic=topic, index=idx,
                            time=timestamps[idx] if timestamps else 0.0,
                            detail={
                                "frame": frame_tag,
                                "rot_error": round(rot_err, 4),
                                "rot_tolerance1": rot_t1, "rot_tolerance2": rot_t2,
                                "rot_score": round(rot_score, 1),
                            }))

        return events

    @staticmethod
    def _emit_jerk_run(events: List[ContentEvent], topic: str,
                       r_idx: List[int], r_ts: List[float],
                       jerk_rms: float, threshold: float,
                       severity: str = "warning") -> None:
        """一段连续抖动帧 → 段事件（与图像 _emit_run 同格式：range + duration_sec）"""
        if not r_idx:
            return
        dur = (r_ts[-1] - r_ts[0]) if len(r_ts) > 1 else 0.0
        events.append(ContentEvent(
            type="pose_jerk", severity=severity, topic=topic, index=r_idx[0],
            time=r_ts[0] if r_ts else 0.0,
            detail={
                "range": [r_idx[0], r_idx[-1]],
                "duration_sec": round(dur, 3),
                "count": len(r_idx),
                "jerk_rms": round(jerk_rms, 2),
                "threshold": threshold,
            }))

    def _check_pose_jitter(self, topic: str, positions: np.ndarray,
                           timestamps: List[float]) -> List[ContentEvent]:
        """位姿抖动：Savitzky-Golay 残差 jerk（参考 Action_Smoothness）。

        1. SG 平滑（window=11, polyorder=3）→ 残差 = 原始 - 平滑
        2. 速度阈值识别静止段（vel < 0.2×max_vel）+ 形态学滤波去碎片
        3. 残差三阶差分 → jerk，仅静止段计入
        4. 连续超标帧 run-length 合并为段事件 pose_jerk
        """
        events: List[ContentEvent] = []
        n, ndim = positions.shape
        if n < 11 or ndim < 3:
            return events, np.zeros(n, dtype=bool), np.zeros(n)
        try:
            from scipy.signal import savgol_filter
        except ImportError:
            return events, np.zeros(n, dtype=bool), np.zeros(n)
        window = min(self.config.pose_jerk_window, n)
        if window % 2 == 0:
            window -= 1
        if window <= self.config.pose_jerk_polyorder + 1:
            return events, np.zeros(n, dtype=bool), np.zeros(n)
        ts = np.asarray(timestamps, dtype=float) if timestamps else np.arange(n, dtype=float)
        dt = np.diff(ts)
        pos_dt = dt[dt > 0]
        avg_dt = float(np.mean(pos_dt)) if pos_dt.size else 1.0
        if avg_dt <= 0:
            avg_dt = 1.0

        smooth = np.zeros_like(positions)
        for ax in range(ndim):
            smooth[:, ax] = savgol_filter(positions[:, ax], window, self.config.pose_jerk_polyorder)
        residual = positions - smooth

        vel = np.zeros_like(positions)
        for ax in range(ndim):
            vel[:, ax] = np.gradient(smooth[:, ax]) / avg_dt
        vel_mag = np.linalg.norm(vel, axis=1)
        max_vel = float(vel_mag.max())
        if max_vel <= 0:
            return events, np.zeros(n, dtype=bool), np.zeros(n)
        # 静止判定：相对线（10%×峰值）与绝对线的最小值——快通道慢滑行
        # 不会被误判静止（min 语义打消慢速数据集全程不检测的顾虑）
        static_line = self.config.pose_jerk_vel_factor * max_vel
        if self.config.pose_jerk_static_vel > 0:
            static_line = min(static_line, self.config.pose_jerk_static_vel)
        stationary = vel_mag < static_line

        min_seg = max(int(0.01 * n), self.config.pose_jerk_min_segment)
        min_seg = min(min_seg, n)
        if min_seg >= 3:
            if min_seg % 2 == 0:
                min_seg -= 1
            try:
                from scipy.ndimage import binary_erosion, binary_dilation
                structure = np.ones(min_seg, dtype=bool)
                eroded = binary_erosion(stationary, structure=structure)
                stationary = stationary & binary_dilation(eroded, structure=structure)
            except ImportError:
                pass
        if not stationary.any():
            return events, stationary, np.zeros(n)

        jerk = residual
        for _ in range(3):
            jerk = np.gradient(jerk, axis=0) / avg_dt
        jerk_mag = np.linalg.norm(jerk, axis=1)

        thr = self.config.pose_jerk_threshold
        thr_err = self.config.pose_jerk_error
        # 双档：warning 线（P80 满分线 5）超阈 + error 线（熔断线 15）超阈
        # 合并为单一 exceed 掩码进行 run-length 合并，最终按段 rms 分档
        exceed = stationary & (jerk_mag > thr)
        gap = max(2, self.config.pose_jerk_min_segment // 2)
        if gap >= 1 and n > gap * 2:
            filled = exceed.copy()
            for off in range(1, gap + 1):
                filled[off:] |= exceed[:-off]
                filled[:-off] |= exceed[off:]
            exceed = filled
        runs: List[Tuple[List[int], List[float], List[float]]] = []
        r_idx: List[int] = []
        r_ts: List[float] = []
        r_sq: List[float] = []
        for i in range(n):
            if exceed[i]:
                r_idx.append(i)
                r_ts.append(float(ts[i]))
                r_sq.append(float(jerk_mag[i]) ** 2)
            else:
                if r_idx:
                    runs.append((r_idx, r_ts, r_sq))
                r_idx, r_ts, r_sq = [], [], []
        if r_idx:
            runs.append((r_idx, r_ts, r_sq))
        for m_idx, m_ts, m_sq in runs:
            rms = float(np.sqrt(np.mean(m_sq))) if m_sq else 0.0
            sev = "error" if rms >= thr_err else "warning"
            self._emit_jerk_run(events, topic, m_idx, m_ts, rms, thr, sev)
        return events, stationary, jerk_mag

    def _check_pose(
        self,
        topic: str,
        ti,
        samples: List[Tuple[int, dict]],
        full_iter: Optional[Iterator[dict]],
    ) -> CheckResult:
        """末端位姿检查器：NaN + lookback 冻结 + SG 残差抖动 + 首末帧复位。

        与 joint_state 分离的原因：末端位姿是 3D 位置(+四元数)，单位 m，
        阈值体系与关节角(rad)不同；且抖动检测（SG 残差 jerk）只针对末端位姿。

        布局语义：无名数组先推断（四元数路标 + 排除法 xyz + mm 判定，
        见 _infer_pose_semantics）；速度/jerk/复位只消费平移切片（米制），
        冻结用原始全维（整条消息陈旧语义）；识别失败退回全维透传。
        """
        events: List[ContentEvent] = []
        freeze_segments: List[dict] = []
        positions: List[np.ndarray] = []
        timestamps: List[float] = []
        # 时间轴（秒）：ti.timestamps 优先；缺失/越界时均匀网格 idx×实测中位 dt
        # （回退 30Hz）——速度/jerk 单位语义统一 m/s 与 m/s³（此前回退帧号导致
        # lookback 速度差 30×、jerk 差 27000×，检测形同虚设）
        _ts = getattr(ti, "timestamps", None) or []
        _dt = self._frame_dt_sec(ti)

        if full_iter is not None:
            for idx, d in enumerate(full_iter):
                if idx >= ti.count:
                    break
                pos_raw = d.get("position") or []
                for j, v in enumerate(pos_raw):
                    if v is None or not math.isfinite(v):
                        events.append(ContentEvent(
                            type="pose_nan", severity="error", topic=topic, index=idx,
                            detail={"dim": j, "value": v}))
                arr = self._pose_arr(d)
                if arr is not None:
                    positions.append(arr)
                    if idx < len(_ts):
                        timestamps.append(float(_ts[idx]))
                    else:
                        timestamps.append(idx * _dt)

        n = len(positions)
        sem: Optional[dict] = None
        xyz_arr: Optional[np.ndarray] = None
        _jerk_skipped = None
        _jerk_stationary_rms: Optional[float] = None
        if n >= 2:
            stacked = np.vstack(positions)

            # ---- 布局/单位推断（四元数路标 + 排除法 xyz + mm 判定）----
            sem = self._infer_pose_semantics(stacked)
            xyz_arr = stacked[:, sem["xyz"][0]:sem["xyz"][1]] * sem["scale"]

            # ---- 冻结候选：原始全维 run-length（"整条消息陈旧"语义）----
            # 时间制门槛（1s/5s 对齐 I/J/A/C 族统一惯例，参考 Action_Smoothness）
            # 与发布节奏自适应下限取 max（fill-forward 源防误报）
            dt_est = (timestamps[1] - timestamps[0]) if len(timestamps) > 1 else _dt
            if dt_est <= 0:
                dt_est = _dt
            min_run = max(3, int(math.ceil(
                self.config.pose_freeze_min_sec / dt_est)))
            cut_run = int(math.ceil(
                self.config.pose_freeze_cut_sec / dt_est))
            diffs = np.linalg.norm(np.diff(stacked, axis=0), axis=1)
            is_same = diffs < self._FREEZE_SAME_EPS
            gap_rows = None
            change_rows = np.where(~is_same)[0] + 1
            if len(change_rows) >= 5:
                gap_rows = round(float(np.median(np.diff(change_rows))), 2)
                min_run = max(min_run, int(math.ceil(
                    self.config.pose_freeze_gap_factor * gap_rows)))
            run_len = 0
            run_start = 0
            for i, same in enumerate(is_same):
                if same:
                    if run_len == 0:
                        run_start = i + 1
                    run_len += 1
                else:
                    if run_len >= min_run:
                        self._emit_freeze_with_lookback(
                            events, freeze_segments, topic, run_start,
                            run_start + run_len - 1, run_len, xyz_arr, timestamps,
                            min_run, cut_run)
                    run_len = 0
            if run_len >= min_run:
                self._emit_freeze_with_lookback(
                    events, freeze_segments, topic, run_start, n - 1,
                    run_len, xyz_arr, timestamps, min_run, cut_run)

            # ---- 抖动：平移切片（米制 m/s³；手爪/姿态维不再混入）----
            # 布局未识别（如四元数未归一的 9 维源）→ 全维含非米制维，
            # jerk 语义不成立 → 跳过（冻结/NaN 不受影响：冻结比整行
            # 是否复制粘贴，不需要知道每维含义）
            if sem["layout"] == "unknown" and sem["raw_ndim"] > 3:
                _jerk_skipped = "layout_unknown"
            else:
                _jerk_skipped = None
                _jerk_ev, _jerk_stationary_mask, _jerk_mag = \
                    self._check_pose_jitter(topic, xyz_arr, timestamps)
                events.extend(_jerk_ev)
                # check 级打分：参考 Action_Smoothness 口径——全静止帧 RMS
                if _jerk_stationary_mask is not None and _jerk_stationary_mask.any():
                    _jerk_stationary_rms = float(
                        np.sqrt(np.mean(_jerk_mag[_jerk_stationary_mask] ** 2)))

        # ---- 首末帧复位：平移切片 + home 双约定 ----
        if sem is not None:
            xyz_rows = [xyz_arr[i] for i in range(n)]
        else:
            xyz_rows = positions   # n<2 无从推断布局：按原始数组（home 按原始维数匹配）
        events.extend(self._check_home_reset(topic, xyz_rows, timestamps, sem,
                                                       raw_positions=positions))

        has_nan = any(e.type == 'pose_nan' for e in events)
        has_freeze = any(e.type == 'data_freeze' for e in events)
        has_jerk = any(e.type == 'pose_jerk' for e in events)
        has_reset = any(e.type == 'pose_reset' for e in events)

        if has_nan:
            severity = "error"; passed = False
            msg = f"{topic}: 位姿数值异常 (NaN/Inf)"
        elif has_freeze:
            severity = ("error" if any(e.severity == "error" for e in events
                                       if e.type == "data_freeze") else "warning")
            passed = False
            msg = f"{topic}: 位姿冻结 {len(freeze_segments)} 段"
        elif has_reset:
            severity = ("error" if any(e.severity == "error" for e in events
                                       if e.type == "pose_reset") else "warning")
            passed = False
            n_r = sum(1 for e in events if e.type == 'pose_reset')
            msg = f"{topic}: 首末帧偏离 home_position ({n_r}处)"
        elif has_jerk:
            severity = ("error" if any(e.severity == "error" for e in events
                                       if e.type == "pose_jerk") else "warning")
            passed = False
            parts = []
            for e in (x for x in events if x.type == 'pose_jerk'):
                rng = e.detail.get("range", [0, 0])
                parts.append(f"[{rng[0]}-{rng[1]}]")
            msg = f"{topic}: 位姿抖动 {len(parts)} 段 ({', '.join(parts[:5])})"
        else:
            severity = "pass"; passed = True
            msg = f"{topic}: 位姿检测正常 ({ti.count}条消息)"

        _detail = {
            "total_count": ti.count,
            "frame_count": n,
            "freeze_segments": freeze_segments,
            "pose_layout": (sem or {}).get("layout", "unknown"),
            "unit_scale": (sem or {}).get("scale", 1.0),
        }
        if n >= 2:
            _detail["publisher_gap_rows"] = gap_rows
            if _jerk_skipped:
                _detail["jerk_skipped"] = _jerk_skipped
            if _jerk_stationary_rms is not None:
                _detail["stationary_jerk_rms"] = round(_jerk_stationary_rms, 2)
                # 0-100 线性评分（Action_Smoothness P80 满分线 5，熔断线 15）
                _detail["jerk_score"] = round(
                    max(0.0, min(100.0,
                        100.0 * (self.config.pose_jerk_error - _jerk_stationary_rms)
                        / (self.config.pose_jerk_error - self.config.pose_jerk_threshold))), 1)
        return CheckResult(
            name=f"content_pose_{topic}",
            passed=passed, severity=severity, message=msg, events=events,
            detail=_detail,
        )
