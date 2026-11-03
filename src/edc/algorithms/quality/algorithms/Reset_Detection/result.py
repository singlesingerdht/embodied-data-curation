"""Reset_Detection report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""
import math

from edc.algorithms.quality.shared.verdicts import SIDE, clock_time

GUESS = {'start': '疑似不是从初始位置开始录包', 'end': '疑似录包结束时没有回到初始位置'}


GUIDE = '开头或结尾的手臂和初始位置（yaml 的 home）差得多，超过 tolerance。跳到所列时刻，和一条复位检测“通过”的数据对比：手臂明显不在初始姿态而任务要求从初始姿态开始和结束，是录早了或录晚了；手臂其实已在初始姿态，就是 home 或 tolerance 要改；任务本来就不要求复位，放过。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    tolerance = (report.get('calibration') or {}).get('tolerance')
    metres = (report.get('calibration') or {}).get('unit') == 'm'  # Tip position instead of joint angles.

    def where(e, key):
        if e.get(f'{key}_joint_index') is None:
            return ''
        time = e.get(f'{key}_time_s')
        index, offset = e[f'{key}_joint_index'], e[f'{key}_joint_offset']
        return ((f"，{'xyz'[index] if index < 3 else index + 1} 方向差约 {abs(offset) * 100:.0f} cm" if metres else
                 f"，第 {index + 1} 关节差约 {abs(math.degrees(offset)):.0f}°")
                + (f'，时刻 {clock_time(time)}' if time else ''))

    def side(channel):
        parts = channel.split('/')
        key = parts[2] if len(parts) >= 3 else next((k for k in SIDE if k in channel), '')
        return SIDE.get(key, labels.get(channel, channel))

    def amount(value):
        return f'{value * 100:.1f} cm' if metres else f'{value:.2f} rad'

    return '；'.join(f"{side(e['channel'])}{label}和初始位置不同"
                    f"（{'末端' if metres else ''}偏离 {amount(e[f'{key}_error'])}{where(e, key)}），{GUESS[key]}"
                    for e in report.get('evidence', []) if e.get('channel')
                    for key, label in (('start', '开头'), ('end', '结尾'))
                    if key in report.get('checked_ends', ('start', 'end'))
                    and tolerance is not None and e.get(f'{key}_error') is not None and e[f'{key}_error'] > tolerance)


def note(reports, items, labels, common):
    segment = next((r for r in reports if r.get('split_segment')), None)
    if segment:
        cut = segment['split_segment']
        return (f"切片（原始第 {cut.get('start_frame')}–{cut.get('end_frame')} 帧），"
                + ('只查开头' if segment.get('checked_ends') else '不查复位'))
    return common
