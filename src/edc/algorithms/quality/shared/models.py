"""共享数据模型 (纯 Python, 去 pydantic 依赖)。

PosePoint: 位姿点, 供 action_smoothness 模块使用。
模块评分 / 子指标 在各模块 scoring.py 中以 plain dict 表示, 直接可 JSON 序列化。
"""
from dataclasses import dataclass


@dataclass
class PosePoint:
    timestamp: float
    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float
