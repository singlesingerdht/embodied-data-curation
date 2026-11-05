"""示例插件 joint_jump：照着写的新算法能跑，检查项和格子文字能进汇总。"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from edc.algorithms.quality.adapters.results import to_result

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/plugins/algorithm'


def test_example_plugin_items_and_note_reach_the_report(monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLE))
    from joint_jump.algorithm import plugin
    from joint_jump.method import jumps
    assert jumps([[0, 0], [0.1, 0], [0.4, 0]], 0.2, 0.5) == {'max_step_rad': 0.30000000000000004, 'rows': [2],
                                                             'verdict': 'REVIEW'}
    arm = lambda name: SimpleNamespace(name=name, modality='joint_space', source_kind='feedback', fields=['position'])
    signals = {'left': np.zeros((5, 7)), 'right': np.array([[0.] * 7] * 3 + [[0.8] + [0.] * 6] * 2)}
    episode = SimpleNamespace(channels={n: arm(n) for n in signals}, profile={'labels': {'left': '左臂关节'}},
                              read_signal=lambda name, field: signals[name])
    raw = plugin.run(SimpleNamespace(episode=episode), {})
    result = to_result(plugin.spec.id, [{'status': 'completed', 'result': {'reports': raw}}])
    assert [(i.target, i.verdict) for i in result.items] == [('左臂关节', 'PASS'), ('right', 'EXCLUDE')]
    assert result.note == '2 条手臂，最大单帧变化 0.80 rad' and result.level == 'EXCLUDE'
