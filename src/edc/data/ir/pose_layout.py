"""位姿布局推断: 四级兜底链 值字典键名 → 消息类型 → profile 声明 → 四元数路标。

排除法: 四元数是路标（4 连续列平方和≈1），找到后唯一 3 连段 = 位置。
"""
import numpy as np
from typing import Dict, Optional, List, Tuple

# 命名布局 → 列区间
NAMED_LAYOUTS: Dict[str, Dict] = {
    "xyz":        {"position": [0, 3], "orientation": None},
    "xyz_quat":   {"position": [0, 3], "orientation": [3, 7]},   # 7 维 zerith 型
    "xyz_quat2":  {"position": [0, 3], "orientation": [3, 7]},   # 9 维 r2v2 型, 后 2 列不管
    "quat_xyz":   {"position": [4, 7], "orientation": [0, 4]},   # 四元数在头部
    "xyz_rpy":    {"position": [0, 3], "orientation": [3, 6]},   # 6 维欧拉, 仅声明可用
}


def resolve_declared(declared, D: int) -> Optional[Dict]:
    """profile 声明 → 布局 dict (method="profile")。越界则 warn 并忽略。"""
    if declared is None:
        return None
    if isinstance(declared, str):
        declared = NAMED_LAYOUTS.get(declared)
    if not isinstance(declared, dict):
        return None
    # 验证列区间不越界
    pos = declared.get("position")
    ori = declared.get("orientation")
    if pos and (pos[0] < 0 or pos[1] > D):
        import warnings
        warnings.warn(f"pose_layout 声明 position={pos} 越界 D={D}, 忽略", stacklevel=3)
        return None
    if ori and (ori[0] < 0 or ori[1] > D):
        import warnings
        warnings.warn(f"pose_layout 声明 orientation={ori} 越界 D={D}, 忽略", stacklevel=3)
        return None
    return {
        "position": pos,
        "orientation": ori,
        "method": "profile",
        "layout": declared.get("layout", "declared"),
    }


def _find_quat_windows(arr: np.ndarray, tol: float = 0.05,
                       min_hit: float = 0.80, min_rows: int = 20) -> List[Tuple[int, int]]:
    """全列扫描: 4 连续列平方和≈1 的窗口, 按命中率排序。"""
    N, D = arr.shape
    results = []
    for i in range(D - 3):
        norms = np.sum(arr[:, i:i+4] ** 2, axis=1)
        hit = float(np.mean(np.abs(norms - 1.0) < tol))
        if N >= min_rows and hit >= min_hit:
            results.append((i, i + 4, hit))
    results.sort(key=lambda x: -x[2])  # 命中率降序
    return [(a, b) for a, b, _ in results]


def _segments_from_mask(mask: list, D: int) -> List[Tuple[int, int]]:
    """True 列被掩盖, 返回连续 unmasked 段 [(a,b), ...]。"""
    segs = []
    start = None
    for i in range(D):
        if mask[i]:
            if start is not None:
                segs.append((start, i))
                start = None
        elif start is None:
            start = i
    if start is not None:
        segs.append((start, D))
    return segs


def _layout_name(pos, ori) -> str:
    """生成人类可读的布局名。"""
    if pos == [0, 3] and ori == [3, 7]:
        return "xyz_quat"
    if pos == [4, 7] and ori == [0, 4]:
        return "quat_xyz"
    if pos == [0, 3] and ori is None:
        return "xyz"
    return "custom"


def infer_pose_layout(arr: np.ndarray, tol: float = 0.05,
                      min_hit: float = 0.80, min_rows: int = 20) -> Optional[Dict]:
    """四元数路标 + 排除法 → 位姿布局。

    Returns:
      {"position": [a,b], "orientation": [a,b]|None, "method": "landmark", "layout": "xyz_quat"}
      或 {"method": "unknown", "reason": "..."} 推不出
      或 None (D<3 非二维)
    """
    if arr.ndim != 2:
        return None
    N, D = arr.shape
    if D < 3:
        return None

    # D==3: 结构性命中, 整列即位置
    if D == 3:
        return {"position": [0, 3], "orientation": None,
                "method": "structural", "layout": "xyz"}

    # 找路标
    windows = _find_quat_windows(arr, tol, min_hit, min_rows)
    if not windows:
        return {"method": "unknown",
                "reason": f"D={D}, 无四元数路标 (N={N}, 命中率<{min_hit*100:.0f}%)"}

    # 掩盖所有路标列
    masked = [False] * D
    for a, b in windows:
        for i in range(a, b):
            masked[i] = True

    # 剩余分段
    segments = _segments_from_mask(masked, D)

    # 唯一 3 连段 → position
    candidates = [(a, b) for a, b in segments if b - a == 3]
    if len(candidates) != 1:
        seg_str = " + ".join(f"[{a}:{b}](len={b-a})" for a, b in segments)
        status = "不唯一" if len(candidates) == 0 else f"多个({len(candidates)})"
        return {"method": "unknown",
                "reason": f"D={D}, 路标 {windows}, 剩余段 {seg_str}, 3连段{status}"}

    position = list(candidates[0])

    # orientation = 最佳 4 连窗口 (平票取最左)
    orientation = None
    for a, b in windows:
        if b - a == 4:
            orientation = [a, b]
            break  # 已按命中率降序, 第一个就是最佳

    return {
        "position": position,
        "orientation": orientation,
        "method": "landmark",
        "layout": _layout_name(position, orientation),
    }