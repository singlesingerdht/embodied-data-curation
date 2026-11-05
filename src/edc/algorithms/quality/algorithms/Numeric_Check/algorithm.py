"""数值检查：框架怎么调用这项检查（逐字段读数、配置和补充检查在这里，检查方法在 method.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm
from edc.robots.scope import numeric_fields


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


class Algorithm(BuiltinAlgorithm):
    name = '数值检查'
    version = 'ir-adapter-21'
    inputs = ('numeric',)
    instance_mode = 'numeric'
    descriptions = ('数值字段可做 NaN/Inf 检查；物理检查还需明确物理量、可转换到 SI 的单位、标定及 Numeric 配置确认。',
                    '动作位置指令需要独立 command_limits.limits；数值冻结只报告同 Episode 逐分量恒值区间，运动后恒值需复核。')
    parameters = {
        'thresholds': {'type': 'object', 'description': '覆盖 robot.yaml 的 numeric_check.thresholds；变更后 Numeric 配置确认失效'},
        'freeze_min_duration_s': {'type': 'number', 'exclusiveMinimum': 0,
            'description': '动过之后数值连续不变短于这个秒数的，视为正常停住，不进复核；不填按原方法的样本数规则'},
        'joint_stuck': {'type': 'object',
            'description': '本机型关节停住时读数完全不变（如 X1 Pro）时使用：不再把单个关节“动过之后不变”列为冻结候选，'
                           '改为整条手臂所有关节同时不变至少 freeze_min_duration_s 秒、且同一手臂末端移动至少 min_move_m 米才算关节读数卡住；'
                           'verdict 为卡住时的结论（默认 DO_NOT_USE）'},
        'joint_limits_pending': {'type': 'string',
            'description': '本机型还没有关节限位来源（如没有 URDF）时写明原因：关节限位子项记为不适用并显示原因；已填 joint.limits 时不起作用'},
        'finiteness_only': {'type': 'array', 'items': {'type': 'string'},
            'description': '“通道.字段”列表：本机型只查 NaN/Inf、视为检查完成；只允许没有物理检查项的字段'},
        'ranges': {'type': 'object',
            'description': '{“通道.字段”: {range: [最小, 最大], unit}}：机器人 yaml 写了 range 的项，超出范围判排除（由 yaml 自动生成）'}}

    def available(self, episode, params):
        from edc.algorithms.quality.algorithms.Numeric_Check.review import numeric_review, numeric_field_status, channel_settings
        review = numeric_review(episode.profile, params)
        fields = [numeric_field_status(episode, channel, field, review)
                  for channel in episode.channels.values() if channel.value_available
                  for field in numeric_fields(episode, channel)]
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
            from edc.algorithms.quality.algorithms.Numeric_Check.supplements import FREEZE_GROUPS
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

    def assess(self, ep, params, context):
        return self._numeric(ep, self.method(), params, context)

    def _numeric(self, ep, module, params, context=None):
        import csv
        import hashlib
        import numpy as np
        from edc.algorithms.quality.algorithms.Numeric_Check.review import (numeric_review, numeric_field_status, channel_settings,
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
            for field in numeric_fields(ep, channel):
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
                    result = module.assess(**{target[0]: values}, **method_settings(settings))
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
                result["robot_part"], result["side"] = channel.robot_part, channel.side
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
                from edc.algorithms.quality.algorithms.Numeric_Check.supplements import supplements
                ranges = params.get('ranges') or {}
                value_range = next((ranges[f'{n}.{field}'] for n in (channel.name, getattr(channel, 'logical_name', None))
                                    if f'{n}.{field}' in ranges), None)
                extra_checks = supplements(values, ep, channel, field, target[0] if target else None,
                    settings['thresholds'], physical, getattr(context, 'workdir', None),
                    freeze_min_duration_s=freeze_min_duration_s, joint_stuck_settings=params.get('joint_stuck'),
                    value_range=value_range)
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
                    from edc.algorithms.quality.algorithms.Numeric_Check.imu_pair import paired_bias
                    imu_pair = paired_bias(ep, channel, values, review)
                    result['imu_pair_check'] = imu_pair
                    if imu_pair['events']:
                        result['review_candidates'].append({'check': 'accel_bias',
                            'needs_review': True, 'events': imu_pair['events'],
                            'reason': '联合输入发现加速度偏置线索，保留历史评分并请求复核。'})
                from edc.algorithms.quality.algorithms.Numeric_Check.coverage import coverage
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
