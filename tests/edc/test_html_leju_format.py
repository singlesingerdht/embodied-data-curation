"""报告：算法出错的格子显示"没跑完"，整条显示"未评估完"；已有的结论和原因不丢。"""
from copy import deepcopy
import re

import pytest

from edc.algorithms.base import AlgorithmResult, Item, FAILED, decide
from edc.reporting.batch import page


def row(results, returncode=None):
    bag = {'name': 'synthetic', 'source': 'synthetic', 'seconds': 0,
           'decision': decide(results), 'algos': {r.algorithm: r.to_dict() for r in results}}
    if returncode is not None:
        bag['returncode'] = returncode
    return bag


def render(bag):
    return page([bag], {'robot': 'test', 'inputs': 'synthetic', 'finished': 'test',
                       'seconds': 0, 'jobs': 1, 'profile': 'robot.yaml'})


def badge_labels(html):
    return re.findall(r'<span class="chip [^"]*"[^>]*>(.*?)</span>', html)


def test_error_keeps_named_causes_without_an_extra_quality_badge_or_losing_results():
    results = [AlgorithmResult('Action_Smoothness', [Item('左臂', verdict='PASS'), Item('右臂', verdict='PASS')],
                               '左臂通过，右臂通过'),
               AlgorithmResult('Video_Integrity', [Item('/camera', FAILED, reason='DecoderError: bad frame')],
                               '没跑完1项')]
    bag = row(results, returncode=1)
    before = deepcopy(bag)
    html = render(bag)
    assert '没跑完' in badge_labels(html) and '未评估完' in badge_labels(html)
    assert '/camera' in html and 'DecoderError: bad frame' in html
    assert '左臂通过，右臂通过' in html
    assert html.count('<h2>') == 4 and html.count('<details') == 10
    assert '<table class="detail">' in html
    assert set(badge_labels(html)) <= {'排除', '可救', '复核', '通过', '不适用', '没跑完', '未评估完'}
    legend = html.split('<div class="box">', 1)[1].split('</div>', 1)[0]
    assert set(badge_labels(legend)) == {'排除', '可救', '复核', '通过', '没跑完'}
    assert bag == before and bag['decision'] == 'unassessed'
    assert results[1].items[0].status == FAILED and results[1].items[0].verdict is None
    assert decide(results) == 'unassessed'


@pytest.mark.parametrize('code', [None, 0, 1])
def test_missing_conditions_are_not_manual_review_or_a_pass(code):
    result = AlgorithmResult('Numeric_Check', [Item('/left.position', FAILED,
                             reason='物理检查未执行：阈值配置待确认')], '42 项：没跑完42')
    bag = row([result], code)
    html = render(bag)
    assert '没跑完' in badge_labels(html) and '阈值配置待确认' in html
    assert '怎么复核：' not in html and '建议保留' not in badge_labels(html)
    assert '未评估完' in badge_labels(html)
    assert bag['decision'] == 'unassessed' and result.items[0].verdict is None


def test_failure_with_exclusion_keeps_quality_conclusion_and_both_reasons():
    result = AlgorithmResult('Numeric_Check', [Item('/left', verdict='EXCLUDE', reason='NaN at row 2'),
                             Item('/right', FAILED, reason='missing unit')], '排除1项，没跑完1项')
    bag = row([result], returncode=1)
    html = render(bag)
    assert '含建议排除项' in html and 'NaN at row 2' in html and 'missing unit' in html
    assert '没跑完' in badge_labels(html)
    assert bag['decision'] == 'exclude' and result.level == 'EXCLUDE'
