"""Representation conversions and hand/tactile IR; all fixtures synthetic."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from edc import convert_orientation, open_dataset, channel_catalog, resolve_channel
from edc.data.ir.units import SIEpisode
from edc.robots.profiles import load_profile

Q = {'kind': 'quaternion', 'order': 'xyzw'}
W = {'kind': 'quaternion', 'order': 'wxyz'}
R = {'kind': 'rpy', 'axes': 'xyz', 'convention': 'extrinsic', 'unit': 'deg'}
M = {'kind': 'rotation_matrix', 'layout': 'matrix'}


def test_known_axes_half_turns_and_quaternion_order():
    angles = [[0, 0, 0], [90, 0, 0], [0, 90, 0], [0, 0, 90], [180, 0, 0]]
    q = convert_orientation(angles, source=R, target=Q).output
    h = np.sqrt(.5)
    np.testing.assert_allclose(q, [[0, 0, 0, 1], [h, 0, 0, h], [0, h, 0, h], [0, 0, h, h], [1, 0, 0, 0]], atol=1e-14)
    w = convert_orientation(q, source=Q, target=W).output
    np.testing.assert_allclose(w, q[:, [3, 0, 1, 2]], atol=1e-14)
    np.testing.assert_allclose(convert_orientation(-q, source=Q, target=M).output,
                               convert_orientation(w, source=W, target=M).output, atol=1e-14)


@pytest.mark.parametrize('target', [Q, W, R, {**R, 'unit': 'rad'}, M,
                                  {**M, 'layout': 'row_major'}, {**M, 'layout': 'column_major'}])
def test_random_rotations_against_independent_scipy_oracle(target):
    rotation = pytest.importorskip('scipy.spatial.transform').Rotation
    angles = np.random.default_rng(42).uniform(-175, 175, (150, 3))
    expected = rotation.from_euler('xyz', angles, degrees=True).as_matrix()
    converted = convert_orientation(angles, source=R, target=target)
    actual = convert_orientation(converted.output, source=target, target=M).output
    np.testing.assert_allclose(actual, expected, atol=1e-12)


@pytest.mark.parametrize('pitch', [90, -90])
def test_gimbal_lock_has_equivalent_rotation_and_explicit_evidence(pitch):
    matrix = convert_orientation([[23, pitch, 57]], source=R, target=M).output
    result = convert_orientation(matrix, source=M, target=R)
    assert result.params['gimbal_lock_rows'] == [0]
    assert result.output[0, 2] == 0
    np.testing.assert_allclose(convert_orientation(result.output, source=R, target=M).output, matrix, atol=1e-12)


@pytest.mark.parametrize('values,source', [
    ([[0, 0, 0]], {}), ([[0, 0, 0]], {'kind': 'rpy', 'unit': 'deg'}),
    ([[0, 0, 0]], {**R, 'unit': 'unknown'}), ([[0, 0, 0]], {**R, 'convention': 'intrinsic'}),
    ([[0, 0, 0, 1]], {'kind': 'quaternion'}),
    ([[0, 0, 0, 0]], Q), ([[0, 0, 0, 2]], Q), ([[0, 0, np.nan, 1]], Q),
    ([[0, 0, np.inf]], R), ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], M),
    ([np.diag([1., 1., -1.])], M), ([np.diag([1., 1., 1.1])], M),
    ([[1, 0, 0, 0, 1, 0, 0, 0, 1]], {'kind': 'rotation_matrix'}),
    ([[0, 0, 0]], {**R, 'handedness': 'left'}),
    ([np.eye(3)], {**M, 'convention': 'passive'}),
])
def test_unknown_or_invalid_orientation_is_not_guessed_or_repaired(values, source):
    with pytest.raises(ValueError):
        convert_orientation(values, source=source, target=Q)


def make_dataset(tmp_path, kind, values):
    if kind == 'episode':
        path = tmp_path / 'episode'; path.mkdir()
        (path / 'trajectory.json').write_text(json.dumps({'data': [
            {'timestamp': i * .01, 'attitude': value} for i, value in enumerate(values.tolist())]}))
    elif kind == 'hdf5':
        h5py = pytest.importorskip('h5py'); path = tmp_path / 'episode.h5'
        with h5py.File(path, 'w') as f:
            f['timestamp'] = np.arange(len(values)) * .01
            f['attitude'] = values
    else:
        pa = pytest.importorskip('pyarrow'); pq = pytest.importorskip('pyarrow.parquet')
        path = tmp_path / 'episode.parquet'
        pq.write_table(pa.table({'timestamp': np.arange(len(values)) * .01, 'attitude': values.tolist()}), path)
    return path


@pytest.mark.parametrize('kind', ['episode', 'hdf5', 'parquet'])
@pytest.mark.parametrize('representation', [R, Q, W, M, {**M, 'layout': 'row_major'}, {**M, 'layout': 'column_major'}])
def test_cross_format_raw_preservation_and_explicit_conversion(tmp_path, kind, representation):
    angles = np.array([[0, 0, 90], [20, 30, -40.]])
    values = (angles if representation == R else convert_orientation(angles, source=R, target=representation).output)
    path = make_dataset(tmp_path, kind, values)
    profile = {'schema_version': 1, 'id': 'fixture', 'version': '1', 'timestamp_unit': 's', 'channels': {
        'attitude': {'logical_name': 'pose/tracker/main', 'modality': 'pose', 'fields': ['orientation'],
                     'unit': 'deg' if representation == R else '1', 'time_source': 'acquisition',
                     'representation': representation}}}
    source_file = path / 'trajectory.json' if kind == 'episode' else path
    before = source_file.read_bytes()
    with next(open_dataset(path, profile=profile).iter_episodes()).opened() as ep:
        raw_metadata = copy.deepcopy(ep.recorded_channels['attitude'].describe())
        np.testing.assert_array_equal(ep.inspect_recorded_signal('attitude', 'orientation'), values)
        np.testing.assert_allclose(ep.read_signal('attitude', 'orientation'),
                                   convert_orientation(values, source=representation, target=Q).output, atol=1e-12)
        result = ep.transform_orientation('pose/tracker/main', target=Q)
        np.testing.assert_allclose(convert_orientation(result.output, source=Q, target=M).output,
                                   convert_orientation(angles, source=R, target=M).output, atol=1e-12)
        assert result.source_mapping['channel'] == 'attitude'
        assert result.source_mapping['requested_channel'] == 'pose/tracker/main'
        assert result.source_mapping['rows'] == 'identity' and result.source_mapping['clock'] is None
        assert result.source_mapping['time_source'] == 'acquisition'
        assert ep.recorded_channels['attitude'].describe() == raw_metadata
        assert ep.channels['attitude'].field_spec('orientation')['dimensions'] == [4]
        np.testing.assert_array_equal(ep.inspect_recorded_signal('attitude', 'orientation'), values)
        # Algorithm execution view must not scale degree input twice.
        view = SIEpisode(ep).open()
        np.testing.assert_allclose(view.transform_orientation('attitude', target=Q).output, result.output)
    assert source_file.read_bytes() == before


def test_field_level_representation_and_rpy_unit_fallback(tmp_path):
    values = np.array([[0, 0, 90.]])
    path = make_dataset(tmp_path, 'episode', values)
    profile = {'schema_version': 1, 'id': 'fixture', 'version': '1', 'channels': {
        'attitude': {'modality': 'pose', 'fields': ['orientation'], 'field_metadata': {
            'orientation': {'unit': 'deg', 'dimensions': [3], 'representation': {k: v for k, v in R.items() if k != 'unit'}}}}}}
    with next(open_dataset(path, profile=profile).iter_episodes()).opened() as ep:
        q = ep.transform_orientation('attitude', target=Q).output
        np.testing.assert_allclose(q, [[0, 0, np.sqrt(.5), np.sqrt(.5)]], atol=1e-12)
    bad = copy.deepcopy(profile)
    bad['channels']['attitude']['field_metadata']['orientation']['dimensions'] = [4]
    with pytest.raises(ValueError, match='dimensions conflict'):
        load_profile(bad)
    bad = copy.deepcopy(profile)
    bad['channels']['attitude']['field_metadata']['orientation'] = {
        'representation': {**Q, 'unit': '1'}, 'unit': 'deg'}
    with pytest.raises(ValueError, match='unit conflicts'):
        load_profile(bad)


def test_ros_encoding_evidence_cannot_be_relabelled():
    from edc.data.ir.dataset import Channel
    from edc.data.transforms.orientation import source_orientation_metadata
    c = Channel('/odom', 'odometry', ['orientation'], 1, 'fixture',
                field_metadata={'orientation': {'dimensions': None, 'representation': {'kind': 'unknown'}}})
    source_orientation_metadata(c, 'nav_msgs/msg/Odometry')
    assert c.field_spec('orientation')['representation']['order'] == 'xyzw'
    assert c.field_spec('orientation')['dimensions'] == [4]
    assert c.field_spec('orientation')['unit'] is None
    assert c.calibration_status == 'unknown'
    c.field_metadata['orientation']['representation'] = R
    with pytest.raises(ValueError, match='ROS orientation schema'):
        source_orientation_metadata(c, 'nav_msgs/msg/Odometry')






def test_registered_algorithm_can_request_orientation_conversion(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from edc import AlgorithmSpec, Registry, run
    path = make_dataset(tmp_path, 'episode', np.array([[0, 0, 90.], [0, 0, 180.]]))
    profile = {'schema_version': 1, 'id': 'fixture', 'version': '1', 'channels': {
        'attitude': {'logical_name': 'pose/tracker/main', 'modality': 'pose',
                     'fields': ['orientation'], 'unit': 'deg', 'representation': R}}}
    def evaluate(context, params):
        result = context.episode.transform_orientation('pose/tracker/main', target=Q)
        return {'assessment_complete': False, 'orientation_transform': result.describe()}
    registry = Registry('edc.algorithms')
    registry.register(SimpleNamespace(spec=AlgorithmSpec('test__orientation', '1', 'Fixture', 'Test', 'Synthetic conversion check'), run=evaluate))
    monkeypatch.setattr('edc.execution.runner.algorithms', lambda: registry)
    with open_dataset(path, profile=profile) as ds:
        result = run(ds, output=tmp_path/'out', algorithm_ids=['test__orientation'])
    assert result['status_counts'] == {'completed': 1}
    record = json.loads(next((tmp_path/'out').rglob('records.jsonl')).read_text())
    evidence = record['result']['reports'][0]['orientation_transform']
    assert evidence['source_mapping']['requested_channel'] == 'pose/tracker/main'
    assert evidence['params']['source_representation']['unit'] == 'deg'
    assert np.asarray(evidence['output']).shape == (2, 4)


@pytest.mark.parametrize('representation', [R, M])
def test_official_lerobot_orientation_hand_and_tactile_fidelity(tmp_path, representation):
    pytest.importorskip('lerobot')
    h5py = pytest.importorskip('h5py')
    from edc import convert_to_lerobot
    angles = np.array([[0., 0., 90.], [15., 30., 45.]])
    values = angles if representation == R else convert_orientation(angles, source=R, target=M).output
    source = tmp_path/'source.h5'
    with h5py.File(source, 'w') as f:
        f['raw_time'] = [.003, .014]
        f['attitude'] = values
        f['hand'] = np.arange(10, dtype=float).reshape(2, 5)
        f['touch'] = np.arange(12, dtype=float).reshape(2, 2, 3)
    p = {'schema_version': 1, 'id': 'fixture', 'version': '1',
         'timestamp_column': 'raw_time', 'timestamp_unit': 's', 'channels': {
        'attitude': {'logical_name': 'pose/tracker/main', 'modality': 'pose', 'fields': ['orientation'],
                     'unit': 'deg' if representation == R else '1', 'representation': representation},
        'hand': {'logical_name': 'joint/hand/right/feedback', 'modality': 'joint_space',
                 'fields': ['position'], 'side': 'right', 'source_kind': 'feedback',
                 'representation': {'device_type': 'dexterous_hand'}},
        'touch': {'logical_name': 'tactile/hand/right@palm', 'modality': 'tactile',
                  'fields': ['taxels'], 'side': 'right', 'dimensions': [2, 3]}}}
    before = source.read_bytes()
    converted = tmp_path/'converted'
    assert convert_to_lerobot(source, profile=p, output=converted, fps=30)['fidelity_status'] == 'passed'
    with next(open_dataset(converted).iter_episodes()).opened() as ep:
        assert ep.backend == 'official'
        np.testing.assert_array_equal(ep.inspect_recorded_signal('attitude', 'orientation'), values)
        np.testing.assert_allclose(ep.read_signal('attitude', 'orientation'),
                                   convert_orientation(values, source=representation, target=Q).output, atol=1e-12)
        np.testing.assert_array_equal(ep.read_signal('hand', 'position'), np.arange(10).reshape(2, 5))
        np.testing.assert_array_equal(ep.read_signal('touch', 'taxels'), np.arange(12).reshape(2, 2, 3))
        actual = ep.transform_orientation('pose/tracker/main', target=M)
        np.testing.assert_allclose(actual.output, convert_orientation(angles, source=R, target=M).output, atol=1e-12)
        assert actual.source_mapping['original_source_mapping']['feature']
        assert resolve_channel(ep, 'tactile/hand/right@palm') == 'touch'
    assert source.read_bytes() == before
