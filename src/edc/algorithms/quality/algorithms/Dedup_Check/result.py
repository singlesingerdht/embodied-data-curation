"""This algorithm's report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""


GUIDE = '时间戳或序号重复：看所列通道是否重复发送了同一条消息。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    """No algorithm-specific wording: the shared rules use the reasons the algorithm wrote."""
    return ''


def note(reports, items, labels, common):
    return common
