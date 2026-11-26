"""人手干预：框架怎么调用这项检查（选相机、读任务说明在这里，检查方法在 method.py）。"""
from pathlib import Path

from edc.algorithms.channel_names import resolve_channel
from edc.algorithms.registry import AlgorithmSpec


def views_of(episode, params):
    """({视角名: 通道名}, [这条数据里没有的视角])。没有的相机跳过，至少要有一路能解码画面的相机。"""
    if not params['views'] or not all(isinstance(ref, str) for ref in params['views'].values()):
        raise ValueError('views 要写成 {视角名: 相机}，至少一路')
    views, missing = {}, []
    for label, ref in params['views'].items():
        try:
            name = resolve_channel(episode, ref)
        except ValueError:
            missing.append(label)
            continue
        if not episode.channels[name].image_available:
            raise ValueError(f'{label}（{name}）不是能解码画面的相机')
        views[label] = name
    if not views:
        raise ValueError('配置的相机这条数据里一路都没有：' + '、'.join(missing))
    return views, missing


def task_of(episode, params):
    """任务说明：配置里的 task 优先，否则读源文件里的 SOP 话题；都没有时写 unknown。"""
    if params.get('task'):
        return params['task']
    from edc.algorithms.value.local_vlm import sop_text
    return sop_text(str(episode.path), params['task_topic']) or 'unknown'


class HandIntervention:
    spec = AlgorithmSpec(
        id='Hand_Intervention', version='1.0.0', author='EDC', category='value', device='cuda',
        name='Human hand intervention',
        description='多路 RGB：有没有人的手/手臂新进入任务区、碰到或移动任务物体（本地 Qwen3-VL 扫描 + 密帧复核）。',
        parameter_schema={'type': 'object', 'additionalProperties': False, 'required': ['views', 'model'], 'properties': {
            'views': {'type': 'object', 'description': '{视角名: 相机}，视角名会告诉模型，如 head、chest、left_wrist'},
            'model': {'type': 'string', 'description': 'Qwen3-VL 模型目录'},
            'task': {'type': 'string', 'description': '任务说明；不填读 task_topic'},
            'task_topic': {'type': 'string', 'default': '/instruction_data'},
            'window_seconds': {'type': 'number', 'minimum': 4, 'default': 12},
            'scan_fps': {'type': 'number', 'exclusiveMinimum': 0, 'default': 2},
            'review_fps': {'type': 'number', 'exclusiveMinimum': 0, 'default': 4}}})

    def preflight(self, episode, params):
        if not Path(params['model']).is_dir():
            return 'missing_dependency', f"模型目录不存在：{params['model']}"
        try:
            import torch, transformers  # noqa: F401
            views_of(episode, params)
        except ImportError as exc:
            return 'missing_dependency', str(exc)
        except (ValueError, KeyError) as exc:
            return 'missing_input', str(exc)
        return 'runnable', ''

    def run(self, context, params):
        from functools import partial
        from edc.algorithms.value.local_vlm import ask
        from .method import merge, review, sample, scan
        episode, work = context.episode, Path(context.workdir)
        views, missing = views_of(episode, params)
        task = task_of(episode, params)
        ask = partial(ask, params['model'])
        frames = sample(episode, views, max(params['scan_fps'], params['review_fps']))
        candidates, failures = scan(ask, frames, task, params['window_seconds'], params['scan_fps'])
        events = [review(ask, frames, task, event, params['review_fps']) for event in merge(candidates)]
        for i, event in enumerate(events):  # 没排除的事件留下复核用的拼图，方便人看
            images = event.pop('images')
            if event['verdict'] != 'rejected':
                event['evidence'] = []
                for k, (_, image) in enumerate(images):
                    image.save(work / f'event{i:02d}_{k}.jpg', quality=85)
                    event['evidence'].append(f'event{i:02d}_{k}.jpg')
        shown = [e for e in events if e['verdict'] != 'rejected']
        say = {'confirmed': '人手接触或移动了物体', 'needs_review': '疑似人手入镜'}
        reason = '；'.join(f"{e['start']:.1f}-{e['end']:.1f}s {say[e['verdict']]}" for e in shown)
        if missing:
            reason = '；'.join(filter(None, [reason, '这条数据没有 ' + '、'.join(missing) + ' 相机，用其余相机检查']))
        if failures:
            reason = '；'.join(filter(None, [reason, f'{len(failures)} 个窗口模型没答出有效结果，需要人看']))
        verdict = ('FAIL' if any(e['verdict'] == 'confirmed' for e in events) else
                   'NEEDS_REVIEW' if shown or failures else 'PASS')
        return {'kind': 'hand_intervention', 'verdict': verdict, 'reason': reason, 'assessment_complete': not failures,
                'task': task, 'views': views, 'missing_views': missing, 'events': events, 'scan_failures': failures}


plugin = HandIntervention()
