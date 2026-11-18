"""Load-evidence gate tests for the v4 decision export (Open-branch drops).

object_drop_by_release is only excluded when the gripper carried a load before
the Open (pre-Open current elevated or a strong window spike). Without load
evidence the event becomes an unconfirmed_drop_by_release REVIEW.
"""

import json
from pathlib import Path

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.decision_export_v4 import convert_progress_episode


def _episode(tmp_path: Path, attr: dict, evaluation: dict, raw_current: float | None,
             cur_max: float = 0.0) -> tuple[Path, Path]:
    episode = tmp_path / "episode"; source = episode / "subtask_eval"; result = tmp_path / "v3"
    source.mkdir(parents=True); result.mkdir()
    (source / "evaluation_summary.json").write_text(json.dumps(evaluation))
    (source / "pred_vllm.json").write_text("[]")
    (result / "attribution_result.json").write_text(json.dumps(attr))
    if raw_current is not None:
        frames = [{"follow_right_joint_cur": [raw_current] * 7} for _ in range(300)]
        (episode / "episode.json").write_text(json.dumps({"data": frames}))
    return episode, result


def _attr(cur_max: float) -> dict:
    # baseline 2.0, std 0.2 (threshold = baseline + 1.5*std)
    return {
        "rule_version": "progress_attribution_v3",
        "regression_results": {
            "regr_100_right": {
                "frame": 100, "arm": "right", "primary_label": "object_drop_by_release",
                "severity": 3,
                "evidence": {
                    "events_in_win": [[90, "open"]],
                    "cur_baseline": 2.0, "cur_spike_threshold": 2.3, "cur_max": cur_max,
                    "pre_drop_prog": 0.5, "cur_dropped_to_baseline": False, "cur_spike": False,
                },
            }
        },
        "stagnation_results": {}, "proactive_findings": [], "training_events_v3": [],
    }


def _attr_drop(pre_drop: float, max_post: float) -> dict:
    attr = _attr(cur_max=2.2)
    event = attr["regression_results"]["regr_100_right"]
    event["evidence"]["pre_drop_prog"] = pre_drop
    event["evidence"]["max_post_prog"] = max_post
    return attr


def _eval() -> dict:
    return {"video_fps": 20, "video_duration_sec": 15, "subtask_results": [
        {"start_frame": 0, "end_frame": 299, "regressions": []}]}


def _first_decision(decisions: dict) -> dict:
    return decisions["decisions"][0]


def test_drop_with_pre_open_current_keeps_exclude(tmp_path):
    episode, result = _episode(tmp_path, _attr(cur_max=2.2), _eval(), raw_current=3.0)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "exclude"
    assert decision["label"] == "object_drop_by_release"
    assert decision["load_evidence"]["pre_open_load"] is True


def test_drop_with_window_spike_keeps_exclude(tmp_path):
    # No raw state file at all, but cur_max 2.7 > baseline+3*std = 2.6.
    episode, result = _episode(tmp_path, _attr(cur_max=2.7), _eval(), raw_current=None)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "exclude"
    assert decision["load_evidence"]["spike_load"] is True


def test_drop_without_load_evidence_downgrades_to_review(tmp_path):
    episode, result = _episode(tmp_path, _attr(cur_max=2.2), _eval(), raw_current=2.0)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "review"
    assert decision["label"] == "unconfirmed_drop_by_release"
    assert decision["severity"] == 2
    assert decision["source_label"] == "object_drop_by_release"
    assert decision["load_evidence"]["pre_open_load"] is False


def test_drop_with_unreadable_raw_data_downgrades_to_review(tmp_path):
    episode, result = _episode(tmp_path, _attr(cur_max=2.2), _eval(), raw_current=None)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "review"
    assert decision["label"] == "unconfirmed_drop_by_release"
    assert decision["load_evidence"]["pre_open_cur"] is None


def test_early_deep_progress_dip_confirms_drop_without_current(tmp_path):
    # v7 path 2: microwave-style hardware — no current evidence, but the drop
    # starts low (0.25) and even the best post sample (0.12) stays 0.13 below.
    episode, result = _episode(tmp_path, _attr_drop(0.25, 0.12), _eval(), raw_current=None)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "exclude"
    assert decision["label"] == "object_drop_by_release"
    assert decision["load_evidence"]["early_deep_drop"] is True


def test_high_progress_release_stays_review_even_with_deep_dip(tmp_path):
    # v7 path 2 guard: pre_drop 0.81 (late-stage release) with a 0.26 dip must
    # NOT confirm — MWR-0046 human judgement: normal release.
    episode, result = _episode(tmp_path, _attr_drop(0.81, 0.55), _eval(), raw_current=2.0)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "review"
    assert decision["load_evidence"]["early_deep_drop"] is False


def test_shallow_dip_stays_review(tmp_path):
    # v7 path 2 guard: pre_drop 0.18 but dip only 0.002 (DEL-0038 pattern:
    # barely a regression at all) must stay REVIEW.
    episode, result = _episode(tmp_path, _attr_drop(0.18, 0.178), _eval(), raw_current=2.0)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "review"


def test_exactly_ten_seconds_stagnation_is_severe(tmp_path):
    # v7 boundary fix (MWR-0029): 200 frames at 20 fps = exactly 10.0 s.
    attr = {
        "rule_version": "progress_attribution_v3", "regression_results": {},
        "stagnation_results": {
            "stag_200": {"start_frame": 0, "end_frame": 199, "duration_sec": 10.0,
                          "primary_label": "x"}},
        "proactive_findings": [], "training_events_v3": [],
    }
    attr["stagnation_results"]["stag_200"]["end_frame"] = 200
    evaluation = {"video_fps": 20, "video_duration_sec": 15, "subtask_results": [
        {"start_frame": 0, "end_frame": 299, "regressions": []}]}
    episode, result = _episode(tmp_path, attr, evaluation, raw_current=None)
    decisions, _, errors = convert_progress_episode(episode, result)
    assert not errors
    decision = _first_decision(decisions)
    assert decision["decision"] == "exclude"
    assert decision["label"] == "severe_stagnation"
