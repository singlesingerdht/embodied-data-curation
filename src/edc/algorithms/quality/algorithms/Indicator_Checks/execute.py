"""Execute repository-owned indicators against the selected EDC episode."""
from dataclasses import asdict, fields
from .bridge import EpisodeBridge
from .plugins import INVENTORY, selected


def json_value(value):
    if isinstance(value, dict):
        return {k: json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(v) for v in (sorted(value) if isinstance(value, set) else value)]
    return value


def configuration(cls, params, **fixed):
    values = dict(params.get('thresholds', {}))
    known = {f.name for f in fields(cls)}
    if set(values) - known:
        raise ValueError('Unknown threshold keys: ' + ', '.join(sorted(set(values) - known)))
    # An individual registered check cannot be silently disabled.
    if any(k.endswith('_enabled') or k in {'unpack', 'numeric_enabled', 'image_layer1_enabled',
            'image_layer2', 'image_fullframe', 'freeze_cross_validate', 'topic_overrides', 'disabled_codes'} for k in values):
        raise ValueError('Use algorithm selection to disable checks; threshold switches are not accepted')
    return cls(**{**values, **fixed})


def execute(plugin, context, params):
    from edc.algorithms.quality.indicator_core.filters.timing_filter import TimingFilter, TimingConfig
    from edc.algorithms.quality.indicator_core.report.format import format_timing_detail, format_sync_detail
    from edc.algorithms.quality.indicator_core.report.report import (
        _timing_indicators, _content_indicators, _structure_indicators, _cross_topic_indicators)
    code, group = plugin.row['code'], plugin.row['group']
    channels = selected(context.episode, plugin.row, params)
    bridge = EpisodeBridge(context.episode, channels, params)
    info = bridge.info()
    raw, entries, limitations, config = {}, [], [], {}
    if group == 'timing':
        cfg = configuration(TimingConfig, params)
        config = asdict(cfg)
        for c in channels:
            detail = format_timing_detail(TimingFilter(cfg).scan(
                [float(t)-bridge.base for t in c.timestamps], topic=c.name))
            raw[c.name] = detail
            entries.extend(_timing_indicators(detail, c.name, info.topics[c.name].modality))
    elif group == 'dedup':
        from edc.algorithms.quality.indicator_core.filters.dedup_filter import DedupFilter
        allowed = {'dedup_ratio_low', 'dedup_ratio_high'}
        if set(params.get('thresholds', {})) - allowed:
            raise ValueError('Unknown duplicate threshold')
        config = dict(params.get('thresholds', {}))
        for c in channels:
            result = DedupFilter(dedup_key='stamp', **config).run_from_series(c.timestamps, topic=c.name)
            raw[c.name] = asdict(result)
        limitations.append('Timestamp duplicate detection only; source sequence counters are not mapped')
    elif group == 'sync':
        from edc.algorithms.quality.indicator_core.filters.sync_filter import SyncConfig, SyncFilter
        axes = {c.name: [float(t)-bridge.base for t in c.timestamps] for c in channels}
        if code == 'M10':
            from edc.algorithms.quality.indicator_core.pipeline.runner import _detect_episode_boundaries
            timing = {n: format_timing_detail(TimingFilter().scan(t, topic=n)) for n, t in axes.items()}
            allowed = {'window_ms', 'min_topics', 'merge_sec'}
            if set(params.get('thresholds', {})) - allowed:
                raise ValueError('Unknown episode consistency threshold')
            config = dict(params.get('thresholds', {}))
            boundaries = _detect_episode_boundaries(timing, {}, **config)
            raw = {'timing': timing, 'episode_boundaries': boundaries}
            entries = _cross_topic_indicators({'episode_boundaries': boundaries}, {})
            limitations.append('Correlates timing events only; does not infer or change episode boundaries')
        else:
            cfg = configuration(SyncConfig, params)
            config = asdict(cfg)
            raw = format_sync_detail(SyncFilter(cfg).analyze(axes))
            entries = _cross_topic_indicators({}, raw)
            limitations.append('Explicit common acquisition clock; receipt-time cross-check unavailable')
    elif group == 'content':
        from edc.algorithms.quality.indicator_core.filters.content_checks.filter import ContentFilter
        from edc.algorithms.quality.indicator_core.filters.content_checks.model import ContentConfig
        cfg = configuration(ContentConfig, params, unpack=True, freeze_cross_validate=False)
        config = asdict(cfg)
        engine = ContentFilter(cfg)
        checks = []
        for c in channels:
            ti = info.topics[c.name]
            if plugin.row['modality'] == 'camera':
                check = engine._check_image_fullframe(c.name, ti, bridge.image_bytes(c.name))
            else:
                values = bridge.channel_values(c.name)
                samples = list(enumerate(values))
                if plugin.row['modality'] == 'imu':
                    check = (engine._check_imu_bias(c.name, ti, iter(values)) if code == 'I6'
                             else engine._check_imu(c.name, ti, samples, iter(values), []))
                elif plugin.row['modality'] == 'joint':
                    check = engine._check_joint_state(c.name, ti, samples, iter(values), [])
                elif plugin.row['modality'] == 'cmd':
                    check = engine._check_joint_cmd(c.name, ti, samples, iter(values), [])
                else:
                    check = engine._check_pose(c.name, ti, samples, iter(values), [])
            checks.append(check)
        engine._enrich_events(info, checks)
        raw = {'checks': [asdict(c) for c in checks]}
        for c in channels:
            entries.extend(_content_indicators(raw, c.name, asdict(cfg), info.topics[c.name].modality))
        limitations.append('Direct selected-channel checks; no inferred motion reference or compressed-header tests')
        if code == 'P5' and not cfg.home_position:
            limitations.append('Home pose reference absent: reset subcheck is not evaluated')
    elif group == 'structure':
        from edc.algorithms.quality.indicator_core.filters.structure_filter import StructureFilter, StructureConfig, TopicSnapshot
        cfg = configuration(StructureConfig, params, unpack=True)
        config = asdict(cfg)
        engine = StructureFilter(required_topics=params.get('required_channels', []), config=cfg)
        topics = {n: TopicSnapshot(name=n, msg_type=t.msg_type, count=t.count, modality=t.modality)
                  for n, t in info.topics.items()}
        if code == 'M3':
            checks = [engine._check_topic_completeness(topics)]
        elif code == 'C7':
            # Include metadata topics without opening a different dataset view.
            topics = {n: TopicSnapshot(name=n, msg_type='', count=c.count, modality=c.modality)
                      for n, c in context.episode.channels.items()}
            checks = [engine._check_camera_info_pairing(topics, context.episode.format)]
            limitations.append('camera_info naming/presence only; calibration validity is not established')
        elif code == 'M8':
            checks = [engine._check_file_integrity(str(context.episode.path))]
            limitations.append('File existence/readability only; container, payload and metadata integrity are not fully scanned')
        else:
            raise ValueError('No EDC adapter for ' + code)
        raw = {'checks': [asdict(c) for c in checks]}
        entries = _structure_indicators(raw)
    else:
        raise ValueError('Unsupported upstream group')
    indicators = [e.to_dict() for e in entries if e.indicator_code == code]
    mapping = bridge.source_mapping()
    config = json_value(config)
    provenance = {'implementation': INVENTORY.get('implementation', 'repository_owned'),
                  'algorithm_version': plugin.spec.version, 'indicator_code': code,
                  'params': params, 'effective_thresholds': config}
    context.write_result('indicator-raw.json', raw)
    context.write_result('source-mapping.json', mapping)
    context.write_result('provenance.json', provenance)
    return {'indicator_code': code, 'indicators': indicators, 'raw': raw,
            'score': None, 'passed': None, 'verdict': None,
            'assessment_complete': False,
            'limitations': limitations + ['Indicator output is evidence for the selected scope; robot acceptance remains a separate configuration decision.'],
            'provenance': provenance, 'source_mapping': mapping}
