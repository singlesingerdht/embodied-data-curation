#!/usr/bin/env python3
"""
Gemini API 视频与事件日志多模态子任务时间对齐脚本

功能：
    接收一段机器人操作视频与一份原始末端位姿 JSON，脚本自动：
      ① 从位姿 JSON 提取夹爪事件文本日志；
      ② 绘制 1 张组合特征图 features.png（2 列 × 4 行 = 8 子图：
         左列=左臂 / 右列=右臂；4 行=夹爪开合 / 末端速度 / 关节电流 / 加加速度）；
      ③ 结合预定义子任务语义定义（task_config JSON），由 Gemini 为每个子任务
         对齐出精确的 clip_start_sec / clip_end_sec。

设计要点：
    - 输入仅需：视频 + 末端位姿 JSON + task-config。
    - 子任务的「语义定义」（step / query）由 task_config 预先给定，Gemini 的职责
      收窄为「时间对齐」：严格按输入顺序/数量输出，仅填时间戳。
    - 特征图横轴=时间(秒)，密集刻度，与视频时间轴一一对应；夹爪事件日志是绝对时间刻度。
    - 输出 JSON 严格匹配 Robo-Dopamine 子任务配置格式
      （task_name / description / video_fps / subtasks[step,query,clip_start_sec,clip_end_sec,partial]）。
    - video_fps / partial / 时间连续性由脚本本地注入与修正，不让 LLM 生成事实性数值。

环境：
    - conda activate robo-forge
    - export GEMINI_API_KEY=...   （绝不硬编码进脚本）
    - API 调用与视频上传必须由调用方显式启用。
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import cv2
import matplotlib

matplotlib.use("Agg")  # 非交互后端，必须在 pyplot 导入前设置
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator
from pydantic import BaseModel, Field

# google-genai is imported lazily inside align_subtasks_with_gemini / main /
# _upload_and_wait_ready so the module is importable without the Gemini SDK /
# API key (e.g. when only doing episode evaluation or attribution).


# ==========================================
# 1. 强制 JSON 输出结构（Pydantic）
# ==========================================
class SubTask(BaseModel):
    step: int = Field(description="子任务序号，从 0 开始递增")
    query: str = Field(description="子任务详细动作描述（评估依据）：说明使用哪个机械臂、对什么物体、做什么操作；中英均可")
    clip_start_sec: float = Field(description="子任务开始时间（秒，浮点），必须严格对齐夹爪事件刻度")
    clip_end_sec: float = Field(description="子任务结束时间（秒，浮点），必须严格对齐夹爪事件刻度")
    partial: bool = Field(default=False, description="是否允许部分完成；当前恒为 false")


class TaskSegmentation(BaseModel):
    task_name: str = Field(description="任务名称")
    description: str = Field(description="一句话中文总体任务描述")
    subtasks: list[SubTask] = Field(description="连续子任务列表，时间从 0 开始且首尾衔接")


# ==========================================
# 2. 视频与特征处理工具
# ==========================================
def get_video_fps_and_duration(video_path: Path) -> tuple[float, float]:
    """读取视频 FPS 与时长（秒）。失败时 fps 回退 30.0。"""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"无法打开视频: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    if not fps or fps <= 0 or fps != fps:  # NaN 检测
        fps = 30.0
    duration = (total_frames / fps) if total_frames > 0 else 0.0
    return float(fps), float(duration)


def _feature_times(feature_json, fps):
    n=len(feature_json.get('data',[]))
    if 'timestamps' not in feature_json: return np.arange(n,dtype=float)/fps
    t=np.asarray(feature_json['timestamps'],float)
    if t.shape!=(n,) or not np.isfinite(t).all() or np.any(np.diff(t)<=0):
        raise ValueError('Feature timestamps must match recorded samples')
    return t-t[0]


def _time_smooth(values,times,seconds,fps):
    if np.allclose(np.diff(times),1/fps,atol=1e-5,rtol=0):
        window=min(len(values),max(1,int(fps*seconds)))
        return np.convolve(values,np.ones(window)/window,mode='same')
    # Same centred window, with boundaries found on the actual recorded axis.
    left=np.searchsorted(times,times-seconds/2,side='left')
    right=np.searchsorted(times,times+seconds/2,side='right')
    sums=np.r_[0,np.cumsum(values)]
    return (sums[right]-sums[left])/(right-left)


def extract_gripper_events(
    feature_json: dict[str, Any],
    fps: float,
    open_ratio: float = 0.67,
    close_ratio: float = 0.33,
) -> str:
    """
    遍历原始特征 JSON，用【状态机+迟滞阈值】检测左右夹爪开合，格式化为文本时间轴。

    阈值【自适应】：
        - 先用每个手臂的 gripper 全量数据计算 min/max
        - open_threshold = min + open_ratio*(max-min)  （默认 67% 范围处）
        - close_threshold = min + close_ratio*(max-min) （默认 33% 范围处）
      这样无论 gripper 是 0~4.5（微波炉）还是 0~0.06（源力），都能自适应检测。
    迟滞区间 (close_threshold, open_threshold) 防止抖动；每个开/合只记录一次。
    初始状态默认「闭合」。
    """
    data_list = feature_json.get("data", [])
    times = _feature_times(feature_json,fps)
    if not data_list or fps <= 0:
        return "(未提取到夹爪事件)"

    arms = [
        ("左臂", "follow_left_gripper"),
        ("右臂", "follow_right_gripper"),
    ]

    blocks: list[str] = []
    for arm_name, key in arms:
        # 自适应阈值：从该手臂全量数据计算 min/max
        values = []
        for frame in data_list:
            raw = frame.get(key)
            if raw is not None:
                try:
                    values.append(float(raw))
                except (TypeError, ValueError):
                    pass
        if not values:
            continue
        vmin, vmax = min(values), max(values)
        gripper_range = vmax - vmin
        if gripper_range < 0.01:
            continue  # 几乎恒定的夹爪值（如左臂不动），跳过避免噪音事件
        open_threshold = vmin + open_ratio * gripper_range
        close_threshold = vmin + close_ratio * gripper_range

        events: list[str] = []
        state = "closed"
        for i, frame in enumerate(data_list):
            raw = frame.get(key)
            if raw is None:
                continue
            try:
                v = float(raw)
            except (TypeError, ValueError):
                continue

            t_sec = float(times[i])
            if state == "closed" and v > open_threshold:
                state = "open"
                events.append(f"  {t_sec:.1f}s: {arm_name}夹爪张开")
            elif state == "open" and v < close_threshold:
                state = "closed"
                events.append(f"  {t_sec:.1f}s: {arm_name}夹爪闭合")

        if events:
            blocks.append(f"{arm_name}夹爪事件\n" + "\n".join(events))

    return "\n".join(blocks) if blocks else "(未提取到夹爪事件)"


def extract_movement_events(
    feature_json: dict[str, Any],
    fps: float,
    move_threshold: float = 0.08,
    min_duration_sec: float = 1.5,
    smooth_window_sec: float = 1.0,
) -> str:
    """
     从末端位姿检测左右臂的【持续运动期】，作为夹爪事件之外的第二种时间锚点。
     同时检测腕部旋转关节的关节空间角速度（带符号方向），覆盖倒水/倒茶等纯旋转动作。

     普适用途：夹爪事件只覆盖"开/合"，但很多动作（推门关门、移动到目标、复位返回）
     夹爪不动、手臂在动 —— 这些动作靠夹爪事件无法定位，本函数补上。

     算法：
        1. follow_*_position → 末端线速度；
        2. follow_*_joint_position → 自动识别夹爪关节（与 follow_*_gripper 相关性 >0.99），
           取夹爪前一个关节作为腕部旋转关节，计算关节角速度（带符号方向）；
        3. 滑动窗口（默认 1s）平滑，抑制抖动；
        4. 迟滞阈值判定运动/静止；
        5. 过滤短于 min_duration_sec 的运动期。

     返回形如：
        左臂运动事件（线速度）
          80.2s-86.2s: 左臂持续运动 (6.0秒)
        左臂运动事件（腕部旋转关节）
          82.0s-85.0s: 正向旋转 (3.0秒)
        右臂运动事件（线速度）
          4.8s-7.0s: 右臂持续运动 (2.2秒)
        右臂运动事件（腕部旋转关节）
          98.9s-101.7s: 负向旋转 (2.8秒)
          108.7s-111.4s: 正向旋转 (2.7秒)
          ...
    """
    data_list = feature_json.get("data", [])
    times = _feature_times(feature_json,fps)
    if not data_list or fps <= 0:
        return "(未提取到运动事件)"

    window = max(1, int(fps * smooth_window_sec))
    kernel = np.ones(window) / window
    min_frames = max(1, int(min_duration_sec * fps))
    stop_threshold = move_threshold * 0.4

    blocks: list[str] = []

    for arm_name, pos_key, jp_key, grip_key in [
        ("左臂", "follow_left_position", "follow_left_joint_position", "follow_left_gripper"),
        ("右臂", "follow_right_position", "follow_right_joint_position", "follow_right_gripper"),
    ]:
        n = len(data_list)
        if n < 2:
            continue

        # --- 线速度检测 ---
        pos = np.array([f.get(pos_key, [0.0, 0.0, 0.0]) for f in data_list], dtype=np.float64)
        if pos.ndim == 2 and pos.shape[0] >= 2:
            dt = np.diff(times)
            disp = np.linalg.norm(np.diff(pos, axis=0), axis=1)
            speed = np.concatenate([[0.0], disp / dt])
            smoothed = _time_smooth(speed,times,smooth_window_sec,fps)

            events: list[str] = []
            moving = False
            move_start = 0
            for i in range(len(smoothed)):
                if not moving and smoothed[i] > move_threshold:
                    moving = True
                    move_start = i
                elif moving and smoothed[i] < stop_threshold:
                    moving = False
                    if times[i]-times[move_start] >= min_duration_sec:
                        t0, t1 = float(times[move_start]), float(times[i])
                        events.append(f"  {t0:.1f}s-{t1:.1f}s: {arm_name}持续运动 ({t1 - t0:.1f}秒)")
            if moving and times[-1]-times[move_start] >= min_duration_sec:
                t0, t1 = float(times[move_start]), float(times[-1])
                events.append(f"  {t0:.1f}s-{t1:.1f}s: {arm_name}持续运动 ({t1 - t0:.1f}秒)")

            if events:
                blocks.append(f"{arm_name}运动事件（线速度）\n" + "\n".join(events))

        # --- 腕部旋转关节检测（关节空间角速度，带符号方向） ---
        jp = np.array([f.get(jp_key, []) for f in data_list], dtype=np.float64)
        grip = np.array([float(f.get(grip_key, 0.0)) for f in data_list], dtype=np.float64)
        if jp.ndim == 2 and jp.shape[0] >= 2 and jp.shape[1] > 0:
            # 自动识别夹爪关节：与 gripper 值相关性 > 0.99
            n_joints = jp.shape[1]
            gripper_joint = -1
            for j in range(n_joints):
                std_jp = np.std(jp[:, j])
                std_grip = np.std(grip)
                if std_jp > 1e-9 and std_grip > 1e-9:
                    corr = np.corrcoef(jp[:, j], grip)[0, 1]
                    if abs(corr) > 0.99:
                        gripper_joint = j
                        break
            # 腕部旋转关节 = 夹爪前一个关节（法兰末端旋转关节）
            wrist_joint = gripper_joint - 1 if gripper_joint > 0 else n_joints - 1

            jp_wrist = jp[:, wrist_joint]
            dt = np.diff(times)
            jvel = np.concatenate([[0.0], np.diff(jp_wrist) / dt])  # (n,) 带符号
            jvel_abs = np.abs(jvel)
            jvel_abs_smoothed = _time_smooth(jvel_abs,times,smooth_window_sec,fps)

            # 自适应阈值：基于 75 分位值，下限 0.02 rad/s
            ang_threshold = max(0.02, float(np.percentile(jvel_abs_smoothed, 75)) * 0.3)
            ang_stop = ang_threshold * 0.4

            events: list[str] = []
            moving = False
            move_start = 0
            for i in range(len(jvel_abs_smoothed)):
                if not moving and jvel_abs_smoothed[i] > ang_threshold:
                    moving = True
                    move_start = i
                elif moving and jvel_abs_smoothed[i] < ang_stop:
                    moving = False
                    if times[i]-times[move_start] >= min_duration_sec:
                        t0, t1 = float(times[move_start]), float(times[i])
                        avg_vel = jvel[move_start:i].mean()
                        direction = "正向" if avg_vel >= 0 else "负向"
                        events.append(f"  {t0:.1f}s-{t1:.1f}s: {direction}旋转 ({t1 - t0:.1f}秒)")
            if moving and times[-1]-times[move_start] >= min_duration_sec:
                t0, t1 = float(times[move_start]), float(times[-1])
                avg_vel = jvel[move_start:].mean()
                direction = "正向" if avg_vel >= 0 else "负向"
                events.append(f"  {t0:.1f}s-{t1:.1f}s: {direction}旋转 ({t1 - t0:.1f}秒)")

            if events:
                blocks.append(f"{arm_name}运动事件（腕部旋转关节）\n" + "\n".join(events))

    return "\n".join(blocks) if blocks else "(未提取到运动事件)"


# ------------------------------------------
# 数值特征序列计算（末端速度 / 加加速度 / 关节电流RMS）
# ------------------------------------------
def _per_frame_time(n: int, fps: float) -> np.ndarray:
    return np.arange(n, dtype=np.float64) / fps


def compute_end_effector_speed(data_list: list[dict], fps: float, times=None) -> dict[str, np.ndarray]:
    """末端线速度（m/s）：||position[i] - position[i-1]|| / dt，首帧补 0。"""
    dt = np.diff(times) if times is not None else 1.0 / fps
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


def compute_jerk(data_list: list[dict], fps: float, times=None) -> dict[str, np.ndarray]:
    """末端加加速度 jerk（m/s^3）：位置的三阶导数幅值，首部补 0 对齐帧数。"""
    dt = np.diff(times) if times is not None else 1.0 / fps
    out: dict[str, np.ndarray] = {}
    for arm, key in (("left", "follow_left_position"), ("right", "follow_right_position")):
        pos = np.array([f.get(key, [0.0, 0.0, 0.0]) for f in data_list], dtype=np.float64)
        n = len(data_list)
        if pos.ndim != 2 or n < 4:
            out[arm] = np.zeros(n)
            continue
        v = np.diff(pos, axis=0) / (dt[:,None] if np.ndim(dt) else dt)                 # 一阶：速度
        a = np.diff(v, axis=0) / (((dt[1:]+dt[:-1])/2)[:,None] if np.ndim(dt) else dt)                   # 二阶：加速度
        j = np.diff(a, axis=0) / (((dt[2:]+2*dt[1:-1]+dt[:-2])/4)[:,None] if np.ndim(dt) else dt)                   # 三阶：加加速度
        mag = np.linalg.norm(j, axis=1)
        mag = np.pad(mag, (3, 0), constant_values=0.0)  # 对齐到原帧数
        out[arm] = mag
    return out


def compute_joint_current_rms(data_list: list[dict]) -> dict[str, np.ndarray]:
    """关节电流 RMS：每帧 follow_*_joint_cur 的均方根，作为受力/接触指示。"""
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


def _choose_tick_interval(duration: float) -> float:
    """主刻度（带文字标签）间隔：按时长自适应，目标约 10-14 个标签，避免重合。"""
    if duration <= 20:
        return 2.0
    if duration <= 80:
        return 5.0
    if duration <= 160:
        return 10.0
    return 20.0


# 4 行特征定义：(序列键, 纵轴标签(无单位), 中文描述)
FEATURE_ROWS: list[tuple[str, str, str]] = [
    ("gripper", "gripper", "左右夹爪开合量（≈4.5 为全开，≈0 为全闭）"),
    ("speed", "speed", "左右机械臂末端速度（速度归零常标志动作结束/停顿）"),
    ("current", "current", "左右机械臂关节电流（突变可反映接触/受力）"),
    ("jerk", "jerk", "左右机械臂末端加加速度（峰值对应急停/方向骤变）"),
]


def generate_feature_plots(
    data_json: dict[str, Any], fps: float, output_dir: Path
) -> dict[str, Path]:
    """
    生成 1 张组合特征图 features.png：2 列 × 4 行 = 8 个子图。

    - 左列 = 左臂，右列 = 右臂（确保左右臂都有，各自独立子图）
    - 自上而下 4 行：gripper / speed / current / jerk
    - 横轴 = 时间(秒)，密集主/次刻度，与视频时间轴一一对应（数据帧 ↔ 视频帧）
    - 纵轴不标注单位（仅特征名）
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    data_list = data_json.get("data", [])
    n = len(data_list)
    t = _feature_times(data_json,fps)
    duration = float(t[-1]) if len(t) else 0.0

    series = {
        "gripper": {
            "left": np.array([float(f.get("follow_left_gripper", 0.0)) for f in data_list]),
            "right": np.array([float(f.get("follow_right_gripper", 0.0)) for f in data_list]),
        },
        "speed": compute_end_effector_speed(data_list, fps,times=t),
        "current": compute_joint_current_rms(data_list),
        "jerk": compute_jerk(data_list, fps,times=t),
    }

    fig, axes = plt.subplots(nrows=4, ncols=2, figsize=(14, 13), dpi=110, sharex=True)
    major = MultipleLocator(_choose_tick_interval(duration))
    minor = MultipleLocator(1.0)  # 次刻度固定 1s（细致刻度线，无文字标签）

    for row, (key, ylabel, _desc) in enumerate(FEATURE_ROWS):
        s = series[key]
        for col, arm in enumerate(("left", "right")):
            ax = axes[row, col]
            ax.plot(t, s[arm], linewidth=0.9)
            ax.set_ylabel(ylabel)
            ax.grid(True, which="both", alpha=0.3)
            ax.xaxis.set_major_locator(major)
            ax.xaxis.set_minor_locator(minor)
            if row == 0:
                ax.set_title("Left arm" if col == 0 else "Right arm", fontsize=11)
            if row == len(FEATURE_ROWS) - 1:
                ax.set_xlabel("time (s)")

    # 主标签稀疏（5s），水平显示即可；次刻度 1s 网格线提供细致感
    for ax in axes.ravel():
        ax.tick_params(axis="x", labelsize=9)
        ax.tick_params(axis="y", labelsize=9)

    fig.suptitle("Feature timeline (left / right arm)", y=0.995, fontsize=13)
    fig.tight_layout()
    out_path = output_dir / "features.png"
    fig.savefig(out_path)
    plt.close(fig)
    return {"features": out_path}


def feature_images_description(paths: dict[str, Path]) -> tuple[str, list[Path]]:
    """
    构造 user_prompt 用的特征图说明文本，并返回按固定顺序的图片路径列表。

    返回:
        (说明文本, [图路径...])  顺序与说明一一对应
    """
    row_descs = [desc for _k, _y, desc in FEATURE_ROWS]
    rows_text = "；".join(f"第{i + 1}行={d}" for i, d in enumerate(row_descs))
    p = paths.get("features")
    lines = [
        "【特征图说明】（已附上 1 张组合图 features.png，横轴=时间(秒)，密集刻度且与视频时间轴完全对齐）：",
        "- features.png：2 列 × 4 行共 8 个子图。左列=左臂，右列=右臂；自上而下 4 行依次为：",
        f"  {rows_text}。",
        "- 请跨子图对齐同一时间点：例如夹爪开合(gripper)突变、速度(speed)归零、电流(current)突变、"
        "加加速度(jerk)峰值，往往共同标志某个子任务的边界时刻。",
    ]
    ordered: list[Path] = []
    if p and p.is_file():
        ordered.append(p)
    return "\n".join(lines), ordered


def load_feature_images(image_paths: list[str]) -> list:
    """将多张特征 png 以 inline Part 形式加载（无需 Files API 轮询）。"""
    from google.genai import types
    parts: list = []
    for p in image_paths:
        path = Path(p)
        if not path.is_file():
            raise FileNotFoundError(f"特征图不存在: {path}")
        parts.append(
            types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png")
        )
    return parts


def load_task_config(config_path: str) -> dict[str, Any]:
    """
    加载预定义子任务配置 JSON。

    必须包含：task_name(str), description(str), subtasks(list)。
    subtasks 支持两种最简形式：
      - 纯字符串列表（推荐）：["抓取物品", "放置物品"]  （数组顺序即顺序）
      - dict 列表（兼容）：   [{"query": "..."}]（可选带 step 用于排序）

    读取时自动：① 按数组顺序（或 step）排序 → ② 归一化 step(0,1,2...)。
    注意：末尾的"机械臂复位"需由用户在输入 subtasks 中自行写出，脚本不再自动追加。
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"task_config 不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    for field in ("task_name", "subtasks"):
        if field not in data:
            raise ValueError(f"task_config 缺少必需字段: {field}")
    data.setdefault("description", "")

    raw = data["subtasks"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("task_config.subtasks 必须是非空列表")

    # 兼容两种形式：纯字符串 or {"query": ...}（可带 step）
    parsed: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            parsed.append({"query": item, "_order": idx})
        elif isinstance(item, dict) and item.get("query"):
            parsed.append({"query": str(item["query"]), "_order": item.get("step", idx)})
        else:
            raise ValueError(f"task_config.subtasks[{idx}] 必须是字符串或含 query 的对象")

    # 若原输入均为 dict 且都带 step，则按 step 升序；否则按数组顺序
    has_explicit_step = all(isinstance(it, dict) and isinstance(it.get("step"), (int, float)) for it in raw)
    if has_explicit_step:
        parsed.sort(key=lambda s: s["_order"])

    # 归一化 step（从 0 递增），清理临时键
    subtasks: list[dict[str, Any]] = []
    for idx, st in enumerate(parsed):
        st.pop("_order", None)
        st["step"] = idx
        subtasks.append(st)

    data["subtasks"] = subtasks
    return data


# ==========================================
# 3. Gemini 上传与推理
# ==========================================
def _upload_and_wait_ready(
    client, video_path: Path, timeout_sec: float = 300.0, max_retries: int = 20
):
    """上传视频并轮询等待服务端处理完成（沿用 robo_forge pipeline 的稳健模式）。

    代理环境下大文件上传偶发 SSL EOF，故对 upload 步骤自动重试。
    """
    if not video_path.is_file():
        raise FileNotFoundError(f"视频不存在: {video_path}")

    from google.genai import types
    deadline = time.time() + timeout_sec

    def _remaining_ms() -> int:
        return max(1000, int((deadline - time.time()) * 1000))

    uploaded = None
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            uploaded = client.files.upload(
                file=str(video_path),
                config=types.UploadFileConfig(
                    http_options=types.HttpOptions(timeout=_remaining_ms()),
                ),
            )
            break
        except Exception as err:  # noqa: BLE001 — 代理偶发连接错误需重试
            last_err = err
            print(f"  视频上传失败（第 {attempt}/{max_retries} 次）: {type(err).__name__}: {err}")
            if attempt < max_retries:
                wait = min(5 * (2 ** (attempt - 1)), 60)
                time.sleep(wait)
    if uploaded is None:
        raise last_err  # type: ignore[misc]

    while uploaded.state and uploaded.state.name == "PROCESSING":
        if time.time() > deadline:
            raise TimeoutError(f"视频处理超时: {video_path}")
        time.sleep(1)
        uploaded = client.files.get(
            name=uploaded.name,
            config=types.GetFileConfig(
                http_options=types.HttpOptions(timeout=_remaining_ms()),
            ),
        )

    if uploaded.state and uploaded.state.name == "FAILED":
        message = getattr(getattr(uploaded, "error", None), "message", "unknown error")
        raise ValueError(f"视频处理失败: {message}")

    return uploaded


def align_subtasks_with_gemini(
    *,
    client,
    video_files: list,
    image_parts: list,
    task_config: dict[str, Any],
    event_log: str,
    movement_log: str,
    feature_images_desc: str,
    video_duration: float,
    model: str,
    response_path: Path | None = None,
) -> dict[str, Any]:
    """调用 Gemini，为预定义子任务对齐时间戳并返回解析后的 dict。"""
    from google.genai import types

    system_instruction = f"""你是一个专业的具身智能（Embodied AI）机器人行为分析专家。
你的任务是为【预先给定的子任务列表】精准对齐 clip_start_sec / clip_end_sec，并原样输出。

【核心分析规则】：
1. 你将收到 3 段同步视频（faceImg=正面主视角、leftImg=左臂视角、rightImg=右臂视角）、
   一张组合特征图 features.png（2列×4行=8子图），
   以及两份事件日志：【夹爪事件日志】与【手臂运动事件日志】。
   特征图各行含义：gripper=夹爪开合量 / speed=末端线速度 / current=关节电流 / jerk=加加速度。
   左列=左臂，右列=右臂。
2. 特征图横轴=时间(秒)，与视频时间轴完全对齐，用于理解趋势与宏观语义。
3. 事件日志提供【精确秒数】作为硬锚点：夹爪事件日志覆盖开/合动作，运动事件日志覆盖手臂移动。
   在卡准 clip_start_sec 和 clip_end_sec 时，必须严格对齐事件日志中的精确时刻，
   不能仅凭视觉或图估计。特征图辅助判断"哪个事件属于哪个子任务"，但时间数值以事件日志为准。
4. 三个视角互补：当机械臂在 faceImg 主视角中不可见时，务必参考 leftImg/rightImg 判断动作是否仍在进行，
    避免因机械臂离开主视角而误判子任务结束。
5. 视频画面帮助你理解宏观语义（它在干什么）。

【子任务边界规则】（按子任务语义选择边界依据）：
  - 抓取类（拿取/抓起/拾取）：
      start = 夹爪开始闭合（gripper 曲线从低→高转折）；end = 夹爪完全闭合稳定夹持。
  - 放置类（放下/放入/放置/松开）：
      start = 抓取闭合后开始移动；end = 夹爪张开释放（gripper 曲线从高→低）。
  - 抓取放置复合类（拿取...放置，可能含多次抓取）：
      start = 第一次抓取开始闭合；end = 最后一次放置张开释放。
      中间多次开合属同一子任务内部过程，严禁拆分。
  - 开门类（按按钮/拨门/组合开门）：
      end = 门打开到可放入程度（视觉判定，门开度>一半）；start = 开始执行开门动作。
  - 关门类（关门/合上门）：
      start = 推门手臂开始运动（speed 子图该手臂速度>0 的起点）；
      end = 门完全关闭/合紧（视觉判定）。
  - 按压类（仅按按钮/开关，非开门）：start = 手接近按钮；end = 按压完成手离开。
  - 倾倒类（倒水/倒茶/注液等倾斜容器使液体流出的动作）：
      start = 容器开始向目标倾斜（腕部旋转关节日志中首次负向旋转的开始）；
      end = 容器最终回正、停止出液（腕部旋转关节日志中最后一次正向回正的结束）。
      一次倾倒可能包含多轮前倾(负向)-回正(正向)周期，须以最后一轮回正终点为 end。
      不同杯之间的边界必须以视频中茶壶移到不同杯子位置为准，不能仅凭线速度事件判断
      （杯内调整也会产生线速度事件）。
  - 复位类：start = 机械臂开始返回的运动期起点（线速度或角速度>0 的起点）；
    end = 机械臂停止运动、回到初始位置的最晚运动事件终点（线速度和角速度均归零的时刻），
    而非视频末尾。复位完成后视频中可能仍有静止时间，这部分不属于复位子任务。
  - 严禁：抓取类边界用张开事件；放置类边界用闭合事件。
  - 首尾衔接：clip_end_sec[i] = clip_start_sec[i+1]（以后者 start 为前者 end 基准）。

【输出硬性约束】：
- subtasks 数量、顺序、query 严格等于输入，仅填时间。
- 多次抓取不拆分，属同一子任务内部过程。
- clip_start_sec[0]=0。
- 首尾衔接，以后者 start 为前者 end 基准。
- 最后一个子任务（通常是复位）的 clip_end_sec 为机械臂真正停止运动的时刻，而非视频末尾。
- 未出现子任务：clip_start_sec == clip_end_sec（零时长，脚本自动删除并重排后续 step）。
- partial 恒为 false。
- 复审：若某子任务时长占比 > 1/{len(task_config['subtasks'])} + 15% = {100*(1/len(task_config['subtasks'])+0.15):.0f}%，
  则边界大概率定错，必须重新审视是否误吞相邻动作或锚点用错。
"""

    predefined = "\n".join(
        f"  ({idx}) {st['query']}"
        for idx, st in enumerate(task_config["subtasks"])
    )

    feature_block = f"{feature_images_desc}\n\n" if feature_images_desc else ""

    user_prompt = f"""请为以下【预定义子任务列表】逐一卡准时间边界，输出完整时间轴。

{feature_block}【预定义子任务列表】（必须原样保留 step 与 query，仅补时间）：
{predefined}

【任务名称】{task_config['task_name']}
【任务描述】{task_config['description']}

【夹爪事件日志】（覆盖开/合动作的精确秒数锚点）：
{event_log}

【手臂运动事件日志】（覆盖手臂移动的精确秒数锚点；无夹爪事件的子任务以此为基准）：
{movement_log}
注意：腕部旋转关节事件基于关节空间角速度（带符号方向），"负向"=倾斜，"正向"=回正。
一次倾倒可能包含多轮"负向→正向"周期，须以最后一轮正向回正终点为子任务 end。
多轮周期是否属于同一杯须以视频画面确认（茶壶是否移到不同杯子），不能仅凭线速度事件判断。

请严格按输出 JSON schema 返回，勿输出任何额外文本。"""

    # contents 顺序：所有视频 → 特征图（顺序与 feature_images_desc 描述一一对应）→ 文本提示
    contents: list[Any] = list(video_files)
    contents.extend(image_parts)
    contents.append(user_prompt)

    max_retries = 20
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=TaskSegmentation.model_json_schema(),
                ),
            )
            text = (response.text or "").strip()
            if response_path is not None:
                response_path.write_text(text)
            if not text:
                raise ValueError("Gemini 返回空内容")
            return json.loads(text)
        except Exception as err:
            last_err = err
            if attempt < max_retries:
                wait = min(5 * (2 ** (attempt - 1)), 60)
                print(f"  API 调用失败（第 {attempt}/{max_retries} 次）: {type(err).__name__}: {err}，{wait}s 后重试...")
                time.sleep(wait)
    raise RuntimeError(f"API 调用失败（已重试 {max_retries} 次）: {type(last_err).__name__}: {last_err}")


# ==========================================
# 4. 后处理：注入本地事实性数值 + 强制连续性
# ==========================================
def enforce_continuity(
    result: dict[str, Any],
    *,
    video_fps: float,
    video_duration: float,
) -> dict[str, Any]:
    """
    用本地真实数值修正 LLM 输出（首尾衔接：以后一个子任务的 start 作为前一个的 end）：
        - 顶层注入 video_fps（脚本读取的真实值）
        - clip_start_sec[0] = 0.0
        - clip_end_sec[-1] = video_duration
        - 首尾衔接：clip_end_sec[i] = clip_start_sec[i+1]（以后者开头为基准），
          保证相邻子任务时间对齐，无间隙、无重叠。
        - 各 start 保留 Gemini 给定值（已对齐各自语义事件）；
          若 Gemini 给的 start 倒序（< prev_start），则顺延为 prev_start。
        - 所有时间裁剪到 [0, video_duration]，且 clip_start_sec <= clip_end_sec
        - partial 恒为 false
    """
    subtasks = result.get("subtasks", [])
    if not subtasks:
        raise ValueError("Gemini 输出 subtasks 为空")

    n = len(subtasks)
    # 第一遍：归一化 start（首条=0，保证单调不减）
    prev_start = 0.0
    for idx, st in enumerate(subtasks):
        start = float(st.get("clip_start_sec", prev_start))
        start = max(0.0, min(start, video_duration))
        if idx == 0:
            start = 0.0
        # 保证 start 单调不减（避免倒序）
        if start < prev_start:
            start = prev_start
        st["clip_start_sec"] = round(start, 3)
        prev_start = start

    # 第二遍：令 end[i] = start[i+1]（以后者开头为基准衔接），末条 end = 视频时长
    for idx, st in enumerate(subtasks):
        if idx < n - 1:
            end = subtasks[idx + 1]["clip_start_sec"]
        else:
            end = video_duration
        # 保证 end >= start
        if end < st["clip_start_sec"]:
            end = st["clip_start_sec"]
        st["clip_end_sec"] = round(end, 3)
        st["partial"] = False
        st["step"] = idx

    # 删除零时长子任务（视频中未出现），将后续 step 前移
    removed = [s for s in subtasks if s["clip_start_sec"] == s["clip_end_sec"]]
    subtasks = [s for s in subtasks if s["clip_start_sec"] != s["clip_end_sec"]]
    for idx, st in enumerate(subtasks):
        st["step"] = idx

    result["subtasks"] = subtasks
    result["video_fps"] = float(video_fps)
    if removed:
        removed_queries = [r["query"][:24] for r in removed]
        result["_removed"] = removed_queries
    return result


def review_subtask_balance(
    result: dict[str, Any], video_duration: float, over_ratio: float = 0.15
) -> list[str]:
    """
    复审：检测时长占比异常偏长的子任务。

    阈值 = 平均占比 1/N + over_ratio(默认15%)。占比超过阈值的子任务大概率边界定错
    （误吞相邻动作 / 锚点用错），需人工复核。

    返回告警行列表（空列表 = 全部正常）。
    """
    subtasks = result.get("subtasks", [])
    n = len(subtasks)
    if n == 0 or video_duration <= 0:
        return []

    threshold = 1.0 / n + over_ratio
    warnings: list[str] = []
    for st in subtasks:
        dur = float(st.get("clip_end_sec", 0.0)) - float(st.get("clip_start_sec", 0.0))
        ratio = dur / video_duration
        if ratio > threshold:
            warnings.append(
                f"  ⚠ step{st.get('step')}「{st.get('query','')[:24]}」"
                f" 时长{dur:.1f}s 占比{ratio*100:.0f}% > 阈值{threshold*100:.0f}% "
                f"({st.get('clip_start_sec')}s→{st.get('clip_end_sec')}s)，疑似边界偏长，请人工复核"
            )
    return warnings


# ==========================================
# 5. CLI
# ==========================================
