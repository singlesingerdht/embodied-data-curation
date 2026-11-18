import json
from pathlib import Path
from types import SimpleNamespace

from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import run_four_event_pipeline as four_pipeline
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.run_four_event_pipeline import (_merge_adjudicated,
                                       _candidate_fingerprint,
                                       _evaluate_after_detection,
                                       _filter_allowed_assembly_internal_contacts,
                                       _filter_stationary_far_ellipsoid_contacts,
                                       _load_persisted_verdicts,
                                       _adjudication_priority,
                                       _needs_grasp_outcome_audit,
                                       _resolve_grasp_outcome_audit,
                                       _run_collision,
                                       _needs_hand_confirmation,
                                       _needs_hand_dense_evidence,
                                       _needs_collision_dense_evidence,
                                       _needs_collision_recheck,
                                       _needs_lid_recheck,
                                       _needs_lid_dense_evidence,
                                       _needs_spill_confirmation,
                                       _needs_spill_dense_evidence,
                                       _resolve_dense_spill_negative,
                                       _policy_replay_source, _write_report)
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.safety_framework import SafetyCandidate, SafetyVerdict


def test_persisted_verdict_recovery_uses_merged_and_current_checkpoint(tmp_path):
    (tmp_path / "events_merged.json").write_text(json.dumps([{
        "adjudications": [
            {"candidate_id": "old-a", "raw_output": "{}"},
            {"candidate_id": "shared", "candidate_fingerprint": "old-fp",
             "raw_output": "old"}],
    }]))
    (tmp_path / "events_adjudicated.json").write_text(json.dumps([
        {"candidate_id": "shared", "candidate_fingerprint": "current-fp",
         "raw_output": "current"},
        {"candidate_id": "new-b", "raw_output": "{}"},
    ]))
    recovered = _load_persisted_verdicts(tmp_path)
    assert [x["candidate_id"] for x in recovered] == [
        "old-a", "shared", "shared", "new-b"]
    assert recovered[-2]["raw_output"] == "current"


def test_persisted_verdict_recovery_tolerates_corrupt_checkpoint(tmp_path):
    (tmp_path / "events_merged.json").write_text(json.dumps([{
        "adjudications": [{"candidate_id": "survivor"}],
    }]))
    (tmp_path / "events_adjudicated.json").write_text("{")
    assert _load_persisted_verdicts(tmp_path) == [
        {"candidate_id": "survivor"}]


def test_adjudication_priority_puts_contact_occlusion_and_lid_chain_first():
    ordinary = SafetyCandidate("collision", 1, 2,
                               evidence={"geometry": {"motion_state": "driven"}})
    lid_chain = SafetyCandidate("lid_fall", 3, 4, prior_confidence=.35)
    hidden = SafetyCandidate("collision", 5, 6, prior_confidence=.3,
                             evidence={"geometry": {
                                 "motion_state": "occluded_tcp_near_cup",
                                 "visibility": "unobservable"}})
    strong = SafetyCandidate("collision", 7, 8, evidence={"geometry": {
        "first_contact_transition": True}})
    direct = SafetyCandidate("spill", 9, 10)
    ordered = sorted([direct, ordinary, lid_chain, hidden, strong],
                     key=_adjudication_priority)
    assert ordered == [strong, hidden, lid_chain, ordinary, direct]


def test_grasp_outcome_audit_targets_only_unresolved_expanded_contacts():
    candidate = SafetyCandidate("collision", 1, 9, evidence={"geometry": {
        "grasp_outcome_interval_expanded": True}})
    assert _needs_grasp_outcome_audit(
        candidate, SimpleNamespace(verdict="confirmed"))
    assert _needs_grasp_outcome_audit(
        candidate, SimpleNamespace(verdict="needs_review"))
    assert not _needs_grasp_outcome_audit(
        candidate, SimpleNamespace(verdict="rejected"))


def test_lid_candidate_gets_empty_gripper_closure_evidence(tmp_path):
    frames = []
    for index in range(50):
        before = index < 30
        frames.append({
            "follow_left_gripper": .01,
            "follow_left_joint_effort": [0, 0, 0, 0, 0, 0, .01],
            "follow_right_gripper": .12 if before else .005,
            "follow_right_joint_effort": [
                0, 0, 0, 0, 0, 0, -.7 if before else -.04],
        })
    (tmp_path / "episode.json").write_text(json.dumps({
        "fps": 10, "data": frames}))
    candidate = SafetyCandidate(
        "lid_fall", 2.0, 3.0, evidence={})
    four_pipeline._enrich_lid_gripper_state([candidate], tmp_path)
    state = candidate.evidence["robot_state"]
    assert state["active_arm"] == "right"
    assert state["empty_closure_during_lid_transition"] is True


def test_direct_lid_contact_audits_rejected_intended_grasp_interpretation():
    candidate = SafetyCandidate("collision", 1, 9, evidence={"geometry": {
        "motion_state": "direct_lid_contact",
        "grasp_outcome_interval_expanded": True}})
    assert _needs_grasp_outcome_audit(
        candidate, SimpleNamespace(verdict="rejected"))


def test_disagreeing_adversarial_grasp_audit_removes_confirmation():
    initial = SimpleNamespace(verdict="confirmed")
    audit = SimpleNamespace(verdict="rejected", limitations=[])
    result = _resolve_grasp_outcome_audit(initial, audit)
    assert result.verdict == "needs_review"
    assert any("disagree" in x for x in result.limitations)


def test_collision_uses_explicit_edc_worker_inputs(tmp_path, monkeypatch):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl import physical_worker
    observed=[]
    monkeypatch.setattr(physical_worker, "run_physical", lambda work,out,model=None: observed.append((work,out)))
    args=SimpleNamespace(episode=tmp_path/"input")
    _run_collision(args,tmp_path/"native")
    assert observed==[(tmp_path,tmp_path/"native")]


def test_candidate_fingerprint_ignores_runtime_review_metadata():
    candidate = SafetyCandidate(
        type="collision", start=1, end=2, objects=["teapot", "cup"],
        evidence={"geometry": {"distance": .01}, "task_context": {}})
    before = _candidate_fingerprint(candidate)
    candidate.evidence["task_context"].update({
        "review_stage": "high_fps_collision_recheck",
        "initial_verdict": "rejected",
        "initial_limitations": ["coarse view"],
        "confirmation_rule": "dense spill evidence required",
        "adjudication_evidence_protocol": {
            "dense": True, "primary_roi_fps": 10.0},
    })
    assert _candidate_fingerprint(candidate) == before


def test_allowed_assembly_internal_overlap_is_not_collision_candidate():
    internal = SafetyCandidate(
        "collision", 3, 3, objects=["A_target:teapot_lid", "A_target:teapot"],
        evidence={"geometry": {"motion_state": "forbidden_target_contact"}})
    external = SafetyCandidate(
        "collision", 3, 3, objects=["gripper:right_arm", "A_target:teapot_lid"],
        evidence={"geometry": {"motion_state": "lid_motion_contact_candidate"}})
    graph = {"contacts": [{
        "subject": "teapot lid", "object": "teapot",
        "disposition": "allowed", "interaction_mode": "assembly"}, {
        "subject": "teapot lid", "object": "teapot",
        "disposition": "forbidden", "interaction_mode": "rigid_contact"}]}
    assert _filter_allowed_assembly_internal_contacts(
        [internal, external], graph) == [external]


def test_stationary_far_single_ellipsoid_overlap_is_filtered():
    false_contact = SafetyCandidate(
        "collision", 78.8, 78.8, objects=["kettle", "teapot"],
        evidence={"geometry": {
            "motion_state": "forbidden_target_contact", "center_gap": .213,
            "subject_step_m": 0.0, "candidate_count": 1}})
    moving_contact = SafetyCandidate(
        "collision", 78.8, 78.8, objects=["kettle", "teapot"],
        evidence={"geometry": {
            "motion_state": "forbidden_target_contact", "center_gap": .213,
            "subject_step_m": .004, "candidate_count": 1}})
    depth_contact = SafetyCandidate(
        "collision", 78.8, 78.8, objects=["kettle", "teapot"],
        evidence={"geometry": {
            "motion_state": "forbidden_target_contact", "center_gap": .213,
            "subject_step_m": 0.0, "candidate_count": 1,
            "depth_contact_consistent": True}})
    assert _filter_stationary_far_ellipsoid_contacts(
        [false_contact, moving_contact, depth_contact]
    ) == [moving_contact, depth_contact]


def test_candidate_fingerprint_ignores_created_empty_review_context():
    candidate = SafetyCandidate(
        type="collision", start=1, end=2, objects=["teapot", "cup"],
        evidence={"geometry": {"distance": .01}})
    before = _candidate_fingerprint(candidate)
    candidate.evidence.setdefault("task_context", {}).update({
        "review_stage": "high_fps_collision_recheck",
        "initial_verdict": "rejected",
        "initial_limitations": ["coarse view"],
        "confirmation_rule": "inspect dense frames",
    })
    assert _candidate_fingerprint(candidate) == before


def test_collision_coarse_evidence_caps_fps_before_dense_recheck(monkeypatch):
    observed = []
    monkeypatch.setattr(
        four_pipeline.DirectEventScanner, "discover_views",
        staticmethod(lambda _episode: [("front", Path("faceImg.mp4"))]))
    monkeypatch.setattr(
        four_pipeline, "make_chunked_contact_sheets",
        lambda path, start, end, fps: observed.append(fps) or
        [(start, end, b"sheet")])
    images = four_pipeline._evidence_images(
        Path("episode"), SafetyCandidate("collision", 1, 2), 3.0)
    assert observed == [1.5]
    assert images


def test_dense_collision_without_roi_uses_synchronized_views(tmp_path,
                                                              monkeypatch):
    front, left = tmp_path / "faceImg.mp4", tmp_path / "leftImg.mp4"
    front.touch(); left.touch()
    monkeypatch.setattr(
        four_pipeline.DirectEventScanner, "discover_views",
        staticmethod(lambda _episode: [("front", front), ("left", left)]))
    monkeypatch.setattr(
        four_pipeline, "make_chunked_contact_sheets",
        lambda path, start, end, fps, chunk_seconds=2.0:
        [(start, end, path.name.encode())])
    candidate = SafetyCandidate("collision", 74.1, 74.1,
        evidence={"geometry": {
            "motion_state": "unverified_grasp_contact",
            "candidate_times_sec": [74.1, 75.0]}})
    images = four_pipeline._collision_roi_images(tmp_path, candidate)
    assert len(images) == 4
    assert {label.split()[0] for label, _ in images} == {"front", "left"}


def test_dense_collision_roi_keeps_auxiliary_full_view(tmp_path, monkeypatch):
    front, left = tmp_path / "faceImg.mp4", tmp_path / "leftImg.mp4"
    front.touch(); left.touch()
    monkeypatch.setattr(
        four_pipeline.DirectEventScanner, "discover_views",
        staticmethod(lambda _episode: [("front", front), ("left", left)]))
    monkeypatch.setattr(
        four_pipeline, "make_roi_contact_sheet",
        lambda *args, **kwargs: b"roi")
    monkeypatch.setattr(
        four_pipeline, "make_chunked_contact_sheets",
        lambda path, start, end, fps, chunk_seconds=2.0:
        [(start, end, b"full")])
    candidate = SafetyCandidate("collision", 97.5, 97.5,
        evidence={"geometry": {"view": "faceImg",
            "roi_xyxy": [10, 10, 100, 100]}})
    images = four_pipeline._collision_roi_images(tmp_path, candidate)
    assert [payload for _, payload in images] == [b"roi", b"full"]
    assert "ROI" in images[0][0]
    assert "SYNCHRONIZED FULL VIEW" in images[1][0]


def test_occlusion_bridge_dense_evidence_covers_interval_continuously(
        tmp_path, monkeypatch):
    front, left = tmp_path / "faceImg.mp4", tmp_path / "leftImg.mp4"
    front.touch(); left.touch()
    calls = []
    monkeypatch.setattr(
        four_pipeline.DirectEventScanner, "discover_views",
        staticmethod(lambda _episode: [("front", front), ("left", left)]))
    monkeypatch.setattr(
        four_pipeline, "make_chunked_contact_sheets",
        lambda path, start, end, fps, chunk_seconds=2.0:
        calls.append((path.name, start, end, fps)) or
        [(start, end, b"full")])
    candidate = SafetyCandidate("collision", 10.0, 15.0,
        evidence={"geometry": {
            "visibility": "unobservable", "occlusion_bridge": True,
            # Deliberately sparse and misleading anchors must be ignored.
            "evidence_hypothesis_times_sec": [10.0, 15.0]}})
    images = four_pipeline._collision_roi_images(tmp_path, candidate)
    assert len(images) == 6
    for path in ("faceImg.mp4", "leftImg.mp4"):
        spans = [(start, end) for name, start, end, _ in calls if name == path]
        assert spans == [(9.6, 11.6), (11.6, 13.6), (13.6, 15.4)]
    assert all("CONTINUOUS HIGH-FPS" in label for label, _ in images)


def test_confirmed_spill_requires_dense_confirmation():
    candidate = SafetyCandidate("spill", 10, 12, subtype="missed_target")
    assert _needs_spill_confirmation(
        candidate, SimpleNamespace(verdict="confirmed"))
    assert not _needs_spill_confirmation(
        candidate, SimpleNamespace(verdict="needs_review", limitations=[]))
    review = SafetyCandidate(
        "spill", 10, 12, subtype="residual_spout_drip_review")
    assert _needs_spill_confirmation(
        review, SimpleNamespace(verdict="needs_review", limitations=[]))
    assert not _needs_spill_confirmation(
        review, SimpleNamespace(
            verdict="needs_review",
            limitations=["adjudication unavailable: RuntimeError"]))


def test_confirmed_hand_requires_dense_object_attribution():
    candidate = SafetyCandidate("hand_intervention", 436, 442)
    assert _needs_hand_confirmation(
        candidate, SimpleNamespace(verdict="confirmed"))
    assert not _needs_hand_confirmation(
        candidate, SimpleNamespace(verdict="needs_review"))
    assert _needs_hand_dense_evidence(candidate)


def test_every_spill_candidate_requires_dense_evidence():
    assert _needs_spill_dense_evidence(
        SafetyCandidate("spill", 10, 12, subtype="missed_receiver"))


def test_dense_spill_negative_rejects_subresolution_doubt():
    verdict = SafetyVerdict(
        candidate_id="spill-a", verdict="needs_review", confidence=.85,
        event_type="spill",
        evidence_chain=[
            "Dense front and left views show no visible stream or droplet.",
            "The cloth remains unchanged with no new persistent wet region."],
        limitations=["A sub-resolution droplet cannot be fully excluded."])
    resolved = _resolve_dense_spill_negative(verdict)
    assert resolved.verdict == "rejected"
    assert "sub-resolution droplets alone" in resolved.limitations[-1]


def test_dense_spill_negative_understands_negated_positive_phrase():
    verdict = SafetyVerdict(
        candidate_id="spill-negated", verdict="needs_review", confidence=.9,
        event_type="spill",
        evidence_chain=[
            "No stream, droplets, or splash is visible outside the rim.",
            "No new persistent wet region appears; the cloth remains dry."],
        limitations=["Tiny droplets cannot be excluded."])
    assert _resolve_dense_spill_negative(verdict).verdict == "rejected"


def test_dense_spill_negative_rejects_contained_flow_with_unchanged_surfaces():
    verdict = SafetyVerdict(
        candidate_id="spill-contained", verdict="needs_review", confidence=.82,
        event_type="spill",
        evidence_chain=[
            "A continuous stream terminates inside the cup opening, not outside it.",
            "No droplets are visible and scene surfaces remain unchanged."],
        limitations=["The cup interior is not visible from above."])
    assert _resolve_dense_spill_negative(verdict).verdict == "rejected"


def test_dense_spill_negative_preserves_positive_outside_trajectory():
    verdict = SafetyVerdict(
        candidate_id="spill-b", verdict="needs_review", confidence=.45,
        event_type="spill",
        evidence_chain=[
            "A thin trailing stream is visible at or just beyond the rim; an "
            "escaped-liquid consequence is suggested.",
            "No new persistent wet region is resolved."],
        limitations=["The exact landing point is occluded."])
    assert _resolve_dense_spill_negative(verdict).verdict == "needs_review"


def test_v51_direct_lid_verdict_is_policy_replay_compatible():
    candidate = SafetyCandidate(
        "collision", 123, 124, objects=["gripper", "teapot lid"],
        evidence={"geometry": {"motion_state": "direct_lid_contact"}})
    fingerprint = _candidate_fingerprint(
        candidate, "four_event_v4_visual_negative_lid_gate_v51")
    verdict = {
        "candidate_id": candidate.candidate_id,
        "candidate_fingerprint": fingerprint,
        "verdict": "rejected", "confirmation_stage": "dense_collision_v1",
        "raw_output": json.dumps({"verdict": "rejected"}),
    }
    assert (_policy_replay_source(verdict, candidate)
            == "four_event_v4_visual_negative_lid_gate_v51")


def test_missing_persisted_verdict_becomes_needs_review():
    candidate = SafetyCandidate("spill", 1, 2)
    merged = _merge_adjudicated([candidate], [], 2, {})
    assert len(merged) == 1
    assert merged[0]["verdict"] == "needs_review"
    assert merged[0]["confidence"] <= .35


def test_report_ignores_stale_verdict_ids(tmp_path):
    candidate = SafetyCandidate("spill", 1, 2)
    merged = _merge_adjudicated([candidate], [], 2, {})
    _write_report(tmp_path, {"source": "test"}, [candidate], [{
        "candidate_id": "stale", "verdict": "confirmed", "confidence": 1,
        "event_type": "spill"}], merged,
        run_config={"adjudication_model": "qwen3.8-max"})
    report = json.loads((tmp_path / "safety_report.json").read_text())
    assert report["algorithm_revision"] == four_pipeline.PIPELINE_REVISION
    assert report["physical_revision"] == four_pipeline.PHYSICAL_REVISION
    assert report["run_config"]["adjudication_model"] == "qwen3.8-max"
    assert report["summary"] == {"confirmed": 0, "needs_review": 1,
                                  "rejected": 0}


def test_strong_unconfirmed_collision_gets_dense_recheck():
    candidate = SafetyCandidate("collision", 1, 2, evidence={"geometry": {
        "first_contact_transition": True, "roi_xyxy": [1, 2, 3, 4]}})
    assert _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="rejected"))
    assert not _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="confirmed"))


def test_unverified_grasp_gets_dense_collision_recheck():
    candidate = SafetyCandidate("collision", 74.1, 74.1,
        evidence={"geometry": {"motion_state": "unverified_grasp_contact"}})
    assert _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="rejected"))


def test_continuous_support_penetration_growth_gets_dense_recheck():
    candidate = SafetyCandidate("collision", 97.5, 97.5,
        evidence={"geometry": {"penetration_growth_m": .0034}})
    assert _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="rejected"))


def test_weak_geometry_does_not_force_dense_recheck():
    candidate = SafetyCandidate("collision", 1, 2,
                                evidence={"geometry": {"center_gap": .1}})
    assert not _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="rejected"))


def test_repeated_sam_contact_gets_dense_recheck():
    candidate = SafetyCandidate("collision", 1, 2, evidence={"geometry": {
        "roi_xyxy": [1, 2, 3, 4], "candidate_count": 5,
        "evidence_sources": ["driven", "sam3_2d_contact"]}})
    assert _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="rejected"))


def test_unobserved_lid_support_loss_gets_dense_recheck():
    candidate = SafetyCandidate("lid_fall", 3, 4,
                                subtype="unobserved_support_loss")
    assert _needs_lid_recheck(candidate, SimpleNamespace(verdict="needs_review"))
    assert not _needs_lid_recheck(candidate, SimpleNamespace(verdict="confirmed"))


def test_lid_recheck_samples_short_ballistic_motion_at_twelve_fps(
        tmp_path, monkeypatch):
    video = tmp_path / "faceImg.mp4"; video.touch()
    observed = []
    monkeypatch.setattr(
        four_pipeline.DirectEventScanner, "discover_views",
        staticmethod(lambda _episode: [("front", video)]))
    monkeypatch.setattr(
        four_pipeline, "make_chunked_contact_sheets",
        lambda path, start, end, fps, chunk_seconds=2.0, tile_size=(480, 270):
        observed.append((fps, chunk_seconds, tile_size)) or
        [(start, end, b"sheet")])
    images = four_pipeline._lid_dense_images(
        tmp_path, SafetyCandidate("lid_fall", 333.3, 334.7))
    assert observed == [(12.0, 2.0, (420, 236))]
    assert images


def test_lid_dense_evidence_uses_bounded_auxiliary_sampling(
        tmp_path, monkeypatch):
    front = tmp_path / "faceImg.mp4"; front.touch()
    side = tmp_path / "leftImg.mp4"; side.touch()
    observed = []
    monkeypatch.setattr(
        four_pipeline.DirectEventScanner, "discover_views",
        staticmethod(lambda _episode: [("front", front), ("left", side)]))
    monkeypatch.setattr(
        four_pipeline, "make_chunked_contact_sheets",
        lambda path, start, end, fps, chunk_seconds=2.0, tile_size=(480, 270):
        observed.append((path.name, fps, chunk_seconds, tile_size)) or
        [(start, end, b"sheet")])
    four_pipeline._lid_dense_images(
        tmp_path, SafetyCandidate("lid_fall", 10, 14.8))
    assert observed == [
        ("faceImg.mp4", 12.0, 2.0, (420, 236)),
        ("leftImg.mp4", 8.0, 2.0, (360, 203)),
    ]


def test_dense_occlusion_bridge_requests_collision_recheck_without_roi():
    candidate = SafetyCandidate("collision", 3, 4, evidence={"geometry": {
        "dense_temporal_sampling": True, "occlusion_bridge": True}})
    assert _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="needs_review"))
    assert _needs_collision_dense_evidence(candidate)


def test_strong_confirmed_collision_still_requires_dense_evidence():
    candidate = SafetyCandidate("collision", 3, 4, evidence={"geometry": {
        "first_contact_transition": True}})
    assert _needs_collision_dense_evidence(candidate)
    assert not _needs_collision_recheck(
        candidate, SimpleNamespace(verdict="confirmed"))


def test_tracking_lid_confirmation_requires_dense_evidence():
    candidate = SafetyCandidate(
        "lid_fall", 3, 4, source=["tracking"],
        subtype="unobserved_support_loss")
    assert _needs_lid_dense_evidence(candidate)
    assert not _needs_lid_recheck(
        candidate, SimpleNamespace(verdict="confirmed"))


def test_candidate_fingerprint_changes_with_evidence():
    first = SafetyCandidate("spill", 1, 2, evidence={"visibility": "partial"})
    second = SafetyCandidate("spill", 1, 2, evidence={"visibility": "clear"})
    assert first.candidate_id == second.candidate_id
    assert _candidate_fingerprint(first) != _candidate_fingerprint(second)


def test_policy_replay_requires_explicit_compatible_revision():
    candidate = SafetyCandidate("collision", 1, 2, objects=["teapot", "table"])
    raw = '{"verdict":"rejected","event_type":"collision"}'
    compatible = four_pipeline.POLICY_REPLAY_COMPATIBLE_REVISIONS[0]
    assert _policy_replay_source({
        "candidate_fingerprint": _candidate_fingerprint(candidate, compatible),
        "raw_output": raw}, candidate) == compatible
    assert _policy_replay_source({
        "candidate_fingerprint": _candidate_fingerprint(
            candidate, compatible, legacy_empty_context=True),
        "raw_output": raw}, candidate) == compatible
    assert _policy_replay_source({
        "candidate_fingerprint": _candidate_fingerprint(candidate, "unknown"),
        "raw_output": raw}, candidate) is None
    assert _policy_replay_source({
        "candidate_fingerprint": _candidate_fingerprint(candidate, compatible),
        "raw_output": "not-json"}, candidate) is None


def test_strong_confirmed_collision_replay_requires_dense_stage():
    candidate = SafetyCandidate("collision", 1, 2, evidence={"geometry": {
        "first_contact_transition": True}})
    compatible = four_pipeline.POLICY_REPLAY_COMPATIBLE_REVISIONS[0]
    saved = {
        "candidate_fingerprint": _candidate_fingerprint(candidate, compatible),
        "raw_output": '{"verdict":"confirmed","event_type":"collision"}',
        "verdict": "confirmed",
    }
    assert _policy_replay_source(saved, candidate) is None
    saved["confirmation_stage"] = "dense_collision_v1"
    assert _policy_replay_source(saved, candidate) == compatible


def test_old_failed_grasp_semantics_are_not_policy_replayed():
    candidate = SafetyCandidate("collision", 121.4, 122.7,
        evidence={"geometry": {"motion_state": "direct_lid_contact"}})
    compatible = "four_event_v4_grasp_outcome_context_v38"
    saved = {
        "candidate_fingerprint": _candidate_fingerprint(candidate, compatible),
        "raw_output": '{"verdict":"rejected","event_type":"collision"}',
        "verdict": "rejected",
        "confirmation_stage": "dense_collision_v1",
    }
    assert _policy_replay_source(saved, candidate) is None


def test_v38_unverified_grasp_can_replay_as_adversarial_audit_input():
    candidate = SafetyCandidate("collision", 128.5, 136.5,
        evidence={"geometry": {
            "motion_state": "unverified_grasp_contact",
            "grasp_outcome_interval_expanded": True}})
    source = "four_event_v4_grasp_outcome_context_v38"
    saved = {
        "candidate_fingerprint": _candidate_fingerprint(candidate, source),
        "raw_output": '{"verdict":"needs_review","event_type":"collision"}',
        "verdict": "needs_review",
        "confirmation_stage": "dense_collision_v1",
    }
    assert _policy_replay_source(saved, candidate) == source


def test_annotation_report_separates_confirmation_from_review(tmp_path):
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"events": [
        {"type": "collision", "start": 10, "end": 11},
        {"type": "spill", "start": 20, "end": 21},
    ]}))
    merged = [
        {"type": "collision", "start": 10.2, "end": 10.8,
         "verdict": "needs_review", "confidence": .4, "objects": []},
        {"type": "spill", "start": 20.1, "end": 21.1,
         "verdict": "confirmed", "confidence": .8, "objects": []},
    ]
    _evaluate_after_detection(annotations, merged, tmp_path, tolerance=1)
    report = json.loads((tmp_path / "annotation_comparison.json").read_text())
    assert report["confirmed_count"] == 1
    assert report["covered_count"] == 2
    assert report["rows"][0]["status"] == "review_coverage"
