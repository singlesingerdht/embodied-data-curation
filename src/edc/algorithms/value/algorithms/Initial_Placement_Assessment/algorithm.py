"""首帧摆放：框架怎么调用这项检查（读 SOP、取首帧在这里，检查方法在 method.py）。"""
from contextlib import closing
import json
from pathlib import Path

from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.registry import AlgorithmSpec


def sop_diversity(episode, params):
    """SOP 的物体摆放要求 {字段名: 九宫格}：数据集 metadata 里的 diversity 优先，否则读源文件里的 SOP 话题。"""
    if 'diversity' in episode.metadata:
        return episode.metadata['diversity']
    from edc.algorithms.value.local_vlm import sop_text
    text = sop_text(str(episode.path), params['sop_topic'])
    return json.loads(text) if text else None


class InitialPlacement:
    spec = AlgorithmSpec(
        id='Initial_Placement_Assessment', version='2.0.0', author='EDC', category='value', device='cuda',
        name='Initial placement (SOP nine-grid)',
        description='首帧 RGB：SOP 写的物体是否放在指定的桌面九宫格（Grounding DINO + SAM2 透视九宫格）。',
        data_requirements=({'id': 'initial_rgb', 'channel_param': 'channel', 'modality': 'rgb',
                            'frames_required': True, 'min_samples': 1},),
        parameter_schema={'type': 'object', 'additionalProperties': False,
                          'required': ['channel', 'detector', 'segmenter'], 'properties': {
            'channel': {'type': 'string', 'description': '看桌面的相机（一般是头部相机）'},
            'detector': {'type': 'string', 'description': 'Grounding DINO 模型目录'},
            'segmenter': {'type': 'string', 'description': 'SAM2 权重文件'},
            'segmenter_config': {'type': 'string', 'default': 'configs/sam2.1/sam2.1_hiera_s.yaml'},
            'model': {'type': 'string', 'description': 'Qwen3-VL 模型目录；只在遇到 object_names.json 里没有的字段名时用'},
            'sop_topic': {'type': 'string', 'default': '/diversity_data'},
            'near_y': {'type': 'number', 'default': 1.10, 'description': '桌子近边在画面里的纵坐标（大于 1 表示在画面下方之外）'},
            'spread': {'type': 'number', 'exclusiveMinimum': 0, 'default': 1.25, 'description': '近边宽度是远边的几倍'}}})

    def preflight(self, episode, params):
        for key in ('detector', 'segmenter'):
            if not Path(params[key]).exists():
                return 'missing_dependency', f'{key} 不存在：{params[key]}'
        try:
            import sam2, torch, transformers  # noqa: F401
            if not sop_diversity(episode, params):
                return 'missing_input', f"没有 SOP 摆放要求：metadata 没有 diversity，源文件里也没有 {params['sop_topic']}"
        except ImportError as exc:
            return 'missing_dependency', str(exc)
        except ValueError as exc:
            return 'missing_input', f'SOP 摆放要求读不出来：{exc}'
        return 'runnable', ''

    def run(self, context, params):
        from PIL import Image
        from .method import check, models
        from .names import lookup
        episode = context.episode
        name = resolve_channel(episode, params['channel'])
        with closing(episode.iter_frames(name)) as frames:
            frame = next(frames, None)
        if frame is None:
            raise ValueError(f'{name} 没有能解码的画面')
        image = Image.fromarray(frame['image']).convert('RGB')
        image.save(Path(context.workdir) / 'initial_frame.jpg')
        diversity = sop_diversity(episode, params)
        names = lookup(list(diversity), params.get('model'))
        objects, alarm = check(models(params['detector'], params['segmenter'], params['segmenter_config']),
                               image, diversity, names, params['near_y'], params['spread'])
        if objects is None:
            verdict, reason = 'NEEDS_REVIEW', '首帧里没找到桌子，无法划九宫格'
        else:
            wrong = [f"{o['field']}：SOP {o['sop_cell']}，实际 {o['actual_cell']}" for o in objects if o['result'] == '报警']
            verdict, reason = ('FAIL', '物体位置和 SOP 不符：' + '；'.join(wrong)) if alarm else ('PASS', '')
        return {'kind': 'placement', 'verdict': verdict, 'reason': reason, 'assessment_complete': objects is not None,
                'channel': name, 'frame_index': frame['index'], 'sop_diversity': diversity, 'objects': objects}


plugin = InitialPlacement()
