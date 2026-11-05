"""Collect one episode's common results (edc.algorithms.base) from a run folder and write quality.json.

Every algorithm already returns the common Item with its report (built-in: quality/shared/verdicts.py `emit`;
new algorithms: QualityAlgorithm). Keep successful sibling results when a record/channel fails. Unavailable
inputs get no quality verdict: not applicable for missing data/calibration, failed for processing errors.
"""
import json
from pathlib import Path

from edc.algorithms.base import Item, AlgorithmResult, NOT_APPLICABLE, FAILED, decide
from edc.algorithms.quality.adapters.cpu import ORDER
from edc.algorithms.quality.shared.verdicts import plain_reason, result_module
from edc.reporting.text import reports


def guide(algo):
    """What to look at when reviewing this algorithm's problems ('' if it gives none)."""
    return getattr(result_module(algo), 'GUIDE', '')


def _unavailable_item(algo, record, report=None, notes=None):
    """Keep a processing failure separate from the data verdict, at its original scope."""
    report = report or {}
    unavailable = report.get('ir_unavailable') or {}
    status = report.get('processing_status')
    if not status and 'ir_unavailable' in report:
        status = ('missing_dependency' if 'dependency_missing' in unavailable else
                  'missing_input' if 'need' in unavailable else 'processing_error')
    status = status or record['status']
    item = report.get('edc_item') or {}
    target = (report.get('input_channel') or item.get('target') or
              record.get('instance_id') or '本条')
    if target == 'episode':
        target = '本条'
    reason = (unavailable.get('error') or unavailable.get('dependency_missing') or unavailable.get('need')
              or report.get('reason') or '；'.join(map(str, report.get('reasons') or []))
              or record.get('reason'))
    return Item(target, NOT_APPLICABLE if status in ('missing_input', 'needs_calibration') else FAILED,
                reason=plain_reason(algo, status, reason, notes),
                evidence=item.get('evidence', []), metrics=item.get('metrics', {}))


def to_result(algo, records, notes=None):
    """One algorithm's records for one episode -> AlgorithmResult."""
    if not records:
        return AlgorithmResult(algo, [Item('本条', FAILED, reason='没有记录')], '没有记录')
    found = [x for r in records for x in reports(r)]
    module = result_module(algo)
    items, native_notes, corrected = [], [], False
    for record in records:
        record_items = []
        for report in reports(record):
            if 'ir_unavailable' in report or report.get('processing_status') in (
                    'processing_error', 'missing_dependency', 'missing_input', 'needs_calibration'):
                record_items.append(_unavailable_item(algo, record, report, notes))
                corrected = True
            elif 'edc_result' in report:  # A QualityAlgorithm subclass returns the whole result.
                result = AlgorithmResult.from_dict(report['edc_result'])
                record_items.extend(result.items)
                native_notes.append(result.note)
            elif 'edc_item' in report:
                record_items.append(Item(**report['edc_item']))
            extra = getattr(module, 'extra_items', lambda r: [])(report)  # 算法另记的项，如别的通道没查
            record_items.extend(extra)
            corrected = corrected or bool(extra)
        if record['status'] != 'completed':
            # A parent failure may have occurred after some children succeeded. Keep those children,
            # but do not let them conceal an error not accounted for by a child item.
            expected = NOT_APPLICABLE if record['status'] in ('missing_input', 'needs_calibration') else FAILED
            if not any(i.status == expected for i in record_items):
                record_items.append(_unavailable_item(algo, record, notes=notes))
            corrected = True
        elif not record_items:
            record_items.append(Item(record.get('instance_id') or '本条', FAILED,
                                     reason='算法没有给出统一格式的结论'))
            corrected = True
        items.extend(record_items)
    if not items:
        return AlgorithmResult(algo, [Item('本条', FAILED, reason='算法没有给出统一格式的结论')], '')
    note = native_notes[0] if native_notes else next((x['edc_note'] for x in found if 'edc_note' in x), '')
    if corrected:
        # Counts attached before processing errors were classified can misleadingly say REVIEW/PASS.
        from collections import Counter
        from edc.algorithms.base import STATUSES, VERDICTS
        counts = Counter(VERDICTS.get(i.verdict) or STATUSES[i.status] for i in items)
        note = ('不适用' if all(i.status == NOT_APPLICABLE for i in items) else
                '，'.join(f'{k}{n}项' for k, n in counts.items()))
    if hasattr(module, 'arms_note') and not native_notes:
        note = module.arms_note(items)  # One invocation is made per arm; the first is not the whole result.
    scopes = [r.get('input_scope') or {} for r in records]
    excluded = [x for scope in scopes for x in scope.get('excluded_inputs', [])]
    if excluded:
        reasons = {'unselected_arm': '不在本次机械臂范围', 'covered_by_derived_fields': '字段由分量视图检查，原数组不重复检查',
                   'missing_part_ownership': '缺少部件归属',
                   'shared_raw_fields_require_component_selection': '共享原字段尚未声明按臂分量，未检查'}
        text = '本次未检查：' + '；'.join(dict.fromkeys(
            f"{x['channel']}（{reasons.get(x['reason'], x['reason'])}）" for x in excluded))
        note = (note + '；' if note else '') + text
    return AlgorithmResult(algo, items, note)


def episode_results(run_dir, episode=None, notes=None):
    """{algorithm: AlgorithmResult} of one episode in a run folder (_runs/<id>/); episode=None: the only one."""
    run_dir = Path(run_dir)
    found = sorted(p.parent.name for p in run_dir.glob('*/records.jsonl'))
    results = {}
    for algo in [a for a in ORDER if a in found] + [a for a in found if a not in ORDER]:
        records = [json.loads(x) for x in (run_dir / algo / 'records.jsonl').read_text(encoding='utf-8').splitlines() if x]
        if episode is not None:
            records = [r for r in records if r['object']['id'] == episode]
        results[algo] = to_result(algo, records, notes)
    return results


def write_quality(run_dir, episodes, notes=None):
    """_runs/<id>/quality.json: every episode's decision and common results."""
    out = []
    for episode in episodes:
        results = episode_results(run_dir, episode, notes)
        out.append({'episode': episode, 'decision': decide(list(results.values())),
                    'results': [r.to_dict() for r in results.values()]})
    (Path(run_dir) / 'quality.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    return out
