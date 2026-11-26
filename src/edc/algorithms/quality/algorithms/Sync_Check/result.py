"""采集同步：把本算法的报告翻成统一检查项 Item（edc.algorithms.base）；各算法共用的规则在 quality/shared/verdicts.py。"""


GUIDE = '“可救”：两组设备的时钟差了一个固定值，程序已自动把时间挪齐，其它检查按挪齐后的时间做；把哪些天、哪台机器时钟没对准反馈给采集方。“排除”：时钟差在数据中途还在变，没法挪齐，画面和动作对不上。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    """No algorithm-specific wording: the shared rules use the reasons the algorithm wrote."""
    return ''


def note(reports, items, labels, common):
    return common
