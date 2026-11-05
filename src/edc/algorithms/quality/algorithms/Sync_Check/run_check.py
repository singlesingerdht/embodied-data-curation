"""模块: 采集同步，框架通过 assess 调用（algorithm.py）。

两项（2026-11-04 用户决定，原来的“跨通道对齐”和时间戳对齐重复、“时钟稳定性”拿不同频率的话题互比没有意义，都去掉）：
  ① 起止和交集：各路是不是同时开始、同时结束（按录包时间），哪一路晚开始／早结束、差几秒；
  ② 各路时钟：每一路自己的“录包时间 − 采集时间”（适配层 clock_diagnosis 算好传入），
     各路差出 1 秒以上是时钟没对准，同一路在数据中变化超过 100 毫秒是时钟漂移或跳变。
没有录包时间时只查第①项。
"""
from edc.algorithms.quality.algorithms.Sync_Check.sync_check import check_coverage

# 录包时间里还含传输延迟（乐聚腕部相机同一时钟下也会晃约 60 毫秒），所以超过 100 毫秒才算时钟在动；
# 和适配层 CLOCK_STABLE_MS 同一个数
CLOCK_MOVING_MS = 100.0
RANK = {'ACCEPTABLE': 0, 'NEEDS_REVIEW': 1, 'DO_NOT_USE': 2}


def assess(timestamps: dict, clock: dict = None, record: dict = None) -> dict:
    # 起止按录包时间比：同一个录包程序同一个钟打的，就是“各路什么时候开始、停止被录下”；
    # 采集时间里含各路的处理延迟（乐聚头部深度晚到约 0.4 秒），拿它比起止会把延迟当成没对齐。没有录包时间才用采集时间
    cov = check_coverage(record or timestamps)
    verdict, reasons, complete = 'ACCEPTABLE', [], True

    def worse(level):
        nonlocal verdict
        verdict = max(verdict, level, key=RANK.get)

    # ① 起止和交集
    for name, seconds in cov['late_start'].items():
        reasons.append(f'{name} 比最早的一路晚开始 {seconds:.1f} 秒')
    for name, seconds in cov['early_end'].items():
        reasons.append(f'{name} 比最晚的一路早结束 {seconds:.1f} 秒')
    reasons.append(f"按{'录包' if record else '采集'}时间，所有数据都在的共同时段 {cov['intersection_sec']:.1f} 秒，占全程 {cov['total_sec']:.1f} 秒的 "
                   f"{cov['coverage_ratio']:.0%}（低于 80% 排除，低于 95% 或有一路差出 5 秒以上复核）")
    if cov['severity'] == 'error':
        worse('DO_NOT_USE')
    elif cov['severity'] == 'warning' or cov['late_start'] or cov['early_end']:
        worse('NEEDS_REVIEW')

    # ② 各路时钟
    rows = (clock or {}).get('channels') or {}
    lags = {n: r['record_minus_header_ms'] for n, r in rows.items() if r.get('record_minus_header_ms') is not None}
    changes = {n: r['record_minus_header_change_ms'] for n, r in rows.items()
               if r.get('record_minus_header_change_ms') is not None}
    moving = {n: v for n, v in changes.items() if v > CLOCK_MOVING_MS}
    if (clock or {}).get('salvage'):
        worse('DO_NOT_USE')  # 原因由适配层的时钟诊断写；固定偏差在统一规则里改成“可救”
    elif not lags:
        complete = False
        reasons.append('缺少录包时间，没法查各路时钟')
    elif moving:
        worse('NEEDS_REVIEW')
        reasons += [f'{n} 的“录包时间 − 采集时间”在数据中变化了 {v:.0f} 毫秒，可能是这一路时钟漂移或跳变'
                    for n, v in moving.items()]
    else:
        reasons.append(f'各路时钟一致：录包时间 − 采集时间在 {min(lags.values()):.1f}–{max(lags.values()):.1f} 毫秒之间'
                       + (f'，数据中最多变化 {max(changes.values()):.1f} 毫秒' if changes else ''))
    return {'name': '采集同步', 'module_key': 'sync_check', 'score': None, 'verdict': verdict,
            'passed': verdict != 'DO_NOT_USE', 'assessment_complete': complete, 'reasons': reasons,
            'sync': {'coverage': cov, 'clock_moving_ms': moving, 'clock_moving_threshold_ms': CLOCK_MOVING_MS},
            'method_version': 'sync_span_and_clock_v1'}
