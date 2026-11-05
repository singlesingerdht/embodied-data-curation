"""Synthetic boundary fixtures for config checks and native clock diagnostics."""
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import importlib.util
import numpy as np
import pytest
from edc.data.ir.metadata import TopicInfo
from edc.data.ir.dataset import open_dataset
from edc.robots.profiles import load_profile
from edc.algorithms.quality.adapters.cpu import builtins, preflight
from edc.algorithms.quality.algorithms.Structure_Check.structure_check import check_camera_configuration
from edc.algorithms.quality.algorithms.Structure_Check.run_check import assess as structure_assess
from edc.algorithms.quality.algorithms.Numeric_Check.numeric_check import check_joint_anomaly


def plugin(name):
    return next(p for p in builtins() if p.spec.id == name)


def profile():
    return {'schema_version': 1, 'id': 'test_config', 'version': '1', 'channels': {
        '/wrist/image_compressed': {'sensor_id': 'left_wrist', 'modality': 'rgb'},
        '/head/compressed': {'sensor_id': 'head', 'modality': 'rgb'},
        '/head/depth': {'sensor_id': 'head', 'modality': 'depth'}},
        'camera_requirements': {'left_wrist': ['rgb'], 'head': ['rgb', 'depth']}}


def topics():
    return {n: TopicInfo(n, count=3, modality=c['modality']) for n,c in profile()['channels'].items()}


def test_heterogeneous_cameras_pass_and_do_not_need_matching_leaf_names(tmp_path):
    source = tmp_path / 'sample.mcap'; source.touch()
    p = profile(); result = structure_assess(str(source), topics(), camera_requirements=p['camera_requirements'], channel_config=p['channels'])
    assert result['structure']['camera_configuration']['severity'] == 'pass'
    assert result['structure']['camera_symmetry']['evaluated'] is False
    assert result['assessment_complete']
    assert next(s for s in result['sub_indicators'] if s['name']=='相机通道配置一致性')['score'] == 100


@pytest.mark.parametrize('change,expected', [('missing','missing'),('empty','empty'),('mismatch','modality_mismatch'),('unmapped','unmapped')])
def test_explicit_camera_failures(change, expected):
    p=profile(); t=topics()
    if change=='missing': del t['/head/depth']
    if change=='empty': t['/head/depth'].count=0
    if change=='mismatch': t['/head/depth'].modality='rgb'
    if change=='unmapped': del p['channels']['/head/depth']
    result=check_camera_configuration(t,p['camera_requirements'],p['channels'])
    assert result['severity']==('not_evaluated' if change=='unmapped' else 'error')
    assert result['checks'][-1]['status']==expected


def test_unconfigured_cameras_never_claim_configuration_pass(tmp_path):
    p=tmp_path/'sample.mcap';p.touch()
    r=structure_assess(str(p),topics())
    assert not r['assessment_complete']
    assert r['verdict']=='OBSERVATION'
    assert next(s for s in r['sub_indicators'] if s['name']=='相机通道配置一致性')['score'] is None


def test_no_cameras_in_data_or_yaml_is_nothing_to_check():
    t={'/joint_states':TopicInfo(name='/joint_states',modality='joint_state',count=10)}
    result=check_camera_configuration(t,{},{})
    assert result['severity']=='pass' and '没有相机' in result['reason']


@pytest.mark.parametrize('requirements',[[],{'head':[]},{'head':['rgb','rgb']},{'head':['foo']}])
def test_invalid_camera_requirement_rejected(requirements):
    p=profile();p['camera_requirements']=requirements
    with pytest.raises(ValueError, match='camera_requirements'): load_profile(p)


def test_joint_step_evidence_preserves_original_numerical_result():
    values=np.array([[0.,0.],[.2,0.],[1.5,2.],[1.6,2.]])
    r=check_joint_anomaly(values)
    assert r['severity']=='warning'
    e=r['events'][0]
    assert e['type']=='joint_jump' and e['count']==1 and e['threshold']==1.
    assert e['component_exceedance_count']==2
    assert e['examples'][0]=={'previous_row':1,'current_row':2,'joint_index':0,'previous_value':.2,'current_value':1.5,'absolute_difference':1.3}
    assert e['threshold_unit']=='rad per adjacent sample'


def write_mcap(path):
    pytest.importorskip('rosbags');pytest.importorskip('mcap')
    from rosbags.typesys import get_typestore, Stores
    from mcap.writer import Writer
    store=get_typestore(Stores.ROS2_HUMBLE)
    joint_type='sensor_msgs/msg/JointState';image_type='sensor_msgs/msg/Image'
    with path.open('wb') as stream:
        writer=Writer(stream);writer.start(profile='ros2')
        channels={}
        for typ in (joint_type,image_type):
            msgdef,_=store.generate_msgdef(typ,ros_version=2)
            schema=writer.register_schema(typ,'ros2msg',msgdef.encode())
            names=['/fast','/zero','/backward','/duplicate'] if typ==joint_type else ['/head']
            for n in names: channels[n]=writer.register_channel(n,'cdr',schema)
        axes={'/fast':[10_000_000_000+i*4_000_000 for i in range(12)],
              '/zero':[0]*12,'/backward':[10_000_000_000,10_004_000_000,10_003_000_000],
              '/duplicate':[10_000_000_000,10_004_000_000,10_004_000_000],
              '/head':[10_000_000_000+i*32_000_000 for i in range(3)]}
        for n,axis in axes.items():
            for i,ns in enumerate(axis):
                stamp=store.types['builtin_interfaces/msg/Time'](ns//10**9,ns%10**9)
                header=store.types['std_msgs/msg/Header'](stamp,'local')
                if n=='/head':
                    msg=store.types[image_type](header,2,2,'rgb8',0,6,np.full(12,i,dtype=np.uint8));typ=image_type
                else:
                    msg=store.types[joint_type](header,['q'],np.array([i*.01]),np.zeros(1),np.zeros(1));typ=joint_type
                log=20_000_000_000+i*(32_000_000 if n=='/head' else 4_000_000)
                writer.add_message(channels[n],log,bytes(store.serialize_cdr(msg,typ)),log)
        writer.finish()
    return axes


def native_profile():
    return {'schema_version':1,'id':'native','version':'1','channels':{'*':{'time_source':'acquisition','clock':None}}}


def test_mcap_retains_both_axes_and_zero_clock_does_not_block_other_channels(tmp_path):
    source=tmp_path/'native.mcap';axes=write_mcap(source);before=hashlib.sha256(source.read_bytes()).hexdigest()
    with next(open_dataset(source,profile=native_profile()).iter_episodes()).opened() as ep:
        assert ep.channels['/fast'].timestamps==[v*1e-9 for v in axes['/fast']]
        assert ep.channels['/head'].count==3 and ep.channels['/fast'].count==12
        zero=ep.channels['/zero'];assert zero.timestamps is None and zero.time_source=='unknown'
        assert zero.time_evidence['zero_header_count']==12
        info=ep.describe_legacy()
        assert info.topics['/zero'].acquisition_timestamps_ns==[0]*12
        assert info.topics['/fast'].container_timestamps_ns[0]==20_000_000_000
        assert info.topics['/fast'].acquisition_timestamps_ns==axes['/fast']
        assert info.topics['/backward'].acquisition_timestamps_ns==axes['/backward']
        assert preflight(plugin('Check_Timestamp'),ep)[0]=='runnable'
        assert preflight(plugin('Sync_Check'),ep)[0]=='missing_input'
        assert preflight(plugin('Root_Cause'),ep)[0]=='missing_input'
        result=plugin('Check_Timestamp').run(SimpleNamespace(episode=ep),{})
        assert result['score'] is None and not result['assessment_complete']
        rows={r['channel']:r for r in result['cadence']['channels']}
        assert rows['/fast']['median_positive_interval_s']==pytest.approx(.004)
        assert rows['/head']['median_positive_interval_s']==pytest.approx(.032)
        assert rows['/zero']['status']=='missing_input'
        assert rows['/backward']['anomaly_examples'][0]['current_row']==2
        assert rows['/duplicate']['zero_interval_count']==1
        assert len(list(ep.iter_frames('/head')))==3
        frame=next(ep.iter_frames('/head'))
        assert frame['source_time']==10. and frame['container_time']==20.
        assert frame['time_source']=='acquisition' and frame['clock'] is None
        reader_frame=next(ep._reader.iter_frames('/head'))
        assert reader_frame['source_time']==10. and reader_frame['container_time']==20.
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def test_missing_headers_never_fallback_or_drop_rows():
    from edc.data.readers.timeline import set_recorded_timelines
    t=TopicInfo('/partial',count=3)
    set_recorded_timelines(t,[20,21,22],[10,None,12])
    assert t.acquisition_timestamps is None
    assert t.acquisition_timestamps_ns==[10,None,12]
    assert t.time_evidence['missing_header_rows']==[1]


def test_dedup_observes_short_runs_and_preserves_legacy_scoring_boundary():
    from edc.algorithms.quality.algorithms.Dedup_Check.dedup_check import check_timestamp_dedup
    short=check_timestamp_dedup([0,.1,.1,.2])
    assert short['duplicate_count']==0 and short['extra_duplicate_count']==1
    r=check_timestamp_dedup([0,.1,.1,.1,.1,.1,.2])
    assert r['duplicate_count']==5 and r['segments']==[{'start':1,'end':5,'count':5}]

@pytest.mark.parametrize('backend',['ros1','db3'])
def test_ros_reader_zero_header_is_unavailable_with_exact_raw_evidence(tmp_path,backend):
    pytest.importorskip('rosbags')
    from rosbags.typesys import get_typestore,Stores
    store=get_typestore(Stores.ROS1_NOETIC if backend=='ros1' else Stores.ROS2_HUMBLE)
    source=tmp_path/('zero.bag' if backend=='ros1' else 'zero')
    if backend=='ros1':
        from rosbags.rosbag1 import Writer
        writer=Writer(source)
    else:
        from rosbags.rosbag2 import Writer
        writer=Writer(source,version=9)
    typ='sensor_msgs/msg/JointState'
    with writer:
        c=writer.add_connection('/joint_states',typ,typestore=store)
        for i,ns in enumerate([0,10_000_000_001,10_000_000_002]):
            stamp=store.types['builtin_interfaces/msg/Time'](ns//10**9,ns%10**9)
            header=store.types['std_msgs/msg/Header'](i,stamp,'base') if backend=='ros1' else store.types['std_msgs/msg/Header'](stamp,'base')
            msg=store.types[typ](header,['q'],np.array([i*.1]),np.zeros(1),np.zeros(1))
            raw=store.serialize_ros1(msg,typ) if backend=='ros1' else store.serialize_cdr(msg,typ)
            writer.write(c,20_000_000_000+i*100_000_000,raw)
    with next(open_dataset(source,profile=native_profile()).iter_episodes()).opened() as ep:
        c=ep.channels['/joint_states'];t=ep.describe_legacy().topics[c.name]
        assert c.timestamps is None and c.time_source=='unknown'
        assert t.acquisition_timestamps_ns==[0,10_000_000_001,10_000_000_002]
        assert t.container_timestamps_ns==[20_000_000_000,20_100_000_000,20_200_000_000]
        assert ep.read_signal(c.name,'position').shape==(3,1)


def test_static_text_excluded_from_native_cadence():
    from edc.algorithms.quality.algorithms.Check_Timestamp.cadence import cadence_report
    from edc.data.ir.dataset import Channel
    t=TopicInfo('/instruction_data',msg_type='std_msgs/msg/String',count=1)
    c=Channel(t.name,'other',[],1,'')
    ep=SimpleNamespace(channels={t.name:c},describe_legacy=lambda:SimpleNamespace(topics={t.name:t}))
    r=cadence_report(ep)
    assert not r['channels'] and r['excluded']==[{'channel':t.name,'reason':'non_sampled_text'}]


def test_mcap_record_order_is_not_silently_sorted_by_log_time(tmp_path):
    from rosbags.typesys import get_typestore, Stores
    from mcap.writer import Writer
    store=get_typestore(Stores.ROS2_HUMBLE);typ='sensor_msgs/msg/JointState'
    source=tmp_path/'backwards-log.mcap'
    with source.open('wb') as stream:
        writer=Writer(stream);writer.start(profile='ros2')
        msgdef,_=store.generate_msgdef(typ,ros_version=2)
        schema=writer.register_schema(typ,'ros2msg',msgdef.encode())
        channel=writer.register_channel('/joint_states','cdr',schema)
        for i,log in enumerate([20_000_000_000,22_000_000_000,21_000_000_000]):
            stamp=store.types['builtin_interfaces/msg/Time'](10,i*4_000_000)
            header=store.types['std_msgs/msg/Header'](stamp,'robot')
            message=store.types[typ](header,['q'],np.array([float(i)]),np.zeros(1),np.zeros(1))
            writer.add_message(channel,log,bytes(store.serialize_cdr(message,typ)),log)
        writer.finish()
    for config in [None,native_profile()]:
        with next(open_dataset(source,profile=config).iter_episodes()).opened() as ep:
            t=ep.describe_legacy().topics['/joint_states']
            assert t.container_timestamps_ns==[20_000_000_000,22_000_000_000,21_000_000_000]
            assert t.acquisition_timestamps_ns==[10_000_000_000,10_004_000_000,10_008_000_000]
            np.testing.assert_array_equal(ep.read_signal('/joint_states','position')[:,0],[0,1,2])
            np.testing.assert_allclose(ep.channels['/joint_states'].timestamps,
                                       [10.,10.004,10.008] if config else [20.,22.,21.], rtol=0, atol=2e-15)
