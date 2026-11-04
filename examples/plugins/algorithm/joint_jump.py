"""Developer example: a new quality algorithm on the common base class.

Flags joint angles that jump between two consecutive rows, per arm. Install this folder (pip install -e .) and
`edc batch` runs it next to the nine built-in algorithms; the report gets one more column.
"""
import numpy as np

from edc.algorithms.base import QualityAlgorithm, Item, PASS, REVIEW, EXCLUDE, NOT_APPLICABLE
from edc.algorithms.registry import AlgorithmSpec


class JointJump(QualityAlgorithm):
    spec = AlgorithmSpec(
        'team_example__joint_jump', '0.1.0', '关节跳变', 'Example team',
        'Joint angle change between two consecutive rows larger than a threshold.',
        presets=('quality_cpu',),                              # Run by `edc batch` with the built-in algorithms.
        parameters={'review_rad': '单帧变化超过它判复核', 'exclude_rad': '单帧变化超过它判排除'},
        parameter_schema={'type': 'object', 'additionalProperties': False, 'properties': {
            'review_rad': {'type': 'number', 'exclusiveMinimum': 0, 'default': 0.2},
            'exclude_rad': {'type': 'number', 'exclusiveMinimum': 0, 'default': 0.5}}},
        input_descriptions=('机器人 yaml 里各手臂的 joints（关节角，框架已换算成 rad）。',))

    def check(self, episode, params):
        review, exclude = params.get('review_rad', 0.2), params.get('exclude_rad', 0.5)
        arms = [c for c in episode.channels.values()
                if c.modality == 'joint_space' and c.source_kind == 'feedback' and 'position' in c.fields]
        if not arms:
            return [Item('本条', status=NOT_APPLICABLE, reason='机器人 yaml 里没写 joints')]
        items = []
        for channel in arms:
            angles = np.asarray(episode.read_signal(channel.name, 'position'), float)
            step = np.abs(np.diff(angles, axis=0)).max(axis=1) if len(angles) > 1 else np.zeros(0)
            worst = float(step.max()) if step.size else 0.0
            rows = np.flatnonzero(step > review) + 1
            verdict = EXCLUDE if worst > exclude else REVIEW if rows.size else PASS
            reason = (f'{rows.size} 处相邻两帧关节角变化超过 {review:g} rad，最大 {worst:.2f} rad' if rows.size
                      else f'相邻两帧最大变化 {worst:.3f} rad')
            items.append(Item(channel.name, verdict=verdict, reason=reason,
                              evidence=[{'row': int(r)} for r in rows[:20]], metrics={'max_step_rad': worst}))
        return items


plugin = JointJump()
