"""Dataset-granularity contract example; not a data valuation method."""
from dqs.registry import AlgorithmSpec


class DatasetInventory:
    spec = AlgorithmSpec('team_example__dataset_inventory', '0.1.0', '数据集输入盘点示例',
        'Example team', 'Demonstrate one result for multiple episodes; no quality or value score.',
        category='value', granularity='dataset',
        parameter_schema={'type': 'object', 'additionalProperties': False, 'properties': {
            'min_episodes': {'type': 'integer', 'minimum': 1, 'default': 2}}},
        input_descriptions=('至少 min_episodes 条显式轨迹；仅展示数据集级调度契约，不做估值。',))

    def preflight_dataset(self, dataset, params):
        count = sum(1 for _ in dataset.iter_episodes())
        return ('runnable', '') if count >= params['min_episodes'] else (
            'missing_input', f'Need at least {params["min_episodes"]} episodes; found {count}')

    def run(self, context, params):
        assert context.episode is None
        rows = [{'episode_id': episode.id, 'source': str(episode.path), 'selector': episode.selector}
                for episode in context.dataset.iter_episodes()]
        context.write_result('episodes.json', rows)
        return {'episode_count': len(rows), 'episodes': rows, 'score': None,
                'scope': 'dataset scheduler demonstration; not a valuation algorithm'}


plugin = DatasetInventory()
