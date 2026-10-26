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


def partition_timing_topics(info, timestamps):
    selected, excluded = {}, []
    for name, values in sorted(timestamps.items()):
        topic = info.topics.get(name)
        reason = timing_exclusion_reason(name, topic)
        if reason:
            excluded.append({'topic': name, 'reason': reason, 'sample_count': len(values),
                             'included_in_score': False})
        else:
            selected[name] = values
    return selected, excluded
