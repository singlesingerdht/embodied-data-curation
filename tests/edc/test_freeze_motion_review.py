"""Association is evidence only: static/occluded/out-of-view motion has no automatic verdict."""
from types import SimpleNamespace
import copy
import numpy as np
import pytest
from edc.algorithms.quality.algorithms.Video_Integrity.inputs import assess
from edc.algorithms.channel_names import resolve_channel_parameters, channel_parameter_bindings
from edc.data.ir.dataset import Channel
from edc.execution.runner import Context


def fixture(tmp_path, storage='frames', motion='moving', clock='common'):
    count = 180
    times = (np.arange(count)/30).tolist()
    camera = Channel('/camera', 'rgb', [], count, str(tmp_path/'source'), image_available=True,
        timestamps=times, time_source='acquisition', clock='common', logical_name='rgb/head')
    channel = Channel('/motion', 'joint_space', ['position'], count, str(tmp_path/'source'),
        value_available=True, source_kind='feedback', unit='rad', robot_part='left',
        timestamps=times, time_source='acquisition', clock=clock, logical_name='joint/arm/left/feedback')
    pixels = np.random.default_rng(5).integers(20, 230, (64, 80, 3), dtype=np.uint8)
    values = np.arange(count)[:,None]*.01 if motion != 'still' else np.ones((count,1))
    profile = {'parts': {'left': {'type': 'arm', 'side': 'left'}, 'right': {'type': 'arm', 'side': 'right'}}}
    def frames(name):
        assert name == '/camera'
        if storage == 'frames':
            for i in range(count):
                yield {'index': i, 'image': pixels.copy()}
        else:
            import cv2
            cap = cv2.VideoCapture(str(tmp_path/'source.mp4'))
            for i in range(count):
                ok, bgr = cap.read()
                assert ok
                yield {'index': i, 'image': bgr[:,:,::-1]}
            cap.release()
    ep = SimpleNamespace(id='synthetic', path=tmp_path/'source', selector={}, format='lerobot',
        channels={'/camera':camera, '/motion':channel}, profile=profile, iter_frames=frames,
        image_storage=lambda n: storage, read_signal=lambda n,f: values.copy())
    if storage == 'video':
        import cv2
        path = tmp_path/'source.mp4'
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 30, (80,64))
        assert writer.isOpened()
        for _ in range(count):
            writer.write(pixels[:,:,::-1])
        writer.release()
        ep.materialize_video = lambda *a: str(path)
        ep.video_evidence = lambda *a, **k: {'inspection_object': 'original_encoded_video'}
    return ep


def run_video(ep, tmp_path, params):
    from edc.algorithms.quality.adapters.selection import ChannelSelection
    context = Context(None, ep, tmp_path)
    if ep.image_storage('/camera') == 'video':
        context.materialize_video = ep.materialize_video
        context.video_evidence = ep.video_evidence
    scoped = ChannelSelection(ep, ['/camera'])
    return assess(scoped, params, context)[0]


@pytest.mark.parametrize('storage', ['frames', 'video'])
@pytest.mark.parametrize('motion', ['still', 'moving', 'outside_view'])
def test_samples_curves_and_scores_unchanged(tmp_path, storage, motion):
    ep = fixture(tmp_path, storage, motion)
    original = run_video(ep, tmp_path, {'sample_step': 1})
    params = resolve_channel_parameters(ep, {'sample_step': 1, 'arms': ['left'],
        'freeze_motion_review': {'rgb/head': ['joint/arm/left/feedback']}})
    result = run_video(ep, tmp_path, params)
    assert result['verdict'] == original['verdict']
    assert result['score'] == original['score']
    review = result['freeze_motion_review']
    assert review['status'] == 'available', review
    segment = review['segments'][0]
    assert segment['status'] == 'available' and segment['image_samples']
    signal = segment['motion'][0]
    assert signal['status'] == 'available'
    assert signal['component_range'][0] == (0 if motion == 'still' else pytest.approx(1.79))
    assert (tmp_path/signal['curve']).is_file()
    assert signal['source_rows'][0] == segment['camera_rows'][0]
    assert all((tmp_path/sample['path']).is_file() for sample in segment['image_samples'])
    assert len(list(tmp_path.glob('*.mp4'))) == (1 if storage == 'video' else 0)


@pytest.mark.parametrize('shortage', ['clock', 'missing', 'command', 'scope', 'unknown_unit'])
def test_missing_joint_evidence_retains_image_result(tmp_path, shortage):
    ep = fixture(tmp_path)
    if shortage == 'clock': ep.channels['/motion'].clock = None
    if shortage == 'command':
        ep.channels['/motion'].source_kind = 'command'
        ep.channels['/motion'].logical_name = None
    if shortage == 'scope': ep.channels['/motion'].robot_part = 'right'
    if shortage == 'unknown_unit': ep.channels['/motion'].unit = None
    if shortage == 'missing': del ep.channels['/motion']
    result = run_video(ep, tmp_path, {'sample_step': 1, 'arms': ['left'],
        'freeze_motion_review': {'/camera': ['/motion']}})
    assert result['assessment_complete']
    review = result['freeze_motion_review']
    assert review['status'] == 'unavailable'
    assert review['segments'][0]['motion'][0]['status'] == ('not_checked' if shortage == 'scope' else 'unavailable')


def test_explicit_mapping_alias_resolution_and_duplicate_rejection(tmp_path):
    ep = fixture(tmp_path, clock='robot')
    ep.channels['/motion'].timestamps = (np.arange(180)/30+2).tolist()
    ep.profile['transforms'] = {'clocks': {'map': {'source': 'robot', 'target': 'common', 'offset_seconds': -2}}}
    raw = {'sample_step': 1, 'freeze_motion_review': {'rgb/head': ['joint/arm/left/feedback']}}
    params = resolve_channel_parameters(ep, raw)
    assert len(channel_parameter_bindings(ep, raw)) == 2
    result = run_video(ep, tmp_path, params)
    assert result['freeze_motion_review']['status'] == 'available'
    assert result['freeze_motion_review']['segments'][0]['motion'][0]['clock_mapping']['mapping'] == 'map'
    with pytest.raises(ValueError, match='Duplicate'):
        resolve_channel_parameters(ep, {'freeze_motion_review': {'rgb/head': ['/motion', 'joint/arm/left/feedback']}})
    missing = resolve_channel_parameters(ep, {'freeze_motion_review': {'rgb/head': ['missing']}})
    assert missing['freeze_motion_review']['/camera'] == ['missing']
