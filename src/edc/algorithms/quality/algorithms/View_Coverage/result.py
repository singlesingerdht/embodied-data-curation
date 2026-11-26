"""拍摄视角覆盖：把本算法的报告翻成统一检查项 Item（edc.algorithms.base）；各算法共用的规则在 quality/shared/verdicts.py。"""

GUIDE = '镜头污渍：每路相机均匀抽 30 帧，找每帧同一位置都有、中心浓边缘虚的圆形暗斑；红框证据图在本条的结果目录。'


def explain(report, labels):
    return '；'.join(report.get('reasons') or [])


def target(report, labels):
    name = report.get('input_channel') or ''
    return labels.get(name, name)


def note(reports, items, labels, common):
    return common
