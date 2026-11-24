"""Synthetic EDC inputs exercise the adapter without model inference."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from edc import open_dataset
from edc.algorithms.contracts import resolve_parameters
from edc.algorithms.quality.adapters.cpu import preflight
from edc.algorithms.recommendations import recommend_episode
from edc.algorithms.value import runtime
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import algorithm, input_contract
from edc.data.ir.units import SIEpisode


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    import cv2
    raw = tmp_path/'raw'; raw.mkdir()
    data = [{'timestamp': 10+i*.1, 'position': [i*.01, 0, 0],
             'attitude': [2**-.5, 0, 0, 2**-.5], 'gripper': [2.],
             'effort': [0, 1, 2, 3, 4, 5, .5]} for i in range(4)]
    (raw/'trajectory.json').write_text(json.dumps({'data': data}))
    video = cv2.VideoWriter(str(raw/'high.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 25, (16, 16))
    assert video.isOpened()
    for i in range(4): video.write(np.full((16, 16, 3), i*40, np.uint8))
    video.release()
    common = {'time_source': 'acquisition', 'clock': 'recorded',
              'source_kind': 'feedback', 'calibration_status': 'confirmed',
              'calibration_reference': 'synthetic fixture'}
    channels = {name: {**common, 'fields': [field], 'unit': unit, 'modality': modality,
                      'representation': {'semantic_status': 'confirmed',
                          'quantity': 'torque' if name == 'effort' else 'orientation' if name == 'attitude' else 'position'}}
        for name, field, unit, modality in [('position', 'position', 'm', 'pose'),
            ('attitude', 'orientation', '1', 'pose'), ('gripper', 'value', 'cm', 'other'),
            ('effort', 'value', 'N*m', 'other')]}
    channels['attitude']['representation'].update(kind='quaternion', order='wxyz', unit='1')
    channels['high'] = {**common, 'modality': 'rgb', 'logical_name': 'rgb/head'}
    profile = {'schema_version': 1, 'id': 'synthetic', 'version': '1', 'timestamp_unit': 's',
               'video_frame_time': 'rows', 'channels': channels}
    dataset = open_dataset(raw, profile=profile)
    ep = SIEpisode(next(dataset.iter_episodes()), 'auto'); ep.open()
    graph = tmp_path/'task.json'; graph.write_text(json.dumps({'schema_version': 1, 'task': 'synthetic',
        'entities': [{'name': 'object', 'category': 'A_obj'}, {'name': 'table', 'category': 'B_env'}]}))
    models = {}
    for name in ('qwen', 'sam3', 'cotracker', 'dinov2'):
        path = tmp_path/name
        if name in {'qwen', 'dinov2'}:
            path.mkdir(); (path/'config.json').write_text('{}')
        else: path.write_bytes(b'synthetic model placeholder')
        models[name] = str(path)
    depth = tmp_path/'depth.npy'; np.save(depth, np.ones((2, 16, 16), np.float32))
    evidence_path = tmp_path/'depth-evidence.json'
    params = resolve_parameters(algorithm.plugin.spec, {
        'views': {'faceImg': 'rgb/head'}, 'task': 'Synthetic grasp', 'task_graph': str(graph),
        'models': models, 'frame_range': [1, 3], 'intrinsics': np.eye(3).tolist(),
        'arms': {'left': {'position': {'channel': 'position', 'field': 'position'},
            'quaternion': {'channel': 'attitude', 'field': 'orientation', 'order': 'wxyz'},
            'gripper': {'channel': 'gripper', 'field': 'value'},
            'terminal_effort': {'channel': 'effort', 'field': 'value', 'indices': [6]},
            'T_arm_to_camera': np.eye(4).tolist()}},
        'depth': {'source': 'sensor_depth', 'unit': 'm', 'registered_to_rgb': True,
            'registration_id': 'synthetic', 'episodes': {ep.id: {'path': str(depth),
                'rgb_channel': 'high', 'frame_range': [1, 3], 'correspondence_evidence': str(evidence_path)}}}})
    rows = list(ep.iter_frames('high'))
    evidence = {'status': 'completed', 'episode_id': ep.id, 'source_episode': str(raw),
        'selector': ep.selector, 'channel': 'high', 'frame_range': [1, 3], 'source': 'sensor_depth',
        'unit': 'm', 'registered_to_rgb': True, 'registration_id': 'synthetic',
        'depth': runtime.fingerprint(depth), 'frames': [
            {'reference_index': i, 'source_index': rows[i]['index'], 'rgb_shape': [16, 16],
             'rgb_sha256': hashlib.sha256(rows[i]['image'].tobytes()).hexdigest()} for i in (1, 2)]}
    evidence_path.write_text(json.dumps(evidence))
    monkeypatch.setattr(runtime, 'check_runtime', lambda *a, **kw: None)
    monkeypatch.setattr(algorithm, 'check_device', lambda p: None)
    yield ep, params, profile
    ep.close()


def test_canonical_orientation_not_converted_twice_and_alias_depth(prepared):
    ep, params, _ = prepared; evidence = {}
    out = algorithm.physical_arrays(ep, params, 'high', evidence, reference_indices=[1, 2])
    np.testing.assert_allclose(out['left_quaternion'], [[0, 0, 2**-.5, 2**-.5]]*2)
    np.testing.assert_allclose(out['left_gripper'], [[.02]]*2)
    np.testing.assert_allclose(out['left_terminal_effort'], [[.5]]*2)
    assert evidence['left_quaternion']['input_order'] == 'xyzw'
    assert evidence['left_quaternion']['configured_order'] == 'wxyz'
    assert evidence['left_quaternion']['framework_normalized']
    assert evidence['left_position']['source_mapping']['reference_indices'] == [1, 2]
    assert algorithm.depth_array(ep, params, (2, 16, 16)).shape == (2, 16, 16)
    assert preflight(algorithm.plugin, ep, params) == ('runnable', '')


def test_preflight_bindings_identify_resolved_inputs(prepared):
    ep, params, _ = prepared; ident = algorithm.plugin.spec.id
    entry = recommend_episode(ep, {ident: algorithm.plugin}, {ident: params})['algorithms'][0]
    assert entry['status'] == 'runnable'
    assert entry['environment_requirements']['device'] == 'cuda'
    assert len(entry['channel_bindings']) == 5
    rgb = next(b for b in entry['channel_bindings'] if b['parameter'] == 'views.faceImg')
    assert rgb['requested'] == 'rgb/head' and rgb['channel'] == 'high'


@pytest.mark.parametrize('case', ['cpu', 'missing_depth', 'empty_arms', 'indices', 'unknown_unit',
    'missing_field', 'bad_task', 'bad_task_shape', 'range', 'empty_model', 'model_type', 'skew', 'extra_binding'])
def test_preflight_classifies_input_and_dependency_errors(prepared, case):
    ep, p, _ = prepared; p = copy.deepcopy(p)
    if case == 'cpu': p['device'] = 'cpu'
    elif case == 'missing_depth': p['depth']['episodes'] = {}
    elif case == 'empty_arms': p['arms'] = {}
    elif case == 'indices': p['arms']['left']['terminal_effort']['indices'] = [7]
    elif case == 'unknown_unit': ep.channels['effort'].field_metadata['value']['unit'] = 'unknown'
    elif case == 'missing_field': p['arms']['left']['position']['field'] = 'missing'
    elif case == 'bad_task': Path(p['task_graph']).write_text('{')
    elif case == 'bad_task_shape': Path(p['task_graph']).write_text('[]')
    elif case == 'range': p['frame_range'] = [1, 2, 3]
    elif case == 'empty_model': Path(p['models']['sam3']).write_bytes(b'')
    elif case == 'model_type': p['models']['sam3'] = p['models']['qwen']
    elif case == 'skew': p['intrinsics'][0][1] = .1
    elif case == 'extra_binding': p['arms']['left']['gripper']['unit'] = 'cm'
    status, reason = preflight(algorithm.plugin, ep, p)
    assert status == ('missing_dependency' if case in {'empty_model', 'model_type'} else 'missing_input'), reason
    assert reason


def test_selected_signal_coverage_uses_clock_mapping_and_never_extrapolates(prepared):
    ep, _, _ = prepared
    # Numeric samples cover only the selected segment after explicit clock conversion.
    signal = ep.channels['effort']; reference = ep.channels['high']
    signal.timestamps = [0., .1, .2, .3]; signal.clock = 'signal'
    reference.timestamps = [9., 10.1, 10.2, 11.]
    ep.profile['transforms'] = {'clocks': {'offset': {'source': 'signal', 'target': 'recorded', 'offset_seconds': 10}}}
    with pytest.raises(ValueError, match='extrapolation'):
        ep.align_signal('effort', 'value', 'high', method='linear')
    result = ep.align_signal('effort', 'value', 'high', method='linear', reference_indices=[1, 2])
    assert result.source_mapping['reference_indices'] == [1, 2]
    assert result.params['target_timestamps'] == [10.1, 10.2]
    assert result.source_mapping['rows']['left_indices'] == [1, 2]
    assert result.source_mapping['original_source_timestamps'] == [0., .1, .2, .3]
    with pytest.raises(ValueError, match='extrapolation'):
        ep.align_signal('effort', 'value', 'high', method='nearest', reference_indices=[2, 3])
    ep.profile['transforms'] = {}
    with pytest.raises(ValueError, match='mapping'):
        ep.align_signal('effort', 'value', 'high', method='linear', reference_indices=[1, 2])


@pytest.mark.parametrize('indices', [[], [-1], [4], [1, 1], [2, 1], [True], [.5], [[1]], np.array([2, 1], dtype=np.uint64)])
def test_invalid_reference_selection_rejected(prepared, indices):
    ep, _, _ = prepared
    with pytest.raises(ValueError, match='reference_indices'):
        ep.align_signal('effort', 'value', 'high', method='linear', reference_indices=indices)


def test_worker_cuda_probe_fails_without_claiming_runnable(monkeypatch):
    monkeypatch.setattr(input_contract.subprocess, 'run', lambda *a, **kw: NS(returncode=0, stdout='false'))
    with pytest.raises(ImportError, match='unavailable'):
        input_contract.check_device({'python': 'worker-python', 'device': 'cuda:0'})


@pytest.mark.parametrize('entrypoint', ['api', 'cli'])
def test_executor_records_native_result_scope_and_bindings(prepared, tmp_path, monkeypatch, entrypoint):
    from edc.execution.runner import run
    from edc.reporting.results import write_json
    ep, params, profile = prepared
    ident = algorithm.plugin.spec.id
    monkeypatch.setattr(algorithm, 'save_provenance', lambda context, *a, **kw:
        (context.write_result('provenance.json', {'synthetic': True}) or {'synthetic': True}))
    def worker(context, *a):
        assert json.loads((context.workdir/'depth-validation.json').read_text())['status'] == 'validated'
        arrays = np.load(context.workdir/'physical-input.npz')
        np.testing.assert_allclose(arrays['left_quaternion'], [[0, 0, 2**-.5, 2**-.5]]*2)
        result = {'assessment_complete': True, 'verdict': 'NEEDS_REVIEW', 'reason': 'Synthetic review',
            'checks': {event: {'status': 'completed'} for event in algorithm.EVENTS},
            'assessment_scope': {'whole_episode': False, 'original_frame_range': [1, 3], 'source_frame_count': 4}}
        write_json(context.workdir/'native-result.json', result)
        return result
    monkeypatch.setattr(algorithm, 'run_worker', worker)
    out = tmp_path/'output'
    if entrypoint == 'cli':
        # Exercise the documented raw rotation-vector card; the API fixture above
        # separately exercises canonical quaternion normalization.
        source = ep.path/'trajectory.json'
        payload = json.loads(source.read_text())
        for row in payload['data']: row['attitude'] = [0, 0, np.pi/2]
        source.write_text(json.dumps(payload))
    before = {p.name: runtime.sha256(p) for p in ep.path.iterdir() if p.is_file()}
    if entrypoint == 'api':
        with open_dataset(ep.path, profile=profile) as ds:
            run(ds, algorithm_ids=[ident], params={ident: params}, output=out)
    else:
        import yaml
        from edc.execution.cli import main
        robot = tmp_path/'robot.yaml'
        cli_params = copy.deepcopy(params)
        cli_params['arms']['left']['position']['field'] = 'value'
        cli_params['arms']['left']['quaternion']['field'] = 'value'
        cli_params['arms']['left']['quaternion']['order'] = 'rotvec'
        card = {'robot': {'name': 'synthetic', 'confirmed_by': 'synthetic fixture'},
            'data': {'time': {'source': 'timestamp', 'unit': 's'}, 'video_frame_time': 'rows'},
            'cameras': {'head': {'source': 'high', 'mount': 'head'}},
            'other': [{'source': name, 'unit': 'rad' if name == 'attitude' else channel['unit'],
                'quantity': channel['representation']['quantity'], 'source_kind': 'feedback',
                'calibration_status': 'confirmed', 'calibration_reference': 'synthetic fixture'}
                for name, channel in profile['channels'].items() if name != 'high'],
            'checks': {ident: cli_params}}
        robot.write_text(yaml.safe_dump(card))
        assert main(['run', str(ep.path), '--robot', str(robot), '--algorithm', ident,
                     '--output', str(out)]) == 0
    record = json.loads(next(out.glob(f'_runs/*/{ident}/records.jsonl')).read_text())
    assert record['status'] == 'completed', record.get('reason')
    assert not record['scope_complete'] and record['result']['reports'][0]['verdict'] == 'NEEDS_REVIEW'
    assert 'score' not in record['result']['reports'][0]
    assert any(b['parameter'] == 'views.faceImg' and b['channel'] == 'high'
               for b in record['channel_bindings'])
    assert (out/'catalog.json').is_file() and (out/'report.html').is_file()
    assert all((out/p).is_file() for p in record['artifacts'])
    assert before == {p.name: runtime.sha256(p) for p in ep.path.iterdir() if p.is_file()}
