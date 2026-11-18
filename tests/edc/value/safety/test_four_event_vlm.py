import json

import cv2
import numpy as np

from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.four_event_vlm import (DirectEventScanner, _SCAN_PROMPT,
                              _filter_repeated_lid_losses,
                              _repeated_unobserved_lid_loss)
from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.safety_framework import SafetyCandidate


def test_prompt_requires_review_when_lid_support_change_is_occluded():
    assert "MANDATORY LID CONTINUITY AUDIT" in _SCAN_PROMPT
    assert "unobserved_support_loss" in _SCAN_PROMPT
    assert "MUST emit" in _SCAN_PROMPT


def test_repeated_unobserved_lid_loss_is_state_not_new_event():
    item = {
        "type": "lid_fall",
        "subtype": "unobserved_support_loss",
        "observation": {
            "before": "teapot lid already absent at interval start",
            "transition": "no change observed",
            "after": "lid remains absent",
        },
    }
    assert _repeated_unobserved_lid_loss(
        item, {"lids": "teapot lid absent throughout prior interval"})


def test_new_unobserved_lid_loss_is_retained():
    item = {
        "type": "lid_fall",
        "subtype": "unobserved_support_loss",
        "observation": {
            "before": "lid seated on teapot",
            "transition": "lid disappears behind gripper",
            "after": "lid absent",
        },
    }
    assert not _repeated_unobserved_lid_loss(
        item, {"lids": "teapot lid seated on teapot"})


def test_resume_filters_repeated_lid_absence_from_old_checkpoint():
    candidates = [SafetyCandidate(
        type="lid_fall", start=20, end=32,
        subtype="unobserved_support_loss",
        evidence={
            "task_context": {"window_index": 2},
            "vlm_observation": {
                "before": "lid already absent at interval start",
                "transition": "no change observed",
                "after": "lid remains absent",
            },
        })]
    history = [{"window_index": 2, "state_before": {
        "lids": "teapot lid absent throughout prior interval"}}]
    assert _filter_repeated_lid_losses(candidates, history) == []


def test_off_lid_without_motion_is_not_repeated_on_resume():
    item = {
        "type": "lid_fall", "subtype": "unobserved_support_loss",
        "observation": {
            "before": "teapot lid already OFF teapot",
            "transition": "no lid motion or passive descent observed",
            "after": "lid state unchanged",
        },
    }
    assert _repeated_unobserved_lid_loss(item, {
        "lids": "Teapot lid OFF teapot throughout; no lid drop observed"})


def test_controlled_hold_to_unknown_remains_reviewable():
    item = {
        "type": "lid_fall", "subtype": "unobserved_support_loss",
        "observation": {
            "before": "lid held at torso",
            "transition": "placement path hidden",
            "after": "lid remains absent",
        },
    }
    assert not _repeated_unobserved_lid_loss(item, {
        "lids": "Teapot lid OFF teapot; human held metallic lid at torso"})


def test_persistent_still_absent_wording_is_suppressed():
    item = {
        "type": "lid_fall", "subtype": "unobserved_support_loss",
        "observation": {
            "before": "lid already absent from teapot",
            "transition": "no new lid transition observed",
            "after": "lid remains absent",
        },
    }
    assert _repeated_unobserved_lid_loss(item, {
        "lids": "teapot lid still absent; persistent absence from prior intervals"})


def test_support_recovery_is_not_lid_fall():
    item = {
        "type": "lid_fall", "subtype": "unobserved_support_loss",
        "observation": {
            "before": "persistent state: lid absent from teapot",
            "transition": "teapot appears covered by a seated lid",
            "after": "lid seated stably; no fall observed",
        },
    }
    assert _repeated_unobserved_lid_loss(item, {
        "lids": "teapot lid still absent from prior intervals"})


class SequenceModel:
    model = "qwen3.8-max"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, images):
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            return ({"state": {"wet_regions": "patch A", "hand": "absent",
                               "lids": "seated"},
                     "candidates": [{"type": "spill", "start": 1, "end": 2,
                                      "confidence": .7,
                                      "observation": {"before": "dry",
                                                      "transition": "new stream",
                                                      "after": "patch A persists"}}]}, "first")
        return ({"state": {"wet_regions": "patch A", "hand": "absent",
                            "lids": "seated"}, "candidates": []}, "second")


def _video(path, seconds=3, fps=4):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                             (64, 48))
    for _ in range(seconds * fps):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()


def test_previous_wet_state_is_passed_to_next_window(tmp_path):
    _video(tmp_path / "faceImg.mp4")
    model = SequenceModel()
    events = DirectEventScanner(model, window_seconds=2, overlap_seconds=.5,
                                sample_fps=1).scan(tmp_path, {})
    assert len(events) == 1
    assert events[0].type == "spill"
    assert "patch A" in model.prompts[1]


def test_direct_scanner_never_generates_collision(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class BadModel:
        model = "qwen3.8-max"
        def generate(self, prompt, images):
            return ({"state": {}, "candidates": [
                {"type": "collision", "start": 0, "end": 1}]}, "bad")

    events = DirectEventScanner(BadModel(), window_seconds=2, overlap_seconds=.5,
                                sample_fps=1).scan(tmp_path, {})
    assert events == []


def test_disabled_direct_event_type_is_not_emitted_or_retried(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class HallucinatingModel:
        model = "local-test"
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            assert 'Runtime-enabled candidate types: ["hand_intervention"]' in prompt
            return ({"state": {"pouring": "active"}, "candidates": [
                {"type": "spill", "start": 0, "end": .5,
                 "confidence": .99},
                {"type": "hand_intervention", "start": .2, "end": .4,
                 "confidence": .8}]}, "result")

    model = HallucinatingModel()
    events = DirectEventScanner(
        model, 2, .5, 1, enabled_event_types={"hand_intervention"}).scan(
            tmp_path, {})
    assert model.calls == 1
    assert [event.type for event in events] == ["hand_intervention"]


def test_scan_failure_retains_three_review_candidates(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class BrokenModel:
        model = "qwen3.8-max"
        def generate(self, prompt, images):
            raise ValueError("truncated JSON")

    progress = tmp_path / "progress.json"
    events = DirectEventScanner(BrokenModel(), window_seconds=2,
                                overlap_seconds=.5, sample_fps=1).scan(
                                    tmp_path, {}, progress)
    assert {x.type for x in events} == {"spill", "hand_intervention", "lid_fall"}
    assert all(x.source == ["vlm_scan_failure"] for x in events)
    assert json.loads(progress.read_text())["scan_failures"]


def test_null_model_times_are_clamped_to_window(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class NullTimeModel:
        model = "qwen3.8-max"
        def generate(self, prompt, images):
            return ({"state": {}, "candidates": [{"type": "spill",
                    "start": None, "end": None, "confidence": None}]}, "null")

    event = DirectEventScanner(NullTimeModel(), window_seconds=2,
                               overlap_seconds=.5, sample_fps=1).scan(tmp_path, {})[0]
    assert event.start == 0
    assert event.end == 0
    assert event.prior_confidence == .55


def test_candidates_nested_in_state_are_normalized(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class NestedModel:
        model = "qwen3.8-max"
        def generate(self, prompt, images):
            return ({"state": {"hand": "present", "candidates": [{
                "type": "hand_intervention", "start": .2, "end": .5,
                "objects": ["cup"]}]}}, "nested")

    event = DirectEventScanner(NestedModel(), window_seconds=2,
                               overlap_seconds=.5, sample_fps=1).scan(tmp_path, {})[0]
    assert event.type == "hand_intervention"
    assert event.objects == ["cup"]


def test_active_pour_without_coarse_spill_triggers_high_fps_review(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class ActivePourModel:
        model = "qwen3.8-max"
        def __init__(self):
            self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            if self.calls == 1:
                return ({"state": {"pouring": "active", "wet_regions": "dry"},
                         "candidates": []}, "coarse")
            assert "high-frame-rate liquid-transfer reviewer" in prompt
            assert len(images) == 2
            assert "temporal overview" in images[0][0]
            assert "spatial-detail" in images[1][0]
            return ({"candidates": [{"type": "spill", "start": .25,
                     "end": .5, "subtype": "missed_target", "confidence": .8,
                     "observation": {"transition": "escaped droplets"}}]}, "focused")

    model = ActivePourModel()
    events = DirectEventScanner(model, window_seconds=2, overlap_seconds=.5,
                                sample_fps=1, spill_review_fps=2).scan(tmp_path, {})
    assert model.calls == 2
    assert len(events) == 1
    assert events[0].type == "spill"
    assert events[0].subtype == "missed_target"
    assert "focused_spill_review" in events[0].source
    assert events[0].evidence["task_context"]["review_stage"] == "high_fps_spill"


def test_low_confidence_pour_check_does_not_suppress_focused_review(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class LowCheckModel:
        model = "qwen3.8-max"
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            if self.calls == 1:
                return ({"state": {"pouring": "active"}, "candidates": [{
                    "type": "spill", "start": 0, "end": .2,
                    "subtype": "pour_stream_check", "confidence": .15}]}, "coarse")
            return ({"candidates": [{"type": "spill", "start": .3, "end": .5,
                    "subtype": "rim_overflow", "confidence": .8}]}, "focused")

    model = LowCheckModel()
    events = DirectEventScanner(model, 2, .5, 1, 2).scan(tmp_path, {})
    assert model.calls == 2
    assert any(x.subtype == "rim_overflow" and "focused_spill_review" in x.source
               for x in events)


def test_visual_pour_evidence_overrides_inactive_summary(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class InconsistentModel:
        model = "qwen3.8-max"
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            if self.calls == 1:
                return ({"state": {"pouring": "inactive"}, "candidates": [{
                    "type": "hand_intervention", "start": 0, "end": .2,
                    "confidence": .3, "observation": {
                        "transition": "kettle tilts; a stream emerges from its spout toward the teapot opening"}}]}, "coarse")
            return ({"candidates": []}, "focused")

    model = InconsistentModel()
    DirectEventScanner(model, 2, .5, 1, 2).scan(tmp_path, {})
    assert model.calls == 2


def test_plain_kettle_reposition_does_not_trigger_spill_review(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class RepositionModel:
        model = "qwen3.8-max"
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            return ({"state": {"pouring": "inactive"}, "candidates": [{
                "type": "hand_intervention", "start": 0, "end": .2,
                "confidence": .8, "observation": {
                    "transition": "human repositions the kettle on the table"}}]}, "coarse")

    model = RepositionModel()
    DirectEventScanner(model, 2, .5, 1, 2).scan(tmp_path, {})
    assert model.calls == 1


def test_active_pour_negative_focus_still_creates_final_audit(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)

    class NegativeFocusModel:
        model = "qwen3.8-max"
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            if self.calls == 1:
                return ({"state": {"pouring": "active"}, "candidates": []}, "coarse")
            return ({"candidates": []}, "focused")

    events = DirectEventScanner(NegativeFocusModel(), 2, .5, 1, 2).scan(
        tmp_path, {})
    assert len(events) == 1
    assert events[0].type == "spill"
    assert events[0].subtype == "active_pour_audit"
    assert events[0].prior_confidence == .2


def test_chunked_evidence_clamps_to_video_duration(tmp_path):
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.four_event_vlm import make_chunked_contact_sheets
    path = tmp_path / "faceImg.mp4"
    _video(path, seconds=1)
    chunks = make_chunked_contact_sheets(path, 0, 4, 1, chunk_seconds=.6)
    assert chunks
    assert chunks[-1][1] <= 1.01


def test_scan_prompt_audits_residual_post_pour_drips():
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.four_event_vlm import _SCAN_PROMPT, _SPILL_REVIEW_PROMPT
    assert "post-pour motion" in _SCAN_PROMPT
    assert "residual_spout_drip_review" in _SCAN_PROMPT
    assert "main pour stops" in _SPILL_REVIEW_PROMPT


def test_explicit_negative_outputs_are_filtered():
    from edc.algorithms.value.algorithms.Collision_Detection_Qwen3vl.four_event_vlm import _explicit_non_event
    assert _explicit_non_event({"type": "spill", "subtype": "none_observed"})
    assert _explicit_non_event({"type": "hand_intervention",
        "observation": {"transition": "person remains in background; no contact"}})
    assert not _explicit_non_event({"type": "hand_intervention",
        "observation": {"transition": "hand at frame edge may touch kettle"}})


def test_resume_rewinds_and_repairs_failed_window(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)
    progress = tmp_path / "progress.json"

    class AlwaysFail:
        model = "qwen3.8-max"
        def generate(self, prompt, images):
            raise TimeoutError("temporary")

    first = DirectEventScanner(AlwaysFail(), 2, .5, 1).scan(
        tmp_path, {}, progress)
    assert len(first) == 3
    failed = json.loads(progress.read_text())
    assert failed["scan_failures"]
    assert failed["window_history"][0]["success"] is False

    class Recovered:
        model = "qwen3.8-max"
        def generate(self, prompt, images):
            return ({"state": {"pouring": "inactive"}, "candidates": []}, "ok")

    repaired = DirectEventScanner(Recovered(), 2, .5, 1).scan(
        tmp_path, {}, progress)
    assert repaired == []
    saved = json.loads(progress.read_text())
    assert saved["scan_failures"] == []
    assert saved["window_history"][0]["success"] is True


def test_completed_scan_resume_does_not_rescan_tail_overlap(tmp_path):
    _video(tmp_path / "faceImg.mp4", seconds=1)
    progress = tmp_path / "progress.json"

    class CountingModel:
        model = "qwen3.8-max"
        def __init__(self): self.calls = 0
        def generate(self, prompt, images):
            self.calls += 1
            return ({"state": {"pouring": "inactive"}, "candidates": [{
                "type": "hand_intervention", "start": .2, "end": .4,
                "subtype": "workspace_entry", "objects": ["cup"]}]}, "ok")

    model = CountingModel()
    scanner = DirectEventScanner(model, 2, .5, 1)
    first = scanner.scan(tmp_path, {}, progress)
    calls_after_first = model.calls
    second = scanner.scan(tmp_path, {}, progress)
    assert model.calls == calls_after_first
    assert len(first) == len(second) == 1
    assert second[0].candidate_id == first[0].candidate_id
