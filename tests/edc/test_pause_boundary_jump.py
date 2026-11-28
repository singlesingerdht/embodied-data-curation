"""子任务切换跳帧：相机空档前后两帧画面显著变化（frac>0.26）判不合格（图像法）。

另覆盖已弃用的末端位移数值法（_pause_boundary_pose_jumps，3cm）的边界行为，
该函数保留兼容但不再参与 assess_from_ir 评分。"""
from types import SimpleNamespace

import numpy as np
import pytest

from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import (
    DEFAULT_PAUSE_JUMP_THRESHOLD_M, _detect_pause_windows,
    _pause_boundary_pose_jumps, _pause_jump_reasons, assess_from_ir)


def timeline(rate_hz=30.0, samples=120, pause_index=60, pause_s=0.5):
    base = [i / rate_hz for i in range(samples)]
    return [t + pause_s if i >= pause_index else t for i, t in enumerate(base)]


def pose_topic(name, side, ts, source_kind='feedback', unit='m'):
    return SimpleNamespace(
        name=name, modality='pose', value_available=True, fields=['position'],
        msg_type='geometry_msgs/PoseStamped', count=len(ts), timestamps=ts,
        timestamps_synthetic=False, time_source='acquisition', source='',
        field_metadata={'position': {'source_kind': source_kind, 'unit': unit}},
        side=side, unit=unit, image_available=False, pose_layout=None)


def cam_topic(name, ts):
    return SimpleNamespace(
        name=name, modality='image', value_available=False, fields=None,
        msg_type='sensor_msgs/CompressedImage', count=len(ts), timestamps=ts,
        timestamps_synthetic=False, time_source='acquisition', source='',
        field_metadata={}, side='unknown', unit=None, image_available=True)


def joint_topic(name, ts):
    return SimpleNamespace(
        name=name, modality='joint_state', value_available=True, fields=['position'],
        msg_type='sensor_msgs/JointState', count=len(ts), timestamps=ts,
        timestamps_synthetic=False, time_source='acquisition', source='',
        field_metadata={}, side='unknown', unit=None, image_available=False)


class FakeReader:
    """require_timestamps + read_field + frames_at：够 assess_from_ir 和边界检查用。"""

    def __init__(self, topics, positions):
        self.topics = topics
        self.positions = positions
        self.frames = {}

    def require_timestamps(self, info, min_channels=2):
        return {n: list(t.timestamps) for n, t in self.topics.items()}

    def read_field(self, info, name, field):
        return self.positions.get(name)

    def frames_at(self, name, indices):
        src = self.frames.get(name, {})
        return {int(i): src[int(i)] for i in indices if int(i) in src}


def frame_pair(jump):
    """空档前后两帧：jump=True 画面全变（frac=1），False 画面不变（frac=0）。"""
    before = np.zeros((240, 320, 3), dtype=np.uint8)
    after = np.full((240, 320, 3), 255, dtype=np.uint8) if jump else before.copy()
    return before, after


def set_camera_jump(reader, jump, gap_index=59):
    b, a = frame_pair(jump)
    for cam in ('/cam0/rgb', '/cam1/rgb'):
        reader.frames[cam] = {gap_index: b, gap_index + 1: a}


def make_case(shift_m=0.0, shift_after=60, pose_kind='feedback', unit='m', with_pose=True):
    ts = timeline()
    topics = {'/cam0/rgb': cam_topic('/cam0/rgb', ts),
              '/cam1/rgb': cam_topic('/cam1/rgb', ts),
              '/joint_left': joint_topic('/joint_left', ts),
              '/joint_right': joint_topic('/joint_right', ts)}
    scale = {'m': 1.0, 'mm': 1000.0, 'cm': 100.0}[unit]
    positions = {}
    if with_pose:
        for name, side, base_x in (('/arm/left/pose', 'left', 0.3), ('/arm/right/pose', 'right', -0.3)):
            topics[name] = pose_topic(name, side, ts, pose_kind, unit)
            arr = np.zeros((len(ts), 3))
            arr[:, 0] = base_x
            if name == '/arm/left/pose':
                arr[shift_after:, 0] += shift_m * scale
            positions[name] = arr
    info = SimpleNamespace(topics=topics, format='mcap')
    return info, FakeReader(topics, positions), ts


def windows_of(ts_map):
    windows = _detect_pause_windows(ts_map)
    assert len(windows) == 1
    return windows


def test_stationary_pause_is_still_benign():
    info, reader, ts = make_case(shift_m=0.0)
    windows = windows_of(reader.require_timestamps(info))
    result = _pause_boundary_pose_jumps(info, reader, reader.require_timestamps(info), windows)
    assert result['status'] == 'checked' and result['jumps'] == []
    assert set(result['channels']) == {'/arm/left/pose', '/arm/right/pose'}


def test_jump_over_threshold_is_flagged():
    info, reader, ts = make_case(shift_m=0.05)
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert len(result['jumps']) == 1
    jump = result['jumps'][0]
    assert jump['channel'] == '/arm/left/pose' and jump['side'] == 'left'
    assert jump['delta_cm'] == pytest.approx(5.0, abs=0.01)
    reasons = _pause_jump_reasons(result)
    assert reasons and '子任务切换跳帧' in reasons[0] and '判不合格' in reasons[0]


def test_sub_centimetre_motion_stays_benign():
    info, reader, ts = make_case(shift_m=0.005)
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert result['jumps'] == []


def test_missing_pose_channel_reports_unchecked_not_failure():
    info, reader, ts = make_case(with_pose=False)
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert result['status'] == 'unchecked' and result['jumps'] == []
    assert '无末端位置' in result['reason']


def test_command_pose_is_not_trusted():
    info, reader, ts = make_case(shift_m=0.05, pose_kind='command')
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert result['status'] == 'unchecked' and result['jumps'] == []


def test_millimetre_units_are_converted():
    info, reader, ts = make_case(shift_m=0.05, unit='mm')
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert len(result['jumps']) == 1
    assert result['jumps'][0]['delta_cm'] == pytest.approx(5.0, abs=0.01)


def test_no_pause_windows_means_nothing_to_check():
    info, reader, ts = make_case(shift_m=0.05)
    result = _pause_boundary_pose_jumps(info, reader, reader.require_timestamps(info), [])
    assert result['status'] == 'checked' and result['jumps'] == []


def test_assess_from_ir_fails_the_episode_on_an_image_jump():
    info, reader, ts = make_case()
    set_camera_jump(reader, jump=True)
    report = assess_from_ir(info, reader)
    assert report['verdict'] == 'DO_NOT_USE' and report['passed'] is False
    assert report['score'] == 0.0
    assert '子任务切换跳帧' in report['reasons'][0]
    assert report['pause_boundary_jumps']['jumps']
    assert report['threshold_profile']['pause_boundary_jumps']['jumps']
    assert report['sub_indicators']


def test_assess_from_ir_keeps_a_stationary_pause_acceptable():
    info, reader, ts = make_case()
    set_camera_jump(reader, jump=False)
    report = assess_from_ir(info, reader)
    assert report['passed'] is True and report['verdict'] == 'ACCEPTABLE'
    assert report['pause_boundary_jumps']['status'] == 'checked'
    assert report['pause_boundary_jumps']['jumps'] == []
    assert report['threshold_profile']['benign_pause_windows']


def test_assess_from_ir_without_frame_data_is_unchecked_not_silent_pass():
    info, reader, ts = make_case()  # frames_at 返回空 → 无边界帧可比
    report = assess_from_ir(info, reader)
    assert report['passed'] is True  # 没有跳帧证据，不误判
    boundary = report['pause_boundary_jumps']
    assert boundary['jumps'] == []
    # 但不能装作检查过：如实记 unchecked，并列出缺失的空档
    assert boundary['status'] == 'unchecked'
    assert boundary['cameras'] == []
    assert any('缺失' in row['reason'] for row in boundary['unchecked_channels'])


def test_assess_from_ir_unchecked_when_decode_raises():
    info, reader, ts = make_case()

    def boom(name, indices):
        raise RuntimeError('decoder unavailable')
    reader.frames_at = boom
    report = assess_from_ir(info, reader)
    assert report['passed'] is True
    assert report['pause_boundary_jumps']['status'] == 'unchecked'


def test_threshold_default_is_three_centimetres():
    assert DEFAULT_PAUSE_JUMP_THRESHOLD_M == 0.03


def test_small_drift_under_three_centimetres_stays_benign():
    """1–2.6cm 的停顿慢漂经人工质检确认不构成可见跳帧，不标记。"""
    info, reader, ts = make_case(shift_m=0.025)
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert result['jumps'] == []


def test_image_threshold_is_configurable_per_call():
    info, reader, ts = make_case()
    set_camera_jump(reader, jump=True)  # frac=1.0
    strict = assess_from_ir(info, reader)
    relaxed = assess_from_ir(info, reader, pause_jump_image_frac=1.0)
    assert strict['verdict'] == 'DO_NOT_USE'
    assert relaxed['passed'] is True and relaxed['verdict'] == 'ACCEPTABLE'
    assert relaxed['pause_boundary_jumps']['threshold_frac'] == 1.0
    assert relaxed['pause_boundary_jumps']['jumps'] == []


def test_invalid_image_threshold_is_rejected():
    info, reader, ts = make_case()
    with pytest.raises(ValueError):
        assess_from_ir(info, reader, pause_jump_image_frac=0.0)
    with pytest.raises(ValueError):
        assess_from_ir(info, reader, pause_jump_image_frac=1.5)


def test_image_jump_default_threshold():
    from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import DEFAULT_PAUSE_JUMP_IMAGE_FRAC
    assert DEFAULT_PAUSE_JUMP_IMAGE_FRAC == 0.26


def test_image_jump_unit_detects_camera_gap_change():
    from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import _pause_boundary_image_jumps
    info, reader, ts = make_case()
    set_camera_jump(reader, jump=True)
    res = _pause_boundary_image_jumps(info, reader, reader.require_timestamps(info))
    assert res['status'] == 'checked' and len(res['jumps']) >= 1
    assert res['jumps'][0]['frac'] == 1.0
    set_camera_jump(reader, jump=False)
    res2 = _pause_boundary_image_jumps(info, reader, reader.require_timestamps(info))
    assert res2['jumps'] == []


def test_undeclared_unit_is_unchecked_not_assumed_metres():
    info, reader, ts = make_case(shift_m=0.05)
    for name in ('/arm/left/pose', '/arm/right/pose'):
        info.topics[name].field_metadata['position'].pop('unit')
        info.topics[name].unit = None
    ts_map = reader.require_timestamps(info)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert result['status'] == 'unchecked' and result['jumps'] == []
    assert all('未声明单位' in row['reason'] for row in result['unchecked_channels'])


def shifted_case(shift_s=0.2, shift_m=0.05):
    """固定时钟偏差场景：pose 通道原始时间比评分时间轴慢 shift_s，且作为派生副本被评分排除。"""
    from edc.algorithms.quality.adapters.cpu import ShiftedReader
    info, inner, ts = make_case(shift_m=shift_m)
    pose_names = ['/arm/left/pose', '/arm/right/pose']
    for name in pose_names:
        info.topics[name].timestamps = [t - shift_s for t in ts]  # 未平移的原始时间戳
    shift = {n: shift_s for n in pose_names}
    reader = ShiftedReader(inner, shift, drop=pose_names)
    ts_map = reader.require_timestamps(info)  # 派生 pose 被排除，只剩相机/关节
    assert not any(n in ts_map for n in pose_names)
    return info, reader, ts_map


def test_derived_pose_uses_shifted_timestamps_not_raw():
    info, reader, ts_map = shifted_case()
    windows = windows_of(ts_map)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows)
    assert result['status'] == 'checked'
    assert len(result['jumps']) == 1
    assert result['jumps'][0]['delta_cm'] == pytest.approx(5.0, abs=0.01)
    # 边界时刻取的是平移后的时间轴，与停顿窗口同轴
    assert result['jumps'][0]['start_time'] == windows[0]['start_time']


def test_derived_pose_without_shifted_source_is_unchecked_not_wrong():
    info, inner, ts = make_case(shift_m=0.05)
    pose_names = ['/arm/left/pose', '/arm/right/pose']
    for name in pose_names:
        info.topics[name].timestamps = [t - 0.2 for t in ts]
    ts_map = {n: t.timestamps for n, t in info.topics.items() if n not in pose_names}
    # inner 没有 shifted_timestamps：宁可 unchecked，也不能拿未平移时间轴算错下标
    result = _pause_boundary_pose_jumps(info, inner, ts_map, windows_of(ts_map))
    assert result['jumps'] == [] and result['status'] == 'unchecked'
    assert len(result['unchecked_channels']) == 2


def test_synthetic_pose_channel_is_not_resurrected():
    info, reader, ts_map = shifted_case()
    info.topics['/arm/left/pose'].timestamps_synthetic = True
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows_of(ts_map))
    assert '/arm/left/pose' not in result['channels']
    assert any(row['channel'] == '/arm/left/pose' and '合成时间' in row['reason']
               for row in result['unchecked_channels'])


def test_arm_moving_while_camera_keeps_recording_is_not_a_jump():
    """相机一直在录（无空档），臂就算动了画面也连续，不算跳帧——大位移误报的根源。"""
    info, reader, ts = make_case(shift_m=0.20)  # 臂动 20cm
    # 相机时间轴抹平停顿：相机连续录制，没有空档
    flat = [i / 30.0 for i in range(120)]
    for n in ('/cam0/rgb', '/cam1/rgb'):
        info.topics[n].timestamps = flat
    ts_map = reader.require_timestamps(info)
    # 停顿窗口仍由关节通道触发
    windows = _detect_pause_windows(ts_map)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows)
    assert result['jumps'] == []


def test_camera_gap_narrower_than_arm_gap_is_not_trusted():
    """相机空档远窄于臂空档：臂的大部分运动被相机录下，画面连续，不采信位移。"""
    info, reader, ts = make_case(shift_m=0.05)
    # 臂在 index 60 停录 2s，但相机只停 0.3s（相机早恢复，录下了后续运动）
    arm_ts = timeline(pause_index=60, pause_s=2.0)
    cam_ts = timeline(pause_index=60, pause_s=0.3)
    for n in ('/arm/left/pose', '/arm/right/pose'):
        info.topics[n].timestamps = arm_ts
    for n in ('/cam0/rgb', '/cam1/rgb', '/joint_left', '/joint_right'):
        info.topics[n].timestamps = cam_ts
    ts_map = reader.require_timestamps(info)
    windows = _detect_pause_windows(ts_map)
    result = _pause_boundary_pose_jumps(info, reader, ts_map, windows)
    assert result['jumps'] == []


# ── 解码鲁棒性与覆盖率如实记录（2026-11-28 review 修复）──

def test_missing_one_boundary_frame_surfaces_unchecked():
    """一路缺边界帧要记 unchecked，不能静默跳过还标 checked；另一路照常检查。"""
    from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import _pause_boundary_image_jumps
    info, reader, ts = make_case()
    set_camera_jump(reader, jump=False)
    del reader.frames['/cam0/rgb'][60]  # cam0 缺空档后一帧
    res = _pause_boundary_image_jumps(info, reader, reader.require_timestamps(info))
    assert res['status'] == 'checked'  # cam1 完成了比较
    assert '/cam1/rgb' in res['cameras'] and '/cam0/rgb' not in res['cameras']
    assert any(row['channel'] == '/cam0/rgb' and '缺失' in row['reason']
               for row in res['unchecked_channels'])


def test_mono8_two_dimensional_frames_are_compared():
    """ROS mono8 解出二维灰度图，不能因固定 RGB2GRAY 崩掉。"""
    from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import _frame_diff_metrics
    a = np.zeros((240, 320), dtype=np.uint8)
    b = np.full((240, 320), 255, dtype=np.uint8)
    mad, frac = _frame_diff_metrics(a, b)
    assert mad == 255.0 and frac == 1.0


def test_non_uint8_frames_are_unchecked_not_crash():
    """16bit 深度等非 8bit 帧：记 unchecked，不中断整条 Check_Timestamp。"""
    info, reader, ts = make_case()
    b, a = frame_pair(False)
    for cam in ('/cam0/rgb', '/cam1/rgb'):
        reader.frames[cam] = {59: b.astype(np.uint16), 60: a.astype(np.uint16)}
    report = assess_from_ir(info, reader)
    assert report['passed'] is True
    boundary = report['pause_boundary_jumps']
    assert boundary['status'] == 'unchecked' and boundary['jumps'] == []
    assert any('不可比较' in row['reason'] for row in boundary['unchecked_channels'])


def test_image_check_failure_never_blocks_timestamp_assessment():
    """图像检查自身任何异常都只跳过自己（unchecked），绝不掀翻时间戳评估主流程。"""
    import edc.algorithms.quality.algorithms.Check_Timestamp.run_check as rc
    info, reader, ts = make_case()
    original = rc._pause_boundary_image_jumps

    def broken(*a, **kw):
        raise RuntimeError('unsupported input format')
    rc._pause_boundary_image_jumps = broken
    try:
        report = rc.assess_from_ir(info, reader)
    finally:
        rc._pause_boundary_image_jumps = original
    assert report['passed'] is True and report['verdict'] == 'ACCEPTABLE'
    assert report['score'] > 0
    boundary = report['pause_boundary_jumps']
    assert boundary['status'] == 'unchecked' and boundary['jumps'] == []
    assert '已跳过' in boundary['reason']
    assert report['threshold_profile']['pause_boundary_jumps']['status'] == 'unchecked'


def test_depth_channels_do_not_join_visual_jump_check():
    """depth 通道被 classify_channel 归为 image，但灰度差阈值对深度值无意义，应排除。"""
    from edc.algorithms.quality.algorithms.Check_Timestamp.run_check import _pause_boundary_image_jumps
    info, reader, ts = make_case()
    set_camera_jump(reader, jump=False)
    info.topics['/cam0/depth/image'] = cam_topic('/cam0/depth/image', ts)
    res = _pause_boundary_image_jumps(info, reader, reader.require_timestamps(info))
    assert '/cam0/depth/image' not in res['cameras']
    assert all(row['channel'] != '/cam0/depth/image' for row in res['unchecked_channels'])


class _DelayedFakeDecoder:
    """模拟解码器延迟输出：喂第 n 个包时吐出第 n-delay 个包的帧；flush 吐剩余的。"""

    def __init__(self, delay=1):
        self.delay = delay
        self.queue = []

    def decode(self, pkt):
        if pkt is None:
            out = [self._frame(p) for p in self.queue]
            self.queue = []
            return out
        self.queue.append(pkt.pts)
        if len(self.queue) > self.delay:
            return [self._frame(self.queue.pop(0))]
        return []

    @staticmethod
    def _frame(pts):
        arr = np.full((4, 4, 3), pts, dtype=np.uint8)
        return SimpleNamespace(pts=pts, to_ndarray=lambda format: arr)


def _fake_video_reader(n_packets=20):
    from edc.data.readers.mcap import McapReader
    reader = McapReader.__new__(McapReader)  # 绕过 mcap 依赖；frames_at 只用以下属性
    reader._path = 'fake.mcap'
    reader._protobuf_classes = {}
    reader._topics_cache = {'/cam': SimpleNamespace(image_available=True)}

    def iter_msgs(name):
        for i in range(n_packets):
            nal = 5 if i % 10 == 0 else 1  # 每 10 包一个 IDR
            data = b'\x00\x00\x00\x01' + bytes([nal]) + b'payload'
            yield 0.0, SimpleNamespace(data=data, format='h264'), 'foxglove_msgs/msg/CompressedVideo'
    reader._iter_ros_messages = iter_msgs
    return reader


def test_frames_at_attributes_delayed_frames_by_pts():
    """B 帧/解码延迟下输出帧必须按 pts 归属喂包索引，flush 帧也要收集。

    旧实现按"喂包时吐帧"记索引：目标帧延迟一包输出即缺失，flush 帧被丢弃。"""
    import sys
    from types import ModuleType
    fake_av = ModuleType('av')

    class FakePacket:
        def __init__(self, data):
            self.data = data
            self.pts = None
            self.dts = None
    fake_av.Packet = FakePacket
    fake_av.CodecContext = SimpleNamespace(create=lambda fmt, mode: _DelayedFakeDecoder(delay=1))
    reader = _fake_video_reader(n_packets=20)
    old = sys.modules.get('av')
    sys.modules['av'] = fake_av
    try:
        out = reader.frames_at('/cam', [5, 15, 19])  # 19 是最后一包，其帧只能 flush 出来
    finally:
        if old is None:
            sys.modules.pop('av', None)
        else:
            sys.modules['av'] = old
    assert set(out) == {5, 15, 19}
    for idx in (5, 15, 19):
        assert int(out[idx][0, 0, 0]) == idx


def test_frames_at_real_h264_bframes_match_presentation_order():
    """真 libx264 编码（含 B 帧重排）回环：frames_at 按包索引取帧须与呈现序参考一致。

    需要 av（PyAV wheel 自带 libx264）；无 av 的环境跳过，在 WSL/完整环境执行。"""
    try:
        import av
    except ImportError:
        return
    from fractions import Fraction
    from edc.data.readers.mcap import McapReader
    try:
        enc = av.CodecContext.create('libx264', 'w')
    except Exception:
        return  # 构建不含 libx264
    enc.width, enc.height, enc.pix_fmt = 64, 48, 'yuv420p'
    enc.time_base = Fraction(1, 30)
    enc.options = {'g': '10', 'bframes': '2', 'profile': 'main'}
    packets = []
    for i in range(40):
        arr = np.zeros((48, 64, 3), dtype=np.uint8)
        arr[:, :, 0] = i * 6
        arr[:, :, 1] = (i * 13) % 256
        arr[i % 48, :, 2] = 255
        frame = av.VideoFrame.from_ndarray(arr, format='rgb24')
        frame.pts = i
        packets.extend(enc.encode(frame))
    packets.extend(enc.encode(None))
    assert len(packets) == 40
    assert any(p.pts != p.dts for p in packets), '编码未产生 B 帧重排，测试未覆盖目标路径'
    # 参考：按编码器原始 pts/dts 喂包 → 呈现索引的帧
    ref = {}
    dec = av.CodecContext.create('h264', 'r')
    for p in packets:
        pkt = av.Packet(bytes(p))
        pkt.pts, pkt.dts = p.pts, p.dts
        for fr in dec.decode(pkt):
            ref[fr.pts] = fr.to_ndarray(format='rgb24')
    for fr in dec.decode(None):
        ref[fr.pts] = fr.to_ndarray(format='rgb24')
    assert len(ref) == 40
    # frames_at：包按解码序（MCAP 到达序）喂入，按包索引归属
    reader = McapReader.__new__(McapReader)
    reader._path = 'fake.mcap'
    reader._protobuf_classes = {}
    reader._topics_cache = {'/cam': SimpleNamespace(image_available=True)}
    raw = [bytes(p) for p in packets]
    pts_of_idx = [p.pts for p in packets]

    def iter_msgs(name):
        for data in raw:
            yield 0.0, SimpleNamespace(data=data, format='h264'), 'foxglove_msgs/msg/CompressedVideo'
    reader._iter_ros_messages = iter_msgs
    want = [0, 7, 13, 25, 39]
    out = reader.frames_at('/cam', want)
    assert set(out) == set(want)
    for idx in want:
        assert np.array_equal(out[idx], ref[pts_of_idx[idx]]), f'帧 {idx} 与呈现序参考不一致'