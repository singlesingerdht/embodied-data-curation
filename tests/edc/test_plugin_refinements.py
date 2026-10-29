"""Import isolation and multi-episode execution, including SI and failure paths."""
import json
from types import SimpleNamespace
import numpy as np
import pytest
from edc.execution.examples import generate
from edc.execution.runner import run
from edc.data.ir.dataset import open_dataset
from edc.algorithms.recommendations import recommend
from edc.algorithms.registry import AlgorithmSpec, Registry, algorithms


def point(name, plugin=None, error=None):
    def load():
        if error:
            raise error
        return plugin
    return SimpleNamespace(name=name, load=load)


def install(monkeypatch, points):
    monkeypatch.setattr('edc.algorithms.registry.entry_points',
        lambda group: points if group == 'edc.algorithms' else [])


def read_records(output, ident):
    return [json.loads(line) for line in next((output/'_runs').glob(f'*/{ident}/records.jsonl')).read_text().splitlines()]


def test_import_failure_visible_and_cpu_execution_continues(tmp_path, monkeypatch):
    install(monkeypatch, [point('bad_import', error=ImportError('optional backend absent')),
                          point('bad_init', error=RuntimeError('optional initialization failed'))])
    registry = algorithms()
    assert len(registry.plugins) == 66
    assert sum('quality_cpu' in p.spec.presets for p in registry.plugins.values()) == 9
    assert [r['status'] for r in registry.discovery_errors] == ['missing_dependency', 'processing_error']
    with open_dataset(generate(tmp_path/'demo')) as dataset:
        pool = recommend(dataset)
        assert pool['discovery_errors'] == registry.discovery_errors
        summary = run(dataset, algorithm_ids=['Numeric_Check'], output=tmp_path/'out')
    assert summary['status_counts'] == {'completed': 2}
    assert all(r['status'] == 'completed' for r in read_records(tmp_path/'out', 'Numeric_Check'))
    snapshot = json.loads(next((tmp_path/'out/_runs').glob('*/run.json')).read_text())
    assert snapshot['discovery_errors'] == registry.discovery_errors
    page = (tmp_path/'out/report.html').read_text()
    assert '插件发现失败' in page and 'optional backend absent' in page


def test_import_isolation_does_not_swallow_invalid_namespace_or_duplicate(monkeypatch):
    plugin = SimpleNamespace(spec=AlgorithmSpec('bad_name', '1', 'Bad', 'Team', 'Bad ID'), run=lambda c,p: {})
    install(monkeypatch, [point('bad', plugin=plugin)])
    with pytest.raises(ValueError, match='team__algorithm'):
        algorithms()
    plugin.spec = AlgorithmSpec('team__valid', '1', 'Valid', 'Team', 'Method')
    install(monkeypatch, [point('one', plugin=plugin), point('two', plugin=plugin)])
    with pytest.raises(ValueError, match='Duplicate'):
        algorithms()


def test_diagnostics_cli_preserves_default_list(monkeypatch, capsys):
    from edc.execution.cli import main
    install(monkeypatch, [point('optional', error=ImportError('absent'))])
    assert main(['algorithms']) == 0
    captured = capsys.readouterr()
    listed = json.loads(captured.out)
    assert len(listed) == 66 and sum('quality_cpu' in p.get('presets', []) for p in listed) == 9
    assert '--diagnostics' in captured.err
    assert main(['algorithms', '--diagnostics']) == 0
    assert json.loads(capsys.readouterr().out)['discovery_errors'][0]['entry_point'] == 'optional'


def test_repository_owned_indicator_pool_has_no_external_algorithm_dependency(monkeypatch):
    install(monkeypatch, [])
    registry = algorithms()
    indicators = {ident: plugin for ident, plugin in registry.plugins.items()
                  if 'quality_indicators' in plugin.spec.presets}
    assert len(indicators) == 57
    assert {'C1', 'J1', 'M3'} <= indicators.keys()
    assert all(plugin.spec.author == 'EDC contributors' for plugin in indicators.values())
    assert not any(ident.startswith('dayan__') for ident in registry.plugins)


def test_indicator_pool_exposes_structured_input_contract(monkeypatch):
    install(monkeypatch, [])
    registry = algorithms()
    indicators = [plugin for plugin in registry.plugins.values()
                  if 'quality_indicators' in plugin.spec.presets]
    assert len(indicators) == 57
    assert all(plugin.spec.data_requirements or plugin.spec.required_inputs for plugin in indicators)
    for plugin in indicators:
        schema = plugin.spec.parameter_schema
        assert schema['properties']['channels']['type'] == ['array', 'null']
        for requirement in plugin.spec.data_requirements:
            parameter = requirement.get('channel_param')
            if parameter:
                assert parameter in schema['properties']
                kinds = schema['properties'][parameter].get('type', [])
                kinds = kinds if isinstance(kinds, list) else [kinds]
                assert 'array' in kinds
        if plugin.spec.id == 'M8':
            assert plugin.spec.required_inputs == ('dataset_file',)
        if plugin.spec.id == 'M9':
            assert plugin.spec.required_inputs == ('tf_chain',)


def test_array_channel_requirements_expand_and_defer_default_scope():
    from edc.algorithms.contracts import evaluate_requirements
    from edc.data.ir.dataset import Channel
    channels = {
        'left': Channel('left', 'joint_space', ['position'], 3, 'source', unit='rad',
                        time_source='acquisition', timestamps=[0., 1., 2.], source_kind='feedback',
                        calibration_status='confirmed', value_available=True,
                        representation={'quantity': 'joint_angle', 'semantic_status': 'confirmed'}),
        'right': Channel('right', 'joint_space', ['position'], 3, 'source', unit='rad',
                         time_source='acquisition', timestamps=[0., 1., 2.], source_kind='feedback',
                         calibration_status='confirmed', value_available=True,
                         representation={'quantity': 'joint_angle', 'semantic_status': 'confirmed'}),
    }
    spec = AlgorithmSpec('fixture__array', '1', 'array', 'fixture', 'array contract',
        parameter_schema={'type': 'object', 'properties': {
            'channels': {'type': ['array', 'null'], 'default': None, 'items': {'type': 'string'}}}},
        data_requirements=({'id': 'positions', 'channel_param': 'channels',
                            'modality': 'joint_space', 'field': 'position', 'unit': 'rad',
                            'source_kind': 'feedback', 'min_channels': 2},))
    episode = SimpleNamespace(channels=channels)
    rows = evaluate_requirements(spec, episode, {'channels': ['left', 'right']})
    assert [row['id'] for row in rows] == ['positions[0]', 'positions[1]']
    assert all(row['status'] == 'satisfied' for row in rows)
    deferred = evaluate_requirements(spec, episode, {'channels': None})
    assert deferred[0]['status'] == 'deferred'


def dataset_plugin(calls, fail=False):
    from edc.data.ir.units import UnitInputError, require_si_field
    class Plugin:
        spec = AlgorithmSpec('team__dataset', '1', 'Dataset fixture', 'Tests', 'Contract only',
            category='value', granularity='dataset', parameter_schema={'type': 'object',
                'additionalProperties': False, 'properties': {'minimum': {'type': 'integer', 'minimum': 1, 'default': 2}}})

        def preflight_dataset(self, dataset, params):
            episodes = list(dataset.iter_episodes())
            if len(episodes) < params['minimum']:
                return 'missing_input', 'Too few episodes'
            try:
                for ep in episodes:
                    with ep.opened():
                        require_si_field(ep, 'follow_right_joint_pos', 'position', 'rad')
            except UnitInputError as exc:
                return 'missing_input', str(exc)
            return 'runnable', ''

        def run(self, context, params):
            assert context.episode is None
            calls.append(context.dataset.id)
            if fail:
                raise RuntimeError('dataset method failed')
            first = []
            for ep in context.dataset.iter_episodes():
                with ep.opened():
                    first.append(ep.read_signal('follow_right_joint_pos', 'position')[1].tolist())
            return {'distribution': first, 'experiment_values': [1.2, -0.4], 'verdict': 'DO_NOT_USE', 'score': None}
    return Plugin()


def test_dataset_executes_once_with_si_and_raw_results_not_episode_filter(tmp_path, monkeypatch):
    calls = []
    install(monkeypatch, [point('team__dataset', plugin=dataset_plugin(calls))])
    import yaml
    source = generate(tmp_path/'demo')
    p = yaml.safe_load((source/'robot.yaml').read_text())
    p['channels']['follow_right_joint_pos']['unit'] = 'deg'
    (source/'robot.yaml').write_text(yaml.safe_dump(p))
    output = tmp_path/'out'
    with open_dataset(source) as dataset:
        raw = []
        for ep in dataset.iter_episodes():
            with ep.opened():
                raw.append(ep.read_signal('follow_right_joint_pos', 'position')[1])
        summary = run(dataset, algorithm_ids=['team__dataset', 'Numeric_Check'], output=output)
    assert len(calls) == 1 and summary['status_counts'] == {'completed': 3}
    records = read_records(output, 'team__dataset')
    assert len(records) == 1 and records[0]['object']['type'] == 'dataset'
    item = records[0]['result']['reports'][0]
    np.testing.assert_allclose(item['distribution'], np.deg2rad(raw))
    assert not np.allclose(item['distribution'], raw)
    assert item['experiment_values'] == [1.2, -0.4] and item['verdict'] == 'DO_NOT_USE'
    evidence = json.loads(next(output.glob('_runs/*/team__dataset/artifacts/dataset/unit-normalization.json')).read_text())
    assert len(evidence['episodes']) == 2
    assert all(ep['evidence']['fields'] for ep in evidence['episodes'])
    catalog = json.loads((output/'catalog.json').read_text())
    choices = json.loads((output/catalog['runs'][0]['selection']).read_text())
    assert all(row['decision'] == 'unassessed' for row in choices)
    assert '数据集级结果单独报告' in (output/'report.html').read_text()


@pytest.mark.parametrize('mode,expected', [('params','missing_input'), ('preflight','missing_input'), ('run','processing_error')])
def test_dataset_bad_inputs_or_failure_do_not_block_episode_methods(tmp_path, monkeypatch, mode, expected):
    calls = []
    install(monkeypatch, [point('team__dataset', plugin=dataset_plugin(calls, fail=mode == 'run'))])
    params = {'team__dataset': {'minimum': 0 if mode == 'params' else 3}} if mode != 'run' else {}
    with open_dataset(generate(tmp_path/'demo')) as dataset:
        run(dataset, algorithm_ids=['team__dataset','Numeric_Check'], params=params, output=tmp_path/'out')
    assert read_records(tmp_path/'out', 'team__dataset')[0]['status'] == expected
    assert len(read_records(tmp_path/'out', 'Numeric_Check')) == 2
    assert len(calls) == (1 if mode == 'run' else 0)


def test_dataset_requires_explicit_preflight_and_segment_still_rejected():
    for granularity, match in [('dataset', 'preflight_dataset'), ('segment', 'granularity')]:
        plugin = SimpleNamespace(spec=AlgorithmSpec('team__bad', '1', 'Bad', 'Test', 'Contract',
            granularity=granularity), run=lambda c,p: {})
        with pytest.raises(ValueError, match=match):
            Registry('edc.algorithms').register(plugin)


def test_bad_episode_in_dataset_diagnostics_cannot_abort_other_results(tmp_path, monkeypatch):
    plugin = dataset_plugin([])
    plugin.preflight_dataset = lambda dataset, params: ('missing_input', 'bad episode')
    install(monkeypatch, [point('team__dataset', plugin=plugin)])
    source = generate(tmp_path/'demo')
    (source/'episodes/episode_001/trajectory.json').write_text('{')
    with open_dataset(source) as dataset:
        result = run(dataset, algorithm_ids=['team__dataset', 'Numeric_Check'], output=tmp_path/'out')
    assert result['status_counts'] == {'processing_error': 1, 'completed': 1, 'missing_input': 1}
    record = read_records(tmp_path/'out', 'team__dataset')[0]
    artifact = next(ref for ref in record['artifacts'] if ref.endswith('unit-normalization.json'))
    evidence = json.loads((tmp_path/'out'/artifact).read_text())
    assert evidence['episodes'][0]['status'] == 'unavailable'


def test_dataset_cannot_silently_ignore_episode_declarations():
    plugin = dataset_plugin([])
    from dataclasses import replace
    plugin.spec = replace(plugin.spec, required_inputs=('numeric',))
    with pytest.raises(ValueError, match='episode-scoped declarations'):
        Registry('edc.algorithms').register(plugin)


@pytest.mark.parametrize('unit,policy', [('unknown','auto'), ('deg','manual')])
def test_dataset_si_gate_rejects_unknown_or_manual_non_si(tmp_path, monkeypatch, unit, policy):
    import yaml
    calls = []
    install(monkeypatch, [point('team__dataset', plugin=dataset_plugin(calls))])
    source = generate(tmp_path/'demo')
    p = yaml.safe_load((source/'robot.yaml').read_text())
    p['channels']['follow_right_joint_pos']['unit'] = unit
    (source/'robot.yaml').write_text(yaml.safe_dump(p))
    with open_dataset(source) as dataset:
        run(dataset, algorithm_ids=['team__dataset'], output=tmp_path/'out', unit_policy=policy)
    record = read_records(tmp_path/'out', 'team__dataset')[0]
    assert record['status'] == 'missing_input' and not calls
