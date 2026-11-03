"""Shared by the robot yaml parts: the error type, small value parsers, and the Builder that collects channels."""
import re
import sys
from pathlib import Path

NO_LIMIT = [-1.0e9, 1.0e9]  # Command limits: none unless configured (the framework requires finite numbers).
# Units of the entries checked for bad numbers (and their declared range) only.
# raw = a number with no physical unit (needs range); unknown = not known, only NaN/Inf is checked.
OTHER_UNITS = {'gripper': ('m', 'cm', 'mm', 'rad', 'deg', 'raw', 'unknown'),
               'hand': ('rad', 'deg', 'm', 'mm', 'raw', 'unknown'),
               'orientation': ('quaternion', 'rpy_rad', 'rpy_deg', 'unknown'),
               'other': ('m', 'cm', 'mm', 'rad', 'deg', 'm/s', 'rad/s', 'deg/s', 'm/s^2', 'A', 'N', 'N*m', 's',
                         'quaternion', 'rpy_rad', 'rpy_deg', 'raw', 'unknown')}


class CardError(ValueError):
    pass


def edc_command():
    """How the user started edc (e.g. `.venv/bin/edc`), so printed commands can be pasted as they are."""
    used = Path(sys.argv[0])
    return str(used) if used.name == 'edc' and used.parent != Path('.') else 'edc'


def unknown_keys(spec, allowed, where):
    extra = set(spec) - set(allowed)
    if extra:
        raise CardError(f'{where}: 不认识的项 {", ".join(sorted(extra))}（可用：{", ".join(sorted(allowed))}）')


def indices(value, where):
    """`3`, `0-5` (inclusive) or a list -> list of ints."""
    if isinstance(value, bool):
        raise CardError(f'{where}.index: 写整数、"起-止"（含两端）或整数列表')
    if isinstance(value, int):
        return [value]
    if isinstance(value, str) and re.fullmatch(r'\s*\d+\s*-\s*\d+\s*', value):
        a, b = (int(x) for x in value.split('-'))
        if b < a:
            raise CardError(f'{where}.index: 终点小于起点')
        return list(range(a, b + 1))
    if isinstance(value, list) and value and all(type(i) is int and i >= 0 for i in value):
        return value
    raise CardError(f'{where}.index: 写整数、"起-止"（含两端）或整数列表')


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float('inf')


def scale_of(entry, where):
    """`scale: {raw: [r0, r1], value: [v0, v1]}` -> [k, b] with value = k * raw + b (in the entry's unit)."""
    scale = entry.get('scale')
    if scale is None:
        return None
    if (not isinstance(scale, dict) or set(scale) != {'raw', 'value'}
            or not all(isinstance(scale[k], list) and len(scale[k]) == 2 and all(number(x) for x in scale[k])
                       for k in scale) or scale['raw'][0] == scale['raw'][1]):
        raise CardError(f'{where}.scale: 写成 {{raw: [原始值1, 原始值2], value: [对应的物理值1, 物理值2]}}，'
                        '如 0~255 对应 -3.14~3.14 rad 写 {raw: [0, 255], value: [-3.14, 3.14]}')
    (r0, r1), (v0, v1) = scale['raw'], scale['value']
    k = (v1 - v0) / (r1 - r0)
    return [k, v0 - k * r0]


def scaled(entry, where):
    scale = scale_of(entry, where)
    return {'scale': scale} if scale else {}


def range_of(entry, where):
    value = entry.get('range')
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2 or not all(number(x) for x in value) or value[0] >= value[1]:
        raise CardError(f'{where}.range: 写成 [最小值, 最大值]，如 [0, 255]')
    return [float(value[0]), float(value[1])]


class Builder:
    """What the yaml parts add up to: internal channels, readable names, recorded sources and Numeric_Check inputs."""

    def __init__(self, profile, confirmed_by, base):
        self.profile, self.confirmed_by, self.base = profile, confirmed_by, Path(base)
        self.channels = profile['channels']
        self.names = {}
        self.sources = {}        # recorded source channel -> {fields: [...]}
        self.finite_only = []    # 'channel.field' checked for NaN/Inf only
        self.ranges = {}         # 'channel.field' -> {range, unit}
        self.limits, self.velocity_limits, self.command_limits = {}, {}, {}   # per internal channel
        self.chains, self.homes = {}, {}

    def source_of(self, entry, where, allow=('scale',)):
        if not isinstance(entry, dict) or not isinstance(entry.get('source'), str) or not entry['source']:
            raise CardError(f'{where}: 写成 {{source: 数据里的名字, field: 字段(可选), index: 下标(可选), unit: 单位}}')
        extra = set(entry) - {'source', 'field', 'index', 'unit', 'label', *allow}
        if extra:
            raise CardError(f'{where}: 不认识的项 {", ".join(sorted(extra))}')
        field = entry.get('field', 'value')
        spec = self.sources.setdefault(entry['source'], {'fields': []})
        if field not in spec['fields']:
            spec['fields'].append(field)
        picks = indices(entry['index'], where) if 'index' in entry else None
        return entry['source'], field, picks

    def add(self, channel, spec, label):
        if channel in self.channels:
            raise CardError(f'{channel} 重复定义')
        self.channels[channel] = spec
        self.names[channel] = label

    def other(self, channel, entry, where, label, units):
        """Values with no physical check of their own: checked for NaN/Inf and, when declared, their range."""
        source, field, picks = self.source_of(entry, where, ('scale', 'range', 'order'))
        unit = entry.get('unit')
        if unit not in units:
            raise CardError(f'{where}.unit 必填：{"、".join(units)}'
                            + ('（raw = 没有物理单位的原始数值，要同时写 range；unknown = 不清楚，只查有没有坏数）'
                               if 'raw' in units else '（unknown = 不清楚，只查有没有坏数）'))
        if unit == 'quaternion' and entry.get('order') not in ('xyzw', 'wxyz'):
            raise CardError(f'{where}.order: 四元数要写顺序 xyzw 或 wxyz')
        if entry.get('order') is not None and unit != 'quaternion':
            raise CardError(f'{where}.order 只给四元数写')
        size = {'quaternion': 4, 'rpy_rad': 3, 'rpy_deg': 3}.get(unit)
        if size and picks is not None and len(picks) != size:
            raise CardError(f'{where}.index: {unit} 是 {size} 个数')
        value_range, scale = range_of(entry, where), scale_of(entry, where)
        if unit == 'raw' and value_range is None:
            raise CardError(f'{where}: unit 写 raw 时要写 range（原始数值的范围），如 range: [0, 255]')
        if (value_range or scale) and unit in ('unknown', 'quaternion', 'rpy_rad', 'rpy_deg'):
            raise CardError(f'{where}: unit 是 {unit} 时不能写 range、scale')
        if scale and unit == 'raw':
            raise CardError(f'{where}: 写了 scale 就把 unit 写成换算后的物理单位（如 m、rad），range 按换算后的值写')
        if picks is None and field == 'value' and not (value_range or scale):
            self.names.setdefault(source, label)
            return
        spec = {'modality': 'other', 'fields': ['value'],
                'select': {'channel': source, 'field': field, 'indices': picks or [0]}}
        if picks is None:
            spec['select'] = {'channel': source, 'fields': {'value': field}}
        if scale:
            spec['field_metadata'] = {'value': {'scale': scale}}
        self.add(channel, spec, label)
        self.finite_only.append(f'{channel}.value')
        if value_range:
            self.ranges[f'{channel}.value'] = {'range': value_range, 'unit': unit}
