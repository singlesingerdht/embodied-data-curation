"""ROS 消息手动反序列化模块

用于解析 rosbags 库无法自动反序列化的自定义消息类型（kuavo_msgs），
以及标准 sensor_msgs 消息的原始字节。

所有解析基于 rosbags AnyReader 返回的 raw bytes，不需要安装实际的 ROS 消息包。
"""

import struct
from typing import List, Dict, Any, Tuple, Optional
from dataclasses import dataclass

import numpy as np


# ======================================================================
# 数据结构
# ======================================================================

@dataclass
class Header:
    """std_msgs/Header"""
    seq: int            # 消息序号（ROS1 自增计数，可用于序号类检测）
    stamp_sec: int      # 时间戳秒部分
    stamp_nsec: int     # 时间戳纳秒部分
    frame_id: str       # 坐标系名称（如 base_link）


@dataclass
class Vector3:
    """geometry_msgs/Vector3"""
    x: float = 0.0    # x 分量（m 或 m/s² 视语境）
    y: float = 0.0    # y 分量
    z: float = 0.0    # z 分量


@dataclass
class Quaternion:
    """geometry_msgs/Quaternion"""
    x: float = 0.0    # 四元数 x 分量
    y: float = 0.0    # 四元数 y 分量
    z: float = 0.0    # 四元数 z 分量
    w: float = 1.0    # 四元数 w 分量（实部）


@dataclass
class Point:
    """geometry_msgs/Point"""
    x: float = 0.0    # x 分量（m）
    y: float = 0.0    # y 分量（m）
    z: float = 0.0    # z 分量（m）


@dataclass
class Pose:
    """geometry_msgs/Pose（position + orientation，识别码来自消息定义）"""
    position: Point = None        # 平移（m）
    orientation: Quaternion = None  # 姿态四元数


@dataclass
class PoseStamped:
    """geometry_msgs/PoseStamped"""
    header: Header            # 消息头（时间戳/序号）
    pose: Pose                # 位姿（平移 + 姿态）


@dataclass
class JointState:
    """sensor_msgs/JointState"""
    header: Header            # 消息头（时间戳/序号）
    name: List[str]           # 关节名称列表（与数值数组一一对应）
    position: List[float]     # 关节位置（rad）
    velocity: List[float]     # 关节速度（rad/s）
    effort: List[float]       # 关节力矩（N·m）


@dataclass
class Imu:
    """sensor_msgs/Imu"""
    header: Header                        # 消息头
    orientation: Quaternion               # 姿态四元数（相对 world）
    orientation_covariance: List[float]   # 姿态协方差（9 元素；全 -1 表示不可用）
    angular_velocity: Vector3             # 角速度（rad/s）
    angular_velocity_covariance: List[float]  # 角速度协方差（9 元素）
    linear_acceleration: Vector3          # 线加速度（m/s²，含重力分量）
    linear_acceleration_covariance: List[float]  # 线加速度协方差（9 元素）


@dataclass
class CameraInfo:
    """sensor_msgs/CameraInfo"""
    header: Header            # 消息头
    height: int               # 图像高度（像素）
    width: int                # 图像宽度（像素）
    distortion_model: str     # 畸变模型（plumb_bob / rational_polynomial）
    D: List[float]            # 畸变系数（无长度前缀的变长数组）
    K: List[float]            # 相机内参 3x3（float64[9]，无前缀）
    R: List[float]            # 矫正旋转 3x3（float64[9]，无前缀）
    P: List[float]            # 投影矩阵 3x4（float64[12]，无前缀）
    binning_x: int            # x 方向降采样因子
    binning_y: int            # y 方向降采样因子
    roi: Dict[str, int]       # ROI：{x_offset, y_offset, width, height, do_rectify}


@dataclass
class TransformStamped:
    """geometry_msgs/TransformStamped"""
    header: Header          # 消息头（stamp 为变换时间戳）
    child_frame_id: str     # 子坐标系名称
    translation: Vector3    # 平移（m）
    rotation: Quaternion    # 旋转四元数


@dataclass
class TFMessage:
    """tf2_msgs/TFMessage"""
    transforms: List[TransformStamped]  # 同一时刻的多个坐标系变换


@dataclass
class JointData:
    """kuavo_msgs/JointData"""
    joint_q: List[float]        # 关节位置（rad）
    joint_v: List[float]        # 关节速度（rad/s）
    joint_vd: List[float]       # 关节加速度（rad/s²）
    joint_torque: List[float]   # 关节力矩（N·m）


@dataclass
class ImuData:
    """kuavo_msgs/ImuData"""
    gyro: Vector3         # 陀螺仪角速度（rad/s）
    acc: Vector3          # 加速度计读数（m/s²，含重力）
    free_acc: Vector3     # 去除重力后的线性加速度（m/s²）
    quat: Quaternion      # 姿态四元数


@dataclass
class EndEffectorData:
    """kuavo_msgs/EndEffectorData"""
    name: List[str]            # 末端执行器名称列表
    position: List[float]      # 末端位置（m）
    velocity: List[float]      # 末端速度（m/s）
    effort: List[float]        # 末端力/力矩（N 或 N·m）


@dataclass
class FTsensorData:
    """kuavo_msgs/FTsensorData"""
    Fx: List[float]   # x 方向力（N）
    Fy: List[float]   # y 方向力（N）
    Fz: List[float]   # z 方向力（N）
    Mx: List[float]   # x 轴力矩（N·m）
    My: List[float]   # y 轴力矩（N·m）
    Mz: List[float]   # z 轴力矩（N·m）


@dataclass
class SensorsData:
    """kuavo_msgs/SensorsData"""
    header: Header                  # 消息头
    sensor_time: int                # 传感器原始时间（ns；低 32 位 | 高 32 位<<32 拼接）
    joint_data: JointData           # 关节状态数据
    imu_data: ImuData               # IMU 数据
    end_effector_data: EndEffectorData  # 末端执行器数据
    ft_sensor_data: FTsensorData    # 力/力矩传感器数据


@dataclass
class JointCmd:
    """kuavo_msgs/JointCmd"""
    header: Header                # 消息头
    joint_q: List[float]          # 关节位置指令（rad）
    joint_v: List[float]          # 关节速度指令（rad/s）
    tau: List[float]              # 力矩指令（N·m）
    tau_max: List[float]          # 最大力矩限制（N·m）
    tau_ratio: List[float]        # 力矩比例（tau/tau_max，0-1）
    joint_kp: List[float]         # 位置环增益
    joint_kd: List[float]         # 速度环增益
    control_modes: List[int]      # 关节控制模式（可能为空，表示未下发）


@dataclass
class RobotHandPosition:
    """kuavo_msgs/RobotHandPosition"""
    header: Header                  # 消息头
    left_hand_position: List[int]   # 左手各指位置（uint8 编码）
    right_hand_position: List[int]  # 右手各指位置（uint8 编码）


# ======================================================================
# 底层解析工具
# ======================================================================

class _Buffer:
    """字节缓冲区，提供顺序读取方法"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos

    def _need(self, n: int) -> None:
        """剩余字节不足时快速失败（防半截消息读到越界/超大分配）"""
        if n < 0 or self.remaining < n:
            raise ValueError(f"消息截断：还需 {n} 字节，剩余 {self.remaining}")

    def read_uint32(self) -> int:
        self._need(4)
        val = struct.unpack_from('<I', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_int32(self) -> int:
        self._need(4)
        val = struct.unpack_from('<i', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_float64(self) -> float:
        self._need(8)
        val = struct.unpack_from('<d', self._data, self._pos)[0]
        self._pos += 8
        return val

    def read_float64_array(self) -> List[float]:
        n = self.read_uint32()
        self._need(n * 8)   # 校验长度前缀与剩余字节匹配（防恶意 n 触发超大分配）
        vals = list(struct.unpack_from(f'<{n}d', self._data, self._pos))
        self._pos += n * 8
        return vals

    def read_int32_array(self) -> List[int]:
        n = self.read_uint32()
        self._need(n * 4)
        vals = list(struct.unpack_from(f'<{n}i', self._data, self._pos))
        self._pos += n * 4
        return vals

    def read_string(self) -> str:
        length = self.read_uint32()
        self._need(length)
        s = self._data[self._pos:self._pos + length].decode('utf-8', errors='replace')
        self._pos += length
        return s

    def read_string_array(self) -> List[str]:
        n = self.read_uint32()
        # 字符串数组按长度前缀逐个读取，各自有 _need 校验
        return [self.read_string() for _ in range(n)]

    def read_uint8_array(self) -> List[int]:
        n = self.read_uint32()
        self._need(n)
        vals = list(self._data[self._pos:self._pos + n])
        self._pos += n
        return vals

    def read_bytes(self) -> bytes:
        n = self.read_uint32()
        self._need(n)
        b = self._data[self._pos:self._pos + n]
        self._pos += n
        return b

    def head_tail(self, n: int, head_len: int = 4096, tail_len: int = 16) -> Tuple[bytes, bytes]:
        """返回长度 n 的字节块的头尾片段，不物化全部内容"""
        if n <= 0:
            return b"", b""
        head = self._data[self._pos:self._pos + min(head_len, n)]
        tail = self._data[self._pos + max(0, n - tail_len):self._pos + n]
        self._pos += n
        return head, tail

    def read_header(self) -> Header:
        seq = self.read_uint32()
        sec = self.read_uint32()
        nsec = self.read_uint32()
        frame_id = self.read_string()
        return Header(seq=seq, stamp_sec=sec, stamp_nsec=nsec, frame_id=frame_id)

    def read_vector3(self) -> Vector3:
        x = self.read_float64()
        y = self.read_float64()
        z = self.read_float64()
        return Vector3(x=x, y=y, z=z)

    def read_quaternion(self) -> Quaternion:
        x = self.read_float64()
        y = self.read_float64()
        z = self.read_float64()
        w = self.read_float64()
        return Quaternion(x=x, y=y, z=z, w=w)


# ======================================================================
# 标准消息解析
# ======================================================================

def parse_joint_state(raw: bytes) -> JointState:
    """解析 sensor_msgs/JointState"""
    buf = _Buffer(raw)
    header = buf.read_header()
    name = buf.read_string_array()
    position = buf.read_float64_array()
    velocity = buf.read_float64_array()
    effort = buf.read_float64_array()
    return JointState(
        header=header,
        name=name,
        position=position,
        velocity=velocity,
        effort=effort,
    )


def parse_pose_stamped(raw: bytes) -> PoseStamped:
    """解析 geometry_msgs/PoseStamped（header + pose{position, orientation}）"""
    buf = _Buffer(raw)
    header = buf.read_header()
    position = Point(x=buf.read_float64(), y=buf.read_float64(), z=buf.read_float64())
    orientation = buf.read_quaternion()
    return PoseStamped(header=header,
                       pose=Pose(position=position, orientation=orientation))


def parse_pose(raw: bytes) -> Pose:
    """解析 geometry_msgs/Pose（position + orientation，无 header）"""
    buf = _Buffer(raw)
    position = Point(x=buf.read_float64(), y=buf.read_float64(), z=buf.read_float64())
    orientation = buf.read_quaternion()
    return Pose(position=position, orientation=orientation)


def parse_imu(raw: bytes) -> Imu:
    """解析 sensor_msgs/Imu"""
    buf = _Buffer(raw)
    header = buf.read_header()
    orientation = buf.read_quaternion()
    orientation_covariance = [buf.read_float64() for _ in range(9)]
    angular_velocity = buf.read_vector3()
    angular_velocity_covariance = [buf.read_float64() for _ in range(9)]
    linear_acceleration = buf.read_vector3()
    linear_acceleration_covariance = [buf.read_float64() for _ in range(9)]
    return Imu(
        header=header,
        orientation=orientation,
        orientation_covariance=orientation_covariance,
        angular_velocity=angular_velocity,
        angular_velocity_covariance=angular_velocity_covariance,
        linear_acceleration=linear_acceleration,
        linear_acceleration_covariance=linear_acceleration_covariance,
    )


def parse_camera_info(raw: bytes) -> CameraInfo:
    """解析 sensor_msgs/CameraInfo

    注意: K/R/P 是固定长度数组，没有长度前缀。
    K: float64[9] (3x3)
    R: float64[9] (3x3)
    P: float64[12] (3x4)
    """
    buf = _Buffer(raw)
    header = buf.read_header()
    height = buf.read_uint32()
    width = buf.read_uint32()
    distortion_model = buf.read_string()
    D = buf.read_float64_array()
    # K, R, P 是固定长度数组，无前缀
    K = list(struct.unpack_from('<9d', buf._data, buf._pos))
    buf._pos += 72
    R = list(struct.unpack_from('<9d', buf._data, buf._pos))
    buf._pos += 72
    P = list(struct.unpack_from('<12d', buf._data, buf._pos))
    buf._pos += 96
    binning_x = buf.read_uint32()
    binning_y = buf.read_uint32()
    roi = {
        'x_offset': buf.read_uint32(),
        'y_offset': buf.read_uint32(),
        'width': buf.read_uint32(),
        'height': buf.read_uint32(),
        'do_rectify': buf._data[buf._pos] if buf.remaining >= 1 else 0,
    }
    return CameraInfo(
        header=header,
        height=height,
        width=width,
        distortion_model=distortion_model,
        D=D,
        K=K,
        R=R,
        P=P,
        binning_x=binning_x,
        binning_y=binning_y,
        roi=roi,
    )


def parse_tf_message(raw: bytes) -> TFMessage:
    """解析 tf2_msgs/TFMessage"""
    buf = _Buffer(raw)
    n = buf.read_uint32()
    transforms = []
    for _ in range(n):
        header = buf.read_header()
        child_frame_id = buf.read_string()
        translation = buf.read_vector3()
        rotation = buf.read_quaternion()
        transforms.append(TransformStamped(
            header=header,
            child_frame_id=child_frame_id,
            translation=translation,
            rotation=rotation,
        ))
    return TFMessage(transforms=transforms)


# ======================================================================
# 自定义消息解析（kuavo_msgs）
# ======================================================================

def parse_joint_data(buf: _Buffer) -> JointData:
    """解析 kuavo_msgs/JointData"""
    return JointData(
        joint_q=buf.read_float64_array(),
        joint_v=buf.read_float64_array(),
        joint_vd=buf.read_float64_array(),
        joint_torque=buf.read_float64_array(),
    )


def parse_imu_data(buf: _Buffer) -> ImuData:
    """解析 kuavo_msgs/ImuData"""
    return ImuData(
        gyro=buf.read_vector3(),
        acc=buf.read_vector3(),
        free_acc=buf.read_vector3(),
        quat=buf.read_quaternion(),
    )


def parse_end_effector_data(buf: _Buffer) -> EndEffectorData:
    """解析 kuavo_msgs/EndEffectorData"""
    return EndEffectorData(
        name=buf.read_string_array(),
        position=buf.read_float64_array(),
        velocity=buf.read_float64_array(),
        effort=buf.read_float64_array(),
    )


def parse_ft_sensor_data(buf: _Buffer) -> FTsensorData:
    """解析 kuavo_msgs/FTsensorData"""
    return FTsensorData(
        Fx=buf.read_float64_array(),
        Fy=buf.read_float64_array(),
        Fz=buf.read_float64_array(),
        Mx=buf.read_float64_array(),
        My=buf.read_float64_array(),
        Mz=buf.read_float64_array(),
    )


def parse_sensors_data(raw: bytes) -> SensorsData:
    """解析 kuavo_msgs/SensorsData"""
    buf = _Buffer(raw)
    header = buf.read_header()
    sensor_time = buf.read_uint32() | (buf.read_uint32() << 32)
    joint_data = parse_joint_data(buf)
    imu_data = parse_imu_data(buf)
    end_effector_data = parse_end_effector_data(buf)
    ft_sensor_data = parse_ft_sensor_data(buf)
    return SensorsData(
        header=header,
        sensor_time=sensor_time,
        joint_data=joint_data,
        imu_data=imu_data,
        end_effector_data=end_effector_data,
        ft_sensor_data=ft_sensor_data,
    )


def parse_joint_cmd(raw: bytes) -> JointCmd:
    """解析 kuavo_msgs/JointCmd

    消息布局：header(16字节) + 7组 float64[]（每组28个关节）
    """
    buf = _Buffer(raw)
    header = buf.read_header()
    joint_q = buf.read_float64_array()
    joint_v = buf.read_float64_array()
    tau = buf.read_float64_array()
    tau_max = buf.read_float64_array()
    tau_ratio = buf.read_float64_array()
    joint_kp = buf.read_float64_array()
    joint_kd = buf.read_float64_array()
    control_modes = buf.read_int32_array() if buf.remaining >= 4 else []
    return JointCmd(
        header=header,
        joint_q=joint_q,
        joint_v=joint_v,
        tau=tau,
        tau_max=tau_max,
        tau_ratio=tau_ratio,
        joint_kp=joint_kp,
        joint_kd=joint_kd,
        control_modes=control_modes,
    )


def parse_robot_hand_position(raw: bytes) -> RobotHandPosition:
    """解析 kuavo_msgs/RobotHandPosition"""
    buf = _Buffer(raw)
    header = buf.read_header()
    left_hand_position = buf.read_uint8_array()
    right_hand_position = buf.read_uint8_array()
    return RobotHandPosition(
        header=header,
        left_hand_position=left_hand_position,
        right_hand_position=right_hand_position,
    )


# ======================================================================
# 图像消息（只取数据长度 + 头尾字节，不做像素解码）
# ======================================================================

def compressed_image_head_tail(raw: bytes) -> Tuple[int, bytes, bytes]:
    """解析 CompressedImage/CompressedDepth 的数据字段，返回 (size, head, tail)

    CompressedImage 与 CompressedDepth 线格式一致：header + format + data(len 前缀)。
    """
    buf = _Buffer(raw)
    buf.read_header()
    buf.read_string()  # format
    n = buf.read_uint32()
    head, tail = buf.head_tail(n)
    return n, head, tail


def raw_image_head_tail(raw: bytes) -> Tuple[int, bytes, bytes]:
    """解析 sensor_msgs/Image 的 data 字段，返回 (size, head, tail)

    原始图像帧较大（如 640x480 bgr8 ≈ 900KB），不解包整帧，只取长度与头尾。
    """
    buf = _Buffer(raw)
    buf.read_header()
    buf.read_uint32()  # height
    buf.read_uint32()  # width
    buf.read_string()  # encoding
    buf._pos += 1      # is_bigendian
    buf.read_uint32()  # step
    n = buf.read_uint32()
    head, tail = buf.head_tail(n)
    return n, head, tail


def compressed_image_bytes(raw: bytes) -> bytes:
    """取 CompressedImage/CompressedDepth 的完整 data 字节（第二层解码用）"""
    buf = _Buffer(raw)
    buf.read_header()
    buf.read_string()  # format
    return buf.read_bytes()


def raw_image_bytes(raw: bytes) -> bytes:
    """取 sensor_msgs/Image 的完整 data 字节（第二层解码用）"""
    buf = _Buffer(raw)
    buf.read_header()
    buf.read_uint32()
    buf.read_uint32()
    buf.read_string()
    buf._pos += 1
    buf.read_uint32()
    return buf.read_bytes()


def raw_image_arr(raw: bytes) -> Optional[np.ndarray]:
    """sensor_msgs/Image 原始像素 → numpy 数组（仿 mcap._rawimage_to_arr）

    处理 step 行填充，支持 bgr8（→RGB 翻转）/rgb8/mono8。
    不认识的 encoding 返回 None。
    """
    buf = _Buffer(raw)
    buf.read_header()
    h = buf.read_uint32()
    w = buf.read_uint32()
    enc = buf.read_string().lower()
    buf._pos += 1  # is_bigendian
    step = buf.read_uint32()
    data = buf.read_bytes()
    if not data or h == 0 or w == 0:
        return None
    try:
        if enc in ("bgr8", "rgb8", "rgba8"):
            bpp = 4 if enc == "rgba8" else 3
            arr = np.frombuffer(data, dtype=np.uint8)
            if len(arr) < step * h:
                return None
            arr = arr.reshape(h, step)[:, :w * bpp].reshape(h, w, bpp)[:, :, :3]
            if enc == "bgr8":
                arr = arr[:, :, ::-1]
            return arr.copy()
        if enc == "mono8":
            arr = np.frombuffer(data, dtype=np.uint8)
            if len(arr) < step * h:
                return None
            return arr.reshape(h, step)[:, :w].copy()
        return None
    except Exception:
        return None


# ======================================================================
# 统一入口
# ======================================================================

# 消息类型 → 解析函数映射
PARSERS = {
    'sensor_msgs/msg/JointState': parse_joint_state,
    'sensor_msgs/msg/Imu': parse_imu,
    'sensor_msgs/msg/CameraInfo': parse_camera_info,
    'geometry_msgs/msg/PoseStamped': parse_pose_stamped,
    'geometry_msgs/PoseStamped': parse_pose_stamped,
    'geometry_msgs/msg/Pose': parse_pose,
    'geometry_msgs/Pose': parse_pose,
    'tf2_msgs/msg/TFMessage': parse_tf_message,
    'kuavo_msgs/msg/SensorsData': parse_sensors_data,
    'kuavo_msgs/msg/sensorsData': parse_sensors_data,
    'kuavo_msgs/msg/JointCmd': parse_joint_cmd,
    'kuavo_msgs/msg/jointCmd': parse_joint_cmd,
    'kuavo_msgs/msg/RobotHandPosition': parse_robot_hand_position,
    'kuavo_msgs/msg/robotHandPosition': parse_robot_hand_position,
    'sensor_msgs/msg/CompressedImage': compressed_image_head_tail,
    'sensor_msgs/CompressedImage': compressed_image_head_tail,
    'sensor_msgs/msg/CompressedDepth': compressed_image_head_tail,
    'sensor_msgs/CompressedDepth': compressed_image_head_tail,
    'sensor_msgs/msg/Image': raw_image_head_tail,
    'sensor_msgs/Image': raw_image_head_tail,
}


def parse_message(raw: bytes, msg_type: str) -> Any:
    """
    统一消息解析入口

    Args:
        raw: 原始字节
        msg_type: 消息类型字符串（如 'sensor_msgs/msg/JointState'）

    Returns:
        解析后的数据对象，若不支持该类型则返回 None
    """
    parser = PARSERS.get(msg_type)
    if parser:
        return parser(raw)
    return None
