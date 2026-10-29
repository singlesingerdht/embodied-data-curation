"""Indicator recommendation and execution share the configured input contract."""
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from edc import open_dataset, recommend, run
from edc.algorithms.contracts import bind_parameters, evaluate_requirements
from edc.algorithms.quality.adapters.cpu import preflight
from edc.algorithms.registry import AlgorithmSpec, Registry, algorithms
from edc.data.ir.dataset import Channel


def fixture(tmp_path):
    h5py = pytest.importorskip('h5py')
    path = tmp_path / 'source.h5'
    with h5py.File(path, 'w') as source:
        source['time'] = np.arange(30) * .02
        source['left'] = np.arange(60).reshape(30, 2) * .01
        source['right'] = np.arange(60).reshape(30, 2) * .02
        source['command'] = np.full((30, 2), 90.)
    channels = {}
    for name, side, role in [('left', 'left', 'feedback'), ('right', 'right', 'feedback'),
                             ('command', 'left', 'command')]:
        channels[name] = {
            'logical_name': f'joint/arm/{side}/{role}',
            'modality': 'joint_space', 'fields': ['position'], 'unit': 'deg',
            'side': side, 'source_kind': role, 'calibration_status': 'confirmed',
            'time_source': 'acquisition', 'clock': 'fixture',
            'representation': {'quantity': 'joint_angle', 'semantic_status': 'confirmed'},
        }
    profile = {'schema_version': 1, 'id': 'indicator_fixture', 'version': '1',
               'timestamp_column': 'time', 'timestamp_unit': 's', 'channels': channels}
    return path, profile


def row(pool, ident):
    return next(item for item in pool['episodes'][0]['algorithms'] if item['algorithm']['id'] == ident)


def record(output, ident):
    return json.loads(next(output.glob(f'_runs/*/{ident}/records.jsonl')).read_text())


@pytest.mark.parametrize('ident', ['J1', 'J4', 'J5'])
def test_default_and_explicit_indicator_scope_recommend_and_execute_identically(tmp_path, ident):
    source, profile = fixture(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    inputs = ['joint/arm/left/feedback', 'joint/arm/right/feedback']
    with open_dataset(source, profile=profile) as dataset:
        default = row(recommend(dataset), ident)
        explicit = row(recommend(dataset, params={ident: {'channels': inputs}}), ident)
        assert default['status'] == explicit['status'] == 'runnable'
        assert default['parameters']['status'] == 'valid'
        assert {b['channel'] for b in default['channel_bindings']} == {'left', 'right'}
        assert [b['requested'] for b in explicit['channel_bindings']] == inputs
        assert all(check['status'] == 'satisfied' for check in explicit['requirement_checks'])
        run(dataset, algorithm_ids=[ident], output=tmp_path / 'default')
        run(dataset, algorithm_ids=[ident], params={ident: {'channels': inputs}},
            output=tmp_path / 'explicit')
    default_record, explicit_record = record(tmp_path / 'default', ident), record(tmp_path / 'explicit', ident)
    assert default_record['status'] == explicit_record['status'] == 'completed'
    first, second = [item['result']['reports'][0] for item in (default_record, explicit_record)]
    assert first['raw'] == second['raw']
    assert first['indicators'] == second['indicators']
    assert all(first[key] is None for key in ('score', 'passed', 'verdict'))
    assert not first['assessment_complete']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_command_field_mapping_uses_logical_names_and_actual_si_input(tmp_path, monkeypatch):
    from edc.data.ir.units import SIEpisode
    source, profile = fixture(tmp_path)
    params = {'A5': {'channels': ['joint/arm/left/command'],
                     'field_map': {'joint/arm/left/command': {'joint_q': 'position'}}}}
    reads = []
    original = SIEpisode.read_signal
    def read(self, channel, field):
        values = original(self, channel, field)
        reads.append((channel, field, values.copy()))
        return values
    monkeypatch.setattr(SIEpisode, 'read_signal', read)
    with open_dataset(source, profile=profile) as dataset:
        entry = row(recommend(dataset, params=params), 'A5')
        assert entry['status'] == 'runnable'
        assert entry['parameters']['effective']['field_map'] == {'command': {'joint_q': 'position'}}
        assert all(check['status'] == 'satisfied' for check in entry['requirement_checks'])
        assert {binding['channel'] for binding in entry['channel_bindings']} == {'command'}
        run(dataset, algorithm_ids=['A5'], params=params, output=tmp_path / 'out')
    assert record(tmp_path / 'out', 'A5')['status'] == 'completed'
    assert reads and all(channel == 'command' and field == 'position' for channel, field, _ in reads)
    for _, _, values in reads:
        np.testing.assert_allclose(values, np.pi / 2)


def test_required_channel_completeness_resolves_logical_inputs(tmp_path):
    source, profile = fixture(tmp_path)
    params = {'M3': {'required_channels': ['joint/arm/left/feedback', 'joint/arm/right/feedback']}}
    with open_dataset(source, profile=profile) as dataset:
        entry = row(recommend(dataset, params=params), 'M3')
        assert entry['status'] == 'runnable'
        assert entry['parameters']['effective']['required_channels'] == ['left', 'right']
        run(dataset, algorithm_ids=['M3'], params=params, output=tmp_path / 'out')
    result = record(tmp_path / 'out', 'M3')
    assert result['status'] == 'completed'
    assert result['effective_params']['required_channels'] == ['left', 'right']


def test_custom_array_parameters_bind_sources_and_reject_alias_duplicates():
    channels = {name: Channel(name, 'joint_space', ['position'], 3, 'fixture',
                             logical_name=f'joint/arm/{name}/feedback') for name in ('left', 'right')}
    episode = SimpleNamespace(channels=channels)
    spec = AlgorithmSpec('test__array', '1', 'Array', 'Tests', 'Input binding',
        parameter_schema={'type': 'object', 'properties': {
            'signals': {'type': 'array', 'items': {'type': 'string'}}}},
        data_requirements=({'id': 'signals', 'channel_param': 'signals', 'min_channels': 2},))
    requested = ['joint/arm/left/feedback', 'joint/arm/right/feedback']
    params, bindings = bind_parameters(spec, episode, {'signals': requested})
    assert params['signals'] == ['left', 'right']
    assert [b['requested'] for b in bindings] == requested
    assert [b['channel'] for b in bindings] == ['left', 'right']
    assert all(b['source'] == 'fixture' for b in bindings)
    with pytest.raises(ValueError, match='Duplicate'):
        bind_parameters(spec, episode, {'signals': ['left', 'joint/arm/left/feedback']})


@pytest.mark.parametrize('kind', ['scalar', 'array'])
def test_omitted_inputs_cannot_bypass_contract_without_default_scope_preflight(kind):
    parameter = {'type': ['string', 'null'], 'default': None} if kind == 'scalar' else {
        'type': ['array', 'null'], 'default': None, 'items': {'type': 'string'}}
    spec = AlgorithmSpec('test__missing', '1', 'Missing', 'Tests', 'Input gate',
        parameter_schema={'type': 'object', 'properties': {'signals': parameter}},
        data_requirements=({'id': 'signals', 'channel_param': 'signals'},))
    plugin = SimpleNamespace(spec=spec, run=lambda c, p: {})
    Registry('edc.algorithms').register(plugin)
    assert preflight(plugin, SimpleNamespace(channels={}), {})[0] == 'missing_input'


def test_sync_contract_requires_independent_acquisition_clock():
    plugin = algorithms().plugins['M1']
    channels = {name: Channel(name, 'rgb', [], 3, 'fixture', image_available=True,
                             timestamps=[0., 1., 2.], time_source='acquisition', clock='fixture')
                for name in ('head', 'wrist')}
    episode = SimpleNamespace(channels=channels, profile={})
    params = {'channels': ['head', 'wrist']}
    assert preflight(plugin, episode, params)[0] == 'runnable'
    channels['wrist'].clock = 'other'
    assert preflight(plugin, episode, params)[0] == 'missing_input'
    channels['wrist'].clock = 'fixture'
    channels['wrist'].time_source = 'container'
    checks = evaluate_requirements(plugin.spec, episode, params)
    assert checks[1]['status'] == 'missing_input'
    assert preflight(plugin, episode, params)[0] == 'missing_input'


def test_minimum_channel_count_cannot_be_bypassed_with_scalar():
    spec = AlgorithmSpec('test__two', '1', 'Two', 'Tests', 'Cardinality',
        parameter_schema={'type': 'object', 'properties': {
            'signals': {'type': ['string', 'array'], 'items': {'type': 'string'}}}},
        data_requirements=({'id': 'signals', 'channel_param': 'signals', 'min_channels': 2},))
    episode = SimpleNamespace(channels={'one': Channel('one', 'other', [], 3, 'fixture')})
    assert evaluate_requirements(spec, episode, {'signals': 'one'})[0]['status'] == 'missing_input'


@pytest.mark.parametrize('code,names', [
    ('C1', ['head']), ('C4', ['head']), ('C5', ['head']), ('C9', ['head']), ('C10', ['head']),
    ('I5', ['imu']), ('I6', ['imu']), ('I7', ['imu']), ('P5', ['pose']), ('P7', ['pose']),
    ('M1', ['head', 'pose']), ('M2', ['head', 'pose']), ('M6', ['head', 'pose']),
    ('M10', ['head', 'pose']), ('M8', None),
])
def test_indicator_families_execute_after_recommendation(tmp_path, code, names):
    from edc.algorithms.recommendations import recommend_episode
    from edc.execution.runner import Context
    if code.startswith('C'):
        pytest.importorskip('cv2')
        pytest.importorskip('xxhash')
    pytest.importorskip('scipy')
    source = tmp_path / 'source.bin'
    source.write_bytes(b'synthetic contract fixture')
    count = 32
    channels = {}
    values = {}
    for name, modality, fields in [('head', 'rgb', []), ('imu', 'imu', ['ang_vel', 'lin_acc']),
                                    ('pose', 'pose', ['position'])]:
        metadata = {field: {'unit': {'ang_vel': 'rad/s', 'lin_acc': 'm/s^2', 'position': 'm'}[field]}
                    for field in fields}
        channels[name] = Channel(name, modality, fields, count, str(source),
            time_source='acquisition', timestamps=[i * .02 for i in range(count)], clock='fixture',
            source_kind='feedback', calibration_status='confirmed',
            representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'},
            field_metadata=metadata, value_available=bool(fields), image_available=not fields)
        for field in fields:
            values[name, field] = np.zeros((count, 3))
    frames = np.random.default_rng(42).integers(0, 256, (count, 32, 32, 3), dtype=np.uint8)
    episode = SimpleNamespace(id='fixture', path=source, format='mcap', profile={}, selector={},
        channels=channels, unit_policy='auto', read_signal=lambda name, field: values[name, field],
        iter_frames=lambda name: ({'index': i, 'image': frames[i]} for i in range(count)))
    plugin = algorithms().plugins[code]
    params = {} if names is None else {'channels': names}
    entry = recommend_episode(episode, {code: plugin}, {code: params})['algorithms'][0]
    assert entry['status'] == 'runnable', entry['reason']
    output = plugin.run(Context(None, episode, tmp_path), entry['parameters']['effective'])
    assert output['indicator_code'] == code
    assert output['score'] is None and output['passed'] is None and not output['assessment_complete']
    assert (tmp_path / 'source-mapping.json').is_file()
    assert (tmp_path / 'indicator-raw.json').is_file()


def test_missing_runtime_dependency_is_reported_before_execution(monkeypatch):
    from importlib import util
    original = util.find_spec
    monkeypatch.setattr(util, 'find_spec', lambda name: None if name == 'scipy' else original(name))
    channel = Channel('pose', 'pose', ['position'], 3, 'fixture', unit='m', value_available=True,
        source_kind='feedback', calibration_status='confirmed', time_source='acquisition',
        timestamps=[0., 1., 2.],
        representation={'quantity': 'position', 'semantic_status': 'confirmed'})
    episode = SimpleNamespace(channels={'pose': channel}, profile={})
    status, reason = preflight(algorithms().plugins['P5'], episode, {'channels': ['pose']})
    assert status == 'missing_dependency' and 'scipy' in reason


@pytest.mark.parametrize('params', [
    {'channels': ['command']},
    {'channels': ['left'], 'field_map': {'left': ['position']}},
    {'channels': ['left'], 'field_map': {'left': {'position': 'missing'}}},
])
def test_invalid_role_or_field_mapping_is_missing_input(tmp_path, params):
    source, profile = fixture(tmp_path)
    with open_dataset(source, profile=profile) as dataset:
        result = row(recommend(dataset, params={'J5': params}), 'J5')
    assert result['status'] == 'missing_input'


def test_file_integrity_does_not_require_an_unrelated_sensor_channel(tmp_path):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'file-only contract fixture')
    episode = SimpleNamespace(channels={}, path=source, profile={})
    assert preflight(algorithms().plugins['M8'], episode)[0] == 'runnable'


def test_completeness_required_channels_cannot_conflict_with_selected_scope(tmp_path):
    source, profile = fixture(tmp_path)
    params = {'M3': {'channels': ['left'], 'required_channels': ['left', 'right']}}
    with open_dataset(source, profile=profile) as dataset:
        result = row(recommend(dataset, params=params), 'M3')
    assert result['status'] == 'missing_input'
    assert 'included in selected channels' in result['reason']
