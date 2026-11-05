"""Numeric_Check report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""
from edc.algorithms.quality.shared.verdicts import SIDE, clock_time


GUIDE = '在回放工具里跳到所列时刻（北京时间）看手臂。速度超限：确实在快速运动就放过，只有一两帧突然跳变则是数据毛刺。数值连续不变：持续很久且画面里手臂在动才是冻结。关节读数卡住判“可救”：整条手臂关节读数不变、但末端位置和画面在动，是关节数据没更新；末端位姿是对的，可以用逆运动学从末端位姿反算出这段关节角。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    def events(kind):
        return [e for c in report.get('review_candidates') or [] for e in c.get('events') or [] if e.get('type') == kind]

    outside = next((c for c in report.get('supplemental_checks') or []
                    if c.get('id') == 'declared_range' and c.get('verdict') == 'DO_NOT_USE'), None)
    if outside:
        low, high = outside['threshold']
        shown = '；'.join(f"第 {e['component_index'] + 1} 个数 {e['rows']} 行，最远 {e['value']:g}" for e in outside['events'][:3])
        return f"{outside['rows_over']}/{outside['rows']} 行超出 yaml 写的范围 [{low:g}, {high:g}]（{shown}）"
    stuck = events('joint_stuck_while_pose_moves')
    if stuck:
        stuck.sort(key=lambda e: -e['duration_s'])
        channel = report.get('input_channel', '')
        arm = SIDE.get(channel.split('/')[2] if channel.count('/') >= 3 else '', '手臂')
        shown = '；'.join(f"{clock_time(e['start_time_s'])} 起 {e['duration_s']:.1f} 秒读数不变，其中 "
                         f"{e.get('pose_moving_s', e['duration_s']):.1f} 秒末端确实在动（最远 {e['pose_move_m'] * 100:.0f} cm）"
                         + ('，一直到数据结尾' if e.get('reaches_end') else '') for e in stuck[:3])
        return (f'{arm}关节读数卡住：关节读数一个都没变，但{arm}末端在动（{shown}）。'
                '这段时间关节数据没有更新，末端位置、姿态和画面是对的')
    fast = events('joint_velocity_over_limit')
    if fast:
        fast.sort(key=lambda e: -abs(e['peak_value']))
        shown = '；'.join(f"第 {e['component_index'] + 1} 关节"
                         + (f" {clock_time(e['start_time_s'])} 起 {e['duration_s']:.2f} 秒" if e.get('start_time_s') else '')
                         + f"，峰值 {abs(e['peak_value']):.2f}"
                         + (f"（上限 {e['limit']:g}）" if len({x['limit'] for x in fast}) > 1 else '') for e in fast[:3])
        more = f'（另有 {len(fast) - 3} 段）' if len(fast) > 3 else ''
        limits = sorted({e['limit'] for e in fast})
        return (f"{len(fast)} 段速度超过 {limits[0]:g} rad/s：" if len(limits) == 1 else
                f"{len(fast)} 段速度超过本关节上限（rad/s）：") + f"{shown}{more}"
    frozen = events('constant_after_motion_candidate')
    if frozen:
        frozen.sort(key=lambda e: -(e.get('duration_s') or 0))
        part = '分量' if report.get('input_channel', '').startswith('imu') else '关节'
        shown = '；'.join(f"第 {e['component_index'] + 1} {part}"
                         + (f" {clock_time(e['start_time_s'])} 起 {e['duration_s'] * 1000:.0f} 毫秒" if e.get('start_time_s') else '')
                         + f"（{e['sample_count']} 个样本）" for e in frozen[:3])
        more = f'（另有 {len(frozen) - 3} 处）' if len(frozen) > 3 else ''
        return f'{len(frozen)} 处“动过之后数值连续不变”（可能是冻结，也可能只是正常停住）：{shown}{more}'
    return ''


def target(report, labels):
    """“通道 字段”，通道有中文名时用中文名；没有字段的报告按通用规则。"""
    channel, field = report.get('input_channel', ''), report.get('input_field')
    if field:
        return f'{labels[channel]} {field}' if channel in labels else f'{channel}.{field}'


def note(reports, items, labels, common):
    return common
