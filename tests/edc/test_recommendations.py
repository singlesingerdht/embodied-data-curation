"""Applicability is configuration evidence, never a substitute for algorithm results."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from edc import open_dataset, recommend, run
from edc.data.ir.units import SIDataset, UnitInputError
from edc.algorithms.registry import AlgorithmSpec, Registry
from edc.robots.numeric_review import confirm_numeric_profile


def fixture(tmp_path, unit=None, extra=None):
    path = tmp_path / 'episode'
    path.mkdir()
    rows = [{'timestamp': i * .05, 'joint_pos': [i * .01], **(extra or {})} for i in range(60)]
    (path / 'trajectory.json').write_text(json.dumps({'data': rows}))
    p = {'schema_version': 1, 'id': 'units', 'version': '1', 'timestamp_unit': 's', 'channels': {'joint_pos': {
        'modality': 'joint_space', 'fields': ['position'], 'unit': unit,
        'source_kind': 'feedback', 'calibration_status': 'confirmed', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    return path, p


def entries(pool):
    return {x['algorithm']['id']: x for x in pool['episodes'][0]['algorithms']}


def record(out, ident):
    return json.loads(next((out / '_runs').glob(f'*/{ident}/records.jsonl')).read_text())


@pytest.mark.parametrize('unit', [None, '', 'unknown'])
def test_blank_units_allowed_but_physics_blocked_and_partial_explicit(tmp_path, unit):
    path, p = fixture(tmp_path, unit)
    original = copy.deepcopy(p)
    with open_dataset(path, profile=p) as ds:
        pool = recommend(ds)
        assert ds.profile == original
        es = entries(pool)
        assert es['Structure_Check']['availability'] == 'available'
        numeric = es['Numeric_Check']
        assert numeric['availability'] == 'partial' and numeric['status'] == 'runnable'
        field = next(x for x in numeric['fields'] if x['channel'] == 'joint_pos')
        assert not field['physical_checks_available']
        assert any('joint_pos.position' in r and 'source unit' in r for r in field['reasons'])
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            with pytest.raises(UnitInputError, match='joint_pos.position'):
                ep.read_signal('joint_pos', 'position')
            assert ep.inspect_recorded_signal('joint_pos', 'position').shape == (60, 1)
        run(ds, algorithm_ids=['Numeric_Check', 'Structure_Check'], output=tmp_path / 'out')
    result = record(tmp_path / 'out', 'Numeric_Check')['result']['reports'][0]
    assert not result['physical_checks_applied'] and not result['assessment_complete']
    assert result['physical_check_blockers'] == field['reasons']
    assert record(tmp_path / 'out', 'Structure_Check')['status'] == 'completed'


def test_si_length_joint_uses_length_detector_target(tmp_path):
    path, p = fixture(tmp_path, 'm')
    with open_dataset(path, profile=p) as ds:
        field = next(x for x in entries(recommend(ds))['Numeric_Check']['fields'] if x['channel'] == 'joint_pos')
        assert field['required_unit'] == 'm'
        assert not field['physical_checks_available']  # author confirmation is still pending
        assert not any('dimensions do not match' in r for r in field['reasons'])
        run(ds, algorithm_ids=['Numeric_Check'], output=tmp_path / 'out')
    result = record(tmp_path / 'out', 'Numeric_Check')['result']['reports'][0]
    assert not result['physical_checks_applied'] and result['unit'] == 'm'


@pytest.mark.parametrize('source_unit', [None, ''])
def test_explicit_head_tilt_and_extension_split_convert_independently(tmp_path, source_unit):
    path, p = fixture(tmp_path, None, {'head': [90., 120.]})
    p['channels'].update({
        'head': {'modality': 'other', 'fields': ['recorded_values'], 'unit': source_unit,
                 'source_kind': 'feedback', 'representation': {'quantity': 'unknown'}},
        'head_tilt': {'modality': 'joint_space', 'fields': ['position'], 'unit': 'deg',
                      'select': {'channel': 'head', 'field': 'recorded_values', 'indices': [0]}, 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}},
        'head_extension': {'modality': 'joint_space', 'fields': ['position'], 'unit': 'mm',
                           'select': {'channel': 'head', 'field': 'recorded_values', 'indices': [1]}, 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}})
    with open_dataset(path, profile=p) as ds:
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            np.testing.assert_allclose(ep.read_signal('head_tilt', 'position'), np.full((60, 1), np.pi / 2))
            np.testing.assert_allclose(ep.read_signal('head_extension', 'position'), np.full((60, 1), .12))
            with pytest.raises(UnitInputError):
                ep.read_signal('head', 'recorded_values')
            np.testing.assert_array_equal(ep.inspect_recorded_signal('head', 'recorded_values')[0], [90., 120.])
            assert ep.channels['head_extension'].field_spec('position')['source_kind'] == 'feedback'
        es = entries(recommend(ds))
        field = next(f for f in es['Numeric_Check']['fields'] if f['channel'] == 'head_extension')
        assert field['required_unit'] == 'm'
        assert not any('dimensions do not match' in r for r in field['reasons'])


def test_video_preflight_does_not_depend_on_unknown_numeric_units(tmp_path, monkeypatch):
    from edc.data.ir.dataset import Channel
    from edc.algorithms.quality.adapters.cpu import builtins, preflight
    VisualQuality = pytest.importorskip('edc.algorithms.quality.adapters.vision', reason='GPU extension is outside CPU distribution').VisualQuality
    ep = SimpleNamespace(format='episode', profile={}, selector={}, image_storage=lambda _: 'video', channels={
        'video': Channel('video', 'rgb', [], 60, str(tmp_path / 'video.mp4'), image_available=True),
        'hand': Channel('hand', 'other', ['recorded_values'], 60, '', value_available=True, unit=None)})
    monkeypatch.setattr('edc.algorithms.quality.adapters.cpu.util.find_spec', lambda _: object())
    monkeypatch.setattr('edc.algorithms.quality.adapters.cpu.shutil.which', lambda _: '/synthetic/tool')
    video = next(p for p in builtins() if p.spec.id == 'Video_Integrity')
    assert preflight(video, ep) == ('runnable', '')
    status, reason = preflight(VisualQuality(), ep)
    assert status == 'missing_input' and 'camera_types' in reason
    assert 'unit' not in reason.lower()


def test_unknown_quantity_with_filled_unit_is_still_blocked(tmp_path):
    path, p = fixture(tmp_path, 'rad')
    p['channels']['joint_pos']['representation'] = {'quantity': 'unknown', 'semantic_status': 'pending_owner_confirmation'}
    with pytest.raises(ValueError, match='physical quantity'):
        confirm_numeric_profile(p, 'owner')
    with open_dataset(path, profile=p) as ds:
        with next(SIDataset(ds).iter_episodes()).opened() as ep:
            assert ep.unit_evidence()['fields'][0]['status'] == 'blocked'
            with pytest.raises(UnitInputError, match='physical quantity'):
                ep.read_signal('joint_pos', 'position')


def test_unrelated_unknown_hand_and_joint_do_not_block_selected_pose(tmp_path):
    pytest.importorskip('scipy')
    path, p = fixture(tmp_path, None, {'right_pose': [.01, .02, .03], 'hand': [9., 10.], 'left_pose': [1., 2., 3.]})
    p['channels'].update({
        'right_pose': {'modality': 'pose', 'fields': ['position'], 'unit': 'mm', 'time_source': 'acquisition', 'clock': 'robot', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}},
        'left_pose': {'modality': 'pose', 'fields': ['position'], 'unit': None, 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}},
        'hand': {'modality': 'other', 'fields': ['recorded_values'], 'unit': None,
                 'body_part': 'hand', 'representation': {'quantity': 'unknown'}}})
    with open_dataset(path, profile=p) as ds:
        assert entries(recommend(ds, params={'Action_Smoothness': {'expected_rate_hz': 20}}))['Action_Smoothness']['availability'] == 'available'
        run(ds, algorithm_ids=['Action_Smoothness'], output=tmp_path / 'out')
        left = entries(recommend(ds, params={'Action_Smoothness': {'side': 'left'}}))['Action_Smoothness']
        assert left['status'] == 'missing_input' and 'left_pose.position' in left['reason']
    assert record(tmp_path / 'out', 'Action_Smoothness')['status'] == 'completed'


@pytest.mark.parametrize('unit,policy,expected', [('deg', 'auto', 'completed'), ('deg', 'manual', 'missing_input'),
    (None, 'auto', 'missing_input'), ('m', 'auto', 'missing_input')])
def test_plugin_explicit_unit_contract_matches_execution(tmp_path, monkeypatch, unit, policy, expected):
    path, p = fixture(tmp_path, unit)
    called = []
    def execute(context, params):
        called.append(True)
        values = context.episode.read_signal('joint_pos', 'position')
        np.testing.assert_allclose(values[-1], [.59 * np.pi / 180])
        return {'verdict': 'OBSERVATION'}
    plugin = SimpleNamespace(spec=AlgorithmSpec('fixture__angular', '1', 'angle', 'fixture', 'probe',
        input_requirements=({'channel': 'joint_pos', 'field': 'position', 'unit': 'rad'},)), run=execute)
    monkeypatch.setattr('edc.algorithms.registry.entry_points', lambda group: [
        SimpleNamespace(name='fixture__angular', load=lambda: plugin)] if group == 'edc.algorithms' else [])
    with open_dataset(path, profile=p) as ds:
        e = entries(recommend(ds, unit_policy=policy))['fixture__angular']
        assert e['status'] == ('runnable' if expected == 'completed' else expected)
        if expected != 'completed':
            assert 'joint_pos.position' in e['reason']
        run(ds, algorithm_ids=['fixture__angular', 'Structure_Check'], output=tmp_path / 'out', unit_policy=policy)
    assert record(tmp_path / 'out', 'fixture__angular')['status'] == expected
    assert bool(called) == (expected == 'completed')
    assert record(tmp_path / 'out', 'Structure_Check')['status'] == 'completed'


def test_broken_plugin_preflight_is_isolated_and_snapshot_is_preserved(tmp_path, monkeypatch):
    path, p = fixture(tmp_path)
    def fail(ep, params): raise RuntimeError('bad plugin hook')
    plugin = SimpleNamespace(spec=AlgorithmSpec('fixture__broken', '1', 'broken', 'fixture', 'probe'),
                             run=lambda c, p: {}, preflight=fail)
    monkeypatch.setattr('edc.algorithms.registry.entry_points', lambda group: [
        SimpleNamespace(name='fixture__broken', load=lambda: plugin)] if group == 'edc.algorithms' else [])
    with open_dataset(path, profile=p) as ds:
        pool = recommend(ds)
        es = entries(pool)
        assert es['fixture__broken']['status'] == 'processing_error'
        assert es['Structure_Check']['availability'] == 'available'
        run(ds, algorithm_ids=['Structure_Check'], output=tmp_path / 'out')
    snapshot = next((tmp_path / 'out/_runs').glob('*/recommendations.json'))
    before = snapshot.read_bytes()
    assert entries(json.loads(before))['fixture__broken']['availability'] == 'unavailable'
    assert entries(json.loads(before))['Structure_Check']['selected'] is True
    from edc.reporting.results import report
    monkeypatch.setattr('edc.algorithms.registry.algorithms', lambda: pytest.fail('historical report must not discover plugins'))
    page = report(tmp_path / 'out').read_text()
    assert 'content="edc_batch_report_v1"' in page and '结构完整性' in page
    assert '推荐算法池' not in page
    assert 'bad plugin hook' in entries(json.loads(before))['fixture__broken']['reason']
    run_ref = snapshot.parent.relative_to(tmp_path / 'out').as_posix()
    assert f'href="{run_ref}/"' in page
    assert snapshot.read_bytes() == before


def test_plugin_unit_contract_requires_explicit_si_dimension():
    for requirement in ({'channel': 'joint_pos', 'field': 'position'},
                        {'channel': 'joint_pos', 'field': 'position', 'unit': 'deg'}):
        plugin = SimpleNamespace(spec=AlgorithmSpec('fixture__invalid', '1', 'x', 'x', 'x', input_requirements=(requirement,)),
                                 run=lambda c, p: {})
        with pytest.raises(ValueError):
            Registry('edc.algorithms').register(plugin)


def test_luoshi_profile_does_not_invent_gripper_or_head_units():
    root = Path(__file__).resolve().parents[2]
    p = yaml.safe_load((root / 'examples/robots/luoshi_intake.yaml').read_text())
    assert not any('gripper' in name for name in p['channels'])
    for name in ('action/effector/position', 'observation/state/effector/position',
                 'action/head/position', 'observation/state/head/position'):
        c = p['channels'][name]
        assert c['unit'] is None and c['modality'] == 'other'
        assert c['fields'] == ['recorded_values'] and c['representation']['quantity'] == 'unknown'
        assert 'robot_part' not in c
    assert p['channels']['action/effector/position']['representation']['device_type'] == 'dexterous_hand'


def test_cli_recommend_pool_is_included_in_inspect(tmp_path, capsys):
    from edc.execution.cli import main
    path, p = fixture(tmp_path)
    config = tmp_path / 'robot.yaml'
    config.write_text(yaml.safe_dump(p))
    assert main(['recommend', str(path), '--profile', str(config)]) == 0
    pool = json.loads(capsys.readouterr().out)
    assert 'Numeric_Check' in pool['episodes'][0]['partial_algorithms']
    assert main(['inspect', str(path), '--profile', str(config)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['episodes'][0]['algorithm_pool'] == pool['episodes'][0]
