"""Run existing timing methods on explicitly selected recorder timestamps."""
from importlib import import_module

import numpy as np

from edc.data.ir.time_axis import time_axis
from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason


BOUNDARY = ('使用容器记录时间分析消息出现的时序；结果受传输和排队影响，'
            '不能证明设备采集同步，也不能确定采集故障根因。')


def plan(episode, params):
    """Keep each source row; never substitute acquisition or generated times."""
    axes, evidence, unavailable, excluded = {}, {}, [], []
    from .cpu import topic_metadata
    info = topic_metadata(episode)
    for name, channel in episode.channels.items():
        reason = timing_exclusion_reason(name, info.topics.get(name, channel) if info else channel)
        if reason or channel.selection:
            excluded.append({'channel': name, 'reason': reason or 'derived_channel'})
            continue
        try:
            axis = time_axis(episode, name, time_source='container')
        except ValueError as exc:
            unavailable.append({'channel': name, 'reason': str(exc)})
            continue
        evidence[name] = axis
        values = axis.output
        if len(values) < 2 or np.any(np.diff(values) < 0) or values[-1] <= values[0]:
            unavailable.append({'channel': name,
                'reason': '记录时间不足两个有效间隔样本、倒退或没有正时间跨度；保留原始时间，不排序修复'})
            continue
        if not axis.params['clock']:
            unavailable.append({'channel': name, 'reason': '记录时间的 clock 未确认'})
            continue
        axes[name] = axis
    if len(axes) < 2:
        raise ValueError('记录时序分析至少需要两路有效容器时间轴及共同记录 clock；不回退到采集或合成时间')
    if len({axis.params['clock'] for axis in axes.values()}) != 1:
        raise ValueError('记录时序分析需要共同的容器 clock，不能比较不同文件或不同记录时钟')
    if params.get('primary') and params['primary'] not in axes:
        raise ValueError('参考通道缺少有效容器时间，不能自动切换参考通道')
    return dict(axes=axes, evidence=evidence, unavailable=unavailable, excluded=excluded)


def assess(episode, params, context, ident):
    prepared = plan(episode, params)
    timestamps = {name: axis.output.tolist() for name, axis in prepared['axes'].items()}
    module = import_module(f'edc.algorithms.quality.algorithms.{ident}.run_check')
    if ident == 'Sync_Check':
        result = module.assess(timestamps, primary=params.get('primary'), weights=params.get('weights'))
    else:
        from edc.algorithms.quality.algorithms.Sync_Check.sync_check import check_stability
        families = {n: g for n, g in (params.get('families') or {}).items() if n in timestamps} or None
        result = module.assess(timestamps, check_stability(timestamps),
                               weights=params.get('weights'), families=families)

    # Use the existing method's judgment for the explicitly selected recording
    # scope. Acquisition coverage is recorded separately, not substituted for it.
    method_verdict = result.get('verdict')
    result.update(method_score=result.get('score'), method_verdict=method_verdict,
                  method_passed=result.get('passed'),
                  method_assessment_complete=result.get('assessment_complete'),
                  assessment_complete=bool(result.get('assessment_complete'))
                      and not prepared['unavailable'],
                  time_source='container', establishes_acquisition_sync=False,
                  acquisition_assessment={'status': 'unassessed',
                      'reason': '本次选择记录时序模式；未使用设备采集时间及其时钟映射进行评估'})
    replacements = {'时钟': '记录相对时序', '周期性丢帧': '周期性记录间隔异常',
                    '跨通道Stall': '跨通道记录断档'}

    def recorded_text(value):
        for before, after in replacements.items():
            value = value.replace(before, after)
        return value

    for row in result.get('sub_indicators') or []:
        for key in ('name', 'raw_value'):
            if isinstance(row.get(key), str):
                row[key] = recorded_text(row[key])
    if ident == 'Sync_Check':
        sync = result['sync']
        alignments = sync['alignment']['alignments']
        p95 = max((r['p95_delta_ms'] for r in alignments.values()), default=0)
        summary = (f"记录时间诊断：{len(timestamps)} 路，记录时间覆盖率 "
                   f"{sync['coverage']['coverage_ratio']:.1%}，相对参考通道的最近邻间隔 P95 最大 {p95:g} ms")
    else:
        root = result['root_cause']
        summary = (f"记录时间诊断：{len(timestamps)} 路，关联断档 {len(root['correlated_gaps'])} 次，"
                   f"最大记录间隔 {root['max_gap_s']:g} 秒；" + recorded_text(root['verdict']))
    result['reasons'] = [summary, BOUNDARY]
    if prepared['unavailable']:
        result['reasons'].append(f"{len(prepared['unavailable'])} 路记录时间不可用，详见源时间证据")
    evidence = {name: dict(output=axis.output.tolist(), params=axis.params,
                           source_mapping=axis.source_mapping)
                for name, axis in prepared['evidence'].items()}
    artifact = context.write_result('container_timeline.json', {
        'operation': 'container_recording_diagnostic', 'params': params,
        'output': {'included_channels': list(timestamps), 'unavailable': prepared['unavailable'],
                   'excluded': prepared['excluded']}, 'source_mapping': evidence})
    result['recording_diagnostic'] = {
        'status': 'completed', 'assessment_complete': bool(result['method_assessment_complete'])
            and not prepared['unavailable'],
        'clock': next(iter(prepared['axes'].values())).params['clock'],
        'time_source': 'container', 'included_channels': list(timestamps),
        'unavailable_inputs': prepared['unavailable'], 'excluded_channels': prepared['excluded'],
        'timeline_evidence': artifact, 'boundary': BOUNDARY}
    result['method_version'] = 'container_recording_diagnostic_v2'
    return result
