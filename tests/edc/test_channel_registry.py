"""Declarative vocabulary, exact bindings and auditable synthetic execution."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from edc import (load_channel_registry, load_config, export_config, open_config_dataset,
                 channel_catalog, resolve_channel, run)
from edc.data.ir.dataset import Channel
from edc.data.ir.modalities import MODALITIES
from edc.robots.profiles import load_profile

ROOT = Path(__file__).resolve().parents[2]
CUSTOM = 'lab__tactile/palm'


def extension():
    return {'schema_version': 'edc_channel_registry_v1', 'version': 'lab-1',
            'channels': {CUSTOM: {'modality': 'tactile', 'side': 'left'}}}


def profile():
    return {'schema_version': 1, 'id': 'registry_test', 'version': '1',
            'channel_registry': extension(), 'channels': {'raw': {
                'logical_name': CUSTOM, 'modality': 'tactile', 'side': 'left', 'fields': ['taxels']}}}


def test_bundled_catalog_covers_modalities_and_returns_independent_values():
    registry = load_channel_registry()
    assert len(registry['channels']) == 107
    assert {spec['modality'] for spec in registry['channels'].values()} == set(MODALITIES)
    registry['channels']['imu/main']['modality'] = 'pose'
    assert load_channel_registry()['channels']['imu/main'] == {'modality': 'imu'}


def test_all_legacy_names_keep_exact_semantics():
    from edc.algorithms.channel_names import STANDARD_CHANNELS
    expected = {
        **{f'{kind}/arm/{side}/{role}': {'modality': modality, 'side': side, 'source_kind': role}
           for kind, modality in [('joint', 'joint_space'), ('pose', 'pose')]
           for side in ('left', 'right', 'main') for role in ('feedback', 'command')},
        **{f'joint/gripper/{side}/{role}': {'modality': 'joint_space', 'side': side,
           'device_type': 'gripper', **({'source_kind': role} if role != 'state' else {})}
           for side in ('left', 'right', 'main') for role in ('state', 'feedback', 'command')},
        **{f'imu/{location}': {'modality': 'imu'} for location in ('main', 'base', 'head', 'chest')},
        **{f'imu/wrist/{side}': {'modality': 'imu', 'side': side} for side in ('left', 'right', 'main')},
        'rgb/head': {'modality': 'rgb', 'camera_role': 'head'},
        'rgb/chest': {'modality': 'rgb', 'camera_role': 'chest'},
        **{f'rgb/wrist/{side}': {'modality': 'rgb', 'camera_role': 'wrist',
           **({'side': side} if side != 'main' else {})} for side in ('left', 'right', 'main')},
        'depth/head': {'modality': 'depth', 'camera_role': 'head'},
        'depth/chest': {'modality': 'depth', 'camera_role': 'chest'},
        'joint/head/state': {'modality': 'joint_space', 'body_part': 'head'},
        'joint/lift/state': {'modality': 'joint_space', 'body_part': 'lift'},
        'odometry/base': {'modality': 'odometry'},
    }
    assert len(expected) == 38
    assert all(STANDARD_CHANNELS[name] == spec for name, spec in expected.items())


def test_extension_does_not_mutate_inputs_or_leak_between_profiles():
    p = profile(); original = copy.deepcopy(p)
    loaded = load_profile(p)
    assert p == original
    assert loaded['channels'] == p['channels']
    assert CUSTOM not in load_channel_registry()['channels']
    for field in ('unit', 'clock', 'calibration_status', 'source_kind'):
        assert field not in loaded['channels']['raw']
    # Old opaque team__ names remain valid without registering semantics.
    del p['channel_registry']
    p['channels']['raw']['side'] = 'right'
    assert load_profile(p) == p


@pytest.mark.parametrize('change,match', [
    ({'schema_version': 'edc_channel_registry_v2'}, 'requires'),
    ({'version': 1}, 'version'),
    ({'base_version': 'old'}, 'base_version'),
    ({'channels': {}}, 'nonempty'),
    ({'channels': {'rgb/banana': {'modality': 'rgb'}}}, 'team__'),
    ({'channels': {'imu/main': {'modality': 'pose'}}}, 'redefine'),
    ({'channels': {CUSTOM+'@backup': {'modality': 'tactile'}}}, 'base name'),
    ({'channels': {CUSTOM: {'modality': 'typo'}}}, 'modality'),
    ({'channels': {CUSTOM: {'modality': 'image'}}}, 'canonical'),
    ({'channels': {CUSTOM: {'modality': 'tactile', 'unit': 'N'}}}, 'semantics'),
    ({'channels': {CUSTOM: {'modality': 'tactile', 'side': 'unknown'}}}, 'side'),
    ({'channels': {CUSTOM: {'modality': 'tactile', 'source_kind': 'state'}}}, 'source_kind'),
])
def test_invalid_registry_contracts_fail(change, match):
    with pytest.raises(ValueError, match=match):
        load_channel_registry({**extension(), **change})


@pytest.mark.parametrize('as_json', [False, True])
def test_duplicate_keys_rejected_in_registry_files(tmp_path, as_json):
    path = tmp_path / 'registry.yaml'
    text = ('{"schema_version":"edc_channel_registry_v1","version":"1","channels":'
            '{"lab__tactile/palm":{"modality":"tactile"},"lab__tactile/palm":{"modality":"imu"}}}'
            if as_json else yaml.safe_dump(extension()) + 'channels: {}' + chr(10))
    path.write_text(text)
    with pytest.raises(ValueError, match='Duplicate'):
        load_channel_registry(path)


def test_references_resolve_relative_and_cycles_fail(tmp_path, monkeypatch):
    folder = tmp_path / 'config'; folder.mkdir()
    (folder / 'registry.yaml').write_text(yaml.safe_dump(extension()))
    p = profile(); p['channel_registry'] = {'ref': 'registry.yaml'}
    path = folder / 'robot.yaml'; path.write_text(yaml.safe_dump(p))
    monkeypatch.chdir(tmp_path)
    assert load_profile(path)['channel_registry']['channels'][CUSTOM]['side'] == 'left'
    (folder / 'registry.yaml').write_text('ref: second.yaml')
    (folder / 'second.yaml').write_text('ref: registry.yaml')
    with pytest.raises(ValueError, match='Cyclic'):
        load_profile(path)


def test_custom_semantics_checked_in_profile_and_runtime():
    p = profile(); p['channels']['raw']['side'] = 'right'
    with pytest.raises(ValueError, match='side'):
        load_profile(p)
    p = load_profile(profile())
    channel = Channel('raw', 'tactile', ['taxels'], 1, 'fixture', side='right', logical_name=CUSTOM)
    ep = SimpleNamespace(profile=p, channels={'raw': channel})
    with pytest.raises(ValueError, match='side'):
        resolve_channel(ep, CUSTOM)
    channel.side = 'left'; channel.logical_name = CUSTOM+'@backup'
    assert resolve_channel(ep, CUSTOM+'@backup') == 'raw'
    with pytest.raises(ValueError, match='no fallback'):
        resolve_channel(ep, CUSTOM)


def test_non_position_fields_cannot_reverse_command_feedback():
    p = {'schema_version': 1, 'id': 'wrench', 'version': '1', 'channels': {'raw': {
        'logical_name': 'wrench/arm/left/feedback', 'modality': 'wrench', 'fields': ['force'],
        'field_metadata': {'force': {'source_kind': 'command'}}}}}
    with pytest.raises(ValueError, match='source_kind'):
        load_profile(p)


def test_example_declarations_remain_pending_and_unbound():
    from edc.configuration import execution_profile
    robot = load_profile(ROOT / 'configs/robots/channel_registry_example.yaml')
    dataset = yaml.safe_load((ROOT / 'configs/datasets/channel_registry_pending.yaml').read_text())
    assert len(robot['channel_registry']['channels']) == 108
    with pytest.raises(ValueError, match='Unbound source channel'):
        execution_profile({'robot': robot, 'dataset': dataset})


def configured_fixture(tmp_path):
    h5py = pytest.importorskip('h5py')
    source = tmp_path / 'source.h5'
    with h5py.File(source, 'w') as f:
        f['time'] = np.arange(30, dtype=np.float64) / 100
        f['raw'] = np.arange(90, dtype=np.float64).reshape(30, 3)
    (tmp_path / 'registry.yaml').write_text(yaml.safe_dump(extension()))
    p = profile(); p['channel_registry'] = {'ref': 'registry.yaml'}
    p.update(timestamp_column='time', timestamp_unit='s')
    p['channels']['raw'].update(time_source='acquisition', clock=None)
    (tmp_path / 'robot.yaml').write_text(yaml.safe_dump(p))
    cfg = {'schema_version': 'edc_config_v1', 'dataset': {'path': 'source.h5',
        'source_bindings': {CUSTOM: 'raw'}}, 'robot': {'ref': 'robot.yaml'},
        'algorithms': {'methods': {'Dedup_Check': {'params': {'channels': [CUSTOM]}}}},
        'workflow': {'stages': [{'id': 'quality', 'kind': 'quality', 'algorithms': ['Dedup_Check']}]}}
    path = tmp_path / 'config.yaml'; path.write_text(yaml.safe_dump(cfg))
    return path, source


def test_config_export_freezes_registry_independent_of_original_files(tmp_path):
    path, _ = configured_fixture(tmp_path)
    frozen = export_config(path, tmp_path / 'export.yaml')
    before = load_config(path)
    (tmp_path / 'registry.yaml').unlink(); (tmp_path / 'robot.yaml').unlink()
    assert load_config(frozen) == before
    assert 'ref' not in load_config(frozen)['robot']['channel_registry']


def test_exact_binding_values_times_and_run_evidence(tmp_path):
    path, source = configured_fixture(tmp_path)
    before = source.read_bytes(); cfg = load_config(path)
    output = tmp_path / 'results'
    with open_config_dataset(cfg) as ds:
        ep = next(ds.iter_episodes()); catalog = channel_catalog(ep)
        assert resolve_channel(ep, CUSTOM) == 'raw'
        np.testing.assert_array_equal(ep.read_signal('raw', 'taxels'), np.arange(90).reshape(30, 3))
        c = ep.channels['raw']
        assert c.time_source == 'acquisition' and c.clock is None
        assert c.unit is None and c.calibration_status == 'unknown'
        assert catalog['binding_coverage']['logical_channel_count'] == 1
        assert catalog['binding_coverage']['missing_declared_logical_names'] == []
        assert catalog['channel_registry'] == cfg['robot']['channel_registry']
        run(ds, algorithm_ids=['Dedup_Check'], output=output, params={'Dedup_Check': {'channels': [CUSTOM]}})
    run_dir = next((output / '_runs').iterdir())
    assert json.loads((run_dir / 'run.json').read_text())['channel_registry'] == cfg['robot']['channel_registry']
    record = json.loads((run_dir / 'Dedup_Check/records.jsonl').read_text())
    assert record['status'] == 'completed'
    assert record['channel_bindings'][0]['requested'] == CUSTOM
    assert record['channel_bindings'][0]['channel'] == 'raw'
    assert (output / 'catalog.json').exists()
    assert source.read_bytes() == before


def test_catalog_reports_missing_declarations_without_fabricating_channels(tmp_path):
    path, _ = configured_fixture(tmp_path); cfg = load_config(path)
    cfg['robot']['channels']['missing'] = {'logical_name': 'imu/main', 'modality': 'imu'}
    with open_config_dataset(cfg) as ds:
        ep = next(ds.iter_episodes()); report = channel_catalog(ep)
        assert report['binding_coverage']['missing_declared_logical_names'] == ['imu/main']
        assert 'missing' not in ep.channels
        with pytest.raises(ValueError, match='no fallback'):
            resolve_channel(ep, 'imu/main')


def test_cli_lists_vocabulary_and_actual_bindings(tmp_path, capsys):
    from edc.execution.cli import main
    assert main(['channels']) == 0
    assert len(json.loads(capsys.readouterr().out)['channels']) == 107
    path, source = configured_fixture(tmp_path)
    assert main(['channels', '--registry', str(tmp_path / 'registry.yaml')]) == 0
    assert len(json.loads(capsys.readouterr().out)['channels']) == 108
    assert main(['channels', str(source), '--profile', str(tmp_path / 'robot.yaml')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['episodes'][0]['binding_coverage']['logical_channel_count'] == 1


def test_duplicate_inline_registry_keys_fail_during_config_load(tmp_path):
    path, _ = configured_fixture(tmp_path)
    robot = tmp_path / 'robot.yaml'
    robot.write_text(robot.read_text() + 'channel_registry: {}' + chr(10))
    with pytest.raises(ValueError, match='Duplicate'):
        load_config(path)
