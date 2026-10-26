"""值规范化: 各格式 → 统一模态语义字典。

joint_state → {position, velocity, effort}    # position=关节角, effort=力矩/电流
imu         → {ang_vel, lin_acc, orientation}
cmd         → {joint_q, joint_v, tau}
pose        → {position, orientation}         # position=笛卡尔 XYZ
"""
import numpy as np
from typing import Any, Dict, List, Optional


def infer_fields(name: str, msg_type: str = "") -> Optional[List[str]]:
    """通道名/ROS 类型 → 能产出的物理量清单。

    优先 msg_type (ROS 结构体消息), 回退 name 关键词 (扁平 JSON/parquet/hdf5)。
    """
    mt = (msg_type or "").lower()
    n = name.lower()

    # ── ROS msg_type 推断 ──
    if "jointstate" in mt:
        return ["position", "velocity", "effort"]
    if "imu" in mt:
        return ["ang_vel", "lin_acc", "orientation"]
    if "jointcmd" in mt or "joint_cmd" in mt:
        return ["joint_q", "joint_v", "tau"]
    if "sensorsdata" in mt:
        return ["joint_q", "joint_v", "tau"]
    if "pose" in mt or "odometry" in mt:
        return ["position", "orientation"]

    # ── name 关键词推断 (扁平格式) ──
    # joint_state 侧
    if "joint_pos" in n or ("joint" in n and "pos" in n and "cur" not in n):
        return ["position"]
    if "joint_cur" in n or "joint_current" in n:
        return ["current"]         # Electrical current is not torque
    if "joint_dev" in n:
        return ["deviation"]
    if "joint_vel" in n or ("velocity" in n and "joint" in n):
        return ["velocity"]
    if "joint_effort" in n or "joint_torque" in n or "joint_tau" in n:
        return ["effort"]
    # cmd 侧 (目标量/前馈量)
    if "q_pos" in n or "qpos" in n or "target_q" in n:
        return ["joint_q"]         # 关节目标位置 (cmd 模态)
    if "joint_v" in n:
        return ["joint_v"]
    if "tauff" in n or "tau_ff" in n:
        return ["tau"]             # 前馈力矩 (cmd 模态, tau = effort 在 cmd 侧的别名)
    if "tau" in n and "joint" not in n:
        return ["tau"]
    if "ee_target" in n or "eef_target" in n:
        return ["joint_q"]         # EE 目标 (用户确认: cmd 类, 关节空间)
    # imu 侧
    if "angular_velocity" in n or "ang_vel" in n or "gyro" in n:
        return ["ang_vel"]
    if "linear_acceleration" in n or "lin_acc" in n or "accel" in n:
        return ["lin_acc"]
    # pose 侧
    if "orientation" in n or "quaternion" in n:
        return ["orientation"]
    if "rotation" in n:
        return ["orientation"]
    if "eef" in n or "ee_pose" in n or "ee_pos" in n:
        return ["position"]
    if "position" in n and "eef" not in n:
        return ["position"]
    return None


def ros_values_to_dict(msg: Any, msg_type: str = "") -> Dict[str, Any]:
    """ROS 消息 → 规范字典 (参考 DF rosbag.py)。"""
    mt = (msg_type or "").lower()
    def xyz(v):
        return [float(v.x), float(v.y), float(v.z)] if all(hasattr(v, k) for k in "xyz") else list(v)

    def xyzw(v):
        return [float(v.x), float(v.y), float(v.z), float(v.w)] \
            if all(hasattr(v, k) for k in "xyzw") else list(v)

    try:
        if "imu" in mt and hasattr(msg, "angular_velocity"):
            return {
                "ang_vel": xyz(msg.angular_velocity),
                "lin_acc": xyz(msg.linear_acceleration),
                "orientation": xyzw(msg.orientation),
            }
        if "jointstate" in mt and hasattr(msg, "position"):
            return {
                "position": list(msg.position),
                "velocity": list(getattr(msg, "velocity", [])),
                "effort": list(getattr(msg, "effort", [])),
            }
        if ("jointcmd" in mt or "joint_cmd" in mt) and hasattr(msg, "joint_q"):
            return {
                "joint_q": list(msg.joint_q),
                "joint_v": list(getattr(msg, "joint_v", [])),
                "tau": list(getattr(msg, "tau", getattr(msg, "joint_torque", []))),
            }
        if "sensorsdata" in mt and hasattr(msg, "joint_data"):
            jd = msg.joint_data
            return {
                "joint_q": list(getattr(jd, "joint_q", [])),
                "joint_v": list(getattr(jd, "joint_v", [])),
                "tau": list(getattr(jd, "joint_torque", [])),
            }
        if any(k in mt for k in ("pose", "odometry")):
            pose = getattr(msg, "pose", msg)
            # Odometry/PoseWithCovariance adds one more pose wrapper.
            pose = getattr(pose, "pose", pose)
            if hasattr(pose, "position"):
                return {
                    "position": xyz(pose.position),
                    "orientation": xyzw(pose.orientation),
                }
    except Exception:
        pass
    return {}


def column_to_fields(col_name: str, value: Any) -> Dict[str, Any]:
    """Parquet/HDF5 列值 → 规范字典 (参考 DF parquet.py _column_to_fields)。"""
    lc = col_name.lower()
    try:
        if isinstance(value, (list, np.ndarray)):
            arr = list(value)
            if any(k in lc for k in ("velocity", "joint_v")):
                return {"velocity": arr}
            if any(k in lc for k in ("effort", "torque", "tau")):
                return {"effort": arr}
            if any(k in lc for k in ("joint_q",)):
                return {"joint_q": arr}
            return {"position": arr}
        if isinstance(value, dict):
            return value
        return {"value": [float(value)]}
    except Exception:
        return {}


def normalize_values(modality: str, raw: Any, name: str = "") -> Dict[str, Any]:
    """统一入口: 按模态调对应的规范化函数。"""
    if modality == "imu":
        if isinstance(raw, dict) and "ang_vel" in raw:
            return raw
        if isinstance(raw, (list, np.ndarray)):
            arr = list(raw)
            return {"ang_vel": arr[:3], "lin_acc": arr[3:6] if len(arr) > 3 else []}
        return raw if isinstance(raw, dict) else {}
    if modality == "joint_state":
        if isinstance(raw, dict) and "position" in raw:
            return raw
        return column_to_fields(name, raw)
    if modality == "cmd":
        if isinstance(raw, dict):
            return raw
        return column_to_fields(name, raw)
    if modality == "pose":
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, (list, np.ndarray)):
            arr = list(raw)
            # 默认按 xyz_quat 7 维布局 (与 read_field 推断互斥, 此处仅死代码防御)
            return {"position": arr[:3], "orientation": arr[3:7] if len(arr) >= 7 else []}
        return {}
    return raw if isinstance(raw, dict) else {}
