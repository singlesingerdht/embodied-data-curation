"""config/ — 机器人级 profile 配置系统 (仿 DataFilter)。

每个 profile 是一个 YAML 文件 (profiles/<robot>.yaml), 声明:
  - topics: {通配名: {modality, fields}}  ← fnmatch 通配, 覆写分类+fields
  - required_channels: [...]               ← 可选, 注入 Structure_Check
  - formats: [episode, ...]               ← 可选, 约束格式

识别链 (三级):
  ① 显式参数 robot="quanta_x1"
  ② 路径段匹配 (DF detect_robot 同款)
  ③ 内容签名: open 后通道名 vs 模式命中率≥30%

应用方式 (post-open patch, 8 reader 零改动):
  info = open_info(path)  →  DataReader.create → open → detect_robot → apply_profile
"""
import os
import sys
import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Any

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from framework.ir.metadata import DataInfo, TopicInfo, classify_channel

PROFILES_DIR = Path(__file__).parent / "profiles"
_NUMERIC_MODS = {"joint_state", "cmd", "imu", "pose"}


def list_profiles() -> List[str]:
    """列出所有可用 profile 名 (= yaml 文件 stem)。"""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(f.stem for f in PROFILES_DIR.glob("*.yaml"))


def load_profile(robot: str) -> Optional[dict]:
    """加载 profile yaml → dict {topics, required_channels, formats, note}。"""
    if not robot:
        return None
    p = PROFILES_DIR / f"{robot}.yaml"
    if not p.is_file():
        return None
    import yaml
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def detect_robot(path: str) -> Optional[str]:
    """从路径段匹配 profile 名 (DF detect_robot 同款)。

    精确匹配 → 前缀匹配 (路径段是某 profile 名的前缀)。
    """
    known = set(list_profiles())
    if not known:
        return None
    parts = list(Path(path).parts)
    # 精确
    for part in reversed(parts):
        if part in known:
            return part
    # 前缀
    for part in reversed(parts):
        matches = sorted(k for k in known if k.startswith(part + "_"))
        if matches:
            return matches[0]
    return None


def match_profile_by_content(info: DataInfo, threshold: float = 0.3) -> Optional[str]:
    """内容签名: 用通道名 vs 各 profile 的模式集, 命中率≥threshold 取最高。"""
    profiles = list_profiles()
    if not profiles or not info.topics:
        return None
    channel_names = set(info.topics.keys())
    best_robot, best_score = None, 0.0
    for robot in profiles:
        prof = load_profile(robot)
        if not prof or not prof.get("topics"):
            continue
        patterns = list(prof["topics"].keys())
        hits = sum(1 for ch in channel_names
                   if any(fnmatch.fnmatch(ch, pat) for pat in patterns))
        # Profiles intentionally list only discriminative channels, while a
        # bag may contain dozens of unrelated telemetry topics. Dividing by
        # every channel made rich ROS2 MCAP files impossible to recognize
        # (e.g. 3 signature hits / 32 total topics). Measure signature recall.
        score = hits / len(patterns) if patterns else 0
        if score > best_score:
            best_score, best_robot = score, robot
    if best_score >= threshold:
        return best_robot
    return None


def apply_profile(info: DataInfo, profile: Optional[dict]) -> DataInfo:
    """post-open patch: fnmatch 遍历 topics, 覆写 modality/fields/pose_layout, 重算能力位。"""
    if not profile or not profile.get("topics"):
        return info
    topics_spec = profile["topics"]
    for name, ti in info.topics.items():
        for pat, spec in topics_spec.items():
            if not isinstance(spec, dict):
                continue
            if fnmatch.fnmatch(name, pat):
                new_mod = spec.get("modality")
                new_fields = spec.get("fields")
                new_pose_layout = spec.get("pose_layout")
                if new_mod:
                    ti.modality = new_mod
                if new_fields is not None:
                    ti.fields = new_fields
                if new_pose_layout is not None:
                    ti.pose_layout = new_pose_layout  # 声明优先, 推断跳过
                # 重算能力位
                ti.value_available = (ti.modality in _NUMERIC_MODS and ti.value_available) \
                    or (new_mod in _NUMERIC_MODS)
                if ti.modality == "other" and new_mod and new_mod != "other":
                    ti.value_available = new_mod in _NUMERIC_MODS
                break
    return info


def open_info(path: str, robot: Optional[str] = None) -> tuple:
    """统一入口: create reader → open → 自动 profile → (info, reader)。

    robot 优先级: 显式参数 > 路径匹配 > 内容签名。
    """
    from framework.readers.base import DataReader
    reader = DataReader.create(path)
    info = reader.open(path)
    # profile 自动识别
    resolved = robot or detect_robot(path)
    if not resolved:
        resolved = match_profile_by_content(info)
    if resolved:
        profile = load_profile(resolved)
        info = apply_profile(info, profile)
        info.extra["profile"] = resolved
    return info, reader
