from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore, get_types_from_msg

from src.loader import Episode, GroupData
from .base_loader import BaseLoader


_BASE_TEXTS: dict[str, str] = {
    "kuavo_msgs/jointData": (
        "float64[] joint_q\n"
        "float64[] joint_v\n"
        "float64[] joint_vd\n"
        "float64[] joint_torque\n"
    ),
    "kuavo_msgs/imuData": (
        "geometry_msgs/Vector3 gyro\n"
        "geometry_msgs/Vector3 acc\n"
        "geometry_msgs/Vector3 free_acc\n"
        "geometry_msgs/Quaternion quat\n"
    ),
    "kuavo_msgs/endEffectorData": (
        "string[] name\n"
        "float64[] position\n"
        "float64[] velocity\n"
        "float64[] effort\n"
    ),
    "kuavo_msgs/FTsensorData": (
        "float64[] Fx\n"
        "float64[] Fy\n"
        "float64[] Fz\n"
        "float64[] Mx\n"
        "float64[] My\n"
        "float64[] Mz\n"
    ),
}

_SENSORSDATA_WITH_FT = (
    "std_msgs/Header header\n"
    "time sensor_time\n"
    "kuavo_msgs/jointData joint_data\n"
    "kuavo_msgs/imuData imu_data\n"
    "kuavo_msgs/endEffectorData end_effector_data\n"
    "kuavo_msgs/FTsensorData FTsensor_data\n"
)

_SENSORSDATA_NO_FT = (
    "std_msgs/Header header\n"
    "time sensor_time\n"
    "kuavo_msgs/jointData joint_data\n"
    "kuavo_msgs/imuData imu_data\n"
    "kuavo_msgs/endEffectorData end_effector_data\n"
)


def _has_ftsensor(bag_path: Path) -> bool:
    with Reader(bag_path) as reader:
        for conn in reader.connections:
            if conn.topic == "/sensors_data_raw":
                for mdef in conn.msgdef:
                    if "FTsensor" in str(mdef):
                        return True
    return False


def _has_dexhand(bag_path: Path) -> bool:
    with Reader(bag_path) as reader:
        for conn in reader.connections:
            if conn.topic == "/dexhand/state":
                return True
    return False


def _build_typestore(has_ft: bool) -> Any:
    typestore = get_typestore(Stores.ROS1_NOETIC)
    for name, text in _BASE_TEXTS.items():
        types = get_types_from_msg(text, name)
        typestore.register(types)

    sensors_text = _SENSORSDATA_WITH_FT if has_ft else _SENSORSDATA_NO_FT
    types = get_types_from_msg(sensors_text, "kuavo_msgs/sensorsData")
    typestore.register(types)
    return typestore


def _extract_bag(
    bag_path: Path,
) -> list[dict[str, Any]]:
    has_ft = _has_ftsensor(bag_path)
    has_dex = _has_dexhand(bag_path)
    typestore = _build_typestore(has_ft)

    sensor_data: list[tuple[int, dict[str, Any]]] = []
    dexhand_data: list[tuple[int, list[float]]] = []

    with Reader(bag_path) as reader:
        connections = {c.topic: c for c in reader.connections}
        conn_sensor = connections.get("/sensors_data_raw")
        conn_dex = connections.get("/dexhand/state") if has_dex else None

        if conn_sensor is None:
            return []

        target_conns = [conn_sensor]
        if conn_dex is not None:
            target_conns.append(conn_dex)

        for c, ts, rawdata in reader.messages(connections=target_conns):
            if c.topic == "/sensors_data_raw":
                d = typestore.deserialize_ros1(rawdata, conn_sensor.msgtype)
                jd = d.joint_data
                imu = d.imu_data
                frame = {
                    "leg_joint_positions": list(jd.joint_q[0:12]),
                    "left_arm_joint_positions": list(jd.joint_q[12:19]),
                    "right_arm_joint_positions": list(jd.joint_q[19:26]),
                    "gripper_joint_positions": list(jd.joint_q[26:28]),
                    "leg_joint_torques": list(jd.joint_torque[0:12]),
                    "base_orientation": [
                        float(imu.quat.w),
                        float(imu.quat.x),
                        float(imu.quat.y),
                        float(imu.quat.z),
                    ],
                    "gravity_vector": [
                        float(imu.acc.x),
                        float(imu.acc.y),
                        float(imu.acc.z),
                    ],
                    "angular_velocity": [
                        float(imu.gyro.x),
                        float(imu.gyro.y),
                        float(imu.gyro.z),
                    ],
                }
                sensor_data.append((ts, frame))

            elif c.topic == "/dexhand/state":
                d = typestore.deserialize_ros1(rawdata, conn_dex.msgtype)
                positions = [float(p) for p in d.position]
                dexhand_data.append((ts, positions))

    if not dexhand_data:
        if has_dex:
            warnings.warn(
                f"Bag {bag_path.name}: /dexhand/state topic exists but no messages "
                "were deserialized. Dexhand data will be missing.",
                stacklevel=2,
            )
        return [frame for _, frame in sensor_data]

    first_dex_positions = dexhand_data[0][1]
    last_dex: list[float] = first_dex_positions
    dex_idx = 0

    for ts, frame in sensor_data:
        while dex_idx < len(dexhand_data) and dexhand_data[dex_idx][0] <= ts:
            last_dex = dexhand_data[dex_idx][1]
            dex_idx += 1

        frame["left_dexhand_positions"] = last_dex[0:6]
        frame["right_dexhand_positions"] = last_dex[6:12]

    return [frame for _, frame in sensor_data]


class KuavoLoader(BaseLoader):
    def load_episode(self, episode_path: Path) -> Episode:
        episode_path = Path(episode_path)
        bag_path: Path

        if episode_path.is_dir():
            bags = sorted(episode_path.glob("*.bag"))
            if not bags:
                raise FileNotFoundError(f"No .bag files found in {episode_path}")
            bag_path = bags[0]
        else:
            bag_path = episode_path

        if not bag_path.exists():
            raise FileNotFoundError(f"Bag file not found: {bag_path}")

        has_ft = _has_ftsensor(bag_path)
        has_dex = _has_dexhand(bag_path)
        frames = _extract_bag(bag_path)

        if not frames:
            raise ValueError(f"No /sensors_data_raw messages found in {bag_path}")

        if not has_dex:
            first_frame = frames[0]
            if "left_dexhand_positions" not in first_frame:
                warnings.warn(
                    f"Bag {bag_path.name}: Configuration enables dexhand parameters "
                    "but /dexhand/state topic is not present in this bag. "
                    "Dexhand detection will be skipped for this episode.",
                    stacklevel=2,
                )

        meta = {
            "bag_file": bag_path.name,
            "has_ftsensor": has_ft,
            "has_dexhand": has_dex,
            "num_frames": len(frames),
        }

        episode = Episode(path=bag_path, meta=meta)

        for group_name in ("arm", "leg", "base", "imu", "dexhand"):
            episode.groups[group_name] = GroupData(
                group_name=group_name,
                frames=frames,
                fps=0,
            )

        return episode
