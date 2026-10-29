"""内容检测数据模型（事件/检查项/结果/配置）"""
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Any

@dataclass
class SamplePoint:
    """采样点（数值内容采样的 头/中/尾 帧）。"""
    index: int                # 帧索引
    time: float               # 相对时间（秒）
    data: Any                 # 规范值字典（position/velocity/effort 等）


@dataclass
class MutationPoint:
    """突变点（字段结构/尺度变化定位）。"""
    between: List[int]        # 变化介于的帧范围 [start, end]
    estimated_at: int         # 估算突变帧
    field: str                # 变化字段
    from_val: str             # 变化前值
    to_val: str               # 变化后值


@dataclass
class ContentEvent:
    """内容检测事件（content 段事件，含事件定位字段）。

    - index: 帧索引（起始）
    - time: 相对时间（秒，来自通道时间轴）
    - detail["range"]: 受影响帧范围 [start, end]（单点事件为 [idx, idx]）
    """
    type: str                 # 事件类型：imu_nan/imu_bounds/bias_suspect/data_freeze/joint_jump/joint_reset/
                              # joint_nan/joint_bounds/cmd_nan/cmd_bounds/cmd_jerk/image_collapse/blur_suspect/
                              # pixel_black/pixel_white/pixel_occlusion/pixel_garbled/pixel_blur/
                              # pixel_low_sharpness/pixel_overexpo/pixel_underexpo/pixel_color_anomaly/
                              # image_corrupt/image_resolution_change/image_encoding_change
    severity: Literal["pass", "warning", "error"]  # 三档（事件档位）
    topic: str                # 通道名（analyze 后富集）
    index: int                # 帧索引（-1 哨兵：相机级整体事件如 pixel_low_sharpness）
    time: float = 0.0         # 相对时间（秒，ScoringMixin 富集）
    detail: Dict[str, Any] = field(default_factory=dict)  # 事件字段：count/range/field/value/mean/sharpness 等


@dataclass
class CheckResult:
    """单项内容检测结果（content.checks 元素）。"""
    name: str                 # content_imu_<topic>/content_joint_<topic>/content_cmd_<topic>/
                              # content_pose_<topic>/content_imu_bias_<topic>/content_image_<topic>/
                              # content_image_pixels_<topic>/content_numeric（解包关闭跳过）
    passed: bool              # 是否通过
    severity: Literal["pass", "warning", "error"]  # 三档（事件最高档，冻结交叉验证后重算）
    message: str              # 人类可读消息
    events: List[ContentEvent] = field(default_factory=list)  # 事件列表
    detail: Dict[str, Any] = field(default_factory=dict)      # 检查专属：freeze_segments/collapse_segments/changes/sampled 等


@dataclass
class ContentResult:
    """内容检测总结果（content 段的对象来源）。"""
    bag_path: str             # 输入路径
    checks: List[CheckResult] # 全部内容检查项
    events: List[ContentEvent]  # 全部事件（扁平，供统计）
    overall_severity: Literal["pass", "warning", "error"]  # 综合档（checks 最高）
    summary: Dict[str, int]   # {error_count, warning_count, pass_count}

    def to_dict(self) -> dict:
        return {
            "bag_path": self.bag_path,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "severity": c.severity,
                    "message": c.message,
                    "events": [
                        {"type": e.type, "severity": e.severity, "topic": e.topic,
                         "index": e.index, "time": e.time,
                         "range": e.detail.get("range", [e.index, e.index]),
                         "detail": e.detail}
                        for e in c.events
                    ],
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "overall_severity": self.overall_severity,
            "summary": self.summary,
        }

@dataclass
class ContentConfig:
    """内容检测配置

    unpack: 是否允许「解包到内存」的检测（消息反序列化 / 像素采样解码）。
       False = 关闭所有需要解包的任务（数值内容 + 图像第二层），
       保留图像第一层（压缩域，零解码）。极限场景（超大/敏感数据）用。
    """
    unpack: bool = None  # 延迟默认# 是否允许解包到内存的检测（数值内容+图像二层）；False 只保留图像一层
    # ---- 各子检测项开关（默认全开）----
    numeric_enabled: bool = None  # 延迟默认# 数值内容检测开关
    image_layer1_enabled: bool = None  # 延迟默认# 图像第一层（压缩域）检测开关
    acc_max_threshold: float = None  # 延迟默认# 20g in m/s²
    gyro_max_threshold: float = None  # 延迟默认# 10 rad/s
    joint_jump_threshold: float = None  # 延迟默认# rad/s
    joint_velocity_peak: float = None  # 延迟默认# rad/s
    jerk_threshold: float = None  # 延迟默认# rad/s³
    # ---- 帧间隔估计（|Δpos|/dt 归一化用，判定参数）----
    sampling_frame_dt_fallback_sec: float = None  # 延迟默认# 无时间戳时兜底 30Hz
    sampling_frame_dt_max_sec: float = None  # 延迟默认# 实测中位 dt 合法上界（高于视作异常→兜底）
    sampling_head_count: int = None  # 延迟默认# 头部采样帧数
    sampling_tail_count: int = None  # 延迟默认# 尾部采样帧数
    sampling_mid_count: int = None  # 延迟默认# 中部采样帧数
    sampling_full_scan_threshold: int = None  # 延迟默认# 样本数 ≤ 该值 → 全量扫描
    sampling_binary_search_depth: int = None  # 延迟默认# 二分定位异常区间最大深度
    joint_limits: Dict[str, Dict[str, Dict[str, float]]] = field(default_factory=dict)  # topic -> {joint_name -> limits}（profile joint_limits 注入）
    # ---- 图像第一层（压缩域，不解码）----
    image_abs_size_floor_bytes: int = None  # 延迟默认# 低于此绝对值 → 疑似黑/白屏
    image_collapse_ratio: float = None  # 延迟默认# 低于中位数的该比例 → 内容骤减
    image_collapse_min_run: int = None  # 延迟默认# 骤减段最少连续帧数（抑制单包噪声）
    image_blur_low_ratio: float = None  # 延迟默认# 模糊带下界（相对中位数）
    image_blur_high_ratio: float = None  # 延迟默认# 模糊带上界（相对中位数）
    image_blur_min_run: int = None  # 延迟默认# 模糊带连续帧数
    image_freeze_low_frames: int = None  # 延迟默认# 相同压缩帧连续帧数（冻结）
    image_freeze_high_frames: int = None  # 延迟默认# 图像冻结 error 帧数（≈5s@30Hz，设计表 cut）
    # ---- 冻结交叉验证（C10/I7/J7）----
    freeze_cross_validate: bool = None  # 延迟默认# 冻结时检查运动参考通道是否同样冻结，区分自然静止
    freeze_motion_threshold: float = None  # 延迟默认# 参考通道等效速度 < 该值(rad/s) 视为自然静止
    # ---- IMU 偏置/噪声（I6）----
    imu_bias_detect: bool = None  # 延迟默认# IMU 偏置/噪声检测开关（I6）
    imu_bias_gyro_max: float = None  # 延迟默认# 静止段 gyro 均值超该值(rad/s) → 偏置
    imu_bias_acc_max: float = None  # 延迟默认# 静止段 |acc|-9.81 超该值(m/s²) → 偏置
    imu_bias_min_static: int = None  # 延迟默认# 静止段最少连续帧（判定静止）
    imu_bias_static_diff: float = None  # 延迟默认# 静止判定：相邻帧 gyro 变化 < 该值(rad/s)
    imu_bias_static_spread: float = None  # 延迟默认# 静止段内 gyro 最大跨度(rad/s)
    # ---- 图像第二层（采样帧内存解码，不落盘，需 unpack=True）----
    image_layer2: bool = None  # 延迟默认# 是否启用第二层采样解码检测
    image_pixel_sample_count: int = None  # 延迟默认# 每通道基础采样帧数（头/中/尾）
    image_layer2_focus_max: int = None  # 延迟默认# 层1异常区域的额外重点采样上限（自适应）
    image_fullframe: bool = None  # 延迟默认# 全帧管线开关（需 unpack=True，解码 1/4 灰度 turbojpeg）
    image_freeze_min_run: int = None  # 延迟默认# 全帧冻结 xxhash 连续相同帧数 → warning
    image_freeze_cut_run: int = None  # 延迟默认# 全帧冻结 xxhash 连续相同帧数 → error
    image_sharpness_floor: float = None  # 延迟默认# VoL 中位数低于此值 → 相机持续模糊
    image_blur_ratio: float = None  # 延迟默认# 帧清晰度 < 中位数×该比例 → 模糊事件
    # ---- 冻结/复位帧数阈值（按模态）----
    imu_freeze_low_frames: int = None  # 延迟默认# IMU 冻结帧数（≈1s@200Hz）
    imu_freeze_high_frames: int = None  # 延迟默认# IMU 冻结 error 帧数（≈5s@200Hz，设计表 cut）
    joint_freeze_low_frames: int = None  # 延迟默认# 关节冻结帧数（≈1s@50Hz）
    joint_freeze_high_frames: int = None  # 延迟默认# 关节冻结 error 帧数（≈5s@50Hz，设计表 cut）
    cmd_freeze_low_frames: int = None  # 延迟默认# cmd 冻结帧数（≈1s@30Hz）
    cmd_freeze_high_frames: int = None  # 延迟默认# cmd 冻结 error 帧数（≈5s@30Hz，设计表 cut）
    joint_reset_ratio: float = None  # 延迟默认# 多关节同步跳变占比 ≥ 该值 → 复位事件
    # ---- 位姿冻结/抖动/复位（P5/P7）----
    pose_freeze_min_sec: float = None  # 延迟默认# 冻结最低时长秒（≤不报，1s/5s 对齐总表）
    pose_freeze_cut_sec: float = None  # 延迟默认# 冻结 error 时长秒（≥→error）
    pose_freeze_lookback: int = None  # 延迟默认# 位姿冻结 lookback 窗口帧数
    pose_freeze_vel_threshold: float = None  # 延迟默认# 位姿冻结前运动速度阈值(m/s)
    pose_freeze_gap_factor: float = None  # 延迟默认# 发布间隙自适应系数(常规间隙×此值以下不报冻结)
    pose_reset_tolerance1: float = None  # 延迟默认# 首末帧复位容忍度1（≤ 不报）
    pose_reset_tolerance2: float = None  # 延迟默认# 首末帧复位容忍度2（≥ error）
    pose_reset_rot_tolerance1: float = None  # 延迟默认# 复位姿态容忍度1 rad
    pose_reset_rot_tolerance2: float = None  # 延迟默认# 复位姿态容忍度2 rad
    pose_jerk_window: int = None  # 延迟默认# SG 滤波窗口
    pose_jerk_polyorder: int = None  # 延迟默认# SG 多项式阶数
    pose_jerk_vel_factor: float = None  # 延迟默认# 静止判定速度因子
    pose_jerk_static_vel: float = None  # 延迟默认# 绝对静止线(m/s，配合相对线取 min)
    pose_jerk_min_segment: int = None  # 延迟默认# 最小静止段帧数
    pose_jerk_threshold: float = None  # 延迟默认# 抖动 jerk 阈值 m/s³（warning 线）
    pose_jerk_error: float = None  # 延迟默认# 抖动 jerk 熔断线 m/s³（≥→error）
    home_position: Dict[str, list] = field(default_factory=dict)  # 通道 home 各维值（profile 注入，休眠）
    # ---- 像素判定阈值（第二层）----
    pixel_black_mean: float = None  # 延迟默认# mean < 该值 → 黑屏
    pixel_black_std: float = None  # 延迟默认# 且 std < 该值 → 黑屏（辅助）
    pixel_black_saturate_level: float = None  # 延迟默认# 饱和占比判定：像素灰度 < 该值记为暗
    pixel_black_saturate_ratio: float = None  # 延迟默认# 暗像素占比 > 该值且 mean 低 → 黑屏
    pixel_white_mean: float = None  # 延迟默认# mean > 该值 → 白屏
    pixel_white_saturate_level: float = None  # 延迟默认# 亮像素定义：灰度 > 该值
    pixel_white_saturate_ratio: float = None  # 延迟默认# 且亮像素占比 > 该值 → 白屏（设计表）
    pixel_overexpo_mean: float = None  # 延迟默认# 过曝判定 mean 下界
    pixel_overexpo_std: float = None  # 延迟默认# 过曝判定 std 下界
    pixel_underexpo_mean: float = None  # 延迟默认# 欠曝判定 mean 上界
    pixel_underexpo_std: float = None  # 延迟默认# 欠曝判定 std 下界
    pixel_occlusion_mean: float = None  # 延迟默认# 遮挡判定 mean 上界（对抗验证：正常机器人场景 mean 84-115 且暗格 14-27% 会被 130 误报 → 收紧至 70，仅整体明显偏暗才判遮挡）
    pixel_occlusion_black_cell: float = None  # 延迟默认# 遮挡判定"黑块"阈值
    pixel_occlusion_dark_cell: float = None  # 延迟默认# 遮挡判定"暗块"阈值
    pixel_occlusion_frac_low: float = None  # 延迟默认# 暗/黑块占比下界
    pixel_occlusion_frac_high: float = None  # 延迟默认# 暗/黑块占比上界
    pixel_garbled_strong_frac: float = None  # 延迟默认# 花屏：强梯度像素占比下界
    pixel_garbled_grad_mean: float = None  # 延迟默认# 花屏：梯度均值下界
    pixel_garbled_grad_threshold: float = None  # 延迟默认# 强梯度定义（|diff|>该值）
    pixel_color_anomaly_range: float = None  # 延迟默认# 严重偏色：通道均值差 > 该值
    # ---- 图像二层占比分级（设计表 C5/C9 处置档，profile 可配）----
    bad_frame_low: float = None  # 延迟默认# 坏帧占比 > 5% → warning
    bad_frame_high: float = None  # 延迟默认# 坏帧占比 ≥ 30% → error
    blur_low: float = None  # 延迟默认# 模糊帧占比 > 10% → warning
    blur_high: float = None  # 延迟默认# 模糊帧占比 ≥ 30% → error
    topic_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # topic/通道 -> {字段: 值} 按通道覆盖阈值（profile topics.<名>.filters.content）


# 单一源完整性校验：defaults.yaml 该组键 == 标量字段（防止 yaml/dataclass 漂移）
    disabled_codes: set = None  # 指标级关断（事件层按 code 过滤）

    def __post_init__(self):
        from ...config.loader import apply_group_defaults
        apply_group_defaults(self, "content")
        self.disabled_codes = self.disabled_codes or set()

INDICATOR_PARAMS: Dict[str, List[str]] = {
    # 键=检测项名（与 indicators.params.INDICATOR_ITEMS 一致，C5 族多检测项共享 code）
    "black_screen": ["pixel_black_mean", "pixel_black_std", "pixel_black_saturate_level",
                     "pixel_black_saturate_ratio", "pixel_white_mean", "pixel_white_saturate_level",
                     "pixel_white_saturate_ratio", "image_abs_size_floor_bytes",
                     "image_collapse_ratio", "image_collapse_min_run"],
    "bad_frame_summary": ["bad_frame_low", "bad_frame_high"],
    "occlusion": ["pixel_occlusion_mean", "pixel_occlusion_black_cell", "pixel_occlusion_dark_cell",
                  "pixel_occlusion_frac_low", "pixel_occlusion_frac_high"],
    "garbled": ["pixel_garbled_strong_frac", "pixel_garbled_grad_mean", "pixel_garbled_grad_threshold"],
    "overexpo": ["pixel_overexpo_mean", "pixel_overexpo_std"],
    "underexpo": ["pixel_underexpo_mean", "pixel_underexpo_std"],
    "color_anomaly": ["pixel_color_anomaly_range"],
    "resolution_change": [],
    "blur": ["image_blur_low_ratio", "image_blur_high_ratio", "image_blur_min_run",
             "image_blur_ratio", "image_sharpness_floor", "blur_low", "blur_high"],
"freeze": ["image_freeze_low_frames", "image_freeze_high_frames",
                "image_freeze_min_run", "image_freeze_cut_run",
                "freeze_cross_validate", "freeze_motion_threshold"],
    "joint_freeze": ["joint_freeze_low_frames", "joint_freeze_high_frames",
                     "freeze_cross_validate", "freeze_motion_threshold"],
    "imu_freeze": ["imu_freeze_low_frames", "imu_freeze_high_frames",
                   "freeze_cross_validate", "freeze_motion_threshold"],
    "cmd_freeze": ["cmd_freeze_low_frames", "cmd_freeze_high_frames",
                   "freeze_cross_validate", "freeze_motion_threshold"],
    "pose": ["pose_freeze_min_sec", "pose_freeze_cut_sec", "pose_freeze_lookback",
             "pose_freeze_vel_threshold", "pose_freeze_gap_factor",
             "pose_reset_tolerance1", "pose_reset_tolerance2",
            "pose_reset_rot_tolerance1", "pose_reset_rot_tolerance2",
             "pose_jerk_window", "pose_jerk_polyorder", "pose_jerk_vel_factor",
             "pose_jerk_static_vel", "pose_jerk_min_segment",
             "pose_jerk_threshold", "pose_jerk_error",
             "freeze_cross_validate", "freeze_motion_threshold"],
    "joint_anomaly": ["joint_jump_threshold", "joint_velocity_peak", "joint_reset_ratio"],
    "cmd_anomaly": ["jerk_threshold"],
    "imu_anomaly": ["acc_max_threshold", "gyro_max_threshold"],
    "bias": ["imu_bias_detect", "imu_bias_gyro_max", "imu_bias_acc_max",
             "imu_bias_min_static", "imu_bias_static_diff", "imu_bias_static_spread"],
    "_系统开关与采样": ["numeric_enabled", "image_layer1_enabled", "image_layer2",
                    "sampling_frame_dt_fallback_sec", "sampling_frame_dt_max_sec",
                    "sampling_head_count", "sampling_tail_count", "sampling_mid_count",
                    "sampling_full_scan_threshold", "sampling_binary_search_depth",
                    "image_pixel_sample_count", "image_layer2_focus_max"],
}
