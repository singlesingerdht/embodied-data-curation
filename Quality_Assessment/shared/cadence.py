"""Per-channel timing observations; no cross-clock claims or new score."""
import numpy as np
from Quality_Assessment.shared.timing_scope import timing_exclusion_reason


def cadence_report(episode):
    info = episode.describe_legacy()
    rows, excluded = [], []
    for name, channel in episode.channels.items():
        if channel.selection:
            continue  # Selected columns inherit the same rows, not a new stream.
        topic = info.topics[name]
        reason = timing_exclusion_reason(name, topic)
        if reason:
            excluded.append({'channel': name, 'reason': reason})
            continue
        evidence = channel.time_evidence
        row = {'channel': name, 'count': channel.count, 'time_source': channel.time_source,
               'clock': channel.clock, 'source_row_mapping': 'identity',
               'time_evidence': evidence, 'establishes_acquisition_sync': False}
        raw = (getattr(topic, 'acquisition_timestamps_ns', None) if channel.time_source == 'acquisition'
               else getattr(topic, 'container_timestamps_ns', None) if channel.time_source == 'container' else None)
        ts = channel.timestamps
        if ts is None or len(ts) < 2 or channel.time_source not in {'container', 'acquisition'}:
            row.update(status='missing_input', reason=evidence.get('unavailable_reason') or 'No recorded timeline with >=2 rows')
        elif len(ts) != channel.count or not np.isfinite(ts).all():
            row.update(status='missing_input', reason='Timestamp count mismatch or nonfinite values; no rows dropped')
        else:
            # Subtract exact integer nanoseconds before conversion, avoiding epoch cancellation.
            stored = getattr(topic, 'recorded_timestamp_values', None)
            unit = getattr(topic, 'recorded_timestamp_unit', None)
            if raw is not None and len(raw) == len(ts):
                dt = np.diff(np.asarray(raw, dtype=np.int64)).astype(float) * 1e-9
            elif stored is not None and len(stored) == len(ts) and unit in {'s', 'ms', 'us', 'ns'}:
                dt = np.diff(np.asarray(stored)).astype(float) * {'s': 1, 'ms': 1e-3, 'us': 1e-6, 'ns': 1e-9}[unit]
            else:
                dt = np.diff(np.asarray(ts, dtype=float))
            positive = dt[dt > 0]
            largest = int(np.argmax(dt))
            row.update(status='observed', interval_count=len(dt),
                       zero_interval_count=int(np.sum(dt == 0)),
                       backward_interval_count=int(np.sum(dt < 0)),
                       min_interval_s=float(np.min(dt)), max_interval_s=float(np.max(dt)),
                       median_positive_interval_s=float(np.median(positive)) if positive.size else None,
                       largest_interval={'previous_row': largest, 'current_row': largest + 1,
                                         'delta_s': float(dt[largest])},
                       anomaly_examples=[{'previous_row': int(i), 'current_row': int(i + 1),
                                          'delta_s': float(dt[i])}
                                         for i in np.flatnonzero(dt <= 0)[:100]])
        rows.append(row)
    return {'channels': rows, 'excluded': excluded, 'score': None,
            'scope': 'Independent original row order and native frequency; no alignment/resampling',
            'establishes_acquisition_sync': False}
