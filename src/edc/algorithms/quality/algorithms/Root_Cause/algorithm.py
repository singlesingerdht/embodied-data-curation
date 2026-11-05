"""时间异常归因：框架怎么调用这项检查（先平移时钟、去掉派生通道，检查方法在 method.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm
from edc.algorithms.quality.shared.clock import clock_diagnosis, clock_shift, derived_copies, invalid_timelines, shift_note


class Algorithm(BuiltinAlgorithm):
    name = '时间异常归因'
    version = 'ir-adapter-19'
    inputs = ('multi_timestamps',)
    timed = True
    descriptions = ('至少两条可比较的独立记录时间轴及共同 clock；公共行时间不能用于跨通道归因。',)
    parameters = {'families': {'type': ['object', 'null'],
        'description': '原始话题的消息源分组（拆出的通道跟源话题走，不用写），用于关联异常；未填沿用原方法分组'}}

    def assess(self, ep, params, context):
        invalid = invalid_timelines(ep, scoped=True)
        if invalid:
            return invalid
        return self._root_cause_on_one_clock(ep, self.method(), params)

    def _root_cause_on_one_clock(self, ep, module, params):
        """Root_Cause after shifting any clock offset out, so gaps on different device clocks line up.

        Fixed or moving, the per-group median offset is removed; the clocks themselves are judged by Sync_Check.
        """
        diagnosis = clock_diagnosis(ep, [c.name for c in ep.channels.values()
            if c.time_source == 'acquisition' and c.clock and c.timestamps])
        shift = clock_shift(ep, diagnosis) if diagnosis['salvage'] else {}
        result = self._root_cause(ep, module, shift, params, derived_copies(ep))
        if not isinstance(result, dict):
            return result
        result['clock_diagnosis'] = diagnosis
        if shift:
            result['clock_shift_s'] = shift
            result['reasons'] = [f'时钟偏差已先平移掉（{shift_note(shift)}），再查断档'] + (result.get('reasons') or [])
        else:
            result['reasons'] = (result.get('reasons') or []) + diagnosis['notes']
        return result

    @staticmethod
    def _root_cause(ep, module, shift, params, drop=()):
        """Root_Cause on the source timelines only, with configured source groups.

        Shortage and generated-time outcomes are reported as not assessable. Real timelines are assessed
        without derived copies, and configured source groups are limited to the topics this episode has,
        since the method rejects names it was not given.
        """
        from edc.data.ir.metadata import IRNotProvided
        from edc.data.readers.base import make_ir_report
        from edc.algorithms.quality.shared.timing_scope import recorded_timelines
        ts_map, excluded, unavailable = recorded_timelines(ep, shift)
        if unavailable:
            return make_ir_report('根因分析', 'root_cause', unavailable)
        scope = {'included_topics': sorted(ts_map),
                 'time_sources': {n: getattr(ep.raw(n), 'time_source', 'unknown') for n in ts_map}}
        if not ts_map:
            report = make_ir_report('根因分析', 'root_cause', IRNotProvided('至少两路非 metadata 时间轴', ep.format))
            report.update(excluded_topics=excluded, timestamp_scope=scope)
            return report
        # 拆出或推算的通道和源话题是同一条时间轴，只算源话题，免得一次断档被数成几个来源
        kept = {n: t for n, t in ts_map.items() if n not in drop}
        ts_map = kept if len(kept) >= 2 else ts_map
        families = {n: g for n, g in (params.get('families') or {}).items() if n in ts_map} or None
        full = module.assess(ts_map, weights=params.get('weights'), families=families)
        full.update(excluded_topics=excluded, timestamp_scope=scope,
                    source_groups_input='configured' if families else 'method default (topic namespace)')
        return full
