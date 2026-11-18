"""Exercise real adapters and executor; native inference is a controlled double."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from edc import open_dataset, run
from edc.algorithms.registry import Registry
from edc.algorithms.value import runtime


@pytest.mark.parametrize('name,jitter', [('Progress_Stagnation_Attribution', False),
                                       ('Progress_Stagnation_Attribution', True)])
def test_failed_episode_retains_provenance_and_next_episode_runs(tmp_path, monkeypatch, name, jitter):
    from importlib import import_module
    method = import_module(f'edc.algorithms.value.algorithms.{name}.algorithm')
    h5py = pytest.importorskip('h5py')
    source = tmp_path / 'source.h5'
    with h5py.File(source, 'w') as handle:
        for index in range(2):
            group = handle.create_group(f'episodes/{index}')
            group['rgb'] = np.full((3, 8, 10, 3), index, dtype=np.uint8)
            group['timestamp'] = 10. + (np.array([0., .031, .069]) if jitter else np.arange(3) / 30.)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    profile = {'schema_version': 1, 'id': 'runner-test', 'version': '1',
               'timestamp_unit': 's', 'channels': {'rgb': {
                   'modality': 'rgb', 'time_source': 'acquisition', 'clock': 'camera'}}}
    model = tmp_path / 'model'; model.mkdir()
    (model / 'weights').write_bytes(b'synthetic weights; no inference')
    monkeypatch.setattr(runtime, 'check_runtime', lambda *args, **kw: None)
    with open_dataset(source, profile=profile) as dataset:
        ids = [ep.id for ep in dataset.iter_episodes()]
    if name == 'Progress_Stagnation_Attribution':
        params = {'views': {key: 'rgb' for key in (
            'cam_high', 'cam_left_wrist', 'cam_right_wrist')}, 'model': str(model),
            'task': 'synthetic pick', 'physical_signals': {},
            'subtasks': [{'step': 0, 'query': 'pick', 'clip_start_frame': 0, 'clip_end_frame': 3}]}
        monkeypatch.setattr(method, 'physical_inputs', lambda ep, *args: NS(
            episode_id=ep.id, timestamps=np.arange(3)/30., arms={}, warnings=[]))
    calls = []
    def native_double(context, effective, package, payload):
        # Adapter must persist provenance before any worker/model can start.
        saved = json.loads((context.workdir / 'provenance.json').read_text())
        assert saved['source_files'] and saved['models']
        if name == 'Progress_Stagnation_Attribution':
            assert saved['shared_source_files']['edc.execution.frames']
        calls.append(context.episode.id)
        context.write_result('request.json', payload)
        (context.workdir / 'worker.log').write_text('controlled model initialization')
        if context.episode.id == ids[0]:
            raise RuntimeError('controlled native initialization failure')
        return {'method': name, 'events': [], 'completed': True}
    monkeypatch.setattr(method, 'run_worker', native_double)
    registry = Registry('edc.algorithms'); registry.register(method.plugin)
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    output = tmp_path / 'results'
    with open_dataset(source, profile=profile) as dataset:
        run(dataset, output=output, algorithm_ids=[name], params={name: params})
    records_file = next(output.glob('_runs/*/*/records.jsonl'))
    records = [json.loads(line) for line in records_file.read_text().splitlines()]
    assert calls == ids
    assert [record['status'] for record in records] == ['processing_error', 'completed']
    assert records[0]['result'] is None
    assert 'controlled native initialization failure' in records[0]['reason']
    for record in records:
        artifacts = [output / path for path in record['artifacts']]
        assert all(path.is_file() for path in artifacts)
        assert {'provenance.json', 'request.json', 'worker.log'} <= {path.name for path in artifacts}
        if name == 'Progress_Stagnation_Attribution':
            timing = json.loads(next(path for path in artifacts if path.name == 'frame-timeline.json').read_text())
            assert timing['playback_timestamps'] is None
            assert timing['uniform_source_intervals'] is (not jitter)
            assert 'input-mapping.json' in {path.name for path in artifacts}
            assert any(path.suffix == '.png' for path in artifacts) is (record['status'] == 'processing_error')
            if record['status'] == 'completed':
                assert record['result']['reports'][0]['frame_cache']['status'] == 'cleaned'
            assert timing['source_timestamps'][0] == 10.
        provenance = json.loads(next(path for path in artifacts if path.name == 'provenance.json').read_text())
        if record['status'] == 'completed':
            assert record['result']['reports'][0]['provenance'] == provenance
    catalog = json.loads((output / 'catalog.json').read_text())
    assert catalog['runs']
    selection = json.loads(next(output.glob('_runs/*/selection.json')).read_text())
    assert all(row['decision'] == 'unassessed' for row in selection)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
