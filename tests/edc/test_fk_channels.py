import copy
import json
import numpy as np
import pytest
from edc.data.ir.dataset import open_dataset
from edc.data.transforms import RobotTransforms, import_urdf_chain
from edc.robots.profiles import load_profile


def urdf(tmp_path, joint='type="continuous"', limit=''):
    p = tmp_path / 'robot.urdf'
    p.write_text(f'''<robot name="synthetic">
    <link name="base"/><link name="arm"/><link name="tool"/>
    <joint name="turn" {joint}><parent link="base"/><child link="arm"/>
    <axis xyz="0 0 1"/>{limit}</joint>
    <joint name="tip" type="fixed"><parent link="arm"/><child link="tool"/>
    <origin xyz="1 0 0" rpy="0 0 0"/></joint></robot>''')
    return p


def fk_episode(tmp_path):
    root = tmp_path / 'episode'
    root.mkdir()
    (root / 'trajectory.json').write_text(json.dumps({'data': [
        {'timestamp': i, 'q': [9.0, q]} for i, q in enumerate([0, np.pi / 2, np.pi])]}))
    profile = {'schema_version': 1, 'id': 'test', 'version': '1', 'timestamp_unit': 's',
               'parts': {'arm': {'type': 'arm', 'side': 'left', 'dof': 1}},
               'channels': {
                   'q': {'modality': 'other', 'fields': ['position'], 'clock': 'robot', 'time_source': 'synthetic'},
                   'arm_q': {'modality': 'joint_space', 'fields': ['position'], 'robot_part': 'arm', 'unit': 'rad',
                             'source_kind': 'feedback', 'calibration_status': 'confirmed',
                             'select': {'channel': 'q', 'field': 'position', 'indices': [1]}},
                   'tip': {'modality': 'pose', 'fields': ['position'], 'robot_part': 'arm', 'unit': 'm',
                           'select': {'fk': 'arm'}}},
               'transforms': {'chains': {'arm': {'urdf': str(urdf(tmp_path)), 'base_frame': 'base', 'tip_frame': 'tool',
                                                 'inputs': {'turn': {'channel': 'arm_q', 'field': 'position', 'index': 0}}}}}}
    return root, profile


def test_fk_selection_derives_pose_on_source_timeline(tmp_path):
    root, profile = fk_episode(tmp_path)
    with next(open_dataset(root, profile=profile).iter_episodes()).opened() as ep:
        tip = ep.channels['tip']
        np.testing.assert_allclose(ep.read_signal('tip', 'position'), [[1, 0, 0], [0, 1, 0], [-1, 0, 0]], atol=1e-12)
        spec = tip.field_spec('position')
        assert (spec['unit'], spec['coordinate_frame'], spec['source_kind'], spec['calibration_status']) == \
            ('m', 'base', 'feedback', 'confirmed')
        assert (tip.clock, tip.count, list(tip.timestamps)) == ('robot', 3, list(ep.channels['q'].timestamps))
        assert ep.read_values('tip')[1]['position'] == pytest.approx([0, 1, 0], abs=1e-12)
        assert ep.raw('tip').value_available


def test_fk_pose_calibration_is_only_as_strong_as_inputs(tmp_path):
    root, profile = fk_episode(tmp_path)
    profile['channels']['arm_q']['calibration_status'] = 'pending'
    with next(open_dataset(root, profile=profile).iter_episodes()).opened() as ep:
        assert ep.channels['tip'].field_spec('position')['calibration_status'] == 'pending'


@pytest.mark.parametrize('change', ['chain', 'fields', 'unit', 'modality', 'pose_input'])
def test_fk_selection_rejects_ambiguous_config(tmp_path, change):
    _, profile = fk_episode(tmp_path)
    p = copy.deepcopy(profile)
    tip = p['channels']['tip']
    if change == 'chain': tip['select']['fk'] = 'absent'
    if change == 'fields': tip['fields'] = ['position', 'orientation']
    if change == 'unit': tip['unit'] = 'mm'
    if change == 'modality': tip['modality'] = 'joint_space'
    if change == 'pose_input':
        p['transforms']['chains']['arm']['inputs']['turn']['channel'] = 'tip'
    with pytest.raises(ValueError):
        load_profile(p)


def test_limit_margin_widens_urdf_limits(tmp_path):
    path = urdf(tmp_path, 'type="revolute"', '<limit lower="0" upper="1"/>')
    strict = RobotTransforms({'transforms': {'chains': {'arm': import_urdf_chain(path, 'base', 'tool')}}})
    with pytest.raises(ValueError, match='limits'):
        strict.fk('arm', {'turn': 1.005})
    loose = RobotTransforms({'transforms': {'chains': {'arm': {
        'urdf': str(path), 'base_frame': 'base', 'tip_frame': 'tool', 'limit_margin': 0.01}}}})
    assert loose.chains['arm']['joints'][0]['limits'] == pytest.approx([-0.01, 1.01])
    assert loose.chains['arm']['urdf_source']['limit_margin'] == 0.01
    loose.fk('arm', {'turn': 1.005})
    with pytest.raises(ValueError, match='limits'):
        loose.fk('arm', {'turn': 1.02})
    for bad in (-0.1, float('nan'), True, '0.01'):
        with pytest.raises(ValueError, match='limit_margin'):
            import_urdf_chain(path, 'base', 'tool', limit_margin=bad)
