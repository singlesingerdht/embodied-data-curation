"""X1 Pro episode JSON (robot-bridge): one row time shared by all channels and videos (2026-10-31)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from edc.algorithms.quality.adapters.cpu import shared_row_intervals, short_pose_holds
from edc.algorithms.quality.adapters.numeric_supplements import joint_stuck
from edc.execution.batch import find_inputs
from edc.reporting.batch import cell, decision, explain

T0 = 1787281153.0  # 2026-08-21


def episode_dir(tmp_path, rows=31, frames=31, gap_at=None):
    cv2 = pytest.importorskip('cv2')
    path = tmp_path / 'ep@MASTER_SLAVE_MODE@2026_08_21_10_59_13'
    path.mkdir()
    times = T0 + np.arange(rows) / 30
    if gap_at is not None:
        times[gap_at:] += 1.0
    (path / 'ep.json').write_text(json.dumps({'fps': 30, 'data': [
        {'timestamp': float(t), 'follow_left_joint_position': [i * .01] * 7, 'follow_left_joint_velocity': [.3] * 7}
        for i, t in enumerate(times)]}))
    writer = cv2.VideoWriter(str(path / 'faceImg.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 30, (64, 48))
    if not writer.isOpened():
        pytest.skip('mp4 writer unavailable')
    for i in range(frames):
        writer.write(np.full((48, 64, 3), 40 + i, np.uint8))
    writer.release()
    profile = {'schema_version': 1, 'id': 'fixture', 'version': '1', 'timestamp_column': 'timestamp',
               'timestamp_unit': 's', 'video_frame_time': 'rows',
               'episode_column_groups': {'arm': {'position': 'follow_left_joint_position',
                                                 'velocity': 'follow_left_joint_velocity'}},
               'channels': {'arm': {'modality': 'other', 'fields': ['position', 'velocity'],
                                    'time_source': 'acquisition', 'clock': 'rows'},
                            'faceImg': {'modality': 'rgb', 'time_source': 'acquisition', 'clock': 'rows'}}}
    return path, profile, times


def test_column_group_and_video_frames_take_the_row_time(tmp_path):
    from edc import open_dataset
    path, profile, times = episode_dir(tmp_path)
    with next(open_dataset(path, profile=profile).iter_episodes()).opened() as ep:
        rows = ep.read_values('arm')
        assert rows[2]['position'] == [.02] * 7 and rows[2]['velocity'] == [.3] * 7
        video = ep.channels['faceImg']
        assert np.allclose(video.timestamps, times) and video.time_source == 'acquisition'
        assert 'frame i = trajectory row i' in video.time_evidence['video_frame_mapping']
    assert find_inputs([tmp_path]) == [path.resolve()]  # `edc batch` finds the episode folder.


def test_video_with_a_different_frame_count_keeps_fps_time(tmp_path):
    from edc import open_dataset
    path, profile, _ = episode_dir(tmp_path, frames=30)
    with next(open_dataset(path, profile=profile).iter_episodes()).opened() as ep:
        assert ep.channels['faceImg'].timestamps[0] == 0
        assert '30 frames but the trajectory has 31 rows' in ep.channels['faceImg'].time_evidence['unavailable_reason']


def shared(times):
    evidence = {'timeline_scope': 'shared_dataset_rows'}
    return SimpleNamespace(channels={'a': SimpleNamespace(name='a', timestamps=list(times), time_evidence=evidence),
                                     'cam': SimpleNamespace(name='cam', timestamps=list(times), time_evidence=evidence)})


def test_shared_row_intervals_pass_or_list_the_gaps():
    smooth = T0 + np.arange(90) / 30
    assert shared_row_intervals(shared(smooth), .1, {})['verdict'] == 'ACCEPTABLE'
    jumpy = smooth.copy(); jumpy[40:] += 1.5
    result = shared_row_intervals(shared(jumpy), .1, {})
    assert result['verdict'] == 'NEEDS_REVIEW' and result['shared_row_intervals']['event_count'] == 1
    text = explain('Check_Timestamp', result)
    assert '1 处空档' in text and '空 1.53 秒' in text
    c = cell('Check_Timestamp', [{'status': 'completed', 'result': {'reports': [result]}}])
    assert c['note'] == '共用一条时间，1 处跳帧' and c['shared_rows']


def arm_episode(q, tip):
    times = T0 + np.arange(len(q)) / 30
    joint = SimpleNamespace(name='joint/arm/left/feedback', robot_part='left', source_kind='feedback', count=len(q),
                            timestamps=list(times), time_source='acquisition', clock='rows', time_evidence={},
                            selection={}, source='s', source_mapping={}, modality='joint_space',
                            field_spec=lambda field: {})
    pose = SimpleNamespace(name='follow_left_position', robot_part='left', source_kind='feedback', count=len(q),
                           timestamps=list(times), modality='pose', value_available=True, fields=['position'])
    ep = SimpleNamespace(channels={joint.name: joint, pose.name: pose},
                         read_signal=lambda name, field: np.asarray(tip, float))
    return ep, joint


def test_joint_readings_that_stop_while_the_tip_moves_are_stuck():
    n = 600  # 20 s at 30 Hz: move 2 s, joints frozen 18 s while the tip keeps moving 5 cm/s.
    q = np.vstack([np.linspace(0, 1, 60)[:, None].repeat(6, 1), np.ones((n - 60, 6))])
    moving_tip = np.c_[np.linspace(0, 1.0, n), np.zeros(n), np.zeros(n)]
    ep, joint = arm_episode(q, moving_tip)
    check = joint_stuck(q, ep, joint, 'position', {'min_move_m': .02}, True, 5)
    assert check['verdict'] == 'DO_NOT_USE' and len(check['events']) == 1
    event = check['events'][0]
    assert event['pose_move_m'] > .8 and event['duration_s'] > 17 and event['pose_moving_s'] > 17 and event['reaches_end']
    assert joint_stuck(q, ep, joint, 'position', {'verdict': 'NEEDS_REVIEW'}, True, 5)['verdict'] == 'NEEDS_REVIEW'
    # Tip drifting slower than 2 cm/s is not real motion.
    slow = np.c_[np.linspace(0, .3, n), np.zeros(n), np.zeros(n)]
    ep, joint = arm_episode(q, slow)
    assert joint_stuck(q, ep, joint, 'position', {'min_move_m': .02}, True, 5)['verdict'] == 'ACCEPTABLE'
    # Same hold with the tip also still: a normal pause.
    still_tip = np.r_[moving_tip[:60], np.repeat(moving_tip[59:60], n - 60, 0)]
    ep, joint = arm_episode(q, still_tip)
    check = joint_stuck(q, ep, joint, 'position', {'min_move_m': .02}, True, 5)
    assert check['verdict'] == 'ACCEPTABLE' and check['observations'][0]['type'] == 'arm_hold'


def test_short_tip_hold_is_a_normal_pause():
    channel = SimpleNamespace(timestamps=list(T0 + np.arange(300) / 30), count=300)
    result = {'verdict': 'NEEDS_REVIEW', 'score': 70.0, 'numeric': {'pose_freeze': {'segments': [{'start': 10, 'length': 3}]}},
              'sub_indicators': [{'name': '姿态冻结', 'score': 70.0}]}
    out = short_pose_holds(result, channel, 5)
    assert out['verdict'] == 'ACCEPTABLE' and out['method_verdict'] == 'NEEDS_REVIEW'
    long = {**result, 'numeric': {'pose_freeze': {'segments': [{'start': 0, 'length': 200}]}}}
    assert short_pose_holds(long, channel, 5)['verdict'] == 'NEEDS_REVIEW'


def test_shared_row_format_has_no_capture_sync_to_check():
    na = {'worst': 'NA', 'note': '', 'problems': [], 'complete': True}
    ok = {'worst': 'ACCEPTABLE', 'complete': True}
    review = {'worst': 'NEEDS_REVIEW', 'complete': True}
    assert decision('unassessed', {'Sync_Check': na, 'Numeric_Check': ok}) == 'keep'
    assert decision('unassessed', {'Sync_Check': na, 'Reset_Detection': review}) == 'review'
    assert decision('unassessed', {'Sync_Check': na, 'Numeric_Check': {'worst': 'ACCEPTABLE', 'complete': False}}) == 'unassessed'


def test_reset_on_tip_position_reads_in_centimetres():
    item = {'verdict': 'NEEDS_REVIEW', 'calibration': {'unit': 'm', 'tolerance': .05},
            'evidence': [{'channel': 'follow_left_position', 'start_error': .168, 'end_error': .01,
                          'start_joint_index': 0, 'start_joint_offset': .168, 'start_time_s': T0}]}
    text = explain('Reset_Detection', item)
    assert text.startswith('左臂开头和初始位置不同（末端偏离 16.8 cm，x 方向差约 17 cm')


# ── 2026-10-31 morning: user decisions ──

def test_smoothness_can_score_only_the_truly_slow_frames():
    from edc.algorithms.quality.adapters.action import slow_frames_only
    jerk = {'score': 18.6, 'is_bad': True, 'assessment_complete': True,
            'details': {'threshold': 15, 'n_frames': 1000, 'stationary_frames': 970,
                        'raw_stationary_jerk_rms_m_s3': 5.4,
                        'stationary_segmentation': {'raw_stationary_frames': 700}}}
    slow = slow_frames_only(jerk)
    assert slow['score'] == 5.4 and not slow['is_bad'] and slow['details']['stationary_frames'] == 700
    assert slow['details']['method_jerk_score'] == 18.6 and jerk['score'] == 18.6  # Input left as it was.
    none = slow_frames_only({**jerk, 'details': {**jerk['details'], 'raw_stationary_jerk_rms_m_s3': None}})
    assert none['assessment_complete'] is False and none['details']['stationary_frames'] == 0
    text = explain('Action_Smoothness', {'score': 98.0, 'side': 'left', 'raw': {'jerk': slow},
                                         'sub_indicators': [{'name': '轨迹抖动', 't_max': 5.0, 't_min': 15.0}]})
    assert '只算真正慢下来的帧；按原算法补帧后是 18.6' in text and '抖动指标 5.4' in text


def test_stuck_joint_readings_are_salvageable_by_ik():
    from edc.reporting.batch import verdict_of
    item = {'verdict': 'ACCEPTABLE', 'supplemental_verdict': 'DO_NOT_USE',
            'supplemental_checks': [{'id': 'joint_stuck', 'verdict': 'DO_NOT_USE'},
                                    {'id': 'joint_velocity_limit', 'verdict': 'ACCEPTABLE'}]}
    assert verdict_of(item) == 'SALVAGE'
    c = cell('Numeric_Check', [{'status': 'completed', 'result': {'reports': [item]}}])
    assert c['worst'] == 'SALVAGE' and c['salvage'] == 'joint_ik'
    # A command out of its limits is a real exclusion, even when the joints also stuck.
    other = {**item, 'supplemental_checks': item['supplemental_checks'] + [{'id': 'command_limits', 'verdict': 'DO_NOT_USE'}]}
    assert verdict_of(other) == 'DO_NOT_USE'
    na = {'worst': 'NA', 'note': '', 'problems': [], 'complete': True}
    assert decision('unassessed', {'Sync_Check': na, 'Numeric_Check': {**c, 'complete': True}}) == 'salvage'
    assert decision('unassessed', {'Sync_Check': na, 'Numeric_Check': {**c, 'complete': True},
                                   'Video_Integrity': {'worst': 'DO_NOT_USE', 'complete': True}}) == 'exclude'


def test_velocity_limit_per_joint():
    from edc.algorithms.quality.adapters.numeric_supplements import joint_velocity_limit
    v = np.zeros((90, 6)); v[10:20, 0] = 4.0; v[30:40, 4] = 4.0  # j1 under its 4.188, j5 over its 3.77
    ep, joint = arm_episode(v, np.zeros((90, 3)))
    check = joint_velocity_limit(v, ep, joint, 'velocity', {'max_abs': [4.188] * 3 + [3.77] * 3}, True)
    assert check['verdict'] == 'NEEDS_REVIEW' and len(check['events']) == 1
    assert check['events'][0]['component_index'] == 4 and check['events'][0]['limit'] == 3.77
    assert check['threshold_text'] == '4.188/4.188/4.188/3.77/3.77/3.77'
    assert joint_velocity_limit(v, ep, joint, 'velocity', {'max_abs': 3.0}, True)['segments'] == 2  # One number still works.
    assert joint_velocity_limit(v, ep, joint, 'velocity', {'max_abs': [4.0] * 7}, True)['status'] == 'unconfigured'


def split_fixture(tmp_path, start):
    root = tmp_path / 'cook_noodles_split'
    ep = root / 'sprinkle_salt' / 'sprinkle_salt_f000810_001275@MASTER_SLAVE_MODE@2026_08_24_14_42_13'
    ep.mkdir(parents=True)
    (root / 'split_manifest.json').write_text(json.dumps({'segments': [
        {'output_episode': ep.name, 'source_episode': 'cook_noodles@MASTER_SLAVE_MODE@2026_08_24_14_42_13',
         'source_frame_range': [start, 1275]}]}))
    return ep


def reset_with(path, start_error, end_error, params):
    from edc.algorithms.quality.adapters.cpu import LegacyAlgorithm
    algo = LegacyAlgorithm('Reset_Detection', ('joint',), ())
    algo._reset_ends = lambda ep: {'method': 'calibrated_home_reference_v1', 'passed': False, 'verdict': 'NEEDS_REVIEW',
                                   'calibration': {'unit': 'm', 'tolerance': .05},
                                   'evidence': [{'channel': 'follow_left_position', 'start_error': start_error,
                                                 'end_error': end_error}]}
    return algo._reset(SimpleNamespace(path=str(path)), params)


def test_a_cut_out_segment_is_found_from_the_cutting_record(tmp_path):
    from edc.algorithms.quality.adapters.cpu import split_segment
    ep = split_fixture(tmp_path, 810)
    segment = split_segment(SimpleNamespace(path=str(ep)))
    assert segment['start_frame'] == 810 and segment['end_frame'] == 1275 and segment['record'].endswith('split_manifest.json')
    own = tmp_path / 'table_clean_x@MASTER_SLAVE_MODE@2026_07_10_11_12_45__pick_and_place_pen_00'
    (own / 'anno').mkdir(parents=True)
    (own / 'anno' / 'source_segment.json').write_text(json.dumps(
        {'source_episode': 'table_clean_x', 'source_start_frame': 456, 'source_end_frame': 1266}))
    assert split_segment(SimpleNamespace(path=str(own)))['start_frame'] == 456
    whole = tmp_path / 'table_clean' / 'table_clean_y@MASTER_SLAVE_MODE@2026_07_10_11_12_45'
    whole.mkdir(parents=True)
    assert split_segment(SimpleNamespace(path=str(whole))) is None  # No record: a whole recording.


def test_reset_on_a_segment_checks_only_real_recording_ends(tmp_path):
    from edc.reporting.batch import verdict_of
    middle = split_fixture(tmp_path / 'a', 810)
    result = reset_with(middle, .3, .3, {'split_segments': True})
    assert result['verdict'] == 'NOT_APPLICABLE' and result['checked_ends'] == [] and result['method_verdict'] == 'NEEDS_REVIEW'
    assert reset_with(middle, .3, .3, {})['verdict'] == 'NEEDS_REVIEW'  # Off unless the robot config asks for it.
    first = split_fixture(tmp_path / 'b', 0)
    assert reset_with(first, .01, .3, {'split_segments': True})['verdict'] == 'ACCEPTABLE'  # End not checked.
    assert reset_with(first, .3, .01, {'split_segments': True})['verdict'] == 'NEEDS_REVIEW'  # Start is the real start.
    assert verdict_of(result) == 'NA'
    c = cell('Reset_Detection', [{'status': 'completed', 'result': result}])
    assert c['worst'] == 'NA' and c['note'].startswith('切片')
    text = explain('Reset_Detection', reset_with(first, .3, .3, {'split_segments': True}))
    assert '开头' in text and '结尾' not in text


def test_signing_accepts_one_velocity_limit_per_joint():
    from edc.robots.numeric_review import numeric_settings
    per_joint = {'max_abs': [4.188, 4.188, 4.188, 3.77, 3.77, 3.77]}
    settings = numeric_settings({'numeric_check': {'thresholds': {'joint_velocity': per_joint}}})
    assert settings['thresholds']['joint_velocity'] == per_joint
    for bad in ({'max_abs': []}, {'max_abs': [4.0, 0]}, {'max_abs': [4.0, float('nan')]}, {'max_abs': [[4.0]]}):
        with pytest.raises(ValueError):
            numeric_settings({'numeric_check': {'thresholds': {'joint_velocity': bad}}})
