"""Recording time is useful evidence, independently of acquisition clocks."""
import copy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from edc.algorithms.quality.adapters.cpu import preflight
from edc.data.ir.dataset import Channel, open_dataset
from edc.data.transforms.core import TransformResult
from edc.execution.completeness import report_complete
from edc.execution.runner import Context, run
from test_config_native_timeline import native_profile, plugin, write_mcap


PARAMS = {'timeline': {'time_source': 'container'}}


@pytest.mark.parametrize('ident', ['Sync_Check', 'Root_Cause'])
def test_real_recorded_axes_run_without_device_mapping_and_preserve_sources(tmp_path, ident):
    path = tmp_path/'axes.mcap'
    write_mcap(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with next(open_dataset(path, profile=native_profile()).iter_episodes()).opened() as ep:
        channels = copy.deepcopy(ep.channels)
        assert preflight(plugin(ident), ep)[0] == 'missing_input'
        params = dict(PARAMS, primary='/head') if ident == 'Sync_Check' else PARAMS
        assert preflight(plugin(ident), ep, params)[0] == 'runnable'
        assert plugin(ident).availability(ep, params)['scope'] == 'container_recording_diagnostic'
        result = plugin(ident).run(Context(None, ep, tmp_path), params)
        assert result['recording_diagnostic']['status'] == 'completed'
        assert '/zero' in result['recording_diagnostic']['included_channels']
        assert result['time_source'] == 'container'
        assert result['score'] == result['method_score']
        assert result['verdict'] == result['method_verdict']
        assert result['passed'] == result['method_passed']
        assert report_complete(result, ident) == bool(result['method_assessment_complete'])
        assert not result['establishes_acquisition_sync']
        assert result['acquisition_assessment']['status'] == 'unassessed'
        assert 'clock_shift_s' not in result and 'clock_diagnosis' not in result
        evidence = json.loads((tmp_path/'container_timeline.json').read_text())
        axis = evidence['source_mapping']['/zero']
        assert axis['source_mapping']['rows'] == 'identity'
        assert axis['source_mapping']['timestamps_ns'][0] == 20_000_000_000
        assert axis['output'][0] == 20
        assert ep.channels == channels
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def fake_episode(monkeypatch, *, gap=False, bad=False, clock_conflict=False):
    axes = {name: np.arange(360)*.01 for name in ('/head/rgb', '/left/joints', '/right/joints')}
    if gap:
        for times in axes.values():
            times[120:] += .7
    if bad:
        axes['/bad/joints'] = np.array([0., .2, .1])
    channels = {name: Channel(name, 'other', [], len(times), '/synthetic.mcap',
                             timestamps=(times+100).tolist(), time_source='acquisition', clock=None)
                for name, times in axes.items()}
    ep = SimpleNamespace(channels=channels, profile={})

    def axis(episode, name, *, time_source):
        assert time_source == 'container'
        clock = 'other' if clock_conflict and name == '/left/joints' else 'recorder'
        return TransformResult(axes[name], {'time_source': time_source, 'clock': clock, 'unit': 's'},
                               {'rows': 'identity', 'timestamps_ns': (axes[name]*1e9).astype(int).tolist()})
    monkeypatch.setattr('edc.algorithms.quality.adapters.recording.time_axis', axis)
    return ep


def test_container_root_cause_finds_correlated_recording_gaps(monkeypatch, tmp_path):
    ep = fake_episode(monkeypatch, gap=True)
    result = plugin('Root_Cause').run(Context(None, ep, tmp_path), PARAMS)
    root = result['root_cause']
    assert len(root['correlated_gaps']) == 1 and root['max_gap_s'] == .71
    assert root['correlated_gaps'][0]['gaps'][0]['after_index'] == 120
    assert result['recording_diagnostic']['assessment_complete']
    assert result['verdict'] == 'DO_NOT_USE' and result['passed'] is False
    assert result['method_verdict'] == 'DO_NOT_USE'
    assert '记录' in result['reasons'][0]
    assert root['has_drift'] is False and root['has_jump'] is False


@pytest.mark.parametrize('ident', ['Sync_Check', 'Root_Cause'])
def test_completed_clean_recording_check_keeps_original_pass(monkeypatch, tmp_path, ident):
    ep = fake_episode(monkeypatch)
    result = plugin(ident).run(Context(None, ep, tmp_path), PARAMS)
    assert result['verdict'] == result['method_verdict'] == 'ACCEPTABLE'
    assert result['score'] == result['method_score'] == 100
    assert result['passed'] is True and report_complete(result, ident)
    assert plugin(ident).availability(ep, PARAMS)['availability'] == 'available'
    assert result['acquisition_assessment']['status'] == 'unassessed'
    assert not result['establishes_acquisition_sync']


@pytest.mark.parametrize('ident', ['Sync_Check', 'Root_Cause'])
def test_bad_container_axis_is_recorded_and_cannot_hide_partial_coverage(monkeypatch, tmp_path, ident):
    ep = fake_episode(monkeypatch, bad=True)
    assert preflight(plugin(ident), ep, PARAMS)[0] == 'runnable'
    result = plugin(ident).run(Context(None, ep, tmp_path), PARAMS)
    assert not result['recording_diagnostic']['assessment_complete']
    assert result['recording_diagnostic']['unavailable_inputs'][0]['channel'] == '/bad/joints'
    evidence = json.loads((tmp_path/'container_timeline.json').read_text())
    assert evidence['source_mapping']['/bad/joints']['output'] == [0., .2, .1]


@pytest.mark.parametrize('ident', ['Sync_Check', 'Root_Cause'])
def test_container_mode_rejects_different_clocks_and_acquisition_mapping(monkeypatch, ident):
    ep = fake_episode(monkeypatch, clock_conflict=True)
    status, reason = preflight(plugin(ident), ep, PARAMS)
    assert status == 'missing_input' and '容器 clock' in reason
    status, reason = preflight(plugin(ident), ep, dict(PARAMS, target_clock='device'))
    assert status == 'missing_input' and 'target_clock' in reason


def test_short_incomplete_recording_diagnostics_do_not_become_quality_pass(tmp_path):
    source = tmp_path/'record.mcap'; write_mcap(source)
    output = tmp_path/'out'
    with open_dataset(source, profile=native_profile()) as dataset:
        run(dataset, output=output, algorithm_ids=['Sync_Check', 'Root_Cause'],
            params={ident: PARAMS for ident in ('Sync_Check', 'Root_Cause')})
    catalog = json.loads((output/'catalog.json').read_text())
    records = [json.loads(line) for ref in catalog['runs'][-1]['records']
               for line in (output/ref).read_text().splitlines()]
    assert len(records) == 2 and all(r['status'] == 'completed' for r in records)
    selection = json.loads((output/catalog['runs'][-1]['selection']).read_text())
    assert selection[0]['decision'] == 'unassessed' and not selection[0]['assessment_complete']
