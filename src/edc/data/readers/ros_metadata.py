"""Recorded Odometry frame references; no frame transforms or calibration claims."""


def record_odometry_frames(topic, message, row):
    if topic.msg_type not in {'nav_msgs/msg/Odometry', 'nav_msgs/Odometry'}:
        return
    if topic.frame_evidence is None:
        topic.frame_evidence = {
            'message_type': topic.msg_type,
            'field_references': {
                'position': 'header.frame_id', 'orientation': 'header.frame_id',
                'pose_covariance': 'header.frame_id', 'lin_vel': 'child_frame_id',
                'ang_vel': 'child_frame_id', 'twist_covariance': 'child_frame_id'},
            'segments': [], 'coordinate_transform_applied': False,
            'calibration_confirmed': False,
        }
    header = getattr(getattr(message, 'header', None), 'frame_id', None)
    child = getattr(message, 'child_frame_id', None)
    frames = {'header.frame_id': header, 'child_frame_id': child}
    segments = topic.frame_evidence['segments']
    if segments and segments[-1]['frames'] == frames and segments[-1]['stop_row'] == row:
        segments[-1]['stop_row'] = row + 1
    else:
        segments.append({'start_row': row, 'stop_row': row + 1, 'frames': frames})
