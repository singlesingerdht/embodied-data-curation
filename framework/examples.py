"""Deterministic synthetic example, explicitly not a real pouring demonstration."""
from pathlib import Path
import math
import yaml
from framework.results import write_json


def generate(output):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    entries = []
    for index in range(2):
        ident = f"episode_{index + 1:03d}"
        rows = [{"follow_right_joint_pos": [0.1 * math.sin(i / 12)] * 6,
                 "follow_right_position": [i / 1000, 0.0, 0.2]} for i in range(60)]
        if index:
            rows[30]["follow_right_joint_pos"] = [100.0] * 6
        write_json(root / "episodes" / ident / "trajectory.json", {"fps": 30, "data": rows})
        entries.append({"id": ident, "path": "episodes/" + ident, "format": "episode"})
    (root / "dataset.yaml").write_text(yaml.safe_dump({"schema_version": 1, "id": "synthetic_cpu_demo",
        "version": "1", "tasks": ["合成接口验证，非真实倒茶数据"], "episodes": entries}, allow_unicode=True))
    (root / "robot.yaml").write_text(yaml.safe_dump({"schema_version": 1, "id": "synthetic_robot", "version": "1",
        "parts": {"right_arm": {"dof": 6}},
        "channels": {"follow_right_joint_pos": {"modality": "joint_state", "robot_part": "right_arm", "fields": ["position"], "unit": "rad", "dimensions": [6], "representation": {"quantity": "joint_angle", "semantic_status": "confirmed"}},
                     "follow_right_position": {"modality": "pose", "fields": ["position"], "unit": "m", "dimensions": [3], "representation": {"quantity": "position", "semantic_status": "confirmed"}}},
        "calibration": {}}, allow_unicode=True))
    return root
