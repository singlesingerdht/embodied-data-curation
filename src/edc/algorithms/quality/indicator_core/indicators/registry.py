"""质检指标注册表（Java 风格枚举：成员即对象，携带多属性）。

权威源：《数据筛选问题总表》（50 项）。本枚举是代码内唯一权威：
代码中所有指标 code / 中文名 / 模态 / 分组 / 阶段的引用一律从本枚举取值，
禁止字面量。输出 JSON 的 indicators 每条必含 code 字段（值 = 枚举成员名）。

- Stage.FAST  初筛：不需要解包（xlsx「归属阶段=初筛」）
- Stage.DEEP  精筛：需要解包（xlsx「归属阶段=精筛」），包含 FAST 全部指标
- implemented=False 的成员是显式标记的缺口（激光 L 族 / M4 / M9），不参与检测。
"""
from enum import Enum, unique


@unique
class Stage(Enum):
    FAST = "fast"
    DEEP = "deep"


@unique
class Modality(Enum):
    CAMERA = "camera"
    IMU = "imu"
    LIDAR = "lidar"
    JOINT = "joint"
    CMD = "cmd"
    POSE = "pose"
    MULTIMODAL = "multimodal"


@unique
class Group(Enum):
    TIMING = "timing"
    SYNC = "sync"
    STRUCTURE = "structure"
    DEDUP = "dedup"
    CONTENT = "content"


@unique
class Indicator(Enum):
    """(name_zh, modality, group, stage, desc, implemented)"""

    # ---- 相机 C ----
    C1 = ("掉帧/缺帧", Modality.CAMERA, Group.TIMING, Stage.FAST, "dt > 2×期望周期", True)
    C2 = ("抖动/帧率不稳", Modality.CAMERA, Group.TIMING, Stage.FAST, "CV(dt)>0.3 或 P95>1.5×T", True)
    C3 = ("乱序", Modality.CAMERA, Group.TIMING, Stage.FAST, "seq/时间戳倒退", True)
    C4 = ("重复消息/帧", Modality.CAMERA, Group.DEDUP, Stage.FAST, "重复率>1%/5%", True)
    C5 = ("黑屏/白屏/花屏/遮挡", Modality.CAMERA, Group.CONTENT, Stage.DEEP, "像素统计/压缩域特征", True)
    C6 = ("分辨率/编码/色彩空间突变", Modality.CAMERA, Group.CONTENT, Stage.FAST, "压缩域头解析", True)
    C7 = ("缺少/不匹配 camera_info", Modality.CAMERA, Group.STRUCTURE, Stage.FAST, "通道配对", True)
    C8 = ("图像时间戳口径问题", Modality.CAMERA, Group.TIMING, Stage.FAST, "stamp 与 receipt 差", True)
    C9 = ("图像质量退化（模糊/马赛克）", Modality.CAMERA, Group.CONTENT, Stage.DEEP, "层1压缩域+层2清晰度", True)
    C10 = ("图像内容冻结", Modality.CAMERA, Group.CONTENT, Stage.DEEP, "压缩域指纹", True)
    # ---- IMU I ----
    I1 = ("掉帧/缺帧", Modality.IMU, Group.TIMING, Stage.FAST, "同 C1", True)
    I2 = ("抖动/帧率不稳", Modality.IMU, Group.TIMING, Stage.FAST, "同 C2", True)
    I3 = ("乱序", Modality.IMU, Group.TIMING, Stage.FAST, "同 C3", True)
    I4 = ("重复消息", Modality.IMU, Group.DEDUP, Stage.FAST, "同 C4", True)
    I5 = ("NaN/Inf/爆表", Modality.IMU, Group.CONTENT, Stage.DEEP, "数值反序列化", True)
    I6 = ("偏置/噪声异常", Modality.IMU, Group.CONTENT, Stage.DEEP, "静止段统计", True)
    I7 = ("IMU 数据冻结", Modality.IMU, Group.CONTENT, Stage.DEEP, "值指纹连续重复", True)
    # ---- 激光 L（未实现，显式缺口） ----
    L1 = ("掉帧/缺帧（少圈）", Modality.LIDAR, Group.TIMING, Stage.FAST, "未实现", False)
    L2 = ("抖动/帧率不稳", Modality.LIDAR, Group.TIMING, Stage.FAST, "未实现", False)
    L3 = ("乱序", Modality.LIDAR, Group.TIMING, Stage.FAST, "未实现", False)
    L4 = ("重复消息", Modality.LIDAR, Group.DEDUP, Stage.FAST, "未实现", False)
    L5 = ("点云为空/稀疏", Modality.LIDAR, Group.CONTENT, Stage.DEEP, "未实现", False)
    L6 = ("圈结构异常", Modality.LIDAR, Group.CONTENT, Stage.DEEP, "未实现", False)
    L7 = ("环境噪声异常", Modality.LIDAR, Group.CONTENT, Stage.DEEP, "未实现", False)
    L8 = ("激光数据冻结", Modality.LIDAR, Group.CONTENT, Stage.DEEP, "未实现", False)
    # ---- 关节 J ----
    J1 = ("掉帧/缺帧", Modality.JOINT, Group.TIMING, Stage.FAST, "同 C1", True)
    J2 = ("抖动/帧率不稳", Modality.JOINT, Group.TIMING, Stage.FAST, "同 C2", True)
    J3 = ("乱序", Modality.JOINT, Group.TIMING, Stage.FAST, "同 C3", True)
    J4 = ("重复消息", Modality.JOINT, Group.DEDUP, Stage.FAST, "同 C4", True)
    J5 = ("NaN/Inf/越界/跳变", Modality.JOINT, Group.CONTENT, Stage.DEEP, "数值反序列化", True)
    J6 = ("单位/坐标约定错误", Modality.JOINT, Group.STRUCTURE, Stage.FAST, "schema 尺度检查", True)
    J7 = ("关节数据冻结", Modality.JOINT, Group.CONTENT, Stage.DEEP, "值指纹连续重复", True)
    # ---- 控制命令 A ----
    A1 = ("掉帧/缺帧", Modality.CMD, Group.TIMING, Stage.FAST, "同 C1", True)
    A2 = ("抖动/帧率不稳", Modality.CMD, Group.TIMING, Stage.FAST, "同 C2", True)
    A3 = ("乱序", Modality.CMD, Group.TIMING, Stage.FAST, "同 C3", True)
    A4 = ("重复消息", Modality.CMD, Group.DEDUP, Stage.FAST, "同 C4", True)
    A5 = ("幅值越界/抖动过强", Modality.CMD, Group.CONTENT, Stage.DEEP, "jerk/限位", True)
    A6 = ("单位/模式切换未记录", Modality.CMD, Group.STRUCTURE, Stage.FAST, "schema 检查", True)
    A7 = ("控制命令冻结", Modality.CMD, Group.CONTENT, Stage.DEEP, "值指纹连续重复", True)
    # ---- 位姿 P ----
    P1 = ("掉帧/缺帧", Modality.POSE, Group.TIMING, Stage.FAST, "同 C1", True)
    P2 = ("抖动/帧率不稳", Modality.POSE, Group.TIMING, Stage.FAST, "同 C2", True)
    P3 = ("乱序", Modality.POSE, Group.TIMING, Stage.FAST, "同 C3", True)
    P4 = ("重复消息", Modality.POSE, Group.DEDUP, Stage.FAST, "同 C4", True)
    P5 = ("NaN/Inf/越界/抖动/复位", Modality.POSE, Group.CONTENT, Stage.DEEP, "数值反序列化+SG残差jerk+首末帧复位", True)
    P6 = ("单位/坐标约定错误", Modality.POSE, Group.STRUCTURE, Stage.FAST, "schema 尺度检查", False)
    P7 = ("位姿数据冻结", Modality.POSE, Group.CONTENT, Stage.DEEP, "SLAM式lookback冻结验证", True)
    # ---- 多模态 M ----
    M1 = ("多模态对不齐", Modality.MULTIMODAL, Group.SYNC, Stage.FAST, "对齐 P95 分级", True)
    M2 = ("晚启动/早停止", Modality.MULTIMODAL, Group.SYNC, Stage.FAST, "覆盖率/首尾差", True)
    M3 = ("话题缺失", Modality.MULTIMODAL, Group.STRUCTURE, Stage.FAST, "required_channels", True)
    M4 = ("控制命令与观测错位", Modality.MULTIMODAL, Group.SYNC, Stage.FAST, "未实现", False)
    M5 = ("延迟异常", Modality.MULTIMODAL, Group.TIMING, Stage.FAST, "P99/尖峰", True)
    M6 = ("时间戳漂移/时钟跳变", Modality.MULTIMODAL, Group.SYNC, Stage.FAST, "Δ(t) 线性拟合/电平跳变", True)
    M7 = ("字段缺失/类型/单位变化", Modality.MULTIMODAL, Group.STRUCTURE, Stage.DEEP, "schema 采样对比", True)
    M8 = ("文件损坏", Modality.MULTIMODAL, Group.STRUCTURE, Stage.FAST, "可读性/计数一致", True)
    M9 = ("TF/外参链缺失", Modality.MULTIMODAL, Group.STRUCTURE, Stage.FAST, "未实现", False)
    M10 = ("episode 一致性问题", Modality.MULTIMODAL, Group.SYNC, Stage.FAST, "多 topic 同窗口异常聚合", True)

    def __init__(self, name_zh, modality, group, stage, desc, implemented):
        self.name_zh = name_zh
        self.modality = modality
        self.group = group
        self.stage = stage
        self.desc = desc
        self.implemented = implemented

    @property
    def code(self) -> str:
        return self.name

    @classmethod
    def from_code(cls, code: str) -> "Indicator":
        return cls[code.upper()]


# ---- 族：同一算法跨模态（显式表，不依赖显示名匹配） ----
# drop 族 = C1/I1/J1/A1（+ 未实现 L1）；jitter/out_of_order/duplicate 同理。
_FAMILY_SEMANTIC: dict = {
    "drop": ("C1", "I1", "J1", "A1", "L1", "P1"),
    "jitter": ("C2", "I2", "J2", "A2", "L2", "P2"),
    "out_of_order": ("C3", "I3", "J3", "A3", "L3", "P3"),
    "duplicate": ("C4", "I4", "J4", "A4", "L4", "P4"),
}
# 语义名 → 族键（按 name_zh 查族时的别名；显示名在表内并不完全一致，如
# C4="重复消息/帧" 而 A4="重复消息"，因此族匹配一律走 _FAMILY_SEMANTIC）
_NAME_TO_SEMANTIC: dict = {}
for _sem, _codes in _FAMILY_SEMANTIC.items():
    for _c in _codes:
        _NAME_TO_SEMANTIC.setdefault(Indicator[_c].name_zh, _sem)


def family(modality: Modality, semantic: str) -> "Indicator | None":
    """按模态 + 族语义（drop/jitter/...或任一族的 name_zh）取族成员。

    family(Modality.CAMERA, "drop") -> C1；family(Modality.IMU, "掉帧/缺帧") -> I1。
    """
    sem = _NAME_TO_SEMANTIC.get(semantic, semantic)
    for code in _FAMILY_SEMANTIC.get(sem, ()):
        ind = Indicator[code]
        if ind.modality is modality and ind.implemented:
            return ind
    return None


def timing_code(modality: Modality, semantic: str) -> str:
    """时序/去重族按通道模态落到具体 code（drop/jitter/out_of_order/duplicate）。"""
    ind = family(modality, semantic)
    return ind.code if ind else semantic.upper()


# 输出事件类型 -> 指标 code 的映射（content 层；报告组装用）
EVENT_TYPE_TO_CODE: dict = {
    "pixel_black": "C5", "pixel_white": "C5", "pixel_occlusion": "C5",
    "pixel_garbled": "C5", "pixel_overexpo": "C5", "pixel_underexpo": "C5",
    "pixel_color_anomaly": "C5", "image_collapse": "C5",
    "pixel_blur": "C9", "pixel_low_sharpness": "C9",
    "image_resolution_change": "C6", "image_encoding_change": "C6",
    "joint_nan": "J5", "joint_bounds": "J5", "joint_jump": "J5",
    "joint_reset": "J5", "joint_velocity": "J5",
    "cmd_nan": "A5", "cmd_bounds": "A5", "cmd_jerk": "A5",
    "imu_nan": "I5", "imu_bounds": "I5", "bias_suspect": "I6",
    "pose_nan": "P5", "pose_jerk": "P5", "pose_reset": "P5",
}

# 冻结事件按检查名前缀 -> code
FREEZE_PREFIX_TO_CODE: dict = {
    "content_imu": "I7", "content_joint": "J7",
    "content_cmd": "A7", "content_image": "C10",
    "content_pose": "P7",
}
