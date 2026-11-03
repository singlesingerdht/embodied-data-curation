"""This algorithm's report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""


GUIDE = '缺相机、缺通道或数据太短：看 edc robot check 是否能读到 yaml 里写的每一项。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    """No algorithm-specific wording: the shared rules use the reasons the algorithm wrote."""
    return ''


def note(reports, items, labels, common):
    return common
