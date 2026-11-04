"""Read a user-supplied URDF, and import one serial chain from it; no meshes or calibration.

read_urdf() is the only place a URDF file is parsed. Everything else uses what it returns: the chain for
forward kinematics with its joint and velocity limits (import_urdf_chain, then the robot yaml and the numeric
checks), and `edc robot urdf` (tree listing, names, notes)."""
import copy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from .core import rotation

KINDS = ('fixed', 'revolute', 'continuous', 'prismatic')


def read_urdf(path):
    """{'path', 'sha256', 'robot', 'links': [names], 'joints': {name: joint}, 'parent': {child link: joint name}}.

    joint: {'name', 'type', 'parent', 'child', 'xyz', 'rpy', 'axis' (text from the file, axis None when absent),
    'lower', 'upper', 'velocity' (numbers, None when absent), 'mimic' (bool)}. Vectors stay text: only the joints
    a chain uses are checked (import_urdf_chain, joint_notes), so a broken finger does not block the arm.
    ValueError, worded for users, when the file is not a plain URDF or its tree is broken."""
    path = Path(path).resolve()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f'读不了 {path}：{exc}') from exc
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('URDF 里有 <!DOCTYPE> 或 <!ENTITY>，不支持')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f'{path.name} 不是合法的 XML：{exc}') from exc
    if root.tag != 'robot':
        raise ValueError('根节点不是 <robot>：如果是 xacro，请先用 xacro 命令展开成普通 URDF')
    if any(node.tag.startswith('{http://wiki.ros.org/xacro}') or '${' in ' '.join(node.attrib.values())
           for node in root.iter()):  # Comments are not nodes: a commented-out ${...} is fine.
        raise ValueError('这是 xacro 模板（含 ${...} 或 xacro: 标签），请先展开成普通 URDF')
    links = [node.get('name') for node in root.findall('link')]
    if any(not n for n in links) or len(links) != len(set(links)):
        raise ValueError('URDF 的连杆名有空的或重复的')

    def number(node, key, name):
        value = node.get(key) if node is not None else None
        try:
            return None if value is None else float(value)
        except ValueError:
            raise ValueError(f'关节 {name} 的 {key}="{value}" 不是数字') from None

    joints, parent = {}, {}
    for node in root.findall('joint'):
        name = node.get('name')
        a, b = (node.find(k).get('link') if node.find(k) is not None else None for k in ('parent', 'child'))
        if not name or name in joints:
            raise ValueError(f'URDF 的关节名有空的或重复的：{name}')
        if a not in links or b not in links:
            raise ValueError(f'关节 {name} 连的连杆不存在：parent={a}，child={b}')
        if b in parent:
            raise ValueError(f'连杆 {b} 有两个上级关节（{parent[b]}、{name}），闭环结构不支持')
        origin, axis, limit = node.find('origin'), node.find('axis'), node.find('limit')
        joints[name] = {'name': name, 'type': node.get('type'), 'parent': a, 'child': b,
                        'xyz': origin.get('xyz', '0 0 0') if origin is not None else '0 0 0',
                        'rpy': origin.get('rpy', '0 0 0') if origin is not None else '0 0 0',
                        'axis': axis.get('xyz', '1 0 0') if axis is not None else None,
                        'lower': number(limit, 'lower', name), 'upper': number(limit, 'upper', name),
                        'velocity': number(limit, 'velocity', name), 'mimic': node.find('mimic') is not None}
        parent[b] = name
    for link in links:
        seen = set()
        while link in parent:
            if link in seen:
                raise ValueError(f'连杆 {link} 所在的关节连成了环，不支持')
            seen.add(link)
            link = joints[parent[link]]['parent']
    return {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'robot': root.get('name'),
            'links': links, 'joints': joints, 'parent': parent}


def route(urdf, base_frame, tip_frame):
    """Joint names (fixed ones too) from base_frame down to tip_frame; ValueError when tip is not below base."""
    names, link = [], tip_frame
    while link != base_frame:
        if link not in urdf['parent']:
            raise ValueError(f'从 {base_frame} 往下走不到 {tip_frame}：tip_link 要在 base_link 下游')
        names.append(urdf['parent'][link])
        link = urdf['joints'][names[-1]]['parent']
    return names[::-1]


def vector(joint, key):
    try:
        value = np.asarray([float(x) for x in joint[key].split()])
    except ValueError:
        value = np.zeros(0)
    if value.shape != (3,) or not np.isfinite(value).all():
        raise ValueError(f'关节 {joint["name"]} 的 {key}="{joint[key]}" 要写 3 个数')
    return value


def import_urdf_chain(path, base_frame, tip_frame, *, inputs=None, limit_margin=0.0, tip_offset=None):
    """limit_margin (rad or m, per joint unit) widens every URDF limit symmetrically as sensor slack.

    tip_offset [x, y, z]（米，tip_frame 坐标系）：数据记录的末端点相对 tip_frame 的位置
    （比如两指中间比法兰往前一截）；接在链末尾，作为一个名为 `tip_offset` 的固定关节。

    Each moving joint also carries the URDF velocity limit (`velocity`, same unit per second) when it has one."""
    if isinstance(limit_margin, bool) or not isinstance(limit_margin, (int, float)) \
            or not np.isfinite(limit_margin) or limit_margin < 0:
        raise ValueError('limit_margin must be a finite nonnegative number')
    urdf = read_urdf(path)
    if base_frame not in urdf['links'] or tip_frame not in urdf['links'] or base_frame == tip_frame:
        raise ValueError(f'base/tip 要是 URDF 里两个不同的连杆：{base_frame}、{tip_frame}')
    joints = []
    for name in route(urdf, base_frame, tip_frame):
        node = urdf['joints'][name]
        kind = node['type']
        if node['mimic']:
            raise ValueError(f'关节 {name} 是 mimic（跟随别的关节动），正运动学不支持')
        if kind not in KINDS:
            raise ValueError(f'关节 {name} 的类型 {kind} 不支持（只支持 {"/".join(KINDS)}）')
        matrix = np.eye(4)
        matrix[:3, 3] = vector(node, 'xyz')
        r, p, y = vector(node, 'rpy')
        matrix[:3, :3] = rotation([0, 0, 1], y) @ rotation([0, 1, 0], p) @ rotation([1, 0, 0], r)
        joint = {'name': name, 'type': 'revolute' if kind == 'continuous' else kind,
                 'origin': matrix.tolist(), 'translation_unit': 'm'}
        if kind != 'fixed':
            axis = vector(node, 'axis') if node['axis'] is not None else np.array([1., 0, 0])
            norm = np.linalg.norm(axis)
            if norm == 0:
                raise ValueError(f'关节 {name} 的转轴是 0 向量')
            joint.update(axis=(axis / norm).tolist(), unit='m' if kind == 'prismatic' else 'rad')
            if kind != 'continuous':
                bounds = [node['lower'], node['upper']]
                if None in bounds:
                    raise ValueError(f'关节 {name} 没写限位 <limit lower upper>（URDF lower/upper limits required）')
                if not np.isfinite(bounds).all() or bounds[0] > bounds[1]:
                    raise ValueError(f'关节 {name} 的限位不对：{bounds[0]} ~ {bounds[1]}（invalid joint limits）')
                joint['limits'] = [bounds[0] - limit_margin, bounds[1] + limit_margin]
            if node['velocity'] is not None and node['velocity'] > 0:
                joint['velocity'] = node['velocity']
        joints.append(joint)
    if tip_offset is not None:
        step = np.eye(4)
        step[:3, 3] = tip_offset
        joints.append({'name': 'tip_offset', 'type': 'fixed', 'origin': step.tolist(), 'translation_unit': 'm'})
    bindings = inputs or {}
    moving = {j['name'] for j in joints if j['type'] != 'fixed'}
    if set(bindings) - moving:
        raise ValueError('URDF input bindings reference absent/fixed joints')
    for joint in joints:
        if joint['name'] in bindings:
            joint['input'] = copy.deepcopy(bindings[joint['name']])
    return {'base_frame': base_frame, 'tip_frame': tip_frame, 'joints': joints,
            'urdf_source': {'path': urdf['path'], 'sha256': urdf['sha256'], 'robot': urdf['robot'],
                            'limit_margin': float(limit_margin), 'tip_offset': tip_offset}}
