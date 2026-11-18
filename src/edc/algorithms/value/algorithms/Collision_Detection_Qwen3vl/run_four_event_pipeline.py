#!/usr/bin/env python3
"""End-to-end four-event robot safety pipeline.

Annotations are optional and are opened only after detection artifacts are final.
"""
from __future__ import annotations

import argparse
import cv2
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np

from .four_event_vlm import (DirectEventScanner, make_chunked_contact_sheets,
                             make_contact_sheet, make_roi_contact_sheet)
from .safety_framework import (
    LocalQwenVisionModel, OpenAICompatibleVisionModel, SafetyCandidate,
    UnifiedSafetyAdjudicator,
    load_collision_candidates, load_lid_tracking_candidates,
    load_support_reappearance_candidates,
    merge_safety_candidates,
)
from .task_graph import TaskGraph


PIPELINE_REVISION = "confirmed_hand_contact_or_change_v62"
PHYSICAL_REVISION = "collision_depth_cache_stride_v4"
# These revisions used the same candidate evidence and final VLM prompts.  Only
# deterministic verdict policy changed, so their saved final raw JSON may be
# replayed through the current policy without another paid vision request.
POLICY_REPLAY_COMPATIBLE_REVISIONS = (
    "background_hand_semantic_gate_v61",
    "single_view_hand_contradiction_gate_v60",
    "task_graph_risk_capability_gating_v59",
    "four_event_v4_clause_local_spill_negation_v58",
    "four_event_v4_grounded_spill_normalization_v57",
    "four_event_v4_focused_spill_hypothesis_v56",
    "four_event_v4_contained_flow_negative_gate_v54",
    "four_event_v4_negation_aware_spill_gate_v53",
    "four_event_v4_dense_spill_negative_gate_v52",
    "four_event_v4_visual_negative_lid_gate_v51",
    "four_event_v4_negation_aware_lid_state_v50",
    "four_event_v4_lid_evidence_quality_gate_v49",
    "four_event_v4_final_replay_compat_v48",
    "four_event_v4_lid_gripper_state_v47",
    "four_event_v4_lid_replay_provenance_gate_v46",
    "four_event_v4_lid_ballistic_replay_gate_v45",
    "four_event_v4_lid_ballistic_motion_v44",
    "four_event_v4_lid_outcome_horizon_v43",
    "four_event_v4_mandatory_lid_grasp_audit_v42",
    "four_event_v4_mandatory_lid_grasp_audit_v41",
    "four_event_v4_staged_grasp_audit_replay_v40",
    "four_event_v4_adversarial_grasp_audit_v39",
    "four_event_v4_grasp_outcome_context_v38",
    "four_event_v4_observable_grasp_state_chain_v37",
    "four_event_v4_failed_grasp_impact_v36",
    "four_event_v4_risk_prioritized_adjudication_v35",
    "four_event_v4_merged_verdict_recovery_v34",
    "four_event_v4_occluded_contact_context_v33",
    "four_event_v4_cross_view_lid_negative_v32",
    "four_event_v4_bounded_multiview_lid_evidence_v31",
    "four_event_v4_stationary_far_ellipsoid_filter_v30",
    "four_event_v4_assembly_internal_contact_filter_v29",
    "four_event_v4_projection_only_collision_gate_v28",
    "four_event_v4_authoritative_evidence_protocol_v27",
    "four_event_v4_large_gap_sufficient_v25",
    "four_event_v4_large_gap_negative_v24",
    "four_event_v4_direct_dense_lid_v23",
    "four_event_v4_direct_dense_collision_v22",
    "four_event_v4_controlled_grasp_convergence_v21",
    "four_event_v4_stable_checkpoint_fingerprint_v20",
    "four_event_v4_policy_replay_v19",
    "four_event_v4_taskgraph_allowed_contact_v18",
    "four_event_v4_semantic_controlled_grasp_v17",
)


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                    default=str))
    temporary.replace(path)


def _load_persisted_verdicts(out: Path) -> list[dict]:
    """Recover reusable VLM responses from checkpoints and completed output."""
    recovered: list[dict] = []
    merged_path = out / "events_merged.json"
    if merged_path.exists():
        try:
            merged = json.loads(merged_path.read_text())
        except (OSError, ValueError):
            merged = []
        for event in merged if isinstance(merged, list) else []:
            if isinstance(event, dict):
                recovered.extend(
                    item for item in event.get("adjudications", [])
                    if isinstance(item, dict))
    checkpoint = out / "events_adjudicated.json"
    if checkpoint.exists():
        try:
            current = json.loads(checkpoint.read_text())
        except (OSError, ValueError):
            current = []
        if isinstance(current, list):
            # Put the newest checkpoint last so its exact fingerprint wins for
            # duplicate candidate IDs during replay classification.
            recovered.extend(item for item in current
                             if isinstance(item, dict))
    unique: dict[tuple[str, str], dict] = {}
    for item in recovered:
        key = (str(item.get("candidate_id", "")),
               str(item.get("candidate_fingerprint", "")))
        unique[key] = item
    return list(unique.values())


def _adjudication_priority(candidate: SafetyCandidate) -> tuple[int, float]:
    """Process safety-critical and uncertain visual checks before bulk audits."""
    geometry = dict(candidate.evidence.get("geometry") or {})
    motion = str(geometry.get("motion_state") or "")
    if candidate.type == "collision":
        strong_contact = bool(
            geometry.get("first_contact_transition")
            or geometry.get("depth_contact_consistent")
            or motion in {"direct_lid_contact", "lid_motion_contact_candidate"}
            or motion.startswith("sam3_2d_contact"))
        if strong_contact:
            return (0, candidate.start)
        if (motion == "occluded_tcp_near_cup"
                or geometry.get("visibility") == "unobservable"):
            return (1, candidate.start)
        return (3, candidate.start)
    if candidate.type == "lid_fall":
        return (2 if candidate.prior_confidence >= .3 else 4,
                candidate.start)
    return (5, candidate.start)


def _filter_allowed_assembly_internal_contacts(
        candidates: list[SafetyCandidate], task_graph: dict
        ) -> list[SafetyCandidate]:
    """Remove geometry-only overlap inside a task-defined assembly.

    A lid resting in its vessel, a plug in a socket, or a tool mounted in its
    holder is expected to overlap/touch its parent.  A simultaneous conditional
    rigid-contact prohibition must not turn that steady internal assembly into
    a collision proposal.  External gripper contacts and dedicated separation/
    fall candidates remain untouched.
    """
    allowed_pairs = {
        frozenset((str(policy.get("subject", "")).replace("_", " ").lower(),
                   str(policy.get("object", "")).replace("_", " ").lower()))
        for policy in task_graph.get("contacts", [])
        if str(policy.get("disposition", "")).lower() == "allowed"
        and str(policy.get("interaction_mode", "")).lower() == "assembly"
    }
    if not allowed_pairs:
        return candidates

    def semantic_name(value: str) -> str:
        return str(value).split(":", 1)[-1].replace("_", " ").strip().lower()

    result = []
    for candidate in candidates:
        geometry = dict(candidate.evidence.get("geometry") or {})
        pair = frozenset(semantic_name(x) for x in candidate.objects)
        internal_overlap = (
            candidate.type == "collision"
            and geometry.get("motion_state") == "forbidden_target_contact"
            and pair in allowed_pairs)
        if not internal_overlap:
            result.append(candidate)
    return result


def _filter_stationary_far_ellipsoid_contacts(
        candidates: list[SafetyCandidate]) -> list[SafetyCandidate]:
    """Suppress isolated long-axis ellipsoid overlaps with no contact motion.

    Target ellipsoids are deliberately conservative.  For large vessels their
    long axes can overlap while the visible objects remain tens of centimetres
    apart.  A first-contact onset attributed to the active target is physically
    inconsistent when that target did not move, the centers remain far apart,
    and neither depth nor repeated observations support contact.
    """
    result = []
    for candidate in candidates:
        geometry = dict(candidate.evidence.get("geometry") or {})
        spurious = (
            candidate.type == "collision"
            and geometry.get("motion_state") == "forbidden_target_contact"
            and float(geometry.get("center_gap") or 0.0) >= 0.18
            and float(geometry.get("subject_step_m") or 0.0) < 0.002
            and int(geometry.get("candidate_count") or 1) <= 1
            and not geometry.get("depth_contact_consistent"))
        if not spurious:
            result.append(candidate)
    return result


def _enrich_lid_gripper_state(candidates: list[SafetyCandidate],
                              episode: Path) -> None:
    """Attach measured gripper closure/load evidence when episode state exists."""
    payload = None
    state_path = None
    for path in episode.glob("*.json"):
        try:
            parsed = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (isinstance(parsed, dict) and isinstance(parsed.get("data"), list)
                and (parsed.get("timestamps") is not None or float(parsed.get("fps") or 0) > 0)):
            payload, state_path = parsed, path
            break
    if payload is None:
        return
    fps = float(payload.get("fps") or 0)
    frames = payload["data"]
    import numpy as np
    times = np.asarray(payload.get("timestamps") if payload.get("timestamps") is not None
                       else np.arange(len(frames))/fps, dtype=float)
    if times.shape != (len(frames),) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('Invalid physical state source timeline')

    def median_window(key: str, start: float, end: float,
                      effort: bool = False) -> float | None:
        lo = int(np.searchsorted(times, start, side="left"))
        hi = int(np.searchsorted(times, end, side="right"))
        values = []
        for frame in frames[lo:hi]:
            value = frame.get(key)
            if effort:
                value = value[-1] if isinstance(value, list) and value else None
            if isinstance(value, (int, float)):
                values.append(float(value))
        return statistics.median(values) if values else None

    for candidate in candidates:
        if candidate.type != "lid_fall":
            continue
        arms = []
        for arm in ("left", "right"):
            pre = median_window(f"follow_{arm}_gripper",
                                candidate.start - 1.0, candidate.start)
            post = median_window(f"follow_{arm}_gripper",
                                 candidate.end, candidate.end + 1.0)
            effort = median_window(f"follow_{arm}_joint_effort",
                                   candidate.end, candidate.end + 1.0,
                                   effort=True)
            if pre is None or post is None:
                continue
            arms.append({
                "arm": arm,
                "pre_gripper_position": round(pre, 5),
                "post_gripper_position": round(post, 5),
                "closure_drop": round(pre - post, 5),
                "post_terminal_effort": (round(effort, 5)
                                           if effort is not None else None),
                "empty_closure": bool(
                    pre >= .05 and post <= .02 and pre - post >= .05
                    and effort is not None and abs(effort) <= .15),
            })
        if not arms:
            continue
        strongest = max(arms, key=lambda item: item["closure_drop"])
        candidate.evidence["robot_state"] = {
            "source": state_path.name,
            "time_source": payload.get("time_source", "legacy_playback"),
            "arms": arms,
            "active_arm": strongest["arm"],
            "empty_closure_during_lid_transition": strongest["empty_closure"],
            "interpretation": (
                "measured near-full closure with near-zero terminal load is "
                "evidence against a secured rigid grasp"
                if strongest["empty_closure"] else
                "gripper state does not independently prove an empty grasp"),
        }


def _run_collision(args, collision_out, model=None):
    from .physical_worker import run_physical
    return run_physical(args.episode.parent, collision_out, model=model)


def _evidence_images(episode: Path, candidate: SafetyCandidate,
                     sample_fps: float) -> list[tuple[str, bytes]]:
    views = DirectEventScanner.discover_views(episode)
    start, end = max(0.0, candidate.start - 2.0), candidate.end + 2.0
    # Collision gets a cheap multi-view coarse pass first. Strong geometry or
    # unresolved contact is still escalated to the dedicated 10 fps ROI pass.
    effective_fps = (min(sample_fps, 1.5)
                     if candidate.type == "collision" else sample_fps)
    images = []
    for label, path in views:
        for chunk_start, chunk_end, sheet in make_chunked_contact_sheets(
                path, start, end, effective_fps):
            images.append((f"{label} evidence {chunk_start:.2f}-{chunk_end:.2f}s",
                           sheet))
    return images


def _collision_roi_images(episode: Path, candidate: SafetyCandidate,
                          sample_fps: float = 10.0) -> list[tuple[str, bytes]]:
    detail = dict(candidate.evidence.get("geometry") or {})
    roi = detail.get("roi_xyxy")
    view = str(detail.get("view") or "faceImg")
    primary_path = episode / (view if view.endswith(".mp4") else f"{view}.mp4")
    if not primary_path.exists() or (roi is not None and len(roi) != 4):
        return []
    discovered = DirectEventScanner.discover_views(episode)
    review_views = discovered or [(view, primary_path)]
    # An occlusion bridge deliberately spans an interval whose actual contact
    # instant is unknown.  Sparse geometry anchors are therefore unsafe: a
    # brief rim graze can fall in the gaps between sheets.  Cover that interval
    # continuously in every synchronized view.  Two-second sheets keep the
    # image count bounded while retaining enough temporal resolution for light
    # contacts that do not visibly displace the passive object.
    if detail.get("occlusion_bridge") or detail.get("visibility") == "unobservable":
        continuous_start = max(0.0, candidate.start - .4)
        continuous_end = candidate.end + .4
        images = []
        for review_label, path in review_views:
            chunk_start = continuous_start
            while chunk_start < continuous_end - 1e-6:
                chunk_end = min(continuous_end, chunk_start + 2.0)
                try:
                    is_primary = path.resolve() == primary_path.resolve()
                    sheet = (make_roi_contact_sheet(
                                path, chunk_start, chunk_end, roi, sample_fps)
                             if roi is not None and is_primary else
                             make_chunked_contact_sheets(
                                path, chunk_start, chunk_end,
                                min(sample_fps, 8.0),
                                chunk_seconds=2.0)[0][2])
                except (RuntimeError, IndexError):
                    chunk_start = chunk_end
                    continue
                evidence_scope = ("ROI" if roi is not None and is_primary else
                                  "SYNCHRONIZED FULL VIEW")
                images.append((
                    f"{review_label} CONTINUOUS HIGH-FPS CONTACT {evidence_scope} "
                    f"{chunk_start:.2f}-{chunk_end:.2f}s; inspect every adjacent "
                    "frame for the first gap closure, including light grazing "
                    "without passive-object displacement",
                    sheet))
                chunk_start = chunk_end
        return images
    anchors = list(detail.get("evidence_hypothesis_times_sec") or [])
    anchors += list(detail.get("candidate_times_sec") or [])
    if not anchors:
        anchors = [(candidate.start + candidate.end) / 2]
    selected = []
    for anchor in sorted(float(x) for x in anchors):
        if not selected or anchor - selected[-1] >= .8:
            selected.append(anchor)
    max_anchors = (5 if detail.get("motion_state") == "direct_lid_contact"
                   else 4)
    if len(selected) > max_anchors:
        indices = sorted({round(i * (len(selected) - 1) / (max_anchors - 1))
                          for i in range(max_anchors)})
        selected = [selected[i] for i in indices]
    images = []
    for anchor in selected:
        start, end = max(0.0, anchor - .6), anchor + .6
        for review_label, path in review_views:
            try:
                is_primary = path.resolve() == primary_path.resolve()
                sheet = (make_roi_contact_sheet(
                            path, start, end, roi, sample_fps)
                         if roi is not None and is_primary else
                         make_chunked_contact_sheets(
                            path, start, end, min(sample_fps, 8.0),
                            chunk_seconds=2.0)[0][2])
            except (RuntimeError, IndexError):
                continue
            evidence_scope = ("ROI" if roi is not None and is_primary else
                              "SYNCHRONIZED FULL VIEW")
            images.append((f"{review_label} HIGH-FPS CONTACT {evidence_scope} "
                           f"{start:.2f}-{end:.2f}s; inspect gap closure and "
                           "reaction frame-by-frame", sheet))
    return images


def _selected_roi_sheet(path: Path, times: list[float], roi: list[float],
                        tile_size: tuple[int, int] = (480, 300)) -> bytes:
    """Put decisive before/contact/outcome frames in one readable comparison."""
    cap = cv2.VideoCapture(str(path)); fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    tiles = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, round(float(t) * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(round(x)) for x in roi]
        mx, my = max(20, int((x2-x1)*.28)), max(20, int((y2-y1)*.28))
        x1, x2 = max(0, x1-mx), min(w, x2+mx)
        y1, y2 = max(0, y1-my), min(h, y2+my)
        tile = cv2.resize(frame[y1:y2, x1:x2], tile_size)
        cv2.putText(tile, f"{t:.2f}s", (8, 26), cv2.FONT_HERSHEY_SIMPLEX,
                    .68, (0,255,255), 2, cv2.LINE_AA)
        tiles.append(tile)
    cap.release()
    if not tiles:
        raise RuntimeError("no selected collision frames decoded")
    cols = min(4, len(tiles)); rows = (len(tiles)+cols-1)//cols
    blank = np.zeros_like(tiles[0]); tiles += [blank]*(rows*cols-len(tiles))
    sheet = cv2.vconcat([cv2.hconcat(tiles[i*cols:(i+1)*cols])
                         for i in range(rows)])
    ok, payload = cv2.imencode(".jpg", sheet,
                               [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok: raise RuntimeError("failed to encode selected collision sheet")
    return payload.tobytes()


def _local_collision_images(episode: Path, candidate: SafetyCandidate
                            ) -> list[tuple[str, bytes]]:
    """Small evidence set tuned for an 8B local judge, not a remote large model."""
    g = dict(candidate.evidence.get("geometry") or {})
    roi = g.get("roi_xyxy"); view = str(g.get("view") or "faceImg")
    path = episode / (view if view.endswith(".mp4") else f"{view}.mp4")
    if not path.exists() or not isinstance(roi, list) or len(roi) != 4:
        return _collision_roi_images(episode, candidate)
    anchors = sorted(set(float(x) for x in (
        g.get("evidence_hypothesis_times_sec") or
        g.get("candidate_times_sec") or [(candidate.start+candidate.end)/2])))
    motion = str(g.get("motion_state") or "")
    if g.get("grasp_outcome_interval_expanded"):
        # Preserve contact onset AND the later pickup/mis-seat outcome.
        times = [max(0,candidate.start-.6)] + anchors
        if candidate.end not in times: times.append(candidate.end)
        if len(times) > 8:
            ids = sorted({round(i*(len(times)-1)/7) for i in range(8)})
            times = [times[i] for i in ids]
        label = ("PRIMARY ROI: compare before contact, gripper retreat/release, and "
                 "late lid outcome. Contact without pickup followed by persistent "
                 "lid offset or rim gap is grasp-impact collision; cite timestamps.")
    else:
        # One continuous closest-approach sequence is easier for an 8B model than
        # many separate multi-view sheets.
        start=max(0,candidate.start-.6); end=candidate.end+.6
        times=list(np.arange(start,end+1e-6,.25))
        long_unobservable = bool(
            end-start > 8.0 and (g.get("adjudication_interval_expanded")
                                 or g.get("visibility") == "unobservable"))
        if len(times)>12 and not long_unobservable:
            ids=sorted({round(i*(len(times)-1)/11) for i in range(12)})
            times=[times[i] for i in ids]
        label=("PRIMARY ROI continuous 4fps: compare teapot and cup boundaries at "
               "each timestamp. A light rigid graze is contact even if the cup "
               "does not tip; cite gap closure and any relative cup motion.")
    if (not g.get("grasp_outcome_interval_expanded")
            and len(times) > 12):
        images = []
        for i in range(0, min(len(times), 48), 12):
            part = times[i:i+12]
            images.append((
                label + (" Contact time is unknown because the carried object was "
                         "occluded at the geometry anchor; inspect every tile and "
                         "do not infer safety from an unchanged cup alone. ") +
                f"Chunk {part[0]:.2f}-{part[-1]:.2f}s.",
                _selected_roi_sheet(path, part, roi)))
    else:
        images = [(label, _selected_roi_sheet(path,times,roi))]
    # At most two compact synchronized overviews prevent the local judge from
    # claiming that apparent ROI contact is unresolved perspective overlap.
    for aux_label, aux_path in ([] if (g.get("grasp_outcome_interval_expanded")
                                         or len(images) > 1)
                                else DirectEventScanner.discover_views(episode)):
        if aux_path.resolve() == path.resolve():
            continue
        try:
            images.append((
                f"{aux_label} synchronized overview {times[0]:.2f}-{times[-1]:.2f}s",
                make_contact_sheet(aux_path, times[0], times[-1],
                                   sample_fps=1.0, cols=4,
                                   tile_size=(320,180))))
        except RuntimeError:
            continue
        if len(images) >= 3:
            break
    return images


def _needs_collision_dense_evidence(candidate: SafetyCandidate) -> bool:
    if candidate.type != "collision":
        return False
    geometry = dict(candidate.evidence.get("geometry") or {})
    sources = set(geometry.get("evidence_sources") or [])
    return bool(geometry.get("first_contact_transition")
                or geometry.get("depth_contact_consistent")
                or float(geometry.get("penetration_growth_m") or 0) >= .002
                or geometry.get("motion_state") in {
                    "direct_lid_contact", "lid_motion_contact_candidate",
                    "unverified_grasp_contact"}
                or geometry.get("dense_temporal_sampling")
                or (geometry.get("roi_xyxy")
                    and int(geometry.get("candidate_count") or 0) >= 3
                    and "sam3_2d_contact" in sources))


def _needs_collision_recheck(candidate: SafetyCandidate, verdict) -> bool:
    return (verdict.verdict != "confirmed"
            and _needs_collision_dense_evidence(candidate))


def _needs_grasp_outcome_audit(candidate: SafetyCandidate, verdict) -> bool:
    geometry = dict(candidate.evidence.get("geometry") or {})
    motion = str(geometry.get("motion_state") or "")
    return bool(candidate.type == "collision"
                and geometry.get("grasp_outcome_interval_expanded")
                and (verdict.verdict in {"confirmed", "needs_review"}
                     or motion == "direct_lid_contact"))


def _resolve_grasp_outcome_audit(initial, audit):
    if initial.verdict == "confirmed" and audit.verdict == "rejected":
        audit.verdict = "needs_review"
        audit.limitations = list(audit.limitations) + [
            "initial and adversarial grasp-impact verdicts disagree; collision cannot remain confirmed"]
    return audit


def _lid_dense_images(episode: Path, candidate: SafetyCandidate,
                      sample_fps: float = 12.0) -> list[tuple[str, bytes]]:
    start, end = max(0.0, candidate.start - 1.0), candidate.end + 1.0
    images = []
    for label, path in DirectEventScanner.discover_views(episode):
        is_primary = str(label).lower() in {"front", "face", "faceimg", "primary"}
        view_fps = sample_fps if is_primary else min(sample_fps, 8.0)
        tile_size = (420, 236) if is_primary else (360, 203)
        for chunk_start, chunk_end, sheet in make_chunked_contact_sheets(
                path, start, end, view_fps, chunk_seconds=2.0,
                tile_size=tile_size):
            images.append((
                f"{label} HIGH-FPS LID CONTINUITY {view_fps:.0f}fps "
                f"{chunk_start:.2f}-{chunk_end:.2f}s; "
                "track the same lid through support loss, possible lateral "
                "ejection/ballistic motion, descent and landing; controlled "
                "handling requires continuous support or rigid co-motion with "
                "the gripper (stable relative pose and orientation). When the "
                "grasp interface is occluded, consecutive independent lid "
                "rotation/tumbling or ballistic translation relative to both "
                "vessel and gripper, followed by impact/settling at an abnormal "
                "rest, is sufficient visual proof of an uncontrolled fall; do "
                "not demand a separately visible air gap in that case. Mere "
                "absence of a visible gripper is not sufficient: cite the "
                "independent motion and landing frames. Stable final "
                "rest does not prove controlled placement",
                sheet))
    return images


def _local_lid_images(episode: Path, candidate: SafetyCandidate
                     ) -> list[tuple[str, bytes]]:
    """Compact lid continuity evidence that an 8B model can read reliably."""
    start, end = max(0.0, candidate.start - 1.5), candidate.end + 3.0
    views = DirectEventScanner.discover_views(episode)
    images = []
    # Wrist/right view is much closer to the lid in this dataset.  Put the
    # complete before/after sequence in the FIRST image: local 8B models often
    # decide from image one and otherwise ignore a later decisive overview.
    ordered = sorted(views, key=lambda item: (
        0 if str(item[0]).lower() == "right" else
        1 if str(item[0]).lower() == "front" else 2))
    for index, (label, path) in enumerate(ordered[:2]):
        try:
            images.append((
                f"{label} COMPLETE LID OUTCOME {start:.2f}-{end:.2f}s: read all "
                "tiles left-to-right/top-to-bottom; compare seated lid before "
                "support loss with detached abnormal rest afterward; controlled "
                "placement requires continuously visible gripper support; cite "
                "exact timestamps and do not stop after the first row",
                make_contact_sheet(path,start,end,2.0 if index == 0 else 1.0,
                                   cols=4,tile_size=(480,270))))
        except RuntimeError:
            continue
    return images


def _needs_lid_recheck(candidate: SafetyCandidate, verdict) -> bool:
    return (verdict.verdict != "confirmed"
            and _needs_lid_dense_evidence(candidate))


def _needs_lid_dense_evidence(candidate: SafetyCandidate) -> bool:
    return (candidate.type == "lid_fall"
            and (candidate.subtype == "unobserved_support_loss"
                 or "tracking" in candidate.source))


def _needs_spill_confirmation(candidate: SafetyCandidate, verdict) -> bool:
    """Use dense evidence for confirmations and unresolved liquid reviews."""
    if candidate.type != "spill":
        return False
    if verdict.verdict == "confirmed":
        return True
    review_subtypes = {
        "active_pour_audit", "residual_spout_drip_review",
        "rim_or_wall_splash_review", "insufficient_transfer_review",
    }
    limitations = " ".join(verdict.limitations).lower()
    unavailable = "adjudication unavailable" in limitations
    return (verdict.verdict == "needs_review"
            and candidate.subtype in review_subtypes and not unavailable)


def _needs_hand_confirmation(candidate: SafetyCandidate, verdict) -> bool:
    """Confirmed hand events need dense evidence for contact attribution."""
    return (candidate.type == "hand_intervention"
            and verdict.verdict == "confirmed")


def _needs_hand_dense_evidence(candidate: SafetyCandidate) -> bool:
    return candidate.type == "hand_intervention"


def _needs_spill_dense_evidence(candidate: SafetyCandidate) -> bool:
    return candidate.type == "spill"


def _resolve_dense_spill_negative(verdict):
    """Reject dense spill reviews supported only by sub-resolution doubt.

    A safety review is warranted when a visible trajectory may cross the
    receiver boundary or a new wet region may have formed.  It is not
    warranted merely because a microscopic droplet can never be excluded from
    ordinary video.  This gate is deliberately one-sided: any positive outside
    trajectory/wet-region language preserves the model's review verdict.
    """
    if verdict.event_type != "spill" or verdict.verdict != "needs_review":
        return verdict
    chain = " ".join(str(x) for x in verdict.evidence_chain).lower()
    def affirmed(phrase: str) -> bool:
        offset = 0
        while True:
            index = chain.find(phrase, offset)
            if index < 0:
                return False
            prefix = chain[max(0, index - 48):index]
            if not any(negation in prefix for negation in (
                    "no ", "not ", "without ", "neither ", "never ")):
                return True
            offset = index + len(phrase)

    positive_outside = any(affirmed(phrase) for phrase in (
        "outside trajectory", "outside the rim", "outside-rim",
        "beyond the rim", "falls short of", "falling short of",
        "landed on the cloth", "landing on the cloth",
        "new wet region appears", "new wet region forms",
        "new persistent wet region appears", "new persistent dark",
        "escaped-liquid consequence is suggested",
    ))
    explicit_no_escape = any(phrase in chain for phrase in (
        "no stream", "no visible stream", "no visible pour stream", "no droplet",
        "no drip", "no liquid stream", "no escaped liquid",
        "no visible escaped liquid",
        "no frame in any view shows", "contained flow",
        "terminating inside", "terminates inside", "contained stream",
        "stream into", "stream falling into",
    ))
    explicit_no_new_wet = any(phrase in chain for phrase in (
        "no new persistent wet region", "no new wet region",
        "no visible escaped liquid or new wet region",
        "no wet mark", "remains unchanged", "uniformly dry",
        "surfaces remain unchanged", "remains dry", "cloth dry",
        "dry blue cloth", "surface appearance is unchanged",
    ))
    if positive_outside or not (explicit_no_escape and explicit_no_new_wet):
        return verdict
    verdict.verdict = "rejected"
    verdict.confidence = max(float(verdict.confidence), .82)
    verdict.limitations = list(verdict.limitations) + [
        "dense multi-view spill negative gate: no visible escaped-liquid "
        "trajectory and no causally new persistent wet region; sub-resolution "
        "droplets alone do not constitute an event"]
    verdict.safety_impact = "no observable spill event in the reviewed interval"
    return verdict


def _candidate_window(candidate: SafetyCandidate):
    return SimpleNamespace(review_start=candidate.start,
                           review_end=candidate.end)


def _hand_dense_images(episode: Path, candidate: SafetyCandidate, verdict,
                       sample_fps: float = 6.0,
                       max_images: int = 12) -> list[tuple[str, bytes]]:
    review_start = (candidate.start if verdict.review_start is None else
                    max(candidate.start, float(verdict.review_start)))
    review_end = (candidate.end if verdict.review_end is None else
                  min(candidate.end, float(verdict.review_end)))
    start, end = max(0.0, review_start - .8), max(
        review_start + .5, review_end + .8)
    images = []
    for label, path in DirectEventScanner.discover_views(episode):
        for chunk_start, chunk_end, sheet in make_chunked_contact_sheets(
                path, start, end, sample_fps, chunk_seconds=2.0):
            images.append((
                f"{label} HIGH-FPS HAND ATTRIBUTION "
                f"{chunk_start:.2f}-{chunk_end:.2f}s; list only visibly "
                "touched or hand-driven objects; entry-only means objects=[]",
                sheet))
    if len(images) > max_images:
        indices = sorted({round(i * (len(images) - 1) / (max_images - 1))
                          for i in range(max_images)})
        images = [images[i] for i in indices]
    return images


def _spill_dense_images(episode: Path, candidate: SafetyCandidate, verdict,
                        sample_fps: float = 6.0,
                        max_images: int = 12) -> list[tuple[str, bytes]]:
    views = DirectEventScanner.discover_views(episode)
    review_start = (candidate.start if verdict.review_start is None else
                    max(candidate.start, float(verdict.review_start)))
    review_end = (candidate.end if verdict.review_end is None else
                  min(candidate.end, float(verdict.review_end)))
    start, end = max(0.0, review_start - 1.0), max(
        review_start + .5, review_end + 1.0)
    baselines = []
    focused = []
    dense = []
    specific_intervals = []
    for item in candidate.evidence.get("merged_candidates", []):
        subtype = str(item.get("subtype") or "")
        if subtype and subtype not in {"active_pour_audit", "stream_alignment_check"}:
            interval = (max(0.0, float(item["start"]) - .5),
                        float(item["end"]) + .5, subtype)
            if interval not in specific_intervals:
                specific_intervals.append(interval)
    specific_intervals = specific_intervals[:3]
    for label, path in views:
        # A wet-looking patch inside the proposal window is not evidence that
        # it was created by this event.  Give the judge a compact, earlier
        # baseline before the dense sheets so it can distinguish old wet marks,
        # cloth texture and shadows from a causally new wet region.
        baseline_start = max(0.0, review_start - 8.0)
        baseline_end = max(baseline_start + .5, review_start - 2.0)
        baseline = make_contact_sheet(
            path, baseline_start, baseline_end, sample_fps=1.0)
        if baseline:
            baselines.append((
                f"{label} PRE-EVENT DRY/WET BASELINE "
                f"{baseline_start:.2f}-{baseline_end:.2f}s; any patch already "
                "present here is OLD and must not confirm this spill candidate",
                baseline))
        for focus_start, focus_end, subtype in specific_intervals:
            focus_sheet = make_contact_sheet(
                path, focus_start, focus_end, sample_fps=6.0,
                tile_size=(480, 270))
            focused.append((
                f"{label} HIGH-RESOLUTION FOCUSED SPILL HYPOTHESIS "
                f"{focus_start:.2f}-{focus_end:.2f}s ({subtype}); inspect "
                "the complete liquid trajectory and vessel rim at every timestamp",
                focus_sheet))
        for chunk_start, chunk_end, sheet in make_chunked_contact_sheets(
                path, start, end, sample_fps, chunk_seconds=2.0):
            dense.append((
                f"{label} HIGH-FPS SPILL CONFIRMATION "
                f"{chunk_start:.2f}-{chunk_end:.2f}s; verify visible escaped "
                "liquid and persistent new wet region, not shadow/compression",
                sheet))
    # Specific short hypotheses carry more small-liquid detail than the broad
    # audit window.  Preserve them first, then sample the broad chronological
    # sheets uniformly so late-window evidence cannot disappear from the image
    # budget.
    images = baselines + focused
    remaining = max(0, max_images - len(images))
    if dense and remaining:
        indices = sorted({round(i * (len(dense) - 1) / max(1, remaining - 1))
                          for i in range(remaining)})
        images.extend(dense[i] for i in indices[:remaining])
    images = images[:max_images]
    return images


def _merge_adjudicated(candidates: list[SafetyCandidate], verdicts: list[dict],
                        gap: float, limits: dict[str, float]) -> list[dict]:
    by_id = {v["candidate_id"]: v for v in verdicts}
    for candidate in candidates:
        by_id.setdefault(candidate.candidate_id, {
            "candidate_id": candidate.candidate_id,
            "verdict": "needs_review", "confidence": min(
                candidate.prior_confidence, .35),
            "event_type": candidate.type, "subtype": candidate.subtype,
            "objects": candidate.objects, "evidence_chain": [],
            "limitations": ["candidate has no persisted adjudication"],
            "safety_impact": "coverage retained for manual review",
            "review_start": candidate.start, "review_end": candidate.end,
        })
    result = []
    for status in ("confirmed", "needs_review"):
        selected = []
        for candidate in candidates:
            verdict = by_id[candidate.candidate_id]
            if verdict["verdict"] != status:
                continue
            review_start = verdict.get("review_start")
            review_end = verdict.get("review_end")
            start = (candidate.start if review_start is None else
                     max(candidate.start, float(review_start)))
            end = (candidate.end if review_end is None else
                   min(candidate.end, float(review_end)))
            if end < start:
                start, end = candidate.start, candidate.end
            if candidate.type == "collision" and status == "confirmed":
                for statement in verdict.get("evidence_chain", []):
                    lower = str(statement).lower()
                    if (any(term in lower for term in (
                            "makes visible contact", "direct contact",
                            "impact occurs", "发生可见接触", "直接接触"))
                            and not any(term in lower for term in (
                                "no visible contact", "without contact",
                                "没有接触", "未接触"))):
                        match = re.search(r"\b(\d+(?:\.\d+)?)s\b", lower)
                        if match:
                            onset = float(match.group(1))
                            start = max(candidate.start, onset - .4)
                            end = min(candidate.end, onset + .8)
                            break
            selected.append((SafetyCandidate(
                type=candidate.type, start=start, end=end,
                objects=list(verdict.get("objects") or []),
                views=candidate.views, source=candidate.source,
                evidence=candidate.evidence,
                prior_confidence=float(verdict.get("confidence") or 0),
                subtype=verdict.get("subtype") or candidate.subtype), verdict))
        grounded_candidates = [item[0] for item in selected]
        for event in merge_safety_candidates(
                grounded_candidates, gap=gap, max_duration=limits):
            members = [verdict for candidate, verdict in selected
                       if candidate.type == event.type
                       and candidate.start <= event.end + gap
                       and candidate.end >= event.start - gap]
            grounded_objects = []
            for member in members:
                for obj in member.get("objects", []):
                    if obj and obj not in grounded_objects:
                        grounded_objects.append(obj)
            result.append({**event.to_dict(), "verdict": status,
                           "objects": grounded_objects,
                           "confidence": max((x["confidence"] for x in members), default=.0),
                           "adjudications": members})
    return sorted(result, key=lambda x: (x["start"], x["type"]))


def _write_report(out: Path, task_graph: dict, raw: list[SafetyCandidate],
                  verdicts: list[dict], merged: list[dict],
                  scan_coverage: dict | None = None,
                  run_config: dict | None = None,
                  safety_capabilities: dict | None = None) -> None:
    active_ids = {x.candidate_id for x in raw}
    counts = {
        "confirmed": sum(x["verdict"] == "confirmed" for x in merged),
        "needs_review": sum(x["verdict"] == "needs_review" for x in merged),
        "rejected": sum(x["verdict"] == "rejected"
                        and x.get("candidate_id") in active_ids for x in verdicts),
    }
    report = {"schema_version": 1,
        "algorithm_revision": PIPELINE_REVISION,
        "physical_revision": PHYSICAL_REVISION,
        "run_config": run_config or {},
        "event_types": sorted(name for name, item in
                              (safety_capabilities or {}).items()
                              if item.get("enabled")),
        "safety_capabilities": safety_capabilities or {},
        "task_graph_source": task_graph.get("source", "unknown"),
        "scan_coverage": scan_coverage or {},
        "summary": counts,
        "confirmed_events": [x for x in merged if x["verdict"] == "confirmed"],
        "needs_review": [x for x in merged if x["verdict"] == "needs_review"],
        "rejected_candidates": [x for x in verdicts if x["verdict"] == "rejected"]}
    (out / "safety_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    lines = ["# Four-event robot safety report", "",
             f"- Confirmed: {counts['confirmed']}",
             f"- Needs review: {counts['needs_review']}",
             f"- Rejected: {counts['rejected']}",
             f"- Direct scan complete: {(scan_coverage or {}).get('direct_scan_complete', 'unknown')}",
             f"- Failed scan windows: {(scan_coverage or {}).get('failed_window_count', 'unknown')}",
             "", "## Event timeline", "",
             "| verdict | type | interval | objects | confidence |", "|---|---|---:|---|---:|"]
    for event in merged:
        lines.append(f"| {event['verdict']} | {event['type']} | "
                     f"{event['start']:.2f}–{event['end']:.2f}s | "
                     f"{', '.join(event['objects']) or 'unknown'} | "
                     f"{event['confidence']:.2f} |")
    (out / "safety_report.md").write_text("\n".join(lines) + "\n")


def _evaluate_after_detection(annotation_path: Path, merged: list[dict], out: Path,
                              tolerance: float) -> None:
    """This is deliberately called only after every inference output is saved."""
    annotations = json.loads(annotation_path.read_text())
    if isinstance(annotations, dict):
        annotations = annotations.get("events", [])
    rows = []
    for truth in annotations:
        matches = [event for event in merged if event["type"] == truth["type"]
                   and event["verdict"] in {"confirmed", "needs_review"}
                   and event["end"] >= float(truth["start"]) - tolerance
                   and event["start"] <= float(truth.get("end", truth["start"])) + tolerance]
        confirmed = [x for x in matches if x["verdict"] == "confirmed"]
        selected = max(confirmed or matches,
                       key=lambda x: x["confidence"], default=None)
        truth_objects = set(truth.get("objects") or [])
        rows.append({"annotation": truth,
                     "status": ("confirmed_hit" if confirmed else
                                "review_coverage" if matches else "miss"),
                     "confirmed": bool(confirmed), "covered": bool(matches),
                     "matched_event": selected,
                     "start_error_seconds": (None if selected is None else round(
                         float(selected["start"]) - float(truth["start"]), 3)),
                     "object_match": (None if not truth_objects or selected is None
                                      else bool(truth_objects & set(
                                          selected.get("objects") or [])))})
    comparison = {"annotation_count": len(rows),
                  "confirmed_count": sum(x["confirmed"] for x in rows),
                  "covered_count": sum(x["covered"] for x in rows),
                  "confirmed_recall": round(sum(x["confirmed"] for x in rows)
                                            / max(1, len(rows)), 4),
                  "candidate_coverage": round(sum(x["covered"] for x in rows)
                                              / max(1, len(rows)), 4),
                  "rows": rows}
    (out / "annotation_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2))
    lines = ["# Annotation comparison", "",
             f"Confirmed {comparison['confirmed_count']}/{comparison['annotation_count']} "
             f"({comparison['confirmed_recall']:.1%})",
             f"Confirmed or review coverage {comparison['covered_count']}/"
             f"{comparison['annotation_count']} ({comparison['candidate_coverage']:.1%})", ""]
    for row in rows:
        truth = row["annotation"]
        lines.append(f"- {row['status']}: "
                     f"{truth['type']} {truth['start']}–{truth.get('end', truth['start'])}s")
    (out / "annotation_comparison.md").write_text("\n".join(lines) + "\n")


def _candidate_fingerprint(candidate: SafetyCandidate,
                           revision: str | None = None,
                           legacy_empty_context: bool = False) -> str:
    payload = candidate.to_dict()
    payload.pop("candidate_id", None)
    # A verdict is only reusable under the exact adjudication/evidence policy
    # that produced it.  Candidate geometry may remain byte-identical while a
    # newer revision fixes evidence coverage or decision rules.
    payload["pipeline_revision"] = revision or PIPELINE_REVISION
    evidence = payload.get("evidence") or {}
    if legacy_empty_context and "task_context" not in evidence:
        evidence["task_context"] = {}
    task_context = evidence.get("task_context")
    if isinstance(task_context, dict):
        for key in ("review_stage", "initial_verdict", "initial_limitations",
                    "confirmation_rule", "attribution_rule",
                    "adjudication_evidence_protocol"):
            task_context.pop(key, None)
        if not task_context and not legacy_empty_context:
            evidence.pop("task_context", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _policy_replay_source(verdict: dict, candidate: SafetyCandidate) -> str | None:
    """Return an explicitly compatible source revision for saved VLM JSON."""
    if not verdict.get("raw_output"):
        return None
    if (_needs_lid_dense_evidence(candidate)
            and verdict.get("policy_replay_from") ==
            "four_event_v4_lid_outcome_horizon_v43"):
        # A later checkpoint may have a newer outer fingerprint while its raw
        # answer was replayed from v43. Preserve provenance transitively so the
        # pre-ballistic prompt cannot pass through an intermediate revision.
        return None
    grasp_motion = str((candidate.evidence.get("geometry") or {}).get(
        "motion_state") or "")
    # Older strong-collision confirmations could bypass dense evidence.  Never
    # replay those as if they had passed the new mandatory dense gate.
    if (_needs_collision_dense_evidence(candidate)
            and verdict.get("verdict") == "confirmed"
            and verdict.get("confirmation_stage") not in {
                "dense_collision_v1", "dense_grasp_audit_v1"}):
        return None
    if (_needs_lid_dense_evidence(candidate)
            and verdict.get("verdict") == "confirmed"
            and verdict.get("confirmation_stage") != "dense_lid_v1"):
        return None
    if (_needs_hand_dense_evidence(candidate)
            and verdict.get("confirmation_stage") != "dense_hand_v1"):
        return None
    if (_needs_spill_dense_evidence(candidate)
            and verdict.get("confirmation_stage") != "dense_spill_v1"):
        return None
    fingerprint = verdict.get("candidate_fingerprint")
    for revision in POLICY_REPLAY_COMPATIBLE_REVISIONS:
        if (revision == "four_event_v4_lid_outcome_horizon_v43"
                and _needs_lid_dense_evidence(candidate)):
            # v44 changed the dense lid prompt to recognize independent
            # tumbling/ballistic motion when the grasp interface is occluded.
            # Replaying v43 here would silently bypass that new evidence rule.
            continue
        if grasp_motion == "direct_lid_contact":
            # v39 was the first revision to request true dense evidence for
            # direct lid contact.  v40 kept the same first-stage evidence, and
            # v41 may feed either answer into the mandatory outcome audit.
            if revision not in {
                    "four_event_v4_negation_aware_spill_gate_v53",
                    "four_event_v4_dense_spill_negative_gate_v52",
                    "four_event_v4_visual_negative_lid_gate_v51",
                    "four_event_v4_lid_gripper_state_v47",
                    "four_event_v4_lid_replay_provenance_gate_v46",
                    "four_event_v4_lid_ballistic_replay_gate_v45",
                    "four_event_v4_lid_ballistic_motion_v44",
                    "four_event_v4_lid_outcome_horizon_v43",
                    "four_event_v4_adversarial_grasp_audit_v39",
                    "four_event_v4_staged_grasp_audit_replay_v40"}:
                continue
        elif grasp_motion in {
                "lid_motion_contact_candidate", "unverified_grasp_contact"}:
            # v38 is a compatible first-stage answer which v40 may feed into
            # the new adversarial audit. v39 already contains that final audit.
            if revision not in {
                    "four_event_v4_negation_aware_spill_gate_v53",
                    "four_event_v4_dense_spill_negative_gate_v52",
                    "four_event_v4_visual_negative_lid_gate_v51",
                    "four_event_v4_lid_gripper_state_v47",
                    "four_event_v4_lid_replay_provenance_gate_v46",
                    "four_event_v4_lid_ballistic_replay_gate_v45",
                    "four_event_v4_lid_ballistic_motion_v44",
                    "four_event_v4_lid_outcome_horizon_v43",
                    "four_event_v4_mandatory_lid_grasp_audit_v42",
                    "four_event_v4_grasp_outcome_context_v38",
                    "four_event_v4_adversarial_grasp_audit_v39"}:
                continue
        if (revision == "four_event_v4_stationary_far_ellipsoid_filter_v30"
                and _needs_lid_dense_evidence(candidate)):
            # v31 deliberately changed the lid evidence payload.  Replaying a
            # v30 lid decision would bypass the bounded multi-view request.
            continue
        compatible_fingerprints = {
            _candidate_fingerprint(candidate, revision),
            _candidate_fingerprint(candidate, revision,
                                   legacy_empty_context=True),
        }
        if fingerprint in compatible_fingerprints:
            try:
                parsed = json.loads(verdict["raw_output"])
            except (TypeError, ValueError):
                return None
            if isinstance(parsed, dict):
                return revision
    return None


class _SavedVisionDecision:
    """Model adapter that replays one previously saved final VLM response."""
    def __init__(self, parsed: dict, raw: str, model: str):
        self.parsed, self.raw = parsed, raw
        self.model = model

    def generate(self, _prompt, _images):
        return self.parsed, self.raw


def main(argv: list[str] | None = None, model=None) -> int:
    parser = argparse.ArgumentParser(description="Unified four-event safety pipeline")
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task", default="robot manipulation task")
    parser.add_argument("--api-config", type=Path,
                        default=None)
    parser.add_argument("--api-timeout", type=int, default=180,
                        help="maximum seconds for one VLM request")
    parser.add_argument("--vlm-backend", choices=("local", "remote"),
                        default="local",
                        help="local is the default and never consumes API tokens")
    parser.add_argument("--local-model", type=Path,
                        default=None)
    parser.add_argument("--local-device", default="cuda:0")
    parser.add_argument("--task-graph", type=Path)
    parser.add_argument("--neuro-report", type=Path,
                        help="reuse a physical report instead of running collision tracking")
    parser.add_argument("--primary-camera", default="faceImg")
    parser.add_argument("--collision-stride", type=int, default=3)
    parser.add_argument("--depth-stride", type=int, default=5,
                        help="monocular depth sampling stride when cache is absent")
    parser.add_argument("--pour-tea", action="store_true")
    parser.add_argument("--window-seconds", type=float, default=12.0)
    parser.add_argument("--overlap-seconds", type=float, default=2.0)
    parser.add_argument("--scan-fps", type=float, default=1.5)
    parser.add_argument("--spill-review-fps", type=float, default=4.0)
    parser.add_argument("--evidence-fps", type=float, default=3.0)
    parser.add_argument("--annotations", type=Path,
                        help="optional post-inference evaluation only")
    parser.add_argument("--no-visualization", action="store_true",
                        help="skip the full-length timeline video (useful for batch runs)")
    parser.add_argument("--no-evidence-clips", action="store_true",
                        help="skip post-verdict mp4 encoding; adjudication evidence is unchanged")
    parser.add_argument("--match-tolerance", type=float, default=2.0)
    parser.add_argument(
        "--candidate-id", action="append", default=[],
        help=("adjudicate only the selected generated candidate ID(s); "
              "diagnostic/regression option, candidate generation is unchanged"))
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    if model is None and args.vlm_backend == "local":
        model = LocalQwenVisionModel(args.local_model, args.local_device)
    elif model is None:
        model = OpenAICompatibleVisionModel.from_private_config(args.api_config)
        model.timeout = min(model.timeout, max(15, args.api_timeout))
    cached_report = args.out / "collision" / "neuro_symbolic_report.json"
    cache_current = False
    if cached_report.exists():
        try:
            cache_current = (json.loads(cached_report.read_text()).get(
                "algorithm_revision") == PHYSICAL_REVISION)
        except (OSError, ValueError):
            cache_current = False
    neuro_report = (args.neuro_report or (cached_report if cache_current
                                          else _run_collision(args, args.out / "collision", model=model)))
    neuro_payload = json.loads(neuro_report.read_text())
    if args.task_graph:
        task_graph = TaskGraph.load(args.task_graph).to_dict()
    else:
        task_graph = neuro_payload.get("task_graph")
    if not task_graph:
        raise RuntimeError("physical report contains no TaskGraph; pass --task-graph")
    task_graph = TaskGraph.from_dict(task_graph).to_dict()
    safety_capabilities = dict(task_graph["safety_capabilities"])
    enabled_types = {name for name, item in safety_capabilities.items()
                     if item.get("enabled")}
    (args.out / "task_graph.json").write_text(
        json.dumps(task_graph, ensure_ascii=False, indent=2))

    collision_candidates = (load_collision_candidates(neuro_report)
                            if "collision" in enabled_types else [])
    collision_candidates = _filter_allowed_assembly_internal_contacts(
        collision_candidates, task_graph)
    collision_candidates = _filter_stationary_far_ellipsoid_contacts(
        collision_candidates)
    lid_tracking_candidates = (load_lid_tracking_candidates(neuro_report)
                               if "lid_fall" in enabled_types else [])
    support_reappearance_candidates = (
        load_support_reappearance_candidates(neuro_report)
        if "collision" in enabled_types else [])
    direct_types = enabled_types & {"spill", "hand_intervention", "lid_fall"}
    scanner = DirectEventScanner(model, args.window_seconds,
                                 args.overlap_seconds, args.scan_fps,
                                 args.spill_review_fps,
                                 enabled_event_types=direct_types)
    progress_path = args.out / "vlm_scan_progress.json"
    if direct_types:
        direct_candidates = scanner.scan(args.episode, task_graph, progress_path)
    else:
        direct_candidates = []
        _write_json_atomic(progress_path, {
            "state": {}, "candidates": [], "scan_failures": [],
            "window_history": [], "completed_until": 0.0,
            "window_index": -1, "skipped": True,
            "reason": "TaskGraph disabled all direct visual event capabilities"})
    scan_progress = json.loads(progress_path.read_text())
    history = list(scan_progress.get("window_history") or [])
    failed_windows = list(scan_progress.get("scan_failures") or [])
    scan_coverage = {
        "direct_scan_complete": not failed_windows,
        "completed_until": float(scan_progress.get("completed_until", 0.0)),
        "window_count": len(history) or int(scan_progress.get("window_index", -1)) + 1,
        "failed_window_count": len({(float(x.get("start", 0)),
                                      float(x.get("end", 0)))
                                     for x in failed_windows}),
        "failures": failed_windows,
    }
    raw = (collision_candidates + lid_tracking_candidates
           + support_reappearance_candidates + direct_candidates)
    _enrich_lid_gripper_state(raw, args.episode)
    _write_json_atomic(args.out / "events_raw.json", [x.to_dict() for x in raw])

    limits = {"hand_intervention": 14.0, "spill": 20.0,
              "collision": 14.0, "lid_fall": 10.0}
    candidates = merge_safety_candidates(raw, gap=2.0, max_duration=limits)
    if args.candidate_id:
        requested = set(args.candidate_id)
        available = {candidate.candidate_id for candidate in candidates}
        missing = sorted(requested - available)
        if missing:
            raise ValueError(f"requested candidate IDs not generated: {missing}")
        candidates = [candidate for candidate in candidates
                      if candidate.candidate_id in requested]
    adjudicator = UnifiedSafetyAdjudicator(model)
    adjudicated_path = args.out / "events_adjudicated.json"
    verdicts = _load_persisted_verdicts(args.out)
    candidate_by_id = {x.candidate_id: x for x in candidates}
    fresh_verdicts = []
    replayable_verdicts: dict[str, tuple[dict, str]] = {}
    for verdict in verdicts:
        candidate = candidate_by_id.get(verdict.get("candidate_id"))
        if candidate is None:
            continue
        if verdict.get("candidate_fingerprint") != _candidate_fingerprint(candidate):
            source_revision = _policy_replay_source(verdict, candidate)
            if source_revision:
                replayable_verdicts[candidate.candidate_id] = (
                    verdict, source_revision)
            continue
        if (_needs_spill_dense_evidence(candidate)
                and verdict.get("confirmation_stage") != "dense_spill_v1"):
            continue
        if (_needs_hand_dense_evidence(candidate)
                and verdict.get("confirmation_stage") != "dense_hand_v1"):
            continue
        if (_needs_collision_dense_evidence(candidate)
                and verdict.get("confirmation_stage") != "dense_collision_v1"):
            continue
        if (_needs_lid_dense_evidence(candidate)
                and verdict.get("confirmation_stage") != "dense_lid_v1"):
            continue
        if "vlm_scan_failure" in candidate.source:
            verdict["verdict"] = "needs_review"
            verdict["confidence"] = min(float(verdict.get("confidence", .35)), .35)
            verdict["limitations"] = list(verdict.get("limitations") or []) + [
                "direct scan failed; persisted verdict forced to needs_review"]
        fresh_verdicts.append(verdict)
    verdicts = fresh_verdicts
    completed_ids = {x.get("candidate_id") for x in verdicts}
    clips = args.out / "evidence_clips"; clips.mkdir(exist_ok=True)
    for candidate in sorted(candidates, key=_adjudication_priority):
        if candidate.candidate_id in completed_ids:
            continue
        replay_info = replayable_verdicts.get(candidate.candidate_id)
        replayed = replay_info is not None
        replay_stage = None
        collision_dense = False
        grasp_audited = False
        lid_dense = False
        hand_dense = False
        spill_dense = False
        if replay_info:
            saved, replay_source = replay_info
            raw_output = str(saved["raw_output"])
            replay_model = _SavedVisionDecision(
                json.loads(raw_output), raw_output,
                str(saved.get("model") or model.model))
            verdict = UnifiedSafetyAdjudicator(replay_model).judge(
                candidate, task_graph, [])
            replay_stage = (saved.get("confirmation_stage") or
                            ("dense_collision_v1"
                             if _needs_collision_dense_evidence(candidate)
                             else "dense_lid_v1"
                             if _needs_lid_dense_evidence(candidate)
                             else None))
        else:
            if _needs_collision_dense_evidence(candidate):
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "direct_high_fps_collision",
                    "adjudication_evidence_protocol": {
                        "dense": True,
                        "proposal_sampling_is_not_adjudication_sampling": True,
                        "primary_roi_fps": 10.0,
                        "synchronized_full_view_fps": 8.0,
                        "coverage": "all selected hypothesis intervals; continuous across any occlusion bridge",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": "coarse_stage_skipped",
                    "initial_limitations": [],
                    "confirmation_rule": (
                        "inspect the complete synchronized contact interval; a "
                        "light rigid graze is a collision even without passive-"
                        "object displacement; reject only when at least two "
                        "unobstructed views resolve a persistent positive gap at "
                        "the actual closest-approach instant, and unchanged object "
                        "pose alone is not separation evidence. Never infer "
                        "successful grasp/placement from task intent: pickup "
                        "requires original support -> secured -> lifted, placement "
                        "requires gripper-supported -> target support -> release; "
                        "direct contact that shifts an already-supported object "
                        "without pickup is a grasp-impact collision")})
                images = (_local_collision_images(args.episode, candidate)
                          if args.vlm_backend == "local" else
                          _collision_roi_images(args.episode, candidate))
                collision_dense = bool(images)
            elif _needs_lid_dense_evidence(candidate):
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "direct_high_fps_lid_continuity",
                    "adjudication_evidence_protocol": {
                        "dense": True, "primary_sampling_fps": 12.0,
                        "synchronized_auxiliary_sampling_fps": 8.0,
                        "max_contact_sheet_seconds": 2.0,
                        "coverage": "chronological support-loss and landing continuity interval",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": "coarse_stage_skipped",
                    "initial_limitations": [],
                    "confirmation_rule": (
                        "dense 12fps evidence overrides coarse observation; "
                        "controlled placement requires continuously visible "
                        "support, and stable final rest is not control evidence")})
                images = (_local_lid_images(args.episode, candidate)
                          if args.vlm_backend == "local" else
                          _lid_dense_images(args.episode, candidate))
                lid_dense = bool(images)
            elif _needs_hand_dense_evidence(candidate):
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "direct_high_fps_hand_attribution",
                    "adjudication_evidence_protocol": {
                        "dense": True, "sampling_fps": 6.0,
                        "coverage": "chronological multi-view entry/contact interval",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": "coarse_stage_skipped",
                    "initial_limitations": [],
                    "attribution_rule": (
                        "objects must contain only visibly touched or hand-driven "
                        "instances; workspace entry without contact uses objects=[]")})
                images = _hand_dense_images(
                    args.episode, candidate, _candidate_window(candidate))
                hand_dense = bool(images)
            elif _needs_spill_dense_evidence(candidate):
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "direct_high_fps_spill_confirmation",
                    "adjudication_evidence_protocol": {
                        "dense": True, "sampling_fps": 6.0,
                        "coverage": "chronological multi-view escaped-liquid and wet-region interval",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": "coarse_stage_skipped",
                    "initial_limitations": [],
                    "confirmation_rule": (
                        "confirm escaped liquid only from a visible outside trajectory "
                        "or a causally new persistent wet region; reject shadows, "
                        "reflections, compression artifacts and contained flow")})
                images = _spill_dense_images(
                    args.episode, candidate, _candidate_window(candidate))
                spill_dense = bool(images)
            else:
                images = []
            if not images:
                images = _evidence_images(
                    args.episode, candidate, args.evidence_fps)
            verdict = adjudicator.judge(candidate, task_graph, images)
        if (not replayed and not collision_dense
                and _needs_collision_recheck(candidate, verdict)):
            roi_images = _collision_roi_images(args.episode, candidate)
            if roi_images:
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "high_fps_collision_recheck",
                    "adjudication_evidence_protocol": {
                        "dense": True,
                        "proposal_sampling_is_not_adjudication_sampling": True,
                        "primary_roi_fps": 10.0,
                        "synchronized_full_view_fps": 8.0,
                        "coverage": "all selected hypothesis intervals; continuous across any occlusion bridge",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": verdict.verdict,
                    "initial_limitations": verdict.limitations,
                    "confirmation_rule": (
                        "inspect the complete synchronized contact interval; a "
                        "light rigid graze is a collision even without passive-"
                        "object displacement; reject only when at least two "
                        "unobstructed views resolve a persistent positive gap at "
                        "the actual closest-approach instant, and unchanged object "
                        "pose alone is not separation evidence. Never infer "
                        "successful grasp/placement from task intent: pickup "
                        "requires original support -> secured -> lifted, placement "
                        "requires gripper-supported -> target support -> release; "
                        "direct contact that shifts an already-supported object "
                        "without pickup is a grasp-impact collision")})
                verdict = adjudicator.judge(candidate, task_graph, roi_images)
                collision_dense = True
        replayed_initial_needs_audit = bool(
            replayed and (
                replay_source == "four_event_v4_grasp_outcome_context_v38"
                or (replay_source in {
                        "four_event_v4_staged_grasp_audit_replay_v40",
                        "four_event_v4_mandatory_lid_grasp_audit_v41"}
                    and replay_stage != "dense_grasp_audit_v1")))
        if ((not replayed or replayed_initial_needs_audit)
                and _needs_grasp_outcome_audit(candidate, verdict)):
            audit_images = (_local_collision_images(args.episode, candidate)
                            if args.vlm_backend == "local" else
                            _collision_roi_images(args.episode, candidate))
            if audit_images:
                initial_grasp_verdict = verdict
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "adversarial_grasp_outcome_audit",
                    "initial_verdict": verdict.verdict,
                    "initial_limitations": verdict.limitations,
                    "confirmation_rule": (
                        "independently falsify the initial grasp interpretation. "
                        "Check every synchronized view before contact: if any "
                        "clear view shows the object already airborne and stably "
                        "carried, reject a stale new-grasp collision hypothesis. "
                        "A later spill or commanded tilt is not proof of impact. "
                        "Confirm only a discrete unintended reaction or the chain "
                        "already-supported -> direct contact -> persistent shift/"
                        "mis-seat -> no pickup. For placement require prior gripper "
                        "support. For a lid, do not call contact an intended successful "
                        "pickup merely because the gripper closes or the lid briefly "
                        "tilts: require continuous visible gripper support followed by "
                        "clear removal from the vessel, or a geometrically seated final "
                        "pose with rim gap and tilt resolved in a synchronized view. A "
                        "lid that remains on the vessel but persistently offset, tilted, "
                        "or mis-seated after release is a failed-grasp impact collision. "
                        "Task intention is not outcome evidence. Rejection is forbidden "
                        "when the lid remains partly attached with a nonzero rim gap and "
                        "the evidence does not show continuous gripper support through "
                        "clear removal or a later fully seated pose. In that case use "
                        "needs_review if release/support is hidden, and confirmed if the "
                        "gripper releases or retreats while the offset/tilt persists. "
                        "State exact view and time for each link; do not "
                        "infer hidden table support from an occluded primary view")})
                audit = adjudicator.judge(candidate, task_graph, audit_images)
                verdict = _resolve_grasp_outcome_audit(
                    initial_grasp_verdict, audit)
                grasp_audited = True
        if (not replayed and not lid_dense
                and _needs_lid_recheck(candidate, verdict)):
            lid_images = (_local_lid_images(args.episode, candidate)
                          if args.vlm_backend == "local" else
                          _lid_dense_images(args.episode, candidate))
            if lid_images:
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "high_fps_lid_continuity_recheck",
                    "adjudication_evidence_protocol": {
                        "dense": True, "primary_sampling_fps": 12.0,
                        "synchronized_auxiliary_sampling_fps": 8.0,
                        "max_contact_sheet_seconds": 2.0,
                        "coverage": "chronological support-loss and landing continuity interval",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": verdict.verdict,
                    "initial_limitations": verdict.limitations,
                    "confirmation_rule": (
                        "dense 12fps evidence overrides coarse observation; "
                        "controlled placement requires continuously visible "
                        "support, and stable final rest is not control evidence")})
                verdict = adjudicator.judge(candidate, task_graph, lid_images)
                lid_dense = True
        hand_confirmed = False
        if (not replayed and not hand_dense
                and _needs_hand_confirmation(candidate, verdict)):
            hand_images = _hand_dense_images(args.episode, candidate, verdict)
            if hand_images:
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "high_fps_hand_attribution",
                    "adjudication_evidence_protocol": {
                        "dense": True, "sampling_fps": 6.0,
                        "coverage": "chronological multi-view entry/contact interval",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": verdict.verdict,
                    "initial_limitations": verdict.limitations,
                    "attribution_rule": (
                        "objects must contain only visibly touched or hand-driven "
                        "instances; workspace entry without contact uses objects=[]")})
                verdict = adjudicator.judge(candidate, task_graph, hand_images)
                hand_confirmed = True
        spill_confirmed = False
        if (not replayed and not spill_dense
                and _needs_spill_confirmation(candidate, verdict)):
            spill_images = _spill_dense_images(args.episode, candidate, verdict)
            if spill_images:
                candidate.evidence.setdefault("task_context", {}).update({
                    "review_stage": "high_fps_spill_confirmation",
                    "adjudication_evidence_protocol": {
                        "dense": True, "sampling_fps": 6.0,
                        "coverage": "chronological multi-view escaped-liquid and wet-region interval",
                        "labels_include_exact_intervals": True,
                    },
                    "initial_verdict": verdict.verdict,
                    "initial_limitations": verdict.limitations,
                    "confirmation_rule": (
                        "confirm escaped liquid only from a visible outside trajectory "
                        "or a causally new persistent wet region; resolve review "
                        "candidates at high frame rate and reject shadows, reflections, "
                        "compression artifacts and clearly contained flow")})
                verdict = adjudicator.judge(candidate, task_graph, spill_images)
                spill_confirmed = True
        if spill_dense or spill_confirmed or replayed:
            verdict = _resolve_dense_spill_negative(verdict)
        verdict_payload = verdict.to_dict()
        verdict_payload["candidate_fingerprint"] = _candidate_fingerprint(candidate)
        if spill_confirmed:
            verdict_payload["confirmation_stage"] = "dense_spill_v1"
        if hand_confirmed:
            verdict_payload["confirmation_stage"] = "dense_hand_v1"
        if hand_dense:
            verdict_payload["confirmation_stage"] = "dense_hand_v1"
        if collision_dense:
            verdict_payload["confirmation_stage"] = "dense_collision_v1"
        if grasp_audited:
            verdict_payload["confirmation_stage"] = "dense_grasp_audit_v1"
        if lid_dense:
            verdict_payload["confirmation_stage"] = "dense_lid_v1"
        if spill_dense:
            verdict_payload["confirmation_stage"] = "dense_spill_v1"
        if replayed:
            verdict_payload["policy_replay_from"] = replay_source
            if replay_stage and not grasp_audited:
                verdict_payload["confirmation_stage"] = replay_stage
        verdicts.append(verdict_payload)
        start, duration = max(0., candidate.start - 2), candidate.end - candidate.start + 4
        clip_path = clips / f"{candidate.candidate_id}.mp4"
        if (not args.no_evidence_clips
                and (not clip_path.exists() or clip_path.stat().st_size == 0)):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(start),
                            "-t", str(duration), "-i", str(args.episode / f"{args.primary_camera}.mp4"),
                            "-c:v", "libx264", "-crf", "23", str(clip_path)],
                           check=False)
        _write_json_atomic(adjudicated_path, verdicts)
    merged = _merge_adjudicated(candidates, verdicts, 2.0, limits)
    _write_json_atomic(args.out / "events_merged.json", merged)
    _write_report(args.out, task_graph, raw, verdicts, merged, scan_coverage,
                  run_config={
                      "adjudication_model": model.model,
                      "window_seconds": args.window_seconds,
                      "overlap_seconds": args.overlap_seconds,
                      "scan_fps": args.scan_fps,
                      "spill_review_fps": args.spill_review_fps,
                      "evidence_fps": args.evidence_fps,
                      "collision_stride": args.collision_stride,
                      "primary_camera": args.primary_camera,
                      "pour_tea": bool(args.pour_tea),
                      "physical_report_revision": neuro_payload.get(
                          "algorithm_revision", "legacy"),
                      "physical_threshold_profile": neuro_payload.get(
                          "threshold_profile", {}),
                  }, safety_capabilities=safety_capabilities)

    if not args.no_visualization:
        primary = args.episode / f"{args.primary_camera}.mp4"
        subprocess.run([sys.executable, "-m",
            __package__ + ".visualize_unified_events",
            "--video", str(primary), "--events", str(args.out / "events_merged.json"),
            "--out", str(args.out / "safety_timeline.mp4")], check=False)

    # Annotation access is kept below all inference and reporting writes.
    if args.annotations:
        _evaluate_after_detection(args.annotations, merged, args.out,
                                  args.match_tolerance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
