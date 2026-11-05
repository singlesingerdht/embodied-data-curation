"""采集同步：框架怎么调用这项检查（挑独立时间轴、时钟诊断在这里，检查方法在 method.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm
from edc.algorithms.quality.shared.clock import clock_diagnosis, invalid_timelines


class Algorithm(BuiltinAlgorithm):
    name = '采集同步'
    version = 'ir-adapter-18'
    inputs = ('sync',)
    dependencies = ('scipy',)
    timed = True
    weights = False
    descriptions = ('至少两条独立原始采集时间轴、有效秒制时间和共同 clock；公共行时间或合成时间不可替代。',)

    def preflight(self, episode, params):
        axes = [tuple(c.timestamps) for c in episode.channels.values() if c.time_source == "acquisition" and c.timestamps]
        if len(set(axes)) < 2:
            return "missing_input", "Independent acquisition timelines are required"
        return 'runnable', ''

    def assess(self, ep, params, context):
        invalid = invalid_timelines(ep, scoped=False)
        if invalid:
            return invalid
        module = self.method()
        ts = {c.name: c.timestamps for c in ep.channels.values()
              if c.time_source == "acquisition" and c.clock and c.timestamps}
        # 拆出的通道和源话题是同一条时间轴，只留一份（优先源话题），免得同一条时间比两遍
        unique = {}
        for name in sorted(ts, key=lambda n: bool(ep.channels[n].selection)):
            unique.setdefault(tuple(ts[name]), name)
        if len(unique) < 2:
            from edc.data.ir.metadata import IRNotProvided
            from edc.data.readers.base import make_ir_report
            return make_ir_report("同步", "sync_check", IRNotProvided("独立采集时间轴", ep.format))
        ts = {name: ts[name] for name in unique.values()}
        diagnosis = clock_diagnosis(ep, list(ts))
        topics = ep.describe_legacy().topics
        record = {n: [v * 1e-9 for v in getattr(topics.get(n), 'container_timestamps_ns', None) or []] for n in ts}
        record = record if all(record.values()) else None  # 有一路没有录包时间就都按采集时间比起止
        result = module.assess(ts, clock=diagnosis, record=record, **params)
        result['clock_diagnosis'] = diagnosis
        result['reasons'] = (result.get('reasons') or []) + result['clock_diagnosis']['notes']
        return result
