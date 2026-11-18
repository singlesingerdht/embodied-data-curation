"""Pending signal interpretations can produce conditional evidence, never completion."""
import copy
import json

import numpy as np
import pytest

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import EpisodeAttributor
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.physical_features import PhysicalFeatures
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import worker
from edc.reporting.results import write_json
from test_load_rules import current_signals


def pending(signals):
    for roles in signals.arms.values():
        for role in ('hand_position', 'hand_load', 'arm_load'):
            cfg = roles[role]['config']
            cfg.pop('delta_threshold', None)
            cfg.update(calibration_status='pending', source_kind='feedback',
                       calibration_reference='Synthetic pending interpretation',
                       quantity='position' if role == 'hand_position' else 'current')
        roles['hand_position']['config'].update(unit='cm', grip_mode='native_relative',
            opening_direction='increasing', direction_reference='Synthetic increasing-open assumption')
        roles['hand_position']['openness'] = None
    return signals


def test_actual_attributor_calculates_pending_signals_without_upgrading_metadata(tmp_path):
    signals = pending(current_signals())
    for roles in signals.arms.values():
        # Opening is outside the existing 2.5 s lookback; closure is inside it.
        roles['hand_position']['values'] = np.r_[np.zeros(20), np.ones(80), np.zeros(100)][:, None]
    before = copy.deepcopy(signals)
    strict = EpisodeAttributor(str(tmp_path), 20, signals=signals)
    trial = EpisodeAttributor(str(tmp_path), 20, signals=signals, evidence_policy='allow_provisional')
    curve = [{'frame_index': i, 'progress': .5 if i < 100 else .2} for i in range(0, 200, 10)]
    strict_result = strict.attribute([100], curve)
    assert strict_result['results']['100_right']['primary_label'] == 'unknown_needs_review'
    result = trial.attribute([100], curve)
    right = result['results']['100_right']
    assert right['primary_label'] == 'early_close_air_grasp'
    assert right['evidence']['close_load_delta'] == pytest.approx(.25)
    assert right['evidence_status'] == 'provisional'
    assert result['assessment_complete'] is False
    assert right['evidence']['calibration_assumptions']
    assert trial.proactive_grasp_scan({i: p['progress'] for i, p in enumerate(curve)}) == []
    np.testing.assert_array_equal(trial.gfeat.gripper['right'], signals.arms['right']['hand_position']['values'][:, 0])
    np.testing.assert_allclose(trial.gfeat.cur_rms['right'], np.sqrt(np.mean(signals.arms['right']['arm_load']['values']**2, axis=1)))
    for arm, roles in signals.arms.items():
        for role, entry in roles.items():
            assert entry['config'] == before.arms[arm][role]['config']
            np.testing.assert_array_equal(entry['values'], before.arms[arm][role]['values'])
    assert signals.arms['right']['hand_position']['openness'] is None


@pytest.mark.parametrize('change', [
    {'unit': 'unknown'}, {'unit': 'N*m'}, {'source_kind': 'command'},
    {'quantity': 'torque'}, {'calibration_status': 'unknown'},
    {'calibration_reference': ''}, {'delta_threshold': .3},
])
def test_provisional_policy_does_not_override_missing_or_conflicting_semantics(change):
    signals = pending(current_signals())
    signals.arms['right']['hand_load']['config'].update(change)
    features = PhysicalFeatures(signals, 20, evidence_policy='allow_provisional')
    assert np.isnan(features.grip_cur_rms['right']).all()
    assert features.window_limitations('right', 0, 199)


def test_confirmed_signals_unchanged_when_policy_enabled():
    signals = current_signals()
    strict = PhysicalFeatures(signals, 20)
    trial = PhysicalFeatures(signals, 20, evidence_policy='allow_provisional')
    assert not any(trial.assumptions.values())
    assert trial.assessment_limitations('right', 0, 199) == []
    for key in ('gripper', 'speed', 'cur_rms', 'cur_baseline', 'cur_std', 'grip_delta'):
        np.testing.assert_array_equal(getattr(trial, key)['right'], getattr(strict, key)['right'])


def test_explicit_torque_rules_keep_rms_and_rule_values_without_conversion(tmp_path):
    signals = current_signals()
    for roles in signals.arms.values():
        roles['hand_position']['values'] = np.r_[np.zeros(20), np.ones(80), np.zeros(100)][:, None]
        roles['hand_position']['openness'] = roles['hand_position']['values'][:, 0]
        for role in ('arm_load', 'hand_load'):
            roles[role]['config'].update(unit='N*m', quantity='torque')
    rules = {'unit': 'N*m', 'close_delta': .3, 'stagnation_empty_delta': .2}
    attr = EpisodeAttributor(str(tmp_path), 20, signals=signals, load_rules=rules)
    curve = [{'frame_index': i, 'progress': .5 if i < 100 else .2} for i in range(0, 200, 10)]
    result = attr.attribute([100], curve)['results']['100_right']
    assert result['primary_label'] == 'early_close_air_grasp'
    assert result['evidence']['close_load_delta'] == pytest.approx(.25)
    assert result['evidence']['load_rules'] == rules
    assert attr.gfeat.load_evidence['right']['arm_load']['quantity'] == 'torque'
    np.testing.assert_array_equal(attr.gfeat.cur_rms['right'], signals.arms['right']['arm_load']['values'][:, 0])
    assert not attr.gfeat.assessment_limitations('right', 0, 199)
    assert not attr.gfeat.proactive_inputs


@pytest.mark.parametrize('rules', [{'unit': 'N*m'}, {'unit': 'N*m', 'close_delta': .3},
                                  {'unit': 'N*m', 'stagnation_empty_delta': .2}])
def test_torque_cannot_inherit_current_defaults(rules):
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.load_rules import load_rules
    with pytest.raises(ValueError, match='explicit'):
        load_rules(rules)


def test_torque_input_cannot_use_legacy_current_scaling():
    signals = current_signals()
    signals.arms['right']['hand_load']['config'].update(unit='N*m', quantity='torque', delta_threshold=.3)
    features = PhysicalFeatures(signals, 20)
    assert np.isnan(features.grip_cur_rms['right']).all()
    assert features.limitations['right']


def test_worker_torque_attribution_cannot_enable_current_filter_rules(tmp_path, monkeypatch):
    from test_worker import prepared_request, install_double, Model
    req = prepared_request(tmp_path, calibrated=True)
    req['params'].update(filter_policy='training_filter_v8',
        load_rules={'unit': 'N*m', 'close_delta': .3, 'stagnation_empty_delta': .2})
    path = tmp_path / 'physical-signals.json'
    data = json.loads(path.read_text())
    for roles in data['arms'].values():
        for role in ('hand_load', 'arm_load'):
            cfg = roles[role]['config']; cfg.pop('delta_threshold')
            cfg.update(unit='N*m', quantity='torque')
    write_json(path, data)
    install_double(monkeypatch, Model())
    result = worker.execute(req)
    assert result['components']['physical']['status'] == 'completed'
    assert result['components']['filter']['status'] == 'incomplete'
    assert 'torque' in result['components']['filter']['reason']
    assert not result['assessment_complete']
    assert 'filter_suggestions' not in result


def test_robot_metadata_reference_survives_reader_and_si_conversion(tmp_path):
    from test_io_contract import signal_fixture
    from edc.data.ir.dataset import open_dataset
    from edc.data.ir.units import SIEpisode
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.algorithm import physical_inputs
    source, profile, params = signal_fixture(tmp_path)
    params['physical_signals']['right']['hand_position']['calibration_reference'] = 'Cannot override robot metadata'
    with open_dataset(source, profile=profile) as dataset:
        episode = SIEpisode(next(dataset.iter_episodes())); episode.open()
        signals = physical_inputs(episode, params, 'pose/arm/right/feedback')
    cfg = signals.arms['right']['hand_position']['config']
    assert cfg['calibration_reference'] == 'synthetic fixture'
    assert cfg['quantity'] == 'position'


@pytest.mark.parametrize('filter_policy', ['none', 'training_filter_v8'])
def test_worker_returns_conditional_labels_but_never_completion_or_filtering(tmp_path, monkeypatch, filter_policy):
    from test_worker import prepared_request, install_double, Model
    from types import SimpleNamespace
    req = prepared_request(tmp_path)
    req['params'].update(evidence_policy='allow_provisional', filter_policy=filter_policy)
    path = tmp_path / 'physical-signals.json'
    data = json.loads(path.read_text())
    pending(SimpleNamespace(arms=data['arms']))
    for roles in data['arms'].values():
        # Movement ensures the physical candidate reaches attribution rules.
        roles['ee_position']['values'] = np.column_stack([np.arange(40)*.02, np.zeros((40, 2))])
    write_json(path, data)
    class Regressing(Model):
        def inference_batch(self, samples):
            return [{'pred': f'<score>{90 if i == 0 else 10}</score>'} for i, _ in enumerate(samples)]
    install_double(monkeypatch, Regressing())
    out = worker.execute(req)
    assert out['components']['visual']['status'] == 'completed'
    assert out['components']['physical']['status'] == 'incomplete'
    assert out['components']['physical']['evidence_status'] == 'provisional'
    assert out['assessment_complete'] is False
    assert out['attribution']['assessment_complete'] is False
    assert out['attribution']['candidates']
    assert all(c['status'] == 'provisional' for c in out['attribution']['candidates'])
    assert 'filter_suggestions' not in out
    if filter_policy != 'none':
        assert out['components']['filter']['status'] == 'incomplete'
        assert not (tmp_path / 'native/training_filter_v8.json').exists()
    text = (tmp_path / 'native/attribution_report.txt').read_text()
    assert '条件归因' in text and '标定仍待确认' in text
    assert json.loads((tmp_path / 'native-result.json').read_text())['assessment_complete'] is False
