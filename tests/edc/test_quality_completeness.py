"""Incomplete numeric checks cannot release data, even with a legacy score of 100."""
import copy
import json
from types import SimpleNamespace
import numpy as np
import pytest
from edc.algorithms.quality.adapters.cpu import builtins
from edc.data.ir.dataset import Channel, open_dataset
from edc.execution.completeness import record_complete
from edc.execution.runner import run
from edc.robots.numeric_review import confirm_numeric_profile
from edc.workflows.gates import quality_gate


def plugin():
    return next(p for p in builtins() if p.spec.id == 'Numeric_Check')


def record(reports):
    return {'algorithm': {'id': 'Numeric_Check', 'version': 'ir-adapter-15'},
        'object': {'type': 'episode', 'id': 'e'}, 'status': 'completed',
        'result': {'reports': reports}}


@pytest.mark.parametrize('status', ['unconfigured', 'insufficient_input', 'pending_confirmation'])
def test_nested_missing_check_blocks_keep_and_review(status):
    report = {'score': 100, 'verdict': 'ACCEPTABLE', 'assessment_complete': True,
        'check_coverage': {'coverage_complete': False, 'checks': [{'id': 'missing', 'status': status}]}}
    row = record([report])
    original = copy.deepcopy(row)
    assert not record_complete(row)
    assert quality_gate([row], ['e'], 'r')['e']['decision'] == 'unassessed'
    assert row == original
    report['review_candidates'] = [{'needs_review': True}]
    assert quality_gate([row], ['e'], 'r')['e']['decision'] == 'unassessed'
    report['verdict'] = 'DO_NOT_USE'
    assert quality_gate([row], ['e'], 'r')['e']['decision'] == 'exclude'


def test_historical_missing_coverage_and_false_summary_cannot_imply_success():
    report = {'score': 100, 'verdict': 'ACCEPTABLE', 'assessment_complete': True}
    row = record([report])
    assert quality_gate([row], ['e'], 'r')['e']['decision'] == 'unassessed'
    report['check_coverage'] = {'coverage_complete': True,
        'checks': [{'status': 'evaluated'}, {'status': 'unconfigured'}]}
    assert not record_complete(row)
    report['check_coverage']['checks'][1]['status'] = 'not_applicable'
    assert record_complete(row)
    assert quality_gate([row], ['e'], 'r')['e']['decision'] == 'keep'


def imu_episode(n=60, accel_z=9.81, gyro=None, with_gyro=True):
    fields = ['ang_vel', 'lin_acc'] if with_gyro else ['lin_acc']
    metadata = {f: {'unit': 'rad/s' if f == 'ang_vel' else 'm/s^2'} for f in fields}
    spec = {'modality': 'imu', 'fields': fields, 'field_metadata': metadata,
        'source_kind': 'feedback', 'calibration_status': 'confirmed',
        'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}
    profile = confirm_numeric_profile({'schema_version': 1, 'id': 'imu', 'version': '1',
        'channels': {'imu': spec}}, 'synthetic fixture')
    channel = Channel('imu', 'imu', fields, n, 'synthetic.json',
        field_metadata=metadata, source_kind='feedback', calibration_status='confirmed',
        representation=spec['representation'], value_available=True)
    values = {'ang_vel': np.zeros((n, 3)) if gyro is None else gyro,
        'lin_acc': np.tile([0., 0., accel_z], (n, 1))}
    ep = SimpleNamespace(profile=profile, channels={'imu': channel},
        read_signal=lambda name, field: values[field], selector={'episode': 4})
    return ep, values


@pytest.mark.parametrize('accel_z,expected', [(9.81, 'keep'), (11., 'review')])
def test_same_channel_pair_runs_bias_without_changing_historical_score(accel_z, expected):
    from edc.algorithms.quality.algorithms.Numeric_Check.run_check import assess
    ep, values = imu_episode(accel_z=accel_z)
    original = {k: v.copy() for k, v in values.items()}
    reports = plugin().run(SimpleNamespace(episode=ep), {})
    acceleration = next(r for r in reports if r['input_field'] == 'lin_acc')
    old = assess(accel=values['lin_acc'])
    for key in ('score', 'verdict', 'passed', 'numeric', 'sub_indicators'):
        assert acceleration[key] == old[key]
    pair = acceleration['imu_pair_check']
    assert pair['status'] == 'evaluated'
    assert pair['source_mapping']['fields'] == ['ang_vel', 'lin_acc']
    assert pair['source_mapping']['episode_selector'] == {'episode': 4}
    assert pair['time_source'] == 'unknown' and pair['clock'] is None
    assert not pair['establishes_acquisition_sync']
    assert acceleration['coverage_complete']
    assert quality_gate([record(reports)], ['e'], 'r')['e']['decision'] == expected
    assert all(np.array_equal(values[k], original[k]) for k in values)


@pytest.mark.parametrize('case', ['missing', 'short', 'mismatch', 'nonfinite', 'moving', 'unknown_unit', 'other_channel'])
def test_unusable_pair_never_claims_bias_evaluated(case):
    ep, values = imu_episode(n=2 if case == 'short' else 60, with_gyro=case not in {'missing', 'other_channel'})
    if case == 'mismatch': values['ang_vel'] = values['ang_vel'][:-1]
    if case == 'nonfinite': values['ang_vel'][0, 0] = np.nan
    if case == 'moving': values['ang_vel'][:, 0] = np.arange(60) * .03
    if case == 'unknown_unit': ep.channels['imu'].field_metadata['ang_vel']['unit'] = 'unknown'
    if case == 'other_channel':
        other, _ = imu_episode()
        ep.channels['other'] = other.channels['imu']
        ep.channels['other'].name = 'other'
        ep.channels['other'].fields = ['ang_vel']
    result = next(r for r in plugin().run(SimpleNamespace(episode=ep), {}) if r['input_field'] == 'lin_acc')
    check = next(c for c in result['check_coverage']['checks'] if c['id'] == 'accel_bias')
    assert check['status'] != 'evaluated'
    assert not result['coverage_complete']
    assert quality_gate([record([result])], ['e'], 'r')['e']['decision'] == 'unassessed'


@pytest.mark.parametrize('limits,expected', [(None, 'unassessed'), ([[-1., 1.]], 'keep')])
def test_direct_run_and_recommendation_use_subcheck_coverage(tmp_path, limits, expected):
    source = tmp_path / 'episode'
    source.mkdir()
    (source / 'trajectory.json').write_text(json.dumps({'data': [
        {'joint_pos': [x]} for x in (0., .1, .2)]}))
    spec = {'modality': 'joint_space', 'fields': ['position'], 'unit': 'rad',
        'source_kind': 'feedback', 'calibration_status': 'confirmed',
        'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}
    profile = confirm_numeric_profile({'schema_version': 1, 'id': 'joint', 'version': '1',
        'channels': {'joint_pos': spec}, 'numeric_check': {'thresholds': {'joint': {'limits': limits}}}},
        'synthetic fixture')
    with open_dataset(source, profile=profile) as ds:
        ep = next(ds.iter_episodes())
        with ep.opened():
            available = plugin().availability(ep, {})
            assert available['availability'] == ('partial' if limits is None else 'available')
        run(ds, algorithm_ids=['Numeric_Check'], output=tmp_path / 'out')
    selected = json.loads(next((tmp_path / 'out/_runs').glob('*/selection.json')).read_text())[0]
    assert selected['decision'] == expected
    assert selected['assessment_complete'] is (limits is not None)
    assert selected['policy_version'] == 'episode_filter_v2'
    if limits is None:
        assert '本次评估尚未完成' in (tmp_path / 'out/report.html').read_text()
