"""Link native visual candidates to physical conclusions without inventing labels."""
import json

from edc.reporting.results import write_json
from .attributor import ATTRIBUTION_RULE_VERSION, LABEL_CN


def save_attribution_report(result, native, timeline):
    evaluation = result.get('evaluation')
    if evaluation is None:
        return
    attribution = result.get('attribution') or {
        'rule_version': ATTRIBUTION_RULE_VERSION, 'regression_results': {},
        'stagnation_results': {}, 'proactive_findings': [], 'summary': {}}
    physical = result['components']['physical']
    candidates = []
    for task in evaluation.get('subtask_results', []):
        step = task['step']
        for kind, key, output in (('regression', 'regressions', 'regression_results'),
                                  ('stagnation', 'stagnations', 'stagnation_results')):
            for index, candidate in enumerate(task.get(key, [])):
                start = candidate.get('frame_index', candidate.get('start_frame'))
                end = candidate.get('end_frame', start)
                matches = [entry for entry in [*attribution.get(output, {}).values(), *attribution.get('skipped_results', [])]
                           if entry.get('subtask_idx', step) in (None, step)
                           and entry.get('frame', entry.get('start_frame')) == start
                           and (('frame' in entry) == (kind == 'regression'))]
                reasons = []
                for entry in matches:
                    reasons.extend(entry.get('evidence', {}).get('limitations', []))
                if not matches:
                    reasons.append(physical.get('reason', 'No physical conclusion was produced for this candidate'))
                candidates.append({
                    'candidate_id': f'subtask_{step}:{kind}:{index}', 'subtask_idx': step,
                    'description': task['description'], 'kind': kind,
                    'start_frame': start, 'end_frame': end,
                    'source_start_time_sec': timeline.time_at(start),
                    'source_end_time_sec': timeline.time_at(end),
                    'rule_version': attribution['rule_version'], 'visual_candidate': candidate,
                    'status': ('incomplete' if reasons else 'provisional'
                               if any(e.get('evidence_status') == 'provisional' for e in matches) else 'completed'),
                    'limitations': sorted(set(reasons)), 'conclusions': matches})
    attribution.update(candidates=candidates, assessment_complete=result['assessment_complete'],
                       physical_component=physical, physical_signals=result.get('physical_signals', {}))
    result['attribution'] = attribution
    write_json(native / 'attribution_result.json', attribution)
    lines = write_text_report(attribution, native)
    report = native / 'assessment_report.txt'
    if report.exists():
        with report.open('a') as stream:
            stream.write('\n物理归因摘要\n' + '\n'.join(lines[:4]) +
                         '\n逐候选结论、物理证据和缺失信息见 attribution_report.txt / attribution_result.json。\n')


def write_text_report(attribution, native):
    """Render an existing native result without recomputing or rewriting evidence."""
    physical = attribution['physical_component']
    candidates = attribution['candidates']
    def format_seconds(value):
        return 'unknown' if value is None else f'{value:.3f}'
    lines = ['任务进度物理归因报告',
             f"完整评估：{'完成' if attribution['assessment_complete'] else '未完成'}",
             f"规则版本：{attribution['rule_version']}",
             f"视觉候选数：{len(candidates)}"]
    if physical.get('reason'):
        lines.append('缺失信息：' + physical['reason'])
    if physical.get('evidence_status') == 'provisional':
        lines.append('条件归因：使用下述暂定单位／语义计算，标定仍待确认；不用于自动筛选。')
    for arm, reasons in physical.get('limitations', {}).items():
        if reasons:
            lines.append(f"{arm}：{'；'.join(reasons)}")
    for warning in attribution.get('physical_signals', {}).get('warnings', []):
        lines.append('输入说明：' + warning)
    for row in candidates:
        lines += ['', f"{row['candidate_id']} | {row['description']}",
                  f"原始帧 {row['start_frame']}–{row['end_frame']}；源时间 "
                  f"{format_seconds(row['source_start_time_sec'])}–{format_seconds(row['source_end_time_sec'])} 秒"]
        for entry in row['conclusions']:
            label = entry['primary_label']
            if entry.get('evidence_status') == 'provisional':
                lines.append('以下为条件归因，依赖暂定的信号解释：')
            if entry['severity'] < 0:
                lines.append(f"{entry['arm']}：归因证据不足，原因及严重程度未确定")
            else:
                lines.append(f"{entry['arm']}：{LABEL_CN.get(label, label)}；严重度 {entry['severity']}")
            if entry.get('recovery_action'):
                recovery = entry['recovery_action']
                lines.append('后续恢复：' + LABEL_CN.get(recovery, recovery))
            evidence = dict(entry.get('evidence', {}))
            sources = evidence.pop('physical_sources', {})
            if evidence:
                lines.append('依据：' + json.dumps(evidence, ensure_ascii=False))
            for role, source in sources.items():
                if not isinstance(source, dict):
                    continue
                lines.append(f"信号：{role}，{source.get('channel', 'unknown')}.{source.get('field', 'unknown')}，"
                             f"单位 {source.get('unit', 'unknown')}，有效样本 {source.get('valid_samples', 'unknown')}；"
                             '完整映射见 ../physical-signals.json')
        if row['limitations']:
            lines.append('归因未完成：' + '；'.join(row['limitations']))
    for finding in attribution.get('proactive_findings', []):
        lines.append(f"主动发现：帧 {finding['frame']}，{finding['arm']}，"
                     f"{LABEL_CN.get(finding['type'], finding['type'])}")
    if not candidates:
        lines.append('未检测到回退／停滞候选。' if attribution['assessment_complete'] else
                     '当前没有候选；评估未完成，不能据此判定轨迹正常。')
    (native / 'attribution_report.txt').write_text('\n'.join(lines) + '\n')
    return lines
