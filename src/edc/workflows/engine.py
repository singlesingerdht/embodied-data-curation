"""Finite stage execution with immutable upstream references and content-checked resume."""

from edc.schemas import CATALOG, RECORD, require_schema
import inspect
import json
import operator
import platform
import sysconfig
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path
from edc.execution.runner import run
from edc.data.ir.dataset import Dataset
from edc.data.ir.canonical import open_dataset
from edc.algorithms.registry import algorithms
from edc.reporting.results import write_json
from .references import DatasetView, dataset_reference, digest, file_digest
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
        rows.extend(require_schema(json.loads(line), RECORD)
                    for line in path.read_text().splitlines() if line.strip())
    return rows


def execution_identity(registry):
    root = Path(__file__).resolve().parents[1]
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    code = []
    code.extend((str(p.relative_to(root)), file_digest(p)) for p in sorted(root.rglob('*.py')))
    plugin_files = {}
    installed = list(distributions())
    for dist in installed:
        if any(point.group == 'edc.algorithms' for point in dist.entry_points):
            for item in dist.files or ():
                path = Path(dist.locate_file(item))
                if path.suffix == '.py' and path.is_file():
                    plugin_files[str(path.resolve())] = file_digest(path)
    scanned = set()
    for plugin in registry.plugins.values():
        try:
            # Callable-backed plugins (e.g. SimpleNamespace) also need the actual
            # implementation fingerprint, not just the stdlib container type.
            sources = [inspect.getsourcefile(type(plugin))]
            sources.extend(inspect.getsourcefile(method) for name in ('run', 'preflight', 'preflight_dataset')
                           if inspect.isfunction(method := getattr(plugin, name, None)))
            for source in filter(None, sources):
                path = Path(source).resolve()
                if path.is_relative_to(root):
                    continue  # Framework source is already fingerprinted above.
                # Installed flat modules share site-packages with unrelated
                # libraries; stdlib types share a directory with the whole Python
                # environment. Never recurse into either as a plugin package.
                flat = path.is_relative_to(stdlib) or bool({'site-packages', 'dist-packages'} & set(path.parts))
                scope = path if flat else path.parent
                if scope in scanned:
                    continue
                scanned.add(scope)
                candidates = [path] if flat else path.parent.rglob('*.py')
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
    source = open_dataset(**cfg['dataset'])
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
    write_json(root / 'resolved-config.json', cfg)
    manifest = {'schema_version': 'edc_workflow_run_v1', 'workflow_id': cfg['id'],
                'workflow_run_id': workflow_run, 'config': cfg, 'stages': []}
    previous = []
    datasets = [source]
    try:
        catalog_path = output / 'catalog.json'
        catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {
            'schema_version': CATALOG, 'runs': []}
        if catalog.get('schema_version') != CATALOG:
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
                if not stage['algorithms']:
                    row.update(status='skipped', reason='Optional stage has no configured method',
                               selected_episodes=[], output_reference=parent['reference'])
                    states[stage['id']] = parent
                    write_json(root / 'workflow.json', manifest)
                    continue
                selected = _select(stage, parent['dataset'], parent['records'])
                retained = []
                awaiting_confirmation = []
                if stage.get('when') == 'needs_editing':
                    awaiting_confirmation = [{'episode_id': i, 'reason': 'Missing inputs or human review; not edited',
                        'quality': parent['quality'].get(i, {})} for i in selected
                        if parent['quality'].get(i, {}).get('decision') in {'review', 'unassessed'}]
                    retained = [ep.id for ep in parent['dataset'].iter_episodes()
                                if parent['quality'].get(ep.id, {}).get('decision') == 'keep']
                    selected = [i for i in selected if parent['quality'].get(i, {}).get('decision') == 'exclude']
                    if not selected:
                        row.update(status='skipped', reason='No confirmed failing samples selected for editing',
                                   output_reference=parent['reference'])
                        states[stage['id']] = parent
                        write_json(root / 'workflow.json', manifest)
                        continue
                row['available_episodes'] = [ep.id for ep in parent['dataset'].iter_episodes()]
                row['requested_episodes'] = list(selected)
                row['withheld_episodes'] = awaiting_confirmation
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
                current = DatasetView(parent['dataset'], selected, stage.get('select', {}).get('channels'))
                datasets.append(current)
                from edc.algorithms.recommendations import recommend
                if any(set(ids) - set(selected) for ids in stage.get('method_select', {}).values()):
                    raise ValueError('Method subset includes episodes not released by the upstream stage')
                pool = recommend(current, stage.get('params', {}), cfg.get('unit_policy', 'auto'),
                                 method_select=stage.get('method_select'))
                for episode in pool['episodes']:
                    episode['algorithms'] = [m for m in episode['algorithms']
                        if m['algorithm']['id'] in stage['algorithms']]
                pool['dataset_algorithms'] = [m for m in pool['dataset_algorithms']
                    if m['algorithm']['id'] in stage['algorithms']]
                if stage['kind'] == 'value':
                    row['method_subsets'] = {m['algorithm']['id']: m['eligible_episodes'] if m['status'] == 'runnable' else []
                        for m in pool['dataset_algorithms'] if m['algorithm']['id'] in stage['algorithms']}
                    for episode in pool['episodes']:
                        for method in episode['algorithms']:
                            ident = method['algorithm']['id']
                            if ident in stage['algorithms'] and method['status'] == 'runnable' and episode['episode_id'] in stage.get('method_select', {}).get(ident, selected):
                                row['method_subsets'].setdefault(ident, []).append(episode['episode_id'])
                row['recommendations'] = pool
                if not stage['algorithms']:
                    row.update(status='blocked', reason='No installed production methods in this stage; SDK examples are excluded')
                    write_json(root / 'workflow.json', manifest)
                    continue
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
                        params=stage.get('params', {}), unit_policy=cfg.get('unit_policy', 'auto'), upstream=upstream,
                        selection_policy=stage.get('selection_policy', 'explicit'),
                        config_snapshot=cfg, method_select=stage.get('method_select'))
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
                    # Automatic selection gates only actually selected runnable methods.
                    required = stage['algorithms'] if stage.get('selection_policy') != 'runnable' else None
                    state['quality'] = combine_gates(parent['quality'], quality_gate(
                        records, selected, row['run_id'], read_reviews(output / 'reviews.jsonl'),
                        required_algorithms=required))
                    row['gate'] = state['quality']
                elif stage['kind'] == 'value':
                    state['valued_ids'] = []
                    for ident in selected:
                        relevant = [r for r in records if (r['object']['type'] == 'dataset' and
                            ident in r.get('eligible_episodes', selected)) or r['object']['id'] == ident]
                        expected = {name for name, subset in row['method_subsets'].items() if ident in subset}
                        if (expected and expected == {r['algorithm']['id'] for r in relevant} and
                                all(r['status'] == 'completed' and (r.get('result') or {}).get('reports') for r in relevant)):
                            state['valued_ids'].append(ident)
                    row['value_completed_episodes'] = state['valued_ids']
                    # Hand off only successfully valued episodes, preserving
                    # independent references for each method's usable subset.
                    row['method_output_references'] = {}
                    for method, subset in row['method_subsets'].items():
                        completed = [i for i in subset if i in state['valued_ids']]
                        if completed:
                            row['method_output_references'][method] = dataset_reference(DatasetView(current, completed))
                    valued = DatasetView(current, state['valued_ids'])
                    datasets.append(valued)
                    state.update(dataset=valued, reference=dataset_reference(valued))
                elif stage['kind'] in {'editing', 'augmentation'}:
                    children = [ep for ep in parent['dataset'].iter_episodes() if ep.id in retained]
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
                    if rejected or awaiting_confirmation:
                        row['status'] = 'partial_success'
                    if awaiting_confirmation:
                        row['reason'] = 'Some selected samples still require input confirmation or human review'
                row['output_reference'] = state['reference']
                states[stage['id']] = state
            except Exception as exc:
                row.update(status='processing_error', reason=f'{type(exc).__name__}: {exc}')
            write_json(root / 'workflow.json', manifest)
        # Detect unexpected writes by a plugin before publishing a workflow result.
        if dataset_reference(source) != source_ref:
            raise ValueError('Source changed during workflow execution')
        statuses = [r['status'] for r in manifest['stages']]
        manifest['status'] = ('completed' if all(s in {'completed', 'skipped'} for s in statuses) else
                              'partial_success' if any(s == 'completed' for s in statuses) else 'blocked')
        consumed = {stage['input'] for stage in cfg['stages']}
        manifest['outputs'] = []
        for ident, state in states.items():
            if ident == 'source' or ident in consumed:
                continue
            accepted = [e.id for e in state['dataset'].iter_episodes()
                        if state['quality'].get(e.id, {}).get('decision') == 'keep']
            final_stage = next(s for s in manifest['stages'] if s['id'] == ident)
            if final_stage['kind'] == 'value' and final_stage['status'] != 'skipped':
                accepted = [i for i in accepted if i in state.get('valued_ids', [])]
            manifest['outputs'].append({'stage': ident, 'data_reference': state['reference'],
                'accepted_episodes': accepted, 'quality': state['quality'],
                'published': final_stage['status'] in {'completed', 'skipped'} and
                    len(accepted) == len(list(state['dataset'].iter_episodes())) and bool(accepted)})
        manifest['delivery_status'] = ('ready' if manifest['outputs'] and
            all(o['published'] for o in manifest['outputs']) and manifest['status'] == 'completed' else 'suspended')
        manifest['pending'] = [{'stage': o['stage'], 'episode_id': i, **gate}
            for o in manifest['outputs'] for i, gate in o['quality'].items() if gate['decision'] != 'keep']
        manifest['pending'] += [{'stage': s['id'], 'status': s['status'], 'reason': s.get('reason',
            'Inspect processing states and per-method eligible subsets')} for s in manifest['stages']
            if s['status'] not in {'completed', 'skipped'}]
        manifest['pending'] += [{'stage': s['id'], **item} for s in manifest['stages']
            if s['kind'] == 'editing' for item in s.get('withheld_episodes', [])]
        write_json(root / 'workflow.json', manifest)
        with (output / '.edc.lock').open('x'):
            pass
        try:
            catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else catalog
            catalog.setdefault('workflows', []).append({'workflow_id': cfg['id'], 'workflow_run_id': workflow_run,
                'manifest': (root / 'workflow.json').relative_to(output).as_posix(),
                'report': (root / 'report.html').relative_to(output).as_posix()})
            write_json(catalog_path, catalog)
        finally:
            (output / '.edc.lock').unlink()
        from .report import render_workflow
        render_workflow(root, manifest, output)
        return manifest
    except Exception as exc:
        manifest.update(status='processing_error', reason=f'{type(exc).__name__}: {exc}')
        write_json(root / 'workflow.json', manifest)
        raise
    finally:
        for dataset in datasets:
            dataset.__exit__(None, None, None)
        lock.unlink(missing_ok=True)
