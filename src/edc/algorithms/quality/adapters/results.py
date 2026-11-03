"""Collect one episode's common results (edc.algorithms.base) from a run folder and write quality.json.

Every algorithm already returns the common Item with its report (built-in: quality/shared/verdicts.py `emit`;
new algorithms: QualityAlgorithm). Here only an algorithm that did not run at all gets its Item: not applicable
when the robot yaml or the data lacks what it needs, failed otherwise.
"""
import json
from importlib import import_module
from pathlib import Path

from edc.algorithms.base import Item, AlgorithmResult, NOT_APPLICABLE, FAILED, decide
from edc.algorithms.quality.shared.verdicts import plain_reason
from edc.reporting.text import reports

ALGOS = ['Structure_Check', 'Numeric_Check', 'Action_Smoothness', 'Check_Timestamp', 'Sync_Check',
         'Root_Cause', 'Dedup_Check', 'Reset_Detection', 'Video_Integrity']


def guide(algo):
    """What to look at when reviewing this algorithm's problems ('' if it gives none)."""
    try:
        return getattr(import_module(f'edc.algorithms.quality.algorithms.{algo}.result'), 'GUIDE', '')
    except ImportError:
        return ''


def to_result(algo, records, notes=None):
    """One algorithm's records for one episode -> AlgorithmResult."""
    if not records:
        return AlgorithmResult(algo, [Item('本条', FAILED, reason='没有记录')], '没有记录')
    failed = [r for r in records if r['status'] != 'completed']
    if failed:
        r = failed[0]
        status = NOT_APPLICABLE if r['status'] in ('missing_input', 'needs_calibration') else FAILED
        return AlgorithmResult(algo, [Item('本条', status, reason=plain_reason(algo, r['status'], r.get('reason'), notes))],
                               '不适用' if status == NOT_APPLICABLE else r['status'])
    found = [x for r in records for x in reports(r)]
    native = [x['edc_result'] for x in found if 'edc_result' in x]
    if native:  # A QualityAlgorithm subclass returns the whole result.
        results = [AlgorithmResult.from_dict(n) for n in native]
        return AlgorithmResult(algo, [i for r in results for i in r.items], results[0].note)
    items = [Item(**x['edc_item']) for x in found if 'edc_item' in x]
    if not items:
        return AlgorithmResult(algo, [Item('本条', FAILED, reason='算法没有给出统一格式的结论')], '')
    return AlgorithmResult(algo, items, next((x['edc_note'] for x in found if 'edc_note' in x), ''))


def episode_results(run_dir, episode=None, notes=None):
    """{algorithm: AlgorithmResult} of one episode in a run folder (_runs/<id>/); episode=None: the only one."""
    run_dir = Path(run_dir)
    found = sorted(p.parent.name for p in run_dir.glob('*/records.jsonl'))
    results = {}
    for algo in [a for a in ALGOS if a in found] + [a for a in found if a not in ALGOS]:
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
