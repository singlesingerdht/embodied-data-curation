"""Import one serial chain from a user-supplied URDF; no meshes or calibration."""
import copy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from .core import rotation


def import_urdf_chain(path, base_frame, tip_frame, *, inputs=None, limit_margin=0.0):
    """limit_margin (rad or m, per joint unit) widens every URDF limit symmetrically as sensor slack."""
    if isinstance(limit_margin, bool) or not isinstance(limit_margin, (int, float)) \
            or not np.isfinite(limit_margin) or limit_margin < 0:
        raise ValueError('limit_margin must be a finite nonnegative number')
    path = Path(path).resolve()
    data = path.read_bytes()
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('URDF DTD/entities are unsupported')
    root = ET.fromstring(data)
    if root.tag != 'robot':
        raise ValueError('Expected expanded URDF robot, not xacro')
    links = [node.get('name') for node in root.findall('link')]
    if any(not n for n in links) or len(links) != len(set(links)):
        raise ValueError('URDF link names must be nonempty and unique')
    if base_frame not in links or tip_frame not in links or base_frame == tip_frame:
        raise ValueError('URDF requires distinct existing base/tip links')
    parents, names = {}, set()
    for node in root.findall('joint'):
        name = node.get('name')
        parent, child = node.find('parent'), node.find('child')
        a = parent.get('link') if parent is not None else None
        b = child.get('link') if child is not None else None
        if not name or name in names or a not in links or b not in links:
            raise ValueError('Invalid URDF joint names or link references')
        if b in parents:
            raise ValueError('Closed chains/multiple parents are unsupported')
        names.add(name)
        parents[b] = (a, node)
    for link in links:
        seen = set()
        while link in parents:
            if link in seen:
                raise ValueError('Closed chains/cycles are unsupported')
            seen.add(link)
            link = parents[link][0]
    route, link = [], tip_frame
    while link != base_frame:
        if link not in parents:
            raise ValueError('Tip must descend from base in the URDF tree')
        link, node = parents[link]
        route.append(node)

    def vector(node, key, default):
        value = np.asarray([float(x) for x in node.get(key, default).split()])
        if value.shape != (3,) or not np.isfinite(value).all():
            raise ValueError(f'Invalid URDF {key}')
        return value

    joints = []
    for node in reversed(route):
        name, kind = node.get('name'), node.get('type')
        if node.find('mimic') is not None:
            raise ValueError(f'{name}: mimic-dependent FK is unsupported')
        if kind not in {'fixed', 'revolute', 'continuous', 'prismatic'}:
            raise ValueError(f'{name}: unsupported URDF joint type {kind}')
        origin = node.find('origin')
        matrix = np.eye(4)
        if origin is not None:
            matrix[:3, 3] = vector(origin, 'xyz', '0 0 0')
            r, p, y = vector(origin, 'rpy', '0 0 0')
            matrix[:3, :3] = rotation([0, 0, 1], y) @ rotation([0, 1, 0], p) @ rotation([1, 0, 0], r)
        joint = {'name': name, 'type': 'revolute' if kind == 'continuous' else kind,
                 'origin': matrix.tolist(), 'translation_unit': 'm'}
        if kind != 'fixed':
            axis_node = node.find('axis')
            axis = vector(axis_node, 'xyz', '1 0 0') if axis_node is not None else np.array([1., 0, 0])
            norm = np.linalg.norm(axis)
            if not np.isfinite(norm) or norm == 0:
                raise ValueError(f'{name}: invalid joint axis')
            joint.update(axis=(axis / norm).tolist(), unit='m' if kind == 'prismatic' else 'rad')
            if kind != 'continuous':
                limit = node.find('limit')
                if limit is None or limit.get('lower') is None or limit.get('upper') is None:
                    raise ValueError(f'{name}: URDF lower/upper limits required')
                bounds = [float(limit.get(k)) for k in ('lower', 'upper')]
                if not np.isfinite(bounds).all() or bounds[0] > bounds[1]:
                    raise ValueError(f'{name}: invalid joint limits')
                joint['limits'] = [bounds[0] - limit_margin, bounds[1] + limit_margin]
        joints.append(joint)
    bindings = inputs or {}
    moving = {j['name'] for j in joints if j['type'] != 'fixed'}
    if set(bindings) - moving:
        raise ValueError('URDF input bindings reference absent/fixed joints')
    for joint in joints:
        if joint['name'] in bindings:
            joint['input'] = copy.deepcopy(bindings[joint['name']])
    return {'base_frame': base_frame, 'tip_frame': tip_frame, 'joints': joints,
            'urdf_source': {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                            'robot': root.get('name'), 'limit_margin': float(limit_margin)}}
