"""格式读取适配器包

- base.py:      DataReader 抽象基类 + 统一入口（read_data / extract_timestamps）
- rosbag/mcap/parquet/hdf5/lerobot/mp4/bvh: 各格式适配器
- dataset.py:   通用折叠式数据集读取器（每集一个文件夹，任意格式）
- protobuf.py:  mcap 内嵌 protobuf schema 动态解码
"""

from .base import DataReader, read_data, extract_timestamps
from .rosbag import RosBagReader, ros_values_to_dict
from .mcap import McapReader
from .parquet import ParquetReader
from .hdf5 import Hdf5Reader
from .lerobot import LerobotReader
from .dataset import DatasetReader
from .mp4 import Mp4Reader
from .bvh import BvhReader

__all__ = [
    "DataReader", "read_data", "extract_timestamps",
    "RosBagReader", "McapReader", "ParquetReader", "Hdf5Reader",
    "LerobotReader", "DatasetReader", "Mp4Reader", "BvhReader",
    "ros_values_to_dict",
]
