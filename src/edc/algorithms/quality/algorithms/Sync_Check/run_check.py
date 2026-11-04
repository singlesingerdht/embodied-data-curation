"""模块: 采集同步 — 可直接运行的评估入口。

两项（2026-11-04 用户决定，原来的“跨通道对齐”和时间戳对齐重复、“时钟稳定性”拿不同频率的话题互比没有意义，都去掉）：
  ① 起止和交集：各路是不是同时开始、同时结束（按录包时间），哪一路晚开始／早结束、差几秒；
  ② 各路时钟：每一路自己的“录包时间 − 采集时间”（适配层 clock_diagnosis 算好传入），
     各路差出 1 秒以上是时钟没对准，同一路在数据中变化超过 100 毫秒是时钟漂移或跳变。
用法:
    python -m algorithms.Sync_Check.run_check <ts.json>
    ts.json 格式: {"topic_a": [0.0, 0.033, ...], "topic_b": [0.0, 0.033, ...]}（没有录包时间，只查第①项）
"""
from pathlib import Path
import sys
import os
import json
import argparse

_QA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if __name__ == "__main__" and str(Path(_QA_ROOT).parent) not in sys.path:
    sys.path.insert(0, str(Path(_QA_ROOT).parent))

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


def assess_from_ir(info, reader) -> dict:
    """从 IR DataInfo 评估（没有录包时间的诊断，只查起止和交集）。需要≥2条独立时间轴。"""
    try:
        ts_map = reader.require_timestamps(info, min_channels=2)
    except Exception as exc:
        from edc.data.readers.base import make_ir_report
        return make_ir_report("采集同步", "sync_check", exc)
    from edc.data.readers.base import filter_real_timestamps, make_synthetic_report, _all_same_axis
    ts_map, has_synthetic = filter_real_timestamps(info, ts_map)
    if ts_map is None:
        return make_synthetic_report("采集同步", "sync_check", info)
    if _all_same_axis(ts_map):
        from edc.data.readers.base import make_ir_report
        from edc.data.ir.metadata import IRNotProvided
        return make_ir_report("采集同步", "sync_check",
            IRNotProvided("全通道共享同一时间戳 (同轴采样)", info.format,
                          "跨通道同步检测仅对独立采集的时间戳有意义"))
    return assess(ts_map)


def main():
    ap = argparse.ArgumentParser(description="采集同步评估")
    ap.add_argument("input", help="时间戳 JSON 或数据目录路径 (IR: episode/dataset)")
    args = ap.parse_args()

    from edc.data.ir.metadata import detect_format, FORMAT_JSON, FORMAT_UNKNOWN
    fmt = detect_format(args.input)
    if fmt != FORMAT_JSON and fmt != FORMAT_UNKNOWN:
        from edc.algorithms.quality.config import open_info
        info, reader = open_info(args.input)
        report = assess_from_ir(info, reader)
    else:
        with open(args.input) as f:
            report = assess(json.load(f))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
