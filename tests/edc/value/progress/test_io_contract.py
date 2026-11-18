"""Actual profile ingestion, native outputs and run-owned cache lifecycle."""
import json
from pathlib import Path

import numpy as np
import pytest

from edc.data.ir.dataset import open_dataset
from edc.robots.card import compile_card
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.algorithm import physical_inputs
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.physical_features import PhysicalFeatures
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.cache import finalize_cache
from edc.reporting.results import write_json


def signal_fixture(tmp_path, *, load_unit='A'):
    source = tmp_path / 'episode'; source.mkdir()
    rows = [{'timestamp': 100+i*.1, 'grip': i*10, 'load': [1., 2.],
             'xyz': [i*10, 0., 0.]} for i in range(10)]
    (source / 'trajectory.json').write_text(json.dumps({'data': rows}))
    card = {'robot': {'name': 'synthetic', 'confirmed_by': 'synthetic fixture'},
            'data': {'time': {'source': 'timestamp', 'unit': 's'}},
            'arms': {'right': {
                'gripper': {'source': 'grip', 'unit': 'mm', 'quantity': 'position', 'calibration_status': 'confirmed',
                            'calibration_reference': 'synthetic fixture'},
                'eef_position': {'source': 'xyz', 'index': '0-2', 'unit': 'mm'}}},
            'other': [{'source': 'load', 'unit': load_unit, 'quantity': 'current', 'source_kind': 'feedback',
                       'calibration_status': 'confirmed', 'calibration_reference': 'synthetic fixture'}]}
    profile, _, _ = compile_card(card)
    params = {'physical_signals': {'right': {
        'hand_position': {'channel': 'gripper/right', 'field': 'value', 'unit': 'm',
                          'open_reference': [.1], 'closed_reference': [0.]},
        'arm_load': {'channel': 'load', 'field': 'value', 'delta_threshold': .3},
        'hand_load': {'proxy_of': 'arm_load', 'reference': 'synthetic fixture'},
        'ee_position': {'channel': 'pose/arm/right/feedback', 'field': 'position'}}}}
    return source, profile, params


@pytest.mark.parametrize("si", [False, True])
def test_robot_card_to_actual_physical_features_and_unit_conversion(tmp_path, si):
    source, profile, params = signal_fixture(tmp_path)
    with open_dataset(source, profile=profile) as dataset:
        episode = next(dataset.iter_episodes())
        if si:
            from edc.data.ir.units import SIEpisode
            episode = SIEpisode(episode)
        episode.open()
        signals = physical_inputs(episode, params, 'pose/arm/right/feedback')
    assert not signals.warnings
    right = signals.arms['right']
    assert right['hand_position']['config']['calibration_status'] == 'confirmed'
    assert right['hand_position']['openness'] == pytest.approx(np.arange(10)/10)
    assert right['ee_position']['values'][-1, 0] == pytest.approx(.09)
    assert right['hand_load']['description']['proxy_of'] == 'arm_load'
    features = PhysicalFeatures(signals, 10)
    assert not features.limitations['right']
    assert features.speed['right'] == pytest.approx(np.full(10, .1))


def test_missing_load_preserves_usable_signals_and_cannot_upgrade_units(tmp_path):
    source, profile, params = signal_fixture(tmp_path, load_unit='unknown')
    params['physical_signals']['right']['arm_load']['unit'] = 'A'
    with open_dataset(source, profile=profile) as dataset:
        episode = next(dataset.iter_episodes()); episode.open()
        signals = physical_inputs(episode, params, 'pose/arm/right/feedback')
    assert 'ee_position' in signals.arms['right']
    assert 'hand_position' in signals.arms['right']
    assert 'arm_load' not in signals.arms['right']
    assert any('unknown' in reason for reason in signals.warnings)


@pytest.mark.parametrize('keep', [True, False])
def test_cache_finalization_references_and_original_files(tmp_path, keep):
    from test_input_timeline import episode, params
    from edc.execution.frames import inspect_frames, cache_frames
    ep = episode(tmp_path); cfg = params(tmp_path)
    work = tmp_path / 'run'; original = tmp_path / 'original.png'; original.write_bytes(b'original')
    manifest = cache_frames(ep, inspect_frames(ep, cfg['views']), work / 'input', [0, 2, 4])
    paths = [item['path'] for view in manifest['views'].values() for item in view['cached_images'].values()]
    native = work / 'native'; native.mkdir()
    write_json(native/'pred_vllm.json', [{'image': paths, 'progress': .5}])
    result = finalize_cache(work, manifest, {'assessment_complete': False}, keep=keep)
    assert original.read_bytes() == b'original'
    assert len(list((work/'input').rglob('*.png'))) == (9 if keep else 0)
    assert result['frame_cache']['status'] == ('retained' if keep else 'cleaned')
    stored = json.loads((work/'input-mapping.json').read_text())
    for view in stored['views'].values():
        for item in view['cached_images'].values():
            assert item['rgb_sha256'] and item['frame_ref']
            assert item['path'] is not None if keep else item['path'] is None
    pred = json.loads((native/'pred_vllm.json').read_text())[0]
    assert pred['progress'] == .5
    assert all(Path(p).exists() if keep else p.startswith('edc-frame:') for p in pred['image'])


def test_optional_video_failure_retains_native_prediction_and_cache(tmp_path, monkeypatch):
    from test_worker import prepared_request, install_double, Model
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import worker, evaluator
    request = prepared_request(tmp_path); request['params']['export_video'] = True
    install_double(monkeypatch, Model())
    def fail(*args):
        raise RuntimeError('controlled renderer failure')
    monkeypatch.setattr(evaluator, 'plot_video_reward', fail)
    with pytest.raises(RuntimeError, match='controlled renderer failure'):
        worker.execute(request)
    assert list((tmp_path/'input').rglob('*.png'))
    assert list((tmp_path/'native').glob('subtask_*/pred_vllm.json'))


def test_incomplete_candidates_have_conclusions_without_fabricated_normality(tmp_path, monkeypatch):
    from test_worker import prepared_request, install_double, Model
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import worker
    request = prepared_request(tmp_path)
    data = json.loads((tmp_path/'physical-signals.json').read_text())
    data['arms'] = {'left': {}, 'right': {}}
    write_json(tmp_path/'physical-signals.json', data)
    class Drop(Model):
        def inference_batch(self, samples):
            return [{'pred': f'<score>{[80, 30, 35, 40][i % 4]}</score>'} for i in range(len(samples))]
    install_double(monkeypatch, Drop())
    out = worker.execute(request)
    assert not out['assessment_complete']
    assert out['attribution']['candidates']
    assert all(c['status'] == 'incomplete' and c['limitations'] for c in out['attribution']['candidates'])
    assert (tmp_path/'native/attribution_report.txt').exists()
    assert not list(tmp_path.rglob('*.mp4'))
    assert list((tmp_path/'native').rglob('*.png'))  # report chart remains
    assert not (tmp_path/'native/.frame_cache').exists()


def test_native_relative_gripper_uses_original_scale_and_event_rules():
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.signals import hand_rule_values
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import detect_grip_events
    raw = np.r_[np.zeros(10), np.linspace(0, 2.16, 30), np.linspace(2.16, .2, 30), np.full(10, .2)]
    cfg = {'grip_mode': 'native_relative', 'calibration_status': 'confirmed',
           'opening_direction': 'increasing', 'direction_reference': 'synthetic fixture'}
    values = hand_rule_values(raw[:, None], cfg)
    assert np.array_equal(values, raw)  # No per-episode min/max normalization.
    def events(v):
        p5, p95 = np.percentile(v, [5, 95])
        vel = np.r_[0, np.abs(np.diff(v)) / .1]
        return detect_grip_events(v, vel, max(p95-p5, .5*np.ptp(v), .01), p5, vel.max())
    assert events(values) == events(raw) and [e[1] for e in events(values)] == ['open', 'close']
    assert hand_rule_values(raw[:, None], {**cfg, 'calibration_status': 'unknown'}) is None
    with pytest.raises(ValueError, match='direction_reference'):
        hand_rule_values(raw[:, None], {**cfg, 'direction_reference': None})


def test_attribution_preserves_task_identity_at_shared_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import attributor as module
    seen = []
    class Analyzer:
        def __init__(self, *args, **kwargs): pass
        def attribute(self, frame, arm, **kwargs):
            seen.append((arm, dict(self.frame_to_prog)))
            return module.AttributeResult(frame, arm, 'unconfirmed_regression', 0)
    monkeypatch.setattr(module, 'WindowAnalyzer', Analyzer)
    obj = module.EpisodeAttributor.__new__(module.EpisodeAttributor)
    obj.gfeat = NS(n=21); obj.grip_events = {}; obj.fps = 10.; obj.episode_json = Path('synthetic')
    obj.proactive_grasp_scan = lambda *a, **kw: []
    tasks = []
    for step, (start, end, progress) in enumerate(((0, 10, .9), (10, 20, .1))):
        folder = tmp_path / f'subtask_{step}_test'; folder.mkdir()
        write_json(folder/'pred_vllm.json', [{'frame_index': 10, 'progress': progress}])
        tasks.append({'step': step, 'start_frame': start, 'end_frame': end,
                      'regressions': [{'frame_index': 10}], 'stagnations': []})
    write_json(tmp_path/'evaluation_summary.json', {'subtask_results': tasks})
    result = obj.attribute_from_assessment(str(tmp_path/'evaluation_summary.json'))
    assert [row[1][10] for row in seen] == [.9, .9, .1, .1]
    assert len(result['regression_results']) == 4
    assert {row['subtask_idx'] for row in result['regression_results'].values()} == {0, 1}


def test_cache_rejects_non_owned_file_before_deleting(tmp_path):
    external = tmp_path/'source.png'; external.write_bytes(b'original')
    manifest = {'views': {'head': {'cached_images': {'0': {'path': str(external)}}}}}
    with pytest.raises(ValueError, match='ownership'):
        finalize_cache(tmp_path/'run', manifest, {})
    assert external.read_bytes() == b'original'


def test_cache_delete_failure_is_visible_and_reference_retained(tmp_path, monkeypatch):
    image = tmp_path/'input/head/0.png'; image.parent.mkdir(parents=True); image.write_bytes(b'image')
    manifest = {'views': {'head': {'cached_images': {'0': {'path': str(image), 'rgb_sha256': 'fixture'}}}}}
    unlink = Path.unlink
    def fail(path, *a, **kw):
        if path == image:
            raise PermissionError('controlled cache deletion error')
        return unlink(path, *a, **kw)
    monkeypatch.setattr(Path, 'unlink', fail)
    result = finalize_cache(tmp_path, manifest, {})
    assert result['frame_cache']['status'] == 'partial_cleanup'
    assert result['frame_cache']['errors'][0]['frame_ref'] == 'edc-frame:head:0'
    assert image.exists() and manifest['views']['head']['cached_images']['0']['path'] == str(image)


def test_text_report_links_full_mapping_without_dumping_it(tmp_path):
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.reporting import write_text_report
    data = {'assessment_complete': False, 'rule_version': 'fixture',
        'physical_component': {'status': 'incomplete'}, 'candidates': [{
        'candidate_id': 'task_0:regression:0', 'description': 'synthetic',
        'start_frame': 10, 'end_frame': 10, 'source_start_time_sec': .333333,
        'source_end_time_sec': .333333, 'limitations': ['missing load'],
        'conclusions': [{'arm': 'right', 'primary_label': 'unknown_needs_review',
        'severity': -1, 'evidence': {'limitations': ['missing load'], 'physical_sources': {
        'ee_position': {'channel': 'pose/right', 'field': 'position', 'unit': 'm',
        'valid_samples': 10000, 'source_mapping': {'timestamps': list(range(10000))}}}}}]}]}
    write_text_report(data, tmp_path)
    text = (tmp_path/'attribution_report.txt').read_text()
    assert '../physical-signals.json' in text and '0.333' in text
    assert '9999' not in text and len(text) < 1000
    assert len(data['candidates'][0]['conclusions'][0]['evidence']['physical_sources']['ee_position']['source_mapping']['timestamps']) == 10000
