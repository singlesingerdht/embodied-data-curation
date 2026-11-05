"""Run-local reuse preserves bytes, sidecars and evidence classification."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from edc.execution.runner import Context
from edc.data.ir.dataset import Channel
from edc.execution.media import MediaCache


def fixture_episode(tmp_path, classification='derived_preview'):
    source = tmp_path/'source.bin'
    source.write_bytes(b'encoded source fixture')
    c = Channel('rgb', 'rgb', [], 3, str(source), image_available=True)
    calls = []
    def materialize(name, target):
        calls.append(name)
        Path(target).write_bytes(b'exact encoded video fixture')
        Path(str(target)+'.frames.json').write_text('{"rows":[0,1,2]}')
        return str(target)
    ep = SimpleNamespace(id='fixture', path=source, selector={}, profile={}, channels={'rgb': c},
        materialize_video=materialize, video_evidence=lambda name, inspection_input: {
            'inspection_object': classification, 'source_evidence': {'inspection_input': str(inspection_input)}})
    return ep, calls


@pytest.mark.parametrize('classification', ['derived_preview','original_encoded_video'])
def test_same_input_materializes_once_and_preserves_evidence(tmp_path, classification):
    ep, calls = fixture_episode(tmp_path, classification)
    cache = MediaCache(tmp_path/'shared')
    contexts = []
    for name in ('visual','projection','video'):
        work = tmp_path/name; work.mkdir()
        ctx = Context(None, ep, work, cache)
        path = ctx.materialize_video('rgb', 'faceImg.mp4')
        assert Path(path).read_bytes() == b'exact encoded video fixture'
        assert json.loads(Path(str(path)+'.frames.json').read_text())['rows'] == [0,1,2]
        assert ctx.video_evidence('rgb', path)['inspection_object'] == classification
        assert ctx.video_evidence('rgb', path)['source_evidence']['inspection_input'] == path
        contexts.append(ctx)
    assert calls == ['rgb']
    rows = [json.loads((c.workdir/'media-inputs.json').read_text())[0] for c in contexts]
    assert [r['reused'] for r in rows] == [False, True, True]
    assert len(json.loads((cache.root/'manifest.json').read_text())['entries']) == 1


def test_configuration_or_selector_change_and_new_run_do_not_reuse(tmp_path):
    ep, calls = fixture_episode(tmp_path)
    cache = MediaCache(tmp_path/'shared')
    cache.obtain(ep, 'rgb')
    ep.selector = {'episode_index': 2}
    cache.obtain(ep, 'rgb')
    ep.profile = {'video_fps': 25}
    cache.obtain(ep, 'rgb')
    MediaCache(tmp_path/'another_run').obtain(ep, 'rgb')
    assert len(calls) == 4


def test_cache_does_not_escape_workdir_or_accept_missing_outputs(tmp_path):
    ep, calls = fixture_episode(tmp_path)
    work = tmp_path/'work'; work.mkdir()
    ctx = Context(None, ep, work, MediaCache(tmp_path/'shared'))
    with pytest.raises(ValueError, match='inside workdir'):
        ctx.materialize_video('rgb', '../bad.mp4')
    ep.materialize_video = lambda name, target: str(target)
    with pytest.raises(ValueError, match='did not produce'):
        ctx.materialize_video('rgb', 'missing.mp4')


def test_cpu_video_and_visual_share_one_materialization(tmp_path, monkeypatch):
    from edc.algorithms.quality.algorithms.Video_Integrity import inputs as video
    vision = pytest.importorskip('edc.algorithms.quality.adapters.vision', reason='GPU extension is outside CPU distribution')
    from edc.algorithms.quality.algorithms.Video_Integrity import method
    ep, calls = fixture_episode(tmp_path, 'original_encoded_video')
    ep.format = 'mp4'
    ep.channels['rgb'].representation = {'camera_role': 'head', 'camera_role_status': 'confirmed'}
    cache = MediaCache(tmp_path/'shared')
    work1 = tmp_path/'video'; work1.mkdir()
    work2 = tmp_path/'visual'; work2.mkdir()
    monkeypatch.setattr(method, 'assess', lambda *args, **kwargs: {'score': 100, 'passed': True})
    monkeypatch.setattr(vision, 'worker', lambda ctx, job, name: {'job': job})
    first = video.assess(ep, {'input_mode': 'video'}, Context(None, ep, work1, cache))[0]
    second = vision.VisualQuality().run(Context(None, ep, work2, cache), {})
    assert first['inspection_object'] == 'original_encoded_video'
    assert Path(second['job']['videos'][0]['path']).read_bytes() == b'exact encoded video fixture'
    assert calls == ['rgb']
