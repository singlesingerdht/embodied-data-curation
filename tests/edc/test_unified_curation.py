"""User-facing configuration, topology, subset and return-path contracts."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import yaml
from edc import (load_config, export_config, open_config_dataset, open_dataset, run,
                 recommend, run_workflow, validate_workflow, open_dataset_reference)
from edc.algorithms.registry import AlgorithmSpec, Registry, algorithms
from edc.workflows.references import dataset_reference, DatasetView


def fixture(tmp_path, arms=('main',), recorded=None):
    h5py = pytest.importorskip('h5py')
    source = tmp_path / 'source'
    source.mkdir()
    channels = {}
    for arm in arms:
        for kind in ('joint', 'pose'):
            name = f'{arm}_{kind}'
            channels[name] = {'modality': 'pose' if kind == 'pose' else 'joint_space',
                'fields': ['position'], 'robot_part': arm, 'side': arm if arm in {'left', 'right'} else 'unknown',
                'source_kind': 'feedback', 'logical_name': f'{kind}/arm/{arm}/feedback',
                'unit': 'm' if kind == 'pose' else 'deg', 'time_source': 'container',
                'representation': {'quantity': 'position' if kind == 'pose' else 'angle', 'semantic_status': 'confirmed'}}
    entries = []
    for i in range(2):
        name = f'e{i}.h5'
        with h5py.File(source / name, 'w') as handle:
            handle['timestamp'] = np.arange(120, dtype=np.float64) * .01
            for arm in (recorded if recorded is not None else arms):
                handle[f'{arm}_joint'] = np.ones((120, 2)) * 45
                handle[f'{arm}_pose'] = np.zeros((120, 3))
                handle[f'{arm}_pose'][:, 2] = .2
        entries.append({'id': f'e{i}', 'path': name, 'format': 'hdf5', 'selector': {'group': '/'}})
    profile = {'schema_version': 1, 'id': 'shared_robot', 'version': '1',
        'timestamp_column': 'timestamp', 'timestamp_unit': 's',
        'parts': {a: {'type': 'arm', 'side': a if a in {'left', 'right'} else 'unknown'} for a in arms}, 'channels': channels}
    return {'schema_version': 'edc_config_v1',
        'dataset': {'path': str(source), 'id': 'task_a', 'version': '1', 'tasks': ['open_drawer'], 'episodes': entries},
        'robot': profile, 'algorithms': {'defaults': {'arms': 'all'}, 'methods': {}},
        'workflow': {'id': 'test_curation', 'stages': [
            {'id': 'quality', 'kind': 'quality', 'input': 'source', 'algorithms': ['Action_Smoothness']}]}}


def records(output, ident):
    return [json.loads(line) for p in (output / '_runs').glob(f'*/{ident}/records.jsonl')
            for line in p.read_text().splitlines() if line]


def test_config_recommendation_respects_explicit_workflow_algorithms(tmp_path):
    from edc.configuration import recommend_config
    cfg = fixture(tmp_path)
    result = recommend_config(cfg)
    pool = result
    assert all([r['algorithm']['id'] for r in ep['algorithms']] == ['Action_Smoothness']
               for ep in pool['episodes'])


@pytest.mark.parametrize('arms,selection,expected', [
    (('main',), 'all', ['main']), (('left', 'right'), 'all', ['left', 'right']),
    (('left', 'right'), ['left'], ['left']), (('left', 'right'), ['right'], ['right'])])
def test_topology_controls_actual_execution(tmp_path, arms, selection, expected):
    cfg = fixture(tmp_path, arms)
    cfg['algorithms']['methods']['Action_Smoothness'] = {'arms': selection, 'params': {'expected_rate_hz': 100}}
    out = tmp_path / 'out'
    result = run_workflow(cfg, out)
    rows = records(out, 'Action_Smoothness')
    assert len(rows) == 2 * len(expected)
    assert {r['instance_id'] for r in rows} == set(expected)
    assert all(r['status'] == 'completed' and r['scope_complete'] for r in rows), rows
    for row in rows:
        assert row['effective_params']['channel'] == row['instance_id'] + '_pose'
        assert row['input_scope']['arms'] == [row['instance_id']]
        assert any(b['channel'] == row['effective_params']['channel'] for b in row['channel_bindings'])
    assert result['status'] == 'completed'


def test_missing_declared_arm_is_partial_and_blocks_release(tmp_path):
    cfg = fixture(tmp_path, ('left', 'right'), recorded=('left',))
    out = tmp_path / 'out'
    result = run_workflow(cfg, out)
    pool = result['stages'][0]['recommendations']['episodes'][0]['algorithms']
    smooth = next(r for r in pool if r['algorithm']['id'] == 'Action_Smoothness')
    assert smooth['availability'] == 'partial'
    assert any(r['instance_id'] == 'right' and r['status'] == 'missing_input' for r in records(out, 'Action_Smoothness'))
    assert all(g['decision'] == 'unassessed' for g in result['stages'][0]['gate'].values())
    assert result['delivery_status'] == 'suspended'


def test_unknown_topology_and_conflicting_explicit_input_fail_without_fallback(tmp_path):
    cfg = fixture(tmp_path, ('left', 'right'))
    cfg['algorithms']['methods']['Action_Smoothness'] = {'arms': ['left'], 'params': {'channel': 'pose/arm/right/feedback'}}
    result = run_workflow(cfg, tmp_path / 'out')
    assert all(r['status'] == 'missing_input' and 'unselected arm right' in r['reason'] for r in records(tmp_path/'out', 'Action_Smoothness'))
    cfg['robot']['parts'] = {}
    for c in cfg['robot']['channels'].values(): c.pop('robot_part')
    cfg['algorithms']['methods'] = {}
    result = run_workflow(cfg, tmp_path / 'unknown')
    assert all('topology' in r['reason'] for r in records(tmp_path/'unknown', 'Action_Smoothness'))


def test_same_robot_two_tasks_and_ref_export_equivalence(tmp_path):
    cfg = fixture(tmp_path)
    folder = tmp_path / 'configs'
    folder.mkdir()
    refs = {'schema_version': 'edc_config_v1'}
    for key in ('dataset', 'robot', 'algorithms', 'workflow'):
        (folder / f'{key}.yaml').write_text(yaml.safe_dump(cfg[key]))
        refs[key] = {'ref': f'{key}.yaml'}
    path = folder / 'run.yaml'
    path.write_text(yaml.safe_dump(refs))
    target = export_config(path, tmp_path/'resolved.yaml')
    assert load_config(path) == load_config(target)
    with open_config_dataset(path) as first, open_config_dataset(target) as second:
        assert dataset_reference(first) == dataset_reference(second)
        cfg_b = load_config(path)
        cfg_b['dataset'].update(id='task_b', tasks=['microwave'])
        with open_config_dataset(cfg_b) as third:
            assert first.profile == third.profile
            assert first.tasks == ['open_drawer'] and third.tasks == ['microwave']
            assert first.id != third.id
    result = run_workflow(path, tmp_path / 'out')
    snap = json.loads(next((tmp_path/'out/_workflows').glob('*/resolved-config.json')).read_text())
    assert snap == load_config(path)
    assert result['config']['config_snapshot'] == snap


@pytest.mark.parametrize('relative', [False, True])
def test_input_override_preserves_explicit_boundary_and_configuration(tmp_path, monkeypatch, relative):
    cfg = fixture(tmp_path)
    cfg['dataset']['episodes'] = cfg['dataset']['episodes'][:1]
    cfg['dataset']['episodes'][0]['metadata'] = {'source_sha256': 'original-file-hash', 'task_status': 'unconfirmed'}
    original = copy.deepcopy(cfg)
    configs = tmp_path / 'configs'
    configs.mkdir()
    config_path = configs / 'run.yaml'
    config_path.write_text(yaml.safe_dump(cfg))
    before = config_path.read_bytes()
    source = Path(cfg['dataset']['path']) / 'e0.h5'
    replacement = tmp_path / 'new input.h5'
    replacement.write_bytes(source.read_bytes())
    monkeypatch.chdir(tmp_path)
    resolved = load_config(config_path, input_path=replacement.name if relative else replacement)
    assert cfg == original and config_path.read_bytes() == before
    assert source.read_bytes() == replacement.read_bytes()
    assert resolved['dataset']['path'] == str(tmp_path)
    entry, = resolved['dataset']['episodes']
    assert entry == {**original['dataset']['episodes'][0], 'path': replacement.name,
                     'metadata': {'task_status': 'unconfirmed'}}
    for component in ('robot', 'algorithms', 'workflow'):
        assert resolved[component] == load_config(config_path)[component]
    with open_config_dataset(resolved) as dataset:
        episode, = dataset.iter_episodes()
        assert episode.id == 'e0' and episode.selector == {'group': '/'}
        with episode.opened():
            np.testing.assert_allclose(episode.read_signal('main_joint', 'position'), 45.)


def test_input_override_without_explicit_index_uses_reader_episode_metadata(tmp_path):
    cfg = fixture(tmp_path)
    cfg['dataset'].pop('episodes')
    source = Path(cfg['dataset']['path']) / 'e1.h5'
    resolved = load_config(cfg, input_path=source)
    assert resolved['dataset']['path'] == str(source)
    assert 'episodes' not in resolved['dataset']
    with open_config_dataset(resolved) as dataset:
        assert len(list(dataset.iter_episodes())) == 1


@pytest.mark.parametrize('entries', [[], 'invalid', [{}, {}]])
def test_input_override_rejects_ambiguous_episode_indexes(tmp_path, entries):
    cfg = fixture(tmp_path)
    cfg['dataset']['episodes'] = entries
    with pytest.raises(ValueError, match='exactly one explicit episode'):
        load_config(cfg, input_path=Path(cfg['dataset']['path']) / 'e0.h5')


def test_cli_config_input_is_shared_by_check_recommend_export_and_run(tmp_path, capsys):
    from edc.execution.cli import main
    cfg = fixture(tmp_path)
    cfg['dataset']['episodes'] = cfg['dataset']['episodes'][:1]
    cfg['algorithms']['methods']['Action_Smoothness'] = {'params': {'expected_rate_hz': 100}}
    source = Path(cfg['dataset']['path']) / 'e0.h5'
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    replacement = inputs / 'replacement.h5'
    replacement.write_bytes(source.read_bytes())
    source.unlink()  # The configured source cannot satisfy any read by mistake.
    config_path = tmp_path / 'run.yaml'
    config_path.write_text(yaml.safe_dump(cfg))
    before = config_path.read_bytes()
    args = [str(config_path), '--input', str(replacement)]
    assert main(['config', 'check', *args]) == 0
    checked = json.loads(capsys.readouterr().out)['config']['config_snapshot']
    assert main(['config', 'recommend', *args]) == 0
    recommended = json.loads(capsys.readouterr().out)
    assert recommended['config_snapshot'] == checked
    assert recommended['episodes'][0]['episode_id'] == 'e0'
    exported = tmp_path / 'resolved.yaml'
    assert main(['config', 'export', *args, '--output', str(exported)]) == 0
    capsys.readouterr()
    assert load_config(exported) == checked
    output = tmp_path / 'out'
    assert main(['config', 'run', *args, '--output', str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['config']['config_snapshot'] == checked
    saved = json.loads(next((output / '_workflows').glob('*/resolved-config.json')).read_text())
    assert saved == checked and saved['dataset']['episodes'][0]['path'] == replacement.name
    assert (output / 'report.html').is_file()
    assert records(output, 'Action_Smoothness')[0]['object']['id'] == 'e0'
    assert config_path.read_bytes() == before


def test_cli_missing_input_fails_before_creating_output(tmp_path, capsys):
    from edc.execution.cli import main
    cfg = fixture(tmp_path)
    config_path = tmp_path / 'run.yaml'
    config_path.write_text(yaml.safe_dump(cfg))
    output = tmp_path / 'out'
    assert main(['config', 'run', str(config_path), '--input', str(tmp_path / 'missing.mcap'),
                 '--output', str(output)]) == 2
    assert 'Input path does not exist' in capsys.readouterr().err
    assert not output.exists()


def test_raw_finiteness_unknown_unit_matches_preflight(tmp_path):
    cfg = fixture(tmp_path)
    for c in cfg['robot']['channels'].values():
        c['unit'] = None
        c['representation']['semantic_status'] = 'unknown'
    params = {'team_example__finite_check': {'channels': ['joint/arm/main/feedback']}}
    with open_config_dataset(cfg) as dataset:
        pool = recommend(dataset, params=params)
        assert all(next(r for r in ep['algorithms'] if r['algorithm']['id']=='team_example__finite_check')['status'] == 'runnable' for ep in pool['episodes'])
        run(dataset, algorithm_ids=list(params), params=params, output=tmp_path/'out')
    assert all(r['status']=='completed' and r['result']['reports'][0]['invalid_count']==0 for r in records(tmp_path/'out','team_example__finite_check'))


def test_default_pool_runs_partial_and_excludes_sdk_examples(tmp_path):
    cfg = fixture(tmp_path)
    cfg['workflow']['stages'][0]['algorithms'] = 'recommended'
    cfg['algorithms']['methods'] = {'Video_Integrity': {'enabled': False}}
    result = run_workflow(cfg, tmp_path/'out')
    rows = records(tmp_path/'out', 'Numeric_Check')
    assert rows and all(r['status']=='completed' for r in rows)
    assert not records(tmp_path/'out','team_example__finite_check')
    assert all(g['decision'] != 'keep' for g in result['stages'][0]['gate'].values())
    assert result['delivery_status'] == 'suspended'


@pytest.mark.parametrize('mode', ['no_gpu', 'available', 'disabled', 'required', 'failed'])
def test_cpu_and_optional_gpu_share_quality_pool(tmp_path, monkeypatch, mode):
    # Contract doubles exercise orchestration, never real CUDA/model inference.
    cfg = fixture(tmp_path)
    template = Path(__file__).resolve().parents[2] / 'configs/workflows/curation.yaml'
    cfg['workflow'] = yaml.safe_load(template.read_text())
    assert [s['kind'] for s in cfg['workflow']['stages']] == ['quality']
    calls = []

    def checked(context, params):
        calls.append(('cpu', context.episode.id))
        return {'verdict': 'ACCEPTABLE'}

    def aligned(context, params):
        calls.append(('gpu', context.episode.id))
        if mode == 'failed':
            raise RuntimeError('Simulated GPU execution failure')
        return {'verdict': 'ACCEPTABLE'}

    def valued(context, params):
        calls.append(('value', context.episode.id))
        return {'raw_value': 0.5}

    plugins = [
        SimpleNamespace(spec=AlgorithmSpec('test__cpu', '1', 'CPU fixture', 'test', 'Contract fixture'), run=checked),
        SimpleNamespace(spec=AlgorithmSpec('test__alignment', '1', 'GPU fixture', 'test',
            'Contract fixture', category='quality', device='cuda'), run=aligned),
        SimpleNamespace(spec=AlgorithmSpec('test__value', '1', 'Value fixture', 'test',
            'Contract fixture', category='value'), run=valued)]
    if mode in {'available', 'disabled', 'failed'}:
        plugins[1].preflight = lambda ep, params: ('runnable', '')
    else:
        plugins[1].preflight = lambda ep, params: ('missing_dependency', 'CUDA unavailable (contract fixture)')
    import edc.algorithms.registry as registry_module
    monkeypatch.setattr(registry_module, 'entry_points', lambda group: [
        SimpleNamespace(name=p.spec.id, load=lambda p=p: p) for p in plugins
    ] if group == 'edc.algorithms' else [])
    cfg['algorithms']['methods'] = {ident: {'enabled': False}
        for ident in algorithms().plugins if ident not in {p.spec.id for p in plugins}}
    if mode == 'disabled':
        cfg['algorithms']['methods']['test__alignment'] = {'enabled': False}
    if mode == 'required':
        cfg['workflow']['stages'][0].update(
            algorithms=['test__cpu', 'test__alignment'], selection_policy='explicit')
    output = tmp_path / 'quality_only'
    result = run_workflow(cfg, output)
    assert [s['kind'] for s in result['stages']] == ['quality']
    assert len([c for c in calls if c[0] == 'cpu']) == 2
    assert not any(c[0] == 'value' for c in calls)
    gpu_rows = records(output, 'test__alignment')
    assert len([c for c in calls if c[0] == 'gpu']) == (2 if mode in {'available', 'failed'} else 0)
    if mode in {'no_gpu', 'disabled'}:
        assert not gpu_rows
    else:
        status = {'available': 'completed', 'required': 'missing_dependency', 'failed': 'processing_error'}[mode]
        assert len(gpu_rows) == 2 and all(r['status'] == status for r in gpu_rows)
    for ep in result['stages'][0]['recommendations']['episodes']:
        info = next(r for r in ep['algorithms'] if r['algorithm']['id'] == 'test__alignment')
        assert info['environment_requirements']['device'] == 'cuda'
        if mode in {'no_gpu', 'required'}:
            assert info['availability'] == 'unavailable' and info['reason']
    assert result['delivery_status'] == ('suspended' if mode in {'required', 'failed'} else 'ready')
    report_path = next((output / '_workflows').glob('*/report.html'))
    flow = report_path.read_text().split('<div class="flow">', 1)[1].split('</div>', 1)[0]
    assert '质量评估' in flow and '价值评估' not in flow
    # Adding a value stage is an explicit opt-in; it still respects the gate.
    cfg['workflow']['stages'].append({'id': 'value', 'kind': 'value', 'input': 'quality',
        'algorithms': ['test__value'], 'selection_policy': 'explicit'})
    calls.clear()
    result = run_workflow(cfg, tmp_path / 'with_value')
    expected = 0 if mode in {'required', 'failed'} else 2
    assert len([c for c in calls if c[0] == 'value']) == expected


def test_dataset_method_own_subset_and_channel_reference_roundtrip(tmp_path, monkeypatch):
    cfg = fixture(tmp_path)
    seen = []
    class Value:
        spec = AlgorithmSpec('test__value', '1', 'subset', 'test', 'dataset subset', category='value', granularity='dataset',
            episode_requirements=({'id':'pose', 'channel':'pose/arm/main/feedback', 'modality':'pose','field':'position','unit':'m'},))
        def preflight_episode(self, episode, params):
            return ('runnable','') if episode.id=='e0' else ('missing_input','Task-specific prerequisite missing')
        def preflight_dataset(self, dataset, params):
            return ('runnable','') if list(dataset.iter_episodes()) else ('missing_input','Empty')
        def run(self, context, params):
            seen.append([e.id for e in context.dataset.iter_episodes()])
            assert context.upstream['quality']['run_id']
            return {'episode_ids': seen[-1], 'score': None}
    plugin = Value()
    import edc.algorithms.registry as registry_module
    monkeypatch.setattr(registry_module, 'entry_points', lambda group: [SimpleNamespace(name='test__value',load=lambda:plugin)] if group=='edc.algorithms' else [])
    cfg['workflow']['stages'] = [
        {'id':'quality','kind':'quality','input':'source','algorithms':['test__finite']},
        {'id':'value','kind':'value','input':'quality','algorithms':['test__value']}]
    class Finite:
        spec=AlgorithmSpec('test__finite','1','test','test','Quality fixture')
        def run(self,context,params):return {'verdict':'ACCEPTABLE'}
    finite=Finite()
    monkeypatch.setattr(registry_module,'entry_points',lambda group:[SimpleNamespace(name=p.spec.id,load=lambda p=p:p) for p in (plugin,finite)] if group=='edc.algorithms' else [])
    result = run_workflow(cfg,tmp_path/'out')
    assert seen == [['e0']]
    assert result['stages'][1]['method_subsets']['test__value']==['e0']
    assert result['delivery_status'] == 'ready'
    assert result['outputs'][0]['accepted_episodes'] == ['e0']
    with open_dataset_reference(result['outputs'][0]['data_reference']) as delivered:
        assert [e.id for e in delivered.iter_episodes()] == ['e0']
    with open_config_dataset(cfg) as dataset:
        subset=DatasetView(dataset,['e1'],['joint/arm/main/feedback'])
        reference=dataset_reference(subset)
    with open_dataset_reference(reference) as restored:
        episode=next(restored.iter_episodes())
        with episode.opened():assert list(episode.channels)==['main_joint']


def test_augmented_failure_suspends_output_and_optional_edit_is_skipped(tmp_path):
    # The full example checks transform consistency and returns through recheck twice.
    from importlib.util import spec_from_file_location, module_from_spec
    spec=spec_from_file_location('demo',Path(__file__).resolve().parents[2]/'examples/workflows/make_example.py')
    module=module_from_spec(spec);spec.loader.exec_module(module)
    path=module.generate(tmp_path/'demo')
    cfg=yaml.safe_load(path.read_text());cfg['dataset']['path']=str(path.parent/'source')
    cfg['stages'][1]['when']='needs_editing'
    result=run_workflow(cfg,tmp_path/'out')
    assert result['stages'][1]['status']=='skipped'
    assert result['delivery_status']=='ready'
    assert result['stages'][-1]['gate']
    cfg['stages'][-1]['algorithms']=['Reset_Detection']
    result=run_workflow(cfg,tmp_path/'suspended')
    assert result['delivery_status']=='suspended'
    assert not result['outputs'][0]['published']
    assert all(g['decision']!='keep' for g in result['stages'][-1]['gate'].values())


def test_explicit_both_arms_preserves_present_side_when_other_is_missing(tmp_path):
    cfg = fixture(tmp_path, ('left', 'right'), recorded=('left',))
    cfg['algorithms']['methods']['Action_Smoothness'] = {'params': {'channels': [
        'pose/arm/left/feedback', 'pose/arm/right/feedback']}}
    result = run_workflow(cfg, tmp_path/'out')
    rows = records(tmp_path/'out', 'Action_Smoothness')
    assert {r['instance_id'] for r in rows if r['status']=='completed'} == {'left'}
    assert {r['instance_id'] for r in rows if r['status']=='missing_input'} == {'right'}
    assert result['delivery_status'] == 'suspended'


@pytest.mark.parametrize('other_review', [False, True])
def test_edit_failed_samples_preserves_passed_samples_and_rechecks_new_version(tmp_path, monkeypatch, other_review):
    import hashlib
    import importlib.util
    import h5py
    import edc.algorithms.registry as registry_module
    spec = importlib.util.spec_from_file_location('edit_demo', Path(__file__).resolve().parents[2]/'examples/workflows/make_example.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    path = module.generate(tmp_path/'demo')
    cfg = yaml.safe_load(path.read_text()); cfg['dataset']['path'] = str(path.parent/'source')
    # Deliberately failing synthetic image, repaired only by an explicitly selected example.
    with h5py.File(path.parent/'source/episode_0.h5', 'r+') as handle:
        handle['head_rgb'][...] = 0
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path.parent/'source').iterdir()}
    class ImageNonzero:
        spec = AlgorithmSpec('test__nonzero', '1', 'test', 'test', 'Synthetic return-path criterion',
            parameter_schema={'type':'object','properties':{'channel':{'type':'string','default':'rgb/head'}}},
            data_requirements=({'id':'image','channel_param':'channel','modality':'rgb'},))
        def run(self, context, params):
            frame = next(context.episode.iter_frames(params['channel']))['image']
            if other_review and context.episode.id=='episode_1':
                return {'verdict': 'NEEDS_REVIEW', 'score': 100}
            return {'verdict': 'ACCEPTABLE' if np.mean(frame) > 0 else 'DO_NOT_USE'}
    original = registry_module.entry_points
    plugin = ImageNonzero()
    monkeypatch.setattr(registry_module, 'entry_points', lambda group: [*original(group=group),
        *([SimpleNamespace(name=plugin.spec.id, load=lambda: plugin)] if group=='edc.algorithms' else [])])
    for stage in cfg['stages']:
        if stage['kind']=='quality': stage['algorithms'] = ['test__nonzero']
        if stage['kind']=='value': stage['params'] = {'team_example__dataset_inventory': {'min_episodes': 1}}
    cfg['stages'][1]['when'] = 'needs_editing'
    result = run_workflow(cfg, tmp_path/'out')
    first, edit, recheck, value, augment, final = result['stages']
    assert first['gate']['episode_0']['decision']=='exclude'
    assert first['gate']['episode_1']['decision']==('review' if other_review else 'keep')
    assert edit['selected_episodes']==['episode_0'] and not edit['transform_errors']
    assert set(recheck['selected_episodes'])==({'episode_0__editing_0'} if other_review else {'episode_0__editing_0','episode_1'})
    assert all(v['decision']=='keep' for v in recheck['gate'].values())
    assert len(final['selected_episodes'])==(2 if other_review else 4) and all(v['decision']=='keep' for v in final['gate'].values())
    assert result['delivery_status']==('suspended' if other_review else 'ready')
    if other_review:
        assert any(p.get('episode_id')=='episode_1' for p in result['pending'])
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path.parent/'source').iterdir()}
    assert before==after


def test_disjoint_value_subsets_and_subset_minimum_use_same_recommendation(tmp_path, monkeypatch):
    import edc.algorithms.registry as registry_module
    cfg=fixture(tmp_path)
    seen={}
    class Quality:
        spec=AlgorithmSpec('test__quality','1','test','test','Quality fixture')
        def run(self,context,params): return {'verdict':'ACCEPTABLE'}
    class Value:
        def __init__(self, ident, episode=None, minimum=1):
            self.episode, self.minimum=episode, minimum
            self.spec=AlgorithmSpec(ident,'1','test','test','Subset fixture', category='value',granularity='dataset')
        def preflight_episode(self,episode,params):
            return ('runnable','') if self.episode is None or episode.id==self.episode else ('missing_input','Other task')
        def preflight_dataset(self,dataset,params):
            return ('runnable','') if len(list(dataset.iter_episodes()))>=self.minimum else ('missing_input','Need two episodes')
        def run(self,context,params):
            seen[self.spec.id]=[e.id for e in context.dataset.iter_episodes()]
            return {'episodes':seen[self.spec.id]}
    plugins=[Quality(),Value('test__value_a','e0'),Value('test__value_b','e1'),Value('test__value_pair',minimum=2)]
    monkeypatch.setattr(registry_module,'entry_points',lambda group:[SimpleNamespace(name=p.spec.id,load=lambda p=p:p)
        for p in plugins] if group=='edc.algorithms' else [])
    cfg['workflow']['stages']=[
        {'id':'quality','kind':'quality','input':'source','algorithms':['test__quality']},
        {'id':'value','kind':'value','input':'quality','algorithms':['test__value_a','test__value_b','test__value_pair'],
         'method_select':{'test__value_pair':['e0']}}]
    result=run_workflow(cfg,tmp_path/'out')
    assert seen=={'test__value_a':['e0'],'test__value_b':['e1']}
    row=result['stages'][1]
    assert row['method_subsets']=={'test__value_a':['e0'],'test__value_b':['e1'],'test__value_pair':[]}
    check=next(m for m in row['recommendations']['dataset_algorithms'] if m['algorithm']['id']=='test__value_pair')
    assert check['status']=='missing_input' and check['reason']=='Need two episodes'
    assert len(row['method_output_references'])==2 and result['delivery_status']=='ready'


def test_config_rejects_invalid_backend_scope_and_cycles(tmp_path):
    cfg=fixture(tmp_path)
    cfg['dataset']['backend']='typo'
    with pytest.raises(ValueError,match='backend'): load_config(cfg)
    cfg['dataset']['backend']='official'
    with pytest.raises(ValueError,match='LeRobot'): open_config_dataset(cfg)
    cfg['dataset']['backend']='auto'
    cfg['robot']['channels']['main_pose']['applicability']={'dataset_ids':['another_dataset']}
    with pytest.raises(ValueError,match='does not apply'): open_config_dataset(cfg)
    (tmp_path/'cycle.yaml').write_text('ref: cycle.yaml\n')
    cfg['robot']={'ref':str(tmp_path/'cycle.yaml')}
    with pytest.raises(ValueError,match='Cyclic'): load_config(cfg)


def test_declared_arm_does_not_imply_wrist_camera(tmp_path):
    import h5py
    from edc.data.ir.units import SIDataset
    from edc.robots.scope import instances
    cfg=fixture(tmp_path)
    cfg['robot']['channels']['head_rgb']={'modality':'rgb','logical_name':'rgb/head',
        'sensor_id':'head','representation':{'camera_role':'head'}}
    with h5py.File(Path(cfg['dataset']['path'])/'e0.h5','r+') as handle:
        handle['head_rgb']=np.zeros((120,8,8,3),dtype=np.uint8)
    with open_config_dataset(cfg) as dataset:
        episode=next(SIDataset(dataset,'auto').iter_episodes())
        with episode.opened():
            row,=instances(episode,{'arms':'all'},'rgb')
            assert row['complete'] and row['scope']['missing_arms']==[]
            assert row['params']['channels']==['head_rgb']
