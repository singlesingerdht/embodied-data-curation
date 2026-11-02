"""Present catalog runs through the same batch table for one or many episodes."""
from collections import OrderedDict
import json
from pathlib import PurePosixPath
from urllib.parse import quote

from edc.reporting.batch import ALGOS, page, summarize_records
from edc.reporting.html import esc, read_json, safe_path


def link(root, ref, label):
    safe_path(root, ref)
    return f'<a href="{esc(quote(ref, safe="/"))}">{esc(label)}</a>'


def run_page(root, entry, index, *, fragment=False, header='', footer=''):
    snapshot = read_json(root, entry.get('run'))
    profile = snapshot.get('profile') or {}
    dataset = snapshot.get('dataset') or {}
    selections = {str(s['episode_id']): s for s in read_json(root, entry.get('selection')) or []}
    inventory = {str(ep['id']): ep for ep in dataset.get('episodes', [])}
    groups = OrderedDict()
    for ref in entry.get('records', []):
        for line in safe_path(root, ref).read_text(encoding='utf-8').splitlines():
            if line.strip():
                record = json.loads(line)
                key = (record['object']['type'], str(record['object']['id']))
                groups.setdefault(key, []).append((record, ref))
    specs = snapshot.get('algorithms') or []
    selected = [s['id'] for s in specs if s.get('granularity') != 'dataset']
    selected += [r['algorithm']['id'] for (kind, _), values in groups.items() if kind == 'episode'
                 for r, _ in values]
    selected = list(dict.fromkeys(selected))
    algorithms = [a for a in ALGOS if a in selected] + [a for a in selected if a not in ALGOS]
    episode_ids = list(dict.fromkeys([*inventory, *selections,
                                    *(ident for kind, ident in groups if kind == 'episode')]))
    rows = []
    for ident in episode_ids:
        values = groups.get(('episode', ident), [])
        selection = selections.get(ident, {})
        framework = selection.get('decision', 'unassessed')
        if framework == 'keep' and selection.get('assessment_complete') is not True:
            framework = 'unassessed'
        summary = summarize_records([r for r, _ in values], framework, algorithms=algorithms)
        episode = inventory.get(ident, {})
        source = selection.get('source') or episode.get('source') or next(
            (r['object'].get('source') for r, _ in values if r['object'].get('source')), '')
        # The batch reference has a compact algorithm table, not an embedded
        # technical report. Full records stay in the existing run directory.
        for record, _ in values:
            for ref in record.get('artifacts', []):
                safe_path(root, ref)
        run_ref = entry.get('run')
        kept = str(PurePosixPath(run_ref).parent) + '/' if run_ref else None
        rows.append(dict(summary, name=ident, source=source, seconds=None, size_mb=None,
                         kept=kept, kept_url=kept))
    info = dict(robot=profile.get('id') or dataset.get('id') or '数据质量评估',
                inputs=dataset.get('source', ''), profile=profile.get('id', '未记录'),
                params='保存在本次配置与版本中', algorithms=algorithms,
                timing_note='运行 ' + str(entry.get('run_id', index)), anchor_prefix=f'run{index}-',
                header_html='', footer_html='')
    return page(rows, info, fragment=fragment)


def render(root, catalog):
    entries = catalog.get('runs', [])
    # Present the current run, exactly as a batch invocation does. Historical
    # runs and workflow manifests remain discoverable through catalog.json.
    if not entries:
        return page([], dict(robot='数据质量评估', inputs='', profile='未记录', params='', algorithms=[],
                             timing_note='暂无评估记录。请先运行所需算法。'))
    return run_page(root, entries[-1], len(entries)-1)
