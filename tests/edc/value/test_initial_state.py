"""Synthetic frames/inference doubles validate contracts, never model accuracy."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from edc import open_dataset, run
from edc.algorithms.contracts import resolve_parameters
from edc.algorithms.registry import Registry
from edc.algorithms.value.algorithms.Initial_State_Distribution import algorithm as method
from edc.algorithms.value.algorithms.Initial_State_Distribution.metrics import (
    compute_distribution_summary, normalize_config)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    h5py = pytest.importorskip('h5py')
    source = tmp_path / 'shared.h5'
    with h5py.File(source, 'w') as handle:
        for i in range(2):
            group = handle.create_group(f'episodes/{i}')
            group['rgb'] = np.full((3, 16, 20, 3), i * 100, dtype='uint8')
            group['timestamp'] = [10., 10.13, 10.27]
    profile = {'schema_version': 1, 'id': 'contract', 'version': '1', 'timestamp_unit': 's',
        'channels': {'rgb': {'modality': 'rgb', 'logical_name': 'rgb/head',
                           'time_source': 'acquisition', 'clock': 'fixture_camera'}}}
    checkpoint = tmp_path / 'checkpoint'; checkpoint.write_bytes(b'contract-only checkpoint')
    params = {'channel': 'rgb/head', 'comparison_space': 'synthetic-fixed-camera',
              'checkpoint': str(checkpoint), 'grid_size': 2,
              'objects': [{'name': 'cup', 'prompt': 'cup'}]}
    registry = Registry('edc.algorithms'); registry.register(method.plugin)
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    monkeypatch.setattr(method, '_missing_dependencies', lambda: [])
    output = tmp_path / 'output'
    return source, profile, params, output


def evaluate(fixture):
    source, profile, params, output = fixture
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with open_dataset(source, profile=profile) as dataset:
        ids = [ep.id for ep in dataset.iter_episodes()]
        summary = run(dataset, algorithm_ids=[method.plugin.spec.id], output=output,
                      params={method.plugin.spec.id: params})
    records = next(output.glob('_runs/*/*/records.jsonl')).read_text().splitlines()
    assert len(records) == 1  # dataset method must run once, even with shared storage
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    return json.loads(records[0]), ids, summary


def backend(calls, fail=False, no_detections=False):
    def create(config, params):
        calls.append('load')
        def extract(image):
            pixel = int(np.asarray(image)[0, 0, 0]); calls.append(pixel)
            if fail and pixel == 0:
                raise RuntimeError('synthetic extraction error')
            point = [.25, .25] if pixel == 0 else [.75, .75]
            return {'width': image.width, 'height': image.height, 'categories': {
                'cup': {'instances': [] if no_detections else [
                    {'centroid_norm': point, 'confidence': .9}]}}}
        return SimpleNamespace(extract_image=extract)
    return create


def assert_saved_provenance(fixture, record):
    paths = [path for path in record['artifacts'] if path.endswith('/provenance.json')]
    assert len(paths) == 1
    saved = json.loads((fixture[3] / paths[0]).read_text())
    checkpoint = Path(fixture[2]['checkpoint']).resolve()
    assert saved['checkpoint'] == str(checkpoint)
    assert saved['checkpoint_sha256'] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert set(saved['versions']) == {'embodied-data-curation', 'torch', 'torchvision', 'sam3'}
    assert all(saved['versions'].values())
    assert saved['method_source'] == json.loads(
        method.files(method.__package__).joinpath('SOURCE.json').read_text())
    if record['status'] == 'completed':
        assert record['result']['reports'][0]['provenance'] == saved
    return saved


def test_raw_frame_dataset_identity_native_stats_and_unassessed_selection(fixture, monkeypatch):
    calls = []
    monkeypatch.setattr(method, '_backend', backend(calls))
    def forbidden(*args, **kwargs):
        raise AssertionError('Single-frame method must not materialize video')
    monkeypatch.setattr('edc.data.ir.dataset.Episode.materialize_video', forbidden)
    record, ids, _ = evaluate(fixture)
    assert calls == ['load', 0, 100]
    assert record['schema_version'] == 'edc_record_v1' and record['status'] == 'completed'
    assert_saved_provenance(fixture, record)
    report = record['result']['reports'][0]
    assert report['mode'] == 'infer' and 'verdict' not in report
    assert [r['episode'] for r in report['per_episode']] == ids and len(set(ids)) == 2
    assert [r['selector'] for r in report['per_episode']] == [
        {'group': '/episodes/0'}, {'group': '/episodes/1'}]
    assert len({r['source'] for r in report['per_episode']}) == 1
    assert len({r['image_evidence'] for r in report['per_episode']}) == 2
    assert all(r['time_source'] == 'acquisition' and r['clock'] == 'fixture_camera'
               and r['frame_evidence']['source_time'] == 10 for r in report['per_episode'])
    cup = report['distribution']['categories']['cup']
    assert cup['heatmap'] == [[.5, 0], [0, .5]] and cup['score'] == 50
    assert report['coverage'] == {'attempted': 2, 'completed': 2, 'errors': 0}
    assert report['provenance']['checkpoint_sha256'] == hashlib.sha256(
        Path(fixture[2]['checkpoint']).read_bytes()).hexdigest()
    assert report['provenance']['method_source']['files']['metrics.py']
    assert any(path.endswith('-probability.png') for path in record['artifacts'])
    selection = json.loads(next(fixture[3].glob('_runs/*/selection.json')).read_text())
    assert all(row['decision'] == 'unassessed' for row in selection)


@pytest.mark.parametrize('fail,no_detections', [(False, False), (False, True), (True, False)])
def test_prepared_worker_matches_inprocess_pixels_mapping_scores_and_failures(fixture, monkeypatch, fail, no_detections):
    from edc.algorithms.value import runtime
    from edc.algorithms.value.algorithms.Initial_State_Distribution import worker
    calls = []
    monkeypatch.setattr(method, '_backend', backend(calls, fail=fail, no_detections=no_detections))
    direct, _, _ = evaluate(fixture)
    direct_detections = json.loads(next(fixture[3].glob('_runs/*/*/artifacts/dataset/initial_state_detections.json')).read_text())
    direct_summary = json.loads(next(fixture[3].glob('_runs/*/*/artifacts/dataset/distribution_summary.json')).read_text())
    source, profile, params, output = fixture
    isolated = (source, profile, {**params, 'python': sys.executable}, output.with_name('worker-output'))
    monkeypatch.setattr(runtime, 'check_runtime', lambda *a, **kw: None)
    requests = []
    def execute(context, effective, package, payload):
        requests.append(payload)
        # JSON round-trip matches the real request contract, including selectors.
        request = json.loads(json.dumps({'params':effective,'workdir':str(context.workdir),**payload}))
        return worker.execute(request)
    monkeypatch.setattr(runtime, 'run_worker', execute)
    record, _, _ = evaluate(isolated)
    assert record['status'] == direct['status']
    assert calls == ['load', 0, 100, 'load', 0, 100]
    assert len(requests) == 1 and len(requests[0]['rows']) == 2
    assert json.loads(next(isolated[3].glob('_runs/*/*/artifacts/dataset/initial_state_detections.json')).read_text()) == direct_detections
    assert json.loads(next(isolated[3].glob('_runs/*/*/artifacts/dataset/distribution_summary.json')).read_text()) == direct_summary
    assert any(path.endswith('/provenance.json') for path in record['artifacts'])
    assert any(path.endswith('/input-mapping.json') for path in record['artifacts'])


def test_failed_episode_keeps_native_denominator_and_cannot_complete(fixture, monkeypatch):
    calls = []; monkeypatch.setattr(method, '_backend', backend(calls, fail=True))
    record, ids, _ = evaluate(fixture)
    assert calls == ['load', 0, 100]
    assert record['status'] == 'processing_error' and record['result'] is None
    assert_saved_provenance(fixture, record)
    output = fixture[3]
    evidence = next(output.glob('_runs/*/*/artifacts/dataset/distribution_summary.json'))
    native = json.loads(evidence.read_text())
    assert native['extraction_error_count'] == 1
    assert native['categories']['cup']['valid_episode_ratio'] == .5
    errors = json.loads(evidence.with_name('domain-errors.json').read_text())
    assert errors[0]['episode_id'] == ids[0] and 'synthetic extraction error' in errors[0]['error']
    selection = json.loads(next(output.glob('_runs/*/selection.json')).read_text())
    assert all(row['decision'] == 'unassessed' for row in selection)


def test_no_detections_is_an_observation_with_warning(fixture, monkeypatch):
    monkeypatch.setattr(method, '_backend', backend([], no_detections=True))
    record, _, _ = evaluate(fixture)
    assert record['status'] == 'completed'
    assert_saved_provenance(fixture, record)
    report = record['result']['reports'][0]
    assert report['distribution']['warnings']
    assert report['distribution']['categories']['cup']['valid_episode_ratio'] == 0
    assert 'verdict' not in report


@pytest.mark.parametrize('stage', ['model', 'statistics'])
def test_failure_keeps_provenance_and_unassessed_selection(fixture, monkeypatch, stage):
    calls = []
    monkeypatch.setattr(method, '_backend', backend(calls))
    def fail(*args):
        if stage == 'model':
            paths = list(fixture[3].glob('_runs/*/*/artifacts/dataset/provenance.json'))
            assert len(paths) == 1
        raise RuntimeError(f'synthetic {stage} failure')
    monkeypatch.setattr(method, '_backend' if stage == 'model' else 'compute_distribution_summary', fail)
    record, _, _ = evaluate(fixture)
    assert record['status'] == 'processing_error' and record['result'] is None
    assert f'synthetic {stage} failure' in record['reason']
    assert_saved_provenance(fixture, record)
    selection = json.loads(next(fixture[3].glob('_runs/*/selection.json')).read_text())
    assert all(row['decision'] == 'unassessed' for row in selection)


def test_provenance_is_computed_once_before_model_initialization(fixture, monkeypatch):
    original_hash = method._sha256
    calls = []
    def fingerprint(path):

        if path == Path(fixture[2]['checkpoint']).resolve():
            calls.append('hash')
        return original_hash(path)
    original_backend = backend(calls)
    def create(config, params):
        assert calls == ['hash']
        saved = next(fixture[3].glob('_runs/*/*/artifacts/dataset/provenance.json'))
        assert json.loads(saved.read_text())['checkpoint_sha256']
        return original_backend(config, params)
    monkeypatch.setattr(method, '_sha256', fingerprint)
    monkeypatch.setattr(method, '_backend', create)
    record, _, _ = evaluate(fixture)
    assert record['status'] == 'completed'
    assert calls == ['hash', 'load', 0, 100]
    assert_saved_provenance(fixture, record)


@pytest.mark.parametrize('stage', ['checkpoint', 'source', 'write'])
def test_provenance_failure_prevents_model_initialization(fixture, monkeypatch, stage):
    calls = []
    monkeypatch.setattr(method, '_backend', backend(calls))
    def fail(*args):
        raise OSError(f'synthetic provenance {stage} failure')
    if stage == 'checkpoint':
        monkeypatch.setattr(method, '_sha256', fail)
    elif stage == 'source':
        monkeypatch.setattr(method, 'files', fail)
    else:
        original_write = method.write_json
        def write(path, data):
            if path.name == 'provenance.json':
                fail()
            return original_write(path, data)
        monkeypatch.setattr(method, 'write_json', write)
    record, _, _ = evaluate(fixture)
    assert record['status'] == 'processing_error' and record['result'] is None
    assert f'synthetic provenance {stage} failure' in record['reason']
    assert calls == []


@pytest.mark.parametrize('change', [
    {'comparison_space': ''}, {'objects': [{'name': 'cup', 'prompt': ''}]},
    {'objects': [{'name': 'cup', 'prompt': 'cup'}, {'name': 'cup', 'prompt': 'cup'}]},
    {'frame_index': 3}, {'channel': 'absent'}])
def test_invalid_input_does_not_start_model(fixture, monkeypatch, change):
    fixture[2].update(change)
    def forbidden(*args):
        raise AssertionError('Invalid input must not start a model')
    monkeypatch.setattr(method, '_backend', forbidden)
    record, _, _ = evaluate(fixture)
    assert record['status'] == 'missing_input'


def test_missing_dependency_is_not_data_failure(fixture, monkeypatch):
    monkeypatch.setattr(method, '_missing_dependencies', lambda: ['sam3'])
    record, _, _ = evaluate(fixture)
    assert record['status'] == 'missing_dependency' and 'sam3' in record['reason']


def test_withheld_episode_is_visible_and_not_in_distribution_denominator(fixture, monkeypatch):
    h5py = pytest.importorskip('h5py')
    with h5py.File(fixture[0], 'a') as handle:
        del handle['episodes/1/rgb']
        # One episode has no selected RGB view; numeric rows still identify it.
        handle['episodes/1/joint_pos'] = np.zeros((3, 1))
    calls = []; monkeypatch.setattr(method, '_backend', backend(calls))
    record, ids, _ = evaluate(fixture)
    assert record['status'] == 'completed' and calls == ['load', 0]
    assert record['eligible_episodes'] == [ids[0]]
    assert record['withheld_episodes'][0]['episode_id'] == ids[1]
    assert record['result']['reports'][0]['distribution']['total_episodes'] == 1


def test_first_frame_closes_decoder_without_reading_rest(fixture, monkeypatch):
    from edc.data.ir.dataset import Episode
    original = Episode.iter_frames
    reads, closed = [], []
    def frames(episode, channel):
        try:
            for frame in original(episode, channel):
                reads.append((episode.id, frame['index']))
                yield frame
        finally:
            closed.append(episode.id)
    monkeypatch.setattr(Episode, 'iter_frames', frames)
    monkeypatch.setattr(method, '_backend', backend([]))
    record, ids, _ = evaluate(fixture)
    assert record['status'] == 'completed'
    assert reads == [(ident, 0) for ident in ids] and closed == ids


def test_discovery_does_not_import_old_framework_or_model_libraries():
    script = '''
import sys
from edc.algorithms.registry import algorithms
registry = algorithms()
assert 'Initial_State_Distribution' in registry.plugins, registry.discovery_errors
assert not ({'torch','torchvision','sam3','PIL','cv2','dqs','framework'} & set(sys.modules))
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_entropy_weighting_confidence_and_missing_samples():
    config = normalize_config({'grid_size': 2, 'objects': [{'name': 'cup', 'prompt': 'cup'}]})
    def row(points):
        return {'categories': {'cup': {'instances': [
            {'centroid_norm': p, 'confidence': confidence} for p, confidence in points]}}}
    result = compute_distribution_summary({'episodes': [
        row([([.25,.25],.9), ([.75,.25],.9), ([.75,.75],.5)]),
        row([([.25,.75],.9)]), row([]), row([])]}, config)['categories']['cup']
    assert result['heatmap'] == [[.25,.25],[.5,0]]
    assert result['valid_episode_ratio'] == .5 and result['total_instances'] == 3
    assert result['normalized_entropy'] == .75 and result['score'] == 37.5
