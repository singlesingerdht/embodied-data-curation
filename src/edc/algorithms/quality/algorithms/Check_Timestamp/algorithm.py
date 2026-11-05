"""时间戳对齐：框架怎么调用这项检查（时钟平移、评分标准在这里，检查方法在 run_check.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm, capabilities
from edc.algorithms.quality.shared.clock import clock_diagnosis, clock_shift, derived_copies, shift_note, ShiftedReader


def shared_row_timeline(episode):
    """True when every timed channel uses the one stored row time (e.g. an export already aligned frame by
    frame): there is no independent channel time to align, only the row intervals themselves."""
    timed = [c for c in episode.channels.values() if c.timestamps]
    return bool(timed) and all((c.time_evidence or {}).get('timeline_scope') == 'shared_dataset_rows' for c in timed)


def shared_row_intervals(episode, max_gap_s, cadence):
    """Check_Timestamp on a shared row timeline (per-robot `shared_row_max_gap_s`): rows must go forward and
    no interval may exceed the limit; a longer interval is a jump in every channel and video at once."""
    import numpy as np
    if not np.isfinite(max_gap_s) or max_gap_s <= 0:
        raise ValueError('shared_row_max_gap_s must be a positive number of seconds')
    channel = next(c for c in episode.channels.values() if c.timestamps)
    times = np.asarray(channel.timestamps, dtype=float)
    dt = np.diff(times)
    bad = np.flatnonzero(~(dt > 0) | (dt > max_gap_s))
    events = [{'row': int(i + 1), 'time_s': float(times[i]), 'interval_s': float(dt[i]),
               'type': 'gap' if dt[i] > max_gap_s else 'not_forward'} for i in bad]
    median = float(np.median(dt)) if len(dt) else None
    reasons = ([f'所有数据和画面共用一条行时间（导出时已逐帧对齐），只检查行间隔：中位数 {median * 1000:.0f} 毫秒，'
                f'没有超过 {max_gap_s:g} 秒的空档'] if not events else
               [f'行时间有 {sum(e["type"] == "gap" for e in events)} 处空档（超过 {max_gap_s:g} 秒）、'
                f'{sum(e["type"] == "not_forward" for e in events)} 处不前进；空档处所有数据和画面一起跳过']) if len(dt) else ['只有一行']
    return {'score': None, 'passed': not events, 'verdict': 'NEEDS_REVIEW' if events else 'ACCEPTABLE',
            'assessment_complete': True, 'method_version': 'shared_row_intervals_v1',
            'shared_row_intervals': {'channel': channel.name, 'rows': int(len(times)), 'max_gap_s': max_gap_s,
                                     'median_interval_s': median, 'largest_interval_s': float(dt.max()) if len(dt) else None,
                                     'events': events[:50], 'event_count': len(events)},
            'review_candidates': [{'type': e['type'], 'time_s': e['time_s'], 'interval_s': e['interval_s']}
                                  for e in events[:50]],
            'cadence': cadence, 'cross_channel_alignment': {'status': 'not_applicable',
                'reason': 'All channels share one stored row timeline; there is no independent channel time to align'},
            'reasons': reasons}


# Check_Timestamp's own command-line options (run_check.py argparse); values from timestamp_standard.
TIMESTAMP_SCORING = {'absolute_error_floor_ms': 'DEFAULT_ABSOLUTE_ERROR_FLOOR_MS',
                     'dt_cv_no_penalty': 'DEFAULT_DT_CV_NO_PENALTY', 'dt_cv_zero': 'DEFAULT_DT_CV_ZERO',
                     'long_gap_ratio_warning': 'DEFAULT_LONG_GAP_RATIO_WARNING',
                     'long_gap_ratio_zero': 'DEFAULT_LONG_GAP_RATIO_ZERO', 'iou_threshold': 'DEFAULT_IOU_THRESHOLD'}


def timestamp_standard(episode):
    """所有机型统一的时间戳评分标准：对齐误差的尺子最小取最快相机一帧的 1/3（训练按相机帧取数据）；
    消息间隔的变异系数 0.3 起扣分、0.6 扣满（普通电脑录包、导出的调度抖动常见 0.17–0.30）。"""
    import numpy as np
    periods = [p for p in (float(np.median(np.diff(c.timestamps))) for c in episode.channels.values()
               if c.modality == 'rgb' and c.timestamps is not None and len(c.timestamps) > 1) if p > 0]
    floor = {'absolute_error_floor_ms': round(min(periods) * 1000 / 3, 1)} if periods else {}
    return {'dt_cv_no_penalty': 0.3, 'dt_cv_zero': 0.6, **floor}


class timestamp_scoring:
    """Run Check_Timestamp with per-robot values for its own CLI options; restored afterwards.

    assess_from_ir builds its options from these module defaults at call time; the method file is unchanged.
    """
    def __init__(self, module, values):
        unknown = set(values or {}) - set(TIMESTAMP_SCORING)
        if unknown:
            raise ValueError(f'Unknown Check_Timestamp scoring option: {sorted(unknown)}')
        self.module, self.values, self.saved = module, values or {}, {}

    def __enter__(self):
        for key, value in self.values.items():
            name = TIMESTAMP_SCORING[key]
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, float(value))

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)


class Algorithm(BuiltinAlgorithm):
    name = '时间戳对齐'
    version = 'ir-adapter-20'
    inputs = ('timestamps',)
    timed = True
    descriptions = ('已有、单位明确的记录时间可逐路观察间隔；公共行时间只表示公共行节奏。',
                    '跨通道对齐评分另需独立时间轴、共同 clock、有效时间以及相机参考。')
    parameters = {'shared_row_max_gap_s': {'type': 'number', 'exclusiveMinimum': 0,
        'description': '所有通道共用一条行时间（导出时已逐帧对齐）时，相邻两行最多相隔多少秒；超过算空档、进复核。不填按中位行间隔的 3 倍'}}

    def available(self, episode, params):
        if 'multi_timestamps' not in capabilities(episode) or not any(
                c.modality == 'rgb' and c.timestamps for c in episode.channels.values()):
            return {'availability': 'partial', 'scope': 'single_timeline_observation',
                    'inputs': [{'channel': c.name, 'time_source': c.time_source, 'clock': c.clock}
                               for c in episode.channels.values()],
                    'reason': '可观察现有单路或公共行时间的间隔；缺少独立时间轴、共同 clock 或相机参考，跨通道对齐不评估。'}

    def assess(self, ep, params, context):
        module = self.method()
        fn = module.assess_from_ir
        info, reader = ep.algorithm_view()
        from edc.algorithms.quality.algorithms.Check_Timestamp.cadence import cadence_report
        cadence = cadence_report(ep)
        params = dict(params)
        row_gap = params.pop('shared_row_max_gap_s', None)
        if shared_row_timeline(ep):
            if row_gap is None:  # Default: a gap is an interval over 3 times the median row interval.
                import numpy as np
                times = np.asarray(next(c for c in ep.channels.values() if c.timestamps).timestamps, float)
                steps = np.diff(times)
                row_gap = 3 * float(np.median(steps[steps > 0])) if (steps > 0).any() else 1.0
            return shared_row_intervals(ep, float(row_gap), cadence)
        has_camera = any(c.modality == 'rgb' and c.timestamps and
                         c.time_source in {'acquisition', 'container'} for c in ep.channels.values())
        invalid = any(row.get('backward_interval_count', 0) or
                      (row['status'] == 'missing_input' and row.get('time_source') in {'container', 'acquisition'})
                      for row in cadence['channels'])
        if 'multi_timestamps' not in capabilities(ep) or not has_camera or invalid:
            return {'score': None, 'passed': None, 'verdict': 'OBSERVATION',
                    'assessment_complete': False, 'cadence': cadence,
                    'cross_channel_alignment': {'status': 'missing_input',
                        'reason': 'Invalid timeline' if invalid else 'Common clock and camera reference not confirmed'},
                    'reasons': ['已逐路检查记录时间间隔；无效时间轴、共同 clock 或参考条件不足时不计算跨路得分'],
                    'method_version': 'native_cadence_with_clock_gate_v1'}
        diagnosis = clock_diagnosis(ep, [c.name for c in ep.channels.values()
            if c.time_source == 'acquisition' and c.clock and c.timestamps])
        # A fixed clock offset is already judged by Sync_Check; shift it out and judge the
        # alignment itself. A moving offset cannot be shifted out, so it is judged as recorded.
        shift = clock_shift(ep, diagnosis) if (diagnosis['salvage'] or {}).get('status') == 'fixed_offset' else {}
        params = dict(params)
        scoring = timestamp_standard(ep)  # 所有机型统一的评分标准，yaml 里不写
        drop = derived_copies(ep, info)
        with timestamp_scoring(module, scoring):
            result = fn(info, ShiftedReader(reader, shift, drop) if shift or drop else reader, **params)
        result['cadence'] = cadence
        result['clock_diagnosis'] = diagnosis
        if scoring:
            result['scoring_overrides'] = dict(scoring)
        if drop:
            result['derived_copies_not_scored'] = drop
        if shift:
            result['clock_shift_s'] = shift
            result['reasons'] = [f'时钟有固定偏差（见采集同步），{shift_note(shift)}后再评价对齐'] + (result.get('reasons') or [])
        else:
            result['reasons'] = (result.get('reasons') or []) + diagnosis['notes']
        result['cross_channel_alignment'] = {'status': 'evaluated',
            'time_source': next(c.time_source for c in ep.channels.values() if c.timestamps),
            'establishes_acquisition_sync': False}
        return result
