"""Direct multi-view VLM candidate scan for spill, hand entry and lid fall."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import cv2
import numpy as np

from .safety_framework import (OpenAICompatibleVisionModel, SafetyCandidate,
                               generate_with_retries)


_SCAN_PROMPT = """You are a policy-blind multi-view observer of a robot task.
TaskGraph is identification/context only and is not evidence:
{task_graph}
Absolute interval: {start:.2f}-{end:.2f}s. Images are chronological contact sheets.
Previous persistent visual state: {previous_state}
Runtime-enabled candidate types: {enabled_types}
Types not listed above are disabled by the TaskGraph and MUST NOT be emitted.

Generate high-recall candidates for exactly these types:
1 spill: a new escaped stream, spout drip, missed receiver, rim overflow, vessel
  leak, or a new persistent wet region caused in this interval. Never re-report an
  old wet mark. Normal liquid entering the intended receiver is not spill.
  ALSO audit post-pour motion: after a vessel has contained or poured liquid,
  inspect its spout and the surface below while it is uprighted, translated,
  rotated, covered, or set down. A tiny residual stream/droplet from the spout is
  a spill. If motion and spout visibility are clear but a tiny droplet cannot be
  resolved, emit low-confidence subtype "residual_spout_drip_review" rather than
  treating the absence of a main pour stream as proof of no spill.
2 hand_intervention: a new human hand/arm entry into the task workspace, direct
  touch, or movement of a task object. Background-only and continued presence are
  state, not new events.
3 lid_fall: the same lid visibly loses support/control, follows passive motion,
  and lands abnormally. A collision may eject it laterally before gravity brings
  it down; lateral translation, little tumbling, or knob-up landing do NOT prove
  control. Controlled human/robot removal requires continuously visible support
  through pickup, transport and placement.
  A frame showing the lid airborne with a clear air gap to vessel, gripper and
  hand, followed by an abnormal resting place, is sufficient fall evidence even
  when the initiating touch was occluded; the cause of support loss is not needed.
  A stable final pose, including knob-up, is expected after many falls and does
  not prove controlled placement.
  If identity/path is broken, emit a candidate but set visibility limitation.

MANDATORY LID CONTINUITY AUDIT: for every lid, compare its support/holder in the
first timestamped frames with the last timestamped frames.  If it changes from
seated/supported to absent, detached, or at a new resting place, you may call it
controlled removal ONLY when the images continuously show a hand or gripper
supporting that same lid through pickup, transport, and stable placement.  If any
part of that path is hidden or identity is uncertain, MUST emit a low-confidence
lid_fall candidate with subtype "unobserved_support_loss", visibility "occluded",
and describe the missing interval.  This candidate means needs-review, not proof
of a fall.  Clear passive descent plus abnormal landing may use higher confidence.
Do not emit this candidate again merely because a lid that was already absent at
the start remains absent at the end.  Persistent unresolved absence is state, not
a new support-loss event; emit only at the newly observed transition.

Do not inspect collision; collision candidates come from geometry/tracking.
Return JSON only. Times are absolute source times:
{{"state":{{"wet_regions":"brief stable state","hand":"absent|present|unknown",
"lids":"brief state","pouring":"active|inactive|unknown",
"liquid_loaded_objects":["objects known or likely to contain liquid"]}},"candidates":[{{"type":"spill|hand_intervention|lid_fall",
"start":0.0,"end":0.0,"subtype":null,"objects":[],"views":[],
"confidence":0.0,"observation":{{"before":"","transition":"","after":"",
"visibility":"clear|partial|occluded"}}}}]}}
"""

_SPILL_REVIEW_PROMPT = """You are a high-frame-rate liquid-transfer reviewer.
TaskGraph is context only: {task_graph}
Absolute interval: {start:.2f}-{end:.2f}s. Timestamped sheets are chronological.
Previous wet-region state: {wet_regions}

The coarse pass observed an active or recently completed liquid transfer but no
credible spill candidate. Inspect the spout, liquid trajectory, receiver rim/wall,
and table at every timestamp. Continue inspecting while a liquid-loaded vessel is
uprighted, moved, rotated, covered, or set down: residual spout droplets and short
trailing streams are spills even after the main pour stops. Report only a
new spill: escaped droplets/stream, missed receiver, rim overflow, or newly formed
persistent wet region. Trace the stream relative to the receiver at each timestamp.
A stream striking the inner wall close to the rim can splash outward: when the
frames do not clearly prove containment, emit a low-confidence candidate with
subtype "rim_or_wall_splash_review". Normal centered flow visibly contained by the
receiver and old wet marks are not events. If resolution, glare, or occlusion
prevents a decision, emit a low-confidence candidate rather than asserting no spill.

Return JSON only:
{{"candidates":[{{"type":"spill","start":0.0,"end":0.0,"subtype":null,
"objects":[],"views":[],"confidence":0.0,"observation":{{"before":"",
"transition":"","after":"","visibility":"clear|partial|occluded"}}}}]}}
"""


def _video_duration(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    cap.release()
    return float(duration)


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    temporary.replace(path)


def make_contact_sheet(path: Path, start: float, end: float,
                       sample_fps: float = 1.5, cols: int = 4,
                       tile_size: tuple[int, int] = (320, 180)) -> bytes:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    for t in np.arange(start, end + 1e-6, 1.0 / sample_fps):
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(float(t) * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        frame = cv2.resize(frame, tile_size)
        cv2.putText(frame, f"{t:.2f}s", (7, 22), cv2.FONT_HERSHEY_SIMPLEX,
                    .58, (0, 255, 255), 2, cv2.LINE_AA)
        frames.append(frame)
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames decoded from {path}")
    rows = (len(frames) + cols - 1) // cols
    blank = np.zeros_like(frames[0])
    frames.extend([blank] * (rows * cols - len(frames)))
    sheet = cv2.vconcat([cv2.hconcat(frames[i * cols:(i + 1) * cols])
                         for i in range(rows)])
    ok, encoded = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("failed to encode contact sheet")
    return encoded.tobytes()


def make_chunked_contact_sheets(path: Path, start: float, end: float,
                                sample_fps: float, chunk_seconds: float = 4.0,
                                tile_size: tuple[int, int] = (480, 270)
                                ) -> list[tuple[float, float, bytes]]:
    """Keep fine spatial evidence legible instead of building one giant sheet."""
    chunks = []
    duration = _video_duration(path)
    cursor = max(0.0, min(start, duration))
    end = max(cursor, min(end, duration))
    while cursor < end - 1e-6:
        chunk_end = min(end, cursor + chunk_seconds)
        chunks.append((cursor, chunk_end, make_contact_sheet(
            path, cursor, chunk_end, sample_fps, cols=4, tile_size=tile_size)))
        cursor = chunk_end
    return chunks


def make_roi_contact_sheet(path: Path, start: float, end: float,
                           roi_xyxy: list[float], sample_fps: float = 10.0,
                           cols: int = 5,
                           tile_size: tuple[int, int] = (480, 300)) -> bytes:
    """Dense, enlarged evidence for a geometry-proposed contact region."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    for t in np.arange(max(0.0, start), end + 1e-6, 1.0 / sample_fps):
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(float(t) * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(round(x)) for x in roi_xyxy]
        margin_x = max(12, int((x2 - x1) * .2))
        margin_y = max(12, int((y2 - y1) * .2))
        x1, x2 = max(0, x1 - margin_x), min(w, x2 + margin_x)
        y1, y2 = max(0, y1 - margin_y), min(h, y2 + margin_y)
        if x2 <= x1 or y2 <= y1:
            continue
        tile = cv2.resize(frame[y1:y2, x1:x2], tile_size)
        cv2.putText(tile, f"{t:.2f}s", (7, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    .62, (0, 255, 255), 2, cv2.LINE_AA)
        frames.append(tile)
    cap.release()
    if not frames:
        raise RuntimeError(f"no ROI frames decoded from {path}")
    rows = (len(frames) + cols - 1) // cols
    blank = np.zeros_like(frames[0])
    frames.extend([blank] * (rows * cols - len(frames)))
    sheet = cv2.vconcat([cv2.hconcat(frames[i * cols:(i + 1) * cols])
                         for i in range(rows)])
    ok, encoded = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("failed to encode ROI contact sheet")
    return encoded.tobytes()


def _has_liquid_transfer_evidence(items: list[dict]) -> bool:
    """Recover active pours when the model's summary state is inconsistent.

    Require either an explicit liquid/stream term, or both a pouring-motion term
    and a receiver term.  This deliberately does not fire for plain kettle
    handling/repositioning.
    """
    text = json.dumps(items, ensure_ascii=False).lower()
    explicit = (
        "liquid", "water stream", "pour stream", "stream emerges", "flowing",
        "droplet", "drip", "液流", "水流", "出水", "滴水", "洒水",
    )
    motion = ("pour", "tilt", "spout", "倒水", "倾斜", "壶嘴")
    receiver = (
        "receiver", "cup", "teapot", "kettle", "rim", "opening",
        "茶杯", "茶壶", "水壶", "杯沿", "壶口",
    )
    return (any(token in text for token in explicit)
            or (any(token in text for token in motion)
                and any(token in text for token in receiver)))


def _explicit_non_event(item: dict) -> bool:
    """Drop only model outputs that explicitly describe their own negative case."""
    kind = str(item.get("type", "")).lower()
    subtype = str(item.get("subtype") or "").lower().replace("-", "_")
    text = json.dumps(item.get("observation") or {}, ensure_ascii=False).lower()
    if kind == "spill" and subtype in {
            "none_observed", "no_spill", "normal_contained_pour"}:
        return True
    if kind == "lid_fall" and subtype in {
            "no_fall", "stable_lid", "controlled_placement"}:
        return True
    if kind == "hand_intervention":
        if subtype in {"background_presence", "no_intervention"}:
            return True
        background = any(x in text for x in ("background", "outside the", "frame edge"))
        explicitly_no_contact = any(x in text for x in (
            "no contact", "without touching", "no approach", "未接触", "未靠近"))
        if background and explicitly_no_contact:
            return True
    return False


def _repeated_unobserved_lid_loss(item: dict, state_before: dict) -> bool:
    """Suppress repeated audits after an already-recorded hidden support loss."""
    if str(item.get("type", "")).lower() != "lid_fall":
        return False
    subtype = str(item.get("subtype") or "").lower().replace("-", "_")
    if subtype != "unobserved_support_loss":
        return False
    prior = str((state_before or {}).get("lids", "")).lower()
    prior_absent = (any(token in prior for token in (
        "lid absent", "lid already absent", "lid not visible",
        "lid off", "lid already off", "lid remains off",
        "persistent absence", "盖子缺失", "壶盖不在", "未见壶盖"))
        or re.search(r"lid.{0,24}(?:absent|off|not visible|unlocated)", prior)
        is not None)
    if not prior_absent:
        return False
    # A lid changing from a known controlled hold to an unobserved state is a
    # genuine support transition and must remain reviewable.
    prior_controlled = any(token in prior for token in (
        "lid held", "holding the lid", "human holds", "human held",
        "gripper holds", "gripper held", "手持", "夹爪持有"))
    if prior_controlled:
        return False
    observation = json.dumps(item.get("observation") or {},
                             ensure_ascii=False).lower()
    # Absent/unresolved -> visibly seated is support recovery, not support loss.
    support_recovered = (any(token in observation for token in (
        "lid seated stably", "lid is seated", "appears covered by a seated lid",
        "stable placement", "盖子稳定盖上", "壶盖已稳定就位"))
        and any(token in observation for token in (
            "already absent", "lid absent", "persistent state",
            "previously absent", "此前缺失")))
    if support_recovered:
        return True
    unchanged = any(token in observation for token in (
        "already absent", "remains absent", "still absent", "no change",
        "throughout interval", "持续缺失", "仍然缺失", "没有变化"))
    unchanged = unchanged or any(token in observation for token in (
        "already off", "remains off", "state unchanged", "no lid motion",
        "no passive descent", "no lid pickup", "no lid visibly",
        "仍未盖上", "没有壶盖运动", "没有被动下落"))
    return unchanged


def _filter_repeated_lid_losses(
        candidates: list[SafetyCandidate],
        window_history: list[dict[str, Any]]) -> list[SafetyCandidate]:
    """Apply the same transition-only rule when resuming old checkpoints."""
    states = {int(item.get("window_index", -1)): dict(
        item.get("state_before") or {}) for item in window_history}
    filtered = []
    for candidate in candidates:
        index = int(candidate.evidence.get(
            "task_context", {}).get("window_index", -1))
        item = {
            "type": candidate.type,
            "subtype": candidate.subtype,
            "observation": candidate.evidence.get("vlm_observation") or {},
        }
        if _repeated_unobserved_lid_loss(item, states.get(index, {})):
            continue
        filtered.append(candidate)
    return filtered


class DirectEventScanner:
    def __init__(self, model: OpenAICompatibleVisionModel,
                 window_seconds: float = 12.0, overlap_seconds: float = 2.0,
                 sample_fps: float = 1.5, spill_review_fps: float = 4.0,
                 enabled_event_types: set[str] | None = None):
        if overlap_seconds >= window_seconds:
            raise ValueError("overlap must be smaller than window")
        self.model = model
        self.window_seconds = window_seconds
        self.overlap_seconds = overlap_seconds
        self.sample_fps = sample_fps
        self.spill_review_fps = spill_review_fps
        self.enabled_event_types = (set(enabled_event_types)
            if enabled_event_types is not None else
            {"spill", "hand_intervention", "lid_fall"})

    @staticmethod
    def discover_views(episode: Path) -> list[tuple[str, Path]]:
        choices = (("front", "faceImg.mp4"), ("left", "leftImg.mp4"),
                   ("right", "rightImg.mp4"), ("front", "cam_front.mp4"))
        found = []
        seen = set()
        for label, filename in choices:
            path = episode / filename
            if path.exists() and label not in seen:
                found.append((label, path)); seen.add(label)
        if not found:
            videos = sorted(episode.glob("*.mp4"))
            if videos:
                found.append(("primary", videos[0]))
        return found

    def scan(self, episode: str | Path, task_graph: dict,
             progress_path: str | Path | None = None) -> list[SafetyCandidate]:
        episode = Path(episode)
        views = self.discover_views(episode)
        if not views:
            raise FileNotFoundError(f"no videos in {episode}")
        duration = min(_video_duration(path) for _, path in views)
        previous_state: dict[str, Any] = {
            "wet_regions": "unknown", "hand": "unknown", "lids": "unknown"}
        candidates: list[SafetyCandidate] = []
        start, index = 0.0, 0
        scan_failures: list[dict[str, Any]] = []
        window_history: list[dict[str, Any]] = []

        def _append_items(items, window_start: float, window_end: float,
                          window_index: int) -> None:
            def _number(value, fallback):
                try:
                    return float(value) if value is not None else float(fallback)
                except (TypeError, ValueError):
                    return float(fallback)
            for item in items or []:
                kind = str(item.get("type", ""))
                if kind not in self.enabled_event_types:
                    continue
                if _explicit_non_event(item):
                    continue
                event_start = min(window_end, max(window_start,
                    _number(item.get("start"), window_start)))
                event_end = min(window_end, max(event_start,
                    _number(item.get("end"), event_start)))
                observation = dict(item.get("observation") or {})
                candidates.append(SafetyCandidate(
                    type=kind, start=event_start, end=event_end,
                    subtype=item.get("subtype"), objects=list(item.get("objects") or []),
                    views=list(item.get("views") or [x[0] for x in views]),
                    source=["vlm_scan"], prior_confidence=_number(
                        item.get("confidence"), .55),
                    evidence={"vlm_observation": observation,
                              "visibility": {"state": observation.get(
                                  "visibility", "unknown")},
                              "task_context": {"window_index": window_index},
                              "scanner_model": self.model.model}))
        if progress_path and Path(progress_path).exists():
            saved = json.loads(Path(progress_path).read_text())
            previous_state = dict(saved.get("state") or previous_state)
            candidates = [SafetyCandidate(**x) for x in saved.get("candidates", [])]
            candidates = [x for x in candidates
                          if x.type in self.enabled_event_types]
            scan_failures = list(saved.get("scan_failures") or [])
            window_history = list(saved.get("window_history") or [])
            candidates = _filter_repeated_lid_losses(candidates, window_history)
            failed_records = [x for x in window_history if not x.get("success", True)]
            if failed_records:
                failed = min(failed_records, key=lambda x: int(x["window_index"]))
                failed_index = int(failed["window_index"])
                candidates = [x for x in candidates if int(
                    x.evidence.get("task_context", {}).get("window_index", -1)
                ) < failed_index]
                scan_failures = [x for x in scan_failures if int(
                    x.get("window_index", -1)) < failed_index]
                window_history = [x for x in window_history if int(
                    x["window_index"]) < failed_index]
                previous_state = dict(failed.get("state_before") or previous_state)
                start, index = float(failed["start"]), failed_index
            else:
                completed = float(saved.get("completed_until", 0.0))
                _append_items(previous_state.pop("candidates", []),
                              max(0.0, completed - self.window_seconds), completed,
                              int(saved.get("window_index", 0)))
                if completed > 0:
                    start = (duration if completed >= duration - 1e-6 else
                             max(0.0, completed - self.overlap_seconds))
                    index = int(saved.get("window_index", 0)) + 1
        while start < duration - 1e-6:
            end = min(duration, start + self.window_seconds)
            state_before = dict(previous_state)
            failures_before = len(scan_failures)
            images = [(f"{label} view {start:.2f}-{end:.2f}s",
                       make_contact_sheet(path, start, end, self.sample_fps))
                      for label, path in views]
            prompt = _SCAN_PROMPT.format(
                task_graph=json.dumps(task_graph, ensure_ascii=False),
                start=start, end=end,
                enabled_types=json.dumps(sorted(self.enabled_event_types)),
                previous_state=json.dumps(previous_state, ensure_ascii=False))
            scan_succeeded = False
            try:
                parsed, raw = generate_with_retries(self.model, prompt, images)
                scan_succeeded = True
            except Exception as exc:
                raw = ""
                failure = {"start": start, "end": end,
                           "window_index": index,
                           "error": type(exc).__name__}
                scan_failures.append(failure)
                # Missing observation is not a negative.  Retain one review
                # candidate per direct-scan class so the final judge can retry
                # on the local evidence clip and the report exposes coverage.
                for kind in sorted(self.enabled_event_types):
                    candidates.append(SafetyCandidate(
                        type=kind, start=start, end=end,
                        views=[x[0] for x in views], source=["vlm_scan_failure"],
                        prior_confidence=.2,
                        evidence={"vlm_observation": {},
                                  "visibility": {"state": "unknown"},
                                  "task_context": {"window_index": index},
                                  "limitations": [f"scan failed: {type(exc).__name__}"]}))
                parsed = {"state": previous_state, "candidates": []}
            previous_state = dict(parsed.get("state") or previous_state)
            nested_items = previous_state.pop("candidates", [])
            primary_items = ((parsed.get("candidates") or [])
                             + list(nested_items or []))
            primary_items = [item for item in primary_items
                             if not _repeated_unobserved_lid_loss(
                                 item, state_before)]
            _append_items(primary_items, start, end, index)
            pouring = str(previous_state.get("pouring", "unknown")).lower()
            def _credible_spill(item: dict) -> bool:
                if str(item.get("type")) != "spill":
                    return False
                try:
                    confidence = float(item.get("confidence") or 0)
                except (TypeError, ValueError):
                    confidence = 0
                subtype = str(item.get("subtype") or "").lower()
                return (confidence >= .45
                        and "check" not in subtype and "unverified" not in subtype)
            if (scan_succeeded
                    and "spill" in self.enabled_event_types
                    and (pouring == "active" or _has_liquid_transfer_evidence(primary_items))
                    and not any(_credible_spill(x) for x in primary_items)):
                focused_images = []
                for label, path in views:
                    focused_images.append((
                        f"{label} temporal overview {start:.2f}-{end:.2f}s",
                        make_contact_sheet(path, start, end,
                                           self.spill_review_fps, cols=8)))
                    for chunk_start, chunk_end, sheet in make_chunked_contact_sheets(
                            path, start, end, min(3.0, self.spill_review_fps)):
                        focused_images.append((
                            f"{label} spatial-detail {chunk_start:.2f}-{chunk_end:.2f}s",
                            sheet))
                focused_prompt = _SPILL_REVIEW_PROMPT.format(
                    task_graph=json.dumps(task_graph, ensure_ascii=False),
                    start=start, end=end, wet_regions=json.dumps(
                        previous_state.get("wet_regions", "unknown"), ensure_ascii=False))
                try:
                    focused, _focused_raw = generate_with_retries(
                        self.model, focused_prompt, focused_images)
                    focused_start = len(candidates)
                    _append_items(focused.get("candidates") or [], start, end, index)
                    for focused_candidate in candidates[focused_start:]:
                        focused_candidate.source = ["vlm_scan", "focused_spill_review"]
                        focused_candidate.evidence.setdefault(
                            "task_context", {})["review_stage"] = "high_fps_spill"
                    if len(candidates) == focused_start:
                        candidates.append(SafetyCandidate(
                            type="spill", start=start, end=end,
                            views=[x[0] for x in views],
                            source=["vlm_scan", "focused_spill_review"],
                            subtype="active_pour_audit", prior_confidence=.2,
                            evidence={
                                "vlm_observation": {
                                    "before": "active liquid transfer observed",
                                    "transition": "focused pass returned no explicit spill",
                                    "after": "requires independent final adjudication",
                                    "visibility": "partial"},
                                "visibility": {"state": "partial"},
                                "task_context": {
                                    "window_index": index,
                                    "review_stage": "high_fps_spill"},
                                "limitations": [
                                    "negative focused scan is not sufficient to suppress final review"]}))
                except Exception as exc:
                    scan_failures.append({"start": start, "end": end,
                                          "window_index": index,
                                          "stage": "spill_review",
                                          "error": type(exc).__name__})
                    candidates.append(SafetyCandidate(
                        type="spill", start=start, end=end,
                        views=[x[0] for x in views], source=["vlm_scan_failure"],
                        prior_confidence=.2,
                        evidence={"vlm_observation": {},
                                  "visibility": {"state": "unknown"},
                                  "task_context": {"window_index": index},
                                  "limitations": ["active-pour focused review failed"]}))
            window_history.append({
                "window_index": index, "start": start, "end": end,
                "state_before": state_before, "state_after": dict(previous_state),
                "success": len(scan_failures) == failures_before})
            if progress_path:
                Path(progress_path).parent.mkdir(parents=True, exist_ok=True)
                _write_json_atomic(Path(progress_path), {
                    "completed_until": end, "state": previous_state,
                    "candidates": [x.to_dict() for x in candidates],
                    "scan_failures": scan_failures, "window_index": index,
                    "window_history": window_history,
                    "last_raw_output": raw})
            if end >= duration:
                break
            start = end - self.overlap_seconds
            index += 1
        return candidates
