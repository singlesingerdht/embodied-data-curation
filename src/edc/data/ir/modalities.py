"""Data modality vocabulary, independent of anatomy, device and command/feedback."""
import re

MODALITIES = {
    'rgb': ('彩色图像', 'image'),
    'depth': ('深度图', 'tensor'),
    'infrared': ('红外图像', 'tensor'),
    'thermal': ('热成像', 'tensor'),
    'imu': ('惯性测量', 'numeric'),
    'joint_space': ('关节空间', 'numeric'),
    'pose': ('空间位姿', 'numeric'),
    'twist': ('空间线速度与角速度', 'numeric'),
    'wrench': ('力与力矩', 'numeric'),
    'tactile': ('触觉', 'tensor'),
    'contact': ('接触事件/状态', 'numeric'),
    'point_cloud': ('点云', 'tensor'),
    'range_scan': ('距离扫描', 'tensor'),
    'event_camera': ('事件相机事件流', 'events'),
    'audio': ('音频', 'tensor'),
    'odometry': ('里程计', 'numeric'),
    'gnss': ('卫星定位', 'numeric'),
    'system_status': ('设备状态/电池/温度等', 'numeric'),
    'task_text': ('任务文本', 'text'),
    'annotation': ('标注/标签', 'records'),
    'other': ('未分类', 'unknown'),
}
ALIASES = {'image':'rgb', 'joint_state':'joint_space', 'cmd':'joint_space'}
NUMERIC_MODALITIES = {name for name, (_, kind) in MODALITIES.items() if kind in {'numeric','tensor'}}


def normalize_modality(value):
    value = ALIASES.get(value, value)
    if not isinstance(value, str) or (value not in MODALITIES and not re.fullmatch(r'[a-z][a-z0-9_]*__[a-z][a-z0-9_]*', value)):
        raise ValueError(f'Unknown modality {value!r}; use a registered name or team__custom_name')
    return value


def legacy_modality(value, source_kind='unknown'):
    value=normalize_modality(value)
    if value=='rgb': return 'image'
    if value=='joint_space': return 'cmd' if source_kind=='command' else 'joint_state'
    return value
