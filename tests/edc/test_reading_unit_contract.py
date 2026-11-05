"""Cross-format read contracts; synthetic values never stand for real acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from edc import open_dataset
from edc.data.ir.units import SIDataset, UnitInputError, require_si_field
from edc.data.readers.timeline import declared_timestamp_unit
from edc.algorithms.quality.adapters.cpu import builtins, preflight


def profile():
    return {'schema_version': 1, 'id': 'reading_contract', 'version': '1',
            'timestamp_column': 'timestamp', 'channels': {
                'joint_pos': {'modality': 'joint_space', 'fields': ['position'],
                              'unit': 'rad', 'time_source': 'acquisition', 'clock': 'robot', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}


def write_source(tmp_path, kind, times, count=None):
    values = np.arange((len(times) if count is None else count)*2, dtype=np.float64).reshape(-1, 2)
    if kind == 'hdf5':
        h5py = pytest.importorskip('h5py')
        path = tmp_path/'source.h5'
        with h5py.File(path, 'w') as handle:
            handle['timestamp'], handle['joint_pos'] = times, values
    elif kind == 'parquet':
        pa = pytest.importorskip('pyarrow'); pq = pytest.importorskip('pyarrow.parquet')
        path = tmp_path/'source.parquet'
        pq.write_table(pa.table({'timestamp': times, 'joint_pos': values.tolist()}), path)
    else:
        path = tmp_path/'source'; path.mkdir()
        (path/'trajectory.json').write_text(json.dumps({'data': [
            {'timestamp': t, 'joint_pos': row.tolist()} for t, row in zip(times, values)]}))
    return path, values


@pytest.mark.parametrize('kind', ['hdf5', 'parquet', 'episode'])
@pytest.mark.parametrize('declaration', ['omitted', None, '', 'unknown'])
def test_unconfirmed_timestamp_units_preserve_raw_evidence_and_disable_physics(tmp_path, kind, declaration):
    raw = [1776694770000000000, 1776694770000000001, 1776694770000000001]
    path, values = write_source(tmp_path, kind, raw)
    p = profile()
    if declaration != 'omitted': p['timestamp_unit'] = declaration
    with open_dataset(path, profile=p) as dataset:
        with next(dataset.iter_episodes()).opened() as ep:
            c = ep.channels['joint_pos']; topic = ep.raw(c.name)
            assert c.timestamps is None and c.time_source == 'unknown' and c.clock is None
            assert topic.recorded_timestamp_values == raw and topic.recorded_timestamp_unit == 'unknown'
            assert c.time_evidence['timestamp_unit_source'] == 'unconfirmed'
            np.testing.assert_array_equal(ep.read_signal(c.name, 'position'), values)
            algorithms = {p.spec.id: p for p in builtins()}
            for name in ('Check_Timestamp', 'Sync_Check', 'Root_Cause'):
                assert preflight(algorithms[name], ep)[0] == 'missing_input', name
            assert preflight(algorithms['Dedup_Check'], ep)[0] == 'runnable'
            result = algorithms['Dedup_Check'].run(SimpleNamespace(episode=ep), {})[0]
            assert result['dedup']['timestamp_dedup']['extra_duplicate_count'] == 1
            assert not result['establishes_acquisition_sync']


@pytest.mark.parametrize('kind', ['hdf5', 'parquet', 'episode'])
@pytest.mark.parametrize('unit,raw', [('s', [1., 1.004]), ('ms', [1000, 1004]),
                                    ('us', [1000000, 1004000]), ('ns', [1000000000, 1004000000])])
def test_declared_time_units_convert_once_without_claiming_sync(tmp_path, kind, unit, raw):
    path, _ = write_source(tmp_path, kind, raw)
    p = profile(); p['timestamp_unit'] = unit
    with open_dataset(path, profile=p) as dataset:
        with next(SIDataset(dataset).iter_episodes()).opened() as ep:
            np.testing.assert_allclose(ep.channels['joint_pos'].timestamps, [1., 1.004], rtol=0, atol=1e-15)
            c = ep.channels['joint_pos']
            assert c.time_source == 'acquisition'
            assert c.time_evidence['algorithm_timestamp_unit'] == 's'
            assert not c.time_evidence['independent_acquisition_timeline']
            assert not c.time_evidence['establishes_acquisition_sync']


@pytest.mark.parametrize('unit', [None, '', 'unknown', 'ms'])
def test_generated_seconds_are_not_scaled_by_raw_time_declaration(tmp_path, unit):
    path = tmp_path/'source'; path.mkdir()
    (path/'trajectory.json').write_text(json.dumps({'fps': 20, 'data': [{'joint_pos': [i]} for i in range(3)]}))
    p = profile(); p['timestamp_unit'] = unit
    with next(open_dataset(path, profile=p).iter_episodes()).opened() as ep:
        c = ep.channels['joint_pos']
        np.testing.assert_allclose(c.timestamps, [0, .05, .1])
        assert c.time_source == 'synthetic'
        assert c.time_evidence['timestamp_unit_source'] == 'generated_seconds'
        assert c.time_evidence['time_generated']


def test_unknown_time_unit_does_not_hide_hdf5_row_count_conflict(tmp_path):
    path, _ = write_source(tmp_path, 'hdf5', [1, 2, 3], count=2)
    with pytest.raises(ValueError, match='length mismatch'):
        next(open_dataset(path, profile=profile()).iter_episodes()).open()


@pytest.mark.parametrize('unit', ['minutes', 'rad', 'm', [], {}])
def test_bad_time_declarations_fail_explicitly(unit):
    with pytest.raises(ValueError, match='timestamp_unit'):
        declared_timestamp_unit(unit)


def test_mixed_physical_fields_are_normalized_individually_without_changing_source(tmp_path):
    h5py = pytest.importorskip('h5py')
    path = tmp_path/'mixed.h5'
    fields = {
        'joint_pos': ('joint_space', 'position', 'deg', 'rad', [90., np.nan], np.pi/180),
        'joint_vel': ('joint_space', 'velocity', 'deg/s', 'rad/s', [180., np.inf], np.pi/180),
        'joint_acc': ('joint_space', 'acceleration', 'deg/s^2', 'rad/s^2', [360., -np.inf], np.pi/180),
        'pose': ('pose', 'position', 'mm', 'm', [1000., -2000.], .001),
        'linear_vel': ('twist', 'linear', 'mm/s', 'm/s', [1000., 2000.], .001),
        'linear_acc': ('imu', 'lin_acc', 'mm/s^2', 'm/s^2', [9810., 0.], .001),
        'current': ('joint_space', 'current', 'mA', 'A', [1000., 250.], .001),
        'torque': ('wrench', 'torque', 'N*mm', 'N*m', [1000., 250.], .001),
        'force': ('wrench', 'force', 'N', 'N', [12., 25.], 1.),
        'touch': ('tactile', 'pressure', None, None, [12., 25.], None),
    }
    p = profile(); p['channels'] = {}
    p.pop('timestamp_column')
    with h5py.File(path, 'w') as handle:
        for name, (modality, field, unit, target, values, factor) in fields.items():
            handle[name] = np.asarray(values, np.float64).reshape(2, 1)
            p['channels'][name] = {'modality': modality, 'fields': [field], 'unit': unit,
                                   'source_kind': 'feedback', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}
        handle['depth'] = np.arange(8, dtype=np.uint16).reshape(2, 2, 2)
        p['channels']['depth'] = {'modality': 'depth', 'fields': ['distance'], 'unit': 'mm', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}
        handle['orientation'] = np.array([[0., 0., 0., 1.], [0., 0., 1., 0.]], np.float64)
        p['channels']['orientation'] = {'modality': 'pose', 'fields': ['orientation'], 'unit': '1',
                                       'pose_layout': {'orientation': [0, 4]},
                                       'representation': {'kind': 'quaternion', 'order': 'xyzw', 'quantity': 'orientation', 'semantic_status': 'confirmed'}}
    digest = hashlib.sha256(path.read_bytes()).hexdigest(); original = copy.deepcopy(p)
    with open_dataset(path, profile=p) as dataset:
        with next(SIDataset(dataset).iter_episodes()).opened() as ep:
            for name, (_, field, unit, target, values, factor) in fields.items():
                if unit is None:
                    with pytest.raises(UnitInputError): ep.read_signal(name, field)
                    np.testing.assert_array_equal(ep.inspect_recorded_signal(name, field)[:, 0], values)
                else:
                    assert require_si_field(ep, name, field, target) == target
                    np.testing.assert_allclose(ep.read_signal(name, field)[:, 0], np.asarray(values)*factor)
            np.testing.assert_allclose(ep.read_signal('depth', 'distance'), np.arange(8).reshape(2, 2, 2)*.001)
            np.testing.assert_array_equal(ep.read_signal('orientation', 'orientation'), [[0., 0., 0., 1.], [0., 0., 1., 0.]])
            with pytest.raises(UnitInputError, match='dimensions do not match'):
                require_si_field(ep, 'current', 'current', 'N*m')
            assert ep._source.read_signal('depth', 'distance').dtype == np.uint16
        assert dataset.profile == original
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('kind', ['hdf5', 'parquet', 'episode'])
def test_large_integer_source_signal_does_not_pass_through_float64(tmp_path, kind):
    raw = [2**60+1, 2**60+2]
    path, _ = write_source(tmp_path, kind, [1, 2])
    if kind == 'hdf5':
        import h5py
        with h5py.File(path, 'a') as handle:
            del handle['joint_pos']; handle['joint_pos'] = np.asarray(raw, np.int64).reshape(2, 1)
    elif kind == 'parquet':
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.table({'timestamp': [1, 2], 'joint_pos': [[x] for x in raw]}), path)
    else:
        (path/'trajectory.json').write_text(json.dumps({'data': [
            {'timestamp': i, 'joint_pos': [value]} for i, value in enumerate(raw)]}))
    with next(open_dataset(path, profile=profile()).iter_episodes()).opened() as ep:
        result = ep.read_signal('joint_pos', 'position')
        assert result.dtype.kind in 'iu'
        assert result[:, 0].tolist() == raw


def test_hdf5_float32_source_dtype_is_retained(tmp_path):
    h5py = pytest.importorskip('h5py'); path = tmp_path/'float32.h5'
    values = np.array([[.1], [np.nan], [np.inf], [-np.inf]], np.float32)
    with h5py.File(path, 'w') as handle: handle['joint_pos'] = values
    p = profile(); p.pop('timestamp_column')
    with next(open_dataset(path, profile=p).iter_episodes()).opened() as ep:
        result = ep.read_signal('joint_pos', 'position')
        assert result.dtype == np.float32
        np.testing.assert_array_equal(result, values)


def test_field_semantic_confirmation_is_checked_independently(tmp_path):
    path, _ = write_source(tmp_path, 'episode', [1, 2])
    p = profile()
    p['channels']['joint_pos'].update(
        representation={'quantity': 'angle', 'semantic_status': 'confirmed'},
        field_metadata={'position': {'quantity': 'unknown', 'semantic_status': 'pending'}})
    with next(SIDataset(open_dataset(path, profile=p)).iter_episodes()).opened() as ep:
        with pytest.raises(UnitInputError, match='unconfirmed'):
            ep.read_signal('joint_pos', 'position')
        np.testing.assert_array_equal(ep.inspect_recorded_signal('joint_pos', 'position'), [[0., 1.], [2., 3.]])


def test_boolean_contact_is_readable_without_turning_it_into_force(tmp_path):
    path = tmp_path/'source'; path.mkdir()
    (path/'trajectory.json').write_text(json.dumps({'data': [{'contact': True}, {'contact': False}]}))
    p = profile(); p.pop('timestamp_column')
    p['channels'] = {'contact': {'modality': 'contact', 'fields': ['active'], 'unit': '1', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}
    with next(SIDataset(open_dataset(path, profile=p)).iter_episodes()).opened() as ep:
        result = ep.read_signal('contact', 'active')
        assert result.dtype == np.bool_ and result.tolist() == [True, False]
        with pytest.raises(UnitInputError, match='dimensions do not match'):
            require_si_field(ep, 'contact', 'active', 'N')


@pytest.mark.parametrize('tensor', [False, True])
@pytest.mark.parametrize('dtype', [np.float32, np.float64, np.int64, np.uint64])
def test_parquet_primitive_and_list_source_dtypes_are_preserved(tmp_path, dtype, tensor):
    pa = pytest.importorskip('pyarrow'); pq = pytest.importorskip('pyarrow.parquet')
    source = tmp_path/'typed.parquet'
    values = np.array([2**60+1, 2**60+2] if np.issubdtype(dtype, np.integer) else [.1, np.nan], dtype=dtype)
    arrow_type = pa.from_numpy_dtype(dtype)
    if tensor:
        values = values.reshape(2, 1)
        arrow_type = pa.list_(arrow_type)
    pq.write_table(pa.table({'joint_pos': pa.array(values.tolist(), type=arrow_type)}), source)
    p = profile()
    with next(open_dataset(source, profile=p).iter_episodes()).opened() as ep:
        result = ep.read_signal('joint_pos', 'position')
        assert result.dtype == dtype
        np.testing.assert_array_equal(result, values)


def test_arrow_numeric_reading_without_pandas(tmp_path):
    pytest.importorskip('pyarrow')
    import edc
    script = r'''
import importlib.abc
import sys
class NoPandas(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pandas' or fullname.startswith('pandas.'):
            raise ModuleNotFoundError('pandas intentionally unavailable', name=fullname)
sys.meta_path.insert(0, NoPandas())
sys.path.insert(0, sys.argv[1])
import numpy as np
import pyarrow as pa
from edc.data.readers.structured import arrow_source_values

for dtype, values in [
    (np.float16, [.1, np.nan, np.inf, -np.inf]),
    (np.float32, [.1, np.nan, np.inf, -np.inf]),
    (np.float64, [.1, np.nan, np.inf, -np.inf]),
    (np.int64, [2**60 + 1, 2**60 + 2]),
    (np.uint64, [2**64 - 2, 2**64 - 1]),
    (np.bool_, [True, False]),
]:
    expected = np.asarray(values, dtype=dtype)
    leaf = pa.from_numpy_dtype(dtype)
    for kind in ('scalar', 'list', 'large_list', 'fixed_size_list'):
        shape = expected if kind == 'scalar' else expected.reshape(-1, 1)
        arrow_type = {'scalar': leaf, 'list': pa.list_(leaf),
                      'large_list': pa.large_list(leaf),
                      'fixed_size_list': pa.list_(leaf, 1)}[kind]
        column = pa.chunked_array([pa.array(shape.tolist(), type=arrow_type)])
        result = arrow_source_values(column)
        assert result.dtype == expected.dtype, (dtype, kind, result.dtype)
        np.testing.assert_array_equal(result, shape)

assert arrow_source_values(pa.array([[1], None], type=pa.list_(pa.int64()))) == [[1], None]
assert arrow_source_values(pa.array([[1], [2, 3]], type=pa.list_(pa.int64()))) == [[1], [2, 3]]
assert 'pandas' not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, '-I', '-c', script, str(Path(edc.__file__).resolve().parents[1])],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
