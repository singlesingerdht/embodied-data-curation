"""检测结果输出模块 - JSON 格式落盘"""

import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional
from dataclasses import asdict


def convert_to_serializable(obj: Any) -> Any:
    """将数据类转换为 JSON 可序列化的字典"""
    if hasattr(obj, '__dataclass_fields__'):
        return asdict(obj)
    elif isinstance(obj, (list, tuple)):
        return [convert_to_serializable(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    elif hasattr(obj, 'item'):
        return obj.item()
    else:
        return str(obj)


def format_structure_detail(result: Any) -> Dict[str, Any]:
    """格式化 StructureFilter 结果"""
    if not hasattr(result, '__dataclass_fields__'):
        return convert_to_serializable(result)

    return result.to_dict()


def format_timing_detail(result: Any) -> Dict[str, Any]:
    """格式化 TimingFilter 结果，添加详细事件信息"""
    if not hasattr(result, '__dataclass_fields__'):
        return convert_to_serializable(result)

    data = {
        "topic": result.topic,
        "msg_type": result.msg_type,
        "total_count": result.total_count,
        "valid_count": result.valid_count,
        "duration_sec": result.duration_sec,
        "time_range": result.time_range,
        "estimated_freq": result.estimated_freq,
        "expected_T_ms": result.expected_T_ms,
        "expected_T_source": getattr(result, "expected_T_source", ""),
        "severity": result.severity,
        # 各子检测项 severity（供按 topic×问题 统计，一个 topic 一个子问题计一次）
        "sub_severity": {
            "drop": result.drop.severity,
            "silence": result.silence.severity,
            "jitter": result.jitter.severity,
            "frequency_deviation": result.frequency_deviation.severity,
            "out_of_order": result.out_of_order.severity,
            "timestamp_duplicate": result.timestamp_duplicate.severity,
            "delay": result.delay.severity,
        },
    }

    # 掉帧详情
    if result.drop.gap_count > 0:
        data["drop_detail"] = {
            "gap_count": result.drop.gap_count,
            "lost_frames": result.drop.lost_frames,
            "drop_ratio": result.drop.drop_ratio,
            "gaps": [
                {
                    "start": round(g.start, 3),
                    "end": round(g.end, 3),
                    "duration_ms": round(g.duration_ms, 2),
                    "lost_frames": g.lost,
                    "isolated": getattr(g, "isolated", False),
                }
                for g in result.drop.gaps
            ],
        }

    # 静默期详情
    if result.silence.has_silence:
        data["silence_detail"] = {
            "total_silence_sec": result.silence.total_silence_sec,
            "periods": [
                {
                    "start": round(p.start, 3),
                    "end": round(p.end, 3),
                    "duration_sec": round(p.duration_sec, 3),
                }
                for p in result.silence.silence_periods
            ],
        }

    # 抖动详情
    data["jitter_detail"] = {
        "level": result.jitter.level,
        "cv": round(result.jitter.cv, 4),
        "dt_mean_ms": round(result.jitter.dt_mean_ms, 2),
        "dt_std_ms": round(result.jitter.dt_std_ms, 2),
    }

    # 频率偏离详情（注入期望频率后有意义）
    fd = result.frequency_deviation
    if abs(fd.expected_freq - fd.estimated_freq) > 1e-9 or fd.severity != "pass":
        data["frequency_deviation"] = {
            "expected_freq": round(fd.expected_freq, 3),
            "estimated_freq": round(fd.estimated_freq, 3),
            "deviation_ratio": round(fd.deviation_ratio, 4),
            "is_below": fd.is_below,
            "is_above": fd.is_above,
            "severity": fd.severity,
        }

    # 乱序详情
    if result.out_of_order.count > 0:
        data["out_of_order_detail"] = {
            "count": result.out_of_order.count,
            "max_backward_ms": round(result.out_of_order.max_backward_ms, 2),
            "positions": [
                {
                    "time": round(p.time, 3),
                    "backward_ms": round(p.backward_ms, 2),
                }
                for p in result.out_of_order.positions
            ],
        }

    # 时间戳重复详情
    if result.timestamp_duplicate.count > 0:
        data["timestamp_duplicate_detail"] = {
            "count": result.timestamp_duplicate.count,
            "max_consecutive": result.timestamp_duplicate.max_consecutive,
            "positions": [
                {
                    "time": round(p.time, 3),
                    "consecutive": p.consecutive,
                }
                for p in result.timestamp_duplicate.positions
            ],
        }

    # 延迟尖峰详情
    if result.delay.spike_count > 0:
        data["delay_detail"] = {
            "mean_ms": round(result.delay.mean_ms, 2),
            "p95_ms": round(result.delay.p95_ms, 2),
            "p99_ms": round(result.delay.p99_ms, 2),
            "spike_count": result.delay.spike_count,
            "spikes": [
                {
                    "time": round(p.time, 3),
                    "delay_ms": round(p.delay_ms, 2),
                }
                for p in result.delay.spike_positions
            ],
        }

    # 事件时间线
    if result.events:
        data["events_timeline"] = [
            {
                "type": e.type,
                "severity": e.severity,
                "time": round(e.time, 3) if e.time is not None else None,
                "start_time": round(e.start_time, 3) if e.start_time else None,
                "end_time": round(e.end_time, 3) if e.end_time else None,
                "detail": e.detail,
            }
            for e in result.events
        ]

    return data


def format_sync_detail(result: Any) -> Dict[str, Any]:
    """格式化 SyncFilter 结果，添加详细事件信息"""
    if not hasattr(result, '__dataclass_fields__'):
        return convert_to_serializable(result)

    data = {
        "overall_severity": result.overall_severity,
        "summary": result.summary,
    }

    # 覆盖分析详情
    data["coverage"] = {
        "coverage_ratio": round(result.coverage.coverage_ratio, 4),
        "severity": result.coverage.severity,
        "time_range": result.coverage.time_range_all,
        "intersection": result.coverage.intersection,
    }

    if result.coverage.late_start_topics:
        data["coverage"]["late_start_topics"] = [
            {"topic": t.topic, "delay_sec": round(t.delay, 3)}
            for t in result.coverage.late_start_topics
        ]

    if result.coverage.early_end_topics:
        data["coverage"]["early_end_topics"] = [
            {"topic": t.topic, "advance_sec": round(t.advance, 3)}
            for t in result.coverage.early_end_topics
        ]

    # 对齐详情
    data["alignments"] = {}
    for topic, alignment in result.alignments.items():
        data["alignments"][topic] = {
            "mean_delta_ms": round(alignment.mean_delta_ms, 2),
            "p95_delta_ms": round(alignment.p95_delta_ms, 2),
            "severity": alignment.severity,
        }

    # 稳定性详情（M6：含时钟核对字段：实际频率/中位间隔/缺口数）
    data["stability"] = {}
    for topic, stab in result.stability.items():
        stab_info = {
            "mode": stab.mode,
            "offset_ms": round(stab.offset_ms, 2),
            "drift_rate_ms_per_min": round(stab.drift_rate_ms_per_min, 2),
            "severity": stab.severity,
            "median_interval_ms": stab.median_interval_ms,
            "est_freq_hz": stab.est_freq_hz,
            "gap_count": stab.gap_count,
        }
        if stab.has_jump:
            stab_info["jump_positions"] = [round(t, 3) for t in stab.jump_positions]
        data["stability"][topic] = stab_info

    # 切段点
    if result.segment_boundaries:
        data["segment_boundaries"] = [
            {"time": round(s.time, 3), "reason": s.reason, "detail": s.detail}
            for s in result.segment_boundaries
        ]

    # 事件时间线
    if result.events:
        data["events_timeline"] = [
            {
                "type": e.type,
                "severity": e.severity,
                "topic": e.topic,
                "time": round(e.time, 3),
                "detail": e.detail,
            }
            for e in result.events
        ]

    return data


def save_result(
    result: Any,
    output_dir: str,
    bag_name: str,
    filter_name: str,
    detail_formatter: Optional[callable] = None,
) -> str:
    """
    保存检测结果到 JSON 文件

    Args:
        result: 检测结果对象
        output_dir: 输出目录
        bag_name: bag 文件名（不含路径和后缀）
        filter_name: 过滤器名称
        detail_formatter: 详细格式化函数

    Returns:
        str: 输出文件路径
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 生成文件名: {bag_name}_{filter_name}_{timestamp}.json
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{bag_name}_{filter_name}_{timestamp}.json"
    filepath = output_path / filename

    # 转换为可序列化的字典
    if detail_formatter:
        data = detail_formatter(result)
    elif hasattr(result, 'to_dict'):
        data = result.to_dict()
    elif hasattr(result, '__dataclass_fields__'):
        data = asdict(result)
    else:
        data = {"result": str(result)}

    # 添加元数据
    output_data = {
        "metadata": {
            "bag_name": bag_name,
            "filter": filter_name,
            "timestamp": timestamp,
            "generated_at": datetime.now().isoformat(),
        },
        "data": data,
    }

    # 写入文件
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False, default=str)

    return str(filepath)
