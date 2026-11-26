"""兼容旧接口的 HDF5 读取类，实际由共用的结构化读取器实现。"""
from .single import SingleEpisodeReader

class Hdf5Reader(SingleEpisodeReader):
    format = "hdf5"
