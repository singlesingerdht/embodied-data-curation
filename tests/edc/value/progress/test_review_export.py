"""Prepared-frame reviews retain scored observations and unknown-time limits."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip('cv2')
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution import evaluator
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.timeline import FrameTimeline
from edc.data.ir.dataset import open_dataset
from edc.execution.frames import inspect_frames

ROLES = ('cam_high', 'cam_left_wrist', 'cam_right_wrist')


@pytest.mark.parametrize('declared', [True, False])
def test_declared_export_rows_pair_without_inventing_recorded_time(tmp_path, declared):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'source.json').write_text(json.dumps({'data': [{'joint_pos': [i]} for i in range(4)]}))
    for role in ROLES:
        writer = cv2.VideoWriter(str(source / f'{role}.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 20, (32, 32))
        assert writer.isOpened()
        for i in range(4):
            writer.write(np.full((32, 32, 3), i * 20, dtype=np.uint8))
        writer.release()
    profile = {'schema_version': 1, 'id': 'export', 'version': '1', 'channels': {}}
    if declared:
        profile['video_frame_time'] = 'rows'
    with open_dataset(source, profile=profile) as dataset:
        ep = next(dataset.iter_episodes())
        manifest = inspect_frames(ep, dict(zip(ROLES, ROLES)))
    assert manifest['coverage_complete'] is declared
    assert not manifest['proves_acquisition_sync']
    for role in ROLES:
        row = manifest['views'][role]
        assert row['source_timestamps'] is None
        assert row['playback_timestamps'] == pytest.approx([0, .05, .1, .15])
        if declared:
            assert row['reference_indices'] == [0, 1, 2, 3]


def test_prepared_review_exports_subtasks_and_overall_at_after_frames(tmp_path, monkeypatch):
    inputs = tmp_path / 'input'
    for cam in ROLES:
        (inputs / cam).mkdir(parents=True)
        for i in (0, 10, 17):
            cv2.imwrite(str(inputs / cam / f'frame_{i:06d}.png'), np.full((32, 32, 3), i, dtype=np.uint8))
    frames = SimpleNamespace(image_path=lambda cam, i: str(inputs / cam / f'frame_{i:06d}.png'))
    obj = evaluator.SubtaskTrajectoryEvaluator('unused', [
        {'step': 0, 'query': 'pick', '_start_frame': 0, '_end_frame': 17}],
        str(inputs), str(tmp_path / 'native'), frame_source=frames,
        timeline=FrameTimeline(None, playback_timestamps=np.arange(18) / 20, count=18),
        mode='forward', export_video=True)
    obj.model = SimpleNamespace(inference_batch=lambda samples: [{'pred': '<score>50%</score>'} for _ in samples])
    rendered = []
    def render(folder):
        predictions = json.loads((folder / 'pred_vllm.json').read_text())
        for item in predictions:
            assert all(Path(p).is_file() for p in item['image'])
            assert int(Path(item['image'][5]).stem.split('_')[-1]) == item['input_after_frame_index']
        rendered.append(folder)
        (folder / 'reward_vis.mp4').write_bytes(b'fake-render')
    monkeypatch.setattr(evaluator, 'plot_video_reward', render)
    run = obj.output_root / 'subtask_0_test'
    run.mkdir()
    result = obj.evaluate_subtask_with_regressions(0, run)
    cumulative = obj._build_cumulative_progress([result])
    obj.generate_overall_video()
    obj.save_results([result], cumulative, 1, obj.generate_filter_conclusion([result], cumulative))
    assert rendered == [run, obj.output_root]
    pred = json.loads((obj.output_root / 'pred_vllm.json').read_text())
    assert [x['input_after_frame_index'] for x in pred] == [10, 17]
    assert [x['frame_index'] for x in pred] == [0, 10]  # Frozen legacy field stays compatible.
    assert [x['progress'] for x in pred] == [.5, .5]
    report = (obj.output_root / 'assessment_report.txt').read_text()
    assert '子任务 0: pick' in report
    assert '停滞未评估' in report
    assert '4 FPS' in report
    assert json.loads((obj.output_root / 'visualization.json').read_text())['status'] == 'completed'


def test_failed_renderer_is_not_reported_as_saved(tmp_path, monkeypatch, capsys):
    obj = evaluator.SubtaskTrajectoryEvaluator.__new__(evaluator.SubtaskTrajectoryEvaluator)
    monkeypatch.setattr(evaluator, 'plot_video_reward', lambda _: None)
    with pytest.raises(RuntimeError, match="Video export failed"):
        obj._render_video(tmp_path)
    assert json.loads((tmp_path / 'visualization.json').read_text())['status'] == 'failed'
    assert '视频已保存' not in capsys.readouterr().out
