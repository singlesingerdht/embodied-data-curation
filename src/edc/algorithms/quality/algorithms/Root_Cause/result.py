"""Root_Cause report -> common Item (edc.algorithms.base); shared rules in quality/shared/verdicts.py."""


GUIDE = '看所列时刻附近各路数据是否同时缺失：同时缺失说明整机卡顿，只缺一路说明是那一路设备的问题。'  # Shown above this algorithm's problems in the report.


def explain(report, labels):
    subs = {s.get('name'): s for s in report.get('sub_indicators') or []}
    parts = [f"{s['raw_value']}（该子项 {s['score']:.0f} 分）" for name in ('跨通道Stall', '周期性丢帧')
             if (s := subs.get(name)) and s.get('score') is not None and s['score'] < 100]
    unknown = [name for name in ('时钟漂移', '时钟跳变') if name in subs and subs[name].get('score') is None]
    short = report.get('short_input')
    if unknown and short:
        ok = [name for name in ('跨通道Stall', '周期性丢帧') if (subs.get(name) or {}).get('score') == 100]
        plain = {'跨通道Stall': '多路同时断档', '周期性丢帧': '周期性丢帧'}
        channel = labels.get(short['channel'], short['channel'] + ' ')
        return (f"数据太短，{'、'.join(unknown)}算不了：整条只有 {short['seconds']:.1f} 秒，{channel}只有 "
                f"{short['samples']} 个样本，算法要求每一路至少 {short['required']} 个"
                + (f"；另外两项（{'、'.join(plain[n] for n in ok)}）正常" if len(ok) == 2 else '')) + '；'.join([''] + parts)
    if unknown:
        parts.append('、'.join(unknown) + '没有评估：没有拿到时钟稳定性数据')
    return '；'.join(parts)


def note(reports, items, labels, common):
    short = next((r['short_input'] for r, i in zip(reports, items) if r.get('short_input') and i.verdict == 'PASS'), None)
    if short:
        return common + ('；' if common else '') + f"数据只有 {short['seconds']:.1f} 秒，漂移和跳变不需要查"
    return common
