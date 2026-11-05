"""关节跳变（示例）：框架怎么调用这项检查——找手臂、读关节角、把结果写成检查项；计算在 method.py。

装上后随 edc batch 运行，报告多一列“关节跳变”。阈值只作演示，没有实测依据。
"""
from edc.algorithms.base import QualityAlgorithm, AlgorithmResult, Item, NOT_APPLICABLE
from edc.algorithms.registry import AlgorithmSpec

from .method import jumps


class Algorithm(QualityAlgorithm):
    spec = AlgorithmSpec(
        'team_example__joint_jump', '0.2.0', '关节跳变', 'Example team',
        '相邻两帧关节角变化超过阈值。',
        presets=('quality_cpu',),                              # 随 edc batch 和九个内置算法一起跑
        example=True,
        parameters={'review_rad': '单帧变化超过它判复核', 'exclude_rad': '单帧变化超过它判排除'},
        parameter_schema={'type': 'object', 'additionalProperties': False, 'properties': {
            'review_rad': {'type': 'number', 'exclusiveMinimum': 0, 'default': 0.2},
            'exclude_rad': {'type': 'number', 'exclusiveMinimum': 0, 'default': 0.5}}},
        input_descriptions=('机器人 yaml 里各手臂的 joints（关节角，框架已换算成 rad）。',))

    def check(self, episode, params):
        review, exclude = params.get('review_rad', 0.2), params.get('exclude_rad', 0.5)
        # 按类型找手臂（关节反馈的 position），不写死通道名，换机器人也能用
        arms = [c for c in episode.channels.values()
                if c.modality == 'joint_space' and c.source_kind == 'feedback' and 'position' in c.fields]
        if not arms:
            return [Item('本条', status=NOT_APPLICABLE, reason='机器人 yaml 里没写 joints')]
        labels = episode.profile.get('labels') or {}               # yaml 里的中文名，如“左臂关节”
        items = []
        for channel in arms:
            r = jumps(episode.read_signal(channel.name, 'position'), review, exclude)
            rows = r['rows']
            reason = (f"{len(rows)} 处相邻两帧关节角变化超过 {review:g} rad，最大 {r['max_step_rad']:.2f} rad" if rows
                      else f"相邻两帧最大变化 {r['max_step_rad']:.3f} rad")
            items.append(Item(labels.get(channel.name, channel.name), verdict=r['verdict'], reason=reason,
                              evidence=[{'row': row} for row in rows[:20]],
                              metrics={'max_step_rad': r['max_step_rad']}))
        worst = max(item.metrics['max_step_rad'] for item in items)
        return AlgorithmResult(self.spec.id, items, f'{len(items)} 条手臂，最大单帧变化 {worst:.2f} rad')


plugin = Algorithm()
