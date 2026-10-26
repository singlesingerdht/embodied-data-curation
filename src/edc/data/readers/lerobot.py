"""Compatibility class backed by the shared structured reader."""
from .single import SingleEpisodeReader

class LerobotReader(SingleEpisodeReader):
    format = "lerobot"
