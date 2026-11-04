"""Root_Cause report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""


GUIDE = '看所列时刻附近各路数据是否同时缺失：同时缺失说明整机卡顿，只缺一路说明是那一路设备的问题。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    subs = {s.get('name'): s for s in report.get('sub_indicators') or []}
    return '；'.join(s['raw_value'] for name in ('跨通道Stall', '单路断档', '周期性丢帧')
                    if (s := subs.get(name)) and s.get('score') is not None and s['score'] < 100)


def note(reports, items, labels, common):
    return common
