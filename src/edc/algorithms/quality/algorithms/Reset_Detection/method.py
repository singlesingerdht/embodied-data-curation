"""复位检测的方法本体：开头、结尾两帧和 yaml 写的 home 比偏差。
calibration 是 algorithm.py 整理好的标定（v2：逐通道 home 与两级容差；v1：统一单位与容差），
source_calibration 是 yaml 里的原样，放进报告备查。"""


def assess(ep, calibration, source_calibration):
    import numpy as np
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
                'source_calibration': source_calibration}
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
            "calibration": calibration, "source_calibration": source_calibration}
