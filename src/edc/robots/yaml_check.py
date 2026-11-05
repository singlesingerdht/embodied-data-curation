"""`edc channels` and `edc robot check`: what is in the data, and whether a robot yaml matches it."""
from pathlib import Path

import numpy as np


def list_channels(data, log=print):
    """Every channel the reader finds in the first episode: the names a robot yaml's `source` can use."""
    from edc.data.ir.dataset import open_dataset
    dataset = open_dataset(data, profile={'schema_version': 1, 'id': 'scan', 'version': '1'}, backend='compat')
    episodes = list(dataset.iter_episodes())
    log(f'{data}：{len(episodes)} 条数据，下面是第 1 条（{episodes[0].id}）里的内容')
    episode = episodes[0]
    with episode.opened():
        log(f'{"名字（填到 source）":48} {"类型":6} {"行数":>7}  字段/形状')
        for name in sorted(episode.channels):
            topic = episode.raw(name)
            kind = '图像' if topic.image_available else '深度' if topic.modality == 'depth' else '数值'
            shape = ''
            loader = getattr(episode._reader, 'loaders', {}).get(name)
            if loader is not None:
                try:
                    first = loader()[0]
                    shape = f'每行 {np.asarray(first).shape or "1 个数"}' if not isinstance(first, dict) else \
                        '字段 ' + ', '.join(f'{k}{np.asarray(v).shape}' for k, v in first.items())
                except Exception:
                    shape = ''
            elif topic.fields:
                shape = '字段 ' + ', '.join(topic.fields)
            if topic.msg_type:
                shape += f'  ({topic.msg_type})'
            log(f'{name:48} {kind:6} {topic.count:>7}  {shape}')
    log('表格数据（LeRobot、Parquet、JSON、HDF5）的数组用 index 选列，字段名不用写；ROS 消息要写 field。')


def check_yaml(robot, data, log=print):
    """Compile the yaml, then read every channel it declares from the first episode. Returns the problem count."""
    from edc.robots.card import compile_card, CardError
    from edc.data.channel_registry import read_unique_yaml
    from edc.data.ir.dataset import open_dataset
    from edc.data.ir.units import SIDataset
    from edc.robots.profiles import load_profile
    problems = 0
    try:
        card = read_unique_yaml(Path(robot).read_text(encoding='utf-8'))
        profile, params, names = compile_card(card, Path(robot).resolve().parent)
        load_profile(robot)  # Same validation `edc batch` applies.
    except (CardError, ValueError) as exc:
        log(f'✗ yaml 有错：{exc}')
        return 1
    log(f'✓ yaml 格式正确：机器人 {profile["id"]}，{len(profile["parts"])} 条手臂，'
        f'{len(profile["camera_requirements"])} 路彩色相机')
    raw = next(iter(open_dataset(data, profile=robot, backend='compat').iter_episodes()))
    episode = next(iter(SIDataset(open_dataset(data, profile=robot, backend='compat'), 'auto').iter_episodes()))
    log(f'用第 1 条数据核对：{episode.id}')
    with episode.opened(), raw.opened():
        for name, spec in profile['channels'].items():
            if name == '*':
                continue
            label = names.get(name, name)
            channel = episode.channels.get(name)
            if channel is None:
                problems += 1
                log(f'  ✗ {label}（{name}）：数据里找不到；用 edc channels 看数据里的名字')
                continue
            if spec.get('modality') in ('rgb', 'depth'):
                ok = channel.image_available or spec.get('modality') == 'depth'
                problems += not ok
                log(f'  {"✓" if ok else "✗"} {label}（{name}）：{channel.count} 帧' + ('' if ok else '，但不能解码成图像'))
                continue
            for field in spec.get('fields', []):
                unit = (spec.get('field_metadata', {}).get(field, {}) or {}).get('unit') or spec.get('unit') or ''
                try:  # Values with a unit through the SI view; NaN/Inf-only values as recorded.
                    values = np.asarray((episode if unit else raw).read_signal(name, field), float)
                except Exception as exc:
                    problems += 1
                    log(f'  ✗ {label}（{name}.{field}）：读不出来：{exc}')
                    continue
                bad = int((~np.isfinite(values)).sum())
                si = {'deg': 'rad', 'deg/s': 'rad/s', 'mm': 'm', 'cm': 'm'}.get(unit, unit)
                finite = values[np.isfinite(values)]
                span = f'{finite.min():.3g} ~ {finite.max():.3g} {si}' if finite.size else '没有有效数'
                declared = (params.get('Numeric_Check', {}).get('ranges') or {}).get(f'{name}.{field}')
                outside = int(((finite < declared['range'][0]) | (finite > declared['range'][1])).sum()) if declared else 0
                problems += bool(outside)  # The yaml range may be wrong; NaN is a data problem, not a yaml one.
                log(f'  {"✓" if not (bad or outside) else "✗"} {label}（{name}.{field}）：{values.shape[0]} 行 × '
                    f'{values.shape[1] if values.ndim > 1 else 1} 个数，范围 {span}' + (f'，{bad} 个 NaN/无穷大' if bad else '')
                    + (f'，yaml 写的范围 [{declared["range"][0]:g}, {declared["range"][1]:g}]' if declared else '')
                    + (f'，{outside} 个数超出' if outside else ''))
    log('核对完成：' + ('没有问题，可以跑 edc batch' if not problems else f'{problems} 处问题，见上面打 ✗ 的行'))
    log('请再看一眼每行的数值范围是否符合单位：关节角一般在 ±3.2 rad 内，末端位置一般在 ±2 m 内。')
    return problems
