"""Execution uses SI while raw reads, author declarations and source files stay unchanged."""
import copy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from edc.execution.runner import run
from edc.data.ir.dataset import open_dataset
from edc.data.ir.units import SIDataset, UnitInputError
from edc.algorithms.registry import AlgorithmSpec
from edc.robots.numeric_review import confirm_numeric_profile, numeric_review
from edc.data.transforms.core import UNITS


def profile(unit='deg'):
    return {'schema_version': 1, 'id': 'unit_trial', 'version': '1', 'timestamp_unit': 's', 'channels': {
        'joint_pos': {'modality': 'joint_space', 'fields': ['position'], 'unit': unit,
            'source_kind': 'feedback', 'calibration_status': 'confirmed',
            'time_source': 'acquisition', 'clock': 'robot', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}


def source(tmp_path, rows):
    path = tmp_path / 'episode'
    path.mkdir(parents=True)
    (path / 'trajectory.json').write_text(json.dumps({'data': rows}))
    return path


def record(output, ident='Numeric_Check'):
    return json.loads(next((output / '_runs').glob(f'*/{ident}/records.jsonl')).read_text())


def evidence(output, item):
    return json.loads((output / item['unit_normalization']['evidence']).read_text())


def test_degree_and_radian_physical_results_are_equal_and_sources_unchanged(tmp_path):
    physical = np.array([[0., 0.], [np.pi / 2, -np.pi / 4], [np.pi, 0.]])
    outputs = []
    for unit, values in [('deg', np.rad2deg(physical)), ('rad', physical)]:
        path = source(tmp_path / unit, [{'joint_pos': row.tolist()} for row in values])
        p = confirm_numeric_profile(profile(unit), 'fixture author')
        before = copy.deepcopy(p)
        digest = hashlib.sha256((path / 'trajectory.json').read_bytes()).hexdigest()
        out = tmp_path / f'out-{unit}'
        with open_dataset(path, profile=p) as dataset:
            raw_ep = next(dataset.iter_episodes())
            with raw_ep.opened():
                np.testing.assert_array_equal(raw_ep.read_signal('joint_pos', 'position'), values)
            run(dataset, algorithm_ids=['Numeric_Check'], output=out)
            assert dataset.profile == p == before
            with raw_ep.opened():
                assert raw_ep.channels['joint_pos'].field_spec('position')['unit'] == unit
                np.testing.assert_array_equal(raw_ep.read_signal('joint_pos', 'position'), values)
        assert hashlib.sha256((path / 'trajectory.json').read_bytes()).hexdigest() == digest
        item = record(out)
        result = item['result']['reports'][0]
        assert result['physical_checks_applied'] and result['assessment_complete']
        assert result['unit'] == 'rad' and result['source_unit'] == unit
        assert numeric_review(p)['status'] == 'confirmed'
        rule = evidence(out, item)['fields'][0]
        assert rule['source_unit'] == unit and rule['target_unit'] == 'rad'
        assert rule['conversion_applied'] == (unit == 'deg')
        assert rule['si_read_count'] == 1
        outputs.append(result)
    for key in ('numeric', 'score', 'verdict', 'numeric_thresholds'):
        assert outputs[0][key] == outputs[1][key]
    page = (tmp_path / 'out-deg/report.html').read_text()
    assert 'content="edc_batch_report_v1"' in page and '数值检查' in page
    snapshot = next((tmp_path / 'out-deg/_runs').glob('*/run.json'))
    assert json.loads(snapshot.read_text())['unit_normalization']['policy'] == 'auto'
    run_ref = snapshot.parent.relative_to(tmp_path / 'out-deg').as_posix()
    assert f'href="{run_ref}/"' in page


@pytest.mark.parametrize('unit', list(UNITS))
def test_all_supported_units_convert_at_execution_read_only(tmp_path, unit):
    path = source(tmp_path, [{'joint_pos': [0., 1.]}, {'joint_pos': [-1., 2.]}])
    with open_dataset(path, profile=profile(unit)) as dataset:
        execution = SIDataset(dataset)
        with next(execution.iter_episodes()).opened() as ep:
            rule = ep.unit_evidence()['fields'][0]
            assert not rule['conversion_applied'] and rule['si_read_count'] == 0
            np.testing.assert_allclose(ep.read_signal('joint_pos', 'position'),
                np.array([[0., 1.], [-1., 2.]]) * UNITS[unit][1])
            assert ep.channels['joint_pos'].field_spec('position')['unit'] == rule['target_unit']


def test_mixed_fields_selection_legacy_bridge_and_plugin_dataset_use_si(tmp_path, monkeypatch):
    path = source(tmp_path, [{'joint_pos': {'position': [90., 180.], 'current': [1000., 2000.]}}])
    p = profile()
    p['channels']['joint_pos'].update(fields=['position', 'current'], field_metadata={
        'position': {'unit': 'deg'}, 'current': {'unit': 'mA'}})
    p['channels']['right'] = {'modality': 'joint_space', 'fields': ['position'],
        'select': {'channel': 'joint_pos', 'field': 'position', 'indices': [1]}}
    seen = []

    def probe(context, params):
        ep = context.episode
        np.testing.assert_allclose(ep.read_signal('joint_pos', 'position'), [[np.pi / 2, np.pi]])
        np.testing.assert_allclose(ep.read_signal('joint_pos', 'current'), [[1., 2.]])
        np.testing.assert_allclose(ep.read_signal('right', 'position'), [[np.pi]])
        info, reader = ep.algorithm_view()
        assert info.topics['joint_pos'].field_metadata['current']['unit'] == 'A'
        np.testing.assert_allclose(reader.channel_values('joint_pos')[0]['position'], [np.pi / 2, np.pi])
        np.testing.assert_allclose(reader.read_field(info, 'right', 'position'), [[np.pi]])
        reopened = next(context.dataset.iter_episodes())
        with reopened.opened():
            np.testing.assert_allclose(reopened.read_signal('joint_pos', 'current'), [[1., 2.]])
        seen.append(True)
        return {'verdict': 'ACCEPTABLE'}

    plugin = SimpleNamespace(spec=AlgorithmSpec('fixture__si', '1', 'SI probe', 'fixture', 'probe'), run=probe)
    monkeypatch.setattr('edc.algorithms.registry.entry_points', lambda group: [
        SimpleNamespace(name='fixture__si', load=lambda: plugin)] if group == 'edc.algorithms' else [])
    out = tmp_path / 'out'
    with open_dataset(path, profile=p) as dataset:
        assert run(dataset, algorithm_ids=['fixture__si'], output=out)['status_counts'] == {'completed': 1}
    assert seen == [True]
    rule = next(x for x in evidence(out, record(out, 'fixture__si'))['fields'] if x['channel'] == 'right')
    assert rule['conversion_applied']
    assert rule['source_mapping']['channel'] == 'joint_pos'
    assert rule['source_mapping']['selection']['indices'] == [1]


@pytest.mark.parametrize('unit', ['deg', 'rad'])
def test_manual_policy_never_forwards_non_si_to_physical_check(tmp_path, unit):
    path = source(tmp_path, [{'joint_pos': [0.]}, {'joint_pos': [1.]}])
    p = confirm_numeric_profile(profile(unit), 'fixture author')
    out = tmp_path / 'out'
    with open_dataset(path, profile=p) as dataset:
        run(dataset, algorithm_ids=['Numeric_Check', 'Structure_Check'], output=out, unit_policy='manual')
        with next(SIDataset(dataset, 'manual').iter_episodes()).opened() as ep:
            if unit == 'deg':
                with pytest.raises(UnitInputError, match='disabled'):
                    ep.read_signal('joint_pos', 'position')
            else:
                np.testing.assert_array_equal(ep.read_signal('joint_pos', 'position'), [[0.], [1.]])
    result = record(out)['result']['reports'][0]
    assert result['physical_checks_applied'] == (unit == 'rad')
    if unit == 'deg':
        assert not result['assessment_complete'] and result['verdict'] == 'NOT_APPLICABLE'
        assert result['input_scope'] == 'recorded_finiteness_only'
        assert 'disabled' in result['unit_normalization_reason']
    assert record(out, 'Structure_Check')['status'] == 'completed'
    assert record(out)['unit_normalization']['policy'] == 'manual'
    rules = evidence(out, record(out))
    assert rules['unit_policy'] == 'manual' and rules['source_unchanged']
    assert not rules['fields'][0]['conversion_applied']
    assert rules['fields'][0]['status'] == ('blocked' if unit == 'deg' else 'identity')
    assert '数值检查' in (out / 'report.html').read_text()


@pytest.mark.parametrize('unit', [None, 'unknown', 'ticks', 'A'])
def test_missing_or_wrong_dimension_does_not_claim_physical_pass(tmp_path, unit):
    path = source(tmp_path, [{'joint_pos': [float('inf'), float('nan'), float('-inf')]}])
    out = tmp_path / 'out'
    with open_dataset(path, profile=profile(unit)) as dataset:
        run(dataset, algorithm_ids=['Numeric_Check', 'Structure_Check'], output=out)
    result = record(out)['result']['reports'][0]
    assert [result[key] for key in ('nan_count', 'posinf_count', 'neginf_count')] == [1, 1, 1]
    assert not result['physical_checks_applied'] and not result['assessment_complete']
    assert result['nonfinite_indices'] == [[0, 0], [0, 1], [0, 2]]
    assert record(out, 'Structure_Check')['status'] == 'completed'


def test_millisecond_timeline_is_not_scaled_twice_or_promoted_to_acquisition(tmp_path):
    path = source(tmp_path, [{'timestamp': 1000 + i * 100, 'joint_pos': [i]} for i in range(3)])
    p = profile(); p['timestamp_unit'] = 'ms'
    p['channels']['joint_pos'].update(time_source='unknown', clock=None)
    with open_dataset(path, profile=p) as dataset:
        with next(SIDataset(dataset).iter_episodes()).opened() as ep:
            np.testing.assert_allclose(ep.channels['joint_pos'].timestamps, [1., 1.1, 1.2])
            assert ep.channels['joint_pos'].time_source == 'unknown'
            assert ep.channels['joint_pos'].clock is None
            assert ep.unit_evidence()['timeline_unit'] == 's'


def test_reset_reference_and_tolerance_convert_together_without_method_changes(tmp_path):
    path = source(tmp_path, [{'joint_pos': [90.]}, {'joint_pos': [92.]}])
    p = profile()
    p['calibration'] = {'Reset_Detection': {'unit': 'deg', 'reference': {'joint_pos': [90.]}, 'tolerance': 3.}}
    out = tmp_path / 'out'
    with open_dataset(path, profile=p) as dataset:
        run(dataset, algorithm_ids=['Reset_Detection'], output=out)
        run(dataset, algorithm_ids=['Reset_Detection'], output=tmp_path / 'manual', unit_policy='manual')
    item = record(out, 'Reset_Detection')
    assert item['status'] == 'completed'
    result = item['result']['reports'][0]
    assert result['passed'] and result['method'] == 'calibrated_home_reference_v1'
    assert result['calibration']['unit'] == 'rad'
    assert result['calibration']['tolerance'] == pytest.approx(np.deg2rad(3.))
    assert result['evidence'][0]['end_error'] == pytest.approx(np.deg2rad(2.))
    assert result['source_calibration'] == p['calibration']['Reset_Detection']
    assert record(tmp_path / 'manual', 'Reset_Detection')['status'] == 'needs_calibration'


def test_smoothness_millimetres_and_metres_preserve_original_results(tmp_path):
    pytest.importorskip('scipy')
    results = []
    for unit, scale in [('mm', 1000.), ('m', 1.)]:
        rows = [{'timestamp': i * .05, 'pose': [i * .001 * scale, 0., .2 * scale]} for i in range(60)]
        path = source(tmp_path / unit, rows)
        p = profile()
        p['channels'] = {'pose': {'modality': 'pose', 'fields': ['position'], 'unit': unit,
            'side': 'right', 'time_source': 'acquisition', 'clock': 'robot', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}
        with open_dataset(path, profile=p) as dataset:
            run(dataset, algorithm_ids=['Action_Smoothness'], output=tmp_path / f'out-{unit}')
        item = record(tmp_path / f'out-{unit}', 'Action_Smoothness')
        assert item['status'] == 'completed'
        results.append(item['result']['reports'][0])
    for key in ('score', 'grade', 'verdict', 'num_poses'):
        assert results[0][key] == results[1][key]


def test_unknown_finite_values_remain_partial_not_a_physical_pass(tmp_path):
    path = source(tmp_path, [{'joint_pos': [0.]}, {'joint_pos': [1.]}])
    with open_dataset(path, profile=profile('ticks')) as dataset:
        run(dataset, algorithm_ids=['Numeric_Check'], output=tmp_path / 'out')
    item = record(tmp_path / 'out')
    result = item['result']['reports'][0]
    assert result['finite'] and result['verdict'] == 'NOT_APPLICABLE'
    assert not result['assessment_complete'] and not result['physical_checks_applied']
    assert result['input_scope'] == 'recorded_finiteness_only'
    assert evidence(tmp_path / 'out', item)['fields'][0]['status'] == 'blocked'


def test_non_si_plugin_failure_is_missing_input_and_does_not_block_other_algorithms(tmp_path, monkeypatch):
    path = source(tmp_path, [{'joint_pos': [90.]}])
    plugin = SimpleNamespace(spec=AlgorithmSpec('fixture__reader', '1', 'probe', 'fixture', 'probe'),
        run=lambda context, params: {'values': context.episode.read_signal('joint_pos', 'position').tolist()})
    monkeypatch.setattr('edc.algorithms.registry.entry_points', lambda group: [
        SimpleNamespace(name='fixture__reader', load=lambda: plugin)] if group == 'edc.algorithms' else [])
    out = tmp_path / 'out'
    with open_dataset(path, profile=profile()) as dataset:
        run(dataset, algorithm_ids=['fixture__reader', 'Structure_Check'], output=out, unit_policy='manual')
    assert record(out, 'fixture__reader')['status'] == 'missing_input'
    assert record(out, 'Structure_Check')['status'] == 'completed'
    assert not evidence(out, record(out, 'Structure_Check'))['fields'][0]['conversion_applied']


def test_cli_inspect_and_run_share_policy_and_preserve_raw_declarations(tmp_path, capsys):
    from edc.execution.cli import main
    path = source(tmp_path, [{'joint_pos': [90.]}])
    config = tmp_path / 'robot.yaml'; config.write_text(yaml.safe_dump(profile()))
    assert main(['inspect', str(path), '--profile', str(config)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['episodes'][0]['channels'][0]['unit'] == 'deg'
    assert result['episodes'][0]['unit_normalization']['fields'][0]['target_unit'] == 'rad'
    assert not result['episodes'][0]['unit_normalization']['fields'][0]['conversion_applied']
    assert main(['run', str(path), '--profile', str(config), '--algorithm', 'Numeric_Check',
        '--output', str(tmp_path / 'out'), '--unit-policy', 'manual']) == 0
    capsys.readouterr()
    item = record(tmp_path / 'out')
    assert item['unit_normalization']['policy'] == 'manual'
    with open_dataset(path, profile=profile()) as dataset:
        with pytest.raises(ValueError, match='unit_policy'):
            run(dataset, output=tmp_path / 'bad', unit_policy='guess')
    assert not (tmp_path / 'bad').exists()
