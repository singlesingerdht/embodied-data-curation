"""Robot yaml, common result format and URDF check (2026-10-31 refactor)."""
from pathlib import Path

import numpy as np
import pytest
import yaml

from edc.algorithms.base import (AlgorithmResult, Item, PASS, REVIEW, SALVAGE, EXCLUDE, NOT_APPLICABLE, FAILED,
                                 decide)
from edc.robots.card import CardError, compile_card, indices
from edc.robots.profiles import load_profile

ROOT = Path(__file__).resolve().parents[2]
ROBOTS = ROOT / 'configs' / 'robots'


def card(**arms):
    return {'robot': {'name': 'demo', 'confirmed_by': 'tester'},
            'arms': arms or {'main': {'joints': {'source': 'observation.state', 'index': '0-5', 'unit': 'rad'},
                                      'eef_position': {'source': 'observation.eef', 'index': '0-2', 'unit': 'm'}}},
            'cameras': {'wrist': {'source': 'observation.images.wrist', 'mount': 'wrist', 'arm': 'main'}}}


def test_index_forms():
    assert indices('0-5', 'x') == [0, 1, 2, 3, 4, 5]
    assert indices(7, 'x') == [7]
    assert indices([0, 2], 'x') == [0, 2]
    with pytest.raises(CardError):
        indices('5-0', 'x')


def test_single_arm_compiles_to_standard_channels():
    profile, params, names = compile_card(card())
    assert profile['parts']['main'] == {'type': 'arm', 'side': 'unknown', 'dof': 6}
    joint = profile['channels']['joint/arm/main/feedback']
    assert joint['select'] == {'channel': 'observation.state', 'fields': {'position': 'value'}, 'indices': list(range(6))}
    assert profile['channels']['pose/arm/main/feedback']['unit'] == 'm'
    assert profile['camera_requirements'] == {'wrist': ['rgb']}
    assert profile['channels']['observation.images.wrist']['logical_name'] == 'rgb/wrist/main'
    assert profile['declared_only'] and '*' not in profile['channels']   # Data not in the yaml is not checked.
    assert profile['numeric_review']['confirmed_by']
    assert 'observation.state.value' in params['Numeric_Check']['finiteness_only']
    assert params['Numeric_Check']['joint_limits_pending']     # No URDF: limits not checked, said so.
    assert names['joint/arm/main/feedback'] == '关节' and profile['labels'] is names


def test_yaml_mistakes_are_explained():
    bad = card()
    del bad['robot']['confirmed_by']
    with pytest.raises(CardError, match='confirmed_by'):
        compile_card(bad)
    bad = card()
    del bad['arms']['main']['joints']['unit']
    with pytest.raises(CardError, match='unit'):
        compile_card(bad)
    bad = card()
    bad['arms']['main']['typo'] = 1
    with pytest.raises(CardError, match='typo'):
        compile_card(bad)
    ok = card()   # 没有 URDF：速度不查超速，原始数据只查 NaN/无穷大
    ok['arms']['main']['joint_velocity'] = {'source': 'v', 'index': '0-5', 'unit': 'rad/s'}
    profile, params, _ = compile_card(ok)
    assert 'velocity' not in profile['channels']['joint/arm/main/feedback']['fields']
    assert 'v.value' in params['Numeric_Check']['finiteness_only']


def test_x1pro_yaml_reads_urdf_limits_and_velocity():
    profile = load_profile(ROBOTS / 'x1pro.yaml')
    limits = profile['numeric_check']['thresholds']['joint']['limits']['joint/arm/left/feedback']
    assert limits[1] == pytest.approx([-0.01, 3.6752])               # URDF 0..3.6652, widened 0.01 rad
    speeds = profile['numeric_check']['thresholds']['joint_velocity']['max_abs']   # One table per arm.
    assert speeds == {f'joint/arm/{side}/feedback': [4.188] * 3 + [3.77] * 3 for side in ('left', 'right')}
    velocity = profile['channels']['joint/arm/left/feedback']['select']['fields']['velocity']
    assert velocity == {'channel': 'follow_left_joint_velocity', 'field': 'value', 'indices': list(range(6))}
    assert profile['calibration']['Reset_Detection']['tolerance'] == 0.023 and 'unchecked' not in profile   # home 用师兄标定的；URDF 齐全


def test_checks_skip_and_unchecked_note():
    data = card()  # 没写 urdf
    data['checks'] = {'skip': ['Reset_Detection']}
    profile, params, _ = compile_card(data)
    assert params['Reset_Detection'] == {'skip': True}
    assert profile['unchecked']['Numeric_Check'] == '未查：关节限位（没写 urdf）'
    data['checks'] = {'skip': ['Reset']}
    with pytest.raises(CardError, match='checks.skip'):
        compile_card(data)


def test_urdf_joint_count_must_match():
    bad = card()
    bad['arms']['main']['urdf'] = {'file': str(ROBOTS / 'x1pro' / 'x1pro_left_arm.urdf'),
                                   'joints': [f'left_arm_joint{i}' for i in range(1, 7)],
                                   'base_link': 'left_arm_base_link', 'tip_link': 'left_arm_link6'}
    bad['arms']['main']['joints']['index'] = '0-4'
    with pytest.raises(CardError, match='写了 6 个关节名'):
        compile_card(bad)


@pytest.mark.parametrize('name', ['x1pro', 'leju_kuavo', 'pepsi_sort', 'luoshi'])
def test_shipped_robot_yamls_load(name):
    assert load_profile(ROBOTS / f'{name}.yaml')['id']


@pytest.mark.parametrize('name', ['single_arm', 'dual_arm'])
def test_templates_load(name):
    assert load_profile(ROOT / 'configs' / 'templates' / f'{name}.yaml')['parts']


def test_item_rules_and_decision_order():
    with pytest.raises(ValueError):
        Item('x', verdict=None)                      # Checked needs a verdict.
    with pytest.raises(ValueError):
        Item('x', status=NOT_APPLICABLE, verdict=PASS)
    with pytest.raises(ValueError):
        Item('x', verdict=SALVAGE, reason='r')       # SALVAGE needs a remedy.
    for kwargs in ({'verdict': REVIEW}, {'verdict': EXCLUDE}, {'verdict': SALVAGE, 'remedy': 'fix'},
                   {'status': NOT_APPLICABLE}, {'status': FAILED}, {'verdict': REVIEW, 'reason': '  '}):
        with pytest.raises(ValueError, match='needs a reason'):
            Item('x', **kwargs)                      # Everything but PASS says why.
    assert Item('x', verdict=PASS).reason == ''
    result = lambda *levels: [AlgorithmResult('a', [Item('x', verdict=v, reason='r', remedy='fix' if v == SALVAGE else '')
                                                    if v in (PASS, REVIEW, SALVAGE, EXCLUDE) else Item('x', status=v, reason='r')])
                              for v in levels]
    assert decide(result(PASS, NOT_APPLICABLE)) == 'keep'
    assert decide(result(PASS, REVIEW)) == 'review'
    assert decide(result(REVIEW, FAILED)) == 'unassessed'
    assert decide(result(FAILED, SALVAGE)) == 'salvage'
    assert decide(result(SALVAGE, EXCLUDE)) == 'exclude'


def record(report, status='completed', reason=None):
    return {'status': status, 'reason': reason, 'result': {'reports': [report]} if report else None}


def test_builtin_reports_carry_four_verdicts():
    from edc.algorithms.quality.shared.verdicts import emit
    from edc.algorithms.quality.adapters.results import to_result

    def result(algo, report, notes=None):
        return to_result(algo, [record(emit(algo, report, {}))], notes)

    stuck = {'verdict': 'ACCEPTABLE', 'supplemental_verdict': 'DO_NOT_USE', 'input_channel': 'joint/arm/left/feedback',
             'supplemental_checks': [{'id': 'joint_stuck', 'verdict': 'DO_NOT_USE'}]}
    item = result('Numeric_Check', stuck).items[0]
    assert (item.verdict, bool(item.remedy)) == (SALVAGE, True)
    assert result('Numeric_Check', {'skipped': 'too_short', 'verdict': 'NOT_APPLICABLE'}).level == NOT_APPLICABLE
    clock = {'verdict': 'DO_NOT_USE', 'clock_diagnosis': {'salvage': {'status': 'fixed_offset'}}}
    assert result('Check_Timestamp', clock).level == SALVAGE      # A fixed clock offset explains the exclusion.
    assert result('Video_Integrity', {'verdict': 'NEEDS_REVIEW'}).level == REVIEW
    missing = to_result('Sync_Check', [record(None, 'missing_input', '只有公共行时间')], {'time': 'LeRobot 按帧号'})
    assert missing.level == NOT_APPLICABLE and 'LeRobot 按帧号' in missing.items[0].reason
    assert to_result('Sync_Check', [record(None, 'processing_error', 'boom')]).level == FAILED


def test_new_algorithm_returns_common_result():
    from edc.algorithms.base import QualityAlgorithm
    from edc.algorithms.quality.adapters.results import to_result

    class Demo(QualityAlgorithm):
        class spec:
            id = 'team__demo'

        def check(self, episode, params):
            return [Item('左臂', verdict=REVIEW, reason='示例')]

    raw = Demo().run(type('C', (), {'episode': None})(), {})
    result = to_result('team__demo', [record(raw)])
    assert result.level == REVIEW and result.items[0].reason == '示例'
