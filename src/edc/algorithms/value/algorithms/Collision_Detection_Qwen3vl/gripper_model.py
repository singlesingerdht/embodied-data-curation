"""Module 2b: 本体感知驱动的夹爪"呼吸"椭球 + 抓取绑定 (bonded)。

核心思想 (Update 1 落地):
  视觉 mask 只在帧 0 用一次 (标定椭球基准尺寸 + TCP 偏移), 之后夹爪状态
  完全由 250Hz 本体感知驱动 —— 无漂移、无延迟:

  中心:  c(t) = p_ee_cam(t) + R(t) @ offset_tcp        (帧0标定的固定偏移)
  朝向:  R(t) = 四元数 → 旋转矩阵 (夹爪坐标系: z=接近轴, x=开口轴)
  呼吸:  r_open(t) = r_finger + gripper_width(t)/2     (米制开度直接驱动)
         —— 开 → 沿开口轴胀大包住两指 + 被夹物; 合 → 收紧贴指
  绑定:  gripper_width < θ_close 且 与 target 椭球相交
         → target 视为被握持: 其中心随 ee 增量平移, 与 gripper 组成
           bonded 实体, 一起参与 B/C 碰撞检测 (抓取接触 ≠ 碰撞, 走白名单)
"""
from __future__ import annotations

import numpy as np

from .geometry import Ellipsoid

# 夹爪几何先验 (米): 典型两指平行夹爪
FINGER_LEN = 0.12      # 指长 (沿接近轴 z)
FINGER_THICK = 0.025   # 单指厚度
PALM_HALF = 0.045      # 掌部半宽 (沿开口轴之外的横轴)
PALM_LEN = 0.06        # 掌/电机部长度 (指根到手腕)
GRASP_CLOSE_TH = 1.0   # 判定"已闭合握持"的 gripper_value 阈值;
                        # pour_tea 右臂: 全闭合~0.008(kettle), 半闭合~0.74(lid);
                        # 左臂卡死 0.0143 但 opened_since_grasp=False 防误报
# 夹爪体总长 = 指长 + 掌 = 0.18m; 体中点在 TCP 后方 (指长+掌/2)/2 ≈ 0.075m
BODY_HALF_Z = (FINGER_LEN + PALM_LEN) / 2  # z 半轴: 覆盖指尖→掌尾 = 0.09m
DEFAULT_TCP_OFFSET = np.array([0.0, 0.0, -(FINGER_LEN / 2 + PALM_LEN / 2)])  # 体中点


def quat_to_rotmat(q: np.ndarray) -> np.ndarray:
    """(w,x,y,z) 四元数 → 3x3 旋转矩阵。"""
    w, x, y, z = np.asarray(q, np.float64) / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


class GripperModel:
    """单臂夹爪: 本体感知流 → 动态呼吸椭球。"""

    def __init__(self, arm: str, base_ellipsoid: Ellipsoid | None = None,
                 tcp_offset: np.ndarray | None = None, open_axis: int = 0):
        """
        Args:
            base_ellipsoid: 帧 0 视觉 MVEE (无则用几何先验合成)
            tcp_offset: 帧 0 标定的 ee→夹爪质心偏移 (夹爪局部系);
                        None 时默认偏移到体中点 (指尖后方)
            open_axis: 开口轴在夹爪局部系的索引 (默认 x=0)
        """
        self.arm = arm
        self.base = base_ellipsoid
        # 无标定时默认偏移到体中点: 椭球覆盖 指尖→掌尾, 不向指尖前方空伸
        self.tcp_offset = (DEFAULT_TCP_OFFSET.copy() if tcp_offset is None
                           else np.asarray(tcp_offset, np.float64))
        self.open_axis = open_axis
        self.holding = False          # 当前是否处于握持(bonded)状态
        self.last_R = np.eye(3)

    # —— 几何先验合成 (帧 0 无视觉时兜底) ——
    @classmethod
    def prior_ellipsoid(cls, gripper_width: float) -> Ellipsoid:
        """几何先验椭球: 以体中点为中心, 覆盖指尖→掌尾。

        radii = (开口半幅, 横向半宽, 体半长), 轴为夹爪局部系。
        z 半轴 = BODY_HALF_Z = (指长+掌)/2, 覆盖指尖(0)到掌尾(-指长-掌)。
        中心在原点(局部系=体中点), 投影到相机系时由 tcp_offset 平移到体中点。"""
        r_open = FINGER_THICK + max(gripper_width, 0.0) / 2 + 0.01
        radii = np.array([r_open, PALM_HALF, BODY_HALF_Z])
        return Ellipsoid(np.zeros(3), np.eye(3), radii, padding=1.0)

    def ellipsoid_at(self, ee_cam: np.ndarray, quat: np.ndarray,
                     gripper_width: float) -> Ellipsoid:
        """由当前本体感知状态生成呼吸椭球。

        中心 = ee (相机系) + R·tcp_offset
        轴   = R (局部 z=接近轴 指向被夹物; x=开口轴)
        呼吸 = 基准开口半径 ∝ gripper_width (米制开度),
               握持时膨胀 1.5× 包住指间被夹物 (防止漏检被握物体的碰撞)
        """
        R = quat_to_rotmat(quat)  # 臂基座系四元数 → 相机系旋转?
        # 注: ee 位姿定义在臂基座系, 相机系旋转 = T_arm2cam 旋转 @ R_arm
        # (调用方传入的 quat 已转到相机系, 见 pipeline)
        self.last_R = R
        center = np.asarray(ee_cam, np.float64) + R @ self.tcp_offset

        # 基准尺寸: 帧 0 视觉 MVEE 优先 (真实尺寸), 否则几何先验
        if self.base is not None:
            axes, base_radii = self.base.axes.copy(), self.base.radii.copy()
            # 帧 0 标定时把视觉椭球旋回夹爪局部系 (见 calibrate), base 已是局部系
            radii = base_radii.copy()
            # 开口轴半径 = 指半厚 + 开度/2 + 视觉基准的一半 (取 max 防过缩)
            r_open = FINGER_THICK + max(gripper_width, 0.0) / 2 + 0.008
            radii[self.open_axis] = max(r_open, base_radii[self.open_axis] * 0.6)
            if self.holding:
                radii[self.open_axis] *= 1.5   # 握持膨胀: 包住指间物体
                radii *= 1.15                  # 整体膨胀 (被夹物略大于指包络)
        else:
            radii = self.prior_ellipsoid(gripper_width).radii.copy()
            if self.holding:
                radii *= 1.3
        # 局部系 → 相机系: axes_world = R @ axes_local
        axes_world = R @ np.eye(3) if self.base is None else R @ axes
        return Ellipsoid(center, axes_world, radii, padding=1.0)

    # —— 帧 0 标定 ——
    @staticmethod
    def calibrate(ee_cam_frame0: np.ndarray, quat_frame0: np.ndarray,
                  gripper_cloud: np.ndarray) -> tuple[np.ndarray, Ellipsoid]:
        """帧 0 视觉标定: 求 tcp_offset 和局部系基准椭球。

        offset = R₀ᵀ (median(cloud) - ee_cam)   —— 视觉夹爪中心在夹爪局部系的坐标
        base_ellipsoid = 视觉 MVEE 旋回局部系: R_local = R₀ᵀ R_vis
        """
        cloud = np.asarray(gripper_cloud, np.float64)
        visual_center = np.median(cloud, axis=0)
        R0 = quat_to_rotmat(quat_frame0)
        offset = R0.T @ (visual_center - np.asarray(ee_cam_frame0, np.float64))
        e_vis = None
        from .geometry import fit_mvee
        e_vis = fit_mvee(cloud) if len(cloud) <= 500 else fit_mvee(cloud)
        axes_local = R0.T @ e_vis.axes        # 相机系主轴 → 夹爪局部系
        base = Ellipsoid(e_vis.center @ R0, axes_local, e_vis.radii, padding=1.0)
        base.center = np.zeros(3)             # 局部系原点 = 标定中心
        return offset, base


def detect_grasp(tcp_cam: np.ndarray, target_ell: Ellipsoid,
                 gripper_width: float, reach: float = 0.08) -> bool:
    """抓取判定: 夹爪闭合 (width<θ) 且 TCP 距目标中心 ≤ 目标最大半径
    + reach (指尖-掌心余量)。

    判据演化:
      椭球相交 → 误报 (闭合夹爪路过/停在大椭球旁即触发);
      目标中心落入夹爪椭球 → 漏报 (夹扁平物夹的是边缘, 中心在 10cm+ 外);
      TCP-中心距离 ≤ r_max+reach → 兼顾两者: 夹边缘时 TCP 贴着物面,
      距离 ≈ 物体半径; 路过不闭合时另一道闸 (active_close) 拦截。"""
    if gripper_width > GRASP_CLOSE_TH:
        return False
    d = float(np.linalg.norm(np.asarray(tcp_cam) - target_ell.center))
    return d <= float(target_ell.radii.max()) + reach
