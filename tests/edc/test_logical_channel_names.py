"""Logical names share exact reader inputs and never rewrite physical semantics."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from edc import open_dataset, channel_catalog, resolve_channel, recommend, run
from edc.algorithms.channel_names import resolve_channel_parameters
from edc.data.ir.dataset import Channel
from edc.robots.profiles import load_profile
from edc.algorithms.quality.adapters.cpu import builtins, preflight


def fixture(tmp_path):
    import h5py
    path = tmp_path/'source.h5'
    with h5py.File(path, 'w') as f:
        f['time'] = np.arange(30, dtype=np.float64)*.01
        f['raw/pose'] = np.arange(90, dtype=np.float64).reshape(30, 3)/100
    profile = {'schema_version': 1, 'id': 'logical', 'version': '1',
        'timestamp_column': 'time', 'timestamp_unit': 's', 'channels': {'raw/pose': {
            'logical_name': 'pose/arm/right/feedback', 'modality': 'pose',
            'fields': ['position'], 'unit': 'm', 'side': 'right', 'source_kind': 'feedback',
            'time_source': 'acquisition', 'clock': 'test', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    return path, profile


def test_public_catalog_retains_names_metadata_and_source(tmp_path):
    path, profile = fixture(tmp_path); original = copy.deepcopy(profile)
    with open_dataset(path, profile=profile) as ds:
        ep = next(ds.iter_episodes()); catalog = channel_catalog(ep)
        row = next(c for c in catalog['channels'] if c['logical_name'])
        assert row['logical_name'] == 'pose/arm/right/feedback'
        assert row['channel'] == 'raw/pose' and row['unit'] == 'm'
        assert row['image_storage'] is None
        assert resolve_channel(ep, row['logical_name']) == 'raw/pose'
        assert resolve_channel(ep, 'raw/pose') == 'raw/pose'
        assert 'pose/arm/right/feedback' not in ep.channels
        assert ep.read_signal('raw/pose', 'position').dtype == np.float64
    assert profile == original




@pytest.mark.parametrize('ident', [p.spec.id for p in builtins()])
def test_every_cpu_preflight_resolves_same_input(tmp_path, ident):
    path, profile = fixture(tmp_path)
    plugin = next(p for p in builtins() if p.spec.id == ident)
    with open_dataset(path, profile=profile) as ds:
        ep = next(ds.iter_episodes()); ep.open()
        assert preflight(plugin, ep, {'channels': ['pose/arm/right/feedback']}) == preflight(plugin, ep, {'channels': ['raw/pose']})
        status, _ = preflight(plugin, ep, {'channels': ['rgb/wrist/right']})
        assert status == 'missing_input'


@pytest.mark.parametrize('ident', ['Numeric_Check', 'Action_Smoothness', 'Dedup_Check'])
def test_public_execution_alias_equals_exact_input_and_keeps_evidence(tmp_path, ident):
    path, profile = fixture(tmp_path); before = path.read_bytes()
    key = 'channels' if ident == 'Numeric_Check' else 'channel'
    records = []
    for label, name in [('source', 'raw/pose'), ('logical', 'pose/arm/right/feedback')]:
        params = {key: [name] if key == 'channels' else name}
        output = tmp_path/label
        with open_dataset(path, profile=profile) as ds:
            pool = recommend(ds, params={ident: params})
            row = next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id'] == ident)
            assert row['channel_bindings'][0]['channel'] == 'raw/pose'
            run(ds, algorithm_ids=[ident], output=output, params={ident: params})
        record = json.loads(next(output.glob(f'_runs/*/{ident}/records.jsonl')).read_text())
        assert record['status'] == 'completed'
        assert record['channel_bindings'][0]['requested'] == name
        records.append(record)
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k != 'process_time_ms'}
        return [stable(v) for v in value] if isinstance(value, list) else value
    assert stable(records[0]['result']) == stable(records[1]['result'])
    assert path.read_bytes() == before


def test_duplicate_source_and_alias_cannot_inspect_twice(tmp_path):
    path, profile = fixture(tmp_path)
    with open_dataset(path, profile=profile) as ds:
        ep = next(ds.iter_episodes()); ep.open()
        with pytest.raises(ValueError, match='Duplicate'):
            resolve_channel_parameters(ep, {'channels': ['raw/pose', 'pose/arm/right/feedback']})


@pytest.mark.parametrize('name, change', [
    ('rgb/wrist/left', {'side': 'right'}), ('rgb/chest', {'modality': 'depth'}),
    ('rgb/chest', {'representation': {'camera_role': 'wrist'}}),
    ('pose/arm/right/feedback', {'source_kind': 'command'}),
    ('invalid', {}), ('rgb/banana', {}),
])
def test_invalid_or_conflicting_declarations_fail(name, change):
    p = {'schema_version': 1, 'id': 'conflict', 'version': '1',
         'channels': {'raw': {'logical_name': name, **change}}}
    with pytest.raises(ValueError): load_profile(p)


def test_duplicate_wildcard_and_source_shadow_fail(tmp_path):
    path, p = fixture(tmp_path)
    p['channels']['other'] = copy.deepcopy(p['channels']['raw/pose'])
    with pytest.raises(ValueError, match='Duplicate'): load_profile(p)
    p['channels'] = {'raw/*': p['channels']['raw/pose']}
    with pytest.raises(ValueError, match='wildcard'): load_profile(p)
    channel = Channel('raw', 'rgb', [], 1, 'test', logical_name='rgb/chest')
    ep = SimpleNamespace(channels={'raw': channel, 'rgb/chest': Channel('rgb/chest', 'rgb', [], 1, 'test')})
    with pytest.raises(ValueError, match='shadows'): resolve_channel(ep, 'raw')


def test_multiple_cameras_require_instance_and_no_missing_right_fallback():
    a = Channel('front', 'rgb', [], 1, 'test', logical_name='rgb/chest@front')
    b = Channel('rear', 'rgb', [], 1, 'test', logical_name='rgb/chest@rear')
    ep = SimpleNamespace(channels={'front': a, 'rear': b})
    assert resolve_channel(ep, 'rgb/chest@rear') == 'rear'
    for name in ['rgb/chest', 'rgb/wrist/right']:
        with pytest.raises(ValueError, match='no fallback'): resolve_channel(ep, name)


def test_reader_metadata_cannot_disagree_with_alias():
    c = Channel('raw', 'depth', [], 1, 'source', logical_name='rgb/head')
    with pytest.raises(ValueError, match='modality'):
        resolve_channel(SimpleNamespace(channels={'raw': c}), 'rgb/head')


def test_field_level_command_cannot_be_called_feedback():
    p = {'schema_version': 1, 'id': 'conflict', 'version': '1',
        'channels': {'raw': {'logical_name': 'pose/arm/right/feedback', 'modality': 'pose',
            'fields': ['position'], 'source_kind': 'feedback',
            'field_metadata': {'position': {'source_kind': 'command'}}}}}
    with pytest.raises(ValueError, match='source_kind'): load_profile(p)


@pytest.mark.parametrize('side', ['left', 'right'])
def test_registered_gripper_type_is_checked_without_inferring_role(side):
    spec = {'logical_name': f'joint/gripper/{side}/state', 'modality': 'joint_space',
            'fields': ['position'], 'side': side, 'source_kind': 'unknown',
            'unit': None, 'representation': {'device_type': 'gripper'}}
    p = {'schema_version': 1, 'id': 'registered', 'version': '1', 'channels': {'raw': spec}}
    loaded = load_profile(p)['channels']['raw']
    assert loaded['source_kind'] == 'unknown' and loaded['unit'] is None
    c = Channel('raw', 'joint_space', ['position'], 1, 'test',
                side=side, logical_name=spec['logical_name'], representation=spec['representation'])
    assert resolve_channel(SimpleNamespace(channels={'raw': c}), spec['logical_name']) == 'raw'
    spec['representation']['device_type'] = 'dexterous_hand'
    with pytest.raises(ValueError, match='device_type'):
        load_profile(p)
    c.representation = {'device_type': 'dexterous_hand'}
    with pytest.raises(ValueError, match='device_type'):
        resolve_channel(SimpleNamespace(channels={'raw': c}), spec['logical_name'])


@pytest.mark.parametrize('logical, change', [
    ('joint/gripper/left/command', {'source_kind': 'feedback'}),
    ('joint/gripper/right/state', {'side': 'left'}),
    ('joint/head/state', {'body_part': 'lift'}),
    ('joint/lift/state', {'body_part': 'head'}),
    ('odometry/base', {'modality': 'pose'}),
])
def test_new_registered_channel_categories_reject_conflicts(logical, change):
    p = {'schema_version': 1, 'id': 'conflict', 'version': '1',
         'channels': {'raw': {'logical_name': logical, **change}}}
    with pytest.raises(ValueError, match='conflicts'):
        load_profile(p)


def test_x1pro_confirmations_keep_units_clocks_and_metadata_unknown():
    root = Path(__file__).resolve().parents[2]
    fixture = root/'docs/framework/evidence/x1pro_topic_registration_20261021/robot.yaml'
    if not fixture.exists():
        pytest.skip('Real robot registration evidence remains in the research repository')
    p = load_profile(fixture)
    channels = p['channels']
    assert len(channels) == 32
    assert sum(bool(c.get('logical_name')) for c in channels.values()) == 23
    for side in ('left', 'right'):
        arm = channels[f'/{side}_arm/joint_states']
        assert arm['source_kind'] == 'feedback' and arm['side'] == side
        for part in ('arm', 'gripper'):
            command = channels[f'/master_{side}_{part}/joint_states']
            assert command['source_kind'] == 'command'
            assert command['logical_name'] == f'joint/{part}/{side}/command'
        gripper = channels[f'/{side}_gripper/joint_states']
        assert gripper['representation']['device_type'] == 'gripper'
        assert gripper['source_kind'] == 'unknown'
    for part, quantity in [('head', 'angle'), ('lift', 'length')]:
        c = channels[f'/{part}/joint_states']
        assert c['field_metadata']['position']['quantity'] == quantity
        assert c['field_metadata']['position']['semantic_status'] == 'confirmed'
        assert c['unit'] is None and c['field_metadata']['position']['unit'] is None
        assert 'velocity' not in c['field_metadata'] and 'effort' not in c['field_metadata']
    depth = channels['/camera_head_front/depth/image_raw/compressedDepth']
    assert depth['logical_name'] == 'depth/head' and depth['sensor_id'] == 'head_front'
    assert all(depth['representation'][k] is None for k in
               ('depth_scale', 'depth_unit', 'invalid_value_rule'))
    assert p['camera_requirements']['head_front'] == ['rgb']
    assert channels['/odom']['modality'] == 'odometry' and channels['/odom']['fields'] == []
    for name in ('instruction_data', 'take_over_data', 'calibration_data', 'distribute_data', 'diversity_data'):
        c = channels[f'/{name}']
        assert c['modality'] == 'other' and c['source_kind'] == 'unknown'
        assert c['fields'] == [] and not c.get('logical_name')
    assert all(c['clock'] is None and c['calibration_status'] == 'unknown' for c in channels.values())


def test_name_mapping_preserves_existing_numeric_author_confirmation():
    from edc.robots.numeric_review import confirm_numeric_profile, numeric_review
    p = {'schema_version': 1, 'id': 'reviewed', 'version': '1', 'channels': {'joint': {
        'modality': 'joint_space', 'fields': ['position'], 'source_kind': 'feedback',
        'unit': 'rad', 'side': 'left', 'calibration_status': 'confirmed', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    confirmed = confirm_numeric_profile(p, 'fixture author')
    before = confirmed['numeric_review']['configuration_sha256']
    confirmed['channels']['joint']['logical_name'] = 'joint/arm/left/feedback'
    review = numeric_review(confirmed)
    assert review['status'] == 'confirmed' and review['configuration_sha256'] == before
    confirmed['channels']['joint']['unit'] = 'deg'
    assert numeric_review(confirmed)['status'] == 'stale'


def test_visual_types_and_projection_nested_bindings_resolve():
    VisualQuality = pytest.importorskip('edc.algorithms.quality.adapters.vision', reason='GPU extension is outside CPU distribution').VisualQuality
    c = Channel('source_rgb', 'rgb', [], 2, 'test', image_available=True, side='left',
        logical_name='rgb/wrist/left', representation={'camera_role': 'wrist', 'camera_role_status': 'confirmed'})
    ep = SimpleNamespace(channels={c.name: c})
    params = {'channels': ['rgb/wrist/left'], 'camera_types': {'rgb/wrist/left': 'wrist'}}
    channels, types, _ = VisualQuality().resolve_cameras(ep, params)
    assert channels == [c] and types == {'source_rgb': 'wrist'}
    assert resolve_channel_parameters(ep, {'camera_channel': 'rgb/wrist/left',
        'poses': {'R': {'channel': 'rgb/wrist/left', 'field': 'position'}}}) == {
        'camera_channel': 'source_rgb', 'poses': {'R': {'channel': 'source_rgb', 'field': 'position'}}}
    with pytest.raises(ValueError):
        VisualQuality().resolve_cameras(ep, {'channels': ['rgb/wrist/right']})


def test_unknown_semantics_stay_unknown_after_logical_binding(tmp_path):
    path, p = fixture(tmp_path)
    spec = p['channels']['raw/pose']
    spec.update(unit='unknown', source_kind='unknown', calibration_status='unknown')
    with open_dataset(path, profile=p) as ds:
        pool = recommend(ds, params={'Action_Smoothness': {'channel': 'pose/arm/right/feedback'}})
        row = next(r for r in pool['episodes'][0]['algorithms'] if r['algorithm']['id'] == 'Action_Smoothness')
        assert row['status'] == 'missing_input'
        ep = next(ds.iter_episodes()); ep.open()
        assert ep.channels['raw/pose'].source_kind == 'unknown'


def test_official_lerobot_roundtrip_keeps_logical_and_source_names(tmp_path):
    pytest.importorskip('lerobot')
    from edc import convert_to_lerobot
    path, profile = fixture(tmp_path)
    profile['lerobot'] = {'original_time': {'time_source': 'acquisition', 'clock': 'test'}}
    output = tmp_path/'converted'
    result = convert_to_lerobot(path, profile=profile, output=output, fps=30)
    assert result['fidelity_status'] == 'passed'
    with open_dataset(output) as ds:
        ep = next(ds.iter_episodes()); catalog = channel_catalog(ep)
        assert ep.backend == 'official'
        row = next(c for c in catalog['channels'] if c['logical_name'])
        assert row['channel'] == 'raw/pose'
        assert row['source_mapping']['feature'] == 'source.raw.pose'
        assert resolve_channel(ep, 'pose/arm/right/feedback') == 'raw/pose'
        values = ep.read_signal('raw/pose', 'position')
        assert values.dtype == np.float64
        np.testing.assert_array_equal(values, np.arange(90, dtype=np.float64).reshape(30, 3)/100)
