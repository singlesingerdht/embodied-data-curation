"""拍摄视角覆盖：框架怎么调用这项检查（选相机在这里，检查方法在 method.py）。"""
from pathlib import Path

from edc.algorithms.quality.adapters.cpu import BuiltinAlgorithm


def cameras(episode):
    return [c.name for c in episode.channels.values() if c.modality == 'rgb' and c.image_available]


class Algorithm(BuiltinAlgorithm):
    name = '拍摄视角覆盖'
    version = 'ir-adapter-1'
    inputs = ('video',)
    dependencies = ('cv2',)
    weights = False
    instance_mode = 'rgb'
    descriptions = ('可解码的 RGB 相机；每路均匀抽帧，找每帧同一位置都有、中心浓边缘虚的圆形暗斑（镜头污渍）。',
                    '阈值只在胸部相机的一种污渍上定过；操作出画、相机没对准暂不检查。')
    parameters = {
        'frames': {'type': 'integer', 'minimum': 5, 'default': 30, 'description': '每路相机均匀抽几帧'},
        'min_dark': {'type': 'number', 'exclusiveMinimum': 0, 'default': .2,
                     'description': '暗斑中心比周围暗多少才报（相对亮度）；本批污渍约 0.27~0.30，桌布上的辣椒粉在 0.18 以下'}}

    def check_inputs(self, episode, params):
        if not cameras(episode):
            return 'missing_input', '没有可解码的 RGB 相机'

    def assess(self, ep, params, context):
        from edc.algorithms.quality.algorithms.View_Coverage.method import assess
        return [assess(ep, name, params.get('frames', 30), params.get('min_dark', .2), Path(context.workdir))
                for name in cameras(ep)]
