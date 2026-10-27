"""Workflow eligibility is separate from original scores and legacy selection."""
from .reviews import subject_id
from edc.execution.completeness import report_complete


def has_review_candidate(report):
    if report.get('verdict') == 'NEEDS_REVIEW' or report.get('review_candidates'):
        return True
    for key, value in report.items():
        if key in {'sub_indicators', 'frame_quality', 'issues', 'warnings', 'events'}:
            if isinstance(value, dict) and has_review_candidate(value):
                return True
            if isinstance(value, list) and any(isinstance(v, dict) and
                    (has_review_candidate(v) or v.get('needs_review') is True) for v in value):
                return True
    return False


def quality_gate(records, episode_ids, run_id, reviews=(), required_algorithms=None):
    decisions = {}
    for ident in episode_ids:
        selected = [r for r in records if (r['object']['type'] == 'dataset' and
                    ident in r.get('eligible_episodes', episode_ids)) or r['object']['id'] == ident]
        reasons = []
        state = 'keep'
        if not selected:
            state = 'unassessed'; reasons.append('No selected quality result')
        if required_algorithms and set(required_algorithms) - {r['algorithm']['id'] for r in selected}:
            state = 'unassessed'; reasons.append('Selected algorithm results missing')
        for record in selected:
            if record['status'] != 'completed' or not (record.get('result') or {}).get('reports'):
                if state != 'exclude': state = 'unassessed'
                reasons.append(record.get('reason') or record['status']); continue
            if not record.get('scope_complete', True):
                if state != 'exclude': state = 'unassessed'
                reasons.append('Declared arm coverage incomplete: ' + str(record.get('input_scope', {})))
            for index, result in enumerate(record['result']['reports']):
                verdict = result.get('verdict')
                if result.get('supplemental_verdict') == 'DO_NOT_USE':
                    state = 'exclude'; reasons.append('Selected supplemental check failed'); continue
                if verdict == 'DO_NOT_USE' or result.get('passed') is False and verdict != 'NEEDS_REVIEW':
                    state = 'exclude'; reasons.append('Selected item failed'); continue
                # Review cannot turn missing coverage into an assessed item.
                if not report_complete(result, record['algorithm']['id']):
                    if state != 'exclude': state = 'unassessed'
                    reasons.append('Required assessment coverage incomplete'); continue
                pending = has_review_candidate(result)
                latest = [r for r in reviews if r['run_id'] == run_id and
                          r['subject_id'] == subject_id(record, index)]
                action = latest[-1]['action'] if latest else None
                if pending and action == 'confirmed':
                    state = 'exclude'; reasons.append('Human confirmed a violation of selected criteria')
                elif pending and action != 'dismissed':
                    if state == 'keep': state = 'review'
                    reasons.append('Human review pending')
                elif verdict not in {'ACCEPTABLE', 'NEEDS_REVIEW'}:
                    if state != 'exclude': state = 'unassessed'
                    reasons.append('No acceptable judgment for selected scope')
        decisions[ident] = {'episode_id': ident, 'decision': state,
                            'reasons': reasons, 'policy_version': 'workflow_gate_v3'}
    return decisions


def combine_gates(previous, current):
    """All selected checks on an unchanged data version remain binding."""
    rank = {'keep': 0, 'review': 1, 'unassessed': 2, 'exclude': 3}
    result = {}
    for ident, item in current.items():
        old = previous.get(ident)
        if old:
            item = {**item, 'decision': max((old['decision'], item['decision']), key=rank.get),
                    'reasons': old['reasons'] + item['reasons']}
        result[ident] = item
    return result
