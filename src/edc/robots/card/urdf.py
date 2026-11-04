"""`arms.<arm>.urdf` of the robot yaml: the URDF chain of one arm, its joint and velocity limits."""
from pathlib import Path

from .common import NO_LIMIT, CardError, edc_command, number

URDF_KEYS = {'file', 'joints', 'base_link', 'tip_link', 'tip_offset', 'limit_margin'}


def urdf_route(path, base_link, tip_link, where):
    """Moving joint names from base_link to tip_link, in URDF order; explained CardError otherwise."""
    from edc.data.transforms.urdf import read_urdf, route
    try:
        urdf = read_urdf(path)
    except ValueError as exc:
        raise CardError(f'{where}.file: {exc}') from exc
    hint = f'（用 {edc_command()} robot urdf --urdf {path} 查看连杆和关节的名字）'
    for key, link in (('base_link', base_link), ('tip_link', tip_link)):
        if isinstance(link, str) and link in urdf['joints']:
            joint = urdf['joints'][link]
            raise CardError(f'{where}.{key}: {link} 是关节，这里要写连杆（连杆树里前面没有 └ 的行）。'
                            f'这个关节连着上面的 {joint["parent"]} 和下面的 {joint["child"]}')
        if not isinstance(link, str) or link not in urdf['links']:
            raise CardError(f'{where}.{key}: URDF 里没有连杆 {link}{hint}')
    try:
        names = route(urdf, base_link, tip_link)
    except ValueError as exc:
        raise CardError(f'{where}: {exc}{hint}') from exc
    return [n for n in names if urdf['joints'][n]['type'] != 'fixed']


def urdf_chain(spec, base, where, dof, channel):
    """Transforms chain config, widened joint limits and URDF velocity limits for one arm, in the data's
    joint order: `joints` names, for each recorded joint column, the URDF joint it is."""
    form = ('写成 {file: URDF 文件, joints: [数据里每一列对应的 URDF 关节名，按数据的列顺序], '
            'base_link: 起点连杆, tip_link: 末端连杆}')
    if not isinstance(spec, dict) or not {'file', 'joints', 'base_link', 'tip_link'} <= set(spec):
        missing = sorted({'file', 'joints', 'base_link', 'tip_link'} - set(spec)) if isinstance(spec, dict) else []
        raise CardError(f'{where}: {form}' + (f'；缺 {", ".join(missing)}' if missing else ''))
    if set(spec) - URDF_KEYS:
        raise CardError(f'{where}: 不认识的项 {", ".join(sorted(set(spec) - URDF_KEYS))}（可用：{", ".join(sorted(URDF_KEYS))}）')
    from edc.data.transforms.urdf import import_urdf_chain
    path = (Path(base) / spec['file']).resolve()
    if not path.is_file():
        raise CardError(f'{where}.file: 找不到 {path}')
    names = spec['joints']
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise CardError(f'{where}.joints: 写成关节名列表，如 [joint1, joint2, ...]')
    if len(names) != dof:
        raise CardError(f'{where}.joints: 写了 {len(names)} 个关节名，但 joints.index 选了 {dof} 列，要一列对应一个')
    if len(set(names)) != len(names):
        raise CardError(f'{where}.joints: 有重复的关节名')
    route = urdf_route(path, spec['base_link'], spec['tip_link'], where)
    if set(route) != set(names):
        missing, extra = [n for n in route if n not in names], [n for n in names if n not in route]
        raise CardError(f'{where}.joints: 要和 URDF 从 {spec["base_link"]} 到 {spec["tip_link"]} 经过的 {len(route)} 个'
                        f'可动关节一一对应（URDF 顺序：{", ".join(route)}）'
                        + (f'；没写 {", ".join(missing)}' if missing else '')
                        + (f'；这条链上没有 {", ".join(extra)}' if extra else ''))
    margin = spec.get('limit_margin', 0.01)
    if not number(margin) or margin < 0:
        raise CardError(f'{where}.limit_margin: 写一个不小于 0 的数（弧度）')
    # 数据记录的末端点在 tip_link 坐标系里的位置（米）：不是 URDF 的内容，是这份数据把哪一点叫末端
    tip_offset = spec.get('tip_offset')
    if tip_offset is not None and not (isinstance(tip_offset, list) and len(tip_offset) == 3 and all(number(v) for v in tip_offset)):
        raise CardError(f'{where}.tip_offset: 写 3 个数 [x, y, z]（m）：数据记录的末端点在 tip_link 坐标系里的位置')
    try:
        chain = import_urdf_chain(path, spec['base_link'], spec['tip_link'], limit_margin=float(margin))
    except ValueError as exc:
        raise CardError(f'{where}: URDF 读不通：{exc}') from exc
    moving = {j['name']: j for j in chain['joints'] if j['type'] != 'fixed'}
    inputs = {n: {'channel': channel, 'field': 'position', 'index': i} for i, n in enumerate(names)}
    config = {'urdf': str(path), 'base_frame': spec['base_link'], 'tip_frame': spec['tip_link'],
              'limit_margin': float(margin), 'inputs': inputs}
    if tip_offset is not None and any(tip_offset):
        config['tip_offset'] = [float(v) for v in tip_offset]
    # Both tables come from the same imported chain, in the yaml's joint order (= the data's columns).
    speeds = [moving[n].get('velocity') for n in names]
    return {'config': config, 'limits': [moving[n].get('limits', NO_LIMIT) for n in names],
            'velocity': None if None in speeds else speeds}
