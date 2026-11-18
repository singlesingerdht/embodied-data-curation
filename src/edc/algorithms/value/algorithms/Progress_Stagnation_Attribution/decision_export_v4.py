"""Export offline Progress Attribution v3 with training filter v4.

This module is deliberately inference-free: it reads saved JSON artifacts only.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FRAME_SCHEMA = "value_frame_decisions_v1"
OBS_SCHEMA = "value_observations_v1"
ERROR_SCHEMA = "value_processing_error_v1"
POLICY_VERSION = "training_filter_v4"
ATTRIBUTION_VERSIONS = {"progress_attribution_v3", "progress_attribution_v9"}
REVIEW_LABELS = {"unconfirmed_load_loss", "unconfirmed_regression"}
NORMAL_LABELS = {
    "normal_load_release", "normal_release_artifact", "idle_motion_misjudge",
    "loaded_motion_misjudge", "normal_contact_success",
}


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def processing_error(episode: str, error_type: str, source: Path, message: str,
                     *, recoverable: bool = True, event_id: str | None = None) -> dict:
    result = {
        "schema_version": ERROR_SCHEMA, "episode": episode,
        "method": "Progress_Stagnation_Attribution", "error_type": error_type,
        "source_path": str(source), "message": message, "recoverable": recoverable,
    }
    if event_id is not None:
        result["event_id"] = event_id
    return result


def _timeline(evaluation: dict) -> tuple[float, int]:
    fps = float(evaluation.get("video_fps") or 30.0)
    duration = float(evaluation.get("video_duration_sec") or 0.0)
    max_end = max((int(s.get("end_frame", 0)) for s in evaluation.get("subtask_results", [])), default=0)
    return fps, max(1, int(round(fps * duration)), max_end + 1)


def _subtask(evaluation: dict, frame: int) -> dict | None:
    for item in evaluation.get("subtask_results", []):
        if int(item.get("start_frame", 0)) <= frame <= int(item.get("end_frame", -1)):
            return item
    return None


def _regression(evaluation: dict, frame: int, timeline=None) -> dict | None:
    task = _subtask(evaluation, frame)
    if not task:
        return None
    candidates = task.get("regressions", [])
    if timeline is not None:
        candidates = [x for x in candidates if 0 <= int(x.get("frame_index", -1)) < timeline.count]
        return min(candidates, key=lambda x: (abs(timeline.time_at(int(x['frame_index'])) - timeline.time_at(frame)), int(x['frame_index'])), default=None)
    return min(candidates, key=lambda x: abs(int(x.get("frame_index", -10**9)) - frame), default=None)


def _event_frame(events: Any, kind: str, fallback: int | None = None) -> int | None:
    values = []
    for event in events or []:
        if isinstance(event, (list, tuple)) and len(event) >= 2 and event[1] == kind:
            values.append(int(event[0]))
    if not values:
        return fallback
    if fallback is None:
        return values[-1]
    before = [x for x in values if x <= fallback]
    return before[-1] if before else min(values, key=lambda x: abs(x - fallback))


def _stable_recovery(predictions: list[dict], start: int, end: int, threshold: float) -> int | None:
    consecutive = 0
    first = None
    for sample in predictions:
        frame = int(sample.get("frame_index", -1))
        if frame < start or frame > end:
            continue
        if float(sample.get("progress", -1.0)) >= threshold:
            consecutive += 1
            first = frame if consecutive == 1 else first
            if consecutive >= 2:
                return first
        else:
            consecutive, first = 0, None
    return None


def _raw_pre_open_current(episode_dir: Path, arm: str, open_frame: int,
                          window: int = 15) -> float | None:
    """Mean arm current over ``window`` frames before ``open_frame``.

    Mirrors GlobalFeatures: joint current RMS with a joint-effort fallback
    when the current channel is absent or flat zero. Returns None when the
    raw state file is unreadable or the pre-Open window is empty.
    """
    if open_frame is None or open_frame <= 0 or arm not in ("left", "right"):
        return None
    raw_path = episode_dir / f"{episode_dir.name}.json"
    try:
        data = _read_json(raw_path).get("data", [])
    except (OSError, json.JSONDecodeError):
        return None
    if len(data) <= 0:
        return None
    cur_key, effort_key = f"follow_{arm}_joint_cur", f"follow_{arm}_joint_effort"
    values = []
    for index in range(max(0, open_frame - window), min(open_frame, len(data))):
        series = data[index].get(cur_key) or []
        try:
            arr = [float(x) for x in series] if series else []
        except (TypeError, ValueError):
            arr = []
        if not arr or all(abs(x) < 1e-9 for x in arr):
            series = data[index].get(effort_key) or []
            try:
                arr = [float(x) for x in series] if series else []
            except (TypeError, ValueError):
                arr = []
        if arr:
            values.append(math.sqrt(sum(x * x for x in arr) / len(arr)))
    if not values:
        return None
    return sum(values) / len(values)


def _open_load_evidence(episode_dir: Path, evidence: dict, arm: str,
                         open_frame: int | None, fps: float = 30.0, pre_open_current=None) -> tuple[bool, dict]:
    """Confirmation gate for Open-branch drops (R01 audit follow-up, v7).

    An object_drop_by_release exclusion requires positive evidence from one of
    two paths:

    1. Current load evidence (works on hardware whose holding current rises
       clearly above the empty-gripper baseline): pre-Open current mean >
       baseline + 2*std, or a strong window spike (cur_max > baseline + 3*std).
    2. Early-deep progress evidence (cross-task, works when currents barely
       modulate): the drop starts low in the subtask (pre_drop_prog <= 0.35)
       and even the best post-drop sample stays >= 0.05 below it.

    R01 audits: path coverage on 13 human-labelled drops across both tasks is
    12/13; the only miss keeps a shallow dip in REVIEW (conservative).
    Without either signal the event is downgraded to REVIEW.
    """
    baseline = float(evidence.get("cur_baseline", 0.0) or 0.0)
    threshold = float(evidence.get("cur_spike_threshold", 0.0) or 0.0)
    cur_std = (threshold - baseline) / 1.5 if threshold > baseline else 0.0
    cur_max = float(evidence.get("cur_max", 0.0) or 0.0)
    pre_drop = float(evidence.get("pre_drop_prog", 0.0) or 0.0)
    max_post = float(evidence.get("max_post_prog", 0.0) or 0.0)
    pre_open_cur = (pre_open_current or _raw_pre_open_current)(episode_dir, arm, int(open_frame or 0),
                                         window=max(1, round(15 * fps / 30.0)) if fps else 15)
    spike_load = cur_max > baseline + 3 * cur_std
    pre_open_load = pre_open_cur is not None and pre_open_cur > baseline + 2 * cur_std
    early_deep_drop = pre_drop <= 0.35 and (pre_drop - max_post) >= 0.05
    detail = {
        "cur_baseline": round(baseline, 6),
        "cur_std": round(cur_std, 6),
        "pre_open_cur": None if pre_open_cur is None else round(pre_open_cur, 6),
        "spike_load": bool(spike_load),
        "pre_open_load": bool(pre_open_load),
        "pre_open_threshold": round(baseline + 2 * cur_std, 6),
        "pre_drop_prog": round(pre_drop, 6),
        "max_post_prog": round(max_post, 6),
        "early_deep_drop": bool(early_deep_drop),
    }
    return bool(spike_load or pre_open_load or early_deep_drop), detail


def _decision(event_id: str, decision: str, label: str, start: int, end: int,
              severity: int, reason: str, source: Path, **extra: Any) -> dict:
    value = {
        "decision_id": event_id, "decision": decision, "label": label,
        "start_frame": int(start), "end_frame": int(end), "severity": int(severity),
        "reason": reason, "evidence_ref": str(source),
    }
    value.update(extra)
    return value


def convert_progress_episode(episode_dir: Path, result_dir: Path | None = None,
                             evaluation_dir: Path | None = None, *, pre_open_current=None, timeline=None) -> tuple[dict, dict, list[dict]]:
    """Return (frame decision record, observation record, processing errors)."""
    episode = episode_dir.name
    base = result_dir if result_dir is not None else episode_dir / "subtask_eval"
    attr_path = base / "attribution_result.json"
    evaluation_dir = evaluation_dir if evaluation_dir is not None else episode_dir / "subtask_eval"
    eval_path = evaluation_dir / "evaluation_summary.json"
    errors: list[dict] = []
    if not attr_path.exists():
        errors.append(processing_error(episode, "attribution_missing", attr_path, "attribution_result.json does not exist"))
        return {}, {}, errors
    if not eval_path.exists():
        errors.append(processing_error(episode, "missing_required_fields", eval_path, "evaluation_summary.json does not exist"))
        return {}, {}, errors
    try:
        attribution = _read_json(attr_path)
    except json.JSONDecodeError as exc:
        errors.append(processing_error(episode, "malformed_result", attr_path, str(exc)))
        return {}, {}, errors
    try:
        evaluation = _read_json(eval_path)
    except json.JSONDecodeError as exc:
        errors.append(processing_error(episode, "malformed_result", eval_path, str(exc)))
        return {}, {}, errors
    if not isinstance(attribution, dict) or not isinstance(evaluation, dict):
        errors.append(processing_error(episode, "missing_required_fields", attr_path, "result root must be an object"))
        return {}, {}, errors
    version = attribution.get("rule_version")
    if version not in ATTRIBUTION_VERSIONS:
        errors.append(processing_error(episode, "unsupported_schema", attr_path, f"unsupported rule_version={version!r}"))
        return {}, {}, errors
    required = ("regression_results", "stagnation_results", "proactive_findings")
    missing = [key for key in required if key not in attribution]
    if missing:
        errors.append(processing_error(episode, "missing_required_fields", attr_path, f"missing fields: {', '.join(missing)}"))
        return {}, {}, errors

    fps, total = (timeline.fps,timeline.count) if timeline is not None else _timeline(evaluation)
    try:
        pred_path = evaluation_dir / "pred_vllm.json"
        predictions = _read_json(pred_path) if pred_path.exists() else []
    except (json.JSONDecodeError, OSError):
        predictions = []
    decisions: list[dict] = []
    observations: list[dict] = []

    for event_id, event in attribution["regression_results"].items():
        if not isinstance(event, dict) or "primary_label" not in event or "frame" not in event:
            errors.append(processing_error(episode, "missing_required_fields", attr_path,
                                           "regression event lacks label or frame", event_id=event_id))
            continue
        label, frame = str(event["primary_label"]), int(event["frame"])
        severity, evidence = int(event.get("severity", 0)), event.get("evidence") or {}
        reg = _regression(evaluation, frame, timeline) or {}
        dip = reg.get("trough_frame", frame)
        if label in REVIEW_LABELS:
            decisions.append(_decision(event_id, "review", label, frame, int(dip), severity,
                "行为可能影响训练质量，但现有信号不足以自动确认", attr_path))
        elif label == "object_drop_by_release":
            open_frame = _event_frame(evidence.get("events_in_win"), "open", frame)
            task = _subtask(evaluation, frame)
            if open_frame is None or task is None:
                decisions.append(_decision(event_id, "review", "failed_grasp_missing_anchor", frame, frame,
                    severity, "掉落判断存在，但缺少可靠时间锚点", attr_path, source_label=label))
                continue
            end = min(total - 1, int(task["end_frame"]))
            threshold = float(evidence.get("pre_drop_prog", 0.0)) - 0.03
            recovered = _stable_recovery(predictions, frame, end, threshold)
            if recovered is not None:
                end = max(int(open_frame), recovered - 1)
            arm = str(event.get("arm") or "")
            load, load_detail = _open_load_evidence(episode_dir, evidence, arm, open_frame,
                                                    fps=fps, pre_open_current=pre_open_current)
            if not load:
                decisions.append(_decision(event_id, "review", "unconfirmed_drop_by_release",
                    int(open_frame), end, min(severity, 2),
                    "Open 回退缺少负载证据：Open 前电流未显著高于空载基线，不能确认掉落", attr_path,
                    source_label=label, recovery_frame=recovered,
                    recovery_threshold=round(threshold, 6), load_evidence=load_detail))
                continue
            decisions.append(_decision(event_id, "exclude", label, int(open_frame), end, severity,
                "明确 Open 后进度未在确认窗口恢复", attr_path,
                recovery_frame=recovered, recovery_threshold=round(threshold, 6),
                load_evidence=load_detail))
        elif label == "early_close_air_grasp":
            # v3 decisions below replace the old unconditional exclusion.
            continue
        elif label == "post_close_cur_drop":
            observations.append(_decision(event_id, "keep", "post_close_current_drop", frame, int(dip), severity,
                "Close 后电流下降本身不具备独立异常语义", attr_path, source_label=label))
        elif label in NORMAL_LABELS:
            family = "motion_misjudge" if label.endswith("motion_misjudge") else label
            observations.append(_decision(event_id, "keep", family, frame, int(dip), severity,
                "归因确认的正常行为或视觉误判", attr_path, source_label=label))

    for event_id, event in attribution["stagnation_results"].items():
        if not isinstance(event, dict) or not all(k in event for k in ("start_frame", "end_frame", "primary_label")):
            errors.append(processing_error(episode, "missing_required_fields", attr_path,
                                           "stagnation event lacks temporal fields", event_id=event_id))
            continue
        start, end = int(event["start_frame"]), int(event["end_frame"])
        task = _subtask(evaluation, start)
        duration = float(event.get("duration_sec", timeline.elapsed(start,end) if timeline is not None else (end-start)/fps))
        task_duration = (timeline.elapsed(int(task["start_frame"]),int(task["end_frame"])) if timeline is not None else (int(task["end_frame"])-int(task["start_frame"]))/fps) if task else 0.0
        ratio = duration / task_duration if task_duration > 0 else 0.0
        # v7: >= 10.0 (was > 10.0). MWR-0029 audit: exactly-10.0s stagnation
        # human-confirmed harmful; a boundary wait should not stay in REVIEW.
        severe = duration >= 10.0 or (duration >= 6.0 and ratio > 0.20)
        medium = duration >= 5.0 or ratio > 0.10
        if severe:
            decisions.append(_decision(event_id, "exclude", "severe_stagnation", start, end,
                int(event.get("severity", 1)), "停滞超过10秒，或至少6秒且占子任务超过20%", attr_path,
                source_label=event["primary_label"], duration_sec=duration, subtask_ratio=round(ratio, 6)))
        elif medium:
            decisions.append(_decision(event_id, "review", "medium_stagnation", start, end,
                int(event.get("severity", 1)), "停滞至少5秒或占子任务超过10%，但未达到自动排除阈值", attr_path,
                source_label=event["primary_label"], duration_sec=duration, subtask_ratio=round(ratio, 6)))
        else:
            observations.append(_decision(event_id, "keep", "short_stagnation", start, end,
                int(event.get("severity", 0)), "停滞不足5秒且未超过子任务10%", attr_path,
                source_label=event["primary_label"], duration_sec=duration, subtask_ratio=round(ratio, 6)))

    for event in attribution.get("training_events_v3", []):
        event_id = str(event.get("event_id", "v3_missing_id"))
        source = event.get("source_evidence") or {}
        evidence = event.get("evidence") or {}
        label, decision = str(event.get("label")), str(event.get("decision"))
        start = source.get("close_frame", source.get("spike_start", event.get("frame")))
        if start is None:
            deltas = source.get("close_deltas") or []
            start = deltas[0].get("frame") if deltas else source.get("stag_start", 0)
        end = source.get("dip_frame", evidence.get("trough_frame", source.get("stag_end", start)))
        item = _decision(event_id, decision, label, int(start), int(end),
            2 if decision != "keep" else 0, str(event.get("reason", "v3离线重归因")), attr_path,
            source_label=event.get("source_type"), rule_evidence=evidence)
        if decision in {"exclude", "review"}:
            decisions.append(item)
        elif decision == "keep":
            observations.append(item)

    # v9 直出 (attributor 产物) 没有 training_events_v3 字段, 其主动抓取发现
    # 仍以 proactive_findings 形式携带, 在这里消费; offline_v3 精炼产物必带
    # training_events_v3 (精炼事件已折叠进去, proactive_findings 原样保留),
    # 此时跳过以避免同一事件双重导出。
    if "training_events_v3" not in attribution:
        for index, event in enumerate(attribution.get("proactive_findings", [])):
            event_id = f"proactive_{index}"
            if not isinstance(event, dict) or "type" not in event:
                errors.append(processing_error(episode, "missing_required_fields", attr_path,
                                               "proactive event lacks type", event_id=event_id))
                continue
            label, evidence = str(event["type"]), event.get("evidence") or {}
            start = evidence.get("close_frame") if label == "failed_grasp_close" else evidence.get("spike_start")
            dip = evidence.get("dip_frame")
            if label in {"failed_grasp_close", "failed_grasp_blocked"} and start is not None and dip is not None:
                decisions.append(_decision(event_id, "exclude", label, int(start), int(dip), int(event.get("severity", 2)),
                    "明确失败抓取动作的事件起点至进度低点", attr_path))
            elif label in {"failed_grasp_close", "failed_grasp_blocked"}:
                fallback = int(event.get("frame", 0))
                decisions.append(_decision(event_id, "review", "failed_grasp_missing_anchor", fallback, fallback,
                    int(event.get("severity", 2)), "失败判断成立，但缺少构建局部区间所需锚点", attr_path,
                    source_label=label))

    def clamp(items: list[dict]) -> list[dict]:
        for item in items:
            item["start_frame"] = max(0, min(total - 1, int(item["start_frame"])))
            item["end_frame"] = max(item["start_frame"], min(total - 1, int(item["end_frame"])))
        return sorted(items, key=lambda x: (x["start_frame"], x["end_frame"], x["decision_id"]))

    method = {"id": "Progress_Stagnation_Attribution", "version": version, "policy_version": POLICY_VERSION}
    common = {"episode": episode, "method": method, "timeline": {"fps": fps, "total_frames": total, **({"source_relative_timestamps":timeline.times.tolist()} if timeline is not None else {})}}
    return ({"schema_version": FRAME_SCHEMA, **common, "decisions": clamp(decisions)},
            {"schema_version": OBS_SCHEMA, **common, "observations": clamp(observations)}, errors)
