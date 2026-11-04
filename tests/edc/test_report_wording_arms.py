"""Report wording must distinguish missing evaluation from findings and summarize every selected arm."""
from copy import deepcopy
import json

import pytest

from edc.algorithms.base import AlgorithmResult, Item, FAILED, NOT_APPLICABLE, decide
from edc.algorithms.quality.adapters.results import to_result
from edc.algorithms.quality.shared.verdicts import emit
from edc.reporting.batch import page


def record(raw, instance='episode'):
    return {'status': 'completed', 'instance_id': instance,
            'result': {'reports': raw if isinstance(raw, list) else [raw]}}


def render(results, **extra):
    bag = {'name': 'synthetic', 'source': 'synthetic', 'seconds': 0,
           'decision': decide(results), 'algos': {r.algorithm: r.to_dict() for r in results}, **extra}
    return page([bag], {'robot': 'test', 'inputs': 'synthetic', 'finished': 'test',
                       'seconds': 0, 'jobs': 1, 'profile': 'synthetic'})


@pytest.mark.parametrize('sides', [('left', 'right'), ('left',), ('right',), ('main',)])
def test_summary_includes_every_selected_arm_without_adding_absent_arm(sides):
    records = [record(emit('Action_Smoothness', {'verdict': 'ACCEPTABLE', 'side': s}, {}), s) for s in sides]
    result = to_result('Action_Smoothness', records)
    names = {'left': '左臂', 'right': '右臂', 'main': '主臂'}
    assert result.note == '，'.join(names[s] + '通过' for s in sides)
    assert [i.verdict for i in result.items] == ['PASS'] * len(sides)
    assert result.note in render([result])


@pytest.mark.parametrize('status', ['review', 'failed'])
def test_two_arm_summary_cannot_hide_nonpassing_side(status):
    left = emit('Action_Smoothness', {'side': 'left', 'verdict': 'ACCEPTABLE'}, {})
    right = emit('Action_Smoothness', {'side': 'right', 'verdict': 'NEEDS_REVIEW',
                                      'reasons': ['实测抖动超过复核线']}, {})
    if status == 'failed':
        right = {'input_channel': '/right_pose', 'ir_unavailable': {'error': 'decode failed'},
                 'processing_status': 'processing_error'}
    result = to_result('Action_Smoothness', [record(left, 'left'), record(right, 'right')])
    assert '左臂通过' in result.note
    assert ('右臂复核' if status == 'review' else '/right_pose没跑完') in result.note
    assert decide([result]) != 'keep'


def numeric(**values):
    return {'input_channel': '/left_joint', 'input_field': 'position', 'verdict': 'NOT_APPLICABLE',
            'finite': True, 'assessment_complete': False, 'source_unit': 'rad',
            'calibration_status': 'confirmed', 'configuration_review': {'status': 'pending'},
            'physical_check_blockers': ['Numeric_Check: configuration confirmation is pending/stale'],
            'check_coverage': {'coverage_complete': False, 'checks': [
                {'id': 'finite', 'name': '有限值', 'status': 'evaluated'},
                {'id': 'joint_limits', 'name': '关节位置限位', 'status': 'unconfigured'},
                {'id': 'joint_step', 'name': '相邻关节位置差', 'status': 'pending_confirmation'}]}, **values}


@pytest.mark.parametrize('verdict,expected', [('DO_NOT_USE', 'EXCLUDE'), ('NEEDS_REVIEW', 'REVIEW')])
def test_measured_findings_survive_incomplete_other_subchecks(verdict, expected):
    raw = emit('Numeric_Check', numeric(verdict=verdict, reason='发现 NaN；'), {})
    result = to_result('Numeric_Check', [record(raw)])
    assert result.items[0].verdict == expected
    assert result.items[0].reason == '发现 NaN；'
    assert decide([result]) != 'keep'


def test_finiteness_only_complete_remains_a_pass():
    raw = numeric(verdict='ACCEPTABLE', assessment_complete=True,
                  check_coverage={'coverage_complete': True, 'checks': [
                      {'id': 'finite', 'status': 'evaluated'}, {'id': 'physical', 'status': 'not_applicable'}]})
    result = to_result('Numeric_Check', [record(emit('Numeric_Check', raw, {}))])
    assert result.level == 'PASS' and decide([result]) == 'keep'


@pytest.mark.parametrize('short', [True, False])
def test_dedup_missing_sibling_time_is_not_a_fault_on_first_channel(short):
    from edc.algorithms.quality.algorithms.Dedup_Check.run_check import assess
    raw = ({'verdict': 'NOT_APPLICABLE', 'score': None, 'assessment_complete': False,
            'reasons': ['At least two recorded samples are required']} if short else assess([1, 2, 3]))
    raw.update(input_channel='/config', assessment_complete=False,
               unavailable_inputs=[{'channel': '/camera', 'reason': 'missing header'}])
    if not short: raw['verdict'] = 'OBSERVATION'
    raw['reasons'] = raw.get('reasons', []) + ['部分原始通道缺有效时间，未完成全通道时间重复检查；已发现的问题仍保留。']
    original = deepcopy(raw)
    result = to_result('Dedup_Check', [record(emit('Dedup_Check', raw, {}))])
    assert result.items[0].level == (NOT_APPLICABLE if short else 'PASS')
    assert result.items[1].target == '/camera' and result.items[1].status == FAILED
    assert result.items[1].verdict is None and '缺少有效采集时间' in result.items[1].reason
    assert decide([result]) == 'unassessed'
    assert {k: v for k, v in raw.items() if not k.startswith('edc_')} == original


def test_dedup_single_record_only_is_not_applicable():
    raw = {'verdict': 'NOT_APPLICABLE', 'assessment_complete': False,
           'reasons': ['At least two recorded samples are required'], 'input_channel': '/config'}
    item = emit('Dedup_Check', raw, {})['edc_item']
    assert item['status'] == NOT_APPLICABLE and item['verdict'] is None
    assert item['reason'] == '少于2条记录，无法检查时间或序列重复'


def test_duplicate_separators_removed_without_changing_numeric_evidence():
    raw = emit('Dedup_Check', {'verdict': 'NEEDS_REVIEW', 'reasons': ['本条偏离+480 ms。；超过阈值;;需复核']}, {})
    item = raw['edc_item']
    assert item['reason'] == '本条偏离+480 ms；超过阈值；需复核'
    html = render([to_result('Dedup_Check', [record(raw)])])
    assert '。；' not in html and ';;' not in html and '+480 ms' in html


def test_machine_json_log_compact_but_original_saved_and_actual_errors_visible():
    results = [AlgorithmResult('Video_Integrity', [Item('/camera', FAILED, reason='DecoderError')])]
    log = json.dumps({'run_id': 'r', 'status': 'partial_success', 'status_counts': {'processing_error': 1}})
    html = render(results, log=log, returncode=1)
    assert '检测进程退出码：1' in html and 'status_counts' not in html
    assert 'DecoderError' in html and '/camera' in html and '怎么复核：' not in html
    assert json.loads(log)['status_counts'] == {'processing_error': 1}
    assert html.count('<h2>') == 4 and html.count('<details') == 10
    assert 'record_header_differences' not in html and '已检查明细' not in html
    for actual in ('Traceback: worker crash', 'edc run 异常退出（退出码 7）', '{bad json'):
        assert actual in render(results, log=actual)
    error_json = json.dumps({'run_id': 'r', 'status': 'failed', 'status_counts': {}, 'error': 'worker crash'})
    assert 'worker crash' in render(results, log=error_json)


def test_reset_per_component_reason_uses_recorded_upper_threshold_and_checked_end():
    raw = {'verdict': 'NEEDS_REVIEW', 'method': 'calibrated_home_reference_v2', 'score': 0,
           'checked_ends': ['end'], 'evidence': [
               {'channel': '/right_arm/end_pose', 'unit': 'm', 'tolerance2': [.00987, .02, .03],
                'start_error': [.1, 0, 0], 'end_error': [.01256, 0, 0]}]}
    before = deepcopy(raw)
    item = emit('Reset_Detection', raw, {})['edc_item']
    assert item['verdict'] == 'REVIEW' and item['metrics']['score'] == 0
    assert '右臂结尾 X 方向偏离初始位置 1.26 cm' in item['reason'] and '0.99 cm' in item['reason']
    assert '开头' not in item['reason'] and '疑似' not in item['reason']
    assert {k: v for k, v in raw.items() if not k.startswith('edc_')} == before
