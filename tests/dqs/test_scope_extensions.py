import copy
import json
from pathlib import Path
import numpy as np
import pytest
import yaml
from framework.ir.dataset import open_dataset
from framework.robots.profiles import load_profile
from framework.transforms import RobotTransforms, import_urdf_chain, resample

ROOT = Path(__file__).resolve().parents[2]


def urdf(tmp_path, extra=''):
    p = tmp_path / 'robot.urdf'
    p.write_text('''<robot name="synthetic">
    <link name="base"/><link name="arm"/><link name="tool"/>
    <joint name="turn" type="continuous"><parent link="base"/><child link="arm"/>
    <axis xyz="0 0 1"/>''' + extra + '''</joint>
    <joint name="tip" type="fixed"><parent link="arm"/><child link="tool"/>
    <origin xyz="1 0 0" rpy="0 0 0"/></joint></robot>''')
    return p


def test_urdf_endpoint_and_relative_yaml_snapshot(tmp_path):
    path = urdf(tmp_path)
    profile = {'schema_version': 1, 'id': 'test', 'version': '1', 'transforms': {'chains': {
        'arm': {'urdf': path.name, 'base_frame': 'base', 'tip_frame': 'tool'}}}}
    config = tmp_path/'robot.yaml';config.write_text(yaml.safe_dump(profile))
    loaded = load_profile(config)
    chain = loaded['transforms']['chains']['arm']
    assert len(chain['urdf_source']['sha256']) == 64
    path.unlink()  # Loading freezes validated geometry for the run snapshot.
    output = RobotTransforms(loaded).fk('arm', {'turn': [0, np.pi/2]}).output
    np.testing.assert_allclose(output[:, :3, 3], [[1, 0, 0], [0, 1, 0]], atol=1e-12)


def test_urdf_origin_rpy_and_prismatic_limits(tmp_path):
    p = urdf(tmp_path)
    p.write_text(p.read_text().replace('type="continuous"', 'type="prismatic"').replace(
        '<axis xyz="0 0 1"/>', '<origin rpy="0 0 1.5707963267948966"/><axis xyz="2 0 0"/><limit lower="0" upper="1"/>'))
    chain = import_urdf_chain(p, 'base', 'tool')
    t = RobotTransforms({'transforms': {'chains': {'arm': chain}}})
    np.testing.assert_allclose(t.fk('arm', {'turn': .5}).output[:3, 3], [0, 1.5, 0], atol=1e-12)
    with pytest.raises(ValueError, match='limits'):t.fk('arm', {'turn': 2})


@pytest.mark.parametrize('change', ['mimic', 'loop', 'missing', 'type', 'limits', 'dtd'])
def test_urdf_rejects_unsupported_or_invalid(tmp_path, change):
    p = urdf(tmp_path, '<mimic joint="driver"/>' if change == 'mimic' else '')
    text = p.read_text()
    if change == 'loop':text = text.replace('</robot>', '<joint name="loop" type="fixed"><parent link="tool"/><child link="base"/></joint></robot>')
    if change == 'missing':text = text.replace('child link="tool"', 'child link="absent"')
    if change == 'type':text = text.replace('type="continuous"', 'type="floating"')
    if change == 'limits':text = text.replace('type="continuous"', 'type="revolute"')
    if change == 'dtd':text = '<!DOCTYPE robot []>' + text
    p.write_text(text)
    with pytest.raises(ValueError):import_urdf_chain(p, 'base', 'tool')


def test_resampling_weights_gaps_and_source_immutability():
    data = np.array([[0., 10], [2, 20], [4, 30]])
    before = data.copy()
    out = resample(data, [0, 1, 2], [.5, 1.5], method='linear', max_gap=1)
    np.testing.assert_allclose(out.output, [[1, 15], [3, 25]])
    assert out.source_mapping['rows']['right_weights'] == [.5, .5]
    np.testing.assert_array_equal(resample(data, [0, 1, 2], [.5, 1.5], method='nearest').output, data[:2])
    np.testing.assert_array_equal(data, before)
    with pytest.raises(ValueError, match='max_gap'):resample(data, [0, 1, 2], [.5], method='linear', max_gap=.1)
    with pytest.raises(ValueError, match='extrapolation'):resample(data, [0, 1, 2], [-.1], method='nearest')
    with pytest.raises(ValueError):resample(data, [0, 0, 2], [.5], method='linear')
    assert resample([[4]], [0], [0], method='linear').output.tolist() == [[4]]


def mapped_episode(tmp_path):
    root = tmp_path/'data';root.mkdir()
    (root/'trajectory.json').write_text(json.dumps({'data': [
        {'timestamp': t, 'state': list(range(14))} for t in [0, 1, 2]]}))
    profile = load_profile(ROOT/'examples/onboarding/joint_split.yaml')
    return root, profile


def test_split_noncontiguous_indices_and_legacy_algorithms(tmp_path):
    from framework.execution import run
    root, p = mapped_episode(tmp_path)
    p['channels']['left_arm_position']['select']['indices'] = [12, 10, 8, 6, 4, 2, 0]
    with open_dataset(root, profile=p) as ds:
        with next(ds.iter_episodes()).opened() as ep:
            np.testing.assert_array_equal(ep.read_signal('left_arm_position', 'position')[0], [12, 10, 8, 6, 4, 2, 0])
            np.testing.assert_array_equal(ep.read_values('state')[0]['position'], list(range(14)))
            info, access = ep.algorithm_view()
            assert access.read_field(info, 'left_arm_position', 'position').shape == (3, 7)
            result = ep.transform_signal('left_arm_position', 'position', target_unit='deg')
            assert result.source_mapping['selection']['indices'] == [12, 10, 8, 6, 4, 2, 0]
        assert run(ds, output=tmp_path/'results', algorithm_ids=['Numeric_Check'])['status_counts'] == {'completed': 1}


def test_dof_mismatch_and_out_of_bounds_fail(tmp_path):
    root, p = mapped_episode(tmp_path)
    p['channels']['state'].update(modality='joint_space', robot_part='left_arm')
    with next(open_dataset(root, profile=p).iter_episodes()).opened() as ep:
        with pytest.raises(ValueError, match='DOF mismatch'):ep.read_signal('state', 'position')
    p['channels']['state']['modality'] = 'other'
    p['channels']['left_arm_position']['select']['indices'][-1] = 99
    with next(open_dataset(root, profile=p).iter_episodes()).opened() as ep:
        with pytest.raises(ValueError, match='outside'):ep.read_signal('left_arm_position', 'position')


def test_split_rejects_ambiguous_config(tmp_path):
    _, original = mapped_episode(tmp_path)
    for indices in ([0]*7, [True, 1, 2, 3, 4, 5, 6], [0, 1], []):
        p = copy.deepcopy(original)
        p['channels']['left_arm_position']['select']['indices'] = indices
        with pytest.raises(ValueError):load_profile(p)


def test_alignment_requires_clock_and_records_synthetic_origin(tmp_path):
    root, p = mapped_episode(tmp_path)
    with next(open_dataset(root, profile=p).iter_episodes()).opened() as ep:
        with pytest.raises(ValueError, match='clocks'):ep.align_signal('left_arm_position', 'position', 'state', method='linear')
        for c in ep.channels.values():c.clock = 'demo';c.time_source = 'synthetic'
        ep.channels['state'].timestamps = [.5, 1.5];ep.channels['state'].count = 2
        out = ep.align_signal('left_arm_position', 'position', 'state', method='linear')
        assert out.params['source_time_source'] == 'synthetic'
        assert out.params['proves_acquisition_sync'] is False
        assert out.source_mapping['rows']['right_weights'] == [.5, .5]
        assert ep.read_signal('left_arm_position', 'position').shape == (3, 7)


def test_explicit_clock_mapping_for_alignment(tmp_path):
    root, p = mapped_episode(tmp_path)
    with next(open_dataset(root, profile=p).iter_episodes()).opened() as ep:
        ep.channels['left_arm_position'].clock='a';ep.channels['state'].clock='b'
        ep.channels['state'].timestamps=[10.5, 11.5];ep.channels['state'].count=2
        with pytest.raises(ValueError, match='mapping'):ep.align_signal('left_arm_position','position','state',method='nearest')
        ep.profile['transforms']={'clocks':{'a_to_b':{'source':'a','target':'b','offset_seconds':10}}}
        out=ep.align_signal('left_arm_position','position','state',method='linear')
        assert out.source_mapping['original_source_timestamps']==[0,1,2]
        assert out.source_mapping['rows']['left_indices']==[0,1]


def test_selection_cannot_relabel_known_unit(tmp_path):
    root,p=mapped_episode(tmp_path)
    p['channels']['state']['unit']='deg'
    with pytest.raises(ValueError,match='relabel unit'):next(open_dataset(root,profile=p).iter_episodes()).open()


def test_unknown_timestamp_unit_has_no_seconds_axis(tmp_path):
    root,p=mapped_episode(tmp_path);p['timestamp_unit']='unknown'
    with next(open_dataset(root,profile=p).iter_episodes()).opened() as ep:
        assert ep.channels['left_arm_position'].timestamps is None


def test_batch_uses_registered_plugin_and_preserves_old_results(tmp_path, monkeypatch):
    from framework.registry import Registry, AlgorithmSpec
    from types import SimpleNamespace
    from Quality_Assessment.batch_check import main
    root,_=mapped_episode(tmp_path)
    registry=Registry('dqs.algorithms')
    registry.register(SimpleNamespace(spec=AlgorithmSpec('team__test','1','test','team','test'),
        run=lambda context,params: {'verdict':'ACCEPTABLE','seen':context.episode.id}))
    monkeypatch.setattr('framework.execution.algorithms',lambda:registry)
    out=tmp_path/'results'
    assert main(['--input',str(root),'--output',str(out),'--algorithm','team__test'])==0
    assert (out/'catalog.json').exists()
    old=tmp_path/'old';old.mkdir();(old/'fast.ndjson').write_text('old data')
    assert main(['--input',str(root),'--output',str(old),'--algorithm','team__test'])==2
    assert (old/'fast.ndjson').read_text()=='old data'


def test_urdf_episode_binding_uses_named_joint_and_preserves_source(tmp_path):
    root = tmp_path/'episode';root.mkdir()
    file = root/'trajectory.json'
    file.write_text(json.dumps({'data': [{'timestamp': i, 'q': [q]} for i,q in enumerate([0,90])]}))
    original = file.read_bytes()
    p = {'schema_version': 1, 'id': 'test', 'version': '1', 'timestamp_unit': 's', 'parts': {'arm': {'dof': 1}},
         'channels': {'q': {'modality': 'joint_space', 'fields': ['position'], 'unit': 'deg',
                            'robot_part': 'arm', 'joint_names': ['turn'], 'source_kind': 'feedback',
                            'clock': 'test', 'time_source': 'synthetic', 'representation': {'quantity': 'fixture_value', 'semantic_status': 'confirmed'}}},
         'transforms': {'chains': {'arm': {'urdf': str(urdf(tmp_path)), 'base_frame': 'base', 'tip_frame': 'tool',
                                         'inputs': {'turn': {'channel': 'q', 'field': 'position', 'index': 0}}}}}}
    with next(open_dataset(root, profile=p).iter_episodes()).opened() as ep:
        out = ep.forward_kinematics('arm')
        np.testing.assert_allclose(out.output[:, :3, 3], [[1,0,0],[0,1,0]], atol=1e-12)
        assert out.source_mapping['time_source'] == 'synthetic'
        assert out.params['definition']['urdf_source']['sha256']
    assert file.read_bytes() == original


def test_selection_inherits_command_for_legacy_view(tmp_path):
    root,p = mapped_episode(tmp_path)
    p['channels']['state']['source_kind']='command'
    for name in ['left_arm_position','right_arm_position']:p['channels'][name].pop('source_kind')
    with next(open_dataset(root,profile=p).iter_episodes()).opened() as ep:
        assert ep.channels['left_arm_position'].source_kind == 'command'
        assert ep.describe_legacy().topics['left_arm_position'].modality == 'cmd'


def test_read_values_cannot_bypass_dof_validation(tmp_path):
    root,p=mapped_episode(tmp_path)
    p['channels']['state'].update(modality='joint_space',robot_part='left_arm')
    with next(open_dataset(root,profile=p).iter_episodes()).opened() as ep:
        with pytest.raises(ValueError,match='DOF mismatch'):ep.read_values('state')


def test_video_missing_timeline_is_missing_input_not_processing_error(tmp_path):
    from framework.ir.dataset import Channel
    from framework.registry import algorithms
    from Quality_Assessment.adapters.cpu import preflight
    from types import SimpleNamespace
    ep=SimpleNamespace(format='hdf5',profile={},selector={},channels={'rgb':Channel(
        'rgb','rgb',[],2,'source.hdf5',image_available=True)})
    status,reason=preflight(algorithms().plugins['Video_Integrity'],ep,{'input_mode':'video'})
    assert status=='missing_input' and 'FPS' in reason


def test_batch_preset_discovers_new_registration_without_dispatch_edit(tmp_path,monkeypatch):
    from framework.registry import Registry,AlgorithmSpec
    from types import SimpleNamespace
    from Quality_Assessment.batch_check import main
    root,_=mapped_episode(tmp_path)
    registry=Registry('dqs.algorithms')
    registry.register(SimpleNamespace(spec=AlgorithmSpec('team__new','1','test','team','test',presets=('quality_cpu',)),
                                     run=lambda context,params: {'verdict':'ACCEPTABLE'}))
    monkeypatch.setattr('framework.registry.algorithms',lambda:registry)
    monkeypatch.setattr('framework.execution.algorithms',lambda:registry)
    assert main(['--input',str(root),'--output',str(tmp_path/'out'),'--fast-only'])==0
    summary=json.loads(next((tmp_path/'out/_runs').glob('*/summary.json')).read_text())
    assert summary['status_counts']=={'completed':1}


def test_fk_rejects_binding_contradicting_declared_joint_order(tmp_path):
    from types import SimpleNamespace
    from framework.ir.dataset import Channel
    from framework.transforms.episode import fk
    chain=import_urdf_chain(urdf(tmp_path),'base','tool',inputs={
        'turn':{'channel':'q','field':'position','index':0}})
    c=Channel('q','joint_space',['position'],2,'source',unit='rad',joint_names=['wrong_joint'],
              timestamps=[0,1],clock='test')
    ep=SimpleNamespace(profile={'transforms':{'chains':{'arm':chain}}},channels={'q':c},
                       open=lambda:None,read_signal=lambda *args:np.array([[0.],[1.]]))
    with pytest.raises(ValueError,match='joint_names'):fk(ep,'arm')
