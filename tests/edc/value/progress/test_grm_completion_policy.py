"""GRM 完成度标记与子任务成败裁决解耦的契约测试。

低完成度（<80% 进度）只作为信息性字段记录，不把子任务判为 failed、
不把 overall_status 置为 fail、不进 problematic_subtasks。
"""
from pathlib import Path
import unittest

from edc.algorithms.value.algorithms.Progress_Stagnation_Attribution.evaluator import (
    FilterConfig,
    SubtaskTrajectoryEvaluator,
)


def _evaluator():
    ev = SubtaskTrajectoryEvaluator.__new__(SubtaskTrajectoryEvaluator)
    ev.episode_path = Path("fake_episode")
    ev.total_subtasks = 3
    ev.filter_config = FilterConfig()
    ev.stats = {
        'total_subtasks': 3,
        'evaluated_subtasks': 3,
        'subtasks_with_regressions': 0,
        'subtasks_with_stagnations': 0,
        'subtasks_below_threshold': 0,
        'low_completion_subtasks': [],
        'pattern_distribution': {'v_shape': 0, 'valley': 0, 'no_regression': 0},
        'severity_distribution': {'严重': 0, '中等': 0, '轻微': 0},
        'stagnation_distribution': {'严重': 0, '中等': 0, '轻微': 0},
    }
    return ev


def _result(idx, progress, *, pattern='no_regression', severity='轻微',
            regressions=None, stagnations=None):
    return {
        'subtask_idx': idx,
        'description': f'subtask {idx}',
        'progress': progress,
        'regressions': regressions or [],
        'pattern': pattern,
        'severity': severity,
        'stagnations': stagnations or [],
        'stagnation_severity': 'none',
        'is_below_threshold': progress < FilterConfig.SUBTASK_COMPLETION_THRESHOLD,
        'risk_score': 0,
        'risk_reasons': [],
    }


class CompletionPolicyTests(unittest.TestCase):
    def test_low_completion_does_not_fail_the_episode(self):
        ev = _evaluator()
        results = [
            _result(0, 0.55),   # 低完成度
            _result(1, 0.98),
            _result(2, 0.99),
        ]
        conclusion = ev.generate_filter_conclusion(results, [{}, {}, {}])
        self.assertEqual(conclusion['overall_status'], 'pass')
        self.assertEqual(conclusion['problematic_subtasks'], [])
        statuses = [d['status'] for d in conclusion['classification_details']]
        self.assertEqual(statuses, ['ok', 'ok', 'ok'])
        # 信息性字段保留: 仍可观察到低完成度
        self.assertEqual(conclusion['summary']['subtasks_below_threshold'], 1)
        self.assertTrue(conclusion['classification_details'][0]['is_below_threshold'])

    def test_regression_and_stagnation_still_classified(self):
        ev = _evaluator()
        results = [
            _result(0, 0.99, pattern='valley', severity='中等',
                    regressions=[{'frame': 10}]),
            _result(1, 0.99, stagnations=[{'start_frame': 5, 'end_frame': 40}]),
        ]
        ev.stats['subtasks_with_regressions'] = 1
        ev.stats['subtasks_with_stagnations'] = 1
        conclusion = ev.generate_filter_conclusion(results, [{}, {}])
        details = conclusion['classification_details']
        self.assertEqual(details[0]['status'], 'problematic')
        self.assertEqual(details[1]['status'], 'potential_problem')
        self.assertEqual(conclusion['overall_status'], 'warning')
        self.assertIn(0, conclusion['problematic_subtasks'])
        self.assertNotIn(1, conclusion['problematic_subtasks'])


if __name__ == "__main__":
    unittest.main()
