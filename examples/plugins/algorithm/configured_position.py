"""Registration example: select a field and declare requirements without a core ID branch.

This example only returns a signal shape; it is not an added quality method.
"""
from edc.algorithms.registry import AlgorithmSpec


class ConfiguredPosition:
    spec = AlgorithmSpec('team_example__configured_position', '0.1.0', '可配置关节位置输入示例',
        'Example team', 'Demonstrate parameter-dependent requirements without assigning quality scores.',
        parameters={'channel': '实际关节位置通道名称', 'require_physics': '是否要求完整物理输入'},
        parameter_schema={'type': 'object', 'additionalProperties': False,
            'properties': {'channel': {'type': 'string'},
                           'require_physics': {'type': 'boolean', 'default': True}},
            'required': ['channel']},
        data_requirements=(
            {'id': 'position', 'channel_param': 'channel', 'field': 'position', 'min_samples': 2},
            {'id': 'physical_input', 'channel_param': 'channel', 'field': 'position',
             'modality': 'joint_space', 'unit': 'rad', 'source_kind': 'feedback',
             'calibration_status': 'confirmed', 'time_sources': ['acquisition'],
             'when': {'require_physics': True}}),
        input_descriptions=('指定通道必须存在 position；物理模式还要求关节反馈、SI 执行输入、已确认标定与原始采集时间。',))

    def run(self, context, params):
        name=params['channel']
        values=(context.episode.read_signal(name, 'position') if params['require_physics']
                else context.episode.inspect_recorded_signal(name, 'position'))
        return {'verdict': 'OBSERVATION', 'score': None, 'shape': list(values.shape),
                'input_channel': name, 'params': params, 'scope': 'input contract demonstration only'}


plugin=ConfiguredPosition()
plugin.spec = __import__('dataclasses').replace(plugin.spec, example=True)
