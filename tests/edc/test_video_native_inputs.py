"""Native-storage dispatch and exact-pixel copy elimination."""
from types import SimpleNamespace
import numpy as np
import pytest
from edc.execution.runner import Context
from edc.data.ir.dataset import Channel
from edc.algorithms.quality.adapters.cpu import builtins, preflight
from edc.algorithms.quality.adapters.video import assess, input_mode


def video_plugin():
    return next(p for p in builtins() if p.spec.id == 'Video_Integrity')


def episode(tmp_path, storage):
    channels = {n: Channel(n, 'rgb', [], 3, str(tmp_path/'source'), image_available=True) for n in storage}
    def frames(name):
        for i in range(3):
            yield {'index': i, 'image': np.full((31, 47, 3), i, dtype=np.uint8)}
    return SimpleNamespace(path=tmp_path/'source', channels=channels, image_storage=storage.__getitem__,
        iter_frames=frames, profile={}, selector={}, format='lerobot')


def forbidden(*args, **kwargs):
    raise AssertionError('Native-frame entry must not generate or probe video')


def test_catalog_exposes_per_channel_storage_without_materialization(tmp_path):
    from edc import channel_catalog
    ep = episode(tmp_path, {'image': 'frames', 'video': 'video', 'undeclared': 'unknown'})
    ep.id = 'mixed_storage_fixture'
    ep.open = lambda: None
    ep.materialize_video = ep.video_evidence = forbidden
    rows = {row['channel']: row for row in channel_catalog(ep)['channels']}
    assert {name: row['image_storage'] for name, row in rows.items()} == {
        'image': 'frames', 'video': 'video', 'undeclared': 'unknown'}
    del ep.image_storage
    assert all(row['image_storage'] == 'unknown' for row in channel_catalog(ep)['channels'])


def test_auto_frames_no_video_or_timeline_required(tmp_path, monkeypatch):
    import edc.algorithms.quality.adapters.cpu as cpu
    ep = episode(tmp_path, {'rgb': 'frames'})
    ep.materialize_video = ep.video_evidence = forbidden
    monkeypatch.setattr(cpu.shutil, 'which', lambda _: None)
    p = video_plugin()
    assert preflight(p, ep, {}) == ('runnable', '')
    assert p.availability(ep, {})['availability'] == 'partial'
    report = assess(ep, {'sample_step': 1, 'downscale': 4}, Context(None, ep, tmp_path))[0]
    assert report['input_mode'] == 'frames' and report['requested_input_mode'] == 'auto'
    assert report['frame_quality']['real_frames'] == 3
    assert report['score'] is None and report['assessment_complete'] is False
    assert report['container_checks']['status'] == 'not_applicable'
    assert not list(tmp_path.glob('*.mp4'))


def test_mixed_storage_routes_each_selected_channel(tmp_path, monkeypatch):
    from edc.algorithms.quality.algorithms.Video_Integrity import run_check
    ep = episode(tmp_path, {'image': 'frames', 'video': 'video'})
    calls = []
    def materialize(name, path):
        calls.append(name)
        assert name == 'video'
        return str(path)
    ep.materialize_video = materialize
    ep.video_evidence = lambda name, inspection_input: {'inspection_object': 'original_encoded_video'}
    monkeypatch.setattr(run_check, 'assess', lambda *a, **kw: {'score': 100, 'passed': True})
    rows = assess(ep, {'sample_step': 1}, Context(None, ep, tmp_path))
    assert [r['input_mode'] for r in rows] == ['frames', 'video']
    assert calls == ['video']
    assert rows[0]['inspection_object'] == 'source_decoded_frames'
    assert rows[1]['inspection_object'] == 'original_encoded_video'


def test_auto_video_keeps_executable_dependency_requirement(tmp_path, monkeypatch):
    import edc.algorithms.quality.adapters.cpu as cpu
    ep = episode(tmp_path, {'rgb': 'video'})
    ep.channels['rgb'].source = str(tmp_path/'original.mp4')
    monkeypatch.setattr(cpu.shutil, 'which', lambda _: None)
    status, reason = preflight(video_plugin(), ep, {})
    assert status == 'missing_dependency' and 'ffprobe' in reason


def test_auto_frames_reject_video_weights_and_unknown_storage(tmp_path):
    ep = episode(tmp_path, {'rgb': 'frames'})
    status, reason = preflight(video_plugin(), ep, {'weights': {'frame_quality': 1}})
    assert status == 'missing_input' and 'weights' in reason
    ep.image_storage = lambda _: 'unknown'
    status, reason = preflight(video_plugin(), ep, {})
    assert status == 'missing_input' and 'storage unknown' in reason
    assert input_mode(ep, ep.channels['rgb'], {'input_mode': 'frames'}) == 'frames'
    assert input_mode(ep, ep.channels['rgb'], {'input_mode': 'video'}) == 'video'
    del ep.image_storage
    assert preflight(video_plugin(), ep, {})[0] == 'missing_input'


@pytest.mark.parametrize('step', [1, 3])
@pytest.mark.parametrize('scale', [1, 4])
def test_rgb_view_matches_old_copy_for_exact_pixels(step, scale):
    import cv2
    from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector, _FrameCapture
    rng = np.random.default_rng(7)
    frames = [rng.integers(0, 256, (101, 131, 3), dtype=np.uint8) for _ in range(40)]
    original = [f.copy() for f in frames]
    class CopyCapture(_FrameCapture):
        def read(self):
            pixels = self._next()
            return (False, None) if pixels is None else (True, cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    make = lambda: ({'index': i, 'image': pixels} for i, pixels in enumerate(frames))
    detector = FrameQualityDetector(sample_step=step, downscale=scale)
    baseline = detector._detect_capture(CopyCapture(make()), len(frames), 'copy baseline')
    assert detector.detect_frames(make(), len(frames)) == baseline
    assert all(np.array_equal(a, b) for a, b in zip(original, frames))
    capture = _FrameCapture(make())
    _, bgr = capture.read()
    assert np.shares_memory(bgr, frames[0])
    capture.release()


def test_frame_generator_closed_on_count_error():
    from edc.algorithms.quality.algorithms.Video_Integrity.frame_quality import FrameQualityDetector
    closed = []
    def frames():
        try:
            yield {'index': 0, 'image': np.zeros((16, 16, 3), dtype=np.uint8)}
        finally:
            closed.append(True)
    with pytest.raises(ValueError, match='expected 2'):
        FrameQualityDetector().detect_frames(frames(), 2)
    assert closed == [True]


def test_reader_storage_declarations_do_not_materialize(tmp_path):
    from edc.data.readers.base import DataReader
    from edc.data.readers.lerobot_official import OfficialReader
    class Reader(DataReader):
        def open(self, path):
            return None
        materialize_image_channel = forbidden
    reader = Reader()
    reader.video_sources = {'encoded': ('shared.mp4', 1, 2)}
    reader.frame_loaders = {'embedded': forbidden}
    assert reader.image_storage('encoded') == 'video'
    assert reader.image_storage('embedded') == 'frames'
    assert reader.image_storage('absent') == 'unknown'
    official = OfficialReader()
    official.features = {'v': {'dtype': 'video'}, 'i': {'dtype': 'image'}, 'd': {'dtype': 'uint16'}}
    official.mapping = {k: {'feature': k} for k in official.features}
    assert [official.image_storage(k) for k in ('v', 'i', 'd')] == ['video', 'frames', 'unknown']


def test_public_hdf5_and_official_lerobot_image_paths_without_timestamps(tmp_path):
    import h5py
    from edc import open_dataset, run, convert_to_lerobot, recommend
    import json
    source = tmp_path/'images.h5'
    with h5py.File(source, 'w') as f:
        f['rgb'] = np.random.default_rng(8).integers(0, 256, (3, 24, 32, 3), dtype=np.uint8)
        f['raw_time'] = np.array([100, 101, 102], dtype=np.int64)
    original = source.read_bytes()
    profile = {'schema_version': 1, 'id': 'image_only', 'version': '1',
               'timestamp_column': 'raw_time', 'timestamp_unit': 'unknown',
               'channels': {'rgb': {'modality': 'rgb', 'logical_name': 'rgb/chest',
                   'representation': {'camera_role': 'chest', 'camera_role_status': 'confirmed'}}}}
    params = {'Video_Integrity': {'channels': ['rgb/chest'], 'sample_step': 1, 'downscale': 4}}
    def execute(path, profile, name):
        with open_dataset(path, profile=profile) as ds:
            ep = next(ds.iter_episodes()); ep.open()
            assert ep.image_storage('rgb') == 'frames'
            assert ep.channels['rgb'].timestamps is None
            row = next(r for r in recommend(ds, params=params)['episodes'][0]['algorithms']
                       if r['algorithm']['id'] == 'Video_Integrity')
            assert row['status'] == 'runnable' and row['availability'] == 'partial', row
            run(ds, output=tmp_path/name, algorithm_ids=['Video_Integrity'], params=params)
        record = json.loads(next((tmp_path/name/'_runs').glob('*/Video_Integrity/records.jsonl')).read_text())
        assert record['status'] == 'completed'
        report = record['result']['reports'][0]
        assert report['input_mode'] == 'frames' and report['frame_quality']['real_frames'] == 3
        assert not list((tmp_path/name).rglob('*.mp4'))
        return report['frame_quality']
    raw = execute(source, profile, 'hdf5-output')
    pytest.importorskip('lerobot')
    converted = tmp_path/'converted'
    convert_to_lerobot(source, profile=profile, output=converted, fps=30)
    assert execute(converted, None, 'official-output') == raw
    assert source.read_bytes() == original
