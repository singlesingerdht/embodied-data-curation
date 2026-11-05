"""时钟诊断和平移：时间戳对齐、采集同步、时间异常归因三项共用。只读录包时间做尺子，不改原时间。"""


CALENDAR_MIN_S = 946684800.0   # 2000-01-01 UTC; earlier headers are device counters, not dates
# Record time mixes clock offset with transport/processing latency (Leju head depth arrives
# 0.2-0.4 s late on a shared clock), so only offsets beyond 1 s are named as separate clocks.
# Smaller mismatches still show in Check_Timestamp's alignment numbers.
CLOCK_GROUP_GAP_MS = 1000.0
# "Salvageable" = every channel's record-minus-header lag is the same at the start and the end of
# the episode (medians of CLOCK_WINDOWS equal slices within CLOCK_STABLE_MS), so one shift per
# channel maps it onto the record clock. Record time also carries transport latency: Leju wrist
# cameras on a correct clock still move up to ~60 ms within one episode, while clock faults are
# hundreds of ms or more. Alignment is re-checked after the shift, so this only says "shift is worth trying".
CLOCK_WINDOWS = 10
CLOCK_STABLE_MS = 100.0


def clock_diagnosis(episode, names):
    """Plain-language causes behind timing scores, for channels declared on one clock.

    Uses each message's bag record time only as a shared ruler: median(record - header) per
    channel. Same-clock channels differ only by latency; groups further apart than
    CLOCK_GROUP_GAP_MS point to unsynchronised device clocks. Never changes timestamps or scores.
    """
    import datetime
    import time
    import numpy as np
    info = episode.describe_legacy()
    latest = time.time() + 365 * 86400
    rows, offsets, notes = {}, {}, []
    for name in names:
        c = episode.channels[name]
        if c.selection:
            continue  # Derived channels share their source's timeline.
        t = np.asarray(c.timestamps, dtype=float)
        record = getattr(info.topics.get(name), 'container_timestamps_ns', None)
        row = {'clock': c.clock, 'calendar_time': bool(CALENDAR_MIN_S <= t.min() and t.max() <= latest)}
        if record is not None and len(record) == len(t):
            lag = (np.asarray(record, dtype=float) * 1e-9 - t) * 1000
            row['record_minus_header_ms'] = round(float(np.median(lag)), 1)
            if len(lag) >= CLOCK_WINDOWS * 3:
                # A fixed offset keeps the same median lag from start to end; drift or a jump moves it.
                windows = [np.median(part) for part in np.array_split(lag, CLOCK_WINDOWS)]
                row['record_minus_header_change_ms'] = round(float(max(windows) - min(windows)), 1)
        rows[name] = row
        if not row['calendar_time']:
            try:
                when = (datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
                        + datetime.timedelta(seconds=float(t[0]))).strftime('%Y-%m-%d')
            except (OverflowError, ValueError):
                when = f'{t[0]:.0f} 秒（超出日期范围）'
            notes.append(f'{name}: header 时间换算为 {when}，不是正常日期时间，可能是设备内部计数（如开机时长），'
                         '不能和其他话题直接比较时间')
        elif 'record_minus_header_ms' in row:
            offsets[name] = row['record_minus_header_ms']
    groups = []
    for name, value in sorted(offsets.items(), key=lambda item: item[1]):
        if groups and value - groups[-1]['offsets'][-1] <= CLOCK_GROUP_GAP_MS:
            groups[-1]['channels'].append(name)
            groups[-1]['offsets'].append(value)
        else:
            groups.append({'channels': [name], 'offsets': [value]})
    groups = [{'channels': g['channels'], 'record_minus_header_ms': round(float(np.median(g['offsets'])), 1),
               'record_minus_header_range_ms': [min(g['offsets']), max(g['offsets'])]} for g in groups]
    if len(groups) > 1:
        def amount(ms):
            return f'{ms / 1000:.1f} 秒' if abs(ms) >= 1000 else f'{ms:.0f} 毫秒'

        def span(g):
            lo, hi = g['record_minus_header_range_ms']
            return f'{amount(lo)}–{amount(hi)}' if hi - lo >= 10 else amount(g['record_minus_header_ms'])
        parts = [f"{'、'.join(g['channels'])}（header 比录包时间早约 {span(g)}）" for g in groups]
        spread = groups[-1]['record_minus_header_ms'] - groups[0]['record_minus_header_ms']
        notes.append(f'声明为同一时钟的话题实际分成 {len(groups)} 组：' + '；'.join(parts) +
                     f'。组间最多相差约 {amount(spread)}，疑似不同设备的时钟没有对准')
    if names and not offsets and not notes:
        notes.append('缺少录包时间，无法估计各话题的时钟偏差')
    salvage = None
    if len(groups) > 1 or any(not row['calendar_time'] for row in rows.values()):
        change = {n: row.get('record_minus_header_change_ms') for n, row in rows.items()}
        moving = {n: v for n, v in change.items() if v is None or v > CLOCK_STABLE_MS}
        salvage = {'status': 'unstable' if moving else 'fixed_offset', 'stable_ms': CLOCK_STABLE_MS,
                   'max_change_ms': max((v for v in change.values() if v is not None), default=None),
                   'moving_channels': moving}
        if moving:
            notes.append('不是固定偏差，不能简单平移：' + '、'.join(
                f'{n}（{"无法测量" if v is None else f"数据内变化 {v:.0f} 毫秒"}）' for n, v in moving.items()))
        else:
            notes.append(f'各话题与录包时间的偏差从头到尾基本不变（最多变化 {salvage["max_change_ms"]:.0f} 毫秒），'
                         '是固定偏差：按各自偏差平移时间后可重新检查（可救）')
    return {'method': 'median(record_time - header), record time as shared ruler',
            'group_gap_ms': CLOCK_GROUP_GAP_MS, 'channels': rows, 'groups': groups, 'salvage': salvage,
            'notes': notes}


def clock_shift(episode, diagnosis):
    """Seconds to add to each channel so every clock group lines up with the largest group.

    A group's clock offset is its smallest record - header: the fastest channel has almost no
    transport latency (a 400 ms depth camera must not drag its group's estimate). Channels inside
    one group keep their own latency differences. Derived channels follow their source.
    """
    groups = diagnosis['groups']
    lag = {n: g['record_minus_header_range_ms'][0] for g in groups for n in g['channels']}
    lag.update({n: row['record_minus_header_ms'] for n, row in diagnosis['channels'].items()
                if not row['calendar_time'] and 'record_minus_header_ms' in row})
    if len(set(lag.values())) < 2:
        return {}
    reference = max(groups, key=lambda g: len(g['channels']))['record_minus_header_range_ms'][0] if groups else 0.0
    def key(c):
        t = c.timestamps
        return (float(t[0]), float(t[-1]), len(t)) if t is not None and len(t) else None
    # Channels computed from another (e.g. FK poses) have no record time of their own but
    # the very same timeline as their source.
    by_timeline = {key(episode.channels[n]): lag[n] for n in lag if n in episode.channels}
    shift = {}
    for name, c in episode.channels.items():
        source = (c.selection or {}).get('channel', name)
        value = lag.get(source, by_timeline.get(key(c)))
        if value is not None and value != reference:
            shift[name] = (value - reference) / 1000
    return shift


def shift_note(shift):
    """'已把 a、b 的时间平移 +31.9 秒' grouped by amount."""
    amounts, counters = {}, []
    for name, seconds in shift.items():
        if abs(seconds) > 365 * 86400:  # header was a device counter, not a date
            counters.append(name)
        else:
            amounts.setdefault(round(seconds, 1), []).append(name)
    parts = [f"已把 {'、'.join(names)} 的时间平移 {s:+.1f} 秒" for s, names in amounts.items()]
    if counters:
        parts.append(f"已把 {'、'.join(counters)} 的设备计数时间换算到统一时钟")
    return '；'.join(parts)


class ShiftedReader:
    """The same reader, with timestamps moved by clock_shift (and derived copies left out) before an algorithm sees them."""
    def __init__(self, reader, shift, drop=()):
        self._reader, self._shift, self._drop = reader, shift, set(drop)

    def __getattr__(self, name):
        return getattr(self._reader, name)

    def require_timestamps(self, info, min_channels=2):
        ts = self._reader.require_timestamps(info, min_channels=min_channels)
        return {n: [t + self._shift[n] for t in v] if n in self._shift else v
                for n, v in ts.items() if n not in self._drop}


def derived_copies(episode, info):
    """Channels that only repeat another channel's timeline: split from one message (selection) or
    computed from it (e.g. FK poses, no record time of their own). Scoring each copy again would
    count one stream's timing several times."""
    def key(c):
        t = c.timestamps
        return (float(t[0]), float(t[-1]), len(t)) if t is not None and len(t) else None
    own = {n for n, c in episode.channels.items() if not c.selection
           and getattr(info.topics.get(n), 'container_timestamps_ns', None) is not None}
    timelines = {key(episode.channels[n]) for n in own}
    return sorted(n for n, c in episode.channels.items() if n not in own and (
        (c.selection or {}).get('channel') in own or key(c) in timelines))


def invalid_timelines(ep, scoped):
    """采集／容器时间含非有限值或倒退的通道 → 直接复核；scoped=True 时不看不参与时间检查的通道（如 metadata）。"""
    import numpy as np
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    issues = [{"channel": c.name, "reason": "nonfinite or decreasing timestamps"}
              for c in ep.channels.values() if c.timestamps and c.time_source in {"acquisition", "container"}
              and (not scoped
                   or timing_exclusion_reason(c.name, c) is None)
              and (not np.isfinite(c.timestamps).all() or np.any(np.diff(c.timestamps) < 0))]
    if issues:
        return {"verdict": "NEEDS_REVIEW", "score": None, "issues": issues,
                "method": "timeline_validation_v1"}
