"""Recorded ROS clocks with identity row mapping; no guessing or alignment."""


def declared_timestamp_unit(value):
    """Normalize unconfirmed declarations without inferring units from values."""
    if value is None or value in ('', 'unknown'):
        return 'unknown'
    if value not in ('s', 'ms', 'us', 'ns'):
        raise ValueError(f'Unsupported timestamp_unit {value!r}; declare s/ms/us/ns or unknown')
    return value


def header_nanoseconds(message):
    stamp = getattr(getattr(message, 'header', None), 'stamp', None)
    # foxglove 消息（如 CompressedVideo）没有 header，采集时间直接写在 timestamp 字段
    stamp = stamp if stamp is not None else getattr(message, 'timestamp', None)
    if not hasattr(stamp, 'nanosec'):  # 自定义消息里普通数字的 timestamp 不算
        return None
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def set_recorded_timelines(topic, container_ns, header_ns, container_field='log_time'):
    topic.container_timestamps_ns = list(container_ns)
    topic.acquisition_timestamps_ns = list(header_ns)
    missing = [i for i, t in enumerate(header_ns) if t is None]
    zeros = [i for i, t in enumerate(header_ns) if t == 0]
    usable = bool(header_ns) and not missing and not zeros and len(header_ns) == topic.count
    topic.acquisition_timestamps = ([t * 1e-9 for t in header_ns] if usable else None)
    differences = [(i, b-a) for i, (a, b) in enumerate(zip(header_ns, header_ns[1:]))
                   if a is not None and b is not None]
    topic.time_evidence = {
        'container_field': container_field, 'acquisition_field': 'header.stamp',
        'raw_timestamp_unit': 'ns', 'algorithm_timestamp_unit': 's',
        'source_row_mapping': 'identity; no sorting, dropping, or resampling',
        'header_available': usable, 'header_count': len(header_ns),
        'missing_header_count': len(missing), 'zero_header_count': len(zeros),
        'missing_header_rows': missing[:100], 'zero_header_rows': zeros[:100],
        'backward_intervals': sum(d < 0 for _, d in differences),
        'equal_intervals': sum(d == 0 for _, d in differences),
        'header_clock_confirmed': False,
        'unavailable_reason': None if usable else 'Missing/zero header.stamp; no container fallback',
    }
