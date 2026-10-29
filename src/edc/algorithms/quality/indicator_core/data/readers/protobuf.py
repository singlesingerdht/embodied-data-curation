"""MCAP protobuf 动态解码工具"""
from ..ir import MODALITY_JOINT_STATE, MODALITY_CMD, MODALITY_IMAGE

_PROTOBUF_SCHEMA_MODALITY = {
    "Locomotion.RobotState": MODALITY_JOINT_STATE,
    "Locomotion.ServoJoint": MODALITY_CMD,
    "ServoEffector": MODALITY_CMD,
    "foxglove.RawImage": MODALITY_IMAGE,
    "foxglove.CompressedImage": MODALITY_IMAGE,
}


def _build_protobuf_class(schema_data: bytes, type_name: str):
    """从 FileDescriptorSet 动态构建 protobuf 消息类"""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(schema_data)
    pool = descriptor_pool.DescriptorPool()
    added = set()

    def add_file(f):
        for dep in f.dependency:
            for f2 in fds.file:
                if f2.name == dep:
                    add_file(f2)
        if f.name not in added:
            pool.Add(f)
            added.add(f.name)

    for f in fds.file:
        add_file(f)
    desc = pool.FindMessageTypeByName(type_name)
    return message_factory.GetMessageClass(desc)


def _protobuf_to_values(schema_name: str, m) -> dict:
    """把动态解码的 protobuf 消息映射为规范值字典"""
    try:
        if schema_name == "Locomotion.RobotState":
            j = m.joints
            return {
                "position": list(j.position),
                "velocity": list(j.velocity),
                "effort": list(j.effort),
            }
        if schema_name == "Locomotion.ServoJoint":
            t = m.target
            return {
                "joint_q": list(t.position),
                "joint_v": list(t.velocity),
                "tau": list(t.effort),
            }
        if schema_name == "ServoEffector":
            adv = m.command.adv
            ratio = float(adv.ratio)
            return {"position": [ratio], "names": [m.header.name]}
        if schema_name == "Locomotion.RobotState.pose":
            p = m.pose
            return {"position": [p.position.x, p.position.y, p.position.z],
                    "orientation": [p.orientation.x, p.orientation.y,
                                    p.orientation.z, p.orientation.w]}
    except Exception:
        return {}
    return {}
