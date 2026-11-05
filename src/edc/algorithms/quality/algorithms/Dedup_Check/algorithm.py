"""时间／序列重复检查：框架怎么调用这项检查（挑原始时间输入在这里，检查方法在 method.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm, topic_metadata


def dedup_inputs(episode, selected=None, info=None):
    """Recorded timelines or an explicitly mapped raw timestamp field; no inferred clock."""
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    if info is None:
        info = topic_metadata(episode)
    inputs = []
    seen_shared = set()
    for c in episode.channels.values():
        if selected is not None and c.name != selected:
            continue
        if c.time_source in {'synthetic', 'playback'}:
            continue
        topic = info.topics[c.name] if info else c
        if c.selection or timing_exclusion_reason(c.name, topic):
            continue
        evidence = getattr(c, 'time_evidence', {})
        raw = getattr(topic, 'recorded_timestamp_values', None)
        recorded = bool(c.timestamps) and c.time_source in {'acquisition', 'container'}
        if not recorded and not (raw is not None and not evidence.get('time_generated', False)):
            continue
        if evidence.get('timeline_scope') == 'shared_dataset_rows':
            key = (c.source, evidence.get('timestamp_field'))
            if key in seen_shared:
                continue
            seen_shared.add(key)
        inputs.append((c.name, None))
    raw = episode.profile.get("timestamp_column")
    c = episode.channels.get(raw)
    if not inputs and c and (selected is None or c.name == selected) and c.value_available and len(c.fields) == 1 and not getattr(c, 'time_evidence', {}).get('time_generated'):
        inputs.append((raw, c.fields[0]))
    return inputs


def unavailable_dedup_inputs(episode, inputs=None, info=None):
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    if info is None:
        info = topic_metadata(episode)
    available = {name for name, _ in (inputs if inputs is not None else dedup_inputs(episode, info=info))}
    rows = []
    for c in episode.channels.values():
        topic = info.topics.get(c.name, c) if info else c
        if c.selection or timing_exclusion_reason(c.name, topic) or c.name in available:
            continue
        evidence = c.time_evidence
        if evidence.get('timeline_scope') == 'shared_dataset_rows' and any(
                episode.channels[name].source == c.source and
                episode.channels[name].time_evidence.get('timestamp_field') == evidence.get('timestamp_field')
                for name in available):
            continue
        rows.append({'channel': c.name, 'time_source': c.time_source, 'status': 'missing_input',
                     'reason': evidence.get('unavailable_reason') or
                         '无有效原始时间；合成时间或播放时间不能用于原始时间重复检查'})
    return rows


class Algorithm(BuiltinAlgorithm):
    name = '时间／序列重复检查'
    version = 'ir-adapter-16'
    missing_reason = '没有可用的原始时间值；合成时间不用于时间重复检查。'
    descriptions = ('至少一条非合成原始时间序列；单位未知仍可比较相等，不能据此评估采集间隔或同步。',
                    '有效序列号为可选输入；不检查图像或轨迹内容，不自动删除记录。')
    parameters = {
        'channel': {'type': ['string', 'null'], 'description': '指定原始时间通道；不填时检查各路，公共行时间只检查一次'},
        'dedup_ratio_low': {'type': 'number', 'minimum': 0, 'maximum': 1, 'default': .01,
            'description': '原有长重复段评分的警告比例阈值，严格大于才触发；不用于新短段或非相邻重复'},
        'dedup_ratio_high': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1, 'default': .05,
            'description': '原有长重复段评分的错误比例阈值，达到即触发；必须大于警告阈值'}}

    def select(self, episode, params):
        view = super().select(episode, params)
        if params.get('channel') and 'channels' in params:
            raise ValueError('Use channel or channels, not both')
        return view

    def check_inputs(self, episode, params):
        if params.get('channel'):
            selected = params['channel']
            if selected not in {name for name, _ in dedup_inputs(episode, selected)}:
                return 'missing_input', f'No usable recorded timestamp input for channel: {selected}'
        if not dedup_inputs(episode):
            return 'missing_input', self.missing_reason

    def available(self, episode, params):
        unavailable = unavailable_dedup_inputs(episode)
        partial = bool(unavailable) and not params.get('channel')
        return {'availability': 'partial' if partial else 'available',
                'reason': '部分通道缺有效原始时间；只能检查其余可用通道。' if partial else '',
                'unavailable_inputs': unavailable,
                'inputs': [{'channel': name, 'field': field,
                            'time_source': episode.channels[name].time_source,
                            'clock': episode.channels[name].clock,
                            'time_evidence': episode.channels[name].time_evidence}
                           for name, field in dedup_inputs(episode, params.get('channel'))]}

    def preflight(self, episode, params):
        if not 0 <= params.get('dedup_ratio_low', .01) < params.get('dedup_ratio_high', .05) <= 1:
            return 'missing_input', 'Require 0 <= dedup_ratio_low < dedup_ratio_high <= 1'
        return 'runnable', ''

    def assess(self, ep, params, context):
        return self._dedup(ep, params, context)

    def _dedup(self, ep, params, context=None):
        import numpy as np
        from edc.algorithms.quality.algorithms.Dedup_Check.method import assess
        selected = params.get("channel")
        inputs = dedup_inputs(ep, selected)
        if not inputs:
            raise ValueError(f"No recorded timestamp input for channel: {selected}")
        reports = []
        info = ep.describe_legacy()
        for name, field in inputs:
            c = ep.channels[name]
            topic = info.topics[name]
            timestamp_unit = ep.profile.get('timestamp_unit', 'unknown') if field else 's'
            if field:
                raw_reader = getattr(ep, "inspect_recorded_signal", None) or ep.read_signal
                values = raw_reader(name, field)
            else:
                raw_ns = (getattr(topic, 'acquisition_timestamps_ns', None) if c.time_source == 'acquisition'
                          else getattr(topic, 'container_timestamps_ns', None) if c.time_source == 'container' else None)
                stored = getattr(topic, 'recorded_timestamp_values', None)
                if raw_ns is not None:
                    values, timestamp_unit = np.asarray(raw_ns, dtype=np.int64), 'ns'
                elif stored is not None:
                    values = np.asarray(stored)
                    timestamp_unit = getattr(topic, 'recorded_timestamp_unit', None) or 'unknown'
                else:
                    values = np.asarray(c.timestamps)
            if values.ndim == 2 and values.shape[1] == 1:
                values = values[:, 0]
            if values.ndim != 1:
                raise ValueError(f"Timestamp values must be one-dimensional: {name}")
            if not np.isfinite(values).all():
                result = {"score": None, "verdict": "OBSERVATION", "assessment_complete": False,
                          "reasons": ["Nonfinite timestamp values; equality check not completed"]}
            elif len(values) < 2:
                result = {"score": None, "verdict": "NOT_APPLICABLE", "assessment_complete": False,
                          "reasons": ["At least two recorded samples are required"]}
            else:
                seqs = getattr(topic, 'seqs', None) if not field else None
                result = assess(values.tolist(), seqs, weights=params.get('weights'),
                                dedup_ratio_low=params.get('dedup_ratio_low', .01),
                                dedup_ratio_high=params.get('dedup_ratio_high', .05))
                workdir = getattr(context, 'workdir', None)
                if workdir is not None and result['dedup']['timestamp_dedup']['extra_duplicate_count']:
                    import csv, hashlib
                    from pathlib import Path
                    target = Path(workdir) / ('timestamp-reuse-' + hashlib.sha256(name.encode()).hexdigest()[:16] + '.csv')
                    seen = {}
                    with target.open('w', newline='') as stream:
                        writer = csv.writer(stream)
                        writer.writerow(['channel', 'timestamp', 'timestamp_unit', 'first_source_row',
                                         'previous_occurrence_row', 'source_row', 'adjacent'])
                        for row, value in enumerate(values.tolist()):
                            if value in seen:
                                first, previous = seen[value]
                                writer.writerow([name, value, timestamp_unit, first, previous, row, row == previous + 1])
                                seen[value] = first, row
                            else:
                                seen[value] = row, row
                    result['timestamp_reuse_evidence'] = {'path': str(target), 'source_rows': 'zero based',
                        'row_count': result['dedup']['timestamp_dedup']['extra_duplicate_count']}
            result.update(input_channel=name, input_field=field, time_source=c.time_source,
                          clock=c.clock, timestamp_unit=timestamp_unit,
                          comparison="raw equality" if field else "recorded timestamp equality",
                          time_evidence=getattr(c, 'time_evidence', {}), source_row_mapping='identity',
                          establishes_acquisition_sync=False)
            if c.time_evidence.get('timeline_scope') == 'shared_dataset_rows':
                timestamp_field = c.time_evidence.get('timestamp_field') or ep.profile.get('timestamp_column')
                result.update(input_label=f'公共时间字段 {timestamp_field}', timestamp_field=timestamp_field,
                              inspection_scope='shared_timestamp_equality_only')
                result['reasons'] = (result.get('reasons') or []) + [
                    '仅检查公共时间字段，相同时间轴只检查一次；input_channel 保留关联来源，不表示检查图像或数值内容。']
            if timestamp_unit == 'unknown' or c.time_source not in {'acquisition', 'container'}:
                result['reasons'] = (result.get('reasons') or []) + [
                    '仅比较文件中已有时间原值是否相同；单位或时间来源未确认，不能检查真实采集间隔、速度或跨通道同步。']
            reports.append(result)
        if reports:
            reports[0]['unavailable_inputs'] = unavailable_dedup_inputs(ep, info=info)
            if reports[0]['unavailable_inputs'] and not selected:
                reports[0]['assessment_complete'] = False
                if reports[0].get('verdict') == 'ACCEPTABLE':
                    reports[0].update(verdict='OBSERVATION', passed=None)
                reports[0]['reasons'] = (reports[0].get('reasons') or []) + [
                    '部分原始通道缺有效时间，未完成全通道时间重复检查；已发现的问题仍保留。']
        return reports
