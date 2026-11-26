"""兼容旧接口的 LeRobot 读取类，实际由共用的结构化读取器实现。"""
from .single import SingleEpisodeReader

class LerobotReader(SingleEpisodeReader):
    format = "lerobot"
