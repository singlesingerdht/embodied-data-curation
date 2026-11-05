"""复位检测：框架怎么调用这项检查（读 yaml 的 home、按 home 选通道在这里，检查方法在 run_check.py）。"""
from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm


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


class Algorithm(BuiltinAlgorithm):
    name = '复位检测'
    version = 'ir-adapter-18'
    inputs = ('joint',)
    calibration = True
    weights = False
    instance_mode = 'joint_feedback'
    descriptions = ('关节 position；已确认的复位 reference、单位与正 tolerance，且与实际输入量纲一致。',)

    def instances(self, episode, params):
        from edc.robots.scope import instances
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
        return super().instances(episode, params)

    def select(self, episode, params):
        view = super().select(episode, params)
        if 'channels' in params:
            calibration = view.profile.get('calibration', {}).get(self.spec.id)
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
        return view

    def missing(self, episode, missing):
        reset_profile = episode.profile.get('calibration', {}).get('Reset_Detection', {})
        if isinstance(reset_profile, dict) and (
                isinstance(reset_profile.get('references'), dict)
                or reset_profile.get('unit') == 'm' and isinstance(reset_profile.get('reference'), dict)):
            # The v2 profile explicitly names Cartesian feedback channels.  Do not
            # require an unrelated joint channel merely because v1 used one.
            missing.discard('joint')
            if not any(c.value_available and c.modality == 'pose' and 'position' in c.fields
                       for c in episode.channels.values()):
                missing.add('pose')
        return missing

    def preflight(self, episode, params):
        if not episode.profile.get('calibration', {}).get(self.spec.id):
            return 'needs_calibration', 'Provide algorithm-specific calibration'
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
        return 'runnable', ''

    def assess(self, ep, params, context):
        return self._reset(ep)

    def _reset(self, ep):
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
