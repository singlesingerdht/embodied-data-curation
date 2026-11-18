import json
import time

import pytest

from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.safety_framework import (
    OpenAICompatibleVisionModel,
    SafetyCandidate, UnifiedSafetyAdjudicator, load_collision_candidates,
    load_lid_tracking_candidates, load_support_reappearance_candidates,
    merge_safety_candidates,
    _wall_clock_deadline,
    generate_with_retries,
)


def test_json_parser_ignores_surrounding_brace_text():
    raw = 'prefix {not json} JSON: {"ok": true} suffix {note}'
    assert OpenAICompatibleVisionModel._json(raw) == {"ok": True}


def test_json_parser_reports_missing_object():
    with pytest.raises(ValueError, match="no JSON object"):
        OpenAICompatibleVisionModel._json("plain text only")


class BrokenModel:
    model = "qwen3.8-max"

    def generate(self, prompt, images):
        raise TimeoutError("offline")


class FixedModel:
    model = "qwen3.8-max"

    def __init__(self, payload):
        self.payload = payload

    def generate(self, prompt, images):
        return self.payload, json.dumps(self.payload)


def test_wall_clock_deadline_interrupts_a_stalled_request():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with _wall_clock_deadline(.05):
            time.sleep(1)
    assert time.monotonic() - started < .5


def test_generate_with_retries_recovers_transient_failure(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    class Flaky:
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transient")
            return {"ok": True}, "ok"
    model = Flaky()
    assert generate_with_retries(model, "x", []) == ({"ok": True}, "ok")
    assert model.calls == 2


def test_generate_retry_changes_prompt_to_break_deterministic_parse_failure(
        monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    class MalformedThenFixed:
        def __init__(self): self.prompts = []
        def generate(self, prompt, images):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                raise json.JSONDecodeError("truncated", "{", 1)
            return {"ok": True}, "{\"ok\": true}"
    model = MalformedThenFixed()
    assert generate_with_retries(model, "original", []) == (
        {"ok": True}, "{\"ok\": true}")
    assert model.prompts[0] == "original"
    assert model.prompts[1].startswith("original")
    assert "RETRY FORMAT REQUIREMENT" in model.prompts[1]


def test_generate_retry_temporarily_expands_local_output_limit(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    class TruncatedThenFixed:
        def __init__(self):
            self.calls = 0
            self.max_new_tokens = 1024
            self.seen_limits = []
        def generate(self, prompt, images):
            self.calls += 1
            self.seen_limits.append(self.max_new_tokens)
            if self.calls == 1:
                raise json.JSONDecodeError("truncated", "{", 1)
            return {"ok": True}, '{"ok": true}'
    model = TruncatedThenFixed()
    assert generate_with_retries(model, "x", []) == (
        {"ok": True}, '{"ok": true}')
    assert model.seen_limits == [1024, 1536]
    assert model.max_new_tokens == 1024


def test_api_failure_is_needs_review_not_safe():
    candidate = SafetyCandidate("collision", 1, 2, objects=["teapot", "cup"])
    result = UnifiedSafetyAdjudicator(BrokenModel()).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert result.confidence <= .35


def test_scan_coverage_gap_cannot_be_upgraded_by_model():
    candidate = SafetyCandidate("hand_intervention", 10, 20,
                                source=["vlm_scan_failure"], prior_confidence=.2)
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .99,
        "event_type": "hand_intervention"})).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert result.confidence == .2


def test_hand_workspace_entry_does_not_inherit_nearby_objects():
    candidate = SafetyCandidate(
        "hand_intervention", 10, 12, objects=["teapot", "cup1"])
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .9,
        "event_type": "hand_intervention", "subtype": "hand_entry",
        "objects": ["teapot", "cup1"],
        "evidence_chain": ["hand enters workspace", "no object reaction"],
    })).judge(candidate, {}, [])
    assert result.objects == []
    assert result.verdict == "needs_review"


def test_generic_hand_interaction_language_is_not_confirmation():
    candidate = SafetyCandidate("hand_intervention", 28, 44)
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .98,
        "event_type": "hand_intervention", "objects": [],
        "evidence_chain": [
            "A hand approaches the kettle and remains near it.",
            "Its presence indicates active interaction with task objects."],
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert result.confidence == .5


def test_negated_contact_does_not_confirm_hand_intervention():
    candidate = SafetyCandidate("hand_intervention", 10, 12)
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .9,
        "event_type": "hand_intervention", "objects": ["teapot"],
        "evidence_chain": ["The hand is visible but does not touch the teapot."],
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert result.objects == []


def test_hand_visible_object_motion_retains_attribution():
    candidate = SafetyCandidate("hand_intervention", 10, 12)
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .9,
        "event_type": "hand_intervention", "subtype": "object_move",
        "objects": ["teapot"],
        "evidence_chain": ["hand touches teapot", "teapot motion begins"],
    })).judge(candidate, {}, [])
    assert result.objects == ["teapot"]


def test_single_view_local_hand_claim_cannot_override_clear_background_negative():
    model = FixedModel({
        "verdict": "confirmed", "confidence": .95,
        "event_type": "hand_intervention", "objects": ["teapot lid"],
        "evidence_chain": [
            "At 140.03s, left view: a human hand touches the teapot lid.",
            "At 140.37s, left view: the hand continues moving the lid."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    candidate = SafetyCandidate("hand_intervention", 140, 152, evidence={
        "vlm_observation": {
            "transition": "No new hand enters; person remains outside the workspace",
            "after": "Hands remain absent and no contact with task objects"},
        "visibility": {"state": "clear"}})
    result = UnifiedSafetyAdjudicator(model).judge(candidate, {}, [])
    assert result.verdict == "rejected"
    assert result.objects == []


def test_plural_background_people_outside_workspace_are_not_hands():
    model = FixedModel({
        "verdict": "confirmed", "confidence": .95,
        "event_type": "hand_intervention", "objects": ["teapot lid"],
        "evidence_chain": [
            "At 140.03s, left view: a hand touches the teapot lid."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    candidate = SafetyCandidate("hand_intervention", 140, 152, evidence={
        "vlm_observation": {
            "transition": "No new hand/arm entry; background persons remain outside workspace",
            "after": "Hands remain absent from task workspace"},
        "visibility": {"state": "clear"}})
    result = UnifiedSafetyAdjudicator(model).judge(candidate, {}, [])
    assert result.verdict == "rejected"
    assert result.objects == []


def test_partial_background_presence_cannot_be_promoted_to_hand_intervention():
    model = FixedModel({
        "verdict": "confirmed", "confidence": .95,
        "event_type": "hand_intervention", "objects": ["kettle"],
        "evidence_chain": [
            "At 160.0s, left view: a hand touches the kettle."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    candidate = SafetyCandidate(
        "hand_intervention", 159.5, 162,
        subtype="background_human_presence_review", evidence={
            "vlm_observation": {
                "transition": "Person remains at the table edge outside the workspace",
                "after": "No contact with task objects; robot continues uninterrupted"},
            "visibility": {"state": "partial"}})
    result = UnifiedSafetyAdjudicator(model).judge(candidate, {}, [])
    assert result.verdict == "rejected"
    assert result.objects == []


def test_generic_human_motion_does_not_ground_an_object():
    model = FixedModel({
        "verdict": "confirmed", "confidence": .8,
        "event_type": "hand_intervention", "objects": ["teapot"],
        "evidence_chain": [
            "At 94.0s, a person moves in the background near the teapot."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    result = UnifiedSafetyAdjudicator(model).judge(
        SafetyCandidate("hand_intervention", 94, 95), {}, [])
    assert result.objects == []


@pytest.mark.parametrize("transition,after", [
    ("Brief contact possible near the teapot handle.",
     "The arm withdraws; no object displacement confirmed."),
    ("Fingers contact or hover at the teapot lid.",
     "The teapot and lid remain in place."),
])
def test_ambiguous_proximity_without_object_change_is_not_intervention(
        transition, after):
    model = FixedModel({
        "verdict": "confirmed", "confidence": .95,
        "event_type": "hand_intervention", "objects": ["teapot"],
        "evidence_chain": [
            "At 451.33s, right view: a hand touches the teapot."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    candidate = SafetyCandidate("hand_intervention", 451, 452, evidence={
        "vlm_observation": {"transition": transition, "after": after},
        "visibility": {"state": "partial"}})
    result = UnifiedSafetyAdjudicator(model).judge(candidate, {}, [])
    assert result.verdict == "rejected"
    assert result.objects == []


def test_visible_human_driven_object_change_remains_intervention():
    model = FixedModel({
        "verdict": "confirmed", "confidence": .95,
        "event_type": "hand_intervention", "objects": ["cup1"],
        "evidence_chain": [
            "At 208.0s, right view: a hand grasps and moves cup1."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    candidate = SafetyCandidate("hand_intervention", 207, 209, evidence={
        "vlm_observation": {
            "transition": "The hand grasps cup1 and repositions it.",
            "after": "Cup1 remains on the table after being moved."},
        "visibility": {"state": "clear"}})
    result = UnifiedSafetyAdjudicator(model).judge(candidate, {}, [])
    assert result.verdict == "confirmed"
    assert result.objects == ["cup1"]


def test_direct_contact_and_passive_voice_motion_are_explicit_actions():
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.safety_framework import has_explicit_hand_object_action
    assert has_explicit_hand_object_action(
        ["The hand makes direct contact with the teapot."])
    assert has_explicit_hand_object_action(
        ["The teapot is being moved by the human hand."])


def test_dense_object_change_can_resolve_ambiguous_coarse_contact():
    model = FixedModel({
        "verdict": "confirmed", "confidence": .95,
        "event_type": "hand_intervention", "objects": ["kettle"],
        "evidence_chain": [
            "At 4.0s, front view: the hand is gripping and moving the kettle."],
    })
    model.model = "local-qwen3-vl-8b-instruct"
    candidate = SafetyCandidate("hand_intervention", 3, 5, evidence={
        "vlm_observation": {
            "transition": "The hand contacts or hovers at the kettle handle."},
        "visibility": {"state": "partial"}})
    result = UnifiedSafetyAdjudicator(model).judge(candidate, {}, [])
    assert result.verdict == "confirmed"
    assert result.objects == ["kettle"]


def test_adjudicator_cannot_silently_change_event_type():
    candidate = SafetyCandidate("spill", 3, 4)
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .8, "event_type": "collision",
        "evidence_chain": ["new stream", "wet region"]})).judge(candidate, {}, [])
    assert result.event_type == "spill"
    assert any("kept candidate type" in x for x in result.limitations)


def test_active_pour_audit_rejection_requires_complete_observability():
    candidate = SafetyCandidate("spill", 3, 9, subtype="active_pour_audit")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .85, "event_type": "spill",
        "limitations": ["partial view; small drips may be hidden"]
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("cannot be safely rejected" in x for x in result.limitations)


def test_residual_drip_review_rejection_requires_complete_observability():
    candidate = SafetyCandidate(
        "spill", 3, 9, subtype="residual_spout_drip_review")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .85, "event_type": "spill",
        "limitations": ["spout partially occluded; tiny drip unresolved"]
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("cannot be safely rejected" in x for x in result.limitations)


def test_active_pour_audit_can_be_rejected_with_complete_view():
    candidate = SafetyCandidate("spill", 3, 9, subtype="active_pour_audit")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .9, "event_type": "spill",
        "limitations": []
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_active_pour_audit_accepts_two_view_containment_and_dry_surface():
    candidate = SafetyCandidate("spill", 3, 9, subtype="active_pour_audit")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .85, "event_type": "spill",
        "evidence_chain": [
            "front view: stream terminates inside the receiver",
            "left view: table remains dry with no new persistent wet region",
        ],
        "limitations": ["right view occluded by kettle body"],
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_collision_merge_requires_same_object_pair():
    events = [
        SafetyCandidate("collision", 10, 11, objects=["teapot", "cup"]),
        SafetyCandidate("collision", 11.2, 12, objects=["teapot_lid", "cup"]),
    ]
    merged = merge_safety_candidates(events, gap=2)
    assert len(merged) == 2


def test_collision_merge_accepts_reversed_same_pair():
    events = [
        SafetyCandidate("collision", 10, 11, objects=["teapot", "cup"]),
        SafetyCandidate("collision", 11.2, 12, objects=["cup", "teapot"]),
    ]
    merged = merge_safety_candidates(events, gap=2)
    assert len(merged) == 1


def test_collision_merge_finds_same_pair_across_interleaved_candidate():
    events = [
        SafetyCandidate("collision", 4.2, 18.8,
                        objects=["teapot_lid", "right_gripper"]),
        SafetyCandidate("collision", 5.4, 5.4,
                        objects=["kettle", "table"]),
        SafetyCandidate("collision", 16.2, 24.2,
                        objects=["right_gripper", "teapot_lid"]),
    ]
    merged = merge_safety_candidates(events, gap=2, max_duration={"collision": 30})
    assert [(x.start, x.end) for x in merged] == [(4.2, 24.2), (5.4, 5.4)]


def test_strong_collision_with_unresolved_interface_is_review():
    candidate = SafetyCandidate("collision", 3, 4, evidence={"geometry": {
        "depth_contact_consistent": True}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .82, "event_type": "collision",
        "limitations": ["single view occlusion; cannot rule out grazing contact"]
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("cannot be safely rejected" in x for x in result.limitations)


def test_strong_collision_can_be_rejected_by_clear_multiview_negative():
    candidate = SafetyCandidate("collision", 3, 4, evidence={"geometry": {
        "first_contact_transition": True}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .92, "event_type": "collision",
        "evidence_chain": [
            "front view shows a visible gap and clear separation throughout",
            "left view confirms diverging motion and no rigid-body reaction",
        ],
        "limitations": ["right view occluded; tiny contact cannot be excluded"],
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_dense_large_stable_gap_rejects_wrong_pair_contact():
    candidate = SafetyCandidate("collision", 17.3, 17.3,
        objects=["teapot", "teapot lid"], evidence={"geometry": {
            "first_contact_transition": True,
            "motion_state": "forbidden_target_contact"}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .85,
        "event_type": "collision",
        "evidence_chain": [
            "front dense ROI shows the pair gap is large and stable",
            "there is no gap closure"],
        "limitations": ["unrelated gripper-handle interface is occluded"],
        "safety_impact": "No candidate-pair contact.",
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_unobservable_collision_bridge_cannot_be_false_safe():
    candidate = SafetyCandidate("collision", 479, 481,
        objects=["teapot", "cup"], evidence={"geometry": {
            "dense_temporal_sampling": True, "occlusion_bridge": True,
            "visibility": "unobservable"}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .8, "event_type": "collision",
        "evidence_chain": [
            "front, left and right views appear to show gap remains",
            "no rigid-body reaction or cup displacement"],
        "limitations": [
            "closest contact interface is occluded; exact contact cannot be excluded"],
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"


def test_projection_only_contact_cannot_confirm_3d_collision():
    candidate = SafetyCandidate("collision", 29.9, 31.5,
        objects=["kettle", "teapot"], evidence={"geometry": {
            "first_contact_transition": True}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .85,
        "event_type": "collision",
        "evidence_chain": [
            "front view masks appear to touch with silhouette overlap",
            "teapot shows no displacement or rigid-body reaction"],
        "limitations": [
            "contact interface is partially occluded; side views do not resolve it"],
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("cannot confirm a 3D collision" in x for x in result.limitations)


def test_controlled_grasp_contact_is_not_forced_to_collision_review():
    candidate = SafetyCandidate("collision", 6.3, 6.3,
        objects=["gripper:right_arm", "teapot"], evidence={"geometry": {
            "first_contact_transition": True,
            "motion_state": "unbound_gripper_target_contact"}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .8, "event_type": "collision",
        "evidence_chain": [
            "controlled intended grasp approach",
            "sustained grasp comotion during the intended task step"],
        "limitations": ["contact interface partially occluded"],
        "safety_impact": "Intended grasp, not an unintended collision.",
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_sam3_prefixed_controlled_grasp_is_not_forced_to_review():
    candidate = SafetyCandidate("collision", 34.1, 38.3,
        objects=["gripper:right_arm", "kettle"], evidence={"geometry": {
            "depth_contact_consistent": True,
            "motion_state": "sam3_unbound_gripper_target_contact"}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .82,
        "event_type": "collision",
        "evidence_chain": [
            "kettle remains in a sustained grasp with smooth pouring"],
        "limitations": ["contact interface partially occluded"],
        "safety_impact": "Contact is the intended grasp, not impact.",
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_controlled_grasp_review_converges_to_rejected():
    candidate = SafetyCandidate("collision", 6.3, 6.3,
        objects=["gripper:right_arm", "teapot"], evidence={"geometry": {
            "first_contact_transition": True,
            "motion_state": "unbound_gripper_target_contact"}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "needs_review", "confidence": .45,
        "event_type": "collision",
        "evidence_chain": [
            "controlled intended grasp with controlled comotion",
            "no rigid-body reaction, knock, slide or tilt"],
        "limitations": ["exact finger interface self-occluded"],
        "safety_impact": "Low risk intended grasp.",
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_taskgraph_allowed_support_reappearance_is_not_collision_review():
    candidate = SafetyCandidate("collision", 8.3, 11.3,
        objects=["A_target:teapot", "B_env:table"],
        subtype="hidden_support_impact_review", evidence={"geometry": {
            "motion_state": "support_reappearance", "occlusion_bridge": True,
            "visibility": "occluded_transition", "center_jump_m": .1}})
    task_graph = {"contacts": [{
        "subject": "teapot", "object": "table",
        "disposition": "allowed", "interaction_mode": "support"}]}
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .8, "event_type": "collision",
        "evidence_chain": [
            "teapot remains upright and stationary on its intended support"],
        "limitations": ["contact interface occluded"],
        "safety_impact": "Allowed table support; no unintended impact.",
    })).judge(candidate, task_graph, [])
    assert result.verdict == "rejected"


def test_taskgraph_support_does_not_hide_hard_impact_geometry():
    candidate = SafetyCandidate("collision", 8.3, 11.3,
        objects=["A_target:teapot", "B_env:table"], evidence={"geometry": {
            "motion_state": "support_reappearance", "occlusion_bridge": True,
            "first_contact_transition": True}})
    task_graph = {"contacts": [{
        "subject": "teapot", "object": "table",
        "disposition": "allowed", "interaction_mode": "support"}]}
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .8, "event_type": "collision",
        "evidence_chain": ["no rigid-body reaction"],
        "limitations": ["exact contact is occluded and cannot be excluded"],
    })).judge(candidate, task_graph, [])
    assert result.verdict == "needs_review"


def test_weak_collision_can_still_be_rejected():
    candidate = SafetyCandidate("collision", 3, 4,
                                evidence={"geometry": {"center_gap": .2}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .9, "event_type": "collision",
        "limitations": ["single view"]
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_failed_grasp_displacement_cannot_be_rejected_as_intended_contact():
    candidate = SafetyCandidate("collision", 121.4, 122.7,
        evidence={"geometry": {"motion_state": "direct_lid_contact"}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .82,
        "event_type": "collision", "evidence_chain": [
            "Gripper closure causes visible lid displacement.",
            "The lid remains seated and is not lifted."],
        "limitations": []})).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("stable pickup" in x for x in result.limitations)


def test_hidden_lid_support_path_cannot_be_rejected_as_controlled():
    candidate = SafetyCandidate("lid_fall", 3, 4,
                                subtype="unobserved_support_loss")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .8, "event_type": "lid_fall",
        "limitations": ["gripper contact is occluded and inferred from co-motion"]
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("continuously visible support" in x for x in result.limitations)


def test_visual_lid_separation_plus_measured_empty_closure_confirms_drop():
    candidate = SafetyCandidate(
        "lid_fall", 333.33, 334.67,
        subtype="unobserved_support_loss", evidence={"robot_state": {
            "empty_closure_during_lid_transition": True}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .82,
        "event_type": "lid_fall", "evidence_chain": [
            "Before 333.30s the same lid is visibly seated on the teapot.",
            "At 334.58s the lid is separating with independent rotation.",
            "At 334.83s it rests stably on the cloth beside the teapot."],
        "limitations": ["gripper interface is occluded"]
    })).judge(candidate, {}, [])
    assert result.verdict == "confirmed"
    assert any("30 Hz gripper state" in x for x in result.limitations)


def test_generic_lid_chain_cannot_confirm_fall():
    candidate = SafetyCandidate(
        "lid_fall", 10, 14.8, subtype="unobserved_support_loss")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "confirmed", "confidence": .75,
        "event_type": "lid_fall",
        "evidence_chain": ["before", "transition", "consequence"],
        "limitations": ["path is inferred and not continuously resolved"]
    })).judge(candidate, {}, [])
    assert result.verdict == "needs_review"
    assert any("substantive timestamped" in x for x in result.limitations)


def test_empty_closure_does_not_override_explicit_no_fall_visual_chain():
    candidate = SafetyCandidate(
        "lid_fall", 96, 98.67, evidence={"robot_state": {
            "empty_closure_during_lid_transition": True}})
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .78,
        "event_type": "lid_fall", "evidence_chain": [
            "At 96.00s the lid starts beside the vessel.",
            "At 98.30s no lid is airborne although ballistic motion was proposed.",
            "At 98.67s it is centered on the rim."],
        "limitations": []
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_visible_controlled_lid_placement_can_be_rejected():
    candidate = SafetyCandidate("lid_fall", 3, 4,
                                subtype="unobserved_support_loss")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .9, "event_type": "lid_fall",
        "limitations": []
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"


def test_one_synchronized_view_resolving_seated_lid_overrides_other_occlusion():
    candidate = SafetyCandidate("lid_fall", 3, 4,
                                subtype="unobserved_support_loss")
    result = UnifiedSafetyAdjudicator(FixedModel({
        "verdict": "rejected", "confidence": .9, "event_type": "lid_fall",
        "evidence_chain": [
            "The left view maintains uninterrupted lid visibility; the lid "
            "remains continuously seated throughout the transition.",
            "There is no passive descent and no abnormal rest location."],
        "limitations": ["The right view is briefly occluded."],
    })).judge(candidate, {}, [])
    assert result.verdict == "rejected"
    assert not any("continuously visible support" in x
                   for x in result.limitations)


def test_support_reappearance_opens_review_not_confirmation(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"target_state_samples": [
        {"time_sec": 10, "name": "teapot", "eid": "A_target:teapot",
         "center": [0, 0, .9], "status": "occluded", "controlled": False,
         "table_distance_m": .05},
        {"time_sec": 10.1, "name": "teapot", "eid": "A_target:teapot",
         "center": [.06, 0, .87], "status": "tracking", "controlled": False,
         "table_distance_m": .005},
    ]}))
    events = load_support_reappearance_candidates(report)
    assert len(events) == 1
    assert events[0].type == "collision"
    assert events[0].subtype == "hidden_support_impact_review"
    assert events[0].prior_confidence == .3
    assert events[0].evidence["geometry"]["collision_valid"] is False


def test_collision_loader_retains_occlusion_as_low_prior(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"collision_candidates": [{
        "time_sec": 4, "subject": "A_target:teapot", "object": "C_obstacle:cup1",
        "detail": {"collision_valid": False, "visibility": "occluded"}}]}))
    event = load_collision_candidates(report)[0]
    assert event.type == "collision"
    assert event.prior_confidence == .3
    assert event.evidence["visibility"]["collision_valid"] is False


def test_single_unobservable_tcp_anchor_gets_forward_contact_context(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"collision_candidates": [{
        "time_sec": 472.3, "subject": "A_target:teapot",
        "object": "C_obstacle:cup2", "detail": {
            "segment_start_sec": 472.3, "segment_end_sec": 472.3,
            "candidate_count": 1, "collision_valid": False,
            "visibility": "unobservable",
            "motion_state": "occluded_tcp_near_cup"}}]}))
    event = load_collision_candidates(report)[0]
    assert event.start == 471.3
    assert event.end == 481.3
    assert event.prior_confidence == .3
    assert event.evidence["geometry"]["physical_segment_start_sec"] == 472.3
    assert event.evidence["geometry"]["adjudication_interval_expanded"] is True


def test_grasp_contact_gets_later_support_transfer_evidence(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"collision_candidates": [{
        "time_sec": 122.7, "subject": "gripper:right_arm",
        "object": "A_target:teapot_lid", "detail": {
            "segment_start_sec": 121.4, "segment_end_sec": 122.7,
            "motion_state": "direct_lid_contact",
            "evidence_hypothesis_times_sec": [121.4, 122.7]}}]}))
    event = load_collision_candidates(report)[0]
    assert event.start == 121.4
    assert event.end == 136.7
    geometry = event.evidence["geometry"]
    assert geometry["physical_segment_end_sec"] == 122.7
    assert geometry["grasp_outcome_interval_expanded"] is True
    assert geometry["evidence_hypothesis_times_sec"][-4:] == [
        126.7, 130.7, 134.7, 136.7]


def test_lid_chain_requires_uncontrolled_downward_separation(tmp_path):
    samples = []
    for frame, lid_z, table_distance, controlled in [
            (0, .20, .20, False), (1, .20, .20, False),
            (2, .12, .12, False), (3, .08, .08, False), (4, .04, .04, False)]:
        samples.extend([
            {"frame": frame, "time_sec": frame / 10, "name": "teapot lid",
             "center": [0, 0, lid_z], "status": "tracking",
             "controlled": controlled, "collision_valid": True,
             "table_distance_m": table_distance},
            {"frame": frame, "time_sec": frame / 10, "name": "teapot",
             "center": [0, 0, .20], "status": "tracking",
             "controlled": False, "collision_valid": True,
             "table_distance_m": .02},
        ])
    report = tmp_path / "report.json"
    report.write_text(json.dumps({
        "task_graph": {"entities": [
            {"name": "teapot lid", "component_of": "teapot"},
            {"name": "teapot", "component_of": None}]},
        "target_state_samples": samples}))
    events = load_lid_tracking_candidates(
        report, min_separation_m=.04, min_downward_motion_m=.02, confirmations=2)
    assert len(events) == 1
    assert events[0].type == "lid_fall"
    assert events[0].source == ["tracking"]


def test_controlled_lid_removal_does_not_trigger(tmp_path):
    samples = []
    for frame, z in enumerate((.2, .15, .1, .05)):
        samples.extend([
            {"frame": frame, "time_sec": frame, "name": "teapot lid",
             "center": [0, 0, z], "status": "driven", "controlled": True,
             "collision_valid": True, "table_distance_m": z},
            {"frame": frame, "time_sec": frame, "name": "teapot",
             "center": [0, 0, .2], "status": "tracking", "controlled": False,
             "collision_valid": True, "table_distance_m": .02},
        ])
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"task_graph": {"entities": [
        {"name": "teapot lid", "component_of": "teapot"},
        {"name": "teapot"}]}, "target_state_samples": samples}))
    assert load_lid_tracking_candidates(report, confirmations=2) == []


def test_merge_preserves_sources_and_evidence():
    first = SafetyCandidate("spill", 1, 2, source=["vlm_scan"],
                            evidence={"vlm_observation": {"before": "dry"}})
    second = SafetyCandidate("spill", 2.5, 3, source=["tracking"],
                             evidence={"tracking": {"persistent": True}})
    merged = merge_safety_candidates([first, second], gap=1)
    assert len(merged) == 1
    assert set(merged[0].source) == {"vlm_scan", "tracking"}
    assert "merged_candidates" in merged[0].evidence
