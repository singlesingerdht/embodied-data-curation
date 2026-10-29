"""HDF5_FORMAT.md 解析：数据上传时附带的结构说明

说明文件（数据集根目录）用类 h5py ls 文本描述 hdf5 内部结构：
- `[Group Attr] rate_hz = 30`          期望频率（数据驱动注入）
- `action/ (Group)` / `  arm/ (Group)`  group 层级（2 空格缩进）
- `position : shape=(343, 14), dtype=float64 单位：rad ...`  通道

利用：期望频率注入、通道结构校验（缺/多）、模态映射（不依赖文件名启发式）。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...ir import (
    MODALITY_CMD, MODALITY_IMAGE, MODALITY_POSE,
    MODALITY_JOINT_STATE,
)


@dataclass
class Hdf5Channel:
    """说明中的通道描述"""
    path: str                 # 完整路径（无前导 /，如 action/arm/position）
    name: str                 # 通道名（如 position）
    shape: Tuple[int, ...] = ()  # 形状（如 (343, 14)；首维=帧数）
    dtype: str = ""           # 数据类型（如 float64 / uint8）
    unit: str = ""            # 单位（如 rad / m）
    attrs: Dict[str, str] = field(default_factory=dict)  # 附加属性（如 orig_shape 图像原尺寸）
    note: str = ""            # 行尾注释/说明（原样截取）


@dataclass
class Hdf5Format:
    """解析出的 hdf5 结构契约"""
    rate_hz: Optional[float] = None  # 期望采样频率（Hz，数据驱动注入）
    source: str = ""          # 数据来源描述（[Group Attr] source）
    groups: List[str] = field(default_factory=list)  # 全部 group 完整路径（无前导 /）
    channels: List[Hdf5Channel] = field(default_factory=list)  # 全部通道描述
    attrs: Dict[str, str] = field(default_factory=dict)  # 顶层 [Group Attr] 键值对

    def channel_paths(self) -> List[str]:
        return [c.path for c in self.channels]

    def modality(self, path: str) -> Optional[str]:
        """按说明路径推断模态（优先级：action > images > state.end > state）"""
        p = "/" + path.strip("/")
        if "/timestamp/" in p:
            return None                       # 时间轴
        if "/action/" in p:
            return MODALITY_CMD
        if "/observation/images/" in p:
            return MODALITY_IMAGE
        if "/observation/state/end/" in p:
            return MODALITY_POSE
        if "/observation/state/" in p:
            return MODALITY_JOINT_STATE
        if "/base/" in p:
            return MODALITY_POSE
        if "/end/" in p:
            return MODALITY_POSE
        return None

    def image_shape_for(self, path: str) -> Optional[Tuple[int, ...]]:
        """图像通道的原始尺寸（attrs.orig_shape，如 [480 640   3]）"""
        for c in self.channels:
            if c.path == path and "orig_shape" in c.attrs:
                try:
                    vals = c.attrs["orig_shape"].replace("[", "").replace("]", "")
                    return tuple(int(x) for x in vals.split())
                except ValueError:
                    return None
        return None


def find_format_file(source: str) -> Optional[Path]:
    """从数据源路径定位 HDF5_FORMAT.md（目录直接、内部递归、上级向上找）"""
    p = Path(source)
    candidates: List[Path] = []
    if p.is_dir():
        candidates.append(p / "HDF5_FORMAT.md")
        candidates.extend(f for f in p.rglob("HDF5_FORMAT.md"))
        candidates.extend(d / "HDF5_FORMAT.md" for d in p.parents[:3])
    else:
        candidates.extend(d / "HDF5_FORMAT.md" for d in [p.parent, *p.parent.parents[:3]])
    for f in candidates:
        if f.is_file() and "__MACOSX" not in f.parts:
            return f
    return None


def parse_hdf5_format(path: Path) -> Hdf5Format:
    """解析 HDF5_FORMAT.md 文本"""
    fmt = Hdf5Format()
    stack: List[str] = []                     # 当前 group 路径栈（无前导 /）

    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()

        # [Group Attr] key = value（属于最近的 group 或顶层）
        m = re.match(r"\[Group Attr\]\s*([\w.-]+)\s*=\s*(.+)", content)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            if k == "rate_hz":
                try:
                    fmt.rate_hz = float(v)
                except ValueError:
                    pass
            elif k == "source":
                fmt.source = v
            if stack:
                pass  # group attrs 暂不细化
            else:
                fmt.attrs[k] = v
            continue

        # Group 行：xxx/ (Group)
        m = re.match(r"([\w.-]+)\s*/\s*\(Group\)", content)
        if m:
            name = m.group(1)
            depth = indent // 2
            stack = stack[:depth] + [name]
            fmt.groups.append("/".join(stack))
            continue

        # 通道行：name : shape=(...), dtype=... [| attrs: ...] [单位：...] [注释]
        m = re.match(
            r"([\w.-]+)\s*:\s*shape=\(([^)]*)\),\s*dtype=([\w|]+)(.*)", content)
        if m:
            name = m.group(1)
            try:
                shape = tuple(int(x) for x in m.group(2).split(",") if x.strip())
            except ValueError:
                shape = ()
            dtype = m.group(3).strip()
            rest = m.group(4)

            attrs: Dict[str, str] = {}
            am = re.search(r"attrs:\s*(.+)", rest)
            if am:
                for pair in re.split(r",\s*(?=[\w.-]+=)", am.group(1)):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        attrs[k.strip()] = v.strip()

            unit_m = re.search(r"单位[:：]\s*([^\s]+)", rest)
            unit = unit_m.group(1) if unit_m else ""

            depth = indent // 2
            stack = stack[:depth]
            full = "/".join(stack + [name])
            fmt.channels.append(Hdf5Channel(
                path=full, name=name, shape=shape, dtype=dtype,
                unit=unit, attrs=attrs, note=rest[:60],
            ))
            continue

    return fmt
