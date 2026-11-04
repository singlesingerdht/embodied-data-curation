"""Optional stages retain explicit quality gates for every derived version."""
import copy
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from edc import run_workflow, validate_workflow, open_dataset, dataset_reference
from edc.algorithms.base import QualityAlgorithm, Item
from edc.algorithms.registry import AlgorithmSpec, algorithms
from edc.workflows.gates import quality_gate
from edc.workflows.reviews import review


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def demo(tmp_path, monkeypatch):
    pytest.importorskip('h5py')
    root = Path(__file__).resolve().parents[2]
    registry = algorithms()
    for name, attributes in [('image_transform', ('editing_plugin', 'augmentation_plugin')),
                             ('dataset_inventory', ('plugin',))]:
        module = load_module(root / 'examples/plugins/workflow' / f'{name}.py', 'wf_' + name)
        for attribute in attributes:
            plugin = getattr(module, attribute)
            if plugin.spec.id not in registry.plugins:
                registry.register(plugin)
    for name in ('edc.workflows.engine', 'edc.workflows.validation',
                 'edc.execution.runner', 'edc.algorithms.recommendations'):
        monkeypatch.setattr(name + '.algorithms', lambda: registry)
    module = load_module(root / 'examples/workflows/make_example.py', 'workflow_data')
    path = module.generate(tmp_path / 'demo')
    config = yaml.safe_load(path.read_text())
    config['dataset']['path'] = str(path.parent / 'source')
    return path, config, registry


@pytest.mark.parametrize('item,expected', [
    (Item('left', verdict='PASS'), 'keep'),
    (Item('right', verdict='REVIEW', reason='Possible shake'), 'review'),
    (Item('right', verdict='EXCLUDE', reason='Out of bounds'), 'exclude'),
    (Item('right', verdict='SALVAGE', reason='Known offset', remedy='Apply approved mapping'), 'exclude'),
    (Item('right', status='not_applicable', reason='Unit unknown'), 'unassessed'),
    (Item('right', status='failed', reason='Decoder failed'), 'unassessed'),
])
@pytest.mark.parametrize('kind', ['edc_item', 'edc_result'])
def test_workflow_uses_common_items_without_rescoring(item, expected, kind):
    from dataclasses import asdict
    from edc.algorithms.base import AlgorithmResult
    report = ({kind: asdict(item)} if kind == 'edc_item' else
              {kind: AlgorithmResult('test__quality', [item]).to_dict()})
    record = {'algorithm': {'id': 'test__quality'}, 'object': {'type': 'episode', 'id': 'e'},
              'status': 'completed', 'result': {'reports': [report]}}
    before = copy.deepcopy(record)
    assert quality_gate([record], ['e'], 'run')['e']['decision'] == expected
    assert record == before


def test_full_chain_and_content_checked_resume(demo, tmp_path):
    path, config, _ = demo
    with open_dataset(config['dataset']['path']) as source:
        before = dataset_reference(source)
    output = tmp_path / 'results'
    first = run_workflow(path, output)
    assert first['status'] == 'completed', first
    assert first['delivery_status'] == 'ready', first['pending']
    assert [len(s['selected_episodes']) for s in first['stages']] == [2, 2, 2, 2, 2, 4]
    assert all(g['decision'] == 'keep' for g in first['stages'][-1]['gate'].values())
    with open_dataset(config['dataset']['path']) as source:
        assert dataset_reference(source) == before
    second = run_workflow(path, output, resume=True)
    assert second['delivery_status'] == 'ready'
    assert all(s['reused'] for s in second['stages'])
    assert [s['run_id'] for s in second['stages']] == [s['run_id'] for s in first['stages']]
    catalog = json.loads((output / 'catalog.json').read_text())
    assert len(catalog['workflows']) == 2
    assert (output / catalog['workflows'][-1]['report']).is_file()
    evidence = next((output / '_runs' / first['stages'][0]['run_id']).rglob('unit-normalization.json'))
    evidence.unlink()
    third = run_workflow(path, output, resume=True)
    assert not third['stages'][0]['reused']


@pytest.mark.parametrize('optional', ['editing', 'value', 'augmentation'])
def test_empty_optional_stage_preserves_current_version(demo, tmp_path, optional):
    _, config, _ = demo
    config['stages'] = [config['stages'][0],
        {'id': 'optional', 'kind': optional, 'input': 'quality', 'algorithms': []}]
    result = run_workflow(config, tmp_path / 'optional')
    assert result['delivery_status'] == 'ready'
    assert result['stages'][1]['status'] == 'skipped'
    assert result['stages'][1]['output_reference'] == result['stages'][0]['output_reference']


@pytest.mark.parametrize('kind', ['editing', 'augmentation'])
@pytest.mark.parametrize('change', ['no_recheck', 'wrong_version', 'another_edit'])
def test_transform_requires_quality_recheck_on_every_branch(demo, tmp_path, kind, change):
    _, config, _ = demo
    index = next(i for i, s in enumerate(config['stages']) if s['kind'] == kind)
    config['stages'] = config['stages'][:index + 2]
    transform, recheck = config['stages'][-2:]
    if change == 'no_recheck':
        config['stages'].pop()
    elif change == 'wrong_version':
        recheck['input'] = transform['input']
    else:
        config['stages'].append({'id': 'wrong', 'kind': 'editing', 'input': transform['id'],
            'algorithms': ['team_example__image_edit'], 'select': {'all': True}})
    output = tmp_path / 'invalid'
    with pytest.raises(ValueError, match='requires a quality recheck'):
        run_workflow(config, output)
    assert not output.exists()


@pytest.mark.parametrize('kind', ['editing', 'augmentation'])
def test_failed_recheck_cannot_inherit_parent_pass(demo, tmp_path, kind):
    _, config, _ = demo
    index = next(i for i, s in enumerate(config['stages']) if s['kind'] == kind)
    config['stages'] = config['stages'][:index + 2]
    config['stages'][-1]['algorithms'] = ['Reset_Detection']  # Fixture has no Home.
    result = run_workflow(config, tmp_path / 'recheck')
    assert result['stages'][index]['status'] == 'completed'
    assert result['delivery_status'] == 'suspended'
    assert all(g['decision'] == 'unassessed' for g in result['stages'][-1]['gate'].values())
    assert not result['outputs'][0]['published']


def test_value_failure_cannot_start_augmentation(demo, tmp_path, monkeypatch):
    _, config, registry = demo
    def fail(*args):
        raise RuntimeError('Value method failed')
    monkeypatch.setattr(registry.plugins['team_example__dataset_inventory'], 'run', fail)
    result = run_workflow(config, tmp_path / 'failure')
    assert result['stages'][3]['status'] == 'processing_error'
    assert result['stages'][4]['status'] == 'blocked'
    assert result['delivery_status'] == 'suspended'


class ReviewQuality(QualityAlgorithm):
    spec = AlgorithmSpec('test__review', '1', 'Review probe', 'Tests', 'Contract fixture')
    def check(self, episode, params):
        return [Item('head', verdict='REVIEW', reason='Possible frozen scene')]


def test_human_review_resumes_without_overwriting_method_output(demo, tmp_path):
    _, config, registry = demo
    registry.register(ReviewQuality())
    config['stages'] = [
        {'id': 'q', 'kind': 'quality', 'input': 'source', 'algorithms': ['test__review']},
        {'id': 'v', 'kind': 'value', 'input': 'q', 'algorithms': ['team_example__dataset_inventory']}]
    output = tmp_path / 'review'
    first = run_workflow(config, output)
    assert first['stages'][1]['status'] == 'pending_review'
    records = next((output / '_runs' / first['stages'][0]['run_id']).glob('*/records.jsonl'))
    before = records.read_bytes()
    for episode in ('episode_0', 'episode_1'):
        review(output, run_id=first['stages'][0]['run_id'], algorithm='test__review', object_id=episode,
            action='dismissed', reviewer='Synthetic test', reason='Static scene', evidence='Fixture')
    second = run_workflow(config, output, resume=True)
    assert second['stages'][0]['reused']
    assert second['delivery_status'] == 'ready'
    assert records.read_bytes() == before


def test_workflow_cli(demo, tmp_path, capsys):
    from edc.execution.cli import main
    path, _, _ = demo
    assert main(['workflow', 'check', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['valid']
    assert main(['workflow', 'run', str(path), '--output', str(tmp_path / 'cli')]) == 0
    assert json.loads(capsys.readouterr().out)['delivery_status'] == 'ready'


def test_robot_declares_topology_stage_selects_arm(demo, tmp_path):
    _, config, _ = demo
    card = {'robot': {'name': 'scope', 'confirmed_by': 'Synthetic test'},
            'arms': {name: {'joints': {'source': 'joint_pos', 'index': index, 'unit': 'deg'}}
                     for name, index in [('left', 0), ('right', 1)]},
            'checks': {'Numeric_Check': {'arms': ['left'], 'freeze_min_duration_s': 7}}}
    robot = tmp_path / 'robot.yaml'
    robot.write_text(yaml.safe_dump(card))
    config['dataset']['robot'] = str(robot)
    config['stages'] = [{'id': 'numeric', 'kind': 'quality', 'input': 'source',
        'algorithms': ['Numeric_Check'], 'params': {'Numeric_Check': {'arms': ['right']}}}]
    resolved = validate_workflow(config)['config']
    assert resolved['dataset']['profile'] == str(robot)
    params = resolved['stages'][0]['params']['Numeric_Check']
    assert params['arms'] == ['right']
    assert params['freeze_min_duration_s'] == 7


def test_review_isolated_between_left_and_right_instances(demo, tmp_path):
    _, config, registry = demo
    class DualReview(ReviewQuality):
        spec = replace(ReviewQuality.spec, id='test__dual_review')
        def instances(self, episode, params):
            return [{'id': arm, 'params': {}, 'scope': {'arms': [arm]}, 'complete': True}
                    for arm in ('left', 'right')]
    registry.register(DualReview())
    config['stages'] = [{'id': 'q', 'kind': 'quality', 'input': 'source',
                         'algorithms': ['test__dual_review']}]
    output = tmp_path / 'arms'
    first = run_workflow(config, output)
    arguments = dict(run_id=first['stages'][0]['run_id'], algorithm='test__dual_review',
        object_id='episode_0', action='dismissed', reviewer='Test', reason='Static fixture', evidence='Fixture')
    with pytest.raises(ValueError, match='instance_id'):
        review(output, **arguments)
    left = review(output, instance_id='left', **arguments)
    second = run_workflow(config, output, resume=True)
    assert second['stages'][0]['gate']['episode_0']['decision'] == 'review'
    right = review(output, instance_id='right', **arguments)
    assert left['subject_id'] != right['subject_id']
    third = run_workflow(config, output, resume=True)
    assert third['stages'][0]['gate']['episode_0']['decision'] == 'keep'
    assert third['stages'][0]['gate']['episode_1']['decision'] == 'review'
