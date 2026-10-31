"""Thin adapters preserve historical scores and complete original reports."""
import functools
from importlib import import_module, util
import shutil
from edc.algorithms.registry import AlgorithmSpec
from edc.data.compat import legacy
from .contracts import cpu_parameters, INPUT_DESCRIPTIONS
from edc.algorithms.channel_names import resolve_channel_parameters, channel_parameter_bindings

# IDs are the official repository directory names. Historical short aliases are resolved by CLI.
SPECS = (
    ("Structure_Check", (), ()),
    ("Numeric_Check", ("numeric",), ()),
    ("Action_Smoothness", ("pose",), ("scipy",)),
    ("Check_Timestamp", ("timestamps",), ()),
    ("Sync_Check", ("sync",), ("scipy",)),
    ("Root_Cause", ("multi_timestamps",), ()),
    ("Dedup_Check", ("dedup_values",), ()),
    ("Reset_Detection", ("joint",), ()),
    ("Video_Integrity", ("video",), ("cv2", "xxhash")),
)


def topic_metadata(episode):
    """Read-only recorded topic metadata (modality, msg_type, count, recorded timestamps).

    Unit/orientation wrappers deep-copy the whole legacy info on every describe_legacy() call,
    timelines included; preflight gating only reads fields those wrappers never change, so it
    uses the innermost episode's info directly. Callers must not mutate the result."""
    source = episode
    while getattr(source, '_source', None) is not None or getattr(source, '_episode', None) is not None:
        source = getattr(source, '_source', None) or source._episode
    describe = getattr(source, 'describe_legacy', None)
    return describe() if callable(describe) else None


def dedup_inputs(episode, selected=None, info=None):
    """Recorded timelines or an explicitly mapped raw timestamp field; no inferred clock."""
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    if info is None:
        info = topic_metadata(episode)
    inputs = []
    seen_shared = set()
    for c in episode.channels.values():
        if selected is not None and c.name != selected:
            continue
        if c.time_source in {'synthetic', 'playback'}:
            continue
        topic = info.topics[c.name] if info else c
        if c.selection or timing_exclusion_reason(c.name, topic):
            continue
        evidence = getattr(c, 'time_evidence', {})
        raw = getattr(topic, 'recorded_timestamp_values', None)
        recorded = bool(c.timestamps) and c.time_source in {'acquisition', 'container'}
        if not recorded and not (raw is not None and not evidence.get('time_generated', False)):
            continue
        if evidence.get('timeline_scope') == 'shared_dataset_rows':
            key = (c.source, evidence.get('timestamp_field'))
            if key in seen_shared:
                continue
            seen_shared.add(key)
        inputs.append((c.name, None))
    raw = episode.profile.get("timestamp_column")
    c = episode.channels.get(raw)
    if not inputs and c and (selected is None or c.name == selected) and c.value_available and len(c.fields) == 1 and not getattr(c, 'time_evidence', {}).get('time_generated'):
        inputs.append((raw, c.fields[0]))
    return inputs


def capabilities(episode):
    channels = list(episode.channels.values())
    caps = set()
    if getattr(episode, 'path', None) is not None:
        caps.add('dataset_file')
    if any(c.value_available for c in channels):
        caps.add("numeric")
    if any(c.value_available and c.modality == "pose" and "position" in c.fields for c in channels):
        caps.add("pose")
    if any(c.value_available and c.modality == "joint_space" and "position" in c.fields for c in channels):
        caps.add("joint")
    if any(c.image_available for c in channels):
        caps.add("video")
    timed = [c for c in channels if c.timestamps and c.time_source in {"acquisition", "container"}]
    if timed:
        caps.add("timestamps")
    if dedup_inputs(episode):
        caps.add("dedup_values")
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    sampled = [c for c in timed if timing_exclusion_reason(c.name, c) is None
               and getattr(c, 'time_evidence', {}).get('timeline_scope') != 'shared_dataset_rows']
    if (len(sampled) >= 2 and all(c.clock for c in sampled)
            and len({c.clock for c in sampled}) == 1 and len({c.time_source for c in sampled}) == 1):
        caps.add("multi_timestamps")
    acquired = [c for c in sampled if c.time_source == "acquisition"]
    clocks = [c.clock for c in acquired]
    if len(clocks) >= 2 and all(clocks) and len(set(clocks)) == 1:
        caps.add("sync")
    return caps


def short_pose_holds(result, channel, min_duration_s):
    """Per-robot freeze_min_duration_s also for the method's own tip-position freeze: when every frozen
    segment is shorter than that on the channel's own time, it is a normal pause. The method's verdict,
    score and segments are kept; only the verdict used for screening changes."""
    import numpy as np
    freeze = (result.get('numeric') or {}).get('pose_freeze') or {}
    segments = freeze.get('segments') or []
    times = np.asarray(channel.timestamps, dtype=float) if channel.timestamps is not None else None
    if not segments or result.get('verdict') != 'NEEDS_REVIEW' or times is None or len(times) != channel.count:
        return result
    held = [float(times[min(s['start'] + s['length'] - 1, len(times) - 1)] - times[s['start']]) for s in segments]
    others = [x for x in result.get('sub_indicators') or [] if x.get('name') != '姿态冻结' and x.get('score') is not None]
    if max(held) >= min_duration_s or any(x['score'] < 100 for x in others):
        return result
    return {**result, 'method_verdict': result['verdict'], 'verdict': 'ACCEPTABLE', 'passed': True,
            'short_pose_holds': {'durations_s': held, 'min_duration_s': min_duration_s},
            'reasons': [f'末端位置停住 {len(held)} 处，最长 {max(held):.2f} 秒，短于本机型的 {min_duration_s:g} 秒，按正常停住处理']}


def shared_row_timeline(episode):
    """True when every timed channel uses the one stored row time (e.g. an export already aligned frame by
    frame): there is no independent channel time to align, only the row intervals themselves."""
    timed = [c for c in episode.channels.values() if c.timestamps]
    return bool(timed) and all((c.time_evidence or {}).get('timeline_scope') == 'shared_dataset_rows' for c in timed)


def shared_row_intervals(episode, max_gap_s, cadence):
    """Check_Timestamp on a shared row timeline (per-robot `shared_row_max_gap_s`): rows must go forward and
    no interval may exceed the limit; a longer interval is a jump in every channel and video at once."""
    import numpy as np
    if not np.isfinite(max_gap_s) or max_gap_s <= 0:
        raise ValueError('shared_row_max_gap_s must be a positive number of seconds')
    channel = next(c for c in episode.channels.values() if c.timestamps)
    times = np.asarray(channel.timestamps, dtype=float)
    dt = np.diff(times)
    bad = np.flatnonzero(~(dt > 0) | (dt > max_gap_s))
    events = [{'row': int(i + 1), 'time_s': float(times[i]), 'interval_s': float(dt[i]),
               'type': 'gap' if dt[i] > max_gap_s else 'not_forward'} for i in bad]
    median = float(np.median(dt)) if len(dt) else None
    reasons = ([f'所有数据和画面共用一条行时间（导出时已逐帧对齐），只检查行间隔：中位数 {median * 1000:.0f} 毫秒，'
                f'没有超过 {max_gap_s:g} 秒的空档'] if not events else
               [f'行时间有 {sum(e["type"] == "gap" for e in events)} 处空档（超过 {max_gap_s:g} 秒）、'
                f'{sum(e["type"] == "not_forward" for e in events)} 处不前进；空档处所有数据和画面一起跳过']) if len(dt) else ['只有一行']
    return {'score': None, 'passed': not events, 'verdict': 'NEEDS_REVIEW' if events else 'ACCEPTABLE',
            'assessment_complete': True, 'method_version': 'shared_row_intervals_v1',
            'shared_row_intervals': {'channel': channel.name, 'rows': int(len(times)), 'max_gap_s': max_gap_s,
                                     'median_interval_s': median, 'largest_interval_s': float(dt.max()) if len(dt) else None,
                                     'events': events[:50], 'event_count': len(events)},
            'review_candidates': [{'type': e['type'], 'time_s': e['time_s'], 'interval_s': e['interval_s']}
                                  for e in events[:50]],
            'cadence': cadence, 'cross_channel_alignment': {'status': 'not_applicable',
                'reason': 'All channels share one stored row timeline; there is no independent channel time to align'},
            'reasons': reasons}


def unavailable_dedup_inputs(episode, inputs=None, info=None):
    from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
    if info is None:
        info = topic_metadata(episode)
    available = {name for name, _ in (inputs if inputs is not None else dedup_inputs(episode, info=info))}
    rows = []
    for c in episode.channels.values():
        topic = info.topics.get(c.name, c) if info else c
        if c.selection or timing_exclusion_reason(c.name, topic) or c.name in available:
            continue
        evidence = c.time_evidence
        if evidence.get('timeline_scope') == 'shared_dataset_rows' and any(
                episode.channels[name].source == c.source and
                episode.channels[name].time_evidence.get('timestamp_field') == evidence.get('timestamp_field')
                for name in available):
            continue
        rows.append({'channel': c.name, 'time_source': c.time_source, 'status': 'missing_input',
                     'reason': evidence.get('unavailable_reason') or
                         '无有效原始时间；合成时间或播放时间不能用于原始时间重复检查'})
    return rows


def preflight(plugin, episode, params=None):
    """The executor and recommendation pool share the same isolated checks."""
    try:
        return _preflight(plugin, episode, params)
    except Exception as exc:
        from edc.data.ir.units import UnitInputError
        from edc.data.ir.metadata import IRNotProvided
        return ('missing_input' if isinstance(exc, (UnitInputError, IRNotProvided)) else
                'missing_dependency' if isinstance(exc, ImportError) else 'processing_error',
                f'{type(exc).__name__}: {exc}')


def selected_pose(episode, params=None):
    params = params or {}
    if params.get('channel'):
        from .selection import ChannelSelection
        episode = ChannelSelection(episode, [params['channel']])
    if getattr(episode, '_explicit_pose_channel', None):
        return episode.channels[episode._explicit_pose_channel]
    info, reader = episode.algorithm_view()
    name, _ = reader.require_pose(info, side=(params or {}).get('side', 'right'))
    return episode.channels[name]


def selected_inputs(episode, params, ident):
    from .selection import select_episode
    reference = None
    if ident == 'Action_Smoothness':
        from .action import validate_options
        validate_options(params)
        requested = (params.get('timeline') or {}).get('reference_channel')
        if requested:
            reference = episode.channels[requested]
    if ident == 'Action_Smoothness' and params.get('channel'):
        if 'channels' in params:
            raise ValueError('Use channel or channels, not both')
        params = {**params, 'channels': [params['channel']]}
    view = select_episode(episode, params)
    if ident == 'Action_Smoothness' and 'channels' in params and len(view.channels) != 1:
        raise ValueError('Action_Smoothness requires exactly one explicit pose channel')
    if ident == 'Action_Smoothness' and 'channels' in params:
        c = next(iter(view.channels.values()))
        if c.modality != 'pose' or not c.value_available or 'position' not in c.fields:
            raise ValueError('Selected action channel requires readable Cartesian position')
        if c.field_spec('position')['source_kind'] != 'feedback':
            raise ValueError('Selected action channel requires explicitly declared feedback, not command/unknown')
        if 'side' in params and c.side != params['side']:
            raise ValueError('Explicit action topic conflicts with requested side; no fallback is allowed')
        view._explicit_pose_channel = c.name
    if ident == 'Action_Smoothness':
        # The pose scope stays exact; the reference contributes timestamps only.
        view._smoothness_reference_channel = reference
    if ident == 'Dedup_Check' and params.get('channel') and 'channels' in params:
        raise ValueError('Use channel or channels, not both')
    if ident == 'Sync_Check' and params.get('primary') and params['primary'] not in view.channels:
        raise ValueError('Sync primary must be included in selected channels')
    if ident == 'Reset_Detection' and 'channels' in params:
        calibration = view.profile.get('calibration', {}).get(ident)
        if calibration and isinstance(calibration.get('references'), dict):
            from edc.algorithms.channel_names import resolve_channel
            references = {resolve_channel(view, n) for n in calibration['references']}
            missing = [n for n in view.channels if n not in references]
            if missing:
                raise ValueError(f'Selected reset channels have no Home reference: {missing}')
        elif calibration and isinstance(calibration.get('reference'), dict):
            missing = [n for n in view.channels if n not in calibration['reference']]
            if missing:
                raise ValueError(f'Selected reset channels have no Home reference: {missing}')
            calibration['reference'] = {n: calibration['reference'][n] for n in view.channels}
    if ident in {'Check_Timestamp', 'Sync_Check', 'Root_Cause'} and params.get('target_clock'):
        from edc.data.clock_view import ClockMappedEpisode
        view = ClockMappedEpisode(view, params['target_clock'])
    return view


def _preflight(plugin, episode, params=None):
    spec = plugin.spec
    from edc.algorithms.contracts import resolve_parameters, evaluate_requirements
    try:
        params = resolve_parameters(spec, params)
        from edc.algorithms.contracts import bind_parameters
        params, _ = bind_parameters(spec, episode, params)
    except ValueError as exc:
        return 'missing_input', f'Invalid algorithm parameters: {exc}'
    if isinstance(plugin, LegacyAlgorithm):
        try:
            episode = selected_inputs(episode, params, spec.id)
        except ValueError as exc:
            return 'missing_input', str(exc)
    requirements = evaluate_requirements(spec, episode, params)
    custom = getattr(plugin, 'preflight', None)
    unmet = [r for r in requirements if r['status'] == 'missing_input']
    if unmet:
        return 'missing_input', '; '.join(reason for row in unmet for reason in row['reasons'])
    if any(r['status'] == 'deferred' for r in requirements) and not callable(custom):
        return 'missing_input', 'Select channels explicitly or provide algorithm preflight for the default scope'
    if spec.id == 'Dedup_Check' and (params or {}).get('channel'):
        selected = params['channel']
        if selected not in {name for name, _ in dedup_inputs(episode, selected)}:
            return 'missing_input', f'No usable recorded timestamp input for channel: {selected}'
    if spec.id == 'Video_Integrity':
        if not any(c.modality == 'rgb' and c.image_available for c in episode.channels.values()):
            return 'missing_input', 'Select at least one readable RGB channel; depth is not RGB'
        from .video import input_mode
        try:
            video_modes = {c.name: input_mode(episode, c, params) for c in episode.channels.values()
                           if c.modality == 'rgb' and c.image_available}
        except ValueError as exc:
            return 'missing_input', str(exc)
    missing = set(spec.required_inputs) - capabilities(episode)
    reset_profile = episode.profile.get('calibration', {}).get('Reset_Detection', {})
    if spec.id == 'Reset_Detection' and isinstance(reset_profile, dict) and (
            isinstance(reset_profile.get('references'), dict)
            or reset_profile.get('unit') == 'm' and isinstance(reset_profile.get('reference'), dict)):
        # The v2 profile explicitly names Cartesian feedback channels.  Do not
        # require an unrelated joint channel merely because v1 used one.
        missing.discard('joint')
        if not any(c.value_available and c.modality == 'pose' and 'position' in c.fields
                   for c in episode.channels.values()):
            missing.add('pose')
    if missing:
        if spec.id in {'Sync_Check', 'Root_Cause', 'Check_Timestamp'}:
            return 'missing_input', ('时间条件不足：需要有效且单位明确的时间轴；跨通道检查还需要独立的原始时间轴和共同 clock。'
                '只有公共行时间、未知单位或合成时间时，不能评估原始采集同步；现有时间仍保留。')
        if spec.id == 'Dedup_Check':
            return 'missing_input', '没有可用的原始时间值；合成时间不用于时间重复检查。'
        return "missing_input", "Missing capabilities: " + ", ".join(sorted(missing))
    from edc.data.ir.units import require_si_field
    for requirement in spec.input_requirements:
        from edc.algorithms.channel_names import resolve_channel
        name, field = resolve_channel(episode, requirement['channel']), requirement['field']
        channel = episode.channels.get(name)
        if channel is None or not channel.value_available or field not in channel.fields:
            return 'missing_input', f'Required numeric input unavailable: {name}.{field}'
        require_si_field(episode, name, field, requirement['unit'])
    # Optional algorithm-owned validation keeps model/configuration rules out of
    # the shared dispatcher. Existing plugins without a hook retain v1 behavior.
    custom = getattr(plugin, 'preflight', None)
    if callable(custom):
        try:
            status, reason = custom(episode, params or {})
            if status not in {'runnable', 'missing_input', 'needs_calibration', 'missing_dependency', 'processing_error'}:
                raise ValueError(f'Invalid preflight status: {status}')
            if status != 'runnable':
                return status, reason
        except Exception as exc:
            from edc.data.ir.units import UnitInputError
            return ('missing_input' if isinstance(exc, UnitInputError) else
                    'missing_dependency' if isinstance(exc, ImportError) else 'processing_error',
                    f'{type(exc).__name__}: {exc}')
    # Single-stream cadence is observable without a camera reference or a common clock.
    # The alignment score below still requires both.
    if spec.id == "Action_Smoothness":
        pose = selected_pose(episode, params)
        require_si_field(episode, pose.name, 'position', 'm')
        if (pose.timestamps is None or len(pose.timestamps) != pose.count
                or pose.time_source not in {'acquisition', 'container'}):
            return "missing_input", "动作平滑度需要单位明确且来源已确认的有效时间轴；未知、播放或合成时间不可替代。"
    if spec.id == "Sync_Check":
        axes = [tuple(c.timestamps) for c in episode.channels.values() if c.time_source == "acquisition" and c.timestamps]
        if len(set(axes)) < 2:
            return "missing_input", "Independent acquisition timelines are required"
    if spec.id == 'Video_Integrity' and 'video' in video_modes.values() and episode.format in {'hdf5', 'parquet', 'episode', 'lerobot'}:
        fps = episode.profile.get('video_fps') or episode.selector.get('info', {}).get('fps')
        from pathlib import Path
        unmaterializable = [c.name for c in episode.channels.values() if video_modes.get(c.name) == 'video'
                            and Path(c.source).suffix.lower() not in {'.mp4', '.avi', '.mov', '.mkv'}
                            and (c.timestamps is None or len(c.timestamps) < 2) and not fps]
        if unmaterializable:
            return 'missing_input', 'Embedded video requires explicit FPS or a source timeline: ' + ', '.join(unmaterializable)
    if spec.calibration and not episode.profile.get("calibration", {}).get(spec.id):
        return "needs_calibration", "Provide algorithm-specific calibration"
    if spec.id == "Reset_Detection":
        try:
            import numpy as np
            reset = reset_calibration(episode)
            if reset.get('schema_version') == 'edc_reset_calibration_v2':
                for channel, item in reset['references'].items():
                    values = episode.read_signal(channel, item['field'])
                    expected = tuple(np.asarray(item['home']).shape)
                    if getattr(values, 'ndim', 0) != 2 or tuple(values.shape[1:]) != expected:
                        return 'needs_calibration', (
                            f'{channel}.{item["field"]}: calibration shape {expected} does not match '
                            f'input shape {tuple(getattr(values, "shape", ()))[1:]}; confirm the channel mapping')
        except (ValueError, KeyError, TypeError) as exc:
            return "needs_calibration", str(exc)
    dependencies = [dep for dep in spec.dependencies if util.find_spec(dep) is None]
    if spec.id == "Video_Integrity" and 'video' in video_modes.values():
        dependencies += [exe for exe in ("ffmpeg", "ffprobe") if not shutil.which(exe)]
    if dependencies:
        return "missing_dependency", ", ".join(dependencies)
    if spec.device != "cpu" and not callable(custom):
        return "missing_dependency", f"Device {spec.device} requires an external executor"
    return "runnable", ""


def reset_calibration(episode):
    """Normalize explicitly declared home references at the adapter boundary."""
    import copy
    import numpy as np
    from edc.data.ir.units import si_unit, UnitInputError
    from edc.data.transforms.core import convert_units
    source = episode.profile["calibration"]["Reset_Detection"]
    if isinstance(source, dict) and isinstance(source.get('references'), dict):
        from edc.algorithms.channel_names import resolve_channel
        normalized = {}
        for requested, item in source['references'].items():
            if not isinstance(item, dict):
                raise ValueError(f'Reset calibration reference must be a mapping: {requested}')
            field = item.get('field', 'position')
            if field != 'position':
                raise ValueError(f'Reset calibration only supports position fields: {requested}.{field}')
            channel = resolve_channel(episode, requested)
            unit = item.get('unit')
            target = si_unit(unit)
            from edc.data.ir.units import require_si_field
            require_si_field(episode, channel, field, target)
            try:
                home = np.asarray(item['home'], dtype=float)
                tolerance1 = np.asarray(item['tolerance1'], dtype=float)
                tolerance2 = np.asarray(item['tolerance2'], dtype=float)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'Reset calibration requires home/tolerance arrays: {requested}') from exc
            if home.ndim == 0 or tolerance1.ndim not in {0, home.ndim} or tolerance2.ndim not in {0, home.ndim}:
                raise ValueError(f'Reset calibration shape is invalid: {requested}')
            if tolerance1.ndim and tolerance1.shape != home.shape:
                raise ValueError(f'Reset tolerance1 shape does not match home: {requested}')
            if tolerance2.ndim and tolerance2.shape != home.shape:
                raise ValueError(f'Reset tolerance2 shape does not match home: {requested}')
            if not np.isfinite(home).all() or not np.isfinite(tolerance1).all() or not np.isfinite(tolerance2).all():
                raise ValueError(f'Reset calibration contains nonfinite values: {requested}')
            t1 = tolerance1 if tolerance1.ndim else np.full(home.shape, float(tolerance1))
            t2 = tolerance2 if tolerance2.ndim else np.full(home.shape, float(tolerance2))
            if np.any(t1 <= 0) or np.any(t2 <= t1):
                raise ValueError(f'Reset calibration requires 0 < tolerance1 < tolerance2: {requested}')
            if unit != target:
                home = convert_units(home, unit, target).output
                t1 = convert_units(t1, unit, target).output
                t2 = convert_units(t2, unit, target).output
            normalized[channel] = {
                'requested_channel': requested,
                'field': field,
                'unit': target,
                'home': home.tolist(),
                'tolerance1': t1.tolist(),
                'tolerance2': t2.tolist(),
            }
        return {'schema_version': 'edc_reset_calibration_v2',
                'references': normalized,
                'deferred': copy.deepcopy(source.get('deferred', [])),
                'source': copy.deepcopy(source.get('source', {}))}
    target = si_unit(source.get("unit"))
    if getattr(episode, "unit_policy", "auto") == "manual" and source["unit"] != target:
        raise UnitInputError("Automatic conversion disabled; provide SI reset reference and tolerance")
    tolerance = float(source["tolerance"])
    if not np.isfinite(tolerance) or tolerance <= 0 or not source["reference"]:
        raise ValueError("Reset calibration requires positive tolerance and reference channels")
    result = copy.deepcopy(source)
    result["unit"] = target
    result["reference"] = {}
    for name, home in source["reference"].items():
        from edc.data.ir.units import require_si_field
        require_si_field(episode, name, 'position', target)
        result["reference"][name] = (convert_units(home, source["unit"], target).output.tolist()
            if source["unit"] != target else copy.deepcopy(home))
    result["tolerance"] = (float(convert_units(tolerance, source["unit"], target).output)
        if source["unit"] != target else tolerance)
    return result


CALENDAR_MIN_S = 946684800.0   # 2000-01-01 UTC; earlier headers are device counters, not dates
# Record time mixes clock offset with transport/processing latency (Leju head depth arrives
# 0.2-0.4 s late on a shared clock), so only offsets beyond 1 s are named as separate clocks.
# Smaller mismatches still show in Sync_Check's own alignment numbers.
CLOCK_GROUP_GAP_MS = 1000.0
# "Salvageable" = every channel's record-minus-header lag is the same at the start and the end of
# the episode (medians of CLOCK_WINDOWS equal slices within CLOCK_STABLE_MS), so one shift per
# channel maps it onto the record clock. Record time also carries transport latency: Leju wrist
# cameras on a correct clock still move up to ~60 ms within one episode, while clock faults are
# hundreds of ms or more. Alignment is re-checked after the shift, so this only says "shift is worth trying".
CLOCK_WINDOWS = 10
CLOCK_STABLE_MS = 100.0


def clock_diagnosis(episode, names):
    """Plain-language causes behind timing scores, for channels declared on one clock.

    Uses each message's bag record time only as a shared ruler: median(record - header) per
    channel. Same-clock channels differ only by latency; groups further apart than
    CLOCK_GROUP_GAP_MS point to unsynchronised device clocks. Never changes timestamps or scores.
    """
    import datetime
    import time
    import numpy as np
    info = episode.describe_legacy()
    latest = time.time() + 365 * 86400
    rows, offsets, notes = {}, {}, []
    for name in names:
        c = episode.channels[name]
        if c.selection:
            continue  # Derived channels share their source's timeline.
        t = np.asarray(c.timestamps, dtype=float)
        record = getattr(info.topics.get(name), 'container_timestamps_ns', None)
        row = {'clock': c.clock, 'calendar_time': bool(CALENDAR_MIN_S <= t.min() and t.max() <= latest)}
        if record is not None and len(record) == len(t):
            lag = (np.asarray(record, dtype=float) * 1e-9 - t) * 1000
            row['record_minus_header_ms'] = round(float(np.median(lag)), 1)
            if len(lag) >= CLOCK_WINDOWS * 3:
                # A fixed offset keeps the same median lag from start to end; drift or a jump moves it.
                windows = [np.median(part) for part in np.array_split(lag, CLOCK_WINDOWS)]
                row['record_minus_header_change_ms'] = round(float(max(windows) - min(windows)), 1)
        rows[name] = row
        if not row['calendar_time']:
            try:
                when = (datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
                        + datetime.timedelta(seconds=float(t[0]))).strftime('%Y-%m-%d')
            except (OverflowError, ValueError):
                when = f'{t[0]:.0f} 秒（超出日期范围）'
            notes.append(f'{name}: header 时间换算为 {when}，不是正常日期时间，可能是设备内部计数（如开机时长），'
                         '不能和其他话题直接比较时间')
        elif 'record_minus_header_ms' in row:
            offsets[name] = row['record_minus_header_ms']
    groups = []
    for name, value in sorted(offsets.items(), key=lambda item: item[1]):
        if groups and value - groups[-1]['offsets'][-1] <= CLOCK_GROUP_GAP_MS:
            groups[-1]['channels'].append(name)
            groups[-1]['offsets'].append(value)
        else:
            groups.append({'channels': [name], 'offsets': [value]})
    groups = [{'channels': g['channels'], 'record_minus_header_ms': round(float(np.median(g['offsets'])), 1),
               'record_minus_header_range_ms': [min(g['offsets']), max(g['offsets'])]} for g in groups]
    if len(groups) > 1:
        def amount(ms):
            return f'{ms / 1000:.1f} 秒' if abs(ms) >= 1000 else f'{ms:.0f} 毫秒'

        def span(g):
            lo, hi = g['record_minus_header_range_ms']
            return f'{amount(lo)}–{amount(hi)}' if hi - lo >= 10 else amount(g['record_minus_header_ms'])
        parts = [f"{'、'.join(g['channels'])}（header 比录包时间早约 {span(g)}）" for g in groups]
        spread = groups[-1]['record_minus_header_ms'] - groups[0]['record_minus_header_ms']
        notes.append(f'声明为同一时钟的话题实际分成 {len(groups)} 组：' + '；'.join(parts) +
                     f'。组间最多相差约 {amount(spread)}，疑似不同设备的时钟没有对准')
    if names and not offsets and not notes:
        notes.append('缺少录包时间，无法估计各话题的时钟偏差')
    salvage = None
    if len(groups) > 1 or any(not row['calendar_time'] for row in rows.values()):
        change = {n: row.get('record_minus_header_change_ms') for n, row in rows.items()}
        moving = {n: v for n, v in change.items() if v is None or v > CLOCK_STABLE_MS}
        salvage = {'status': 'unstable' if moving else 'fixed_offset', 'stable_ms': CLOCK_STABLE_MS,
                   'max_change_ms': max((v for v in change.values() if v is not None), default=None),
                   'moving_channels': moving}
        if moving:
            notes.append('不是固定偏差，不能简单平移：' + '、'.join(
                f'{n}（{"无法测量" if v is None else f"数据内变化 {v:.0f} 毫秒"}）' for n, v in moving.items()))
        else:
            notes.append(f'各话题与录包时间的偏差从头到尾基本不变（最多变化 {salvage["max_change_ms"]:.0f} 毫秒），'
                         '是固定偏差：按各自偏差平移时间后可重新检查（可救）')
    return {'method': 'median(record_time - header), record time as shared ruler',
            'group_gap_ms': CLOCK_GROUP_GAP_MS, 'channels': rows, 'groups': groups, 'salvage': salvage,
            'notes': notes}


def clock_shift(episode, diagnosis):
    """Seconds to add to each channel so every clock group lines up with the largest group.

    A group's clock offset is its smallest record - header: the fastest channel has almost no
    transport latency (a 400 ms depth camera must not drag its group's estimate). Channels inside
    one group keep their own latency differences. Derived channels follow their source.
    """
    groups = diagnosis['groups']
    lag = {n: g['record_minus_header_range_ms'][0] for g in groups for n in g['channels']}
    lag.update({n: row['record_minus_header_ms'] for n, row in diagnosis['channels'].items()
                if not row['calendar_time'] and 'record_minus_header_ms' in row})
    if len(set(lag.values())) < 2:
        return {}
    reference = max(groups, key=lambda g: len(g['channels']))['record_minus_header_range_ms'][0] if groups else 0.0
    def key(c):
        t = c.timestamps
        return (float(t[0]), float(t[-1]), len(t)) if t is not None and len(t) else None
    # Channels computed from another (e.g. FK poses) have no record time of their own but
    # the very same timeline as their source.
    by_timeline = {key(episode.channels[n]): lag[n] for n in lag if n in episode.channels}
    shift = {}
    for name, c in episode.channels.items():
        source = (c.selection or {}).get('channel', name)
        value = lag.get(source, by_timeline.get(key(c)))
        if value is not None and value != reference:
            shift[name] = (value - reference) / 1000
    return shift


def shift_note(shift):
    """'已把 a、b 的时间平移 +31.9 秒' grouped by amount."""
    amounts, counters = {}, []
    for name, seconds in shift.items():
        if abs(seconds) > 365 * 86400:  # header was a device counter, not a date
            counters.append(name)
        else:
            amounts.setdefault(round(seconds, 1), []).append(name)
    parts = [f"已把 {'、'.join(names)} 的时间平移 {s:+.1f} 秒" for s, names in amounts.items()]
    if counters:
        parts.append(f"已把 {'、'.join(counters)} 的设备计数时间换算到统一时钟")
    return '；'.join(parts)


class ShiftedReader:
    """The same reader, with timestamps moved by clock_shift (and derived copies left out) before an algorithm sees them."""
    def __init__(self, reader, shift, drop=()):
        self._reader, self._shift, self._drop = reader, shift, set(drop)

    def __getattr__(self, name):
        return getattr(self._reader, name)

    def require_timestamps(self, info, min_channels=2):
        ts = self._reader.require_timestamps(info, min_channels=min_channels)
        return {n: [t + self._shift[n] for t in v] if n in self._shift else v
                for n, v in ts.items() if n not in self._drop}


def derived_copies(episode, info):
    """Channels that only repeat another channel's timeline: split from one message (selection) or
    computed from it (e.g. FK poses, no record time of their own). Scoring each copy again would
    count one stream's timing several times."""
    def key(c):
        t = c.timestamps
        return (float(t[0]), float(t[-1]), len(t)) if t is not None and len(t) else None
    own = {n for n, c in episode.channels.items() if not c.selection
           and getattr(info.topics.get(n), 'container_timestamps_ns', None) is not None}
    timelines = {key(episode.channels[n]) for n in own}
    return sorted(n for n, c in episode.channels.items() if n not in own and (
        (c.selection or {}).get('channel') in own or key(c) in timelines))


# Check_Timestamp's own command-line options (run_check.py argparse), settable per robot.
TIMESTAMP_SCORING = {'absolute_error_floor_ms': 'DEFAULT_ABSOLUTE_ERROR_FLOOR_MS',
                     'dt_cv_no_penalty': 'DEFAULT_DT_CV_NO_PENALTY', 'dt_cv_zero': 'DEFAULT_DT_CV_ZERO',
                     'long_gap_ratio_warning': 'DEFAULT_LONG_GAP_RATIO_WARNING',
                     'long_gap_ratio_zero': 'DEFAULT_LONG_GAP_RATIO_ZERO', 'iou_threshold': 'DEFAULT_IOU_THRESHOLD'}


class timestamp_scoring:
    """Run Check_Timestamp with per-robot values for its own CLI options; restored afterwards.

    assess_from_ir builds its options from these module defaults at call time; the method file is unchanged.
    """
    def __init__(self, module, values):
        unknown = set(values or {}) - set(TIMESTAMP_SCORING)
        if unknown:
            raise ValueError(f'Unknown Check_Timestamp scoring option: {sorted(unknown)}')
        self.module, self.values, self.saved = module, values or {}, {}

    def __enter__(self):
        for key, value in self.values.items():
            name = TIMESTAMP_SCORING[key]
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, float(value))

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)


def needs_person(result, reason):
    """No automatic verdict possible: hand the item to a person instead of leaving OBSERVATION."""
    result.update(original_verdict=result.get('verdict'), verdict='NEEDS_REVIEW', passed=None,
                  assessment_complete=True, automatic_check_complete=False)
    result.setdefault('review_candidates', []).append({'check': 'manual', 'needs_review': True, 'reason': reason})
    result['reasons'] = [reason] + list(result.get('reasons') or [])
    return result


# Used when a profile has no min_duration_s. None here (library calls, tests); `edc run` sets
# CLI_MIN_DURATION_S so every robot and input format checked from the command line gets 3 s.
DEFAULT_MIN_DURATION_S = None


def too_short(episode):
    """{'seconds', 'min_duration_s'} when the episode is shorter than min_duration_s, else None.

    The robot profile may set its own min_duration_s, or null to switch the rule off.
    Duration is the longest single channel's own span, so device-counter clocks are never mixed.
    """
    profile = getattr(episode, 'profile', None) or {}
    minimum = profile.get('min_duration_s', DEFAULT_MIN_DURATION_S)
    if minimum is None:
        return None
    if type(minimum) not in (int, float) or not minimum > 0:
        raise ValueError('min_duration_s must be a positive number of seconds')
    spans = [c.timestamps[-1] - c.timestamps[0] for c in episode.channels.values()
             if c.timestamps is not None and len(c.timestamps) > 1]
    if not spans or max(spans) >= minimum:
        return None
    return {'seconds': round(float(max(spans)), 2), 'min_duration_s': float(minimum)}


@functools.lru_cache(maxsize=32)
def _manifest_segments(path):
    import json
    with open(path, encoding='utf-8') as handle:
        segments = json.load(handle).get('segments') or []
    return {s['output_episode']: s for s in segments if isinstance(s, dict) and s.get('output_episode')}


def split_segment(episode):
    """Where this episode was cut out of a longer recording, as the dataset's own cutting record says, else None.

    Two records are read, nothing is guessed from folder names: the episode's own anno/source_segment.json,
    or a split_manifest.json up to three folders above that lists this episode as an output_episode.
    Frame numbers are the source recording's rows, end exclusive."""
    import json
    from pathlib import Path
    source = episode
    while not getattr(source, 'path', None) and hasattr(source, '_episode'):
        source = source._episode
    path = getattr(source, 'path', None)
    if not path:
        return None
    folder = Path(path).resolve()
    folder = folder if folder.is_dir() else folder.parent
    own = folder / 'anno' / 'source_segment.json'
    if own.is_file():
        data = json.loads(own.read_text(encoding='utf-8'))
        return {'record': str(own), 'source_episode': data.get('source_episode'),
                'start_frame': data.get('source_start_frame'), 'end_frame': data.get('source_end_frame')}
    for parent in list(folder.parents)[:3]:
        manifest = parent / 'split_manifest.json'
        if manifest.is_file():
            segment = _manifest_segments(str(manifest)).get(folder.name)
            if segment:
                start, end = (list(segment.get('source_frame_range') or []) + [None, None])[:2]
                return {'record': str(manifest), 'source_episode': segment.get('source_episode'),
                        'start_frame': start, 'end_frame': end}
    return None


class LegacyAlgorithm:
    def instances(self, episode, params):
        from edc.robots.scope import instances
        if self.spec.id == 'Reset_Detection':
            # v2 calibration explicitly names its executable pose channels.
            # Keep them visible to the worker instead of applying the v1
            # joint-feedback scope to an unrelated signal family.
            calibration = episode.profile.get('calibration', {}).get(self.spec.id, {})
            if isinstance(calibration, dict) and isinstance(calibration.get('references'), dict):
                import copy
                scoped = copy.deepcopy(params)
                scoped.setdefault('channels', list(calibration['references']))
                return instances(episode, scoped, 'channels')
            if (isinstance(calibration, dict) and isinstance(calibration.get('reference'), dict)
                    and calibration.get('unit') == 'm'):
                # A Home in metres names Cartesian tip channels instead of joints (X1 Pro: its joint
                # readings can stop updating while the arm moves, the recorded tip position does not).
                import copy
                scoped = copy.deepcopy(params)
                scoped.setdefault('channels', list(calibration['reference']))
                return instances(episode, scoped, 'channels')
        modes = {'Action_Smoothness': 'pose_feedback', 'Reset_Detection': 'joint_feedback',
                 'Video_Integrity': 'rgb', 'Numeric_Check': 'numeric'}
        return instances(episode, params, modes.get(self.spec.id, 'channels'))

    def __init__(self, ident, inputs, dependencies):
        version = {'Numeric_Check': 'ir-adapter-21', 'Action_Smoothness': 'ir-adapter-22',
                   'Reset_Detection': 'ir-adapter-17', 'Sync_Check': 'ir-adapter-17',
                   'Root_Cause': 'ir-adapter-18', 'Check_Timestamp': 'ir-adapter-19',
                   'Video_Integrity': 'ir-adapter-16'}.get(ident, 'ir-adapter-15')
        descriptions, schema = cpu_parameters(ident)
        self.spec = AlgorithmSpec(ident, version, ident, "Repository contributors",
            "Existing CPU implementation; see edc.algorithms.quality/algorithms/" + ident,
            required_inputs=inputs, dependencies=dependencies, calibration=ident == "Reset_Detection",
            presets=("quality_cpu", "quality"),
            parameters=descriptions, parameter_schema=schema,
            input_descriptions=INPUT_DESCRIPTIONS[ident])

    input_bindings = staticmethod(channel_parameter_bindings)

    def availability(self, episode, params):
        params = resolve_channel_parameters(episode, params)
        episode = selected_inputs(episode, params, self.spec.id)
        if self.spec.id == 'Video_Integrity':
            from .video import input_mode
            inputs = [{'channel': c.name, 'input_mode': input_mode(episode, c, params)}
                      for c in episode.channels.values() if c.modality == 'rgb' and c.image_available]
            partial = any(row['input_mode'] == 'frames' for row in inputs)
            return {'availability': 'partial' if partial else 'available', 'inputs': inputs,
                    'reason': '图像入口直接检查解码画面；视频容器子项不适用，不生成完整视频综合分。' if partial else '',
                    'scope': 'native_image_or_video'}
        if self.spec.id == 'Dedup_Check':
            unavailable = unavailable_dedup_inputs(episode)
            partial = bool(unavailable) and not params.get('channel')
            return {'availability': 'partial' if partial else 'available',
                    'reason': '部分通道缺有效原始时间；只能检查其余可用通道。' if partial else '',
                    'unavailable_inputs': unavailable,
                    'inputs': [{'channel': name, 'field': field,
                                'time_source': episode.channels[name].time_source,
                                'clock': episode.channels[name].clock,
                                'time_evidence': episode.channels[name].time_evidence}
                               for name, field in dedup_inputs(episode, params.get('channel'))]}
        if self.spec.id == 'Action_Smoothness':
            c = selected_pose(episode, params)
            from .action import timeline_plan
            plan = timeline_plan(episode, c, params)
            inputs = [{'channel': c.name, 'field': 'position', 'unit': 'm',
                       'side': c.side, 'time_source': c.time_source}]
            if plan['reference_channel']:
                inputs.append({'channel': plan['reference_channel'], 'role': 'reference_timeline',
                               'time_source': 'acquisition', 'frames_required': False})
            return {'availability': 'partial' if plan['issues'] else 'available',
                    'reason': '; '.join(plan['issues']), 'inputs': inputs,
                    'scope': 'residual_jerk_on_explicit_timeline'}
        if self.spec.id == 'Check_Timestamp':
            if 'multi_timestamps' not in capabilities(episode) or not any(
                    c.modality == 'rgb' and c.timestamps for c in episode.channels.values()):
                return {'availability': 'partial', 'scope': 'single_timeline_observation',
                        'inputs': [{'channel': c.name, 'time_source': c.time_source, 'clock': c.clock}
                                   for c in episode.channels.values()],
                        'reason': '可观察现有单路或公共行时间的间隔；缺少独立时间轴、共同 clock 或相机参考，跨通道对齐不评估。'}
        if self.spec.id != 'Numeric_Check':
            return {'inputs': [{'channel': c.name} for c in episode.channels.values()]}
        from edc.robots.numeric_review import numeric_review, numeric_field_status, channel_settings
        review = numeric_review(episode.profile, params)
        fields = [numeric_field_status(episode, channel, field, review)
                  for channel in episode.channels.values() if channel.value_available
                  for field in channel.fields]
        for item in fields:
            target = item.pop('target')
            channel = episode.channels[item['channel']]
            settings = channel_settings(review['settings'], channel)['thresholds']
            blockers = list(item['reasons'])
            kind = target[0] if target else None
            if kind == 'joint_positions' and settings['joint']['limits'] is None:
                blockers.append('关节限位未配置；范围检查不会执行')
            if kind == 'cmd_positions' and settings['command_limits']['limits'] is None:
                blockers.append('动作指令独立限位未配置；不借用关节反馈限位')
            minimum = (3 if kind in {'joint_positions', 'cmd_positions'} else
                settings['imu']['min_static_frames'] + 1 if kind in {'gyro', 'accel'} else
                settings['pose_freeze']['min_run'] if kind == 'pose_freeze_positions' else 1)
            from .numeric_supplements import FREEZE_GROUPS
            if kind in FREEZE_GROUPS:
                minimum = max(minimum, settings[FREEZE_GROUPS[kind]]['min_run'])
            if channel.count < minimum:
                blockers.append(f'至少需要 {minimum} 个样本才能执行全部适用子项')
            if kind == 'accel':
                if 'ang_vel' not in channel.fields:
                    blockers.append('加速度偏置需要同一 IMU 通道内逐行对应的 ang_vel')
                else:
                    gyro = numeric_field_status(episode, channel, 'ang_vel', review)
                    blockers.extend(gyro['reasons'])
            item['coverage_blockers'] = blockers
        ready = sum(item['physical_checks_available'] for item in fields)
        partial = any(item['coverage_blockers'] for item in fields) or not fields
        return {'availability': 'partial' if partial else 'available', 'fields': fields,
                'inputs': [{'channel': r['channel'], 'field': r['field']} for r in fields],
                'reason': (f'Finiteness checks available; physical checks configured for {ready}/{len(fields)} fields. '
                           '仍有子项缺少配置、联合输入或足够样本，详见字段原因；实际区段覆盖在运行后核验。') if partial else '',
                'scope': 'finiteness_and_explicitly_supported_physical_fields'}

    def preflight(self, episode, params):
        if self.spec.id == 'Dedup_Check' and not 0 <= params.get('dedup_ratio_low', .01) < params.get('dedup_ratio_high', .05) <= 1:
            return 'missing_input', 'Require 0 <= dedup_ratio_low < dedup_ratio_high <= 1'
        return 'runnable', ''

    def run(self, context, params):
        legacy()
        short = too_short(context.episode)
        if short and self.spec.id != 'Structure_Check':
            # Structure_Check excludes it with the reason; nothing else is worth checking.
            return {'score': None, 'passed': None, 'verdict': 'NOT_APPLICABLE', 'assessment_complete': True,
                    'skipped': 'too_short', 'duration': short,
                    'reasons': [f"数据只有 {short['seconds']:.1f} 秒，短于本机型要求的 {short['min_duration_s']:g} 秒，"
                                '已在结构完整性里排除，本项不检查']}
        params = resolve_channel_parameters(context.episode, params)
        ep = selected_inputs(context.episode, params, self.spec.id)
        params = {k: v for k, v in params.items() if k not in {'channels', 'target_clock'}}
        if self.spec.id == 'Action_Smoothness':
            params.pop('channel', None)
        if self.spec.id == "Action_Smoothness":
            channel = selected_pose(ep, params)
            from .action import assess
            return assess(ep, channel, params, context)
        if self.spec.id == "Dedup_Check":
            return self._dedup(ep, params, context)
        if self.spec.id == "Reset_Detection":
            return self._reset(ep, params)
        if self.spec.id == 'Video_Integrity':
            from .video import assess
            return assess(ep, params, context)
        if self.spec.id in {"Sync_Check", "Root_Cause"}:
            import numpy as np
            from edc.algorithms.quality.shared.timing_scope import timing_exclusion_reason
            issues = [{"channel": c.name, "reason": "nonfinite or decreasing timestamps"}
                      for c in ep.channels.values() if c.timestamps and c.time_source in {"acquisition", "container"}
                      and (self.spec.id not in {'Check_Timestamp', 'Root_Cause'}
                           or timing_exclusion_reason(c.name, c) is None)
                      and (not np.isfinite(c.timestamps).all() or np.any(np.diff(c.timestamps) < 0))]
            if issues:
                return {"verdict": "NEEDS_REVIEW", "score": None, "issues": issues,
                        "method": "timeline_validation_v1"}
        module = import_module(f"edc.algorithms.quality.algorithms.{self.spec.id}.run_check")
        fn = module.assess_from_ir
        if self.spec.id == "Numeric_Check":
            return self._numeric(ep, module, params, context)
        info, reader = ep.algorithm_view()
        if self.spec.id == 'Check_Timestamp':
            from edc.algorithms.quality.shared.cadence import cadence_report
            cadence = cadence_report(ep)
            params = dict(params)
            row_gap = params.pop('shared_row_max_gap_s', None)
            if row_gap is not None and shared_row_timeline(ep):
                return shared_row_intervals(ep, float(row_gap), cadence)
            has_camera = any(c.modality == 'rgb' and c.timestamps and
                             c.time_source in {'acquisition', 'container'} for c in ep.channels.values())
            invalid = any(row.get('backward_interval_count', 0) or
                          (row['status'] == 'missing_input' and row.get('time_source') in {'container', 'acquisition'})
                          for row in cadence['channels'])
            if 'multi_timestamps' not in capabilities(ep) or not has_camera or invalid:
                return {'score': None, 'passed': None, 'verdict': 'OBSERVATION',
                        'assessment_complete': False, 'cadence': cadence,
                        'cross_channel_alignment': {'status': 'missing_input',
                            'reason': 'Invalid timeline' if invalid else 'Common clock and camera reference not confirmed'},
                        'reasons': ['已逐路检查记录时间间隔；无效时间轴、共同 clock 或参考条件不足时不计算跨路得分'],
                        'method_version': 'native_cadence_with_clock_gate_v1'}
            diagnosis = clock_diagnosis(ep, [c.name for c in ep.channels.values()
                if c.time_source == 'acquisition' and c.clock and c.timestamps])
            # A fixed clock offset is already judged by Sync_Check; shift it out and judge the
            # alignment itself. A moving offset cannot be shifted out, so it is judged as recorded.
            shift = clock_shift(ep, diagnosis) if (diagnosis['salvage'] or {}).get('status') == 'fixed_offset' else {}
            params = dict(params)
            scoring = params.pop('scoring', None)
            drop = derived_copies(ep, info) if params.pop('count_each_source_once', False) else []
            with timestamp_scoring(module, scoring):
                result = fn(info, ShiftedReader(reader, shift, drop) if shift or drop else reader, **params)
            result['cadence'] = cadence
            result['clock_diagnosis'] = diagnosis
            if scoring:
                result['scoring_overrides'] = dict(scoring)
            if drop:
                result['derived_copies_not_scored'] = drop
            if shift:
                result['clock_shift_s'] = shift
                result['reasons'] = [f'时钟有固定偏差（见采集同步），{shift_note(shift)}后再评价对齐'] + (result.get('reasons') or [])
            else:
                result['reasons'] = (result.get('reasons') or []) + diagnosis['notes']
            result['cross_channel_alignment'] = {'status': 'evaluated',
                'time_source': next(c.time_source for c in ep.channels.values() if c.timestamps),
                'establishes_acquisition_sync': False}
            return result
        if self.spec.id == "Sync_Check":
            ts = {c.name: c.timestamps for c in ep.channels.values()
                  if c.time_source == "acquisition" and c.clock and c.timestamps}
            if len({tuple(t) for t in ts.values()}) < 2:
                from edc.data.ir.metadata import IRNotProvided
                from edc.data.readers.base import make_ir_report
                return make_ir_report("同步", "sync_check", IRNotProvided("独立采集时间轴", ep.format))
            result = module.assess(ts, **params)
            result['clock_diagnosis'] = clock_diagnosis(ep, list(ts))
            result['reasons'] = (result.get('reasons') or []) + result['clock_diagnosis']['notes']
            return result
        if self.spec.id == "Structure_Check":
            result = module.assess(str(ep.path), info.topics, required_topics=ep.profile.get("required_channels"),
                camera_requirements=ep.profile.get("camera_requirements"),
                channel_config=ep.profile.get("channels", {}), **params)
            if short:
                result.update(original_verdict=result.get('verdict'), verdict='DO_NOT_USE', passed=False, too_short=short)
                result['reasons'] = [f"数据太短：只有 {short['seconds']:.1f} 秒，本机型要求至少 {short['min_duration_s']:g} 秒"]                     + list(result.get('reasons') or [])
            return result
        if self.spec.id == 'Root_Cause':
            return self._root_cause_on_one_clock(ep, module, info, reader, params)
        result = fn(info, reader, **params)
        if self.spec.id == 'Action_Smoothness' and getattr(ep, '_explicit_pose_channel', None):
            result['side'] = ep.channels[ep._explicit_pose_channel].side
        return result

    def _root_cause_on_one_clock(self, ep, module, info, reader, params):
        """Root_Cause after shifting any clock offset out, so drift and jumps can be measured.

        Fixed or moving, the per-group median offset is removed; whatever moves around that
        median is left for the method's own drift/jump checks to judge.
        """
        diagnosis = clock_diagnosis(ep, [c.name for c in ep.channels.values()
            if c.time_source == 'acquisition' and c.clock and c.timestamps])
        shift = clock_shift(ep, diagnosis) if diagnosis['salvage'] else {}
        result = self._root_cause(module, info, ShiftedReader(reader, shift) if shift else reader, params)
        if not isinstance(result, dict):
            return result
        result['clock_diagnosis'] = diagnosis
        if shift:
            result['clock_shift_s'] = shift
            result['reasons'] = [f'时钟偏差已先平移掉（{shift_note(shift)}），再查漂移、跳变和断档'] + (result.get('reasons') or [])
        else:
            result['reasons'] = (result.get('reasons') or []) + diagnosis['notes']
        if result.get('verdict') == 'OBSERVATION':
            missing = [s['name'] for s in result.get('sub_indicators') or [] if s.get('score') is None]
            # check_stability skips any channel with fewer than its min_samples (100) samples.
            names = (result.get('timestamp_scope') or {}).get('included_topics') or []
            counts = {n: len(ep.channels[n].timestamps) for n in names if n in ep.channels and ep.channels[n].timestamps}
            short = {n: k for n, k in counts.items() if k < 100}
            if short and missing:
                # Each channel's own span: device-counter clocks cannot be compared with each other.
                seconds = max(ep.channels[n].timestamps[-1] - ep.channels[n].timestamps[0] for n in counts)
                fewest = min(short, key=short.get)
                result['short_input'] = {'seconds': round(float(seconds), 2), 'channel': fewest,
                                         'samples': short[fewest], 'required': 100, 'channels': sorted(short)}
                reason = (f'数据太短：整条只有 {seconds:.1f} 秒，{fewest} 只有 {short[fewest]} 个样本；'
                          f'{"、".join(missing)}每一路至少要 100 个样本才能算，所以没算出来')
                checked = [s for s in result.get('sub_indicators') or [] if s.get('score') is not None]
                if checked and all(s['score'] >= 100 for s in checked):
                    # User decision 2026-10-30: in a few seconds a clock cannot drift or jump in a way
                    # that matters, so the unmeasurable items do not block a pass when the rest is clean.
                    result.update(original_verdict=result.get('verdict'), verdict='ACCEPTABLE', passed=True,
                                  assessment_complete=True, automatic_check_complete=False)
                    result['reasons'] = [reason + '；这么短的数据里时钟来不及漂移或跳变，这两项不需要查'] + list(result.get('reasons') or [])
                else:
                    needs_person(result, reason)
            else:
                needs_person(result, '时间异常归因没能自动做完' + (f"（{'、'.join(missing)}没有算出来）" if missing else '')
                             + '：请看各路数据在时间轴上是否有同时断档或时间突然跳动')
        return result

    @staticmethod
    def _root_cause(module, info, reader, params):
        """Root_Cause with the clock-stability input its IR entry never passes.

        The original entry runs first and keeps every shortage/synthetic-time outcome. When it
        evaluated real timelines, the same timelines are re-assessed with Sync_Check's own
        check_stability (drift and jumps), and configured source groups are limited to the
        topics this episode has, since the method rejects names it was not given.
        """
        families = params.get('families')
        result = module.assess_from_ir(info, reader, **{**params, 'families': None})
        if not (result.get('timestamp_scope') or {}).get('included_topics'):
            return result
        import numpy as np
        from edc.data.readers.base import filter_real_timestamps
        from edc.algorithms.quality.shared.timing_scope import partition_timing_topics
        from edc.algorithms.quality.algorithms.Sync_Check.sync_check import check_stability
        ts_map, _ = filter_real_timestamps(info, reader.require_timestamps(info, min_channels=2))
        ts_map, _ = partition_timing_topics(info, ts_map)
        stability = check_stability({n: np.asarray(t, dtype=float).tolist() for n, t in ts_map.items()})
        families = {n: g for n, g in (families or {}).items() if n in ts_map} or None
        full = module.assess(ts_map, stability, weights=params.get('weights'), families=families)
        full.update(excluded_topics=result.get('excluded_topics'), timestamp_scope=result['timestamp_scope'],
                    stability_input='Sync_Check.check_stability on the same timelines (adapter)',
                    source_groups_input='configured' if families else 'method default (topic namespace)')
        return full

    def _dedup(self, ep, params, context=None):
        import numpy as np
        from edc.algorithms.quality.algorithms.Dedup_Check.run_check import assess
        selected = params.get("channel")
        inputs = dedup_inputs(ep, selected)
        if not inputs:
            raise ValueError(f"No recorded timestamp input for channel: {selected}")
        reports = []
        info = ep.describe_legacy()
        for name, field in inputs:
            c = ep.channels[name]
            topic = info.topics[name]
            timestamp_unit = ep.profile.get('timestamp_unit', 'unknown') if field else 's'
            if field:
                raw_reader = getattr(ep, "inspect_recorded_signal", None) or ep.read_signal
                values = raw_reader(name, field)
            else:
                raw_ns = (getattr(topic, 'acquisition_timestamps_ns', None) if c.time_source == 'acquisition'
                          else getattr(topic, 'container_timestamps_ns', None) if c.time_source == 'container' else None)
                stored = getattr(topic, 'recorded_timestamp_values', None)
                if raw_ns is not None:
                    values, timestamp_unit = np.asarray(raw_ns, dtype=np.int64), 'ns'
                elif stored is not None:
                    values = np.asarray(stored)
                    timestamp_unit = getattr(topic, 'recorded_timestamp_unit', None) or 'unknown'
                else:
                    values = np.asarray(c.timestamps)
            if values.ndim == 2 and values.shape[1] == 1:
                values = values[:, 0]
            if values.ndim != 1:
                raise ValueError(f"Timestamp values must be one-dimensional: {name}")
            if not np.isfinite(values).all():
                result = {"score": None, "verdict": "OBSERVATION", "assessment_complete": False,
                          "reasons": ["Nonfinite timestamp values; equality check not completed"]}
            elif len(values) < 2:
                result = {"score": None, "verdict": "NOT_APPLICABLE", "assessment_complete": False,
                          "reasons": ["At least two recorded samples are required"]}
            else:
                seqs = getattr(topic, 'seqs', None) if not field else None
                result = assess(values.tolist(), seqs, weights=params.get('weights'),
                                dedup_ratio_low=params.get('dedup_ratio_low', .01),
                                dedup_ratio_high=params.get('dedup_ratio_high', .05))
                workdir = getattr(context, 'workdir', None)
                if workdir is not None and result['dedup']['timestamp_dedup']['extra_duplicate_count']:
                    import csv, hashlib
                    from pathlib import Path
                    target = Path(workdir) / ('timestamp-reuse-' + hashlib.sha256(name.encode()).hexdigest()[:16] + '.csv')
                    seen = {}
                    with target.open('w', newline='') as stream:
                        writer = csv.writer(stream)
                        writer.writerow(['channel', 'timestamp', 'timestamp_unit', 'first_source_row',
                                         'previous_occurrence_row', 'source_row', 'adjacent'])
                        for row, value in enumerate(values.tolist()):
                            if value in seen:
                                first, previous = seen[value]
                                writer.writerow([name, value, timestamp_unit, first, previous, row, row == previous + 1])
                                seen[value] = first, row
                            else:
                                seen[value] = row, row
                    result['timestamp_reuse_evidence'] = {'path': str(target), 'source_rows': 'zero based',
                        'row_count': result['dedup']['timestamp_dedup']['extra_duplicate_count']}
            result.update(input_channel=name, input_field=field, time_source=c.time_source,
                          clock=c.clock, timestamp_unit=timestamp_unit,
                          comparison="raw equality" if field else "recorded timestamp equality",
                          time_evidence=getattr(c, 'time_evidence', {}), source_row_mapping='identity',
                          establishes_acquisition_sync=False)
            if c.time_evidence.get('timeline_scope') == 'shared_dataset_rows':
                timestamp_field = c.time_evidence.get('timestamp_field') or ep.profile.get('timestamp_column')
                result.update(input_label=f'公共时间字段 {timestamp_field}', timestamp_field=timestamp_field,
                              inspection_scope='shared_timestamp_equality_only')
                result['reasons'] = (result.get('reasons') or []) + [
                    '仅检查公共时间字段，相同时间轴只检查一次；input_channel 保留关联来源，不表示检查图像或数值内容。']
            if timestamp_unit == 'unknown' or c.time_source not in {'acquisition', 'container'}:
                result['reasons'] = (result.get('reasons') or []) + [
                    '仅比较文件中已有时间原值是否相同；单位或时间来源未确认，不能检查真实采集间隔、速度或跨通道同步。']
            reports.append(result)
        if reports:
            reports[0]['unavailable_inputs'] = unavailable_dedup_inputs(ep, info=info)
            if reports[0]['unavailable_inputs'] and not selected:
                reports[0]['assessment_complete'] = False
                if reports[0].get('verdict') == 'ACCEPTABLE':
                    reports[0].update(verdict='OBSERVATION', passed=None)
                reports[0]['reasons'] = (reports[0].get('reasons') or []) + [
                    '部分原始通道缺有效时间，未完成全通道时间重复检查；已发现的问题仍保留。']
        return reports

    def _numeric(self, ep, module, params, context=None):
        import csv
        import hashlib
        import numpy as np
        from edc.robots.numeric_review import (numeric_review, numeric_field_status, channel_settings,
                                               method_settings, ADAPTER_TARGETS)
        from edc.data.ir.units import UnitInputError
        reports = []
        review = numeric_review(getattr(ep, 'profile', {}), params)
        # Per-robot declaration: fields Numeric_Check has no physical check for are only
        # checked for NaN/Inf, and that counts as a finished check ("channel.field").
        finiteness_only = set(params.get('finiteness_only') or ())
        # Per-robot: a constant run after motion shorter than this is a normal pause, not a freeze candidate.
        freeze_min_duration_s = params.get('freeze_min_duration_s')
        if freeze_min_duration_s is not None and (type(freeze_min_duration_s) not in (int, float) or freeze_min_duration_s <= 0):
            raise ValueError('freeze_min_duration_s must be a positive number of seconds')
        for channel in ep.channels.values():
            if not channel.value_available:
                continue
            settings = channel_settings(review['settings'], channel)
            for field in channel.fields:
                unit_reason = None
                recorded_orientation = field == 'orientation' and callable(getattr(ep, 'inspect_recorded_signal', None))
                try:
                    if recorded_orientation:
                        values = ep.inspect_recorded_signal(channel.name, field)
                        unit_reason = 'Source orientation finiteness diagnostic; canonical conversion is not a physical quality check'
                    else:
                        values = ep.read_signal(channel.name, field)
                except UnitInputError as exc:
                    # Finiteness does not require units. Never forward these raw
                    # values to a physical detector or another algorithm.
                    values = ep.inspect_recorded_signal(channel.name, field)
                    unit_reason = str(exc)
                spec = (ep.recorded_channels[channel.name] if recorded_orientation else channel).field_spec(field)
                eligibility = numeric_field_status(ep, channel, field, review)
                target = eligibility['target']
                declared = bool({f'{n}.{field}' for n in (channel.name, getattr(channel, 'logical_name', None))} & finiteness_only)
                if declared and target:
                    raise ValueError(f'{channel.name}.{field}: 有物理检查项，不能声明为只查有限值')
                unit = spec["unit"]
                finite = bool(np.isfinite(values).all())
                physical = not unit_reason and eligibility['physical_checks_available']
                # A declared finiteness-only field may be one number per row (e.g. a gripper or lift value).
                shape_valid = ((values.ndim == 2 and values.shape[1] > 0 or declared and not target and values.ndim == 1)
                    and len(values) == channel.count
                    and (not target or target[0] not in {'gyro', 'accel'} or values.shape[1] == 3))
                if values.size == 0:
                    result = {"score": None, "verdict": "NOT_APPLICABLE", "assessment_complete": False,
                              "reason": "Empty numeric field", "finite": None}
                elif not finite:
                    result = {"score": None, "verdict": "DO_NOT_USE", "finite": False,
                              "reason": "Nonfinite numeric values"}
                elif physical and shape_valid and target[0] in ADAPTER_TARGETS:
                    # Adapter-only check; its verdict is filled from the supplement below.
                    result = {'score': None, 'verdict': None, 'adapter_check': target[0]}
                elif physical and shape_valid:
                    result = module.assess(**{target[0]: values}, unit=target[1],
                                           **method_settings(settings))
                    if target[0] == 'pose_freeze_positions' and freeze_min_duration_s:
                        result = short_pose_holds(result, channel, freeze_min_duration_s)
                elif physical:
                    result = {'score': None, 'verdict': 'NOT_APPLICABLE', 'finite': True,
                              'reason': 'Numeric field shape/count does not match the declared input'}
                elif declared:
                    result = {"score": None, "verdict": "ACCEPTABLE", "finite": True,
                              "reason": "按机型配置只查有限值：物理量或单位尚未确认，不做物理检查"}
                else:
                    finite = bool(np.isfinite(values).all())
                    result = {"score": None, "verdict": "NOT_APPLICABLE" if finite else "DO_NOT_USE",
                              "finite": finite, "assessment_complete": False,
                              "reason": "Physical thresholds not applied: Numeric configuration confirmation, source unit or calibration pending"}
                bad = np.argwhere(~np.isfinite(values))
                examples = []
                selection = getattr(channel, 'selection', {}) or {}
                def location(index):
                    indices = index.tolist()
                    source_indices = list(indices)
                    if len(indices) > 1 and selection.get('indices'):
                        source_indices[1] = selection['indices'][indices[1]]
                    value = values[tuple(indices)]
                    return {'indices': indices, 'source_indices': source_indices,
                            'kind': 'NaN' if np.isnan(value) else '+Inf' if value > 0 else '-Inf'}
                examples = [location(index) for index in bad[:100]]
                if len(bad) and getattr(context, 'workdir', None):
                    name = hashlib.sha256(f'{channel.name}\0{field}'.encode()).hexdigest()[:16]
                    with (context.workdir / f'nonfinite-{name}.csv').open('w', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=['source', 'channel', 'field', 'indices', 'source_indices', 'kind'])
                        writer.writeheader()
                        for index in bad:
                            writer.writerow({'source': channel.source,
                                'channel': selection.get('channel', channel.name),
                                'field': selection.get('field', field), **location(index)})
                result.update(nan_count=int(np.isnan(values).sum()), posinf_count=int(np.isposinf(values).sum()),
                              neginf_count=int(np.isneginf(values).sum()), nonfinite_count=len(bad),
                              nonfinite_indices=[x['indices'] for x in examples], nonfinite_examples=examples,
                              nonfinite_indices_truncated=len(bad) > 100,
                              finite=finite if values.size else None,
                              assessment_complete=bool(values.size and finite and (physical or declared) and shape_valid),
                              physical_checks_applied=bool(values.size and finite and physical and shape_valid),
                              configuration_review={k: v for k, v in review.items() if k not in {'settings', 'unit_suggestions'}},
                              suggested_unit=target[1] if target else None,
                              source_mapping={'source': channel.source, 'channel': selection.get('channel', channel.name),
                                  'field': selection.get('field', field), 'selection': selection,
                                  'episode_selector': getattr(ep, 'selector', {})})
                result["input_channel"] = channel.name
                result["input_field"] = field
                result["unit"] = unit
                result["source_unit"] = spec.get("source_unit", unit)
                result["unit_normalization_reason"] = unit_reason
                result["physical_check_blockers"] = eligibility['reasons']
                result["input_scope"] = "recorded_finiteness_only" if unit_reason else "SI"
                result["finiteness_only_declared"] = declared
                result["source_kind"] = spec["source_kind"]
                result["calibration_status"] = spec["calibration_status"]
                result["shape"] = list(values.shape)
                from .numeric_supplements import supplements
                extra_checks = supplements(values, ep, channel, field, target[0] if target else None,
                    settings['thresholds'], physical, getattr(context, 'workdir', None),
                    freeze_min_duration_s=freeze_min_duration_s, joint_stuck_settings=params.get('joint_stuck'))
                result['supplemental_checks'] = extra_checks
                if result.get('adapter_check'):
                    check = next(c for c in extra_checks if c['id'] == 'joint_velocity_limit')
                    result.update(verdict=check['verdict'], passed=check['verdict'] == 'ACCEPTABLE',
                                  reasons=[f"{check['frames_over']}/{check['frames']} 帧、{check['segments']} 段超过 "
                                           f"{check['threshold_text']} rad/s（最大 {check['max_abs']:.2f}）"] if check['events'] else [],
                                  joint_velocity={k: check[k] for k in ('threshold', 'frames', 'frames_over',
                                      'samples_over', 'segments', 'max_abs', 'per_component')})
                if any(check.get('verdict') == 'DO_NOT_USE' for check in extra_checks):
                    result['supplemental_verdict'] = 'DO_NOT_USE'
                result['review_candidates'] = [{'check': check['id'], 'needs_review': True,
                    'events': check['events'], 'reason': check['reason']} for check in extra_checks
                    if any(event.get('needs_review') for event in check['events'])]
                imu_pair = None
                if target and target[0] == 'accel' and physical and finite and values.size:
                    from .imu_pair import paired_bias
                    imu_pair = paired_bias(ep, channel, values, review)
                    result['imu_pair_check'] = imu_pair
                    if imu_pair['events']:
                        result['review_candidates'].append({'check': 'accel_bias',
                            'needs_review': True, 'events': imu_pair['events'],
                            'reason': '联合输入发现加速度偏置线索，保留历史评分并请求复核。'})
                from .numeric_coverage import coverage
                result['check_coverage'] = coverage(values, eligibility, settings['thresholds'],
                    result, physical, unit_reason, imu_pair, extra_checks, shape_valid, declared,
                    params.get('joint_limits_pending'))
                result['coverage_complete'] = result['check_coverage']['coverage_complete']
                joint_result = (result.get('numeric') or {}).get('joint') or {}
                for event in joint_result.get('events', []):
                    for example in event.get('examples', []):
                        j = example['joint_index']
                        example['source_joint_index'] = selection.get('indices', list(range(values.shape[1])))[j]
                        example['source_channel'] = selection.get('channel', channel.name)
                        example['source_field'] = selection.get('field', field)
                        if channel.joint_names and j < len(channel.joint_names):
                            example['joint_name'] = channel.joint_names[j]
                reports.append(result)
        return reports

    def _reset(self, ep, params=None):
        """Reset check; with `split_segments`, an episode cut out of a longer recording (per the dataset's own
        cutting record) is checked only at an end that is a real end of the recording: the start when the cut
        begins at source frame 0. The end is never known to be the recording's end, so it is not checked."""
        result = self._reset_ends(ep)
        segment = split_segment(ep) if (params or {}).get('split_segments') else None
        if not segment:
            return result
        ends = ['start'] if segment.get('start_frame') == 0 else []
        where = (f"从长录制剪出的切片（来源 {segment.get('source_episode')} 第 {segment.get('start_frame')}–"
                 f"{segment.get('end_frame')} 帧，记录在 {segment['record']}）")
        result.update(split_segment=segment, checked_ends=ends, method_verdict=result.get('verdict'))
        if not ends:
            result.update(verdict='NOT_APPLICABLE', passed=None, score=None, assessment_complete=True,
                          reasons=[where + '：开头和结尾都是剪出来的，本来就不在初始位置，不查复位'])
            return result
        evidence = result['evidence']
        if result['method'] == 'calibrated_home_reference_v2':
            score = min(e['start_score'] for e in evidence)
            result.update(score=score, passed=score > 0)
        else:
            tolerance = float(result['calibration']['tolerance'])
            result['passed'] = all(e['start_error'] <= tolerance for e in evidence)
        result.update(verdict='ACCEPTABLE' if result['passed'] else 'NEEDS_REVIEW',
                      reasons=[where + '：从原始录制的第 0 帧开始，只查开头；结尾是剪出来的，不查'])
        return result

    def _reset_ends(self, ep):
        import numpy as np
        calibration = reset_calibration(ep)
        if calibration.get('schema_version') == 'edc_reset_calibration_v2':
            def vector_score(error, tolerance1, tolerance2):
                values = np.asarray(error, dtype=float)
                t1 = np.asarray(tolerance1, dtype=float)
                t2 = np.asarray(tolerance2, dtype=float)
                scores = np.where(values <= t1, 100.0,
                                  np.where(values >= t2, 0.0,
                                           100.0 * (t2 - values) / (t2 - t1)))
                # Historical Reset_Detection semantics: a zero component
                # score invalidates the complete vector parameter.
                return 0.0 if np.any(scores == 0.0) else float(np.mean(scores))

            evidence = []
            scores = []
            for channel, item in calibration['references'].items():
                values = ep.read_signal(channel, item['field'])
                home = np.asarray(item['home'], dtype=float)
                tolerance1 = np.asarray(item['tolerance1'], dtype=float)
                tolerance2 = np.asarray(item['tolerance2'], dtype=float)
                if values.ndim != 2 or home.shape != values.shape[1:]:
                    raise ValueError(f'Invalid reset reference shape for {channel}: expected {values.shape[1:]}, got {home.shape}')
                if not np.isfinite(values).all():
                    raise ValueError(f'Nonfinite reset signal: {channel}.{item["field"]}')
                start_error = np.abs(values[0] - home)
                end_error = np.abs(values[-1] - home)
                start_score = vector_score(start_error, tolerance1, tolerance2)
                end_score = vector_score(end_error, tolerance1, tolerance2)
                score = min(start_score, end_score)
                scores.append(score)
                evidence.append({
                    'channel': channel,
                    'requested_channel': item.get('requested_channel', channel),
                    'field': item['field'],
                    'unit': item['unit'],
                    'home': item['home'],
                    'tolerance1': item['tolerance1'],
                    'tolerance2': item['tolerance2'],
                    'start_error': start_error.tolist(),
                    'end_error': end_error.tolist(),
                    'start_max_error': float(np.max(start_error)),
                    'end_max_error': float(np.max(end_error)),
                    'start_score': start_score,
                    'end_score': end_score,
                    'score': score,
                })
            if not evidence:
                raise ValueError('Reset calibration has no executable references')
            score = min(scores)
            return {'method': 'calibrated_home_reference_v2', 'score': score,
                    'passed': score > 0,
                    'verdict': 'ACCEPTABLE' if score > 0 else 'NEEDS_REVIEW',
                    'evidence': evidence, 'deferred': calibration.get('deferred', []),
                    'threshold_profile': 'per-component tolerance1/tolerance2; min(start,end)',
                    'calibration': calibration,
                    'source_calibration': ep.profile['calibration'][self.spec.id]}
        reference = calibration["reference"]
        tolerance = float(calibration["tolerance"])
        if not np.isfinite(tolerance) or tolerance <= 0 or not reference:
            raise ValueError("Reset calibration requires positive tolerance and reference channels")
        evidence = []
        for channel, home in reference.items():
            if ep.channels[channel].field_spec("position")["unit"] != calibration.get("unit") or not calibration.get("unit"):
                raise ValueError("Reset calibration/channel units must match explicitly")
            values = ep.read_signal(channel, "position")
            home = np.asarray(home, dtype=float)
            if values.ndim != 2 or home.shape != values.shape[1:] or not np.isfinite(values).all() or not np.isfinite(home).all():
                raise ValueError("Invalid reset reference shape or nonfinite signal")
            evidence.append({"channel": channel, "start_error": float(np.max(np.abs(values[0] - home))),
                             "end_error": float(np.max(np.abs(values[-1] - home)))})
            # Where to look when reviewing: the worst joint and the time of the first/last sample.
            times = getattr(ep.channels[channel], "timestamps", None)
            for key, row in (("start", values[0]), ("end", values[-1])):
                worst = int(np.argmax(np.abs(row - home)))
                evidence[-1][f"{key}_joint_index"] = worst
                evidence[-1][f"{key}_joint_offset"] = float(row[worst] - home[worst])
                evidence[-1][f"{key}_time_s"] = (float(times[0 if key == "start" else -1])
                                                 if times is not None and len(times) == len(values) else None)
        passed = all(max(e["start_error"], e["end_error"]) <= tolerance for e in evidence)
        return {"method": "calibrated_home_reference_v1", "passed": passed,
                "verdict": "ACCEPTABLE" if passed else "NEEDS_REVIEW", "evidence": evidence,
                "calibration": calibration, "source_calibration": ep.profile["calibration"][self.spec.id]}


def builtins():
    """Return the stable nine-method CPU compatibility surface."""
    return [LegacyAlgorithm(*args) for args in SPECS]


def indicator_builtins():
    """Return the repository-owned modality indicator pool."""
    from edc.algorithms.quality.algorithms.Indicator_Checks.plugins import plugins
    return list(plugins())
