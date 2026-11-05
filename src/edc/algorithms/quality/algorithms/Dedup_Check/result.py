"""This algorithm's report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""
from edc.algorithms.base import Item, FAILED
from edc.algorithms.quality.shared.verdicts import texts


GUIDE = '时间戳或序号重复：看所列通道是否重复发送了同一条消息。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    """No algorithm-specific wording: the shared rules use the reasons the algorithm wrote."""
    return ''


def note(reports, items, labels, common):
    return common


def missing_inputs(report):
    """缺有效采集时间、整体没查的通道（适配层记在第一份报告上）。"""
    return (report.get('unavailable_inputs') or []) if any(
        '未完成全通道时间重复检查' in str(t) for t in report.get('reasons') or []) else []


def complete(report):
    """别的通道缺时间、本通道自己查完了：本通道按查完算，缺的通道另记一项（extra_items）。"""
    return (bool(missing_inputs(report)) and bool(report.get('dedup')) and report.get('score') is not None
            and not (report['dedup'].get('timestamp_dedup') or {}).get('observation_only_extra_count'))


def not_applicable(report):
    return '少于2条记录，无法检查时间或序列重复' if 'At least two recorded samples are required' in texts(report) else ''


def extra_items(report):
    return [Item(m['channel'], FAILED, reason='缺少有效采集时间，本通道时间重复未检查') for m in missing_inputs(report)]
