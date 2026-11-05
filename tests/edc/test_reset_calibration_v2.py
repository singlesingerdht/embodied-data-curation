import json

import numpy as np

from edc import open_dataset, run
from edc.algorithms.quality.adapters.cpu import builtins, preflight
from edc.algorithms.quality.algorithms.Reset_Detection.algorithm import reset_calibration


def _profile(calibration):
    return {
        'schema_version': 1,
        'id': 'reset_v2_fixture',
        'version': '1',
        'parts': {'left': {'type': 'arm', 'side': 'left', 'dof': 6}},
        'channels': {
            'pose': {
                'modality': 'pose', 'fields': ['position'], 'side': 'left',
                'body_part': 'arm', 'robot_part': 'left', 'source_kind': 'feedback',
                'time_source': 'synthetic', 'logical_name': 'pose/arm/left/feedback',
                'field_metadata': {'position': {
                    'unit': 'm', 'quantity': 'position', 'semantic_status': 'confirmed',
                    'source_kind': 'feedback', 'calibration_status': 'unknown'}},
            }
        },
        'calibration': {'Reset_Detection': calibration},
    }


def _source(tmp_path, rows):
    path = tmp_path / 'episode'
    path.mkdir()
    (path / 'trajectory.json').write_text(json.dumps({'data': rows}))
    return path


def _calibration(home=(0.0, 0.0, 0.0)):
    return {
        'schema_version': 'edc_reset_calibration_v2',
        'source': {'source_robot_id': 'quanta_x1', 'confirmed_by': 'user'},
        'references': {'pose/arm/left/feedback': {
            'field': 'position', 'unit': 'm', 'home': list(home),
            'tolerance1': [0.01, 0.01, 0.01],
            'tolerance2': [0.02, 0.02, 0.02],
        }},
        'deferred': [{'parameter': 'left_arm.joint_pos', 'reason': 'dimension mapping pending'}],
    }


def test_reset_v2_resolves_logical_channel_and_keeps_deferred_items(tmp_path):
    path = _source(tmp_path, [
        {'pose': [0.0, 0.0, 0.0]},
        {'pose': [0.005, 0.0, 0.0]},
        {'pose': [0.0, 0.0, 0.0]},
    ])
    profile = _profile(_calibration())
    with open_dataset(path, profile=profile) as dataset:
        episode = next(dataset.iter_episodes())
        with episode.opened():
            plugin = next(p for p in builtins() if p.spec.id == 'Reset_Detection')
            assert preflight(plugin, episode)[0] == 'runnable'
            normalized = reset_calibration(episode)
            assert list(normalized['references']) == ['pose']
            result = plugin._reset(episode)
            assert result['method'] == 'calibrated_home_reference_v2'
            assert result['passed'] and result['score'] == 100.0
            assert result['evidence'][0]['requested_channel'] == 'pose/arm/left/feedback'
            assert result['deferred'][0]['parameter'] == 'left_arm.joint_pos'


def test_reset_v2_dimension_mismatch_is_needs_calibration(tmp_path):
    path = _source(tmp_path, [{'pose': [0.0, 0.0, 0.0]}, {'pose': [0.0, 0.0, 0.0]}])
    profile = _profile(_calibration(home=(0.0, 0.0, 0.0)))
    profile['calibration']['Reset_Detection']['references']['pose/arm/left/feedback']['home'] = [0.0] * 7
    with open_dataset(path, profile=profile) as dataset:
        result = run(dataset, algorithm_ids=['Reset_Detection'], output=tmp_path / 'out')
    assert result['status_counts'] == {'needs_calibration': 1}
