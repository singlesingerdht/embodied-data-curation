"""导出器版本透传契约: v9 输入 → method.version 输出 v9。

白名单是准入闸 (拒绝不认识的版本), 不是重新盖章 —— 输入的 rule_version
必须原样出现在导出的 method.version 里, 保证溯源不断链。
覆盖两个导出器:
- Progress_Stagnation_Attribution.decision_export_v4 (policy training_filter_v4)
- Value_Assessment._shared.decision_export (policy training_filter_v3, 正式入口)
"""
import json
import tempfile
from pathlib import Path

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.decision_export_v4 import (
    convert_progress_episode as convert_v4,
)
def _episode(tmp: Path, rule_version: str) -> Path:
    attr = {
        "rule_version": rule_version,
        "regression_results": {},
        "stagnation_results": {},
        "proactive_findings": [{
            "frame": 100, "type": "failed_grasp_close", "severity": 2,
            "evidence": {"close_frame": 100, "dip_frame": 124},
        }],
    }
    evaluation = {"video_fps": 30, "video_duration_sec": 31,
                  "subtask_results": [{"start_frame": 0, "end_frame": 900,
                                       "regressions": []}]}
    episode = tmp / "episode"
    out = episode / "subtask_eval"
    out.mkdir(parents=True)
    (out / "attribution_result.json").write_text(json.dumps(attr))
    (out / "evaluation_summary.json").write_text(json.dumps(evaluation))
    return episode


def test_v4_exporter_passthrough_v9():
    with tempfile.TemporaryDirectory() as tmp:
        frame, _, errors = convert_v4(_episode(Path(tmp), "progress_attribution_v9"))
    assert not errors, errors
    assert frame["method"]["version"] == "progress_attribution_v9"
    assert frame["method"]["policy_version"] == "training_filter_v4"
    assert frame["decisions"]


def test_v4_exporter_keeps_v3():
    with tempfile.TemporaryDirectory() as tmp:
        frame, _, errors = convert_v4(_episode(Path(tmp), "progress_attribution_v3"))
    assert not errors, errors
    assert frame["method"]["version"] == "progress_attribution_v3"


def test_v4_exporter_rejects_v2_frozen_input():
    # v2 冻结产物由 _shared 导出器 (policy v3) 负责; v4 拒收。
    with tempfile.TemporaryDirectory() as tmp:
        frame, obs, errors = convert_v4(_episode(Path(tmp), "progress_attribution_v2"))
    assert frame == {} and obs == {}
    assert errors and errors[0]["error_type"] == "unsupported_schema"


def test_v4_exporter_rejects_unknown_version():
    with tempfile.TemporaryDirectory() as tmp:
        frame, obs, errors = convert_v4(_episode(Path(tmp), "progress_attribution_v99"))
    assert frame == {} and obs == {}
    assert errors and errors[0]["error_type"] == "unsupported_schema"
