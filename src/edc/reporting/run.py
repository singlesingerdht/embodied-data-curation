"""Render an existing run with the batch report template, without judging results."""
from datetime import datetime
import json
from pathlib import Path

from edc.reporting.batch import page


def write_run_report(output, run_dir, quality, *, seconds, episode_seconds):
    output, run_dir = Path(output), Path(run_dir)
    from edc.schemas import RUN, require_schema
    metadata = require_schema(json.loads((run_dir / 'run.json').read_text(encoding='utf-8')), RUN)
    algorithm_ids = [a['id'] for a in metadata['algorithms'] if a['category'] == 'quality']
    if not algorithm_ids:
        return None
    sources = {e['id']: e['source'] for e in metadata['dataset']['episodes']}
    bags = [{'name': row['episode'], 'source': sources[row['episode']],
             'decision': row['decision'],
             'algos': {r['algorithm']: r for r in row['results'] if r['algorithm'] in algorithm_ids},
             'seconds': episode_seconds[row['episode']], 'kept': str(output)}
            for row in quality]
    info = {'robot': metadata['profile'].get('id', '未指定'),
            'inputs': metadata['dataset']['source'], 'profile': str(run_dir / 'run.json'),
            'finished': f'{datetime.now():%Y-%m-%d %H:%M}', 'seconds': seconds, 'jobs': 1}
    html = page(bags, info, algorithm_ids=algorithm_ids, title='质检报告')
    report = run_dir / 'report.html'
    report.write_text(html, encoding='utf-8')
    (output / 'report.html').write_text(html, encoding='utf-8')
    return report.relative_to(output).as_posix()
