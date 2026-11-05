"""Timing scores apply to sampled streams, not static metadata or task text.

Do not infer that a short numeric/image stream is metadata: insufficient sensor
samples must remain visible to the timing algorithm.
"""


def timing_exclusion_reason(name, topic):
    modality = getattr(topic, 'modality', None)
    if modality in {'task_text', 'annotation'}:
        return 'non_sampled_metadata'
    leaf = name.strip('/').split('/')[-1].lower()
    msg_type = (getattr(topic, 'msg_type', '') or '').replace('/msg/', '/')
    if leaf in {'metadata', 'camera_info', 'tf_static'} or msg_type == 'sensor_msgs/CameraInfo':
        return 'static_metadata_topic'
    if msg_type == 'std_msgs/String' and getattr(topic, 'count', 0) <= 1:
        return 'non_sampled_text'
    return None


def recorded_timelines(ep, shift=None, drop=()):
    """时间类算法比较的各路时间轴 {通道: [秒]}，返回 (时间轴, 没参与的通道, 不能评估的原因)。

    依次：取各路有限的时间（不足两路 → 原因）；按 shift 平移时钟、去掉 drop 里的派生副本；
    去掉生成的时间（全是 → 原因）；metadata、文本等非采样通道列进“没参与”。有原因时时间轴为 None。"""
    import math
    from edc.data.ir.metadata import IRNotProvided
    shift = shift or {}
    ts_map = {}
    for name, c in ep.channels.items():
        if c.timestamps is not None:
            values = [float(t) for t in c.timestamps if math.isfinite(t)]
            if values:
                ts_map[name] = values
    if len(ts_map) < 2:
        return None, [], IRNotProvided('>=2 通道时间戳', ep.format, available_summary(ep))
    ts_map = {n: [t + shift[n] for t in v] if n in shift else v for n, v in ts_map.items() if n not in drop}
    ts_map = {n: v for n, v in ts_map.items() if not getattr(ep.raw(n), 'timestamps_synthetic', False)}
    if not ts_map:
        return None, [], IRNotProvided('合成时间戳 (i/fps, 全通道同轴)', ep.format,
                                       '时间类检测 (Sync/Dedup/Check_Timestamp) 仅在真实采集时间戳上有意义')
    selected, excluded = {}, []
    for name, values in sorted(ts_map.items()):
        reason = timing_exclusion_reason(name, ep.raw(name))
        if reason:
            excluded.append({'topic': name, 'reason': reason, 'sample_count': len(values),
                             'included_in_score': False})
        else:
            selected[name] = values
    return selected, excluded, None


def available_summary(ep):
    """这条数据各路能提供什么（数值、图像、时间戳…），写进“不能评估”的说明。"""
    if not ep.channels:
        return '无通道'
    return ' / '.join(sorted({ep.raw(n).capability_summary() for n in ep.channels}))
