#!/usr/bin/env python3
"""
Robo-Dopamine 轨迹回退归因系统 (Regression Attribution System v3.1)

架构：三层混合
  Layer 1: 全局向量化特征提取 (NumPy)
  Layer 2: 相对波峰波谷迟滞状态机 (Schmitt Trigger)
  Layer 3: 窗口归因决策树

使用：
  1. 作为模块导入：from regression_attribution import EpisodeAttributor, AttributeResult
  2. 命令行 CLI：python regression_attribution.py --episode <路径> --assessment <json>
"""

import json
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

import numpy as np


ATTRIBUTION_RULE_VERSION = "progress_attribution_v9"
PROGRESS_RECOVERY_TOLERANCE = 0.03
PROGRESS_RECOVERY_MIN_CONSECUTIVE = 2
CONFIRMED_DROP_LABELS = {"object_drop_by_release"}


def _is_progress_recovered(pre_drop_prog: float, post_progs: list[float],
                           final_subtask_progress: float = 0.0) -> bool:
    """Return whether progress returned to its pre-regression level.

    A recovery must persist for two consecutive samples.  This preserves
    single-sample regression detection while preventing one rebound sample
    from clearing a real regression.  At the end of a subtask, where fewer
    than two post samples exist, the final subtask score is the fallback.
    """
    threshold = max(0.0, float(pre_drop_prog) - PROGRESS_RECOVERY_TOLERANCE)
    consecutive = 0
    for value in post_progs:
        if float(value) >= threshold:
            consecutive += 1
            if consecutive >= PROGRESS_RECOVERY_MIN_CONSECUTIVE:
                return True
        else:
            consecutive = 0
    if len(post_progs) < PROGRESS_RECOVERY_MIN_CONSECUTIVE:
        return float(final_subtask_progress) >= 0.8
    return False


# =============================================================================
# 1. 输出数据结构
# =============================================================================
@dataclass
class AttributeResult:
    """单臂单回退帧的归因结果"""
    frame: int
    arm: str
    primary_label: str
    severity: int
    recovery_action: Optional[str] = None
    raw_evidence: dict = field(default_factory=dict)


# =============================================================================
# 2. Layer 1: 全局向量化特征提取
# =============================================================================
def extract_gripper_sequence(data_list: list[dict], arm: str) -> np.ndarray:
    """提取夹爪开合序列"""
    key = f"follow_{arm}_gripper"
    return np.array([float(f.get(key, 0.0)) for f in data_list], dtype=np.float64)


def compute_gripper_velocity(grip: np.ndarray, fps: float, *, timeline=None) -> np.ndarray:
    """夹爪开合速度（一阶差分）"""
    vel = np.zeros_like(grip)
    if timeline is not None and not timeline.matches_playback:
        vel[1:] = np.abs(np.diff(grip)) / np.diff(timeline.times)
    else:
        vel[1:] = np.abs(np.diff(grip)) * fps
    return vel


def compute_joint_current_rms(data_list: list[dict]) -> dict[str, np.ndarray]:
    """关节电流 RMS：每帧 follow_*_joint_cur 的均方根"""
    out: dict[str, np.ndarray] = {}
    for arm, key in (("left", "follow_left_joint_cur"), ("right", "follow_right_joint_cur")):
        series = []
        for f in data_list:
            cur = f.get(key, [])
            try:
                arr = np.asarray(cur, dtype=np.float64)
                series.append(float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0)
            except (TypeError, ValueError):
                series.append(0.0)
        out[arm] = np.array(series)
    return out


def compute_end_effector_speed(data_list: list[dict], fps: float, *, timeline=None) -> dict[str, np.ndarray]:
    """末端线速度：||position[i] - position[i-1]|| / dt"""
    dt = np.diff(timeline.times) if timeline is not None and not timeline.matches_playback else 1.0 / fps
    out: dict[str, np.ndarray] = {}
    for arm, key in (("left", "follow_left_position"), ("right", "follow_right_position")):
        pos = np.array([f.get(key, [0.0, 0.0, 0.0]) for f in data_list], dtype=np.float64)
        if pos.ndim != 2 or pos.shape[0] < 2:
            out[arm] = np.zeros(len(data_list))
            continue
        disp = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        speed = np.concatenate([[0.0], disp / dt])
        out[arm] = speed
    return out


# ===== Suboptical (灵巧手) format signal extraction =====

def extract_gripper_from_hand_position(data_list: list[dict], arm: str) -> np.ndarray:
    """Retained symbol for compatibility; hand semantics require a profile."""
    raise ValueError("灵巧手开合不能固定取第 2 维；请使用 IR profile 配置关节和开合方向")


def compute_joint_effort_rms(data_list: list[dict]) -> dict[str, np.ndarray]:
    """灵巧手：follow_*_joint_effort 的 RMS (替代 joint_cur)"""
    out: dict[str, np.ndarray] = {}
    for arm, key in (("left", "follow_left_joint_effort"), ("right", "follow_right_joint_effort")):
        series = []
        for f in data_list:
            eff = f.get(key, [])
            try:
                arr = np.asarray(eff, dtype=np.float64)
                series.append(float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0)
            except (TypeError, ValueError):
                series.append(0.0)
        out[arm] = np.array(series)
    return out


def compute_joint_velocity_speed(data_list: list[dict]) -> dict[str, np.ndarray]:
    """灵巧手：follow_*_joint_velocity[0:6] 的 RMS (排除腕部旋转关节 index 6)"""
    out: dict[str, np.ndarray] = {}
    for arm, key in (("left", "follow_left_joint_velocity"), ("right", "follow_right_joint_velocity")):
        series = []
        for f in data_list:
            vel = f.get(key, [])
            try:
                arr = np.asarray(vel, dtype=np.float64)
                if arr.size >= 6:
                    arr = arr[:6]
                series.append(float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0)
            except (TypeError, ValueError):
                series.append(0.0)
        out[arm] = np.array(series)
    return out


def compute_hand_effort_rms(data_list: list[dict]) -> dict[str, np.ndarray]:
    """灵巧手：follow_*_hand_effort 的 RMS (手指电机电流，用于 Close-event delta)"""
    out: dict[str, np.ndarray] = {}
    for arm, key in (("left", "follow_left_hand_effort"), ("right", "follow_right_hand_effort")):
        series = []
        for f in data_list:
            eff = f.get(key, [])
            try:
                arr = np.asarray(eff, dtype=np.float64)
                series.append(float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0)
            except (TypeError, ValueError):
                series.append(0.0)
        out[arm] = np.array(series)
    return out


class GlobalFeatures:
    """Layer 1 输出：所有臂的全局特征"""
    def __init__(self, data_json: dict[str, Any], fps: float = 20.0, *, timeline=None):
        data_list = data_json.get("data", [])
        self.n = len(data_list)
        self.fps = fps
        self.arms = ["left", "right"]
        if not self.n:
            raise ValueError("物理信号数据为空")
        if timeline is not None and len(timeline.times) != self.n:
            raise ValueError("Physical rows and source frame timestamps must have equal length")
        self.limitations = {arm: [] for arm in self.arms}
        for arm in self.arms:
            if not all(f"follow_{arm}_gripper" in row for row in data_list):
                self.limitations[arm].append("缺少已确认语义的夹爪开合；灵巧手请通过 profile 接入")
            if not all(f"follow_{arm}_joint_cur" in row for row in data_list):
                self.limitations[arm].append("旧 JSON 缺少电流；effort 的单位和阈值须通过 profile 校准")
            if not all(f"follow_{arm}_position" in row for row in data_list):
                self.limitations[arm].append("缺少末端位置；关节速度须使用独立校准阈值")
            for key, dimension in (("joint_cur", None), ("position", 3)):
                try:
                    vectors = [np.asarray(row.get(f"follow_{arm}_{key}", []),float).reshape(-1) for row in data_list]
                    valid = all(v.size and np.isfinite(v).all() and (dimension is None or v.size==dimension) for v in vectors)
                except (ValueError,TypeError):
                    valid = False
                if not valid:
                    self.limitations[arm].append(f"旧 JSON {key} 含空值、非有限值或错误维度")

        # 原始信号 — 自动检测 grasper / suboptical 格式
        self.gripper = {arm: extract_gripper_sequence(data_list, arm) for arm in self.arms}
        # A valid zero gripper position must not trigger a different sensor.
        self.grip_vel = {arm: compute_gripper_velocity(self.gripper[arm], fps, timeline=timeline) for arm in self.arms}

        self.cur_rms = compute_joint_current_rms(data_list)
        # 若 joint_cur 全为零，回退到 joint_effort
        if np.max(np.abs(self.cur_rms["right"])) < 1e-6 and np.max(np.abs(self.cur_rms["left"])) < 1e-6:
            self.cur_rms = compute_joint_effort_rms(data_list)

        self.speed = compute_end_effector_speed(data_list, fps, timeline=timeline)
        # 若 position 全为零，回退到 joint_velocity[0:6] RMS
        if np.max(np.abs(self.speed["right"])) < 1e-6 and np.max(np.abs(self.speed["left"])) < 1e-6:
            self.speed = compute_joint_velocity_speed(data_list)

        # 灵巧手：手指电机电流 RMS (用于 Close-event delta)
        try:
            self.grip_cur_rms = compute_hand_effort_rms(data_list)
            if np.max(np.abs(self.grip_cur_rms["right"])) < 1e-6 and np.max(np.abs(self.grip_cur_rms["left"])) < 1e-6:
                self.grip_cur_rms = self.cur_rms
        except Exception:
            self.grip_cur_rms = self.cur_rms

        # ========== 全局统计量 ==========
        # grip_delta = P95 - P5 (相对行程)，0.675 物理上限兜底
        self.grip_p5 = {arm: np.percentile(self.gripper[arm], 5) for arm in self.arms}
        self.grip_p90 = {arm: np.percentile(self.gripper[arm], 90) for arm in self.arms}
        self.grip_p95 = {arm: np.percentile(self.gripper[arm], 95) for arm in self.arms}
        # grip_delta = max(P95-P5, grip绝对范围的50%, 0.01)
        grip_abs_range = {arm: float(np.max(self.gripper[arm]) - np.min(self.gripper[arm])) for arm in self.arms}
        self.grip_delta = {arm: max(self.grip_p95[arm] - self.grip_p5[arm],
                                     grip_abs_range[arm] * 0.5,
                                     0.01) for arm in self.arms}

        # cur_baseline = 夹爪处于极端位置（<P5 或 >P95）时 cur_rms 中位数
        self.cur_baseline = {}
        self.cur_std = {}
        self.cur_spike_threshold = {}
        for arm in self.arms:
            grip = self.gripper[arm]
            cur = self.cur_rms[arm]
            extreme_mask = (grip <= self.grip_p5[arm]) | (grip >= self.grip_p95[arm])
            if np.any(extreme_mask):
                self.cur_baseline[arm] = float(np.median(cur[extreme_mask]))
                self.cur_std[arm] = float(np.std(cur[extreme_mask]))
            else:
                self.cur_baseline[arm] = float(np.median(cur))
                self.cur_std[arm] = float(np.std(cur))
            self.cur_spike_threshold[arm] = self.cur_baseline[arm] + 1.5 * self.cur_std[arm]

        # 全局 max(grip_vel) 用于 active_open 阈值
        self.max_grip_vel = {arm: float(np.max(self.grip_vel[arm])) for arm in self.arms}

    def window_limitations(self, arm, start, end):
        return self.limitations[arm]


# =============================================================================
# 3. Layer 2: 相对波峰波谷迟滞状态机 (Schmitt Trigger)
# =============================================================================
def detect_grip_events(grip: np.ndarray, grip_vel: np.ndarray,
                       delta: float, p5: float, max_grip_vel: float,
                       margin_ratio: float = 0.15,
                       active_vel_ratio: float = 0.1) -> list[tuple[int, str]]:
    """
    相对波峰波谷迟滞状态机，检测夹爪开合事件。
    """
    n = len(grip)
    margin = margin_ratio * delta
    active_vel_thresh = active_vel_ratio * max_grip_vel if max_grip_vel > 0 else 1e-3

    state = "close"  # 初始态：闭合
    valley = p5  # 初始 valley = p5
    peak = p5 + delta
    events: list[tuple[int, str]] = []

    for i in range(n):
        if state == "close":
            valley = min(valley, grip[i])
            if grip[i] > valley + margin and grip_vel[i] > active_vel_thresh:
                events.append((i, "open"))
                state = "open"
                peak = grip[i]
        else:  # open
            peak = max(peak, grip[i])
            if grip[i] < peak - margin:
                events.append((i, "close"))
                state = "close"
                valley = grip[i]

    return events




# =============================================================================
# 4. Layer 3: 窗口归因与决策树
# =============================================================================
class _SourceTimeWindows:
    """Translate the original 20 FPS rule windows on the source timeline.

    Legacy callers and timelines matching playback retain the original rounded
    frame bounds. Nonuniform source time uses half-open array bounds or an
    inclusive upper event bound explicitly, so an event after the requested
    time cannot enter a window merely because the next frame is nearby.
    """

    def _bound(self, frame, legacy_frames, *, inclusive=False):
        timeline = getattr(self, "timeline", None)
        if timeline is None or timeline.rule_fps is not None:
            direction = 1 if legacy_frames >= 0 else -1
            return frame + direction * self._frames(abs(legacy_frames))
        side = "right" if inclusive else "left"
        bound = timeline.offset(frame, legacy_frames / 20.0, side=side)
        return bound - 1 if inclusive else bound

    def _elapsed(self, start, end):
        timeline = getattr(self, "timeline", None)
        return timeline.elapsed(start, end) if timeline is not None else (end - start) / self.fps

    def _nearest_progress(self, frames, frame):
        timeline = getattr(self, "timeline", None)
        if timeline is None or timeline.rule_fps is not None:
            return min(frames, key=lambda f: abs(f - frame))
        query = timeline.time_at(frame)
        return min(frames, key=lambda f: abs(timeline.time_at(f) - query))


class WindowAnalyzer(_SourceTimeWindows):
    """单臂单回退帧的窗口分析器"""
    def __init__(self, gfeat: GlobalFeatures, grip_events: dict[str, list[tuple[int, str]]],
                 prog_samples: list[dict], fps: float = 20.0, *, timeline=None):
        self.gfeat = gfeat
        self.timeline = timeline
        self.grip_events = grip_events
        self.fps = fps
        self.prog_samples = prog_samples
        self.load_rules = getattr(gfeat, 'load_rules', {
            'unit': 'legacy_native', 'close_delta': 0.3, 'stagnation_empty_delta': 0.2})

        # 构建 frame->progress 映射（用于 prog_recovery）
        self.frame_to_prog = {s["frame_index"]: s["progress"] for s in prog_samples}
        self.prog_frames = sorted(self.frame_to_prog.keys())

    def _frames(self, legacy_frames: int) -> int:
        timeline = getattr(self, 'timeline', None)
        rate = timeline.rule_fps if timeline is not None and timeline.rule_fps is not None else self.fps
        return max(1, round(legacy_frames * rate / 20.0))

    def _limited(self, frame, arm, start, end):
        if hasattr(self.gfeat, "window_limitations"):
            reasons = self.gfeat.window_limitations(arm, start, end)
            if reasons:
                return AttributeResult(frame, arm, "unknown_needs_review", -1,
                    raw_evidence={"limitations": reasons,
                        "physical_sources": self.gfeat.signals.describe()["arms"][arm]
                            if hasattr(self.gfeat, "signals") else {"source":"legacy_json"}})
        return None

    def _get_prog_at_frame(self, frame: int) -> float:
        """找最近的 progress 样本"""
        if not self.prog_frames:
            return 0.0
        nearest = self._nearest_progress(self.prog_frames, frame)
        return self.frame_to_prog[nearest]

    def _events_in_window(self, arm: str, start: int, end: int) -> list[tuple[int, str]]:
        """窗口内的 grip 事件"""
        return [(f, t) for f, t in self.grip_events[arm] if start <= f <= end]

    def _get_window_stat(self, arr: np.ndarray, start: int, end: int) -> dict:
        """窗口内统计"""
        s, e = max(0, start), min(len(arr), end)
        if s >= e:
            return {"mean": 0, "max": 0, "min": 0}
        w = arr[s:e]
        return {"mean": float(np.mean(w)), "max": float(np.max(w)), "min": float(np.min(w))}

    def _detect_secondary_grasp(
        self, arm: str, fail_frame: int, pre_drop_prog: float, prog_at_dip: float
    ) -> Optional[str]:
        """
        在失败帧之后搜索 Close 事件，判断二次抓取成功/尝试。

        成功条件：二次 Close 后 5 秒（100 帧）内进度峰值超过跌落量的中点，
        即 max(prog in [close, close+100]) > (pre_drop_prog + prog_at_dip) / 2。
        """
        search_end = min(self._bound(fail_frame, 100, inclusive=True), self.gfeat.n)
        close_events = [
            (f, t)
            for f, t in self.grip_events[arm]
            if fail_frame < f <= search_end and t == "close"
        ]
        if not close_events:
            return None

        first_close = min(f for f, _ in close_events)
        measure_end = min(self._bound(first_close, 100, inclusive=True), self.gfeat.n)
        timeline = getattr(self, "timeline", None)
        if timeline is not None and timeline.rule_fps is None:
            # Search the recorded progress samples within five source seconds;
            # an FPS-derived stride can skip a recovery on an irregular clock.
            measure_frames = [f for f in self.prog_frames if first_close <= f <= measure_end]
            measure_frames = measure_frames or [first_close]
        else:
            measure_frames = range(first_close, measure_end + 1, self._frames(10))
        prog_after_close = max(self._get_prog_at_frame(f) for f in measure_frames)
        recovery_midpoint = (pre_drop_prog + prog_at_dip) / 2

        if prog_after_close > recovery_midpoint:
            return "secondary_grasp_success"
        return "secondary_grasp_attempt"

    def attribute(self, frame: int, arm: str, progress_context: dict = None) -> AttributeResult:
        """
        对单臂单回退帧执行归因。

        Args:
            frame: 回退帧索引
            arm: 'left' / 'right'
            progress_context: 评估系统预计算的进度上下文，格式：
                {"pre_drop_prog": 0.452, "post_progs": [0.175, 0.188, ...], "samples_available": 7}
                如果为 None，则从 self.frame_to_prog 自行推断（兼容旧模式）。
        """
        result = AttributeResult(frame=frame, arm=arm, primary_label="unknown_needs_review", severity=-1)

        win_start = self._bound(frame, -50)
        win_end = self._bound(frame, 50)

        speed_values = self.gfeat.speed[arm][max(0, win_start):win_end]
        if len(speed_values) and np.isfinite(speed_values).all():
            speed_stat = self._get_window_stat(self.gfeat.speed[arm], win_start, win_end)
            if speed_stat["mean"] < 0.01:
                result.primary_label = "ignore_static_arm"
                result.severity = 0
                result.raw_evidence = {"avg_speed": speed_stat["mean"]}
                return result
        limited = self._limited(frame, arm, self._bound(frame, -50), self._bound(frame, 100, inclusive=True))
        if limited is not None:
            return limited

        # ========== 窗口内特征 ==========
        events_win = self._events_in_window(arm, win_start, self._bound(frame, 50, inclusive=True))
        cur_stat = self._get_window_stat(self.gfeat.cur_rms[arm], win_start, win_end)
        cur_spike = cur_stat["max"] > self.gfeat.cur_spike_threshold[arm]

        # prog_recovery：回到回退前进度（允许3%容差）并连续保持至少2个采样点。
        # 单点回退仍保留；这里只判断回退后是否恢复。
        if progress_context is not None:
            pre_drop_prog = progress_context.get("pre_drop_prog", 0.0)
            post_progs = progress_context.get("post_progs", [])
            final_prog = progress_context.get("final_subtask_progress", 0.0)
            prog_recovery = _is_progress_recovered(
                pre_drop_prog, post_progs, final_prog)
        else:
            # 旧路径：回归帧是"跌落已发生"的帧，pre_drop_prog 应取前一个样本的 progress
            pre_drop_prog = self._get_prog_at_frame(max(0, self._bound(frame, -10)))
            timeline = getattr(self, "timeline", None)
            if timeline is not None and timeline.rule_fps is None:
                post_frames = [self._bound(frame, i * 10, inclusive=True) for i in range(1, 8)]
            else:
                # Preserve the legacy rounded sampling stride on uniform time.
                post_frames = [frame + i * self._frames(10) for i in range(1, 8)]
            post_progs = [self._get_prog_at_frame(f) for f in post_frames]
            prog_recovery = _is_progress_recovered(pre_drop_prog, post_progs)

        # 谷底进度：用于二次抓取恢复判定
        prog_at_dip = min(post_progs) if post_progs else pre_drop_prog

        # kin_vel_drop: 速度骤降（窗口后半均值 < 前半均值的30%）
        speed = self.gfeat.speed[arm]
        s1, e1 = max(0, self._bound(frame, -50)), max(0, frame)
        s2, e2 = max(0, frame), min(len(speed), self._bound(frame, 50))
        kin_vel_drop = False
        if e1 > s1 and e2 > s2:
            vel_half1 = np.mean(speed[s1:e1]) + 1e-6
            vel_half2 = np.mean(speed[s2:e2])
            kin_vel_drop = vel_half2 < 0.3 * vel_half1

        # 预计算：cur 距 baseline 的距离
        cur_near_baseline = abs(cur_stat["mean"] - self.gfeat.cur_baseline[arm]) < self.gfeat.cur_std[arm]
        # cur 是否显著跌落：post-window 均值 < pre-window 均值的 65%
        pre_cur_stat = self._get_window_stat(self.gfeat.cur_rms[arm], max(0, self._bound(frame, -50)), frame)
        post_cur_stat = self._get_window_stat(self.gfeat.cur_rms[arm], frame, self._bound(frame, 50))
        cur_dropped_to_baseline = (post_cur_stat["mean"] < pre_cur_stat["mean"] * 0.65
                                    and pre_cur_stat["mean"] > self.gfeat.cur_baseline[arm] + 2 * self.gfeat.cur_std[arm])

        result.raw_evidence = {
            "events_in_win": events_win,
            "cur_max": float(cur_stat["max"]),
            "cur_baseline": float(self.gfeat.cur_baseline[arm]),
            "cur_spike_threshold": float(self.gfeat.cur_spike_threshold[arm]),
            "cur_spike": bool(cur_spike),
            "cur_dropped_to_baseline": bool(cur_dropped_to_baseline),
            "pre_drop_prog": float(pre_drop_prog),
            "max_post_prog": float(max(post_progs)) if post_progs else 0.0,
            "prog_recovery": bool(prog_recovery),
            "kin_vel_drop": bool(kin_vel_drop),
            "load_rules": self.load_rules,
            "load_sources": getattr(self.gfeat, 'load_evidence', {}).get(arm, {}),
        }

        # ========== 1.B: grip_event == Open ==========
        # Open 必须在回归帧之前（Open 是原因），无 Close 时允许 Open 贴近回归帧（≤3帧）
        has_open = any(t == "open" for f, t in events_win if f <= frame)
        has_close = any(t == "close" for f, t in events_win if f <= frame)
        if not has_open and not has_close:
            has_open = any(t == "open" for f, t in events_win if frame < f <= self._bound(frame, 3, inclusive=True))
        if has_open:
            open_frame = min(f for f, t in events_win if t == "open" and f <= self._bound(frame, 3, inclusive=True))
            prog_at_open = self._get_prog_at_frame(open_frame)

            if not prog_recovery and prog_at_open < 0.8:
                # 宽松恢复：中高进度下只要相对恢复 80% 即算正常释放
                relaxed_recovery = (pre_drop_prog >= 0.2 and max(post_progs, default=0.0) >= 0.8 * pre_drop_prog)
                if relaxed_recovery:
                    result.primary_label = "normal_release_artifact"
                    result.severity = 0
                else:
                    # ========== 掉落确认前置条件（v7，双证据路径） ==========
                    # 路径1 电流负载证据（倒茶类硬件：持物电流明显抬升）
                    pre_open_cur = float(np.mean(self.gfeat.grip_cur_rms[arm][max(0, self._bound(open_frame, -15)):open_frame]))
                    load_evidence = (
                        pre_open_cur > self.gfeat.cur_baseline[arm] + 2 * self.gfeat.cur_std[arm]
                        or cur_stat["max"] > self.gfeat.cur_baseline[arm] + 3 * self.gfeat.cur_std[arm]
                    )
                    # 路径2 早期深跌证据（跨任务：微波炉类电流几乎不随负载变化的硬件）
                    # 子任务早期（pre_drop<=0.35）Open 后最深恢复点仍低于回退前 0.05 以上
                    early_deep_drop = (pre_drop_prog <= 0.35
                                       and (pre_drop_prog - max(post_progs if post_progs else [0.0])) >= 0.05)
                    if load_evidence or early_deep_drop:
                        result.primary_label = "object_drop_by_release"
                        result.severity = 3
                        result.recovery_action = self._detect_secondary_grasp(
                            arm, frame, pre_drop_prog, prog_at_dip
                        )
                    else:
                        result.primary_label = "unconfirmed_drop_by_release"
                        result.severity = 2
                        result.recovery_action = self._detect_secondary_grasp(
                            arm, frame, pre_drop_prog, prog_at_dip
                        )
            else:
                result.primary_label = "normal_release_artifact"
                result.severity = 0

            return result

        # ========== 1.A: grip_event == Close ==========
        has_close = any(t == "close" for f, t in events_win if f <= frame)
        if has_close:
            # 空0: Close 前后 cur 没涨 → 空抓（不管绝对 cur 水平）
            close_frame = max(f for f, t in events_win if t == "close" and f <= frame)
            pre_close_cur = float(np.mean(self.gfeat.grip_cur_rms[arm][max(0, self._bound(close_frame, -15)):close_frame]))
            post_close_cur = float(np.mean(self.gfeat.grip_cur_rms[arm][close_frame:min(len(self.gfeat.grip_cur_rms[arm]), self._bound(close_frame, 15))]))
            close_cur_delta = post_close_cur - pre_close_cur
            result.raw_evidence.update(close_frame=close_frame,
                pre_close_load=pre_close_cur, post_close_load=post_close_cur,
                close_load_delta=close_cur_delta)

            if abs(close_cur_delta) < self.load_rules['close_delta'] and not prog_recovery:  # Close 后 cur 没涨 + 进度未恢复 → 空抓
                result.primary_label = "early_close_air_grasp"
                result.severity = 2
                result.recovery_action = self._detect_secondary_grasp(
                    arm, frame, pre_drop_prog, prog_at_dip
                )
                return result

            if close_cur_delta < -self.load_rules['close_delta'] and not prog_recovery:  # Close 后 cur 下降 + 进度未恢复 → 闭合后卸载
                result.primary_label = "post_close_cur_drop"
                result.severity = 2
                result.recovery_action = self._detect_secondary_grasp(
                    arm, frame, pre_drop_prog, prog_at_dip
                )
                return result

            if prog_recovery:  # Close 有 cur 变化但进度恢复了 → 误判
                result.primary_label = "idle_motion_misjudge"
                result.severity = 0
                return result

            if cur_spike and kin_vel_drop:
                result.primary_label = "grasp_collision"
                result.severity = 3
                result.recovery_action = self._detect_secondary_grasp(
                    arm, frame, pre_drop_prog, prog_at_dip
                )
                return result

        # ========== 2.A/2.B: grip_event == Stable ==========
        # 无 Open/Close 事件时，电流卸载本身不能确认物体滑落。
        if cur_dropped_to_baseline:
            if prog_recovery:
                result.primary_label = "normal_load_release"
                result.severity = 0
            else:
                result.primary_label = "unconfirmed_load_loss"
                result.severity = 2
                result.recovery_action = self._detect_secondary_grasp(
                    arm, frame, pre_drop_prog, prog_at_dip
                )
            return result

        # 2.A 刚性触碰：cur_spike + prog_recovery → 正常接触成功
        if cur_spike and prog_recovery:
            result.primary_label = "normal_contact_success"
            result.severity = 0
            return result

        # loaded_motion_misjudge: prog恢复 + cur高于空载
        if prog_recovery and not cur_near_baseline:
            result.primary_label = "loaded_motion_misjudge"
            result.severity = 0
            return result

        # idle_motion_misjudge: prog恢复 + cur近空载
        if prog_recovery and cur_near_baseline:
            result.primary_label = "idle_motion_misjudge"
            result.severity = 0
            return result

        # cur_spike but prog NOT recovered → 有接触但进度恢复慢，无侧滑证据 → 仍为正常接触
        if cur_spike and not prog_recovery:
            result.primary_label = "normal_contact_success"
            result.severity = 0
            return result

        # cur 正常、无夹爪事件、prog 未恢复 → 无物理证据的回退（纯视觉误判或恢复缓慢）
        if not cur_spike and not prog_recovery:
            result.primary_label = "unconfirmed_regression"
            result.severity = 0
            return result

        return result

    def attribute_stagnation(self, start_frame: int, end_frame: int, arm: str,
                              total_frames: int, next_stag_start: int = None,
                              prev_stag_end: int = None,
                              consumed_close_frames: set = None,
                              frame_to_prog: dict = None) -> AttributeResult:
        """
        对单臂停滞区间执行归因。

        Args:
            start_frame, end_frame: 停滞区间 [start, end]
            arm: 'left' / 'right'
            total_frames: 整条轨迹总帧数
            next_stag_start: 下一个停滞区间的起始帧（用于 edge 不越界）
            prev_stag_end: 上一个停滞的结束帧（用于 lookback 不重叠）
            consumed_close_frames: 已被前序停滞消费的 Close 事件帧号集合
            frame_to_prog: {frame: progress} 映射，用于 edge Close 的 progress 恢复检测
        """
        result = AttributeResult(frame=start_frame, arm=arm,
                                  primary_label="unknown_stagnation", severity=-1)

        # ========== 特征窗口 ==========
        # 停滞窗口
        stag_start = max(0, start_frame)
        stag_end = min(len(self.gfeat.speed[arm]) - 1, end_frame)
        # 碰撞回溯窗口：30帧，且不重叠前一个停滞的edge
        lookback_start = max(0, self._bound(start_frame, -30))
        if prev_stag_end is not None:
            lookback_start = max(lookback_start, prev_stag_end + 1)
        lookback_end = max(0, start_frame)

        # ========== 0. 静止臂前置门控 ==========
        speed_values = self.gfeat.speed[arm][stag_start:stag_end]
        if len(speed_values) and np.isfinite(speed_values).all():
            speed_stag = self._get_window_stat(self.gfeat.speed[arm], stag_start, stag_end)
            if speed_stag["mean"] < 0.005:
                result.primary_label = "ignore_static_arm"
                result.severity = 0
                result.raw_evidence = {"avg_speed": speed_stag["mean"]}
                return result
        limited = self._limited(start_frame, arm, self._bound(start_frame, -30), self._bound(end_frame, 100, inclusive=True))
        if limited is not None:
            return limited

        # ========== 窗口特征 ==========
        cur_stag = self._get_window_stat(self.gfeat.cur_rms[arm], stag_start, stag_end)
        grip_stag = self._get_window_stat(self.gfeat.gripper[arm], stag_start, stag_end)

        # 碰撞回溯：lookback 窗口内是否有 spike
        cur_lookback = self._get_window_stat(self.gfeat.cur_rms[arm], lookback_start, lookback_end)
        lookback_spike = cur_lookback["max"] > self.gfeat.cur_spike_threshold[arm]
        speed_zero = speed_stag["mean"] < 0.01

        # ========== 窗口内 Close 事件 + cur 变化检测 ==========
        cur_arr = self.gfeat.grip_cur_rms[arm]
        stag_events = self._events_in_window(arm, stag_start, stag_end)
        lookback_events = self._events_in_window(arm, lookback_start, stag_start)
        # ±20帧扩展：补回评估切分导致的 Close 遗漏，但不超过下一个停滞的起点
        edge_end_bound = min(len(cur_arr) - 1, self._bound(stag_end, 20, inclusive=True))
        if next_stag_start is not None:
            edge_end_bound = min(edge_end_bound, next_stag_start - 1)
        edge_events = self._events_in_window(arm, stag_end, edge_end_bound)
        all_close_events = [(f, t) for f, t in lookback_events + stag_events + edge_events if t == "close"]
        # 过滤已被前序停滞消费的 Close 事件
        if consumed_close_frames:
            all_close_events = [(f, t) for f, t in all_close_events if f not in consumed_close_frames]
        close_deltas = []

        for close_f, _ in all_close_events:
            pre_start = max(0, self._bound(close_f, -15))
            post_end = min(len(cur_arr) - 1, self._bound(close_f, 15))
            pre_cur = float(np.mean(cur_arr[pre_start:close_f])) if close_f > pre_start else float(cur_arr[close_f])
            post_cur = float(np.mean(cur_arr[close_f:post_end])) if post_end > close_f else float(cur_arr[close_f])
            close_deltas.append({
                "frame": close_f,
                "pre_cur": pre_cur,
                "post_cur": post_cur,
                "delta": post_cur - pre_cur,
            })

        # de-dup by frame（extended 可能和 lookback/stag 重叠）
        seen_frames = set()
        unique_deltas = []
        for d in close_deltas:
            if d["frame"] not in seen_frames:
                seen_frames.add(d["frame"])
                unique_deltas.append(d)
        close_deltas = unique_deltas

        # cur 状态：基于窗口内 Close 事件前后的变化，而非全局 baseline
        GRASP_DELTA_THRESH = self.load_rules['close_delta']
        if close_deltas:
            # 有 Close 事件：看 cur 是否上涨
            max_delta = max(d["delta"] for d in close_deltas)
            min_delta = min(d["delta"] for d in close_deltas)
            cur_loaded = max_delta > GRASP_DELTA_THRESH
            cur_idle = max_delta < GRASP_DELTA_THRESH * 0.5 and min_delta > -GRASP_DELTA_THRESH
        else:
            # 无 Close 事件：默认判为 idle-like（无抓取操作谈不上loaded）
            cur_idle = True
            cur_loaded = False

        # grip 状态
        grip_closed = grip_stag["mean"] < self.gfeat.grip_p5[arm]
        grip_open = grip_stag["mean"] > self.gfeat.grip_p95[arm]

        result.raw_evidence = {
            "stag_start": stag_start,
            "stag_end": stag_end,
            "cur_mean": float(cur_stag["mean"]),
            "cur_loaded": bool(cur_loaded),
            "cur_idle": bool(cur_idle),
            "close_deltas": close_deltas,
            "load_rules": self.load_rules,
            "load_sources": getattr(self.gfeat, 'load_evidence', {}).get(arm, {}),
            "grip_mean": float(grip_stag["mean"]),
            "grip_p5": float(self.gfeat.grip_p5[arm]),
            "grip_p95": float(self.gfeat.grip_p95[arm]),
            "grip_closed": bool(grip_closed),
            "grip_open": bool(grip_open),
            "speed_mean": float(speed_stag["mean"]),
            "speed_zero": bool(speed_zero),
            "lookback_spike": bool(lookback_spike),
            "lookback_cur_max": float(cur_lookback["max"]),
        }

        # ========== 0. 全局时序拦截层 ==========
        # 尾部冗余
        if end_frame >= total_frames * 0.95:
            result.primary_label = "redundant_tail_frames"
            result.severity = 0
            return result

        # ========== 1. 夹爪稳态层 ==========
        # 空抓检测方式1：停滞初期 grip 在极闭合区 + cur 空载 + 近期有 Close 事件
        grip_early = self._get_window_stat(self.gfeat.gripper[arm], stag_start, min(self._bound(stag_start, 20), stag_end))
        close_recently = any(
            t == "close" and self._bound(stag_start, -200) <= f <= stag_start
            for f, t in self.grip_events.get(arm, [])
        )
        if grip_early["mean"] < self.gfeat.grip_p5[arm] * 1.2 and cur_idle and close_recently:
            result.primary_label = "early_close_air_grasp"
            result.severity = 2
            return result

        # 空抓检测方式2：Close 前后 cur 没变（|delta|<0.2）且 Close 时 grip 在闭合区
        #   edge Close：检查 progress 是否回升——回升说明成功抓取打破停滞，不回升才是空抓
        for d in close_deltas:
            if abs(d["delta"]) < self.load_rules['stagnation_empty_delta']:
                close_f = d["frame"]
                # edge Close 的 progress 恢复检查
                if close_f > stag_end and frame_to_prog:
                    prog_frames = sorted(frame_to_prog.keys())
                    post_progs = [frame_to_prog[f] for f in prog_frames if close_f <= f <= self._bound(close_f, 100, inclusive=True)]
                    pre_prog = frame_to_prog.get(prog_frames[-1], 0)  # fallback
                    for f in prog_frames:
                        if f <= close_f:
                            pre_prog = frame_to_prog[f]
                    if post_progs and max(post_progs) > pre_prog + 0.05:
                        continue  # progress 回升 → 成功抓取，不是空抓
                close_f = d["frame"]
                grip_around_close = self._get_window_stat(
                    self.gfeat.gripper[arm],
                    max(0, self._bound(close_f, -5)), min(len(self.gfeat.gripper[arm]) - 1, self._bound(close_f, 15)))
                if grip_around_close["min"] < self.gfeat.grip_p5[arm] * 3.0:
                    result.primary_label = "early_close_air_grasp"
                    result.severity = 2
                    return result

        # 释放卸载检测：Close 后 cur 显著下降 → 松手/滑落
        for d in close_deltas:
            if d["delta"] < -GRASP_DELTA_THRESH:
                close_f = d["frame"]
                grip_around_close = self._get_window_stat(
                    self.gfeat.gripper[arm],
                    max(0, self._bound(close_f, -5)), min(len(self.gfeat.gripper[arm]) - 1, self._bound(close_f, 15)))
                if grip_around_close["min"] < self.gfeat.grip_p5[arm] * 3.0:
                    result.primary_label = "post_release_hesitation"
                    result.severity = 1
                    return result

        if grip_closed and cur_loaded:
            result.primary_label = "grasp_stuck_or_hesitation"
            result.severity = 2
            return result

        if grip_open and cur_idle:
            result.primary_label = "post_release_hesitation"
            result.severity = 1
            return result

        # ========== 2. 夹爪中立/稳定层 ==========
        if cur_loaded:
            if lookback_spike and speed_zero:
                result.primary_label = "post_collision_standoff"
                result.severity = 3
            elif not lookback_spike and speed_zero:
                result.primary_label = "insufficient_force_or_blocked"
                result.severity = 2
            else:
                result.primary_label = "loaded_hovering_hesitation"
                result.severity = 1
            return result

        if cur_idle:
            if speed_zero:
                result.primary_label = "idle_static_freeze"
                result.severity = 1
            else:
                result.primary_label = "teleop_search_or_hesitation"
                result.severity = 1
            return result

        # cur 在 loaded 和 idle 之间的灰色地带 → 倾向判为 idle-like
        if not cur_loaded:
            result.primary_label = "teleop_search_or_hesitation"
            result.severity = 1
            return result

        return result


# =============================================================================
# 5. EpisodeAttributor：顶层封装
# =============================================================================
class EpisodeAttributor(_SourceTimeWindows):
    """单条轨迹的归因器：整合 Layer1+Layer2+Layer3"""
    def _frames(self, legacy_frames):
        timeline = getattr(self, 'timeline', None)
        rate = timeline.rule_fps if timeline is not None and timeline.rule_fps is not None else self.fps
        return max(1, round(legacy_frames*rate/20.0))

    def __init__(self, episode_path: str, fps: float = 20.0, *, signals=None, timeline=None, load_rules=None,
                 evidence_policy="confirmed_only"):
        self.episode_path = Path(episode_path)
        self.fps = fps
        self.timeline = timeline
        if signals is not None:
            from .physical_features import PhysicalFeatures
            self.gfeat = PhysicalFeatures(signals, fps, load_rules=load_rules, evidence_policy=evidence_policy)
            self.raw_data = None
            self.episode_json = Path(signals.episode_id)
            self.grip_events = {arm: detect_grip_events(
                self.gfeat.gripper[arm], self.gfeat.grip_vel[arm],
                self.gfeat.grip_delta[arm], self.gfeat.grip_p5[arm],
                self.gfeat.max_grip_vel[arm]) for arm in ("left", "right")}
            return

        # 尝试从视频文件自动检测 FPS
        if fps == 20.0:
            for vname in ["rightImg.mp4", "faceImg.mp4", "cam_right_wrist.mp4"]:
                vpath = self.episode_path / vname
                if vpath.exists():
                    try:
                        import cv2
                        cap = cv2.VideoCapture(str(vpath))
                        detected = cap.get(cv2.CAP_PROP_FPS)
                        cap.release()
                        if detected > 0:
                            self.fps = float(detected)
                            break
                    except Exception:
                        pass

        # 加载原始特征 JSON：优先加载含 'data' 键的文件
        json_files = list(self.episode_path.glob("*.json"))
        valid_json = None
        for f in json_files:
            if f.name in ("subtask_config.json", "assessment.json"):
                continue
            with open(f, "r") as fp:
                try:
                    d = json.load(fp)
                    if "data" in d and isinstance(d["data"], list) and len(d["data"]) > 0:
                        valid_json = f
                        break
                except json.JSONDecodeError:
                    continue

        if valid_json is None:
            raise FileNotFoundError(f"No valid feature JSON found in {episode_path}")
        self.episode_json = valid_json

        with open(self.episode_json, "r") as f:
            self.raw_data = json.load(f)

        # Layer 1: 全局特征计算
        self.gfeat = GlobalFeatures(self.raw_data, self.fps, timeline=timeline)

        # Layer 2: 夹爪事件检测
        self.grip_events = {}
        for arm in ["left", "right"]:
            self.grip_events[arm] = detect_grip_events(
                self.gfeat.gripper[arm],
                self.gfeat.grip_vel[arm],
                self.gfeat.grip_delta[arm],
                self.gfeat.grip_p5[arm],
                self.gfeat.max_grip_vel[arm],
            )

    def _mark_provisional(self, output):
        assumptions = getattr(self.gfeat, 'assumptions', {})
        if not any(assumptions.values()):
            return output
        output.update(evidence_policy=self.gfeat.evidence_policy, evidence_status='provisional',
                      evidence_assumptions=assumptions, assessment_complete=False)
        entries = [*output.get('results', {}).values(), *output.get('regression_results', {}).values(),
                   *output.get('stagnation_results', {}).values(), *output.get('skipped_results', [])]
        for entry in entries:
            used = [{**item, 'arm': arm} for arm in entry.get('arms', [entry['arm']])
                    for item in assumptions.get(arm, [])]
            if used:
                entry['evidence_status'] = 'provisional'
                entry.setdefault('evidence', {})['calibration_assumptions'] = used
        return output

    def attribute(self, regression_frames: list[int], prog_samples: list[dict]) -> dict:
        """
        对一组回退帧执行归因。

        Args:
            regression_frames: 回退帧列表
            prog_samples: 子任务 progress 样本 [{frame_index, progress, hop}, ...]

        Returns:
            {
                "episode": str,
                "results": {
                    frame: {
                        "arm": str,
                        "primary_label": str,
                        "severity": int,
                        "recovery_action": str | None,
                        "evidence": dict
                    }
                },
                "summary": {  # 问题帧统计（非忽略类，非SEV0）
                    "problem_frames": list,
                    "total_regressions": int,
                    "drop_detected": int,
                    "misjudge_only": int,
                }
            }
        """
        analyzer = WindowAnalyzer(self.gfeat, self.grip_events, prog_samples, self.fps, timeline=getattr(self, "timeline", None))

        results = {}
        problem_frames = []
        drop_count = 0
        misjudge_count = 0

        for frame in regression_frames:
            for arm in ["right", "left"]:  # 优先右臂（操作臂通常为右）
                result = analyzer.attribute(frame, arm)

                if result.primary_label == "ignore_static_arm":
                    continue  # 静态臂跳过

                results[f"{frame}_{arm}"] = {
                    "frame": frame,
                    "arm": arm,
                    "primary_label": result.primary_label,
                    "severity": result.severity,
                    "recovery_action": result.recovery_action,
                    "evidence": result.raw_evidence,
                }

                # 问题帧统计
                if result.severity > 0:
                    problem_frames.append(frame)
                    if (result.severity >= 3
                            and result.primary_label in CONFIRMED_DROP_LABELS):
                        drop_count += 1
                elif result.severity == 0 and "misjudge" in result.primary_label.lower():
                    misjudge_count += 1

        return self._mark_provisional({
            "rule_version": ATTRIBUTION_RULE_VERSION,
            "episode": self.episode_json.name,
            "results": results,
            "summary": {
                "problem_frames": list(set(problem_frames)),
                "total_regressions": len(regression_frames),
                "drop_detected": drop_count,
                "misjudge_only": len(problem_frames) == 0 and misjudge_count > 0,
            }
        })

    def proactive_grasp_scan(self, frame_to_prog: dict, skip_frames: set[int] = None, *, frame_bounds=None) -> list[dict]:
        """
        主动扫描所有 Close 事件和 hand_effort 尖峰，检测抓取失败。

        两种模式：
        1. Close 事件 + progress 达峰后回落 (>0.04, 峰<0.7)
        2. hand_effort 尖峰 (>10) 无夹爪变化 + progress 达峰后回落

        独立于回归/停滞归因，不修改现有逻辑。
        skip_frames: 已在回归/停滞中覆盖的帧号集合，跳过附近事件避免重复。
        """
        skip_set = skip_frames or set()
        if (any(getattr(self.gfeat, "limitations", {}).values())
                or any(getattr(self.gfeat, "assumptions", {}).values())):
            # Missing calibrated evidence cannot support proactive exclusion.
            return []
        findings: list[dict] = []
        prog_frames = sorted(frame_to_prog.keys())
        if not prog_frames:
            return findings

        first, stop = frame_bounds or (0, self.gfeat.n)
        first, stop = max(first, prog_frames[0]), min(stop, prog_frames[-1] + 1)

        def get_prog(frame: int) -> float:
            nearest = self._nearest_progress(prog_frames, frame)
            return frame_to_prog[nearest]

        for arm in ["right", "left"]:
            grip = self.gfeat.gripper[arm]
            he = self.gfeat.grip_cur_rms[arm]
            if hasattr(self.gfeat, "signals"):
                calibrated = getattr(self.gfeat, "proactive_inputs", {}).get(arm)
                if calibrated is None:
                    continue
                grip, he = calibrated
            events = self.grip_events[arm]

            # ===== Type 1: Close event → progress peak → dip =====
            close_events = [(f, t) for f, t in events if t == "close" and first <= f < stop]
            for cf, _ in close_events:
                # 跳过已在回归/停滞中覆盖的事件
                if any(self._bound(cf, -10) <= sf <= self._bound(cf, 10, inclusive=True) for sf in skip_set):
                    continue
                progs = [(f, get_prog(f)) for f in range(cf, min(self._bound(cf, 30), stop))]
                if not progs:
                    continue
                peak_f, peak_p = max(progs, key=lambda x: x[1])
                after = [(f, get_prog(f)) for f in range(peak_f, min(self._bound(peak_f, 30), stop))]
                if not after:
                    continue
                min_f, min_p = min(after, key=lambda x: x[1])
                dip = peak_p - min_p
                if dip > 0.04 and peak_p < 0.7:
                    findings.append({
                        "frame": cf,
                        "arm": arm,
                        "type": "failed_grasp_close",
                        "severity": 2,
                        "evidence": {
                            "close_frame": cf,
                            "peak_frame": peak_f,
                            "peak_prog": round(peak_p, 4),
                            "dip_frame": min_f,
                            "dip_prog": round(min_p, 4),
                            "dip": round(dip, 4),
                        }
                    })

            # ===== Type 2: hand_effort spike (>10, no grip change) → progress peak → dip =====
            in_spike = False
            spike_start = 0
            for i in range(first, stop):
                if he[i] > 10.0 and not in_spike:
                    spike_start = i
                    in_spike = True
                elif he[i] <= 10.0 and in_spike:
                    spike_end = i
                    in_spike = False
                    grange = float(grip[spike_start:spike_end].max() - grip[spike_start:spike_end].min())
                    if grange >= 5.0:
                        continue  # grip changed, not a blocked grasp
                    progs = [(f, get_prog(f)) for f in range(spike_end, min(self._bound(spike_end, 30), stop))]
                    if not progs:
                        continue
                    peak_f, peak_p = max(progs, key=lambda x: x[1])
                    after = [(f, get_prog(f)) for f in range(peak_f, min(self._bound(peak_f, 30), stop))]
                    if not after:
                        continue
                    min_f, min_p = min(after, key=lambda x: x[1])
                    dip = peak_p - min_p
                    if dip > 0.04 and peak_p < 0.7:
                        findings.append({
                            "frame": spike_start,
                            "arm": arm,
                            "type": "failed_grasp_blocked",
                            "severity": 2,
                            "evidence": {
                                "spike_start": spike_start,
                                "spike_end": spike_end,
                                "he_max": round(float(he[spike_start:spike_end].max()), 1),
                                "grip_during": float(grip[spike_start]),
                                "peak_frame": peak_f,
                                "peak_prog": round(peak_p, 4),
                                "dip_frame": min_f,
                                "dip_prog": round(min_p, 4),
                                "dip": round(dip, 4),
                            }
                        })

        return findings

    def attribute_from_assessment(self, assessment_path: str) -> dict:
        """
        从 evaluation_summary.json 归因（同时处理 regressions 和 stagnations）。

        预期格式：
        {
          "subtask_results": [{
            "step": 0, "start_frame": 0, "end_frame": 770,
            "regressions": [{"frame_index": 300, "hop": -0.237, "progress_before": 0.452,
                             "progress_after_7": [...], "samples_after_available": 7}],
            "stagnations": [{"start_frame": 900, "end_frame": 1010,
                             "avg_progress": 0.068, "amplitude": 0.048, "severity": "严重"}]
          }]
        }
        """
        with open(assessment_path, "r") as f:
            assessment = json.load(f)

        analyzer = WindowAnalyzer(self.gfeat, self.grip_events, [], self.fps, timeline=getattr(self, "timeline", None))
        total_frames = self.gfeat.n

        # Keep each task's curve separate: a new task starts its own progress scale.
        # AFTER frame identity is shared by both visual candidates and native predictions.
        task_progress = {}
        sub_eval_dir = Path(assessment_path).parent
        for pf in sorted(sub_eval_dir.glob("subtask_*/pred_vllm.json")):
            try:
                step = int(pf.parent.name.split("_")[1])
            except (ValueError, IndexError):
                continue
            task_progress[step] = {s["frame_index"]: s.get("progress", 0)
                                   for s in json.loads(pf.read_text()) if "frame_index" in s}

        def progress_for(sr, index):
            rows = task_progress.get(sr.get("step", sr.get("subtask_idx", index)), {})
            return {f: p for f, p in rows.items()
                    if sr.get("start_frame", 0) <= f <= sr.get("end_frame", total_frames - 1)}

        # ===== 回归归因 =====
        regression_results = {}
        skipped_results = []
        total_regr = 0
        drop_count = 0
        regression_problem_frames = []

        for task_index, sr in enumerate(assessment.get("subtask_results", [])):
            local_progress = progress_for(sr, task_index)
            analyzer.frame_to_prog = local_progress
            analyzer.prog_frames = sorted(local_progress)
            final_subtask_prog = sr.get("subtask_progress", sr.get("progress", 0.0))

            for reg in sr.get("regressions", []):
                frame = reg["frame_index"]
                total_regr += 1

                progress_context = {
                    "pre_drop_prog": reg.get("progress_before", reg.get("progress_at_regression", 0.0)),
                    "post_progs": reg.get("progress_after_7", []),
                    "samples_available": reg.get("samples_after_available", len(reg.get("progress_after_7", []))),
                    "final_subtask_progress": final_subtask_prog,
                }

                for arm in ["right", "left"]:
                    result = analyzer.attribute(frame, arm, progress_context=progress_context)


                    if result.primary_label == "ignore_static_arm":
                        skipped_results.append({'frame': frame, 'arm': arm,
                            'subtask_idx': sr.get('step', sr.get('subtask_idx')),
                            'primary_label': result.primary_label, 'severity': result.severity,
                            'evidence': result.raw_evidence})
                        continue

                    key = f"regr_{frame}_{arm}"
                    if key in regression_results:
                        key += f"_subtask_{task_index}"
                    regression_results[key] = {
                        "frame": frame,
                        "subtask_idx": sr.get('step', sr.get('subtask_idx')),
                        "arm": arm,
                        "primary_label": result.primary_label,
                        "severity": result.severity,
                        "recovery_action": result.recovery_action,
                        "evidence": result.raw_evidence,
                    }

                    if result.severity > 0:
                        regression_problem_frames.append(frame)
                        if (result.severity >= 3
                                and result.primary_label in CONFIRMED_DROP_LABELS):
                            drop_count += 1

        # ===== 停滞归因 =====
        stagnation_results = {}
        total_stag = 0
        stagnation_problem = []

        for task_index, sr in enumerate(assessment.get("subtask_results", [])):
            local_progress = progress_for(sr, task_index)
            analyzer.frame_to_prog = local_progress
            analyzer.prog_frames = sorted(local_progress)
            stag_list = sorted(sr.get("stagnations", []), key=lambda s: s["start_frame"])
            consumed_close = set()  # 每个子任务内独立追踪
            for i, stag in enumerate(stag_list):
                sframe = stag["start_frame"]
                eframe = stag["end_frame"]
                total_stag += 1
                next_start = stag_list[i + 1]["start_frame"] if i + 1 < len(stag_list) else None
                prev_end = stag_list[i - 1]["end_frame"] if i > 0 else None

                # v9: 同一停滞窗口先对两臂归因；双臂结论一致时合并为单条发布，
                # 消除同一区间按双臂重复上报（R2V2 pilot 发现 B）。两臂结论不同
                # 时保留各自记录，真实差异不受影响。
                arm_results = []
                for arm in ["right", "left"]:
                    result = analyzer.attribute_stagnation(
                        sframe, eframe, arm,
                        total_frames=total_frames,
                        next_stag_start=next_start,
                        prev_stag_end=prev_end,
                        consumed_close_frames=consumed_close,
                        frame_to_prog=local_progress,
                    )
                    if result.primary_label == "ignore_static_arm":
                        skipped_results.append({'start_frame': sframe, 'end_frame': eframe, 'arm': arm,
                            'subtask_idx': sr.get('step', sr.get('subtask_idx')),
                            'primary_label': result.primary_label, 'severity': result.severity,
                            'evidence': result.raw_evidence})
                        continue
                    arm_results.append((arm, result))
                    # 标记已被消费的 Close 事件帧号
                    for d in result.raw_evidence.get("close_deltas", []):
                        consumed_close.add(d["frame"])

                if len(arm_results) == 2:
                    (arm_a, res_a), (arm_b, res_b) = arm_results
                    identical = ((res_a.primary_label, res_a.severity)
                                 == (res_b.primary_label, res_b.severity))
                    to_publish = (
                        [(arm_a, res_a, [arm_a, arm_b])] if identical
                        else [(arm_a, res_a, None), (arm_b, res_b, None)]
                    )
                else:
                    to_publish = [(a, r, None) for a, r in arm_results]

                for arm, result, arms_consistent in to_publish:
                    entry = {
                        "start_frame": sframe,
                        "end_frame": eframe,
                        "subtask_idx": sr.get('step', sr.get('subtask_idx')),
                        "arm": arm,
                        "duration_sec": round(self._elapsed(sframe, eframe), 1),
                        "primary_label": result.primary_label,
                        "severity": result.severity,
                        "evidence": result.raw_evidence,
                    }
                    if arms_consistent:
                        entry["arms"] = arms_consistent
                    key = f"stag_{sframe}_{eframe}_{arm}"
                    if key in stagnation_results:
                        key += f"_subtask_{task_index}"
                    stagnation_results[key] = entry
                    if result.severity > 0:
                        stagnation_problem.append(sframe)

        # ===== 主动抓取扫描 =====
        proactive_findings = []
        for task_index, sr in enumerate(assessment.get("subtask_results", [])):
            findings = self.proactive_grasp_scan(progress_for(sr, task_index),
                skip_frames=set(regression_problem_frames),
                frame_bounds=(sr.get("start_frame", 0), sr.get("end_frame", total_frames - 1) + 1))
            proactive_findings.extend({**row, 'subtask_idx': sr.get('step', task_index)} for row in findings)

        return self._mark_provisional({
            "rule_version": ATTRIBUTION_RULE_VERSION,
            "episode": self.episode_json.name,
            "skipped_results": skipped_results,
            "regression_results": regression_results,
            "stagnation_results": stagnation_results,
            "proactive_findings": proactive_findings,
            "summary": {
                "total_regressions": total_regr,
                "drop_detected": drop_count,
                "regression_problem_frames": list(set(regression_problem_frames)),
                "total_stagnations": total_stag,
                "stagnation_problem_count": len(set(stagnation_problem)),
                "proactive_findings_count": len(proactive_findings),
            }
        })


# =============================================================================
# 6. 文本报告生成
# =============================================================================
LABEL_CN = {
    "object_drop_by_release": "主动松开掉落",
    "drop_by_slippage": "搬运滑落",
    "normal_contact_success": "正常接触(误判)",
    "normal_release_artifact": "正常释放(伪回退)",
    "normal_load_release": "正常卸载(进度已恢复)",
    "unconfirmed_load_loss": "负载消失(待复核)",
    "idle_motion_misjudge": "空载运动(误判)",
    "loaded_motion_misjudge": "带载运动(误判)",
    "unconfirmed_regression": "无物理证据回退",
    "contact_deflection_slippage": "接触打滑",
    "transport_collision": "搬运碰撞",
    "grasp_collision": "抓取碰撞",
    "early_close_air_grasp": "抓空",
    "teleop_search_or_hesitation": "空载搜索/遥操作犹豫",
    "idle_static_freeze": "空载彻底僵死",
    "post_release_hesitation": "松手后悬停犹豫",
    "post_close_cur_drop": "闭合后负载消失",
    "grasp_stuck_or_hesitation": "抓取卡死或犹豫",
    "insufficient_force_or_blocked": "力量不足或卡死",
    "loaded_hovering_hesitation": "带载徘徊",
    "post_collision_standoff": "碰撞后僵持死锁",
    "redundant_tail_frames": "尾部录制冗余",
    "subtask_transition_artifact": "子任务过渡停顿",
    "unknown_stagnation": "停滞原因未知",
    "unknown_needs_review": "待人工复核",
    "ignore_static_arm": "静止臂(已跳过)",
    "secondary_grasp_success": "二次抓取成功",
    "secondary_grasp_attempt": "二次抓取尝试",
    "failed_grasp_close": "抓取失败(闭合后进度回落)",
    "failed_grasp_blocked": "抓取失败(手指受阻未闭合)",
}
SEV_DESC = {3: "严重(实质性失败)", 2: "需关注(物理交互异常)", 1: "次优轨迹", 0: "正常/误判", -1: "未知"}


def fmts(sec, fps=20.0):
    m, s = int(sec // 60), sec % 60
    return f"{m:02d}:{s:05.2f}"


def generate_text_report(result: dict, fp, fps: float = 20.0, *, timeline=None):
    """从归因结果生成人类可读文本报告"""
    frame_time = timeline.time_at if timeline is not None else lambda frame: frame / fps
    lines = []
    sep = "=" * 70
    lines.append(sep)
    lines.append("轨迹回归与停滞归因报告")
    lines.append(sep)
    lines.append(f"轨迹: {result['episode'].replace('.json', '')}")
    s = result["summary"]
    lines.append(f"回归帧数: {s['total_regressions']}  掉落次数: {s['drop_detected']}  停滞数: {s['total_stagnations']}  主动发现: {s.get('proactive_findings_count', 0)}")

    regr_items = list(result.get("regression_results", {}).values())
    stag_items = list(result.get("stagnation_results", {}).values())

    if regr_items:
        lines.append("")
        lines.append("-" * 70)
        lines.append("【回归归因详情】")
        lines.append("-" * 70)
        for v in sorted(regr_items, key=lambda x: x["frame"]):
            frame = v["frame"]
            label = LABEL_CN.get(v["primary_label"], v["primary_label"])
            sev = v["severity"]
            sev_str = f" [SEV{sev} {SEV_DESC.get(sev, '?')}]" if sev > 0 else " [SEV0 正常]"
            rec = ""
            if v.get("recovery_action"):
                rec = f"  -> {LABEL_CN.get(v['recovery_action'], v['recovery_action'])}"
            lines.append(f"  帧 {frame:4d} ({fmts(frame_time(frame))}) {v['arm']}: {label}{sev_str}{rec}")

    if stag_items:
        lines.append("")
        lines.append("-" * 70)
        lines.append("【停滞归因详情】")
        lines.append("-" * 70)
        for v in sorted(stag_items, key=lambda x: x["start_frame"]):
            sf, ef = v["start_frame"], v["end_frame"]
            label = LABEL_CN.get(v["primary_label"], v["primary_label"])
            sev = v["severity"]
            sev_str = f" [SEV{sev} {SEV_DESC.get(sev, '?')}]" if sev > 0 else " [SEV0 正常]"
            lines.append(f"  帧 [{sf:4d}-{ef:4d}] ({fmts(frame_time(sf))}-{fmts(frame_time(ef))}, {v['duration_sec']:.1f}s) {v['arm']}: {label}{sev_str}")

    proact_items = result.get("proactive_findings", [])
    if proact_items:
        lines.append("")
        lines.append("-" * 70)
        lines.append("【主动抓取扫描】")
        lines.append("-" * 70)
        for v in sorted(proact_items, key=lambda x: x["frame"]):
            frame = v["frame"]
            tlabel = LABEL_CN.get(v["type"], v["type"])
            sev = v["severity"]
            sev_str = f" [SEV{sev} {SEV_DESC.get(sev, '?')}]"
            evidence = v.get("evidence", {})
            detail = ""
            if evidence.get("dip"):
                detail = f" (progress {evidence['peak_prog']:.3f}→{evidence['dip_prog']:.3f}, dip={evidence['dip']:.3f})"
            lines.append(f"  帧 {frame:4d} ({fmts(frame_time(frame))}) {v['arm']}: {tlabel}{sev_str}{detail}")

    if not regr_items and not stag_items and not proact_items:
        lines.append("")
        lines.append("  (无回归帧，无停滞，无主动发现 — 轨迹正常)")

    lines.append("")
    lines.append("-" * 70)
    lines.append("严重度: 3=致命(掉落/碰撞死锁) 2=需关注 1=次优 0=正常/误判 -1=未知")
    lines.append(sep)
    fp.write("\n".join(lines))


# =============================================================================
# 7. CLI 入口
# =============================================================================
