"""Current inputs need fixed rules, not an extra adapter-scale calibration."""
import copy

import numpy as np
import pytest

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.algorithm import physical_inputs
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import WindowAnalyzer
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.load_rules import (
    load_rules, validate_load_configuration,
)
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.physical_features import PhysicalFeatures
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.signals import PhysicalSignals


def current_signals():
    n = 200
    grip = np.r_[np.zeros(50), np.ones(50), np.zeros(100)]
    current = np.r_[np.full(100, .5), np.full(100, .75)]
    values = {
        'hand_position': ('rad', grip[:, None]),
        'hand_load': ('A', current[:, None]),
        'arm_load': ('A', np.column_stack([current, current])),
        'ee_position': ('m', np.column_stack([np.arange(n)*.01, np.zeros((n, 2))])),
    }
    entries = {role: {'config': {'unit': unit, 'calibration_status': 'confirmed'},
                      'values': array, 'description': {'signal_state': 'varying'}}
               for role, (unit, array) in values.items()}
    entries['hand_position']['openness'] = grip
    return PhysicalSignals('synthetic', np.arange(n)/20, {'left': copy.deepcopy(entries), 'right': entries}, [])


def test_current_preserved_and_statistics_estimated_without_adapter_threshold():
    signals = current_signals()
    features = PhysicalFeatures(signals, 20)
    assert not features.limitations['right']
    expected = signals.arms['right']['hand_load']['values'][:, 0]
    np.testing.assert_array_equal(features.grip_cur_rms['right'], expected)
    assert features.cur_baseline['right'] == pytest.approx(np.median(expected))
    assert features.cur_std['right'] == pytest.approx(np.std(expected))
    assert features.cur_spike_threshold['right'] == pytest.approx(np.median(expected) + 1.5*np.std(expected))


def test_fixed_threshold_changes_decision_without_rescaling_evidence():
    signals = current_signals()
    outcomes = []
    for threshold in (.3, .1):
        features = PhysicalFeatures(signals, 20, load_rules={'close_delta': threshold})
        analyzer = WindowAnalyzer(features, {'left': [], 'right': [(100, 'close')]},
                                  [{'frame_index': i, 'progress': .5 if i < 100 else .2}
                                   for i in range(0, 200, 10)], 20)
        result = analyzer.attribute(100, 'right')
        assert result.raw_evidence['close_load_delta'] == pytest.approx(.25)
        assert result.raw_evidence['load_sources']['hand_load']['rule_unit'] == 'A'
        assert result.raw_evidence['load_rules']['close_delta'] == threshold
        outcomes.append(result.primary_label)
    assert outcomes[0] == 'early_close_air_grasp'
    assert outcomes[1] != 'early_close_air_grasp'


@pytest.mark.parametrize('unit,calibration', [('unknown', 'confirmed'), ('N*m', 'confirmed'), ('A', 'pending')])
def test_unknown_or_torque_signals_cannot_become_current(unit, calibration):
    signals = current_signals()
    signals.arms['right']['hand_load']['config'].update(unit=unit, calibration_status=calibration)
    features = PhysicalFeatures(signals, 20)
    assert features.limitations['right']
    assert np.isnan(features.grip_cur_rms['right']).all()


def test_legacy_explicit_scale_preserved_and_cannot_mix_with_current_rules():
    signals = current_signals()
    for role in ('hand_load', 'arm_load'):
        signals.arms['right'][role]['config']['delta_threshold'] = .6
    features = PhysicalFeatures(signals, 20)
    assert features.grip_cur_rms['right'] == pytest.approx(signals.arms['right']['hand_load']['values'][:, 0]*.5)
    assert features.load_evidence['right']['hand_load']['rule_unit'] == 'legacy_normalized'
    with pytest.raises(ValueError, match='cannot be combined'):
        PhysicalFeatures(signals, 20, load_rules={})
    with pytest.raises(ValueError, match='cannot be combined'):
        validate_load_configuration({'load_rules': {}, 'physical_signals': {
            'right': {'arm_load': {'delta_threshold': .6}}}})


@pytest.mark.parametrize('config', [
    {'close_delta': 0}, {'close_delta': float('nan')}, {'stagnation_empty_delta': -1},
    {'close_delta': True}, {'unit': 'N*m'}, {'baseline': 1.0}, {'std': .1}, [],
])
def test_invalid_or_estimated_rule_parameters_rejected(config):
    with pytest.raises(ValueError):
        load_rules(config)


def test_robot_reader_to_current_features_without_delta_threshold(tmp_path):
    from test_io_contract import signal_fixture
    from edc.data.ir.dataset import open_dataset
    from edc.data.ir.units import SIEpisode
    source, profile, params = signal_fixture(tmp_path)
    del params['physical_signals']['right']['arm_load']['delta_threshold']
    with open_dataset(source, profile=profile) as dataset:
        episode = SIEpisode(next(dataset.iter_episodes())); episode.open()
        signals = physical_inputs(episode, params, 'pose/arm/right/feedback')
    assert not signals.warnings
    features = PhysicalFeatures(signals, 10)
    assert not features.limitations['right']
    np.testing.assert_allclose(features.grip_cur_rms['right'], np.sqrt(2.5))
