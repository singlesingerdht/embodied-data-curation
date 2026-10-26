"""Subcheck evidence distinguishes unconfigured/short/blocked from evaluated."""
import json
from types import SimpleNamespace
import numpy as np
from framework.ir.dataset import Channel
from framework.robots.numeric_review import confirm_numeric_profile
from Quality_Assessment.adapters.cpu import builtins


def evaluate(values, modality='joint_space', field='position', kind='feedback', confirmed=True, thresholds=None):
    unit = {'joint_space': 'rad', 'pose': 'm', 'imu': 'rad/s' if field == 'ang_vel' else 'm/s^2'}[modality]
    spec = {'modality': modality, 'fields': [field], 'unit': unit,
            'source_kind': kind, 'calibration_status': 'confirmed', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}
    p = {'schema_version': 1, 'id': 'coverage_fixture', 'version': '1', 'channels': {'data': spec}}
    if thresholds:
        p['numeric_check'] = {'thresholds': thresholds}
    if confirmed:
        p = confirm_numeric_profile(p, 'fixture owner')
    c = Channel('data', modality, [field], len(values), 'fixture.json', unit=unit,
        source_kind=kind, calibration_status='confirmed', value_available=True, representation={'quantity': 'fixture_value', 'semantic_status': 'confirmed'})
    ep = SimpleNamespace(profile=p, channels={'data': c}, read_signal=lambda *args: values)
    plugin = next(p for p in builtins() if p.spec.id == 'Numeric_Check')
    result = plugin.run(SimpleNamespace(episode=ep), {})[0]
    return result, {row['id']: row for row in result['check_coverage']['checks']}


def test_limits_unconfigured_is_separate_from_success_and_legacy_score():
    from Quality_Assessment.algorithms.Numeric_Check.run_check import assess
    values = np.array([[0.], [.1], [.2]])
    result, checks = evaluate(values)
    original = assess(joint_positions=values)
    for key in ('score','passed','verdict','numeric','sub_indicators'):
        assert result[key] == original[key]
    assert result['assessment_complete'] is True  # compatibility scope explicitly retained
    assert result['check_coverage']['coverage_complete'] is False
    assert checks['finite']['status'] == checks['joint_step']['status'] == 'evaluated'
    assert checks['joint_limits']['status'] == 'unconfigured' and checks['joint_limits']['outcome'] is None
    assert checks['gyro_range']['status'] == 'not_applicable'


def test_confirmed_limit_and_jump_events_preserve_values():
    values = np.array([[0.], [.1], [2.]])
    result, checks = evaluate(values, thresholds={'joint': {'limits': [[-1, 1]]}})
    assert checks['joint_limits']['status'] == 'evaluated'
    assert checks['joint_limits']['outcome'] == checks['joint_step']['outcome'] == 'finding'
    assert checks['joint_step']['events'][0]['examples'][0]['absolute_difference'] == 1.9
    assert 'sample' in checks['joint_step']['unit']


def test_finite_always_runs_while_physical_awaits_confirmation():
    _, checks = evaluate(np.array([[0.], [.1], [.2]]), confirmed=False)
    assert checks['finite']['status'] == 'evaluated'
    assert checks['joint_step']['status'] == 'pending_confirmation'
    assert checks['joint_limits']['status'] == 'unconfigured'
    result, checks = evaluate(np.array([[0.], [np.inf], [np.nan]]))
    assert checks['finite']['outcome'] == 'finding'
    assert checks['joint_step']['status'] == 'blocked_nonfinite'
    assert result['verdict'] == 'DO_NOT_USE'


def test_short_command_and_pose_not_claimed_evaluated():
    _, checks = evaluate(np.zeros((2,1)), kind='command')
    assert checks['command_difference']['status'] == 'insufficient_input'
    _, checks = evaluate(np.zeros((2,3)), modality='pose')
    assert checks['pose_freeze']['status'] == 'insufficient_input'


def test_imu_range_and_bias_are_separate():
    _, checks = evaluate(np.zeros((60,3)), modality='imu', field='lin_acc')
    assert checks['accel_range']['status'] == 'evaluated'
    assert checks['accel_bias']['status'] == 'insufficient_input'
    _, checks = evaluate(np.zeros((10,3)), modality='imu', field='ang_vel')
    assert checks['gyro_range']['status'] == 'evaluated'
    assert checks['gyro_bias']['status'] == 'insufficient_input'


def test_report_shows_subchecks_and_does_not_invent_historical_coverage(tmp_path):
    from framework.reporting import numeric_panel
    result, _ = evaluate(np.zeros((3,1)))
    record = {'algorithm': {'id': 'Numeric_Check'}, 'status': 'completed', 'result': {'reports': [result]}}
    page = numeric_panel([record], tmp_path)
    assert '关节位置限位' in page and '未配置' in page and '已检查' in page
    assert '位置差' in page and '不代表真实速度' in page
    old = json.loads(json.dumps(record))
    del old['result']['reports'][0]['check_coverage']
    assert '历史记录未保存分项覆盖' in numeric_panel([old], tmp_path)
