"""归因 (attribution) 与判定 (verdict) 生成器。

从原 routers/unified.py 的归因收集与 verdict/reasons 逻辑抽取, 改为作用于
单个模块 dict (plain dict, 由各模块 scoring.py 产出)。

模块 dict 形状:
    {
      "name": str, "module_key": str, "max_score": 100, "total_score": float,
      "passed": bool, "grade": str, "warnings": [str],
      "sub_indicators": [ {name, weight, passed, score, gap, weighted_gap,
                           raw_value, details, ...}, ... ],
    }
"""
from typing import List, Tuple


def build_attribution(module: dict) -> List[dict]:
    """收集模块内所有 weighted_gap > 0 的原子, 按 weighted_gap 降序。

    - 复合子指标 (details 非空): 逐原子收集 gap>0 者。
    - 简单子指标 (details 为空): 子指标自身作为原子。
    """
    attribution = []
    for sub in module.get("sub_indicators", []):
        if sub.get("weight", 0) == 0:
            continue
        details = sub.get("details") or []
        if details:
            for atom in details:
                atom_gap = atom.get("gap", 0)
                if atom_gap > 0:
                    attribution.append({
                        "sub_indicator": sub["name"],
                        "atom": atom.get("name", ""),
                        "value": atom.get("val", 0),
                        "t_min": atom.get("t_min", 0),
                        "t_max": atom.get("t_max", 0),
                        "quality": atom.get("quality", 0),
                        "score": atom.get("score", 0),
                        "gap": atom_gap,
                        "weighted_gap": round(atom_gap * sub.get("weight", 0), 1),
                        "direction": sub.get("direction", ""),
                    })
        else:
            gap = sub.get("gap", 0)
            if gap > 0:
                attribution.append({
                    "sub_indicator": sub["name"],
                    "atom": sub["name"],
                    "value": sub.get("current_value", 0) or 0,
                    "t_min": sub.get("t_min", 0) or 0,
                    "t_max": sub.get("t_max", 0) or 0,
                    "quality": sub.get("quality", 0),
                    "score": sub.get("score", 0),
                    "gap": gap,
                    "weighted_gap": sub.get("weighted_gap", 0),
                    "direction": sub.get("direction", ""),
                })
    attribution.sort(key=lambda x: -x["weighted_gap"])
    return attribution


def build_verdict_reasons(module: dict, threshold: float = 85.0) -> Tuple[str, List[str]]:
    """生成单模块判定: verdict + reasons。

    - 模块未通过 (任一熔断原子=0): DO_NOT_USE, reasons=失败子指标明细。
    - 通过但 total_score < threshold: NEEDS_REVIEW。
    - 否则: ACCEPTABLE。
    """
    name = module.get("name", module.get("module_key", "module"))
    subs = module.get("sub_indicators", [])
    failed = [s for s in subs if not s.get("passed", True) and s.get("weight", 0) > 0]

    if not module.get("passed", True):
        reasons = [
            f"{name}/{s['name']}: score={s.get('score', 0)} {str(s.get('raw_value', ''))[:60]}"
            for s in failed
        ]
        if not reasons:
            reasons = [f"{name}: 模块未通过(熔断), 无具体失败子指标"]
        return "DO_NOT_USE", reasons

    if module.get("total_score", 0) < threshold:
        return "NEEDS_REVIEW", [
            f"{name} score={module.get('total_score', 0)} < {threshold}, 建议人工检查"
        ]

    return "ACCEPTABLE", []


def format_report(module: dict, threshold: float = 85.0) -> dict:
    """组装单模块的最终输出契约: score + grade + verdict + reasons + 归因 + 子指标。

    各算法的 method.py 调用此函数得到统一输出结构。
    """
    attribution = build_attribution(module)
    verdict, reasons = build_verdict_reasons(module, threshold)
    return {
        "module": module.get("module_key", ""),
        "name": module.get("name", ""),
        "score": module.get("total_score", 0),
        "grade": module.get("grade", ""),
        "passed": module.get("passed", False),
        "verdict": verdict,
        "reasons": reasons,
        "attribution": attribution,
        "sub_indicators": module.get("sub_indicators", []),
        "warnings": module.get("warnings", []),
        "threshold_profile": module.get("threshold_profile"),
    }
