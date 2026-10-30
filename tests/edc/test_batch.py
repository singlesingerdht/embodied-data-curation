import pytest

from edc.execution.batch import find_inputs, resolve_robot
from edc.reporting.batch import cell, decision, page


def record(*reports, status='completed'):
    return {'status': status, 'algorithm': {'id': 'X'}, 'result': {'reports': list(reports)}}


def test_not_applicable_counts_as_pass_and_worst_wins():
    c = cell('Numeric_Check', [record({'verdict': 'NOT_APPLICABLE'}, {'verdict': 'ACCEPTABLE'},
                                      {'verdict': 'NEEDS_REVIEW', 'input_channel': 'joint/a', 'reasons': ['fast']})])
    assert c['worst'] == 'NEEDS_REVIEW'
    assert c['note'] == '3 项：通过2 复核1'
    assert c['problems'] == [['joint/a', 'NEEDS_REVIEW', 'fast']]
    assert cell('Dedup_Check', [record({'verdict': 'NOT_APPLICABLE'})])['worst'] == 'ACCEPTABLE'


def test_clock_reason_and_unfinished_record():
    text = '声明为同一时钟的话题实际分成 2 组：a；b。组间最多相差约 31.9 秒，疑似不同设备的时钟没有对准'
    c = cell('Sync_Check', [record({'verdict': 'DO_NOT_USE', 'reasons': ['score=0', text]})])
    assert c['note'] == '时钟差约 31.9 秒' and '没对准' in c['problems'][0][2]
    assert cell('Sync_Check', [record(status='processing_error')])['worst'] == 'missing'
    assert cell('Sync_Check', [])['worst'] == 'missing'


def test_frames_without_container_pass_and_timing_reasons():
    frames = {'verdict': 'OBSERVATION', 'inspection_object': 'source_decoded_frames', 'reasons': ['原始解码画面已检查']}
    c = cell('Video_Integrity', [record(frames, {'verdict': 'ACCEPTABLE'}, {'verdict': 'ACCEPTABLE'})])
    assert c['worst'] == 'ACCEPTABLE' and c['problems'] == [] and '无视频容器' in c['note']
    mean = {'verdict': 'NEEDS_REVIEW', 'reasons': ['reference topic 为 /cam。', '共评价 17 个 topic，算术平均分为 87.749。']}
    assert cell('Check_Timestamp', [record(mean)])['problems'][0][2] == '每一路都拿来和/cam对比打分，17 路平均 87.7 分（高于 90 通过，80–90 复核，80 及以下排除）。没有单独点名出问题的话题'
    assert cell('Check_Timestamp', [record(mean)])['problems'][0][2].startswith('每一路都拿来和/cam对比打分')
    rc = {'verdict': 'OBSERVATION', 'reasons': ['根因分析 score=83.6 < 85.0'], 'sub_indicators': [
        {'name': '跨通道Stall', 'score': 70.0, 'raw_value': '关联时间间隔异常: 4次'},
        {'name': '周期性丢帧', 'score': 100.0, 'raw_value': '未发现'},
        {'name': '时钟漂移', 'score': None}, {'name': '时钟跳变', 'score': None}]}
    reason = cell('Root_Cause', [record(rc)])['problems'][0][2]
    assert reason == '关联时间间隔异常: 4次（该子项 70 分）；时钟漂移、时钟跳变没有评估：没有拿到时钟稳定性数据'


def test_fixed_clock_offset_is_salvageable_and_review_items_say_where():
    clock = {'verdict': 'DO_NOT_USE', 'reasons': ['声明为同一时钟的话题实际分成 2 组：a；b。组间最多相差约 32.0 秒，疑似…'],
             'clock_diagnosis': {'salvage': {'status': 'fixed_offset'}}}
    sync = cell('Sync_Check', [record(clock)])
    assert sync['salvage'] == 'fixed_offset' and sync['note'] == '时钟差约 32.0 秒，可救'
    assert sync['worst'] == 'SALVAGE' and sync['problems'][0][1] == 'SALVAGE' and '已把时间挪齐' in sync['problems'][0][2]
    waiting = cell('Check_Timestamp', [record({'verdict': 'OBSERVATION', 'precondition': 'clock_fixed_offset'})])
    assert waiting['note'] == '等时钟平移后再评'
    blurry = cell('Video_Integrity', [record({'verdict': 'DO_NOT_USE', 'frame_quality': {'blur_ratio': .3}})])
    assert decision('exclude', {'Sync_Check': sync, 'Check_Timestamp': waiting}) == 'salvage'
    assert decision('exclude', {'Sync_Check': sync, 'Video_Integrity': blurry}) == 'exclude'
    unstable = {'verdict': 'DO_NOT_USE', 'clock_diagnosis': {'salvage': {'status': 'unstable'}},
                'reasons': ['声明为同一时钟的话题实际分成 2 组：a；b。组间最多相差约 31.9 秒，疑似…', '不是固定偏差，不能简单平移：/cam'],
                'sub_indicators': [{'name': '/arm', 'raw_value': 'score=0.0 | IoU=0.0'}, {'name': '/cam2', 'raw_value': 'IoU=0.99'}]}
    assert cell('Check_Timestamp', [record(unstable)])['problems'][0][2] == (
        '画面和动作配不上：相机和机器人本体两组设备的时钟没对准，相差约 31.9 秒，而且这个差在数据中途还在变，'
        '没法挪齐（详见采集同步）；结果有 1 路和头部相机在时间上完全不重合，记 0 分')
    shifted = cell('Check_Timestamp', [record({'verdict': 'DO_NOT_USE', 'clock_shift_s': {'/cam': -32.0}})])
    assert shifted['note'] == '已平移时钟后评价' and decision('exclude', {'Sync_Check': sync, 'Check_Timestamp': shifted}) == 'exclude'
    speed = {'verdict': 'NEEDS_REVIEW', 'review_candidates': [{'events': [
        {'type': 'joint_velocity_over_limit', 'component_index': 4, 'start_time_s': 1780889192.35,
         'duration_s': .28, 'peak_value': -4.52, 'limit': 3.0}]}]}
    assert cell('Numeric_Check', [record(speed)])['problems'][0][2] == \
        '1 段速度超过 3 rad/s：第 5 关节 11:26:32.35 起 0.28 秒，峰值 4.52'
    video = {'verdict': 'NEEDS_REVIEW', 'frame_quality': {'blur_ratio': .182, 'total_frames': 22,
                                                          'sampling': {'sample_step': 5}}}
    assert cell('Video_Integrity', [record(video)])['problems'][0][2] == '抽检 22 帧（每 5 帧抽 1）中：模糊 4 帧'
    timing = {'verdict': 'NEEDS_REVIEW', 'reasons': ['topic /arm：score=66.8，动作流采样不稳定',
                                                     'topic joint/arm：score=66.8，动作流采样不稳定'],
              'sub_indicators': [{'name': '/arm', 'raw_value': 'score=66.8 | P99=7.19ms | dt_cv=0.25'}],
              'cadence': {'channels': [{'channel': '/arm', 'min_interval_s': .0047, 'max_interval_s': .0228,
                                        'median_positive_interval_s': .0101}]}}
    assert cell('Check_Timestamp', [record(timing)])['problems'][0][2] == \
        '分数最低的：/arm和对照相机对比 67 分：本该每 10 毫秒发一条，实际间隔在 4.7 到 22.8 毫秒之间变化，最长一次隔了 22.8 毫秒（中间约少了 1 条）。'
    depth = {'verdict': 'NEEDS_REVIEW', 'reasons': ['reference topic 为 /cam。', 'topic /depth：score=0.000，采集时间范围或采样周期不可评价'],
             'sub_indicators': [{'name': '/depth', 'raw_value': 'score=0.0 | status=window_iou_failed | IoU=0.890528 | P99=0.00ms'}]}
    assert cell('Check_Timestamp', [record(depth)])['problems'][0][2] == \
        '分数最低的：/depth和/cam对比 0 分：/depth和/cam的录制时间段只重合 89%（算法要求至少 95%），不够就直接记 0 分，计入平均分。'


def test_video_passes_with_bad_frames_noted():
    ok = {'verdict': 'ACCEPTABLE', 'input_channel': '/cam_l/color/image_raw/compressed', 'inspection_object': 'source_decoded_frames',
          'frame_quality': {'blur_ratio': .092, 'total_frames': 173, 'sampling': {'sample_step': 5}}}
    c = cell('Video_Integrity', [record(ok)])
    assert c['worst'] == 'ACCEPTABLE' and c['problems'] == []
    assert c['note'].endswith('通过但有问题帧：cam_l 抽检 173 帧（每 5 帧抽 1）中：模糊 16 帧')


def test_reset_review_names_joint_and_time():
    reset = {'verdict': 'NEEDS_REVIEW', 'calibration': {'tolerance': 0.6}, 'evidence': [
        {'channel': 'joint/arm/right/feedback', 'start_error': 1.2, 'end_error': 0.3,
         'start_joint_index': 1, 'start_joint_offset': -1.2, 'start_time_s': 1780889192.35}]}
    assert cell('Reset_Detection', [record(reset)])['problems'][0][2] == \
        '右臂开头和初始位置不同（偏离 1.20 rad，第 2 关节差约 69°，时刻 11:26:32.35），疑似不是从初始位置开始录包'


def test_timing_sentence_uses_robot_names_and_says_whether_it_matters():
    item = {'verdict': 'DO_NOT_USE', 'reasons': ['reference topic 为 /cam。',
            'topic /arm：score=54.4，P99 尾部误差偏大；跨流抖动；动作流采样不稳定；动作流局部中断'],
            'sub_indicators': [{'name': '/arm', 'raw_value': 'score=54.4', 'details': [
                {'name': 'P99近邻误差', 'val': 8.92, 't_min': 3.7375, 't_max': 8.72}]}],
            'cadence': {'channels': [{'channel': '/arm', 'min_interval_s': .0014, 'max_interval_s': .0309,
                                      'median_positive_interval_s': .010},
                                     {'channel': '/cam', 'median_positive_interval_s': .0334}]}}
    text = cell('Check_Timestamp', [record(item)], {'/arm': '手臂指令', '/cam': '头部相机'})['problems'][0][2]
    assert text == ('分数最低的：手臂指令（/arm）和头部相机对比 54 分：本该每 10 毫秒发一条，实际间隔在 1.4 到 30.9 毫秒之间变化，'
                    '最长一次隔了 30.9 毫秒（中间约少了 2 条）；因此给画面配手臂指令时，最差的 1% 画面要差 8.9 毫秒'
                    '才能找到手臂指令（算法要求 3.7 毫秒以内才不扣分）。头部相机每 33 毫秒一帧，这点误差对配对影响很小。')


def test_root_cause_on_too_short_data_says_so():
    rc = {'verdict': 'NEEDS_REVIEW', 'short_input': {'seconds': 1.7, 'channel': '/cam', 'samples': 51, 'required': 100},
          'sub_indicators': [{'name': '跨通道Stall', 'score': 100.0}, {'name': '周期性丢帧', 'score': 100.0},
                             {'name': '时钟漂移', 'score': None}, {'name': '时钟跳变', 'score': None}]}
    assert cell('Root_Cause', [record(rc)])['problems'][0][2] == (
        '数据太短，时钟漂移、时钟跳变算不了：整条只有 1.7 秒，/cam 只有 51 个样本，算法要求每一路至少 100 个；'
        '另外两项（多路同时断档、周期性丢帧）正常')


def test_short_data_root_cause_pass_is_noted():
    rc = {'verdict': 'ACCEPTABLE', 'short_input': {'seconds': 1.7, 'channel': '/cam', 'samples': 51, 'required': 100},
          'clock_shift_s': {'/cam': -32.0}}
    c = cell('Root_Cause', [record(rc)])
    assert c['worst'] == 'ACCEPTABLE' and c['problems'] == [] and c['note'] == '已平移时钟后评价；数据只有 1.7 秒，漂移和跳变不需要查'


def test_too_short_data_is_excluded_by_structure_and_others_say_not_checked():
    short = {'seconds': 1.7, 'min_duration_s': 3.0}
    structure = cell('Structure_Check', [record({'verdict': 'DO_NOT_USE', 'too_short': short})])
    assert structure['problems'][0][2] == '数据太短：只有 1.7 秒，本机型要求至少 3 秒；其它 8 项不再检查'
    skipped = cell('Numeric_Check', [record({'verdict': 'NOT_APPLICABLE', 'skipped': 'too_short', 'duration': short})])
    assert skipped['worst'] == 'SKIPPED' and skipped['problems'] == []


def test_passing_item_with_freeze_candidates_shows_as_review_like_the_framework():
    item = {'verdict': 'ACCEPTABLE', 'input_channel': 'joint/arm/left/command', 'review_candidates': [
        {'check': 'cmd_freeze', 'needs_review': True, 'events': [
            {'type': 'constant_after_motion_candidate', 'component_index': 4, 'sample_count': 4,
             'start_time_s': 1780889192.35, 'duration_s': 0.03, 'needs_review': True}]}]}
    c = cell('Numeric_Check', [record(item)])
    assert c['worst'] == 'NEEDS_REVIEW'
    assert c['problems'][0][2] == '1 处“动过之后数值连续不变”（可能是冻结，也可能只是正常停住）：第 5 关节 11:26:32.35 起 30 毫秒（4 个样本）'


def test_page_renders_every_episode():
    bag = {'name': 'a.bag', 'decision': 'exclude', 'source': '/d/a.bag', 'seconds': 3, 'size_mb': 1, 'kept': None,
           'algos': {'Sync_Check': cell('Sync_Check', [record({'verdict': 'DO_NOT_USE', 'reasons': ['bad <x>']})])}}
    failed = {'name': 'b.bag', 'decision': 'failed', 'source': '/d/b.bag', 'seconds': 1, 'size_mb': 1,
              'kept': None, 'algos': {}, 'log': 'Traceback'}
    html = page([bag, failed], {'robot': 'r', 'inputs': '/d', 'jobs': 2, 'seconds': 4, 'finished': 'now',
                                'profile': 'p.yaml', 'params': ''})
    assert 'a.bag' in html and 'b.bag' in html and '运行失败' in html and 'bad &lt;x&gt;' in html


def test_inputs_and_robot(tmp_path):
    (tmp_path / 'x').mkdir()
    (tmp_path / 'x' / 'a.bag').write_bytes(b'')
    (tmp_path / 'b.bag').write_bytes(b'')
    assert [p.name for p in find_inputs([tmp_path])] == ['b.bag', 'a.bag']
    (tmp_path / 'y').mkdir()
    (tmp_path / 'y' / 'a.bag').write_bytes(b'')
    with pytest.raises(ValueError, match='同名'):
        find_inputs([tmp_path])
    profile, params = resolve_robot('leju_kuavo')
    assert profile.name == 'leju_kuavo_signed.yaml' and params.name == 'params.json'
    with pytest.raises(ValueError, match='找不到机器人配置'):
        resolve_robot('no_such_robot')
