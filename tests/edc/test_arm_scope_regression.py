"""Public recommendation and execution must bind the same selected arm arrays."""
import copy
import hashlib
import json
import numpy as np
import pytest
from edc import open_dataset, run
from edc.algorithms.registry import algorithms
from edc.algorithms.recommendations import recommend_episode
from edc.data.ir.units import SIEpisode
from edc.robots.card import compile_card


@pytest.mark.parametrize('layout', ['shared', 'separate'])
@pytest.mark.parametrize('selection', [['left'], ['right'], 'all'])
@pytest.mark.parametrize('explicit_raw', [False, True])
def test_unselected_nan_cannot_leak(tmp_path, layout, selection, explicit_raw):
    h5py = pytest.importorskip('h5py')
    source = tmp_path/'data.h5'
    left = np.linspace(0, .5, 300).reshape(-1, 1)
    right = left.copy(); right[150, 0] = np.nan
    with h5py.File(source, 'w') as f:
        f['timestamp'] = np.arange(300)*.01
        if layout == 'shared':
            f['state'] = np.column_stack([left, right])
        else:
            f['l'] = left; f['r'] = right
    card = {'robot': {'name': 'test', 'confirmed_by': 'synthetic'},
        'data': {'time': {'source': 'timestamp', 'unit': 's'}, 'min_duration_s': 1},
        'arms': {a: {'joints': {'source': 'state' if layout == 'shared' else src,
            'index': i if layout == 'shared' else 0, 'unit': 'rad'}}
            for a,src,i in [('left','l',0),('right','r',1)]}}
    profile, defaults, _ = compile_card(card)
    params = {**defaults['Numeric_Check'], 'arms': selection}
    if explicit_raw:
        params['channels'] = ['state'] if layout == 'shared' else (['l', 'r'] if selection == 'all' else
            ['l' if selection == ['left'] else 'r'])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    with open_dataset(source, profile=profile) as ds:
        ep = SIEpisode(next(ds.iter_episodes())).open()
        try:
            entry = recommend_episode(ep, {'Numeric_Check': algorithms().plugins['Numeric_Check']},
                {'Numeric_Check': params})['algorithms'][0]
            recommended = entry['instances'][0]['input_scope']['channels']
        finally:
            ep.close()
        run(ds, algorithm_ids=['Numeric_Check'], params={'Numeric_Check': params}, output=tmp_path/'out')
    records = list((tmp_path/'out').glob('_runs/*/Numeric_Check/records.jsonl'))
    record = json.loads(records[0].read_text().splitlines()[0])
    assert record['status'] == 'completed', record['reason']
    assert recommended == record['input_scope']['channels']
    reports = record['result']['reports']
    expected = ['left', 'right'] if selection == 'all' else selection
    assert {r['input_channel'] for r in reports} == {f'joint/arm/{a}/feedback' for a in expected}
    assert any(r.get('finite') is False for r in reports) == ('right' in expected)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert any(x['reason'] == 'covered_by_derived_fields' for x in record['input_scope']['excluded_inputs'])


def test_main_gripper_and_missing_ownership(tmp_path):
    from types import SimpleNamespace
    from edc.data.ir.dataset import Channel
    from edc.robots.scope import instances
    profile, _, _ = compile_card({'robot': {'name': 'single', 'confirmed_by': 'test'},
        'arms': {'main': {'joints': {'source': 'state', 'index': 0, 'unit': 'rad'},
                         'gripper': {'source': 'grip', 'unit': 'unknown'}}}})
    assert profile['channels']['gripper/main']['robot_part'] == 'main'
    assert profile['channels']['grip']['robot_part'] == 'main'
    c = Channel('joint/arm/main/feedback', 'joint_space', ['position'], 10, 'x', value_available=True,
        robot_part='main', source_kind='feedback')
    raw = Channel('unowned', 'other', ['value'], 10, 'x', value_available=True)
    ep = SimpleNamespace(profile=profile, channels={c.name:c, raw.name:raw})
    instance = instances(ep, {}, 'numeric')[0]
    assert instance['scope']['arms'] == ['main']
    assert instance['scope']['channels'] == [c.name]
    assert instance['scope']['missing_ownership'] == ['unowned']
    assert not instance['complete']


def test_independent_single_arm_public_run(tmp_path):
    folder = tmp_path/'single'; folder.mkdir()
    source = folder/'trajectory.json'
    source.write_text(json.dumps({'data': [{'timestamp': i*.1, 'state': [i*.01]} for i in range(50)]}))
    profile, params, _ = compile_card({'robot': {'name': 'single', 'confirmed_by': 'synthetic'},
        'data': {'time': {'source': 'timestamp', 'unit': 's'}},
        'arms': {'main': {'joints': {'source': 'state', 'index': 0, 'unit': 'rad'}}}})
    with open_dataset(folder, profile=profile) as ds:
        run(ds, algorithm_ids=['Numeric_Check'], params=params, output=tmp_path/'out')
    record = json.loads(next((tmp_path/'out').glob('_runs/*/Numeric_Check/records.jsonl')).read_text())
    assert record['status'] == 'completed'
    assert record['scope_complete'] and record['input_scope']['arms'] == ['main']
    assert not record['input_scope']['missing_arms']
    assert {r['input_channel'] for r in record['result']['reports']} == {'joint/arm/main/feedback'}


def test_unmapped_shared_field_does_not_cross_arm_scope():
    from types import SimpleNamespace
    from edc.data.ir.dataset import Channel
    from edc.robots.scope import instances
    raw = Channel('state', 'other', ['position', 'effort'], 10, 'x', value_available=True)
    channels = {'state': raw}
    profile = {'parts': {a: {'type': 'arm', 'side': a} for a in ['left','right']}}
    for a,i in [('left',0),('right',1)]:
        channels[a] = Channel(a, 'joint_space', ['position'], 10, 'x', value_available=True,
            source_kind='feedback', robot_part=a,
            selection={'channel':'state','field':'position','indices':[i]})
    scope = instances(SimpleNamespace(profile=profile,channels=channels), {'arms':['left']}, 'numeric')[0]
    assert scope['scope']['channels'] == ['left']
    assert scope['scope']['missing_ownership'] == ['state']
    assert not scope['complete']
