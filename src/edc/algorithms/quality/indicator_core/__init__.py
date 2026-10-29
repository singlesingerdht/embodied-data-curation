"""edc.algorithms.quality.indicator_core - 机器人具身数据质检模块

包结构（六层，依赖自上而下）：
- cli/                  命令行入口（qc / bag_info）
- api.py                对外门面（run_qc / detect / run_task）
- tasks/                任务层（TaskRepository 端口 + HttpTaskRepository HTTP 控制面适配器）
- pipeline/ executor/   编排（runner）与并行执行
- filters/ report/      检测段（含 content_checks/ + root_cause）与报告组装
- indicators/           指标注册表（Indicator 枚举，49 项，代码内唯一权威）
- data/                 数据层：ir + readers/（_parsers 为其内部工具）
- config/               配置层：variants + loader + profiles/（机型/场景 YAML）
"""

from .data.ir import (
    Message, TopicInfo, RawMessage, DataInfo,
    MODALITY_IMAGE, MODALITY_IMU, MODALITY_JOINT_STATE, MODALITY_CMD,
    MODALITY_POSE, MODALITY_ENCODED, MODALITY_OTHER, _NUMERIC_MODALITIES,
    classify_channel, detect_format, normalize_time_seconds, decode_image_bytes,
    is_folded_dataset,  # noqa: F401  # 包级 re-export（对外 API）
)
from .data.readers import (
    DataReader, read_data, extract_timestamps,
    RosBagReader, McapReader, ParquetReader, Hdf5Reader,
    LerobotReader, Mp4Reader, BvhReader, DatasetReader,
)
from .filters.structure_filter import StructureFilter, StructureConfig, Event
from .filters.dedup_filter import DedupFilter
from .filters.timing_filter import TimingFilter, TimingConfig
from .filters.sync_filter import SyncFilter, SyncConfig
from .filters.content_checks import ContentFilter, ContentConfig, ContentResult
from .config.variants import (
    VariantProfile, load_profile, detect_robot, available_profiles,
    resolve_and_build, build_structure_filter, build_timing_config,
    build_content_config,
)
from .pipeline.runner import run_qc, run_batch, detect, preprocess, should_preprocess, write_sidecar

__version__ = "0.3.0"

__all__ = [
    "Message", "TopicInfo", "RawMessage", "DataInfo",
    "MODALITY_IMAGE", "MODALITY_IMU", "MODALITY_JOINT_STATE", "MODALITY_CMD",
    "MODALITY_POSE", "MODALITY_ENCODED", "MODALITY_OTHER", "_NUMERIC_MODALITIES",
    "classify_channel", "detect_format", "normalize_time_seconds", "decode_image_bytes",
    "DataReader", "read_data", "extract_timestamps",
    "RosBagReader", "McapReader", "ParquetReader", "Hdf5Reader",
    "LerobotReader", "Mp4Reader", "BvhReader",
    "DatasetReader",
    "StructureFilter", "StructureConfig", "Event",
    "DedupFilter", "TimingFilter", "TimingConfig",
    "SyncFilter", "SyncConfig",
    "ContentFilter", "ContentConfig", "ContentResult",
    "VariantProfile", "load_profile", "detect_robot", "available_profiles",
    "resolve_and_build", "build_structure_filter", "build_timing_config",
    "build_content_config",
    "run_qc", "run_batch", "detect", "preprocess", "should_preprocess", "write_sidecar",
]