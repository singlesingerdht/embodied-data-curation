"""Conservative attempt-level revision of frozen v3 results (no GRM inference).

Signal recovery is a separate observation, never proof of a successful grasp.
Current change/retry alone cannot confirm an empty grasp.

training_filter_v6: adds the Open-branch load-evidence gate — object drops are
only excluded when pre-Open current elevation or a strong spike shows the
gripper carried a load; otherwise the event becomes an
unconfirmed_drop_by_release REVIEW (R01 audit: 4/7 audited drop excludes
were empty-gripper opens).

training_filter_v7: two-path drop confirmation and a stagnation boundary fix,
both driven by the microwave R01 audit (deletion pool 11/11, review-miss
census 56): (a) drops may also confirm via early-deep progress evidence
(pre_drop_prog <= 0.35 with post-drop max still >= 0.05 below it) because
microwave holding currents barely rise above baseline — the current-only
gate had downgraded 5/6 human-confirmed drops; (b) severe stagnation now
includes exactly 10.0 s (>= 10.0, was > 10.0).

training_filter_v8: driven by the pour-tea review-miss audit (131 samples):
(a) empty-grasp confirmation — Close with |close_current_delta| < 0.10 plus a
retry promotes failed grasps to EXCLUDE (pour-tea 10/11, microwave 2/2
human-confirmed; |delta| >= 0.10 stays REVIEW); (b) Open-branch drop windows
are capped at the next gripper action instead of stretching to the subtask
end (windows up to 26 s covered successful re-execution after the failure).
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

RULE_VERSION = 'progress_attribution_v4'
POLICY_VERSION = 'training_filter_v8'

def load(path):
    return json.loads(path.read_text())

def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def classify(event, *, timeline=None):
    kind, evidence = event['source_type'], event.get('evidence', {})
    # A failed closure has independent physical evidence; later progress recovery
    # does not undo it. Small current change alone is not this evidence.
    if kind == 'failed_grasp_blocked':
        closes = evidence.get('nearby_close_events', [])
        spike = event.get('source_evidence', {}).get('spike_start')
        near = spike is not None and any(k == 'close' and (abs(timeline.time_at(int(f))-timeline.time_at(spike)) <= 10/30 if timeline is not None else abs(int(f)-spike) <= 10) for f,k in closes)
        if evidence.get('already_closed_30_frames') is True:
            return 'keep', 'loaded_gripper_current_spike', '尖峰前夹爪已持续闭合，保留持物状态观察'
        if (near and evidence.get('already_closed_30_frames') is False
                and evidence.get('incomplete_close') is True
                and evidence.get('progress_drop', 0) >= .10):
            return 'exclude', kind, 'Close附近尖峰、未正常闭合且进度明显下降；后续恢复不撤销当次受阻'
        return 'review', 'unconfirmed_grasp_blocked', '受阻证据不足，不能确认失败或正常搬运'
    # v8: empty-grasp confirmation. Close produced almost no current change
    # (|delta| < 0.10, gripper closed on air) AND the robot retried — two
    # independent physical signals. R01 audits: pour-tea 10/11 and microwave
    # 2/2 human-confirmed harmful in this bucket; |delta| >= 0.10 stays REVIEW
    # (pour-tea 3/11 and microwave 1/3 were normal there).
    delta = evidence.get('close_current_delta')
    if (evidence.get('empty_current_signal') is True
            and evidence.get('retry_events')
            and delta is not None and abs(delta) < 0.10):
        return 'exclude', 'failed_grasp_close', 'Close 后电流几乎无上升（疑似空抓）且伴随重试：双重物理证据确认抓取失败'
    return 'review', 'unconfirmed_grasp_regression', '抓取候选仍有失败疑点；小幅下降、后续恢复、电流弱变化或重试均不能单独判正常或确认抓空'

def bounds(event, evaluation, grip_events, total, timeline=None):
    source, evidence = event.get('source_evidence', {}), event.get('evidence', {})
    arm = event.get('arm')
    start = source.get('close_frame', source.get('spike_start'))
    if start is None:
        closes = [int(f) for f,k in source.get('events_in_win', []) if k == 'close']
        closes += [int(x['frame']) for x in source.get('close_deltas', []) if 'frame' in x]
        start = max(closes) if closes else None
    end = source.get('dip_frame', evidence.get('trough_frame'))
    if arm not in ('left', 'right') or start is None or end is None:
        return None, 'missing_temporal_anchor'
    start, end = int(start), int(end)
    if not (0 <= start <= end < total): return None, 'invalid_temporal_anchor'
    tasks = [x for x in evaluation.get('subtask_results', []) if x['start_frame'] <= start <= x['end_frame']]
    if len(tasks) != 1: return None, 'ambiguous_subtask_anchor'
    task_end = int(tasks[0]['end_frame'])
    close_anchor = start
    if event['source_type'] == 'failed_grasp_blocked':
        near_closes = [int(f) for f,k in evidence.get('nearby_close_events', [])
                       if k == 'close' and (abs(timeline.time_at(int(f))-timeline.time_at(start))<=10/30 if timeline is not None else abs(int(f)-start)<=10)]
        close_anchor = max([start]+near_closes)
    subsequent = sorted((int(f), k) for f,k in grip_events.get(arm, [])
                        if close_anchor < int(f) <= task_end and k in ('open', 'close'))
    next_action = subsequent[0] if subsequent else None
    end = min(end, task_end, next_action[0]-1 if next_action else total-1)
    return {'start_frame': start, 'end_frame': end, 'original_end_frame': int(source.get('dip_frame', evidence.get('trough_frame'))),
            'next_action_frame': next_action[0] if next_action else None,
            'next_action_type': next_action[1] if next_action else None,
            'boundary_basis': 'close_or_spike_to_dip_capped_before_next_gripper_action_and_subtask_end',
            'boundary_status': 'signal_anchored_not_human_verified'}, None

def convert(attribution, evaluation, frame_record, observation_record, grip_events, *, timeline=None):
    if attribution.get('rule_version') != 'progress_attribution_v3':
        raise ValueError('unsupported source rule version')
    d, o = copy.deepcopy(frame_record), copy.deepcopy(observation_record)
    total = int(d['timeline']['total_frames'])
    events = attribution.get('training_events_v3', [])
    if 'training_events_v3' not in attribution: raise ValueError('missing candidate event structure')
    candidate_ids = {x['event_id'] for x in events}
    def inherited(x):
        # Preserve independently classified stagnation even when the legacy
        # exporter reused its ID for an early-close candidate.
        return x['decision_id'] not in candidate_ids or x['label'] in {'severe_stagnation', 'medium_stagnation', 'short_stagnation'}
    d['decisions'] = [x for x in d['decisions'] if inherited(x)]
    o['observations'] = [x for x in o['observations'] if inherited(x)]
    attempts, errors, comparisons = [], [], []
    for event in events:
        source_id = event['event_id']
        event_id = source_id+':attempt' if source_id.startswith('stag_') else source_id
        decision, label, reason = classify(event,timeline=timeline)
        interval, error = bounds(event, evaluation, grip_events, total,timeline=timeline)
        if error:
            errors.append({'episode': d['episode'], 'method': 'Progress_Stagnation_Attribution',
                           'error_type': error, 'event_id': event_id, 'recoverable': True,
                           'message': 'Cannot safely construct attempt interval; no exclude emitted'})
            attempts.append({'attempt_id': event_id, 'source_event_id': source_id,
                             'status': 'unresolved_interval', 'reason': error})
            continue
        recovery = event.get('evidence', {}).get('recovery_frame')
        attempt = dict(attempt_id=event_id, source_event_id=source_id, arm=event.get('arm'),
                       decision=decision, label=label, **interval,
                       progress_recovered=event.get('evidence', {}).get('recovered'),
                       recovery_frame=recovery, success_confirmed=False,
                       source_evidence=event.get('source_evidence', {}),
                       rule_evidence=event.get('evidence', {}))
        attempts.append(attempt)
        item = dict(decision_id=event_id, decision=decision, label=label, severity=2 if decision!='keep' else 0,
                    start_frame=interval['start_frame'], end_frame=interval['end_frame'], reason=reason,
                    attempt_id=event_id, source_event_id=source_id, source_label=event['source_type'],
                    boundary_status=interval['boundary_status'], rule_evidence=event.get('evidence', {}))
        (d['decisions'] if decision!='keep' else o['observations']).append(item)
        if isinstance(recovery, int) and 0 <= recovery < total:
            o['observations'].append(dict(decision_id=event_id+':recovery', decision='keep',
                label='progress_recovery_observation', severity=0, start_frame=recovery, end_frame=recovery,
                related_attempt_id=event_id, success_confirmed=False,
                reason='独立记录进度恢复；不撤销前次失败，也不证明后续动作成功'))
        comparisons.append(dict(episode=d['episode'], decision_id=event_id,
            old_decision=event['decision'], old_label=event['label'],
            new_decision=decision, new_label=label,
            start_frame=item['start_frame'], end_frame=item['end_frame']))
    # v8: cap Open-branch drop windows at the next gripper action, matching
    # attempt-interval semantics. The exporter stretches a drop window to the
    # subtask end whenever progress never re-reaches the pre-drop level, but
    # R01 audit notes show such windows covered up to 26 s of successful
    # re-execution after the actual failure (MIS-0124/0125).
    regressions = attribution.get('regression_results') or {}
    fps = float(d['timeline'].get('fps') or 30.0)
    horizon = int(round(fps * 5.0))  # 5 s fallback cap; longer idle is stagnation's domain
    for item in d['decisions']:
        if item.get('label') not in ('object_drop_by_release', 'unconfirmed_drop_by_release'):
            continue
        source_event = regressions.get(str(item.get('source_event_id') or item.get('decision_id') or ''))
        arm = (source_event or {}).get('arm')
        if arm not in ('left', 'right'):
            arm = 'right' if str(item['decision_id']).endswith('_right') else 'left'
        start = int(item['start_frame'])
        following = sorted(int(f) for f, k in grip_events.get(arm, []) if int(f) > start)
        horizon_frame=timeline.offset(start,5.0,side='right')-1 if timeline is not None else start+horizon
        limit = horizon_frame
        if following:
            limit = min(limit, following[0] - 1)
        capped = max(min(int(item['end_frame']), limit), start)
        if capped < int(item['end_frame']):
            item['window_cap'] = dict(original_end_frame=int(item['end_frame']),
                                       next_action_frame=following[0] if following else None,
                                       horizon_frame=horizon_frame,
                                       boundary_basis='open_to_next_gripper_action_or_5s')
            item['end_frame'] = capped
    for record, key in [(d,'decisions'), (o,'observations')]:
        record['method'] = dict(id='Progress_Stagnation_Attribution', version=RULE_VERSION, policy_version=POLICY_VERSION)
        record[key].sort(key=lambda x:(x['start_frame'], x['end_frame'], x['decision_id']))
        if len({x['decision_id'] for x in record[key]}) != len(record[key]): raise ValueError('duplicate decision ID')
    return d, o, attempts, errors, comparisons

def training_segments(record):
    """Partition the whole timeline; KEEP here means not excluded, not success."""
    total = record['timeline']['total_frames']
    events = record['decisions']
    cuts = sorted({0,total} | {f for e in events for f in (e['start_frame'], e['end_frame']+1)})
    segments = []
    for start, stop in zip(cuts,cuts[1:]):
        active = [e for e in events if e['start_frame'] <= start <= e['end_frame']]
        excluded = [e['decision_id'] for e in active if e['decision']=='exclude']
        reviews = [e['decision_id'] for e in active if e['decision']=='review']
        segments.append(dict(start_frame=start,end_frame=stop-1,training_decision='exclude' if excluded else 'keep',
                             exclude_ids=excluded,review_ids=reviews,success_confirmed=False))
    return dict(episode=record['episode'],frame_ranges='inclusive',segments=segments)
