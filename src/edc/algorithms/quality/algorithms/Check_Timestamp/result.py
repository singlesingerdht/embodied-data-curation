"""Check_Timestamp report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""
import re

from edc.algorithms.quality.shared.verdicts import clock_time, texts
from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import (
    DEFAULT_PASS_THRESHOLD as _PASS, DEFAULT_REVIEW_THRESHOLD as _REVIEW)


GUIDE = (f'每一路单独打分，整条结论看所有路的平均分：{_PASS:g} 分及以上通过，{_REVIEW:g}–{_PASS:g} 复核，'
         f'{_REVIEW:g} 以下排除。对每帧画面找时间上最近的那条动作消息，两者的时间差叫拼接误差；'
         'P99 是最差 1% 的拼接误差，小于相机半帧时配对影响很小。所有数据和画面共用一条时间的格式，'
         '只查有没有跳帧：跳到所列时刻看画面是否突然跳一下、是否正好落在关键动作上。')  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    jumps = (report.get('pause_boundary_jumps') or {}).get('jumps') or []
    if jumps:
        listed = '；'.join(
            f"{clock_time(j['start_time'])}处相机空档（{j['gap_ms']:.0f} 毫秒）前后画面显著变化"
            f"（{labels.get(j.get('camera'), j.get('camera', ''))}，变化像素占比 {j.get('frac', 0):.2f}）"
            for j in jumps[:3])
        more = f'，共 {len(jumps)} 处' if len(jumps) > 3 else ''
        thr = report['pause_boundary_jumps'].get('threshold_frac') or 0.26
        return (f'子任务切换跳帧：{listed}{more}。超过 {thr:.2f} 判定阈值，'
                '空档期间画面没录上、前后画面突然跳一下，判不合格')
    if report.get('shared_row_intervals'):
        rows = report['shared_row_intervals']
        gaps = [e for e in rows['events'] if e['type'] == 'gap']
        back = [e for e in rows['events'] if e['type'] != 'gap']
        if not rows['events']:
            return ''
        listed = '、'.join(f"{clock_time(e['time_s'])}（空 {e['interval_s']:.2f} 秒）"
                          for e in sorted(gaps, key=lambda e: -e['interval_s'])[:3])
        return ('所有数据和画面共用一条时间（导出时已逐帧对齐），只查有没有跳帧：'
                + (f"{len(gaps)} 处空档（相邻两行相隔超过 {rows['max_gap_s']:g} 秒），最长的：{listed}" if gaps else '')
                + (f"；{len(back)} 处时间不前进" if back else '') + '。空档处画面和动作一起跳过')
    lines = texts(report)
    topics, seen = [], set()
    for score, name, why in sorted((float(m.group(2)), m.group(1), m.group(3)) for t in lines
                                   if (m := re.match(r'topic (\S+)：score=([\d.]+)，(.*)', t))):
        if (round(score, 1), why) not in seen:  # Channels split from one message repeat its result.
            seen.add((round(score, 1), why))
            topics.append((score, name, why))
    mean = next((m for t in lines if (m := re.search(r'共评价 (\d+) 个 topic，算术平均分为 ([\d.]+)', t))), None)
    # The verdict comes from the mean over all topics, not from the worst one.
    reference = next((m.group(1) for t in lines if (m := re.search(r'reference topic 为 ([^\s。，]+)', t))), None)
    ref = labels.get(reference, reference) if reference else '对照相机'
    head = (f'每一路都拿来和{ref}对比打分，{mean.group(1)} 路平均 {float(mean.group(2)):.1f} 分'
            f'（{_PASS:g} 分及以上通过，{_REVIEW:g}–{_PASS:g} 复核，{_REVIEW:g} 以下排除）。'
            if mean else '')
    if topics:
        return head + '分数最低的：' + ''.join(sentence(report, name, score, reference or '对照相机', why, labels)
                                          for score, name, why in topics[:2])
    return head + '没有单独点名出问题的话题' if head else ''


def sentence(report, name, score, reference, labels_text, labels):
    """One plain sentence per topic from what the algorithm recorded."""
    label = labels.get(name, name)
    called = f'{label}（{name}）' if label != name else name
    ref = labels.get(reference, reference)
    sub = next((s for s in report.get('sub_indicators') or [] if s.get('name') == name), {})
    atom = {d.get('name'): d for d in sub.get('details') or []}
    iou = re.search(r'IoU=([\d.]+)', sub.get('raw_value', ''))
    rows = {c.get('channel'): c for c in (report.get('cadence') or {}).get('channels') or []}

    def ms(row, key):
        return row[key] * 1000 if row.get(key) is not None else None

    row = rows.get(name, {})
    low, high, median = ms(row, 'min_interval_s'), ms(row, 'max_interval_s'), ms(row, 'median_positive_interval_s')
    frame = ms(rows.get(reference, {}), 'median_positive_interval_s')
    found = [x for x in labels_text.split('；') if x]
    parts = []
    if '采集时间范围或采样周期不可评价' in found:
        parts.append(f'{label}和{ref}的录制时间段只重合 {float(iou.group(1)):.0%}（算法要求至少 95%），不够就直接记 0 分，计入平均分'
                     if iou else f'{label}的录制时间段或采样周期没法评价，直接记 0 分，计入平均分')
    unit = '一帧' if any('相机流' in x for x in found) or (median or 0) > 25 else '一条'
    if median and low is not None and high is not None and any(
            x in found for x in ('动作流采样不稳定', '相机流采样不稳定', '动作流局部中断')):
        text = f'本该每 {median:.0f} 毫秒发{unit}，实际间隔在 {low:.1f} 到 {high:.1f} 毫秒之间变化'
        missing = round(high / median) - 1
        if missing >= 1:
            text += f'，最长一次隔了 {high:.1f} 毫秒（中间约少了 {missing} {unit[1:]}）'
        parts.append(text)
    p99 = atom.get('P99近邻误差') or {}
    if any(x in found for x in ('P99 尾部误差偏大', 'P50 对齐误差偏大')) and p99.get('val') is not None:
        text = (f'{label}和{ref}逐帧配对时，最差的 1% 帧相差 {p99["val"]:.1f} 毫秒' if unit == '一帧' else
                f'因此给画面配{label}时，最差的 1% 画面要差 {p99["val"]:.1f} 毫秒才能找到{label}')
        if p99.get('t_min') is not None:
            text += f'（算法要求 {p99["t_min"]:.1f} 毫秒以内才不扣分）'
        if frame:
            text += (f'。{ref}每 {frame:.0f} 毫秒一帧，这点误差对配对影响很小' if p99['val'] < frame / 2 else
                     f'。{ref}每 {frame:.0f} 毫秒一帧，这个误差已接近或超过半帧，{ref}和{label}可能错开一帧')
        parts.append(text)
    if '参考帧匹配覆盖率不足' in found and (atom.get('匹配覆盖率') or {}).get('val') is not None:
        parts.append(f'{ref}只有 {atom["匹配覆盖率"]["val"]:.1f}% 的帧找到了对应的{label}')
    for key, text in (('固定延迟', f'{label}的时间比{ref}整体早或晚一截'),
                      ('基准时间偏移', f'{label}的时间相对{ref}整体有偏移'),
                      ('时钟漂移', f'{label}的时间相对{ref}越走越偏')):
        if key in found:
            offset = (atom.get('系统偏移') or {}).get('val')
            parts.append(text + (f'（约 {abs(offset):.1f} 毫秒）' if key != '时钟漂移' and offset is not None else ''))
    known = {'采集时间范围或采样周期不可评价', '动作流采样不稳定', '相机流采样不稳定', '动作流局部中断', 'P99 尾部误差偏大',
             'P50 对齐误差偏大', '参考帧匹配覆盖率不足', '固定延迟', '基准时间偏移', '时钟漂移', '跨流抖动'}
    parts += [x for x in found if x not in known]
    return f'{called}和{ref}对比 {score:.0f} 分：' + '；'.join(parts) + '。'


def note(reports, items, labels, common):
    rows = next((r['shared_row_intervals'] for r in reports if r.get('shared_row_intervals')), None)
    if rows:
        return f"共用一条时间，{rows['event_count']} 处跳帧" if rows['event_count'] else '共用一条时间，没有跳帧'
    jumps = next((r['pause_boundary_jumps']['jumps'] for r in reports
                  if (r.get('pause_boundary_jumps') or {}).get('jumps')), None)
    if jumps:
        return '；'.join(filter(None, [f'子任务切换跳帧 {len(jumps)} 处', common]))
    return common
