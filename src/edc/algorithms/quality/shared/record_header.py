"""Informational paired-clock evidence; never a score, gate, or causal diagnosis."""
import math


def paired_time_evidence(episode, threshold_ms=None):
    if threshold_ms is not None and (isinstance(threshold_ms, bool) or
            not isinstance(threshold_ms, (int, float)) or not math.isfinite(threshold_ms) or threshold_ms < 0):
        raise ValueError('record_header_deviation_threshold_ms must be null or a finite nonnegative number')
    rows = []
    for name, channel in episode.channels.items():
        # Derived views carry the same recorded evidence; count the source messages only once.
        if channel.selection:
            continue
        topic = episode.raw(name)
        record = getattr(topic, 'container_timestamps_ns', None)
        header = getattr(topic, 'acquisition_timestamps_ns', None)
        row = {'channel': name, 'threshold_ms': threshold_ms,
               'basis': 'raw container minus raw header; before clock mapping',
               'source_row_mapping': 'identity', 'source': channel.source}
        if record is None or header is None or len(record) != len(header) or not len(record):
            row.update(status='unavailable', reason='Missing paired raw record/header timestamps or unequal row counts')
            rows.append(row)
            continue
        valid = [(i, int(r) - int(h)) for i, (r, h) in enumerate(zip(record, header))
                 if r is not None and h not in (None, 0)]
        if not valid:
            row.update(status='unavailable', reason='No valid paired header timestamps (missing or zero)')
            rows.append(row)
            continue
        # Subtract integer ns BEFORE any floating-point conversion, including the median.
        # The doubled median is exact for both even and odd row counts.
        differences = sorted(d for _, d in valid)
        n = len(differences)
        center2 = differences[(n-1)//2] + differences[n//2]
        def message(pair):
            i, delta = pair
            return {'source_row': i, 'message_number': i + 1,
                    'record_ns': int(record[i]), 'header_ns': int(header[i]),
                    'difference_ms': delta / 1e6, 'deviation_ms': (2*delta-center2) / 2e6}
        extreme = max(valid, key=lambda p: abs(2*p[1]-center2))
        detail = message(extreme)
        row.update(status='available', paired_count=n, invalid_pair_count=len(record)-n,
            median_difference_ms=center2/2e6, min_difference_ms=differences[0]/1e6,
            max_difference_ms=differences[-1]/1e6,
            max_absolute_deviation_ms=abs(detail['deviation_ms']), max_deviation_message=detail)
        row['summary'] = (f"{name} 第{detail['message_number']}条：通常差值{center2/2e6:g} ms，"
            f"本条差值{detail['difference_ms']:g} ms，偏离通常水平{detail['deviation_ms']:+g} ms。")
        if threshold_ms is not None:
            exceeded = [p for p in valid if abs(2*p[1]-center2)/2e6 > threshold_ms]
            periods = []
            for p in exceeded:
                i = p[0]
                if periods and i == periods[-1]['end_source_row'] + 1:
                    periods[-1].update(end_source_row=i, end_header_ns=int(header[i]), count=periods[-1]['count']+1)
                else:
                    periods.append({'start_source_row': i, 'end_source_row': i, 'count': 1,
                        'start_header_ns': int(header[i]), 'end_header_ns': int(header[i])})
            row.update(exceedance_count=len(exceeded), exceedance_periods=periods,
                exceedance_messages=[message(p) for p in exceeded[:20]],
                details_truncated=len(exceeded)>20, detail_limit=20)
        rows.append(row)
    return {'method': 'raw_record_header_difference_v1', 'informational_only': True,
            'threshold_ms': threshold_ms, 'channels': rows,
            'reason': 'No original paired timestamp channels' if not rows else None}


def evidence_sentences(evidence):
    sentences = []
    for row in (evidence or {}).get('channels', []):
        if row['status'] != 'available':
            sentences.append(f"{row['channel']}：记录/header 时间差无法计算，{row['reason']}")
        else:
            sentences.append(row['summary'])
            sentences.append(f"差值范围 [{row['min_difference_ms']:g}, {row['max_difference_ms']:g}] ms；"
                             f"最大绝对偏离 {row['max_absolute_deviation_ms']:g} ms。")
            if row['threshold_ms'] is not None:
                sentences.append(f"显式偏离阈值 {row['threshold_ms']:g} ms：超限 {row['exceedance_count']} 条，"
                    f"{len(row['exceedance_periods'])} 段；明细最多20条"
                    + ('，已截断。' if row['details_truncated'] else '。'))
    if evidence and not evidence.get('channels'):
        sentences.append('记录/header 时间差无法计算：未选中具有原始配对时间的消息通道。')
    return sentences
