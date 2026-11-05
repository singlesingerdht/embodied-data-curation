"""各算法共用的打分小工具：分数转等级、连续分数、权重归一化。
连续分数: quality = (val - t_min) / (t_max - t_min), clamp [0,1]
"""
from typing import List, Dict, Optional, Tuple


# ═══════════════════════════════════════════
# 连续分数核心函数
# ═══════════════════════════════════════════

def to_grade(score: float) -> str:
    """连续分数 → 等级带"""
    if score >= 95: return 'S'
    if score >= 85: return 'A'
    if score >= 70: return 'B'
    if score >= 50: return 'C'
    return 'D'


def compute_quality(val: float, t_min: Optional[float], t_max: Optional[float],
                    direction: str = "forward") -> float:
    """
    计算原子层质量分 [0, 1]。
    - forward: 分数越高越好 (val >= t_max → 1.0, val <= t_min → 0.0)
    - inverse: 分数越低越好 (val <= t_max → 1.0, val >= t_min → 0.0)
    - binary:  val >= t_min → 1.0, else 0.0
    - t_min/t_max 为 None 时跳过 (返回 1.0)
    """
    if direction == "binary":
        if t_min is None:
            return 1.0
        return 1.0 if val >= t_min else 0.0
    if t_max is None or t_min is None:
        return 1.0  # 不可靠指标跳过
    if direction == "inverse":
        if val <= t_max:
            return 1.0
        if val >= t_min:
            return 0.0
        return max(0.0, min(1.0, (t_min - val) / (t_min - t_max)))
    # forward (default)
    if val <= t_min:
        return 0.0
    if val >= t_max:
        return 1.0
    return max(0.0, min(1.0, (val - t_min) / (t_max - t_min)))


def normalize_weights(defaults: Dict[str, float],
                      overrides: Optional[Dict[str, float]] = None,
                      module_name: str = "") -> Tuple[Dict[str, float], List[str]]:
    """
    权重归一化: 用户覆盖部分权重, 剩余按默认比例分配, 保证 Σ=1.0。
    Returns: (final_weights, warnings)
    """
    if not overrides:
        return dict(defaults), []

    # 只取与当前模块相关的覆盖
    relevant_overrides = {k: v for k, v in overrides.items() if k in defaults}
    if not relevant_overrides:
        return dict(defaults), []

    result = {}
    specified = {}
    unspecified = []

    for name, dw in defaults.items():
        if name in relevant_overrides and relevant_overrides[name] != 0:
            specified[name] = relevant_overrides[name]
            result[name] = relevant_overrides[name]
        else:
            unspecified.append(name)

    specified_sum = sum(specified.values())
    warnings = []

    if unspecified:
        remaining = 1.0 - specified_sum
        if remaining < 0:
            total = specified_sum
            acc = 0.0
            for i, name in enumerate(specified):
                if i == len(specified) - 1:
                    result[name] = round(1.0 - acc, 4)
                else:
                    w = round(specified[name] / total, 4)
                    result[name] = w
                    acc += w
            for name in unspecified:
                result[name] = 0
            warnings.append(f"{module_name}模块: 权重总和={specified_sum:.2f}>1.00, 已自动归一化")
        elif remaining > 0:
            share = remaining / len(unspecified)
            for i, name in enumerate(unspecified):
                if i == len(unspecified) - 1:
                    result[name] = round(1.0 - sum(result.values()), 4)  # 最后一项收尾
                else:
                    result[name] = round(share, 4)
            if abs(specified_sum - 1.0) > 0.0001 and specified_sum != 0:
                warnings.append(f"{module_name}模块: 用户指定权重总和={specified_sum:.2f}≠1.00, 剩余已自动分配")
        else:
            for name in unspecified:
                result[name] = 0
    else:
        if abs(specified_sum - 1.0) > 0.0001:
            acc = 0.0
            for i, name in enumerate(specified):
                if i == len(specified) - 1:
                    result[name] = round(1.0 - acc, 4)
                else:
                    w = round(specified[name] / specified_sum, 4)
                    result[name] = w
                    acc += w
            warnings.append(f"{module_name}模块: 权重总和={specified_sum:.2f}≠1.00, 已自动归一化")

    return result, warnings
