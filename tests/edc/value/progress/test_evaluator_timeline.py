"""Real timestamp timing with the frozen GRM frames, formula and thresholds."""

import json

import numpy as np
import pytest

pytest.importorskip("cv2")

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import evaluator
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.timeline import FrameTimeline


def detector(times=None, *, fps=10, interval=10):
    obj = evaluator.SubtaskTrajectoryEvaluator.__new__(evaluator.SubtaskTrajectoryEvaluator)
    obj.fps = fps
    obj.frame_interval = interval
    obj.filter_config = evaluator.FilterConfig()
    obj.timeline = FrameTimeline(times, fps) if times is not None else None
    return obj


def hops(values):
    return [{"sample": i, "hop": value, "progress": .4}
            for i, value in enumerate(values)]


@pytest.mark.parametrize("interval,fps", [(10, 10), (7, 30)])
def test_uniform_timeline_keeps_frozen_sample_threshold_and_severity(interval, fps):
    sample_hops = hops([.4] + [0] * 25 + [.2] + [0] * 5)
    indices = list(range(0, (len(sample_hops) + 1) * interval, interval))
    times = np.arange(indices[-1] + 1) / fps
    legacy = detector(fps=fps, interval=interval)
    timed = detector(times, fps=fps, interval=interval)
    assert timed.timeline.matches_playback
    assert timed.detect_stagnations(sample_hops, indices) == legacy.detect_stagnations(sample_hops)


def test_fewer_samples_can_cross_five_real_seconds():
    indices = [0, 10, 20, 30, 40]
    times = np.interp(np.arange(41), indices, [100, 100.4, 102, 103.8, 105.8])
    sample_hops = hops([.4, 0, 0, 0])
    assert detector().detect_stagnations(sample_hops) == []
    phase, = detector(times).detect_stagnations(sample_hops, indices)
    assert phase["duration_sec"] == 5.4
    assert phase["duration_start_frame"] == 10
    assert phase["duration_end_frame"] == 40
    assert phase["source_start_time_sec"] == pytest.approx(.4)
    assert phase["source_end_time_sec"] == pytest.approx(5.8)


def test_many_samples_below_five_real_seconds_do_not_trigger():
    sample_hops = hops([.4] + [0] * 6)
    indices = list(range(0, 80, 10))
    assert detector().detect_stagnations(sample_hops)
    timed = detector(np.arange(71) * .02)
    assert timed.detect_stagnations(sample_hops, indices) == []


@pytest.mark.parametrize("source_step,expected", [(.12, False), (.13, True)])
def test_partial_final_interval_uses_actual_nonzero_frame_boundaries(source_step, expected):
    # The last interval is one frame, not another full 20-frame period.
    indices = [10, 30, 50, 70, 71]
    sample_hops = hops([.4, 0, 0, 0])
    timed = detector(np.arange(72) * source_step, interval=20)
    phases = timed.detect_stagnations(sample_hops, indices)
    assert bool(phases) is expected
    assert detector(interval=20).detect_stagnations(sample_hops)
    if expected:
        assert phases[0]["duration_sec"] == 5.33
        assert phases[0]["duration_end_frame"] == 71
        assert phases[0]["proportion"] == round(41 / 61, 3)


def test_severity_ratio_uses_real_subtask_duration_not_sample_count():
    indices = list(range(0, 110, 10))
    anchors = [0, .4, 2, 3.8, 5.8, 10, 20, 30, 40, 50, 70]
    timed = detector(np.interp(np.arange(101), indices, anchors))
    sample_hops = hops([.4, 0, 0, 0] + [.1] * 6)
    phase, = timed.detect_stagnations(sample_hops, indices)
    assert phase["duration_sec"] == 5.4
    assert phase["proportion"] == round(5.4 / 70, 3)
    assert phase["severity"] == "轻微"


def test_nonuniform_timing_requires_complete_sample_boundaries():
    timed = detector(np.arange(101) * .12)
    with pytest.raises(ValueError, match="all sampled interval boundaries"):
        timed.detect_stagnations(hops([0] * 6), [0, 10, 20, 30, 40, 50])


def test_video_frames_and_progress_stay_unchanged_with_explicit_source_evidence(tmp_path, monkeypatch):
    class Video:
        def get(self, key):
            return 10 if key == evaluator.cv2.CAP_PROP_FPS else 41

        def release(self):
            pass

    class Model:
        samples = []

        def inference_batch(self, samples):
            self.samples = samples
            return [{"pred": f"<score>{20 * (i + 1)}</score>"} for i in range(len(samples))]

    monkeypatch.setattr(evaluator.cv2, "VideoCapture", lambda _: Video())
    monkeypatch.setattr(evaluator, "plot_video_reward", lambda _: None)
    times = 100 + np.arange(41) * .13
    obj = evaluator.SubtaskTrajectoryEvaluator(
        "unused", [{"step": 0, "query": "pick", "clip_start_sec": 1, "clip_end_sec": 4}],
        str(tmp_path / "input"), str(tmp_path / "native"), frame_interval=10, mode="forward",
        timeline=FrameTimeline(times, 10),
    )
    obj.model = Model()
    monkeypatch.setattr(obj, "extract_frames", lambda *args: None)
    run_root = obj.output_root / "subtask_0_test"
    run_root.mkdir()
    result = obj.evaluate_subtask_with_regressions(0, run_root)
    assert result["sample_frame_indices"] == [10, 20, 30, 40]
    assert [d["progress"] for d in result["progress_details"]] == [.2, .4, .6]
    assert result["start_time"] == 1
    assert result["end_time"] == 4
    assert result["source_start_time_sec"] == pytest.approx(1.3)
    assert result["source_end_time_sec"] == pytest.approx(5.2)
    preds = json.loads((run_root / "pred_vllm.json").read_text())
    assert [p["frame_index"] for p in preds] == [10, 20, 30]
    assert [p["input_after_frame_index"] for p in preds] == [20, 30, 40]
    assert [p["hop_start_frame_index"] for p in preds] == [10, 20, 30]
    assert [p["hop_end_frame_index"] for p in preds] == [20, 30, 40]
    assert preds[-1]["playback_time_sec"] == 3
    assert preds[-1]["source_time_sec"] == pytest.approx(3.9)
    assert preds[-1]["source_end_time_sec"] == pytest.approx(5.2)
    # Inference still sees exactly the same sampled images and reference images.
    assert obj.model.samples[0]["image"][-3].endswith("frame_000020.png")
    assert obj.model.samples[-1]["image"][-3].endswith("frame_000040.png")
    assert obj.total_duration == 4.1
    assert obj.total_frames == 41
    assert obj.source_duration == pytest.approx(5.2)
    obj._build_cumulative_progress([result])
    obj.save_overall_predictions()
    combined = json.loads((obj.output_root / "pred_vllm.json").read_text())
    assert combined[-1]["frame_index"] == 30
    assert combined[-1]["input_after_frame_index"] == 40
    assert combined[-1]["source_time_sec"] == pytest.approx(3.9)
    # Recompute must retain the real final boundary rather than guess a full tail.
    monkeypatch.setattr(obj, "_finalize_and_save", lambda *args, **kwargs: {})
    recomputed, _ = obj.recompute_from_existing()
    assert recomputed[0]["sample_frame_indices"] == [10, 20, 30, 40]
    assert [d["progress"] for d in recomputed[0]["progress_details"]] == [.2, .4, .6]
    monkeypatch.setattr(obj, "save_text_report", lambda *args: None)
    monkeypatch.setattr(obj, "plot_progress_chart", lambda *args: None)
    obj.save_results([], [], 0, {})
    summary = json.loads((obj.output_root / "evaluation_summary.json").read_text())
    assert summary["video_duration_sec"] == 4.1
    assert summary["source_duration_sec"] == pytest.approx(5.2)


@pytest.mark.parametrize("source_step,expected_end", [(.13, 71), (.1, 70)])
def test_attribution_stagnation_boundaries_include_final_hop_on_source_path_only(
        tmp_path, monkeypatch, source_step, expected_end):
    class Video:
        def get(self, key):
            return 10 if key == evaluator.cv2.CAP_PROP_FPS else 72

        def release(self):
            pass

    class Model:
        def inference_batch(self, samples):
            return [{"pred": "<score>40</score>"} for _ in samples]

    monkeypatch.setattr(evaluator.cv2, "VideoCapture", lambda _: Video())
    obj = evaluator.SubtaskTrajectoryEvaluator(
        "unused", [{"step": 0, "query": "pick", "clip_start_sec": 1, "clip_end_sec": 7.1}],
        str(tmp_path / "input"), str(tmp_path / "native"), frame_interval=20, mode="forward",
        timeline=FrameTimeline(200 + np.arange(72) * source_step, 10),
    )
    obj.model = Model()
    monkeypatch.setattr(obj, "extract_frames", lambda *args: None)
    run_root = obj.output_root / "subtask_0_test"
    run_root.mkdir()
    result = obj.evaluate_subtask_with_regressions(0, run_root)
    assert result["sample_frame_indices"] == [10, 30, 50, 70, 71]
    published = obj._build_cumulative_progress([result])[0]["stagnations"][0]
    assert published["start_frame"] == 30
    assert published["end_frame"] == expected_end
    if source_step == .13:
        assert published["end_frame"] == published["duration_end_frame"]
        assert published["end_time"] == 7.1
        assert published["source_end_time_sec"] == pytest.approx(71 * .13)
        assert published["source_end_time_sec"] - published["source_start_time_sec"] == pytest.approx(5.33)
    else:
        assert "end_time" not in published
        assert "duration_end_frame" not in published
    # Both live inference and no-model recomputation publish through the same
    # enrichment boundary consumed by the physical stagnation attributor.
    written = []
    monkeypatch.setattr(obj, "_finalize_and_save", lambda results, cumulative, *args, **kwargs:
                        written.append(cumulative) or {})
    obj.recompute_from_existing()
    assert written[0][0]["stagnations"][0] == published


def test_uniform_prepared_images_retain_frozen_decisions_without_playback_rate():
    from types import SimpleNamespace as NS
    from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import WindowAnalyzer
    sample_hops=hops([.4]+[0]*25+[.2]+[0]*5)
    indices=list(range(0,(len(sample_hops)+1)*7,7));times=np.arange(indices[-1]+1)/30
    legacy=detector(fps=30,interval=7)
    outputs=[];bounds=[]
    for playback in (None,times.tolist(),(times*2).tolist()):
        m={'frame_count':len(times),'reference':'cam_high','views':{'cam_high':{'source_timestamps':times.tolist(),'playback_timestamps':playback}}}
        timeline=FrameTimeline.from_manifest(m)
        obj=detector(fps=timeline.fps,interval=7);obj.timeline=timeline;obj.frame_source=NS()
        outputs.append(obj.detect_stagnations(sample_hops,indices))
        analyzer=WindowAnalyzer.__new__(WindowAnalyzer);analyzer.timeline=timeline;analyzer.fps=timeline.fps
        bounds.append(analyzer._bound(100,10,inclusive=True))
    assert outputs[0]==outputs[1]==outputs[2]==legacy.detect_stagnations(sample_hops)
    assert bounds==[115]*3
