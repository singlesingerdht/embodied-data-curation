"""时间异常归因：把本算法的报告翻成统一检查项 Item（edc.algorithms.base）；各算法共用的规则在 quality/shared/verdicts.py。"""


GUIDE = '看所列时刻附近各路数据是否同时缺失：同时缺失说明整机卡顿，只缺一路说明是那一路设备的问题。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    subs = {s.get('name'): s for s in report.get('sub_indicators') or []}
    return '；'.join(s['raw_value'] for name in ('跨通道Stall', '单路断档', '周期性丢帧')
                    if (s := subs.get(name)) and s.get('score') is not None and s['score'] < 100)


def note(reports, items, labels, common):
    return common
