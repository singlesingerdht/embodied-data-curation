"""Append-only review decisions refer to exact immutable algorithm reports."""
import json
from datetime import datetime, timezone
from pathlib import Path
from edc.reporting.results import append_record
from .references import digest


def subject_id(record, report_index):
    subject = {'algorithm': record['algorithm'], 'object': record['object'],
               'report_index': report_index, 'report': record['result']['reports'][report_index]}
    if record.get('instance_id') is not None:
        subject['instance_id'] = record['instance_id']
    return digest(subject)


def read_reviews(path):
    if not path or not Path(path).exists():
        return []
    result = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (row.get('schema_version') != 'dqs_review_v1' or
                row.get('action') not in {'confirmed', 'dismissed'} or
                any(not row.get(k) for k in ('run_id', 'subject_id', 'reviewer', 'reason', 'evidence', 'timestamp'))):
            raise ValueError('Invalid review record')
        result.append(row)
    return result


def review(output, *, run_id, algorithm, object_id, report_index=0,
           action, reviewer, reason, evidence, instance_id=None):
    output = Path(output).resolve()
    if action not in {'confirmed', 'dismissed'} or any(not isinstance(v, str) or not v.strip()
            for v in (reviewer, reason, evidence)):
        raise ValueError('Review requires action, reviewer, reason and evidence')
    catalog = json.loads((output / 'catalog.json').read_text())
    matches = [r for r in catalog['runs'] if r['run_id'] == run_id]
    if len(matches) != 1:
        raise ValueError('Review must reference one existing run')
    records = []
    for ref in matches[0]['records']:
        path = (output / ref).resolve()
        if not path.is_relative_to(output):
            raise ValueError('Record reference escapes output')
        records.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    matches = [r for r in records if r['algorithm']['id'] == algorithm and r['object']['id'] == object_id]
    if instance_id is not None:
        matches = [r for r in matches if r.get('instance_id') == instance_id]
    if len(matches) != 1 or matches[0]['status'] != 'completed':
        raise ValueError('Only a completed, uniquely identified report can be reviewed; '
                         'specify instance_id when an algorithm has multiple arm instances')
    record = matches[0]
    reports = record['result']['reports']
    if type(report_index) is not int or not 0 <= report_index < len(reports):
        raise ValueError('Invalid report index')
    report = reports[report_index]
    from .gates import has_review_candidate
    if not has_review_candidate(report):
        raise ValueError('Report has no review candidate; review cannot override automatic failure')
    row = {'schema_version': 'dqs_review_v1', 'run_id': run_id,
           'subject_id': subject_id(record, report_index), 'algorithm': algorithm,
           'object_id': object_id, 'report_index': report_index, 'action': action,
           'reviewer': reviewer, 'reason': reason, 'evidence': evidence,
           'timestamp': datetime.now(timezone.utc).isoformat()}
    if record.get('instance_id') is not None:
        row['instance_id'] = record['instance_id']
    # Use the same output lock as execution to avoid interleaved concurrent writes.
    lock = output / '.edc.lock'
    with lock.open('x'):
        pass
    try:
        append_record(output / 'reviews.jsonl', row)
    finally:
        lock.unlink()
    return row
