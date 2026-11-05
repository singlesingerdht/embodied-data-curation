"""文件与通道结构完整性检查 (DF 方案)

检查项:
  - 文件完整性: 存在/可读/格式识别
  - 通道完整性: 必需通道存在 + 消息数非零
  - 相机配对: 每个 image topic 有对应 camera_info
  - 相机配置: 显式 sensor_id 对应的必需模态存在且非空
"""
import os
from pathlib import Path
from typing import Dict, List, Optional
from edc.data.ir.metadata import TopicInfo, MODALITY_IMAGE


def check_file_integrity(path: str) -> dict:
    """文件完整性: 存在/可读/大小"""
    p = Path(path)
    if not p.exists():
        return {"exists": False, "readable": False, "format": "unknown", "size_bytes": 0,
                "severity": "error", "reason": "file_not_found"}
    if not os.access(path, os.R_OK):
        return {"exists": True, "readable": False, "format": "unknown", "size_bytes": p.stat().st_size if p.is_file() else 0,
                "severity": "error", "reason": "not_readable"}
    fmt = _detect_format(p)
    return {
        "exists": True, "readable": True,
        "format": fmt,
        "size_bytes": p.stat().st_size if p.is_file() else 0,
        "is_dir": p.is_dir(),
        "severity": "pass" if fmt != "unknown" else "warning",
        "reason": "ok" if fmt != "unknown" else "unrecognized_format",
    }


def _detect_format(p: Path) -> str:
    """统一走 IR 层格式探测 (data.ir.detect_format)。

    与旧本地实现的差异: 能识别 lerobot/episode/dataset/json 及
    .avi/.mov/.mkv 等视频扩展; 目录探测基于顶层结构而非递归扩展名集合。
    仅用于报告展示与 unknown 判定, 不影响其他检查逻辑。
    """
    try:
        from edc.data.ir.metadata import detect_format
        return detect_format(str(p))
    except ImportError:
        # 独立导入 (无 QA 根路径) 时退回本地最简扩展名映射
        ext = p.suffix.lower()
        return {
            ".bag": "ros1_bag", ".db3": "ros2_bag", ".mcap": "mcap",
            ".parquet": "parquet", ".pq": "parquet", ".h5": "hdf5",
            ".hdf5": "hdf5", ".mp4": "mp4", ".bvh": "bvh",
        }.get(ext, "unknown" if p.is_file() else "dataset")


def check_topics(topics: Dict[str, TopicInfo],
                 required_topics: Optional[List[str]] = None) -> dict:
    """通道完整性: 必需通道存在 + 消息数非零"""
    result = {
        "total_topics": len(topics),
        "topic_names": list(topics.keys()),
        "missing_required": [],
        "empty_topics": [],
        "severity": "pass",
    }
    if required_topics:
        for req in required_topics:
            if req not in topics:
                result["missing_required"].append(req)
            elif topics[req].count == 0:
                result["empty_topics"].append(req)
    for name, ti in topics.items():
        if ti.count == 0:
            result["empty_topics"].append(name)
    if result["missing_required"]:
        result["severity"] = "error"
    elif result["empty_topics"]:
        result["severity"] = "warning"
    return result


def check_camera_pairs(topics: Dict[str, TopicInfo]) -> dict:
    """相机配对: 每个 image topic 有对应 camera_info"""
    image_topics = [n for n, ti in topics.items() if ti.modality == MODALITY_IMAGE]
    pairs = []
    orphans = []
    for img in image_topics:
        info = _infer_camera_info(img, topics)
        pairs.append({"image": img, "camera_info": info, "paired": info is not None})
        if info is None:
            orphans.append(img)
    return {
        "image_count": len(image_topics),
        "paired_count": len(image_topics) - len(orphans),
        "orphan_images": orphans,
        "pairs": pairs,
        "severity": "warning" if orphans else "pass",
    }


def _infer_camera_info(img_topic: str, topics: Dict[str, TopicInfo]) -> Optional[str]:
    parts = img_topic.split("/")
    if len(parts) >= 2:
        cam_prefix = "/".join(parts[:-1])
        info_topic = cam_prefix + "/camera_info"
        if info_topic in topics:
            return info_topic
    return None


def check_camera_configuration(topics, requirements=None, channel_config=None):
    """Validate explicit sensor/modalities, never infer cross-camera symmetry."""
    import fnmatch
    from edc.data.ir.modalities import normalize_modality
    if not requirements and not any(t.modality in {"image", "rgb", "depth"} for t in topics.values()):
        return {"evaluated": True, "severity": "pass", "checks": [],
                "reason": "数据里没有相机，机器人 yaml 也没写 cameras：没有相机要查"}
    if not requirements:
        return {"evaluated": False, "severity": "not_evaluated", "checks": [],
                "reason": "未配置 camera_requirements；不推断各相机应具有相同通道"}
    checks = []
    for sensor, modalities in requirements.items():
        for modality in modalities:
            declarations = [(p, c) for p, c in (channel_config or {}).items()
                            if c.get("sensor_id") == sensor
                            and normalize_modality(c.get("modality", "other")) == modality]
            matches = []
            for name, topic in topics.items():
                config = next((c for p, c in (channel_config or {}).items()
                               if fnmatch.fnmatchcase(name, p)), {})
                if (config.get("sensor_id") == sensor
                        and normalize_modality(config.get("modality", "other")) == modality):
                    matches.append((name, topic))
            status = ("unmapped" if not declarations else "missing" if not matches else
                      "modality_mismatch" if any(normalize_modality(t.modality) != modality for _, t in matches) else
                      "empty" if any(t.count == 0 for _, t in matches) else "present")
            checks.append({"sensor_id": sensor, "modality": modality, "status": status,
                           "configured_patterns": [p for p, _ in declarations],
                           "channels": [n for n, _ in matches],
                           "counts": {n: t.count for n, t in matches}})
    complete = not any(c['status'] == 'unmapped' for c in checks)
    failed = any(c['status'] in {'missing', 'empty', 'modality_mismatch'} for c in checks)
    return {"evaluated": complete, "camera_count": len(requirements), "checks": checks,
            "reason": None if complete else "所需相机模态尚未完成通道映射；请确认配置",
            "severity": "error" if failed else "pass" if complete else "not_evaluated",
            "scope": "explicit required modalities per sensor; no symmetry inference"}


def check_structure(path: str,
                    topics: Optional[Dict[str, TopicInfo]] = None,
                    required_topics: Optional[List[str]] = None,
                    camera_requirements=None, channel_config=None) -> dict:
    """一站式结构检查"""
    fi = check_file_integrity(path)
    if fi["severity"] == "error":
        return {"file_integrity": fi, "severity": "error", "passed": False}
    topics = topics or {}
    tp = check_topics(topics, required_topics) if topics else {"severity": "pass"}
    cp = check_camera_pairs(topics) if topics else {"severity": "pass"}
    cs = check_camera_configuration(topics, camera_requirements, channel_config)
    rank = {"pass": 0, "warning": 1, "error": 2}
    sev = max(fi["severity"], tp.get("severity", "pass"),
              cp.get("severity", "pass"), cs.get("severity", "pass"),
              key=lambda s: rank.get(s, 0))
    return {
        "file_integrity": fi,
        "topics": tp,
        "camera_pairs": cp,
        "camera_configuration": cs,
        "camera_symmetry": {"evaluated": False, "severity": "not_evaluated",
                            "reason": "Legacy inference disabled; see camera_configuration"},
        "severity": sev,
        "passed": sev == "pass",
    }
