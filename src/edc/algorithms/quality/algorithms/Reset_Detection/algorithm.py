"""复位检测：框架怎么调用这项检查（读 yaml 的 home、按 home 选通道在这里，检查方法在 method.py）。"""
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
        return self.method().assess(ep, reset_calibration(ep), ep.profile['calibration'][self.spec.id])
