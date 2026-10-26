"""Boundary/fidelity regressions; official integrations skip in the NumPy-only install."""
import json
from pathlib import Path
import sys
import zlib
import numpy as np
import pytest
from edc import open_dataset, convert_to_lerobot
from edc.data.readers.lerobot_official import bindings


def profile():
    return {'schema_version': 1, 'id': 'fidelity_fixture', 'version': '1',
            'timestamp_column': 'raw_time', 'timestamp_unit': 'unknown',
            'lerobot': {'non_frame_fields': ['subtask_transitions']},
            'channels': {'angle': {'modality': 'joint_space', 'fields': ['position'], 'unit': 'deg'},
                         'rgb': {'modality': 'rgb'}, 'depth': {'modality': 'depth'}, '*': {'modality': 'other'}}}


def hdf(tmp_path, *, bad_rows=False, bad_depth=False):
    h5py = pytest.importorskip('h5py')
    from PIL import Image
    import io
    path = tmp_path / 'source.hdf5'
    with h5py.File(path, 'w') as f:
        f['raw_time'] = np.array([9.12345678901234, 10., 11.], dtype=np.float64)
        f['angle'] = np.array([[90.12345678901234], [np.inf], [-np.inf]], dtype=np.float64)
        f['nan'] = np.array([np.nan, -0., 1.0000000000000002], dtype=np.float64)
        f['empty'] = np.empty((3, 0))
        f['subtask_transitions'] = np.array([2], dtype=np.int32)
        f['integer'] = np.array([[2**60+1], [2**60+2], [2**60+3]], dtype=np.int64)
        if bad_rows:
            f['short'] = np.ones((2, 1))
        for name in ('rgb', 'depth'):
            ds = f.create_dataset(name, (3,), dtype=h5py.vlen_dtype(np.dtype('uint8')))
            ds.attrs['orig_shape'] = '[4 6 3]' if name == 'rgb' else '[4 6]'
            if name == 'depth':
                ds.attrs['compressed_with'], ds.attrs['orig_dtype'] = 'zlib', 'uint16'
            for i in range(3):
                if name == 'rgb':
                    buffer = io.BytesIO()
                    Image.fromarray(np.full((4, 6, 3), i*40, np.uint8)).save(buffer, format='JPEG')
                    payload = buffer.getvalue()
                else:
                    payload = b'broken' if bad_depth and i == 1 else zlib.compress((np.arange(24, dtype=np.uint16)+i*1000).tobytes())
                ds[i] = np.frombuffer(payload, dtype=np.uint8)
    return path


def official():
    pytest.importorskip('lerobot')
    from edc.data.readers.lerobot_official import official_class
    official_class()


def test_feature_mapping_rejects_missing_and_semantic_duplicates():
    with pytest.raises(ValueError, match='Unmapped'):
        bindings({'lerobot': {'feature_mapping': {}}}, {'x': {'dtype': 'float64', 'shape': [2]}})
    with pytest.raises(ValueError, match='semantics'):
        bindings({'lerobot': {'feature_mapping': {'a': {'feature': 'x', 'unit': 'rad'}}}}, {'x': {}})


def test_unknown_backend_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='backend'):
        open_dataset(tmp_path, backend='magic')


def test_right_pose_never_falls_back_to_left():
    from edc.data.readers.base import DataReader
    from edc.data.ir.metadata import DataInfo, TopicInfo, IRNotProvided
    info = DataInfo(path='fixture', topics={'/left/pose': TopicInfo('/left/pose', modality='pose', value_available=True, fields=['position'])})
    with pytest.raises(IRNotProvided, match='opposite-side'):
        DataReader.require_pose(None, info, side='right')
    assert DataReader.require_pose(None, info, side='left')[0] == '/left/pose'


def test_source_and_output_protection(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    with pytest.raises(FileExistsError):
        convert_to_lerobot(source, profile=profile(), output=source)
    with pytest.raises(ValueError, match='overlap'):
        convert_to_lerobot(source, profile=profile(), output=source/'converted')


def test_official_fidelity_and_unknown_semantics(tmp_path):
    official()
    source = hdf(tmp_path)
    output = tmp_path / 'converted'
    result = convert_to_lerobot(source, profile=profile(), output=output, fps=30)
    assert result['fidelity_status'] == 'passed'
    assert result['quality_status'] == 'not_assessed'
    assert result['training_frames'] == 3
    assert result['source_unchanged']
    from edc.data.ir.units import SIEpisode, UnitInputError
    with open_dataset(output) as dataset:
        with next(dataset.iter_episodes()).opened() as ep:
            assert ep.backend == 'official'
            assert ep.channels['angle'].timestamps is None
            assert ep.channels['angle'].time_source == 'unknown'
            assert ep.read_signal('angle', 'position').dtype == np.float64
            assert ep.read_signal('integer', 'recorded_values')[0, 0] == 2**60+1
            assert ep.read_signal('nan', 'recorded_values').shape == (3,)
            assert ep.read_signal('depth', 'recorded_values').dtype == np.uint16
            assert ep.channels['depth'].modality == 'depth'
            assert len(list(ep.iter_frames('rgb'))) == 3
            with pytest.raises(UnitInputError, match='unconfirmed'):
                SIEpisode(ep).read_signal('angle', 'position')
    evidence = result['episodes'][0]['inventory']
    assert {x['field'] for x in evidence if x['disposition'] != 'frame_feature'} == {'empty', 'subtask_transitions'}
    (output/'data/chunk-000/file-000.parquet').unlink()
    with open_dataset(output) as dataset:
        with pytest.raises(FileNotFoundError):
            next(dataset.iter_episodes()).open()


@pytest.mark.parametrize('failure', ['bad_rows', 'bad_depth'])
def test_corrupt_or_multirate_conversion_is_not_published(tmp_path, failure):
    official()
    source = hdf(tmp_path, **{failure: True})
    output = tmp_path / 'bad'
    with pytest.raises((ValueError, zlib.error)):
        convert_to_lerobot(source, profile=profile(), output=output, fps=30)
    acceptance = json.loads((output/'acceptance.json').read_text())
    assert acceptance['conversion_status'] == 'failed'
    assert acceptance['fidelity_status'] == 'failed'


def test_confirmed_degree_input_converts_only_during_execution(tmp_path):
    official()
    p = profile()
    p['channels']['angle']['representation'] = {'quantity': 'angle', 'semantic_status': 'confirmed'}
    output = tmp_path/'converted'
    convert_to_lerobot(hdf(tmp_path), profile=p, output=output, fps=30)
    from edc.data.ir.units import SIEpisode, UnitInputError
    with open_dataset(output) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            raw = ep.read_signal('angle', 'position')
            si = SIEpisode(ep)
            np.testing.assert_array_equal(si.read_signal('angle', 'position'), np.deg2rad(raw))
            assert raw[0, 0] == 90.12345678901234
            assert si.unit_evidence()['fields'][0]['source_mapping']
            with pytest.raises(UnitInputError, match='disabled'):
                SIEpisode(ep, 'manual').read_signal('angle', 'position')


def test_known_original_time_is_separate_from_index_time(tmp_path):
    official()
    p = profile()
    p['timestamp_unit'] = 'ms'
    p['lerobot']['original_time'] = {'time_source': 'acquisition', 'clock': 'recorder'}
    output = tmp_path/'converted'
    convert_to_lerobot(hdf(tmp_path), profile=p, output=output, fps=30)
    with open_dataset(output) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            assert ep.channels['angle'].time_source == 'acquisition'
            assert ep.channels['angle'].clock == 'recorder'
            assert ep.channels['angle'].timestamps[0] == pytest.approx(.00912345678901234)
            assert ep.read_signal('raw_time', 'recorded_values')[0] == 9.12345678901234
            assert ep.channels['angle'].time_evidence['timeline_scope']=='shared_dataset_rows'
            assert ep.image_storage('rgb') == 'frames'
            frames = list(ep.iter_frames('rgb'))
            assert [frame['source_time'] for frame in frames] == pytest.approx(
                [9.12345678901234 / 1000, .01, .011])
            assert [frame['playback_time'] for frame in frames] == pytest.approx([0, 1/30, 2/30])
            assert [frame['source_index'] for frame in frames] == [0, 1, 2]
            assert not list(output.rglob('*.mp4'))
            from edc.algorithms.quality.adapters.cpu import builtins, preflight
            plugins={plugin.spec.id:plugin for plugin in builtins()}
            assert preflight(plugins['Sync_Check'],ep)[0]=='missing_input'
            assert preflight(plugins['Root_Cause'],ep)[0]=='missing_input'
            assert len(plugins['Dedup_Check'].run(type('Context',(),{'episode':ep})(),{}))==1
    import yaml
    saved = yaml.safe_load((output/'robot.yaml').read_text())
    saved['channels']['angle']['clock'] = 'other'
    with open_dataset(output, profile=saved) as ds:
        with pytest.raises(ValueError, match='conflicting'):
            next(ds.iter_episodes()).open()


@pytest.mark.parametrize('declaration', ['omitted', None, '', 'unknown'])
def test_official_original_time_blank_unit_remains_unknown(tmp_path, declaration):
    official()
    output = tmp_path/'converted'
    convert_to_lerobot(hdf(tmp_path), profile=profile(), output=output, fps=30)
    import yaml
    saved = yaml.safe_load((output/'robot.yaml').read_text())
    timeline = saved['lerobot']['original_time']
    if declaration == 'omitted': timeline.pop('unit', None)
    else: timeline['unit'] = declaration
    for spec in saved['channels'].values():
        spec.update(time_source='acquisition', clock='declared')
    with open_dataset(output, profile=saved) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            c = ep.channels['angle']; topic = ep.describe_legacy().topics['angle']
            assert c.timestamps is None and c.time_source == 'unknown' and c.clock is None
            assert topic.recorded_timestamp_unit == 'unknown'
            assert topic.recorded_timestamp_values == [9.12345678901234, 10., 11.]
            assert c.time_evidence['algorithm_timestamp_unit'] is None


def test_missing_official_dependency_is_actionable(monkeypatch):
    from importlib.metadata import PackageNotFoundError
    import edc.data.readers.lerobot_official as module
    def missing(name):
        raise PackageNotFoundError(name)
    monkeypatch.setattr(module, 'version', missing)
    with pytest.raises(ImportError, match='embodied-data-curation'):
        module.official_class()


def test_failed_conversion_cannot_enter_algorithm_execution(tmp_path):
    (tmp_path/'acceptance.json').write_text(json.dumps({'conversion_status': 'failed'}))
    with pytest.raises(ValueError, match='acceptance failed'):
        open_dataset(tmp_path)


def v21(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from edc.data.readers.video import encode_frames
    root = tmp_path/'v21'
    (root/'meta').mkdir(parents=True)
    features = {'q': {'dtype':'float32', 'shape':[2], 'names':None},
                'rgb': {'dtype':'video', 'shape':[8,8,3], 'names':['height','width','channels']}}
    for key in ('timestamp','frame_index','episode_index','index','task_index'):
        features[key] = {'dtype': 'float32' if key == 'timestamp' else 'int64', 'shape':[1], 'names':None}
    info = {'codebase_version':'v2.1', 'fps':10, 'chunks_size':1000, 'total_episodes':2,
            'total_frames':6, 'features':features,
            'data_path':'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet',
            'video_path':'videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4'}
    (root/'meta/info.json').write_text(json.dumps(info))
    (root/'meta/episodes.jsonl').write_text(chr(10).join(json.dumps({'episode_index':i,'length':3,'tasks':['test task']}) for i in range(2)))
    (root/'meta/tasks.jsonl').write_text(json.dumps({'task_index':0,'task':'test task'}))
    for e in range(2):
        path = root/info['data_path'].format(episode_chunk=0,episode_index=e)
        path.parent.mkdir(parents=True,exist_ok=True)
        pq.write_table(pa.table({'q': pa.array([[e,1],[e,2],[e,3]],type=pa.list_(pa.float32(),2)),
            'timestamp': pa.array([0.,.1,.2],type=pa.float32()), 'frame_index':[0,1,2],
            'episode_index':[e]*3,'index':list(range(e*3,e*3+3)),'task_index':[0]*3}),path)
        video = root/info['video_path'].format(episode_chunk=0,episode_index=e,video_key='rgb')
        encode_frames(({'index':i,'playback_time':i/10,'image':np.full((8,8,3),e*100+i,dtype=np.uint8)} for i in range(3)),video,fps=10)
    return root


def test_v21_copy_preserves_videos_tasks_boundaries_and_official_modality(tmp_path):
    official()
    source = v21(tmp_path)
    p = {'schema_version':1,'id':'dex','version':'1', 'channels':{'q':{'modality':'joint_space','fields':['recorded_values'],'unit':'unknown', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}}
    output = tmp_path/'v3'
    result = convert_to_lerobot(source, profile=p, output=output)
    assert result['training_frames'] == 6
    assert [x['id'] for x in result['episodes']] == ['v21:0','v21:1']
    assert [x['frames'] for x in result['episodes']] == [3,3]
    from edc.data.conversion.lerobot import sha256
    with open_dataset(output) as ds:
        episodes = list(ds.iter_episodes())
        for e,ep in enumerate(episodes):
            with ep.opened():
                assert ep.channels['rgb'].modality == 'rgb'
                assert ep.channels['rgb'].time_source == 'unknown'
                assert ep.video_evidence('rgb')['inspection_object'] == 'original_encoded_video'
                assert sha256(ep.video_source('rgb')) == sha256(source/f'videos/chunk-000/rgb/episode_{e:06d}.mp4')
                assert len(list(ep.iter_frames('rgb'))) == 3
                assert ep.read_signal('q','recorded_values').dtype == np.float32
    with open_dataset(output, backend='compat') as ds:
        assert len(list(ds.iter_episodes())) == 2
    with pytest.raises(ValueError, match='FPS cannot change'):
        convert_to_lerobot(source, profile=p, output=tmp_path/'badfps', fps=20)
    (output/'videos/rgb/chunk-000/file-000.mp4').unlink()
    with open_dataset(output) as ds:
        with pytest.raises(FileNotFoundError):
            next(ds.iter_episodes()).open()


def test_declared_time_anomaly_preserved_and_detected(tmp_path):
    official()
    source = hdf(tmp_path)
    import h5py
    with h5py.File(source,'a') as f:
        f['raw_time'][:] = [10., 9., np.nan]
    p = profile()
    p['timestamp_unit'] = 's'
    p['lerobot']['original_time'] = {'time_source':'acquisition','clock':'recorder'}
    output=tmp_path/'v3'
    convert_to_lerobot(source,profile=p,output=output,fps=30)
    with open_dataset(output) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            np.testing.assert_equal(ep.read_signal('raw_time','recorded_values'),[10.,9.,np.nan])
            assert ep.channels['angle'].timestamps[:2] == [10.,9.]
            assert np.isnan(ep.channels['angle'].timestamps[2])


def test_v21_declared_dtype_conflict_rejects_precision_change(tmp_path):
    official()
    source=v21(tmp_path)
    path=source/'meta/info.json'
    info=json.loads(path.read_text())
    info['features']['q']['dtype']='float64'
    path.write_text(json.dumps(info))
    with pytest.raises(ValueError,match='dtype.*conflicts'):
        convert_to_lerobot(source,profile={'schema_version':1,'id':'fixture','version':'1'},output=tmp_path/'bad')
    assert json.loads((tmp_path/'bad/acceptance.json').read_text())['fidelity_status']=='failed'


def test_invalid_shape_mapping_is_not_a_free_reshape():
    features={'x':{'dtype':'float64','shape':[2,3]}}
    with pytest.raises(ValueError,match='shape conflicts'):
        bindings({'lerobot':{'feature_mapping':{'a':{'feature':'x','source_sample_shape':[6]}}}},features)


def test_cli_backend_and_convert_entrypoints(tmp_path,capsys):
    from edc.execution.cli import main
    assert main(['inspect',str(tmp_path),'--backend','official'])==2
    with pytest.raises(SystemExit) as exc:
        main(['convert-to-lerobot','--help'])
    assert exc.value.code==0
    assert '--fps' in capsys.readouterr().out
