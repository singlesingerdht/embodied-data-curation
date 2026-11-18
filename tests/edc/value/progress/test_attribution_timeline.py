"""Attribution windows follow source seconds without changing frame identities."""

import io
import json
from pathlib import Path

import numpy as np
import pytest

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import (
    EpisodeAttributor,
    GlobalFeatures,
    WindowAnalyzer,
    generate_text_report,
)
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.timeline import FrameTimeline


def _features(n=200, timeline=None):
    data = {"data": [
        {key: value for arm in ("left", "right") for key, value in (
            (f"follow_{arm}_gripper", 0.0),
            (f"follow_{arm}_joint_cur", [1.0] * 7),
            (f"follow_{arm}_position", [i * 0.01, 0.0, 0.0]),
        )}
        for i in range(n)
    ]}
    return GlobalFeatures(data, fps=20.0, timeline=timeline)


def _analyzer(timeline=None, events=None):
    return WindowAnalyzer(
        _features(),
        {"left": [], "right": events or []},
        [{"frame_index": i, "progress": 0.5 if i < 100 else 0.2}
         for i in range(0, 200, 10)],
        fps=20.0,
        timeline=timeline,
    )


def test_uniform_source_preserves_existing_regression_attribution():
    events = [(100, "open")]
    legacy = _analyzer(events=events).attribute(100, "right")
    timeline = FrameTimeline(np.arange(200) / 20.0, 20.0)
    actual = _analyzer(timeline, events).attribute(100, "right")
    assert actual == legacy


def test_old_open_outside_real_window_is_not_attributed_to_new_regression():
    # Frame 60 is within the old 50-frame lookback, but is four source
    # seconds before the regression: outside its actual 2.5-second window.
    timeline = FrameTimeline(np.arange(200) * 0.1, 20.0)
    context = {"pre_drop_prog": 0.5, "post_progs": [0.2] * 7}
    legacy = _analyzer(events=[(60, "open")]).attribute(100, "right", context)
    actual = _analyzer(timeline, [(60, "open")]).attribute(100, "right", context)
    assert [60, "open"] == list(legacy.raw_evidence["events_in_win"][0])
    assert actual.raw_evidence["events_in_win"] == []
    assert legacy.primary_label == "unconfirmed_drop_by_release"
    assert actual.primary_label != "unconfirmed_drop_by_release"


def test_event_just_after_real_window_is_excluded():
    # The next frame after 13.50 s is frame 123 at 13.53 s. A search for
    # the 2.5-second upper bound must not accidentally include that frame.
    timeline = FrameTimeline(np.arange(200) * 0.11, 20.0)
    actual = _analyzer(timeline, [(123, "open")]).attribute(
        100, "right", {"pre_drop_prog": 0.5, "post_progs": [0.2] * 7}
    )
    assert actual.raw_evidence["events_in_win"] == []


def test_nearest_progress_uses_source_time_instead_of_frame_distance():
    timeline = FrameTimeline([0.0, 0.49, 0.50, 0.51, 0.52], 20.0)
    analyzer = WindowAnalyzer(
        _features(5), {"left": [], "right": []},
        [{"frame_index": 0, "progress": 0.1}, {"frame_index": 4, "progress": 0.9}],
        fps=20.0, timeline=timeline,
    )
    assert analyzer._get_prog_at_frame(1) == 0.9


def test_physical_derivatives_use_actual_source_intervals():
    timeline = FrameTimeline([0.0, 0.1, 0.3], 20.0)
    data = {"data": [
        {key: value for arm in ("left", "right") for key, value in (
            (f"follow_{arm}_gripper", t),
            (f"follow_{arm}_joint_cur", [1.0]),
            (f"follow_{arm}_position", [t, 0.0, 0.0]),
        )}
        for t in timeline.times
    ]}
    features = GlobalFeatures(data, 20.0, timeline=timeline)
    assert features.grip_vel["right"][1:] == pytest.approx([1.0, 1.0])
    assert features.speed["right"][1:] == pytest.approx([1.0, 1.0])


def test_stagnation_output_and_text_report_use_source_seconds(tmp_path):
    timeline = FrameTimeline(np.arange(200) * 0.1, 20.0)
    attributor = EpisodeAttributor.__new__(EpisodeAttributor)
    attributor.timeline = timeline
    attributor.fps = 20.0
    attributor.gfeat = _features()
    attributor.grip_events = {"left": [], "right": []}
    attributor.episode_json = Path("example.json")
    assessment = tmp_path / "evaluation_summary.json"
    assessment.write_text(json.dumps({"subtask_results": [{
        "start_frame": 0, "end_frame": 199, "regressions": [],
        "stagnations": [{"start_frame": 20, "end_frame": 60}],
    }]}))
    result = attributor.attribute_from_assessment(str(assessment))
    entries = list(result["stagnation_results"].values())
    assert len(entries) == 1  # Identical arm findings retain existing deduplication.
    assert entries[0]["duration_sec"] == 4.0
    assert (entries[0]["start_frame"], entries[0]["end_frame"]) == (20, 60)
    report = io.StringIO()
    generate_text_report(result, report, 20.0, timeline=timeline)
    assert "00:02.00-00:06.00, 4.0s" in report.getvalue()


def test_fallback_progress_context_uses_half_second_source_offsets():
    timeline = FrameTimeline(np.arange(200) * 0.1, 20.0)
    predictions = [
        {"frame_index": 90, "progress": 0.6},
        {"frame_index": 95, "progress": 0.3},
        {"frame_index": 100, "progress": 0.2},
        *[{"frame_index": i, "progress": 0.4} for i in range(105, 140, 5)],
    ]
    events = {"left": [], "right": [(100, "open")]}
    legacy = WindowAnalyzer(_features(), events, predictions, 20.0).attribute(100, "right")
    actual = WindowAnalyzer(_features(), events, predictions, 20.0, timeline=timeline).attribute(100, "right")
    assert not legacy.raw_evidence["prog_recovery"]
    assert actual.raw_evidence["prog_recovery"]
    assert legacy.primary_label == "unconfirmed_drop_by_release"
    assert actual.severity == 0


def test_secondary_grasp_search_includes_actual_progress_samples():
    timeline = FrameTimeline(np.arange(200) * 0.1, 20.0)
    events = {"left": [], "right": [(20, "close")]}
    predictions = [{"frame_index": i, "progress": 0.8 if i == 21 else 0.2}
                   for i in range(20, 71)]
    analyzer = WindowAnalyzer(_features(), events, predictions, 20.0, timeline=timeline)
    assert analyzer._detect_secondary_grasp("right", 10, 0.6, 0.2) == "secondary_grasp_success"
