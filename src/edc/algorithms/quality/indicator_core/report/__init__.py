"""报告层：report（qc-2.0 组装）+ format（各 filter 结果序列化辅助）。"""
from .report import to_topic_structure, annotate_lerobot_episodes, save_full_report  # noqa: F401
from .format import format_structure_detail, format_timing_detail, format_sync_detail  # noqa: F401
