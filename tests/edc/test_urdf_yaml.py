"""URDF part of the robot yaml (explicit joint mapping, tip_offset) and `edc robot urdf`;
declared units, raw ranges and scale of the other entries."""
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from edc.data.ir.dataset import open_dataset
from edc.data.transforms.urdf import import_urdf_chain
from edc.robots.card import CardError, compile_card
from edc.robots.urdf_check import forward, list_urdf, rotation, verify

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / 'configs' / 'robots' / 'x1pro' / 'x1pro_left_arm.urdf'
NAMES = [f'left_arm_joint{i}' for i in range(1, 7)]


def angles(n=300, start=0):
    t = np.linspace(start, start + 20, n)[:, None]
    centre, amplitude = np.array([0, 1.2, -1.2, 0, 0, 0]), np.array([.6, .5, .5, .6, .6, .8])
    return centre + amplitude * np.sin(t * np.array([.31, .47, .53, .71, .89, 1.13]) + np.arange(6))


def rpy(matrices):
    return np.column_stack([np.arctan2(matrices[:, 2, 1], matrices[:, 2, 2]), np.arcsin(-matrices[:, 2, 0]),
                            np.arctan2(matrices[:, 1, 0], matrices[:, 0, 0])])


def recording(tmp_path, q, name='episode'):
    """一条 JSON 数据：关节角，和机器人会记录的末端位姿（另一个坐标系，末端点在 tip 前 5 cm）。"""
    chain = import_urdf_chain(ARM, 'left_arm_base_link', 'left_arm_link6')
    poses = forward(chain, q)
    frame, tool = rotation([0, 0, 1], .6), rotation([1, 0, 0], .3)
    tip = (poses[:, :3, 3] + poses[:, :3, :3] @ [.05, 0, 0]) @ frame.T + [.1, .2, .3]
    orientation = rpy(np.einsum('ij,njk,kl->nil', frame, poses[:, :3, :3], tool))
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    rows = [{'timestamp': 100 + i / 30, 'q': list(a), 'tip': list(p), 'rot': list(r)}
            for i, (a, p, r) in enumerate(zip(q, tip, orientation))]
    (root / 'trajectory.json').write_text(json.dumps({'data': rows}))
    return root


def robot_yaml(tmp_path, urdf=None, arm=None):
    arm_spec = {'joints': {'source': 'q', 'index': '0-5', 'unit': 'rad'},
                'eef_position': {'source': 'tip', 'index': '0-2', 'unit': 'm'},
                'eef_orientation': {'source': 'rot', 'unit': 'rpy_rad'},
                'urdf': {'file': str(ARM), 'joints': list(NAMES), 'base_link': 'left_arm_base_link',
                         'tip_link': 'left_arm_link6', 'tip_offset': [.05, 0, 0], **(urdf or {})}}
    arm_spec['urdf'] = {k: v for k, v in arm_spec['urdf'].items() if v is not None}
    arm_spec.update(arm or {})
    card = {'robot': {'name': 'synthetic', 'confirmed_by': 'tester'}, 'data': {'time': {'source': 'timestamp'}},
            'arms': {'left': {k: v for k, v in arm_spec.items() if v is not None}}}
    path = tmp_path / 'robot.yaml'
    path.write_text(yaml.safe_dump(card, allow_unicode=True), encoding='utf-8')
    return path


def check(tmp_path, q=None, **change):
    """两条数据（第二条换一段动作）核对 yaml。"""
    q = angles() if q is None else q
    data = [recording(tmp_path, q), recording(tmp_path, angles(start=30), 'episode2')]
    lines = []
    result = verify(robot_yaml(tmp_path, **change), data, log=lines.append)['left']
    return result, '\n'.join(lines)


def test_list_shows_tree_and_the_yaml_lines(capsys):
    assert list_urdf(ARM) == 0
    assert 'left_arm_joint3 [转动，-2.792 ~ 0 rad，速度上限 4.188]' in capsys.readouterr().out
    assert list_urdf(ARM, 'left_arm_base_link', 'left_arm_link6') == 0
    out = capsys.readouterr().out
    assert f'joints: [{", ".join(NAMES)}]' in out and f'file: {ARM.parent.name}/{ARM.name}' in out
    assert list_urdf(ARM, 'left_arm_link6', 'left_arm_base_link') == 1     # Tip above base.
    capsys.readouterr()
    assert list_urdf(ARM, 'left_arm_joint1', 'left_arm_joint6') == 1      # Joint names instead of links.
    assert 'left_arm_joint1 是关节，这里要写连杆' in capsys.readouterr().out


def test_joint_and_velocity_limits_follow_the_yaml_order():
    from edc.robots.card import urdf_chain
    names = ['left_arm_joint4', 'left_arm_joint3', 'left_arm_joint1', 'left_arm_joint2', 'left_arm_joint5', 'left_arm_joint6']
    spec = {'file': str(ARM), 'joints': names, 'base_link': 'left_arm_base_link', 'tip_link': 'left_arm_link6'}
    chain = urdf_chain(spec, ARM.parent, 'arms.left.urdf', 6, 'joint/arm/left/feedback')
    np.testing.assert_allclose(chain['limits'][:2], [[-1.5808, 1.5808], [-2.8025, 0.01]], atol=2e-3)
    assert chain['velocity'][:2] == [3.77, 4.188]      # 按 yaml 的顺序


def test_each_arm_has_its_own_velocity_table(tmp_path):
    from types import SimpleNamespace
    from edc.algorithms.quality.algorithms.Numeric_Check.review import channel_settings, numeric_settings
    card = yaml.safe_load(robot_yaml(tmp_path).read_text(encoding='utf-8'))
    card['arms']['left']['joint_velocity'] = {'source': 'v', 'index': '0-5', 'unit': 'rad/s'}
    card['arms']['right'] = {'joints': {'source': 'q2', 'index': '0-6', 'unit': 'rad'},
                             'joint_velocity': {'source': 'v2', 'index': '0-6', 'unit': 'rad/s'}}   # 没有 URDF
    profile, params, _ = compile_card(card)
    settings = numeric_settings(profile, params['Numeric_Check'])
    pick = lambda side: channel_settings(settings, SimpleNamespace(name=f'joint/arm/{side}/feedback'))
    assert pick('left')['thresholds']['joint_velocity']['max_abs'] == [4.188] * 3 + [3.77] * 3
    assert pick('right')['thresholds']['joint_velocity']['max_abs'] is None   # 没有 URDF：不查超速


def test_commented_out_xacro_is_not_xacro(tmp_path, capsys):
    path = tmp_path / 'comment.urdf'
    path.write_text(ARM.read_text(encoding='utf-8').replace('</robot>', '<!-- size="${x}" --></robot>'), encoding='utf-8')
    assert list_urdf(path) == 0
    path.write_text(ARM.read_text(encoding='utf-8').replace('</robot>', '<link name="a${x}"/></robot>'), encoding='utf-8')
    assert list_urdf(path) == 1 and 'xacro' in capsys.readouterr().out


def test_problems_off_the_arm_do_not_block(tmp_path, capsys):
    text = ARM.read_text(encoding='utf-8').replace('</robot>', '''
  <link name="finger"/>
  <joint name="finger_joint" type="revolute"><parent link="left_arm_link6"/><child link="finger"/>
    <axis xyz="0 0 1"/><limit lower="0" upper="1" velocity="0"/><mimic joint="left_arm_joint6"/></joint>
</robot>''')
    path = tmp_path / 'hand.urdf'
    path.write_text(text, encoding='utf-8')
    assert list_urdf(path) == 0 and 'mimic' in capsys.readouterr().out
    card = yaml.safe_load(robot_yaml(tmp_path, urdf={'file': str(path)}).read_text(encoding='utf-8'))
    profile, _, _ = compile_card(card)                 # The finger is not on the arm chain.
    assert profile['transforms']['chains']['left']['tip_frame'] == 'left_arm_link6'


@pytest.mark.parametrize('urdf, message', [
    ({'joints': NAMES[:5] + ['nope']}, '没写 left_arm_joint6；这条链上没有 nope'),
    ({'joints': NAMES[:5]}, '写了 5 个关节名'),
    ({'joints': NAMES[:5] + [NAMES[0]]}, '重复'),
    ({'base_link': 'nope'}, 'URDF 里没有连杆 nope'),
    ({'base_link': 'left_arm_link6', 'tip_link': 'left_arm_base_link'}, '走不到'),
    ({'typo': 1}, '不认识的项 typo'),
])
def test_urdf_mistakes_are_explained(tmp_path, urdf, message):
    card = yaml.safe_load(robot_yaml(tmp_path, urdf=urdf).read_text(encoding='utf-8'))
    with pytest.raises(CardError, match=message):
        compile_card(card)
    card['arms']['left']['urdf'].pop('joints')
    with pytest.raises(CardError, match='joints'):
        compile_card(card)


def test_data_order_maps_columns_to_joints(tmp_path):
    """Data columns in another order than the URDF: limits and FK follow the yaml mapping."""
    q = angles()
    order = [3, 0, 1, 2, 5, 4]                         # Data column i holds URDF joint order[i].
    data = recording(tmp_path, q)
    rows = json.loads((data / 'trajectory.json').read_text())
    for row, a in zip(rows['data'], q):
        row['q'] = [a[k] for k in order]
    (data / 'trajectory.json').write_text(json.dumps(rows))
    path = robot_yaml(tmp_path, urdf={'joints': [NAMES[k] for k in order]}, arm={'eef_position': 'fk'})
    card = yaml.safe_load(path.read_text(encoding='utf-8'))
    profile, _, _ = compile_card(card)
    limits = profile['numeric_check']['thresholds']['joint']['limits']['joint/arm/left/feedback']
    assert limits[0] == pytest.approx([-1.581, 1.581], abs=1e-3)          # Column 0 is joint 4.
    episode = next(open_dataset(data, profile=profile).iter_episodes())
    with episode.opened():
        tip = episode.read_signal('pose/arm/left/feedback', 'position')
    expected = forward(import_urdf_chain(ARM, 'left_arm_base_link', 'left_arm_link6', tip_offset=[.05, 0, 0]), q)[:, :3, 3]
    np.testing.assert_allclose(tip, expected, atol=1e-9)


def test_episode_group_splits_one_hdf5_into_episodes(tmp_path):
    h5py = pytest.importorskip('h5py')
    path = tmp_path / 'demos.hdf5'
    with h5py.File(path, 'w') as f:                    # robomimic layout: data/demo_N/obs/...
        for i, rows in enumerate((40, 50)):
            f.create_dataset(f'data/demo_{i}/obs/q', data=np.full((rows, 6), 0.1 * i))
    card = yaml.safe_load(robot_yaml(tmp_path).read_text(encoding='utf-8'))
    card['data'] = {'episode_group': 'data', 'time_note': '只存帧'}
    card['arms']['left'] = {'joints': {'source': 'obs/q', 'index': '0-5', 'unit': 'rad'}}
    profile, _, _ = compile_card(card)
    episodes = list(open_dataset(path, profile=profile).iter_episodes())
    assert [e.id.rsplit('/', 1)[-1] for e in episodes] == ['demo_0', 'demo_1']
    with episodes[1].opened():
        assert np.asarray(episodes[1].read_signal('joint/arm/left/feedback', 'position')).shape == (50, 6)


def test_verify_matches_correct_yaml(tmp_path):
    result, text = check(tmp_path)
    assert result['verdict'] == 'correct' and result['rms_mm'] < 0.1 and result['deg'] < 0.1, text
    assert np.allclose(result['frame_shift'], [.1, .2, .3], atol=1e-6)          # 第一条求出的坐标系就是造数据用的


@pytest.mark.parametrize('change', [
    {'urdf': {'joints': [NAMES[0], NAMES[1], NAMES[3], NAMES[2], NAMES[4], NAMES[5]]}},
    {'urdf': {'tip_offset': None}},                    # 数据末端点在 tip 前 5 cm，yaml 没写
    {'urdf': {'tip_offset': [.08, 0, 0]}},             # tip_offset 写错
    {'arm': {'joints': {'source': 'q', 'index': '0-5', 'unit': 'deg'}}},
    {'arm': {'eef_orientation': {'source': 'rot', 'unit': 'rpy_deg'}}},
])
def test_wrong_yaml_does_not_match(tmp_path, change):
    result, text = check(tmp_path, **change)
    assert result['verdict'] == 'mismatch' and '对不上' in text and 'fix' not in result, text


def test_end_effector_needs_two_episodes(tmp_path):
    lines = []
    result = verify(robot_yaml(tmp_path), recording(tmp_path, angles()), log=lines.append)['left']
    assert result['verdict'] == 'unverified' and '再给一条数据' in lines[-1]


def test_tip_offset_moves_the_fk_point(tmp_path):
    plain = import_urdf_chain(ARM, 'left_arm_base_link', 'left_arm_link6')
    longer = import_urdf_chain(ARM, 'left_arm_base_link', 'left_arm_link6', tip_offset=[0, 0, .1])
    a, b = forward(plain, angles(5)), forward(longer, angles(5))
    assert np.allclose(b[:, :3, 3], a[:, :3, 3] + a[:, :3, 2] * .1)
    with pytest.raises(CardError, match='tip_offset'):
        compile_card(yaml.safe_load(robot_yaml(tmp_path, urdf={'tip_offset': [0, .1]}).read_text(encoding='utf-8')))


def test_verify_without_recorded_tip_is_unverified(tmp_path):
    result, text = check(tmp_path, arm={'eef_position': 'fk', 'eef_orientation': None})
    assert result['verdict'] == 'unverified' and '末端未验证' in text
    q = angles()
    q[:, 1] = -q[:, 1]                                 # Outside joint 2's 0..3.67 limits.
    result, text = check(tmp_path, q, arm={'eef_position': 'fk', 'eef_orientation': None})
    assert result['verdict'] == 'mismatch' and '✗ 第 2 列' in text


def card_with(**arm):
    return {'robot': {'name': 'demo', 'confirmed_by': 'tester'},
            'arms': {'main': {'joints': {'source': 'state', 'index': '0-5', 'unit': 'rad'}, **arm}}}


@pytest.mark.parametrize('entry, message', [
    ({'gripper': {'source': 'g'}}, 'gripper.unit 必填'),
    ({'gripper': {'source': 'g', 'unit': 'raw'}}, '要写 range'),
    ({'gripper': {'source': 'g', 'unit': 'raw', 'range': [5, 1]}}, 'range'),
    ({'hand': {'source': 'h', 'unit': 'unknown', 'range': [0, 1]}}, '不能写 range'),
    ({'eef_orientation': {'source': 'o', 'unit': 'quaternion'}}, 'xyzw 或 wxyz'),
    ({'eef_orientation': {'source': 'o', 'index': '0-2', 'unit': 'quaternion', 'order': 'xyzw'}}, '4 个数'),
    ({'joints': {'source': 'state', 'index': '0-5', 'unit': 'rad', 'scale': {'raw': [0, 0], 'value': [0, 1]}}}, 'scale'),
    ({'joints': {'source': 'state', 'index': '0-5', 'unit': 'rad', 'range': [0, 1]}}, '不认识的项 range'),
])
def test_every_entry_states_its_unit(entry, message):
    with pytest.raises(CardError, match=message):
        compile_card(card_with(**entry))


def test_raw_range_and_scale_compile():
    profile, params, _ = compile_card(card_with(
        joints={'source': 'state', 'index': '0-5', 'unit': 'rad', 'scale': {'raw': [0, 4096], 'value': [-3.1416, 3.1416]}},
        gripper={'source': 'state', 'index': 6, 'unit': 'raw', 'range': [0, 255]},
        hand={'source': 'hand', 'unit': 'unknown'},
        eef_orientation={'source': 'pose', 'index': '3-6', 'unit': 'quaternion', 'order': 'wxyz'}))
    scale = profile['channels']['joint/arm/main/feedback']['field_metadata']['position']['scale']
    assert scale == pytest.approx([6.2832 / 4096, -3.1416])
    assert params['Numeric_Check']['ranges'] == {'gripper/main.value': {'range': [0.0, 255.0], 'unit': 'raw'}}
    assert 'hand.value' in params['Numeric_Check']['finiteness_only']


def test_scale_maps_raw_joint_readings(tmp_path):
    data = []
    for name, q in (('episode', angles()), ('episode2', angles(start=30))):
        data.append(recording(tmp_path, q, name))
        rows = json.loads((data[-1] / 'trajectory.json').read_text())
        for row, a in zip(rows['data'], q):
            row['q'] = [round((v + np.pi) / (2 * np.pi) * 4096) for v in a]     # 12 位编码器读数
        (data[-1] / 'trajectory.json').write_text(json.dumps(rows))
    lines = []
    path = robot_yaml(tmp_path, arm={'joints': {'source': 'q', 'index': '0-5', 'unit': 'rad',
                                                'scale': {'raw': [0, 4096], 'value': [-np.pi, np.pi]}}})
    result = verify(path, data, log=lines.append)['left']
    assert result['verdict'] == 'correct' and result['rms_mm'] < 2, '\n'.join(lines)    # Only rounding left.


def test_out_of_range_raw_values_exclude():
    from types import SimpleNamespace
    from edc.algorithms.quality.algorithms.Numeric_Check.supplements import declared_range
    from edc.algorithms.quality.algorithms.Numeric_Check.result import explain
    channel = SimpleNamespace(selection={'indices': [6]}, source='x', name='gripper/main', source_mapping={},
                              field_spec=lambda field: {'joint_names': None})
    values = np.array([[0.], [128.], [300.], [255.]])
    check = declared_range(values, SimpleNamespace(id='e', selector={}), channel, 'value', {'range': [0, 255], 'unit': 'raw'})
    assert check['verdict'] == 'DO_NOT_USE' and check['rows_over'] == 1 and check['events'][0]['value'] == 300
    assert '1/4 行超出 yaml 写的范围 [0, 255]' in explain({'supplemental_checks': [check]}, {})
    assert declared_range(values[:2], SimpleNamespace(id='e', selector={}), channel, 'value',
                          {'range': [0, 255]})['verdict'] == 'ACCEPTABLE'


def test_range_runs_through_numeric_check(tmp_path):
    """A declared range reaches Numeric_Check through the yaml: out-of-range rows exclude that entry only."""
    from edc import open_dataset as open_source, run
    data = recording(tmp_path, angles(120))
    rows = json.loads((data / 'trajectory.json').read_text())
    for i, row in enumerate(rows['data']):
        row['grip'] = 150 if i == 60 else 40
        row['hand'] = 50
    (data / 'trajectory.json').write_text(json.dumps(rows))
    card = yaml.safe_load(robot_yaml(tmp_path).read_text(encoding='utf-8'))
    card['arms']['left'].update(gripper={'source': 'grip', 'unit': 'raw', 'range': [0, 100]},
                                hand={'source': 'hand', 'unit': 'raw', 'range': [0, 100]})
    profile, params, _ = compile_card(card)
    with open_source(data, profile=profile) as dataset:
        run(dataset, algorithm_ids=['Numeric_Check'], params={'Numeric_Check': params['Numeric_Check']},
            output=tmp_path / 'out')
    text = next((tmp_path / 'out/_runs').glob('*/Numeric_Check/records.jsonl')).read_text(encoding='utf-8')
    assert 'Invalid algorithm parameters' not in text
    reports = [r for line in text.splitlines() for r in (json.loads(line).get('result') or {}).get('reports') or []]
    by_channel = {r.get('input_channel'): r for r in reports}
    assert by_channel['gripper/left']['supplemental_verdict'] == 'DO_NOT_USE'
    assert 'supplemental_verdict' not in by_channel['hand/left']
