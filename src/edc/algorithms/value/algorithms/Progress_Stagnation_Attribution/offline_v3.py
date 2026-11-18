"""Offline Progress Attribution v3 refinement; never invokes GRM."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from .attributor import EpisodeAttributor

RULE_VERSION = "progress_attribution_v3"
DROP_THRESHOLD = 0.10
RECOVERY_TOLERANCE = 0.03
RECOVERY_SAMPLES = 7
RECOVERY_CONSECUTIVE = 2
RETRY_WINDOW_FRAMES = 90
CLOSE_EVENT_MARGIN_FRAMES = 10
ALREADY_CLOSED_LOOKBACK_FRAMES = 30
# v8 constants were tuned on 30fps hardware (pour-tea/microwave). Frame
# windows must be rescaled for other capture rates so the wall-clock
# semantics (3s retry window, 0.5s delta window, ...) stay fixed.
LEGACY_FPS = 30.0


def _scaled(frames: int, fps: float) -> int:
    return max(1, round(frames * float(fps) / LEGACY_FPS))


def _bound(attributor,frame,legacy_frames,*,inclusive_end=False):
    timeline=getattr(attributor,'timeline',None)
    if timeline is not None:
        return timeline.offset(frame,legacy_frames/LEGACY_FPS,side="right" if inclusive_end else "left") - int(inclusive_end)
    sign=1 if legacy_frames>=0 else -1
    return frame+sign*_scaled(abs(legacy_frames),attributor.fps)


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def _stable_recovery(predictions: list[dict], start: int, threshold: float) -> tuple[bool, int | None, list[dict]]:
    following = [x for x in predictions if int(x.get("frame_index", -1)) > start][:RECOVERY_SAMPLES]
    consecutive = 0
    first = None
    for sample in following:
        if float(sample.get("progress", -1.0)) >= threshold:
            consecutive += 1
            first = int(sample["frame_index"]) if consecutive == 1 else first
            if consecutive >= RECOVERY_CONSECUTIVE:
                return True, first, following
        else:
            consecutive, first = 0, None
    return False, None, following


def _events(attributor: EpisodeAttributor, arm: str, start: int, end: int, kinds: set[str] | None = None) -> list[list[Any]]:
    rows = [[int(frame), kind] for frame, kind in attributor.grip_events.get(arm, []) if start <= frame <= end]
    return [x for x in rows if kinds is None or x[1] in kinds]


def _current_delta(attributor: EpisodeAttributor, arm: str, frame: int) -> float:
    current = attributor.gfeat.grip_cur_rms[arm]
    before = current[max(0,_bound(attributor,frame,-15)):frame]
    after = current[frame:min(len(current),_bound(attributor,frame,15))]
    return float(np.mean(after) - np.mean(before)) if len(before) and len(after) else 0.0


def _already_closed(attributor: EpisodeAttributor, arm: str, frame: int) -> bool:
    gripper = attributor.gfeat.gripper[arm]
    start=max(0,_bound(attributor,frame,-ALREADY_CLOSED_LOOKBACK_FRAMES))
    before = gripper[start:frame]
    timeline=getattr(attributor,'timeline',None)
    complete=(timeline.time_at(frame)>=1.0 if timeline is not None else frame>=_scaled(ALREADY_CLOSED_LOOKBACK_FRAMES,attributor.fps))
    if not complete or not len(before):
        return False
    threshold = float(attributor.gfeat.grip_p5[arm]) * 1.2
    return bool(np.mean(before <= threshold) >= 0.9)


def _incomplete_close(attributor: EpisodeAttributor, arm: str, frame: int) -> bool:
    gripper = attributor.gfeat.gripper[arm]
    after = gripper[frame:min(len(gripper),_bound(attributor,frame,15))]
    threshold = float(attributor.gfeat.grip_p5[arm]) * 1.2
    return bool(len(after) and np.median(after) > threshold)


def _decision(label: str, decision: str, reason: str, **evidence: Any) -> dict:
    return {"decision": decision, "label": label, "reason": reason, "evidence": evidence}


def _refine_close(event: dict, attributor: EpisodeAttributor, predictions: list[dict]) -> dict:
    evidence = event.get("evidence") or {}; arm = str(event.get("arm", "right"))
    close = int(evidence.get("close_frame", event.get("frame", 0))); dip_frame = int(evidence.get("dip_frame", close))
    drop = float(evidence.get("dip", 0.0)); peak = float(evidence.get("peak_prog", 0.0))
    recovered, recovery_frame, following = _stable_recovery(predictions, dip_frame, max(0.0, peak - RECOVERY_TOLERANCE))
    delta = _current_delta(attributor, arm, close); retry = _events(attributor, arm, close + 1, _bound(attributor,close,RETRY_WINDOW_FRAMES,inclusive_end=True), {"open", "close"})
    common = {"progress_drop": drop, "drop_threshold": DROP_THRESHOLD, "recovered": recovered,
              "recovery_frame": recovery_frame, "post_samples": following, "close_current_delta": delta,
              "empty_current_signal": abs(delta) < 0.2, "retry_events": retry}
    if drop < DROP_THRESHOLD:
        return _decision("grasp_progress_fluctuation", "keep", "Close后进度下降不足10%，不确认抓取失败", **common)
    if recovered:
        return _decision("normal_grasp_progress_artifact", "keep", "Close后进度曾下降，但随后稳定恢复", **common)
    if abs(delta) < 0.2 or retry:
        return _decision("failed_grasp_close", "exclude", "进度下降至少10%、未恢复，且存在抓空电流或重试证据", **common)
    return _decision("unconfirmed_grasp_regression", "review", "进度下降至少10%且未恢复，但缺少抓空或重试证据", **common)


def _refine_blocked(event: dict, attributor: EpisodeAttributor, predictions: list[dict]) -> dict:
    evidence = event.get("evidence") or {}; arm = str(event.get("arm", "right"))
    spike = int(evidence.get("spike_start", event.get("frame", 0))); dip_frame = int(evidence.get("dip_frame", spike))
    drop = float(evidence.get("dip", 0.0)); peak = float(evidence.get("peak_prog", 0.0))
    recovered, recovery_frame, following = _stable_recovery(predictions, dip_frame, max(0.0, peak - RECOVERY_TOLERANCE))
    close_events = _events(attributor, arm, _bound(attributor,spike,-CLOSE_EVENT_MARGIN_FRAMES), _bound(attributor,spike,CLOSE_EVENT_MARGIN_FRAMES,inclusive_end=True), {"close"})
    already_closed = _already_closed(attributor, arm, spike); incomplete = _incomplete_close(attributor, arm, spike)
    common = {"progress_drop": drop, "drop_threshold": DROP_THRESHOLD, "recovered": recovered,
              "recovery_frame": recovery_frame, "post_samples": following, "nearby_close_events": close_events,
              "already_closed_30_frames": already_closed, "incomplete_close": incomplete}
    if already_closed or not close_events:
        return _decision("loaded_gripper_current_spike", "keep", "电流尖峰发生时夹爪已闭合，或附近没有新的Close动作", **common)
    if recovered:
        return _decision("normal_blocked_progress_artifact", "keep", "Close附近出现电流尖峰，但进度随后稳定恢复", **common)
    if drop >= DROP_THRESHOLD and incomplete:
        return _decision("failed_grasp_blocked", "exclude", "Close附近受阻、未正常闭合、进度下降至少10%且未恢复", **common)
    return _decision("unconfirmed_grasp_blocked", "review", "存在Close附近电流尖峰，但失败证据不完整", **common)


def _progress_drop(predictions: list[dict], start: int, end: int, fps: float = LEGACY_FPS, timeline=None) -> tuple[float, float, float, int]:
    lo=timeline.offset(start,-70/LEGACY_FPS) if timeline is not None else start-_scaled(70,fps)
    hi=timeline.offset(end,70/LEGACY_FPS,side='right')-1 if timeline is not None else end+_scaled(70,fps)
    before = [x for x in predictions if lo <= int(x.get("frame_index", -1)) <= start]
    during = [x for x in predictions if start <= int(x.get("frame_index", -1)) <= hi]
    pre = max((float(x.get("progress", 0.0)) for x in before), default=0.0)
    trough = min(during, key=lambda x: float(x.get("progress", 0.0)), default={"progress": pre, "frame_index": end})
    post = float(trough.get("progress", pre))
    return max(0.0, pre - post), pre, post, int(trough.get("frame_index", end))


def _refine_early_close(event: dict, predictions: list[dict], fps: float = LEGACY_FPS, timeline=None) -> dict:
    evidence = event.get("evidence") or {}
    start = int(event.get("start_frame", evidence.get("stag_start", 0)))
    end = int(event.get("end_frame", evidence.get("stag_end", start)))
    drop, pre, post, trough = _progress_drop(predictions, start, end, fps,timeline=timeline)
    frame = int((evidence.get("close_deltas") or [{}])[0].get("frame", end))
    drop = max(0.0, pre - post)
    recovered, recovery_frame, following = _stable_recovery(predictions, frame, max(0.0, pre - RECOVERY_TOLERANCE))
    deltas = evidence.get("close_deltas") or []
    no_load = bool(evidence.get("cur_idle")) or any(abs(float(x.get("delta", 999))) < 0.2 for x in deltas)
    common = {"progress_drop": drop, "pre_progress": pre, "trough_progress": post,
              "trough_frame": trough, "drop_threshold": DROP_THRESHOLD, "recovered": recovered,
              "recovery_frame": recovery_frame, "post_samples": following, "no_load_signal": no_load}
    if drop >= DROP_THRESHOLD and not recovered and no_load:
        return _decision("early_close_air_grasp", "exclude", "过早Close后未建立负载、进度下降至少10%且未恢复", **common)
    if recovered:
        return _decision("normal_early_close_artifact", "keep", "过早Close候选后进度稳定恢复", **common)
    return _decision("unconfirmed_early_close_air_grasp", "review", "存在过早Close候选，但不足以自动确认抓空", **common)


def _refine_early_regression(event: dict, evaluation: dict) -> dict:
    evidence = event.get("evidence") or {}; frame = int(event.get("frame", 0))
    regression = next((r for task in evaluation.get("subtask_results", []) for r in task.get("regressions", [])
                       if int(r.get("frame_index", -1)) == frame), {})
    pre = float(regression.get("progress_before", evidence.get("pre_drop_prog", 0.0)))
    trough = float(regression.get("progress_at_regression", pre)); drop = max(0.0, pre - trough)
    threshold = max(0.0, pre - RECOVERY_TOLERANCE); consecutive = 0; recovered = False
    for value in regression.get("progress_after_7", [])[:RECOVERY_SAMPLES]:
        consecutive = consecutive + 1 if float(value) >= threshold else 0
        if consecutive >= RECOVERY_CONSECUTIVE: recovered = True; break
    close_events = [x for x in evidence.get("events_in_win", []) if len(x) >= 2 and x[1] == "close"]
    no_load = not bool(evidence.get("cur_spike"))
    common = {"progress_drop":drop,"pre_progress":pre,"trough_progress":trough,
              "trough_frame":int(regression.get("trough", regression.get("trough_frame", frame))),
              "drop_threshold":DROP_THRESHOLD,"recovered":recovered,"recovery_threshold":threshold,
              "post_samples":regression.get("progress_after_7", [])[:RECOVERY_SAMPLES],
              "no_load_signal":no_load,"close_events":close_events}
    if drop >= DROP_THRESHOLD and not recovered and no_load and close_events:
        return _decision("early_close_air_grasp", "exclude", "过早Close后未建立负载、进度下降至少10%且未恢复", **common)
    if recovered or drop < DROP_THRESHOLD:
        return _decision("normal_early_close_artifact", "keep", "候选事件下降不足10%或随后稳定恢复", **common)
    return _decision("unconfirmed_early_close_air_grasp", "review", "过早Close候选的失败证据不完整", **common)


def refine_episode(episode: Path, output: Path, source: Path | None = None, *, attributor=None) -> dict:
    # External frozen evaluation runs need not live inside the raw episode.
    source = source if source is not None else episode / "subtask_eval"
    attribution = _load(source / "attribution_result.json")
    evaluation = _load(source / "evaluation_summary.json")
    predictions = _load(source / "pred_vllm.json")
    attributor = attributor or EpisodeAttributor(str(episode), fps=float(evaluation.get("video_fps") or 30.0))
    fps = attributor.fps
    result = copy.deepcopy(attribution); result["source_rule_version"] = attribution.get("rule_version")
    result["rule_version"] = RULE_VERSION; result["training_events_v3"] = []
    for index, event in enumerate(attribution.get("proactive_findings", [])):
        kind = event.get("type")
        if kind == "failed_grasp_close": refined = _refine_close(event, attributor, predictions)
        elif kind == "failed_grasp_blocked": refined = _refine_blocked(event, attributor, predictions)
        else: continue
        result["training_events_v3"].append({"event_id": f"proactive_{index}", "source_type": kind,
            "arm": event.get("arm"), "source_evidence": event.get("evidence", {}), **refined})
    for event_id, event in attribution.get("stagnation_results", {}).items():
        if event.get("primary_label") == "early_close_air_grasp":
            result["training_events_v3"].append({"event_id": event_id, "source_type": "early_close_air_grasp",
                "arm": event.get("arm"), "source_evidence": event.get("evidence", {}),                 **_refine_early_close(event, predictions, fps,timeline=getattr(attributor,'timeline',None))})
    for event_id, event in attribution.get("regression_results", {}).items():
        if event.get("primary_label") == "early_close_air_grasp":
            result["training_events_v3"].append({"event_id": event_id, "source_type": "early_close_air_grasp",
                "arm": event.get("arm"), "source_evidence": event.get("evidence", {}),
                **_refine_early_regression(event, evaluation)})
    output.mkdir(parents=True, exist_ok=True)
    (output / "attribution_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
