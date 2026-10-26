"""Finite stage execution with immutable upstream references and content-checked resume."""
import copy
import inspect
import json
import operator
import platform
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path
from framework.execution import run
from framework.ir.dataset import Dataset, open_dataset
from framework.registry import algorithms
from framework.results import write_json
from .references import DatasetView, dataset_reference, open_dataset_reference, digest, file_digest
from .validation import validate_workflow
from .reviews import read_reviews
from .gates import quality_gate, combine_gates
from .transforms import validate_transform


def read_records(output, entry):
    rows = []
    for reference in entry['records']:
        path = (output / reference).resolve()
        if not path.is_relative_to(output):
            raise ValueError('Run reference escapes output')
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return rows


def execution_identity(registry):
    root = Path(__file__).resolve().parents[2]
    code = []
    for folder in ('framework', 'Quality_Assessment'):
        code.extend((str(p.relative_to(root)), file_digest(p)) for p in sorted((root / folder).rglob('*.py')))
    plugin_files = {}
    installed = list(distributions())
    for dist in installed:
        if any(point.group == 'dqs.algorithms' for point in dist.entry_points):
            for item in dist.files or ():
                path = Path(dist.locate_file(item))
                if path.suffix == '.py' and path.is_file():
                    plugin_files[str(path.resolve())] = file_digest(path)
    for plugin in registry.plugins.values():
        try:
            path = inspect.getsourcefile(type(plugin))
            if path:
                path = Path(path)
                # Installed flat modules share site-packages with unrelated
                # libraries. Their helpers were collected by distribution above.
                candidates = [path] if {'site-packages', 'dist-packages'} & set(path.parts) else path.parent.rglob('*.py')
                for p in candidates:
                    if '__pycache__' not in p.parts:
                        plugin_files[str(p)] = file_digest(p)
        except (TypeError, OSError):
            pass
    return digest({'python': platform.python_version(), 'code': code,
        'algorithm_specs': {name: asdict(plugin.spec) for name, plugin in registry.plugins.items()},
        'plugins': plugin_files, 'environment': sorted((d.metadata['Name'], d.version) for d in installed)})


def run_evidence(output, entry):
    root = output / '_runs' / entry['run_id']
    return digest([(str(p.relative_to(root)), file_digest(p)) for p in sorted(root.rglob('*')) if p.is_file()])


def _select(stage, dataset, parent_records):
    ids = [ep.id for ep in dataset.iter_episodes()]
    rule = stage.get('select', {'all': True})
    requested = rule.get('episodes', ids)
    if any(i not in ids for i in requested):
        raise ValueError('Selection references an unavailable episode')
    comparison = {'eq': operator.eq, 'ne': operator.ne, 'gt': operator.gt,
                  'gte': operator.ge, 'lt': operator.lt, 'lte': operator.le}
    selected = []
    for ident in requested:
        matches = True
        for condition in rule.get('where', []):
            rows = [r for r in parent_records if r['algorithm']['id'] == condition['algorithm'] and
                    (r['object']['type'] == 'dataset' or r['object']['id'] == ident) and r['status'] == 'completed']
            if len(rows) != 1:
                matches = False; break
            value = rows[0]
            try:
                for key in condition['path'].split('.'):
                    value = value[int(key)] if isinstance(value, list) else value[key]
                matches = matches and comparison[condition['op']](value, condition['value'])
            except (KeyError, TypeError, IndexError, ValueError):
                matches = False
        if matches:
            selected.append(ident)
    return selected


def run_workflow(config, output='results', *, resume=False):
    cfg = validate_workflow(config)['config']
    output = Path(output).resolve()
    source = open_dataset(**{'path': cfg['dataset']['path'],
        **{k: v for k, v in cfg['dataset'].items() if k != 'path'}})
    for ep in source.iter_episodes():
        if output == ep.path or ep.path.is_dir() and output.is_relative_to(ep.path):
            source.__exit__(None, None, None)
            raise ValueError('Workflow output cannot be inside a source episode')
    if source.path.is_dir() and output.is_relative_to(source.path):
        source.__exit__(None, None, None)
        raise ValueError('Workflow output must remain outside source dataset')
    output.mkdir(parents=True, exist_ok=True)
    lock = output / '.workflow.lock'
    with lock.open('x'):
        pass
    workflow_run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:10]
    root = output / '_workflows' / workflow_run
    root.mkdir(parents=True)
    manifest = {'schema_version': 'dqs_workflow_run_v1', 'workflow_id': cfg['id'],
                'workflow_run_id': workflow_run, 'config': cfg, 'stages': []}
    previous = []
    datasets = [source]
    try:
        catalog_path = output / 'catalog.json'
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {
            'schema_version': 'dqs_catalog_v1', 'runs': []}
        if catalog.get('schema_version') != 'dqs_catalog_v1':
            raise ValueError('Unsupported output catalog')
        if resume:
            for item in reversed(catalog.get('workflows', [])):
                if item['workflow_id'] == cfg['id']:
                    prior = (output / item['manifest']).resolve()
                    if not prior.is_relative_to(output):
                        raise ValueError('Workflow manifest reference escapes output')
                    previous = json.loads(prior.read_text())['stages']; break
        registry = algorithms()
        runtime = execution_identity(registry)
        source_ref = dataset_reference(source)
        manifest['source'] = source_ref
        states = {'source': {'dataset': source, 'reference': source_ref, 'records': [],
                             'quality': {}, 'value_ready': False}}
        for stage in cfg['stages']:
            row = {'id': stage['id'], 'kind': stage['kind'], 'input': stage['input']}
            manifest['stages'].append(row)
            parent = states.get(stage['input'])
            if parent is None:
                row.update(status='blocked', reason='Upstream stage has no validated output')
                write_json(root / 'workflow.json', manifest); continue
            try:
                selected = _select(stage, parent['dataset'], parent['records'])
                row['available_episodes'] = [ep.id for ep in parent['dataset'].iter_episodes()]
                row['requested_episodes'] = list(selected)
                row['withheld_episodes'] = []
                if stage['kind'] == 'value':
                    row['withheld_episodes'] = [{'episode_id': i, 'reason': 'Quality gate is not keep',
                        'quality': parent['quality'].get(i, {})} for i in selected
                        if parent['quality'].get(i, {}).get('decision') != 'keep']
                    selected = [i for i in selected if parent['quality'].get(i, {}).get('decision') == 'keep']
                if stage['kind'] == 'augmentation':
                    row['withheld_episodes'] = [{'episode_id': i, 'reason': 'Selected value methods did not all complete'}
                        for i in selected if i not in parent.get('valued_ids', [])]
                    selected = [i for i in selected if i in parent.get('valued_ids', [])]
                row['selected_episodes'] = selected
                if not selected:
                    pending = any(parent['quality'].get(i, {}).get('decision') == 'review'
                                  for i in row['requested_episodes'])
                    row.update(status='pending_review' if pending else 'blocked', reason='No eligible selected episodes')
                    write_json(root / 'workflow.json', manifest); continue
                current = DatasetView(parent['dataset'], selected)
                datasets.append(current)
                reference = dataset_reference(current)
                upstream = {k: {'run_id': v.get('run_id'), 'dataset': v['reference'],
                            'records': v['records'], 'quality': v['quality']}
                            for k, v in states.items() if k != 'source'}
                # Exact run references and quality decisions are part of dependent-stage identity.
                identity = digest({'stage': stage, 'input': reference['fingerprint'],
                    'unit_policy': cfg.get('unit_policy', 'auto'), 'runtime': runtime, 'upstream': upstream})
                row['identity'] = identity
                cached = next((r for r in previous if r.get('identity') == identity and
                               r.get('run_id') and r.get('evidence')), None)
                entry = None
                catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else catalog
                if cached:
                    entry = next((r for r in catalog['runs'] if r['run_id'] == cached['run_id']), None)
                    if entry and run_evidence(output, entry) != cached['evidence']:
                        entry = None
                if entry:
                    row['reused'] = True
                    summary_path = (output / entry['summary']).resolve()
                    if not summary_path.is_relative_to(output):
                        raise ValueError('Summary reference escapes output')
                    summary = json.loads(summary_path.read_text())
                else:
                    row['reused'] = False
                    summary = run(current, output=output, algorithm_ids=stage['algorithms'],
                        params=stage.get('params', {}), unit_policy=cfg.get('unit_policy', 'auto'), upstream=upstream)
                    catalog = json.loads(catalog_path.read_text())
                    entry = next(r for r in catalog['runs'] if r['run_id'] == summary['run_id'])
                records = read_records(output, entry)
                row.update(run_id=summary['run_id'], processing=summary['status_counts'],
                           evidence=run_evidence(output, entry), status='completed')
                if any(r['status'] != 'completed' for r in records):
                    row['status'] = ('partial_success' if any(r['status'] == 'completed' for r in records)
                        else 'processing_error' if any(r['status'] == 'processing_error' for r in records) else 'blocked')
                state = {'dataset': current, 'reference': reference, 'records': records,
                         'run_id': row['run_id'], 'quality': parent['quality'],
                         'valued_ids': parent.get('valued_ids', [])}
                if stage['kind'] == 'quality':
                    state['quality'] = combine_gates(parent['quality'], quality_gate(
                        records, selected, row['run_id'], read_reviews(output / 'reviews.jsonl'),
                        required_algorithms=stage['algorithms']))
                    row['gate'] = state['quality']
                elif stage['kind'] == 'value':
                    state['valued_ids'] = []
                    for ident in selected:
                        relevant = [r for r in records if r['object']['type'] == 'dataset' or r['object']['id'] == ident]
                        if (set(stage['algorithms']) == {r['algorithm']['id'] for r in relevant} and
                                all(r['status'] == 'completed' and (r.get('result') or {}).get('reports') for r in relevant)):
                            state['valued_ids'].append(ident)
                    row['value_completed_episodes'] = state['valued_ids']
                elif stage['kind'] in {'editing', 'augmentation'}:
                    children = []
                    derived_profile = None
                    rejected = []
                    for record in records:
                        try:
                            derived = validate_transform({**record, '_run_id': row['run_id']}, current, output)
                            if derived_profile is not None and derived_profile != derived.profile:
                                raise ValueError('Output profiles disagree')
                            derived_profile = derived.profile
                            children.extend(derived.iter_episodes())
                        except Exception as exc:
                            rejected.append({'object': record['object'], 'reason': f'{type(exc).__name__}: {exc}'})
                    row['transform_errors'] = rejected
                    ids = [ep.id for ep in children]
                    if len(ids) != len(set(ids)):
                        raise ValueError('Derived episode IDs collide')
                    if not children:
                        row.update(status='blocked', reason='No transform output passed validation')
                        write_json(root / 'workflow.json', manifest); continue
                    current = Dataset(output / '_runs' / row['run_id'], derived_profile,
                        {'id': parent['dataset'].id + '__' + stage['id'], 'version': row['run_id'],
                         'tasks': parent['dataset'].tasks,
                         'metadata': {'curation_parent': reference['fingerprint']}}, children)
                    datasets.append(current)
                    state.update(dataset=current, reference=dataset_reference(current), quality={}, valued_ids=[])
                    if rejected:
                        row['status'] = 'partial_success'
                row['output_reference'] = state['reference']
                states[stage['id']] = state
            except Exception as exc:
                row.update(status='processing_error', reason=f'{type(exc).__name__}: {exc}')
            write_json(root / 'workflow.json', manifest)
        # Detect unexpected writes by a plugin before publishing a workflow result.
        if dataset_reference(source) != source_ref:
            raise ValueError('Source changed during workflow execution')
        statuses = [r['status'] for r in manifest['stages']]
        manifest['status'] = ('completed' if all(s == 'completed' for s in statuses) else
                              'partial_success' if any(s == 'completed' for s in statuses) else 'blocked')
        write_json(root / 'workflow.json', manifest)
        with (output / '.dqs.lock').open('x'):
            pass
        try:
            catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else catalog
            catalog.setdefault('workflows', []).append({'workflow_id': cfg['id'], 'workflow_run_id': workflow_run,
                'manifest': (root / 'workflow.json').relative_to(output).as_posix(),
                'report': (root / 'report.html').relative_to(output).as_posix()})
            write_json(catalog_path, catalog)
        finally:
            (output / '.dqs.lock').unlink()
        from .report import render_workflow
        render_workflow(root, manifest, output)
        from framework.results import report
        report(output)
        return manifest
    except Exception as exc:
        manifest.update(status='processing_error', reason=f'{type(exc).__name__}: {exc}')
        write_json(root / 'workflow.json', manifest)
        raise
    finally:
        for dataset in datasets:
            dataset.__exit__(None, None, None)
        lock.unlink()
