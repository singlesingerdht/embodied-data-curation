"""Regression tests for placement rules, frame selection and native evidence."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

from edc.algorithms.value.algorithms.Initial_Placement_Assessment import algorithm, method
from edc.execution.runner import Context


@pytest.fixture
def case(tmp_path):
    channel = SimpleNamespace(source_mapping={'source': 'camera'}, time_source='unknown', clock=None)
    episode = SimpleNamespace(id='demo', path=tmp_path / 'input.h5', selector={'group': 'demo'},
                              channels={'camera': channel}, metadata={'task_requirements': 'cup on the left'})
    def frames(name):
        assert name == 'camera'
        yield {'index': 0, 'source_index': 15, 'image': np.zeros((4, 6, 3), dtype=np.uint8)}
        pytest.fail('Placement should read only the first frame')
    episode.iter_frames = frames
    ctx = Context(SimpleNamespace(id='dataset'), episode, tmp_path)
    params = dict(channel='camera', base_url='http://127.0.0.1:8000/v1', model='test-model',
                  task_requirements='shared rule', max_tokens=4096, image_max_side=2048, timeout_seconds=60)
    return ctx, params


def reply(requirement='cup on the left', kind='image_position', status='satisfied'):
    return {'checks': [{'requirement': requirement, 'requirement_type': kind, 'status': status,
                        'observation': '可见杯子', 'reason': '杯子在左侧'}], 'requirements_complete': True,
            'unassessed_requirements': [], 'limitations': []}


def use_model(monkeypatch, payload):
    transport = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'http://localhost/'),
                     json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload)}}]}))
    monkeypatch.setattr(httpx.Client, 'post', transport)
    return transport


@pytest.mark.parametrize('kind,status,verdict,complete', [
    ('image_position', 'satisfied', 'correct', True),
    ('image_position', 'violated', 'incorrect', True),
    ('image_position', 'undetermined', 'undetermined', False),
    ('physical_distance', 'satisfied', 'undetermined', False),
    ('three_dimensional', 'violated', 'undetermined', False)])
def test_run_native_and_mapping(case, monkeypatch, kind, status, verdict, complete):
    context, params = case
    transport = use_model(monkeypatch, reply(kind=kind, status=status))
    result = algorithm.plugin.run(context, params)
    assert (result['placement_status'], result['assessment_complete']) == (verdict, complete)
    assert result['request']['record_id'] == 'demo' and 'score' not in result
    assert json.loads((context.workdir / 'input-mapping.json').read_text())['frame']['source_index'] == 15
    assert json.loads((context.workdir / 'placement_report.json').read_text()) == result
    sent = transport.call_args.kwargs['json']
    assert sent['temperature'] == 0 and sent['response_format'] == {'type': 'json_object'}
    prompt = sent['messages'][1]['content']
    assert json.loads(prompt[0]['text'])['task_requirements'] == 'cup on the left'
    assert prompt[1]['image_url']['url'].startswith('data:image/png;base64,')


def test_uncovered_requirement_preserves_native_violation():
    data = reply(status='violated')
    data['unassessed_requirements'] = ['second rule']
    report = method.PlacementReport(**data)
    assert method.decide(report) == ('incorrect', False)
    assert report.requirements_complete is False
    data.update(checks=[], unassessed_requirements=[])
    assert method.decide(method.PlacementReport(**data)) == ('undetermined', None)


@pytest.mark.parametrize('data,exception', [(reply('invented rule'), ValueError), ({'status': 'correct'}, ValidationError)])
def test_bad_response_retains_input_and_response_artifacts(case, monkeypatch, data, exception):
    context, params = case
    use_model(monkeypatch, data)
    with pytest.raises(exception):
        algorithm.plugin.run(context, params)
    for name in ('initial_frame.png', 'provenance.json', 'input-mapping.json', 'provider-response.json'):
        assert (context.workdir / name).is_file()
    assert not (context.workdir / 'placement_report.json').exists()


def test_preflight_requires_rules_and_explicit_external_service(case):
    context, params = case
    context.episode.metadata['task_requirements'] = ''
    assert algorithm.plugin.preflight(context.episode, params)[0] == 'missing_input'
    context.episode.metadata.clear()
    assert algorithm.plugin.preflight(context.episode, params)[0] == 'runnable'
    params['base_url'] = 'https://example.com/v1'
    assert algorithm.plugin.preflight(context.episode, params)[0] == 'missing_input'
    params['allow_remote_upload'] = True
    assert algorithm.plugin.preflight(context.episode, params)[0] == 'runnable'


def test_edc_run_uses_cloud_default_from_example(tmp_path, monkeypatch):
    from pathlib import Path

    import h5py
    from edc.execution.cli import main

    source = tmp_path / 'sample.h5'
    with h5py.File(source, 'w') as dataset:
        dataset.create_dataset('faceImg', data=np.zeros((1, 4, 6, 3), dtype=np.uint8))
    output = tmp_path / 'results'
    card = Path(__file__).resolve().parents[3] / 'configs/robots/placement_example.yaml'
    rule = 'The pencil case starts on the left side of the visible work surface.'
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'example-test-key')
    transport = use_model(monkeypatch, reply(rule))
    assert main(['run', str(source), '--robot', str(card), '--algorithm',
                 'Initial_Placement_Assessment', '--output', str(output)]) == 0
    transport.assert_called_once()
    args, kwargs = transport.call_args
    assert args == ('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',)
    assert kwargs['headers']['Authorization'] == 'Bearer example-test-key'
    assert kwargs['json']['model'] == 'qwen3.8-max'
    assert kwargs['json']['enable_thinking'] is False
    record = json.loads(next(output.glob('_runs/*/Initial_Placement_Assessment/records.jsonl')).read_text())
    assert record['status'] == 'completed'
    assert record['result']['reports'][0]['model'] == 'qwen3.8-max'
