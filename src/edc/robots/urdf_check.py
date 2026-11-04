"""`edc robot urdf`：查 URDF 里的名字；用数据核对机器人 yaml 里的 urdf 段写得对不对。

1. `edc robot urdf --urdf U [--base B --tip T]` 把 URDF 列成连杆树（连杆、关节、类型、限位），
   让用户自己写 `arms.<手臂>.urdf`；加 --base/--tip 时列出两者之间的可动关节和可粘贴的几行。
2. `edc robot urdf --robot R --data D [D2]` 逐条手臂核对 yaml，只判对错，不猜改法：
   - 关节角在不在 URDF 限位内；
   - 数据录了末端位置时：用关节角按 URDF 算出末端（tip_link，加 yaml 的 tip_offset），和记录值比。
     数据记录末端用的坐标系 URDF 里没有（比如仿真的世界坐标系），所以要知道它相对 base_link 在哪：
     用第一条数据求出这个坐标系（平移+旋转），原样用到第二条数据上验证。坐标系是机器人摆放的位置，
     对每条数据都一样；第二条数据没参与求坐标系，它对得上才说明 URDF 和 yaml 是对的。
   每条手臂的结论：对得上 / 对不上 / 末端未验证（数据没录末端，或只有一条数据：只查了关节范围）。
"""
from pathlib import Path

import numpy as np

from edc.data.transforms.core import rotation
from edc.data.transforms.urdf import KINDS as SUPPORTED, read_urdf

GOOD_MM = 10.0       # 末端位置误差（均方根）在这以内算对得上
GOOD_DEG = 5.0       # 末端姿态误差（均方根）在这以内算对得上
OUTSIDE = 0.01       # 某个关节超出 URDF 限位的行超过这个比例，算对不上
MIN_ROWS = 20        # 每条数据至少要有这么多行能配上同一时刻的关节和末端
MAX_ROWS = 3000
KINDS = {'revolute': '转动', 'continuous': '转动无限位', 'prismatic': '移动', 'fixed': '固定'}


def joint_notes(joint):
    """Problems of one joint (a read_urdf joint) that stop forward kinematics through it."""
    name, kind = joint['name'], joint['type']
    if kind not in SUPPORTED:
        return [f'关节 {name} 类型 {kind} 不支持（只支持 {"/".join(SUPPORTED)}）']
    out = []
    if joint['mimic']:
        out.append(f'关节 {name} 是 mimic（跟随别的关节动），正运动学不支持')
    if kind in ('revolute', 'prismatic'):
        if joint['lower'] is None or joint['upper'] is None:
            out.append(f'关节 {name} 没写 <limit lower upper>')
        elif joint['lower'] > joint['upper']:
            out.append(f'关节 {name} 限位下限大于上限')
    if kind != 'fixed' and joint['axis'] is not None:
        try:
            vector = np.array([float(x) for x in joint['axis'].split()])
        except ValueError:
            vector = np.zeros(0)
        if vector.shape != (3,) or np.linalg.norm(vector) == 0:
            out.append(f'关节 {name} 的转轴不对：{joint["axis"]}')
    return out


def describe(joint):
    kind = joint['type']
    text = KINDS.get(kind, kind)
    if kind in ('revolute', 'prismatic') and joint['lower'] is not None and joint['upper'] is not None:
        unit = 'm' if kind == 'prismatic' else 'rad'
        text += f'，{joint["lower"]:.4g} ~ {joint["upper"]:.4g} {unit}'
    if kind != 'fixed' and joint['velocity'] is not None:
        text += f'，速度上限 {joint["velocity"]:g}'
    return text


def list_urdf(path, base=None, tip=None, log=print):
    """Print the URDF tree, or the moving joints between base and tip with the yaml lines. 0 when readable."""
    from edc.robots.card import edc_command
    try:
        urdf = read_urdf(path)
    except ValueError as exc:
        log(f'✗ {exc}')
        return 1
    joints = urdf['joints']
    children = {}
    for joint in joints.values():
        children.setdefault(joint['parent'], []).append(joint)
    roots = [link for link in urdf['links'] if link not in urdf['parent']]
    moving = sum(1 for j in joints.values() if j['type'] != 'fixed')
    log(f'{path}：{len(urdf["links"])} 个连杆，{len(joints)} 个关节（{moving} 个可动）')
    notes = [n for joint in joints.values() for n in joint_notes(joint)]
    if len(roots) != 1:
        notes.append(f'应该只有一个根连杆，现在有 {len(roots)} 个：{", ".join(roots[:5])}')
    if notes:
        log(f'提示：下面 {len(notes)} 处只有手臂链用到时才算问题（核对时会再查）：')
        for n in notes[:8]:
            log(f'  - {n}')
        if len(notes) > 8:
            log(f'  - …… 另有 {len(notes) - 8} 处')
    if base or tip:
        from edc.robots.card import urdf_route, CardError
        try:
            route = urdf_route(path, base, tip, '--base/--tip')
        except CardError as exc:
            log(f'✗ {exc}')
            return 1
        log(f'\n从 {base} 到 {tip} 依次经过 {len(route)} 个可动关节：')
        for i, name in enumerate(route):
            log(f'  {i + 1}. {name}（{describe(joints[name])}）')
        where = Path(path).resolve()
        log('\n把下面 5 行原样粘贴到机器人 yaml 的 arms.<手臂>: 下面（缩进和 joints: 那行对齐），'
            '替换模板里“▼ URDF”标记下的 urdf 几行。\n'
            f'file 按“URDF 放在 configs/robots/{where.parent.name}/、yaml 放在 configs/robots/”写的，放别处就改成相对 yaml 的路径。\n'
            'joints 是 URDF 里的顺序；数据第 1 列是哪个关节就写在第 1 个，数据顺序不同就调换：\n')
        log(f'    urdf:\n      file: {where.parent.name}/{where.name}\n      joints: [{", ".join(route)}]\n'
            f'      base_link: {base}\n      tip_link: {tip}')
        log('\n数据记录的末端点如果不在 tip_link 原点（比如在两指中间、比法兰往前一截），再加一行 tip_offset: [x, y, z]'
            '（m，tip_link 坐标系）。这个数要从数据的说明里查（比如仿真器、SDK 文档里“末端”的定义），核对不会替你算。')
        return 0
    log('\n连杆树（缩进表示上下级；└ 关节名 [类型，限位] 下面一行是它带动的连杆）：')

    def walk(link, depth):
        log('  ' * depth + link)
        for joint in children.get(link, []):
            log('  ' * depth + f'  └ {joint["name"]} [{describe(joint)}]')
            walk(joint['child'], depth + 2)
    for root in roots:
        walk(root, 0)
    log('\n下一步：选手臂的起点连杆 base_link（一般是手臂装在身上的连杆，或数据里末端位置所用的坐标系）'
        '和末端连杆 tip_link（一般是手的工作点，名字常含 end_effector、tool、tcp、ee），再运行\n'
        f'  {edc_command()} robot urdf --urdf {path} --base 起点连杆 --tip 末端连杆\n'
        '得到两者之间的关节，填进 yaml。注意 --base、--tip 写连杆名（树里前面没有 └ 的行），不是关节名。')
    return 0


def forward(chain, angles):
    """N x 4 x 4 tip poses of an imported chain (edc.data.transforms.urdf) for N x dof angles in URDF order:
    the framework's own forward kinematics, without its limit check (the angles may be outside a wrong URDF's)."""
    from edc.data.transforms.core import RobotTransforms
    moving = [j['name'] for j in chain['joints'] if j['type'] != 'fixed']
    angles = np.asarray(angles, float)
    positions = {name: angles[:, i] for i, name in enumerate(moving)}
    return RobotTransforms({'transforms': {'chains': {'arm': chain}}}).fk('arm', positions, check_limits=False).output


def rigid(points, recorded):
    """把 points 对到 recorded 的最佳刚体变换 rotation @ point + shift，返回 (rotation, shift)（Kabsch 算法）。"""
    a, b = points - points.mean(0), recorded - recorded.mean(0)
    u, _, vt = np.linalg.svd(a.T @ b)
    rot = vt.T @ np.diag([1, 1, np.sign(np.linalg.det(vt.T @ u.T))]) @ u.T
    return rot, recorded.mean(0) - points.mean(0) @ rot.T


def project(m):
    """离 m 最近的旋转矩阵。"""
    u, _, vt = np.linalg.svd(m)
    return u @ np.diag([1, 1, np.sign(np.linalg.det(u @ vt))]) @ vt


def rotations(values, unit, order=None):
    """N x 3 x 3：四元数（order xyzw/wxyz）或 URDF 的 roll-pitch-yaw（R = Rz(y) Ry(p) Rx(r)）。"""
    values = np.asarray(values, float).reshape(len(values), -1)
    if unit == 'quaternion':
        q = values if order == 'wxyz' else values[:, [3, 0, 1, 2]]
        q = q / np.linalg.norm(q, axis=1, keepdims=True)
        w, x, y, z = q.T
        return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1),
                         np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1),
                         np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)], 1)
    r, p, y = (np.radians(values) if unit == 'rpy_deg' else values).T
    return np.stack([rotation([0, 0, 1], c) @ rotation([0, 1, 0], b) @ rotation([1, 0, 0], a)
                     for a, b, c in zip(r, p, y)])


def fmt(vector):
    return '(' + ', '.join(f'{round(v, 3) + 0.0:.3f}' for v in vector) + ')'  # + 0.0：不显示 -0.000


def nearest(source_times, target_times, tolerance):
    """每个 target 时刻最近的 source 行号；离得比 tolerance 远的记 -1。"""
    index = np.clip(np.searchsorted(source_times, target_times), 1, len(source_times) - 1)
    left = source_times[index - 1]
    pick = np.where(np.abs(target_times - left) <= np.abs(source_times[index] - target_times), index - 1, index)
    return np.where(np.abs(source_times[pick] - target_times) <= tolerance, pick, -1)


def orientation_of(raw, spec, tag, count):
    """(N x 3 x 3 姿态或 None, 说明)：这条手臂记录的末端姿态。"""
    rot = spec.get('eef_orientation') or {}
    if rot.get('unit') not in ('quaternion', 'rpy_rad', 'rpy_deg'):
        return None, ('没写 eef_orientation，只比位置' if not rot else f'eef_orientation 的 unit 是 {rot.get("unit")}，只比位置')
    name = f'eef_orientation/{tag}' if f'eef_orientation/{tag}' in raw.channels else rot['source']
    values = np.asarray(raw.read_signal(name, 'value'), float)
    values = values.reshape(len(values), -1)
    if len(values) != count or values.shape[1] != (4 if rot['unit'] == 'quaternion' else 3):
        return None, f'姿态是 {len(values)} 行 × {values.shape[1]} 个数，和末端位置配不上，只比位置'
    return rotations(values, rot['unit'], rot.get('order')), ''


def samples(episode, raw, spec, tag):
    """一条数据里同一时刻的 (关节角, 末端位置, 末端姿态或 None, 姿态说明)；关节读数没更新的行不要。"""
    joints = np.asarray(episode.read_signal(f'joint/arm/{tag}/feedback', 'position'), float)
    channel = episode.channels[f'joint/arm/{tag}/feedback']
    tip = np.asarray(episode.read_signal(f'pose/arm/{tag}/feedback', 'position'), float)
    tip_channel = episode.channels[f'pose/arm/{tag}/feedback']
    rot, note = orientation_of(raw, spec, tag, len(tip))
    if len(tip) == len(joints) and np.array_equal(channel.timestamps, tip_channel.timestamps):
        pair = np.arange(len(tip))
    else:  # 按时间给每个末端记录配上最近的关节行
        times = np.asarray(channel.timestamps, float)
        step = float(np.median(np.diff(times))) if len(times) > 1 else 0.0
        pair = nearest(times, np.asarray(tip_channel.timestamps, float), max(step, 1e-3))
    rows = np.flatnonzero((pair >= 0) & np.isfinite(tip).all(axis=1))
    rows = rows[np.isfinite(joints[pair[rows]]).all(axis=1)]
    if rot is not None:
        rows = rows[np.isfinite(rot[rows]).all(axis=(1, 2))]
    if len(rows):
        rows = rows[np.r_[True, np.any(np.diff(joints[pair[rows]], axis=0) != 0, axis=1)]]
        rows = rows[np.linspace(0, len(rows) - 1, min(len(rows), MAX_ROWS)).astype(int)]
    return joints[pair[rows]], tip[rows], (rot[rows] if rot is not None else None), note


def errors(poses, tip, rot, frame, shift, tool):
    """按给定的坐标系（frame, shift）和末端轴约定（tool），算出的末端和记录值差多少：(mm, 度或 None)。"""
    mm = float(np.sqrt(((poses[:, :3, 3] @ frame.T + shift - tip) ** 2).sum(1).mean())) * 1000
    if rot is None:
        return mm, None
    rest = np.einsum('nji,njk->nik', np.einsum('ij,njk,kl->nil', frame, poses[:, :3, :3], tool), rot)
    cos = np.clip((np.trace(rest, axis1=1, axis2=2) - 1) / 2, -1, 1)
    return mm, float(np.degrees(np.sqrt((np.arccos(cos) ** 2).mean())))


def matched(mm, degrees):
    return mm <= GOOD_MM and (degrees is None or degrees <= GOOD_DEG)


def check_arm(pairs, arm, spec, config, log):
    """pairs：最多两条数据的 (episode, raw)。"""
    from edc.data.transforms.urdf import import_urdf_chain
    from edc.robots.card import indices
    urdf = spec['urdf']
    names = list(urdf['joints'])
    side = spec.get('side', arm if arm in ('left', 'right') else 'unknown')
    tag = side if side != 'unknown' else 'main'
    log(f'\n== 手臂 {arm}：{Path(config["urdf"]).name}，{config["base_frame"]} → {config["tip_frame"]}')
    parsed = read_urdf(config['urdf'])['joints']
    problems = [p for n in names for p in joint_notes(parsed[n])]
    if problems:
        for p in problems:
            log(f'  ✗ {p}')
        log(f'  结论（{arm}）：对不上。URDF 链上的关节没法算')
        return {'verdict': 'mismatch'}
    chain = import_urdf_chain(config['urdf'], config['base_frame'], config['tip_frame'],
                              limit_margin=config['limit_margin'], tip_offset=config.get('tip_offset'))
    order = [names.index(j['name']) for j in chain['joints'] if j['type'] != 'fixed']
    nodes = {j['name']: j for j in chain['joints']}
    angles = np.concatenate([np.asarray(e.read_signal(f'joint/arm/{tag}/feedback', 'position'), float)
                             for e, _ in pairs])
    angles = angles[np.isfinite(angles).all(axis=1)]
    lows = np.array([nodes[n].get('limits', [-np.inf, np.inf])[0] for n in names])
    highs = np.array([nodes[n].get('limits', [-np.inf, np.inf])[1] for n in names])
    outside = ((angles < lows) | (angles > highs)).mean(0) if len(angles) else np.zeros(len(names))
    source, picks = spec['joints'], indices(spec['joints']['index'], 'joints')
    log(f'  1. 关节范围（{len(pairs)} 条数据；关节角已按 yaml 换算成 rad；限位已放宽 {config["limit_margin"]:g}）：')
    for i, name in enumerate(names):
        span = f'{angles[:, i].min():.3f} ~ {angles[:, i].max():.3f}' if len(angles) else '无数据'
        limit = f'{lows[i]:.3f} ~ {highs[i]:.3f}' if np.isfinite(lows[i]) else '无限位'
        column = source['source'] + (f'.{source["field"]}' if source.get('field') else '') + f'[{picks[i]}]'
        log(f'     {"✗" if outside[i] > OUTSIDE else "✓"} 第 {i + 1} 列（{column}）→ {name}：'
            f'数据 {span}，限位 {limit}' + (f'，{outside[i]:.1%} 行超出' if outside[i] > 0 else ''))
    result = {'outside': outside.tolist()}
    if any(outside > OUTSIDE):
        log(f'  结论（{arm}）：对不上。有关节超过 1% 的行超出 URDF 限位（见打 ✗ 的行）')
        return {**result, 'verdict': 'mismatch'}
    if spec.get('eef_position') in (None, 'fk'):
        log('  2. 末端：数据没录末端位置，没有可比的')
        log(f'  结论（{arm}）：末端未验证。关节都在限位内；URDF 算出的末端准不准，数据里没有可比的')
        return {**result, 'verdict': 'unverified'}
    usable = []
    for episode, raw in pairs:
        q, p, r, note = samples(episode, raw, spec, tag)
        if len(q) >= MIN_ROWS:
            usable.append((episode.id, forward(chain, q[:, order]), p, r, note))
    if len(usable) < 2:
        log(f'  2. 末端：要两条数据（第一条求坐标系，第二条验证），能用的只有 {len(usable)} 条'
            f'（每条至少 {MIN_ROWS} 行能配上同一时刻的关节和末端）')
        log(f'  结论（{arm}）：末端未验证。关节都在限位内；再给一条数据才能验证末端')
        return {**result, 'verdict': 'unverified'}
    (first, poses, tip, rot, note), (second, poses2, tip2, rot2, _) = usable[:2]
    frame, shift = rigid(poses[:, :3, 3], tip)
    tool = None
    if rot is not None and rot2 is not None:  # 末端坐标轴的朝向约定（哪根轴朝前）也从第一条数据求
        tool = project(np.einsum('nij,nik->jk', np.einsum('ij,njk->nik', frame, poses[:, :3, :3]), rot))
    else:
        rot = rot2 = None
    turn = np.degrees(np.arccos(np.clip((np.trace(frame) - 1) / 2, -1, 1)))
    declared = config.get('tip_offset')
    point = f'tip_link 加 tip_offset {fmt(declared)} m' if declared else f'tip_link（{config["tip_frame"]}）原点'
    mm1, deg1 = errors(poses, tip, rot, frame, shift, tool)
    mm2, deg2 = errors(poses2, tip2, rot2, frame, shift, tool)

    def show(mm, deg):
        return f'位置误差 {mm:.1f} mm' + (f'，姿态误差 {deg:.1f}°' if deg is not None else '')
    log(f'  2. 末端（末端点取 {point}；{note or "位置和姿态都比"}）：\n'
        f'     第一条 {first}：求出数据坐标系相对 {config["base_frame"]} 平移 {fmt(shift)} m、旋转 {turn:.1f}°，'
        f'这条自己的{show(mm1, deg1)}\n'
        f'     第二条 {second}：直接用上面的坐标系，{show(mm2, deg2)}'
        f'（位置 {GOOD_MM:g} mm、姿态 {GOOD_DEG:g}° 以内算对得上）')
    result.update(first_mm=mm1, first_deg=deg1, rms_mm=mm2, deg=deg2, frame_shift=shift.tolist(),
                  frame_turn_deg=turn)
    if matched(mm1, deg1) and matched(mm2, deg2):
        log(f'  结论（{arm}）：对得上')
        return {**result, 'verdict': 'correct'}
    log(f'  结论（{arm}）：对不上。URDF 按 yaml 算出的末端和数据记录的末端差得超过标准')
    return {**result, 'verdict': 'mismatch'}


def episodes(data, profile):
    """依次给出 data 里的 (换算成国际单位的 episode, 原始 episode)；data 是一个或几个路径。"""
    from edc.data.ir.dataset import open_dataset
    from edc.data.ir.units import SIDataset
    for path in ([data] if isinstance(data, (str, Path)) else data):
        yield from zip(SIDataset(open_dataset(path, profile=profile, backend='compat'), 'auto').iter_episodes(),
                       open_dataset(path, profile=profile, backend='compat').iter_episodes())


def verify(robot, data, arms=None, log=print):
    """核对 yaml 里每条写了 urdf 的手臂；返回 {手臂: {verdict: correct/mismatch/unverified, ...}}。
    data：一个或几个路径，取前两条数据（一个文件里有多条数据时，一个路径就够）。"""
    from contextlib import ExitStack
    from itertools import islice
    from edc.robots.card import CardError, compile_card, edc_command
    from edc.data.channel_registry import read_unique_yaml
    robot = Path(robot)
    card = read_unique_yaml(robot.read_text(encoding='utf-8'))
    try:
        profile, _, _ = compile_card(card, robot.resolve().parent)
    except CardError as exc:
        log(f'✗ yaml 有错：{exc}')
        return {}
    chains = (profile.get('transforms') or {}).get('chains') or {}
    todo = [a for a in (card.get('arms') or {}) if a in chains and (not arms or a in arms)]
    if not todo:
        log(f'yaml 里没有手臂写了 urdf：先用 {edc_command()} robot urdf --urdf 文件 查名字，写进 arms.<手臂>.urdf')
        return {}
    pairs = list(islice(episodes(data, profile), 2))
    log(f'用 {len(pairs)} 条数据核对 yaml 里的 URDF：' + '、'.join(e.id for e, _ in pairs))
    with ExitStack() as stack:
        for episode, raw in pairs:
            stack.enter_context(episode.opened())
            stack.enter_context(raw.opened())
        return {arm: check_arm(pairs, arm, card['arms'][arm], chains[arm], log) for arm in todo}
