"""Default quaternion IR, source audit, execution and failure isolation."""
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from edc import (AlgorithmSpec, Registry, OrientationInputError, channel_catalog,
                 dataset_reference, open_dataset, open_dataset_reference, open_source_dataset, run)
from edc.data.ir.units import SIEpisode

Q = {'kind': 'quaternion', 'order': 'xyzw', 'unit': '1'}
R = {'kind': 'rpy', 'axes': 'xyz', 'convention': 'extrinsic', 'unit': 'deg'}


def fixture(tmp_path, values=None, representation=R):
    path = tmp_path/'source'; path.mkdir()
    values = [[0., 0., 90.], [0., 0., 0.]] if values is None else values
    (path/'trajectory.json').write_text(json.dumps({'data': [
        {'timestamp': 10 + i*.07, 'attitude': v, 'hand': [i, i+1]} for i, v in enumerate(values)]}))
    channel = {'logical_name': 'pose/tracker/main', 'modality': 'pose', 'fields': ['orientation'],
               'time_source': 'acquisition', 'clock': 'fixture_clock', 'coordinate_frame': 'world',
               'calibration_status': 'unknown'}
    if representation is not None:
        channel['representation'] = copy.deepcopy(representation)
        channel['unit'] = representation.get('unit', '1')
    profile = {'schema_version': 1, 'id': 'fixture', 'version': '1', 'timestamp_unit': 's',
               'channels': {'attitude': channel, 'hand': {'modality': 'joint_space', 'fields': ['position']}}}
    return path, profile


def test_public_and_config_reads_are_canonical_but_source_audit_is_lossless(tmp_path):
    from edc import open_config_dataset
    path, p = fixture(tmp_path)
    before = (path/'trajectory.json').read_bytes()
    cfg = {'schema_version': 'edc_config_v1', 'dataset': {'path': str(path)}, 'robot': p,
           'algorithms': {'methods': {'Numeric_Check': {}}},
           'workflow': {'id': 'fixture', 'stages': [{'id': 'quality', 'kind': 'quality', 'input': 'source', 'algorithms': ['Numeric_Check']}]}}
    expected = np.array([[0, 0, np.sqrt(.5), np.sqrt(.5)], [0, 0, 0, 1]])
    for dataset in [open_dataset(path, profile=p), open_config_dataset(cfg)]:
        with dataset, next(dataset.iter_episodes()).opened() as ep:
            np.testing.assert_allclose(ep.read_signal('pose/tracker/main', 'orientation'), expected)
            rows = ep.read_values('attitude', indices=[1, 0, 1])
            np.testing.assert_allclose([r['orientation'] for r in rows], expected[[1, 0, 1]])
            info, reader = ep.algorithm_view()
            assert info.topics['attitude'].field_metadata['orientation']['representation'] == Q
            np.testing.assert_allclose(reader.read_field(info, 'attitude', 'orientation'), expected)
            np.testing.assert_allclose(reader.channel_values('attitude')[0]['orientation'], expected[0])
            assert ep.channels['attitude'].timestamps == [10, 10.07]
            assert ep.channels['attitude'].clock == 'fixture_clock'
            assert ep.channels['attitude'].calibration_status == 'unknown'
            meta = channel_catalog(ep)['channels'][0]['field_metadata']['orientation']
            assert meta['dimensions'] == [4] and meta['unit'] == '1' and meta['source_unit'] == 'deg'
            np.testing.assert_array_equal(ep.inspect_recorded_signal('attitude', 'orientation'), [[0, 0, 90], [0, 0, 0]])
    with next(open_source_dataset(path, profile=p).iter_episodes()).opened() as ep:
        np.testing.assert_array_equal(ep.read_signal('attitude', 'orientation'), [[0, 0, 90], [0, 0, 0]])
        assert ep.channels['attitude'].representation == R
    assert before == (path/'trajectory.json').read_bytes()


@pytest.mark.parametrize('manual', [False, True])
def test_execution_wraps_source_or_canonical_once_and_preserves_evidence(tmp_path, manual):
    path, p = fixture(tmp_path)
    expected = [[0, 0, np.sqrt(.5), np.sqrt(.5)], [0, 0, 0, 1]]
    for opener in [open_source_dataset, open_dataset]:
        with opener(path, profile=p) as ds:
            ep = SIEpisode(next(ds.iter_episodes()), 'manual' if manual else 'auto')
            with ep.opened():
                np.testing.assert_allclose(ep.read_signal('pose/tracker/main', 'orientation'), expected)
                e = ep.orientation_evidence()['fields'][0]
                assert e['read_count'] == 1 and e['normalization_applied']
                assert e['params']['source_representation']['unit'] == 'deg'
                assert e['source_mapping']['rows'] == 'identity'
                assert e['source_mapping']['clock'] == 'fixture_clock'
                assert e['source_mapping']['coordinate_frame'] == 'world'
                assert ep.unit_evidence()['fields'][0]['si_read_count'] == 0
                ep.reset_unit_evidence()
                assert not ep.orientation_evidence()['fields'][0]['normalization_applied']


@pytest.mark.parametrize('representation,values,error', [
    (None, [[0, 0, 90]], OrientationInputError),
    (Q, [[0, 0, 0, 0]], ValueError),
    (Q, [[0, 0, float('nan'), 1]], ValueError),
    ({'kind': 'rotation_matrix', 'layout': 'matrix'}, [[[1, 0, 0], [0, 1, 0], [0, 0, -1]]], ValueError),
])
def test_invalid_or_unknown_never_leaks_noncanonical_values(tmp_path, representation, values, error):
    path, p = fixture(tmp_path, values, representation)
    with next(open_dataset(path, profile=p).iter_episodes()).opened() as ep:
        with pytest.raises(error): ep.read_signal('attitude', 'orientation')
        with pytest.raises(error): ep.read_values('attitude')
        np.testing.assert_array_equal(ep.inspect_recorded_signal('attitude', 'orientation'), values)
        np.testing.assert_array_equal(ep.read_signal('hand', 'position'), [[0, 1]])
        assert not ep.orientation_evidence()['fields'][0]['normalization_applied']


def test_serialized_dataset_reference_reopens_canonical_without_changing_fingerprint(tmp_path):
    path, p = fixture(tmp_path)
    with open_dataset(path, profile=p) as ds:
        ref = dataset_reference(ds)
    with open_dataset_reference(ref) as ds:
        assert dataset_reference(ds) == ref
        with next(ds.iter_episodes()).opened() as ep:
            assert ep.read_signal('attitude', 'orientation').shape == (2, 4)


@pytest.mark.parametrize('bad', [False, True])
def test_registered_plugins_receive_quaternions_and_record_automatic_evidence(tmp_path, monkeypatch, bad):
    path, p = fixture(tmp_path, representation=None if bad else R)
    registry = Registry('edc.algorithms')
    def evaluate(context, params):
        a = context.episode.read_signal('pose/tracker/main', 'orientation')
        with next(context.dataset.iter_episodes()).opened() as other:
            b = other.read_values('attitude')
            np.testing.assert_allclose(a, [r['orientation'] for r in b])
        return {'assessment_complete': False, 'shape': list(a.shape)}
    registry.register(SimpleNamespace(spec=AlgorithmSpec('test__canonical', '1', 'Fixture', 'Test', 'Canonical input'), run=evaluate))
    registry.register(SimpleNamespace(spec=AlgorithmSpec('test__unrelated', '1', 'Fixture', 'Test', 'Isolation'), run=lambda c,p: {'assessment_complete':False}))
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    output = tmp_path/'output'
    with open_dataset(path, profile=p) as ds: run(ds, algorithm_ids=list(registry.plugins), output=output)
    record = json.loads(next(output.glob('_runs/*/test__canonical/records.jsonl')).read_text())
    assert record['status'] == ('missing_input' if bad else 'completed')
    evidence = json.loads((output/record['orientation_normalization']['evidence']).read_text())
    field = evidence['fields'][0]
    assert field['normalization_applied'] is not bad
    assert field['read_count'] == (0 if bad else 2)
    assert json.loads(next(output.glob('_runs/*/test__unrelated/records.jsonl')).read_text())['status'] == 'completed'
    assert (output/'catalog.json').is_file() and (output/'report.html').is_file()


def test_numeric_check_preserves_invalid_source_orientation_diagnostics(tmp_path):
    path, p = fixture(tmp_path, [[0, 0, float('nan'), 1]], Q)
    out = tmp_path/'output'
    with open_dataset(path, profile=p) as ds: run(ds, algorithm_ids=['Numeric_Check'], output=out)
    record = json.loads(next(out.glob('_runs/*/Numeric_Check/records.jsonl')).read_text())
    assert record['status'] == 'completed'
    item = next(r for r in record['result']['reports'] if r['input_field'] == 'orientation')
    assert item['nan_count'] == 1 and item['nonfinite_indices'] == [[0, 2]]
    assert item['input_scope'] == 'recorded_finiteness_only'
    assert not item['physical_checks_applied']


def test_dataset_plugin_retains_reads_after_close_and_resets_between_algorithms(tmp_path, monkeypatch):
    path, p = fixture(tmp_path)
    registry = Registry('edc.algorithms')
    def evaluate(context, params):
        for ep in context.dataset.iter_episodes():
            with ep.opened():
                q = ep.read_signal('attitude', 'orientation')
                assert q.shape == (2, 4)
        return {'assessment_complete': False}
    for ident, fn in [('test__dataset_q', evaluate),
                      ('test__dataset_unread', lambda c, p: {'assessment_complete': False})]:
        registry.register(SimpleNamespace(spec=AlgorithmSpec(ident, '1', 'Fixture', 'Test',
            'Dataset orientation evidence', granularity='dataset'), run=fn,
            preflight_dataset=lambda ds, params: ('runnable', '')))
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    out = tmp_path/'output'
    with open_dataset(path, profile=p) as ds:
        run(ds, algorithm_ids=list(registry.plugins), output=out)
    for ident, expected in [('test__dataset_q', 1), ('test__dataset_unread', 0)]:
        record = json.loads(next(out.glob(f'_runs/*/{ident}/records.jsonl')).read_text())
        assert record['status'] == 'completed'
        evidence = json.loads((out/record['orientation_normalization']['evidence']).read_text())
        field = evidence['episodes'][0]['evidence']['fields'][0]
        assert field['read_count'] == expected
        assert field['normalization_applied'] == bool(expected)
