"""Thin adapters preserve historical scores and complete original reports."""
from importlib import import_module, util
import shutil
from framework.registry import AlgorithmSpec
from framework.compat import legacy
from .contracts import cpu_parameters, INPUT_DESCRIPTIONS
from framework.channel_names import resolve_channel_parameters, channel_parameter_bindings

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


def dedup_inputs(episode, selected=None, info=None):
    """Recorded timelines or an explicitly mapped raw timestamp field; no inferred clock."""
    from Quality_Assessment.shared.timing_scope import timing_exclusion_reason
    describe = getattr(episode, 'describe_legacy', None)
    if info is None and callable(describe):
        info = describe()
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
    from Quality_Assessment.shared.timing_scope import timing_exclusion_reason
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


def unavailable_dedup_inputs(episode, inputs=None, info=None):
    from Quality_Assessment.shared.timing_scope import timing_exclusion_reason
    describe = getattr(episode, 'describe_legacy', None)
    if info is None and callable(describe):
        info = describe()
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
        from framework.ir.units import UnitInputError
        from framework.ir.metadata import IRNotProvided
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
    if ident == 'Dedup_Check' and params.get('channel') and 'channels' in params:
        raise ValueError('Use channel or channels, not both')
    if ident == 'Sync_Check' and params.get('primary') and params['primary'] not in view.channels:
        raise ValueError('Sync primary must be included in selected channels')
    if ident == 'Reset_Detection' and 'channels' in params:
        calibration = view.profile.get('calibration', {}).get(ident)
        if calibration and isinstance(calibration.get('reference'), dict):
            missing = [n for n in view.channels if n not in calibration['reference']]
            if missing:
                raise ValueError(f'Selected reset channels have no Home reference: {missing}')
            calibration['reference'] = {n: calibration['reference'][n] for n in view.channels}
    if ident in {'Check_Timestamp', 'Sync_Check', 'Root_Cause'} and params.get('target_clock'):
        from framework.clock_view import ClockMappedEpisode
        view = ClockMappedEpisode(view, params['target_clock'])
    return view


def _preflight(plugin, episode, params=None):
    spec = plugin.spec
    from framework.algorithm_contracts import resolve_parameters, evaluate_requirements
    try:
        params = resolve_parameters(spec, params)
        from framework.algorithm_contracts import bind_parameters
        params, _ = bind_parameters(spec, episode, params)
    except ValueError as exc:
        return 'missing_input', f'Invalid algorithm parameters: {exc}'
    if isinstance(plugin, LegacyAlgorithm):
        try:
            episode = selected_inputs(episode, params, spec.id)
        except ValueError as exc:
            return 'missing_input', str(exc)
    unmet = [r for r in evaluate_requirements(spec, episode, params) if r['status'] == 'missing_input']
    if unmet:
        return 'missing_input', '; '.join(reason for row in unmet for reason in row['reasons'])
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
    if missing:
        if spec.id in {'Sync_Check', 'Root_Cause', 'Check_Timestamp'}:
            return 'missing_input', ('时间条件不足：需要有效且单位明确的时间轴；跨通道检查还需要独立的原始时间轴和共同 clock。'
                '只有公共行时间、未知单位或合成时间时，不能评估原始采集同步；现有时间仍保留。')
        if spec.id == 'Dedup_Check':
            return 'missing_input', '没有可用的原始时间值；合成时间不用于时间重复检查。'
        return "missing_input", "Missing capabilities: " + ", ".join(sorted(missing))
    from framework.ir.units import require_si_field
    for requirement in spec.input_requirements:
        from framework.channel_names import resolve_channel
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
            from framework.ir.units import UnitInputError
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
            reset_calibration(episode)
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
    from framework.ir.units import si_unit, UnitInputError
    from framework.transforms.core import convert_units
    source = episode.profile["calibration"]["Reset_Detection"]
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
        from framework.ir.units import require_si_field
        require_si_field(episode, name, 'position', target)
        result["reference"][name] = (convert_units(home, source["unit"], target).output.tolist()
            if source["unit"] != target else copy.deepcopy(home))
    result["tolerance"] = (float(convert_units(tolerance, source["unit"], target).output)
        if source["unit"] != target else tolerance)
    return result


class LegacyAlgorithm:
    def __init__(self, ident, inputs, dependencies):
        version = 'ir-adapter-13'  # Unified semantic gates, logical bindings and explicit clock view.
        descriptions, schema = cpu_parameters(ident)
        self.spec = AlgorithmSpec(ident, version, ident, "Repository contributors",
            "Existing CPU implementation; see Quality_Assessment/algorithms/" + ident,
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
            return {'inputs': [{'channel': c.name, 'field': 'position', 'unit': 'm',
                                'side': c.side, 'time_source': c.time_source}]}
        if self.spec.id == 'Check_Timestamp':
            if 'multi_timestamps' not in capabilities(episode) or not any(
                    c.modality == 'rgb' and c.timestamps for c in episode.channels.values()):
                return {'availability': 'partial', 'scope': 'single_timeline_observation',
                        'inputs': [{'channel': c.name, 'time_source': c.time_source, 'clock': c.clock}
                                   for c in episode.channels.values()],
                        'reason': '可观察现有单路或公共行时间的间隔；缺少独立时间轴、共同 clock 或相机参考，跨通道对齐不评估。'}
        if self.spec.id != 'Numeric_Check':
            return {'inputs': [{'channel': c.name} for c in episode.channels.values()]}
        from framework.robots.numeric_review import numeric_review, numeric_field_status
        review = numeric_review(episode.profile, params)
        fields = [numeric_field_status(episode, channel, field, review)
                  for channel in episode.channels.values() if channel.value_available
                  for field in channel.fields]
        for item in fields:
            item.pop('target')
        ready = sum(item['physical_checks_available'] for item in fields)
        partial = ready != len(fields) or not fields
        return {'availability': 'partial' if partial else 'available', 'fields': fields,
                'inputs': [{'channel': r['channel'], 'field': r['field']} for r in fields],
                'reason': (f'Finiteness checks available; physical checks configured for {ready}/{len(fields)} fields. '
                           'Unconfirmed units/semantics are never passed to physical checks') if partial else '',
                'scope': 'finiteness_and_explicitly_supported_physical_fields'}

    def preflight(self, episode, params):
        if self.spec.id == 'Dedup_Check' and not 0 <= params.get('dedup_ratio_low', .01) < params.get('dedup_ratio_high', .05) <= 1:
            return 'missing_input', 'Require 0 <= dedup_ratio_low < dedup_ratio_high <= 1'
        return 'runnable', ''

    def run(self, context, params):
        legacy()
        params = resolve_channel_parameters(context.episode, params)
        ep = selected_inputs(context.episode, params, self.spec.id)
        params = {k: v for k, v in params.items() if k not in {'channels', 'target_clock'}}
        if self.spec.id == 'Action_Smoothness':
            params.pop('channel', None)
        if self.spec.id == "Action_Smoothness":
            channel = selected_pose(ep, params)
            ep.read_signal(channel.name, "position")  # Validate only the input actually consumed.
            if getattr(ep, '_explicit_pose_channel', None):
                params['side'] = 'any'  # One validated explicit topic; legacy reader cannot choose another.
        if self.spec.id == "Dedup_Check":
            return self._dedup(ep, params, context)
        if self.spec.id == "Reset_Detection":
            return self._reset(ep)
        if self.spec.id == 'Video_Integrity':
            from .video import assess
            return assess(ep, params, context)
        if self.spec.id in {"Sync_Check", "Root_Cause"}:
            import numpy as np
            from Quality_Assessment.shared.timing_scope import timing_exclusion_reason
            issues = [{"channel": c.name, "reason": "nonfinite or decreasing timestamps"}
                      for c in ep.channels.values() if c.timestamps and c.time_source in {"acquisition", "container"}
                      and (self.spec.id not in {'Check_Timestamp', 'Root_Cause'}
                           or timing_exclusion_reason(c.name, c) is None)
                      and (not np.isfinite(c.timestamps).all() or np.any(np.diff(c.timestamps) < 0))]
            if issues:
                return {"verdict": "NEEDS_REVIEW", "score": None, "issues": issues,
                        "method": "timeline_validation_v1"}
        module = import_module(f"Quality_Assessment.algorithms.{self.spec.id}.run_check")
        fn = module.assess_from_ir
        if self.spec.id == "Numeric_Check":
            return self._numeric(ep, module, params, context)
        info, reader = ep.algorithm_view()
        if self.spec.id == 'Check_Timestamp':
            from Quality_Assessment.shared.cadence import cadence_report
            cadence = cadence_report(ep)
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
            result = fn(info, reader, **params)
            result['cadence'] = cadence
            result['cross_channel_alignment'] = {'status': 'evaluated',
                'time_source': next(c.time_source for c in ep.channels.values() if c.timestamps),
                'establishes_acquisition_sync': False}
            return result
        if self.spec.id == "Sync_Check":
            ts = {c.name: c.timestamps for c in ep.channels.values()
                  if c.time_source == "acquisition" and c.clock and c.timestamps}
            if len({tuple(t) for t in ts.values()}) < 2:
                from framework.ir.metadata import IRNotProvided
                from framework.readers.base import make_ir_report
                return make_ir_report("同步", "sync_check", IRNotProvided("独立采集时间轴", ep.format))
            return module.assess(ts, **params)
        if self.spec.id == "Structure_Check":
            return module.assess(str(ep.path), info.topics, required_topics=ep.profile.get("required_channels"),
                camera_requirements=ep.profile.get("camera_requirements"),
                channel_config=ep.profile.get("channels", {}), **params)
        result = fn(info, reader, **params)
        if self.spec.id == 'Action_Smoothness' and getattr(ep, '_explicit_pose_channel', None):
            result['side'] = ep.channels[ep._explicit_pose_channel].side
        return result

    def _dedup(self, ep, params, context=None):
        import numpy as np
        from Quality_Assessment.algorithms.Dedup_Check.run_check import assess
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
        from framework.robots.numeric_review import numeric_review, numeric_field_status
        from framework.ir.units import UnitInputError
        reports = []
        review = numeric_review(getattr(ep, 'profile', {}), params)
        for channel in ep.channels.values():
            if not channel.value_available:
                continue
            for field in channel.fields:
                unit_reason = None
                try:
                    values = ep.read_signal(channel.name, field)
                except UnitInputError as exc:
                    # Finiteness does not require units. Never forward these raw
                    # values to a physical detector or another algorithm.
                    values = ep.inspect_recorded_signal(channel.name, field)
                    unit_reason = str(exc)
                spec = channel.field_spec(field)
                eligibility = numeric_field_status(ep, channel, field, review)
                target = eligibility['target']
                unit = spec["unit"]
                finite = bool(np.isfinite(values).all())
                physical = not unit_reason and eligibility['physical_checks_available']
                if values.size == 0:
                    result = {"score": None, "verdict": "NOT_APPLICABLE", "assessment_complete": False,
                              "reason": "Empty numeric field", "finite": None}
                elif not finite:
                    result = {"score": None, "verdict": "DO_NOT_USE", "finite": False,
                              "reason": "Nonfinite numeric values"}
                elif physical:
                    result = module.assess(**{target[0]: values}, **review['settings'])
                else:
                    finite = bool(np.isfinite(values).all())
                    result = {"score": None, "verdict": "NOT_APPLICABLE" if finite else "DO_NOT_USE",
                              "finite": finite, "assessment_complete": False,
                              "reason": "Physical thresholds not applied: author confirmation, source unit or calibration pending"}
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
                              assessment_complete=bool(values.size and finite and physical),
                              physical_checks_applied=bool(values.size and finite and physical),
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
                result["source_kind"] = spec["source_kind"]
                result["calibration_status"] = spec["calibration_status"]
                result["shape"] = list(values.shape)
                from .numeric_coverage import coverage
                result['check_coverage'] = coverage(values, eligibility, review['settings']['thresholds'],
                    result, physical, unit_reason)
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

    def _reset(self, ep):
        import numpy as np
        calibration = reset_calibration(ep)
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
        passed = all(max(e["start_error"], e["end_error"]) <= tolerance for e in evidence)
        return {"method": "calibrated_home_reference_v1", "passed": passed,
                "verdict": "ACCEPTABLE" if passed else "NEEDS_REVIEW", "evidence": evidence,
                "calibration": calibration, "source_calibration": ep.profile["calibration"][self.spec.id]}


def builtins():
    return [LegacyAlgorithm(*args) for args in SPECS]
