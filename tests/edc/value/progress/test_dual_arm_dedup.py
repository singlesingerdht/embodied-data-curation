"""v9 双臂去重测试: 同一停滞窗口双臂结论一致时合并为单条发布。

R2V2 pilot 发现 B —— 同一停滞区间按双臂重复上报（identical 区间 ×2）。
v9 行为：双臂 (primary_label, severity) 一致 → 单条记录（arms 字段注明双臂）；
结论不同 → 各自保留。
"""
from pathlib import Path
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor as attributor_mod
from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.attributor import (
    AttributeResult,
    EpisodeAttributor,
)


def _result(arm, label, severity, evidence=None):
    return AttributeResult(frame=0, arm=arm, primary_label=label,
                           severity=severity, raw_evidence=evidence or {})


class FakeAnalyzer:
    """双臂各返回配置好的结果。"""

    by_arm = {}

    def __init__(self, *args, **kwargs):
        pass

    def attribute_stagnation(self, start, end, arm, **kwargs):
        return self.by_arm[arm]

    def attribute(self, frame, arm, **kwargs):
        # 回归归因在测试里不触发 (assessment 无 regressions)
        raise AssertionError("unexpected call")


def _attribution():
    ev = EpisodeAttributor.__new__(EpisodeAttributor)
    ev.episode_path = Path("fake")
    ev.episode_json = Path("fake_ep.json")
    ev.fps = 20.0
    ev.gfeat = SimpleNamespace(n=100)
    ev.grip_events = {}
    ev.proactive_grasp_scan = lambda *a, **kw: []
    return ev


def _run(tmp, arms_map):
    FakeAnalyzer.by_arm = arms_map
    sub_eval = Path(tmp) / "subtask_eval"
    sub_eval.mkdir()
    assessment = {
        "subtask_results": [{
            "regressions": [],
            "stagnations": [{"start_frame": 10, "end_frame": 60}],
        }],
    }
    path = sub_eval / "evaluation_summary.json"
    path.write_text(json.dumps(assessment))
    ev = _attribution()
    with mock.patch.object(attributor_mod, "WindowAnalyzer", FakeAnalyzer):
        return ev.attribute_from_assessment(str(path))


class DualArmDedupTests(unittest.TestCase):
    def test_identical_dual_arm_findings_merge_into_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, {
                "right": _result("right", "idle_static_freeze", 1),
                "left": _result("left", "idle_static_freeze", 1),
            })
            items = list(result["stagnation_results"].values())
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["arm"], "right")
            self.assertEqual(items[0]["arms"], ["right", "left"])
            self.assertEqual(items[0]["primary_label"], "idle_static_freeze")

    def test_divergent_dual_arm_findings_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, {
                "right": _result("right", "grasp_collision", 3),
                "left": _result("left", "idle_motion_misjudge", 0),
            })
            items = list(result["stagnation_results"].values())
            self.assertEqual(len(items), 2)
            self.assertEqual({i["arm"] for i in items}, {"right", "left"})
            self.assertTrue(all("arms" not in i for i in items))
            labels = {i["primary_label"] for i in items}
            self.assertEqual(labels, {"grasp_collision", "idle_motion_misjudge"})

    def test_static_arm_still_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, {
                "right": _result("right", "ignore_static_arm", 0),
                "left": _result("left", "idle_static_freeze", 1),
            })
            items = list(result["stagnation_results"].values())
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["arm"], "left")
            self.assertNotIn("arms", items[0])

    def test_rule_version_is_v9(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, {
                "right": _result("right", "idle_static_freeze", 1),
                "left": _result("left", "idle_static_freeze", 1),
            })
            self.assertEqual(result["rule_version"], "progress_attribution_v9")


if __name__ == "__main__":
    unittest.main()
