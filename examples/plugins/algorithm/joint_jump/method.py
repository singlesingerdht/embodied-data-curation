"""关节跳变的方法本体：只做计算，不认识框架，可以单独测试。"""
import numpy as np


def jumps(angles, review, exclude):
    """关节角（N 行 × 关节数，rad）→ 相邻两帧的最大变化、变化超过 review 的行号、结论。

    最大变化超过 exclude 判排除；有行超过 review 判复核；否则通过。"""
    angles = np.asarray(angles, dtype=float)
    step = np.abs(np.diff(angles, axis=0)).max(axis=1) if len(angles) > 1 else np.zeros(0)
    worst = float(step.max()) if step.size else 0.0
    rows = np.flatnonzero(step > review) + 1
    verdict = 'EXCLUDE' if worst > exclude else 'REVIEW' if rows.size else 'PASS'
    return {'max_step_rad': worst, 'rows': rows.tolist(), 'verdict': verdict}
