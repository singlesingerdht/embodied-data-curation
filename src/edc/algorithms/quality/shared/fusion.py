"""模块融合 (fusion): 将 A/B/C 三模块组合成统一评分与判定。

从原 routers/unified.py 的 overall_score / is_bad 逻辑抽取。
- overall_score = (avg_A + avg_B + avg_C) / 3
- is_bad = 任一模块未通过(熔断)
- verdict: is_bad→DO_NOT_USE; overall<85→NEEDS_REVIEW; else ACCEPTABLE
"""
from typing import List, Optional


def fuse_modules(modules: List[dict], threshold: float = 85.0) -> dict:
    """融合多个模块报告 (各模块为 format_report 产出或裸 module dict)。

    modules: [{module_key, score/total_score, passed, name, sub_indicators, ...}, ...]
    返回: {overall_score, is_bad, verdict, reasons, module_scores}
    NOT_APPLICABLE 模块 (score=None) 不计入 overall 平均。
    """
    module_scores = []
    any_bad = False
    reasons = []
    complete = not any(m.get('assessment_complete') is False or 'ir_unavailable' in m for m in modules)
    for m in modules:
        score = m.get("score", m.get("total_score", 0))
        passed = m.get("passed", True)
        key = m.get("module", m.get("module_key", m.get("name", "")))
        module_scores.append({"module": key, "score": score, "passed": passed,
                              "verdict": m.get("verdict", "")})
        if not passed and score is not None:
            any_bad = True
            for r in m.get("reasons", []):
                reasons.append(r)

    scorable = [s for s in module_scores if s["score"] is not None]
    if not scorable:
        return {
            "overall_score": None,
            "is_bad": False,
            "verdict": "NOT_APPLICABLE",
            "reasons": [],
            "module_scores": module_scores,
            "assessment_complete": False,
        }

    overall = round(sum(s["score"] for s in scorable) / len(scorable), 1)

    if any_bad:
        verdict = "DO_NOT_USE"
        if not reasons:
            reasons = ["存在未通过(熔断)的模块"]
    elif not complete:
        verdict = "NOT_APPLICABLE"
        reasons = ["存在未完成的评估；处理失败不构成数据质量判断"]
    elif overall < threshold:
        verdict = "NEEDS_REVIEW"
        reasons = [f"overall_score={overall} < {threshold}, 建议人工检查"]
    else:
        verdict = "ACCEPTABLE"
        reasons = []

    return {
        "overall_score": overall,
        "is_bad": any_bad,
        "verdict": verdict,
        "reasons": reasons,
        "module_scores": module_scores,
        "assessment_complete": complete,
    }
