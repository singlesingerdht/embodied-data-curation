from pathlib import Path
"""轻量通道描述 IR — 已收编到 data/ir, 本文件为兼容 re-export。

旧代码引用 shared.topic_info 的 TopicInfo/classify_channel/extract_timestamps
现在全部从 data.ir 重新导出。data.ir 的 TopicInfo 新增了 timestamps_synthetic 字段。
"""
import sys
import os

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

from framework.ir.metadata import (
    TopicInfo, classify_channel, normalize_time_seconds,
    MODALITY_IMAGE, MODALITY_IMU, MODALITY_JOINT_STATE,
    MODALITY_CMD, MODALITY_POSE, MODALITY_OTHER,
)
from framework.readers.base import extract_timestamps

__all__ = [
    "TopicInfo", "classify_channel", "extract_timestamps",
    "extract_modality_groups", "normalize_time_seconds",
    "MODALITY_IMAGE", "MODALITY_IMU", "MODALITY_JOINT_STATE",
    "MODALITY_CMD", "MODALITY_POSE", "MODALITY_OTHER",
]


def extract_modality_groups(topics):
    """按模态分组: {"image": ["cam_r/color", ...], "joint_state": [...]}"""
    groups = {}
    for name, ti in topics.items():
        groups.setdefault(ti.modality, []).append(name)
    return groups
