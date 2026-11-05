"""Action_Smoothness report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""
from edc.algorithms.base import VERDICTS, STATUSES
from edc.algorithms.quality.shared.verdicts import SIDE


GUIDE = ('把末端轨迹里每秒来回 2–10 次的部分当作抖动（更慢的是有意动作），量它的加速度（米/秒²，1 米/秒² 约 0.1 g）。'
         '只在手停住时判断：超过复核线就复核，具体数值写在原因里（复核线是暂定值）。运动时的数值只列出来参考，不参与判断：'
         '快速伸手、急停、撒盐这类动作本身也会抬高它。手中途读数冻结也复核。没停住过、或采样率低于 10 Hz 时不适用。'
         '复核时跳到所列时刻，看手腕相机画面是否在抖。')  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    s = report.get('smoothness')
    if not s or report.get('not_applicable'):
        return '；'.join(report.get('reasons') or [])
    rate, (low, high) = s['rate_hz'], s['band_hz']
    still, motion = s['still'], s['motion']
    parts = []
    if still.get('rms_m_s2') is None:
        parts.append(f'停住时不到 {s["params"]["still_min_s"]:g} 秒，不评价')
    else:
        text = (f"停住时（{still['seconds']:.1f} 秒）抖动加速度 {still['rms_m_s2']:.2f} 米/秒²"
                f"（复核线 {still['review_m_s2']:g}）")
        if still['rms_m_s2'] > still['review_m_s2'] and still.get('worst_at_s') is not None:
            text += f"，最抖在第 {still['worst_at_s']:.1f} 秒附近（0.5 秒内 {still['worst_0p5s_m_s2']:.2f}）"
        parts.append(text)
    for f in s.get('freezes') or []:
        parts.append(f"第 {f['start_s']:.1f} 秒起位置读数完全不变 {f['duration_s']:.1f} 秒，而之前还在以 "
                     f"{f['speed_before_m_s'] * 100:.0f} 厘米/秒移动：读数可能冻结")
    if motion.get('rms_m_s2') is not None:
        parts.append(f"运动时 {motion['rms_m_s2']:.2f}（仅供参考，不参与判断）")
    arm = SIDE.get(report.get('side'), '手臂')
    scope = (f'采样率 {rate:.0f} Hz，只查到每秒来回 {high:.0f} 次以内的抖动' if high < s['params']['band_hz'][1]
             else f'查每秒来回 {low:g}–{high:g} 次的抖动')
    return f"{arm}{scope}：" + '；'.join(parts)


NOT_RUN = ((('pose/arm',), '没有末端位置：数据里没录（yaml 没写 eef_position），也没提供 URDF 来算（eef_position: fk）'),)


def arms_note(items):
    """每条臂各跑一次，格子里按臂列出结论。"""
    return '，'.join(f'{SIDE.get(i.target, i.target)}{VERDICTS.get(i.verdict) or STATUSES[i.status]}' for i in items)


def note(reports, items, labels, common):
    if any(r.get('skipped') == 'too_short' for r in reports):
        return ''
    return ' · '.join(f"{SIDE.get(r.get('side'), '')}{VERDICTS.get(i.verdict) or STATUSES[i.status]}"
                      for r, i in zip(reports, items))
