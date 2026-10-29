"""topic 语义解析（统一收敛：此前 structure/sync/root_cause 三处各写一套且规则不一致）。

canonical 实现（取各处最完整版本）：
- modal_of: sync 的对齐灵敏度模态判定
- sensor_family: root_cause 的传感器族（同相机同流算一个参与方）
- is_image_topic / camera_prefix / infer_camera_info_topic: structure 的相机语义
  （aligned_depth / images 层级 / sensor/camera 结构等完整启发式）
"""
import re
from typing import Optional


def modal_of(topic: str) -> str:
    """通道名 -> 模态（camera/depth/imu/lidar/joint/cmd/other），供对齐灵敏度阈值选择。"""
    t = (topic or "").lower()
    if "lidar" in t:
        return "lidar"
    if "depth" in t:
        return "depth"
    if "imu" in t:
        return "imu"
    if "joint" in t or "state" in t:
        return "joint"
    if "cmd" in t or "command" in t or "action" in t or "traj" in t or "hand" in t:
        return "cmd"
    if "camera" in t or "cam_" in t or "image" in t or "compressed" in t:
        return "camera"
    return "other"


def sensor_family(topic: str) -> str:
    """传感器族：同相机同流的不同 topic（camera_info/metadata/image）视为一个参与方，
    避免同一相机的 3 个通道巧合缺帧被误判为系统级事件。"""
    parts = topic.strip("/").split("/")
    if len(parts) >= 2 and parts[0].startswith("cam"):
        # 相机级流族：/cam_X/color|depth（camera_info/metadata/image 同流算一个参与方）
        return "/" + "/".join(parts[:2])
    return topic


def is_image_topic(topic_name: str) -> bool:
    """判断是否为图像数据通道（排除 camera_info/metadata 等元数据通道）。"""
    lower = topic_name.lower()
    if "image" in lower and ("raw" in lower or "compressed" in lower):
        return True
    # /images/<cam>/<color|depth> 结构（如 luoshi hdf5）
    if "/images/" in lower and (lower.endswith("/color") or lower.endswith("/depth")):
        return True
    # 其他含 camera/image 且以图像模态结尾的路径
    if ("image" in lower or "camera" in lower or "cam_" in lower) and \
            lower.endswith(("/image_raw", "/compressed", "/color", "/depth")):
        return True
    return False


def camera_prefix(topic_name: str) -> Optional[str]:
    """提取相机前缀（cam_l/cam_r/cam_h 或 images 结构下的相机路径）。

    /cam_r/color/image_raw/compressed → cam_r
    /observation/images/rs/cam_high/color → rs/cam_high
    /left_arm/manip_t/sensor/camera/color → left_arm/manip_t/sensor/camera
    """
    parts = topic_name.split('/')
    if len(parts) >= 2 and re.match(r'^cam_[a-zA-Z0-9]+$', parts[1]):
        return parts[1]
    # images 层级：camera = 前两级
    for i, part in enumerate(parts):
        if part == 'images' and i + 2 < len(parts):
            return '/'.join(parts[i + 1:i + 3])
    if len(parts) >= 2 and 'camera' in parts[1].lower():
        return parts[1]
    # 命名空间中的 /sensor/camera/ 结构：camera = 到 camera 段为止
    for i, part in enumerate(parts):
        if part == 'camera' and 1 <= i < len(parts) - 1:
            return '/'.join(parts[1:i + 1])
    return None


def infer_camera_info_topic(image_topic: str) -> Optional[str]:
    """从 image topic 名推断 camera_info topic 名。

    /cam_r/color/image_raw/compressed → /cam_r/color/camera_info
    /camera1/aligned_depth_to_color/image_raw/compressed → /camera1/color/camera_info
    /observation/images/rs/cam_high/color → /observation/images/rs/cam_high/camera_info
    """
    parts = image_topic.split('/')

    # aligned_depth_to_color / aligned_depth_to_infra 共享 color/infra 相机的 camera_info
    if 'aligned_depth_to_color' in image_topic:
        for i, part in enumerate(parts):
            if part == 'aligned_depth_to_color':
                namespace = '/'.join(parts[:i])
                if namespace:
                    return f"{namespace}/color/camera_info"
                return None
    elif 'aligned_depth_to_infra' in image_topic:
        for i, part in enumerate(parts):
            if part == 'aligned_depth_to_infra':
                namespace = '/'.join(parts[:i])
                if namespace:
                    return f"{namespace}/infra/camera_info"
                return None

    # /images/<group>/<cam>/<color|depth> → <group>/<cam>/camera_info
    for i, part in enumerate(parts):
        if part == 'images':
            for k in range(i + 1, len(parts)):
                if parts[k] in ('color', 'depth', 'image', 'rgb'):
                    namespace = '/'.join(parts[:k])
                    return f"{namespace}/camera_info"
            break

    # 标准命名空间推断
    for i, part in enumerate(parts):
        if 'image' in part.lower() or 'compressed' in part.lower():
            namespace = '/'.join(parts[:i])
            if namespace:
                return f"{namespace}/camera_info"
            return None

    # 没找到 image 关键词时尝试 "camera" 层级
    for i, part in enumerate(parts):
        if 'camera' in part.lower() and i > 0:
            namespace = '/'.join(parts[:i])
            return f"{namespace}/camera_info"

    return None
