"""StructureFilter - 结构过滤器模块

质检流程第一道关卡，检查数据的结构完整性：文件是否可读、元数据是否完整、
必选Topic是否存在、配套关系是否齐全。不解包payload。
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Literal, Any, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from ..data.readers import DataReader
from ..data.ir import MODALITY_IMAGE
from . import topic_semantics


@dataclass
class TopicSnapshot:
    """Topic 快照（不存储消息体，structure.topics 元素）。"""
    name: str                 # 通道名
    msg_type: str             # 原生消息类型
    count: int                # 消息数
    modality: str = "other"   # 规范模态：image/imu/joint_state/cmd/pose/encoded/other


@dataclass
class StructureConfig:
    """结构检查配置"""
    integrity_metadata_count_tolerance: float = None  # 延迟默认# ROS2 metadata.yaml 消息计数偏差容忍比例（M8）
    unpack: bool = None  # 延迟默认# False = 跳过需要反序列化 payload 的字段稳定性检查
    camera_defs: Dict[str, Dict[str, str]] = field(default_factory=dict)  # 相机定义（profile cameras）：通道名/通配 -> {camera: 分组名, type: color/depth}
    # ---- M8 坏块分级（设计表：<1% 轻 / 1-10% 中 / ≥10% 重，整集损坏近似）----
    integrity_corrupt_low: float = None  # 延迟默认# 损坏集占比 ≥ 此值 → warning
    integrity_corrupt_high: float = None  # 延迟默认# 损坏集占比 ≥ 此值 → error
    # ---- M7 尺度突变（rad↔deg 57.3 倍）判定范围 ----
    scale_window_low: float = None  # 延迟默认# 首尾采样幅度比 ≥ 此值 疑尺度突变
    scale_window_high: float = None  # 延迟默认# 幅度比 ≤ 此值（57.3 邻域）
    # ---- 采样突变二分定位（E9 接线：字段集合突变边界定位精度）----
    sampling_binary_search_depth: int = None  # 延迟默认# 相邻采样点间二分定位突变首行的最大迭代数

    disabled_codes: set = None  # 指标级关断（indicators:{code:false}），按检查名映射跳过

    def __post_init__(self):
        from ..config.loader import apply_group_defaults
        apply_group_defaults(self, "structure")
        self.disabled_codes = self.disabled_codes or set()

@dataclass
class Event:
    """结构检查事件（供可视化，structure.events）。"""
    type: str                 # missing_camera_info/rgbd_missing/metadata_count_mismatch/camera_asymmetry/topic_missing/topic_empty/schema_change/schema_mutation
    severity: str             # warning|error
    topic: str                # 通道名
    time: float = 0.0         # 事件时间（相对秒，结构类多为 0）
    detail: Dict[str, Any] = field(default_factory=dict)  # 事件专属字段


@dataclass
class CheckItem:
    """单项检查结果（structure.checks 元素）。"""
    name: str                 # 检查名：file_integrity/format_recognition/metadata_completeness/topic_completeness/
                              # camera_info_pairing/camera_symmetry/rgbd_pairing/schema_<topic>/lerobot_integrity
    passed: bool              # 是否通过
    severity: Literal["pass", "warning", "error"]  # 三档
    message: str              # 人类可读消息
    affected_topics: List[str] = field(default_factory=list)  # 受影响通道
    detail: Dict[str, Any] = field(default_factory=dict)      # 检查专属字段（missing/empty/corrupted_episodes/format_spec 等）


@dataclass
class StructureResult:
    """结构检查总结果（structure 段 format_structure_detail 的对象来源）。"""
    bag_path: str             # 输入路径
    file_exists: bool         # 文件/目录存在
    file_readable: bool       # 可读（含目录含数据文件）
    format_recognized: bool   # 格式识别成功
    topic_count: int          # 通道数
    topics: Dict[str, TopicSnapshot]  # 通道快照
    checks: List[CheckItem]   # 全部检查项
    overall_severity: Literal["pass", "warning", "error"]  # 综合档（checks 最高）
    summary: Dict[str, int]   # {error: N, warning: N, pass: N}
    bag_format: str           # ros1_bag|ros2_bag|mcap|parquet|hdf5|lerobot|mp4|bvh|dataset
    events: List[Event] = field(default_factory=list)  # 结构事件

    def to_dict(self) -> dict:
        return {
            "bag_path": self.bag_path,
            "bag_format": self.bag_format,
            "file_exists": self.file_exists,
            "file_readable": self.file_readable,
            "format_recognized": self.format_recognized,
            "topic_count": self.topic_count,
            "topics": {
                name: {"name": t.name, "msg_type": t.msg_type, "count": t.count}
                for name, t in self.topics.items()
            },
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "severity": c.severity,
                    "message": c.message,
                    "affected_topics": c.affected_topics,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "events": [
                {
                    "type": e.type,
                    "severity": e.severity,
                    "topic": e.topic,
                    "time": e.time,
                    "detail": e.detail,
                }
                for e in self.events
            ],
            "overall_severity": self.overall_severity,
            "summary": self.summary,
        }


class StructureFilter:
    """
    结构过滤器

    检查数据包的结构完整性，不解包payload。
    """

    def __init__(self, required_topics: Optional[List[str]] = None, config: Optional[StructureConfig] = None):
        """
        初始化结构过滤器

        Args:
            required_topics: 必选topic列表，不传则跳过topic完整性检查
            config: 结构检查配置
        """
        self.required_topics = required_topics or []
        self.config = config or StructureConfig()

    def run(self, bag_path: str, reader=None) -> StructureResult:
        """
        执行结构检查

        Args:
            bag_path: bag 文件路径
            reader: 已打开的 DataReader（可选；共享 reader 避免重复打开）

        Returns:
            StructureResult: 检查结果
        """
        checks: List[CheckItem] = []
        topics: Dict[str, TopicSnapshot] = {}
        bag_format = "unknown"
        file_exists = False
        file_readable = False
        format_recognized = False

        created = reader is None
        if created:
            try:
                reader = DataReader.create(bag_path)
                reader.open(bag_path)
            except Exception:
                reader = None

        # ===== 2.1 文件完整性 =====
        file_check = self._check_file_integrity(bag_path)
        checks.append(file_check)
        file_exists = file_check.detail.get("exists", False)
        file_readable = file_check.detail.get("readable", False)

        if not file_readable:
            # 文件不可读，后续检查无意义
            if created and reader is not None:
                reader.close()
            return StructureResult(
                bag_path=bag_path,
                file_exists=file_exists,
                file_readable=False,
                format_recognized=False,
                topic_count=0,
                topics={},
                checks=checks,
                overall_severity="error",
                summary={"error": 1, "warning": 0, "pass": len(checks) - 1},
                bag_format="unknown",
            )

        # ===== 尝试打开 bag，获取 topic 信息 =====
        try:
            topics, bag_format = self._read_bag_structure(reader)
            format_recognized = True
        except Exception as e:
            if created and reader is not None:
                reader.close()
            checks.append(CheckItem(
                name="format_recognition",
                passed=False,
                severity="error",
                message=f"格式无法识别: {e}",
            ))
            return StructureResult(
                bag_path=bag_path,
                file_exists=file_exists,
                file_readable=file_readable,
                format_recognized=False,
                topic_count=0,
                topics={},
                checks=checks,
                overall_severity="error",
                summary={"error": 1, "warning": 0, "pass": len(checks) - 1},
                bag_format="unknown",
            )

        checks.append(CheckItem(
            name="format_recognition",
            passed=True,
            severity="pass",
            message=f"格式识别成功: {bag_format}",
            detail={"format": bag_format},
        ))

        # ===== 2.2 元数据完整性 =====
        metadata_check = self._check_metadata(bag_path, bag_format, topics)
        checks.append(metadata_check)

        # ===== 2.3 Topic 完整性 =====
        topic_check = self._check_topic_completeness(topics)
        checks.append(topic_check)

        # ===== 2.4 配套关系完整性 =====
        pairing_checks = self._check_pairing(topics, bag_format)
        checks.extend(pairing_checks)

        # ===== 2.4 RGBD 配套 =====
        rgbd_check = self._check_rgbd_pairing(topics)
        checks.append(rgbd_check)

        # ===== 2.5 Schema 一致性（采样检查，需解包） =====
        if self.config.unpack:
            schema_checks = self._check_schema_consistency(reader, topics)
        else:
            schema_checks = [CheckItem(
                name="schema_consistency",
                passed=True, severity="pass",
                message="解包已关闭，跳过字段稳定性检查",
                detail={"skipped": True, "reason": "unpack_disabled"},
            )]
        checks.extend(schema_checks)

        # ===== 2.6 LeRobot episode 完整性 =====
        if bag_format == "lerobot":
            _extra = {}
            try:
                _ri = reader.info() if reader is not None else None
                _extra = (_ri.extra or {}) if _ri is not None else {}
            except Exception:
                _extra = {}
            checks.append(self._check_lerobot_integrity(info_extra=_extra))
        # 指标级开关：按检查名→code 映射过滤（indicators:{code:false}）
        if self.config.disabled_codes:
            _NAME2CODE = {"file_integrity": "M8", "lerobot_integrity": "M8",
                          "metadata_completeness": "M8", "format_recognition": "M8",
                          "topic_completeness": "M3", "camera_info_pairing": "C7",
                          "camera_symmetry": "C7", "rgbd_pairing": "C7"}
            checks = [c for c in checks
                      if not (_NAME2CODE.get(c.name) in self.config.disabled_codes
                              or (c.name.startswith("schema_") and "M7" in self.config.disabled_codes))]

        if created and reader is not None:
            reader.close()

        # ===== 收集事件 =====
        events = self._collect_events(checks)

        # ===== 汇总 =====
        overall_severity = self._compute_overall_severity(checks)
        summary = self._compute_summary(checks)

        return StructureResult(
            bag_path=bag_path,
            file_exists=file_exists,
            file_readable=file_readable,
            format_recognized=format_recognized,
            topic_count=len(topics),
            topics=topics,
            checks=checks,
            overall_severity=overall_severity,
            summary=summary,
            bag_format=bag_format,
            events=events,
        )

    # ==================================================================
    # 2.1 文件完整性
    # ==================================================================
    def _check_file_integrity(self, bag_path: str) -> CheckItem:
        """检查文件是否存在、可读"""
        path = Path(bag_path)

        if not path.exists():
            return CheckItem(
                name="file_integrity",
                passed=False,
                severity="error",
                message=f"路径不存在: {bag_path}",
                detail={"exists": False, "readable": False},
            )

        # 目录（数据集）视为合法：含任意受支持数据文件（直接或递归）
        if path.is_dir():
            _SUPPORTED = (".parquet", ".pq", ".bag", ".db3", ".mcap",
                          ".h5", ".hdf5", ".mp4", ".bvh")
            found_files = [f for f in path.rglob("*")
                           if f.is_file() and f.suffix.lower() in _SUPPORTED
                           and "__MACOSX" not in f.parts
                           and not f.name.startswith("._") and f.name != ".DS_Store"]
            if not found_files:
                return CheckItem(
                    name="file_integrity",
                    passed=False,
                    severity="error",
                    message=f"目录中未找到数据文件: {bag_path}",
                    detail={"exists": True, "readable": False},
                )
            kinds = sorted({f.suffix.lower() for f in found_files})
            return CheckItem(
                name="file_integrity",
                passed=True,
                severity="pass",
                message=f"数据目录: {len(found_files)} 个文件 ({', '.join(kinds)})",
                detail={"exists": True, "readable": True, "format": "data_dir",
                        "file_count": len(found_files), "extensions": kinds},
            )

        if not os.access(bag_path, os.R_OK):
            return CheckItem(
                name="file_integrity",
                passed=False,
                severity="error",
                message=f"文件不可读: {bag_path}",
                detail={"exists": True, "readable": False},
            )

        return CheckItem(
            name="file_integrity",
            passed=True,
            severity="pass",
            message="文件存在且可读",
            detail={"exists": True, "readable": True, "size_bytes": path.stat().st_size},
        )

    # ==================================================================
    # 读取 bag 结构（只读 connections，不读消息体）
    # ==================================================================
    def _read_bag_structure(self, reader) -> Tuple[Dict[str, TopicSnapshot], str]:
        """
        读取数据的通道结构（使用已打开的 reader）

        Returns:
            (topics, bag_format)
        """
        info = reader.info()

        topics = {
            t: TopicSnapshot(name=t, msg_type=ti.msg_type, count=ti.count,
                             modality=ti.modality)
            for t, ti in info.topics.items()
        }
        return topics, info.format

    # ==================================================================
    # 2.2 元数据完整性
    # ==================================================================
    def _check_metadata(
        self, bag_path: str, bag_format: str, topics: Dict[str, TopicSnapshot]
    ) -> CheckItem:
        """
        检查元数据完整性

        - 仅 ROS2 sqlite3（.db3）有独立 metadata.yaml，需要检查
        - 其余格式（ros1_bag / mcap / parquet / hdf5 / lerobot / mp4 / bvh）跳过
        """
        if bag_format != "ros2_bag":
            return CheckItem(
                name="metadata_completeness",
                passed=True,
                severity="pass",
                message=f"格式 {bag_format} 无需检查 metadata",
                detail={"skipped": True, "reason": "format_not_applicable"},
            )

        # ROS2 sqlite3 格式检查 metadata.yaml
        bag_path_obj = Path(bag_path)
        metadata_path = bag_path_obj.parent / "metadata.yaml"
        if not metadata_path.exists():
            metadata_path = bag_path_obj.parent.parent / "metadata.yaml"

        has_metadata = metadata_path.exists()

        if not has_metadata:
            return CheckItem(
                name="metadata_completeness",
                passed=False,
                severity="warning",
                message="缺少 metadata.yaml，可追溯性受损",
                detail={"has_metadata_yaml": False},
            )

        # 解析 metadata.yaml 中的消息计数，与实际扫描数对比
        recorded_counts = self._parse_metadata_yaml(metadata_path)
        actual_counts = {name: t.count for name, t in topics.items()}

        inconsistencies = []
        for topic_name, actual in actual_counts.items():
            recorded = recorded_counts.get(topic_name)
            if recorded is not None:
                diff = abs(actual - recorded)
                tolerance = max(1, int(recorded * self.config.integrity_metadata_count_tolerance))
                if diff > tolerance:
                    inconsistencies.append({
                        "topic": topic_name,
                        "recorded": recorded,
                        "actual": actual,
                        "diff": diff,
                    })

        if inconsistencies:
            return CheckItem(
                name="metadata_completeness",
                passed=False,
                severity="warning",
                message=f"{len(inconsistencies)}个topic消息计数与metadata不一致",
                detail={
                    "has_metadata_yaml": True,
                    "inconsistencies": inconsistencies,
                },
            )

        return CheckItem(
            name="metadata_completeness",
            passed=True,
            severity="pass",
            message="metadata.yaml 存在且消息计数一致",
            detail={"has_metadata_yaml": True, "inconsistencies": []},
        )

    def _parse_metadata_yaml(self, metadata_path: Path) -> Dict[str, int]:
        """
        解析 ROS2 metadata.yaml，提取每个 topic 的 message_count

        不依赖 pyyaml，用文本解析实现
        """
        counts: Dict[str, int] = {}
        try:
            text = metadata_path.read_text(encoding="utf-8")
            current_topic = None
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("name:"):
                    value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    current_topic = value
                elif stripped.startswith("message_count:") and current_topic:
                    try:
                        count = int(stripped.split(":", 1)[1].strip())
                        counts[current_topic] = count
                    except ValueError:
                        pass
                    current_topic = None
        except Exception:
            pass
        return counts

    # ==================================================================
    # 2.3 Topic 完整性
    # ==================================================================
    def _check_topic_completeness(self, topics: Dict[str, TopicSnapshot]) -> CheckItem:
        """
        检查必选 topic 是否存在且非空
        """
        if not self.required_topics:
            return CheckItem(
                name="topic_completeness",
                passed=True,
                severity="pass",
                message="未配置必选topic，跳过检查",
                detail={"skipped": True, "reason": "no_required_topics"},
            )

        existing_topics = set(topics.keys())
        missing_topics = []
        empty_topics = []

        for required in self.required_topics:
            if required not in existing_topics:
                missing_topics.append(required)
            elif topics[required].count == 0:
                empty_topics.append(required)

        if missing_topics:
            return CheckItem(
                name="topic_completeness",
                passed=False,
                severity="error",
                message=f"缺少必选topic: {', '.join(missing_topics)}",
                affected_topics=missing_topics,
                detail={"missing": missing_topics, "empty": empty_topics},
            )

        if empty_topics:
            return CheckItem(
                name="topic_completeness",
                passed=False,
                severity="error",
                message=f"必选topic为空: {', '.join(empty_topics)}",
                affected_topics=empty_topics,
                detail={"missing": [], "empty": empty_topics},
            )

        return CheckItem(
            name="topic_completeness",
            passed=True,
            severity="pass",
            message=f"所有必选topic存在且非空 ({len(self.required_topics)}个)",
            detail={"missing": [], "empty": []},
        )

    # ==================================================================
    # 2.4 配套关系完整性
    # ==================================================================
    def _check_pairing(self, topics: Dict[str, TopicSnapshot], bag_format: str = "") -> List[CheckItem]:
        """检查配套关系"""
        results = []

        # camera_info 配套检查（ROS 原生概念，仅对 ROS 类格式生效）
        results.append(self._check_camera_info_pairing(topics, bag_format))

        # 多相机对称性检查
        results.append(self._check_camera_symmetry(topics))

        return results

    def _check_camera_info_pairing(self, topics: Dict[str, TopicSnapshot], bag_format: str = "") -> CheckItem:
        """
        检查每个 image topic 是否有对应的 camera_info

        匹配规则：
        /cam_r/color/image_raw/compressed → /cam_r/color/camera_info
        /cam_r/depth/image_rect_raw/compressedDepth → /cam_r/depth/camera_info

        camera_info 是 ROS 原生消息概念，非 ROS 类格式（parquet/hdf5/mp4 等）
        不含该配套，直接跳过。
        """
        if bag_format not in ("ros1_bag", "ros2_bag"):
            return CheckItem(
                name="camera_info_pairing",
                passed=True,
                severity="pass",
                message=f"格式 {bag_format} 无 camera_info 配套概念，跳过",
                detail={"skipped": True, "reason": f"format_{bag_format}"},
            )

        image_topics = []
        for topic_name in topics:
            if self._is_image_topic(topic_name):
                image_topics.append(topic_name)

        if not image_topics:
            return CheckItem(
                name="camera_info_pairing",
                passed=True,
                severity="pass",
                message="未发现 image topic，跳过 camera_info 配套检查",
                detail={"skipped": True, "reason": "no_image_topics"},
            )

        missing_info = []
        paired_info = []

        for image_topic in image_topics:
            info_topic = self._infer_camera_info_topic(image_topic)
            if info_topic and info_topic in topics:
                paired_info.append({"image": image_topic, "camera_info": info_topic})
            elif info_topic:
                missing_info.append({"image": image_topic, "expected_camera_info": info_topic})
            else:
                missing_info.append({"image": image_topic, "expected_camera_info": "unable_to_infer"})

        if missing_info:
            missing_topics = [m["image"] for m in missing_info]
            return CheckItem(
                name="camera_info_pairing",
                passed=False,
                severity="error",
                message=f"{len(missing_info)}个 image topic 缺少 camera_info",
                affected_topics=missing_topics,
                detail={"missing": missing_info, "paired": paired_info},
            )

        return CheckItem(
            name="camera_info_pairing",
            passed=True,
            severity="pass",
            message=f"所有 image topic 都有对应的 camera_info ({len(paired_info)}对)",
            detail={"missing": [], "paired": paired_info},
        )

    def _camera_groups(self, topics: Dict[str, TopicSnapshot]):
        """识别相机分组 → (groups, by_modality)。

        优先级：profile camera_defs（通配）→ 话题名模式（cam_r/images/…/camera）
        → 图像模态通道回退（如 lerobot image / left_wrist_image）。
        """
        defs = self._match_camera_defs(topics)
        if defs is not None:
            # {camera: {type: [ch]}} -> {camera: set(types)}（symmetry 需要子路径集合）
            return {cam: set(types.keys()) for cam, types in defs.items()}, False
        groups: Dict[str, Set[str]] = defaultdict(set)
        for name in topics:
            camera = self._extract_camera_prefix(name)
            if camera:
                sub_path = self._extract_camera_sub_path(name)
                if sub_path:
                    groups[camera].add(sub_path)
        if groups:
            return groups, False
        for name, ts in topics.items():
            if ts.modality != MODALITY_IMAGE:
                continue
            base = name.rstrip('/')
            cam = re.sub(r'(/|_)(image|rgb|color)$', '', base, flags=re.I) or base
            groups[cam].add("image")
        return groups, True

    def _match_camera_defs(self, topics: Dict[str, TopicSnapshot]):
        """按 profile camera_defs（通道名/通配 → {camera, type}）分组。

        返回 {camera: {type: [channel]}}；未配置或未匹配返回 None。
        """
        defs = getattr(self.config, "camera_defs", None) or {}
        if not defs:
            return None
        import fnmatch as _fn
        out: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        matched = False
        for name in topics:
            cdef = None
            if name in defs:
                cdef = defs[name]
            else:
                for pat, d in defs.items():
                    if _fn.fnmatch(name, pat):
                        cdef = d
                        break
            if cdef:
                matched = True
                cam = cdef.get("camera") or name
                typ = cdef.get("type", "color")
                out[cam][typ].append(name)
        return out if matched else None

    def _camera_types(self, topics: Dict[str, TopicSnapshot]):
        """按相机+类型（color/depth）分组的通道映射，供 RGBD 检查。

        profile camera_defs 优先；否则按名字模式提取 color/depth。
        """
        from_defs = self._match_camera_defs(topics)
        if from_defs is not None:
            return from_defs
        out: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for topic_name in topics:
            camera = self._extract_camera_prefix(topic_name)
            if camera is None:
                continue
            sub = self._extract_camera_sub_path(topic_name)
            if not sub:
                continue
            modality = sub.split('/')[0]
            if modality in ("color", "depth") and self._is_image_topic(topic_name):
                out[camera][modality].append(topic_name)
        return out

    def _check_camera_symmetry(self, topics: Dict[str, TopicSnapshot]) -> CheckItem:
        """
        检查多相机对称性

        识别相机分组（cam_l/cam_r/cam_h），检查各相机是否有相同的子topic集
        """
        # 提取相机分组（名字优先，图像模态回退）
        camera_groups, _ = self._camera_groups(topics)

        if len(camera_groups) < 2:
            return CheckItem(
                name="camera_symmetry",
                passed=True,
                severity="pass",
                message=f"相机数量不足（{len(camera_groups)}个），跳过对称性检查",
                detail={"skipped": True, "reason": "insufficient_cameras", "cameras": list(camera_groups.keys())},
            )

        # 找出所有相机共有的子路径
        all_sub_paths = set()
        for sub_paths in camera_groups.values():
            all_sub_paths.update(sub_paths)

        # 检查每个相机是否缺少某些子路径
        asymmetries = []
        for camera, sub_paths in camera_groups.items():
            missing = all_sub_paths - sub_paths
            if missing:
                asymmetries.append({
                    "camera": camera,
                    "missing_sub_paths": sorted(missing),
                    "has_sub_paths": sorted(sub_paths),
                })

        if asymmetries:
            return CheckItem(
                name="camera_symmetry",
                passed=False,
                severity="warning",
                message=f"多相机不对称: {', '.join(a['camera'] for a in asymmetries)}",
                detail={
                    "cameras": {c: sorted(s) for c, s in camera_groups.items()},
                    "asymmetries": asymmetries,
                },
            )

        return CheckItem(
            name="camera_symmetry",
            passed=True,
            severity="pass",
            message=f"多相机对称: {len(camera_groups)}个相机各有 {len(all_sub_paths)} 类topic",
            detail={
                "cameras": {c: sorted(s) for c, s in camera_groups.items()},
                "asymmetries": [],
            },
        )

    def _check_rgbd_pairing(self, topics: Dict[str, TopicSnapshot]) -> CheckItem:
        """
        检查 RGBD 配套：每个相机的 color image 是否有对应的 depth image

        匹配规则：
        /cam_r/color/image_raw/compressed → 查找 /cam_r/depth/ 下的 image topic
        """
        camera_groups = self._camera_types(topics)
        cam_groups, by_modality = self._camera_groups(topics)

        # 回退到图像模态时：lerobot 类仅 RGB 相机（无 depth），跳过配套检查
        if by_modality and not camera_groups:
            return CheckItem(
                name="rgbd_pairing",
                passed=True,
                severity="pass",
                message=f"识别到 {len(cam_groups)} 个相机（图像模态），均无 depth 通道，跳过RGBD配套检查",
                detail={"skipped": True, "reason": "rgb_only_cameras",
                        "cameras": sorted(cam_groups.keys())},
            )

        if not camera_groups:
            return CheckItem(
                name="rgbd_pairing",
                passed=True,
                severity="pass",
                message="未发现相机topic，跳过RGBD配套检查",
                detail={"skipped": True, "reason": "no_camera_topics"},
            )

        # 全 RGB 相机（无任何 depth 通道）：属合法配置，跳过配套检查
        has_any_depth = any("depth" in mods for mods in camera_groups.values())
        if not has_any_depth:
            return CheckItem(
                name="rgbd_pairing",
                passed=True,
                severity="pass",
                message=f"识别到 {len(camera_groups)} 个相机，均为 RGB（无 depth），跳过RGBD配套检查",
                detail={"skipped": True, "reason": "rgb_only_cameras",
                        "cameras": sorted(camera_groups.keys())},
            )

        missing_depth = []
        paired_rgbd = []
        for camera, modalities in camera_groups.items():
            color_topics = modalities.get("color", [])
            depth_topics = modalities.get("depth", [])
            if color_topics and not depth_topics:
                for t in color_topics:
                    missing_depth.append({"camera": camera, "color_topic": t})
            elif color_topics and depth_topics:
                for ct in color_topics:
                    for dt in depth_topics:
                        paired_rgbd.append({"color": ct, "depth": dt})

        if missing_depth:
            affected = [m["color_topic"] for m in missing_depth]
            return CheckItem(
                name="rgbd_pairing",
                passed=False,
                severity="warning",
                message=f"{len(missing_depth)}个color topic缺少depth配套",
                affected_topics=affected,
                detail={"missing_depth": missing_depth, "paired": paired_rgbd},
            )

        return CheckItem(
            name="rgbd_pairing",
            passed=True,
            severity="pass",
            message=f"所有{len(camera_groups)}个相机均有color+depth配套 ({len(paired_rgbd)}对)",
            detail={"missing_depth": [], "paired": paired_rgbd},
        )

    # ==================================================================
    # 2.5 Schema 一致性（采样检查）
    # ==================================================================
    def _check_schema_consistency(
        self, reader, topics: Dict[str, TopicSnapshot]
    ) -> List[CheckItem]:
        """
        Schema 一致性采样检查（与格式解耦）

        通过读取器的规范值字典，对每个通道抽样前/中/后消息，对比「字段集合」
        是否一致（检测字段缺失/结构突变）。任何支持值访问的格式都适用。
        """
        checks: List[CheckItem] = []
        info = reader.info()

        for topic_name, snap in topics.items():
            ti = info.topics.get(topic_name)
            if ti is None or not ti.value_available:
                continue
            count = ti.count
            if count < 3:
                checks.append(CheckItem(
                    name=f"schema_{topic_name}",
                    passed=True, severity="pass",
                    message=f"{topic_name}: 消息数不足({count})，跳过",
                    affected_topics=[topic_name],
                    detail={"skipped": True, "reason": "insufficient_messages"},
                ))
                continue

            sample_positions = self._get_sample_positions(count)
            try:
                samples = reader.channel_values(topic_name, sample_positions)
            except Exception:
                # 单条畸形消息（如截断 payload）不应让结构段崩溃：跳过该 topic 采样
                checks.append(CheckItem(
                    name=f"schema_{topic_name}",
                    passed=True, severity="pass",
                    message=f"{topic_name}: 值采样失败（畸形消息），跳过 schema 一致性",
                    affected_topics=[topic_name],
                    detail={"skipped": True, "reason": "decode_error"},
                ))
                continue
            if not samples:
                continue
            checks.append(self._compare_value_schema(
                topic_name, samples, count,
                reader=reader, positions=list(sample_positions)))

        if not checks:
            checks.append(CheckItem(
                name="schema_consistency",
                passed=True, severity="pass",
                message="无可采样通道，跳过",
                detail={"skipped": True, "reason": "no_value_channels"},
            ))
        return checks

    def _get_sample_positions(self, count: int) -> List[int]:
        """获取采样位置"""
        sample_positions = [0, 1, 2]
        if count > 6:
            mid = count // 2
            sample_positions.extend([mid - 1, mid, mid + 1])
        sample_positions.extend([count - 3, count - 2, count - 1])
        seen_pos = set()
        unique_positions = []
        for p in sample_positions:
            if 0 <= p < count and p not in seen_pos:
                seen_pos.add(p)
                unique_positions.append(p)
        return unique_positions

    def _compare_value_schema(
        self,
        topic_name: str,
        samples: List[dict],
        total_count: int,
        reader=None,
        positions: Optional[List[int]] = None,
    ) -> CheckItem:
        """
        比对采样帧的「字段集合」一致性（通用 schema 稳定性）

        规范值字典中的字段（position/velocity/effort/joint_q/tau/ang_vel/...）
        集合在时序上不应突变；变化说明结构/编码可能被破坏。
        突变边界用二分定位（sampling_binary_search_depth，E9 接线）。
        """
        if len(samples) < 2:
            return CheckItem(
                name=f"schema_{topic_name}",
                passed=True, severity="pass",
                message=f"{topic_name}: 采样不足，跳过",
                affected_topics=[topic_name],
                detail={"skipped": True, "reason": "insufficient_samples"},
            )

        def fields(d: dict):
            return tuple(sorted(k for k, v in d.items()
                               if isinstance(v, list) and len(v) > 0))

        base = fields(samples[0])
        changes = []
        mutation_points = []
        for i in range(1, len(samples)):
            cur = fields(samples[i])
            if cur != base:
                prev_base = base
                changes.append({
                    "sample_index": i,
                    "field_changes": [
                        {"field": k, "from": base, "to": cur} for k in set(base) | set(cur)
                    ],
                    "severity": "warning",
                })
                mp = {"between": [0, i], "estimated_at": i}
                # 二分细化突变边界：在相邻采样位置间定位字段集合首变行（sampling_binary_search_depth）
                if reader is not None and positions and i > 0:
                    lo, hi = positions[i - 1], positions[i]
                    if hi - lo > 1:
                        est = self._bisect_mutation(
                            reader, topic_name, prev_base, lo, hi)
                        mp["between"] = [lo, est]
                        mp["estimated_at"] = est
                mutation_points.append(mp)
                base = cur

        # 设计表 M7：尺度突变（rad↔deg 57.3 倍）—— 首尾采样幅度比 ∈[40,75] 疑单位突变（重度）
        # 按字段分别计算首尾幅度比（跨字段取全局 max 会被量纲不同的字段稀释而漏检）
        scale_change = None
        try:
            def _field_max_abs(d: dict, field: str) -> float:
                v = d.get(field)
                if isinstance(v, list) and v:
                    vals = [abs(float(x)) for x in v
                            if isinstance(x, (int, float)) and x == x]  # 非 NaN
                    return max(vals) if vals else 0.0
                return 0.0

            s0, sN = samples[0], samples[-1]
            ratio = 0.0
            hit = None
            for k in s0:
                if k not in sN or not isinstance(s0[k], list) or not s0[k]:
                    continue
                a, b = _field_max_abs(s0, k), _field_max_abs(sN, k)
                if a > 0 and b > 0:
                    r = b / a if b >= a else a / b
                    if (self.config.scale_window_low <= r
                            <= self.config.scale_window_high) and r > ratio:
                        ratio, hit = r, k
            if hit is not None:
                scale_change = {
                    "scale_start": round(_field_max_abs(s0, hit), 3),
                    "scale_end": round(_field_max_abs(sN, hit), 3),
                    "ratio": round(ratio, 2),
                    "field": hit,
                    "suspect": "rad↔deg (57.3×)",
                }
        except Exception:
            scale_change = None

        if scale_change is not None:
            return CheckItem(
                name=f"schema_{topic_name}",
                passed=False, severity="error",
                message=f"{topic_name}: 检测到疑似尺度突变（rad↔deg，幅度比 {scale_change['ratio']}×）",
                affected_topics=[topic_name],
                detail={
                    "total_count": total_count,
                    "sample_count": len(samples),
                    "scale_change": scale_change,
                    "changes": changes,
                    "mutation_points": mutation_points,
                },
            )

        if changes:
            return CheckItem(
                name=f"schema_{topic_name}",
                passed=False, severity="warning",
                message=f"{topic_name}: 检测到 {len(changes)} 处字段集合变化",
                affected_topics=[topic_name],
                detail={
                    "total_count": total_count,
                    "sample_count": len(samples),
                    "changes": changes,
                    "mutation_points": mutation_points,
                },
            )

        return CheckItem(
            name=f"schema_{topic_name}",
            passed=True, severity="pass",
            message=f"{topic_name}: 字段集合一致 ({len(samples)}个采样点，{total_count}条消息)",
            affected_topics=[topic_name],
            detail={"total_count": total_count, "sample_count": len(samples)},
        )

    def _bisect_mutation(self, reader, topic_name: str, base_fields: tuple,
                         lo: int, hi: int) -> int:
        """在 [lo, hi) 行区间二分定位字段集合首变行（E9 sampling_binary_search_depth）。

        采样点间字段集合突变时，用 reader.channel_values 读中点帧比对字段集合，
        收敛到第一个与 base 不同的行；迭代数受 config.sampling_binary_search_depth 限制。
        """
        depth = self.config.sampling_binary_search_depth
        while lo < hi and depth > 0:
            mid = (lo + hi) // 2
            try:
                vals = reader.channel_values(topic_name, [mid])
            except Exception:
                break
            if not vals or not vals[0]:
                break
            cur = tuple(sorted(k for k, v in vals[0].items()
                               if isinstance(v, list) and len(v) > 0))
            if cur == base_fields:
                lo = mid + 1
            else:
                hi = mid
            depth -= 1
        return lo

    def _check_lerobot_integrity(self, info_extra=None) -> CheckItem:
        """LeRobot 数据完整性（episode 级）

        依赖 LerobotReader 在 open() 时统计的各 episode 指标：
        - frame_index 连续性（缺口 = 丢帧）
        - 时间戳单调性
        - episode_index / task_index 一致性
        """
        extra = info_extra or {}
        episodes = extra.get("episodes", [])
        corrupted = extra.get("corrupted_episodes", []) or []

        if not episodes and not corrupted:
            return CheckItem(
                name="lerobot_integrity",
                passed=True, severity="pass",
                message="无 episode 元数据，跳过",
                detail={"skipped": True, "reason": "no_episode_meta"},
            )

        if corrupted:
            # 设计表 M8：坏块占比 <1% 轻 / 1-10% 中 / ≥10% 重（整集损坏近似）。
            # 质检三档：<1% → pass（仅记录，不降级）/ 1-10% → warning / ≥10% → error
            corrupt_ratio = len(corrupted) / max(1, len(episodes) + len(corrupted))
            sev = ("error" if corrupt_ratio >= self.config.integrity_corrupt_high
                   else "warning" if corrupt_ratio >= self.config.integrity_corrupt_low
                   else "pass")
            return CheckItem(
                name="lerobot_integrity",
                passed=sev == "pass", severity=sev,
                message=f"检测到 {len(corrupted)} 个损坏 episode 文件: {', '.join(corrupted[:5])}"
                        + (" ..." if len(corrupted) > 5 else ""),
                detail={"corrupted_episodes": corrupted,
                        "corrupt_reason": "unreadable_parquet",
                        "corrupt_ratio": round(corrupt_ratio, 3),
                        "readable_episodes": len(episodes)},
            )

        drift = extra.get("schema_drift", []) or []
        if drift:
            return CheckItem(
                name="lerobot_integrity",
                passed=False, severity="error",
                message=f"检测到 {len(drift)} 个 episode schema 漂移（缺列）",
                detail={"schema_drift": drift, "episode_count": len(episodes)},
            )

        issues = []
        for ep in episodes:
            rec = {"file": ep.get("file", ""), "rows": ep.get("rows", 0)}
            if ep.get("frame_gaps", 0) > 0:
                rec["frame_index_gaps"] = ep["frame_gaps"]
            if ep.get("ts_non_monotonic", 0) > 0:
                rec["timestamp_non_monotonic"] = ep["ts_non_monotonic"]
            if ep.get("episode_values") and len(ep["episode_values"]) > 1:
                rec["episode_index_mixed"] = ep["episode_values"]
            if ep.get("task_values") and len(ep["task_values"]) > 1:
                rec["task_index_mixed"] = ep["task_values"]
            if len(rec) > 2:  # file+rows 之外至少 1 个问题标志即记录（此前 >3 会吞掉单问题集）
                issues.append(rec)

        if issues:
            has_err = any("episode_index_mixed" in r or "task_index_mixed" in r
                          for r in issues)
            return CheckItem(
                name="lerobot_integrity",
                passed=False,
                severity="error" if has_err else "warning",
                message=f"检测到 {len(issues)} 个 episode 数据完整性问题",
                detail={"episode_count": len(episodes), "issues": issues},
            )

        return CheckItem(
            name="lerobot_integrity",
            passed=True, severity="pass",
            message=f"{len(episodes)} 个 episode 数据完整（frame_index/时间戳/标签一致）",
            detail={"episode_count": len(episodes), "episodes": episodes},
        )

    # ==================================================================
    # 事件收集
    # ==================================================================
    def _collect_events(self, checks: List[CheckItem]) -> List[Event]:
        """从所有检查结果中收集事件"""
        events: List[Event] = []

        for check in checks:
            if check.passed:
                continue

            if check.name == "camera_info_pairing":
                for m in check.detail.get("missing", []):
                    events.append(Event(
                        type="missing_camera_info",
                        severity="error",
                        topic=m.get("image", ""),
                        detail={"expected_camera_info": m.get("expected_camera_info", "")},
                    ))

            elif check.name == "rgbd_pairing":
                for m in check.detail.get("missing_depth", []):
                    events.append(Event(
                        type="rgbd_missing",
                        severity="warning",
                        topic=m.get("color_topic", ""),
                        detail={"camera": m.get("camera", "")},
                    ))

            elif check.name == "metadata_completeness":
                for inc in check.detail.get("inconsistencies", []):
                    events.append(Event(
                        type="metadata_count_mismatch",
                        severity="warning",
                        topic=inc.get("topic", ""),
                        detail={
                            "recorded": inc.get("recorded"),
                            "actual": inc.get("actual"),
                            "diff": inc.get("diff"),
                        },
                    ))

            elif check.name == "camera_symmetry":
                for a in check.detail.get("asymmetries", []):
                    events.append(Event(
                        type="camera_asymmetry",
                        severity="warning",
                        topic=a.get("camera", ""),
                        detail={"missing_sub_paths": a.get("missing_sub_paths", [])},
                    ))

            elif check.name == "topic_completeness":
                for t in check.detail.get("missing", []):
                    events.append(Event(
                        type="topic_missing",
                        severity="error",
                        topic=t,
                    ))
                for t in check.detail.get("empty", []):
                    events.append(Event(
                        type="topic_empty",
                        severity="error",
                        topic=t,
                    ))

            elif check.name.startswith("schema_"):
                topic = check.name.replace("schema_", "")
                for change in check.detail.get("changes", []):
                    fields = [fc["field"] for fc in change.get("field_changes", [])]
                    events.append(Event(
                        type="schema_change",
                        severity=change.get("severity", "warning"),
                        topic=topic,
                        time=0.0,
                        detail={
                            "message_index": change.get("message_index"),
                            "fields": fields,
                            "field_changes": change.get("field_changes", []),
                        },
                    ))
                for mp in check.detail.get("mutation_points", []):
                    events.append(Event(
                        type="schema_mutation",
                        severity="error",
                        topic=topic,
                        time=0.0,
                        detail={
                            "between": mp.get("between", []),
                            "estimated_at": mp.get("estimated_at"),
                            "changes": mp.get("changes", []),
                        },
                    ))

        return events

    # ==================================================================
    # 辅助方法
    # ==================================================================
    def _is_image_topic(self, topic_name: str) -> bool:
        return topic_semantics.is_image_topic(topic_name)

    def _infer_camera_info_topic(self, image_topic: str) -> Optional[str]:
        return topic_semantics.infer_camera_info_topic(image_topic)

    def _extract_camera_prefix(self, topic_name: str) -> Optional[str]:
        return topic_semantics.camera_prefix(topic_name)

    def _extract_camera_sub_path(self, topic_name: str) -> Optional[str]:
        """
        提取相机下的子路径

        /cam_r/color/image_raw/compressed → color/image_raw/compressed
        /cam_r/depth/camera_info → depth/camera_info
        /observation/images/rs/cam_high/color → color
        /left_arm/manip_t/sensor/camera/color → color
        """
        parts = topic_name.split('/')
        if len(parts) >= 3 and re.match(r'^cam_[a-zA-Z0-9]+$', parts[1]):
            return '/'.join(parts[2:])
        for i, part in enumerate(parts):
            if part == 'images' and i + 3 < len(parts):
                return '/'.join(parts[i + 3:])
        for i, part in enumerate(parts):
            if part == 'camera' and 1 <= i < len(parts) - 1:
                return '/'.join(parts[i + 1:])
        return None

    # ==================================================================
    # 汇总
    # ==================================================================
    def _compute_overall_severity(self, checks: List[CheckItem]) -> Literal["pass", "warning", "error"]:
        """计算整体严重度"""
        for check in checks:
            if check.severity == "error":
                return "error"
        for check in checks:
            if check.severity == "warning":
                return "warning"
        return "pass"

    def _compute_summary(self, checks: List[CheckItem]) -> Dict[str, int]:
        """计算摘要"""
        summary = {"error": 0, "warning": 0, "pass": 0}
        for check in checks:
            if check.severity in summary:
                summary[check.severity] += 1
            else:
                summary["pass"] += 1
        return summary
# ---- 指标-参数映射（参数↔指标对应；供 --config-dump / 完整性校验）----
INDICATOR_PARAMS: Dict[str, List[str]] = {
    # 键=检测项名（与 indicators.params.INDICATOR_ITEMS 一致）
    "file_integrity": ["integrity_metadata_count_tolerance"],
    # 以下为纯结构判定（配对/缺失/约定检查），无可调参数
    "camera_info_pairing": [], "camera_symmetry": [], "rgbd_pairing": [],
    "topic_completeness": [],
    "unit_convention": [],
    "lerobot_integrity": ["integrity_corrupt_low", "integrity_corrupt_high"],
    "scale_change": ["scale_window_low", "scale_window_high"],
    "_系统采样": ["sampling_binary_search_depth"],
}
