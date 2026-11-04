"""Keep the leju-kuavo report concise without discarding recorded evidence."""
from copy import deepcopy

from edc.algorithms.base import AlgorithmResult, Item, FAILED, NOT_APPLICABLE, VERDICTS, decide
from edc.algorithms.quality.adapters.results import to_result
from edc.algorithms.quality.shared.verdicts import emit
from edc.reporting.batch import page


def evidence(deviation=480, threshold=None):
    msg = {'message_number': 151, 'source_row': 150, 'record_ns': 500_000_000,
           'header_ns': 0, 'difference_ms': 20 + deviation, 'deviation_ms': deviation}
    row = {'channel': '/arm', 'status': 'available', 'threshold_ms': threshold,
           'median_difference_ms': 20, 'min_difference_ms': min(20, 20 + deviation),
           'max_difference_ms': max(20, 20 + deviation), 'max_absolute_deviation_ms': abs(deviation),
           'max_deviation_message': msg}
    if threshold is not None:
        row.update(exceedance_count=int(abs(deviation) > threshold),
                   exceedance_periods=[{'start_source_row': 150, 'end_source_row': 150, 'count': 1}]
                   if abs(deviation) > threshold else [],
                   exceedance_messages=[msg] if abs(deviation) > threshold else [], details_truncated=False)
    return {'informational_only': True, 'channels': [row]}


def result(algo, data):
    report = emit(algo, {'verdict': 'ACCEPTABLE', 'assessment_complete': True,
                         'record_header_differences': deepcopy(data)}, {})
    return to_result(algo, [{'status': 'completed', 'result': {'reports': [report]}}])


def render(results):
    return page([{'name': '合成样例', 'source': 'synthetic', 'seconds': 0,
                  'decision': decide(results), 'algos': {r.algorithm: r.to_dict() for r in results}}],
                {'robot': 'test', 'inputs': 'synthetic', 'finished': 'test',
                 'seconds': 0, 'jobs': 1, 'profile': 'synthetic'})


def test_shared_evidence_stays_in_json_without_new_html_sections():
    data = evidence(threshold=100)
    results = [result(a, data) for a in ('Check_Timestamp', 'Sync_Check', 'Root_Cause')]
    for item in results:
        assert item.level == 'PASS' and item.note == ''
        assert item.items[0].reason == ''
        assert item.items[0].evidence[0]['data'] == data
    before = [deepcopy(r.to_dict()) for r in results]
    html = render(results)
    assert '记录与采集时间差' not in html and '差值范围' not in html
    assert '+480 ms' not in html and '已检查明细' not in html
    assert html.count('<details') == 10  # Nine algorithm groups and one episode.
    assert html.count('<h2>') == 4
    assert '<b>结论怎么读</b>' in html
    assert '未完成评估' not in html and '没跑完' not in html and '未判定' not in html
    assert [r.to_dict() for r in results] == before


def test_different_input_evidence_remains_separate_in_results():
    results = [result('Check_Timestamp', evidence()), result('Sync_Check', evidence(-480))]
    before = [deepcopy(r.to_dict()) for r in results]
    render(results)
    assert [r.to_dict() for r in results] == before
    assert results[0].items[0].evidence[0]['data']['channels'][0]['max_deviation_message']['deviation_ms'] == 480
    assert results[1].items[0].evidence[0]['data']['channels'][0]['max_deviation_message']['deviation_ms'] == -480


def test_normal_and_missing_pairs_do_not_create_empty_html_tables():
    data = evidence(0)
    data['channels'].append({'channel': '/missing', 'status': 'unavailable', 'reason': 'Missing header'})
    results = [result('Sync_Check', data)]
    html = render(results)
    assert '时间差保持稳定' not in html and '无法计算' not in html
    assert '/missing' not in html and '显式阈值统计' not in html
    assert results[0].items[0].evidence[0]['data'] == data


def test_execution_failure_has_no_quality_verdict_and_cannot_be_kept():
    failed = AlgorithmResult('Sync_Check', [Item('本条', status=FAILED, reason='算法异常')])
    na = AlgorithmResult('Check_Timestamp', [Item('本条', status=NOT_APPLICABLE, reason='缺少采集时间')])
    assert set(VERDICTS) == {'PASS', 'REVIEW', 'SALVAGE', 'EXCLUDE'}
    assert failed.items[0].verdict is None and decide([failed, na]) == 'unassessed'
    html = render([failed, na])
    # A real execution error remains visible and cannot silently become PASS.
    assert '算法异常' in html and '未评估完' in html
    assert '未完成评估' not in html and 'execution-status' not in html and '· 未判定' not in html


def test_successful_checks_use_existing_summary_and_keep_details_in_json():
    smoothness = AlgorithmResult('Action_Smoothness', [
        Item('左臂', verdict='PASS', reason='保持段抖动加速度 0.21 m/s²，低于复核线',
             metrics={'still_shake_m_s2': 0.21}),
        Item('右臂', verdict='PASS', reason='保持段抖动加速度 0.09 m/s²，低于复核线',
             metrics={'still_shake_m_s2': 0.09})], note='双臂通过')
    before = deepcopy(smoothness.to_dict())
    html = render([smoothness])
    assert '双臂通过' in html and '已检查明细' not in html
    assert '保持段抖动加速度' not in html
    assert smoothness.to_dict() == before
    assert smoothness.to_dict()['level'] == 'PASS' and decide([smoothness]) == 'keep'


def test_scope_bookkeeping_stays_in_json_and_existing_warning_remains_visible():
    scope = '本次未检查：raw（缺少部件归属）；raw2（字段由分量视图检查，原数组不重复检查）'
    warning = '3 项：通过3；通过但有问题帧：右相机抽检83帧，模糊7帧'
    results = [AlgorithmResult('Structure_Check', [Item('本条', verdict='PASS')], note=scope),
               AlgorithmResult('Video_Integrity', [Item('右相机', verdict='PASS', reason='抽检83帧，模糊7帧',
                                                       metrics={'score': 100})], note=warning + '；' + scope)]
    before = [deepcopy(r.to_dict()) for r in results]
    html = render(results)
    assert '检查范围说明' not in html and '检查范围见详情' not in html
    assert '缺少部件归属' not in html and '原数组不重复检查' not in html
    assert warning in html
    assert [r.to_dict() for r in results] == before


def test_actual_problem_location_and_missing_input_reason_remain_visible():
    reason = '/arm 第151条：时间差500 ms（通常20 ms，偏离+480 ms）'
    reviewed = AlgorithmResult('Check_Timestamp', [Item('/arm', verdict='REVIEW', reason=reason)])
    unavailable = AlgorithmResult('Sync_Check', [Item('本条', status=NOT_APPLICABLE, reason='缺少采集时间')])
    html = render([reviewed, unavailable])
    assert reason in html and '缺少采集时间' in html
    assert '建议复核' in html and '记录与采集时间差' not in html
