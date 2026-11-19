"""Depth is bound to actual ordered RGB frames before any safety model starts."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import algorithm
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.depth_evidence import validate_correspondence
from edc.algorithms.value.runtime import fingerprint
from edc.execution.runner import Context
from edc.reporting.results import write_json


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / 'raw'; source.mkdir()
    rgb = np.stack([np.full((8, 8, 3), i * 40, np.uint8) for i in range(4)])
    ep = NS(id='synthetic', path=source, selector={'episode_index': 3}, format='episode',
            open=lambda: None, image_storage=lambda _: 'frames')
    ep.channels = {'high': NS(modality='rgb', image_available=True, count=4, clock='recorded',
        time_source='acquisition', timestamps=np.array([1., 1.1, 1.3, 1.5]),
        source_mapping={}, source=str(source), time_evidence={})}
    ep.iter_frames = lambda _: iter({'index': i, 'source_index': 10+i, 'image': im}
                                   for i, im in enumerate(rgb))
    path = tmp_path/'depth.npy'; np.save(path, np.ones((2, 8, 8), np.float32))
    evidence_path = tmp_path/'provenance.json'
    evidence = {'status': 'completed', 'episode_id': ep.id, 'source_episode': str(source),
        'selector': ep.selector, 'channel': 'high', 'frame_range': [1, 3],
        'source': 'monocular_estimate', 'unit': 'm', 'registered_to_rgb': True,
        'registration_id': 'synthetic-registration', 'depth': fingerprint(path),
        'frames': [{'reference_index': i, 'source_index': i+10, 'rgb_shape': [8, 8],
                    'rgb_sha256': hashlib.sha256(rgb[i].tobytes()).hexdigest()} for i in [1, 2]]}
    write_json(evidence_path, evidence)
    params = {'views': {'faceImg': 'high'}, 'frame_range': [1, 3], 'models': {},
        'task_graph': str(tmp_path/'task.json'), 'arms': {'left': {}}, 'intrinsics': np.eye(3).tolist(),
        'depth': {'source': 'monocular_estimate', 'model': 'synthetic', 'unit': 'm',
            'registered_to_rgb': True, 'registration_id': 'synthetic-registration',
            'episodes': {ep.id: {'path': str(path), 'rgb_channel': 'high',
                'frame_range': [1, 3], 'correspondence_evidence': str(evidence_path)}}}}
    manifest = algorithm.inspect_safety_frames(ep, params)
    return ep, params, manifest, rgb[1:3], evidence, evidence_path


@pytest.mark.parametrize('source,unit', [('monocular_estimate', 'm'), ('sensor_depth', 'mm')])
def test_valid_evidence_binds_content_and_preserves_raw_files(prepared, tmp_path, source, unit):
    ep, params, manifest, rgb, evidence, path = prepared
    evidence.update(source=source, unit=unit); params['depth'].update(source=source, unit=unit)
    write_json(path, evidence)
    before = path.read_bytes()
    result = validate_correspondence(ep, params, manifest, rgb, tmp_path/'validation.json')
    assert result['status'] == 'validated' and result['validated_frames'] == 2
    assert result['original_frame_range'] == [1, 3]
    assert path.read_bytes() == before and not list(ep.path.iterdir())


@pytest.mark.parametrize('case', [
    'episode_id', 'source_episode', 'selector', 'channel', 'frame_range', 'status',
    'unit', 'source', 'registered_to_rgb', 'registration_id', 'rgb', 'rgb_shape',
    'reference_index', 'source_index', 'order', 'missing', 'extra', 'depth', 'missing_hash',
    'invalid_json', 'missing_file', 'not_object'])
def test_wrong_or_incomplete_evidence_is_rejected(prepared, tmp_path, case):
    ep, params, manifest, rgb, evidence, path = prepared
    if case in {'episode_id', 'source_episode', 'channel', 'unit', 'source', 'registration_id'}:
        evidence[case] = 'wrong'
    elif case == 'selector': evidence[case] = {'episode_index': 4}
    elif case == 'frame_range': evidence[case] = [0, 2]
    elif case == 'status': evidence[case] = 'preparing'
    elif case == 'registered_to_rgb': evidence[case] = False
    elif case == 'rgb': rgb = rgb.copy(); rgb[0, 0, 0, 0] += 1
    elif case == 'rgb_shape': evidence['frames'][0][case] = [4, 16]
    elif case in {'reference_index', 'source_index'}: evidence['frames'][0][case] += 1
    elif case == 'order': evidence['frames'].reverse()
    elif case == 'missing': evidence['frames'].pop()
    elif case == 'extra': evidence['frames'].append(evidence['frames'][0])
    elif case == 'depth': np.save(params['depth']['episodes'][ep.id]['path'], np.full((2, 8, 8), 2, np.float32))
    elif case == 'missing_hash': del evidence['depth']
    write_json(path, [] if case == 'not_object' else evidence)
    if case == 'invalid_json': path.write_text('{')
    if case == 'missing_file': path.unlink()
    with pytest.raises(ValueError, match='Invalid depth correspondence'):
        validate_correspondence(ep, params, manifest, rgb, tmp_path/'validation.json')
    saved = json.loads((tmp_path/'validation.json').read_text())
    assert saved['status'] == 'failed' and saved['reason']


@pytest.mark.parametrize('valid', [False, True])
def test_adapter_checks_depth_before_video_rendering_and_model_start(prepared, tmp_path, monkeypatch, valid):
    ep, params, manifest, rgb, evidence, path = prepared
    if not valid:
        evidence['frames'][0]['rgb_sha256'] = 'wrong'
        write_json(path, evidence)
    work = tmp_path/'run'; work.mkdir(); calls = []
    def provenance(context, *a, **kw):
        context.write_result('provenance.json', {'test': True})
        return {'test': True}
    def render(*a, **kw):
        calls.append('render'); return {'views': {'faceImg': {'path': 'synthetic.mp4'}}}
    def worker(context, *a):
        assert json.loads((context.workdir/'depth-validation.json').read_text())['status'] == 'validated'
        calls.append('worker'); return {'assessment_complete': True}
    monkeypatch.setattr(algorithm, 'save_provenance', provenance)
    monkeypatch.setattr(algorithm, 'render_videos', render)
    monkeypatch.setattr(algorithm, 'run_worker', worker)
    monkeypatch.setattr(algorithm, 'physical_arrays', lambda *a: {
        'left_gripper': np.ones((4, 1)), 'left_terminal_effort': np.zeros((4, 1))})
    context = Context(None, ep, work)
    if valid:
        result = algorithm.plugin.run(context, params)
        assert result['assessment_complete'] and calls == ['render', 'worker']
    else:
        with pytest.raises(ValueError, match='RGB SHA-256'):
            algorithm.plugin.run(context, params)
        assert calls == []
        assert (work/'provenance.json').is_file() and (work/'input-mapping.json').is_file()
        assert list((work/'frames').rglob('*.png'))
        assert json.loads((work/'depth-validation.json').read_text())['status'] == 'failed'


def test_executor_catalog_keeps_mismatch_evidence_without_a_verdict(prepared, tmp_path, monkeypatch):
    from edc.algorithms.registry import AlgorithmSpec, Registry
    from edc.data.ir.canonical import open_dataset
    from edc.execution.runner import run
    ep, params, manifest, rgb, evidence, path = prepared
    evidence['frames'][0]['rgb_sha256'] = 'wrong'; write_json(path, evidence)
    (ep.path/'trajectory.json').write_text(json.dumps({'data': [{'timestamp': 1., 'joint_pos': [0.]}]}))
    def provenance(context, *a, **kw):
        context.write_result('provenance.json', {'test': True}); return {'test': True}
    monkeypatch.setattr(algorithm, 'save_provenance', provenance)
    monkeypatch.setattr(algorithm, 'run_worker', lambda *a: pytest.fail('Model started with mismatched input'))
    class Probe:
        spec = AlgorithmSpec('test__depth_contract', '1', 'Depth', 'Tests', 'Input failure', category='value')
        def run(self, context, ignored):
            return algorithm.plugin.run(Context(context.dataset, ep, context.workdir), params)
    registry = Registry('edc.algorithms'); registry.register(Probe())
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    output = tmp_path/'catalog-output'
    with open_dataset(ep.path) as ds:
        run(ds, output=output, algorithm_ids=['test__depth_contract'])
    entry, = json.loads((output/'catalog.json').read_text())['runs']
    record = json.loads((output/entry['records'][0]).read_text())
    assert record['status'] == 'processing_error' and record['result'] is None
    assert 'RGB SHA-256' in record['reason']
    assert {'depth-validation.json', 'provenance.json', 'input-mapping.json'} <= {
        Path(p).name for p in record['artifacts']}
    assert all((output/p).is_file() for p in record['artifacts'])
