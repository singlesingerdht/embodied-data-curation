"""Behavioral checks across canonical IR, old adapters and public format libraries."""
import json
import sys
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pytest
import yaml
from framework.ir.dataset import open_dataset
from Quality_Assessment.adapters.cpu import builtins, preflight
from framework.execution import run


def profile(channels, **extra):
    return dict(schema_version=1, id='test_robot', version='1', channels=channels, **extra)


def episode(tmp_path, rows, mapping):
    source = tmp_path / 'episode'
    source.mkdir()
    (source / 'trajectory.json').write_text(json.dumps({'data': rows}))
    return next(open_dataset(source, profile=mapping).iter_episodes())


def plugin(ident):
    return next(p for p in builtins() if p.spec.id == ident)


def test_internal_imports_do_not_mutate_search_path():
    before = list(sys.path)
    import dqs.dataset
    import framework.ir.dataset
    assert dqs.dataset.Episode is framework.ir.dataset.Episode
    from Quality_Assessment.algorithms.Numeric_Check import run_check
    assert before == sys.path


def test_imu_only_numeric_checks_measured_inputs(tmp_path):
    from framework.robots.numeric_review import confirm_numeric_profile
    ep = episode(tmp_path, [{'imu': {'lin_acc': [0., 0., 9.81]}} for _ in range(40)],
        confirm_numeric_profile(profile({'imu': {'modality':'imu', 'fields':['lin_acc'],
            'unit':'m/s^2', 'calibration_status':'confirmed', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}), 'test fixture author'))
    with ep.opened():
        p = plugin('Numeric_Check')
        assert preflight(p, ep)[0] == 'runnable'
        result = p.run(SimpleNamespace(episode=ep), {})
        assert len(result) == 1 and result[0]['input_field'] == 'lin_acc'
        assert result[0]['score'] is not None
        assert result[0]['method_version'] == 'numeric_available_inputs_v4'


def test_smoothness_never_invents_timestamps(tmp_path):
    ep = episode(tmp_path, [{'pose':[i*.01,0,0]} for i in range(40)],
        profile({'pose':{'modality':'pose','fields':['position'],'unit':'m', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}}))
    with ep.opened():
        assert preflight(plugin('Action_Smoothness'), ep)[0] == 'missing_input'


def test_hdf5_pixels_and_value_frontend(tmp_path):
    h5py = pytest.importorskip('h5py')
    pytest.importorskip('av')
    source = tmp_path / 'data.hdf5'
    pixels = np.zeros((4, 16, 16, 3), dtype=np.uint8)
    pixels[:, :, :, 0] = 150
    with h5py.File(source, 'w') as f:
        f['timestamps'] = [1000,1100,1200,1300]
        f['head_rgb'] = pixels
        f['joint_pos'] = np.arange(8).reshape(4,2)
    mapping = profile({'head_rgb':{'modality':'image','time_source':'acquisition'},
        'joint_pos':{'fields':['position'],'modality':'joint_state','unit':'rad', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}},timestamp_unit='ms')
    cfg = tmp_path / 'robot.yaml'; cfg.write_text(yaml.safe_dump(mapping))
    open_value_input = pytest.importorskip('Value_Assessment._shared.value_input', reason='Legacy value bridge stays in research repository').open_value_input
    with open_value_input(str(source), profile_path=str(cfg), backend='framework') as ctx:
        frames = list(ctx.reader.episode.iter_frames('head_rgb'))
        assert frames[-1]['source_time'] == 1.3
        np.testing.assert_array_equal(frames[0]['image'], pixels[0])
        np.testing.assert_array_equal(ctx.reader.read_field(ctx.info,'joint_pos','position'),np.arange(8).reshape(4,2))
        video = ctx.materialize_video('head_rgb', tmp_path / 'cache')
        assert video['frame_count'] == 4 and video['package'] == 'embodied-dqs'
        assert Path(video['source_mapping']).is_file()
        assert video['time_source'] == 'acquisition'


@pytest.mark.parametrize('dof,group_name,signal_name,time_name', [
    (6, 'sessions/take_a', 'measurements/encoders', 'clock/ticks'),
    (7, 'recordings/trial_b', 'sensors/joints', 'timing/sample_time'),
])
def test_hdf5_onboarding_changes_only_profile(tmp_path, dof, group_name, signal_name, time_name):
    """Different robot layouts share the reader and registered CPU adapter."""
    h5py = pytest.importorskip('h5py')
    source = tmp_path / 'another_robot.h5'
    values = np.arange(40 * dof * 2).reshape(40, dof * 2) * .001
    with h5py.File(source, 'w') as handle:
        group = handle.create_group(group_name)
        group[signal_name] = values
        group[time_name] = np.arange(40) * 20
    before = source.read_bytes()
    # Deliberately interleaved joints: do not assume left=first half.
    indices = list(range(1, dof * 2, 2))
    mapping = profile({
        signal_name: {'modality': 'other', 'fields': ['position'],
                      'unit': 'rad', 'source_kind': 'feedback',
                      'time_source': 'acquisition', 'clock': 'controller', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}},
        'selected_arm': {'modality': 'joint_space', 'fields': ['position'],
                         'robot_part': 'arm', 'body_part': 'arm', 'side': 'left',
                         'dimensions': [dof],
                         'select': {'channel': signal_name, 'field': 'position', 'indices': indices}},
    }, episode_group=group_name.rsplit('/', 1)[0], timestamp_column=time_name,
       timestamp_unit='ms', parts={'arm': {'dof': dof}})
    config = tmp_path / 'robot.yaml'
    config.write_text(yaml.safe_dump(mapping))
    from dqs import open_dataset as public_open_dataset
    with public_open_dataset(source, profile=config) as dataset:
        episodes = list(dataset.iter_episodes())
        assert len(episodes) == 1
        with episodes[0].opened() as ep:
            assert ep.selector['group'].strip('/') == group_name
            np.testing.assert_array_equal(ep.read_signal('selected_arm', 'position'), values[:, indices])
            np.testing.assert_allclose(ep.channels['selected_arm'].timestamps, np.arange(40) * .02)
            assert ep.channels['selected_arm'].clock == 'controller'
            assert ep.channels['selected_arm'].field_spec('position')['source_kind'] == 'feedback'
        result = run(dataset, output=tmp_path / 'results', algorithm_ids=['Numeric_Check'])
        assert result['status_counts'] == {'completed': 1}
    assert source.read_bytes() == before


def write_ros(path, backend):
    pytest.importorskip('rosbags')
    from rosbags.typesys import get_typestore, Stores
    store = get_typestore(Stores.ROS1_NOETIC if backend == 'ros1' else Stores.ROS2_HUMBLE)
    if backend == 'ros1':
        from rosbags.rosbag1 import Writer
        writer = Writer(path)
    else:
        from rosbags.rosbag2 import Writer, StoragePlugin
        writer = Writer(path, version=9, storage_plugin=StoragePlugin.MCAP if backend == 'mcap' else StoragePlugin.SQLITE3)
    with writer:
        connections = {n:writer.add_connection(n,t,typestore=store) for n,t in
            [('/joint_states','sensor_msgs/msg/JointState'),('/head_rgb','sensor_msgs/msg/Image')]}
        for i in range(40):
            stamp = store.types['builtin_interfaces/msg/Time'](10+i//10,(i%10)*100000000)
            header = store.types['std_msgs/msg/Header'](i,stamp,'base') if backend == 'ros1' else store.types['std_msgs/msg/Header'](stamp,'base')
            joint = store.types['sensor_msgs/msg/JointState'](header,['a','b'],np.array([i*.01,i*.02]),np.zeros(2),np.zeros(2))
            img = store.types['sensor_msgs/msg/Image'](header,16,16,'rgb8',0,48,np.full(768,i,dtype=np.uint8))
            for name,msg in [('/joint_states',joint),('/head_rgb',img)]:
                conn=connections[name]
                raw = store.serialize_ros1(msg,conn.msgtype) if backend=='ros1' else store.serialize_cdr(msg,conn.msgtype)
                writer.write(conn,int((20+i*.1+(0.005 if name=='/head_rgb' else 0))*1e9),raw)


@pytest.mark.parametrize('backend',['ros1','db3','mcap'])
def test_ros_clocks_values_and_images(tmp_path,backend):
    pytest.importorskip('av')
    source = tmp_path / ('test.bag' if backend=='ros1' else 'bag')
    write_ros(source,backend)
    ep=next(open_dataset(source).iter_episodes())
    with ep.opened():
        assert ep.channels['/joint_states'].time_source=='container'
        np.testing.assert_allclose(ep.read_signal('/joint_states','position')[1],[.01,.02])
        assert len(list(ep.iter_frames('/head_rgb')))==40
        if backend != 'ros1':
            assert ep.describe_legacy().topics['/joint_states'].seqs is None
    mapping=profile({'*':{'time_source':'acquisition','clock':'robot'}})
    ep=next(open_dataset(source,profile=mapping).iter_episodes())
    with ep.opened():
        assert ep.channels['/joint_states'].timestamps[0]==10
        assert ep.channels['/joint_states'].time_source=='acquisition'
        assert preflight(plugin('Sync_Check'),ep)[0]=='missing_input'  # same header clock array
    # Run the actual temporal algorithms on independently recorded container axes.
    if backend=='ros1':
        mapping=profile({'*':{'time_source':'container','clock':'recorder'}})
        with open_dataset(source,profile=mapping) as dataset:
            summary=run(dataset,output=tmp_path/'out',algorithm_ids=['Check_Timestamp','Root_Cause','Dedup_Check'])
        assert summary['status_counts']=={'completed':3}


def test_all_cpu_adapters_execute_without_gpu(tmp_path):
    pytest.importorskip('av');pytest.importorskip('scipy');pytest.importorskip('cv2');pytest.importorskip('xxhash')
    from framework.readers.video import encode_frames
    rows=[{'timestamp':i*.05,'joint_pos':[.01*np.sin(i/5),.02*np.sin(i/5)],'pose':[i*.001,0,.2]} for i in range(60)]
    from framework.robots.numeric_review import confirm_numeric_profile
    mapping=profile({'joint_pos':{'modality':'joint_state','fields':['position'],'unit':'rad','time_source':'acquisition','clock':'robot',
                                'calibration_status':'confirmed','source_kind':'feedback', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}},
        'pose':{'modality':'pose','fields':['position'],'unit':'m','side':'right','time_source':'acquisition','clock':'robot',
                'calibration_status':'confirmed', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}},
        timestamp_unit='s', calibration={'Reset_Detection':{'reference':{'joint_pos':[0,0]},'unit':'rad','tolerance':.1}})
    mapping=confirm_numeric_profile(mapping, 'synthetic fixture author')
    ep=episode(tmp_path,rows,mapping)
    encode_frames(({'index':i,'playback_time':i*.05,'source_time':i*.05,'image':np.full((32,32,3),i,dtype=np.uint8)} for i in range(60)),ep.path/'head_rgb.mp4',fps=20)
    with ep.opened():
        # Explicit independent channel acquisition times, as supplied by a reader plugin.
        ep.channels['pose'].timestamps=[i*.05+.001*np.sin(i) for i in range(60)]
        for name in ('pose', 'joint_pos'):
            ep.channels[name].time_evidence = {'timeline_scope': 'independent_channel',
                                              'independent_acquisition_timeline': True}
        ctx=SimpleNamespace(episode=ep)
        for p in builtins():
            if p.spec.id == 'Check_Timestamp':
                source = tmp_path / 'reference.bag'
                write_ros(source, 'ros1')
                with next(open_dataset(source,profile=profile({'*':{'clock':'recorder'}})).iter_episodes()).opened() as ros_ep:
                    assert preflight(p,ros_ep)[0] == 'runnable'
                    assert p.run(SimpleNamespace(episode=ros_ep),{})['verdict'] != 'NOT_APPLICABLE'
                continue
            assert preflight(p,ep)[0]=='runnable',p.spec.id
            result=p.run(ctx,{})
            assert result,p.spec.id
            reports=result if isinstance(result,list) else [result]
            assert all(r.get('verdict')!='NOT_APPLICABLE' for r in reports),p.spec.id
    assert 'torch' not in sys.modules

@pytest.mark.parametrize('version',['v2.1','v3.0'])
def test_lerobot_video_episode_boundaries(tmp_path,version):
    pa=pytest.importorskip('pyarrow');pq=pytest.importorskip('pyarrow.parquet');pytest.importorskip('av')
    from framework.readers.video import encode_frames
    root=tmp_path/'lerobot';(root/'meta').mkdir(parents=True);(root/'data').mkdir()
    info={'codebase_version':version,'fps':10,'features':{'head_rgb':{'dtype':'video'}},'chunks_size':1000}
    rows=[{'episode_index':i,'length':3} for i in range(2)]
    (root/'videos').mkdir()
    if version=='v3.0':
        info['video_path']='videos/{video_key}_{chunk_index}_{file_index}.mp4'
        for i,row in enumerate(rows):
            row.update({'videos/head_rgb/chunk_index':0,'videos/head_rgb/file_index':0,
                        'videos/head_rgb/from_timestamp':i*.3,'videos/head_rgb/to_timestamp':(i+1)*.3})
        (root/'meta/episodes').mkdir()
        pq.write_table(pa.Table.from_pylist(rows),root/'meta/episodes/index.parquet')
        encode_frames(({'index':i,'playback_time':i*.1,'source_time':i*.1,'image':np.full((16,16,3),30 if i<3 else 200,dtype=np.uint8)} for i in range(6)),root/'videos/head_rgb_0_0.mp4',fps=10)
    else:
        info['video_path']='videos/{video_key}_{episode_chunk}_{episode_index}.mp4'
        (root/'meta/episodes.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
        for e in range(2):
            encode_frames(({'index':i,'playback_time':i*.1,'source_time':i*.1,'image':np.full((16,16,3),30 if e==0 else 200,dtype=np.uint8)} for i in range(3)),root/f'videos/head_rgb_0_{e}.mp4',fps=10)
    (root/'meta/info.json').write_text(json.dumps(info))
    records=[{'episode_index':e,'frame_index':i,'timestamp':i*.1,'joint_pos':[e,i]} for e in range(2) for i in reversed(range(3))]
    pq.write_table(pa.Table.from_pylist(records),root/'data/shared.parquet')
    with open_dataset(root, backend='compat') as ds:
        episodes=list(ds.iter_episodes());assert len(episodes)==2
        for e,ep in enumerate(episodes):
            with ep.opened():
                frames=list(ep.iter_frames('head_rgb'));assert len(frames)==3
                assert abs(frames[0]['image'].mean()-(30 if e==0 else 200))<5
                assert ep.channels['head_rgb'].time_source=='synthetic'
                assert ep.channels['joint_pos'].time_source=='synthetic'
                assert ep.channels['joint_pos'].time_evidence['time_generated']
                assert ep.channels['joint_pos'].time_evidence['timestamp_unit_source']=='format_standard'
                np.testing.assert_array_equal(ep.read_signal('joint_pos','position')[:,1],[0,1,2])
                output=ep.materialize_video('head_rgb',tmp_path/f'episode_{e}.mp4')
                assert Path(output).is_file()
                if version=='v3.0':
                    mapping=json.loads(Path(output+'.frames.json').read_text())
                    assert mapping[0]['source_time']==pytest.approx(e*.3)
    with open_dataset(root, backend='compat', profile=profile({}, timestamp_unit='ms')) as ds:
        with pytest.raises(ValueError, match='standard timestamp is in seconds'):
            next(ds.iter_episodes()).open()


def test_missing_field_fails_without_dropping_row(tmp_path):
    ep=episode(tmp_path,[{'imu':{'lin_acc':[0,0,1]}},{'imu':{'ang_vel':[0,0,1]}}],
        profile({'imu':{'modality':'imu','fields':['lin_acc']}}))
    with ep.opened(), pytest.raises(ValueError,match='missing declared fields'):
        ep.read_signal('imu','lin_acc')


def test_value_multi_episode_requires_selector(tmp_path):
    from framework.examples import generate
    open_value_input = pytest.importorskip('Value_Assessment._shared.value_input', reason='Legacy value bridge stays in research repository').open_value_input
    root=generate(tmp_path/'dataset')
    with pytest.raises(ValueError,match='exactly one episode'):
        open_value_input(str(root))
    with open_value_input(str(root),episode_id='episode_001') as context:
        assert context.reader.episode.id=='episode_001'


def test_parquet_embedded_images_and_reference_escape(tmp_path):
    pa=pytest.importorskip('pyarrow');pq=pytest.importorskip('pyarrow.parquet');pytest.importorskip('av')
    import cv2
    ok,data=cv2.imencode('.png',np.full((16,16,3),90,dtype=np.uint8));assert ok
    source=tmp_path/'images.parquet'
    pq.write_table(pa.Table.from_pylist([{'image':{'bytes':data.tobytes(),'path':None},'timestamp':.1}]),source)
    with next(open_dataset(source).iter_episodes()).opened() as ep:
        assert next(ep.iter_frames('image'))['image'].mean()==90
    pq.write_table(pa.Table.from_pylist([{'image':{'bytes':None,'path':'../outside.png'},'timestamp':.1}]),source)
    with next(open_dataset(source).iter_episodes()).opened() as ep, pytest.raises(ValueError,match='outside'):
        next(ep.iter_frames('image'))


def test_hdf5_auxiliary_metadata_is_not_forced_onto_frame_clock(tmp_path):
    h5py=pytest.importorskip('h5py')
    source=tmp_path/'meta.hdf5'
    with h5py.File(source,'w') as f:
        f['timestamp']=[0,.1,.2]
        f['joint_pos']=[[0],[1],[2]]
        f['subtask_transitions']=[2]
    with next(open_dataset(source).iter_episodes()).opened() as ep:
        assert ep.channels['subtask_transitions'].timestamps is None
        assert ep.read_signal('joint_pos','position').shape==(3,1)


def test_standalone_mcap_numeric_and_image(tmp_path):
    pytest.importorskip('mcap');pytest.importorskip('av')
    root=tmp_path/'bag';write_ros(root,'mcap')
    source=next(root.glob('*.mcap'))
    with next(open_dataset(source).iter_episodes()).opened() as ep:
        assert ep.channels['/joint_states'].time_source=='container'
        np.testing.assert_allclose(ep.read_signal('/joint_states','position')[1],[.01,.02])
        assert ep.read_values('/joint_states',[])==[]
        assert len(list(ep.iter_frames('/head_rgb')))==40


def test_old_robot_argument_keeps_legacy_boundary(monkeypatch):
    value_input = pytest.importorskip('Value_Assessment._shared.value_input', reason='Legacy value bridge stays in research repository')
    expected = SimpleNamespace(warnings=[])
    monkeypatch.setattr(value_input, "_open_legacy_value_input", lambda *a, **kw: expected)
    result = value_input.open_value_input("/old/data", robot="unregistered_old_robot")
    assert result is expected and "Legacy" in result.warnings[0]


def test_parquet_binary_rgb_and_uninterpreted_pose(tmp_path):
    pa = pytest.importorskip('pyarrow')
    pq = pytest.importorskip('pyarrow.parquet')
    cv2 = pytest.importorskip('cv2')
    pytest.importorskip('av')
    ok, encoded = cv2.imencode('.png', np.full((12, 16, 3), 73, dtype=np.uint8))
    assert ok
    source = tmp_path / 'binary.parquet'
    pq.write_table(pa.Table.from_pylist([
        {'camera': encoded.tobytes(), 'pose': list(range(9))},
        {'camera': encoded.tobytes(), 'pose': list(range(9, 18))},
    ]), source)
    mapping = profile({'camera': {'modality': 'rgb'},
                       'pose': {'modality': 'pose', 'fields': ['recorded_values'], 'unit': 'unknown', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}})
    with next(open_dataset(source, profile=mapping).iter_episodes()).opened() as ep:
        frames = list(ep.iter_frames('camera'))
        assert len(frames) == 2 and frames[0]['image'].shape == (12, 16, 3)
        assert np.all(frames[0]['image'] == 73)
        assert frames[0]['source_time'] is None
        np.testing.assert_array_equal(ep.read_signal('pose', 'recorded_values'), np.arange(18).reshape(2, 9))
        assert ep.image_storage('camera') == 'frames'
        assert preflight(plugin('Video_Integrity'), ep)[0] == 'runnable'
        assert preflight(plugin('Video_Integrity'), ep, {'input_mode': 'video'})[0] == 'missing_input'


@pytest.mark.parametrize('with_summary', [True, False])
def test_mcap_unsupported_encoding_exposes_only_container_time(tmp_path, with_summary):
    pytest.importorskip('mcap')
    from mcap.writer import Writer, IndexType
    source = tmp_path / 'protobuf.mcap'
    with source.open('wb') as stream:
        writer = Writer(stream, index_types=IndexType.ALL if with_summary else IndexType.NONE,
                        repeat_channels=with_summary, repeat_schemas=with_summary,
                        use_statistics=with_summary, use_summary_offsets=with_summary)
        writer.start()
        schema = writer.register_schema(name='foxglove.RawImage', encoding='protobuf', data=b'')
        channel = writer.register_channel(topic='/camera/image', message_encoding='protobuf', schema_id=schema)
        writer.add_message(channel_id=channel, log_time=1_000_000_000, publish_time=900_000_000, data=b'opaque')
        writer.finish()
    with next(open_dataset(source).iter_episodes()).opened() as ep:
        channel = ep.channels['/camera/image']
        assert channel.timestamps == [1.0] and channel.time_source == 'container'
        assert not channel.image_available and not channel.value_available
        assert preflight(plugin('Video_Integrity'), ep)[0] == 'missing_input'
        assert preflight(plugin('Dedup_Check'), ep)[0] == 'runnable'
